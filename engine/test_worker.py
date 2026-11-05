import concurrent.futures
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import signal
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze
import autocue_worker as worker
from worker_fixture import fixture_request


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.request = fixture_request(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_prepared_pipeline_runs_offline_without_models_or_ffmpeg_and_keeps_source_unchanged(self):
        before = Path(self.request["source"]).read_bytes()
        messages = []
        with patch.object(analyze, "_run_beats", side_effect=AssertionError("model called")), \
                patch.object(analyze, "_run_demucs", side_effect=AssertionError("model called")), \
                patch.object(analyze, "_run_sections", side_effect=AssertionError("model called")), \
                patch.object(analyze, "_run_words", side_effect=AssertionError("model called")), \
                patch.object(analyze, "_run_lyrics", side_effect=AssertionError("network lookup")), \
                patch.object(analyze, "torch_device", side_effect=AssertionError("Torch loaded")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("network called")), \
                patch("shutil.which", return_value=None):
            result = worker.run(self.request, messages.append)
        self.assertEqual(Path(self.request["source"]).read_bytes(), before)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["track_hash"], analyze.file_hash(self.request["source"]))
        self.assertEqual(result["plan"]["grid"]["source"], "prepared_downbeats")
        self.assertEqual(result["plan"]["grid"]["bar_s"], 2)
        self.assertEqual(set(result["plan"]["cues"]), set("ABCDEFGH"))
        self.assertEqual({slot: result["plan"]["cues"][slot]["bar"] for slot in "ABCDEFGH"},
                         dict(zip("ABCDEFGH", [None, 0, None, None, None, None, 16, 24])))
        self.assertLessEqual(max(c["confidence"] or 0 for c in result["plan"]["cues"].values()), 0.55)
        self.assertEqual(result["plan"]["provenance"]["profile"], "prepared")
        self.assertTrue(result["plan"]["provenance"]["beats_hash"])
        self.assertEqual([m["sequence"] for m in messages], list(range(1, len(messages) + 1)))
        self.assertTrue(all(m["job_id"] == self.request["job_id"] and m["schema_version"] == 1 for m in messages))
        self.assertEqual(messages[-1]["status"], "completed")
        self.assertEqual(list(self.root.rglob("job-*")), [])
        self.assertEqual(list(self.root.rglob("decoded.wav")), [])

    def test_prepared_cache_does_not_pollute_model_cache_and_reuses_features(self):
        first = worker.run(self.request)
        digest = first["track_hash"]
        cache = Path(self.request["cache_dir"])
        self.assertFalse((cache / digest).exists())
        with patch.object(analyze, "compute_features", side_effect=AssertionError("computed twice")):
            second = worker.run(self.request)
        self.assertEqual(first["plan"]["cues"], second["plan"]["cues"])
        self.assertEqual(first["plan"]["provenance"], second["plan"]["provenance"])
        observations = json.loads(Path(self.request["beats_path"]).read_text())
        observations["downbeats"] = [t + 0.05 for t in observations["downbeats"]]
        Path(self.request["beats_path"]).write_text(json.dumps(observations))
        changed = worker.run(self.request)
        self.assertNotEqual(first["plan"]["provenance"]["beats_hash"], changed["plan"]["provenance"]["beats_hash"])
        self.assertEqual(len(list((cache / "prepared-v1").iterdir())), 2)

    def test_metadata_and_config_are_part_of_proposal_provenance(self):
        with patch.object(analyze, "analyze", side_effect=lambda *a, **k: {"cues": {}}):
            first = worker.run(self.request)
            changed = worker.run({**self.request, "genre": "house"})
        self.assertNotEqual(first["plan"]["provenance"]["settings_hash"],
                            changed["plan"]["provenance"]["settings_hash"])

    def test_invalid_requests_fail_before_analyzers_or_network(self):
        invalid = [
            {**self.request, "schema_version": True}, {**self.request, "schema_version": 2},
            {**self.request, "source": ""}, {**self.request, "shell": "not permitted"},
            {**self.request, "profile": "unknown"}, {**self.request, "beats_path": ""},
            {**self.request, "gemini": True}, {**self.request, "allow_lyrics": True},
            {**self.request, "allow_lyrics": "false"},
            {**self.request, "source": "relative.wav"},
            {**self.request, "cache_dir": ""},
            {**self.request, "experiments": ["unrecognized"]},
        ]
        with patch.object(analyze, "analyze", side_effect=AssertionError("analyzer called")):
            for request in invalid:
                with self.subTest(request=request), self.assertRaises(ValueError):
                    worker.run(request)

    def test_mismatched_hash_and_beats_outside_audio_report_failed_not_completed(self):
        messages = []
        with self.assertRaisesRegex(ValueError, "track_hash"):
            worker.run({**self.request, "track_hash": "wrong"}, messages.append)
        self.assertEqual(messages[-1]["status"], "failed")
        data = json.loads(Path(self.request["beats_path"]).read_text())
        data["beats"].append(1000)
        Path(self.request["beats_path"]).write_text(json.dumps(data))
        messages = []
        with self.assertRaisesRegex(ValueError, "duration"):
            worker.run(self.request, messages.append)
        self.assertEqual(messages[-1]["status"], "failed")
        self.assertNotIn("completed", [m["status"] for m in messages])
        self.assertEqual(list(self.root.rglob("job-*")), [])

    def test_beat_artifacts_reject_unsorted_boolean_or_invalid_values(self):
        for values in ([0, 2, 1, 4], [0, 1, True, 4], [0, 1, 2, float("nan")], [0, 1, 2, 2]):
            Path(self.request["beats_path"]).write_text(json.dumps({"beats": values, "downbeats": [0, 2, 4, 6]}))
            with self.assertRaises(ValueError):
                worker.load_beats(self.request["beats_path"])

    def test_cancel_between_steps_keeps_progress_truthful_and_cleans_only_owned_work(self):
        stop = threading.Event()
        messages = []
        unrelated = self.root / "cache" / "unrelated" / "keep.txt"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("keep")

        def progress(message):
            messages.append(message)
            if message["kind"] == "progress" and message["data"]["step"] == "beats":
                stop.set()

        with self.assertRaises(analyze.AnalysisCancelled):
            worker.run(self.request, progress, stop.is_set)
        self.assertEqual(messages[-1]["status"], "cancelled")
        self.assertNotIn("completed", [m["status"] for m in messages])
        self.assertEqual(list(self.root.rglob("job-*")), [])
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertEqual(worker.run(self.request)["status"], "completed")

    def test_result_write_is_atomic_and_never_targets_job_inputs(self):
        target = self.root / "result.json"
        target.write_text('{"previous":true}')
        result = {"status": "completed", "schema_version": 1}
        with patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                worker.write_result(target, result)
        self.assertEqual(json.loads(target.read_text()), {"previous": True})
        self.assertEqual(list(self.root.glob("result.json.*.tmp")), [])
        for path in (self.request["source"], self.request["beats_path"]):
            with self.assertRaises(ValueError):
                worker.write_result(path, result, [path])
        worker.write_result(target, result)
        self.assertEqual(json.loads(target.read_text()), result)

    def test_cli_writes_one_completed_message_and_schema_valid_result(self):
        target = self.root / "result.json"
        command = [sys.executable, str(Path(worker.__file__)), "fixture", "--out", str(target)]
        completed = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        messages = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(messages[-1]["status"], "completed")
        self.assertEqual([m["sequence"] for m in messages], list(range(1, len(messages) + 1)))
        self.assertEqual(sum(m["status"] == "completed" for m in messages), 1)
        result = json.loads(target.read_text())
        self.assertEqual(messages[-1]["data"]["track_hash"], result["track_hash"])
        self.assertEqual(result["plan"]["provenance"]["profile"], "prepared")

    def test_cli_output_failure_never_announces_completion(self):
        command = [sys.executable, str(Path(worker.__file__)), "fixture", "--out", str(self.root / "not-json.txt")]
        completed = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(completed.returncode, 0)
        messages = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(messages[-1]["status"], "failed")
        self.assertNotIn("completed", [m["status"] for m in messages])
        self.assertFalse((self.root / "not-json.txt").exists())

    def test_cli_cancellation_after_analysis_does_not_replace_previous_result(self):
        target = self.root / "result.json"
        target.write_text('{"previous":true}')
        original_run = worker.run

        def finish_then_cancel(*args):
            result = original_run(*args)
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return result

        messages, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["autocue-worker", "fixture", "--out", str(target)]), \
                patch.object(worker, "run", side_effect=finish_then_cancel), \
                patch.object(sys, "__stdout__", messages), redirect_stdout(messages), redirect_stderr(errors):
            self.assertEqual(worker.main(), 130)
        events = [json.loads(line) for line in messages.getvalue().splitlines()]
        self.assertEqual(events[-1]["status"], "cancelled")
        self.assertNotIn("completed", [m["status"] for m in events])
        self.assertEqual(json.loads(target.read_text()), {"previous": True})

    def test_source_change_between_worker_identity_and_engine_run_fails(self):
        def change_source(*args, **kwargs):
            Path(self.request["source"]).write_bytes(b"changed")
            return {"cues": {}}
        messages = []
        with patch.object(analyze, "analyze", side_effect=change_source):
            with self.assertRaisesRegex(RuntimeError, "identity"):
                worker.run(self.request, messages.append)
        self.assertEqual(messages[-1]["status"], "failed")

    def test_cache_write_failure_preserves_previous_features_and_retries(self):
        import numpy as np
        path = self.root / "features.npz"
        analyze._write_npz(path, value=[1, 2])
        with patch.object(np, "savez_compressed", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                analyze._write_npz(path, value=[3, 4])
        with np.load(path) as arrays:
            self.assertEqual(arrays["value"].tolist(), [1, 2])
        self.assertEqual(list(self.root.glob("features.npz.*.tmp")), [])
        analyze._write_npz(path, value=[3, 4])
        with np.load(path) as arrays:
            self.assertEqual(arrays["value"].tolist(), [3, 4])


class WorkspaceTests(unittest.TestCase):
    def test_same_track_is_serialized_but_other_tracks_have_independent_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            same = root / "same"
            other = root / "other"
            same.mkdir()
            other.mkdir()
            entered, release, waiting = threading.Event(), threading.Event(), threading.Event()

            def first():
                with analyze.track_workspace(analyze.Paths(same), None):
                    entered.set()
                    self.assertTrue(release.wait(5))

            def second():
                self.assertTrue(entered.wait(5))
                with analyze.track_workspace(analyze.Paths(same), None):
                    waiting.set()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(first), pool.submit(second)
                try:
                    self.assertTrue(entered.wait(5))
                    with analyze.track_workspace(analyze.Paths(other), None):
                        self.assertFalse(waiting.wait(0.1))
                finally:
                    release.set()
                a.result(timeout=5)
                b.result(timeout=5)
            self.assertTrue(waiting.is_set())
            self.assertEqual(list(root.rglob("job-*")), [])

    def test_waiting_job_can_cancel_without_removing_active_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = analyze.Paths(root)
            stop = threading.Event()
            with analyze.track_workspace(paths, None):
                marker = paths.work / "active.txt"
                marker.write_text("active")
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    def wait():
                        with analyze.track_workspace(analyze.Paths(root), stop.is_set):
                            self.fail("cancelled job acquired the workspace")
                    future = pool.submit(wait)
                    stop.set()
                    with self.assertRaises(analyze.AnalysisCancelled):
                        future.result(timeout=5)
                self.assertEqual(marker.read_text(), "active")
            self.assertFalse(marker.exists())
