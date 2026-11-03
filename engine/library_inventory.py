#!/usr/bin/env python3
"""Read-only, resumable metadata inventory. No audio decoding, copying, models or network."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import sys
import time
import uuid

from mutagen import File, MutagenError
from imported_anchors import read_annotations

SCHEMA_VERSION = 1
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aiff", ".aif", ".ogg", ".opus"}
SCHEMA = """
CREATE TABLE inventory_schema (version INTEGER NOT NULL);
INSERT INTO inventory_schema VALUES (1);
CREATE TABLE scans (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at REAL NOT NULL,
    updated_at REAL NOT NULL, error TEXT
);
CREATE TABLE roots (
    scan_id TEXT NOT NULL REFERENCES scans(id), id INTEGER NOT NULL,
    path TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
    PRIMARY KEY (scan_id,id)
);
CREATE TABLE entries (
    scan_id TEXT NOT NULL, root_id INTEGER NOT NULL, relative_path TEXT NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL,
    size_bytes INTEGER, mtime_ns INTEGER, ctime_ns INTEGER, device INTEGER, inode INTEGER,
    metadata_json TEXT, error TEXT,
    PRIMARY KEY (scan_id,root_id,relative_path),
    FOREIGN KEY (scan_id,root_id) REFERENCES roots(scan_id,id)
);
CREATE INDEX pending_entries ON entries(scan_id,status,kind,root_id,relative_path);
"""


def _database_path(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError("Inventory database must not be a symlink.")
    path = path.resolve()
    if path.suffix.lower() not in {".sqlite3", ".sqlite", ".db"}:
        raise ValueError("Use a dedicated .sqlite3, .sqlite or .db inventory database.")
    return path


def _check_output(database, roots):
    for root in roots:
        if database == root or root in database.parents:
            raise ValueError("Inventory database must be outside every source root.")


@contextmanager
def _database(database, *, create=False):
    database = _database_path(database)
    if create:
        database.parent.mkdir(parents=True, exist_ok=True)
    elif not database.is_file():
        raise ValueError("Inventory database does not exist.")
    lock = database.with_name(database.name + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as locking:
        try:
            fcntl.flock(locking, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another inventory operation is using this database.") from error
        new = not database.exists()
        if new:
            descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
        db = sqlite3.connect(database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if new:
                db.executescript(SCHEMA)
            else:
                version = db.execute("SELECT version FROM inventory_schema").fetchone()
                if not version or version["version"] != SCHEMA_VERSION:
                    raise ValueError("Unsupported inventory schema; use a separate database.")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        except sqlite3.OperationalError as error:
            raise ValueError("Not a supported AutoCue inventory database: " + str(error)) from error
        finally:
            db.close()


def create_scan(database, roots):
    database = _database_path(database)
    selected = []
    for value in roots:
        path = Path(value).expanduser()
        if path.is_symlink():
            raise ValueError("Source roots must be directories, not symlinks.")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Source root is not a directory: " + str(path))
        if any(path == other or path in other.parents or other in path.parents for other in selected):
            raise ValueError("Source roots must not duplicate or contain one another.")
        selected.append(path)
    if not selected:
        raise ValueError("At least one explicit source root is required.")
    _check_output(database, selected)
    scan_id = uuid.uuid4().hex
    now = time.time()
    with _database(database, create=True) as db, db:
        db.execute("INSERT INTO scans VALUES (?,?,?,?,NULL)", (scan_id, "queued", now, now))
        for index, path in enumerate(selected):
            info = path.stat()
            db.execute("INSERT INTO roots VALUES (?,?,?,?,?)",
                       (scan_id, index, str(path), info.st_dev, info.st_ino))
            db.execute("""INSERT INTO entries(scan_id,root_id,relative_path,kind,status)
                          VALUES (?,?,'','directory','queued')""", (scan_id, index))
    return scan_id


def _open_beneath(root, relative_path, *, directory=False):
    """Walk through directory descriptors so queued paths cannot follow substituted symlinks."""
    parts = PurePosixPath(relative_path).parts
    if any(part in {".", "..", "/"} for part in parts):
        raise ValueError("Invalid root-relative inventory path.")
    descriptor = os.open(root["path"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != (root["device"], root["inode"]):
            raise OSError("Source root identity changed; start a new scan.")
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _fingerprint(info):
    return (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino)


def read_metadata(incoming):
    audio = File(incoming, easy=True)
    if audio is None or audio.info is None:
        raise ValueError("No supported audio metadata/header found.")
    tags = audio.tags or {}
    fields = {}
    for key, alternate in (
        ("title", "TIT2"), ("artist", "TPE1"), ("album", "TALB"),
        ("genre", "TCON"), ("date", "TDRC"), ("bpm", "TBPM"), ("initialkey", "TKEY"),
    ):
        value = tags.get(key)
        if value is None:
            value = getattr(tags.get(alternate), "text", [])
        fields[key] = [str(item) for item in value] if isinstance(value, (list, tuple)) else [str(value)]
    for key, attribute in (("duration_s", "length"), ("sample_rate", "sample_rate"),
                           ("channels", "channels"), ("bitrate", "bitrate")):
        value = getattr(audio.info, attribute, None)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError("Invalid audio metadata field: " + key)
        fields[key] = value
    fields["container"] = type(audio).__name__
    incoming.seek(0)
    native = File(incoming, easy=False)
    if native is None:
        raise ValueError("Could not read native tags for annotation inventory.")
    fields["imported_anchors"] = read_annotations(native, fields["duration_s"])
    fields["extractor"] = "mutagen_metadata_v2"
    return fields


def _summary(db, scan_id):
    scan = db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if scan is None:
        raise ValueError("Unknown inventory scan: " + scan_id)
    counts = {}
    for row in db.execute("""SELECT kind,status,COUNT(*) AS count FROM entries
                             WHERE scan_id=? GROUP BY kind,status""", (scan_id,)):
        counts.setdefault(row["kind"], {})[row["status"]] = row["count"]
    error_count = sum(states.get("error", 0) for states in counts.values())
    annotation_counts = db.execute("""SELECT
        COALESCE(SUM(json_extract(metadata_json,'$.imported_anchors.error_count')),0),
        COALESCE(SUM(json_extract(metadata_json,'$.imported_anchors.unsupported_count')),0)
        FROM entries WHERE scan_id=?""", (scan_id,)).fetchone()
    return {**dict(scan), "schema_version": SCHEMA_VERSION, "counts": counts,
            "annotation_error_count": annotation_counts[0],
            "annotation_unsupported_count": annotation_counts[1],
            "annotation_issues_truncated": sum(annotation_counts) > 20,
            "annotation_issues": [dict(row) for row in db.execute("""
                SELECT root_id,relative_path,
                    json_extract(source.value,'$.field') AS field,
                    json_extract(source.value,'$.status') AS status,
                    COALESCE(json_extract(source.value,'$.error'),
                             json_extract(source.value,'$.diagnostic')) AS diagnostic
                FROM entries, json_each(metadata_json,'$.imported_anchors.sources') AS source
                WHERE scan_id=? AND json_extract(source.value,'$.status') IN ('error','unsupported')
                ORDER BY root_id,relative_path,source.key LIMIT 20""", (scan_id,))],
            "error_count": error_count, "errors_truncated": error_count > 20,
            "errors": [dict(row) for row in db.execute(
                """SELECT root_id,relative_path,error FROM entries
                   WHERE scan_id=? AND status='error' ORDER BY root_id,relative_path LIMIT 20""", (scan_id,))],
            "roots": [dict(row) for row in db.execute(
                "SELECT id,path FROM roots WHERE scan_id=? ORDER BY id", (scan_id,))],
            "audio_bytes": db.execute(
                "SELECT COALESCE(SUM(size_bytes),0) FROM entries WHERE scan_id=? AND kind='audio'",
                (scan_id,)).fetchone()[0]}


def status(database, scan_id):
    with _database(database) as db:
        return _summary(db, scan_id)


def list_scans(database):
    with _database(database) as db:
        return [_summary(db, row["id"]) for row in db.execute(
            "SELECT id FROM scans ORDER BY created_at DESC LIMIT 20").fetchall()]


def _expand_directory(db, scan_id, root, entry):
    descriptor = _open_beneath(root, entry["relative_path"], directory=True)
    try:
        with os.scandir(descriptor) as children:
            for child in children:
                relative = str(PurePosixPath(entry["relative_path"]) / child.name)
                try:
                    info = child.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        kind, state, reason = "other", "skipped", "Symlink not followed."
                    elif stat.S_ISDIR(info.st_mode):
                        kind, state, reason = "directory", "queued", None
                    elif not stat.S_ISREG(info.st_mode):
                        kind, state, reason = "other", "skipped", "Not a regular file."
                    elif Path(child.name).suffix.lower() in AUDIO_EXTENSIONS:
                        kind, state, reason = "audio", "queued", None
                    else:
                        kind, state, reason = "other", "skipped", "Unsupported extension."
                    fingerprint = _fingerprint(info)
                except OSError as error:
                    kind, state, reason, fingerprint = "other", "error", str(error), (None,) * 5
                db.execute("""INSERT OR IGNORE INTO entries
                    (scan_id,root_id,relative_path,kind,status,size_bytes,mtime_ns,ctime_ns,
                     device,inode,error) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (scan_id, root["id"], relative, kind, state, *fingerprint, reason))
    finally:
        os.close(descriptor)


def _read_entry(root, entry):
    descriptor = _open_beneath(root, entry["relative_path"])
    with os.fdopen(descriptor, "rb") as incoming:
        before = os.fstat(incoming.fileno())
        expected = tuple(entry[key] for key in ("size_bytes", "mtime_ns", "ctime_ns", "device", "inode"))
        if not stat.S_ISREG(before.st_mode) or _fingerprint(before) != expected:
            raise ValueError("Source changed after discovery; start a new scan.")
        metadata = read_metadata(incoming)
        if _fingerprint(os.fstat(incoming.fileno())) != expected:
            raise ValueError("Source changed while reading metadata; start a new scan.")
    return json.dumps(metadata, ensure_ascii=True, allow_nan=False)


def resume_scan(database, scan_id, *, limit=None, on_progress=None):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Audio-attempt limit must be a positive integer.")
    with _database(database) as db:
        initial = _summary(db, scan_id)
        roots = {row["id"]: row for row in db.execute("SELECT * FROM roots WHERE scan_id=?", (scan_id,))}
        _check_output(_database_path(database), [Path(root["path"]) for root in roots.values()])
        if initial["status"] in {"complete", "complete_with_errors"}:
            return initial
        with db:
            db.execute("UPDATE scans SET status='running',updated_at=?,error=NULL WHERE id=?",
                       (time.time(), scan_id))
        attempts = 0
        try:
            for root in roots.values():
                os.close(_open_beneath(root, "", directory=True))
            if on_progress:
                on_progress(_summary(db, scan_id))
            while True:
                entry = db.execute("""SELECT * FROM entries WHERE scan_id=? AND status='queued'
                    ORDER BY kind,root_id,relative_path LIMIT 1""",
                    (scan_id,)).fetchone()
                if entry is None:
                    errors = db.execute("SELECT COUNT(*) FROM entries WHERE scan_id=? AND status='error'",
                                        (scan_id,)).fetchone()[0]
                    annotation_errors = db.execute("""SELECT
                        COALESCE(SUM(json_extract(metadata_json,'$.imported_anchors.error_count')),0)
                        FROM entries WHERE scan_id=?""", (scan_id,)).fetchone()[0]
                    final = "complete_with_errors" if errors or annotation_errors else "complete"
                    break
                if limit is not None and attempts >= limit:
                    final = "paused"
                    break
                metadata, error, state = None, None, "done"
                with db:
                    try:
                        if entry["kind"] == "directory":
                            _expand_directory(db, scan_id, roots[entry["root_id"]], entry)
                        else:
                            attempts += 1
                            metadata = _read_entry(roots[entry["root_id"]], entry)
                    except (OSError, ValueError, MutagenError) as problem:
                        state, error = "error", str(problem)
                    db.execute("""UPDATE entries SET status=?,metadata_json=?,error=?
                                  WHERE scan_id=? AND root_id=? AND relative_path=?""",
                               (state, metadata, error, scan_id, entry["root_id"], entry["relative_path"]))
                    db.execute("UPDATE scans SET updated_at=? WHERE id=?", (time.time(), scan_id))
                if on_progress and entry["kind"] == "audio" and attempts % 25 == 0:
                    on_progress(_summary(db, scan_id))
        except BaseException as problem:
            with db:
                db.execute("UPDATE scans SET status=?,updated_at=?,error=? WHERE id=?",
                           ("paused" if isinstance(problem, KeyboardInterrupt) else "failed",
                            time.time(), str(problem) or type(problem).__name__, scan_id))
            raise
        with db:
            db.execute("UPDATE scans SET status=?,updated_at=? WHERE id=?", (final, time.time(), scan_id))
        result = _summary(db, scan_id)
        if on_progress:
            on_progress(result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Dedicated local inventory SQLite file outside source roots.")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Start a new scan of explicitly selected directories.")
    scan.add_argument("--root", action="append", required=True, help="Source directory; repeat for disjoint roots.")
    scan.add_argument("--limit", type=int, help="Pause after this many audio-file attempts.")
    resume = commands.add_parser("resume", help="Continue a paused, failed or interrupted scan.")
    resume.add_argument("scan_id")
    resume.add_argument("--limit", type=int)
    summary = commands.add_parser("status", help="Show scan counts without scanning sources.")
    summary.add_argument("scan_id")
    commands.add_parser("list", help="Show the latest 20 scans, including recoverable scan IDs.")
    args = parser.parse_args(argv)
    if getattr(args, "limit", None) is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
    try:
        if args.command == "list":
            print(json.dumps(list_scans(args.database)))
            return 0
        if args.command == "status":
            print(json.dumps(status(args.database, args.scan_id)))
            return 0
        scan_id = create_scan(args.database, args.root) if args.command == "scan" else args.scan_id
        result = resume_scan(args.database, scan_id, limit=args.limit,
                             on_progress=lambda progress: print(json.dumps(progress), flush=True))
        return 2 if result["status"] == "complete_with_errors" else 0
    except KeyboardInterrupt:
        print("Inventory paused; resume using the scan ID in the progress output.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print("Inventory failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
