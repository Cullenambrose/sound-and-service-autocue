#!/usr/bin/env python3
"""Compare audio-only event candidates with candidates augmented by unverified imported cues."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import tempfile
import zipfile

import feature_cache
import library_inventory as inventory
from timing import bar_to_time

DETECTOR_VERSION = "anchor-candidates-v1"
AUDIO_SCORE_THRESHOLD = 1.3
MIN_BAR_COVERAGE = 0.95
HUMAN_CORRECTION_TOLERANCE_BEATS = 1.0


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number.")
    return float(value)


def _audio_scores(features, quality):
    import numpy as np

    if not isinstance(quality, dict):
        raise ValueError("Feature artifact quality metadata must be an object.")
    indices = np.asarray(features["bar_index"])
    coverage = np.asarray(features["coverage"], dtype=float)
    rms = np.asarray(features["rms"], dtype=float)
    low = np.asarray(features["low_energy"], dtype=float)
    if any(array.ndim != 1 for array in (indices, coverage, rms, low)) \
            or len({len(indices), len(coverage), len(rms), len(low)}) != 1:
        raise ValueError("Feature artifact has inconsistent per-bar arrays.")
    if len(indices) == 0 or any(not np.all(np.isfinite(array)) for array in (coverage, rms, low)):
        raise ValueError("Feature artifact has empty or non-finite bar features.")
    if np.any(coverage < 0) or np.any(coverage > 1) or np.any(rms < 0) or np.any(low < 0):
        raise ValueError("Feature artifact contains invalid coverage or energy values.")
    if any(right <= left for left, right in zip(indices, indices[1:])):
        raise ValueError("Feature artifact bar indices are not strictly increasing.")
    bars = {
        int(index): {"coverage": float(coverage[position]), "rms": float(rms[position]),
                     "low": float(low[position])}
        for position, index in enumerate(indices)
    }
    threshold = quality.get("grid_confidence")
    origin = _finite(quality.get("grid_origin_s"), "Feature grid origin")
    bar_s = _finite(quality.get("bar_duration_s"), "Feature bar duration")
    duration = _finite(quality.get("audio_duration_s"), "Feature audio duration")
    if bar_s <= 0 or duration <= 0:
        raise ValueError("Feature artifact has invalid grid or audio duration.")

    def score_at(bar):
        window = [bars.get(index) for index in (*range(bar - 4, bar), bar, bar + 1)]
        if any(row is None or row["coverage"] < MIN_BAR_COVERAGE for row in window):
            return None
        before, after = window[:4], window[4:]
        rms_before = sorted(row["rms"] for row in before)[2]
        low_before = sorted(row["low"] for row in before)[2]
        if rms_before <= 1e-7 or low_before <= 1e-7:
            return None
        rms_ratio = sum(row["rms"] for row in after) / len(after) / rms_before
        low_ratio = sum(row["low"] for row in after) / len(after) / low_before
        score = 0.6 * rms_ratio + 0.4 * low_ratio
        return {
            "score": score, "rms_ratio": rms_ratio, "low_ratio": low_ratio,
            "grid_confidence": threshold,
        }

    return bars, origin, bar_s, duration, score_at


def _audio_candidates(bars, origin, bar_s, score_at):
    candidates = []
    for bar in sorted(bars):
        evidence = score_at(bar)
        if evidence is None or evidence["score"] < AUDIO_SCORE_THRESHOLD:
            continue
        candidates.append({
            "bar": bar, "time_s": origin + bar * bar_s,
            "source": "audio_energy_change",
            "audio_evidence": evidence,
        })
    return candidates


def _union_candidates(audio_candidates, anchor_candidates):
    merged = {}
    for candidate in sorted(audio_candidates + anchor_candidates, key=lambda point: point["time_s"]):
        key = round(candidate["time_s"], 6)
        existing = merged.get(key)
        if existing is None:
            existing = dict(candidate)
            existing["sources"] = [candidate["source"]]
            if "marker" in existing:
                existing["markers"] = [existing.pop("marker")]
            merged[key] = existing
            continue
        source = candidate["source"]
        if source not in existing["sources"]:
            existing["sources"].append(source)
        existing["source"] = (
            "audio_and_imported_anchor"
            if {"audio_energy_change", "imported_anchor"}.issubset(existing["sources"])
            else existing["sources"][0]
        )
        if "marker" in candidate:
            existing.setdefault("markers", []).append(candidate["marker"])
        if candidate.get("audio_evidence") is not None:
            existing["audio_evidence"] = candidate["audio_evidence"]
        existing["bar"] = min(existing["bar"], candidate["bar"])
    return list(merged.values())


def _anchor_candidates(metadata, origin, bar_s, duration, score_at):
    anchors = metadata.get("imported_anchors", {})
    sources = anchors.get("sources", []) if isinstance(anchors, dict) else []
    candidates = []
    ignored = []
    for source in sources:
        if not isinstance(source, dict) or source.get("format") != "serato_markers2_v1.1":
            continue
        if source.get("status") != "parsed":
            ignored.append({"field": source.get("field"), "reason": source.get("error") or source.get("status")})
            continue
        for record in source.get("records", []):
            if not isinstance(record, dict) or record.get("type") != "CUE":
                continue
            marker_time = record.get("time_s")
            if marker_time is None:
                ignored.append({"field": source.get("field"), "index": record.get("index"),
                                "reason": "unset cue position"})
                continue
            if type(marker_time) not in (int, float) or not math.isfinite(marker_time) \
                    or not 0 <= marker_time <= duration + 0.05:
                ignored.append({"field": source.get("field"), "index": record.get("index"),
                                "reason": "cue position is invalid or outside audio"})
                continue
            bar_position = (float(marker_time) - origin) / bar_s
            bar = math.floor(bar_position + 0.5)
            grid_time = origin + bar * bar_s
            audio_evidence = score_at(bar)
            candidates.append({
                "bar": bar, "time_s": float(marker_time), "grid_time_s": grid_time,
                "grid_distance_s": float(marker_time) - grid_time,
                "source": "imported_anchor",
                "marker": {
                    "field": source.get("field"), "format": source.get("format"),
                    "source_sha256": source.get("sha256"), "occurrence": source.get("occurrence"),
                    "index": record.get("index"), "name": record.get("name"),
                    "producer": source.get("producer", "unknown"),
                    "human_verified": False, "role": "unverified_imported_candidate",
                },
                "audio_evidence": audio_evidence,
            })
    return candidates, ignored


def _read_artifact(cache_dir, row, expected_hash):
    import numpy as np

    relative = PurePosixPath(row["artifact_path"] or "")
    if relative.is_absolute() or not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("Feature cache contains an unsafe artifact path.")
    artifact = cache_dir.joinpath(*relative.parts)
    if artifact.is_symlink() or not artifact.is_file() or cache_dir not in artifact.resolve().parents:
        raise ValueError("Feature cache artifact is missing or outside its cache directory.")
    if feature_cache._sha256_file(artifact) != row["artifact_sha256"]:
        raise ValueError("Feature cache artifact hash does not match its checkpoint.")
    try:
        with np.load(artifact, allow_pickle=False) as source:
            quality = json.loads(str(source["quality_json"]))
            arrays = {name: source[name].copy() for name in (
                "bar_index", "coverage", "rms", "low_energy")}
    except (EOFError, KeyError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise ValueError("Feature cache artifact is unreadable or incomplete.") from error
    if not isinstance(quality, dict):
        raise ValueError("Feature artifact quality metadata must be an object.")
    if quality.get("source_audio_sha256") != expected_hash:
        raise ValueError("Feature artifact belongs to a different audio file.")
    if quality.get("extractor_version") != feature_cache.EXTRACTOR_VERSION:
        raise ValueError("Feature artifact uses an unsupported extractor version.")
    return arrays, quality


def _time_from_prediction(cue, variant, fallback_grid):
    if not isinstance(cue, dict) or cue.get("bar") is None:
        return None
    time_s = cue.get("time_s")
    if time_s is not None:
        return _finite(time_s, "Baseline prediction time")
    if not isinstance(fallback_grid, dict):
        raise ValueError("Saved baseline has no usable timing grid.")
    grid = variant.get("grid") or {
        "t1": variant.get("t1", fallback_grid.get("t1")),
        "bar_s": variant.get("bar_s", fallback_grid.get("bar_s")),
        "offset_ms": variant.get("offset_ms", fallback_grid.get("offset_ms", 0)),
    }
    if not isinstance(grid, dict):
        raise ValueError("Saved baseline has a malformed timing grid.")
    return bar_to_time(_finite(cue["bar"], "Baseline prediction bar"), grid)


def _review_inputs(review_path):
    if review_path is None:
        return {}, {}
    try:
        document = json.loads(Path(review_path).expanduser().read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as error:
        raise ValueError("Review export is not valid JSON.") from error
    if not isinstance(document, dict) or document.get("schema_version") != 2:
        raise ValueError("Review export must use schema_version 2.")
    answer_rows = document.get("answer_keys")
    track_rows = document.get("tracks")
    if not isinstance(answer_rows, list) or not isinstance(track_rows, list):
        raise ValueError("Review export must contain answer_keys and tracks arrays.")
    answers = {}
    for item in answer_rows:
        if not isinstance(item, dict):
            raise ValueError("Review export contains a malformed answer key.")
        digest = item.get("hash")
        answer = item.get("answer")
        if not isinstance(digest, str) or len(digest) != 64 or not isinstance(answer, dict):
            raise ValueError("Review answer key is missing its audio hash or answer.")
        if any(character not in "0123456789abcdef" for character in digest.lower()):
            raise ValueError("Review answer key has an invalid audio SHA-256.")
        digest = digest.lower()
        if answer.get("source") != "human_review":
            continue
        if digest in answers:
            raise ValueError("Review export contains duplicate answer keys.")
        answers[digest] = answer
    tracks = {}
    for item in track_rows:
        if not isinstance(item, dict) or not isinstance(item.get("hash"), str):
            continue
        digest = item["hash"].lower()
        if len(digest) == 64 and all(character in "0123456789abcdef" for character in digest):
            tracks[digest] = item
    return answers, tracks


def _review_targets(answer, track, candidates, tolerance_beats):
    if answer is None:
        return []
    cues = answer.get("cues")
    grid = answer.get("grid")
    if not isinstance(cues, dict) or not isinstance(grid, dict):
        raise ValueError("Human answer key must include cue positions and a review grid.")
    beat_s = (bar_to_time(1, grid) - bar_to_time(0, grid)) / 4
    if beat_s <= 0:
        raise ValueError("Human answer key has an invalid review grid.")
    tolerance_s = beat_s * tolerance_beats
    plan = (track or {}).get("plan", {}) if isinstance(track, dict) else {}
    if not isinstance(plan, dict):
        plan = {}
    variants = plan.get("variants", {})
    baseline = variants.get("baseline") if isinstance(variants, dict) else None
    if not isinstance(baseline, dict):
        baseline = None
    baseline_cues = baseline.get("cues", {}) if baseline else {}
    plan_grid = plan.get("grid", {})
    targets = []
    for slot, cue in cues.items():
        if not isinstance(cue, dict) or "bar" not in cue or cue["bar"] is None:
            continue
        target_time = bar_to_time(_finite(cue["bar"], "Reviewed cue bar"), grid)
        nearest = lambda points: min(
            (abs(point["time_s"] - target_time) for point in points),
            default=None,
        )
        audio_error = nearest(candidates["audio_only"])
        assisted_error = nearest(candidates["anchor_assisted"])
        baseline_cue = baseline_cues.get(slot) if isinstance(baseline_cues, dict) else None
        baseline_time = _time_from_prediction(baseline_cue, baseline or {}, plan_grid)
        baseline_meaning = baseline_cue.get("meaning") if isinstance(baseline_cue, dict) else None
        target_meaning = cue.get("meaning")
        corrected = None if baseline is None else (
            baseline_time is None
            or (target_meaning and baseline_meaning and target_meaning != baseline_meaning)
            or abs(baseline_time - target_time) > beat_s * HUMAN_CORRECTION_TOLERANCE_BEATS
        )
        targets.append({
            "slot": str(slot), "meaning": target_meaning,
            "time_s": target_time, "baseline_corrected": bool(corrected) if corrected is not None else None,
            "audio_only_covered": audio_error is not None and audio_error <= tolerance_s,
            "anchor_assisted_covered": assisted_error is not None and assisted_error <= tolerance_s,
            "audio_only_error_s": audio_error, "anchor_assisted_error_s": assisted_error,
            "tolerance_s": tolerance_s,
        })
    return targets


def _metrics(rows, variant):
    targets = [target for row in rows for target in row["review_targets"]]
    assessed = [target for target in targets if target["baseline_corrected"] is not None]
    corrected = [target for target in assessed if target["baseline_corrected"]]
    covered_key = variant + "_covered"
    covered = sum(target[covered_key] for target in targets)
    corrected_covered = sum(target[covered_key] for target in corrected)
    by_slot = {}
    for target in targets:
        item = by_slot.setdefault(target["slot"], {"covered": 0, "total": 0,
                                                   "correction_assessable": 0,
                                                   "corrected_covered": 0, "corrected_total": 0})
        item["total"] += 1
        item["covered"] += int(target[covered_key])
        if target["baseline_corrected"] is not None:
            item["correction_assessable"] += 1
        if target["baseline_corrected"]:
            item["corrected_total"] += 1
            item["corrected_covered"] += int(target[covered_key])
    return {
        "positioned_review_targets": len(targets), "candidates_within_tolerance": covered,
        "candidate_recall": round(covered / len(targets), 4) if targets else None,
        "baseline_comparable_targets": len(assessed),
        "correction_status_unknown_targets": len(targets) - len(assessed),
        "review_corrected_targets": len(corrected), "corrected_targets_covered": corrected_covered,
        "corrected_target_recall": round(corrected_covered / len(corrected), 4) if corrected else None,
        "by_slot": by_slot,
    }


def _load_feature_cache(database, dataset_path, cache_dir):
    cache_dir = Path(cache_dir).expanduser().resolve()
    with feature_cache._cache_database(cache_dir) as db:
        meta = dict(db.execute("SELECT key,value FROM cache_meta"))
        if not meta or not meta.get("run_signature"):
            raise ValueError("Feature cache has no completed run configuration.")
        grid_path = meta.get("grid_file") or None
        tasks, _, source_roots, signature = feature_cache._load_inputs(
            database, dataset_path, grid_path)
        expected_signature = hashlib.sha256(
            (signature + "\0" + meta.get("ffmpeg_version", "")).encode("utf-8")).hexdigest()
        if expected_signature != meta["run_signature"]:
            raise ValueError("Dataset or grid inputs no longer match the feature cache.")
        jobs = {row["task_id"]: dict(row) for row in db.execute("SELECT * FROM jobs")}
        tracks = []
        for task in tasks:
            row = jobs.get(task["task_id"])
            if row is None:
                raise ValueError("Feature cache is missing a task from the frozen dataset.")
            tracks.append((task, row))
        return cache_dir, tracks, source_roots, meta.get("grid_file") or None


def run_detector(database, dataset_path, cache_dir, *, review_path=None, detector="both",
                 tolerance_beats=4.0, output_path=None):
    if detector not in {"audio-only", "anchor-assisted", "both"}:
        raise ValueError("Detector must be audio-only, anchor-assisted, or both.")
    tolerance_beats = _finite(tolerance_beats, "Review tolerance in beats")
    if tolerance_beats <= 0 or tolerance_beats > 64:
        raise ValueError("Review tolerance must be greater than 0 and no more than 64 beats.")
    cache_dir = Path(cache_dir).expanduser().resolve()
    cache_dir, cached, root_paths, grid_path = _load_feature_cache(database, dataset_path, cache_dir)
    if output_path:
        output = Path(output_path).expanduser()
        if output.is_symlink():
            raise ValueError("Detector report output must not be a symlink.")
        output = output.resolve()
        if output.exists() and output.is_dir():
            raise ValueError("Detector report output must be a file path.")
        protected_inputs = [Path(dataset_path).expanduser().resolve(),
                            inventory._database_path(database)]
        if review_path:
            protected_inputs.append(Path(review_path).expanduser().resolve())
        if grid_path:
            protected_inputs.append(Path(grid_path).expanduser().resolve())
        if output in protected_inputs:
            raise ValueError("Detector report must not overwrite one of its inputs.")
        for root in root_paths:
            if output == root or root in output.parents:
                raise ValueError("Detector report must be outside every audio source root.")
        if output == cache_dir or cache_dir in output.parents:
            raise ValueError("Detector report must be outside the feature cache.")
        if output == inventory._database_path(database) or output in inventory._database_path(database).parents:
            raise ValueError("Detector report must not overwrite or contain the inventory database.")
    else:
        output = None

    tasks = [task for task, _ in cached]
    cache_jobs = {task["task_id"]: row for task, row in cached}
    tasks_by_hash = {task["audio_sha256"]: (task, cache_jobs[task["task_id"]]) for task in tasks}
    answers, review_tracks = _review_inputs(review_path)
    if detector == "audio-only":
        selected = {"audio_only"}
    elif detector == "anchor-assisted":
        selected = {"anchor_assisted"}
    else:
        selected = {"audio_only", "anchor_assisted"}

    rows = []
    for digest, (task, job) in sorted(tasks_by_hash.items()):
        row = {"audio_sha256": digest, "status": job["state"], "imported_anchor_count": 0,
               "ignored_anchors": [], "candidate_counts": {}, "candidates": {},
               "review_targets": []}
        if job["state"] != "done":
            quality = json.loads(job["quality_json"] or "{}")
            row["detail"] = job.get("error") or ", ".join(quality.get("flags", ["feature cache not done"]))
            rows.append(row)
            continue
        arrays, quality = _read_artifact(cache_dir, job, digest)
        bars, origin, bar_s, duration, score_at = _audio_scores(arrays, quality)
        audio = _audio_candidates(bars, origin, bar_s, score_at)
        anchors, ignored = _anchor_candidates(task["metadata"], origin, bar_s, duration, score_at)
        assisted = _union_candidates(audio, anchors)
        row["status"] = "done"
        row["grid_quality"] = quality
        row["imported_anchor_count"] = len(anchors)
        row["ignored_anchors"] = ignored[:20]
        row["ignored_anchors_truncated"] = len(ignored) > 20
        all_sets = {"audio_only": audio, "anchor_assisted": assisted}
        row["candidate_counts"] = {name: len(all_sets[name]) for name in selected}
        row["candidates"] = {name: all_sets[name] for name in selected}
        targets = _review_targets(answers.get(digest), review_tracks.get(digest),
                                  all_sets, tolerance_beats)
        row["review_targets"] = targets
        rows.append(row)

    result = {
        "schema_version": 1, "detector_version": DETECTOR_VERSION, "selected_detectors": sorted(selected),
        "audio_threshold": AUDIO_SCORE_THRESHOLD, "review_tolerance_beats": tolerance_beats,
        "evaluation_policy": (
            "Imported cue positions are untyped candidate hints, never event labels or targets. "
            "Recall uses only explicit human_review answer keys. Whether those human targets were "
            "independently created from imported tags is unknown; this is not held-out accuracy."
        ),
        "counts": {
            "dataset_tracks": len(rows),
            "feature_ready_tracks": sum(row["status"] == "done" for row in rows),
            "tracks_with_imported_anchors": sum(row["imported_anchor_count"] > 0 for row in rows),
            "imported_anchor_candidates": sum(row["imported_anchor_count"] for row in rows),
            "matched_human_review_tracks": sum(task["audio_sha256"] in answers for task in tasks),
        },
        "review_comparison": {
            name: _metrics(rows, name) for name in sorted(selected)
        } if answers else None,
        "incremental_correction_coverage": None,
        "tracks": rows,
    }
    if answers and "audio_only" in selected and "anchor_assisted" in selected:
        audio_targets = {(row["audio_sha256"], target["slot"]): target
                         for row in rows for target in row["review_targets"]}
        assisted_targets = {(row["audio_sha256"], target["slot"]): target
                            for row in rows for target in row["review_targets"]}
        corrections = [target for key, target in audio_targets.items()
                       if target["baseline_corrected"] is True and key in assisted_targets]
        assessed_targets = [target for target in audio_targets.values()
                            if target["baseline_corrected"] is not None]
        newly_covered = sum(target["anchor_assisted_covered"] and not target["audio_only_covered"]
                            for target in corrections)
        result["incremental_correction_coverage"] = {
            "baseline_comparable_targets": len(assessed_targets),
            "review_corrected_targets": len(corrections),
            "newly_covered_by_imported_anchor_candidates": newly_covered,
        }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".anchor-detector-", dir=output.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                json.dump(result, destination, ensure_ascii=True, allow_nan=False, indent=2)
                destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary_name, output)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        result["report_path"] = str(output)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Inventory database used for the frozen dataset.")
    parser.add_argument("--dataset", required=True, help="Frozen dataset JSON manifest.")
    parser.add_argument("--cache-dir", required=True, help="Completed B04 feature cache directory.")
    parser.add_argument("--reviews", help="Optional schema-2 AutoCue experiments export with human answer keys.")
    parser.add_argument("--detector", choices=("audio-only", "anchor-assisted", "both"), default="both")
    parser.add_argument("--tolerance-beats", type=float, default=4.0,
                        help="Review-target candidate recall tolerance, in beats (default: one bar).")
    parser.add_argument("--out", help="Optional JSON report path outside the source roots and feature cache.")
    args = parser.parse_args(argv)
    try:
        result = run_detector(args.database, args.dataset, args.cache_dir, review_path=args.reviews,
                              detector=args.detector, tolerance_beats=args.tolerance_beats,
                              output_path=args.out)
        print(json.dumps({key: value for key, value in result.items() if key != "tracks"}, allow_nan=False))
        return 2 if any(row["status"] == "error" for row in result["tracks"]) else 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print("Anchor detector failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
