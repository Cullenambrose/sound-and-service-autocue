import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import anchor_detector
import dataset_splits
import feature_cache
import library_inventory as inventory

try:
    import numpy as np
except ImportError:
    np = None


class AnchorCandidateTests(unittest.TestCase):
    def test_audio_change_candidates_use_local_energy_evidence(self):
        if np is None:
            self.skipTest("NumPy is required for feature scoring.")
        features = {
            "bar_index": np.arange(8),
            "coverage": np.ones(8),
            "rms": np.array([0.1, 0.1, 0.1, 0.1, 0.5, 0.5, 0.5, 0.5]),
            "low_energy": np.array([0.1, 0.1, 0.1, 0.1, 0.5, 0.5, 0.5, 0.5]),
        }
        quality = {
            "grid_origin_s": 0.25, "bar_duration_s": 2.0, "audio_duration_s": 16.0,
            "grid_confidence": 0.9,
        }

        bars, origin, bar_s, _, score_at = anchor_detector._audio_scores(features, quality)
        candidates = anchor_detector._audio_candidates(bars, origin, bar_s, score_at)

        self.assertEqual([candidate["bar"] for candidate in candidates], [4, 5])
        self.assertAlmostEqual(candidates[0]["audio_evidence"]["score"], 5.0)
        self.assertEqual(candidates[0]["source"], "audio_energy_change")

    def test_imported_markers_remain_untyped_and_out_of_range_markers_are_skipped(self):
        metadata = {
            "imported_anchors": {"sources": [{
                "format": "serato_markers2_v1.1", "status": "parsed",
                "field": "Serato Markers2", "sha256": "a" * 64,
                "records": [
                    {"type": "CUE", "index": 3, "time_s": 4.25, "name": "First Drop"},
                    {"type": "CUE", "index": 4, "time_s": 30.0, "name": "Out of range"},
                    {"type": "LOOP", "index": 5, "time_s": 2.0, "name": "Loop"},
                ],
            }]},
        }

        candidates, ignored = anchor_detector._anchor_candidates(
            metadata, 0.25, 2.0, 8.0, lambda _bar: None)

        self.assertEqual(len(candidates), 1)
        marker = candidates[0]["marker"]
        self.assertEqual(candidates[0]["time_s"], 4.25)
        self.assertEqual(marker["name"], "First Drop")
        self.assertEqual(marker["role"], "unverified_imported_candidate")
        self.assertFalse(marker["human_verified"])
        self.assertNotIn("meaning", marker)
        self.assertEqual(len(ignored), 1)

    def test_identical_audio_and_anchor_positions_merge_without_losing_provenance(self):
        audio = [{"bar": 2, "time_s": 4.25, "source": "audio_energy_change",
                  "audio_evidence": {"score": 2.0}}]
        anchors = [{"bar": 2, "time_s": 4.25, "source": "imported_anchor",
                    "marker": {"index": 3, "name": "Unverified"},
                    "audio_evidence": {"score": 2.0}}]

        candidates = anchor_detector._union_candidates(audio, anchors)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "audio_and_imported_anchor")
        self.assertEqual(candidates[0]["sources"], ["audio_energy_change", "imported_anchor"])
        self.assertEqual(candidates[0]["markers"], [{"index": 3, "name": "Unverified"}])
        self.assertEqual(candidates[0]["audio_evidence"], {"score": 2.0})

    def test_review_metrics_use_human_review_answer_and_compare_baseline_corrections(self):
        answer = {
            "source": "human_review",
            "grid": {"t1": 0.35, "bar_s": 2.0, "offset_ms": 100},
            "cues": {"B": {"bar": 2, "meaning": "start"}},
        }
        track = {"plan": {"grid": answer["grid"], "variants": {
            "baseline": {"cues": {"B": {"bar": 0, "time_s": 0.25, "meaning": "start"}}},
        }}}
        points = {
            "audio_only": [],
            "anchor_assisted": [{"time_s": 4.25}],
        }

        targets = anchor_detector._review_targets(answer, track, points, 4.0)
        rows = [{"review_targets": targets}]

        self.assertEqual(len(targets), 1)
        self.assertTrue(targets[0]["baseline_corrected"])
        self.assertFalse(targets[0]["audio_only_covered"])
        self.assertTrue(targets[0]["anchor_assisted_covered"])
        self.assertEqual(anchor_detector._metrics(rows, "anchor_assisted")["corrected_target_recall"], 1.0)
        self.assertEqual(anchor_detector._metrics(rows, "audio_only")["candidate_recall"], 0.0)
        no_baseline = anchor_detector._review_targets(
            answer, {"plan": {"grid": answer["grid"]}}, points, 4.0)
        no_baseline_metrics = anchor_detector._metrics([{"review_targets": no_baseline}], "anchor_assisted")
        self.assertIsNone(no_baseline[0]["baseline_corrected"])
        self.assertEqual(no_baseline_metrics["correction_status_unknown_targets"], 1)


@unittest.skipIf(np is None, "NumPy is required for feature extraction.")
class AnchorDetectorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "music"
        self.root.mkdir()
        self.database = self.base / "state" / "inventory.sqlite3"
        self.dataset_path = self.base / "dataset.json"
        self.cache_dir = self.base / "feature-cache"

    def tearDown(self):
        self.temporary.cleanup()

    def _audio(self):
        path = self.root / "track.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(44100)
            block = []
            for index in range(44100 * 8):
                sample = int(6000 * math.sin(2 * math.pi * 220 * index / 44100))
                block.extend((sample, sample))
                if len(block) >= 8192:
                    output.writeframes(b"".join(
                        int(value).to_bytes(2, "little", signed=True) for value in block))
                    block.clear()
            if block:
                output.writeframes(b"".join(
                    int(value).to_bytes(2, "little", signed=True) for value in block))
        return path

    @staticmethod
    def _copy_wav_from_fd(command, **_options):
        source = command[command.index("-i") + 1]
        descriptor = int(source.rsplit("/", 1)[1])
        with os.fdopen(os.dup(descriptor), "rb") as incoming, open(command[-1], "wb") as output:
            shutil.copyfileobj(incoming, output)
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_switchable_pools_and_review_correction_coverage(self):
        source = self._audio()
        scan_id = inventory.create_scan(self.database, [self.root])
        self.assertEqual(inventory.resume_scan(self.database, scan_id)["status"], "complete")
        dataset_splits.build_manifest(
            self.database, scan_id, self.dataset_path, seed="anchor-test")
        manifest = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        digest = manifest["tracks"][0]["audio_sha256"]
        manifest["tracks"][0]["metadata"]["imported_anchors"] = {
            "sources": [{
                "format": "serato_markers2_v1.1", "status": "parsed",
                "field": "Serato Markers2", "sha256": "b" * 64,
                "records": [{"type": "CUE", "index": 2, "time_s": 4.25, "name": "Drop"}],
            }],
        }
        self.dataset_path.write_text(json.dumps(manifest), encoding="utf-8")
        grids = self.base / "grids.json"
        grids.write_text(json.dumps({digest: {
            "t1": 0.35, "bar_s": 2.0, "offset_ms": 100,
            "source": "reviewed_fixture", "confidence": 0.9, "human_verified": True,
        }}), encoding="utf-8")
        with patch.object(feature_cache.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                patch.object(feature_cache, "_ffmpeg_version",
                             return_value=("/usr/bin/ffmpeg", "fixture ffmpeg")), \
                patch.object(feature_cache.subprocess, "run", side_effect=self._copy_wav_from_fd):
            feature_cache.run_cache(
                self.database, self.cache_dir, self.dataset_path, grid_path=grids)

        grid = {"t1": 0.35, "bar_s": 2.0, "offset_ms": 100}
        review_path = self.base / "reviews.json"
        review_path.write_text(json.dumps({
            "schema_version": 2,
            "answer_keys": [{"hash": digest, "answer": {
                "source": "human_review", "grid": grid,
                "cues": {"B": {"bar": 2, "name": "START", "meaning": "start"}},
            }}],
            "tracks": [{"hash": digest, "plan": {"grid": grid, "variants": {
                "baseline": {"cues": {"B": {"bar": 0, "time_s": 0.25, "meaning": "start"}}},
            }}}],
        }), encoding="utf-8")
        output = self.base / "candidate-report.json"

        result = anchor_detector.run_detector(
            self.database, self.dataset_path, self.cache_dir,
            review_path=review_path, output_path=output)

        track = result["tracks"][0]
        self.assertEqual(track["status"], "done")
        self.assertEqual(track["candidate_counts"]["audio_only"], 0)
        self.assertEqual(track["candidate_counts"]["anchor_assisted"], 1)
        self.assertEqual(track["candidates"]["anchor_assisted"][0]["source"], "imported_anchor")
        self.assertEqual(result["review_comparison"]["anchor_assisted"]["corrected_target_recall"], 1.0)
        self.assertEqual(result["incremental_correction_coverage"]["newly_covered_by_imported_anchor_candidates"], 1)
        self.assertEqual(json.loads(output.read_text())["detector_version"], anchor_detector.DETECTOR_VERSION)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        manifest_bytes = self.dataset_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite one of its inputs"):
            anchor_detector.run_detector(
                self.database, self.dataset_path, self.cache_dir,
                review_path=review_path, output_path=self.dataset_path)
        self.assertEqual(self.dataset_path.read_bytes(), manifest_bytes)


if __name__ == "__main__":
    unittest.main()
