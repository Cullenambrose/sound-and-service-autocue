import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import marks
from score_v6 import load_marks


class MarksTests(unittest.TestCase):
    def event(self, action, beat=None, **extra):
        return {"track": "01", "cue": "B", "action": action, "to_beat": beat, **extra}

    def test_sequence_beats_clock_skew_clear_remark_and_nudges(self):
        events = [
            self.event("correct", 32, sequence=1, saved_at="tomorrow"),
            self.event("clear", sequence=2, saved_at="yesterday"),
            self.event("set_here", 41, sequence=3, saved_at="yesterday"),
            self.event("nudge", 42, sequence=4),
        ]
        self.assertEqual(marks.truth_beats(reversed(events)), {("01", "B"): 41})
        self.assertEqual(marks.truth_beats(events[:2]), {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "marks.json"
            path.write_text(json.dumps(list(reversed(events))))
            self.assertEqual(load_marks(path), marks.truth_beats(events))

    def test_timestamp_ties_preserve_file_order_and_empty_paths_do_not_read_research(self):
        self.assertEqual(marks.events([]), [])
        events = [self.event("correct", 8, saved_at="same"), self.event("clear", saved_at="same")]
        self.assertEqual(marks.truth_beats(events), {})
        self.assertEqual(marks.truth_beats(reversed(events)), {("01", "B"): 8})

    def test_dataset_collision_requires_explicit_selection(self):
        events = [self.event("correct", 8, dataset_id="one", sequence=1),
                  self.event("correct", 40, dataset_id="two", sequence=1)]
        with self.assertRaisesRegex(ValueError, "dataset_id"):
            marks.truth_beats(events)
        self.assertEqual(marks.truth_beats(events, "two"), {("01", "B"): 40})

    def test_deduplicates_event_exports_but_rejects_conflicting_history(self):
        event = self.event("correct", 8, event_id="saved", sequence=1)
        self.assertEqual(marks.truth_beats([event, event]), {("01", "B"): 8})
        for invalid in (
            [event, {**event, "to_beat": 12}],
            [event, self.event("clear", sequence=1)],
            [event, self.event("clear")],
            [self.event("correct", True)],
            [self.event("correct", float("nan"))],
        ):
            with self.assertRaises(ValueError):
                marks.truth_beats(invalid)

    def test_all_slots_and_content_ids_remain_distinct(self):
        events = [dict(self.event("correct", i), cue=slot, track_hash="a" * 64)
                  for i, slot in enumerate("ABCDEFGH")]
        self.assertEqual(len(marks.truth_beats(events)), 8)
        self.assertEqual(marks.truth_beats(events)[("a" * 64, "E")], 4)

    def test_legacy_number_aliases_share_order_and_clear_semantics(self):
        events = [self.event("correct", 32, sequence=1),
                  {**self.event("clear", sequence=2), "track": "1"}]
        self.assertEqual(marks.truth_beats(events), {})
