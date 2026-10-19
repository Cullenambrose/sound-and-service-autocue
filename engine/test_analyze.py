"""Pipeline tests with synthetic audio. Analyzers that are not installed are replaced by fixtures,
so this checks the orchestration, cache layout, fallbacks and confidence capping, not model accuracy."""
import json
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
