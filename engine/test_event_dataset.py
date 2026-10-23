import json
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
import anchor_detector
import dataset_splits
import event_dataset
import feature_cache
import library_inventory as inventory

try:
    import numpy as np
except ImportError:
    np = None


def human_review(digest, *, state="complete", certainty="certain", time_s=4.25):
    return {
        "track_hash": digest, "source": "human_review", "state": state,
        "note": "Fixture", "grid": {}, "updated_at": "2026-10-22T12:00:00Z",
        "annotations": [{
            "id": 1, "source": "human_review", "event_type": "chorus", "certainty": certainty,
            "time_s": time_s, "bar": 999, "active": True, "retracted_at": None,
            "note": "By ear", "grid": {"t1": 1, "bar_s": 2, "offset_ms": -100},
            "created_at": "2026-10-22T12:00:00Z",
        }],
    }


def imported_metadata():
    return {"imported_anchors": {"sources": [{
        "format": "serato_markers2_v1.1", "status": "parsed",
        "field": "Serato Markers2", "sha256": "f" * 64,
        "records": [{"type": "CUE", "time_s": 8.25, "index": 3, "name": "First DROP"}],
    }]}}


@unittest.skipIf(np is None, "NumPy is required for cached features.")
class EventDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "music"
        self.root.mkdir()
        self.cache = self.base / "features"
        self.cache.mkdir()
        self.database = self.base / "inventory.sqlite3"
        self.dataset = self.base / "dataset.json"
        self.reviews = self.base / "reviews.json"
        self.arrays = {
            "bar_index": np.arange(10),
            "coverage": np.ones(10),
            "rms": np.asarray([0.1] * 4 + [0.5] * 6),
            "low_energy": np.asarray([0.1] * 4 + [0.5] * 6),
        }
        self.quality = {
            "grid_origin_s": 0.25, "bar_duration_s": 2.0, "audio_duration_s": 20.25,
            "grid_confidence": 0.9, "grid_human_verified": True, "flags": [],
        }
        self.digests = [character * 64 for character in "abcde"]
        self.document = {
            "schema_version": 1, "grouping": {"review_required": True},
            "tracks": [], "groups": [],
        }
        self.cached = []
        for index, digest in enumerate(self.digests):
            split = "validation" if index == 1 else "test" if index == 2 else "train"
            group = f"group-{index}"
            self.document["groups"].append({"group_id": group, "split": split})
            self.document["tracks"].append({
                "audio_sha256": digest, "group_id": group, "split": split,
            })
            self.cached.append((
                {"audio_sha256": digest, "metadata": imported_metadata()},
                {"state": "error" if index == 4 else "done", "artifact_sha256": "0" * 64,
                 "error": "Fixture extraction failure" if index == 4 else None, "quality_json": "{}"},
            ))
        self.dataset.write_text(json.dumps(self.document))
        self.review_doc = {"schema_version": 1, "reviews": [
            human_review(digest, state="skipped" if index == 3 else "complete")
            for index, digest in enumerate(self.digests)
        ]}
        self.reviews.write_text(json.dumps(self.review_doc))

    def build(self, output=None, *, limit=20):
        with patch.object(anchor_detector, "_load_feature_cache",
                          return_value=(self.cache, self.cached, [self.root], None)), \
                patch.object(anchor_detector, "_read_artifact",
                             return_value=(self.arrays, self.quality)) as reader:
            result = event_dataset.build_dataset(
                self.database, self.dataset, self.cache, self.reviews,
                output or self.base / "learning.json", limit=limit)
            return result, reader.call_args_list

    def test_combines_weak_and_human_labels_without_inventing_negative_targets(self):
        result, _ = self.build()
        document = json.loads(Path(result["path"]).read_text())
        track = document["tracks"][0]
        human = next(row for row in track["samples"] if row["bar"] == 2)
        weak = next(row for row in track["samples"] if row["bar"] == 4)
        unmarked = next(row for row in track["samples"] if row["bar"] == 0)
        self.assertEqual(human["event_target"], ["chorus"])
        self.assertTrue(human["eligible_for_training"])
        self.assertEqual(human["human_labels"][0]["time_s"], 4.25)
        self.assertEqual(human["human_labels"][0]["grid"]["offset_ms"], -100)
        self.assertEqual(human["human_labels"][0]["grid_distance_s"], 0)
        self.assertEqual(weak["supervision"], "weak_candidate")
        self.assertIsNone(weak["event_target"])
        self.assertTrue(weak["eligible_for_weak_learning"])
        self.assertEqual({label["source"] for label in weak["weak_labels"]},
                         {"audio_energy_change", "imported_anchor"})
        imported = next(label for label in weak["weak_labels"] if "marker" in label)
        self.assertEqual(imported["marker"]["name"], "First DROP")
        self.assertIsNone(imported["event_type"])
        self.assertEqual(unmarked["supervision"], "unlabeled")
        self.assertIsNone(unmarked["event_target"])
        self.assertEqual(document["counts"]["negative_bars"], 0)

    def test_test_groups_are_reserved_and_validation_cannot_be_training_data(self):
        result, calls = self.build()
        document = json.loads(Path(result["path"]).read_text())
        test = document["tracks"][2]
        self.assertEqual(test["status"], "reserved_test")
        self.assertNotIn("samples", test)
        self.assertNotIn("review_provenance", test)
        self.assertNotIn(self.digests[2], [call.args[2] for call in calls])
        validation = document["tracks"][1]
        human = next(row for row in validation["samples"] if row["bar"] == 2)
        self.assertTrue(human["eligible_for_validation"])
        self.assertFalse(human["eligible_for_training"])
        self.assertFalse(human["eligible_for_weak_learning"])
        self.assertEqual(document["tracks"][3]["status"], "excluded_review")
        self.assertEqual(document["tracks"][4]["status"], "feature_unavailable")

    def test_uncertain_retracted_and_incomplete_labels_do_not_become_targets(self):
        review = human_review(self.digests[0], certainty="uncertain")
        rows, _, _ = event_dataset._bar_samples(self.arrays, self.quality, {}, review)
        uncertain = next(row for row in rows if row["bar"] == 2)
        self.assertEqual(uncertain["supervision"], "uncertain")
        self.assertIsNone(uncertain["event_target"])
        review["annotations"][0]["active"] = False
        rows, _, _ = event_dataset._bar_samples(self.arrays, self.quality, {}, review)
        self.assertTrue(all(not row["human_labels"] for row in rows))
        review = human_review(self.digests[0], state="in_progress")
        rows, _, _ = event_dataset._bar_samples(self.arrays, self.quality, {}, review)
        incomplete = next(row for row in rows if row["bar"] == 2)
        self.assertEqual(incomplete["supervision"], "incomplete_review")
        self.assertIsNone(incomplete["event_target"])
        review["annotations"] = []
        rows, _, _ = event_dataset._bar_samples(self.arrays, self.quality, {}, review)
        self.assertTrue(all(row["event_target"] is None for row in rows))

    def test_partial_bars_are_masked_and_outside_labels_are_explicitly_unmapped(self):
        review = human_review(self.digests[0])
        for coverage, expected in ((0.949, False), (0.95, True)):
            with self.subTest(coverage=coverage):
                self.arrays["coverage"][2] = coverage
                rows, _, _ = event_dataset._bar_samples(self.arrays, self.quality, {}, review)
                human = next(row for row in rows if row["bar"] == 2)
                self.assertEqual(human["feature_usable"], expected)
        review["annotations"][0]["time_s"] = 1000
        _, unmapped, _ = event_dataset._bar_samples(self.arrays, self.quality, {}, review)
        self.assertEqual(unmapped[0]["reason"], "human_label_outside_audio")

    def test_bounded_build_is_deterministic_and_does_not_overwrite_inputs_or_outputs(self):
        result, calls = self.build(limit=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["counts"]["deferred_tracks"], 1)
        second, _ = self.build(self.base / "second.json", limit=1)
        self.assertEqual(second["sha256"], result["sha256"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.build(limit=1)
        with self.assertRaisesRegex(ValueError, "overwrite an input"):
            self.build(self.dataset)
        with self.assertRaisesRegex(ValueError, "outside audio source"):
            self.build(self.root / "dataset.json")
        with self.assertRaisesRegex(ValueError, "outside audio source"):
            self.build(self.cache / "dataset.json")

    def test_invalid_split_and_nonhuman_provenance_are_rejected(self):
        self.document["tracks"][1]["split"] = "train"
        self.dataset.write_text(json.dumps(self.document))
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.build()
        self.document["tracks"][1]["split"] = "validation"
        self.dataset.write_text(json.dumps(self.document))
        self.review_doc["reviews"][0]["annotations"][0]["source"] = "imported_anchor"
        self.reviews.write_text(json.dumps(self.review_doc))
        with self.assertRaisesRegex(ValueError, "human-reviewed annotations"):
            self.build()

    def test_ambiguous_annotation_history_is_rejected(self):
        review = human_review(self.digests[0])
        review["annotations"][0]["active"] = "true"
        with self.assertRaisesRegex(ValueError, "explicit active/retracted"):
            event_dataset._bar_samples(self.arrays, self.quality, {}, review)
        review["annotations"][0]["active"] = True
        review["annotations"][0]["retracted_at"] = "2026-10-22T13:00:00Z"
        with self.assertRaisesRegex(ValueError, "retraction timestamp"):
            event_dataset._bar_samples(self.arrays, self.quality, {}, review)

    def test_real_manifest_and_cache_flow_uses_verified_artifacts(self):
        source = self.root / "generated.wav"
        rate = 8000
        signal = (5000 * np.sin(2 * np.pi * 220 * np.arange(rate * 8) / rate)).astype("<i2")
        with wave.open(str(source), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(signal.tobytes())
        scan = inventory.create_scan(self.database, [self.root])
        self.assertEqual(inventory.resume_scan(self.database, scan)["status"], "complete")
        manifest_path = self.base / "real-manifest.json"
        dataset_splits.build_manifest(self.database, scan, manifest_path, seed="learning-test")
        manifest = json.loads(manifest_path.read_text())
        manifest["tracks"][0]["split"] = manifest["groups"][0]["split"] = "train"
        manifest_path.write_text(json.dumps(manifest))
        digest = manifest["tracks"][0]["audio_sha256"]
        grids = self.base / "grids.json"
        grids.write_text(json.dumps({digest: {
            "t1": 0.35, "bar_s": 2, "offset_ms": 100, "source": "fixture",
            "confidence": 0.9, "human_verified": True,
        }}))

        def copy_audio(command, **_options):
            descriptor = int(command[command.index("-i") + 1].rsplit("/", 1)[1])
            with os.fdopen(os.dup(descriptor), "rb") as incoming, open(command[-1], "wb") as output:
                shutil.copyfileobj(incoming, output)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(feature_cache, "_ffmpeg_version",
                          return_value=("/usr/bin/ffmpeg", "fixture ffmpeg")), \
                patch.object(feature_cache.subprocess, "run", side_effect=copy_audio):
            summary = feature_cache.run_cache(
                self.database, self.cache, manifest_path, grid_path=grids)
        self.assertEqual(summary["counts"], {"done": 1})
        self.reviews.write_text(json.dumps({
            "schema_version": 1, "reviews": [human_review(digest)],
        }))
        output = self.base / "verified-learning.json"
        result = event_dataset.build_dataset(
            self.database, manifest_path, self.cache, self.reviews, output)
        self.assertEqual(result["counts"]["human_positive_bars"], 1)
        with sqlite3.connect(self.cache / "feature-cache.sqlite3") as db:
            artifact = db.execute("SELECT artifact_path FROM jobs").fetchone()[0]
        (self.cache / artifact).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            event_dataset.build_dataset(
                self.database, manifest_path, self.cache, self.reviews, self.base / "bad.json")
        self.assertFalse((self.base / "bad.json").exists())


if __name__ == "__main__":
    unittest.main()
