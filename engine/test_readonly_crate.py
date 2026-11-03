import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from scripts import verify_crate_dry_run as qa
from web import fixtures


def plan(start_ms=23221, exit_bar=40, out_bar=81):
    grid = {"source": "serato_beatgrid", "bpm": 84.0, "bar_s": 240 / 84, "t1": 0.045958}
    bars = {"A": 0, "B": 8, "C": None, "D": exit_bar - 8, "E": exit_bar - 4,
            "F": exit_bar, "G": out_bar - 8, "H": out_bar}
    cues = {slot: {"bar": bar, "time_s": None if bar is None else qa.bar_to_time(bar, grid)}
            for slot, bar in bars.items()}
    cues["B"].update(time_s=start_ms / 1000, unsnapped=True)
    return {"grid": grid, "cues": cues}


class ReadonlyCrateTests(unittest.TestCase):
    def test_command_analyzes_only_copies_and_verifies_the_entire_music_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            library = home / "Music" / "Master Music"
            library.mkdir(parents=True)
            names = ("Van Morrison - Into The Mystic (Re-Drum) - 5B - 84.mp3",
                     "06 You Make My Dreams - 7B - 84.m4a",
                     "Mambo No.5 (MMP Intro).mp3", "Chic - Le Freak (Clean).mp3")
            for name in names:
                (library / name).write_bytes(fixtures.m4a() if name.endswith(".m4a") else fixtures.mp3())
            (home / "Music" / "unrelated.txt").write_text("do not modify")
            original = qa.snapshot(home.joinpath("Music").rglob("*"))
            seen = []
            cache = home / "cache"

            def analysis(source, **kwargs):
                seen.append(source)
                self.assertNotIn(home / "Music", source.parents)
                self.assertEqual(source.read_bytes(), (library / source.name).read_bytes())
                self.assertEqual(kwargs, {"cache_dir": cache, "gemini": False})
                return plan(22743, 30, 80) if "Mambo" in source.name else plan()

            output = io.StringIO()
            with patch.object(qa.Path, "home", return_value=home), \
                    patch.object(qa.analyze, "analyze", side_effect=analysis), redirect_stdout(output):
                result = qa.main(["--cache-dir", str(cache), "--verify-music-tree"])
            self.assertEqual(result, 0)
            self.assertEqual(len(seen), 4)
            self.assertEqual(qa.snapshot(home.joinpath("Music").rglob("*")), original)
            self.assertEqual(output.getvalue().count("8 H"), 4)
            self.assertIn("MP4 tag keys:", output.getvalue())
            self.assertIn("SHA-256 unchanged: 5 files", output.getvalue())
            self.assertTrue(all(not source.exists() for source in seen))

    def test_regression_checks_fail_without_mutating_plan(self):
        cases = ((plan(23222), {"start_ms": 23221}),
                 (plan(exit_bar=41), {"exit_bar": 40}),
                 (plan(out_bar=82), {"out_bar": 81}),
                 ({**plan(), "grid": {"source": "detected", "t1": 0, "bpm": 168, "bar_s": 240 / 168}},
                  {"serato_bpm": 84}))
        for record, expected in cases:
            with self.subTest(expected=expected), redirect_stdout(io.StringIO()):
                before = copy.deepcopy(record)
                with self.assertRaises(ValueError):
                    qa.report_plan(record, expected)
                self.assertEqual(record, before)

    def test_music_tree_changes_are_detected_even_on_analysis_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            library = home / "Music" / "Master Music"
            library.mkdir(parents=True)
            for pattern, _ in qa.TRACKS:
                name = pattern.replace("*", "")
                (library / name).write_bytes(fixtures.m4a() if name.endswith(".m4a") else fixtures.mp3())
            unrelated = home / "Music" / "unrelated.txt"
            unrelated.write_text("original")

            def failure(*_args, **_kwargs):
                unrelated.write_text("changed by a simulated external process")
                raise ValueError("simulated analysis failure")

            errors = io.StringIO()
            with patch.object(qa.Path, "home", return_value=home), \
                    patch.object(qa.analyze, "analyze", side_effect=failure), \
                    redirect_stdout(io.StringIO()), redirect_stderr(errors):
                with self.assertRaises(SystemExit) as result:
                    qa.main(["--cache-dir", str(home / "cache"), "--verify-music-tree"])
            self.assertEqual(result.exception.code, 2)
            self.assertIn("Music SHA-256 verification FAILED", errors.getvalue())

    def test_missing_tracks_and_music_cache_fail_before_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            (home / "Music" / "Master Music").mkdir(parents=True)
            errors = io.StringIO()
            with patch.object(qa.Path, "home", return_value=home), patch.object(qa.analyze, "analyze") as analysis, \
                    redirect_stderr(errors):
                with self.assertRaises(SystemExit) as result:
                    qa.main(["--cache-dir", str(home / "Music" / "cache")])
                self.assertEqual(result.exception.code, 2)
                with self.assertRaises(SystemExit) as result:
                    qa.main(["--cache-dir", str(home / "cache")])
                self.assertEqual(result.exception.code, 2)
                analysis.assert_not_called()
            self.assertIn("outside ~/Music", errors.getvalue())
            self.assertIn("found 0", errors.getvalue())
