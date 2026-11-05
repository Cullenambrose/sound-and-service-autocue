import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_store


class MigrationTests(unittest.TestCase):
    def connection(self, path):
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tracks(hash TEXT PRIMARY KEY,plan_json TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        return db

    def test_migration_is_once_only_preserves_original_and_survives_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "corrections.json"
            original = json.dumps([
                {"track": "01", "cue": "B", "action": "clear", "saved_at": "2"},
                {"track": "01", "cue": "B", "action": "correct", "to_beat": 32, "saved_at": "1"},
            ]).encode()
            legacy.write_bytes(original)
            path = root / "library.sqlite3"
            with self.connection(path) as db:
                review_store.initialize(db, legacy)
                identity = review_store.dataset_id(db)
            with self.connection(path) as db:
                review_store.initialize(db, legacy)
                self.assertEqual(len(review_store.export_events(db)), 2)
                self.assertEqual(review_store.export_events(db)[-1]["action"], "clear")
            self.assertEqual(legacy.read_bytes(), original)
            relocated = root / "moved.sqlite3"
            shutil.copyfile(path, relocated)
            with self.connection(relocated) as db:
                review_store.initialize(db, legacy)
                self.assertEqual(review_store.dataset_id(db), identity)
            with self.connection(root / "other.sqlite3") as db:
                review_store.initialize(db, root / "absent.json")
                self.assertNotEqual(review_store.dataset_id(db), identity)

    def test_invalid_legacy_log_aborts_migration_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "corrections.json"
            legacy.write_text('{"not":"an event list"}')
            db = self.connection(root / "library.sqlite3")
            try:
                with self.assertRaisesRegex(ValueError, "array"):
                    with db:
                        review_store.initialize(db, legacy)
                self.assertIsNone(db.execute("SELECT 1 FROM settings WHERE key='corrections_migrated'").fetchone())
                self.assertEqual(review_store.export_events(db), [])
            finally:
                db.close()
