import concurrent.futures
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

import waveform


class WaveformTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        self.samples = np.zeros(waveform.RATE * 8 + 37, dtype="<i2")
        self.samples[19], self.samples[-1] = -24000, 30000
        with wave.open(str(self.source), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(waveform.RATE)
            stream.writeframes(self.samples.tobytes())
        self.cache = self.root / "cache"

    def tearDown(self):
        self.temporary.cleanup()

    def decode(self, args, **kwargs):
        shutil.copyfile(self.source, args[-1])
        return subprocess.CompletedProcess(args, 0)

    def test_pyramid_keeps_duration_extrema_and_fractional_tail(self):
        with patch.object(waveform.subprocess, "run", side_effect=self.decode) as decode:
            full = waveform.query(self.source, self.cache, points=64)
            detail = waveform.query(self.source, self.cache, start=7.9, points=4096)
        self.assertEqual(decode.call_count, 1)
        self.assertFalse(full["cached"])
        self.assertTrue(detail["cached"])
        self.assertAlmostEqual(full["duration"], len(self.samples) / waveform.RATE)
        self.assertLessEqual(len(full["columns"]), 65)
        self.assertAlmostEqual(min(row[0] for row in full["columns"]), -24000 / 32768, places=4)
        self.assertAlmostEqual(max(row[1] for row in full["columns"]), 30000 / 32768, places=4)
        self.assertAlmostEqual(max(row[1] for row in detail["columns"]), 30000 / 32768, places=4)
        self.assertLess(detail["level"], full["level"])
        with np.load(self.cache / f"waveform-v{waveform.VERSION}.npz") as archive:
            for key in archive.files:
                if key.startswith("level_"):
                    level = archive[key]
                    self.assertEqual(level[:, 5].sum(), len(self.samples))
                    np.testing.assert_allclose((level[:, 2:5] * level[:, 5:6]).sum(0),
                                               (archive["level_0"][:, 2:5] * archive["level_0"][:, 5:6]).sum(0),
                                               rtol=1e-5)
        self.assertEqual(sorted(path.name for path in self.cache.iterdir()),
                         ["waveform-v1.npz", "waveform.lock"])

    def test_cache_reopen_never_invokes_decoder_and_has_bounded_detail_payload(self):
        with patch.object(waveform.subprocess, "run", side_effect=self.decode):
            waveform.query(self.source, self.cache)
        with patch.object(waveform.subprocess, "run", side_effect=AssertionError("unexpected decode")):
            data = waveform.query(self.source, self.cache, start=1.125, end=3.625, points=256)
        self.assertLessEqual(len(data["columns"]), 258)
        self.assertLessEqual(data["start"], 1.125)
        self.assertGreaterEqual(data["start"] + len(data["columns"]) * data["hop_s"], 3.625)
        self.assertTrue(np.isfinite(data["columns"]).all())

    def test_concurrent_requests_build_only_once(self):
        with patch.object(waveform.subprocess, "run", side_effect=self.decode) as decode:
            with concurrent.futures.ThreadPoolExecutor(2) as executor:
                results = list(executor.map(lambda _: waveform.query(self.source, self.cache), range(2)))
        self.assertEqual(decode.call_count, 1)
        self.assertEqual(sorted(result["cached"] for result in results), [False, True])

    def test_invalid_ranges_and_points_do_not_decode(self):
        with patch.object(waveform.subprocess, "run", side_effect=AssertionError("unexpected decode")):
            for values in ({"start": float("nan")}, {"start": -1}, {"end": float("inf")},
                           {"start": 3, "end": 2}, {"points": 4097}, {"points": 0},
                           {"points": 100.5}):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    waveform.query(self.source, self.cache, **values)

    def test_decoder_errors_are_explicit_and_do_not_leave_partial_cache(self):
        for error, expected in ((FileNotFoundError(), "requires ffmpeg"),
                                (subprocess.TimeoutExpired("ffmpeg", 300), "timeout"),
                                (subprocess.CalledProcessError(1, "ffmpeg"), "could not be decoded")):
            with self.subTest(error=error), patch.object(waveform.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex((ValueError, RuntimeError), expected):
                    waveform.query(self.source, self.cache)
            self.assertFalse((self.cache / "waveform-v1.npz").exists())
            self.assertEqual([path.name for path in self.cache.iterdir()], ["waveform.lock"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for decoder integration")
    def test_real_ffmpeg_build_and_read_only_source(self):
        before = self.source.read_bytes()
        result = waveform.query(self.source, self.cache)
        self.assertAlmostEqual(result["duration"], len(self.samples) / waveform.RATE)
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
