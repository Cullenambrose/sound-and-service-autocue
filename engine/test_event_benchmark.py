import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import event_benchmark


def _track(digest, group_id, split):
    return {
        "audio_sha256": digest, "group_id": group_id, "split": split,
        "grouping_method": "normalized_artist_title",
    }


def _review(digest, state, annotations=(), grid=None):
    return {
        "track_hash": digest, "track_name": "fixture.wav", "state": state,
        "note": "", "grid": grid or {}, "source": "human_review",
        "updated_at": "2026-10-22T12:00:00Z",
        "annotations": [
            {
                "id": index + 1, "event_type": kind, "certainty": certainty,
                "time_s": time_s, "bar": None, "note": "", "grid": grid or {},
                "source": "human_review", "created_at": "2026-10-22T12:00:00Z",
                "active": True, "retracted_at": None,
            }
            for index, (kind, certainty, time_s) in enumerate(annotations)
        ],
    }


def _prediction(digest, audio=(), assisted=None):
    assisted = audio if assisted is None else assisted
    return {
        "audio_sha256": digest, "status": "done",
        "candidates": {
            "audio_only": [{"time_s": time_s, "source": "audio_energy_change"} for time_s in audio],
            "anchor_assisted": [{"time_s": time_s, "source": "imported_anchor"} for time_s in assisted],
        },
    }


class EventBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.group_test = "group-test"
        self.group_train = "group-train"
        self.test_a = "a" * 64
        self.test_b = "b" * 64
        self.train = "c" * 64
        self.dataset = {
            "schema_version": 1,
            "grouping": {
                "review_required": True,
                "note": "Metadata groups require human review.",
            },
            "groups": [
                {"group_id": self.group_test, "split": "test",
                 "grouping_method": "normalized_artist_title"},
                {"group_id": self.group_train, "split": "train",
                 "grouping_method": "audio_sha256"},
            ],
            "tracks": [
                _track(self.test_a, self.group_test, "test"),
                _track(self.test_b, self.group_test, "test"),
                _track(self.train, self.group_train, "train"),
            ],
        }
        grid = {"t1": 0.25, "bar_s": 2.0, "offset_ms": 0}
        self.reviews = {
            "schema_version": 1,
            "reviews": [
                _review(self.test_a, "complete", [
                    ("drop", "certain", 10.0),
                    ("build", "certain", 20.0),
                    ("chorus", "uncertain", 30.0),
                ], grid),
                _review(self.test_b, "complete", (), grid),
                _review(self.train, "complete", [("drop", "certain", 50.0)], grid),
            ],
        }
        self.candidates = {
            "schema_version": 1, "detector_version": "anchor-candidates-v1",
            "tracks": [
                _prediction(self.test_a, [10.5, 21.0, 30.5, 40.0]),
                _prediction(self.test_b, [5.0]),
                _prediction(self.train, [50.0]),
            ],
        }

    def test_benchmark_uses_only_complete_test_groups_and_reports_metrics(self):
        report = event_benchmark.build_benchmark(
            self.dataset, self.reviews, self.candidates)
        audio = report["pools"]["audio_only"]

        self.assertEqual(report["counts"]["test_tracks"], 2)
        self.assertEqual(report["counts"]["complete_human_review_test_tracks"], 2)
        self.assertEqual(report["related_mix_grouping"]["test_groups"], 1)
        self.assertEqual(report["related_mix_grouping"]["reviewed_test_groups"], 1)
        self.assertTrue(report["related_mix_grouping"]["grouping_review_required"])
        self.assertEqual(audio["scored_tracks"], 2)
        self.assertEqual(audio["positioned_targets"], 2)
        self.assertEqual(audio["matched_targets"], 2)
        self.assertEqual(audio["target_recall"], 1.0)
        self.assertEqual(audio["false_positive_candidates"], 2)
        self.assertEqual(audio["uncertainty_excluded_candidates"], 1)
        self.assertEqual(audio["candidate_count"], 5)
        self.assertEqual(audio["candidate_precision"], 0.4)
        self.assertEqual(audio["mean_absolute_timing_error_s"], 0.75)
        self.assertEqual(audio["by_event_type"]["drop"]["recall"], 1.0)
        self.assertEqual(audio["by_event_type"]["chorus"]["targets"], 0)
        self.assertEqual(audio["tracks"][0]["group_id"], self.group_test)
        self.assertNotIn(self.train, {row["audio_sha256"] for row in audio["tracks"]})
        self.assertTrue(report["evaluation_policy"]["unlabeled_tracks_or_sections_are_not_negatives"])

    def test_skipped_uncertain_and_missing_reviews_do_not_enter_scores(self):
        reviews = copy.deepcopy(self.reviews)
        reviews["reviews"] = [
            _review(self.test_a, "skipped", [("drop", "certain", 10.0)]),
            _review(self.test_b, "uncertain", [("build", "certain", 5.0)]),
            reviews["reviews"][2],
        ]

        report = event_benchmark.build_benchmark(self.dataset, reviews, self.candidates)
        scores = report["pools"]["audio_only"]

        self.assertEqual(report["counts"]["complete_human_review_test_tracks"], 0)
        self.assertEqual(report["counts"]["excluded_test_reviews"]["skipped"], 1)
        self.assertEqual(report["counts"]["excluded_test_reviews"]["uncertain"], 1)
        self.assertEqual(scores["scored_tracks"], 0)
        self.assertIsNone(scores["target_recall"])
        self.assertIsNone(scores["candidate_precision"])

    def test_gridless_reviews_use_explicit_fallback_tolerance(self):
        reviews = {"schema_version": 1, "reviews": [
            _review(self.test_a, "complete", [("other", "certain", 10.0)]),
        ]}
        candidates = {"schema_version": 1, "detector_version": "fixture",
                      "tracks": [_prediction(self.test_a, [11.5])]}

        report = event_benchmark.build_benchmark(
            self.dataset, reviews, candidates, fallback_tolerance_s=1.0)
        track = report["pools"]["audio_only"]["tracks"][0]

        self.assertEqual(track["tolerance_s"], 1.0)
        self.assertEqual(track["tolerance_source"], "fallback_seconds")
        self.assertEqual(track["matched_count"], 0)

    def test_rejects_related_group_split_leakage_and_inconsistent_track_split(self):
        dataset = copy.deepcopy(self.dataset)
        dataset["tracks"][1]["split"] = "train"
        with self.assertRaisesRegex(ValueError, "does not match"):
            event_benchmark.build_benchmark(dataset, self.reviews, self.candidates)

        dataset = copy.deepcopy(self.dataset)
        dataset["groups"].append({"group_id": self.group_test, "split": "train"})
        with self.assertRaisesRegex(ValueError, "duplicate group IDs"):
            event_benchmark.build_benchmark(dataset, self.reviews, self.candidates)

    def test_duplicate_reviews_and_nonfinite_candidate_times_are_rejected(self):
        reviews = copy.deepcopy(self.reviews)
        reviews["reviews"].append(copy.deepcopy(reviews["reviews"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate track reviews"):
            event_benchmark.build_benchmark(self.dataset, reviews, self.candidates)

        candidates = copy.deepcopy(self.candidates)
        candidates["tracks"][0]["candidates"]["audio_only"][0]["time_s"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite number"):
            event_benchmark.build_benchmark(self.dataset, self.reviews, candidates)

    def test_report_writer_refuses_to_replace_an_input_and_writes_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "source.json"
            input_path.write_text("unchanged")
            with self.assertRaisesRegex(ValueError, "overwrite an input"):
                event_benchmark.write_report({}, input_path, [input_path])
            output = event_benchmark.write_report(
                {"schema_version": 1}, root / "report.json", [input_path])
            self.assertEqual(json.loads(output.read_text()), {"schema_version": 1})
            self.assertEqual(input_path.read_text(), "unchanged")
            with self.assertRaisesRegex(ValueError, "already exists"):
                event_benchmark.write_report({}, output, [input_path])
            self.assertEqual(json.loads(output.read_text()), {"schema_version": 1})

    def test_command_help_is_available(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("event_benchmark.py")), "--help"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dataset", result.stdout)
        self.assertIn("--reviews", result.stdout)
        self.assertIn("--candidates", result.stdout)

    def test_cli_writes_explicit_no_benchmark_report_and_returns_incomplete_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path, reviews_path, candidates_path, output_path = (
                root / "dataset.json", root / "reviews.json",
                root / "candidates.json", root / "benchmark.json",
            )
            dataset_path.write_text(json.dumps(self.dataset))
            reviews_path.write_text(json.dumps({"schema_version": 1, "reviews": []}))
            candidates_path.write_text(json.dumps(self.candidates))
            result = subprocess.run(
                [
                    sys.executable, str(Path(__file__).with_name("event_benchmark.py")),
                    "--dataset", str(dataset_path), "--reviews", str(reviews_path),
                    "--candidates", str(candidates_path), "--out", str(output_path),
                ],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(output_path.read_text())
            self.assertEqual(report["counts"]["complete_human_review_test_tracks"], 0)
            self.assertIsNone(report["pools"]["audio_only"]["target_recall"])
            self.assertIn('"positioned_targets": 0', result.stdout)


if __name__ == "__main__":
    unittest.main()
