import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from web import catalog, fixtures, scan_jobs


class ScanJobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.source = self.music / "Clean.wav"
        self.source.write_bytes(fixtures.tagged_wav())
        with self.connect() as db:
            catalog.initialize(db)
            scan_jobs.initialize(db)
        self.body = {"dataset_id": "synthetic", "request_id": "scan-1", "path": str(self.music)}

    def tearDown(self):
        self.temporary.cleanup()

    def connect(self):
        db = sqlite3.connect(self.root / "library.db", timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def submit(self, **changes):
        with self.connect() as db:
            return scan_jobs.submit(db, {**self.body, **changes}, "synthetic")

    def cancel(self, job_id):
        with self.connect() as db:
            return scan_jobs.cancel(db, {"dataset_id": "synthetic", "job_id": job_id}, "synthetic")

    def jobs(self):
        with self.connect() as db:
            return scan_jobs.jobs(db)

    def index(self):
        with self.connect() as db:
            return ([dict(row) for row in db.execute("SELECT * FROM library_roots")],
                    [dict(row) for row in db.execute("SELECT * FROM library_files")])

    def run_scan(self):
        self.assertTrue(scan_jobs.run_next(self.connect))
        return self.jobs()[0]

    def test_submission_receipt_survives_reload_missing_source_and_concurrent_retries(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.submit(), range(12)))
        self.assertTrue(all(reply == replies[0] for reply in replies))
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(self.index(), ([], []))
        self.source.unlink()
        self.music.rmdir()
        self.assertEqual(self.submit(), replies[0])
        with self.assertRaises(catalog.Conflict):
            self.submit(path=str(self.root))

    def test_input_dataset_root_identity_and_overlapping_jobs_fail_closed(self):
        for changes in ({"dataset_id": "other"}, {"path": "relative"}, {"path": str(self.source)},
                        {"path": ""}, {"request_id": False}):
            with self.assertRaises((ValueError, OSError)):
                self.submit(**changes)
        self.assertEqual(self.jobs(), [])
        self.submit()
        nested = self.music / "nested"
        nested.mkdir()
        for path in (self.music, nested, self.root):
            with self.assertRaises(catalog.Conflict):
                self.submit(request_id="other", path=str(path))

    def test_complete_result_progress_incremental_reuse_and_source_invariance(self):
        original, stat = self.source.read_bytes(), self.source.stat()
        receipt = self.submit()
        job = self.run_scan()
        self.assertEqual(job["id"], receipt["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["scanned"], 1)
        self.assertEqual(job["progress"], {"phase": "publishing", "scanned": 1, "reused": 0,
                                          "errors": 0, "skipped": 0, "current_path": ""})
        first = self.index()
        self.submit(request_id="scan-2")
        with patch.object(catalog, "file_hash", side_effect=AssertionError("unchanged file rehashed")):
            self.assertEqual(self.run_scan()["result"]["reused"], 1)
        self.assertEqual(self.index()[1], first[1])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.source.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertFalse(scan_jobs.run_next(self.connect))

    def test_cancel_queued_is_acknowledged_retryable_and_never_touches_files(self):
        receipt = self.submit()
        first = self.cancel(receipt["job_id"])
        self.assertEqual(first["job"]["status"], "cancelled")
        self.assertEqual(self.cancel(receipt["job_id"]), first)
        self.assertFalse(scan_jobs.run_next(self.connect))
        self.assertEqual(self.submit(), receipt)
        self.assertEqual(self.index(), ([], []))

    def test_running_cancel_after_metadata_does_not_publish_or_mark_missing(self):
        self.submit()
        self.run_scan()
        before = self.index()
        self.source.unlink()
        (self.music / "new.wav").write_bytes(fixtures.tagged_wav("Explicit"))
        job_id = self.submit(request_id="scan-2")["job_id"]
        reader = catalog.read_metadata
        def cancel_during_read(path):
            reply = self.cancel(job_id)
            self.assertEqual(reply["job"]["status"], "cancelling")
            return reader(path)
        with patch.object(catalog, "read_metadata", side_effect=cancel_during_read):
            self.assertEqual(self.run_scan()["status"], "cancelled")
        self.assertEqual(self.index(), before)

    def test_cancel_during_hash_stops_at_chunk_checkpoint(self):
        job_id = self.submit()["job_id"]
        original = catalog.file_hash
        def cancelling_hash(path, checkpoint):
            self.cancel(job_id)
            return original(path, checkpoint)
        with patch.object(catalog, "file_hash", side_effect=cancelling_hash):
            self.assertEqual(self.run_scan()["status"], "cancelled")
        self.assertEqual(self.index(), ([], []))

    def test_cancellation_immediately_before_publication_wins(self):
        job_id = self.submit()["job_id"]
        original = catalog.root_status
        def cancel_before_transaction(root):
            self.cancel(job_id)
            return original(root)
        with patch.object(catalog, "root_status", side_effect=cancel_before_transaction):
            self.assertEqual(self.run_scan()["status"], "cancelled")
        self.assertEqual(self.index(), ([], []))

    def test_completed_publication_wins_late_cancellation_without_relabeling(self):
        job_id = self.submit()["job_id"]
        job = self.run_scan()
        self.assertEqual(self.cancel(job_id)["job"], job)
        self.assertEqual(len(self.index()[1]), 1)

    def test_startup_recovery_marks_owned_work_interrupted_and_keeps_queued_work(self):
        self.submit()
        other = self.root / "other"
        other.mkdir()
        self.submit(request_id="scan-2", path=str(other))
        with self.connect() as db:
            db.execute("UPDATE catalog_scan_jobs SET status='running' WHERE request_id='scan-1'")
            scan_jobs.recover(db)
            scan_jobs.recover(db)
        statuses = {job["path"]: job["status"] for job in self.jobs()}
        self.assertEqual(statuses, {str(self.music.resolve()): "interrupted", str(other.resolve()): "queued"})
        self.assertEqual(self.run_scan()["status"], "completed")
        self.assertEqual(len(self.index()[0]), 1)
        self.submit(request_id="explicit-rescan")

    def test_restart_after_cancellation_records_cancelled_not_completed(self):
        job_id = self.submit()["job_id"]
        with self.connect() as db:
            db.execute("UPDATE catalog_scan_jobs SET status='running'")
        self.cancel(job_id)
        with self.connect() as db:
            scan_jobs.recover(db)
        self.assertEqual(self.jobs()[0]["status"], "cancelled")
        self.assertEqual(self.index(), ([], []))

    def test_shutdown_mid_metadata_is_interrupted_not_a_metadata_error(self):
        stop = threading.Event()
        self.submit()
        reader = catalog.read_metadata
        def stopping_reader(path):
            stop.set()
            return reader(path)
        with patch.object(catalog, "read_metadata", side_effect=stopping_reader):
            self.assertTrue(scan_jobs.run_next(self.connect, stop.is_set))
        self.assertEqual(self.jobs()[0]["status"], "interrupted")
        self.assertEqual(self.index(), ([], []))

    def test_folder_replaced_while_queued_fails_without_adopting_new_identity(self):
        self.submit()
        self.music.rename(self.root / "old-music")
        self.music.mkdir()
        with self.assertLogs(scan_jobs.LOG, level="ERROR"):
            self.assertEqual(self.run_scan()["status"], "failed")
        self.assertIn("identity changed", self.jobs()[0]["error"])
        self.assertEqual(self.index(), ([], []))

    def test_traversal_failure_preserves_entire_prior_index_and_allows_explicit_retry(self):
        self.submit()
        self.run_scan()
        before = self.index()
        self.submit(request_id="scan-2")
        with patch.object(catalog.os, "walk", side_effect=PermissionError("synthetic traversal denied")), \
                self.assertLogs(scan_jobs.LOG, level="ERROR"):
            job = self.run_scan()
        self.assertEqual(job["status"], "failed")
        self.assertIn("synthetic traversal denied", job["error"])
        self.assertEqual(self.index(), before)
        self.submit(request_id="scan-3")
        self.assertEqual(self.run_scan()["status"], "completed")

    def test_metadata_errors_and_symlinks_are_reported_without_source_changes(self):
        self.source.write_bytes(b"synthetic unreadable audio")
        (self.music / "linked.wav").symlink_to(self.source)
        self.submit()
        result = self.run_scan()["result"]
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["skipped_symlinks"], ["linked.wav"])
        self.assertEqual(len(self.index()[1]), 1)

    def test_terminal_job_write_failure_rolls_back_index_and_persists_failed_state(self):
        self.submit()
        with self.connect() as db:
            db.execute("""CREATE TRIGGER reject_complete BEFORE UPDATE ON catalog_scan_jobs
                          WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'synthetic completion failure'); END""")
        with self.assertLogs(scan_jobs.LOG, level="ERROR"):
            self.assertEqual(self.run_scan()["status"], "failed")
        self.assertEqual(self.index(), ([], []))

    def test_admission_storage_failure_never_acknowledges_or_leaves_a_partial_job(self):
        with self.connect() as db:
            db.execute("""CREATE TRIGGER reject_scan BEFORE INSERT ON catalog_scan_jobs
                          BEGIN SELECT RAISE(ABORT,'synthetic admission failure'); END""")
        with self.assertRaisesRegex(sqlite3.Error, "synthetic admission failure"):
            self.submit()
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.index(), ([], []))
        with self.connect() as db:
            db.execute("DROP TRIGGER reject_scan")
        self.assertTrue(self.submit()["ok"])

    def test_slow_admission_filesystem_checks_do_not_hold_the_review_write_lock(self):
        entered, release = threading.Event(), threading.Event()
        selected = self.music.resolve()
        original = Path.stat
        def delayed_stat(path, *args, **kwargs):
            if path == selected:
                entered.set()
                self.assertTrue(release.wait(5))
            return original(path, *args, **kwargs)
        with patch.object(Path, "stat", delayed_stat), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            admission = pool.submit(self.submit)
            try:
                self.assertTrue(entered.wait(5))
                with self.connect() as db:
                    db.execute("PRAGMA busy_timeout=1000")
                    db.execute("CREATE TABLE synthetic_review (value TEXT)")
                    db.execute("INSERT INTO synthetic_review VALUES ('correction')")
            finally:
                release.set()
            self.assertTrue(admission.result(timeout=5)["ok"])

    def test_file_bound_failure_never_marks_old_entries_missing(self):
        self.submit()
        self.run_scan()
        before = self.index()
        (self.music / "another.wav").write_bytes(fixtures.tagged_wav("Explicit"))
        self.submit(request_id="over-limit")
        with patch.object(catalog, "MAX_FILES", 1), self.assertLogs(scan_jobs.LOG, level="ERROR"):
            self.assertEqual(self.run_scan()["status"], "failed")
        self.assertIn("at most 1", self.jobs()[0]["error"])
        self.assertEqual(self.index(), before)

    def test_existing_offline_or_overlapping_root_is_not_readmitted(self):
        self.submit()
        self.run_scan()
        before = self.index()
        nested = self.music / "nested"
        nested.mkdir()
        with self.assertRaises(catalog.Conflict):
            self.submit(request_id="nested", path=str(nested))
        self.music.rename(self.root / "disconnected")
        self.music.mkdir()
        with self.assertRaises(catalog.Conflict):
            self.submit(request_id="replacement")
        self.assertEqual(self.index(), before)

    def test_acknowledged_cancellation_takes_precedence_over_simultaneous_shutdown(self):
        stop = threading.Event()
        job_id = self.submit()["job_id"]
        reader = catalog.read_metadata
        def cancel_and_stop(path):
            self.cancel(job_id)
            stop.set()
            return reader(path)
        with patch.object(catalog, "read_metadata", side_effect=cancel_and_stop):
            self.assertTrue(scan_jobs.run_next(self.connect, stop.is_set))
        self.assertEqual(self.jobs()[0]["status"], "cancelled")
        self.assertEqual(self.index(), ([], []))

    def test_single_worker_ownership_keeps_review_writes_available_during_hashing(self):
        self.submit()
        entered, release = threading.Event(), threading.Event()
        original = catalog.file_hash
        def blocked_hash(path, checkpoint):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(path, checkpoint)
        with patch.object(catalog, "file_hash", side_effect=blocked_hash), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(scan_jobs.run_next, self.connect)
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(scan_jobs.run_next(self.connect))
                with self.connect() as db:
                    db.execute("CREATE TABLE synthetic_review (value TEXT)")
                    db.execute("INSERT INTO synthetic_review VALUES ('correction')")
                self.assertEqual(self.jobs()[0]["status"], "running")
            finally:
                release.set()
            self.assertTrue(running.result(timeout=5))
        expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(self.index()[1][0]["content_hash"], expected)

    def test_polling_lists_all_active_and_only_twenty_recent_terminal_jobs(self):
        first_id = None
        for index in range(25):
            job_id = self.submit(request_id=f"scan-{index}")["job_id"]
            if first_id is None:
                first_id = job_id
            self.cancel(job_id)
        self.submit(request_id="active")
        jobs = self.jobs()
        self.assertEqual(len(jobs), 21)
        self.assertEqual(sum(job["status"] == "queued" for job in jobs), 1)
        self.assertFalse(any(job["id"] == first_id for job in jobs))

    def test_worker_stop_wakes_without_waiting_for_more_work(self):
        stop = threading.Event()
        thread = threading.Thread(target=scan_jobs.worker, args=(self.connect, stop))
        thread.start()
        stop.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def moved_fixture(self):
        self.submit()
        self.run_scan()
        before = self.index()
        moved = self.root / "moved-music"
        self.music.rename(moved)
        body = {"request_id": "reassociate-1", "path": str(moved),
                "root_id": before[0][0]["id"], "base_revision": before[0][0]["scan_revision"]}
        return before, moved, body

    def history(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM catalog_root_reassociations")]

    def test_reassociation_preserves_ids_forces_hashes_and_records_atomic_history(self):
        before, moved, body = self.moved_fixture()
        original = (moved / self.source.name).read_bytes()
        receipt = self.submit(**body)
        with patch.object(catalog, "file_hash", wraps=catalog.file_hash) as hashing:
            job = self.run_scan()
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(hashing.call_count, 1)
        self.assertTrue(job["result"]["reassociated"])
        self.assertEqual(job["result"]["verified_files"], 1)
        after = self.index()
        self.assertEqual(after[0][0]["id"], before[0][0]["id"])
        self.assertEqual(after[0][0]["path"], str(moved.resolve()))
        self.assertEqual(after[0][0]["scan_revision"], before[0][0]["scan_revision"] + 1)
        self.assertEqual(after[1][0]["id"], before[1][0]["id"])
        self.assertEqual(after[1][0]["content_hash"], before[1][0]["content_hash"])
        with self.connect() as db:
            self.assertEqual(catalog.file_records(db)[0]["state"], "available")
        history = self.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["job_id"], receipt["job_id"])
        self.assertEqual(json.loads(history[0]["before_json"])["path"], before[0][0]["path"])
        self.assertEqual((moved / self.source.name).read_bytes(), original)
        self.assertEqual(self.submit(**body), receipt)
        self.assertEqual(self.history(), history)

    def test_reassociation_admission_rejects_online_unknown_stale_or_invalid_revision(self):
        self.submit()
        self.run_scan()
        root = self.index()[0][0]
        for changes in ({"root_id": root["id"], "base_revision": root["scan_revision"]},
                        {"root_id": "unknown", "base_revision": 1},
                        {"root_id": root["id"], "base_revision": 0},
                        {"root_id": root["id"], "base_revision": True},
                        {"root_id": root["id"]}):
            with self.assertRaises(ValueError):
                self.submit(request_id="bad", **changes)
        self.assertEqual(self.history(), [])

    def test_changed_missing_errored_or_symlink_proof_rolls_back_reassociation(self):
        before, moved, body = self.moved_fixture()
        proof = moved / self.source.name
        original = proof.read_bytes()
        for mode in ("changed", "missing", "symlink", "errored"):
            proof.unlink(missing_ok=True)
            proof.write_bytes(original)
            if mode == "changed":
                proof.write_bytes(original + b"changed")
            elif mode == "missing":
                proof.unlink()
            elif mode == "symlink":
                target = self.root / "outside.wav"
                target.write_bytes(original)
                proof.unlink()
                proof.symlink_to(target)
            elif mode == "errored":
                with self.connect() as db:
                    db.execute("UPDATE library_files SET error='synthetic parse error'")
            prior = self.index()
            self.submit(**{**body, "request_id": mode})
            with self.assertLogs(scan_jobs.LOG, level="ERROR"):
                self.assertEqual(self.run_scan()["status"], "failed")
            self.assertEqual(self.index(), prior)
            self.assertEqual(self.history(), [])
        self.assertEqual(self.index()[0], before[0])

    def test_empty_index_cannot_prove_reassociation(self):
        self.source.unlink()
        before, _, body = self.moved_fixture()
        self.submit(**body)
        with self.assertLogs(scan_jobs.LOG, level="ERROR"):
            self.assertEqual(self.run_scan()["status"], "failed")
        self.assertEqual(self.index(), before)
        self.assertEqual(self.history(), [])

    def test_reassociation_allows_additional_files_without_stealing_existing_ids(self):
        before, moved, body = self.moved_fixture()
        (moved / "new.wav").write_bytes(fixtures.tagged_wav("Explicit"))
        self.submit(**body)
        self.assertEqual(self.run_scan()["status"], "completed")
        after = self.index()
        self.assertEqual(len(after[1]), 2)
        self.assertIn(before[1][0]["id"], [row["id"] for row in after[1]])
        self.assertEqual(len(self.history()), 1)

    def test_reassociation_to_same_path_replacement_requires_full_bytes_not_stat_reuse(self):
        before, moved, body = self.moved_fixture()
        shutil.copytree(moved, self.music)
        self.submit(**{**body, "path": str(self.music)})
        with patch.object(catalog, "file_hash", wraps=catalog.file_hash) as hashing:
            self.assertEqual(self.run_scan()["status"], "completed")
        self.assertEqual(hashing.call_count, 1)
        self.assertEqual(self.index()[0][0]["id"], before[0][0]["id"])
        self.assertNotEqual(self.index()[0][0]["inode"], before[0][0]["inode"])

    def test_reassociation_cancellation_preserves_entire_root_index_and_history(self):
        before, _, body = self.moved_fixture()
        job_id = self.submit(**body)["job_id"]
        reader = catalog.read_metadata
        def cancel_read(path):
            self.cancel(job_id)
            return reader(path)
        with patch.object(catalog, "read_metadata", side_effect=cancel_read):
            self.assertEqual(self.run_scan()["status"], "cancelled")
        self.assertEqual(self.index(), before)
        self.assertEqual(self.history(), [])

    def test_reassociation_cannot_publish_after_original_returns_or_revision_changes(self):
        before, moved, body = self.moved_fixture()
        for mode in ("online", "revision"):
            self.submit(**{**body, "request_id": mode})
            reader = catalog.read_metadata
            def change_during_read(path):
                value = reader(path)
                if mode == "online":
                    self.music.symlink_to(moved, target_is_directory=True)
                else:
                    with self.connect() as db:
                        db.execute("UPDATE library_roots SET scan_revision=scan_revision+1")
                return value
            with patch.object(catalog, "read_metadata", side_effect=change_during_read), \
                    self.assertLogs(scan_jobs.LOG, level="ERROR"):
                self.assertEqual(self.run_scan()["status"], "failed")
            if self.music.is_symlink():
                self.music.unlink()
            self.assertEqual(self.index()[0][0]["path"], before[0][0]["path"])
            self.assertEqual(self.history(), [])

    def test_reassociation_terminal_write_failure_rolls_back_root_and_audit(self):
        before, _, body = self.moved_fixture()
        self.submit(**body)
        with self.connect() as db:
            db.execute("""CREATE TRIGGER reject_reassociate BEFORE UPDATE ON catalog_scan_jobs
                          WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'synthetic completion failure'); END""")
        with self.assertLogs(scan_jobs.LOG, level="ERROR"):
            self.assertEqual(self.run_scan()["status"], "failed")
        self.assertEqual(self.index(), before)
        self.assertEqual(self.history(), [])

    def test_reassociation_rejects_replacement_changed_between_execution_identity_checks(self):
        before, moved, body = self.moved_fixture()
        self.submit(**body)
        original = catalog.root_status
        replaced = False
        def replace_before_proof(root):
            nonlocal replaced
            if root["path"] == before[0][0]["path"] and not replaced:
                replaced = True
                saved = self.root / "replacement-before-proof"
                moved.rename(saved)
                shutil.copytree(saved, moved)
            return original(root)
        with patch.object(catalog, "root_status", side_effect=replace_before_proof), \
                self.assertLogs(scan_jobs.LOG, level="ERROR"):
            self.assertEqual(self.run_scan()["status"], "failed")
        self.assertIn("identity changed", self.jobs()[0]["error"])
        self.assertEqual(self.index(), before)
        self.assertEqual(self.history(), [])

    def test_reassociation_blocks_pending_same_root_and_overlapping_index(self):
        _, moved, body = self.moved_fixture()
        self.submit(**body)
        copy = self.root / "copy"
        shutil.copytree(moved, copy)
        with self.assertRaises(catalog.Conflict):
            self.submit(**{**body, "request_id": "other", "path": str(copy)})
        with self.assertRaises(catalog.Conflict):
            self.submit(request_id="ordinary", path=str(moved))
        with self.connect() as db:
            db.execute("UPDATE catalog_scan_jobs SET status='cancelled' WHERE status='queued'")
        with self.connect() as db:
            catalog.scan_folder(db, str(copy))
        with self.assertRaises(catalog.Conflict):
            self.submit(**{**body, "request_id": "steal", "path": str(copy)})

    def test_reassociation_detects_early_proof_file_changed_while_later_file_is_read(self):
        (self.music / "z-last.wav").write_bytes(fixtures.tagged_wav("Last"))
        before, moved, body = self.moved_fixture()
        self.submit(**body)
        reader = catalog.read_metadata
        def mutate_earlier(path):
            value = reader(path)
            if path.name == "z-last.wav":
                (moved / self.source.name).write_bytes(b"synthetic mutation after proof")
            return value
        with patch.object(catalog, "read_metadata", side_effect=mutate_earlier), \
                self.assertLogs(scan_jobs.LOG, level="ERROR"):
            self.assertEqual(self.run_scan()["status"], "failed")
        self.assertEqual(self.index(), before)
        self.assertEqual(self.history(), [])

    def test_reassociation_keeps_missing_rows_missing_and_rejects_changed_returned_bytes(self):
        old_missing = self.music / "Missing.wav"
        old_missing.write_bytes(fixtures.tagged_wav("Missing"))
        self.submit()
        self.run_scan()
        missing_bytes = old_missing.read_bytes()
        old_missing.unlink()
        self.submit(request_id="missing-scan")
        self.run_scan()
        before = self.index()
        moved = self.root / "moved-music"
        self.music.rename(moved)
        root = before[0][0]
        body = {"request_id": "reassociate-missing", "path": str(moved),
                "root_id": root["id"], "base_revision": root["scan_revision"]}
        self.submit(**body)
        self.assertEqual(self.run_scan()["status"], "completed")
        self.assertEqual(next(row for row in self.index()[1] if row["relative_path"] == "Missing.wav")["available"], 0)
        # A returning historical file is only allowed to retain its old identity
        # if its bytes still agree, not simply because the relative name agrees.
        moved.rename(self.root / "second-move")
        replacement = self.root / "second-move"
        (replacement / "Missing.wav").write_bytes(missing_bytes + b"changed")
        root = self.index()[0][0]
        self.submit(request_id="changed-return", path=str(replacement), root_id=root["id"],
                    base_revision=root["scan_revision"])
        prior = self.index()
        with self.assertLogs(scan_jobs.LOG, level="ERROR"):
            self.assertEqual(self.run_scan()["status"], "failed")
        self.assertEqual(self.index(), prior)


if __name__ == "__main__":
    unittest.main()
