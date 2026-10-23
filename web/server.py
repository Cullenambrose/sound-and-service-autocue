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
if str(ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(ROOT / "engine"))
from timing import bar_to_time, grid_origin, time_to_bar
from serato_tags import (
    FLAC_KEY, MP4_KEY, VORBIS_HEADER,
    marker2_entries as _marker2_entries, vorbis_decode as _vorbis_decode,
)

WEB = Path(__file__).resolve().parent
DATA = Path(os.environ.get("AUTOCUE_WEB_DATA", ROOT / "web-data")).expanduser().resolve()
UPLOADS = DATA / "audio"
DB_PATH = DATA / "library.sqlite3"
ALLOWED = {".wav", ".mp3", ".m4a", ".flac", ".aiff", ".aif", ".ogg", ".opus"}
SLOT_NAMES = {
    "A": "INTRO", "B": "START", "C": "EXIT -32", "D": "EXIT -16",
    "E": "EXIT/JUMP", "F": "LAST CHORUS", "G": "GO -32", "H": "OUTRO",
}
DROP_SLOT_NAMES = {
    "A": "INTRO", "B": "START", "C": "-32 DROP", "D": "FIRST DROP",
    "E": "-32 LAST DROP", "F": "LAST DROP", "G": "GO -32", "H": "OUTRO",
}
EVENT_TYPES = {"drop", "build", "breakdown", "chorus", "other"}
EVENT_CERTAINTIES = {"certain", "uncertain"}
EVENT_REVIEW_STATES = {"pending", "in_progress", "complete", "uncertain", "skipped"}
LEGACY_CUE_NAMES = {"CHORUS", "LAST CHORUS"}
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


def _cue_maps_module():
    engine = str(ROOT / "engine")
    if engine not in sys.path:
        sys.path.insert(0, engine)
    import cue_maps
    return cue_maps


def _staged_audio_directory():
    audio_dir = DATA.resolve() / "audio"
    if UPLOADS.is_symlink() or UPLOADS.resolve() != audio_dir:
        raise ValueError("The staged audio directory must be inside this library, not a symlink.")
    return audio_dir


def staged_audio_path(row, *, missing_ok=False):
    """Resolve only hash-named copies inside this library, including legacy absolute paths."""
    file_hash = row["hash"]
    if len(file_hash) != 64 or any(char not in "0123456789abcdef" for char in file_hash):
        raise ValueError("Invalid staged audio hash.")
    audio_dir = _staged_audio_directory()
    stored = Path(row["audio_path"])
    if (stored.suffix.lower() not in ALLOWED or stored.stem != file_hash
            or (not stored.is_absolute() and stored.parts != ("audio", stored.name))):
        raise ValueError("Invalid staged audio path; expected audio/<hash>.<extension>.")
    preferred = audio_dir / stored.name
    candidates = [preferred]
    if not preferred.exists() and not preferred.is_symlink():
        candidates.extend(sorted(path for path in audio_dir.glob(file_hash + ".*")
                                 if path != preferred and path.suffix.lower() in ALLOWED))
    for path in candidates:
        if path.is_symlink():
            raise ValueError("Staged audio must not be a symlink: " + path.name)
        if path.is_file():
            return path
        if path.exists():
            raise ValueError("Staged audio is not a regular file: " + path.name)
    if missing_ok:
        return preferred
    raise FileNotFoundError("Staged audio is missing from this library: " + preferred.name)


def initialize():
    global SESSION_TOKEN
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    _staged_audio_directory()
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
            CREATE TABLE IF NOT EXISTS event_reviews (
                track_hash TEXT PRIMARY KEY REFERENCES tracks(hash) ON DELETE CASCADE,
                state TEXT NOT NULL CHECK(state IN ('pending','in_progress','complete','uncertain','skipped')),
                note TEXT NOT NULL DEFAULT '',
                grid_json TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'human_review',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS event_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_hash TEXT NOT NULL REFERENCES tracks(hash) ON DELETE CASCADE,
                event_type TEXT NOT NULL CHECK(event_type IN ('drop','build','breakdown','chorus','other')),
                certainty TEXT NOT NULL CHECK(certainty IN ('certain','uncertain')),
                time_s REAL NOT NULL CHECK(time_s >= 0),
                bar REAL,
                note TEXT NOT NULL DEFAULT '',
                grid_json TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'human_review',
                created_at TEXT NOT NULL,
                retracted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS event_annotations_track_idx
                ON event_annotations(track_hash, id);
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
        for row in db.execute("SELECT hash,audio_path,error FROM tracks").fetchall():
            previous = "\n".join(line for line in (row["error"] or "").splitlines()
                                 if not line.startswith("Staged audio unavailable:"))
            try:
                path = staged_audio_path(row)
            except (ValueError, OSError) as error:
                message = "Staged audio unavailable: " + str(error)
                current = (previous + "\n" if previous else "") + message
                if current != row["error"]:
                    db.execute("UPDATE tracks SET error=? WHERE hash=?", (current, row["hash"]))
            else:
                relative = path.relative_to(DATA.resolve()).as_posix()
                if relative != row["audio_path"] or (previous or None) != row["error"]:
                    db.execute("UPDATE tracks SET audio_path=?,error=? WHERE hash=?",
                               (relative, previous or None, row["hash"]))
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('gemini', 'false')")
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('strictness', 'standard')")
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('cue_maps', ?)",
                   (json.dumps(_cue_maps_module().DEFAULT_CUE_MAPS),))


def track_record(row):
    plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
    cues = plan.get("cues", {})
    names = _slot_names(plan.get("map_type", "VOCAL"))
    return {
        "hash": row["hash"],
        "name": row["name"],
        "relative_path": row["relative_path"],
        "source_available": bool(row["source_path"] and Path(row["source_path"]).is_file()),
        "write_supported": bool(write_format(row["name"])),
        "status": row["status"],
        "edited": bool(row["edited"]),
        "reviewed": bool(row["reviewed"]),
        "error": row["error"],
        "bpm": plan.get("bpm") or plan.get("grid", {}).get("bpm"),
        "map_type": plan.get("map_type", "VOCAL"),
        "map_override": plan.get("map_override"),
        "map_choice": plan.get("map_choice"),
        "gemini": plan.get("gemini"),
        "outro_vocal_evidence": plan.get("outro_vocal_evidence"),
        "no_vocal_free_outro": plan.get("no_vocal_free_outro", False),
        "genre": plan.get("genre", ""),
        "confidence": _mean_confidence(cues),
        "t1": plan.get("grid", {}).get("t1"),
        "bar_s": plan.get("grid", {}).get("bar_s"),
        "offset_ms": plan.get("grid", {}).get("offset_ms", 0),
        "cues": {
            slot: {
                "name": _cue_name(cues.get(slot, {}).get("name"), slot, names,
                                  preserve_custom=bool(plan.get("cue_map"))),
                "bar": cues.get(slot, {}).get("bar"),
                "confidence": cues.get(slot, {}).get("confidence"),
                "reason": cues.get(slot, {}).get("reason", ""),
                "flags": cues.get(slot, {}).get("flags", []),
                "meaning": cues.get(slot, {}).get("meaning"),
                "color_id": cues.get(slot, {}).get("color_id") or
                            _cue_maps_module().DEFAULT_CUE_MAPS["maps"][plan.get("map_type", "VOCAL")][slot]["color"],
                "color": cues.get(slot, {}).get("display_color") or
                         _cue_maps_module().PALETTE_BY_ID[
                             _cue_maps_module().DEFAULT_CUE_MAPS["maps"][plan.get("map_type", "VOCAL")][slot]["color"]
                         ]["display"],
            } for slot in "ABCDEFGH"
        },
        "variants": plan.get("variants") or {},
        "url": "/media/" + row["hash"],
    }


def _event_review_payload(db, file_hash):
    review = db.execute("SELECT * FROM event_reviews WHERE track_hash=?", (file_hash,)).fetchone()
    annotations = db.execute(
        """SELECT id,event_type,certainty,time_s,bar,note,grid_json,source,created_at
           FROM event_annotations WHERE track_hash=? AND retracted_at IS NULL ORDER BY time_s,id""",
        (file_hash,),
    ).fetchall()
    return {
        "track_hash": file_hash,
        "review": {
            "state": review["state"] if review else "pending",
            "note": review["note"] if review else "",
            "source": review["source"] if review else None,
            "grid": json.loads(review["grid_json"]) if review else {},
            "updated_at": review["updated_at"] if review else None,
        },
        "annotations": [{
            "id": row["id"], "event_type": row["event_type"], "certainty": row["certainty"],
            "time_s": row["time_s"], "bar": row["bar"], "note": row["note"],
            "grid": json.loads(row["grid_json"]), "source": row["source"],
            "created_at": row["created_at"],
        } for row in annotations],
    }


def _event_grid(plan_json):
    plan = json.loads(plan_json) if plan_json else {}
    grid = plan.get("grid", {})
    if not isinstance(grid, dict):
        raise ValueError("Track has malformed grid metadata.")
    if grid and grid.get("t1") is not None and grid.get("bar_s") is not None:
        grid_origin(grid)
        return grid
    return {}


def _valid_track_hash(file_hash):
    return isinstance(file_hash, str) and len(file_hash) == 64 \
        and all(character in "0123456789abcdef" for character in file_hash)


def _utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slot_names(map_type):
    return DROP_SLOT_NAMES if str(map_type).upper() == "DROP" else SLOT_NAMES


def _cue_name(name, slot, names, preserve_custom=False):
    value = str(name or "").strip()
    return names[slot] if not value or (not preserve_custom and value.upper() in LEGACY_CUE_NAMES) else value


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


def _decode_crate_text(raw):
    # Serato stores text as UTF-16BE; a NUL in every other byte is the tell, and it must win over UTF-8
    # (which would silently accept the NULs and garble non-ASCII names).
    if len(raw) % 2 == 0 and raw and sum(raw[index] == 0 for index in range(0, len(raw), 2)) >= len(raw) // 4:
        try:
            return raw.decode("utf-16-be").replace("\x00", "").strip()
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8", "mac_roman"):
        try:
            return raw.decode(encoding).replace("\x00", "").strip()
        except UnicodeDecodeError:
            continue
    return ""


def _resolve_crate_path(value, crate_path):
    """Serato stores paths relative to the drive root: boot drive "Users/..", external drive "Music/..".
    Normalize to NFC/NFD whichever exists on disk, since macOS file systems vary."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        parts = Path(crate_path).resolve().parts
        drive = Path("/Volumes") / parts[2] if len(parts) > 2 and parts[1] == "Volumes" else Path("/")
        path = drive / value
    if not path.exists():
        import unicodedata
        for form in ("NFC", "NFD"):
            alternate = Path(unicodedata.normalize(form, str(path)))
            if alternate.exists():
                return alternate
    return path


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
        raw = data[start:end].strip(b"\r\n\t ")
        value = _decode_crate_text(raw)
        if value.startswith("file://"):
            value = urllib.parse.unquote(urllib.parse.urlparse(value).path)
        value = value.rstrip("\r\n")
        if value and value not in seen and Path(value).suffix.lower() in ALLOWED:
            seen.add(value)
            path = _resolve_crate_path(value, crate_path)
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
        parts = label.removesuffix(".crate").split("%%")
        found.append({
            "name": " › ".join(parts),
            "depth": len(parts) - 1,
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


def _cue_binary(cues, plan, existing_data=None):
    entries = _marker2_entries(existing_data)
    entries = [(name, payload) for name, payload in entries
               if name != "CUE" or len(payload) < 2 or payload[1] not in range(8)]
    names = _slot_names(plan.get("map_type", "VOCAL"))
    grid = plan.get("grid", {})
    grid_origin(grid)
    for index, slot in enumerate("ABCDEFGH"):
        cue = cues.get(slot, {})
        bar = cue.get("bar")
        if bar is None:
            continue
        bar_position = float(bar)
        if not math.isfinite(bar_position):
            raise ValueError(f"Cue {slot} has a non-finite bar position.")
        position = max(0, int(round(bar_to_time(bar_position, grid) * 1000)))
        if position >= 0xFFFFFFFF:
            raise ValueError(f"Cue {slot} exceeds Serato's supported timestamp.")
        palette = _cue_maps_module().PALETTE_BY_ID
        default_id = _cue_maps_module().DEFAULT_CUE_MAPS["maps"][plan.get("map_type", "VOCAL")][slot]["color"]
        color_hex = cue.get("color") or palette[default_id]["metadata"]
        if color_hex not in {item["metadata"] for item in palette.values()}:
            raise ValueError(f"Cue {slot} color is not a Serato-compatible palette value.")
        color = tuple(int(color_hex[offset:offset + 2], 16) for offset in (1, 3, 5))
        label = _cue_name(cue.get("name"), slot, names,
                          preserve_custom=bool(plan.get("cue_map"))).encode("utf-8")[:120] + b"\x00"
        payload = (bytes((0, index)) + position.to_bytes(4, "big") + bytes((0, *color, 0, 0)) + label)
        entries.append(("CUE", payload))
    raw = bytearray(b"\x01\x01")
    for name, payload in entries:
        raw.extend(name.encode("ascii") + b"\x00" + len(payload).to_bytes(4, "big") + payload)
    return b"\x01\x01" + base64.b64encode(bytes(raw)) + b"\x00"


WRITABLE = {".mp3": "mp3", ".aiff": "aiff", ".aif": "aiff", ".flac": "flac", ".m4a": "m4a"}


def write_format(path):
    return WRITABLE.get(Path(path).suffix.lower())


def _require_mutagen():
    try:
        import mutagen
        return mutagen
    except ImportError as error:
        raise RuntimeError("Serato writing needs the Mutagen package. Install the repo's documented audio dependencies.") from error


def _hash_ranges(path, ranges):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for start, end in ranges:
            source.seek(start)
            remaining = max(0, end - start)
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    break
                digest.update(block)
                remaining -= len(block)
    return digest.hexdigest()


def _iff_ranges(path):
    """AIFF: every chunk except the ID3 tag chunk, so tag edits never change the audio hash."""
    ranges = []
    with open(path, "rb") as source:
        header = source.read(12)
        if header[:4] != b"FORM" or header[8:12] not in (b"AIFF", b"AIFC"):
            raise ValueError("Not an AIFF file.")
        size = Path(path).stat().st_size
        while source.tell() + 8 <= size:
            start = source.tell()
            ident, length = source.read(4), int.from_bytes(source.read(4), "big")
            end = start + 8 + length + (length & 1)
            if ident.lower() != b"id3 ":
                ranges.append((start, min(end, size)))
            source.seek(min(end, size))
    return ranges


def _flac_ranges(path):
    """FLAC: the frames after the metadata blocks."""
    with open(path, "rb") as source:
        if source.read(4) != b"fLaC":
            raise ValueError("Not a FLAC file.")
        while True:
            header = source.read(4)
            if len(header) < 4:
                raise ValueError("Truncated FLAC metadata.")
            source.seek(int.from_bytes(header[1:], "big"), os.SEEK_CUR)
            if header[0] & 0x80:
                return [(source.tell(), Path(path).stat().st_size)]


def _mp4_ranges(path):
    """M4A: the payload of every top-level mdat atom (offsets inside moov may move, audio bytes do not)."""
    ranges, size = [], Path(path).stat().st_size
    with open(path, "rb") as source:
        offset = 0
        while offset + 8 <= size:
            source.seek(offset)
            length, kind = int.from_bytes(source.read(4), "big"), source.read(4)
            header = 8
            if length == 1:
                length, header = int.from_bytes(source.read(8), "big"), 16
            elif length == 0:
                length = size - offset
            if length < header:
                raise ValueError("Malformed MP4 atom.")
            if kind == b"mdat":
                ranges.append((offset + header, min(offset + length, size)))
            offset += length
    if not ranges:
        raise ValueError("No audio data (mdat) found in this M4A.")
    return ranges


def _audio_hash(path):
    kind = write_format(path)
    if kind == "mp3":
        return _mp3_audio_hash(path)
    if kind == "aiff":
        return _hash_ranges(path, _iff_ranges(path))
    if kind == "flac":
        return _hash_ranges(path, _flac_ranges(path))
    if kind == "m4a":
        return _hash_ranges(path, _mp4_ranges(path))
    raise ValueError(f"{Path(path).name}: Write not supported yet for {Path(path).suffix or 'this'} files.")


def _vorbis_encode(data):
    raw = base64.b64encode(VORBIS_HEADER + data).rstrip(b"=")
    return b"\n".join(raw[index:index + 72] for index in range(0, len(raw), 72)).decode("ascii")


def _existing_markers(path, kind):
    """Return (existing Markers2 GEOB-style bytes or None, error if a conflicting tag exists)."""
    mutagen = _require_mutagen()
    if kind in ("mp3", "aiff"):
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            tags = ID3(path) if kind == "mp3" else (mutagen.File(path).tags or ID3())
        except ID3NoHeaderError:
            return None
        if tags.getall("GEOB:Serato Markers_"):
            raise RuntimeError(f"{Path(path).name} uses legacy Serato Markers_ cues; writing is blocked to avoid a conflicting cue set.")
        frames = tags.getall("GEOB:Serato Markers2")
        if len(frames) > 1:
            raise RuntimeError(f"{Path(path).name} has multiple Serato Markers2 frames; writing is blocked.")
        return frames[0].data if frames else None
    if kind == "flac":
        values = (mutagen.File(path).tags or {}).get(FLAC_KEY, [])
        if len(values) > 1:
            raise RuntimeError(f"{Path(path).name} has multiple Serato Markers2 fields; writing is blocked.")
        return _vorbis_decode(values[0]) if values else None
    from mutagen.mp4 import MP4
    values = (MP4(path).tags or {}).get(MP4_KEY, [])
    if len(values) > 1:
        raise RuntimeError(f"{Path(path).name} has multiple Serato Markers2 atoms; writing is blocked.")
    return _vorbis_decode(bytes(values[0]).decode("ascii")) if values else None


def _store_markers(path, kind, data):
    mutagen = _require_mutagen()
    if kind in ("mp3", "aiff"):
        from mutagen.id3 import ID3, GEOB, ID3NoHeaderError
        if kind == "mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("GEOB:Serato Markers2")
            tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2", data=data))
            tags.save(path, v2_version=3)
        else:
            audio = mutagen.File(path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("GEOB:Serato Markers2")
            audio.tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2", data=data))
            audio.save(v2_version=3)
    elif kind == "flac":
        audio = mutagen.File(path)
        audio[FLAC_KEY] = [_vorbis_encode(data)]
        audio.save()
    else:
        from mutagen.mp4 import MP4, MP4FreeForm
        audio = MP4(path)
        audio[MP4_KEY] = [MP4FreeForm(_vorbis_encode(data).encode("ascii"))]
        audio.save()


def _write_markers2(path, cues, plan):
    path = Path(path)
    kind = write_format(path)
    if not kind:
        raise ValueError(f"{path.name}: Write not supported yet for {path.suffix or 'this'} files.")
    original_hash = _sha256(path)
    audio_hash = _audio_hash(path)
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.autocue{path.suffix}")
    try:
        shutil.copy2(path, temporary)
        marker_data = _cue_binary(cues, plan, _existing_markers(temporary, kind))
        _store_markers(temporary, kind, marker_data)
        if _existing_markers(temporary, kind) != marker_data:
            raise RuntimeError(f"Could not verify the written Serato cue tag in {path.name}.")
        if _audio_hash(temporary) != audio_hash:
            raise RuntimeError(f"Audio data changed while writing Serato cues to {path.name}.")
        if _sha256(path) != original_hash:
            raise RuntimeError(f"{path.name} changed during the write; no changes were applied.")
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
    return _hash_ranges(path, [(start, end)])


def _validate_audio(path):
    mutagen = _require_mutagen()
    try:
        audio = mutagen.File(path)
    except Exception as error:
        raise ValueError(f"{Path(path).name} is not a readable audio file: {error}") from error
    if audio is None or audio.info is None:
        raise ValueError(f"{Path(path).name} has no readable audio stream.")


def _read_serato_geob(path):
    """Raw Serato tag data for the backup, as base64 per format-specific field."""
    kind = write_format(path)
    _require_mutagen()
    try:
        if kind in ("mp3", "aiff"):
            import mutagen
            from mutagen.id3 import ID3, ID3NoHeaderError
            try:
                tags = ID3(path) if kind == "mp3" else (mutagen.File(path).tags or ID3())
            except ID3NoHeaderError:
                return []
            return [{"desc": frame.desc, "data_b64": base64.b64encode(frame.data).decode("ascii")}
                    for frame in tags.getall("GEOB") if frame.desc in ("Serato Markers2", "Serato Markers_")]
        if kind == "flac":
            import mutagen
            return [{"desc": FLAC_KEY, "data_b64": base64.b64encode(value.encode("utf-8")).decode("ascii")}
                    for value in (mutagen.File(path).tags or {}).get(FLAC_KEY, [])]
        if kind == "m4a":
            from mutagen.mp4 import MP4
            return [{"desc": MP4_KEY, "data_b64": base64.b64encode(bytes(value)).decode("ascii")}
                    for value in (MP4(path).tags or {}).get(MP4_KEY, [])]
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
        if not write_format(path):
            raise ValueError(f"{row['name']}: Write not supported yet for {path.suffix or 'this'} files "
                             f"(supported: {', '.join(sorted(WRITABLE))}).")
        if path.is_symlink():
            raise ValueError(f"{row['name']}: writing through symbolic links is blocked.")
        _validate_audio(path)
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


def _restore_serato_folder(archive, index, target):
    target = Path(target)
    prefix = f"serato/{index}-{target.parent.name}/{target.name}/"
    members = [info for info in archive.infolist() if info.filename.startswith(prefix) and not info.is_dir()]
    staging = target.with_name(f".{target.name}.autocue-restore-{uuid.uuid4().hex[:8]}")
    aside = target.with_name(f".{target.name}.autocue-before-restore-{uuid.uuid4().hex[:8]}")
    try:
        for info in members:
            relative = Path(info.filename[len(prefix):])
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("Backup contains an unsafe path; restore stopped.")
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(destination, "wb") as output:
                shutil.copyfileobj(source, output)
            if destination.stat().st_size != info.file_size:
                raise RuntimeError(f"Could not verify restored file {relative}.")
        staging.mkdir(parents=True, exist_ok=True)
        had_target = target.exists()
        if had_target:
            os.replace(target, aside)
        try:
            os.replace(staging, target)
        except Exception:
            if had_target:
                os.replace(aside, target)
            raise
        shutil.rmtree(aside, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def restore_backup(backup_id, confirmation):
    """Put back every song file and the _Serato_ folder(s) exactly as they were when the backup was made."""
    if confirmation != "RESTORE":
        raise ValueError("Type RESTORE to confirm restoring everything from this backup.")
    with connect() as db:
        backup = db.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    if not backup:
        raise ValueError("Backup not found.")
    manifest = json.loads(backup["manifest_json"])
    with zipfile.ZipFile(backup["backup_path"], "r") as archive:
        broken = archive.testzip()
        if broken:
            raise RuntimeError("Backup is damaged (" + broken + "). Nothing was restored.")
        for entry in manifest.get("tracks", []):
            if _archive_entry_sha256(archive, entry["archive_name"]) != entry["original_sha256"]:
                raise RuntimeError("Backup audio failed verification. Nothing was restored.")
        for entry in manifest.get("tracks", []):
            _restore_backup_entry(archive, entry)
        for index, folder in enumerate(manifest.get("serato_folders", [])):
            _restore_serato_folder(archive, index, folder)
    with connect() as db:
        for entry in manifest.get("tracks", []):
            current = entry.get("post_write_sha256") or entry["hash"]
            db.execute("UPDATE tracks SET hash=?,status=?,reviewed=?,edited=? WHERE hash IN (?,?)",
                       (entry["hash"], entry["previous_status"], entry["previous_reviewed"],
                        entry["previous_edited"], current, entry["hash"]))
        db.execute("UPDATE backups SET status='restored' WHERE id=?", (backup_id,))


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
    _staged_audio_directory()
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
        destination = staged_audio_path(
            {"hash": file_hash, "audio_path": "audio/" + file_hash + suffix}, missing_ok=True)
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
                (file_hash, Path(name).name, relative_path, destination.relative_to(DATA.resolve()).as_posix(), source_path,
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


LIVE = {"hash": None, "name": "", "steps": {}, "detail": "", "cues_found": []}


def _live_reset(row):
    LIVE.update(hash=row["hash"], name=row["name"], steps={}, detail="", cues_found=[])


def _tags(path):
    try:
        from mutagen import File
        audio = File(path, easy=True)
        get = lambda key: (audio.get(key) or [""])[0] if audio else ""
        return get("artist"), get("title"), get("genre")
    except Exception:
        return "", "", ""


def _experiments_module():
    sys.path.insert(0, str(ROOT / "engine"))
    import experiments
    return experiments


def _enabled_experiments(settings):
    module = _experiments_module()
    raw = settings.get("experiments")
    return module.normalize_enabled(json.loads(raw) if raw else None)


def _graded_tracks():
    with connect() as db:
        rows = db.execute("SELECT hash,name,plan_json,reviewed FROM tracks WHERE plan_json IS NOT NULL").fetchall()
    tracks = [{"hash": row["hash"], "name": row["name"], "reviewed": bool(row["reviewed"]),
               "plan": json.loads(row["plan_json"])} for row in rows]
    return [track for track in tracks if track["reviewed"] or track["plan"].get("answer_key")]


def _analysis_import():
    sys.path.insert(0, str(ROOT / "engine"))
    import analyze
    return analyze


def waveform_peaks(file_hash, points=900):
    analyze = _analysis_import()
    path = analyze.Paths(analyze.cache_root() / file_hash).features
    if not path.is_file():
        return None
    import numpy as np
    peak = np.load(path)["peak"]
    size = max(1, len(peak) // points)
    trimmed = peak[: len(peak) // size * size].reshape(-1, size).max(1)
    top = float(trimmed.max()) or 1.0
    return {"peaks": [round(float(value) / top, 3) for value in trimmed], "duration": len(peak) / 50}


def process_builtin(row, settings, map_override=None):
    analyze = _analysis_import()
    _live_reset(row)
    audio_path = str(staged_audio_path(row))
    artist, title, genre = _tags(audio_path)
    cue_maps = _cue_maps_module().normalize_cue_maps(
        json.loads(settings["cue_maps"]) if settings.get("cue_maps") else None
    )
    if map_override in ("DROP", "VOCAL"):
        cue_maps["mode"] = map_override

    def progress(step, state, detail):
        LIVE["steps"][step] = state
        LIVE["detail"] = detail or LIVE["detail"]

    plan = analyze.analyze(audio_path, artist, title, genre, progress, cue_maps=cue_maps,
                           experiments=_enabled_experiments(settings),
                           gemini=settings.get("gemini") == "true")
    LIVE["cues_found"] = [slot for slot, cue in plan["cues"].items() if cue.get("bar") is not None]
    return plan


def _store_plan(file_hash, plan, status):
    """Save a new plan. A reviewed song keeps its reviewed cues as the answer key for scoring."""
    experiments = _experiments_module()
    plan["variants"] = experiments.scoring_variants(plan)
    with connect() as db:
        old = db.execute("SELECT plan_json,reviewed FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        if old and old["plan_json"]:
            previous = json.loads(old["plan_json"])
            key = previous.get("answer_key")
            if old["reviewed"] and not key:
                key = experiments.review_answer(previous)
            if key:
                plan["answer_key"] = key
        db.execute("UPDATE tracks SET plan_json=?, status=?, error=NULL, reviewed=0, edited=0 WHERE hash=?",
                   (json.dumps(plan, ensure_ascii=False), status, file_hash))


def process_track(file_hash):
    with connect() as db:
        row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        settings = {item["key"]: item["value"] for item in db.execute("SELECT * FROM settings")}
    if not row:
        raise ValueError("Track not found.")
    command = os.environ.get("AUTOCUE_PROCESSOR")
    if not command:
        try:
            previous = json.loads(row["plan_json"]) if row["plan_json"] else {}
            override = previous.get("map_override")
            plan = process_builtin(row, settings, override)
            if override:
                plan["map_override"] = override
        except Exception as error:
            if type(error).__name__ == "AnalyzerMissing":
                raise RuntimeError(str(error)) from error
            raise
        threshold = {"relaxed": 0.45, "standard": 0.6, "strict": 0.8}.get(
            settings.get("strictness", "standard"), 0.6)
        low = any((cue.get("confidence") or 0) < threshold for cue in plan["cues"].values())
        _store_plan(file_hash, plan, "Needs a look" if low else "Cues ready")
        return
    import shlex
    output = DATA / "results" / (file_hash + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    placeholders = {"{input}": str(staged_audio_path(row)), "{output}": str(output), "{hash}": file_hash}
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
    _store_plan(file_hash, plan, "Needs a look" if low else "Cues ready")


def remove_tracks(hashes):
    """Remove tracks and their app-local annotations; never delete source audio."""
    with connect() as db:
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','paused') LIMIT 1").fetchone():
            raise RuntimeError("Wait for the running batch to finish (or cancel it) before removing tracks.")
        rows = db.execute("SELECT hash,audio_path FROM tracks").fetchall()
        wanted = None if hashes is None else set(hashes)
        doomed = [row for row in rows if wanted is None or row["hash"] in wanted]
        paths = [(row, staged_audio_path(row, missing_ok=True)) for row in doomed]
        removed, failures = 0, []
        for row, path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                failures.append(path.name + ": " + str(error))
            else:
                db.execute("DELETE FROM tracks WHERE hash=?", (row["hash"],))
                removed += 1
    if failures:
        raise RuntimeError(f"Removed {removed} tracks; could not delete staged audio for "
                           f"{len(failures)} tracks (kept in library): " + "; ".join(failures))
    return {"removed": removed}


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
                    body = dict(job) if job else {}
                    body["live"] = dict(LIVE)
                    payload = json.dumps(body)
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
        if parsed.path == "/timing.js":
            return self._static(WEB / "timing.js", "text/javascript; charset=utf-8")
        if parsed.path == "/style.css":
            return self._static(WEB / "style.css", "text/css; charset=utf-8")
        if parsed.path == "/api/library":
            with connect() as db:
                rows = db.execute("SELECT * FROM tracks ORDER BY imported_at DESC").fetchall()
            return json_response(self, {"tracks": [track_record(row) for row in rows]})
        if parsed.path.startswith("/api/tracks/") and parsed.path.endswith("/event-review"):
            file_hash = parsed.path.split("/")[-2]
            if not _valid_track_hash(file_hash):
                return json_response(self, {"error": "Invalid track hash."}, 400)
            with connect() as db:
                track = db.execute("SELECT hash FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                if not track:
                    return json_response(self, {"error": "Track not found."}, 404)
                return json_response(self, _event_review_payload(db, file_hash))
        if parsed.path == "/api/event-reviews/export":
            with connect() as db:
                reviews = db.execute(
                    """SELECT r.*,t.name FROM event_reviews r
                       JOIN tracks t ON t.hash=r.track_hash ORDER BY r.updated_at,r.track_hash"""
                ).fetchall()
                exported = []
                for review in reviews:
                    annotations = db.execute(
                        """SELECT id,event_type,certainty,time_s,bar,note,grid_json,source,
                                  created_at,retracted_at
                           FROM event_annotations WHERE track_hash=? ORDER BY id""",
                        (review["track_hash"],),
                    ).fetchall()
                    exported.append({
                        "track_hash": review["track_hash"], "track_name": review["name"],
                        "state": review["state"], "note": review["note"],
                        "grid": json.loads(review["grid_json"]), "source": review["source"],
                        "updated_at": review["updated_at"],
                        "annotations": [{
                            "id": row["id"], "event_type": row["event_type"],
                            "certainty": row["certainty"], "time_s": row["time_s"],
                            "bar": row["bar"], "note": row["note"],
                            "grid": json.loads(row["grid_json"]), "source": row["source"],
                            "created_at": row["created_at"],
                            "active": row["retracted_at"] is None,
                            "retracted_at": row["retracted_at"],
                        } for row in annotations],
                    })
            document = {
                "schema_version": 1, "exported_at": _utc_timestamp(),
                "taxonomy": sorted(EVENT_TYPES),
                "provenance_policy": "Manual local human review; imported cues are not labels.",
                "reviews": exported,
            }
            body = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="autocue-event-labels.json"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/jobs":
            with connect() as db:
                rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
            return json_response(self, {"jobs": [dict(row) for row in rows]})
        if parsed.path == "/api/settings":
            with connect() as db:
                settings = {row["key"]: row["value"] for row in db.execute("SELECT * FROM settings")}
            module = _cue_maps_module()
            settings["cue_maps"] = module.normalize_cue_maps(
                json.loads(settings["cue_maps"]) if settings.get("cue_maps") else None
            )
            settings["cue_map_catalog"] = module.cue_map_catalog()
            settings["cue_map_defaults"] = module.DEFAULT_CUE_MAPS
            experiments = _experiments_module()
            settings["experiments"] = _enabled_experiments(settings)
            settings["experiment_catalog"] = experiments.CATALOG
            import gemini_assist
            settings["gemini_status"] = gemini_assist.configuration_status(_analysis_import().cache_root() / "gemini")
            return json_response(self, settings)
        if parsed.path == "/api/experiments/report":
            return json_response(self, _experiments_module().score_tracks(_graded_tracks()))
        if parsed.path == "/api/experiments/export":
            tracks = _graded_tracks()
            experiments = _experiments_module()
            export = {"schema_version": 2, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "answer_keys": [
                          {"hash": track["hash"], "name": track["name"],
                           "answer": track["plan"].get("answer_key") or experiments.review_answer(track["plan"])}
                          for track in tracks],
                      "report": _experiments_module().score_tracks(tracks), "tracks": tracks}
            body = json.dumps(export, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", 'attachment; filename="autocue-experiments.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/status":
            return json_response(self, {
                "processor_configured": True,
                "external_processor": bool(os.environ.get("AUTOCUE_PROCESSOR")),
                "analyzers": _analysis_import().environment_report(),
                "writable_formats": sorted(WRITABLE),
                "write_dependency_available": importlib.util.find_spec("mutagen") is not None,
                "write_platform_supported": sys.platform == "darwin",
            })
        if parsed.path.startswith("/api/waveform/"):
            if urllib.parse.parse_qs(parsed.query).get("detail") == ["1"]:
                try:
                    file_hash = parsed.path.removeprefix("/api/waveform/")
                    with connect() as db:
                        row = db.execute("SELECT hash,audio_path FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                    if not row:
                        return json_response(self, {"error": "Track not found"}, 404)
                    analyze = _analysis_import()
                    import waveform
                    query = urllib.parse.parse_qs(parsed.query)
                    data = waveform.query(staged_audio_path(row), analyze.cache_root() / file_hash / "waveform",
                                          start=float(query.get("start", [0])[0]),
                                          end=float(query["end"][0]) if "end" in query else None,
                                          points=int(query.get("points", [1600])[0]))
                    return json_response(self, data)
                except (ValueError, OSError, RuntimeError, ImportError) as error:
                    return json_response(self, {"error": str(error)}, 400)
            try:
                found = waveform_peaks(parsed.path.removeprefix("/api/waveform/"))
            except ImportError:
                found = None
            return json_response(self, found or {"peaks": [], "duration": 0})
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
                row = db.execute("SELECT hash,audio_path FROM tracks WHERE hash=?", (file_hash,)).fetchone()
            if not row:
                return json_response(self, {"error": "Audio not found"}, 404)
            try:
                path = staged_audio_path(row)
            except FileNotFoundError as error:
                return json_response(self, {"error": str(error)}, 404)
            except (ValueError, OSError) as error:
                return json_response(self, {"error": str(error)}, 400)
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
            if parsed.path.startswith("/api/event-reviews/"):
                file_hash = parsed.path.removeprefix("/api/event-reviews/")
                if not _valid_track_hash(file_hash):
                    raise ValueError("Invalid track hash.")
                body = self._body_json()
                if not isinstance(body, dict):
                    raise ValueError("Event review body must be a JSON object.")
                state = body.get("state")
                if not isinstance(state, str) or state not in EVENT_REVIEW_STATES:
                    raise ValueError("Event review state must be pending, in_progress, complete, uncertain, or skipped.")
                note = body.get("note", "")
                if not isinstance(note, str) or len(note) > 500:
                    raise ValueError("Event review note must be text no longer than 500 characters.")
                updated_at = _utc_timestamp()
                with connect() as db:
                    track = db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                    if not track:
                        return json_response(self, {"error": "Track not found."}, 404)
                    grid = _event_grid(track["plan_json"])
                    db.execute(
                        """INSERT INTO event_reviews(track_hash,state,note,grid_json,source,updated_at)
                           VALUES(?,?,?,?,?,?)
                           ON CONFLICT(track_hash) DO UPDATE SET state=excluded.state,
                             note=excluded.note,grid_json=excluded.grid_json,source=excluded.source,
                             updated_at=excluded.updated_at""",
                        (file_hash, state, note.strip(), json.dumps(grid, allow_nan=False),
                         "human_review", updated_at),
                    )
                    result = _event_review_payload(db, file_hash)
                return json_response(self, result)
            if parsed.path.startswith("/api/event-annotations/") and parsed.path.endswith("/retract"):
                annotation_id = parsed.path.split("/")[-2]
                if not annotation_id.isdecimal() or len(annotation_id) > 19 \
                        or int(annotation_id) > 9223372036854775807:
                    raise ValueError("Invalid event annotation id.")
                retracted_at = _utc_timestamp()
                with connect() as db:
                    row = db.execute(
                        "SELECT track_hash FROM event_annotations WHERE id=? AND retracted_at IS NULL",
                        (int(annotation_id),),
                    ).fetchone()
                    if not row:
                        return json_response(self, {"error": "Active event annotation not found."}, 404)
                    db.execute("UPDATE event_annotations SET retracted_at=? WHERE id=?",
                               (retracted_at, int(annotation_id)))
                    db.execute(
                        """INSERT INTO event_reviews(track_hash,state,note,grid_json,source,updated_at)
                           VALUES(?,'in_progress','', '{}','human_review',?)
                           ON CONFLICT(track_hash) DO UPDATE SET state='in_progress',
                             updated_at=excluded.updated_at""",
                        (row["track_hash"], retracted_at),
                    )
                    result = _event_review_payload(db, row["track_hash"])
                return json_response(self, result)
            if parsed.path.startswith("/api/event-annotations/"):
                file_hash = parsed.path.removeprefix("/api/event-annotations/")
                if not _valid_track_hash(file_hash):
                    raise ValueError("Invalid track hash.")
                body = self._body_json()
                if not isinstance(body, dict):
                    raise ValueError("Event annotation body must be a JSON object.")
                event_type = body.get("event_type")
                certainty = body.get("certainty")
                time_s = body.get("time_s")
                note = body.get("note", "")
                if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
                    raise ValueError("Event type must be drop, build, breakdown, chorus, or other.")
                if not isinstance(certainty, str) or certainty not in EVENT_CERTAINTIES:
                    raise ValueError("Event certainty must be certain or uncertain.")
                if type(time_s) not in (int, float) or not math.isfinite(time_s) or time_s < 0:
                    raise ValueError("Event time must be a finite non-negative number of seconds.")
                if not isinstance(note, str) or len(note) > 500:
                    raise ValueError("Event note must be text no longer than 500 characters.")
                created_at = _utc_timestamp()
                with connect() as db:
                    track = db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                    if not track:
                        return json_response(self, {"error": "Track not found."}, 404)
                    grid = _event_grid(track["plan_json"])
                    bar = time_to_bar(time_s, grid) if grid else None
                    db.execute(
                        """INSERT INTO event_annotations
                           (track_hash,event_type,certainty,time_s,bar,note,grid_json,source,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (file_hash, event_type, certainty, float(time_s), bar, note.strip(),
                         json.dumps(grid, allow_nan=False), "human_review", created_at),
                    )
                    db.execute(
                        """INSERT INTO event_reviews(track_hash,state,note,grid_json,source,updated_at)
                           VALUES(?,'in_progress','',?,'human_review',?)
                           ON CONFLICT(track_hash) DO UPDATE SET state='in_progress',
                             grid_json=excluded.grid_json,source=excluded.source,updated_at=excluded.updated_at""",
                        (file_hash, json.dumps(grid, allow_nan=False), created_at),
                    )
                    result = _event_review_payload(db, file_hash)
                return json_response(self, result)
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
                temp = _staged_audio_directory() / (uuid.uuid4().hex + ".incoming")
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
            if parsed.path.startswith("/api/tracks/") and parsed.path.endswith("/map"):
                file_hash = parsed.path.split("/")[-2]
                choice = str(self._body_json().get("map_type", "")).upper()
                if choice not in ("AUTO", "DROP", "VOCAL"):
                    raise ValueError("Map must be Auto, Drop, or Vocal.")
                with connect() as db:
                    row = db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                    if not row:
                        raise ValueError("Track not found.")
                    plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
                    if choice == "AUTO":
                        plan.pop("map_override", None)
                    else:
                        plan["map_override"] = choice
                    db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan, ensure_ascii=False), file_hash))
                job_id, _ = create_job([file_hash], force=True)
                return json_response(self, {"job_id": job_id})
            if parsed.path == "/api/tracks/remove":
                body = self._body_json()
                return json_response(self, remove_tracks(None if body.get("all") else body.get("hashes", [])))
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
            if parsed.path.startswith("/api/backups/") and parsed.path.endswith("/restore"):
                backup_id = parsed.path.split("/")[-2]
                confirmation = self._body_json().get("confirmation", "")
                if confirmation != "RESTORE":
                    raise ValueError("Type RESTORE to confirm restoring everything from this backup.")
                if _serato_open():
                    raise RuntimeError("Serato is open. Close it before restoring a backup.")
                if not write_lock.acquire(blocking=False):
                    raise RuntimeError("Another Serato write or undo is already in progress.")
                try:
                    restore_backup(backup_id, confirmation)
                finally:
                    write_lock.release()
                return json_response(self, {"ok": True})
            if parsed.path == "/api/settings":
                settings = self._body_json()
                normalized_cue_maps = None
                if "cue_maps" in settings:
                    normalized_cue_maps = _cue_maps_module().normalize_cue_maps(settings["cue_maps"])
                with connect() as db:
                    if "gemini" in settings:
                        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('gemini',?)",
                                   (str(settings["gemini"]).lower(),))
                    if "strictness" in settings:
                        if settings["strictness"] not in ("relaxed", "standard", "strict"):
                            raise ValueError("Review strictness must be relaxed, standard, or strict.")
                        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('strictness',?)",
                                   (settings["strictness"],))
                    if "experiments" in settings:
                        enabled = _experiments_module().normalize_enabled(settings["experiments"])
                        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('experiments',?)",
                                   (json.dumps(enabled),))
                    if normalized_cue_maps is not None:
                        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('cue_maps',?)",
                                   (json.dumps(normalized_cue_maps),))
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
                row = db.execute("SELECT plan_json,audio_path,reviewed FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                if not row:
                    return json_response(self, {"error": "Track not found"}, 404)
                plan = json.loads(row["plan_json"]) if row["plan_json"] else {
                    "map_type": body.get("map_type", "VOCAL"), "cues": {},
                }
                fallback_names = _slot_names(plan.get("map_type", "VOCAL"))
                grid = plan.get("grid", {})
                if grid.get("t1") is None or not grid.get("bar_s"):
                    raise ValueError("Analyze this track and build its beat grid before saving a cue review.")
                grid_origin(grid)
                experiments = _experiments_module()
                previous_answer = plan.get("answer_key")
                if not previous_answer and row["reviewed"]:
                    previous_answer = experiments.review_answer(plan)
                plan["variants"] = experiments.scoring_variants(plan, allow_current=not row["reviewed"])
                changed = False
                events = []
                t1, bar_s = grid.get("t1"), grid.get("bar_s")
                for slot, edit in body.get("cues", {}).items():
                    if slot not in "ABCDEFGH":
                        raise ValueError("Unknown cue slot: " + slot)
                    cue = plan.setdefault("cues", {}).setdefault(slot, {})
                    old_name, old_bar = cue.get("name"), cue.get("bar")
                    new_name = _cue_name(edit.get("name", fallback_names[slot]), slot, fallback_names,
                                         preserve_custom=bool(plan.get("cue_map")))[:80]
                    new_bar = float(edit["bar"]) if edit.get("bar") not in (None, "") else None
                    earliest = min(0, math.ceil(time_to_bar(0, grid) - 1e-6))
                    if new_bar is not None and (not math.isfinite(new_bar) or new_bar < earliest):
                        raise ValueError(f"Cue {slot} bar must be a finite number on or after bar {earliest}.")
                    cue_changed = cue.get("name") != new_name or cue.get("bar") != new_bar
                    changed |= cue_changed
                    cue.update({
                        "name": new_name,
                        "bar": new_bar,
                        "time_s": None if new_bar is None else round(bar_to_time(new_bar, grid), 3),
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
                        "from_time": None if old_bar is None else round(bar_to_time(old_bar, grid), 3),
                        "to_time": None if new_bar is None else round(bar_to_time(new_bar, grid), 3),
                        "original_source": cue.get("source", "suggest"),
                        "original_beat": None if old_bar is None else float(old_bar) * 4,
                        "bar_s": bar_s, "t1": t1, "offset_ms": grid.get("offset_ms", 0),
                        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                plan["answer_key"] = experiments.merge_review_answer(plan, previous_answer, body.get("cues", {}))
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
        except (ValueError, TypeError, OSError, json.JSONDecodeError) as error:
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
