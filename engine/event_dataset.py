#!/usr/bin/env python3
"""Build local positive/weak/unlabeled bar samples without exposing held-out test data."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

import anchor_detector
import event_benchmark
from timing import grid_origin

BUILDER_VERSION = "event-dataset-v1"
FEATURE_SCHEMA = "bar-rms-low-coverage-v1"


def _human_labels(review):
    labels = []
    seen = set()
    for annotation in review.get("annotations", []):
        if type(annotation.get("active")) is not bool:
            raise ValueError("Human annotation must have an explicit active/retracted decision.")
        if not annotation["active"]:
            continue
        if annotation.get("retracted_at") is not None:
            raise ValueError("Active human annotation cannot have a retraction timestamp.")
        annotation_id = annotation.get("id")
        if type(annotation_id) is not int or annotation_id < 1 or annotation_id in seen:
            raise ValueError("Human annotation IDs must be positive and unique within a track.")
        seen.add(annotation_id)
        original_grid = annotation.get("grid", {})
        if not isinstance(original_grid, dict):
            raise ValueError("Human annotation grid must be an object.")
        if original_grid:
            grid_origin(original_grid)
        note = annotation.get("note", "")
        created_at = annotation.get("created_at")
        if not isinstance(note, str) or not isinstance(created_at, str) or not created_at:
            raise ValueError("Human annotation must preserve its note and creation timestamp.")
        labels.append({
            "annotation_id": annotation_id, "source": annotation["source"],
            "event_type": annotation["event_type"], "certainty": annotation["certainty"],
            "time_s": float(annotation["time_s"]), "grid": original_grid,
            "note": note, "created_at": created_at,
        })
    return sorted(labels, key=lambda item: (item["time_s"], item["annotation_id"]))


def _bar_samples(arrays, quality, metadata, review):
    if arrays["bar_index"].dtype.kind not in "iu":
        raise ValueError("Cached feature bar indices must be integers.")
    bars, origin, bar_s, duration, score_at = anchor_detector._audio_scores(arrays, quality)
    rows = {
        bar: {
            "bar": bar, "time_s": origin + bar * bar_s,
            "features": {"rms": values["rms"], "low_energy": values["low"],
                         "coverage": values["coverage"]},
            "audio_evidence": score_at(bar), "weak_labels": [], "human_labels": [],
        }
        for bar, values in sorted(bars.items())
    }
    audio = anchor_detector._audio_candidates(bars, origin, bar_s, score_at)
    imported, ignored = anchor_detector._anchor_candidates(metadata, origin, bar_s, duration, score_at)
    unmapped = []
    for candidate in audio + imported:
        label = {
            "source": candidate["source"], "time_s": candidate["time_s"],
            "event_type": None, "strength": "weak",
            "audio_evidence": candidate["audio_evidence"],
        }
        if "marker" in candidate:
            label["marker"] = candidate["marker"]
        if candidate["bar"] not in rows:
            unmapped.append({"label": label, "reason": "nearest_grid_bar_has_no_features"})
        else:
            rows[candidate["bar"]]["weak_labels"].append(label)
    state = review["state"] if review else "pending"
    for label in _human_labels(review or {}):
        if label["time_s"] > duration + 0.05:
            unmapped.append({"label": label, "reason": "human_label_outside_audio"})
            continue
        bar = math.floor((label["time_s"] - origin) / bar_s + 0.5)
        if bar not in rows:
            unmapped.append({"label": label, "reason": "nearest_grid_bar_has_no_features"})
            continue
        rows[bar]["human_labels"].append({
            **label, "grid_distance_s": label["time_s"] - rows[bar]["time_s"],
        })
    for row in rows.values():
        human = row["human_labels"]
        if any(label["certainty"] == "uncertain" for label in human):
            supervision, target = "uncertain", None
        elif human and state == "complete":
            supervision = "human_positive"
            target = sorted({label["event_type"] for label in human})
        elif human:
            supervision, target = "incomplete_review", None
        elif row["weak_labels"]:
            supervision, target = "weak_candidate", None
        else:
            supervision, target = "unlabeled", None
        row.update({
            "feature_usable": row["features"]["coverage"] >= anchor_detector.MIN_BAR_COVERAGE,
            "supervision": supervision, "event_target": target,
        })
    return list(rows.values()), unmapped, ignored


def _output_path(path, source_roots, cache_dir, protected):
    output = Path(path).expanduser()
    if output.is_symlink():
        raise ValueError("Learning dataset output must not be a symlink.")
    output = output.resolve()
    if any(output == item.resolve() for item in protected):
        raise ValueError("Learning dataset must not overwrite an input.")
    if output.exists():
        raise ValueError("Learning dataset already exists; choose a new output path.")
    for directory in [*source_roots, cache_dir]:
        directory = Path(directory).resolve()
        if output == directory or directory in output.parents:
            raise ValueError("Learning dataset must be outside audio source roots and feature cache.")
    return output


def build_dataset(database, dataset_path, cache_dir, review_path, output_path, *, limit=20):
    if type(limit) is not int or limit < 1:
        raise ValueError("Feature-track limit must be a positive integer.")
    dataset_path = Path(dataset_path).expanduser().resolve()
    review_path = Path(review_path).expanduser().resolve()
    dataset_bytes, review_bytes = dataset_path.read_bytes(), review_path.read_bytes()
    dataset, review_document = json.loads(dataset_bytes), json.loads(review_bytes)
    indexed = event_benchmark._index_dataset(dataset)
    non_test = {digest: track for digest, track in indexed.items() if track["split"] != "test"}
    reviewed = event_benchmark._index_reviews(review_document, non_test)
    raw_reviews = {
        item["track_hash"].lower(): item for item in review_document["reviews"]
        if item["track_hash"].lower() in reviewed
    }
    cache_dir, tasks, source_roots, grid_path = anchor_detector._load_feature_cache(
        database, dataset_path, cache_dir)
    protected = [dataset_path, review_path, Path(database).expanduser().resolve()]
    if grid_path:
        protected.append(Path(grid_path).expanduser())
    output = _output_path(output_path, source_roots, cache_dir, protected)
    tracks, attempted = [], 0
    counts = {
        "dataset_tracks": len(indexed), "feature_tracks_included": 0,
        "unmatched_review_tracks": sum(
            item["track_hash"].lower() not in indexed for item in review_document["reviews"]),
        "reserved_test_tracks": 0, "excluded_review_tracks": 0,
        "feature_unavailable_tracks": 0, "deferred_tracks": 0, "bar_samples": 0,
        "human_positive_bars": 0, "weak_candidate_bars": 0, "unlabeled_bars": 0,
        "uncertain_bars": 0, "incomplete_review_bars": 0, "negative_bars": 0,
        "unmapped_labels": 0, "eligible_training_bars": 0, "eligible_weak_learning_bars": 0,
        "eligible_validation_bars": 0,
    }
    for task, job in sorted(tasks, key=lambda pair: pair[0]["audio_sha256"]):
        digest = task["audio_sha256"]
        track = dict(indexed[digest])
        tracks.append(track)
        if track["split"] == "test":
            track["status"] = "reserved_test"
            counts["reserved_test_tracks"] += 1
            continue
        review = raw_reviews.get(digest)
        state = reviewed[digest]["state"] if digest in reviewed else "pending"
        track["review_state"] = state
        if state in {"uncertain", "skipped"}:
            track["status"] = "excluded_review"
            counts["excluded_review_tracks"] += 1
            continue
        if job["state"] != "done":
            track["status"] = "feature_unavailable"
            track["feature_state"] = job["state"]
            track["detail"] = job.get("error") or json.loads(job["quality_json"] or "{}").get("flags", [])
            counts["feature_unavailable_tracks"] += 1
            continue
        if attempted >= limit:
            track["status"] = "deferred"
            counts["deferred_tracks"] += 1
            continue
        attempted += 1
        arrays, quality = anchor_detector._read_artifact(cache_dir, job, digest)
        samples, unmapped, ignored = _bar_samples(arrays, quality, task["metadata"], review)
        for row in samples:
            row["eligible_for_training"] = (
                track["split"] == "train" and row["feature_usable"]
                and row["supervision"] == "human_positive")
            row["eligible_for_weak_learning"] = (
                track["split"] == "train" and row["feature_usable"]
                and row["supervision"] == "weak_candidate")
            row["eligible_for_validation"] = (
                track["split"] == "validation" and row["feature_usable"]
                and row["supervision"] == "human_positive")
            counts[row["supervision"] + "_bars"] += 1
            counts["eligible_training_bars"] += int(row["eligible_for_training"])
            counts["eligible_weak_learning_bars"] += int(row["eligible_for_weak_learning"])
            counts["eligible_validation_bars"] += int(row["eligible_for_validation"])
        track.update({
            "status": "included", "artifact_sha256": job["artifact_sha256"],
            "feature_quality": quality, "samples": samples, "unmapped_labels": unmapped,
            "ignored_imported_anchors": ignored,
            "review_provenance": {
                "source": review.get("source"), "note": review.get("note", ""),
                "grid": review.get("grid", {}), "updated_at": review.get("updated_at"),
            } if review else None,
        })
        counts["feature_tracks_included"] += 1
        counts["bar_samples"] += len(samples)
        counts["unmapped_labels"] += len(unmapped)
    if dataset_path.read_bytes() != dataset_bytes or review_path.read_bytes() != review_bytes:
        raise ValueError("Dataset or human-review inputs changed during the build; retry with frozen inputs.")
    result = {
        "schema_version": 1, "builder_version": BUILDER_VERSION, "feature_schema": FEATURE_SCHEMA,
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "reviews_sha256": hashlib.sha256(review_bytes).hexdigest(),
        "candidate_detector_version": anchor_detector.DETECTOR_VERSION, "feature_track_limit": limit,
        "grouping": dataset.get("grouping", {}), "counts": counts, "tracks": tracks,
        "supervision_policy": {
            "human_targets": "Certain active annotations from complete human reviews only.",
            "weak_labels": "Untyped audio changes and imported anchors; never human event targets.",
            "unmarked_bars": "Unlabeled, including complete reviews with no annotations; never negative.",
            "uncertain_labels": "Preserved but masked; any uncertain annotation masks its nearest bar.",
            "test_split": "Reserved: no test features, labels or imported-marker metadata are included.",
            "limitations": "Positive/weak/unlabeled preparation only. No model is trained or validated.",
        },
    }
    encoded = (json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".event-dataset-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary_name, output, follow_symlinks=False)
    finally:
        os.unlink(temporary_name)
    return {"path": str(output), "sha256": hashlib.sha256(encoded).hexdigest(), "counts": counts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Inventory used by the frozen dataset.")
    parser.add_argument("--dataset", required=True, help="Frozen B03 dataset manifest.")
    parser.add_argument("--cache-dir", required=True, help="B04 feature cache for that manifest.")
    parser.add_argument("--reviews", required=True, help="B06 event-label JSON export.")
    parser.add_argument("--out", required=True, help="New JSON output outside source roots/cache.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum feature tracks to include (default 20).")
    args = parser.parse_args(argv)
    try:
        result = build_dataset(args.database, args.dataset, args.cache_dir, args.reviews,
                               args.out, limit=args.limit)
        print(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, UnicodeError, ValueError, RuntimeError, sqlite3.Error) as error:
        print("Event dataset failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
