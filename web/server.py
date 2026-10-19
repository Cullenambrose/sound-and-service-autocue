#!/usr/bin/env python3
"""Local-only library and review server for AutoCue."""
import hashlib
import importlib.util
import json
import mimetypes
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import zipfile
import base64
import glob
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent
DATA = Path(os.environ.get("AUTOCUE_WEB_DATA", ROOT / "web-data")).expanduser().resolve()
UPLOADS = DATA / "audio"
DB_PATH = DATA / "library.sqlite3"
ALLOWED = {".wav", ".mp3", ".m4a", ".flac", ".aiff", ".aif", ".ogg", ".opus"}
SLOT_NAMES = {
    "A": "INTRO", "B": "START", "C": "CHORUS", "D": "LAST CHORUS",
    "E": "EXIT/JUMP", "F": "JUMP IN", "G": "GO -32", "H": "OUTRO",
}
COLORS = {
    "A": "#38bdf8", "B": "#22c55e", "C": "#a78bfa", "D": "#f472b6",
    "E": "#fb7185", "F": "#facc15", "G": "#2dd4bf", "H": "#f97316",
}
worker_queue = queue.Queue()
paused_jobs = set()
cancelled_jobs = set()
write_lock = threading.Lock()
SESSION_TOKEN = ""


def connect():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize():
    global SESSION_TOKEN
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    token_path = DATA / ".session-token"
    if not token_path.exists():
        token_path.write_text(uuid.uuid4().hex + uuid.uuid4().hex, encoding="ascii")
        token_path.chmod(0o600)
    SESSION_TOKEN = token_path.read_text(encoding="ascii").strip()
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                hash TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                relative_path TEXT NOT NULL DEFAULT '',
                audio_path TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                source_size INTEGER NOT NULL DEFAULT 0,
                source_mtime REAL NOT NULL DEFAULT 0,
                imported_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'Original',
                plan_json TEXT,
                edited INTEGER NOT NULL DEFAULT 0,
                reviewed INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                track_hashes TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS backups (
                id TEXT PRIMARY KEY,
                backup_path TEXT NOT NULL,
                track_hashes TEXT NOT NULL,
                created_at REAL NOT NULL,
                status TEXT NOT NULL,
                manifest_json TEXT NOT NULL
            );
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(tracks)")}
        for name, declaration in (
            ("source_path", "TEXT NOT NULL DEFAULT ''"),
            ("source_size", "INTEGER NOT NULL DEFAULT 0"),
            ("source_mtime", "REAL NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE tracks ADD COLUMN {name} {declaration}")
        db.execute("UPDATE jobs SET status='queued', updated_at=? WHERE status IN ('running','paused')", (time.time(),))
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('gemini', 'false')")
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('strictness', 'standard')")


def track_record(row):
    plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
    cues = plan.get("cues", {})
    return {
        "hash": row["hash"],
        "name": row["name"],
        "relative_path": row["relative_path"],
        "source_available": bool(row["source_path"] and Path(row["source_path"]).is_file()),
        "status": row["status"],
        "edited": bool(row["edited"]),
        "reviewed": bool(row["reviewed"]),
        "error": row["error"],
        "bpm": plan.get("bpm") or plan.get("grid", {}).get("bpm"),
        "map_type": plan.get("map_type", "VOCAL"),
        "genre": plan.get("genre", ""),
        "confidence": _mean_confidence(cues),
        "t1": plan.get("grid", {}).get("t1"),
        "bar_s": plan.get("grid", {}).get("bar_s"),
        "cues": {
            slot: {
                "name": cues.get(slot, {}).get("name") or SLOT_NAMES[slot],
                "bar": cues.get(slot, {}).get("bar"),
                "confidence": cues.get(slot, {}).get("confidence"),
                "reason": cues.get(slot, {}).get("reason", ""),
            } for slot in "ABCDEFGH"
        },
        "url": "/media/" + row["hash"],
    }


def _mean_confidence(cues):
    scores = [cue.get("confidence") for cue in cues.values()
              if isinstance(cue.get("confidence"), (float, int))]
    return round(sum(scores) / len(scores), 2) if scores else None


def crate_roots():
    roots = [Path.home() / "Music" / "_Serato_" / "Subcrates"]
    roots.extend(Path(path) / "_Serato_" / "Subcrates" for path in glob.glob("/Volumes/*"))
    return [path.resolve() for path in roots if path.is_dir()]


def _is_known_crate(path):
    path = path.resolve()
    if DATA / "crates" in path.parents and path.is_file() and path.suffix.lower() == ".crate":
        return True
    return any(root in path.parents and path.is_file() and path.suffix.lower() == ".crate"
               for root in crate_roots())


def crate_songs(crate_path):
    data = Path(crate_path).read_bytes()
    paths, seen = [], set()
    offset = 0
    tags = (b"pfil", b"ptrk", b"path")
    while offset < len(data):
        candidates = [(data.find(tag, offset), tag) for tag in tags]
        candidates = [(position, tag) for position, tag in candidates if position >= 0]
        if not candidates:
            break
        marker, _tag = min(candidates, key=lambda item: item[0])
        offset = marker + 4
        if offset + 4 > len(data):
            continue
        size = int.from_bytes(data[offset:offset + 4], "big")
        start, end = offset + 4, offset + 4 + size
        if size <= 0 or end > len(data):
            continue
        raw = data[start:end].strip(b"\x00\r\n\t ")
        value = ""
        for encoding in (("utf-8", "utf-16-le", "utf-16-be", "mac_roman")
                         if b"\x00" in raw else ("utf-8", "mac_roman")):
            try:
                value = raw.decode(encoding).replace("\x00", "").strip()
            except UnicodeDecodeError:
                continue
            if value:
                break
        if value.startswith("file://"):
            value = urllib.parse.unquote(urllib.parse.urlparse(value).path)
        value = value.rstrip("\r\n")
        if value and value not in seen and Path(value).suffix.lower() in ALLOWED:
            seen.add(value)
            path = Path(value).expanduser()
            exists = path.is_file()
            paths.append({"path": str(path), "name": path.name, "missing": not exists})
        offset = max(offset, end)
    return paths


def list_crates():
    found = []
    with connect() as db:
        known = {row["source_path"]: row["status"] for row in
                 db.execute("SELECT source_path,status FROM tracks WHERE source_path!=''")}
    candidates = []
    for root in crate_roots():
        candidates.extend(root.rglob("*.crate"))
    upload_dir = DATA / "crates"
    if upload_dir.is_dir():
        candidates.extend(upload_dir.glob("*.crate"))
    for crate in sorted(set(candidates)):
        try:
            songs = crate_songs(crate)
        except OSError:
            continue
        present = [song for song in songs if not song["missing"]]
        done = sum(known.get(song["path"]) in ("Cues ready", "Needs a look", "In Serato", "Edited by you")
                   for song in present)
        try:
            root = next(root for root in crate_roots() if root in crate.parents)
            label = str(crate.relative_to(root))
        except (ValueError, StopIteration):
            label = crate.name
        found.append({
            "name": label.removesuffix(".crate"),
            "path": str(crate),
            "songs": len(songs),
            "done": done,
            "missing": sum(song["missing"] for song in songs),
            "drive": crate.parts[2] if len(crate.parts) > 2 and crate.parts[1] == "Volumes" else "LOCAL",
        })
    return found


def _serato_dirs():
    candidates = [Path.home() / "Music" / "_Serato_"]
    candidates.extend(Path(path) / "_Serato_" for path in glob.glob("/Volumes/*"))
    return [path.resolve() for path in candidates if path.is_dir()]


def _serato_open():
    if sys.platform != "darwin":
        return False
    for process_name in ("Serato", "Serato DJ Pro", "Serato DJ Lite"):
        try:
            result = subprocess.run(["pgrep", "-x", process_name], capture_output=True, check=False)
        except OSError as error:
            raise RuntimeError("Could not check whether Serato is open: " + str(error)) from error
        if result.returncode == 0:
            return True
    return False


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_entry_sha256(archive, name):
    digest = hashlib.sha256()
    with archive.open(name, "r") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _marker2_entries(data):
    if not data:
        return []
    encoded = data[2:].replace(b"\n", b"").split(b"\x00", 1)[0]
    encoded += b"=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded, validate=False)
    except ValueError as error:
        raise ValueError("Could not decode the existing Serato Markers2 tag.") from error
    if not decoded.startswith(b"\x01\x01"):
        raise ValueError("Existing Serato Markers2 tag has an unsupported header.")
    entries, offset = [], 2
    while offset < len(decoded):
        if decoded[offset:] == b"\x00" or not decoded[offset:].strip(b"\x00"):
            break
        end_name = decoded.find(b"\x00", offset)
        if end_name < 0 or end_name + 5 > len(decoded):
            raise ValueError("Existing Serato Markers2 entry is malformed; refusing to overwrite it.")
        try:
            name = decoded[offset:end_name].decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("Existing Serato Markers2 entry has an unknown encoding.") from error
        length = int.from_bytes(decoded[end_name + 1:end_name + 5], "big")
        end = end_name + 5 + length
        if end > len(decoded):
            raise ValueError("Existing Serato Markers2 entry length is invalid.")
        payload = decoded[end_name + 5:end]
        entries.append((name, payload))
        offset = end
    return entries


def _cue_binary(cues, plan, existing_data=None):
    entries = _marker2_entries(existing_data)
    entries = [(name, payload) for name, payload in entries
               if name != "CUE" or len(payload) < 2 or payload[1] not in range(8)]
    t1 = float(plan.get("grid", {}).get("t1", 0))
    bar_s = float(plan.get("grid", {}).get("bar_s", 0))
    if not math.isfinite(t1) or not math.isfinite(bar_s) or bar_s <= 0:
        raise ValueError("A valid v6 beat grid is required before writing cues.")
    for index, slot in enumerate("ABCDEFGH"):
        cue = cues.get(slot, {})
        bar = cue.get("bar")
        if bar is None:
            continue
        bar_position = float(bar)
        if not math.isfinite(bar_position):
            raise ValueError(f"Cue {slot} has a non-finite bar position.")
        position = max(0, int(round((t1 + bar_position * bar_s) * 1000)))
        if position >= 0xFFFFFFFF:
            raise ValueError(f"Cue {slot} exceeds Serato's supported timestamp.")
        color = tuple(int(SLOT_HEX[index][offset:offset + 2], 16) for offset in (1, 3, 5))
        label = str(cue.get("name") or SLOT_NAMES[slot]).encode("utf-8")[:120] + b"\x00"
        payload = (bytes((0, index)) + position.to_bytes(4, "big") + bytes((0, *color, 0, 0)) + label)
        entries.append(("CUE", payload))
    raw = bytearray(b"\x01\x01")
    for name, payload in entries:
        raw.extend(name.encode("ascii") + b"\x00" + len(payload).to_bytes(4, "big") + payload)
    return b"\x01\x01" + base64.b64encode(bytes(raw)) + b"\x00"


SLOT_HEX = ["#38bdf8", "#22c55e", "#a78bfa", "#f472b6", "#fb7185", "#facc15", "#2dd4bf", "#f97316"]


def _write_markers2(path, cues, plan):
    try:
        from mutagen.id3 import ID3, GEOB, ID3NoHeaderError
    except ImportError as error:
        raise RuntimeError("Serato writing needs the Mutagen package. Install the repo's documented audio dependencies.") from error
    original_hash = _sha256(path)
    audio_hash = _mp3_audio_hash(path)
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.autocue.mp3")
    try:
        shutil.copy2(path, temporary)
        try:
            tags = ID3(temporary)
        except ID3NoHeaderError:
            tags = ID3()
        legacy = tags.getall("GEOB:Serato Markers_")
        if legacy:
            raise RuntimeError(f"{Path(path).name} uses legacy Serato Markers_ cues; writing is blocked to avoid a conflicting cue set.")
        frames = tags.getall("GEOB:Serato Markers2")
        if len(frames) > 1:
            raise RuntimeError(f"{Path(path).name} has multiple Serato Markers2 frames; writing is blocked.")
        existing_data = frames[0].data if frames else None
        marker_data = _cue_binary(cues, plan, existing_data)
        tags.delall("GEOB:Serato Markers2")
        tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2",
                      data=marker_data))
        tags.save(temporary, v2_version=3)
        saved = ID3(temporary).getall("GEOB:Serato Markers2")
        if len(saved) != 1 or saved[0].data != marker_data:
            raise RuntimeError(f"Could not verify the written Serato cue tag in {Path(path).name}.")
        if _mp3_audio_hash(temporary) != audio_hash:
            raise RuntimeError(f"Audio frames changed while writing Serato cues to {Path(path).name}.")
        if _sha256(path) != original_hash:
            raise RuntimeError(f"{Path(path).name} changed during the write; no changes were applied.")
        written_hash = _sha256(temporary)
        os.replace(temporary, path)
        return written_hash
    finally:
        temporary.unlink(missing_ok=True)


def _mp3_audio_hash(path):
    size = Path(path).stat().st_size
    with open(path, "rb") as source:
        header = source.read(10)
    start = 0
    if len(header) == 10 and header[:3] == b"ID3":
        tag_size = sum((header[index] & 0x7f) << shift
                       for index, shift in zip(range(6, 10), (21, 14, 7, 0)))
        start = 10 + tag_size + (10 if header[5] & 0x10 else 0)
    end = size
    if size >= 128:
        with open(path, "rb") as source:
            source.seek(-128, os.SEEK_END)
            if source.read(3) == b"TAG":
                end -= 128
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        source.seek(start)
        remaining = max(0, end - start)
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def _validate_mp3(path):
    try:
        from mutagen.mp3 import MP3
        audio = MP3(path)
    except ImportError as error:
        raise RuntimeError("Serato writing needs the Mutagen package to validate MP3 audio.") from error
    except Exception as error:
        raise ValueError(f"{Path(path).name} is not a readable MP3: {error}") from error
    if audio.info is None:
        raise ValueError(f"{Path(path).name} has no readable MPEG audio stream.")


def _read_serato_geob(path):
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
    except ImportError:
        raise RuntimeError("Serato backup needs the Mutagen package to read original cue tags.")
    try:
        return [{"desc": frame.desc, "data_b64": base64.b64encode(frame.data).decode("ascii")}
                for frame in ID3(path).getall("GEOB")
                if frame.desc in ("Serato Markers2", "Serato Markers_")]
    except ID3NoHeaderError:
        return []
    except Exception as error:
        raise RuntimeError(f"Could not safely read Serato tag data from {Path(path).name}: {error}") from error


def _zip_tree(archive, directory, prefix):
    base = directory
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            archive.write(path, arcname=str(Path(prefix) / path.relative_to(base)))


def _create_backup(tracks):
    roots = _serato_dirs()
    if not roots:
        raise RuntimeError("No _Serato_ library folder was found in ~/Music or at a mounted drive root. No cues were written.")
    backup_root = Path.home() / "Music" / "AutoCue Backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup_path = backup_root / (backup_id + ".zip")
    song_total = sum(_write_path(row).stat().st_size for row in tracks)
    serato_total = sum(path.stat().st_size for root in roots for path in root.rglob("*") if path.is_file() and not path.is_symlink())
    required = song_total + serato_total + 10 * 1024 * 1024
    if shutil.disk_usage(backup_root).free < required:
        raise RuntimeError(f"Backup needs at least {required // (1024 * 1024)} MB free. No cues were written.")
    manifest = {"created_at": time.time(), "tracks": [], "serato_folders": [str(path) for path in roots]}
    temp_path = backup_path.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, root in enumerate(roots):
                _zip_tree(archive, root, f"serato/{index}-{root.parent.name}/{root.name}")
            for row in tracks:
                source = _write_path(row)
                arcname = "audio_originals/" + row["hash"] + source.suffix.lower()
                archive.write(source, arcname=arcname)
                manifest["tracks"].append({
                    "hash": row["hash"], "path": row["source_path"] or str(source),
                    "archive_name": arcname, "original_sha256": _sha256(source),
                    "mode": source.stat().st_mode & 0o777, "mtime_ns": source.stat().st_mtime_ns,
                    "previous_status": row["status"], "previous_reviewed": row["reviewed"],
                    "previous_edited": row["edited"],
                    "post_write_sha256": None, "serato_geob": _read_serato_geob(source),
                })
            archive.writestr("autocue-backup.json", json.dumps(manifest, indent=2))
        with zipfile.ZipFile(temp_path, "r") as archive:
            broken = archive.testzip()
            if broken:
                raise RuntimeError("Backup verification failed on " + broken)
            for entry in manifest["tracks"]:
                digest = _archive_entry_sha256(archive, entry["archive_name"])
                if digest != entry["original_sha256"]:
                    raise RuntimeError("Audio backup hash verification failed.")
            saved_manifest = json.loads(archive.read("autocue-backup.json"))
            if saved_manifest["tracks"] != manifest["tracks"]:
                raise RuntimeError("Backup manifest verification failed.")
        os.replace(temp_path, backup_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        raise
    with connect() as db:
        db.execute("INSERT INTO backups(id,backup_path,track_hashes,created_at,status,manifest_json) VALUES(?,?,?,?,?,?)",
                   (backup_id, str(backup_path), json.dumps([row["hash"] for row in tracks]),
                    time.time(), "verified", json.dumps(manifest)))
    return backup_id, backup_path, manifest


def write_to_serato(hashes):
    hashes = list(dict.fromkeys(hashes))
    if not hashes:
        raise ValueError("Select reviewed tracks to write.")
    if sys.platform != "darwin":
        raise RuntimeError("Serato tag writing is supported only on macOS.")
    if _serato_open():
        raise RuntimeError("Serato is open. Close it before writing cues; no files were changed.")
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM tracks WHERE hash IN (%s)" % ",".join("?" for _ in hashes), hashes
        ).fetchall()
    records = {row["hash"]: row for row in rows}
    if set(records) != set(hashes):
        raise ValueError("A selected track is no longer in the library.")
    tracks = []
    for file_hash in hashes:
        row = records[file_hash]
        path = _write_path(row)
        if path is None or not path.is_file():
            raise ValueError(f"{row['name']}: use a crate or pasted local path to link the original file before writing.")
        if path.suffix.lower() != ".mp3":
            raise ValueError(f"{row['name']}: cue writing currently supports MP3 only.")
        if path.is_symlink():
            raise ValueError(f"{row['name']}: writing through symbolic links is blocked.")
        _validate_mp3(path)
        if _sha256(path) != row["hash"]:
            raise ValueError(f"{row['name']}: original file changed since import. Re-import it before writing.")
        if not row["reviewed"] or not row["plan_json"]:
            raise ValueError(f"{row['name']}: review and save this track before writing.")
        plan = json.loads(row["plan_json"])
        if not any(cue.get("bar") is not None for cue in plan.get("cues", {}).values()):
            raise ValueError(f"{row['name']}: no cue bars are available to write.")
        tracks.append(row)
    backup_id, backup_path, manifest = _create_backup(tracks)
    touched = []
    try:
        for row, entry in zip(tracks, manifest["tracks"]):
            target_path = _write_path(row)
            if _sha256(target_path) != entry["original_sha256"]:
                raise RuntimeError(f"{row['name']} changed after backup. Nothing further will be written.")
            plan = json.loads(row["plan_json"])
            entry["post_write_sha256"] = _write_markers2(target_path, plan["cues"], plan)
            touched.append(entry)
            with connect() as db:
                duplicate = db.execute("SELECT hash FROM tracks WHERE hash=? AND hash!=?",
                                       (entry["post_write_sha256"], row["hash"])).fetchone()
                if duplicate:
                    raise RuntimeError(f"{row['name']} now matches another library hash; refusing to merge two source files.")
                db.execute("UPDATE tracks SET hash=?,status='In Serato' WHERE hash=?",
                           (entry["post_write_sha256"], row["hash"]))
        with connect() as db:
            db.execute("UPDATE backups SET status='written',manifest_json=? WHERE id=?",
                       (json.dumps(manifest), backup_id))
    except Exception as error:
        with zipfile.ZipFile(backup_path, "r") as archive:
            for entry in touched:
                if _sha256(entry["path"]) != entry["post_write_sha256"]:
                    raise RuntimeError(f"{Path(entry['path']).name} changed during rollback; refusing to overwrite newer data.")
                _restore_backup_entry(archive, entry)
        with connect() as db:
            for entry in touched:
                db.execute("UPDATE tracks SET hash=?,status=?,reviewed=?,edited=? WHERE hash=?",
                           (entry["hash"], entry["previous_status"], entry["previous_reviewed"],
                            entry["previous_edited"], entry["post_write_sha256"]))
            db.execute("UPDATE backups SET status='rolled_back',manifest_json=? WHERE id=?",
                       (json.dumps(manifest), backup_id))
        raise RuntimeError(f"Write failed; verified originals were restored. {error}") from error
    return {"backup_id": backup_id, "backup_path": str(backup_path), "written": len(tracks)}


def _write_path(row):
    return Path(row["source_path"]) if row["source_path"] else None


def undo_backup(backup_id):
    with connect() as db:
        backup = db.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    if not backup:
        raise ValueError("Backup not found.")
    manifest = json.loads(backup["manifest_json"])
    entries = manifest.get("tracks", [])
    if not entries or any(not item.get("post_write_sha256") for item in entries):
        raise ValueError("This backup has no completed write batch to undo.")
    with zipfile.ZipFile(backup["backup_path"], "r") as archive:
        for entry in entries:
            target = Path(entry["path"])
            if not target.is_file() or _sha256(target) != entry["post_write_sha256"]:
                raise RuntimeError(f"{target.name} changed after the batch. Undo stopped to avoid overwriting newer edits.")
        for entry in entries:
            _restore_backup_entry(archive, entry)
    with connect() as db:
        for entry in entries:
            db.execute("UPDATE tracks SET hash=?,status=?,reviewed=?,edited=? WHERE hash=?",
                       (entry["hash"], entry["previous_status"], entry["previous_reviewed"],
                        entry["previous_edited"], entry["post_write_sha256"]))
        db.execute("UPDATE backups SET status='undone' WHERE id=?", (backup_id,))


def _restore_backup_entry(archive, entry):
    target = Path(entry["path"])
    temporary = target.with_name("." + target.name + ".autocue-restore")
    try:
        with archive.open(entry["archive_name"], "r") as source, open(temporary, "wb") as destination:
            shutil.copyfileobj(source, destination)
        if _sha256(temporary) != entry["original_sha256"]:
            raise RuntimeError(f"Could not verify restored file {target.name}.")
        os.chmod(temporary, entry.get("mode", target.stat().st_mode & 0o777))
        os.utime(temporary, ns=(entry["mtime_ns"], entry["mtime_ns"]))
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def copy_import(source, name, relative_path="", source_path=""):
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError("Unsupported audio file type: " + (suffix or "(no extension)"))
    digest = hashlib.sha256()
    temporary = UPLOADS / (uuid.uuid4().hex + ".tmp")
    try:
        with open(source, "rb") as incoming, open(temporary, "wb") as staged:
            while True:
                block = incoming.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                staged.write(block)
        file_hash = digest.hexdigest()
        destination = UPLOADS / (file_hash + suffix)
        if destination.exists():
            temporary.unlink()
        else:
            os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    with connect() as db:
        existing = db.execute("SELECT hash FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        if existing:
            if source_path:
                stat = Path(source_path).stat()
                db.execute("UPDATE tracks SET source_path=?,source_size=?,source_mtime=? WHERE hash=?",
                           (source_path, stat.st_size, stat.st_mtime, file_hash))
            return file_hash, False
        stat = Path(source_path).stat() if source_path and Path(source_path).is_file() else None
        try:
            db.execute(
                "INSERT INTO tracks(hash,name,relative_path,audio_path,source_path,source_size,source_mtime,imported_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (file_hash, Path(name).name, relative_path, str(destination), source_path,
                 stat.st_size if stat else 0, stat.st_mtime if stat else 0, time.time()),
            )
        except sqlite3.IntegrityError:
            if source_path and stat:
                db.execute("UPDATE tracks SET source_path=?,source_size=?,source_mtime=? WHERE hash=?",
                           (source_path, stat.st_size, stat.st_mtime, file_hash))
            return file_hash, False
    return file_hash, True


def create_job(hashes, force=False):
    unique = list(dict.fromkeys(hashes))
    if not unique:
        raise ValueError("Select at least one audio file.")
    job_id = uuid.uuid4().hex
    now = time.time()
    with connect() as db:
        rows = db.execute(
            "SELECT hash,status FROM tracks WHERE hash IN (%s)" % ",".join("?" for _ in unique), unique
        ).fetchall()
        known = {row["hash"]: row["status"] for row in rows}
        unknown = [item for item in unique if item not in known]
        if unknown:
            raise ValueError("Some selected tracks are no longer in the library.")
        selected = [item for item in unique if force or known[item] in ("Original", "Needs a look")]
        if not selected:
            return None, 0
        db.execute(
            "INSERT INTO jobs(id,status,track_hashes,created_at,updated_at,message) VALUES(?,?,?,?,?,?)",
            (job_id, "queued", json.dumps(selected), now, now, "Waiting to start"),
        )
        db.executemany("UPDATE tracks SET error=NULL WHERE hash=?", ((item,) for item in selected))
    worker_queue.put(job_id)
    return job_id, len(unique) - len(selected)


def process_track(file_hash):
    with connect() as db:
        row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
    command = os.environ.get("AUTOCUE_PROCESSOR")
    if not command:
        raise RuntimeError(
            "No raw-audio processor is configured. The v6 planner currently consumes cached analysis; "
            "this checkout has no one-file pipeline entry point. The audio copy is safe and unchanged."
        )
    import shlex
    output = DATA / "results" / (file_hash + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    placeholders = {"{input}": row["audio_path"], "{output}": str(output), "{hash}": file_hash}
    argv = [next_value for part in shlex.split(command)
            for next_value in [part.replace("{input}", placeholders["{input}"])
                                  .replace("{output}", placeholders["{output}"])
                                  .replace("{hash}", placeholders["{hash}"])]]
    if not argv:
        raise RuntimeError("AUTOCUE_PROCESSOR is empty.")
    environment = dict(os.environ)
    with connect() as db:
        settings = {item["key"]: item["value"] for item in db.execute("SELECT * FROM settings")}
    environment["AUTOCUE_GEMINI_TIEBREAK"] = settings.get("gemini", "false")
    environment["AUTOCUE_REVIEW_STRICTNESS"] = settings.get("strictness", "standard")
    completed = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                               timeout=6 * 60 * 60, env=environment)
    if completed.returncode:
        message = (completed.stderr or completed.stdout or "processor exited unsuccessfully").strip()
        raise RuntimeError(message[-2000:])
    if not output.is_file():
        raise RuntimeError("Processor completed without creating its requested JSON output.")
    with open(output, encoding="utf-8") as stream:
        plan = json.load(stream)
    if not isinstance(plan.get("cues"), dict):
        raise RuntimeError("Processor output must contain a cues object.")
    threshold = {"relaxed": 0.45, "standard": 0.6, "strict": 0.8}.get(
        settings.get("strictness", "standard"), 0.6)
    low = any((cue.get("confidence") or 0) < threshold for cue in plan["cues"].values())
    with connect() as db:
        db.execute("UPDATE tracks SET plan_json=?, status=?, error=NULL WHERE hash=?",
                   (json.dumps(plan, ensure_ascii=False), "Needs a look" if low else "Cues ready", file_hash))


def worker():
    while True:
        job_id = worker_queue.get()
        try:
            with connect() as db:
                job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not job:
                    continue
                hashes = json.loads(job["track_hashes"])
            done = int(job["completed"])
            _update_job(job_id, status="running", message="Processing locally")
            for index, file_hash in enumerate(hashes):
                if index < done:
                    continue
                while job_id in paused_jobs and job_id not in cancelled_jobs:
                    time.sleep(0.25)
                if job_id in cancelled_jobs:
                    _update_job(job_id, status="cancelled", completed=done, message="Cancelled")
                    break
                try:
                    process_track(file_hash)
                except Exception as error:
                    with connect() as db:
                        db.execute("UPDATE tracks SET error=? WHERE hash=?", (str(error), file_hash))
                done += 1
                _update_job(job_id, completed=done, message=f"{done} of {len(hashes)} processed")
            else:
                _update_job(job_id, status="completed", completed=done, message="Batch finished")
        finally:
            paused_jobs.discard(job_id)
            cancelled_jobs.discard(job_id)
            worker_queue.task_done()


def _update_job(job_id, **values):
    values["updated_at"] = time.time()
    assignments = ", ".join(key + "=?" for key in values)
    with connect() as db:
        db.execute("UPDATE jobs SET " + assignments + " WHERE id=?",
                   (*values.values(), job_id))


def json_response(handler, value, status=200):
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    handler.end_headers()
    handler.wfile.write(payload)


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoCueLocal/1"

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args))

    def _local_request(self):
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost")

    def _valid_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urllib.parse.urlparse(origin)
        return parsed.scheme == "http" and parsed.netloc == self.headers.get("Host", "")

    def _check_mutation(self):
        if not self._local_request() or not self._valid_origin():
            json_response(self, {"error": "Request rejected: local origin required."}, 403)
            return False
        if self.headers.get("X-AutoCue-Token") != SESSION_TOKEN:
            json_response(self, {"error": "Request rejected: local session token missing or invalid."}, 403)
            return False
        return True

    def do_GET(self):
        if not self._local_request():
            return self.send_error(403)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/session":
            if not self._valid_origin():
                return json_response(self, {"error": "Request rejected: local origin required."}, 403)
            return json_response(self, {"token": SESSION_TOKEN})
        if parsed.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            last_payload = None
            try:
                while True:
                    with connect() as db:
                        job = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1").fetchone()
                    payload = json.dumps(dict(job) if job else {})
                    if payload != last_payload:
                        self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                        self.wfile.flush()
                        last_payload = payload
                    else:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                return
        if parsed.path == "/":
            return self._static(WEB / "index.html", "text/html; charset=utf-8")
        if parsed.path == "/app.js":
            return self._static(WEB / "app.js", "text/javascript; charset=utf-8")
        if parsed.path == "/style.css":
            return self._static(WEB / "style.css", "text/css; charset=utf-8")
        if parsed.path == "/api/library":
            with connect() as db:
                rows = db.execute("SELECT * FROM tracks ORDER BY imported_at DESC").fetchall()
            return json_response(self, {"tracks": [track_record(row) for row in rows]})
        if parsed.path == "/api/jobs":
            with connect() as db:
                rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
            return json_response(self, {"jobs": [dict(row) for row in rows]})
        if parsed.path == "/api/settings":
            with connect() as db:
                settings = {row["key"]: row["value"] for row in db.execute("SELECT * FROM settings")}
            return json_response(self, settings)
        if parsed.path == "/api/status":
            return json_response(self, {
                "processor_configured": bool(os.environ.get("AUTOCUE_PROCESSOR")),
                "write_dependency_available": importlib.util.find_spec("mutagen") is not None,
                "write_platform_supported": sys.platform == "darwin",
            })
        if parsed.path == "/api/crates":
            return json_response(self, {"crates": list_crates()})
        if parsed.path == "/api/crates/detail":
            try:
                path = Path(urllib.parse.parse_qs(parsed.query).get("path", [""])[0]).resolve(strict=True)
            except OSError as error:
                return json_response(self, {"error": str(error)}, 400)
            if not _is_known_crate(path):
                return json_response(self, {"error": "Crate is outside known Serato folders."}, 403)
            try:
                songs = crate_songs(path)
            except (OSError, ValueError) as error:
                return json_response(self, {"error": str(error)}, 400)
            return json_response(self, {"songs": songs})
        if parsed.path == "/api/backups":
            with connect() as db:
                rows = db.execute("SELECT id,created_at,status,track_hashes,backup_path FROM backups ORDER BY created_at DESC").fetchall()
            return json_response(self, {"backups": [
                {**dict(row), "track_hashes": json.loads(row["track_hashes"])} for row in rows
            ]})
        if parsed.path.startswith("/media/"):
            file_hash = parsed.path.removeprefix("/media/")
            with connect() as db:
                row = db.execute("SELECT audio_path FROM tracks WHERE hash=?", (file_hash,)).fetchone()
            if not row or not Path(row["audio_path"]).is_file():
                return json_response(self, {"error": "Audio not found"}, 404)
            path = Path(row["audio_path"])
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(path, "rb") as stream:
                shutil.copyfileobj(stream, self.wfile)
            return
        self.send_error(404)

    def do_POST(self):
        if not self._check_mutation():
            return
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path.startswith("/api/finder/"):
                file_hash = parsed.path.rsplit("/", 1)[-1]
                with connect() as db:
                    row = db.execute("SELECT source_path FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                if not row or not row["source_path"] or not Path(row["source_path"]).exists():
                    return json_response(self, {"error": "Original path is unavailable. The staged audio copy remains in the app."}, 404)
                if sys.platform != "darwin":
                    return json_response(self, {"error": "Show in Finder is available only on macOS."}, 501)
                subprocess.Popen(["open", "-R", row["source_path"]])
                return json_response(self, {"ok": True})
            if parsed.path == "/api/import":
                query = urllib.parse.parse_qs(parsed.query)
                name = query.get("name", [""])[0]
                relative = query.get("relative", [""])[0]
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("Uploaded file is empty.")
                temp = UPLOADS / (uuid.uuid4().hex + ".incoming")
                try:
                    remaining = length
                    with open(temp, "wb") as destination:
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("Upload ended before all bytes were received.")
                            destination.write(chunk)
                            remaining -= len(chunk)
                    file_hash, added = copy_import(temp, name, relative)
                finally:
                    temp.unlink(missing_ok=True)
                return json_response(self, {"hash": file_hash, "added": added})
            if parsed.path == "/api/import-path":
                body = self._body_json()
                path = Path(body.get("path", "")).expanduser().resolve(strict=True)
                paths = [path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []
                results, skipped = [], 0
                for item in paths:
                    if not item.is_file() or item.suffix.lower() not in ALLOWED:
                        skipped += 1
                        continue
                    try:
                        file_hash, added = copy_import(item, item.name, str(item.relative_to(path)) if path.is_dir() else item.name, str(item))
                        results.append({"hash": file_hash, "added": added})
                    except OSError:
                        skipped += 1
                return json_response(self, {"items": results, "skipped": skipped})
            if parsed.path == "/api/crates/upload":
                name = urllib.parse.parse_qs(parsed.query).get("name", ["crate.crate"])[0]
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 10 * 1024 * 1024:
                    raise ValueError("Crate file is empty or exceeds 10 MB.")
                crate_dir = DATA / "crates"
                crate_dir.mkdir(parents=True, exist_ok=True)
                crate_path = crate_dir / (uuid.uuid4().hex + ".crate")
                with open(crate_path, "wb") as output:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            crate_path.unlink(missing_ok=True)
                            raise ValueError("Crate upload ended before all bytes were received.")
                        output.write(chunk)
                        remaining -= len(chunk)
                songs = crate_songs(crate_path)
                return json_response(self, {"crate_path": str(crate_path), "songs": len(songs)})
            if parsed.path == "/api/crates/import":
                body = self._body_json()
                crate_path = Path(body.get("crate_path", "")).resolve(strict=True)
                if not _is_known_crate(crate_path):
                    return json_response(self, {"error": "Crate is outside known Serato folders."}, 403)
                allowed_paths = {song["path"] for song in crate_songs(crate_path) if song["path"] and not song["missing"]}
                requested = body.get("paths", [])
                if any(str(Path(item).resolve()) not in allowed_paths for item in requested):
                    raise ValueError("One or more selected paths do not belong to this crate.")
                items, missing = [], 0
                for path_text in requested:
                    path = Path(path_text)
                    if not path.is_file():
                        missing += 1
                        continue
                    file_hash, added = copy_import(path, path.name, path.name, str(path))
                    items.append({"hash": file_hash, "added": added})
                return json_response(self, {"items": items, "missing": missing})
            if parsed.path == "/api/jobs":
                body = self._body_json()
                job_id, skipped = create_job(body.get("hashes", []), bool(body.get("force")))
                return json_response(self, {"job_id": job_id, "skipped": skipped})
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/action"):
                job_id = parsed.path.split("/")[-2]
                action = self._body_json().get("action")
                with connect() as db:
                    row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    return json_response(self, {"error": "Job not found"}, 404)
                if action == "pause":
                    paused_jobs.add(job_id)
                    _update_job(job_id, status="paused", message="Paused")
                elif action == "resume":
                    paused_jobs.discard(job_id)
                    _update_job(job_id, status="running", message="Resumed")
                elif action == "cancel":
                    cancelled_jobs.add(job_id)
                    paused_jobs.discard(job_id)
                else:
                    raise ValueError("Action must be pause, resume, or cancel.")
                return json_response(self, {"ok": True})
            if parsed.path == "/api/write":
                body = self._body_json()
                if not write_lock.acquire(blocking=False):
                    raise RuntimeError("Another Serato write or undo is already in progress.")
                try:
                    result = write_to_serato(body.get("hashes", []))
                finally:
                    write_lock.release()
                return json_response(self, result)
            if parsed.path.startswith("/api/backups/") and parsed.path.endswith("/undo"):
                backup_id = parsed.path.split("/")[-2]
                if _serato_open():
                    raise RuntimeError("Serato is open. Close it before undoing a write batch.")
                if not write_lock.acquire(blocking=False):
                    raise RuntimeError("Another Serato write or undo is already in progress.")
                try:
                    undo_backup(backup_id)
                finally:
                    write_lock.release()
                return json_response(self, {"ok": True})
            if parsed.path == "/api/settings":
                settings = self._body_json()
                with connect() as db:
                    db.executemany("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                                   ((key, str(value).lower() if isinstance(value, bool) else str(value))
                                    for key, value in settings.items()
                                    if key in ("gemini", "strictness")))
                return json_response(self, {"ok": True})
            self.send_error(404)
        except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, json.JSONDecodeError) as error:
            return json_response(self, {"error": str(error)}, 400)

    def do_PUT(self):
        if not self._check_mutation():
            return
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/tracks/"):
            return self.send_error(404)
        file_hash = parsed.path.rsplit("/", 1)[-1]
        try:
            body = self._body_json()
            with connect() as db:
                row = db.execute("SELECT plan_json,audio_path FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                if not row:
                    return json_response(self, {"error": "Track not found"}, 404)
                plan = json.loads(row["plan_json"]) if row["plan_json"] else {
                    "map_type": body.get("map_type", "VOCAL"), "cues": {},
                }
                grid = plan.get("grid", {})
                if grid.get("t1") is None or not grid.get("bar_s"):
                    raise ValueError("Analyze this track and build its beat grid before saving a cue review.")
                changed = False
                events = []
                t1, bar_s = grid.get("t1"), grid.get("bar_s")
                for slot, edit in body.get("cues", {}).items():
                    if slot not in "ABCDEFGH":
                        raise ValueError("Unknown cue slot: " + slot)
                    cue = plan.setdefault("cues", {}).setdefault(slot, {})
                    old_name, old_bar = cue.get("name"), cue.get("bar")
                    new_name = str(edit.get("name", SLOT_NAMES[slot]))[:80]
                    new_bar = float(edit["bar"]) if edit.get("bar") not in (None, "") else None
                    if new_bar is not None and (not math.isfinite(new_bar) or new_bar < 0):
                        raise ValueError(f"Cue {slot} bar must be a finite non-negative number.")
                    cue_changed = cue.get("name") != new_name or cue.get("bar") != new_bar
                    changed |= cue_changed
                    cue.update({
                        "name": new_name,
                        "bar": new_bar,
                        "confidence": cue.get("confidence"),
                        "reason": "Edited by you" if cue_changed else cue.get("reason", ""),
                    })
                    events.append({
                        "track": file_hash, "track_hash": file_hash,
                        "file": Path(row["audio_path"]).name, "cue": slot,
                        "name": new_name,
                        "action": "clear" if new_bar is None else
                            "correct" if old_bar == new_bar else "set_here",
                        "from_beat": None if old_bar is None else float(old_bar) * 4,
                        "to_beat": None if new_bar is None else new_bar * 4,
                        "from_time": None if old_bar is None or t1 is None or bar_s is None
                            else round(float(t1) + float(old_bar) * float(bar_s), 3),
                        "to_time": None if new_bar is None or t1 is None or bar_s is None
                            else round(float(t1) + new_bar * float(bar_s), 3),
                        "original_source": cue.get("source", "suggest"),
                        "original_beat": None if old_bar is None else float(old_bar) * 4,
                        "bar_s": bar_s, "t1": t1,
                        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                status = "Edited by you" if changed else "Cues ready"
                db.execute("UPDATE tracks SET plan_json=?,status=?,edited=?,reviewed=1 WHERE hash=?",
                           (json.dumps(plan, ensure_ascii=False), status, int(changed), file_hash))
                correction_path = DATA / "corrections.json"
                corrections = []
                if correction_path.is_file():
                    with open(correction_path, encoding="utf-8") as source:
                        corrections = json.load(source)
                corrections.extend(events)
                temporary = correction_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, correction_path)
            return json_response(self, {"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            return json_response(self, {"error": str(error)}, 400)

    def _body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise ValueError("Request body is too large.")
        return json.loads(self.rfile.read(length) or b"{}")

    def _static(self, path, content_type):
        try:
            data = path.read_bytes()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
                         "object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
        self.end_headers()
        self.wfile.write(data)


def main():
    initialize()
    with connect() as db:
        queued = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at").fetchall()
    for row in queued:
        worker_queue.put(row["id"])
    threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("AUTOCUE_PORT", "8770"))), Handler)
    print("AutoCue web app: http://127.0.0.1:%d" % server.server_address[1], flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
