import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import cue_maps
import serato_writer
import experiments
from score_v6 import labels_exact
from scripts.audit_phrasing import audit_library


def mapped_plan(start=8, exit_bar=32, out=80, settings=None):
    plan = {"map_type": "VOCAL", "bar_count": 100, "mix_end_bar": 100,
            "grid": {"t1": 0.017, "bar_s": 2.0},
            "cues": {slot: {} for slot in "ABCDEFGH"}}
    return cue_maps.apply_cue_map(plan, settings, {
        "start": {"bar": start}, "first_chorus_end": {"bar": exit_bar}, "out": {"bar": out},
    }, 0)


class PhrasingTests(unittest.TestCase):
    def test_explicit_units_and_fallbacks_measure_the_actual_target(self):
        for start, lead in ((8, 8), (7, 4), (3, 2), (1, 1)):
            with self.subTest(start=start):
                plan = mapped_plan(start=start, exit_bar=start + lead)
                self.assertEqual(plan["cues"]["A"]["name"], f"Intro {lead} bars ({lead * 4} beats)")
                cue = plan["cues"]["D"]
                self.assertEqual(cue["bar"], start)
                self.assertEqual(cue["name"], f"GO {lead} bars ({lead * 4} beats) > EXIT")
                report = cue_maps.phrasing_report(plan)
                self.assertFalse(report["errors"])
                interval = next(row for row in report["intervals"] if row["slot"] == "D")
                self.assertEqual(interval["beats"], lead * 4)

    def test_go_out_can_shorten_but_never_exceeds_requested_lead(self):
        for out, lead in ((8, 8), (7, 4), (3, 2), (1, 1)):
            plan = mapped_plan(start=0, exit_bar=None, out=out)
            self.assertEqual(plan["cues"]["G"]["bar"], out - lead)
            self.assertEqual(plan["cues"]["G"]["name"], f"GO {lead} bars ({lead * 4} beats) > OUT")

    def test_old_pop_templates_migrate_but_custom_names_survive(self):
        settings = {"maps": {"VOCAL": {
            "A": {"label": "Intro - {bars}BTS"}, "D": {"label": "GO-8Exit"},
            "E": {"label": "My turnaround"}, "G": {"label": "GO-8OUT"},
        }}}
        plan = mapped_plan(settings=settings)
        self.assertEqual(plan["cues"]["A"]["name"], "Intro 8 bars (32 beats)")
        self.assertEqual(plan["cues"]["E"]["name"], "My turnaround")
        self.assertFalse(cue_maps.phrasing_report(plan)["errors"])

    def test_corrupt_name_cannot_validate_against_a_matching_resolved_label(self):
        plan = mapped_plan()
        plan["cues"]["D"]["name"] = "GO 32 bars (128 beats) > EXIT"
        plan["cue_map"]["resolved"]["D"]["label"] = plan["cues"]["D"]["name"]
        with self.assertRaisesRegex(ValueError, "label"):
            cue_maps.validate_automatic_plan(plan)
        with self.assertRaisesRegex(ValueError, "label"):
            serato_writer.plan_pads(plan)
        self.assertFalse(labels_exact(plan))

    def test_manual_anchor_change_relabels_actual_distances_without_moving_cues(self):
        plan = mapped_plan()
        before = copy.deepcopy(plan["cues"])
        plan["cues"]["F"].update(bar=36, time_s=72.017)
        cue_maps.refresh_dynamic_labels(plan)
        self.assertEqual(plan["cues"]["D"]["bar"], before["D"]["bar"])
        self.assertEqual(plan["cues"]["D"]["name"], "GO 12 bars (48 beats) > EXIT")
        self.assertEqual(plan["cues"]["E"]["name"], "Mix 8 bars (32 beats) > EXIT")
        cue_maps.validate_phrasing(plan)

    def test_refresh_does_not_turn_an_outro_mix_window_into_a_zero_length_countdown(self):
        plan = {"map_type": "VOCAL", "bar_count": 100, "mix_end_bar": 100,
                "grid": {"t1": 0.017, "bar_s": 2.0}, "cues": {slot: {} for slot in "ABCDEFGH"}}
        cue_maps.apply_cue_map(plan, {"maps": {"VOCAL": cue_maps.legacy_v7_vocal_map()}},
                               {"start": {"bar": 8}, "first_chorus_end": {"bar": 40},
                                "outro_start": {"bar": 80}}, 0)
        cue_maps.refresh_dynamic_labels(plan)
        self.assertEqual(plan["cues"]["H"]["name"], "EXIT-32")
        cue_maps.validate_phrasing(plan)
        plan["cues"]["H"]["name"] = "EXIT-64"
        plan["cue_map"]["resolved"]["H"]["label"] = "EXIT-64"
        with self.assertRaisesRegex(ValueError, "mix window"):
            cue_maps.validate_phrasing(plan)

    def test_missing_target_and_timestamp_mismatch_are_blocked(self):
        plan = mapped_plan()
        plan["cues"]["F"]["bar"] = None
        with self.assertRaisesRegex(ValueError, "target is empty"):
            cue_maps.validate_phrasing(plan)
        plan = mapped_plan()
        plan["cues"]["D"]["time_s"] += 1
        with self.assertRaisesRegex(ValueError, "timestamp"):
            serato_writer.plan_pads(plan)

    def test_pickup_warning_distinguishes_structural_bars_from_actual_vocal_time(self):
        plan = mapped_plan()
        plan["cues"]["B"].update(unsnapped=True, time_s=15.094)
        report = cue_maps.phrasing_report(plan)
        self.assertFalse(report["errors"])
        self.assertTrue(any("structural anchor" in warning for warning in report["warnings"]))
        self.assertEqual(serato_writer.plan_pads(plan)[1]["position_ms"], 15094)

    def test_database_audit_reports_legacy_32_bar_go_without_modifying_database(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / "library.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE tracks(hash TEXT,name TEXT,plan_json TEXT)")
                plan = {"grid": {"t1": 0, "bar_s": 2},
                        "cues": {"G": {"bar": 48, "name": "GO -32"},
                                 "H": {"bar": 80, "name": "OUTRO"}}}
                db.execute("INSERT INTO tracks VALUES(?,?,?)", ("test", "synthetic", json.dumps(plan)))
            before = path.read_bytes()
            report = audit_library(path)[0]
            self.assertEqual(report["status"], "invalid")
            self.assertTrue(any("ambiguous units" in error for error in report["errors"]))
            self.assertEqual(report["intervals"][0]["beats"], 128)
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "legacy GO"):
                serato_writer.plan_pads(plan)

    def test_scoring_review_and_partial_merge_keep_vocal_pickup_timestamp(self):
        plan = mapped_plan()
        plan["cues"]["B"].update(unsnapped=True, time_s=15.094)
        answer = experiments.review_answer(plan, ["B"])
        self.assertEqual(experiments._truth_time(answer["cues"]["B"], answer["grid"]), 15.094)
        fresh = mapped_plan()
        fresh["grid"]["t1"] += 0.1
        merged = experiments.merge_review_answer(fresh, answer, ["A"])
        self.assertEqual(experiments._truth_time(merged["cues"]["B"], merged["grid"]), 15.094)
