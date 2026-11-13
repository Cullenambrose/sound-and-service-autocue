import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from web import catalog, library_operations, studio_assistant, studio_history, studio_service


class StudioHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "library.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        for module in (catalog, library_operations, studio_service, studio_assistant, studio_history):
            module.initialize(self.db)
        with self.db:
            self.db.execute("INSERT INTO library_roots(id,path,device,inode) VALUES('root','/synthetic',1,1)")
            for index in range(2):
                self.db.execute("INSERT INTO library_files VALUES(?, 'root', ?, 'hash',100,1,1,?,'',1)",
                    (f"file-{index}", f"{index}.wav", json.dumps({"title": f"Song {index}", "artist": "Artist"})))
        self.dataset = "dataset"
        self.bodies = []
        for index in range(3):
            body = {"request_id": f"play-{index}", "dataset_id": self.dataset,
                    "file_id": f"file-{index % 2}", "played_at": f"2026-11-12T10:0{index}:00Z", "note": f"Note {index}"}
            with self.db:
                library_operations.record_play(self.db, body, self.dataset)
            self.bodies.append(body)

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def preview(self, ids=None):
        with self.db:
            return studio_history.preview(self.db, {"dataset_id": self.dataset,
                                                    "event_ids": ids or ["play-0", "play-2"]}, self.dataset)

    def request(self, preview, operation="approve", request_id=None):
        body = {"dataset_id": self.dataset, "edit_id": preview["edit_id"], "event_ids": preview["event_ids"],
                "request_id": request_id or operation}
        with self.db:
            return getattr(studio_history, operation)(self.db, body, self.dataset)

    def test_review_removal_undo_replay_and_rediscovery_without_source_or_preference_changes(self):
        with self.db:
            self.db.execute("INSERT INTO studio_feedback VALUES('file-0',1,'explicit preference',1)")
        before = library_operations.play_history(self.db)
        with patch.object(catalog, "file_hash", side_effect=AssertionError("No audio read")):
            preview = self.preview()
            self.assertEqual(library_operations.play_history(self.db), before)
            receipt = self.request(preview)
        self.assertEqual(receipt, self.request(preview))
        self.assertEqual([item["request_id"] for item in library_operations.play_history(self.db)], ["play-1"])
        self.assertEqual(library_operations.history_summary(self.db)["file-1"]["play_count"], 1)
        self.assertNotIn("file-0", library_operations.history_summary(self.db))
        with self.db:
            selected = studio_service.search(self.db, {"never_played": True})
        self.assertEqual([track["id"] for track in selected["tracks"]], ["file-0"])
        with self.db:
            replay = library_operations.record_play(self.db, self.bodies[0], self.dataset)
        self.assertEqual(replay["request_id"], "play-0")
        self.assertEqual(len(library_operations.play_history(self.db)), 1)
        self.assertEqual(self.db.execute("SELECT rating FROM studio_feedback").fetchone()[0], 1)
        undone = self.request(preview, "undo")
        self.assertEqual(undone, self.request(preview, "undo"))
        self.assertEqual(library_operations.play_history(self.db), before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM library_play_events").fetchone()[0], 3)
        self.assertIsNotNone(studio_history.edits(self.db)[0]["undo"])

    def test_old_preview_cannot_override_remove_undo_or_newer_history_revision(self):
        old = self.preview()
        current = self.preview()
        self.request(current)
        with self.assertRaises(catalog.Conflict):
            self.request(old, request_id="stale")
        self.request(current, "undo")
        with self.assertRaises(catalog.Conflict):
            self.request(old, request_id="stale-after-undo")
        newest = self.preview()
        self.request(newest, request_id="new-removal")
        with self.assertRaises(catalog.Conflict):
            self.request(current, "undo", request_id="repeat-undo-new-id")
        self.assertEqual(len(library_operations.play_history(self.db)), 1)

    def test_changed_event_or_one_changed_removal_aborts_entire_batch(self):
        preview = self.preview()
        with self.db:
            self.db.execute("UPDATE library_play_events SET note='newer' WHERE request_id='play-2'")
        with self.assertRaises(catalog.Conflict):
            self.request(preview)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM library_play_removals").fetchone()[0], 0)
        preview = self.preview()
        self.request(preview)
        with self.db:
            self.db.execute("UPDATE library_play_removals SET revision=revision+1 WHERE event_id='play-2'")
        with self.assertRaises(catalog.Conflict):
            self.request(preview, "undo")
        self.assertEqual(len(library_operations.play_history(self.db)), 1)

    def test_failed_receipt_and_second_tombstone_write_roll_back_everything(self):
        for target in ("studio_receipts", "library_play_removals"):
            preview = self.preview()
            condition = "" if target == "studio_receipts" else "WHEN NEW.event_id='play-2'"
            self.db.execute(f"""CREATE TRIGGER reject_history BEFORE INSERT ON {target} {condition}
                BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""")
            with self.assertRaises(sqlite3.IntegrityError):
                self.request(preview, request_id="failure-" + target)
            self.assertEqual(len(library_operations.play_history(self.db)), 3)
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM library_play_removals").fetchone()[0], 0)
            self.assertIsNone(self.db.execute("SELECT approval_json FROM studio_history_edits WHERE id=?",
                                            (preview["edit_id"],)).fetchone()[0])
            self.db.execute("DROP TRIGGER reject_history")
        preview = self.preview()
        self.request(preview)
        self.db.execute("""CREATE TRIGGER reject_undo BEFORE UPDATE ON library_play_removals
            WHEN NEW.event_id='play-2' BEGIN SELECT RAISE(ABORT,'synthetic undo failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.request(preview, "undo")
        self.assertEqual(len(library_operations.play_history(self.db)), 1)

    def test_exact_receipt_ids_expiry_dataset_and_payload_validation(self):
        preview = self.preview()
        body = {"request_id": "exact", "dataset_id": self.dataset, "edit_id": preview["edit_id"],
                "event_ids": list(reversed(preview["event_ids"]))}
        with self.db, self.assertRaises(catalog.Conflict):
            studio_history.approve(self.db, body, self.dataset)
        with patch.object(studio_history.time, "time", return_value=preview["expires_at"] + 1), \
                self.assertRaises(catalog.Conflict):
            self.request(preview)
        for ids in ([], ["play-0", "play-0"], [True], ["missing"], ["x"] * 501):
            with self.subTest(ids=ids[:2]), self.db, self.assertRaises((ValueError, catalog.Conflict)):
                studio_history.preview(self.db, {"dataset_id": self.dataset, "event_ids": ids}, self.dataset)
        with self.db, self.assertRaises(catalog.Conflict):
            studio_history.preview(self.db, {"dataset_id": "other", "event_ids": ["play-0"]}, self.dataset)
        self.request(preview)
        with self.db, self.assertRaises(catalog.Conflict):
            studio_history.undo(self.db, {"request_id": "approve", "dataset_id": self.dataset,
                "edit_id": preview["edit_id"], "event_ids": preview["event_ids"]}, self.dataset)

    def test_exact_500_events_boundary_and_restart_recovery(self):
        with self.db:
            for index in range(3, 500):
                library_operations.record_play(self.db, {**self.bodies[0], "request_id": f"play-{index}"},
                                               self.dataset)
        ids = [f"play-{index}" for index in range(500)]
        preview = self.preview(ids)
        self.request(preview)
        self.assertEqual(library_operations.play_history(self.db), [])
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.request(preview, "undo")
        self.assertEqual(len(library_operations.play_history(self.db, limit=500)), 500)
        self.assertEqual(library_operations.history_summary(self.db)["file-0"]["play_count"], 499)

    def test_fresh_connection_search_cache_invalidates_on_removal_and_restore(self):
        def read():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    return studio_service.search(db, {"never_played": True})
            finally:
                db.close()
        before = read()
        self.assertEqual(before["total"], 0)
        preview = self.preview()
        self.request(preview)
        removed = read()
        self.assertEqual(removed["total"], 1)
        self.assertNotEqual(removed["library_revision"], before["library_revision"])
        self.request(preview, "undo")
        restored = read()
        self.assertEqual(restored["total"], 0)
        self.assertNotEqual(restored["library_revision"], removed["library_revision"])

    def test_undo_retains_new_observations_and_backup_includes_removed_state(self):
        from web import personal_crates, studio_backup
        personal_crates.initialize(self.db)
        preview = self.preview()
        self.request(preview)
        with self.db:
            library_operations.record_play(self.db, {**self.bodies[0], "request_id": "new-play"}, self.dataset)
        other = sqlite3.connect(self.path)
        try:
            bundle, manifest = studio_backup.create(other)
        finally:
            other.close()
        self.assertEqual(manifest["table_counts"]["library_play_events"], 4)
        self.assertEqual(manifest["table_counts"]["library_play_removals"], 2)
        restored = Path(self.temporary.name) / "restored"
        studio_backup.restore(bundle, restored.absolute())
        restored_db = sqlite3.connect(restored / "library.sqlite3")
        restored_db.row_factory = sqlite3.Row
        try:
            self.assertEqual(len(library_operations.play_history(restored_db)), 2)
            with restored_db:
                studio_history.undo(restored_db, {"dataset_id": self.dataset, "request_id": "restored-undo",
                    "edit_id": preview["edit_id"], "event_ids": preview["event_ids"]}, self.dataset)
            self.assertEqual(len(library_operations.play_history(restored_db)), 4)
        finally:
            restored_db.close()
        self.request(preview, "undo")
        self.assertEqual(len(library_operations.play_history(self.db)), 4)
        self.assertTrue(any(event["request_id"] == "new-play" for event in library_operations.play_history(self.db)))


if __name__ == "__main__":
    unittest.main()
