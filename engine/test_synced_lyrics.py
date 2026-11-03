import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from synced_lyrics import align_synced_lyrics, parse_synced_lyrics


def lyrics(rows):
    return "\n".join(f"[{int(time) // 60:02d}:{time % 60:05.2f}] {text}" for time, text in rows)


def transcript(rows):
    return [{"word": word, "start": time + index * 0.15, "end": time + index * 0.15 + 0.1}
            for time, text in rows for index, word in enumerate(text.split())]


CHORUS = [(0, "hold the night forever"), (4, "we are dancing together")]


class LocalLyricAlignmentTests(unittest.TestCase):
    def test_global_offset_and_tempo_use_actual_word_boundaries(self):
        rows = CHORUS + [(20 + time, text) for time, text in CHORUS]
        result = align_synced_lyrics(lyrics(rows), transcript([(3 + time * 1.04, text) for time, text in rows]), 2)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["mode"], "global")
        self.assertAlmostEqual(result["offset_s"], 3)
        self.assertAlmostEqual(result["tempo_ratio"], 1.04)
        self.assertEqual(len(result["choruses"]), 2)
        self.assertAlmostEqual(result["choruses"][0]["end_s"], 7.61 + 0.1)

    def test_inserted_instrumental_break_uses_separate_local_offsets(self):
        source = CHORUS + [(20 + time, text) for time, text in CHORUS]
        audio = [(10 + time, text) for time, text in CHORUS] + [(100 + time, text) for time, text in CHORUS]
        result = align_synced_lyrics(lyrics(source), transcript(audio), 2)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["mode"], "sectionwise")
        self.assertIsNone(result["offset_s"])
        self.assertIsNone(result["tempo_ratio"])
        self.assertEqual(len(result["sections"]), 2)
        self.assertEqual([span["start_s"] for span in result["choruses"]], [10, 100])
        self.assertTrue(all(span["end_s"] <= 105 for span in result["choruses"]))

    def test_reordered_and_repeated_sections_are_supported_locally(self):
        verse = [(10, "walking past the window"), (14, "shadows on the pavement")]
        source = CHORUS + verse + [(30 + time, text) for time, text in CHORUS]
        audio = [(40 + time, text) for time, text in verse] + [(80 + time, text) for time, text in CHORUS]
        audio += [(140 + time, text) for time, text in CHORUS]
        result = align_synced_lyrics(lyrics(source), transcript(audio), 2)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["matched_lines"], 6)
        self.assertEqual(result["mode"], "sectionwise")
        self.assertEqual([span["start_s"] for span in result["choruses"]], [80, 140])
        intervals = [(item["first_token"], item["last_token"]) for item in result["matches"]]
        self.assertTrue(all(right[0] > left[1] for left, right in zip(intervals, intervals[1:])))

    def test_unperformed_lyric_choruses_are_not_extrapolated(self):
        source = CHORUS + [(20 + time, text) for time, text in CHORUS]
        result = align_synced_lyrics(lyrics(source), transcript(CHORUS), 2)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["matched_lines"], 2)
        self.assertEqual(result["choruses"], [])

    def test_deleted_lines_do_not_force_wrong_global_mapping(self):
        rows = [(0, "one day we will fly"), (4, "never let the music die"),
                (8, "this missing line is different"), (12, "stars will guide us home"),
                (16, "find the place where we belong")]
        audio = [(time + 5, text) for time, text in rows if time != 8]
        result = align_synced_lyrics(lyrics(rows), transcript(audio), 2)
        self.assertEqual(result["matched_lines"], 4)
        self.assertEqual(result["mode"], "global")
        self.assertEqual(result["offset_s"], 5)

    def test_shared_short_words_and_single_line_repetition_are_not_alignment(self):
        for source, audio in [
            ([(0, "oh yeah"), (4, "oh yeah")], [(1, "oh yeah"), (5, "oh yeah")]),
            ([(0, "we keep moving onward"), (4, "we keep moving onward")],
             [(1, "we keep moving onward"), (5, "we keep moving onward")]),
            (CHORUS, [(0, "words from a different song"), (4, "nothing matches these lyrics")]),
        ]:
            with self.subTest(source=source):
                result = align_synced_lyrics(lyrics(source), transcript(audio), 2)
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["choruses"], [])

    def test_implausible_local_tempo_and_scattered_word_salad_are_rejected(self):
        for audio in ([(0, CHORUS[0][1]), (40, CHORUS[1][1])],
                      [(index * 20, word) for index, word in enumerate("hold the night forever we are dancing together".split())]):
            result = align_synced_lyrics(lyrics(CHORUS), transcript(audio), 2)
            self.assertEqual(result["status"], "rejected")

    def test_invalid_words_do_not_raise_or_supply_fake_evidence(self):
        words = transcript(CHORUS) + [
            None, {"word": "hold", "start": float("nan"), "end": 2},
            {"word": "night", "start": 1, "end": float("inf")},
            {"word": "together", "start": -1, "end": 1},
            {"word": "hold", "start": 1}, {"word": "hold", "start": 2, "end": 1}]
        result = align_synced_lyrics(lyrics(CHORUS), list(reversed(words)), 2)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["ignored_word_timings"], 6)
        self.assertEqual(result["matched_lines"], 2)

    def test_candidate_explosion_is_explicitly_rejected(self):
        source = [(index * 4, CHORUS[index % 2][1]) for index in range(100)]
        result = align_synced_lyrics(lyrics(source), transcript(source), 2)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("candidate limit", result["reason"])

    def test_parser_sorts_timestamps_and_drops_invalid_seconds(self):
        parsed = parse_synced_lyrics("[00:04] later lyric line\n[00:61] invalid timestamp\n[00:00] first lyric line")
        self.assertEqual([line["time_s"] for line in parsed], [0, 4])

    def test_planner_consumes_local_choruses_without_inventing_unperformed_sections(self):
        from rules_v6 import build_plan
        source = CHORUS + [(20 + time, text) for time, text in CHORUS]
        audio = [(40 + time, text) for time, text in CHORUS] + [(100 + time, text) for time, text in CHORUS]
        result = build_plan("remix", {"grid": {"t1": 0, "bar_s": 2}, "segments": []},
                            {"cues": {"B": {"bar": 4}}}, 160,
                            words=transcript(audio), synced_lyrics=lyrics(source))
        self.assertEqual(result["lyric_alignment"]["mode"], "sectionwise")
        self.assertEqual(len(result["chorus_spans"]), 2)
        self.assertEqual([span["source"] for span in result["chorus_spans"]],
                         ["aligned_synced_lyrics"] * 2)
        self.assertEqual(result["chorus_spans"][-1]["start_bar"], 52)


if __name__ == "__main__":
    unittest.main()
