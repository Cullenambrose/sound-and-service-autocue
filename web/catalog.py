"""Immutable catalog snapshots and a read-only, explicitly reviewed local file index."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import unicodedata
import uuid

from mutagen import File, MutagenError

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aiff", ".aif", ".ogg", ".opus"}
MAX_FILES = 10000


class Conflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def text(value, field, *, required=False):
    if not isinstance(value, str) or len(value) > 4000 or (required and not value.strip()):
        raise ValueError(f"{field} must be {'nonempty ' if required else ''}text, at most 4000 characters.")
    return value


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS catalog_snapshots (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, document_json TEXT NOT NULL,
            imported_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS catalog_crates (
            snapshot_id TEXT NOT NULL REFERENCES catalog_snapshots(id),
            source_id TEXT NOT NULL, title TEXT NOT NULL, available INTEGER NOT NULL,
            metadata_json TEXT NOT NULL, position INTEGER NOT NULL,
            PRIMARY KEY(snapshot_id,source_id)
        );
        CREATE TABLE IF NOT EXISTS catalog_songs (
            snapshot_id TEXT NOT NULL REFERENCES catalog_snapshots(id),
            source_id TEXT NOT NULL, artist TEXT NOT NULL, title TEXT NOT NULL,
            version TEXT NOT NULL, metadata_json TEXT NOT NULL,
            PRIMARY KEY(snapshot_id,source_id)
        );
        CREATE TABLE IF NOT EXISTS catalog_memberships (
            snapshot_id TEXT NOT NULL, crate_id TEXT NOT NULL, position INTEGER NOT NULL,
            song_id TEXT NOT NULL,
            PRIMARY KEY(snapshot_id,crate_id,position),
            FOREIGN KEY(snapshot_id,crate_id) REFERENCES catalog_crates(snapshot_id,source_id),
            FOREIGN KEY(snapshot_id,song_id) REFERENCES catalog_songs(snapshot_id,source_id)
        );
        CREATE TABLE IF NOT EXISTS library_roots (
            id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            device INTEGER NOT NULL, inode INTEGER NOT NULL,
            scanned_at REAL, error TEXT NOT NULL DEFAULT '', scan_revision INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS library_files (
            id TEXT PRIMARY KEY, root_id TEXT NOT NULL REFERENCES library_roots(id),
            relative_path TEXT NOT NULL, content_hash TEXT, size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL, ctime_ns INTEGER NOT NULL,
            metadata_json TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
            available INTEGER NOT NULL, UNIQUE(root_id,relative_path)
        );
        CREATE TABLE IF NOT EXISTS catalog_match_decisions (
            snapshot_id TEXT NOT NULL, song_id TEXT NOT NULL,
            revision INTEGER NOT NULL, file_id TEXT REFERENCES library_files(id),
            content_hash TEXT, note TEXT NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY(snapshot_id,song_id),
            FOREIGN KEY(snapshot_id,song_id) REFERENCES catalog_songs(snapshot_id,source_id)
        );
        CREATE TABLE IF NOT EXISTS catalog_match_receipts (
            request_id TEXT PRIMARY KEY, body_json TEXT NOT NULL, receipt_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS catalog_staged_tracks (
            snapshot_id TEXT NOT NULL, song_id TEXT NOT NULL, decision_revision INTEGER NOT NULL,
            track_hash TEXT NOT NULL, provenance_json TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(snapshot_id,song_id,decision_revision),
            FOREIGN KEY(snapshot_id,song_id) REFERENCES catalog_songs(snapshot_id,source_id)
        );
        CREATE TABLE IF NOT EXISTS catalog_staging_receipts (
            request_id TEXT PRIMARY KEY, body_json TEXT NOT NULL, receipt_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS catalog_root_reassociations (
            job_id TEXT PRIMARY KEY, root_id TEXT NOT NULL REFERENCES library_roots(id),
            before_json TEXT NOT NULL, after_json TEXT NOT NULL,
            verified_files INTEGER NOT NULL, committed_at REAL NOT NULL
        );
    """)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(library_roots)")}
    if "scan_revision" not in columns:
        db.execute("ALTER TABLE library_roots ADD COLUMN scan_revision INTEGER NOT NULL DEFAULT 0")


def validate_snapshot(document):
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int \
            or document["schema_version"] != 1:
        raise ValueError("Catalog snapshot requires schema_version 1.")
    text(document.get("source"), "source", required=True)
    if not isinstance(document.get("crates"), list) or not document["crates"]:
        raise ValueError("Catalog snapshot requires a nonempty crates array.")
    songs, crate_ids = {}, set()
    for crate in document["crates"]:
        if not isinstance(crate, dict):
            raise ValueError("Each crate must be an object.")
        key = text(crate.get("id"), "crate id", required=True)
        if key in crate_ids:
            raise ValueError("Duplicate crate id: " + key)
        crate_ids.add(key)
        text(crate.get("title"), "crate title", required=True)
        if type(crate.get("available")) is not bool:
            raise ValueError("Each crate requires an explicit available boolean.")
        entries = crate.get("songs")
        if not isinstance(entries, list) or (not crate["available"] and entries):
            raise ValueError("Crate songs must be an array, empty for an unavailable crate.")
        for song in entries:
            if not isinstance(song, dict):
                raise ValueError("Each song must be an object.")
            song_id = text(song.get("id"), "song id", required=True)
            for field in ("artist", "title"):
                text(song.get(field), "song " + field, required=True)
            text(song.get("version", ""), "song version")
            for field in ("bpm", "duration_s"):
                value = song.get(field)
                if value is not None and (type(value) not in (int, float)
                                          or not math.isfinite(value) or value < 0):
                    raise ValueError(f"Song {field} must be a nonnegative finite number or null.")
            encoded = canonical(song)
            if song_id in songs and songs[song_id] != encoded:
                raise ValueError("Conflicting metadata for source song id: " + song_id)
            songs[song_id] = encoded
    canonical(document)
    return songs


def import_snapshot(db, document):
    songs = validate_snapshot(document)
    identity = digest(document)
    db.execute("BEGIN IMMEDIATE")
    previous = db.execute("SELECT id FROM catalog_snapshots WHERE id=?", (identity,)).fetchone()
    if previous:
        return {"snapshot_id": identity, "added": False, "crates": len(document["crates"]),
                "songs": len(songs), "memberships": sum(len(c["songs"]) for c in document["crates"])}
    db.execute("INSERT INTO catalog_snapshots VALUES(?,?,?,?)",
               (identity, document["source"], canonical(document), time.time()))
    for key, encoded in songs.items():
        song = json.loads(encoded)
        db.execute("INSERT INTO catalog_songs VALUES(?,?,?,?,?,?)",
                   (identity, key, song["artist"], song["title"], song.get("version", ""), encoded))
    for position, crate in enumerate(document["crates"]):
        db.execute("INSERT INTO catalog_crates VALUES(?,?,?,?,?,?)",
                   (identity, crate["id"], crate["title"], int(crate["available"]),
                    canonical({key: value for key, value in crate.items() if key != "songs"}), position))
        db.executemany("INSERT INTO catalog_memberships VALUES(?,?,?,?)",
                       [(identity, crate["id"], index, song["id"]) for index, song in enumerate(crate["songs"])])
    return {"snapshot_id": identity, "added": True, "crates": len(document["crates"]),
            "songs": len(songs), "memberships": sum(len(c["songs"]) for c in document["crates"])}


def snapshots(db):
    return [dict(row) for row in db.execute(
        "SELECT id,source,imported_at FROM catalog_snapshots ORDER BY imported_at DESC,id")]


def crates(db, snapshot_id, query=""):
    rows = db.execute("""
        SELECT c.*,COUNT(m.position) AS songs FROM catalog_crates c
        LEFT JOIN catalog_memberships m ON m.snapshot_id=c.snapshot_id AND m.crate_id=c.source_id
        WHERE c.snapshot_id=? GROUP BY c.source_id ORDER BY c.position
    """, (snapshot_id,))
    return [{**dict(row), "metadata": json.loads(row["metadata_json"])}
            for row in rows if query.casefold() in row["title"].casefold()]


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def version_title(song):
    return normalize(song["title"] + " " + song.get("version", ""))


def base_title(title):
    # Used only to find candidates; stripping version text never certifies a match.
    return normalize(re.sub(r"\([^)]*\)|\[[^]]*\]", "", title))


def read_metadata(path):
    audio = File(path, easy=True)
    if audio is None:
        raise ValueError("Unsupported or unreadable audio metadata.")
    def tag(key):
        values = audio.get(key) or [""]
        value = str(values[0])
        if not value and audio.tags is not None:
            id3_key = {"artist": "TPE1", "title": "TIT2", "version": "TXXX:VERSION",
                       "bpm": "TBPM", "initialkey": "TKEY"}[key]
            value = str(audio.tags.get(id3_key, ""))
        return value
    return {"artist": tag("artist"), "title": tag("title"), "version": tag("version"),
            "bpm": tag("bpm"), "key": tag("initialkey"),
            "duration_s": float(audio.info.length) if audio.info else None,
            "provenance": "file_tags"}


def file_hash(path, checkpoint=None):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if checkpoint:
                checkpoint()
            value.update(block)
    return value.hexdigest()


def signature(stat):
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def root_status(row):
    path = Path(row["path"])
    try:
        stat = path.stat()
        if not path.is_dir() or (stat.st_dev, stat.st_ino) != (row["device"], row["inode"]):
            return "offline", "Folder identity changed; select the original folder/volume."
        return "online", ""
    except OSError as error:
        return "offline", str(error)


def roots(db):
    result = []
    for row in db.execute("SELECT * FROM library_roots ORDER BY path"):
        state, error = root_status(row)
        result.append({**dict(row), "state": state, "availability_error": error})
    return result


def scan_folder(db, path_text, metadata_reader=None, *, checkpoint=None, progress=None, expected_identity=None,
                reassociation=None):
    metadata_reader = metadata_reader or read_metadata
    text(path_text, "folder path", required=True)
    requested = Path(path_text).expanduser()
    if not requested.is_absolute():
        raise ValueError("Select an absolute folder path.")
    path = requested.resolve()
    if expected_identity is not None:
        stat = path.stat()
        if (stat.st_dev, stat.st_ino) != expected_identity:
            raise Conflict("Selected folder identity changed since this scan was queued.")
    original_root = None
    if reassociation:
        original_root = db.execute("SELECT * FROM library_roots WHERE id=?",
                                   (reassociation["root_id"],)).fetchone()
        if not original_root or original_root["scan_revision"] != reassociation["base_revision"]:
            raise Conflict("Indexed folder revision changed; refresh before reassociating.")
        if root_status(original_root)[0] != "offline":
            raise Conflict("Original indexed folder is online; reassociation is only for offline roots.")
        stat = path.stat()
        if expected_identity is not None and (stat.st_dev, stat.st_ino) != expected_identity:
            raise Conflict("Replacement folder identity changed before verification.")
        root = {**dict(original_root), "path": str(path), "device": stat.st_dev, "inode": stat.st_ino}
    else:
        root = db.execute("SELECT * FROM library_roots WHERE path=?", (str(path),)).fetchone()
    existing_root = root is not None
    if root:
        state, error = root_status(root)
        if state == "offline":
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE library_roots SET error=? WHERE id=?", (error, root["id"]))
            return {"root_id": root["id"], "state": state, "error": error, "scanned": 0}
    else:
        stat = path.stat()
        if not path.is_dir():
            raise ValueError("Library index requires a folder, not a file.")
        # Nested/overlapping roots create confusing duplicate location identities.
        for other in db.execute("SELECT path FROM library_roots"):
            existing = Path(other["path"])
            if path.is_relative_to(existing) or existing.is_relative_to(path):
                raise ValueError("Selected folder overlaps an indexed root.")
        identity = str(uuid.uuid4())
        root = {"id": identity, "path": str(path), "device": stat.st_dev, "inode": stat.st_ino,
                "scanned_at": None, "scan_revision": 0}
    previous = {row["relative_path"]: row for row in db.execute(
        "SELECT * FROM library_files WHERE root_id=?", (root["id"],))}
    required = {relative: old for relative, old in previous.items() if old["available"]}
    if reassociation and (not required or any(not old["content_hash"] or old["error"] for old in required.values())):
        raise Conflict("Reassociation requires a nonempty, successfully hashed previous index; errored files cannot prove identity.")
    seen, errors, skipped, reused, updates = set(), [], [], 0, []

    def report(phase, current_path=""):
        if checkpoint:
            checkpoint()
        if progress:
            progress({"phase": phase, "scanned": len(seen), "reused": reused,
                      "errors": len(errors), "skipped": len(skipped), "current_path": current_path})

    def walk_error(error):
        raise error

    report("walking")
    for folder, directories, filenames in os.walk(path, followlinks=False, onerror=walk_error):
        report("walking", str(Path(folder).relative_to(path)))
        for directory in list(directories):
            entry = Path(folder) / directory
            if entry.is_symlink():
                skipped.append(str(entry.relative_to(path)))
                directories.remove(directory)
        for name in sorted(filenames):
            if checkpoint:
                checkpoint()
            entry = Path(folder) / name
            if entry.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            relative = str(entry.relative_to(path))
            if entry.is_symlink():
                skipped.append(relative)
                continue
            seen.add(relative)
            if len(seen) > MAX_FILES:
                raise ValueError(f"Initial scanner supports at most {MAX_FILES} audio files per root.")
            report("indexing", relative)
            before = entry.stat()
            if not entry.is_file():
                raise ValueError("Audio path is not a regular file: " + relative)
            old = previous.get(relative)
            if not reassociation and old and signature(before) == (old["size"], old["mtime_ns"], old["ctime_ns"]) and not old["error"]:
                reused += 1
                continue
            identity = old["id"] if old else str(uuid.uuid4())
            error, metadata, content_hash = "", {}, None
            try:
                content_hash = file_hash(entry, checkpoint) if checkpoint else file_hash(entry)
                metadata = metadata_reader(entry)
                if signature(entry.stat()) != signature(before):
                    raise OSError("File changed during scan; retry after the file is stable.")
            except (OSError, ValueError, MutagenError) as failure:
                error = str(failure) or type(failure).__name__
                errors.append({"path": relative, "error": error})
            if reassociation and old and (error or not old["content_hash"] or content_hash != old["content_hash"]):
                raise Conflict("Replacement file does not prove the indexed bytes: " + relative)
            updates.append((identity, root["id"], relative, content_hash, *signature(before), canonical(metadata), error))
    if reassociation:
        missing = required.keys() - seen
        if missing:
            raise Conflict("Replacement folder is missing indexed files: " + ", ".join(sorted(missing)[:10]))
        # Every proof file must remain stable until the publication gate, not just
        # during its own hash read. The filesystem is not transactionally locked.
        for update in updates:
            if checkpoint:
                checkpoint()
            entry = path / update[2]
            if entry.is_symlink() or not entry.resolve().is_relative_to(path) or signature(entry.stat()) != update[4:7]:
                raise Conflict("Replacement file changed before publication: " + update[2])
    report("publishing")
    if root_status(root)[0] != "online":
        raise OSError("Folder disconnected during scan; previous index was preserved.")
    # Hashing/tag parsing never holds the database write lock used by cue review.
    db.execute("BEGIN IMMEDIATE")
    if checkpoint:
        checkpoint()
    current = (db.execute("SELECT * FROM library_roots WHERE id=?", (root["id"],)).fetchone() if reassociation
               else db.execute("SELECT * FROM library_roots WHERE path=?", (str(path),)).fetchone())
    if (existing_root and (not current or current["scan_revision"] != root["scan_revision"])) \
            or (not existing_root and current):
        raise Conflict("Another scan finished for this folder; retry against the latest index.")
    if reassociation:
        if root_status(root)[0] != "online":
            raise Conflict("Replacement folder disconnected before publication.")
        if root_status(current)[0] != "offline":
            raise Conflict("Original folder returned online; reassociation was not applied.")
        for other in db.execute("SELECT id,path FROM library_roots WHERE id!=?", (root["id"],)):
            existing = Path(other["path"])
            if path.is_relative_to(existing) or existing.is_relative_to(path):
                raise Conflict("Replacement folder overlaps another indexed root.")
        db.execute("UPDATE library_roots SET path=?,device=?,inode=? WHERE id=?",
                   (str(path), root["device"], root["inode"], root["id"]))
        db.execute("INSERT INTO catalog_root_reassociations VALUES(?,?,?,?,?,?)",
                   (reassociation["job_id"], root["id"], canonical(dict(current)),
                    canonical({"path": str(path), "device": root["device"], "inode": root["inode"],
                               "scan_revision": root["scan_revision"] + 1}), len(required), time.time()))
    if not existing_root:
        for other in db.execute("SELECT path FROM library_roots"):
            existing = Path(other["path"])
            if path.is_relative_to(existing) or existing.is_relative_to(path):
                raise Conflict("Another scan added an overlapping root; reload indexed folders.")
        db.execute("INSERT INTO library_roots(id,path,device,inode) VALUES(?,?,?,?)",
                   (root["id"], str(path), root["device"], root["inode"]))
    for update in updates:
        db.execute("""
                INSERT INTO library_files VALUES(?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(root_id,relative_path) DO UPDATE SET
                    content_hash=excluded.content_hash,size=excluded.size,mtime_ns=excluded.mtime_ns,
                    ctime_ns=excluded.ctime_ns,metadata_json=excluded.metadata_json,
                    error=excluded.error,available=1
            """, update)
    for relative, old in previous.items():
        db.execute("UPDATE library_files SET available=? WHERE id=?", (int(relative in seen), old["id"]))
    db.execute("UPDATE library_roots SET scanned_at=?,error='',scan_revision=scan_revision+1 WHERE id=?",
               (time.time(), root["id"]))
    return {"root_id": root["id"], "state": "online", "scanned": len(seen),
            "reused": reused, "errors": errors, "skipped_symlinks": skipped,
            **({"reassociated": True, "verified_files": len(required)} if reassociation else {})}


def file_status(row, root):
    if root_status(root)[0] != "online":
        return "offline"
    if not row["available"]:
        return "missing"
    if row["error"]:
        return "error"
    path = Path(root["path"]) / row["relative_path"]
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(Path(root["path"])):
            return "changed"
        if signature(path.stat()) != (row["size"], row["mtime_ns"], row["ctime_ns"]):
            return "changed"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    return "available"


def file_record(row, root):
    return {**dict(row), "metadata": json.loads(row["metadata_json"]),
            "path": str(Path(root["path"]) / row["relative_path"]),
            "state": file_status(row, root)}


def file_records(db):
    root_map = {row["id"]: row for row in db.execute("SELECT * FROM library_roots")}
    return [file_record(row, root_map[row["root_id"]])
            for row in db.execute("SELECT * FROM library_files ORDER BY root_id,relative_path")]


def reviewed_file(db, body):
    decision = db.execute("SELECT * FROM catalog_match_decisions WHERE snapshot_id=? AND song_id=?",
                          (body["snapshot_id"], body["song_id"])).fetchone()
    if not decision or (decision["revision"], decision["file_id"], decision["content_hash"]) != (
            body["decision_revision"], body["file_id"], body["content_hash"]):
        raise Conflict("Reviewed match changed or is missing; reload the crate before staging.")
    row = db.execute("SELECT * FROM library_files WHERE id=?", (decision["file_id"],)).fetchone()
    if not row:
        raise Conflict("Reviewed file is no longer indexed.")
    root = db.execute("SELECT * FROM library_roots WHERE id=?", (row["root_id"],)).fetchone()
    file = file_record(row, root)
    if file["state"] != "available" or file["content_hash"] != decision["content_hash"]:
        raise Conflict("Reviewed file changed or is unavailable; rescan and review before staging.")
    return file, dict(decision)


def candidates(song, files):
    found = []
    for file in files:
        metadata = file["metadata"]
        if not metadata.get("artist") or not metadata.get("title") or file["error"]:
            continue
        if normalize(metadata["artist"]) != normalize(song["artist"]):
            continue
        same = version_title(metadata) == version_title(song)
        if not same and base_title(metadata["title"]) != base_title(song["title"]):
            continue
        found.append({**file, "metadata_equal": same,
                      "reason": "Artist/title/version tags agree; recording still requires review." if same else
                                "Same song-title family; version text differs. Explicit override required."})
    return sorted(found, key=lambda row: (not row["metadata_equal"], row["path"]))


def songs(db, snapshot_id, crate_id):
    rows = db.execute("""
        SELECT s.*,m.position FROM catalog_memberships m JOIN catalog_songs s
        ON s.snapshot_id=m.snapshot_id AND s.source_id=m.song_id
        WHERE m.snapshot_id=? AND m.crate_id=? ORDER BY m.position
    """, (snapshot_id, crate_id)).fetchall()
    files = file_records(db)
    decisions = {row["song_id"]: dict(row) for row in db.execute(
        "SELECT * FROM catalog_match_decisions WHERE snapshot_id=?", (snapshot_id,))}
    result = []
    for row in rows:
        song = json.loads(row["metadata_json"])
        choices = candidates(song, files)
        decision = decisions.get(row["source_id"])
        chosen = None
        state = "ambiguous" if len(choices) > 1 else "unreviewed" if choices else "missing"
        if decision:
            if decision["file_id"] is None:
                state = "missing"
            else:
                chosen = next((f for f in files if f["id"] == decision["file_id"]), None)
                state = ("exact" if chosen and chosen["state"] == "available"
                         and chosen["content_hash"] == decision["content_hash"] else "stale")
        result.append({"song": song, "position": row["position"], "state": state,
                       "candidates": choices, "decision": decision,
                       "selected_file": chosen,
                       "revision": decision["revision"] if decision else 0})
    return result


def decide(db, body):
    if not isinstance(body, dict):
        raise ValueError("Match decision must be an object.")
    if set(body) - {"request_id", "dataset_id", "snapshot_id", "song_id", "base_revision",
                    "file_id", "content_hash", "override", "note"}:
        raise ValueError("Unknown match decision fields.")
    if "file_id" not in body or "content_hash" not in body:
        raise ValueError("Match decision requires explicit file_id and content_hash (null when missing).")
    for field in ("request_id", "dataset_id", "snapshot_id", "song_id"):
        text(body.get(field), field, required=True)
    text(body.get("note", ""), "note")
    if type(body.get("base_revision")) is not int or body["base_revision"] < 0:
        raise ValueError("base_revision must be a nonnegative integer.")
    if type(body.get("override", False)) is not bool:
        raise ValueError("override must be a boolean.")
    if body.get("file_id") is not None:
        text(body["file_id"], "file_id", required=True)
        text(body.get("content_hash"), "content_hash", required=True)
    elif body["content_hash"] is not None:
        raise ValueError("A missing match must have a null content_hash.")
    encoded = canonical(body)
    db.execute("BEGIN IMMEDIATE")
    receipt = db.execute("SELECT * FROM catalog_match_receipts WHERE request_id=?", (body["request_id"],)).fetchone()
    if receipt:
        if receipt["body_json"] != encoded:
            raise Conflict("Request ID was already used with a different decision.")
        return json.loads(receipt["receipt_json"])
    song = db.execute("SELECT metadata_json FROM catalog_songs WHERE snapshot_id=? AND source_id=?",
                      (body["snapshot_id"], body["song_id"])).fetchone()
    if not song:
        raise ValueError("Catalog song not found in this snapshot.")
    previous = db.execute("SELECT revision FROM catalog_match_decisions WHERE snapshot_id=? AND song_id=?",
                          (body["snapshot_id"], body["song_id"])).fetchone()
    revision = previous["revision"] if previous else 0
    if revision != body["base_revision"]:
        raise Conflict("Match decision changed; reload before reviewing again.")
    file_id, content_hash = body.get("file_id"), None
    if file_id is not None:
        row = db.execute("SELECT * FROM library_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            raise Conflict("Selected file is not indexed.")
        root = db.execute("SELECT * FROM library_roots WHERE id=?", (row["root_id"],)).fetchone()
        file = file_record(row, root)
        if file["state"] != "available" or file["content_hash"] != body["content_hash"]:
            raise Conflict("Selected file is unavailable or changed; rescan before selecting it.")
        # Hash again at acceptance; a metadata suggestion is never proof of file identity.
        if file_hash(Path(file["path"])) != body["content_hash"]:
            raise Conflict("Selected file bytes changed; rescan before selecting it.")
        if file_status(file, root) != "available":
            raise Conflict("Selected file changed while verifying; rescan before selecting it.")
        metadata = file["metadata"]
        catalog_song = json.loads(song["metadata_json"])
        agrees = normalize(metadata.get("artist", "")) == normalize(catalog_song["artist"]) \
            and version_title(metadata) == version_title(catalog_song)
        if not agrees and (not body.get("override") or not body.get("note", "").strip()):
            raise ValueError("Version/metadata conflict requires explicit override and a review note.")
        content_hash = file["content_hash"]
    receipt = {"ok": True, "request_id": body["request_id"], "dataset_id": body["dataset_id"],
               "snapshot_id": body["snapshot_id"],
               "song_id": body["song_id"], "revision": revision + 1,
               "file_id": file_id, "content_hash": content_hash}
    db.execute("""
        INSERT INTO catalog_match_decisions VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(snapshot_id,song_id) DO UPDATE SET revision=excluded.revision,file_id=excluded.file_id,
            content_hash=excluded.content_hash,note=excluded.note,updated_at=excluded.updated_at
    """, (body["snapshot_id"], body["song_id"], revision + 1, file_id, content_hash,
          body.get("note", ""), time.time()))
    db.execute("INSERT INTO catalog_match_receipts VALUES(?,?,?)",
               (body["request_id"], encoded, canonical(receipt)))
    return receipt
