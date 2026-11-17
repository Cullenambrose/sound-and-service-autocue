import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from web import catalog, personal_crates, studio, studio_backup, studio_cleanup, studio_service


class StudioCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root_path = (Path(self.temporary.name) / "music").resolve()
        self.root_path.mkdir()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        catalog.initialize(self.db)
        personal_crates.initialize(self.db)
        studio_service.initialize(self.db)
        studio_cleanup.initialize(self.db)
        snapshot = {
            "schema_version": 1,
            "source": "synthetic-cleanup-fixture",
            "crates": [{
                "id": "crate-1", "title": "Synthetic crate", "available": True,
                "songs": [{"id": "song-1", "artist": "Fixture Artist",
                           "title": "Fixture Song", "version": ""}],
            }],
        }
        with self.db:
            self.snapshot_id = catalog.import_snapshot(self.db, snapshot)["snapshot_id"]
            root = self.root_path.stat()
            self.db.execute(
                "INSERT INTO library_roots(id,path,device,inode,scanned_at,error) VALUES(?,?,?,?,?,?)",
                ("root-1", str(self.root_path), root.st_dev, root.st_ino, time.time(), ""),
            )
            self.source_path = self.root_path / "source.mp3"
            self.keeper_path = self.root_path / "keeper.mp3"
            self.source_path.write_bytes(b"synthetic audio fixture")
            self.keeper_path.write_bytes(b"synthetic audio fixture")
            self.add_file("source", self.source_path)
            self.add_file("keeper", self.keeper_path)
            self.db.execute(
                "INSERT INTO catalog_match_decisions VALUES(?,?,?,?,?,?,?)",
                (self.snapshot_id, "song-1", 1, "source",
                 catalog.file_hash(self.source_path), "Synthetic review", time.time()),
            )

    def add_file(self, file_id, path):
        info = path.stat()
        self.db.execute("""
            INSERT INTO library_files
            (id,root_id,relative_path,content_hash,size,mtime_ns,ctime_ns,metadata_json,error,available)
            VALUES(?,?,?,?,?,?,?,?,?,1)
        """, (file_id, "root-1", path.name, catalog.file_hash(path), info.st_size,
              info.st_mtime_ns, info.st_ctime_ns, json.dumps({"artist": "Fixture Artist"}), ""))

    def refresh_file_index(self, file_id, path):
        info = path.stat()
        self.db.execute("""
            UPDATE library_files SET content_hash=?,size=?,mtime_ns=?,ctime_ns=?
            WHERE id=?
        """, (catalog.file_hash(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, file_id))

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def preview(self):
        with self.db:
            return studio_cleanup.preview(self.db, {
                "dataset_id": "synthetic-dataset",
                "source_file_id": "source",
                "keeper_file_id": "keeper",
            }, "synthetic-dataset")

    def decision(self):
        return dict(self.db.execute(
            "SELECT * FROM catalog_match_decisions WHERE snapshot_id=? AND song_id='song-1'",
            (self.snapshot_id,),
        ).fetchone())

    def test_exact_hash_reference_repair_is_reviewed_idempotent_and_undoable(self):
        before_bytes = (self.source_path.read_bytes(), self.keeper_path.read_bytes())
        preview = self.preview()
        self.assertEqual(preview["reference_count"], 1)
        self.assertEqual(preview["references"][0]["song_id"], "song-1")

        body = {"request_id": "repair-1", "dataset_id": "synthetic-dataset",
                "edit_id": preview["edit_id"], "source_file_id": "source",
                "keeper_file_id": "keeper"}
        with self.db:
            receipt = studio_cleanup.approve(self.db, body, "synthetic-dataset")
        self.assertEqual(receipt["operation"], "approve")
        self.assertEqual(self.decision()["file_id"], "keeper")
        self.assertEqual(self.decision()["revision"], 2)
        with self.db:
            self.assertEqual(studio_cleanup.approve(self.db, body, "synthetic-dataset"), receipt)

        undo_body = {**body, "request_id": "repair-undo-1"}
        with self.db:
            undone = studio_cleanup.undo(self.db, undo_body, "synthetic-dataset")
        self.assertEqual(undone["operation"], "undo")
        self.assertEqual(self.decision()["file_id"], "source")
        self.assertEqual(self.decision()["revision"], 3)
        with self.db:
            self.assertEqual(studio_cleanup.undo(self.db, undo_body, "synthetic-dataset"), undone)
        self.assertEqual((self.source_path.read_bytes(), self.keeper_path.read_bytes()), before_bytes)

    def test_preview_rejects_nonidentical_or_changed_files_and_unreferenced_source(self):
        self.keeper_path.write_bytes(b"different synthetic audio fixture")
        with self.db:
            self.refresh_file_index("keeper", self.keeper_path)
        with self.assertRaisesRegex(ValueError, "identical current whole-file"):
            self.preview()

        self.keeper_path.write_bytes(b"synthetic audio fixture")
        with self.assertRaises(catalog.Conflict):
            self.preview()

        with self.db:
            self.refresh_file_index("keeper", self.keeper_path)
        with self.db:
            self.db.execute("UPDATE catalog_match_decisions SET file_id='keeper' WHERE file_id='source'")
        with self.assertRaisesRegex(ValueError, "no AutoCue catalog references"):
            self.preview()

    def test_approval_refuses_changed_reference_and_undo_refuses_changed_source(self):
        preview = self.preview()
        with self.assertRaisesRegex(catalog.Conflict, "source/keeper IDs differ"):
            with self.db:
                studio_cleanup.approve(self.db, {
                    "request_id": "repair-wrong-keeper", "dataset_id": "synthetic-dataset",
                    "edit_id": preview["edit_id"], "source_file_id": "source",
                    "keeper_file_id": "other",
                }, "synthetic-dataset")
        with self.db:
            self.db.execute(
                "UPDATE catalog_match_decisions SET note='newer review' WHERE file_id='source'"
            )
        with self.assertRaisesRegex(catalog.Conflict, "catalog links changed"):
            with self.db:
                studio_cleanup.approve(self.db, {
                    "request_id": "repair-stale", "dataset_id": "synthetic-dataset",
                    "edit_id": preview["edit_id"], "source_file_id": "source",
                    "keeper_file_id": "keeper",
                }, "synthetic-dataset")
        self.assertEqual(self.decision()["file_id"], "source")

        with self.db:
            self.db.execute(
                "UPDATE catalog_match_decisions SET note='Synthetic review' WHERE file_id='source'"
            )
        preview = self.preview()
        with self.db:
            studio_cleanup.approve(self.db, {
                "request_id": "repair-2", "dataset_id": "synthetic-dataset",
                "edit_id": preview["edit_id"], "source_file_id": "source",
                "keeper_file_id": "keeper",
            }, "synthetic-dataset")
        self.source_path.write_bytes(b"changed after repair")
        with self.assertRaises(catalog.Conflict):
            with self.db:
                studio_cleanup.undo(self.db, {
                    "request_id": "repair-undo-2", "dataset_id": "synthetic-dataset",
                    "edit_id": preview["edit_id"], "source_file_id": "source",
                    "keeper_file_id": "keeper",
                }, "synthetic-dataset")
        self.assertEqual(self.decision()["file_id"], "keeper")

    def test_preview_validates_dataset_exact_fields_and_source_selection(self):
        for body in (
            {"dataset_id": "other", "source_file_id": "source", "keeper_file_id": "keeper"},
            {"dataset_id": "synthetic-dataset", "source_file_id": "source", "keeper_file_id": "source"},
            {"dataset_id": "synthetic-dataset", "source_file_id": "unknown", "keeper_file_id": "keeper"},
            {"dataset_id": "synthetic-dataset", "source_file_id": "source",
             "keeper_file_id": "keeper", "extra": True},
        ):
            with self.subTest(body=body):
                with self.assertRaises((ValueError, catalog.Conflict)):
                    with self.db:
                        studio_cleanup.preview(self.db, body, "synthetic-dataset")

    def test_studio_dispatch_exposes_reviewed_catalog_repair_endpoints(self):
        body = {"dataset_id": "synthetic-dataset", "source_file_id": "source",
                "keeper_file_id": "keeper"}
        with self.db:
            preview = studio.dispatch(self.db, "file-repair/preview", body, "synthetic-dataset")
        request = {"request_id": "dispatch-repair", "dataset_id": "synthetic-dataset",
                   "edit_id": preview["edit_id"], "source_file_id": "source",
                   "keeper_file_id": "keeper"}
        with self.db:
            receipt = studio.dispatch(self.db, "file-repair/approve", request, "synthetic-dataset")
        self.assertEqual(receipt["operation"], "approve")
        self.assertEqual(self.decision()["file_id"], "keeper")

    def test_repair_journal_survives_private_database_backup(self):
        preview = self.preview()
        with self.db:
            studio_cleanup.approve(self.db, {
                "request_id": "repair-backup", "dataset_id": "synthetic-dataset",
                "edit_id": preview["edit_id"], "source_file_id": "source",
                "keeper_file_id": "keeper",
            }, "synthetic-dataset")
        bundle, manifest = studio_backup.create(self.db)
        database, inspected = studio_backup.inspect(bundle)
        self.assertEqual(manifest, inspected)
        self.assertEqual(manifest["table_counts"]["studio_file_repairs"], 1)
        restored_path = Path(self.temporary.name) / "restored.sqlite3"
        restored_path.write_bytes(database)
        restored = sqlite3.connect(restored_path)
        try:
            row = restored.execute(
                "SELECT approval_json FROM studio_file_repairs WHERE id=?",
                (preview["edit_id"],),
            ).fetchone()
        finally:
            restored.close()
        self.assertIsNotNone(row[0])


if __name__ == "__main__":
    unittest.main()
