import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import event_dataset
import event_ranker


def sample(bar, *, energy=0.6, evidence=0.5, label=False, weak=False,
           training=False, validation=False, weak_eligible=False):
    human_labels = ([{
        "annotation_id": bar + 1, "source": "human_review",
        "event_type": "chorus", "certainty": "certain",
    }] if label else [])
    return {
        "bar": bar, "feature_usable": True,
        "features": {"rms": energy, "low_energy": energy * 0.7, "coverage": 1.0},
        "audio_evidence": {
            "score": evidence, "rms_ratio": 2.0 if label else 0.5,
            "low_ratio": 2.0 if label else 0.5,
        },
        "human_labels": human_labels,
        "weak_labels": [{"source": "audio_energy_change"}] if weak else [],
        "supervision": "human_positive" if label else "weak_candidate" if weak else "unlabeled",
        "event_target": ["chorus"] if label else None,
        "eligible_for_training": training and label,
        "eligible_for_validation": validation and label,
        "eligible_for_weak_learning": weak_eligible,
    }


def track(index, split, samples, *, group=None, status="included"):
    row = {
        "audio_sha256": f"{index:064x}", "group_id": group or f"group-{index}",
        "split": split, "status": status,
    }
    if status == "included":
        row.update({"review_state": "complete", "samples": samples})
    return row


def dataset_fixture():
    tracks = [
        track(index, "train", [
            sample(3, label=True, training=True),
            sample(10, energy=0.99, evidence=0.99, weak=True, weak_eligible=True),
            sample(11),
        ])
        for index in range(1, 4)
    ]
    tracks.append(track(4, "validation", [
        sample(0, energy=0.05, evidence=0.1, weak=True),
        sample(4, label=True, weak=True, validation=True),
        sample(8, energy=0.02, evidence=0.05, weak=True),
    ]))
    tracks.append(track(5, "test", [], status="reserved_test"))
    return {
        "schema_version": 1, "builder_version": event_dataset.BUILDER_VERSION,
        "feature_schema": event_dataset.FEATURE_SCHEMA, "tracks": tracks,
    }


class EventRankerTests(unittest.TestCase):
    def setUp(self):
        self.dataset = dataset_fixture()
        self.model = event_ranker.train_model(self.dataset, seed="fixture", top_k=1)

    def test_only_explicit_certain_human_positives_fit_the_prototype(self):
        prototype = self.model["normalization"]["classes"]["chorus"]
        self.assertEqual(prototype["positive_sample_count"], 3)
        self.assertEqual(prototype["independent_group_count"], 3)
        self.assertEqual(self.model["training"]["training_positive_samples"], 3)
        self.assertEqual(self.model["training"]["group_sufficiency_warnings"], {})
        self.assertEqual(self.model["algorithm_parameters"]["negative_samples_used"], 0)
        self.assertEqual(self.model["claims"]["unlabeled_samples_are_negatives"], False)

    def test_validation_metrics_use_full_candidate_tracks_and_are_deterministic(self):
        repeated = event_ranker.train_model(self.dataset, seed="fixture", top_k=1)
        self.assertEqual(self.model, repeated)
        metric = self.model["validation"]["by_event_type"]["chorus"]
        self.assertEqual(metric["targets"], 1)
        self.assertEqual(metric["recall_at_k"], 1.0)
        self.assertFalse(self.model["claims"]["held_out_accuracy_established"])

    def test_rank_command_logic_returns_candidates_without_event_predictions(self):
        report = event_ranker.rank_candidates(self.dataset, self.model, top_k=1)
        self.assertEqual(report["split"], "validation")
        self.assertEqual(len(report["tracks"]), 1)
        self.assertEqual(report["tracks"][0]["ranked_candidates"][0]["bar"], 4)
        self.assertEqual(report["tracks"][0]["ranked_candidates"][0]["event_prediction"], None)
        self.assertEqual(len(report["tracks"][0]["ranked_candidates"]), 1)
        self.assertIn("not a probability", report["score_interpretation"])

    def test_seed_is_metadata_and_candidate_scores_are_not_probabilities(self):
        similarity = event_ranker.score_sample(self.dataset["tracks"][0]["samples"][0], self.model)
        self.assertEqual(set(similarity), {"chorus"})
        self.assertGreaterEqual(similarity["chorus"], 0.0)
        self.assertLessEqual(similarity["chorus"], 1.0)

    def test_no_human_positive_training_labels_fails_clearly(self):
        data = copy.deepcopy(self.dataset)
        for row in data["tracks"][0]["samples"]:
            row["eligible_for_training"] = False
        data["tracks"] = data["tracks"][:1]
        with self.assertRaisesRegex(ValueError, "No eligible certain human-positive"):
            event_ranker.train_model(data)

    def test_inconsistent_related_mix_split_is_rejected(self):
        data = copy.deepcopy(self.dataset)
        data["tracks"][3]["group_id"] = data["tracks"][0]["group_id"]
        with self.assertRaisesRegex(ValueError, "appears in more than one split"):
            event_ranker.train_model(data)

    def test_weak_candidates_do_not_require_complete_human_reviews(self):
        data = copy.deepcopy(self.dataset)
        pending = track(6, "train", [sample(1, weak=True, weak_eligible=True)])
        pending["review_state"] = "pending"
        data["tracks"].append(pending)
        model = event_ranker.train_model(data)
        self.assertEqual(model["training"]["training_positive_samples"], 3)

    def test_test_split_must_not_contain_features(self):
        data = copy.deepcopy(self.dataset)
        data["tracks"][-1]["samples"] = []
        with self.assertRaisesRegex(ValueError, "test data must remain reserved"):
            event_ranker.train_model(data)

    def test_duplicate_tracks_and_eligible_nonpositive_rows_are_rejected(self):
        data = copy.deepcopy(self.dataset)
        data["tracks"].append(copy.deepcopy(data["tracks"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate track hashes"):
            event_ranker.train_model(data)

        data = copy.deepcopy(self.dataset)
        data["tracks"][0]["samples"][1]["eligible_for_training"] = True
        with self.assertRaisesRegex(ValueError, "explicit human-positive"):
            event_ranker.train_model(data)

    def test_ranking_requires_the_training_snapshot_and_never_allows_test(self):
        with self.assertRaisesRegex(ValueError, "test remains reserved"):
            event_ranker.rank_candidates(self.dataset, self.model, split="test")
        data = copy.deepcopy(self.dataset)
        data["tracks"][0]["samples"][0]["features"]["rms"] = 0.7
        with self.assertRaisesRegex(ValueError, "does not match the model"):
            event_ranker.rank_candidates(data, self.model)

    def test_model_artifacts_are_new_and_cannot_overwrite_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "ranker.json"
            event_ranker.write_model(self.model, destination)
            with self.assertRaisesRegex(ValueError, "already exists"):
                event_ranker.write_model(self.model, destination)
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                event_ranker.write_model(self.model, Path(directory) / "input.json",
                                         [Path(directory) / "input.json"])

    def test_train_and_rank_cli_write_versioned_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            dataset_path = directory / "learning.json"
            model_path = directory / "ranker.json"
            report_path = directory / "ranked.json"
            dataset_path.write_text(json.dumps(self.dataset), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(event_ranker.main([
                    "train", "--dataset", str(dataset_path), "--out", str(model_path),
                    "--seed", "cli-fixture",
                ]), 0)
                self.assertEqual(event_ranker.main([
                    "rank", "--dataset", str(dataset_path), "--model", str(model_path),
                    "--split", "validation", "--top-k", "1", "--out", str(report_path),
                ]), 0)
            model = json.loads(model_path.read_text(encoding="utf-8"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(model["seed"], "cli-fixture")
            self.assertEqual(report["tracks"][0]["ranked_candidates"][0]["bar"], 4)

    def test_malformed_ranker_prototypes_are_rejected(self):
        model = copy.deepcopy(self.model)
        model["normalization"]["classes"]["chorus"]["variance"][0] = 0
        with self.assertRaisesRegex(ValueError, "variances must be positive"):
            event_ranker.rank_candidates(self.dataset, model)


if __name__ == "__main__":
    unittest.main()
