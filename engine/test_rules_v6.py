import sys
import unittest
import json
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cue_maps
import rules_v6
from rules_v6 import build_plan, detect_drops, detect_drops_with_fallback
from gemini_v6 import apply as apply_gemini
from score_v6 import label_valid, labels_exact, score
from synced_lyrics import align_synced_lyrics, parse_synced_lyrics

_SAVED = {}


def setUpModule():
    # These regression tests pin the pre-v6.1 VOCAL layout; v6.1 pop cues are covered in test_v61_pop_cues.
    _SAVED["vocal"] = cue_maps.DEFAULT_CUE_MAPS["maps"]["VOCAL"]
    _SAVED["pop_v61"] = rules_v6.DEFAULT_CONFIG["switches"]["pop_v61"]
    cue_maps.DEFAULT_CUE_MAPS["maps"]["VOCAL"] = cue_maps.legacy_v7_vocal_map()
    rules_v6.DEFAULT_CONFIG["switches"]["pop_v61"] = False


def tearDownModule():
    cue_maps.DEFAULT_CUE_MAPS["maps"]["VOCAL"] = _SAVED["vocal"]
    rules_v6.DEFAULT_CONFIG["switches"]["pop_v61"] = _SAVED["pop_v61"]


class V6RulesTests(unittest.TestCase):
    def setUp(self):
        self.grid = {"t1": 0.0, "bar_s": 2.0, "offset_ms": 0, "bpm": 120}
        self.analysis = {
            "grid": self.grid,
            "segments": [
                {"label": "intro", "start": 0, "end": 16},
                {"label": "verse", "start": 16, "end": 32},
                {"label": "chorus", "start": 32, "end": 40},
                {"label": "verse", "start": 40, "end": 56},
                {"label": "chorus", "start": 56, "end": 64},
                {"label": "outro", "start": 64, "end": 88},
            ],
        }
        self.plan = {"cues": {
            "B": {"bar": 16, "confidence": 0.82},
            "H": {"bar": 64, "confidence": 0.72},
        }}
        self.config = {
            "switches": {"synced_lyrics": True, "drop_detection": True, "genre_map": True,
                         "gemini_tiebreaker": False},
            "drop_genres": ["house", "edm"], "drop_bpm_range": [118, 150],
            "drop_min_confidence": 0.65, "drop_phrase_bars": 8, "drop_recovery_ratio": 1.8,
            "gemini_confidence_threshold": 0.6, "gemini_spend_cap_usd": 3,
        }

    def test_vocal_map_exact_slots_and_label_format(self):
        result = build_plan("01", self.analysis, self.plan, 176, config=self.config)
        self.assertEqual(result["map_type"], "VOCAL")
        self.assertEqual(result["cue_order"], list("ABCDEFGH"))
        self.assertEqual(list(result["cues"]), list("ABCDEFGH"))
        self.assertEqual(result["cues"]["A"]["name"], "INTRO-32")
        self.assertEqual(result["cues"]["C"]["name"], "EXIT -32")
        self.assertEqual(result["cues"]["D"]["name"], "EXIT -16")
        self.assertEqual(result["cues"]["E"]["bar"], 32)
        self.assertEqual(result["cues"]["D"]["bar"], 16)
        self.assertIsNone(result["cues"]["C"]["bar"])
        self.assertEqual(result["cues"]["F"]["bar"], 28)
        self.assertTrue(all(label_valid("VOCAL", slot, result["cues"][slot]["name"]) for slot in "ABCDEFGH"))
        self.assertTrue(labels_exact(result))

    def test_exit_offsets_are_exact_and_near_start_is_flagged(self):
        analysis = {
            "grid": self.grid,
            "segments": [
                {"label": "intro", "start": 0, "end": 32},
                {"label": "verse", "start": 32, "end": 64},
                {"label": "chorus", "start": 64, "end": 128},
                {"label": "verse", "start": 128, "end": 160},
                {"label": "chorus", "start": 160, "end": 192},
                {"label": "outro", "start": 192, "end": 256},
            ],
        }
        plan = {"cues": {"B": {"bar": 16, "confidence": 0.8}}}
        result = build_plan("04", analysis, plan, 512, config=self.config)
        self.assertEqual(result["cues"]["E"]["bar"], 64)
        self.assertEqual(result["cues"]["C"]["bar"], 32)
        self.assertEqual(result["cues"]["D"]["bar"], 48)
        self.assertEqual(result["cues"]["C"]["name"], "EXIT -32")
        self.assertEqual(result["cues"]["D"]["name"], "EXIT -16")

        short = build_plan("05", self.analysis, self.plan, 176, config=self.config)
        self.assertEqual(short["cues"]["D"]["bar"], 16)
        self.assertIn("C lands before START", short["flags"])

    def test_gemini_only_adjusts_low_confidence_anchor_and_preserves_offsets(self):
        analysis = {
            "grid": self.grid,
            "segments": [
                {"label": "intro", "start": 0, "end": 32},
                {"label": "verse", "start": 32, "end": 64},
                {"label": "chorus", "start": 64, "end": 128},
                {"label": "verse", "start": 128, "end": 160},
                {"label": "chorus", "start": 160, "end": 192},
                {"label": "outro", "start": 192, "end": 256},
            ],
        }
        result = build_plan("06", analysis, self.plan, 512, config=self.config)
        original_exit = result["cues"]["E"]["bar"]
        picks = apply_gemini(result, {"E": {"pick": original_exit + 1, "why": "nearby candidate"}},
                             confidence_threshold=0.8)
        self.assertEqual(picks["E"]["bar"], original_exit + 1)
        self.assertEqual(result["cues"]["C"]["bar"], result["cues"]["E"]["bar"] - 32)
        self.assertEqual(result["cues"]["D"]["bar"], result["cues"]["E"]["bar"] - 16)
        self.assertTrue(labels_exact(result))
        unchanged = apply_gemini(result, {"B": {"pick": result["cues"]["B"]["bar"] + 1}}, 0.6)
        self.assertNotIn("B", unchanged)

    def test_drop_map_and_single_drop_repeats_slots(self):
        drops = [{"bar": 48, "time_s": 96, "confidence": 0.8, "reason": "bass return"}]
        result = build_plan("02", self.analysis, self.plan, 176, genre="House", config=self.config,
                            detected_drops=drops)
        self.assertEqual(result["map_type"], "DROP")
        self.assertEqual(result["genre_phrase_bars"], 32)
        self.assertEqual(result["cues"]["D"]["name"], "FIRST DROP")
        self.assertEqual(result["cues"]["F"]["name"], "LAST DROP")
        self.assertEqual(result["cues"]["D"]["bar"], result["cues"]["F"]["bar"])
        self.assertEqual(result["cues"]["C"]["bar"], result["cues"]["E"]["bar"])
        self.assertIn("single drop: last-drop slots repeat first-drop slots", result["flags"])
        self.assertTrue(labels_exact(result))

    def test_house_drop_detection_uses_drop_phrase_not_genre_phrase(self):
        config = {**self.config, "genres": {"house_edm": {"phrase_bars": 32}}}
        grid = {"t1": 0.0, "bar_s": 2.0, "bpm": 120}
        stems = {key: [] for key in ("drums", "bass")}
        low_energy = []
        for bar in range(129):
            level = 0.1 if 112 <= bar < 120 else 1.0
            for values in (*stems.values(), low_energy):
                values.extend([level] * 4)
        for values in (*stems.values(), low_energy):
            values[120 * 4:121 * 4] = [1.0] * 4
        result = build_plan("house-drop", {"grid": grid, "segments": []},
                            {"cues": {"B": {"bar": 8}}}, 258, genre="House",
                            stems=stems, low_energy=low_energy, hop_s=0.5, config=config)

        self.assertEqual(result["genre_phrase_bars"], 32)
        self.assertEqual([drop["bar"] for drop in result["drops"]], [120])
        self.assertEqual(result["map_type"], "DROP")
        self.assertEqual(result["cues"]["D"]["bar"], 120)

    def test_eight_bar_intro_uses_the_beginning_when_it_fits_exactly(self):
        plan = {"cues": {"B": {"bar": 8, "confidence": 0.7}}}
        result = build_plan("03", self.analysis, plan, 176, config=self.config)
        self.assertEqual(result["cues"]["A"]["name"], "INTRO-32")
        self.assertEqual(result["cues"]["A"]["bar"], 0)

    def test_custom_cue_map_controls_meaning_label_and_serato_color(self):
        import copy
        from cue_maps import DEFAULT_CUE_MAPS, PALETTE_BY_ID

        settings = copy.deepcopy(DEFAULT_CUE_MAPS)
        settings["maps"]["VOCAL"]["F"] = {
            "meaning": "last_chorus_start", "label": "FINAL CHORUS", "color": "red", "enabled": True,
        }
        result = build_plan("custom", self.analysis, self.plan, 176, config=self.config,
                            cue_maps=settings)

        self.assertEqual(result["cues"]["F"]["bar"], 28)
        self.assertEqual(result["cues"]["F"]["name"], "FINAL CHORUS")
        self.assertEqual(result["cues"]["F"]["meaning"], "last_chorus_start")
        self.assertEqual(result["cues"]["F"]["color"], PALETTE_BY_ID["red"]["metadata"])
        self.assertEqual(result["cues"]["F"]["display_color"], PALETTE_BY_ID["red"]["display"])

    def test_dynamic_labels_follow_custom_meanings_after_gemini_moves_an_anchor(self):
        import copy
        from cue_maps import DEFAULT_CUE_MAPS

        settings = copy.deepcopy(DEFAULT_CUE_MAPS)
        settings["maps"]["VOCAL"]["A"]["label"] = "WARMUP {bars}"
        settings["maps"]["VOCAL"]["F"]["label"] = "FINAL CHORUS"
        result = build_plan("dynamic-custom", self.analysis, self.plan, 176,
                            config=self.config, cue_maps=settings)
        self.assertEqual(result["cues"]["A"]["name"], "WARMUP 8")
        original_exit = result["cues"]["E"]["bar"]
        original_start = result["cues"]["B"]["bar"]
        picks = apply_gemini(result, {
            "B": {"pick": original_start + 1, "why": "nearby"},
            "E": {"pick": original_exit + 1, "why": "nearby"},
        }, confidence_threshold=0.9)

        self.assertEqual(picks["E"]["bar"], original_exit + 1)
        self.assertEqual(picks["B"]["bar"], original_start + 1)
        self.assertIsNone(result["cues"]["C"]["bar"])
        self.assertEqual(result["cues"]["D"]["bar"], result["cues"]["E"]["bar"] - 16)
        self.assertEqual(result["cues"]["F"]["name"], "FINAL CHORUS")
        self.assertEqual(result["cues"]["A"]["name"], "WARMUP 8")
        self.assertEqual(result["cues"]["A"]["bar"], result["cues"]["B"]["bar"] - 8)

    def test_cue_map_rejects_invalid_values_as_validation_errors(self):
        import copy
        from cue_maps import DEFAULT_CUE_MAPS, normalize_cue_maps

        invalid_meaning = copy.deepcopy(DEFAULT_CUE_MAPS)
        invalid_meaning["maps"]["VOCAL"]["A"]["meaning"] = []
        with self.assertRaisesRegex(ValueError, "unsupported meaning"):
            normalize_cue_maps(invalid_meaning)

        invalid_color = copy.deepcopy(DEFAULT_CUE_MAPS)
        invalid_color["maps"]["VOCAL"]["A"]["color"] = {}
        with self.assertRaisesRegex(ValueError, "Serato palette"):
            normalize_cue_maps(invalid_color)

    def test_manual_drop_map_leaves_drop_cues_blank_without_evidence(self):
        import copy
        from cue_maps import DEFAULT_CUE_MAPS

        settings = copy.deepcopy(DEFAULT_CUE_MAPS)
        settings["mode"] = "DROP"
        result = build_plan("forced-drop", self.analysis, self.plan, 176,
                            config=self.config, cue_maps=settings)

        self.assertEqual(result["map_type"], "DROP")
        self.assertTrue(all(result["cues"][slot]["bar"] is None for slot in "CDEF"))
        self.assertTrue(all("no evidence" in result["cues"][slot]["reason"] for slot in "CDEF"))

    def test_energy_fade_infers_low_confidence_outro_and_go_32(self):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 128}]}
        energy = [1.0] * (128 * 10)
        for frame in range(56 * 2 * 10, len(energy)):
            energy[frame] = 0.25

        result = build_plan("fade", analysis, {"cues": {"B": {"bar": 8}}}, 128,
                            genre="pop", config=self.config, energy=energy, fps=10)

        self.assertEqual(result["cues"]["H"]["bar"], 56)
        self.assertEqual(result["cues"]["G"]["bar"], 48)
        self.assertLess(result["cues"]["H"]["confidence"], 0.6)
        self.assertLess(result["cues"]["G"]["confidence"], 0.6)
        self.assertTrue(any("outro inferred from" in flag for flag in result["flags"]))
        self.assertIn("outro inferred from energy/vocal fade", result["cues"]["H"]["flags"])

    def test_stable_energy_does_not_invent_outro(self):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 128}]}
        result = build_plan("stable", analysis, {"cues": {"B": {"bar": 8}}}, 128,
                            genre="pop", config=self.config, energy=[1.0] * 1280, fps=10)

        self.assertIsNone(result["cues"]["H"]["bar"])
        self.assertIsNone(result["cues"]["G"]["bar"])
        self.assertEqual(result["cues"]["H"]["confidence"], 0.0)

    def test_short_dance_start_is_not_moved_to_fit_a_preferred_intro(self):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 128}]}
        energy = [0.2] * 400 + [1.0] * 880
        result = build_plan("edm", analysis, {"cues": {"B": {"bar": 2}}}, 128,
                            genre="House", config=self.config, energy=energy, fps=10)
        self.assertEqual(result["cues"]["B"]["bar"], 2)
        self.assertEqual(result["cues"]["A"]["name"], "INTRO-8")
        self.assertEqual(result["cues"]["A"]["bar"], 0)
        pop = build_plan("pop", analysis, {"cues": {"B": {"bar": 2}}}, 128,
                         genre="pop", config=self.config, energy=energy, fps=10)
        self.assertEqual(pop["cues"]["B"]["bar"], 2)

    def test_odd_dance_start_is_preserved_and_energy_rise_is_not_a_drop(self):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 128}]}
        energy = [0.2] * 400 + [1.0] * 880
        result = build_plan("edm", analysis, {"cues": {"B": {"bar": 7}}}, 128,
                            genre="House", config=self.config, energy=energy, fps=10)
        self.assertEqual(result["cues"]["B"]["bar"], 7)
        self.assertEqual(result["cues"]["A"]["name"], "INTRO-16")
        self.assertEqual(result["drops"], [])
        self.assertEqual(result["map_type"], "VOCAL")

    def test_short_opening_intro_boundary_is_preserved_for_dance_tracks(self):
        for end_bar in (3, 8, 15):
            with self.subTest(end_bar=end_bar):
                analysis = {"grid": self.grid, "segments": [
                    {"label": "intro", "start": 0, "end": end_bar * 2},
                    {"label": "verse", "start": end_bar * 2, "end": 128}]}
                result = build_plan("short-section", analysis, {"cues": {}}, 128, genre="House",
                                    config=self.config, energy=[1.0] * 1280, fps=10)
                self.assertEqual(result["cues"]["B"]["bar"], end_bar)
                self.assertEqual(result["drops"], [])
                self.assertFalse(any("START uncertain" in flag for flag in result["flags"]))

    def test_missing_dance_start_uses_observed_entry_without_phrase_rounding(self):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 128}]}
        for entry in (3, 7, 20):
            with self.subTest(entry=entry):
                result = build_plan("observed-entry", analysis, {"cues": {}}, 128, genre="House",
                                    config=self.config,
                                    energy=[0.2] * (entry * 20) + [1.0] * (1280 - entry * 20), fps=10)
                self.assertEqual(result["cues"]["B"]["bar"], entry)
                self.assertEqual(result["drops"], [])

    def test_missing_dance_start_does_not_default_to_bar_32(self):
        for energy in (None, [1.0] * 1280):
            with self.subTest(has_energy=energy is not None):
                analysis = {"grid": self.grid, "segments": [
                    {"label": "verse", "start": 0, "end": 56},
                    {"label": "intro", "start": 56, "end": 80}]}
                result = build_plan("no-entry", analysis, {"cues": {}}, 128, genre="House",
                                    config=self.config, energy=energy, fps=10)
                self.assertEqual(result["cues"]["B"]["bar"], 0)
                self.assertLess(result["cues"]["B"]["confidence"], 0.6)
                self.assertIsNone(result["cues"]["A"]["bar"])
                self.assertTrue(any("START uncertain" in flag for flag in result["flags"]))

    def test_gemini_last_drop_move_rechecks_lead_in_order(self):
        drops = [{"bar": bar, "confidence": 0.8, "reason": "detected", "time_s": bar * 2}
                 for bar in (24, 57)]
        result = build_plan("last-lead", self.analysis, self.plan, 256, genre="House",
                            config=self.config, detected_drops=drops)
        self.assertEqual(result["cues"]["E"]["bar"], 25)
        picks = apply_gemini(result, {"F": {"pick": 56, "why": "nearby return"}}, 0.9)
        self.assertEqual(picks["F"]["bar"], 56)
        self.assertEqual(result["cues"]["D"]["bar"], 24)
        self.assertEqual(result["cues"]["E"]["bar"], 40)
        self.assertEqual(result["cues"]["E"]["name"], "-16 LAST DROP")
        self.assertEqual(result["drops"][-1]["bar"], 56)
        self.assertEqual(result["cue_map"]["candidates"]["last_drop_start"]["bar"], 56)

    def test_gemini_rejects_reversed_drop_events_without_mutating_cues(self):
        import copy
        drops = [{"bar": bar, "confidence": 0.8, "reason": "detected", "time_s": bar * 2}
                 for bar in (24, 25)]
        result = build_plan("reversed", self.analysis, self.plan, 256, genre="House",
                            config=self.config, detected_drops=drops)
        original = copy.deepcopy(result)
        picks = apply_gemini(result, {"F": {"pick": 23}}, 0.9)
        self.assertEqual(picks, {})
        self.assertEqual(result["cues"], original["cues"])
        self.assertEqual(result["drops"], original["drops"])
        self.assertEqual(result["cue_map"], original["cue_map"])
        self.assertTrue(any("Gemini cue picks rejected" in flag for flag in result["flags"]))

    def test_gemini_single_drop_move_updates_both_pads_through_resolver(self):
        drops = [{"bar": 48, "confidence": 0.8, "reason": "detected", "time_s": 96}]
        result = build_plan("single", self.analysis, self.plan, 256, genre="House",
                            config=self.config, detected_drops=drops)
        picks = apply_gemini(result, {"D": {"pick": 49}}, 0.9)
        self.assertEqual(picks["D"]["bar"], 49)
        self.assertEqual(result["cues"]["D"]["bar"], result["cues"]["F"]["bar"])
        self.assertEqual(result["cues"]["C"]["bar"], result["cues"]["E"]["bar"])
        self.assertEqual(result["drops"][0]["bar"], 49)

    def test_gemini_rejects_conflicting_custom_pads_for_the_same_event(self):
        import copy
        from cue_maps import DEFAULT_CUE_MAPS
        settings = copy.deepcopy(DEFAULT_CUE_MAPS)
        settings["maps"]["DROP"]["F"]["meaning"] = "first_drop_start"
        drops = [{"bar": bar, "confidence": 0.8, "reason": "detected", "time_s": bar * 2}
                 for bar in (24, 57)]
        result = build_plan("aliases", self.analysis, self.plan, 256, genre="House",
                            config=self.config, detected_drops=drops, cue_maps=settings)
        original = copy.deepcopy(result["cues"])
        self.assertEqual(apply_gemini(result, {"D": {"pick": 25}, "F": {"pick": 26}}, 0.9), {})
        self.assertEqual(result["cues"], original)
        self.assertTrue(any("conflicting selections" in flag for flag in result["flags"]))

    def test_gemini_resolution_preserves_custom_disabled_pads_and_mix_lengths(self):
        import copy
        from cue_maps import DEFAULT_CUE_MAPS
        settings = copy.deepcopy(DEFAULT_CUE_MAPS)
        settings["mix_lengths"]["intro"] = [16, 8, 4]
        settings["maps"]["DROP"]["F"]["label"] = "FINAL RETURN"
        settings["maps"]["DROP"]["F"]["color"] = "red"
        settings["maps"]["DROP"]["E"]["enabled"] = False
        drops = [{"bar": bar, "confidence": 0.8, "reason": "detected", "time_s": bar * 2}
                 for bar in (24, 57)]
        result = build_plan("custom-resolve", self.analysis, self.plan, 256, genre="House",
                            config=self.config, detected_drops=drops, cue_maps=settings)
        self.assertIn("F", apply_gemini(result, {"F": {"pick": 56}}, 0.9))
        self.assertIsNone(result["cues"]["E"]["bar"])
        self.assertEqual(result["cues"]["F"]["name"], "FINAL RETURN")
        self.assertEqual(result["cues"]["F"]["color_id"], "red")
        self.assertEqual(result["cues"]["A"]["name"], "INTRO-16")
        self.assertEqual(result["cue_map"]["settings"], settings["maps"]["DROP"])

    def test_gemini_cannot_edit_derived_leads(self):
        result = self._house_plan(24, 0.1)
        result["cues"]["C"]["candidate_bars"] = [6, 7, 8]
        result["cues"]["C"]["confidence"] = 0.3
        original = result["cues"]["C"]["bar"]
        self.assertEqual(apply_gemini(result, {"C": {"pick": 7}}, 0.9), {})
        self.assertEqual(result["cues"]["C"]["bar"], original)

    def test_go_never_lands_before_the_last_drop(self):
        analysis = {"grid": self.grid, "segments": [{"label": "outro", "start": 100, "end": 128}]}
        drops = [{"bar": 40, "confidence": 0.8, "reason": "d", "time_s": 80},
                 {"bar": 50, "confidence": 0.8, "reason": "d", "time_s": 100}]
        result = build_plan("late", analysis, {"cues": {"B": {"bar": 8}}}, 128,
                            genre="House", config=self.config, detected_drops=drops)
        self.assertEqual(result["cues"]["H"]["bar"], 50)
        self.assertIsNone(result["cues"]["G"]["bar"])

    def _house_plan(self, drop_bar, vocal_level, mode=None):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 256}]}
        drops = [{"bar": drop_bar, "confidence": 0.8, "reason": "d", "time_s": drop_bar * 2}]
        stems = {"vocals": [vocal_level] * 5120}
        return build_plan("h", analysis, {"cues": {"B": {"bar": 16}}}, 256, genre="House", config=self.config,
                          detected_drops=drops, stems=stems, hop_s=0.05, cue_maps={"mode": mode} if mode else None)

    def test_auto_map_picks_vocal_for_vocal_led_house_with_only_a_late_drop(self):
        result = self._house_plan(100, 0.1)
        self.assertEqual(result["map_type"], "VOCAL")
        self.assertIn("vocal-led", result["map_choice"]["reason"])
        self.assertGreaterEqual(result["map_choice"]["vocal_ratio"], 0.9)
        self.assertTrue(any("vocal-led" in flag for flag in result["flags"]))

    def test_auto_map_keeps_drop_for_early_drop_or_instrumental_tracks(self):
        self.assertEqual(self._house_plan(24, 0.1)["map_type"], "DROP")
        self.assertEqual(self._house_plan(100, 0.0)["map_type"], "DROP")

    def test_manual_map_choice_beats_the_auto_rule(self):
        self.assertEqual(self._house_plan(100, 0.1, "DROP")["map_type"], "DROP")
        self.assertEqual(self._house_plan(24, 0.0, "VOCAL")["map_type"], "VOCAL")

    def _assist(self, answer, map_forced=False, fail=None):
        import gemini_assist
        plan = self._house_plan(24, 0.1)
        calls = []

        def caller(context, key):
            calls.append(context)
            if fail:
                raise RuntimeError(fail)
            return answer, 0.01

        with tempfile.TemporaryDirectory() as cache, patch("gemini_assist.load_api_key", return_value="test-key"):
            result = gemini_assist.assist(plan, lambda mode: self._house_plan(24, 0.1, mode), "h", cache,
                                          "Artist", "Title", map_forced=map_forced, caller=caller)
            again = gemini_assist.assist(self._house_plan(24, 0.1), lambda mode: self._house_plan(24, 0.1, mode),
                                         "h", cache, "Artist", "Title", map_forced=map_forced, caller=caller)
        return plan, result, again, calls

    def test_gemini_assist_switches_map_on_confident_disagreement_and_caches(self):
        plan, result, again, calls = self._assist({"map": "VOCAL", "map_confidence": 0.9, "map_why": "chorus-led"})
        self.assertEqual(plan["map_type"], "DROP")
        self.assertEqual(result["map_type"], "VOCAL")
        self.assertTrue(result["gemini"]["switched_map"])
        self.assertIn("Gemini chose VOCAL", result["map_choice"]["reason"])
        self.assertFalse(any("manually selected" in flag for flag in result["flags"]))
        self.assertEqual(len(calls), 1)
        self.assertTrue(again["gemini"]["cached"])
        self.assertEqual(result["gemini"]["baseline"]["map_type"], "DROP")
        self.assertEqual(result["gemini"]["baseline"]["cues"], plan["cues"])

    def test_gemini_map_switch_discards_original_map_picks(self):
        import gemini_assist
        drops = [{"bar": 24, "confidence": 0.8, "reason": "detected", "time_s": 48}]
        analysis = {"grid": self.grid, "segments": [
            {"label": "intro", "start": 0, "end": 32},
            {"label": "verse", "start": 32, "end": 64},
            {"label": "chorus", "start": 64, "end": 84},
            {"label": "outro", "start": 128, "end": 176}]}

        def rebuild(mode):
            return build_plan("switch", analysis, self.plan, 176, genre="House",
                              config=self.config, detected_drops=drops, cue_maps={"mode": mode})

        original = rebuild("DROP")
        expected = rebuild("VOCAL")
        answer = {"map": "VOCAL", "map_confidence": 0.95,
                  "picks": {"D": {"pick": 26, "why": "original first-drop candidate"}},
                  "concerns": [{"cue": "D", "issue": "check original first drop"}]}
        self.assertIn(26, original["cues"]["D"]["candidate_bars"])
        self.assertIn(26, expected["cues"]["D"]["candidate_bars"])
        with tempfile.TemporaryDirectory() as cache, patch("gemini_assist.load_api_key", return_value="test-key"):
            result = gemini_assist.assist(original, rebuild, "switch", cache,
                                         confidence_threshold=0.9, caller=lambda *_: (answer, 0.01))
        self.assertEqual(result["cues"], expected["cues"])
        self.assertEqual(result["gemini"]["suggestions"], {})
        self.assertIn("discarded", result["gemini"]["picks_discarded_reason"])
        self.assertIn("Original DROP map", result["gemini"]["concerns"][0]["issue"])
        self.assertEqual(result["gemini"]["baseline"]["cues"]["D"]["bar"], 24)
        result["cues"]["D"]["bar"] = 999
        self.assertEqual(result["gemini"]["baseline"]["cues"]["D"]["bar"], 24)

    def test_malformed_gemini_responses_preserve_local_cues_and_report_error(self):
        invalid = [[], {"concerns": 3}, {"concerns": "oops"}, {"concerns": [3]},
                   {"concerns": [{"cue": "A", "issue": None}]}, {"picks": []},
                   {"picks": {"A": {"pick": True}}}, {"picks": {"A": {"pick": 1, "why": []}}},
                   {"map_confidence": float("nan")}, {"map_confidence": float("inf")},
                   {"map_confidence": 2}, {"map_confidence": True}, {"map_why": []}]
        for answer in invalid:
            with self.subTest(answer=answer):
                _, result, again, calls = self._assist(answer)
                self.assertFalse(result["gemini"]["used"])
                self.assertIn("Invalid Gemini response", result["gemini"]["error"])
                self.assertEqual(result["cues"], self._house_plan(24, 0.1)["cues"])
                self.assertFalse(again["gemini"]["used"])
                self.assertEqual(len(calls), 1)

    def test_gemini_api_envelope_validation_reports_errors_instead_of_crashing(self):
        import gemini_assist
        invalid = [[], {}, {"usageMetadata": []},
                   {"usageMetadata": {"promptTokenCount": "100"}},
                   {"usageMetadata": {}, "candidates": []},
                   {"usageMetadata": {}, "candidates": ["oops"]},
                   {"usageMetadata": {}, "candidates": [{"content": []}]},
                   {"usageMetadata": {}, "candidates": [{"content": {"parts": [3]}}]},
                   {"usageMetadata": {}, "candidates": [{"content": {"parts": [{"text": []}]}}]}]
        for envelope in invalid:
            with self.subTest(envelope=envelope):
                def caller(*_):
                    return gemini_assist._parse_response(envelope)
                with tempfile.TemporaryDirectory() as cache, patch("gemini_assist.load_api_key", return_value="test-key"):
                    result = gemini_assist.assist(self._house_plan(24, 0.1), lambda _: None,
                                                  "envelope", cache, caller=caller)
                self.assertFalse(result["gemini"]["used"])
                self.assertIn("Gemini API response", result["gemini"]["error"])

    def test_gemini_api_parser_uses_answer_text_and_includes_thinking_cost(self):
        import gemini_assist
        answer, cost = gemini_assist._parse_response({
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "thoughtsTokenCount": 20},
            "candidates": [{"content": {"parts": [
                {"text": "private reasoning", "thought": True},
                {"text": '{"map": "DROP",'}, {"text": '"map_confidence": 0.8}'}]}}],
        })
        self.assertEqual(answer, {"map": "DROP", "map_confidence": 0.8})
        self.assertAlmostEqual(cost, (100 * 2 + 70 * 12) / 1_000_000)

    def test_gemini_assist_respects_manual_choice_and_low_confidence(self):
        _, forced, _, _ = self._assist({"map": "VOCAL", "map_confidence": 0.95}, map_forced=True)
        self.assertEqual(forced["map_type"], "DROP")
        _, unsure, _, _ = self._assist({"map": "VOCAL", "map_confidence": 0.5})
        self.assertEqual(unsure["map_type"], "DROP")
        self.assertFalse(unsure["gemini"]["switched_map"])

    def test_gemini_assist_failure_keeps_plan_and_records_error(self):
        plan, result, _, _ = self._assist({}, fail="boom")
        self.assertEqual(result["map_type"], "DROP")
        self.assertFalse(result["gemini"]["used"])
        self.assertIn("boom", result["gemini"]["error"])

    def test_gemini_assist_without_key_is_a_recorded_noop(self):
        import gemini_assist
        import os
        saved = os.environ.pop("GEMINI_API_KEY", None)
        original = gemini_assist.gemini_config.ROOT
        gemini_assist.gemini_config.ROOT = Path(tempfile.gettempdir()) / "no-such-root"
        try:
            with tempfile.TemporaryDirectory() as cache:
                result = gemini_assist.assist(self._house_plan(24, 0.1), lambda mode: None, "h", cache)
        finally:
            gemini_assist.gemini_config.ROOT = original
            if saved:
                os.environ["GEMINI_API_KEY"] = saved
        self.assertFalse(result["gemini"]["used"])
        self.assertIn("GEMINI_API_KEY", result["gemini"]["error"])

    def test_intro_leads_start_by_eight_bars_despite_a_late_intro_segment(self):
        analysis = {"grid": self.grid, "segments": [
            {"label": "verse", "start": 0, "end": 56},
            {"label": "intro", "start": 56, "end": 80},
            {"label": "chorus", "start": 80, "end": 120},
        ]}
        result = build_plan("late-intro", analysis, {"cues": {"B": {"bar": 32, "confidence": 0.8}}}, 240,
                            genre="pop", config=self.config)
        self.assertEqual(result["cues"]["B"]["bar"], 32)
        self.assertEqual(result["cues"]["A"]["bar"], 24)
        self.assertEqual(result["cues"]["A"]["time_s"], 48.0)
        self.assertEqual(result["cues"]["A"]["name"], "INTRO-32")

    def test_intro_preferences_and_bounds_in_both_maps(self):
        cases = [(40, 32, 8), (16, 8, 8), (8, 0, 8), (7, 3, 4),
                 (4, 0, 4), (3, 1, 2), (2, 0, 2), (1, 0, 1), (0, None, None)]
        for mode in ("DROP", "VOCAL"):
            for start, expected, lead in cases:
                with self.subTest(mode=mode, start=start):
                    result = build_plan(
                        "intro-preference", self.analysis, {"cues": {"B": {"bar": start}}},
                        176, config=self.config, cue_maps={"mode": mode})
                    cue = result["cues"]["A"]
                    self.assertEqual(result["cues"]["B"]["bar"], start)
                    self.assertEqual(cue["bar"], expected)
                    self.assertEqual(cue["time_s"], None if expected is None else expected * 2.0)
                    self.assertEqual(cue["candidate_bars"], [] if expected is None else [expected])
                    if lead is not None:
                        self.assertEqual(cue["name"], f"INTRO-{lead * 4}")
                    else:
                        self.assertEqual(cue["confidence"], 0.0)
                        self.assertTrue(cue["flags"])

    def test_intro_respects_negative_first_bar_and_offset(self):
        from cue_maps import apply_cue_map

        plan = {"map_type": "VOCAL", "bar_count": 100,
                "grid": {"t1": 2.1, "bar_s": 2.0, "offset_ms": 50, "first_bar": -1},
                "cues": {slot: {} for slot in "ABCDEFGH"}}
        events = {"start": {"bar": 7}}
        cue = apply_cue_map(plan, None, events, -1)["cues"]["A"]
        self.assertEqual(cue["bar"], -1)
        self.assertEqual(cue["name"], "INTRO-32")
        self.assertAlmostEqual(cue["time_s"], 0.05)

    def test_gemini_start_change_recalculates_intro_fallback(self):
        result = build_plan("intro-fallback", self.analysis, {"cues": {"B": {"bar": 7}}},
                            176, config=self.config)
        self.assertEqual(result["cues"]["A"]["bar"], 3)
        picks = apply_gemini(result, {"B": {"pick": 8, "why": "nearby"}},
                             confidence_threshold=0.8)
        self.assertIn("B", picks)
        self.assertEqual(result["cues"]["A"]["bar"], 0)
        self.assertEqual(result["cues"]["A"]["name"], "INTRO-32")
        self.assertEqual(result["cue_map"]["candidates"]["intro_start"]["bar"], 0)

    def test_last_drop_lead_in_never_lands_before_the_first_drop(self):
        import cue_maps
        plan = {"map_type": "DROP", "bar_count": 100, "grid": {"t1": 0.0, "bar_s": 2.0}, "cues": {slot: {} for slot in "ABCDEFGH"}}
        events = {"start": {"bar": 15}, "intro_start": {"bar": 0}, "first_drop_start": {"bar": 24},
                  "last_drop_start": {"bar": 48}, "outro_start": {"bar": 80}}
        cues = cue_maps.apply_cue_map(plan, None, events, 0)["cues"]
        self.assertEqual((cues["C"]["bar"], cues["C"]["name"]), (8, "-16 DROP"))
        self.assertEqual((cues["E"]["bar"], cues["E"]["name"]), (32, "-16 LAST DROP"))
        self.assertGreater(cues["E"]["bar"], cues["D"]["bar"])
        self.assertLess(cues["E"]["bar"], cues["F"]["bar"])

    def test_mix_windows_are_beats_and_customizable_in_both_maps(self):
        from cue_maps import apply_cue_map

        for mode in ("DROP", "VOCAL"):
            for lengths, expected in [
                (None, (24, 72, "INTRO-32", "GO-32", "EXIT-32")),
                ({"intro": [16, 8, 4], "go": [16, 8, 4], "exit": [64, 32]}, (28, 76, "INTRO-16", "GO-16", "EXIT-64")),
            ]:
                with self.subTest(mode=mode, lengths=lengths):
                    plan = {"map_type": mode, "bar_count": 100, "grid": self.grid,
                            "cues": {slot: {} for slot in "ABCDEFGH"}}
                    events = {"start": {"bar": 32}, "outro_start": {"bar": 80}}
                    settings = {"mix_lengths": lengths} if lengths else None
                    cues = apply_cue_map(plan, settings, events, 0)["cues"]
                    a, g, a_label, g_label, h_label = expected
                    self.assertEqual((cues["A"]["bar"], cues["G"]["bar"], cues["H"]["bar"]), (a, g, 80))
                    self.assertEqual((cues["A"]["name"], cues["G"]["name"], cues["H"]["name"]), (a_label, g_label, h_label))

    def test_mix_windows_fall_back_and_do_not_count_partial_bars(self):
        from cue_maps import apply_cue_map, refresh_dynamic_labels

        for available, lead, label in [(8, 8, 32), (7, 4, 16), (3, 2, 8), (1, 1, 4)]:
            plan = {"map_type": "DROP", "bar_count": 81 + available, "mix_end_bar": 80 + available,
                    "grid": self.grid, "cues": {slot: {} for slot in "ABCDEFGH"}}
            events = {"start": {"bar": 8}, "first_drop_start": {"bar": 16},
                      "last_drop_start": {"bar": 79 - available}, "outro_start": {"bar": 80}}
            cues = apply_cue_map(plan, None, events, 0)["cues"]
            self.assertEqual(cues["G"]["bar"], 80 - lead)
            self.assertEqual(cues["G"]["name"], f"GO-{label}")
            self.assertEqual(cues["H"]["name"], f"EXIT-{label}")
            refresh_dynamic_labels(plan)
            self.assertEqual(cues["G"]["name"], f"GO-{label}")
        plan["mix_end_bar"] = 80.9
        self.assertIsNone(apply_cue_map(plan, None, events, 0)["cues"]["H"]["bar"])

    def test_cue_map_migration_preserves_custom_labels_and_colors(self):
        from cue_maps import normalize_cue_maps

        old = {"version": 1, "maps": {"VOCAL": {
            "A": {"meaning": "intro_start", "label": "INTRO -{bars}", "color": "red", "enabled": False},
            "G": {"meaning": "before_outro_32", "label": "GO -32"},
            "H": {"meaning": "outro_start", "label": "MY OUTRO {bars}"},
        }}}
        settings = normalize_cue_maps(old)
        self.assertEqual(settings["version"], 2)
        self.assertEqual(settings["maps"]["VOCAL"]["A"]["label"], "INTRO-{beats}")
        self.assertEqual(settings["maps"]["VOCAL"]["A"]["color"], "red")
        self.assertFalse(settings["maps"]["VOCAL"]["A"]["enabled"])
        self.assertEqual(settings["maps"]["VOCAL"]["G"]["meaning"], "before_outro_8")
        self.assertEqual(settings["maps"]["VOCAL"]["H"]["label"], "MY OUTRO {bars}")
        self.assertEqual(normalize_cue_maps(settings), settings)
        for lengths in ([32, 16, 0], [16, 32], [32, 32], [7], [], [True], "32"):
            with self.subTest(lengths=lengths), self.assertRaises(ValueError):
                normalize_cue_maps({"mix_lengths": {"intro": lengths}})

    def test_outro_moves_after_even_a_brief_late_vocal_return(self):
        analysis = {"grid": self.grid, "segments": [{"label": "outro", "start": 120, "end": 200}]}
        vocals = [0.0] * 4000
        vocals[100:500] = [0.2] * 400
        vocals[3200:3202] = [0.2] * 2
        result = build_plan("vocal-return", analysis, {"cues": {"B": {"bar": 8}}}, 200,
                            config=self.config, stems={"vocals": vocals})
        self.assertEqual(result["cues"]["H"]["bar"], 81)
        self.assertEqual(result["cues"]["H"]["name"], "EXIT-32")
        self.assertEqual(result["cues"]["G"]["bar"], 73)
        self.assertTrue(any("final detected vocal" in flag for flag in result["flags"]))
        result["cues"]["H"]["confidence"] = 0.5
        apply_gemini(result, {"H": {"pick": 80}}, confidence_threshold=0.8)
        self.assertEqual(result["cues"]["H"]["bar"], 81)

    def test_quiet_vocal_returns_and_decay_move_outro_not_just_loud_vocals(self):
        vocals = [0.0] * 4000
        vocals[100:500] = [0.01] * 400
        vocals[3200:3202] = [0.004] * 2
        vocals[3202:3240] = [0.0005] * 38
        analysis = {"grid": self.grid, "segments": [{"label": "outro", "start": 120, "end": 200}]}
        result = build_plan("quiet-return", analysis, {"cues": {"B": {"bar": 8}}}, 200,
                            stems={"vocals": vocals})
        self.assertEqual(result["cues"]["H"]["bar"], 81)
        evidence = result["outro_vocal_evidence"]
        self.assertLess(evidence["threshold"], 0.004)
        self.assertGreaterEqual(evidence["last_vocal_end_s"], 160.1)
        self.assertEqual(evidence["status"], "analyzed")
        self.assertTrue(any("leakage/reverb" in flag for flag in result["flags"]))

    def test_transcription_in_silent_tail_cannot_extend_vocal_boundary(self):
        vocals = [0.0] * 4000
        vocals[100:500] = [0.2] * 400
        result = build_plan("hallucinated-tail", self.analysis, self.plan, 200,
                            stems={"vocals": vocals}, words=[
                                {"start": 190, "end": 199.9, "word": "hallucination"},
                                {"start": 190, "end": 250, "word": "past end"},
                                {"start": -1, "end": 10},
                                {"start": 190, "end": float("nan")},
                                {"start": 190}, None])
        self.assertEqual(result["outro_vocal_evidence"]["ignored_words"], 6)
        self.assertEqual(result["outro_vocal_evidence"]["last_vocal_end_s"], 25)
        self.assertEqual(result["cues"]["H"]["bar"], 32)
        self.assertFalse(result["no_vocal_free_outro"])

    def test_words_with_local_stem_support_can_extend_frame_boundary(self):
        vocals = [0.0] * 4000
        vocals[3200] = 0.01
        result = build_plan("supported-word", self.analysis, self.plan, 200,
                            stems={"vocals": vocals}, words=[
                                {"start": 160, "end": 160.2, "word": "return", "score": 0.9}])
        self.assertEqual(result["outro_vocal_evidence"]["ignored_words"], 0)
        self.assertAlmostEqual(result["outro_vocal_evidence"]["last_vocal_end_s"], 160.2)
        self.assertEqual(result["cues"]["H"]["bar"], 81)

    def test_invalid_or_partially_missing_stem_cannot_certify_instrumental_outro(self):
        for vocals in ([0.0] * 3999, [0.0] * 3999 + [float("nan")],
                       [0.0] * 3999 + [-0.1], [0.0] * 3999 + [float("inf")]):
            with self.subTest(tail=vocals[-1], count=len(vocals)):
                result = build_plan("invalid-stem", self.analysis, self.plan, 200,
                                    stems={"vocals": vocals})
                self.assertEqual(result["outro_vocal_evidence"]["status"], "unverified")
                self.assertIsNone(result["outro_vocal_free_bar"])
                self.assertLessEqual(result["cues"]["H"]["confidence"], 0.4)

    def test_partial_final_stem_frame_counts_activity_through_exact_song_end(self):
        result = build_plan("tail", self.analysis, self.plan, 175.31,
                            stems={"vocals": [0.0] * 3506 + [0.01]})
        self.assertEqual(result["outro_vocal_evidence"]["last_vocal_end_s"], 175.31)
        self.assertEqual(result["cues"]["H"]["name"], "EXIT-END")
        self.assertEqual(result["cues"]["H"]["time_s"], 175.31)

    def test_disabled_or_custom_remapped_pads_do_not_mutate_event_evidence(self):
        from cue_maps import apply_cue_map
        settings = {"maps": {"VOCAL": {"B": {"enabled": False}, "H": {"meaning": "start"}}}}
        result = build_plan("custom", self.analysis, self.plan, 176, cue_maps=settings)
        events = result["cue_map"]["candidates"]
        self.assertEqual(events["start"]["bar"], 16)
        self.assertEqual(events["outro_start"]["bar"], 32)
        self.assertIsNone(result["cues"]["B"]["bar"])
        self.assertEqual(result["cues"]["H"]["bar"], 16)
        apply_cue_map(result, settings, events, 0)
        self.assertEqual(result["cues"]["A"]["bar"], 8)
        self.assertEqual(result["cues"]["G"]["bar"], 30)

    def test_insufficient_configured_windows_have_explicit_reasons(self):
        result = build_plan("no-room", self.analysis, {"cues": {"B": {"bar": 2}}}, 80,
                            cue_maps={"mix_lengths": {"intro": [32], "exit": [64]}})
        self.assertIsNone(result["cues"]["A"]["bar"])
        self.assertIn("minimum 32-beat intro", result["cues"]["A"]["reason"])
        self.assertIsNone(result["cues"]["H"]["bar"])
        self.assertIn("minimum 64-beat EXIT", result["cues"]["H"]["reason"])

    def test_unordered_drop_evidence_is_sorted_before_selecting_first_and_last(self):
        result = build_plan("unordered", self.analysis, self.plan, 200, genre="House", detected_drops=[
            {"bar": 72, "confidence": 0.9, "reason": "later"},
            {"bar": 24, "confidence": 0.9, "reason": "earlier"}])
        self.assertEqual(result["cues"]["D"]["bar"], 24)
        self.assertEqual(result["cues"]["F"]["bar"], 72)

    def test_final_automatic_guard_rejects_inconsistent_output_without_mutation(self):
        from cue_maps import validate_automatic_plan
        original = build_plan("guard", self.analysis, self.plan, 176)
        changes = [
            lambda plan: plan["cues"]["A"].update(bar=-1),
            lambda plan: plan["cues"]["B"].update(time_s=99),
            lambda plan: plan["cues"]["G"].update(bar=31, time_s=62),
            lambda plan: plan["cues"]["H"].update(name="WRONG LABEL"),
            lambda plan: plan["cue_map"]["resolved"]["A"].update(enabled=False),
            lambda plan: plan.update(outro_vocal_free_bar=80),
            lambda plan: plan["cue_map"]["candidates"]["first_chorus_start"].update(bar=40),
        ]
        for change in changes:
            plan = copy.deepcopy(original)
            change(plan)
            before = copy.deepcopy(plan)
            with self.assertRaisesRegex(ValueError, "Invalid automatic cue plan"):
                validate_automatic_plan(plan)
            self.assertEqual(plan, before)
        validate_automatic_plan(original)

    def test_positive_grid_offset_cannot_place_automatic_intro_before_audio(self):
        analysis = {**self.analysis, "grid": {**self.grid, "offset_ms": 100}}
        result = build_plan("no-negative-audio", analysis, {"cues": {"B": {"bar": 8}}}, 176)
        self.assertEqual(result["grid"]["first_bar"], 1)
        self.assertEqual(result["cues"]["A"]["bar"], 4)
        self.assertTrue(all(cue["time_s"] >= 0 for cue in result["cues"].values()
                            if cue["time_s"] is not None))

    def test_end_of_song_fallback_when_vocals_continue_to_the_end(self):
        result = build_plan("no-outro", self.analysis, self.plan, 176, config=self.config,
                            stems={"vocals": [0.2] * 3520})
        self.assertEqual(result["cues"]["H"]["time_s"], 176)
        self.assertEqual(result["cues"]["H"]["name"], "EXIT-END")
        self.assertEqual(result["cues"]["G"]["time_s"], 160)
        self.assertEqual(result["cues"]["G"]["name"], "GO-32")
        self.assertTrue(result["no_vocal_free_outro"])
        self.assertTrue(any("no instrumental outro" in flag for flag in result["cues"]["G"]["flags"]))
        self.assertIn("no full vocal-free outro bar remains", result["flags"])

    def test_end_of_song_fallback_can_be_disabled(self):
        result = build_plan("no-outro", self.analysis, self.plan, 176, config=self.config,
                            stems={"vocals": [0.2] * 3520},
                            cue_maps={"end_of_song_fallback": False})
        self.assertIsNone(result["cues"]["G"]["bar"])
        self.assertIsNone(result["cues"]["H"]["bar"])

    def test_fractional_song_end_fallback_respects_offset_and_custom_preferences(self):
        analysis = {**self.analysis, "grid": {**self.grid, "offset_ms": 50}}
        result = build_plan(
            "fractional-end", analysis, self.plan, 175.3, config=self.config,
            stems={"vocals": [0.2] * 3506},
            cue_maps={"mix_lengths": {"go": [16, 8, 4]}})
        self.assertEqual(result["cues"]["H"]["time_s"], 175.3)
        self.assertEqual(result["cues"]["H"]["name"], "EXIT-END")
        self.assertEqual(result["cues"]["G"]["time_s"], 167.3)
        self.assertEqual(result["cues"]["G"]["name"], "GO-16")
        self.assertEqual(result["cues"]["H"]["candidate_bars"], [result["mix_end_bar"]])
        from cue_maps import refresh_dynamic_labels
        refresh_dynamic_labels(result)
        self.assertEqual(result["cues"]["H"]["name"], "EXIT-END")

    def test_missing_or_incomplete_vocal_analysis_marks_outro_unverified(self):
        for stems in (None, {"vocals": [0.0] * 20}):
            result = build_plan("unknown-vocals", self.analysis, self.plan, 176,
                                config=self.config, stems=stems)
            self.assertTrue(any("unverified" in flag for flag in result["cues"]["H"]["flags"]))
            self.assertLessEqual(result["cues"]["H"]["confidence"], 0.4)

    def test_vocal_fade_can_infer_outro_when_full_mix_does_not_fade(self):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 128}]}
        vocals = [1.0] * int(128 / 0.05)
        for frame in range(int(112 / 0.05), len(vocals)):
            vocals[frame] = 0.0
        result = build_plan("vocal-fade", analysis, {"cues": {"B": {"bar": 8}}}, 128,
                            genre="pop", config=self.config, energy=[1.0] * 1280, fps=10,
                            stems={"vocals": vocals}, hop_s=0.05)

        self.assertEqual(result["cues"]["H"]["bar"], 56)
        self.assertLess(result["cues"]["H"]["confidence"], 0.6)
        self.assertIn("outro inferred from energy/vocal fade", result["cues"]["H"]["flags"])

    def test_inferred_outro_and_go_32_stay_inside_track_bounds(self):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 70}]}
        energy = [1.0] * 700
        for frame in range(60 * 10, len(energy)):
            energy[frame] = 0.2
        result = build_plan("short-fade", analysis, {"cues": {"B": {"bar": 8}}}, 70,
                            genre="pop", config=self.config, energy=energy, fps=10)

        self.assertLess(result["cues"]["H"]["bar"], result["bar_count"])
        self.assertLess(result["cues"]["H"]["time_s"], 70)
        self.assertTrue(result["cues"]["G"]["bar"] is None or
                        0 <= result["cues"]["G"]["bar"] < result["bar_count"])

    def test_timed_lyrics_alignment_handles_offset_and_tempo(self):
        text = "[00:00.00] hello there friend\n[00:02.00] we sing this line\n[00:20.00] hello there friend\n[00:22.00] we sing this line"
        words = []
        for base, phrase in ((3, "hello there friend"), (5, "we sing this line"),
                             (23, "hello there friend"), (25, "we sing this line")):
            words.extend({"word": word, "start": base + index * 0.2, "end": base + index * 0.2 + 0.1}
                         for index, word in enumerate(phrase.split()))
        result = align_synced_lyrics(text, words, 2.0)
        self.assertEqual(result["status"], "aligned")
        self.assertAlmostEqual(result["offset_s"], 3.0, places=1)
        self.assertTrue(result["choruses"])
        self.assertEqual(len(parse_synced_lyrics(text)), 4)

    def test_poor_transcript_alignment_is_rejected(self):
        result = align_synced_lyrics("[00:00.00] words from another song",
                                     [{"word": "unrelated", "start": 2}], 2.0)
        self.assertEqual(result["status"], "rejected")

    def test_drop_detection_requires_low_energy_then_return(self):
        grid = {"t1": 0.0, "bar_s": 2.0}
        hop = 0.5
        drums, bass, low = [], [], []
        for bar in range(33):
            level = 0.1 if 24 <= bar < 32 else 1.0
            drums.extend([level] * 4)
            bass.extend([level] * 4)
            low.extend([level] * 4)
        drums[32 * 4:33 * 4] = [1.0] * 4
        bass[32 * 4:33 * 4] = [1.0] * 4
        low[32 * 4:33 * 4] = [1.0] * 4
        result = detect_drops({"drums": drums, "bass": bass}, low, hop, grid, 66, 8, 1.8)
        self.assertTrue(any(drop["bar"] == 32 for drop in result))
        long_drums, long_bass, long_low = [], [], []
        for bar in range(65):
            level = 0.1 if 48 <= bar < 64 else 1.0
            long_drums.extend([level] * 4)
            long_bass.extend([level] * 4)
            long_low.extend([level] * 4)
        long_drums[64 * 4:65 * 4] = [1.0] * 4
        long_bass[64 * 4:65 * 4] = [1.0] * 4
        long_low[64 * 4:65 * 4] = [1.0] * 4
        long_result = detect_drops({"drums": long_drums, "bass": long_bass}, long_low,
                                   hop, grid, 130, 32, 1.8)
        self.assertTrue(any(drop["bar"] == 64 and drop["breakdown_start_bar"] == 48 for drop in long_result))

    def _breakdown_stems(self, dips):
        """Per-bar energy 1.0 with shallow (0.7) dips; hop 0.5s with 2s bars."""
        level = {}
        for bar in range(97):
            level[bar] = 0.1 if 40 <= bar < 48 else 0.7 if 72 <= bar < 80 else 1.0
        series = [level[bar] for bar in range(97) for _ in range(4)]
        return {"drums": list(series), "bass": list(series)}, list(series)

    def test_shallow_return_supplements_one_confident_drop_only(self):
        stems, low = self._breakdown_stems(None)
        grid = {"t1": 0.0, "bar_s": 2.0}
        strict = detect_drops(stems, low, 0.5, grid, 194, 8, 1.8)
        self.assertEqual([drop["bar"] for drop in strict], [48])
        combined = detect_drops_with_fallback(stems, low, 0.5, grid, 194, 8, 0.55)
        self.assertEqual([drop["bar"] for drop in combined][0], 48)
        self.assertTrue(all(drop["bar"] >= 48 for drop in combined))
        none = detect_drops_with_fallback({"drums": [1.0] * 400, "bass": [1.0] * 400}, [1.0] * 400, 0.5, grid, 200)
        self.assertEqual(none, [])

    def test_scorer_uses_review_beats_and_counts_exact_labels(self):
        plan = build_plan("07", self.analysis, self.plan, 176, config=self.config)
        old_plan = {"cues": {"B": {"bar": 15}}}
        with tempfile.TemporaryDirectory() as temporary:
            v6_dir, v5_dir = Path(temporary) / "v6", Path(temporary) / "v5"
            v6_dir.mkdir()
            v5_dir.mkdir()
            (v6_dir / "07.json").write_text(json.dumps(plan), encoding="utf-8")
            (v5_dir / "07.json").write_text(json.dumps(old_plan), encoding="utf-8")
            marks = {("07", "B"): 64}
            result = score(str(v6_dir), marks, str(v5_dir))
        self.assertEqual(result["B"]["v6_beat"], 1)
        self.assertEqual(result["B"]["v5_beat"], 0)
        self.assertEqual(result["B"]["label"], 1)
        self.assertEqual(result["B"]["n_label"], 1)


if __name__ == "__main__":
    unittest.main()
