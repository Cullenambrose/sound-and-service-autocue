import sys
import unittest
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules_v6 import build_plan, detect_drops
from gemini_v6 import apply as apply_gemini
from score_v6 import label_valid, labels_exact, score
from synced_lyrics import align_synced_lyrics, parse_synced_lyrics


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
        self.assertEqual(result["cues"]["A"]["name"], "INTRO -16")
        self.assertEqual(result["cues"]["C"]["name"], "EXIT -32")
        self.assertEqual(result["cues"]["D"]["name"], "EXIT -16")
        self.assertEqual(result["cues"]["E"]["bar"], 32)
        self.assertEqual(result["cues"]["D"]["bar"], 16)
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

    def test_intro_shorter_than_16_uses_measured_count(self):
        plan = {"cues": {"B": {"bar": 8, "confidence": 0.7}}}
        result = build_plan("03", self.analysis, plan, 176, config=self.config)
        self.assertEqual(result["cues"]["A"]["name"], "INTRO -8")
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
        self.assertEqual(result["cues"]["A"]["name"], "WARMUP 16")
        original_exit = result["cues"]["E"]["bar"]
        original_start = result["cues"]["B"]["bar"]
        picks = apply_gemini(result, {
            "B": {"pick": original_start + 1, "why": "nearby"},
            "E": {"pick": original_exit + 1, "why": "nearby"},
        }, confidence_threshold=0.9)

        self.assertEqual(picks["E"]["bar"], original_exit + 1)
        self.assertEqual(picks["B"]["bar"], original_start + 1)
        self.assertEqual(result["cues"]["C"]["bar"], result["cues"]["E"]["bar"] - 32)
        self.assertEqual(result["cues"]["D"]["bar"], result["cues"]["E"]["bar"] - 16)
        self.assertEqual(result["cues"]["F"]["name"], "FINAL CHORUS")
        self.assertEqual(result["cues"]["A"]["name"], "WARMUP 17")

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
        self.assertEqual(result["cues"]["G"]["bar"], 24)
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

    def test_vocal_fade_can_infer_outro_when_full_mix_does_not_fade(self):
        analysis = {"grid": self.grid, "segments": [{"label": "verse", "start": 0, "end": 128}]}
        vocals = [1.0] * int(128 / 0.05)
        for frame in range(int(112 / 0.05), len(vocals)):
            vocals[frame] = 0.2
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
