import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave

import library_inventory as inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "music"
        self.root.mkdir()
        self.database = self.base / "state" / "inventory.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def audio(self, relative="song.wav"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 8000)
        return path

    def rows(self, scan_id):
        with sqlite3.connect(self.database) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM entries WHERE scan_id=? ORDER BY relative_path", (scan_id,))]

    def test_reads_real_headers_recurses_and_leaves_sources_byte_identical(self):
        paths = [self.audio(), self.audio("nested/two.WAV")]
        (self.root / "notes.txt").write_text("not music")
        before = {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                  for path in paths}
        scan_id = inventory.create_scan(self.database, [self.root])
        progress = []
        result = inventory.resume_scan(self.database, scan_id, on_progress=progress.append)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["counts"], {"audio": {"done": 2}, "directory": {"done": 2},
                                           "other": {"skipped": 1}})
        self.assertEqual(result["audio_bytes"], sum(path.stat().st_size for path in paths))
        for row in self.rows(scan_id):
            if row["kind"] == "audio":
                metadata = json.loads(row["metadata_json"])
                self.assertEqual(metadata["duration_s"], 1)
                self.assertEqual(metadata["sample_rate"], 8000)
                self.assertEqual(metadata["extractor"], "mutagen_metadata_v2")
                self.assertEqual(metadata["title"], [])
        self.assertEqual(before, {path: (hashlib.sha256(path.read_bytes()).hexdigest(),
                                         path.stat().st_mtime_ns) for path in paths})
        self.assertEqual(progress[0]["status"], "running")
        self.assertEqual(progress[-1]["status"], "complete")
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)

    def test_pause_and_resume_does_not_repeat_finished_files(self):
        for index in range(3):
            self.audio(f"{index}.wav")
        scan_id = inventory.create_scan(self.database, [self.root])
        read = inventory.read_metadata
        with mock.patch.object(inventory, "read_metadata", wraps=read) as reader:
            result = inventory.resume_scan(self.database, scan_id, limit=1)
            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["counts"]["audio"], {"done": 1, "queued": 2})
            result = inventory.resume_scan(self.database, scan_id)
            self.assertEqual(result["status"], "complete")
            inventory.resume_scan(self.database, scan_id)
            self.assertEqual(reader.call_count, 3)
        self.assertEqual(inventory.status(self.database, scan_id)["counts"]["audio"]["done"], 3)

    def test_interrupt_rolls_back_current_entry_and_can_resume(self):
        self.audio()
        scan_id = inventory.create_scan(self.database, [self.root])
        with mock.patch.object(inventory, "read_metadata", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                inventory.resume_scan(self.database, scan_id)
        self.assertEqual(inventory.status(self.database, scan_id)["status"], "paused")
        self.assertEqual(inventory.status(self.database, scan_id)["counts"]["audio"], {"queued": 1})
        self.assertEqual(inventory.resume_scan(self.database, scan_id)["status"], "complete")

    def test_unexpected_errors_remain_explicit_and_do_not_consume_queue(self):
        self.audio()
        scan_id = inventory.create_scan(self.database, [self.root])
        with mock.patch.object(inventory, "read_metadata", side_effect=RuntimeError("backend bug")):
            with self.assertRaisesRegex(RuntimeError, "backend bug"):
                inventory.resume_scan(self.database, scan_id)
        result = inventory.status(self.database, scan_id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "backend bug")
        self.assertEqual(result["counts"]["audio"], {"queued": 1})
        self.assertEqual(inventory.resume_scan(self.database, scan_id)["status"], "complete")

    def test_corrupt_audio_is_not_success_and_other_files_continue(self):
        (self.root / "broken.mp3").write_bytes(b"not an mp3")
        self.audio()
        scan_id = inventory.create_scan(self.database, [self.root])
        result = inventory.resume_scan(self.database, scan_id)
        self.assertEqual(result["status"], "complete_with_errors")
        self.assertEqual(result["counts"]["audio"], {"done": 1, "error": 1})
        row = next(row for row in self.rows(scan_id) if row["relative_path"] == "broken.mp3")
        self.assertTrue(row["error"])
        self.assertIsNone(row["metadata_json"])
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["errors"][0]["relative_path"], "broken.mp3")
        self.assertTrue(result["errors"][0]["error"])
        self.assertFalse(result["errors_truncated"])

    def test_native_id3_metadata_is_read_without_saving_tags(self):
        from mutagen.id3 import TIT2, TPE1, TBPM
        from mutagen.wave import WAVE
        path = self.audio()
        tagged = WAVE(path)
        tagged.add_tags()
        tagged.tags.add(TIT2(encoding=3, text=["Synthetic title"]))
        tagged.tags.add(TPE1(encoding=3, text=["Synthetic artist"]))
        tagged.tags.add(TBPM(encoding=3, text=["128"]))
        tagged.save()
        original = path.read_bytes()
        scan_id = inventory.create_scan(self.database, [self.root])
        inventory.resume_scan(self.database, scan_id)
        row = next(row for row in self.rows(scan_id) if row["kind"] == "audio")
        metadata = json.loads(row["metadata_json"])
        self.assertEqual(metadata["title"], ["Synthetic title"])
        self.assertEqual(metadata["artist"], ["Synthetic artist"])
        self.assertEqual(metadata["bpm"], ["128"])
        self.assertEqual(path.read_bytes(), original)

    def test_error_details_are_bounded_and_pending_queue_uses_index_order(self):
        for index in range(21):
            (self.root / f"bad-{index:02}.mp3").write_bytes(b"bad")
        scan_id = inventory.create_scan(self.database, [self.root])
        result = inventory.resume_scan(self.database, scan_id)
        self.assertEqual(result["error_count"], 21)
        self.assertEqual(len(result["errors"]), 20)
        self.assertTrue(result["errors_truncated"])
        with sqlite3.connect(self.database) as db:
            details = [row[3] for row in db.execute("""EXPLAIN QUERY PLAN SELECT * FROM entries
                WHERE scan_id=? AND status='queued' ORDER BY kind,root_id,relative_path LIMIT 1""", (scan_id,))]
        self.assertFalse(any("TEMP B-TREE" in detail for detail in details), details)
        self.assertTrue(any("pending_entries" in detail for detail in details), details)

    def test_symlinks_and_special_files_are_skipped_without_following(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.mp3").write_bytes(b"do not read")
        (self.root / "linked-directory").symlink_to(outside, target_is_directory=True)
        (self.root / "linked.mp3").symlink_to(outside / "secret.mp3")
        os.mkfifo(self.root / "pipe.mp3")
        scan_id = inventory.create_scan(self.database, [self.root])
        with mock.patch.object(inventory, "read_metadata") as reader:
            result = inventory.resume_scan(self.database, scan_id)
            reader.assert_not_called()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["counts"]["other"], {"skipped": 3})
        self.assertFalse(any("secret.mp3" in row["relative_path"] for row in self.rows(scan_id)))

    def test_substituted_directory_symlink_cannot_escape_root(self):
        nested = self.root / "nested"
        nested.mkdir()
        self.audio("nested/song.wav")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.mp3").write_bytes(b"never read")
        scan_id = inventory.create_scan(self.database, [self.root])
        expand = inventory._expand_directory

        def replace_after_discovery(db, scan, root, entry):
            expand(db, scan, root, entry)
            if entry["relative_path"] == "":
                nested.rename(self.base / "original-nested")
                nested.symlink_to(outside, target_is_directory=True)

        with mock.patch.object(inventory, "_expand_directory", side_effect=replace_after_discovery):
            result = inventory.resume_scan(self.database, scan_id)
        self.assertEqual(result["status"], "complete_with_errors")
        self.assertFalse(any("secret.mp3" in row["relative_path"] for row in self.rows(scan_id)))

    def test_changed_file_between_discovery_and_read_is_an_error(self):
        path = self.audio()
        scan_id = inventory.create_scan(self.database, [self.root])
        expand = inventory._expand_directory

        def replace_after_discovery(db, scan, root, entry):
            expand(db, scan, root, entry)
            path.write_bytes(b"changed")

        with mock.patch.object(inventory, "_expand_directory", side_effect=replace_after_discovery):
            result = inventory.resume_scan(self.database, scan_id)
        self.assertEqual(result["status"], "complete_with_errors")
        row = next(row for row in self.rows(scan_id) if row["kind"] == "audio")
        self.assertIn("changed after discovery", row["error"])

    def test_changed_file_during_read_is_an_error(self):
        path = self.audio()
        scan_id = inventory.create_scan(self.database, [self.root])
        read = inventory.read_metadata

        def mutate(incoming):
            value = read(incoming)
            with path.open("ab") as target:
                target.write(b"changed")
            return value

        with mock.patch.object(inventory, "read_metadata", side_effect=mutate):
            self.assertEqual(inventory.resume_scan(self.database, scan_id)["status"], "complete_with_errors")
        row = next(row for row in self.rows(scan_id) if row["kind"] == "audio")
        self.assertIn("changed while reading", row["error"])

    def test_missing_volume_fails_without_consuming_entries_then_recovers(self):
        self.audio()
        scan_id = inventory.create_scan(self.database, [self.root])
        moved = self.base / "disconnected"
        self.root.rename(moved)
        with self.assertRaises(OSError):
            inventory.resume_scan(self.database, scan_id)
        self.assertEqual(inventory.status(self.database, scan_id)["status"], "failed")
        moved.rename(self.root)
        self.assertEqual(inventory.resume_scan(self.database, scan_id)["status"], "complete")

    def test_replaced_root_is_not_silently_scanned(self):
        self.audio()
        scan_id = inventory.create_scan(self.database, [self.root])
        self.root.rename(self.base / "previous-music")
        self.root.mkdir()
        with self.assertRaisesRegex(OSError, "identity changed"):
            inventory.resume_scan(self.database, scan_id)
        self.assertEqual(inventory.status(self.database, scan_id)["counts"], {"directory": {"queued": 1}})

    def test_invalid_roots_and_in_source_database_are_rejected_before_creation(self):
        cases = [[], [self.root, self.root], [self.root, self.root / "nested"]]
        (self.root / "nested").mkdir()
        for roots in cases:
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                inventory.create_scan(self.database, roots)
            self.assertFalse(self.database.exists())
        with self.assertRaises(FileNotFoundError):
            inventory.create_scan(self.database, [self.base / "missing"])
        with self.assertRaisesRegex(ValueError, "outside"):
            inventory.create_scan(self.root / "state" / "bad.db", [self.root])
        self.assertFalse((self.root / "state").exists())
        linked = self.base / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "not symlinks"):
            inventory.create_scan(self.database, [linked])

    def test_disjoint_roots_and_distinct_scans_do_not_overwrite(self):
        self.audio()
        second = self.base / "other"
        second.mkdir()
        first_id = inventory.create_scan(self.database, [self.root, second])
        second_id = inventory.create_scan(self.database, [self.root])
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(len(inventory.resume_scan(self.database, first_id)["roots"]), 2)
        self.assertEqual(inventory.status(self.database, second_id)["status"], "queued")
        self.assertEqual([scan["id"] for scan in inventory.list_scans(self.database)], [second_id, first_id])

    def test_unrelated_database_is_rejected_without_changing_it(self):
        self.database.parent.mkdir()
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE tracks (name TEXT)")
            db.execute("INSERT INTO tracks VALUES ('preserve')")
        before = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, "Not a supported"):
            inventory.create_scan(self.database, [self.root])
        self.assertEqual(before, self.database.read_bytes())

    def test_invalid_limits_and_unknown_ids_fail_explicitly(self):
        scan_id = inventory.create_scan(self.database, [self.root])
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                inventory.resume_scan(self.database, scan_id, limit=limit)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            inventory.status(self.database, "absent")

    def test_concurrent_writer_is_rejected(self):
        scan_id = inventory.create_scan(self.database, [self.root])
        lock = self.database.with_name(self.database.name + ".lock")
        with lock.open("a") as target:
            fcntl.flock(target, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "Another inventory"):
                inventory.resume_scan(self.database, scan_id)

    def test_cli_progress_status_resume_and_error_exit_codes(self):
        self.audio()
        self.audio("two.wav")
        command = [sys.executable, str(Path(inventory.__file__)), "--database", str(self.database)]
        result = subprocess.run(command + ["scan", "--root", str(self.root), "--limit", "1"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        progress = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(progress[-1]["status"], "paused")
        scan_id = progress[-1]["id"]
        result = subprocess.run(command + ["resume", scan_id], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])["status"], "complete")
        result = subprocess.run(command + ["status", scan_id], text=True, capture_output=True)
        self.assertEqual(json.loads(result.stdout)["counts"]["audio"], {"done": 2})
        result = subprocess.run(command + ["list"], text=True, capture_output=True)
        self.assertEqual(json.loads(result.stdout)[0]["id"], scan_id)
        (self.root / "bad.mp3").write_bytes(b"bad")
        result = subprocess.run(command + ["scan", "--root", str(self.root)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])["status"], "complete_with_errors")
        result = subprocess.run(command + ["scan", "--root", str(self.root), "--limit", "0"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive integer", result.stderr)


if __name__ == "__main__":
    unittest.main()
