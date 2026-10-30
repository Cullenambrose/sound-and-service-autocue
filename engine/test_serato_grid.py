import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serato_grid


def frame(markers):
    data = b"\x01\x00" + struct.pack(">I", len(markers))
    for marker in markers:
        data += struct.pack(">f", marker["pos"])
        data += struct.pack(">f", marker["bpm"]) if "bpm" in marker else struct.pack(">I", marker["beats_to_next"])
    return data + b"\x00"


class SeratoGridTests(unittest.TestCase):
    detected = {"source": "beat_this_downbeats", "t1": 2.15, "bar_s": 1.93548, "bpm": 124.0, "offset_ms": 0.0}

    def test_parse_round_trips_single_marker(self):
        markers = serato_grid.parse_beatgrid(frame([{"pos": 0.25, "bpm": 124.0}]))
        self.assertEqual(markers, [{"pos": 0.25, "bpm": 124.0}])

    def test_malformed_frames_are_rejected(self):
        self.assertIsNone(serato_grid.parse_beatgrid(b"\x01\x00"))
        self.assertIsNone(serato_grid.parse_beatgrid(b"\x01\x00" + struct.pack(">I", 5) + b"\x00" * 8))

    def test_matching_tempo_adopts_serato_grid(self):
        grid, note = serato_grid.reconcile(self.detected, [{"pos": 0.2145, "bpm": 124.0}])
        self.assertEqual(grid["source"], "serato_beatgrid")
        self.assertAlmostEqual(grid["t1"], 0.2145, places=3)
        self.assertAlmostEqual(grid["bar_s"], 240 / 124.0, places=4)
        self.assertIn("used the Serato beatgrid", note)

    def test_tempo_disagreement_still_uses_serato_grid_as_ruler(self):
        grid, note = serato_grid.reconcile(self.detected, [{"pos": 0.2, "bpm": 62.0}])
        self.assertEqual(grid["source"], "serato_beatgrid")
        self.assertEqual((grid["t1"], grid["bpm"]), (0.2, 62.0))
        self.assertAlmostEqual(grid["bar_s"], 240 / 62.0, places=6)
        self.assertFalse(grid["provenance"]["detected_tempo_agrees"])
        self.assertIn("disagrees", note)

    def test_tempo_changing_grid_is_ignored(self):
        markers = [{"pos": 0.0, "beats_to_next": 64}, {"pos": 30.0, "bpm": 140.0}]
        grid, note = serato_grid.reconcile(self.detected, markers)
        self.assertIs(grid, self.detected)
        self.assertIn("changes tempo", note)

    def test_constant_multi_marker_grid_is_used(self):
        beat_s = 60 / 124.0
        markers = [{"pos": 0.2, "beats_to_next": 64}, {"pos": 0.2 + 64 * beat_s, "bpm": 124.0}]
        grid, _ = serato_grid.reconcile(self.detected, markers)
        self.assertEqual(grid["source"], "serato_beatgrid")

    def test_file_without_tags_returns_none(self):
        messages = []
        self.assertIsNone(serato_grid.read_beatgrid("/nonexistent/file.mp3", messages.append))
        self.assertTrue(any("Could not read" in message for message in messages))

    def test_nonfinite_negative_and_unordered_binary_markers_are_rejected(self):
        for markers in ([{"pos": float("nan"), "bpm": 124}],
                        [{"pos": float("inf"), "bpm": 124}],
                        [{"pos": -1, "bpm": 124}],
                        [{"pos": 0, "bpm": float("inf")}],
                        [{"pos": 0, "bpm": float("nan")}],
                        [{"pos": 0, "beats_to_next": 0}, {"pos": 2, "bpm": 124}],
                        [{"pos": 2, "beats_to_next": 4}, {"pos": 1, "bpm": 124}]):
            with self.subTest(markers=markers):
                self.assertIsNone(serato_grid.parse_beatgrid(frame(markers)))
        self.assertIsNone(serato_grid.parse_beatgrid(b"\x02\x00" + frame([{"pos": 0, "bpm": 124}])[2:]))

    def test_invalid_external_markers_report_reason_without_poisoning_grid(self):
        for markers in ([{"pos": float("nan"), "bpm": 124}], [{"pos": 0, "bpm": 0}],
                        [{"pos": 0}], [{"pos": "invalid", "bpm": 124}],
                        [{"pos": 0, "beats_to_next": "four"}, {"pos": 2, "bpm": 124}]):
            with self.subTest(markers=markers):
                grid, reason = serato_grid.reconcile(self.detected, markers)
                self.assertIs(grid, self.detected)
                self.assertTrue(reason)

    def test_actual_id3_tag_is_read_without_modifying_file(self):
        from mutagen.id3 import ID3, GEOB
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tagged.mp3"
            tags = ID3()
            tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato BeatGrid",
                          data=frame([{"pos": 0.25, "bpm": 124}])))
            tags.save(path)
            original = path.read_bytes()
            markers = serato_grid.read_beatgrid(path)
            self.assertEqual(markers, [{"pos": 0.25, "bpm": 124}])
            self.assertEqual(path.read_bytes(), original)
            grid, _ = serato_grid.reconcile(self.detected, markers)
            from rules_v6 import build_plan
            plan = build_plan("tagged", {"grid": grid, "segments": []}, {"cues": {}}, 128)
            self.assertEqual(plan["grid"]["source"], "serato_beatgrid")
            self.assertEqual(plan["grid"]["bpm"], 124)
            self.assertEqual(plan["grid"]["provenance"]["marker_count"], 1)
            self.assertFalse(plan["grid"]["provenance"]["user_verified"])

    def test_m4a_serato_beatgrid_is_read_without_modifying_file(self):
        import base64
        from mutagen.mp4 import MP4, MP4FreeForm
        from serato_tags import MP4_BEATGRID_KEY
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import fixtures
        payload = b"application/octet-stream\x00\x00Serato BeatGrid\x00" + frame([{"pos": 0.5, "bpm": 88.5}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tagged.m4a"
            path.write_bytes(fixtures.m4a())
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            audio[MP4_BEATGRID_KEY] = [MP4FreeForm(base64.b64encode(payload).rstrip(b"="))]
            audio.save()
            original = path.read_bytes()
            self.assertEqual(serato_grid.read_beatgrid(path), [{"pos": 0.5, "bpm": 88.5}])
            self.assertEqual(path.read_bytes(), original)
            audio[MP4_BEATGRID_KEY] = [MP4FreeForm(base64.b64encode(b"wrong header"))]
            audio.save()
            messages = []
            self.assertIsNone(serato_grid.read_beatgrid(path, messages.append))
            self.assertTrue(any("decode failed" in message for message in messages))

    def test_unsupported_format_has_explicit_diagnostic(self):
        messages = []
        self.assertIsNone(serato_grid.read_beatgrid("track.flac", messages.append))
        self.assertTrue(any("MP3" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
