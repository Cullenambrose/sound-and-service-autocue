import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from web import catalog, library_operations


class LibraryOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "library.db"
        self.db = self.connect()
        self.db.execute("PRAGMA foreign_keys=ON")
        catalog.initialize(self.db)
        library_operations.initialize(self.db)
        snapshot = {
            "schema_version": 1,
            "source": "synthetic-only",
            "crates": [{
                "id": "crate-1",
                "title": "Synthetic crate",
                "available": True,
                "songs": [{"id": "song-1", "artist": "Invented Artist",
                           "title": "Invented Song", "version": "Clean"}],
            }],
        }
        with self.db:
            catalog.import_snapshot(self.db, snapshot)
            self.db.execute(
                "INSERT INTO library_roots(id,path,device,inode,scanned_at,error) "
                "VALUES(?,?,?,?,?,?)",
                ("root-online", str(Path(self.temporary.name) / "not-mounted"), 0, 0, 100, ""),
            )
            self.db.execute(
                "INSERT INTO library_roots(id,path,device,inode,scanned_at,error) "
                "VALUES(?,?,?,?,?,?)",
                ("root-offline", str(Path(self.temporary.name) / "offline"), 0, 0, 100,
                 "Synthetic offline state"),
            )
            self.add_file("file-1", "root-online", "a.wav", "sha256-a",
                          {"artist": "Cafe\u0301 Band", "title": "Song (Radio Edit)",
                           "version": "Radio Edit"})
            self.add_file("file-2", "root-online", "b.wav", "sha256-a",
                          {"artist": "Café Band", "title": "Song", "version": "Club Edit"})
            self.add_file("file-3", "root-online", "c.wav", "sha256-c",
                          {"artist": "Café Band", "title": "Song", "version": "Extended"})
            self.add_file("file-4", "root-offline", "d.wav", "sha256-d",
                          {"artist": "Other", "title": "Offline"}, error="Synthetic read error")
            self.add_file("file-5", "root-online", "e.wav", None,
                          {"artist": "Other", "title": "Missing"}, available=0)

    def connect(self):
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        return db

    def add_file(self, file_id, root_id, relative_path, content_hash, metadata,
                 *, available=1, error=""):
        self.db.execute(
            "INSERT INTO library_files "
            "(id,root_id,relative_path,content_hash,size,mtime_ns,ctime_ns,metadata_json,error,available) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (file_id, root_id, relative_path, content_hash, 100, 1, 1,
             json.dumps(metadata), error, available),
        )

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def play_body(self, **changes):
        return {
            "request_id": "play-1",
            "dataset_id": "synthetic-dataset",
            "file_id": "file-1",
            "played_at": "2026-11-12T10:00:00Z",
            "note": "Manual synthetic observation",
            **changes,
        }

    def test_health_is_database_only_and_distinguishes_hashes_from_related_edits(self):
        with patch.object(catalog, "root_status", side_effect=AssertionError("filesystem probe")), \
                patch.object(catalog, "file_status", side_effect=AssertionError("filesystem probe")), \
                patch.object(Path, "stat", side_effect=AssertionError("filesystem probe")):
            result = library_operations.health(self.db)

        self.assertEqual(result["basis"], "indexed_snapshot_only")
        self.assertFalse(result["fresh_validation_performed"])
        self.assertEqual(result["counts"]["files"], 5)
        self.assertEqual(result["counts"]["available_at_last_scan"], 4)
        self.assertEqual(result["counts"]["missing_at_last_scan"], 1)
        self.assertEqual(result["counts"]["files_under_roots_offline_at_last_check"], 1)
        self.assertEqual(result["root_states"]["offline_at_last_check"], 1)
        self.assertEqual(result["counts"]["exact_hash_duplicate_groups"], 1)
        exact = result["duplicate_groups"][0]
        self.assertEqual(exact["evidence"], "whole_file_sha256")
        self.assertEqual(exact["identity"], "sha256-a")
        self.assertEqual(set(exact["file_ids"]), {"file-1", "file-2"})
        self.assertIn("make any copy disposable", exact["description"])
        related = result["related_edit_groups"][0]
        self.assertEqual(related["evidence"], "catalog_matcher_identity")
        self.assertEqual(set(related["file_ids"]), {"file-1", "file-2", "file-3"})
        self.assertIn("metadata-family", related["description"])
        self.assertEqual({row["file_id"] for row in related["files"]}, set(related["file_ids"]))

    def test_health_uses_current_indexed_metadata_and_presents_stored_errors(self):
        self.db.execute("UPDATE library_files SET metadata_json=? WHERE id='file-2'",
                        (json.dumps({"artist": "Different Band", "title": "Song"}),))
        self.db.execute("UPDATE library_files SET metadata_json='not-json' WHERE id='file-4'")
        self.db.commit()
        result = library_operations.health(self.db)
        self.assertEqual(result["counts"]["exact_hash_duplicate_groups"], 1)
        self.assertEqual(result["counts"]["related_edit_groups"], 1)
        self.assertEqual(result["related_edit_groups"][0]["file_ids"], ["file-1", "file-3"])
        self.assertEqual(result["counts"]["metadata_errors"], 1)
        self.assertTrue(any(error["scope"] == "file" and error["file_id"] == "file-4"
                            for error in result["errors"]))
        self.assertTrue(any(error["scope"] == "metadata" and error["file_id"] == "file-4"
                            for error in result["errors"]))

    def test_play_history_is_durable_idempotent_and_summarized_per_exact_file(self):
        body = self.play_body()
        with self.db:
            receipt = library_operations.record_play(self.db, body, "synthetic-dataset")
        with self.db:
            self.assertEqual(library_operations.record_play(self.db, body, "synthetic-dataset"), receipt)

        self.db.close()
        self.db = self.connect()
        self.assertEqual(library_operations.play_history(self.db), [{
            "request_id": "play-1",
            "dataset_id": "synthetic-dataset",
            "file_id": "file-1",
            "played_at": "2026-11-12T10:00:00.000000Z",
            "note": "Manual synthetic observation",
        }])
        self.assertEqual(library_operations.history_summary(self.db), {
            "file-1": {"play_count": 1, "last_played": "2026-11-12T10:00:00.000000Z"}
        })
        self.assertEqual(library_operations.history_summary(self.db)["file-1"]["play_count"], 1)

        with self.assertRaises(catalog.Conflict):
            with self.db:
                library_operations.record_play(
                    self.db, {**body, "note": "different"}, "synthetic-dataset")

    def test_play_history_aggregates_events_and_orders_by_played_at(self):
        with self.db:
            library_operations.record_play(self.db, self.play_body(), "synthetic-dataset")
            library_operations.record_play(
                self.db, self.play_body(request_id="play-2", played_at="2026-11-13T10:00:00+00:00",
                                        file_id="file-2"), "synthetic-dataset")
            library_operations.record_play(
                self.db, self.play_body(request_id="play-3", played_at="2026-11-14T10:00:00Z"),
                "synthetic-dataset")
        self.assertEqual(library_operations.history_summary(self.db), {
            "file-1": {"play_count": 2, "last_played": "2026-11-14T10:00:00.000000Z"},
            "file-2": {"play_count": 1, "last_played": "2026-11-13T10:00:00.000000Z"},
        })
        self.assertEqual(library_operations.play_history(self.db, limit=2)[0]["request_id"], "play-3")

    def test_play_history_validates_dataset_exact_file_timestamp_and_fields(self):
        body = self.play_body()
        with self.assertRaises(catalog.Conflict):
            with self.db:
                library_operations.record_play(self.db, body, "another-dataset")
        for invalid in (
            {**body, "file_id": "unknown"},
            {key: value for key, value in body.items() if key != "file_id"},
            {**body, "title": "ambiguous display name"},
            {**body, "played_at": "2026-11-12T10:00:00"},
            {**body, "played_at": "not-a-date"},
            {**body, "played_at": "2026-11-12T10:00:00+01:00"},
            {**body, "note": "x" * (library_operations.MAX_NOTE_LENGTH + 1)},
            {**body, "request_id": ""},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                with self.db:
                    library_operations.record_play(self.db, invalid, "synthetic-dataset")
        self.assertEqual(library_operations.history_summary(self.db), {})

    def test_receipt_conflicts_and_storage_rollback_can_be_retried(self):
        body = self.play_body()
        self.db.execute("""
            CREATE TRIGGER fail_play_receipt BEFORE INSERT ON library_play_events
            BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db:
                library_operations.record_play(self.db, body, "synthetic-dataset")
        self.assertEqual(library_operations.history_summary(self.db), {})
        self.db.execute("DROP TRIGGER fail_play_receipt")
        self.db.commit()
        with self.db:
            receipt = library_operations.record_play(self.db, body, "synthetic-dataset")
        self.assertTrue(receipt["ok"])
        self.assertEqual(library_operations.history_summary(self.db)["file-1"]["play_count"], 1)

    def test_history_limit_is_bounded_and_rejects_non_integer_values(self):
        for invalid in (0, -1, library_operations.MAX_HISTORY_LIMIT + 1, True, 1.5, "2"):
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                library_operations.play_history(self.db, limit=invalid)
        self.assertEqual(library_operations.play_history(self.db, limit=1), [])


if __name__ == "__main__":
    unittest.main()
