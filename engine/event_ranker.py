#!/usr/bin/env python3
"""Train and evaluate a deterministic positive-only ranker over weak event candidates."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import event_dataset

RANKER_VERSION = "positive-prototype-ranker-v1"
FEATURES = ("log_rms", "log_low_energy", "log_energy_score", "log_rms_ratio", "log_low_ratio")
EVENT_TYPES = ("drop", "build", "breakdown", "chorus", "other")
SCALE_FLOOR = 0.25


def _finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + " must be a finite number.")
    return float(value)


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _feature_map(sample):
    features = sample.get("features")
    evidence = sample.get("audio_evidence")
    if not isinstance(features, dict):
        raise ValueError("Training sample is missing feature values.")
    evidence = evidence if isinstance(evidence, dict) else {}
    rms = _finite(features.get("rms"), "RMS feature")
    low = _finite(features.get("low_energy"), "Low-band feature")
    if rms < 0 or low < 0:
        raise ValueError("Energy features must be non-negative.")
    values = (rms, low, evidence.get("score"), evidence.get("rms_ratio"), evidence.get("low_ratio"))
    result = {}
    for name, value in zip(FEATURES, values):
        if value is None:
            result[name] = None
        else:
            number = _finite(value, name)
            if name != "log_energy_score" and number < 0:
                raise ValueError(name + " must be non-negative.")
            result[name] = math.log1p(max(0.0, number))
    return result


def _read_samples(dataset):
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1 \
            or dataset.get("builder_version") != event_dataset.BUILDER_VERSION:
        raise ValueError("Learning dataset must be a supported event_dataset schema version 1.")
    if dataset.get("feature_schema") != event_dataset.FEATURE_SCHEMA:
        raise ValueError("Learning dataset uses an unsupported feature schema.")
    tracks = dataset.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError("Learning dataset must contain a tracks array.")
    seen_groups, seen_tracks = {}, set()
    training, validation = [], []
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError("Learning dataset contains a malformed track.")
        digest = event_dataset.event_benchmark._digest(track.get("audio_sha256"), "Track hash")
        if digest in seen_tracks:
            raise ValueError("Learning dataset contains duplicate track hashes.")
        seen_tracks.add(digest)
        split, group_id = track.get("split"), track.get("group_id")
        if split not in {"train", "validation", "test"} or not isinstance(group_id, str) or not group_id:
            raise ValueError("Learning dataset track is missing its split or related-mix group.")
        previous = seen_groups.setdefault(group_id, split)
        if previous != split:
            raise ValueError("Related-mix group appears in more than one split.")
        if split == "test":
            if track.get("status") != "reserved_test" or "samples" in track:
                raise ValueError("Held-out test data must remain reserved and feature-free.")
            continue
        if track.get("status") != "included":
            continue
        if split == "validation":
            validation.append(track)
        rows = track.get("samples")
        if not isinstance(rows, list):
            raise ValueError("Included learning track is missing bar samples.")
        eligible_human = any(
            row.get("eligible_for_training") is True
            or row.get("eligible_for_validation") is True
            for row in rows if isinstance(row, dict)
        )
        if eligible_human and track.get("review_state") != "complete":
            raise ValueError("Eligible learning labels require a completed human review.")
        seen_bars = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Included learning track contains a malformed bar sample.")
            if row.get("feature_usable") is not True:
                continue
            bar = row.get("bar")
            if type(bar) is not int or bar < 0 or bar in seen_bars:
                raise ValueError("Usable sample bars must be unique non-negative integers.")
            seen_bars.add(bar)
            if row.get("eligible_for_training") is True and split != "train":
                raise ValueError("Training eligibility is only valid on the training split.")
            if row.get("eligible_for_validation") is True and split != "validation":
                raise ValueError("Validation eligibility is only valid on the validation split.")
            if row.get("eligible_for_weak_learning") is True and (
                    split != "train" or row.get("supervision") != "weak_candidate"
                    or row.get("event_target") is not None):
                raise ValueError("Weak candidate eligibility cannot create a human event target.")
            labels = row.get("human_labels")
            if labels is None:
                labels = []
            if not isinstance(labels, list) or any(not isinstance(label, dict) for label in labels):
                raise ValueError("Human labels on a usable sample must be an array of objects.")
            if split == "train" and row.get("eligible_for_training") is True:
                certain_labels = [
                    label for label in labels
                    if label.get("certainty") == "certain" and label.get("event_type") in EVENT_TYPES
                ]
                target_types = sorted({label["event_type"] for label in certain_labels})
                if (row.get("supervision") != "human_positive"
                        or not target_types or len(certain_labels) != len(labels)
                        or any(label.get("source") != "human_review" for label in certain_labels)
                        or track.get("review_state") != "complete"
                        or row.get("event_target") != target_types):
                    raise ValueError("Training eligibility requires explicit human-positive labels.")
                for label in certain_labels:
                    training.append({
                        "track_hash": digest, "group_id": group_id, "bar": row["bar"],
                        "event_type": label["event_type"], "features": _feature_map(row),
                    })
            elif split == "validation" and row.get("eligible_for_validation") is True:
                certain_labels = [
                    label for label in labels
                    if label.get("certainty") == "certain" and label.get("event_type") in EVENT_TYPES
                ]
                target_types = sorted({label["event_type"] for label in certain_labels})
                if (row.get("supervision") != "human_positive" or not target_types
                        or len(certain_labels) != len(labels)
                        or any(label.get("source") != "human_review" for label in certain_labels)
                        or track.get("review_state") != "complete"
                        or row.get("event_target") != target_types):
                    raise ValueError("Validation eligibility requires explicit human-positive labels.")
    return training, validation, hashlib.sha256(
        json.dumps(dataset, ensure_ascii=True, allow_nan=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _fit(training):
    if not training:
        raise ValueError("No eligible certain human-positive training labels were found.")
    global_medians = {}
    global_scales = {}
    for name in FEATURES:
        values = sorted(item["features"][name] for item in training if item["features"][name] is not None)
        if not values:
            global_medians[name] = 0.0
            global_scales[name] = SCALE_FLOOR
            continue
        median = _median(values)
        deviations = [abs(value - median) for value in values]
        mad = _median(deviations)
        global_medians[name] = median
        global_scales[name] = max(SCALE_FLOOR, 1.4826 * mad)
    transformed = []
    for item in training:
        vector = [
            ((global_medians[name] if item["features"][name] is None else item["features"][name])
             - global_medians[name]) / global_scales[name]
            for name in FEATURES
        ]
        transformed.append({**item, "vector": vector})
    classes = {}
    for event_type in EVENT_TYPES:
        samples = [item for item in transformed if item["event_type"] == event_type]
        if not samples:
            continue
        means = [sum(item["vector"][index] for item in samples) / len(samples)
                 for index in range(len(FEATURES))]
        variances = [
            max(SCALE_FLOOR ** 2, sum((item["vector"][index] - means[index]) ** 2
                                     for item in samples) / len(samples))
            for index in range(len(FEATURES))
        ]
        group_count = len({item["group_id"] for item in samples})
        classes[event_type] = {
            "positive_sample_count": len(samples), "independent_group_count": group_count,
            "mean": means, "variance": variances,
            "data_sufficiency": "preliminary" if group_count < 3 else "multi-group",
        }
    return {
        "feature_names": list(FEATURES), "log1p_transform": True,
        "global_median": global_medians, "global_scale": global_scales,
        "classes": classes,
    }


def _vector(sample, model):
    features = _feature_map(sample)
    normalization = model["normalization"]
    return [
        ((normalization["global_median"][name] if features[name] is None else features[name])
         - normalization["global_median"][name]) / normalization["global_scale"][name]
        for name in FEATURES
    ]


def score_sample(sample, model):
    vector = _vector(sample, model)
    scores = {}
    for event_type, prototype in model["normalization"]["classes"].items():
        distance = sum(
            (vector[index] - prototype["mean"][index]) ** 2 / prototype["variance"][index]
            for index in range(len(FEATURES))
        ) / len(FEATURES)
        scores[event_type] = math.exp(-0.5 * min(distance, 100.0))
    return scores


def _candidate_rows(track):
    rows = track.get("samples", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)
            and row.get("feature_usable") is True
            and isinstance(row.get("weak_labels"), list) and row["weak_labels"]]


def _evaluate_validation(validation_tracks, model, top_k, tolerance_bars):
    per_type = {event_type: {"targets": 0, "covered_at_k": 0, "rank_sum": 0,
                             "scored_candidates": 0, "hits_at_k": 0}
                for event_type in EVENT_TYPES}
    scored_tracks, matched_tracks = 0, 0
    track_results = []
    for track in validation_tracks:
        samples = _candidate_rows(track)
        targets_by_type = {event_type: [] for event_type in EVENT_TYPES}
        for sample in track["samples"]:
            if sample.get("eligible_for_validation") is not True:
                continue
            for label in sample.get("human_labels", []):
                if label.get("certainty") == "certain" and label.get("event_type") in EVENT_TYPES:
                    targets_by_type[label["event_type"]].append(float(sample["bar"]))
                    per_type[label["event_type"]]["targets"] += 1
        if not targets_by_type or not any(targets_by_type.values()):
            continue
        candidates = []
        for sample in samples:
            scores = score_sample(sample, model)
            candidates.append({"bar": float(sample["bar"]), "scores": scores,
                               "sources": sorted({label.get("source", "") for label in sample["weak_labels"]})})
        if candidates:
            scored_tracks += 1
        ranked_results = {}
        any_hit = False
        for event_type in EVENT_TYPES:
            targets = targets_by_type[event_type]
            if not targets:
                continue
            ranked = sorted(candidates, key=lambda item: (-item["scores"].get(event_type, 0),
                                                          item["bar"]))
            top = ranked[:top_k]
            covered = 0
            ranks = []
            remaining = list(top)
            for target in sorted(targets):
                near = [item for item in remaining
                        if abs(item["bar"] - target) <= tolerance_bars]
                if not near:
                    continue
                best = min(near, key=lambda item: abs(item["bar"] - target))
                remaining.remove(best)
                covered += 1
                rank = ranked.index(best) + 1
                ranks.append(rank)
                any_hit = True
            metrics = per_type[event_type]
            metrics["covered_at_k"] += covered
            metrics["rank_sum"] += sum(ranks)
            metrics["scored_candidates"] += min(top_k, len(ranked))
            metrics["hits_at_k"] += covered
            ranked_results[event_type] = {
                "target_count": len(targets), "covered_at_k": covered,
                "top_bars": [{"bar": item["bar"], "similarity": item["scores"].get(event_type, 0),
                              "sources": item["sources"]} for item in top],
            }
        matched_tracks += int(any_hit)
        track_results.append({
            "audio_sha256": track["audio_sha256"], "group_id": track["group_id"],
            "candidate_count": len(candidates), "by_event_type": ranked_results,
        })
    return {
        "validation_tracks_scored": scored_tracks,
        "validation_tracks_with_top_k_hit": matched_tracks,
        "top_k": top_k, "tolerance_bars": tolerance_bars,
        "by_event_type": {
            event_type: {
                "targets": values["targets"], "covered_at_k": values["covered_at_k"],
                "recall_at_k": (round(values["covered_at_k"] / values["targets"], 4)
                                if values["targets"] else None),
                "mean_rank_of_hits": (round(values["rank_sum"] / values["hits_at_k"], 3)
                                      if values["hits_at_k"] else None),
                "candidate_points_ranked_at_k": values["scored_candidates"],
            }
            for event_type, values in per_type.items()
        },
        "interpretation": (
            "Validation ranking diagnostics over weak candidate points only. Similarities are "
            "uncalibrated one-class scores, not probabilities; unlabeled candidates are not negatives. "
            "This is not test-set accuracy or evidence of model promotion."
        ),
        "tracks": track_results,
    }


def train_model(dataset, *, seed="autocue-event-v1", top_k=5, tolerance_bars=1.0):
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("Training seed must be a non-empty string.")
    if type(top_k) is not int or top_k < 1 or top_k > 100:
        raise ValueError("top-k must be an integer between 1 and 100.")
    tolerance_bars = _finite(tolerance_bars, "Tolerance in bars")
    if tolerance_bars <= 0 or tolerance_bars > 64:
        raise ValueError("Tolerance in bars must be greater than 0 and no more than 64.")
    training, validation, dataset_sha256 = _read_samples(dataset)
    normalization = _fit(training)
    if not normalization["classes"]:
        raise ValueError("No supported human event types were found in training labels.")
    group_ids = sorted({item["group_id"] for item in training})
    model = {
        "schema_version": 1, "ranker_version": RANKER_VERSION,
        "builder_version": event_dataset.BUILDER_VERSION,
        "feature_schema": event_dataset.FEATURE_SCHEMA,
        "features": list(FEATURES), "seed": seed, "algorithm": "diagonal_positive_prototype",
        "algorithm_parameters": {
            "variance_floor": SCALE_FLOOR ** 2, "score": "exp(-0.5 * mean diagonal Mahalanobis distance)",
            "weak_labels_used_for_fitting": False, "negative_samples_used": 0,
            "randomized_steps": False,
        },
        "training": {
            "dataset_sha256": dataset_sha256, "training_positive_samples": len(training),
            "training_groups": len(group_ids), "training_group_ids": group_ids,
            "positive_samples_by_type": {
                event_type: normalization["classes"].get(event_type, {}).get("positive_sample_count", 0)
                for event_type in EVENT_TYPES
            },
            "group_sufficiency_warnings": {
                event_type: item["independent_group_count"]
                for event_type, item in normalization["classes"].items()
                if item["independent_group_count"] < 3
            },
        },
        "normalization": normalization,
        "validation": _evaluate_validation(validation, {
            "normalization": normalization,
        }, top_k, tolerance_bars) if validation else None,
        "claims": {
            "event_type_scores_are_probabilities": False,
            "unlabeled_samples_are_negatives": False,
            "test_split_used": False,
            "held_out_accuracy_established": False,
        },
    }
    return model


def rank_candidates(dataset, model, *, split="validation", top_k=20):
    if split not in {"train", "validation"}:
        raise ValueError("Candidate ranking split must be train or validation; test remains reserved.")
    if type(top_k) is not int or not 1 <= top_k <= 1000:
        raise ValueError("Candidate top-k must be between 1 and 1000.")
    if not isinstance(model, dict) or model.get("schema_version") != 1 \
            or model.get("ranker_version") != RANKER_VERSION \
            or model.get("builder_version") != event_dataset.BUILDER_VERSION \
            or model.get("feature_schema") != event_dataset.FEATURE_SCHEMA \
            or model.get("features") != list(FEATURES):
        raise ValueError("Ranker artifact has an unsupported schema or feature version.")
    if not isinstance(model.get("normalization"), dict) \
            or not isinstance(model["normalization"].get("classes"), dict) \
            or not model["normalization"]["classes"]:
        raise ValueError("Ranker artifact has no trained positive prototypes.")
    normalization = model["normalization"]
    if not isinstance(normalization.get("global_median"), dict) \
            or not isinstance(normalization.get("global_scale"), dict):
        raise ValueError("Ranker artifact is missing feature normalization.")
    for name in FEATURES:
        _finite(normalization["global_median"].get(name), "Ranker feature median")
        if _finite(normalization["global_scale"].get(name), "Ranker feature scale") <= 0:
            raise ValueError("Ranker feature scales must be positive.")
    for event_type, prototype in normalization["classes"].items():
        if event_type not in EVENT_TYPES or not isinstance(prototype, dict) \
                or not isinstance(prototype.get("mean"), list) \
                or not isinstance(prototype.get("variance"), list) \
                or len(prototype["mean"]) != len(FEATURES) \
                or len(prototype["variance"]) != len(FEATURES):
            raise ValueError("Ranker artifact has a malformed class prototype.")
        for value in prototype["mean"]:
            _finite(value, "Ranker prototype mean")
        for value in prototype["variance"]:
            if _finite(value, "Ranker prototype variance") <= 0:
                raise ValueError("Ranker prototype variances must be positive.")
    training, _, dataset_sha256 = _read_samples(dataset)
    model_training = model.get("training")
    if not isinstance(model_training, dict) or dataset_sha256 != model_training.get("dataset_sha256"):
        raise ValueError("Learning dataset does not match the model's training snapshot.")
    tracks = []
    for track in dataset["tracks"]:
        if track.get("split") != split or track.get("status") != "included":
            continue
        candidates = []
        for sample in _candidate_rows(track):
            similarity = score_sample(sample, model)
            candidates.append({
                "bar": sample["bar"], "candidate_sources": sorted({
                    label.get("source", "") for label in sample["weak_labels"]
                }),
                "prototype_similarity": max(similarity.values()),
                "similarity_by_type": similarity,
                "event_prediction": None,
            })
        candidates.sort(key=lambda row: (-row["prototype_similarity"], row["bar"]))
        tracks.append({
            "audio_sha256": track["audio_sha256"], "group_id": track["group_id"],
            "candidate_count": len(candidates), "ranked_candidates": candidates[:top_k],
        })
    return {
        "schema_version": 1, "ranker_version": RANKER_VERSION,
        "dataset_sha256": dataset_sha256,         "ranker_training_sha256": model_training["dataset_sha256"],
        "split": split, "top_k": top_k, "tracks": tracks,
        "score_interpretation": (
            "Prototype similarity is an uncalibrated one-class ranking score, not a probability, "
            "event classification, or confidence. No unlabeled point is a negative example."
        ),
        "training_positive_samples": len(training),
    }


def write_model(model, output_path, protected_paths=()):
    output = Path(output_path).expanduser()
    if output.is_symlink():
        raise ValueError("Ranker artifact must not be a symlink.")
    output = output.resolve()
    if any(output == Path(path).expanduser().resolve() for path in protected_paths):
        raise ValueError("Ranker artifact must not overwrite an input.")
    if output.exists():
        raise ValueError("Ranker artifact already exists; choose a new output path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".event-ranker-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            json.dump(model, destination, ensure_ascii=True, allow_nan=False, indent=2)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.link(temporary_name, output, follow_symlinks=False)
    finally:
        os.unlink(temporary_name)
    return output


def _command_train(args):
    input_path = Path(args.dataset).expanduser().resolve()
    dataset = json.loads(input_path.read_text(encoding="utf-8"))
    model = train_model(dataset, seed=args.seed, top_k=args.top_k,
                        tolerance_bars=args.tolerance_bars)
    artifact = write_model(model, args.out, [input_path])
    return {
        "artifact_path": str(artifact), "ranker_version": RANKER_VERSION,
        "training": model["training"], "validation": model["validation"],
    }


def _command_rank(args):
    dataset_path = Path(args.dataset).expanduser().resolve()
    model_path = Path(args.model).expanduser().resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    model = json.loads(model_path.read_text(encoding="utf-8"))
    result = rank_candidates(dataset, model, split=args.split, top_k=args.top_k)
    artifact = write_model(result, args.out, [dataset_path, model_path])
    return {"report_path": str(artifact), "ranker_version": RANKER_VERSION,
            "split": result["split"], "top_k": result["top_k"],
            "tracks": len(result["tracks"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train", help="Fit a deterministic ranker on human-positive training labels.")
    train.add_argument("--dataset", required=True, help="C01 positive/weak/unlabeled dataset JSON.")
    train.add_argument("--out", required=True, help="New ranker artifact JSON path.")
    train.add_argument("--seed", default="autocue-event-v1", help="Recorded reproducibility seed.")
    train.add_argument("--top-k", type=int, default=5, help="Validation candidates retained per event type and track.")
    train.add_argument("--tolerance-bars", type=float, default=1.0,
                       help="Validation target tolerance in bars (default 1).")
    rank = commands.add_parser("rank", help="Rank weak candidate points using a trained positive-only artifact.")
    rank.add_argument("--dataset", required=True, help="Exact C01 dataset used to train the model.")
    rank.add_argument("--model", required=True, help="C02 ranker artifact JSON.")
    rank.add_argument("--split", choices=("train", "validation"), default="validation",
                      help="Split to rank; held-out test is always reserved.")
    rank.add_argument("--top-k", type=int, default=20, help="Candidate results retained per track.")
    rank.add_argument("--out", required=True, help="New ranked candidate report JSON.")
    args = parser.parse_args(argv)
    try:
        if args.command == "train":
            result = _command_train(args)
        else:
            result = _command_rank(args)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        print("Event ranker failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
