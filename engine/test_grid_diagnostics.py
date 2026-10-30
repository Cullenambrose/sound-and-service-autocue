import copy
import base64
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from serato_grid import diagnose, reconcile, read_beatgrid


class GridDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.grid = {"source": "serato_beatgrid", "t1": 0.017, "bar_s": 2.0, "offset_ms": 0}
        self.times = [0.017 + index * 2 for index in range(150)]

    def codes(self, result):
        return {issue["code"] for issue in result["issues"]}

    def test_exact_alignment_reports_all_three_regions_without_changing_inputs(self):
        original = copy.deepcopy(self.grid)
        times = list(self.times)
        result = diagnose(self.grid, times, 300)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual([region["status"] for region in result["regions"]], ["aligned"] * 3)
        self.assertEqual(result["accumulated_drift_s"], 0)
        self.assertEqual(result["aligned_fraction"], 1)
        self.assertEqual(self.grid, original)
        self.assertEqual(times, self.times)
        json.dumps(result, allow_nan=False)

    def test_offset_is_applied_and_constant_phase_error_is_not_mislabeled_drift(self):
        grid = {**self.grid, "t1": 0.217, "offset_ms": 200}
        self.assertEqual(diagnose(grid, self.times, 300)["status"], "aligned")
        result = diagnose(self.grid, [time + 0.5 for time in self.times], 300)
        self.assertIn("phase_disagreement", self.codes(result))
        self.assertNotIn("accumulated_drift", self.codes(result))

    def test_subpercent_tempo_difference_accumulates_past_phase_wraparound(self):
        times = [0.017 + index * 2.01 for index in range(300)]
        result = diagnose(self.grid, times, 604)
        self.assertIn("accumulated_drift", self.codes(result))
        self.assertGreater(result["accumulated_drift_s"], 2.5)
        self.assertNotIn("tempo_disagreement", self.codes(result))

    def test_late_change_not_hidden_by_perfect_majority_or_median_fit(self):
        times = [time if index < 130 else time + 0.3 for index, time in enumerate(self.times)]
        result = diagnose(self.grid, times, 300)
        self.assertEqual(result["regions"][0]["status"], "aligned")
        self.assertEqual(result["regions"][1]["status"], "aligned")
        self.assertEqual(result["regions"][2]["status"], "review_required")
        self.assertIn("accumulated_drift", self.codes(result))

    def test_missing_downbeats_can_skip_bars_without_inventing_drift(self):
        times = [time for index, time in enumerate(self.times) if index % 7]
        result = diagnose(self.grid, times, 300)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["accumulated_drift_s"], 0)

    def test_half_and_double_time_are_detected_even_when_downbeats_land_on_grid(self):
        for duration, code in ((4, "possible_double_time_grid"), (1, "possible_half_time_grid")):
            with self.subTest(duration=duration):
                result = diagnose(self.grid, [0.017 + index * duration for index in range(1, int(298 / duration))], 300)
                self.assertIn(code, self.codes(result))
                self.assertEqual(result["status"], "review_required")

    def test_ordinary_tempo_disagreement_is_not_diagnosed_as_half_time(self):
        result = diagnose(self.grid, [0.017 + index * 2.1 for index in range(140)], 300)
        self.assertIn("tempo_disagreement", self.codes(result))
        self.assertNotIn("possible_half_time_grid", self.codes(result))

    def test_incomplete_coverage_and_no_observations_never_report_aligned(self):
        for times in (None, [], self.times[:50]):
            with self.subTest(count=len(times or [])):
                self.assertEqual(diagnose(self.grid, times, 300)["status"], "unverified")

    def test_invalid_unordered_and_duplicate_observations_are_explicit_and_json_safe(self):
        result = diagnose(self.grid, self.times[::-1] + [None, True, math.nan, math.inf, -1, 900, self.times[0]], 300)
        self.assertIn("invalid_observations", self.codes(result))
        self.assertEqual(result["ignored_downbeats"], 7)
        json.dumps(result, allow_nan=False)

    def test_outlier_interval_does_not_create_a_trustworthy_accumulated_drift_estimate(self):
        result = diagnose(self.grid, sorted(self.times + [81.117]), 300)
        self.assertIn("irregular_intervals", self.codes(result))
        self.assertNotIn("accumulated_drift_s", result)

    def test_unsupported_variable_grid_is_visible_even_when_fallback_aligns_perfectly(self):
        markers = [{"pos": 0.017, "beats_to_next": 64}, {"pos": 32.017, "bpm": 140}]
        result = diagnose(self.grid, self.times, 300, markers)
        self.assertIn("unsupported_serato_grid", self.codes(result))
        self.assertEqual(result["status"], "review_required")

    def test_unsupported_marker_evidence_is_not_erased_by_sparse_downbeats(self):
        markers = [{"pos": 0.017, "beats_to_next": 64}, {"pos": 32.017, "bpm": 140}]
        result = diagnose(self.grid, [], 300, markers)
        self.assertIn("unsupported_serato_grid", self.codes(result))
        self.assertNotEqual(result["status"], "aligned")

    def test_small_marker_variation_cannot_hide_cumulative_error_in_flattened_grid(self):
        markers = [{"pos": 0.017, "beats_to_next": 200},
                   {"pos": 100.017, "beats_to_next": 200}, {"pos": 200.317, "bpm": 120}]
        chosen, _ = reconcile(self.grid, markers)
        self.assertEqual(chosen["source"], "serato_beatgrid")
        result = diagnose(chosen, self.times, 300, markers)
        self.assertIn("serato_marker_drift", self.codes(result))
        self.assertAlmostEqual(result["serato_marker_max_deviation_s"], 0.3, places=5)

    def test_final_marker_tempo_is_checked_through_the_end_of_audio(self):
        markers = [{"pos": 0.017, "beats_to_next": 64}, {"pos": 32.017, "bpm": 120.3}]
        result = diagnose(self.grid, self.times, 300, markers)
        self.assertIn("serato_marker_drift", self.codes(result))
        self.assertGreater(result["serato_marker_max_deviation_s"], 0.6)

    def test_threshold_uses_beats_not_fixed_seconds_at_different_tempos(self):
        for bpm in (60, 120, 200):
            bar_s = 240 / bpm
            grid = {**self.grid, "bar_s": bar_s}
            times = [0.017 + index * bar_s + 0.24 * bar_s / 4 for index in range(150)]
            self.assertEqual(diagnose(grid, times, 150 * bar_s)["status"], "aligned")
            times = [time + 0.02 * bar_s / 4 for time in times]
            self.assertIn("phase_disagreement", self.codes(diagnose(grid, times, 150 * bar_s)))

    def test_exact_quarter_beat_threshold_does_not_fail_from_float_roundoff(self):
        times = [time + 0.125 for time in self.times]
        result = diagnose(self.grid, times, 300)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["aligned_fraction"], 1)

    def test_invalid_ruler_or_duration_raises_explicit_error(self):
        for grid, duration in (({}, 300), ({**self.grid, "bar_s": 0}, 300),
                               (self.grid, 0), (self.grid, True), (self.grid, math.nan)):
            with self.subTest(grid=grid, duration=duration), self.assertRaises(ValueError):
                diagnose(grid, self.times, duration)


class M4ABeatGridTests(unittest.TestCase):
    def test_real_freeform_atom_wrapping_padding_and_detected_octaves_preserve_serato_ruler(self):
        from mutagen.mp4 import MP4, MP4FreeForm
        from serato_tags import ENVELOPE_PREFIX, MP4_BEATGRID_KEY
        from web.fixtures import m4a
        from serato_grid import parse_beatgrid

        binary = b"\x01\x00" + struct.pack(">Iff", 1, 0.045958, 84.0002) + b"\x00"
        encoded = base64.b64encode(ENVELOPE_PREFIX + b"Serato BeatGrid\x00" + binary)
        for text in (encoded, encoded.rstrip(b"="),
                     b"\r\n".join(encoded[index:index + 32] for index in range(0, len(encoded), 32))):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "grid.m4a"
                path.write_bytes(m4a())
                audio = MP4(path)
                audio.add_tags()
                audio[MP4_BEATGRID_KEY] = [MP4FreeForm(text)]
                audio.save()
                original = path.read_bytes()
                messages = []
                markers = read_beatgrid(path, messages.append)
                self.assertEqual(markers, parse_beatgrid(binary))
                self.assertEqual(messages, [])
                for detected_bpm in (166.885, 168.0004, 42.0001):
                    grid, _ = reconcile({"t1": 1.0, "bar_s": 240 / detected_bpm}, markers)
                    self.assertEqual(grid["source"], "serato_beatgrid")
                    self.assertAlmostEqual(grid["bpm"], 84.0002, delta=0.01)
                    self.assertAlmostEqual(grid["t1"], 0.045958, places=6)
                self.assertEqual(path.read_bytes(), original)

    def test_text_values_and_explicit_missing_decode_and_parse_diagnostics(self):
        from types import SimpleNamespace
        from serato_tags import ENVELOPE_PREFIX, MP4_BEATGRID_KEY
        binary = b"\x01\x00" + struct.pack(">Iff", 1, 0.5, 84.0) + b"\x00"
        valid = base64.b64encode(ENVELOPE_PREFIX + b"Serato BeatGrid\x00" + binary).decode("ascii")
        cases = [
            ({MP4_BEATGRID_KEY: [valid]}, None),
            ({}, "key missing"),
            ({MP4_BEATGRID_KEY: []}, "key missing"),
            ({MP4_BEATGRID_KEY: ["%%" + valid]}, "decode failed"),
            ({MP4_BEATGRID_KEY: ["abcde"]}, "decode failed"),
            ({MP4_BEATGRID_KEY: [base64.b64encode(b"wrong envelope")]}, "decode failed"),
            ({MP4_BEATGRID_KEY: [b"\xff"]}, "decode failed"),
            ({MP4_BEATGRID_KEY: [base64.b64encode(ENVELOPE_PREFIX + b"Serato BeatGrid\x00bad")]},
             "parse failed"),
        ]
        for tags, failure in cases:
            with self.subTest(failure=failure), patch("mutagen.mp4.MP4", return_value=SimpleNamespace(tags=tags)):
                messages = []
                markers = read_beatgrid("synthetic.m4a", messages.append)
                if failure:
                    self.assertIsNone(markers)
                    self.assertEqual(len(messages), 1)
                    self.assertIn(failure, messages[0])
                    self.assertIn(MP4_BEATGRID_KEY, messages[0])
                else:
                    self.assertEqual(markers, [{"pos": 0.5, "bpm": 84.0}])
                    self.assertEqual(messages, [])
