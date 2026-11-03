import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset_splits
import feature_cache
import library_inventory as inventory

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, "NumPy is required for feature extraction.")
class FeatureCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "music"
        self.root.mkdir()
        self.database = self.base / "state" / "inventory.sqlite3"
        self.dataset = self.base / "dataset.json"
        self.cache = self.base / "feature-cache"

    def tearDown(self):
        self.temporary.cleanup()

    def audio(self, relative="track.wav", frequency=220):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(44100)
            frames = []
            for index in range(44100 * 4):
                sample = int(8000 * math.sin(2 * math.pi * frequency * index / 44100))
                frames.append((sample, sample))
                if len(frames) == 4096:
                    output.writeframes(b"".join(int(value).to_bytes(2, "little", signed=True)
                                                 for frame in frames for value in frame))
                    frames.clear()
            if frames:
                output.writeframes(b"".join(int(value).to_bytes(2, "little", signed=True)
                                             for frame in frames for value in frame))
        return path

    def prepare(self, names=("track.wav",)):
        for index, name in enumerate(names):
            self.audio(name, 220 + 220 * index)
        scan_id = inventory.create_scan(self.database, [self.root])
        self.assertEqual(inventory.resume_scan(self.database, scan_id)["status"], "complete")
        manifest = dataset_splits.build_manifest(
            self.database, scan_id, self.dataset, seed="feature-test")
        return json.loads(self.dataset.read_text()), manifest

    def grid_file(self, manifest, *, t1=0.35, offset_ms=100, source="fixture_grid"):
        grid = {
            track["audio_sha256"]: {
                "t1": t1, "bar_s": 2.0, "offset_ms": offset_ms,
                "source": source, "confidence": 0.9, "human_verified": True,
            }
            for track in manifest["tracks"]
        }
        path = self.base / "grids.json"
        path.write_text(json.dumps(grid))
        return path

    @staticmethod
    def copy_wav_from_fd(command, **_options):
        source = command[command.index("-i") + 1]
        descriptor = int(source.rsplit("/", 1)[1])
        with os.fdopen(os.dup(descriptor), "rb") as incoming, open(command[-1], "wb") as output:
            shutil.copyfileobj(incoming, output)
        return subprocess.CompletedProcess(command, 0, "", "")

    def run_cache(self, grids, **options):
        with patch.object(feature_cache.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                patch.object(feature_cache, "_ffmpeg_version",
                             return_value=("/usr/bin/ffmpeg", "fixture ffmpeg")), \
                patch.object(feature_cache.subprocess, "run", side_effect=self.copy_wav_from_fd):
            return feature_cache.run_cache(
                self.database, self.cache, self.dataset, grid_path=grids, **options)

    def test_extracts_versioned_grid_aligned_features_and_quality_flags(self):
        manifest, _ = self.prepare()
        grids = self.grid_file(manifest)
        original = (self.root / "track.wav").read_bytes()

        result = self.run_cache(grids)

        self.assertEqual(result["counts"], {"done": 1})
        self.assertEqual(result["attempted_this_run"], 1)
        with sqlite3.connect(self.cache / "feature-cache.sqlite3") as db:
            row = db.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row[2], "done")
        artifact = self.cache / row[6]
        self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), row[7])
        with np.load(artifact) as features:
            self.assertEqual(features["bar_index"].tolist(), [-1, 0, 1])
            self.assertAlmostEqual(float(features["bar_start_s"][1]), 0.25, places=3)
            self.assertGreater(float(features["rms"][1]), 0)
            quality = json.loads(str(features["quality_json"]))
        self.assertEqual(quality["extractor_version"], "bar-energy-v1")
        self.assertEqual(quality["base_feature_version"], "analyze-compute-features-50fps-v1")
        self.assertEqual(quality["grid_source"], "fixture_grid")
        self.assertEqual(quality["ffmpeg_version"], "fixture ffmpeg")
        self.assertTrue(quality["grid_human_verified"])
        self.assertIn("partial_bar_coverage", quality["flags"])
        self.assertEqual((self.root / "track.wav").read_bytes(), original)

    def test_limit_checkpoints_and_resume_processes_only_queued_tracks(self):
        manifest, _ = self.prepare(("one.wav", "two.wav"))
        grids = self.grid_file(manifest)

        paused = self.run_cache(grids, limit=1)
        with sqlite3.connect(self.cache / "feature-cache.sqlite3") as db:
            db.execute("UPDATE jobs SET state='running' WHERE state='queued'")
        resumed = self.run_cache(grids)

        self.assertEqual(paused["attempted_this_run"], 1)
        self.assertEqual(paused["counts"].get("queued"), 1)
        self.assertEqual(resumed["attempted_this_run"], 1)
        self.assertEqual(resumed["counts"], {"done": 2})
        self.assertEqual(feature_cache.cache_status(self.cache)["counts"], {"done": 2})

    def test_absent_grid_is_explicitly_skipped_and_no_grid_cache_is_resumable(self):
        self.prepare()
        result = self.run_cache(None)

        self.assertEqual(result["counts"], {"skipped": 1})
        with sqlite3.connect(self.cache / "feature-cache.sqlite3") as db:
            row = db.execute("SELECT state,quality_json FROM jobs").fetchone()
        self.assertEqual(row[0], "skipped")
        self.assertIn("no_usable_grid", row[1])
        self.assertEqual(result["attempted_this_run"], 0)

    def test_imported_constant_serato_grid_is_used_but_not_marked_verified(self):
        metadata = {"imported_anchors": {"sources": [{
            "format": "serato_beatgrid_v1.0", "status": "parsed",
            "records": [{"pos": 0.25, "bpm": 120.0}],
        }]}}

        grid, reason = feature_cache._imported_grid(metadata)

        self.assertIsNone(reason)
        self.assertEqual(grid["source"], "serato_beatgrid_unverified")
        self.assertAlmostEqual(grid["t1"], 0.25)
        self.assertAlmostEqual(grid["bar_s"], 2.0)
        self.assertFalse(grid["human_verified"])
        variable, diagnostic = feature_cache._imported_grid({"imported_anchors": {"sources": [{
            "format": "serato_beatgrid_v1.0", "status": "parsed",
            "records": [{"pos": 0.0, "beats_to_next": 64}, {"pos": 30.0, "bpm": 140.0}],
        }]}})
        self.assertIsNone(variable)
        self.assertIn("unusable_imported_grid", diagnostic)

    def test_invalid_grid_and_changed_cache_signature_fail_explicitly(self):
        manifest, _ = self.prepare()
        invalid = self.base / "invalid-grids.json"
        invalid.write_text(json.dumps({manifest["tracks"][0]["audio_sha256"]: {"t1": 0, "bar_s": 0}}))
        with self.assertRaisesRegex(ValueError, "bar_s"):
            feature_cache.run_cache(self.database, self.cache, self.dataset, grid_path=invalid)
        self.assertFalse(self.cache.exists())
        invalid.write_text(json.dumps({"0" * 64: {"t1": 0.25, "bar_s": 2.0}}))
        with self.assertRaisesRegex(ValueError, "absent from the frozen dataset"):
            feature_cache.run_cache(self.database, self.cache, self.dataset, grid_path=invalid)
        self.assertFalse(self.cache.exists())

        first_grid = self.grid_file(manifest, source="first")
        self.run_cache(first_grid, limit=1)
        changed_grid = self.grid_file(manifest, source="second")
        with self.assertRaisesRegex(ValueError, "inputs/settings changed"):
            self.run_cache(changed_grid)

    def test_changed_audio_is_not_cached_and_cache_location_is_checked(self):
        manifest, _ = self.prepare()
        grids = self.grid_file(manifest)
        path = self.root / "track.wav"
        path.write_bytes(path.read_bytes() + b"changed")
        result = self.run_cache(grids)
        self.assertEqual(result["counts"], {"error": 1})
        self.assertIn("changed after inventory", result["errors"][0]["error"])

        nested = self.root / "cache"
        with self.assertRaisesRegex(ValueError, "outside every audio source root"):
            feature_cache.run_cache(self.database, nested, self.dataset, grid_path=grids)

    def test_failed_track_is_reported_and_retried_only_when_requested(self):
        manifest, _ = self.prepare()
        grids = self.grid_file(manifest)
        with patch.object(feature_cache.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                patch.object(feature_cache.subprocess, "run",
                             side_effect=subprocess.CalledProcessError(1, ["ffmpeg"], stderr="bad audio")):
            with patch.object(feature_cache, "_ffmpeg_version",
                               return_value=("/usr/bin/ffmpeg", "fixture ffmpeg")):
                first = feature_cache.run_cache(self.database, self.cache, self.dataset, grid_path=grids)
                second = feature_cache.run_cache(self.database, self.cache, self.dataset, grid_path=grids)
        self.assertEqual(first["counts"], {"error": 1})
        self.assertIn("bad audio", first["errors"][0]["error"])
        self.assertEqual(second["attempted_this_run"], 0)
        self.assertEqual(second["counts"], {"error": 1})

        retried = self.run_cache(grids, retry_errors=True)
        self.assertEqual(retried["counts"], {"done": 1})
        self.assertEqual(retried["attempted_this_run"], 1)

    def test_status_does_not_create_a_missing_cache(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            feature_cache.cache_status(self.cache)
        self.assertFalse(self.cache.exists())

    @unittest.skipUnless(feature_cache.shutil.which("ffmpeg"), "ffmpeg is required for decoder integration.")
    def test_ffmpeg_decodes_from_the_secured_source_descriptor(self):
        manifest, _ = self.prepare()
        grids = self.grid_file(manifest)

        result = feature_cache.run_cache(self.database, self.cache, self.dataset, grid_path=grids)

        self.assertEqual(result["counts"], {"done": 1})
        with sqlite3.connect(self.cache / "feature-cache.sqlite3") as db:
            artifact = db.execute("SELECT artifact_path FROM jobs").fetchone()[0]
        with np.load(self.cache / artifact) as features:
            self.assertGreater(len(features["bar_index"]), 0)


if __name__ == "__main__":
    unittest.main()
