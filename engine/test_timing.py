import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from timing import bar_to_time, grid_origin, time_to_bar
from rules_v6 import _bar_mean, _section_bars, build_plan


class TimingTests(unittest.TestCase):
    def test_cross_language_conversion_cases_round_trip_without_truncating_bars(self):
        cases = json.loads(Path(__file__).with_name("timing_cases.json").read_text())
        for case in cases:
            with self.subTest(case=case):
                self.assertAlmostEqual(bar_to_time(case["bar"], case["grid"]), case["time_s"], places=9)
                self.assertAlmostEqual(time_to_bar(case["time_s"], case["grid"]), case["bar"], places=9)

    def test_invalid_grids_and_positions_are_rejected(self):
        for grid in ({}, {"t1": None, "bar_s": 2}, {"t1": 0, "bar_s": 0},
                     {"t1": 0, "bar_s": -2}, {"t1": float("inf"), "bar_s": 2},
                     {"t1": 0, "bar_s": 2, "offset_ms": float("nan")},
                     {"t1": 0, "bar_s": 2, "offset_ms": None}):
            with self.subTest(grid=grid), self.assertRaises(ValueError):
                grid_origin(grid)
        for value in (None, "", True, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    bar_to_time(value, {"t1": 0, "bar_s": 2})
                with self.assertRaises(ValueError):
                    time_to_bar(value, {"t1": 0, "bar_s": 2})

    def test_feature_windows_and_section_conversion_use_offset(self):
        grid = {"t1": 1, "bar_s": 2, "offset_ms": 1000}
        self.assertEqual(_bar_mean([1, 1, 8, 8, 3, 3], 1, grid, 1), 8)
        self.assertEqual(_section_bars({"start": 16, "end": 32}, grid), (8, 16))

    def test_plan_and_exact_end_use_same_offset_aware_grid(self):
        grid = {"t1": 2, "bar_s": 2, "offset_ms": 100}
        duration = 100.25
        plan = build_plan("offset", {"grid": grid, "segments": [
            {"label": "intro", "start": 1.9, "end": 17.9}]}, {"cues": {}}, duration,
            stems={"vocals": [0.2] * 2005})
        self.assertEqual(plan["cues"]["B"]["bar"], 8)
        self.assertAlmostEqual(plan["cues"]["B"]["time_s"], 17.9)
        self.assertAlmostEqual(plan["mix_end_bar"], 49.175)
        self.assertEqual(plan["cues"]["H"]["name"], "EXIT-END")
        self.assertEqual(plan["cues"]["H"]["time_s"], duration)
        self.assertEqual(plan["cues"]["G"]["time_s"], duration - 16)
        for cue in plan["cues"].values():
            if cue["bar"] is not None:
                self.assertLessEqual(abs(cue["time_s"] - bar_to_time(cue["bar"], grid)), 0.0005)


if __name__ == "__main__":
    unittest.main()
