import sys
import copy
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiments
from rules_v6 import build_plan
from synced_lyrics import align_synced_lyrics


def base_kwargs():
    grid = {"t1": 0.0, "bar_s": 2.0, "offset_ms": 0, "bpm": 120}
    analysis = {"grid": grid, "downbeats": [index * 2.0 for index in range(60)], "segments": [
        {"label": "intro", "start": 0, "end": 16}, {"label": "verse", "start": 16, "end": 32},
        {"label": "chorus", "start": 32, "end": 40}, {"label": "verse", "start": 40, "end": 56},
        {"label": "chorus", "start": 56, "end": 64}, {"label": "outro", "start": 64, "end": 88}]}
    return dict(track_id="01", analysis=analysis,
                plan={"cues": {"B": {"bar": 16, "confidence": 0.8}, "H": {"bar": 64, "confidence": 0.7}}},
                duration_s=176, config={"switches": {"synced_lyrics": True, "drop_detection": True,
                                                     "genre_map": True, "gemini_tiebreaker": False},
                                        "drop_min_confidence": 0.65, "drop_phrase_bars": 8,
                                        "drop_recovery_ratio": 1.8},
                energy=[1.0] * 8800, fps=50.0, low_energy=[1.0] * 8800, hop_s=0.05, stems=None)


class ExperimentTests(unittest.TestCase):
    def test_enabled_list_is_validated_and_keeps_baseline_first(self):
        self.assertEqual(experiments.normalize_enabled(["kick_grid"]), ["baseline", "kick_grid"])
        self.assertIn("kick_grid", experiments.normalize_enabled(None))
        self.assertNotIn("roformer_stems", experiments.normalize_enabled(None))
        with self.assertRaises(ValueError):
            experiments.normalize_enabled(["made_up"])

    def test_energy_jumps_land_on_phrase_boundaries(self):
        grid = {"t1": 0.0, "bar_s": 2.0}
        fps = 10
        rms = [0.2] * (16 * 2 * fps) + [0.6] * (16 * 2 * fps)
        low = [0.1] * (16 * 2 * fps) + [0.5] * (16 * 2 * fps)
        jumps = experiments.detect_energy_jumps(rms, low, fps, grid, 64.0)
        self.assertEqual([jump["bar"] for jump in jumps], [16])
        self.assertEqual(experiments.detect_energy_jumps([0.3] * 640, [0.2] * 640, fps, grid, 64.0), [])

    def test_kick_grid_shifts_onto_measured_kicks(self):
        kwargs = base_kwargs()
        low = [0.0] * 8800
        for beat in kwargs["analysis"]["downbeats"]:
            low[int(round((beat + 0.04) * 50))] = 100.0
        context = {**kwargs, "low_energy_raw": low, "fps": 50.0}
        result = experiments._kick_grid(context)
        self.assertAlmostEqual(result["analysis"]["grid"]["t1"], 0.04, places=2)
        self.assertEqual(kwargs["analysis"]["grid"]["t1"], 0.0)

    def test_kick_grid_never_replaces_imported_serato_grid(self):
        kwargs = base_kwargs()
        kwargs["analysis"]["grid"]["source"] = "serato_beatgrid"
        with self.assertRaisesRegex(experiments.Unavailable, "imported Serato beatgrid retained"):
            experiments._kick_grid(kwargs)

    def test_unavailable_and_failed_variants_never_invent_cues(self):
        kwargs = base_kwargs()
        baseline = build_plan(**kwargs)
        context = {**kwargs, "low_energy_raw": None, "fps": 50.0, "paths": None, "decoded_wav": None}
        variants = experiments.run_variants(kwargs, context, baseline,
                                            ["baseline", "kick_grid", "fuzzy_lyrics", "roformer_stems"], build_plan)
        self.assertEqual(variants["baseline"]["status"], "ok")
        for key in ("kick_grid", "fuzzy_lyrics", "roformer_stems"):
            self.assertEqual(variants[key]["status"], "unavailable")
            self.assertEqual(variants[key]["cues"], {})

    def test_fuzzy_alignment_accepts_misheard_words(self):
        lyrics = "\n".join(f"[00:{10 + 4 * index:02d}.00] we are dancing tonight number {index}" for index in range(6))
        words = []
        for index in range(6):
            for offset, text in enumerate(["we", "are", "dancin", "tonite", "number", str(index)]):
                words.append({"word": text, "start": 10 + 4 * index + offset * 0.4, "end": 10.3 + 4 * index + offset * 0.4})
        strict = align_synced_lyrics(lyrics, words, 2.0)
        fuzzy = align_synced_lyrics(lyrics, words, 2.0, fuzzy=True)
        self.assertGreaterEqual(fuzzy["matched_lines"], strict["matched_lines"])
        self.assertEqual(fuzzy["status"], "aligned")

    def test_scoring_counts_wins_and_losses_against_graded_cues(self):
        grid = {"t1": 0.0, "bar_s": 2.0}
        cues = {slot: {"bar": 10, "time_s": 20.0} for slot in "AB"}
        graded = {"grid": grid, "map_type": "VOCAL", "cues": cues, "variants": {
            "baseline": {"status": "ok", "label": "Baseline", "cues": {"A": {"bar": 10, "time_s": 20.0},
                                                                          "B": {"bar": 12, "time_s": 24.0}}},
            "kick_grid": {"status": "ok", "label": "Kick", "cues": {"A": {"bar": 10, "time_s": 20.1},
                                                                    "B": {"bar": 10, "time_s": 20.2}}},
            "roformer_stems": {"status": "unavailable", "label": "R", "cues": {}}}}
        report = experiments.score_tracks([{"name": "a.mp3", "plan": graded, "reviewed": True}])
        self.assertEqual(report["graded_songs"], 1)
        self.assertEqual(report["variants"]["baseline"]["right"], 1)
        self.assertEqual(report["variants"]["baseline"]["total"], 2)
        self.assertEqual(report["variants"]["kick_grid"]["wins"], 1)
        self.assertEqual(report["variants"]["kick_grid"]["losses"], 0)
        self.assertNotIn("roformer_stems", report["variants"])

    def test_score_uses_answer_grid_offset_and_tempo_instead_of_current_grid(self):
        graded = {"grid": {"t1": 0, "bar_s": 4, "offset_ms": 0}, "map_type": "VOCAL",
                  "answer_key": {"grid": {"t1": 0, "bar_s": 2, "offset_ms": 800},
                                 "cues": {"A": {"bar": 10.25}}},
                  "variants": {"baseline": {"status": "ok", "label": "Baseline", "cues": {
                      "A": {"bar": 10.25, "time_s": 19.7}}},
                      "kick_grid": {"status": "ok", "label": "Kick", "cues": {
                          "A": {"bar": 10.25, "time_s": 20.5}}}}}
        result = experiments.score_tracks([{"name": "offset.mp3", "plan": graded}])
        slot = result["tracks"][0]["slots"]["A"]
        self.assertAlmostEqual(slot["truth"], 19.7)
        self.assertTrue(slot["variants"]["baseline"]["right"])
        self.assertFalse(slot["variants"]["kick_grid"]["right"])

    def test_summary_and_bar_features_retain_offset_timing(self):
        grid = {"t1": 1, "bar_s": 2, "offset_ms": 1000}
        summary = experiments.summarize({"grid": grid}, "offset")
        self.assertEqual(summary["offset_ms"], 1000)
        table = experiments.bar_energy_table(None, [1, 1, 8, 8], [1, 1, 8, 8], 1, 1, grid, 4)
        self.assertEqual(len(table), 2)
        self.assertEqual(table[1]["rms"], 8)

    def test_unreviewed_plan_never_scores_against_itself(self):
        plan = build_plan(**base_kwargs())
        plan["variants"] = {"baseline": experiments.summarize(plan, "Rules")}
        report = experiments.score_tracks([{"name": "unreviewed", "plan": plan}])
        self.assertEqual(report["graded_songs"], 0)
        self.assertEqual(report["variants"], {})
        self.assertIn("no human-reviewed", report["skipped"][0]["reason"])

    def test_explicit_empty_targets_are_separate_and_omitted_targets_unknown(self):
        plan = {"grid": {"t1": 0, "bar_s": 2},
                "answer_key": {"grid": {"t1": 0, "bar_s": 2}, "cues": {
                    "A": {"bar": 10}, "B": {"bar": None}, "C": {"bar": 20}, "D": {"bar": None}}},
                "variants": {"baseline": {"status": "ok", "cues": {
                    "A": {"bar": 10}, "B": {"bar": None}, "C": {"bar": None}, "D": {"bar": 1}}}}}
        report = experiments.score_tracks([{"name": "partial", "plan": plan}])
        row = report["variants"]["baseline"]
        self.assertEqual((row["total"], row["right"]), (4, 2))
        self.assertEqual((row["positioned_total"], row["positioned_right"]), (2, 1))
        self.assertEqual((row["empty_total"], row["empty_right"]), (2, 1))
        self.assertEqual((row["missing"], row["false_positives"]), (1, 1))
        self.assertEqual(set(report["tracks"][0]["slots"]), set("ABCD"))
        self.assertEqual(report["tracks"][0]["slots"]["C"]["variants"]["baseline"]["failure"], "missing cue")
        self.assertEqual(report["tracks"][0]["slots"]["D"]["variants"]["baseline"]["failure"], "false positive")

    def test_missing_prediction_is_not_a_correct_empty(self):
        plan = {"grid": {"t1": 0, "bar_s": 2},
                "answer_key": {"cues": {"A": {"bar": None}}},
                "variants": {"baseline": {"status": "ok", "cues": {}}}}
        report = experiments.score_tracks([{"name": "missing", "plan": plan}])
        self.assertEqual(report["variants"]["baseline"]["right"], 0)
        self.assertIn("missing this slot", report["tracks"][0]["slots"]["A"]["variants"]["baseline"]["failure"])

    def test_different_maps_and_custom_meanings_are_excluded_not_counted_as_errors(self):
        plan = {"map_type": "DROP", "grid": {"t1": 0, "bar_s": 2},
                "answer_key": {"map_type": "VOCAL", "cues": {"A": {"bar": 1, "meaning": "start"}, "D": {"bar": 20}}},
                "variants": {"baseline": {"status": "ok", "map_type": "DROP", "cues": {
                    "A": {"bar": 1, "meaning": "intro_start"}, "D": {"bar": 20}}},
                    "gemini": {"status": "ok", "map_type": "VOCAL", "cues": {
                        "A": {"bar": 1, "meaning": "start"}, "D": {"bar": 20}}}}}
        report = experiments.score_tracks([{"name": "map-switch", "plan": plan}])
        self.assertNotIn("baseline", report["variants"])
        self.assertEqual(report["variants"]["gemini"]["right"], 2)
        self.assertEqual(report["variants"]["gemini"]["wins"], 0)
        self.assertEqual(report["map_comparison"]["baseline"], {"right": 0, "total": 1})
        self.assertEqual(report["map_comparison"]["gemini"], {"right": 1, "total": 1})
        self.assertIsNone(report["tracks"][0]["slots"]["D"]["variants"]["baseline"]["right"])

    def test_rules_only_snapshot_is_independent_and_ai_cost_is_scoped(self):
        kwargs = base_kwargs()
        rules = build_plan(**kwargs)
        assisted = copy.deepcopy(rules)
        assisted["cues"]["B"]["bar"] += 2
        assisted["cues"]["B"]["time_s"] += 4
        assisted["gemini"] = {"used": True, "baseline": rules, "cached": False, "new_cost_usd": 0.0123}
        variants = experiments.run_variants(kwargs, kwargs, assisted, ["baseline"], build_plan)
        self.assertEqual(variants["baseline"]["cues"]["B"]["bar"], rules["cues"]["B"]["bar"])
        self.assertEqual(variants["gemini"]["cues"]["B"]["bar"], assisted["cues"]["B"]["bar"])
        assisted["variants"] = variants
        assisted["answer_key"] = experiments.review_answer(assisted, ["B"])
        report = experiments.score_tracks([{"name": "ai", "plan": assisted}])
        self.assertEqual(report["variants"]["gemini"]["wins"], 1)
        self.assertEqual(report["variants"]["baseline"]["right"], 0)
        self.assertEqual(report["gemini_cost"]["new_cost_usd"], 0.0123)
        self.assertEqual(report["gemini_cost"]["requests"], 1)
        assisted["gemini"]["cached"] = True
        assisted["gemini"]["new_cost_usd"] = 0
        again = experiments.score_tracks([{"name": "ai", "plan": assisted}])
        self.assertEqual(again["gemini_cost"]["requests"], 0)
        self.assertEqual(again["gemini_cost"]["cache_hits"], 1)
        assisted["cues"]["B"]["bar"] = 999
        self.assertNotEqual(variants["gemini"]["cues"]["B"]["bar"], 999)

    def test_legacy_assisted_plan_without_rules_snapshot_does_not_invent_baseline(self):
        plan = {"grid": {"t1": 0, "bar_s": 2}, "cues": {"A": {"bar": 1}},
                "gemini": {"used": True},
                "variants": {"baseline": {"status": "ok", "cues": {"A": {"bar": 1}}}}}
        variants = experiments.scoring_variants(plan)
        self.assertEqual(variants["baseline"]["status"], "unavailable")
        self.assertEqual(variants["gemini"]["status"], "ok")

    def test_old_reviewed_cues_cannot_be_relabelled_as_automatic_predictions(self):
        plan = {"map_type": "DROP", "grid": {"t1": 0, "bar_s": 2},
                "cues": {"A": {"bar": 10, "name": "manual edit"}}}
        variants = experiments.scoring_variants(plan, allow_current=False)
        self.assertEqual(variants["baseline"]["status"], "unavailable")
        self.assertIn("no saved pre-review", variants["baseline"]["detail"])
        plan["gemini"] = {"used": True, "baseline": copy.deepcopy(plan)}
        variants = experiments.scoring_variants(plan, allow_current=False)
        self.assertEqual(variants["baseline"]["status"], "ok")
        self.assertEqual(variants["gemini"]["status"], "unavailable")

    def test_partial_reviews_preserve_old_physical_times_and_meanings_across_reanalysis(self):
        original = {"map_type": "VOCAL", "grid": {"t1": 0, "bar_s": 2, "offset_ms": 800},
                    "cues": {"A": {"bar": 10.25, "name": "MY INTRO", "meaning": "intro_start"},
                             "D": {"bar": None, "name": "EMPTY"}}}
        answer = experiments.review_answer(original)
        fresh = {"map_type": "DROP", "grid": {"t1": 1, "bar_s": 4},
                 "cues": {"B": {"bar": 20, "name": "START", "meaning": "start"}}}
        merged = experiments.merge_review_answer(fresh, answer, ["B"])
        self.assertAlmostEqual(experiments._truth_time(merged["cues"]["A"], merged["grid"]), 19.7)
        self.assertEqual(merged["cues"]["A"]["name"], "MY INTRO")
        self.assertEqual(merged["cues"]["A"]["map_type"], "VOCAL")
        self.assertEqual(merged["cues"]["B"]["map_type"], "DROP")
        self.assertIsNone(merged["cues"]["D"]["bar"])
        self.assertEqual(original["cues"]["A"]["bar"], 10.25)

    def test_timing_metrics_and_invalid_predictions_are_explicit(self):
        plan = {"grid": {"t1": 0, "bar_s": 2},
                "answer_key": {"cues": {"A": {"bar": 10}, "B": {"bar": 20}, "C": {"bar": 30}}},
                "variants": {"baseline": {"status": "ok", "cues": {
                    "A": {"bar": 10, "time_s": 20.5}, "B": {"bar": 20, "time_s": 41.5},
                    "C": {"bar": 30, "time_s": float("nan")}}},
                    "failed": {"status": "failed", "detail": "analyzer failed"}}}
        report = experiments.score_tracks([{"name": "timing", "plan": plan}])
        row = report["variants"]["baseline"]
        self.assertEqual(row["positioned_right"], 1)
        self.assertEqual(row["within_bar"], 2)
        self.assertEqual(row["mean_error_s"], 1)
        self.assertEqual(row["median_error_s"], 1)
        self.assertIn("not finite", report["tracks"][0]["slots"]["C"]["variants"]["baseline"]["failure"])
        self.assertEqual(report["unavailable_predictions"][0]["reason"], "analyzer failed")


if __name__ == "__main__":
    unittest.main()
