import sys
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
        report = experiments.score_tracks([{"name": "a.mp3", "plan": graded}])
        self.assertEqual(report["graded_songs"], 1)
        self.assertEqual(report["variants"]["baseline"]["right"], 1 + 6)  # A right, B wrong, C-H empty both
        self.assertEqual(report["variants"]["kick_grid"]["wins"], 1)
        self.assertEqual(report["variants"]["kick_grid"]["losses"], 0)
        self.assertNotIn("roformer_stems", report["variants"])


if __name__ == "__main__":
    unittest.main()
