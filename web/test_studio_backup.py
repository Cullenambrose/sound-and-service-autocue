from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from web import catalog, library_operations, personal_crates, studio_assistant, studio_backup
from web import studio_ingest, studio_service


class StudioBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "original"
        self.data.mkdir()
        self.path = self.data / "library.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        for module in (catalog, personal_crates, library_operations, studio_service,
                       studio_ingest, studio_assistant):
            module.initialize(self.db)
        self.db.execute("INSERT INTO library_roots(id,path,device,inode) VALUES('root',?,1,1)",
                        (str(self.root / "untouched-music"),))
        self.db.execute("INSERT INTO library_files VALUES('file','root','track.wav','hash',100,1,1,?,'',1)",
                        (json.dumps({"title": "Synthetic Song", "artist": "Synthetic Artist"}),))
        self.db.execute("INSERT INTO studio_rules VALUES('rule','Preserved','',1,'{}')")
        self.db.execute("INSERT INTO studio_conversations VALUES('chat',1,'[]')")
        self.db.execute("INSERT INTO studio_receipts VALUES('receipt','{}','{\"ok\":true}')")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def backup(self):
        with closing(sqlite3.connect(self.path)) as db:
            return studio_backup.create(db)

    def alter(self, bundle, *, change_manifest=None, change_database=None, extra=None):
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            database = archive.read("library.sqlite3")
        if change_manifest:
            change_manifest(manifest)
        if change_database:
            database = change_database(database)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("library.sqlite3", database)
            if extra:
                if extra[0] == "library.sqlite3":
                    with self.assertWarnsRegex(UserWarning, "Duplicate name"):
                        archive.writestr(*extra)
                else:
                    archive.writestr(*extra)
        return output.getvalue()

    def test_wal_snapshot_preserves_exact_state_and_restores_without_audio(self):
        self.assertTrue(Path(str(self.path) + "-wal").stat().st_size > 0)
        with patch.object(catalog, "file_hash", side_effect=AssertionError("No audio hashing")), \
                patch.object(catalog, "file_record", side_effect=AssertionError("No source probes")):
            bundle, manifest = self.backup()
        database, checked = studio_backup.inspect(bundle)
        self.assertEqual(checked, manifest)
        self.assertEqual(manifest["database_bytes"], len(database))
        destination = self.root / "restored"
        self.assertEqual(studio_backup.restore(bundle, destination), manifest)
        with closing(sqlite3.connect(destination / "library.sqlite3")) as restored:
            for table in manifest["table_counts"]:
                self.assertEqual(list(map(tuple, self.db.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))),
                                 restored.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall())
            self.assertEqual(restored.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual({item.name for item in destination.iterdir()}, {"library.sqlite3"})
        self.assertEqual((destination / "library.sqlite3").stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / "untouched-music").exists())

    def test_pending_writes_are_rejected_but_other_connection_copies_committed_state(self):
        self.db.execute("INSERT INTO studio_rules VALUES('pending','Uncommitted','',1,'{}')")
        with self.assertRaises(catalog.Conflict):
            studio_backup.create(self.db)
        bundle, manifest = self.backup()
        self.assertEqual(manifest["table_counts"]["studio_rules"], 1)
        self.db.rollback()
        self.assertEqual(studio_backup.inspect(bundle)[1], manifest)

    def test_corruption_future_formats_duplicates_and_counts_are_rejected_before_restore(self):
        bundle, _ = self.backup()
        bad = [
            b"not zip",
            self.alter(bundle, change_database=lambda value: value[:-1]),
            self.alter(bundle, change_manifest=lambda value: value.update(schema_version=2)),
            self.alter(bundle, change_manifest=lambda value: value.update(schema_version=True)),
            self.alter(bundle, change_manifest=lambda value: value.update(note=float("nan"))),
            self.alter(bundle, change_manifest=lambda value: value["table_counts"].update(studio_rules=2)),
            self.alter(bundle, change_manifest=lambda value: value["table_counts"].update(studio_rules=True)),
            self.alter(bundle, extra=("../outside.sqlite3", b"never extracted")),
            self.alter(bundle, extra=("library.sqlite3", b"duplicate")),
        ]
        for index, value in enumerate(bad):
            destination = self.root / f"rejected-{index}"
            with self.subTest(index=index), self.assertRaises(ValueError):
                studio_backup.restore(value, destination)
            self.assertFalse(destination.exists())
        self.assertFalse((self.root / "outside.sqlite3").exists())

    def test_bounds_and_existing_paths_never_overwrite(self):
        bundle, _ = self.backup()
        with patch.object(studio_backup, "MAX_DATABASE_BYTES", 1), self.assertRaises(ValueError):
            self.backup()
        with patch.object(studio_backup, "MAX_BUNDLE_BYTES", 1), self.assertRaises(ValueError):
            studio_backup.inspect(bundle)
        with patch.object(studio_backup.time, "monotonic", side_effect=(0, 31)), \
                self.assertRaises(TimeoutError):
            self.backup()
        before = self.path.read_bytes()
        with self.assertRaises(catalog.Conflict):
            studio_backup.restore(bundle, self.data)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            studio_backup.restore(bundle, "relative")
        link = self.root / "symlink"
        link.symlink_to(self.root / "missing")
        with self.assertRaises(catalog.Conflict):
            studio_backup.restore(bundle, link)
        archive = self.root / "existing.zip"
        archive.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            studio_backup.save(bundle, archive)
        self.assertEqual(archive.read_bytes(), b"original")

    def test_disk_and_publication_faults_leave_prior_state_and_no_partial_destination(self):
        bundle, _ = self.backup()
        for operation in ("fsync", "link"):
            destination = self.root / f"failure-{operation}"
            with patch.object(studio_backup.os, operation, side_effect=OSError("synthetic disk failure")):
                with self.assertRaises(OSError):
                    studio_backup.restore(bundle, destination)
                with self.assertRaises(OSError):
                    studio_backup.save(bundle, destination.with_suffix(".zip"))
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".zip").exists())
        self.assertFalse(any(item.name.startswith((".studio-restore-", ".studio-backup-"))
                             for item in self.root.iterdir()))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_rules").fetchone()[0], 1)

    def test_console_backup_restore_and_restart_schema_roundtrip(self):
        backup = self.root / "console.zip"
        destination = self.root / "console-restored"
        for arguments in (("--data", str(self.data), "--backup", str(backup)),
                          ("--data", str(destination), "--restore", str(backup)),
                          ("--data", str(destination), "--check")):
            completed = subprocess.run([sys.executable, "-m", "web.studio", *arguments],
                                       capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertIn("table_counts" if "--check" not in arguments else "release_gates", result)
        with closing(sqlite3.connect(destination / "library.sqlite3")) as restored:
            self.assertEqual(restored.execute("SELECT name FROM studio_rules").fetchone()[0], "Preserved")


if __name__ == "__main__":
    unittest.main()
