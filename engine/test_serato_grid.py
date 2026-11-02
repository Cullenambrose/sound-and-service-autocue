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
        # Use 90 BPM vs 124 BPM - disagrees but not 2x or 0.5x
        grid, note = serato_grid.reconcile(self.detected, [{"pos": 0.2, "bpm": 90.0}])
        self.assertEqual(grid["source"], "serato_beatgrid")
        self.assertEqual((grid["t1"], grid["bpm"]), (0.2, 90.0))
        self.assertAlmostEqual(grid["bar_s"], 240 / 90.0, places=6)
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

    def test_m4a_beatgrid_at_84_bpm_is_read_correctly(self):
        """M4A with Serato BeatGrid at 84 BPM should be read with source=serato_beatgrid."""
        import base64
        from mutagen.mp4 import MP4, MP4FreeForm
        from serato_tags import MP4_BEATGRID_KEY
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import fixtures
        
        payload = b"application/octet-stream\x00\x00Serato BeatGrid\x00" + frame([{"pos": 0.25, "bpm": 84.0}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "84bpm.m4a"
            path.write_bytes(fixtures.m4a())
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            audio[MP4_BEATGRID_KEY] = [MP4FreeForm(base64.b64encode(payload).rstrip(b"="))]
            audio.save()
            
            markers = serato_grid.read_beatgrid(path)
            self.assertIsNotNone(markers)
            self.assertEqual(len(markers), 1)
            self.assertAlmostEqual(markers[0]["pos"], 0.25, places=3)
            self.assertAlmostEqual(markers[0]["bpm"], 84.0, places=2)
            
            # Reconcile with a detected grid and verify source
            detected = {"source": "beat_this_downbeats", "t1": 0.2, "bar_s": 2.857, "bpm": 84.0, "offset_ms": 0.0}
            grid, note = serato_grid.reconcile(detected, markers)
            self.assertEqual(grid["source"], "serato_beatgrid")
            self.assertAlmostEqual(grid["bpm"], 84.0, places=1)

    def test_m4a_beatgrid_with_newlines_in_base64_is_decoded(self):
        """M4A BeatGrid with newlines in base64 should decode correctly."""
        import base64
        from mutagen.mp4 import MP4, MP4FreeForm
        from serato_tags import MP4_BEATGRID_KEY
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import fixtures
        
        payload = b"application/octet-stream\x00\x00Serato BeatGrid\x00" + frame([{"pos": 1.0, "bpm": 120.0}])
        encoded = base64.b64encode(payload)
        # Add newlines every 64 chars (as Serato sometimes does)
        encoded_with_newlines = b"\n".join([encoded[i:i+64] for i in range(0, len(encoded), 64)])
        
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "newlines.m4a"
            path.write_bytes(fixtures.m4a())
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            audio[MP4_BEATGRID_KEY] = [MP4FreeForm(encoded_with_newlines)]
            audio.save()
            
            markers = serato_grid.read_beatgrid(path)
            self.assertIsNotNone(markers)
            self.assertAlmostEqual(markers[0]["bpm"], 120.0, places=2)

    def test_m4a_beatgrid_without_padding_is_decoded(self):
        """M4A BeatGrid without base64 padding should decode correctly."""
        import base64
        from mutagen.mp4 import MP4, MP4FreeForm
        from serato_tags import MP4_BEATGRID_KEY
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import fixtures
        
        payload = b"application/octet-stream\x00\x00Serato BeatGrid\x00" + frame([{"pos": 0.5, "bpm": 128.0}])
        encoded = base64.b64encode(payload).rstrip(b"=")  # Strip padding
        
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nopad.m4a"
            path.write_bytes(fixtures.m4a())
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            audio[MP4_BEATGRID_KEY] = [MP4FreeForm(encoded)]
            audio.save()
            
            markers = serato_grid.read_beatgrid(path)
            self.assertIsNotNone(markers)
            self.assertAlmostEqual(markers[0]["bpm"], 128.0, places=2)

    def test_m4a_malformed_envelope_reports_decode_failure(self):
        """M4A BeatGrid with wrong envelope header should report decode failed."""
        import base64
        from mutagen.mp4 import MP4, MP4FreeForm
        from serato_tags import MP4_BEATGRID_KEY
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import fixtures
        
        # Wrong envelope header
        payload = b"wrong/header\x00\x00Serato BeatGrid\x00" + frame([{"pos": 0.5, "bpm": 128.0}])
        
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad_envelope.m4a"
            path.write_bytes(fixtures.m4a())
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            audio[MP4_BEATGRID_KEY] = [MP4FreeForm(base64.b64encode(payload))]
            audio.save()
            
            messages = []
            markers = serato_grid.read_beatgrid(path, messages.append)
            self.assertIsNone(markers)
            self.assertTrue(any("decode failed" in msg for msg in messages))

    def test_m4a_malformed_binary_reports_parse_failure(self):
        """M4A BeatGrid with malformed binary should report parse failed."""
        import base64
        from mutagen.mp4 import MP4, MP4FreeForm
        from serato_tags import MP4_BEATGRID_KEY
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import fixtures
        
        # Correct envelope but malformed BeatGrid binary
        payload = b"application/octet-stream\x00\x00Serato BeatGrid\x00\xff\xff\xff\xff"
        
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad_binary.m4a"
            path.write_bytes(fixtures.m4a())
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            audio[MP4_BEATGRID_KEY] = [MP4FreeForm(base64.b64encode(payload))]
            audio.save()
            
            messages = []
            markers = serato_grid.read_beatgrid(path, messages.append)
            self.assertIsNone(markers)
            self.assertTrue(any("parse failed" in msg for msg in messages))

    def test_m4a_missing_key_reports_key_missing(self):
        """M4A without BeatGrid key should report key missing."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import fixtures
        
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "no_key.m4a"
            path.write_bytes(fixtures.m4a())
            # Don't add any tags
            
            messages = []
            markers = serato_grid.read_beatgrid(path, messages.append)
            self.assertIsNone(markers)
            # Should report either "no tags" or "key missing"
            self.assertTrue(any("no tags" in msg.lower() or "key missing" in msg.lower() for msg in messages))

    def test_mp3_and_m4a_parity_for_same_beatgrid(self):
        """MP3 GEOB and M4A freeform should give identical results for the same BeatGrid."""
        import base64
        from mutagen.id3 import ID3, GEOB
        from mutagen.mp4 import MP4, MP4FreeForm
        from serato_tags import MP4_BEATGRID_KEY
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import fixtures
        
        beatgrid_binary = frame([{"pos": 1.5, "bpm": 100.0}])
        
        with tempfile.TemporaryDirectory() as directory:
            # Create MP3 with GEOB
            mp3_path = Path(directory) / "test.mp3"
            tags = ID3()
            tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato BeatGrid", data=beatgrid_binary))
            tags.save(mp3_path)
            mp3_markers = serato_grid.read_beatgrid(mp3_path)
            
            # Create M4A with freeform
            m4a_path = Path(directory) / "test.m4a"
            m4a_path.write_bytes(fixtures.m4a())
            payload = b"application/octet-stream\x00\x00Serato BeatGrid\x00" + beatgrid_binary
            audio = MP4(m4a_path)
            if audio.tags is None:
                audio.add_tags()
            audio[MP4_BEATGRID_KEY] = [MP4FreeForm(base64.b64encode(payload).rstrip(b"="))]
            audio.save()
            m4a_markers = serato_grid.read_beatgrid(m4a_path)
            
            # Both should give identical markers
            self.assertEqual(mp3_markers, m4a_markers)
            self.assertAlmostEqual(mp3_markers[0]["bpm"], 100.0, places=2)

    def test_double_time_detection_reports_likely_error(self):
        """When detected tempo is ~2x Serato, report as likely detection error."""
        markers = [{"pos": 0.5, "bpm": 84.0}]
        # Detected grid is approximately double-time (168 BPM vs 84 BPM)
        detected = {"source": "beat_this_downbeats", "t1": 0.5, "bar_s": 240.0 / 168.0, "bpm": 168.0, "offset_ms": 0.0}
        
        grid, note = serato_grid.reconcile(detected, markers)
        self.assertEqual(grid["source"], "serato_beatgrid")
        self.assertAlmostEqual(grid["bpm"], 84.0, places=1)
        self.assertIn("double-time", note.lower())
        self.assertIn("detection error", note.lower())

    def test_half_time_detection_reports_likely_error(self):
        """When detected tempo is ~0.5x Serato, report as likely detection error."""
        markers = [{"pos": 0.5, "bpm": 166.0}]
        # Detected grid is approximately half-time (83 BPM vs 166 BPM)
        detected = {"source": "beat_this_downbeats", "t1": 0.5, "bar_s": 240.0 / 83.0, "bpm": 83.0, "offset_ms": 0.0}
        
        grid, note = serato_grid.reconcile(detected, markers)
        self.assertEqual(grid["source"], "serato_beatgrid")
        self.assertAlmostEqual(grid["bpm"], 166.0, places=1)
        self.assertIn("half-time", note.lower())
        self.assertIn("detection error", note.lower())

    def test_exact_double_time_case_84_vs_166(self):
        """Real-world case: Serato 84 BPM, detected 166.885 BPM (double-time)."""
        markers = [{"pos": 0.25, "bpm": 84.0}]
        # Detected is ~2x
        detected = {"source": "beat_this_downbeats", "t1": 0.2, "bar_s": 240.0 / 166.885, "bpm": 166.885, "offset_ms": 0.0}
        
        grid, note = serato_grid.reconcile(detected, markers)
        self.assertEqual(grid["source"], "serato_beatgrid")
        self.assertAlmostEqual(grid["bpm"], 84.0, places=1)
        self.assertFalse(grid["provenance"]["detected_tempo_agrees"])
        self.assertIn("double-time", note.lower())


if __name__ == "__main__":
    unittest.main()
