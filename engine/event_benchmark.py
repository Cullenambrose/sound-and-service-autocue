#!/usr/bin/env python3
"""Score B05 candidate pools against complete human event reviews on frozen test groups."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile

BENCHMARK_VERSION = "event-benchmark-v1"
EVENT_TYPES = {"drop", "build", "breakdown", "chorus", "other"}
POOLS = {"audio_only", "anchor_assisted"}


def _load_json(path, label):
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(label + " is missing or not valid JSON: " + str(error)) from error


def _digest(value, label):
    if not isinstance(value, str) or len(value) != 64 \
            or any(character not in "0123456789abcdef" for character in value.lower()):
        raise ValueError(label + " must be a SHA-256 hex digest.")
    return value.lower()


def _number(value, label, *, minimum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + " must be a finite number.")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ValueError(label + " must be at least " + str(minimum) + ".")
    return value


def _index_dataset(dataset):
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1:
        raise ValueError("Frozen dataset must use schema_version 1.")
    tracks = dataset.get("tracks")
    groups = dataset.get("groups")
    if not isinstance(tracks, list) or not isinstance(groups, list):
        raise ValueError("Frozen dataset must contain tracks and groups arrays.")
    group_by_id = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("group_id"), str):
            raise ValueError("Frozen dataset contains a malformed split group.")
        group_id, split = group["group_id"], group.get("split")
        if not isinstance(split, str) or split not in {"train", "validation", "test"}:
            raise ValueError("Frozen dataset group has an invalid split.")
        if group_id in group_by_id:
            raise ValueError("Frozen dataset contains duplicate group IDs.")
        group_by_id[group_id] = group
    indexed = {}
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError("Frozen dataset contains a malformed track.")
        digest = _digest(track.get("audio_sha256"), "Dataset audio_sha256")
        group_id = track.get("group_id")
        group = group_by_id.get(group_id)
        if group is None or track.get("split") != group["split"]:
            raise ValueError("Frozen dataset track split does not match its related-mix group.")
        if digest in indexed:
            raise ValueError("Frozen dataset contains duplicate audio hashes.")
        indexed[digest] = {
            "audio_sha256": digest, "split": group["split"], "group_id": group_id,
            "grouping_method": track.get("grouping_method", group.get("grouping_method")),
        }
    return indexed


def _index_reviews(document, dataset):
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Event-label export must use schema_version 1.")
    reviews = document.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("Event-label export must contain a reviews array.")
    indexed = {}
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("Event-label export contains a malformed review.")
        digest = _digest(review.get("track_hash"), "Event review track_hash")
        if digest not in dataset:
            continue
        if digest in indexed:
            raise ValueError("Event-label export contains duplicate track reviews.")
        if review.get("source") != "human_review":
            raise ValueError("Event benchmark accepts only explicit human-review records.")
        state = review.get("state")
        if not isinstance(state, str) or state not in {
                "pending", "in_progress", "complete", "uncertain", "skipped"}:
            raise ValueError("Event-label export contains an invalid review state.")
        annotations = review.get("annotations")
        if not isinstance(annotations, list):
            raise ValueError("Event review annotations must be an array.")
        grid = review.get("grid", {})
        if not isinstance(grid, dict):
            raise ValueError("Event review grid metadata must be an object.")
        certain, uncertain = [], []
        for annotation in annotations:
            if not isinstance(annotation, dict):
                raise ValueError("Event review contains a malformed annotation.")
            if annotation.get("active") is False:
                continue
            event_type = annotation.get("event_type")
            if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
                raise ValueError("Event annotation has an unsupported event type.")
            if annotation.get("source") != "human_review":
                raise ValueError("Event benchmark accepts only human-reviewed annotations.")
            time_s = _number(annotation.get("time_s"), "Event annotation time", minimum=0)
            target = {"event_type": event_type, "time_s": time_s,
                      "certainty": annotation.get("certainty")}
            if annotation.get("certainty") == "certain":
                certain.append(target)
            elif annotation.get("certainty") == "uncertain":
                uncertain.append(target)
            else:
                raise ValueError("Event annotation certainty must be certain or uncertain.")
        indexed[digest] = {
            "state": state, "certain": sorted(certain, key=lambda item: item["time_s"]),
            "uncertain": sorted(uncertain, key=lambda item: item["time_s"]),
            "grid": grid,
        }
    return indexed


def _index_candidates(document, dataset):
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("B05 candidate report must use schema_version 1.")
    if not isinstance(document.get("detector_version"), str):
        raise ValueError("Candidate report is missing its detector version.")
    tracks = document.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError("Candidate report must contain a tracks array.")
    indexed = {}
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError("Candidate report contains a malformed track.")
        digest = _digest(track.get("audio_sha256"), "Candidate report audio_sha256")
        if digest not in dataset:
            continue
        if digest in indexed:
            raise ValueError("Candidate report contains duplicate track hashes.")
        pool_data = track.get("candidates", {})
        if not isinstance(pool_data, dict):
            raise ValueError("Candidate track candidates must be an object.")
        pools = {}
        for name in POOLS:
            if name not in pool_data:
                continue
            points = pool_data[name]
            if not isinstance(points, list):
                raise ValueError("Candidate pool must be an array: " + name)
            normalized = []
            for point in points:
                if not isinstance(point, dict):
                    raise ValueError("Candidate pool contains a malformed candidate.")
                time_s = _number(point.get("time_s"), "Candidate time", minimum=0)
                normalized.append({"time_s": time_s, "source": point.get("source")})
            pools[name] = sorted(normalized, key=lambda point: point["time_s"])
        indexed[digest] = {"status": track.get("status"), "pools": pools}
    return indexed


def _match(candidates, targets, uncertain_targets, tolerance_s):
    predictions = sorted(candidates, key=lambda item: item["time_s"])
    truths = sorted(targets, key=lambda item: item["time_s"])
    matched, unmatched_predictions, unmatched_targets = [], [], []
    prediction_index = target_index = 0
    while prediction_index < len(predictions) and target_index < len(truths):
        prediction = predictions[prediction_index]
        target = truths[target_index]
        delta = prediction["time_s"] - target["time_s"]
        if delta < -tolerance_s:
            unmatched_predictions.append(prediction)
            prediction_index += 1
        elif delta > tolerance_s:
            unmatched_targets.append(target)
            target_index += 1
        else:
            matched.append({
                "event_type": target["event_type"], "target_time_s": target["time_s"],
                "candidate_time_s": prediction["time_s"], "error_s": abs(delta),
                "candidate_source": prediction.get("source"),
            })
            prediction_index += 1
            target_index += 1
    unmatched_predictions.extend(predictions[prediction_index:])
    unmatched_targets.extend(truths[target_index:])
    excluded_predictions = []
    false_positives = []
    for prediction in unmatched_predictions:
        if any(abs(prediction["time_s"] - target["time_s"]) <= tolerance_s
               for target in uncertain_targets):
            excluded_predictions.append(prediction)
        else:
            false_positives.append(prediction)
    return matched, false_positives, unmatched_targets, excluded_predictions


def _score_pool(test_tracks, reviews, predictions, pool, tolerance_beats, fallback_tolerance_s):
    details, matches, false_positives, misses, excluded = [], [], [], [], []
    candidate_total = 0
    complete_track_count = 0
    event_counts = {event_type: {"targets": 0, "covered": 0, "timing_errors_s": []}
                    for event_type in sorted(EVENT_TYPES)}
    for digest, metadata in sorted(test_tracks.items()):
        review = reviews.get(digest)
        if review is None or review["state"] != "complete":
            continue
        complete_track_count += 1
        prediction_row = predictions.get(digest)
        candidates = [] if prediction_row is None else prediction_row["pools"].get(pool, [])
        valid = prediction_row is not None and prediction_row["status"] == "done" and pool in prediction_row["pools"]
        if not valid:
            details.append({
                "audio_sha256": digest, "group_id": metadata["group_id"],
                "status": "missing_candidate_pool", "review_state": review["state"],
                "target_count": len(review["certain"]), "candidate_count": None,
            })
            continue
        candidate_total += len(candidates)
        targets = review["certain"]
        tolerances = []
        review_grid = None
        review_grid = review.get("grid")
        if review_grid and review_grid.get("bar_s") is not None:
            beat_s = _number(review_grid.get("bar_s"), "Review grid bar duration", minimum=0.0000001) / 4
            tolerance = beat_s * tolerance_beats
            tolerance_source = "review_grid"
        else:
            tolerance = fallback_tolerance_s
            tolerance_source = "fallback_seconds"
        tolerances.append(tolerance)
        matched, false, missed, excluded_candidates = _match(
            candidates, targets, review["uncertain"], tolerance)
        matches.extend(matched)
        false_positives.extend(false)
        misses.extend(missed)
        excluded.extend(excluded_candidates)
        by_type = {event_type: {"targets": 0, "covered": 0} for event_type in sorted(EVENT_TYPES)}
        for target in targets:
            event_counts[target["event_type"]]["targets"] += 1
            by_type[target["event_type"]]["targets"] += 1
        for item in matched:
            event_counts[item["event_type"]]["covered"] += 1
            event_counts[item["event_type"]]["timing_errors_s"].append(item["error_s"])
            by_type[item["event_type"]]["covered"] += 1
        details.append({
            "audio_sha256": digest, "group_id": metadata["group_id"],
            "status": "scored", "review_state": review["state"],
            "target_count": len(targets), "uncertain_target_count": len(review["uncertain"]),
            "candidate_count": len(candidates), "matched_count": len(matched),
            "false_positive_candidate_count": len(false),
            "uncertainty_excluded_candidate_count": len(excluded_candidates),
            "missed_target_count": len(missed), "tolerance_s": tolerance,
            "tolerance_source": tolerance_source, "by_event_type": by_type,
        })
    scored = [row for row in details if row["status"] == "scored"]
    target_total = sum(row["target_count"] for row in scored)
    matched_total = len(matches)
    output_by_type = {}
    for event_type, counts in event_counts.items():
        target_count = counts["targets"]
        output_by_type[event_type] = {
            "targets": target_count, "covered": counts["covered"],
            "recall": round(counts["covered"] / target_count, 4) if target_count else None,
            "mean_absolute_timing_error_s": (
                round(sum(counts["timing_errors_s"]) / len(counts["timing_errors_s"]), 4)
                if counts["timing_errors_s"] else None
            ),
        }
    return {
        "complete_test_tracks": complete_track_count,
        "scored_tracks": len(scored),
        "missing_candidate_pool_tracks": complete_track_count - len(scored),
        "groups_scored": len({row["group_id"] for row in scored}),
        "positioned_targets": target_total,
        "matched_targets": matched_total,
        "target_recall": round(matched_total / target_total, 4) if target_total else None,
        "candidate_count": candidate_total,
        "false_positive_candidates": len(false_positives),
        "uncertainty_excluded_candidates": len(excluded),
        "candidate_precision": round(matched_total / candidate_total, 4) if candidate_total else None,
        "mean_absolute_timing_error_s": (
            round(sum(item["error_s"] for item in matches) / len(matches), 4) if matches else None
        ),
        "median_absolute_timing_error_s": (
            _median([item["error_s"] for item in matches]) if matches else None
        ),
        "tolerance_beats": tolerance_beats,
        "fallback_tolerance_s": fallback_tolerance_s,
        "by_event_type": output_by_type,
        "tracks": details,
        "matches": matches,
        "false_positive_details": false_positives,
        "missed_targets": misses,
    }


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 4)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 4)


def build_benchmark(dataset_document, review_document, candidate_document, *,
                    pools=("audio_only", "anchor_assisted"), tolerance_beats=4.0,
                    fallback_tolerance_s=2.0):
    tolerance_beats = _number(tolerance_beats, "Tolerance in beats")
    fallback_tolerance_s = _number(fallback_tolerance_s, "Fallback tolerance in seconds")
    if not 0 < tolerance_beats <= 64:
        raise ValueError("Tolerance in beats must be greater than 0 and no more than 64.")
    if not 0 < fallback_tolerance_s <= 30:
        raise ValueError("Fallback tolerance must be greater than 0 and no more than 30 seconds.")
    if not pools or any(pool not in POOLS for pool in pools) or len(set(pools)) != len(pools):
        raise ValueError("Choose one or both supported candidate pools.")
    dataset = _index_dataset(dataset_document)
    reviews = _index_reviews(review_document, dataset)
    candidates = _index_candidates(candidate_document, dataset)
    test_tracks = {digest: row for digest, row in dataset.items() if row["split"] == "test"}
    group_splits = {}
    for digest, row in dataset.items():
        previous = group_splits.setdefault(row["group_id"], row["split"])
        if previous != row["split"]:
            raise ValueError("Related mix group leaks across dataset splits.")
    complete_reviews = {
        digest: review for digest, review in reviews.items()
        if dataset[digest]["split"] == "test" and review["state"] == "complete"
    }
    skipped_test_reviews = {
        "pending": 0, "in_progress": 0, "uncertain": 0, "skipped": 0, "missing": 0,
    }
    for digest in test_tracks:
        review = reviews.get(digest)
        state = review["state"] if review else "missing"
        if state in skipped_test_reviews:
            skipped_test_reviews[state] += 1
    pool_results = {
        pool: _score_pool(test_tracks, complete_reviews, candidates, pool,
                          tolerance_beats, fallback_tolerance_s)
        for pool in pools
    }
    reviewed_groups = {dataset[digest]["group_id"] for digest in complete_reviews}
    return {
        "schema_version": 1, "benchmark_version": BENCHMARK_VERSION,
        "candidate_detector_version": candidate_document["detector_version"],
        "split_policy": "Frozen B03 test split only; exact hashes joined across local exports.",
        "related_mix_grouping": {
            "test_groups": len({row["group_id"] for row in test_tracks.values()}),
            "reviewed_test_groups": len(reviewed_groups),
            "grouping_review_required": (
                dataset_document.get("grouping", {}).get("review_required", True)
                if isinstance(dataset_document.get("grouping", {}), dict) else True
            ),
            "grouping_note": (
                dataset_document.get("grouping", {}).get("note", "")
                if isinstance(dataset_document.get("grouping", {}), dict) else ""
            ),
        },
        "counts": {
            "dataset_tracks": len(dataset),
            "test_tracks": len(test_tracks),
            "complete_human_review_test_tracks": len(complete_reviews),
            "complete_test_reviewed_groups": len(reviewed_groups),
            "excluded_test_reviews": skipped_test_reviews,
        },
        "evaluation_policy": {
            "only_complete_human_reviews_are_scored": True,
            "uncertain_annotations_are_not_positive_or_negative_targets": True,
            "unlabeled_tracks_or_sections_are_not_negatives": True,
            "candidate_type_classification_is_not_evaluated": True,
            "matching": "Ordered one-to-one candidate-to-event matching within tolerance.",
            "false_positive_policy": (
                "Unmatched candidates count as false positives only on tracks marked complete; "
                "candidates near individually uncertain labels are excluded."
            ),
            "limitations": (
                "This is a local held-out candidate-coverage benchmark, not a musical-accuracy "
                "or model-promotion result. Metadata-derived mix groups still require human review."
            ),
        },
        "pools": pool_results,
    }


def write_report(result, output_path, protected_paths):
    output = Path(output_path).expanduser()
    if output.is_symlink():
        raise ValueError("Benchmark report output must not be a symlink.")
    output = output.resolve()
    if output.exists() and output.is_dir():
        raise ValueError("Benchmark report output must be a file path.")
    if any(output == Path(path).expanduser().resolve() for path in protected_paths):
        raise ValueError("Benchmark report must not overwrite an input file.")
    if output.exists():
        raise ValueError("Benchmark report already exists; choose a new output path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".event-benchmark-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            json.dump(result, destination, ensure_ascii=True, allow_nan=False, indent=2)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_name, output)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Frozen B03 dataset manifest.")
    parser.add_argument("--reviews", required=True, help="B06 human event-label JSON export.")
    parser.add_argument("--candidates", required=True, help="B05 candidate report JSON.")
    parser.add_argument("--pools", default="audio_only,anchor_assisted",
                        help="Comma-separated candidate pools to score.")
    parser.add_argument("--tolerance-beats", type=float, default=4.0,
                        help="Matching tolerance in beats when the reviewed grid exists.")
    parser.add_argument("--fallback-tolerance-seconds", type=float, default=2.0,
                        help="Matching tolerance for gridless reviews.")
    parser.add_argument("--out", required=True, help="New versioned JSON benchmark report path.")
    args = parser.parse_args(argv)
    try:
        paths = [args.dataset, args.reviews, args.candidates]
        result = build_benchmark(
            _load_json(args.dataset, "Dataset manifest"),
            _load_json(args.reviews, "Event-label export"),
            _load_json(args.candidates, "Candidate report"),
            pools=tuple(item.strip() for item in args.pools.split(",") if item.strip()),
            tolerance_beats=args.tolerance_beats,
            fallback_tolerance_s=args.fallback_tolerance_seconds,
        )
        output = write_report(result, args.out, paths)
        summary = {
            "report_path": str(output), "counts": result["counts"],
            "pools": {
                name: {
                    key: data[key] for key in (
                        "scored_tracks", "groups_scored", "positioned_targets",
                        "target_recall", "candidate_count", "false_positive_candidates",
                        "candidate_precision", "mean_absolute_timing_error_s",
                    )
                }
                for name, data in result["pools"].items()
            },
        }
        print(json.dumps(summary, allow_nan=False))
        return 2 if not result["counts"]["complete_human_review_test_tracks"] else 0
    except (OSError, ValueError, RuntimeError) as error:
        print("Event benchmark failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
