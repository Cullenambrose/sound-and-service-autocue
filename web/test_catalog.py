import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from web import catalog
from web import fixtures


def snapshot():
    song = {"id": "song-1", "artist": "Invented Artist", "title": "Synthetic Song",
            "version": "Clean", "bpm": 0, "key": "0A", "link": "https://example.invalid/song"}
    return {"schema_version": 1, "source": "synthetic-only",
            "crates": [{"id": "crate-1", "title": "Same title", "available": True,
                        "curator": "Synthetic curator", "songs": [song, copy.deepcopy(song)]},
                       {"id": "crate-2", "title": "Same title", "available": True, "songs": [song]},
                       {"id": "unavailable", "title": "Unavailable source", "available": False, "songs": []}]}


def metadata(path):
    return {"artist": "Invented Artist", "title": "Synthetic Song", "version": path.stem,
            "bpm": "", "key": "", "duration_s": 20, "provenance": "synthetic"}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.db = sqlite3.connect(self.root / "library.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        catalog.initialize(self.db)
        self.document = snapshot()
        with self.db:
            self.imported = catalog.import_snapshot(self.db, self.document)
        self.identity = self.imported["snapshot_id"]

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def scan(self, reader=metadata):
        with self.db:
            return catalog.scan_folder(self.db, str(self.music), reader)

    def song(self):
        return catalog.songs(self.db, self.identity, "crate-1")[0]

    def decision(self, **changes):
        file = catalog.file_records(self.db)[0]
        return {"request_id": "request-1", "dataset_id": "synthetic-dataset",
                "snapshot_id": self.identity, "song_id": "song-1",
                "base_revision": 0, "file_id": file["id"], "content_hash": file["content_hash"],
                "note": "", **changes}

    def decide(self, body):
        with self.db:
            return catalog.decide(self.db, body)

    def test_import_identity_order_duplicate_titles_zero_and_unavailable_are_preserved(self):
        self.assertEqual(self.imported["memberships"], 3)
        self.assertEqual(self.imported["songs"], 1)
        crates = catalog.crates(self.db, self.identity)
        self.assertEqual([c["source_id"] for c in crates], ["crate-1", "crate-2", "unavailable"])
        self.assertEqual([s["position"] for s in catalog.songs(self.db, self.identity, "crate-1")], [0, 1])
        self.assertEqual(self.song()["song"]["bpm"], 0)
        self.assertEqual(self.song()["song"]["link"], "https://example.invalid/song")
        self.assertFalse(crates[-1]["available"])
        with self.db:
            receipt = catalog.import_snapshot(self.db, json.loads(json.dumps(self.document)))
        self.assertFalse(receipt["added"])
        self.assertEqual(receipt["snapshot_id"], self.identity)
        self.assertEqual(len(catalog.snapshots(self.db)), 1)

    def test_changed_snapshot_keeps_original_and_does_not_inherit_decisions(self):
        (self.music / "Clean.wav").write_bytes(b"synthetic")
        self.scan()
        self.decide(self.decision())
        changed = copy.deepcopy(self.document)
        changed["crates"][0]["title"] = "Updated name"
        with self.db:
            receipt = catalog.import_snapshot(self.db, changed)
        self.assertNotEqual(receipt["snapshot_id"], self.identity)
        self.assertEqual(len(catalog.snapshots(self.db)), 2)
        self.assertIsNone(catalog.songs(self.db, receipt["snapshot_id"], "crate-1")[0]["decision"])

    def test_invalid_rows_and_conflicting_ids_fail_without_partial_import(self):
        for mutation in ("duplicate", "conflict", "missing", "unavailable", "bool-bpm"):
            document = copy.deepcopy(self.document)
            if mutation == "duplicate":
                document["crates"].append(copy.deepcopy(document["crates"][0]))
            elif mutation == "conflict":
                document["crates"][0]["songs"][1]["version"] = "Explicit"
            elif mutation == "missing":
                del document["crates"][0]["songs"][0]["artist"]
            elif mutation == "unavailable":
                document["crates"][0]["available"] = False
            else:
                document["crates"][0]["songs"][0]["bpm"] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError), self.db:
                catalog.import_snapshot(self.db, document)
        self.assertEqual(len(catalog.snapshots(self.db)), 1)

    def test_storage_failure_rolls_back_whole_snapshot_then_retry_succeeds(self):
        document = copy.deepcopy(self.document)
        document["source"] = "another"
        self.db.execute("""CREATE TRIGGER fail_membership BEFORE INSERT ON catalog_memberships
                           BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            catalog.import_snapshot(self.db, document)
        self.assertEqual(len(catalog.snapshots(self.db)), 1)
        self.db.execute("DROP TRIGGER fail_membership")
        with self.db:
            self.assertTrue(catalog.import_snapshot(self.db, document)["added"])

    def test_synthetic_1423_crate_81153_membership_import_reconciles_and_retries(self):
        songs = [{"id": f"generated-song-{i}", "artist": "Invented bulk fixture",
                  "title": f"Generated title {i}", "version": "Synthetic"} for i in range(1000)]
        crates = []
        cursor = 0
        for index in range(1423):
            count = 57 + (index < 42)
            crates.append({"id": f"generated-crate-{index}", "title": "Repeated synthetic title",
                           "available": True, "songs": [songs[(cursor + j) % 1000] for j in range(count)]})
            cursor += count
        document = {"schema_version": 1, "source": "Synthetic volume test, not the real export",
                    "crates": crates}
        with self.db:
            imported = catalog.import_snapshot(self.db, document)
        self.assertEqual(imported["crates"], 1423)
        self.assertEqual(imported["memberships"], 81153)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM catalog_memberships WHERE snapshot_id=?",
                                        (imported["snapshot_id"],)).fetchone()[0], 81153)
        with self.db:
            retried = catalog.import_snapshot(self.db, document)
        self.assertFalse(retried["added"])
        self.assertEqual(len(catalog.crates(self.db, imported["snapshot_id"])), 1423)
        rows = catalog.songs(self.db, imported["snapshot_id"], "generated-crate-0")
        self.assertEqual([row["song"]["id"] for row in rows],
                         [song["id"] for song in document["crates"][0]["songs"]])

    def test_scan_is_incremental_read_only_and_keeps_distinct_copies(self):
        for name in ("Clean.wav", "copy.wav"):
            (self.music / name).write_bytes(b"same bytes")
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.music.iterdir()}
        self.assertEqual(self.scan()["scanned"], 2)
        files = catalog.file_records(self.db)
        self.assertEqual(len({f["id"] for f in files}), 2)
        self.assertEqual(len({f["content_hash"] for f in files}), 1)
        with patch.object(catalog, "file_hash", side_effect=AssertionError("unchanged file rehashed")):
            self.assertEqual(self.scan()["reused"], 2)
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.music.iterdir()}, before)
        self.assertEqual(len(catalog.file_records(self.db)), 2)

    def test_real_mutagen_scan_records_missing_tags_without_filename_guessing(self):
        import wave
        path = self.music / "Invented Artist - Synthetic Song.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\0\0" * 8000)
        with self.db:
            catalog.scan_folder(self.db, str(self.music))
        file = catalog.file_records(self.db)[0]
        self.assertEqual(file["metadata"]["artist"], "")
        self.assertEqual(file["metadata"]["duration_s"], 1)
        self.assertEqual(self.song()["candidates"], [])

    def test_wave_id3_tags_are_read_without_treating_filename_as_metadata(self):
        path = self.music / "unrelated-filename.wav"
        path.write_bytes(fixtures.tagged_wav())
        before = path.read_bytes()
        with self.db:
            catalog.scan_folder(self.db, str(self.music))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.song()["candidates"][0]["metadata"]["title"], "Synthetic Song")
        self.assertTrue(self.song()["candidates"][0]["metadata_equal"])

    def test_metadata_processing_does_not_block_independent_database_writers(self):
        (self.music / "Clean.wav").write_bytes(b"synthetic")
        def reader(path):
            with sqlite3.connect(self.root / "library.db", timeout=0.1) as other:
                other.execute("CREATE TABLE independent_review (id INTEGER)")
                other.execute("INSERT INTO independent_review VALUES(1)")
            return metadata(path)
        self.assertEqual(self.scan(reader)["scanned"], 1)

    def test_competing_scan_cannot_overwrite_a_newer_committed_index(self):
        path = self.music / "Clean.wav"
        path.write_bytes(b"synthetic")
        for existing in (False, True):
            with self.subTest(existing=existing), patch.object(catalog.time, "time", return_value=1):
                if existing:
                    self.scan()
                    path.write_bytes(b"updated synthetic")
                def compete(file):
                    with sqlite3.connect(self.root / "library.db") as other:
                        other.row_factory = sqlite3.Row
                        other.execute("PRAGMA foreign_keys=ON")
                        catalog.scan_folder(other, str(self.music), metadata)
                    return metadata(file)
                with self.assertRaises(catalog.Conflict):
                    self.scan(compete)
                self.assertEqual(len(catalog.file_records(self.db)), 1)
                self.assertEqual(catalog.file_records(self.db)[0]["content_hash"], catalog.file_hash(path))

    def test_file_limit_and_disconnect_abort_without_publishing_partial_scan(self):
        (self.music / "Clean.wav").write_bytes(b"synthetic")
        self.scan()
        before = [dict(row) for row in self.db.execute("SELECT * FROM library_files")]
        (self.music / "Explicit.wav").write_bytes(b"new")
        with patch.object(catalog, "MAX_FILES", 1):
            with self.assertRaisesRegex(ValueError, "at most"), self.db:
                catalog.scan_folder(self.db, str(self.music), metadata)
        self.assertEqual([dict(row) for row in self.db.execute("SELECT * FROM library_files")], before)
        original = catalog.root_status
        count = 0
        def disconnect(root):
            nonlocal count
            count += 1
            return original(root) if count == 1 else ("offline", "synthetic disconnect")
        with patch.object(catalog, "root_status", side_effect=disconnect):
            with self.assertRaisesRegex(OSError, "disconnected"), self.db:
                catalog.scan_folder(self.db, str(self.music), metadata)
        self.assertEqual([dict(row) for row in self.db.execute("SELECT * FROM library_files")], before)

    def test_parse_errors_are_visible_retried_and_never_candidates(self):
        (self.music / "Clean.mp3").write_bytes(b"not audio")
        with self.db:
            result = catalog.scan_folder(self.db, str(self.music))
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(catalog.file_records(self.db)[0]["state"], "error")
        self.assertEqual(self.song()["candidates"], [])
        self.assertEqual(self.scan()["reused"], 0)
        self.assertEqual(self.song()["state"], "unreviewed")

    def test_offline_root_is_not_deletion_and_can_reconnect(self):
        (self.music / "Clean.wav").write_bytes(b"music")
        self.scan()
        offline = self.root / "disconnected"
        self.music.rename(offline)
        result = self.scan()
        self.assertEqual(result["state"], "offline")
        self.assertEqual(catalog.file_records(self.db)[0]["state"], "offline")
        self.assertEqual(self.db.execute("SELECT available FROM library_files").fetchone()[0], 1)
        offline.rename(self.music)
        self.assertEqual(self.scan()["reused"], 1)
        self.assertEqual(catalog.roots(self.db)[0]["state"], "online")

    def test_replaced_folder_is_not_treated_as_original_volume(self):
        self.scan()
        self.music.rename(self.root / "old-volume")
        self.music.mkdir()
        (self.music / "Clean.wav").write_bytes(b"different volume")
        self.assertEqual(self.scan()["state"], "offline")
        self.assertEqual(len(catalog.file_records(self.db)), 0)

    def test_deleted_files_mark_missing_only_after_successful_scan(self):
        path = self.music / "Clean.wav"
        path.write_bytes(b"music")
        self.scan()
        path.unlink()
        self.assertEqual(catalog.file_records(self.db)[0]["state"], "missing")
        self.scan()
        self.assertEqual(self.db.execute("SELECT available FROM library_files").fetchone()[0], 0)

    def test_symlinks_are_skipped_overlapping_roots_rejected_and_failed_walk_rolls_back(self):
        outside = self.root / "outside.wav"
        outside.write_bytes(b"outside")
        (self.music / "escape.wav").symlink_to(outside)
        self.assertEqual(self.scan()["skipped_symlinks"], ["escape.wav"])
        self.assertEqual(catalog.file_records(self.db), [])
        nested = self.music / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, "overlaps"), self.db:
            catalog.scan_folder(self.db, str(nested))
        with patch.object(catalog.os, "walk", side_effect=PermissionError("synthetic denied")):
            with self.assertRaises(PermissionError), self.db:
                catalog.scan_folder(self.db, str(self.music))
        self.assertEqual(len(catalog.roots(self.db)), 1)

    def test_candidate_agreement_is_not_auto_acceptance_and_version_conflict_needs_override(self):
        (self.music / "Explicit.wav").write_bytes(b"music")
        self.scan()
        song = self.song()
        self.assertEqual(song["state"], "unreviewed")
        self.assertFalse(song["candidates"][0]["metadata_equal"])
        with self.assertRaisesRegex(ValueError, "override"):
            self.decide(self.decision())
        receipt = self.decide(self.decision(override=True, note="Reviewed synthetic explicit version intentionally."))
        self.assertTrue(receipt["ok"])
        self.assertEqual(self.song()["state"], "exact")

    def test_multiple_candidates_are_ambiguous_and_normalization_keeps_version_words(self):
        (self.music / "Clean.wav").write_bytes(b"clean")
        (self.music / "Explicit.wav").write_bytes(b"explicit")
        self.scan()
        self.assertEqual(self.song()["state"], "ambiguous")
        self.assertTrue(self.song()["candidates"][0]["metadata_equal"])
        self.assertNotEqual(catalog.normalize("Radio Edit"), catalog.normalize("Extended Edit"))
        self.assertEqual(catalog.normalize("Cafe\u0301  Song"), catalog.normalize("Caf\u00e9 Song"))

    def test_receipt_retries_conflicts_and_old_retry_never_restore_previous_choice(self):
        (self.music / "Clean.wav").write_bytes(b"music")
        self.scan()
        body = self.decision()
        receipt = self.decide(body)
        self.assertEqual(self.decide(body), receipt)
        with self.assertRaises(catalog.Conflict):
            self.decide({**body, "note": "different"})
        with self.assertRaises(catalog.Conflict):
            self.decide({**body, "request_id": "stale"})
        missing = {**body, "request_id": "missing", "base_revision": 1, "file_id": None, "content_hash": None}
        self.decide(missing)
        self.assertEqual(self.decide(body), receipt)
        self.assertIsNone(self.song()["decision"]["file_id"])
        self.assertEqual(self.song()["revision"], 2)

    def test_changed_file_invalidates_previous_decision_and_cannot_accept_stale_candidate(self):
        path = self.music / "Clean.wav"
        path.write_bytes(b"music")
        self.scan()
        body = self.decision()
        self.decide(body)
        path.write_bytes(b"different music")
        self.assertEqual(self.song()["state"], "stale")
        with self.assertRaises(catalog.Conflict):
            self.decide({**body, "request_id": "changed", "base_revision": 1})
        self.scan()
        self.assertEqual(self.song()["state"], "stale")
        self.decide(self.decision(request_id="accept-new", base_revision=1))
        self.assertEqual(self.song()["state"], "exact")

    def test_receipt_failure_rolls_back_decision_and_can_retry(self):
        (self.music / "Clean.wav").write_bytes(b"music")
        self.scan()
        body = self.decision()
        self.db.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON catalog_match_receipts
                           BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.decide(body)
        self.assertIsNone(self.song()["decision"])
        self.db.execute("DROP TRIGGER fail_receipt")
        self.assertTrue(self.decide(body)["ok"])

    def test_incomplete_or_unknown_decision_fields_cannot_clear_a_reviewed_choice(self):
        (self.music / "Clean.wav").write_bytes(b"music")
        self.scan()
        body = self.decision()
        self.decide(body)
        for changes in ({"file_id": None}, {"unexpected": True}, {"base_revision": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.decide({**body, "request_id": "invalid", "base_revision": 1, **changes})
        incomplete = {key: value for key, value in body.items() if key != "file_id"}
        with self.assertRaises(ValueError):
            self.decide(incomplete)
        self.assertEqual(self.song()["state"], "exact")
        self.assertEqual(self.song()["revision"], 1)


if __name__ == "__main__":
    unittest.main()
