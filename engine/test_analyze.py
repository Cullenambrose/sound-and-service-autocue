"""Pipeline tests with synthetic audio. Analyzers that are not installed are replaced by fixtures,
so this checks the orchestration, cache layout, fallbacks and confidence capping, not model accuracy."""
import json
import shutil
import sys
import tempfile
import unittest
import wave
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import numpy as np
except ImportError:
    np = None

import analyze


def click_track(path, seconds=90, bpm=120, rate=44100):
    t = np.arange(int(seconds * rate)) / rate
    audio = 0.05 * np.sin(2 * np.pi * 220 * t)
    beat = 60 / bpm
    for index in range(int(seconds / beat)):
        start = int(index * beat * rate)
        kick = np.arange(2000) / rate
        audio[start:start + 2000] += 0.8 * np.sin(2 * np.pi * 60 * kick) * np.exp(-kick * 30)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(np.repeat(pcm[:, None], 2, 1).tobytes())


@unittest.skipIf(np is None, "NumPy is required")
class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.song = root / "click.wav"
        click_track(self.song)
        self.cache = root / "cache"

    def tearDown(self):
        self.tmp.cleanup()

    def fake_beats(self, _wav, paths):
        beat = 0.5
        record = {"beats": [round(i * beat, 4) for i in range(180)],
                  "downbeats": [round(i * beat * 4, 4) for i in range(45)]}
        analyze._write_json(paths.beats, record)
        return record

    def run_pipeline(self, events):
        with patch.object(analyze, "_run_beats", self.fake_beats), \
                patch.object(analyze, "decode_wav", lambda src, dst, **_k: (dst.parent.mkdir(parents=True, exist_ok=True), shutil.copy(src, dst))), \
                patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch.object(analyze, "available", lambda name: name == "numpy"), \
                patch.object(analyze, "_run_lyrics", lambda *_a: {"syncedLyrics": None}):
            return analyze.analyze(self.song, "A", "T", "House", lambda *e: events.append(e), self.cache)

    def test_fallbacks_cap_confidence_name_missing_analyzers_and_never_touch_audio(self):
        before = self.song.read_bytes()
        events = []
        plan = self.run_pipeline(events)
        self.assertEqual(self.song.read_bytes(), before)
        states = {step: state for step, state, _ in events}
        self.assertEqual(states["beats"], "done")
        self.assertEqual(states["sections"], "skipped")
        self.assertEqual(states["vocals"], "skipped")
        self.assertTrue(any("allin1 is not installed" in note for note in plan["pipeline"]["notes"]))
        self.assertTrue(any("demucs is not installed" in note for note in plan["pipeline"]["notes"]))
        self.assertAlmostEqual(plan["grid"]["bar_s"], 2.0, places=3)
        for cue in plan["cues"].values():
            if cue.get("confidence") is not None:
                self.assertLessEqual(cue["confidence"], analyze.DEGRADED_CONFIDENCE_CAP)
        self.assertIn("section labels unavailable", " ".join(plan["flags"]))

    def test_results_are_cached_in_the_v5_layout_by_content_hash(self):
        self.run_pipeline([])
        folder = self.cache / analyze.file_hash(self.song)
        for relative in ("features/00.npz", "out/beat_this/00.json", "meta.json"):
            self.assertTrue((folder / relative).is_file(), relative)
        meta = json.loads((folder / "meta.json").read_text())
        self.assertIn("analyzers", meta)
        features = np.load(folder / "features/00.npz")
        self.assertAlmostEqual(float(features["dur"]), 90, places=0)

    def test_missing_beat_model_is_a_named_install_error(self):
        with patch.object(analyze, "decode_wav", lambda src, dst, **_k: (dst.parent.mkdir(parents=True, exist_ok=True), shutil.copy(src, dst))), \
                patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch.object(analyze, "available", lambda name: name == "numpy"):
            with self.assertRaisesRegex(RuntimeError, "beat_this is not installed"):
                analyze.analyze(self.song, cache_dir=self.cache)

    def test_grid_fit_recovers_bar_length(self):
        grid = analyze.fit_grid([1.0 + 1.9 * i for i in range(20)])
        self.assertAlmostEqual(grid["bar_s"], 1.9, places=3)
        self.assertAlmostEqual(grid["t1"], 1.0, places=3)
        with self.assertRaises(RuntimeError):
            analyze.fit_grid([1, 2])

    def test_stem_envelopes_keep_final_partial_frame_for_outro_validation(self):
        paths = analyze.Paths(self.cache / "tail")
        decoded = paths.work / "decoded.wav"
        stem_dir = paths.work / "demix" / "htdemucs" / decoded.stem
        stem_dir.mkdir(parents=True)
        (stem_dir / "vocals.wav").touch()
        samples = np.concatenate((np.zeros(analyze.STEM_HOP, dtype=np.float32),
                                  np.full(17, 0.2, dtype=np.float32)))
        with patch.object(analyze, "available", return_value=True), \
                patch.object(analyze, "read_stem", return_value=(samples, 44100)), \
                patch("shutil.which", return_value=None):
            analyze._run_demucs(decoded, paths)
        with np.load(paths.stems_env) as env:
            for name in ("vocals", "drums", "bass", "other"):
                self.assertEqual(len(env[name]), 2)
                self.assertAlmostEqual(float(env[name][-1]), 0.2, places=6)
            self.assertGreaterEqual(len(env["vocals"]) * float(env["hop_s"]), len(samples) / 44100)

    def test_grid_fit_supports_slow_tempos_and_preserves_the_fast_range(self):
        for bpm in (60, 73, 80, 120, 220, 240):
            with self.subTest(bpm=bpm):
                downbeats = [0.04 + 240 / bpm * i for i in range(96)]
                grid = analyze.fit_grid(downbeats)
                self.assertAlmostEqual(grid["bpm"], bpm, places=2)
                self.assertLess(grid["fit"], 0.001)
        for bpm in (50, 250):
            with self.subTest(bpm=bpm), self.assertRaises(RuntimeError):
                analyze.fit_grid([0.04 + 240 / bpm * i for i in range(96)])

    def test_slow_grid_with_local_false_downbeats_retains_serato_reconciliation(self):
        import serato_grid
        for bpm in (73, 80.02):
            with self.subTest(bpm=bpm):
                downbeats = [0.04 + 240 / bpm * i for i in range(96)]
                downbeats += [1.24, 2.88, 91.18, 91.34]
                detected = analyze.fit_grid(sorted(downbeats))
                self.assertAlmostEqual(detected["bpm"], bpm, places=2)
                grid, note = serato_grid.reconcile(detected, [{"pos": 0.02, "bpm": bpm}])
                self.assertEqual(grid["source"], "serato_beatgrid")
                self.assertIn("used the Serato beatgrid", note)

    def test_word_runner_reuses_successful_records_but_retries_failed_alignment(self):
        paths = analyze.Paths(self.cache / "words")
        paths.vocal_wav.parent.mkdir(parents=True, exist_ok=True)
        paths.vocal_wav.touch()
        records = [
            {"language": "en", "words": [], "alignment_error": "temporary model error"},
            {"language": "en", "words": [{"start": 51.125, "end": 51.375}], "alignment_error": None},
        ]
        module = SimpleNamespace(transcribe_local=Mock(side_effect=records))
        with patch.object(analyze, "available", return_value=True), patch.dict(sys.modules, {"wx_one": module}):
            with self.assertRaisesRegex(RuntimeError, "WhisperX word alignment failed: temporary model error"):
                analyze._run_words(paths)
            self.assertIn("temporary", json.loads(paths.words.read_text())["alignment_error"])
            self.assertIsNone(analyze._run_words(paths)["alignment_error"])
            self.assertEqual(analyze._run_words(paths)["words"][0]["start"], 51.125)
        self.assertEqual(module.transcribe_local.call_count, 2)

    def test_empty_section_result_is_cached_without_reloading_model(self):
        paths = analyze.Paths(self.cache / "sections")
        module = SimpleNamespace(analyze=Mock(return_value=SimpleNamespace(segments=[])))
        with patch.object(analyze, "available", return_value=True), \
                patch.object(analyze, "_install_allin1_compat"), \
                patch.dict(sys.modules, {"allin1": module}):
            first = analyze._run_sections(paths.work / "decoded.wav", paths)
            second = analyze._run_sections(paths.work / "decoded.wav", paths)
        self.assertEqual(first, {"engine": "allin1", "segments": []})
        self.assertEqual(second, first)
        self.assertEqual(module.analyze.call_count, 1)

    def test_word_alignment_failure_is_visible_in_pipeline_progress_and_notes(self):
        def vocals(_wav, paths):
            paths.vocal_wav.parent.mkdir(parents=True, exist_ok=True)
            paths.vocal_wav.touch()
        events = []
        with patch.object(analyze, "_run_demucs", vocals), \
                patch.object(analyze, "_run_words", side_effect=RuntimeError(
                    "WhisperX word alignment failed: No default align-model for language: la")):
            plan = self.run_pipeline(events)
        self.assertTrue(any(step == "lyrics" and state == "failed" and "language: la" in detail
                            for step, state, detail in events))
        self.assertTrue(any("WhisperX word alignment failed" in note for note in plan["pipeline"]["notes"]))
        self.assertIn("word timings unavailable", " ".join(plan["flags"]))

    def test_slow_tempo_pipeline_reaches_imported_grid_without_fallback(self):
        beats = {"beats": [0.04 + 60 / 73 * i for i in range(100)],
                 "downbeats": [0.04 + 240 / 73 * i for i in range(25)]}
        with patch.object(self, "fake_beats", return_value=beats), \
                patch("serato_grid.read_beatgrid", return_value=[{"pos": 0.02, "bpm": 73}]):
            plan = self.run_pipeline([])
        self.assertEqual(plan["grid"]["source"], "serato_beatgrid")
        self.assertAlmostEqual(plan["grid"]["bpm"], 73)
        self.assertAlmostEqual(plan["grid"]["provenance"]["detected_grid"]["bpm"], 73)
        self.assertFalse(plan["grid"]["provenance"]["user_verified"])

    def test_grid_fit_ignores_local_false_downbeats_and_missing_bars(self):
        true_downbeats = [0.32 + 1.96 * i for i in range(120) if i not in (16, 62)]
        false_cluster = [89.28, 90.74, 90.84, 91.32, 91.72, 91.82, 92.8, 93.78, 94.68, 94.76, 95.24, 96.72]
        grid = analyze.fit_grid(sorted(true_downbeats + false_cluster))
        self.assertAlmostEqual(grid["bar_s"], 1.96, places=2)
        self.assertAlmostEqual(grid["t1"], 0.32, places=2)
        self.assertLess(grid["fit"], 0.05)

    @unittest.skipUnless(analyze.available("torch"), "Torch is required for NATTEN compatibility tests.")
    def test_allin1_legacy_attention_compatibility(self):
        import torch
        analyze._install_allin1_compat()
        from natten import functional

        q1 = torch.randn(1, 2, 7, 4)
        bias1 = torch.zeros(2, 5)
        scores1 = functional.natten1dqkrpb(q1, q1, bias1, 3, 1)
        context1 = functional.natten1dav(torch.softmax(scores1, -1), q1, 3, 1)
        self.assertEqual(tuple(scores1.shape), (1, 2, 7, 3))
        self.assertEqual(tuple(context1.shape), tuple(q1.shape))

        q2 = torch.randn(1, 2, 4, 5, 3)
        bias2 = torch.zeros(2, 5, 5)
        scores2 = functional.natten2dqkrpb(q2, q2, bias2, 3, 1)
        context2 = functional.natten2dav(torch.softmax(scores2, -1), q2, 3, 1)
        self.assertEqual(tuple(scores2.shape), (1, 2, 4, 5, 9))
        self.assertEqual(tuple(context2.shape), tuple(q2.shape))

    @unittest.skipUnless(analyze.available("torch"), "Torch is required for MPS fallback tests.")
    def test_beat_this_retries_on_cpu_after_mps_out_of_memory(self):
        import torch

        class FakeBeatThis:
            def __init__(self, checkpoint_path, device, dbn):
                self.device = device

            def __call__(self, _path):
                if self.device == "mps":
                    raise RuntimeError("MPS backend out of memory")
                return [0.1, 0.6, 1.1, 1.6], [0.1, 2.1, 4.1, 6.1]

        paths = analyze.Paths(Path(self.tmp.name) / "beat-cache")
        with patch("analyze.torch_device", return_value="mps"), \
                patch("beat_this.inference.File2Beats", side_effect=FakeBeatThis), \
                patch.object(torch.mps, "empty_cache") as empty_cache:
            record = analyze._run_beats(Path(self.tmp.name) / "input.wav", paths)
        self.assertEqual(record["device"], "cpu")
        self.assertEqual(len(record["downbeats"]), 4)
        empty_cache.assert_called_once()

    @unittest.skipUnless(analyze.available("madmom") and analyze.available("natten"),
                         "madmom and NATTEN are required for decoder compatibility tests.")
    def test_allin1_madmom_decoder_handles_variable_length_paths(self):
        analyze._install_allin1_compat()
        from madmom.features.downbeats import DBNDownBeatTrackingProcessor

        activations = np.full((800, 2), 0.001)
        for frame in range(0, 800, 50):
            if frame % 200 == 0:
                activations[frame:frame + 3] = [0.05, 0.9]
            else:
                activations[frame:frame + 3] = [0.9, 0.05]
        common = dict(fps=100, min_bpm=100, max_bpm=140, num_tempi=5)
        processor = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], **common)
        actual = processor(activations)
        self.assertEqual(actual.shape[1], 2)
        self.assertGreater(len(actual), 8)
        self.assertTrue(np.all(np.isfinite(actual)))
        self.assertTrue(np.all(np.diff(actual[:, 0]) > 0))
        self.assertEqual(set(actual[:, 1]), {1, 2, 3, 4})
        expected = DBNDownBeatTrackingProcessor(beats_per_bar=[4], **common)(activations)
        np.testing.assert_array_equal(actual, expected)


if __name__ == "__main__":
    unittest.main()
