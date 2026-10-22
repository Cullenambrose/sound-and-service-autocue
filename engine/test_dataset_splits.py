import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE

import dataset_splits
import library_inventory as inventory


class DatasetSplitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "music"
        self.root.mkdir()
        self.database = self.base / "state" / "inventory.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def audio(self, relative, frames=8000):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0" * frames)
        return path

    def tag(self, path, title, artist="Example Artist"):
        tagged = WAVE(path)
        tagged.add_tags()
        tagged.tags.add(TIT2(encoding=3, text=[title]))
        tagged.tags.add(TPE1(encoding=3, text=[artist]))
        tagged.save()

    def scan(self):
        scan_id = inventory.create_scan(self.database, [self.root])
        result = inventory.resume_scan(self.database, scan_id)
        self.assertEqual(result["status"], "complete")
        return scan_id

    def freeze(self, scan_id, name, **options):
        output = self.base / name
        result = dataset_splits.build_manifest(self.database, scan_id, output, **options)
        return result, json.loads(output.read_text())

    def test_hash_deduplicates_exact_copies_and_keeps_all_source_paths(self):
        original = self.audio("one.wav")
        shutil.copyfile(original, self.root / "copy.wav")
        scan_id = self.scan()

        result, manifest = self.freeze(scan_id, "frozen.json")

        self.assertEqual(result["counts"]["audio_files_seen"], 2)
        self.assertEqual(result["counts"]["tracks_after_exact_deduplication"], 1)
        self.assertEqual(result["counts"]["exact_duplicate_files_removed"], 1)
        self.assertEqual(len(manifest["tracks"]), 1)
        self.assertEqual(len(manifest["tracks"][0]["paths"]), 2)
        self.assertEqual(manifest["tracks"][0]["audio_sha256"],
                         hashlib.sha256(original.read_bytes()).hexdigest())

    def test_alternate_mix_candidates_are_grouped_and_split_together(self):
        extended = self.audio("extended.wav", frames=8000)
        radio = self.audio("radio.wav", frames=9000)
        self.tag(extended, "Song Name (Extended Mix)")
        self.tag(radio, "Song Name - Radio Edit")
        scan_id = self.scan()

        _, manifest = self.freeze(scan_id, "frozen.json", ratios=(0.0001, 0.0001, 0.9998))

        self.assertEqual(len(manifest["groups"]), 1)
        self.assertEqual(manifest["groups"][0]["grouping_method"], "normalized_artist_title")
        self.assertEqual(manifest["groups"][0]["track_count"], 2)
        self.assertEqual({track["split"] for track in manifest["tracks"]}, {"test"})
        self.assertTrue(manifest["grouping"]["review_required"])

    def test_split_assignment_is_reproducible_and_seed_sensitive(self):
        for index in range(20):
            self.audio(f"{index}.wav", frames=8000 + index)
        scan_id = self.scan()
        _, first = self.freeze(scan_id, "first.json", seed="fixed")
        _, repeated = self.freeze(scan_id, "repeated.json", seed="fixed")
        _, different = self.freeze(scan_id, "different.json", seed="other")

        assignments = lambda manifest: {
            track["audio_sha256"]: track["split"] for track in manifest["tracks"]}
        self.assertEqual(assignments(first), assignments(repeated))
        self.assertNotEqual(assignments(first), assignments(different))
        self.assertEqual(
            {track["group_id"] for track in first["tracks"]},
            {group["group_id"] for group in first["groups"]},
        )

    def test_incomplete_scan_and_mutated_audio_are_rejected(self):
        path = self.audio("track.wav")
        self.audio("another.wav")
        scan_id = inventory.create_scan(self.database, [self.root])
        inventory.resume_scan(self.database, scan_id, limit=1)
        with self.assertRaisesRegex(ValueError, "must be complete"):
            dataset_splits.build_manifest(self.database, scan_id, self.base / "incomplete.json")

        self.assertEqual(inventory.resume_scan(self.database, scan_id)["status"], "complete")
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            dataset_splits.build_manifest(self.database, scan_id, self.base / "changed.json")
        self.assertFalse((self.base / "changed.json").exists())

    def test_manifest_is_never_overwritten_and_invalid_ratios_are_rejected(self):
        self.audio("track.wav")
        scan_id = self.scan()
        output = self.base / "frozen.json"
        output.write_text("keep")
        with self.assertRaises(FileExistsError):
            dataset_splits.build_manifest(self.database, scan_id, output)
        self.assertEqual(output.read_text(), "keep")

        for ratios in ((0.7, 0.2, 0.2), (1, 0, 0), (True, 0.2, 0.8)):
            with self.subTest(ratios=ratios), self.assertRaises(ValueError):
                dataset_splits.build_manifest(self.database, scan_id, self.base / "bad.json",
                                              ratios=ratios)

    def test_cli_freezes_manifest_and_reports_counts(self):
        self.audio("track.wav")
        scan_id = self.scan()
        output = self.base / "cli.json"
        command = [
            sys.executable, str(Path(dataset_splits.__file__)),
            "--database", str(self.database), "freeze", scan_id, "--out", str(output),
        ]

        result = subprocess.run(command, text=True, capture_output=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["counts"]["tracks_after_exact_deduplication"], 1)
        self.assertEqual(json.loads(output.read_text())["created_from_scan"], scan_id)


if __name__ == "__main__":
    unittest.main()
