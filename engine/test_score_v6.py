import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_v6 import ReviewMark, library_report_markdown, load_marks, score
from experiments import score_tracks


class ScoreTimingTests(unittest.TestCase):
    def test_fractional_bar_marks_are_not_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = {"cues": {"A": {"bar": 1.4}}}
            Path(directory, "01.json").write_text(json.dumps(plan))
            result = score(directory, {("01", "A"): 5.6}, check_labels=False)
        self.assertEqual(result["A"]["v6_beat"], 1)

    def test_explicit_review_time_and_saved_grid_are_retained(self):
        events = [
            {"track": "1", "cue": "A", "action": "set_here", "to_beat": 41,
             "to_time": 19.7, "t1": 0, "bar_s": 2, "offset_ms": 800},
            {"track": "1", "cue": "B", "action": "correct", "to_beat": 41,
             "t1": 0, "bar_s": 2, "offset_ms": 800},
            {"track": "1", "cue": "C", "action": "correct", "to_beat": 41},
            {"track": "1", "cue": "D", "action": "correct", "to_beat": 20, "saved_at": "1"},
            {"track": "1", "cue": "D", "action": "clear", "saved_at": "2"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "marks.json")
            path.write_text(json.dumps(events))
            marks = load_marks(path)
        self.assertEqual(marks[("01", "A")], ReviewMark(41, 19.7, 0.5))
        self.assertEqual(marks[("01", "B")], ReviewMark(41, 19.7, 0.5))
        self.assertEqual(marks[("01", "C")], 41)
        self.assertNotIn(("01", "D"), marks)

    def test_scoring_compares_physical_times_not_bars_across_different_grids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            new, old = root / "new", root / "old"
            new.mkdir()
            old.mkdir()
            # Same bar numbers, but only the old grid is within a beat of the reviewed audio time.
            new.joinpath("01.json").write_text(json.dumps({
                "grid": {"t1": 0, "bar_s": 4}, "cues": {"A": {"bar": 10.25}}}))
            old.joinpath("01.json").write_text(json.dumps({
                "grid": {"t1": 0, "bar_s": 2, "offset_ms": 800}, "cues": {"A": {"bar": 10.25}}}))
            result = score(new, {("01", "A"): ReviewMark(41, 19.7, 0.5)}, old, check_labels=False)
        self.assertEqual(result["A"]["v6_beat"], 0)
        self.assertEqual(result["A"]["v5_beat"], 1)

    def test_legacy_beat_marks_use_current_grid_as_reference_when_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            new, old = root / "new", root / "old"
            new.mkdir()
            old.mkdir()
            new.joinpath("01.json").write_text(json.dumps({
                "grid": {"t1": 0, "bar_s": 2, "offset_ms": 800}, "cues": {"A": {"bar": 10.25}}}))
            old.joinpath("01.json").write_text(json.dumps({
                "grid": {"t1": 0, "bar_s": 2}, "cues": {"A": {"bar": 10.25}}}))
            result = score(new, {("01", "A"): 41}, old, check_labels=False)
        self.assertEqual(result["A"]["v6_beat"], 1)
        self.assertEqual(result["A"]["v5_beat"], 0)

    def test_current_library_export_cli_scores_reviewed_hashes_without_legacy_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = root / "library.json"
            output = root / "score.md"
            exported.write_text(json.dumps({"schema_version": 2, "tracks": [{
                "hash": "a" * 64, "name": "reviewed | song.mp3", "reviewed": True,
                "plan": {"grid": {"t1": 0, "bar_s": 2, "offset_ms": 800},
                         "cues": {"A": {"bar": 10.25}, "B": {"bar": None}},
                         "variants": {"baseline": {"status": "ok", "label": "Rules only",
                             "cues": {"A": {"bar": 10.25, "time_s": 20.5}, "B": {"bar": None}}}}}
            }]}))
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("score_v6.py")),
                 "--library-export", str(exported), "--report", str(output)],
                capture_output=True, text=True, check=True)
            report = output.read_text()
            self.assertEqual(report, completed.stdout)
            self.assertIn("Rules only | 0/1 | 1/1", report)
            self.assertIn("reviewed \\| song.mp3 | A | baseline | timing error", report)
            self.assertIn("0.8", report)

    def test_no_ground_truth_produces_no_accuracy_estimate(self):
        report = score_tracks([{"name": "not reviewed", "plan": {"grid": {"t1": 0, "bar_s": 2}}}])
        text = library_report_markdown(report)
        self.assertIn("No accuracy estimate", text)
        self.assertIn("no human-reviewed answer key", text)


if __name__ == "__main__":
    unittest.main()
