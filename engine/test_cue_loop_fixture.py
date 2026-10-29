import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import cue_loop_fixture as fixture
from scripts.apply_single_track_qa import Refused
import serato_writer


class CueLoopFixtureTests(unittest.TestCase):
    def test_capture_is_read_only_preserves_unknown_geob_and_never_overwrites_snapshot(self):
        from mutagen.id3 import ID3, GEOB
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            track = directory / fixture.NAME
            track.write_bytes(b"\xff\xfb" + b"synthetic audio bytes" * 100)
            tags = ID3()
            raw = b"\x01\xff\x00potential future format"
            tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Unknown Cue Loop Data", data=raw))
            tags.save(track)
            manifest = {"schema_version": 1, "file": fixture.NAME,
                        "audio_sha256": serato_writer.mp3_audio_sha256(track)}
            (directory / fixture.MANIFEST).write_text(json.dumps(manifest))
            before = track.read_bytes()
            with patch.object(fixture, "check_serato_closed"):
                output = fixture.snapshot(directory, "baseline")
                with self.assertRaises(FileExistsError):
                    fixture.snapshot(directory, "baseline")
            import base64
            frame = json.loads(output.read_text())["geob_frames"][0]
            self.assertEqual(base64.b64decode(frame["data_base64"]), raw)
            self.assertEqual(track.read_bytes(), before)

    def test_unknown_audio_or_fixture_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            track = directory / fixture.NAME
            track.write_bytes(b"not generated audio")
            (directory / fixture.MANIFEST).write_text(json.dumps({
                "schema_version": 1, "file": fixture.NAME, "audio_sha256": "wrong"}))
            with patch.object(fixture, "check_serato_closed"), self.assertRaisesRegex(Refused, "Audio does not match"):
                fixture.snapshot(directory, "baseline")
            self.assertFalse((directory / "baseline.json").exists())

    def test_existing_fixture_creation_refuses_without_overwrite(self):
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            track = directory / fixture.NAME
            track.write_bytes(b"existing")
            with patch.object(fixture, "check_serato_closed"), patch.object(fixture.shutil, "which", return_value="/ffmpeg"):
                with self.assertRaisesRegex(Refused, "already exists"):
                    fixture.create(directory)
            self.assertEqual(track.read_bytes(), b"existing")

    def test_open_serato_refuses_all_operations(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.object(fixture, "check_serato_closed", side_effect=Refused("Serato is open")):
                with self.assertRaises(Refused):
                    fixture.create(Path(home))
                with self.assertRaises(Refused):
                    fixture.snapshot(Path(home), "baseline")
