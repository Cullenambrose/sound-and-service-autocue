import copy
import unittest

from setlist.transitions import transition_report, validate_playback


def track(file_id, **changes):
    return {"id": file_id, "content_hash": "a" * 64, "duration_s": 180,
            "bpm": 120, "key": "8A", "version": "Clean", **changes}


def window(file_id, **changes):
    return {"file_id": file_id, "content_hash": "a" * 64,
            "entry_s": 10, "exit_s": 130, "reviewed": True, **changes}


def playback(ids=("a", "b", "c"), seconds=10):
    return {"windows": [window(file_id) for file_id in ids], "overlaps": [
        {"from_id": left, "to_id": right, "seconds": seconds}
        for left, right in zip(ids, ids[1:])]}


class TransitionTests(unittest.TestCase):
    def test_explicit_arithmetic_timeline_and_inputs_unchanged(self):
        tracks = [track(file_id) for file_id in ("a", "b", "c")]
        value = playback()
        original = copy.deepcopy((tracks, value))
        report = transition_report(tracks, value)
        self.assertEqual(report["status"], "estimated")
        self.assertEqual(report["full_file_runtime_s"], 540)
        self.assertEqual(report["play_window_subtotal_s"], 360)
        self.assertEqual(report["estimated_elapsed_s"], 340)
        self.assertEqual(report["timeline"], [
            {"file_id": "a", "start_s": 0, "end_s": 120},
            {"file_id": "b", "start_s": 110, "end_s": 230},
            {"file_id": "c", "start_s": 220, "end_s": 340},
        ])
        self.assertIn("audition_required", report["transitions"][0]["warnings"])
        self.assertEqual((tracks, value), original)

    def test_no_invented_windows_or_implicit_zero_overlap(self):
        tracks = [track("a"), track("b")]
        report = transition_report(tracks)
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["estimated_elapsed_s"])
        self.assertIsNone(report["play_window_subtotal_s"])
        value = playback(("a", "b"), 0)
        value["overlaps"] = []
        report = transition_report(tracks, value)
        self.assertIsNone(report["estimated_elapsed_s"])
        self.assertEqual(report["play_window_subtotal_s"], 240)
        self.assertIn("missing_explicit_overlap", [item["code"] for item in report["issues"]])
        value = playback(("a", "b"), 0)
        self.assertEqual(transition_report(tracks, value)["estimated_elapsed_s"], 240)
        self.assertEqual(transition_report([track("a")], playback(("a",)))["estimated_elapsed_s"], 120)
        self.assertIsNone(transition_report([])["estimated_elapsed_s"])

    def test_stale_unreviewed_out_of_range_unknown_duration_fail_closed(self):
        cases = [
            (window("a", content_hash="b" * 64), track("a"), "window_hash_mismatch"),
            (window("a", reviewed=False), track("a"), "window_not_reviewed"),
            (window("a", exit_s=181), track("a"), "window_outside_file"),
            (window("a"), track("a", duration_s=None), "unknown_full_duration"),
        ]
        for selected, record, code in cases:
            with self.subTest(code=code):
                report = transition_report([record], {"windows": [selected], "overlaps": []})
                self.assertIsNone(report["estimated_elapsed_s"])
                self.assertEqual(report["issues"][0]["code"], code)

    def test_each_and_combined_overlap_bounds(self):
        for seconds, expected in ((60, "estimated"), (60.001, "incomplete"), (120, "incomplete"), (121, "incomplete")):
            with self.subTest(seconds=seconds):
                report = transition_report([track(file_id) for file_id in ("a", "b", "c")],
                                           playback(seconds=seconds))
                self.assertEqual(report["status"], expected)
                if seconds > 60:
                    self.assertIn("combined_overlaps_exceed_window", [item["code"] for item in report["issues"]])
                if seconds > 120:
                    self.assertIn("overlap_exceeds_window", [item["code"] for item in report["issues"]])
        self.assertEqual(transition_report([track("a"), track("b")],
                                          playback(("a", "b"), 120))["estimated_elapsed_s"], 120)

    def test_decimal_exact_arithmetic_and_unused_pairs_visible(self):
        value = {"windows": [window("a", entry_s=0, exit_s=0.1),
                             window("b", entry_s=0, exit_s=0.2),
                             window("unused")],
                 "overlaps": [{"from_id": "a", "to_id": "b", "seconds": 0.1},
                              {"from_id": "b", "to_id": "a", "seconds": 100}]}
        report = transition_report([track("a"), track("b")], value)
        self.assertEqual(report["estimated_elapsed_s"], 0.2)
        self.assertEqual(report["unused_window_ids"], ["unused"])
        self.assertEqual(report["unused_overlap_pairs"], [["b", "a"]])
        value["overlaps"][0]["seconds"] = 0.10000000000000002
        self.assertIsNone(transition_report([track("a"), track("b")], value)["estimated_elapsed_s"])

    def test_metadata_and_analysis_warnings_do_not_become_safety_claims(self):
        evidence = {"reviewed": False, "error": "grid error", "flags": ["vocal overlap"],
                    "grid_diagnostics": {"status": "needs_review"},
                    "cues": {"A": {"mix_window_evidence": {"status": "vocal_overlap"}}},
                    "mix_window_evidence": {"intro": {"status": "candidate"}}}
        report = transition_report([track("a", bpm=None, key=None, cue_evidence=evidence),
                                    track("b", bpm=125)], playback(("a", "b")))
        card = report["transitions"][0]
        self.assertIsNone(card["tempo_change_percent"])
        self.assertIn("unknown_bpm", card["warnings"])
        self.assertIn("unknown_key", card["warnings"])
        self.assertIn("outgoing_cue_analysis_unreviewed", card["warnings"])
        self.assertIn("outgoing_grid_diagnostics_require_review", card["warnings"])
        self.assertIn("outgoing_detected_vocal_activity_in_candidate_window", card["warnings"])
        self.assertIn("outgoing_candidate_stem_windows_require_audition", card["warnings"])
        self.assertEqual(report["status"], "estimated")  # Arithmetic, not musical certification.
        self.assertEqual(report["tracks"][0]["cue_evidence"], evidence)

    def test_invalid_playback_shapes_types_and_ranges(self):
        invalid = [None, {}, {"windows": [], "overlaps": [], "safe": True},
                   {"windows": [], "overlaps": None}]
        for changes in ({"entry_s": -1}, {"entry_s": True}, {"exit_s": "130"},
                        {"exit_s": float("inf")}, {"entry_s": 130}, {"reviewed": 1},
                        {"file_id": ""}, {"content_hash": "ABC"}, {"cue_slot": "A"}):
            invalid.append({"windows": [window("a", **changes)] if "file_id" not in changes else
                            [window(changes["file_id"])], "overlaps": []})
        invalid += [{"windows": [window("a"), window("a")], "overlaps": []},
                    {"windows": [], "overlaps": [{"from_id": "a", "to_id": "a", "seconds": 0}]},
                    {"windows": [], "overlaps": [{"from_id": "a", "to_id": "b", "seconds": -1}]},
                    {"windows": [window(str(index)) for index in range(61)], "overlaps": []}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_playback(value)
