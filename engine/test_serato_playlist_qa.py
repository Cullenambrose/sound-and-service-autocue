import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import serato_playlist_qa as qa
from scripts.apply_single_track_qa import Refused
from web import fixtures, server


class SeratoPlaylistQATests(unittest.TestCase):
    def setUp(self):
        self.saved = (server.DATA, server.UPLOADS, server.DB_PATH, server.SESSION_TOKEN)
        self.addCleanup(self.restore)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "qa"

    def restore(self):
        server.DATA, server.UPLOADS, server.DB_PATH, server.SESSION_TOKEN = self.saved

    def generate(self, directory):
        from mutagen.id3 import ID3, TIT2, TPE1
        directory.mkdir(parents=True)
        path = directory / qa.cue_loop_fixture.NAME
        path.write_bytes(fixtures.mp3())
        tags = ID3()
        tags.add(TIT2(encoding=3, text=["AutoCue Synthetic Cue Loop QA"]))
        tags.add(TPE1(encoding=3, text=["AutoCue Test Fixture"]))
        tags.save(path)
        (directory / qa.cue_loop_fixture.MANIFEST).write_text(json.dumps({
            "schema_version": 1, "file": path.name, "audio_sha256": qa.serato_writer.mp3_audio_sha256(path)}))
        return path

    def create(self):
        with patch.object(qa, "check_serato_closed"), patch.object(qa.cue_loop_fixture, "create", self.generate):
            return qa.create(self.root)

    def test_package_backs_up_all_generated_files_and_preserves_ordered_duplicate_playlist(self):
        output = self.create()
        record = json.loads((self.root / "acceptance.json").read_text())
        self.assertEqual(output.read_text().splitlines()[1:], record["expected_order"])
        self.assertEqual(record["expected_order"][0], record["expected_order"][2])
        self.assertNotEqual(record["expected_order"][0], record["expected_order"][1])
        for path in (self.root / "playback-copies").iterdir():
            self.assertEqual(path.read_bytes(), (self.root / "backup" / "playback-copies" / path.name).read_bytes())
        with patch.object(qa, "check_serato_closed"):
            result = qa.capture(self.root, "5.0-test", False, False, False)
            with self.assertRaises(FileExistsError):
                qa.capture(self.root, "5.0-test", True, True, True)
        report = json.loads(result.read_text())
        self.assertFalse(report["passed"])
        self.assertEqual(report["files"][0]["serato_frames"]["Serato Markers2"], None)
        with patch.object(qa, "check_serato_closed"), self.assertRaises(Refused):
            qa.create(self.root)

    def test_capture_is_read_only_and_requires_original_and_copy_backups(self):
        self.create()
        before = {path: path.read_bytes() for path in self.root.rglob("*.mp3")}
        with patch.object(qa, "check_serato_closed"):
            result = qa.capture(self.root, "5.0-test", True, True, True)
        self.assertTrue(json.loads(result.read_text())["passed"])
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*.mp3")})
        result.unlink()
        saved = next((self.root / "backup" / "playback-copies").iterdir())
        saved.write_bytes(b"changed")
        with patch.object(qa, "check_serato_closed"), self.assertRaisesRegex(Refused, "backup changed"):
            qa.capture(self.root, "5.0-test", True, True, True)
        self.assertFalse(result.exists())

    def test_changed_audio_unsafe_paths_and_open_serato_refuse_capture(self):
        self.create()
        record_path = self.root / "acceptance.json"
        record = json.loads(record_path.read_text())
        with patch.object(qa, "check_serato_closed", side_effect=Refused("Serato open")):
            with self.assertRaises(Refused):
                qa.capture(self.root, "5.0-test", True, True, True)
        record_path.write_text(json.dumps({**record, "original": "../outside.mp3"}))
        with patch.object(qa, "check_serato_closed"), self.assertRaisesRegex(Refused, "Original path"):
            qa.capture(self.root, "5.0-test", True, True, True)
        record_path.write_text(json.dumps(record))
        Path(record["expected_order"][0]).write_bytes(b"changed audio")
        with patch.object(qa, "check_serato_closed"), self.assertRaisesRegex(Refused, "audio changed"):
            qa.capture(self.root, "5.0-test", True, True, True)
