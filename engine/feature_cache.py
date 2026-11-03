#!/usr/bin/env python3
"""Build resumable, versioned beat/bar feature caches from a frozen dataset split."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

import analyze
import library_inventory as inventory
import serato_grid
from timing import grid_origin

SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "bar-energy-v1"
BASE_FEATURE_VERSION = "analyze-compute-features-50fps-v1"
SAMPLE_RATE = 44100
FPS = 50
MAX_DECODE_SECONDS = 1200
SCHEMA = """
CREATE TABLE cache_schema (version INTEGER NOT NULL);
INSERT INTO cache_schema VALUES (1);
CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE jobs (
    task_id TEXT PRIMARY KEY, audio_sha256 TEXT NOT NULL, state TEXT NOT NULL,
    source_json TEXT NOT NULL, grid_json TEXT, metadata_json TEXT NOT NULL,
    artifact_path TEXT, artifact_sha256 TEXT, quality_json TEXT, error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
);
CREATE INDEX cache_jobs_state ON jobs(state,task_id);
"""


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as incoming:
        for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_path(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError("Feature cache directory must not be a symlink.")
    return path.resolve()


@contextmanager
def _cache_database(path):
    path = _cache_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = path / "feature-cache.sqlite3"
    if database.is_symlink():
        raise ValueError("Feature cache database must not be a symlink.")
    lock_path = path / "feature-cache.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another feature-cache operation is using this directory.") from error
        created = not database.exists()
        if created:
            descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
        db = sqlite3.connect(database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if created:
                db.executescript(SCHEMA)
            else:
                version = db.execute("SELECT version FROM cache_schema").fetchone()
                if not version or version["version"] != SCHEMA_VERSION:
                    raise ValueError("Unsupported feature-cache schema; use a new cache directory.")
            yield db
        except sqlite3.OperationalError as error:
            raise ValueError("Not a supported AutoCue feature cache: " + str(error)) from error
        finally:
            db.close()


def _grid_from_override(value):
    if not isinstance(value, dict):
        raise ValueError("Grid must be a JSON object.")
    grid = dict(value)
    for field in ("t1", "bar_s"):
        if type(grid.get(field)) not in (int, float) or not math.isfinite(grid[field]):
            raise ValueError("Grid " + field + " must be finite.")
    if grid["bar_s"] <= 0:
        raise ValueError("Grid bar_s must be positive.")
    if not 60 <= 240 / grid["bar_s"] <= 220:
        raise ValueError("Grid tempo must be between 60 and 220 BPM.")
    grid.setdefault("offset_ms", 0)
    origin = grid_origin(grid)
    if not math.isfinite(origin):
        raise ValueError("Grid origin must be finite.")
    source = grid.get("source", "provided_grid")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Grid source must be a non-empty string.")
    grid["source"] = source
    confidence = grid.get("confidence")
    if confidence is not None and (type(confidence) not in (int, float)
                                   or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("Grid confidence must be between 0 and 1.")
    grid["human_verified"] = grid.get("human_verified", False)
    if type(grid["human_verified"]) is not bool:
        raise ValueError("Grid human_verified must be boolean.")
    return grid


def _imported_grid(metadata):
    anchors = metadata.get("imported_anchors")
    if not isinstance(anchors, dict):
        return None, "no_usable_grid"
    all_sources = anchors.get("sources", [])
    if not isinstance(all_sources, list) or any(not isinstance(source, dict) for source in all_sources):
        return None, "invalid_imported_grid"
    sources = [source for source in all_sources
               if source.get("format") == "serato_beatgrid_v1.0" and source.get("status") == "parsed"]
    if len(sources) != 1:
        if sources:
            return None, "ambiguous_imported_grid"
        if any(source.get("field") == "Serato BeatGrid" and source.get("status") in {"error", "unsupported"}
               for source in all_sources):
            return None, "invalid_imported_grid"
        return None, "no_usable_grid"
    t1, bar_s, reason = serato_grid.grid_from_markers(sources[0].get("records"))
    if t1 is None:
        return None, "unusable_imported_grid: " + reason
    try:
        return _grid_from_override({
            "t1": t1, "bar_s": bar_s, "source": "serato_beatgrid_unverified",
            "human_verified": False,
        }), None
    except ValueError as error:
        return None, "unusable_imported_grid: " + str(error)


def _resolve_grid(track, overrides):
    digest = track["audio_sha256"]
    if digest in overrides:
        return _grid_from_override(overrides[digest]), None
    return _imported_grid(track.get("metadata", {}))


def _load_inputs(database, dataset_path, grid_path):
    dataset_bytes = Path(dataset_path).expanduser().read_bytes()
    try:
        dataset = json.loads(dataset_bytes)
    except (UnicodeError, ValueError) as error:
        raise ValueError("Dataset manifest is not valid JSON.") from error
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1:
        raise ValueError("Unsupported dataset manifest schema.")
    scan_id = dataset.get("created_from_scan")
    tracks = dataset.get("tracks")
    if not isinstance(scan_id, str) or not isinstance(tracks, list) or not tracks \
            or any(not isinstance(track, dict) for track in tracks):
        raise ValueError("Dataset manifest must identify a scan and contain tracks.")

    overrides, grid_bytes = {}, b""
    if grid_path:
        grid_bytes = Path(grid_path).expanduser().read_bytes()
        try:
            overrides = json.loads(grid_bytes)
        except (UnicodeError, ValueError) as error:
            raise ValueError("Grid file is not valid JSON.") from error
        if not isinstance(overrides, dict):
            raise ValueError("Grid file must map audio SHA-256 values to grid objects.")
        if any(not isinstance(key, str) or len(key) != 64
               or any(character not in "0123456789abcdef" for character in key.lower())
               for key in overrides):
            raise ValueError("Grid keys must be audio SHA-256 values.")
        normalized_overrides = {key.lower(): value for key, value in overrides.items()}
        if len(normalized_overrides) != len(overrides):
            raise ValueError("Grid file contains duplicate SHA-256 keys with different casing.")
        overrides = normalized_overrides
    signature = hashlib.sha256(_json({
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "grid_sha256": hashlib.sha256(grid_bytes).hexdigest(),
        "extractor": EXTRACTOR_VERSION,
        "base_features": BASE_FEATURE_VERSION,
        "sample_rate": SAMPLE_RATE,
        "fps": FPS,
    }).encode("utf-8")).hexdigest()

    tasks = []
    with inventory._database(database) as db:
        scan = db.execute("SELECT status FROM scans WHERE id=?", (scan_id,)).fetchone()
        if scan is None or scan["status"] not in {"complete", "complete_with_errors"}:
            raise ValueError("Dataset source inventory must remain a completed scan.")
        roots = {row["id"]: dict(row) for row in db.execute(
            "SELECT * FROM roots WHERE scan_id=?", (scan_id,))}
        manifest_root_rows = dataset.get("source_roots")
        if not isinstance(manifest_root_rows, list):
            raise ValueError("Dataset manifest source_roots must be an array.")
        manifest_roots = {}
        for root in manifest_root_rows:
            if not isinstance(root, dict) or type(root.get("root_id")) is not int \
                    or not isinstance(root.get("path"), str):
                raise ValueError("Dataset manifest contains a malformed source root.")
            manifest_roots[root["root_id"]] = root["path"]
        if set(manifest_roots) != set(roots):
            raise ValueError("Dataset manifest source roots do not match its inventory scan.")
        for root_id, root in roots.items():
            if manifest_roots.get(root_id) != root["path"]:
                raise ValueError("Dataset manifest source roots do not match its inventory scan.")
        root_paths = [Path(root["path"]) for root in roots.values()]
        seen_hashes = set()
        for track in tracks:
            digest = track.get("audio_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(
                    character not in "0123456789abcdef" for character in digest.lower()):
                raise ValueError("Dataset track has an invalid audio SHA-256.")
            digest = digest.lower()
            if digest in seen_hashes:
                raise ValueError("Dataset manifest contains duplicate audio hashes; freeze it again.")
            seen_hashes.add(digest)
            paths = track.get("paths")
            if not isinstance(paths, list) or not paths:
                raise ValueError("Dataset track has no source paths.")
            source = None
            for item in paths:
                if not isinstance(item, dict):
                    raise ValueError("Dataset source path is malformed.")
                if type(item.get("root_id")) is not int or not isinstance(item.get("path"), str):
                    raise ValueError("Dataset source path is malformed.")
                root = roots.get(item["root_id"])
                if root is None:
                    raise ValueError("Dataset source path refers to an unknown inventory root.")
                entry = db.execute("""SELECT * FROM entries WHERE scan_id=? AND root_id=?
                    AND relative_path=? AND kind='audio'""",
                    (scan_id, item["root_id"], item["path"])).fetchone()
                if entry is None or entry["status"] != "done":
                    raise ValueError("Dataset source is not a successfully inventoried audio file.")
                source = {"root_id": item["root_id"], "path": item["path"],
                          "fingerprint": {field: entry[field] for field in (
                              "size_bytes", "mtime_ns", "ctime_ns", "device", "inode")}}
                break
            metadata = track.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("Dataset track metadata must be an object.")
            grid, skip_reason = _resolve_grid(track, overrides)
            task_id = hashlib.sha256((digest + "\0" + ("" if grid is None else _json(grid))
                                      + "\0" + EXTRACTOR_VERSION).encode("utf-8")).hexdigest()
            tasks.append({
                "task_id": task_id, "audio_sha256": digest.lower(), "source": source,
                "grid": grid, "metadata": metadata, "skip_reason": skip_reason,
            })
        unknown_grids = set(overrides) - seen_hashes
        if unknown_grids:
            raise ValueError("Grid file contains hashes absent from the frozen dataset.")
    return tasks, roots, root_paths, signature


def _check_cache_location(cache_dir, database, roots):
    cache_dir = _cache_path(cache_dir)
    for root in roots:
        if cache_dir == root or root in cache_dir.parents:
            raise ValueError("Feature cache directory must be outside every audio source root.")
    db_path = inventory._database_path(database)
    if cache_dir == db_path or cache_dir in db_path.parents:
        raise ValueError("Feature cache directory must not contain the inventory database.")
    return cache_dir


def _quality_without_grid(reason):
    return {
        "extractor_version": EXTRACTOR_VERSION, "feature_schema_version": SCHEMA_VERSION,
        "base_feature_version": BASE_FEATURE_VERSION,
        "grid_usable": False,
        "flags": [reason], "analyzed_bar_count": 0,
    }


def _aggregate_features(npz_path, grid, metadata):
    import numpy as np

    with np.load(npz_path) as source:
        fps = float(source["fps"])
        duration = float(source["dur"])
        frame_count = len(source["rms"])
        if not math.isfinite(fps) or fps <= 0 or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Decoded audio has invalid duration or feature frame rate.")
        frame_times = np.arange(frame_count, dtype=np.float64) / fps
        origin = grid_origin(grid)
        bar_s = float(grid["bar_s"])
        if origin < -bar_s or origin >= duration:
            raise ValueError("Grid origin is outside the supported audio range.")
        indices = np.floor((frame_times - origin) / bar_s).astype(np.int64)
        unique_bars = np.unique(indices)
        bar_index, bar_start, bar_coverage = [], [], []
        rms, peak, low, mid, high, frames_per_bar = [], [], [], [], [], []
        for index in unique_bars:
            selected = indices == index
            count = int(selected.sum())
            start_s = origin + int(index) * bar_s
            coverage = min(1.0, count / (bar_s * fps))
            bar_index.append(index)
            bar_start.append(start_s)
            bar_coverage.append(coverage)
            frames_per_bar.append(count)
            rms.append(float(np.mean(source["rms"][selected])))
            peak.append(float(np.max(source["peak"][selected])))
            low.append(float(np.mean(source["lo"][selected])))
            mid.append(float(np.mean(source["mi"][selected])))
            high.append(float(np.mean(source["hi"][selected])))
        if not bar_index:
            raise ValueError("No audio feature frames overlap the supplied grid.")

        rms_array = np.asarray(rms, dtype=np.float32)
        peak_array = np.asarray(peak, dtype=np.float32)
        coverage_array = np.asarray(bar_coverage, dtype=np.float32)
        flags = []
        if np.any(coverage_array < 0.95):
            flags.append("partial_bar_coverage")
        if np.any(peak_array >= 0.999):
            flags.append("peak_near_full_scale")
        if np.all(rms_array < 1e-6):
            flags.append("near_silence")
        if origin > 0:
            flags.append("pre_grid_bar_present")
        if grid.get("confidence") is None:
            flags.append("grid_confidence_unknown")
        elif grid["confidence"] < 0.5:
            flags.append("low_grid_confidence")
        if not grid["human_verified"]:
            flags.append("grid_unverified")
        expected_duration = metadata.get("duration_s")
        if type(expected_duration) in (int, float) and math.isfinite(expected_duration):
            if abs(float(expected_duration) - duration) > max(0.5, duration * 0.005):
                flags.append("metadata_duration_mismatch")
        quality = {
            "grid_usable": True,
            "grid_source": grid["source"], "grid_confidence": grid.get("confidence"),
            "grid_human_verified": grid["human_verified"], "grid_t1": grid["t1"],
            "grid_offset_ms": grid.get("offset_ms", 0),
            "grid_origin_s": origin, "bar_duration_s": bar_s,
            "audio_duration_s": duration, "base_feature_fps": fps,
            "analyzed_bar_count": len(bar_index),
            "partial_bar_count": int(np.sum(coverage_array < 0.95)),
            "near_silent_bar_count": int(np.sum(rms_array < 1e-6)),
            "near_full_scale_bar_count": int(np.sum(peak_array >= 0.999)),
            "flags": flags,
        }
        arrays = {
            "bar_index": np.asarray(bar_index, dtype=np.int32),
            "bar_start_s": np.asarray(bar_start, dtype=np.float32),
            "coverage": coverage_array,
            "frame_count": np.asarray(frames_per_bar, dtype=np.int32),
            "rms": rms_array, "peak": peak_array,
            "low_energy": np.asarray(low, dtype=np.float32),
            "mid_energy": np.asarray(mid, dtype=np.float32),
            "high_energy": np.asarray(high, dtype=np.float32),
        }
    return arrays, quality


def _decode_and_extract(source, root, task, cache_dir, ffmpeg_path, ffmpeg_version):
    descriptor = inventory._open_beneath(root, source["path"])
    work_dir = None
    try:
        before = os.fstat(descriptor)
        expected = tuple(source["fingerprint"][field] for field in (
            "size_bytes", "mtime_ns", "ctime_ns", "device", "inode"))
        if not stat.S_ISREG(before.st_mode) or inventory._fingerprint(before) != expected:
            raise ValueError("Audio changed after inventory; start a new inventory scan.")
        work_dir = tempfile.TemporaryDirectory(prefix=".feature-work-", dir=cache_dir)
        work = Path(work_dir.name)
        decoded = work / "decoded.wav"
        base_features = work / "base.npz"
        command = [
            ffmpeg_path, "-y", "-nostdin", "-v", "error", "-i", f"/dev/fd/{descriptor}",
            "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE), "-acodec", "pcm_s16le", str(decoded),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=MAX_DECODE_SECONDS,
                       pass_fds=(descriptor,))
        if inventory._fingerprint(os.fstat(descriptor)) != expected:
            raise ValueError("Audio changed while decoding; start a new inventory scan.")
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        with os.fdopen(os.dup(descriptor), "rb") as incoming:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != task["audio_sha256"]:
            raise ValueError("Audio content no longer matches the frozen dataset manifest.")
        if inventory._fingerprint(os.fstat(descriptor)) != expected:
            raise ValueError("Audio changed while hashing; start a new inventory scan.")
        analyze.compute_features(decoded, base_features)
        arrays, quality = _aggregate_features(base_features, task["grid"], task["metadata"])
        quality["source_audio_sha256"] = task["audio_sha256"]
        quality["extractor_version"] = EXTRACTOR_VERSION
        quality["base_feature_version"] = BASE_FEATURE_VERSION
        quality["feature_schema_version"] = SCHEMA_VERSION
        quality["ffmpeg_version"] = ffmpeg_version
        return arrays, quality
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or str(error)).strip()
        raise RuntimeError("ffmpeg could not decode audio: " + detail[:1000]) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("ffmpeg decoding exceeded the 20-minute per-track limit.") from error
    finally:
        os.close(descriptor)
        if work_dir is not None:
            work_dir.cleanup()


def _ffmpeg_version():
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required for feature extraction; install it with `brew install ffmpeg`.")
    try:
        result = subprocess.run([executable, "-version"], check=True, capture_output=True,
                                text=True, timeout=10)
    except subprocess.CalledProcessError as error:
        raise RuntimeError("Could not read the installed ffmpeg version.") from error
    return executable, (result.stdout.splitlines() or ["unknown ffmpeg version"])[0]


def _save_artifact(cache_dir, task_id, arrays, quality):
    import numpy as np

    artifact_dir = cache_dir / "features"
    if artifact_dir.is_symlink():
        raise ValueError("Feature cache artifact directory must not be a symlink.")
    artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = artifact_dir / (task_id + ".npz")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".feature-", dir=artifact_dir)
    try:
        with os.fdopen(descriptor, "wb") as output:
            np.savez_compressed(output, quality_json=_json(quality), **arrays)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination, _sha256_file(destination)


def _summary(db, cache_dir, ffmpeg_version=None):
    counts = {row["state"]: row["count"] for row in db.execute(
        "SELECT state,COUNT(*) AS count FROM jobs GROUP BY state")}
    return {
        "schema_version": SCHEMA_VERSION, "extractor_version": EXTRACTOR_VERSION,
        "base_feature_version": BASE_FEATURE_VERSION,
        "counts": counts,
        "errors": [dict(row) for row in db.execute(
            "SELECT audio_sha256,error FROM jobs WHERE state='error' ORDER BY task_id LIMIT 20")],
        "errors_truncated": counts.get("error", 0) > 20,
        "cache_dir": str(cache_dir),
        "ffmpeg_version": ffmpeg_version,
    }


def run_cache(database, cache_dir, dataset_path, *, grid_path=None, limit=None, retry_errors=False,
              on_progress=None):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Feature-attempt limit must be a positive integer.")
    tasks, roots, root_paths, signature = _load_inputs(database, dataset_path, grid_path)
    cache_dir = _check_cache_location(cache_dir, database, root_paths)
    if not analyze.available("numpy"):
        raise RuntimeError("NumPy is required for feature extraction; install it in the AutoCue environment.")
    ffmpeg_path, ffmpeg_version = _ffmpeg_version() if any(task["grid"] for task in tasks) else (None, "not_used")
    signature = hashlib.sha256((signature + "\0" + ffmpeg_version).encode("utf-8")).hexdigest()

    with _cache_database(cache_dir) as db:
        meta = dict(db.execute("SELECT key,value FROM cache_meta"))
        if meta and meta.get("run_signature") != signature:
            raise ValueError("Feature cache inputs/settings changed; use a new cache directory.")
        if not meta:
            if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]:
                raise ValueError("Feature cache metadata is missing; use a new cache directory.")
            with db:
                db.execute("INSERT INTO cache_meta VALUES ('run_signature',?)", (signature,))
                db.execute("INSERT INTO cache_meta VALUES ('dataset_manifest',?)",
                           (str(Path(dataset_path).expanduser().resolve()),))
                db.execute("INSERT INTO cache_meta VALUES ('grid_file',?)",
                           (str(Path(grid_path).expanduser().resolve()) if grid_path else "",))
                db.execute("INSERT INTO cache_meta VALUES ('ffmpeg_version',?)", (ffmpeg_version,))
                now = time.time()
                for task in tasks:
                    state = "queued" if task["grid"] is not None else "skipped"
                    quality = None if task["grid"] is not None else _quality_without_grid(task["skip_reason"])
                    db.execute("""INSERT INTO jobs
                        (task_id,audio_sha256,state,source_json,grid_json,metadata_json,
                         quality_json,updated_at)
                        VALUES (?,?,?,?,?,?,?,?)""", (
                            task["task_id"], task["audio_sha256"], state,
                            _json(task["source"]), _json(task["grid"]) if task["grid"] else None,
                            _json(task["metadata"]), _json(quality) if quality else None, now))
        with db:
            db.execute("UPDATE jobs SET state='queued',updated_at=? WHERE state='running'", (time.time(),))
            if retry_errors:
                db.execute("UPDATE jobs SET state='queued',error=NULL,updated_at=? WHERE state='error'",
                           (time.time(),))
        attempts = 0
        while limit is None or attempts < limit:
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY task_id LIMIT 1").fetchone()
            if row is None:
                break
            attempts += 1
            with db:
                db.execute("UPDATE jobs SET state='running',attempts=attempts+1,error=NULL,updated_at=? "
                           "WHERE task_id=?", (time.time(), row["task_id"]))
            try:
                source = json.loads(row["source_json"])
                if source is None:
                    raise ValueError("No safe source path is available for this dataset track.")
                arrays, quality = _decode_and_extract(
                    source, roots[source["root_id"]],
                    {"audio_sha256": row["audio_sha256"], "grid": json.loads(row["grid_json"]),
                     "metadata": json.loads(row["metadata_json"])},
                    cache_dir, ffmpeg_path, ffmpeg_version,
                )
                artifact, artifact_hash = _save_artifact(cache_dir, row["task_id"], arrays, quality)
                with db:
                    db.execute("""UPDATE jobs SET state='done',artifact_path=?,artifact_sha256=?,
                        quality_json=?,error=NULL,updated_at=? WHERE task_id=?""",
                        (str(artifact.relative_to(cache_dir)), artifact_hash, _json(quality),
                         time.time(), row["task_id"]))
            except (OSError, ValueError, RuntimeError) as error:
                with db:
                    db.execute("UPDATE jobs SET state='error',error=?,updated_at=? WHERE task_id=?",
                               (str(error)[:1000], time.time(), row["task_id"]))
            if on_progress:
                progress = _summary(db, cache_dir, ffmpeg_version)
                progress["attempted_this_run"] = attempts
                on_progress(progress)
        summary = _summary(db, cache_dir, ffmpeg_version)
        summary["attempted_this_run"] = attempts
        return summary


def cache_status(cache_dir):
    cache_dir = _cache_path(cache_dir)
    if not (cache_dir / "feature-cache.sqlite3").is_file():
        raise ValueError("Feature cache does not exist.")
    with _cache_database(cache_dir) as db:
        metadata = dict(db.execute("SELECT key,value FROM cache_meta"))
        return _summary(db, cache_dir, metadata.get("ffmpeg_version"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Dedicated AutoCue inventory SQLite file.")
    parser.add_argument("--cache-dir", required=True, help="New local cache directory outside audio source roots.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Build or resume versioned bar-aligned feature artifacts.")
    run.add_argument("--dataset", required=True, help="Frozen JSON dataset manifest from dataset_splits.py.")
    run.add_argument("--grids", help="Optional JSON map from audio SHA-256 to validated grid objects.")
    run.add_argument("--limit", type=int, help="Pause after this many attempted audio tracks.")
    run.add_argument("--retry-errors", action="store_true", help="Retry failed tracks.")
    commands.add_parser("status", help="Report durable per-track feature-cache status.")
    args = parser.parse_args(argv)
    if getattr(args, "limit", None) is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
    try:
        if args.command == "status":
            result = cache_status(args.cache_dir)
        else:
            result = run_cache(args.database, args.cache_dir, args.dataset, grid_path=args.grids,
                               limit=args.limit, retry_errors=args.retry_errors,
                               on_progress=lambda progress: print(json.dumps(progress, allow_nan=False),
                                                                 flush=True))
        print(json.dumps(result, allow_nan=False))
        return 2 if result["counts"].get("error") else 0
    except KeyboardInterrupt:
        print("Feature extraction interrupted; run the same command to resume.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print("Feature cache failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
