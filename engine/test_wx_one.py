import concurrent.futures
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class TranscriptionReuseTests(unittest.TestCase):
    def setUp(self):
        self.model = Mock()
        self.backend = SimpleNamespace(
            load_model=Mock(return_value=self.model),
            load_audio=Mock(side_effect=lambda wav: wav),
            load_align_model=Mock(side_effect=lambda **kwargs: (kwargs["language_code"], {})),
            align=Mock(side_effect=lambda segments, *_args, **_kwargs: {"segments": segments}),
        )
        torch = SimpleNamespace(load=Mock(), set_num_threads=Mock())
        spec = importlib.util.spec_from_file_location("wx_one_fixture", Path(__file__).with_name("wx_one.py"))
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"torch": torch, "whisperx": self.backend}):
            spec.loader.exec_module(self.module)
        self.module.vocal_window = lambda audio: "window:" + audio
        self.languages = {"one": "en", "two": "es", "three": "en", "four": "fr", "five": "es"}

        def transcribe(audio, **kwargs):
            if audio.startswith("window:"):
                return {"language": self.languages[audio.removeprefix("window:")]}
            language = self.languages[audio]
            self.assertEqual(kwargs["language"], language)
            return {"language": language, "segments": [{"words": [
                {"word": audio, "start": 51.125, "end": 51.375, "score": 0.95}]}]}

        self.model.transcribe.side_effect = transcribe

    def test_model_reused_with_per_track_language_and_unchanged_full_track_timings(self):
        for audio in ("one", "two", "three", "four", "five"):
            record = self.module.transcribe_local(audio)
            self.assertEqual(record["language"], self.languages[audio])
            self.assertIsNone(record["alignment_error"])
            self.assertEqual(record["words"][0]["start"], 51.125)
            self.assertEqual(record["words"][0]["end"], 51.375)
            self.assertLessEqual(len(self.module._aligners), 2)
        self.backend.load_model.assert_called_once_with("small", "cpu", compute_type="int8")
        self.assertEqual([call.kwargs["language_code"] for call in self.backend.load_align_model.call_args_list],
                         ["en", "es", "fr", "es"])
        self.assertEqual(self.model.transcribe.call_count, 10)
        self.assertEqual([call.args[0] for call in self.backend.load_audio.call_args_list],
                         ["one", "two", "three", "four", "five"])

    def test_failed_model_load_is_reported_and_not_cached(self):
        self.backend.load_model.side_effect = [RuntimeError("model unavailable"), self.model]
        with self.assertRaisesRegex(RuntimeError, "model unavailable"):
            self.module.transcribe_local("one")
        self.assertIsNone(self.module._model)
        self.assertEqual(self.module.transcribe_local("one")["language"], "en")
        self.assertEqual(self.backend.load_model.call_count, 2)

    def test_failed_aligner_is_reported_and_retried_on_next_track(self):
        self.backend.load_align_model.side_effect = [RuntimeError("aligner unavailable"), ("en", {})]
        record = self.module.transcribe_local("one")
        self.assertIn("aligner unavailable", record["alignment_error"])
        self.assertNotIn("en", self.module._aligners)
        self.assertIsNone(self.module.transcribe_local("three")["alignment_error"])
        self.assertEqual(self.backend.load_align_model.call_count, 2)

    def test_parallel_requests_cannot_use_shared_model_concurrently(self):
        active, maximum = 0, 0
        counter_lock = threading.Lock()

        def transcribe(wav, model, language, aligners):
            nonlocal active, maximum
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with counter_lock:
                active -= 1
            return {"language": "en", "words": [{"word": wav}], "alignment_error": None}

        self.module.transcribe = transcribe
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            records = list(workers.map(self.module.transcribe_local, ("one", "two", "three", "four")))
        self.assertEqual([record["words"][0]["word"] for record in records], ["one", "two", "three", "four"])
        self.assertEqual(maximum, 1)
        self.backend.load_model.assert_called_once()

    def test_manifest_retries_failed_alignment_and_resumes_successful_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.csv").write_text("id\n1\n")
            (root / "audio").mkdir()
            (root / "audio" / "01.wav").touch()
            (root / "words").mkdir()
            (root / "words" / "01.json").write_text(json.dumps({"alignment_error": "retry me"}))
            transcribe = Mock(return_value={"language": "en", "words": [], "alignment_error": None})
            with patch.object(self.module, "transcribe_local", transcribe):
                first = self.module.transcribe_manifest(root / "manifest.csv", root / "audio", root / "words")
                second = self.module.transcribe_manifest(root / "manifest.csv", root / "audio", root / "words")
            self.assertEqual((first["transcribed"], first["skipped"]), (1, 0))
            self.assertEqual((second["transcribed"], second["skipped"]), (0, 1))
            transcribe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
