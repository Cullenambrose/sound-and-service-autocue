"""Freeze the existing musical output, including the legacy name on empty pad C."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v61_pop_cues import plan_for, synthetic_track


class CueBaselineTests(unittest.TestCase):
    def test_cf38ec3_synthetic_pop_baseline(self):
        path = Path(__file__).resolve().parents[1] / "tests/baselines/cf38ec3-pop.json"
        baseline = json.loads(path.read_text())
        plan = plan_for(synthetic_track(**baseline["recipe"]))
        actual = {
            "grid": plan["grid"], "cue_spec": plan["cue_spec"],
            "cues": {slot: {key: cue.get(key) for key in ("name", "bar", "time_s", "color", "unsnapped")}
                     for slot, cue in plan["cues"].items()},
        }
        self.assertEqual(actual, baseline["expected"])
