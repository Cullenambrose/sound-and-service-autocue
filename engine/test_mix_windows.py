import math
import unittest

import pop_cues
import cue_maps
from rules_v6 import build_plan


class MixWindowTests(unittest.TestCase):
    def setUp(self):
        self.grid = {"t1": 0.0, "bar_s": 2.0, "bpm": 120.0}
        self.hop = 0.05
        self.duration = 160.0
        self.vocals = [0.0] * 3200
        self.drums = [0.3] * 3200

    def evidence(self, start=8, **kwargs):
        return pop_cues.intro_loop_evidence(
            self.grid, 0, start, self.duration,
            kwargs.pop("stems", {"vocals": self.vocals, "drums": self.drums}),
            self.hop, out_bar=80, outro_first_bar=64, **kwargs)

    def activate(self, begin, end, values=None):
        values = self.vocals if values is None else values
        values[round(begin / self.hop):round(end / self.hop)] = [0.3] * round((end - begin) / self.hop)

    def test_prefers_eight_bars_and_preserves_audition_status_not_a_clean_guarantee(self):
        result = self.evidence()
        self.assertEqual(result["intro"]["selected"]["start_bar"], 0)
        self.assertEqual(result["intro"]["status"], "candidate")
        self.assertEqual(result["intro_loop_fallback"]["status"], "not_needed")
        self.assertIn("loop seam", result["intro"]["reason"])

    def test_spoken_tag_before_song_can_reduce_window_to_four_two_or_one_bar(self):
        for last_s, lead in ((7.0, 4), (11.0, 2), (13.0, 1)):
            self.vocals = [0.0] * 3200
            self.activate(0, last_s)
            result = self.evidence()
            self.assertEqual(result["intro"]["selected"]["start_bar"], 8 - lead)
            self.assertEqual(result["intro"]["selected"]["end_bar"], 8)

    def test_pickup_overlap_in_every_lead_proposes_outro_loop_and_first_downbeat_start(self):
        self.activate(15.1, 15.9)
        result = self.evidence()
        self.assertEqual(result["intro"]["status"], "unavailable")
        fallback = result["intro_loop_fallback"]
        self.assertEqual(fallback["status"], "needs_serato_fixture")
        self.assertEqual((fallback["target_slot"], fallback["start_slot"]), ("A", "B"))
        self.assertEqual(fallback["proposed_start_bar"], 0)
        self.assertEqual((fallback["loop"]["start_bar"], fallback["loop"]["end_bar"]), (64, 72))
        self.assertEqual(fallback["loop"]["beats"], 32)
        self.assertFalse(fallback["applied"])

    def test_tiny_late_vocal_return_cannot_be_diluted_by_a_window_average(self):
        self.activate(15.1, 15.9)
        self.activate(130.0, 130.05)
        result = self.evidence()["intro_loop_fallback"]
        self.assertGreater(result["loop"]["start_bar"], 65)
        self.assertEqual(result["loop"]["active_vocal_frames"], 0)

    def test_missing_truncated_invalid_or_nonfinite_stems_never_claim_clean_window(self):
        for bad in (None, [], [0.0] * 3199, [math.nan] * 3200, [-0.1] * 3200, [True] * 3200):
            for key in ("vocals", "drums"):
                with self.subTest(key=key, bad_type=type(bad)):
                    stems = {"vocals": self.vocals, "drums": self.drums, key: bad}
                    result = self.evidence(stems=stems)
                    self.assertEqual(result["intro"]["status"], "unverified")
                    self.assertNotIn("loop", result["intro_loop_fallback"])
        for hop in (True, 0, -0.05, math.inf, math.nan):
            self.assertEqual(pop_cues.envelope_profile(self.vocals, hop, self.duration)["status"], "unverified")

    def test_missing_drums_or_a_silent_bar_is_not_a_drum_loop(self):
        self.activate(0, 16)
        self.drums = [0.0] * 3200
        result = self.evidence()
        self.assertEqual(result["intro_loop_fallback"]["status"], "unavailable")
        self.assertNotIn("loop", result["intro_loop_fallback"])

    def test_no_outro_window_is_not_replaced_with_an_arbitrary_earlier_loop(self):
        self.activate(0, 16)
        self.activate(128, 160)
        result = self.evidence()
        self.assertEqual(result["intro_loop_fallback"]["status"], "unavailable")

    def test_loop_length_falls_back_to_four_two_one_bars_at_track_end(self):
        self.activate(0, 160)
        for bars in (4, 2, 1):
            with self.subTest(bars=bars):
                begin = round((160 - bars * 2) / self.hop)
                self.vocals = [0.3] * begin + [0.0] * (3200 - begin)
                loop = self.evidence()["intro_loop_fallback"]["loop"]
                self.assertEqual(loop["bars"], bars)
                self.assertEqual(loop["end_bar"], 80)

    def test_boundary_activity_at_window_end_does_not_count_but_inside_does(self):
        self.activate(16, 16.05)
        self.assertEqual(self.evidence()["intro"]["status"], "candidate")
        self.activate(15.95, 16.0)
        self.assertEqual(self.evidence()["intro"]["status"], "unavailable")

    def test_out_requires_full_vocal_and_drum_evidence_and_catches_brief_vocals(self):
        energy = [0.5] * 8000
        for vocals, drums, expected in (
            (None, self.drums, "unverified_end"),
            (self.vocals[:100], self.drums, "unverified_end"),
            (self.vocals, None, "unverified_end"),
            (self.vocals, [0.0] * 3200, "unverified_end"),
            (self.vocals, self.drums, "drum_outro"),
        ):
            result = pop_cues.detect_out(energy, 50, self.grid, 8, self.duration,
                                         vocals, self.hop, drums)
            self.assertEqual(result["kind"], expected)
        self.activate(158, 158.05)
        result = pop_cues.detect_out(energy, 50, self.grid, 8, self.duration,
                                     self.vocals, self.hop, self.drums)
        self.assertEqual(result["kind"], "hard_end")

    def test_flat_energy_does_not_confirm_a_chorus_ending(self):
        bar, evidence = pop_cues.refine_chorus_exit(
            32, self.grid, 8, 80, vocals=[0.3] * 3200, hop_s=self.hop, energy=[0.5] * 8000)
        self.assertEqual(bar, 32)
        self.assertEqual(evidence["status"], "unverified")
        self.assertIn("without a clear", evidence["reason"])

    def test_planner_saves_fallback_proposal_without_moving_start_or_masquerading_as_loop(self):
        self.activate(15.1, 15.9)
        plan = build_plan("synthetic", {"grid": self.grid, "segments": [
            {"label": "intro", "start": 0, "end": 16},
            {"label": "chorus", "start": 48, "end": 64},
            {"label": "outro", "start": 128, "end": 160},
        ]}, {"cues": {"B": {"bar": 8}}}, self.duration, genre="pop",
            stems={"vocals": self.vocals, "drums": self.drums},
            energy=[0.5] * 8000, hop_s=self.hop)
        fallback = plan["mix_window_evidence"]["intro_loop_fallback"]
        self.assertEqual(fallback["status"], "needs_serato_fixture")
        self.assertFalse(fallback["applied"])
        self.assertEqual(plan["cues"]["B"]["bar"], 8)
        self.assertEqual(plan["cues"]["A"]["mix_window_evidence"]["status"], "vocal_overlap")
        self.assertLessEqual(plan["cues"]["A"]["confidence"], 0.35)
        self.assertTrue(any("Intro is structural only" in flag for flag in plan["flags"]))
        mapping = plan["cue_map"]
        cue_maps.apply_cue_map(plan, {"maps": {"VOCAL": mapping["settings"]}},
                               mapping["candidates"], 0)
        self.assertNotIn("mix_window_evidence", plan)
        self.assertEqual(plan["cues"]["A"]["mix_window_evidence"]["status"], "unverified")
