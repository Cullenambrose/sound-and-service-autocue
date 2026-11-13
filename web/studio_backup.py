"""Consistent app-database snapshots; source media and existing state stay untouched."""
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import zipfile

from . import catalog

MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
NOTE = ("Private app-database backup, including saved crates/rules, receipts, conversations, "
        "settings and indexed evidence. No audio, model weights, exported ZIPs, session token "
        "or browser pending actions are included. Restore only to a new data directory; "
        "indexed availability is historical, not fresh file verification.")


def _inventory(db):
    if [row[0] for row in db.execute("PRAGMA quick_check")] != ["ok"]:
        raise ValueError("Backup database failed SQLite integrity verification.")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Backup database has invalid references.")
    schema = db.execute("""SELECT type,name,tbl_name,sql FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name""").fetchall()
    if any(row[0] == "view" or (row[3] and "CREATE VIRTUAL TABLE" in row[3].upper()) for row in schema):
        raise ValueError("Studio backup does not support views or virtual tables.")
    tables = [row[1] for row in schema if row[0] == "table"]
    if not {"library_files", "library_roots", "personal_crates", "studio_rules"} <= set(tables):
        raise ValueError("Backup is not an initialized Studio database.")
    counts = {name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
              for name in tables}
    return {"schema_digest": catalog.digest([list(row) for row in schema]), "table_counts": counts}


def create(db):
    if db.in_transaction and db.total_changes:
        raise catalog.Conflict("Commit or roll back pending writes before creating a backup.")
    deadline = time.monotonic() + 30
    page_size = db.execute("PRAGMA page_size").fetchone()[0]

    def progress(status, remaining, total):
        if total * page_size > MAX_DATABASE_BYTES:
            raise ValueError("Studio backup database exceeds 256 MiB.")
        if time.monotonic() > deadline:
            raise TimeoutError("Studio backup exceeded 30 seconds; no download was produced.")

    with tempfile.TemporaryDirectory(prefix="studio-database-backup-") as directory:
        path = Path(directory) / "library.sqlite3"
        with closing(sqlite3.connect(path)) as snapshot:
            db.backup(snapshot, pages=256, progress=progress, sleep=.01)
            inventory = _inventory(snapshot)
        database = path.read_bytes()
    manifest = {"format": "autocue-studio-database", "schema_version": 1,
                "database_bytes": len(database), "database_sha256": hashlib.sha256(database).hexdigest(),
                **inventory, "note": NOTE}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("library.sqlite3", database)
        archive.writestr("manifest.json", catalog.canonical(manifest))
    bundle = buffer.getvalue()
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise ValueError("Studio backup archive exceeds 64 MiB.")
    return bundle, manifest


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Backup manifest has duplicate fields.")
        result[key] = value
    return result


def inspect(bundle):
    if not isinstance(bundle, bytes) or len(bundle) > MAX_BUNDLE_BYTES:
        raise ValueError("Backup must be a ZIP of at most 64 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            entries = archive.infolist()
            if len(entries) != 2 or {item.filename for item in entries} != {"library.sqlite3", "manifest.json"}:
                raise ValueError("Backup must contain only library.sqlite3 and manifest.json.")
            for item in entries:
                limit = MAX_MANIFEST_BYTES if item.filename == "manifest.json" else MAX_DATABASE_BYTES
                if item.file_size > limit or item.flag_bits & 1:
                    raise ValueError("Backup contains an oversized or encrypted entry.")
            manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_unique_object)
            database = archive.read("library.sqlite3")
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid Studio backup ZIP or manifest.") from error
    expected = {"format", "schema_version", "database_bytes", "database_sha256", "schema_digest",
                "table_counts", "note"}
    if (not isinstance(manifest, dict) or set(manifest) != expected
            or manifest["format"] != "autocue-studio-database"
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1):
        raise ValueError("Unsupported Studio backup format; no data was restored.")
    catalog.canonical(manifest)
    if not isinstance(manifest["note"], str):
        raise ValueError("Backup note must be text.")
    if (type(manifest["database_bytes"]) is not int or manifest["database_bytes"] != len(database)
            or manifest["database_sha256"] != hashlib.sha256(database).hexdigest()):
        raise ValueError("Backup database does not match its manifest.")
    if (not isinstance(manifest["table_counts"], dict)
            or any(not isinstance(key, str) or type(count) is not int or count < 0
                   for key, count in manifest["table_counts"].items())):
        raise ValueError("Backup table counts must be nonnegative integers.")
    with tempfile.TemporaryDirectory(prefix="studio-backup-check-") as directory:
        path = Path(directory) / "library.sqlite3"
        path.write_bytes(database)
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                deadline = time.monotonic() + 30
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                inventory = _inventory(db)
        except sqlite3.Error as error:
            raise ValueError("Backup database could not be verified: " + str(error)) from error
    if any(manifest[field] != inventory[field] for field in ("schema_digest", "table_counts")):
        raise ValueError("Backup schema or table counts do not match its manifest.")
    return database, manifest


def restore(bundle, destination):
    destination = Path(destination).expanduser()
    if not destination.is_absolute():
        raise ValueError("Restore needs an absolute new data-directory path.")
    destination = destination.parent.resolve(strict=True) / destination.name
    if destination.exists() or destination.is_symlink():
        raise catalog.Conflict("Restore refuses an existing data directory; choose a new empty location.")
    database, manifest = inspect(bundle)
    # Publish only a verified, fsynced database, without replacing any existing path.
    with tempfile.TemporaryDirectory(prefix=".studio-restore-", dir=destination.parent) as directory:
        staging = Path(directory) / "library.sqlite3"
        with staging.open("xb") as output:
            os.chmod(staging, 0o600)
            output.write(database)
            output.flush()
            os.fsync(output.fileno())
        destination.mkdir(mode=0o700)
        try:
            os.link(staging, destination / "library.sqlite3")
        except OSError:
            destination.rmdir()
            raise
        for path in (destination, destination.parent):
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    return manifest


def save(bundle, destination):
    destination = Path(destination).expanduser()
    with tempfile.TemporaryDirectory(prefix=".studio-backup-", dir=destination.parent) as directory:
        staging = Path(directory) / "backup.zip"
        with staging.open("xb") as output:
            os.chmod(staging, 0o600)
            output.write(bundle)
            output.flush()
            os.fsync(output.fileno())
        os.link(staging, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read(source):
    with Path(source).expanduser().open("rb") as file:
        bundle = file.read(MAX_BUNDLE_BYTES + 1)
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise ValueError("Backup ZIP exceeds 64 MiB.")
    return bundle
