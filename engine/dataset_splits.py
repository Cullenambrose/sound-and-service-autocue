#!/usr/bin/env python3
"""Freeze read-only, grouped dataset splits from a completed metadata inventory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unicodedata

import library_inventory as inventory

SCHEMA_VERSION = 1
VERSION_SUFFIXES = {
    "acapella", "acapella version", "club mix", "club remix", "dub", "dub mix",
    "extended", "extended mix", "instrumental", "instrumental version", "live",
    "mono", "original mix", "radio edit", "radio version", "remaster",
    "remastered", "remix", "rework", "short edit", "stereo",
}


def _text(values):
    if not isinstance(values, list):
        return ""
    return " ".join(str(value) for value in values if value).strip()


def _normalize(value):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(character for character in decomposed
                            if not unicodedata.combining(character)).split())


def _base_title(title):
    normalized = _normalize(title)
    for opener, closer in ((" (", ")"), (" [", "]")):
        if normalized.endswith(closer):
            position = normalized.rfind(opener)
            if position >= 0 and normalized[position + len(opener):-1] in VERSION_SUFFIXES:
                return normalized[:position].strip()
    for suffix in sorted(VERSION_SUFFIXES, key=len, reverse=True):
        marker = " - " + suffix
        if normalized.endswith(marker):
            return normalized[:-len(marker)].strip()
    return normalized


def _hash_file(root, entry):
    descriptor = inventory._open_beneath(root, entry["relative_path"])
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as incoming:
        before = os.fstat(incoming.fileno())
        expected = tuple(entry[key] for key in
                         ("size_bytes", "mtime_ns", "ctime_ns", "device", "inode"))
        if not stat.S_ISREG(before.st_mode) or inventory._fingerprint(before) != expected:
            raise ValueError("Source changed after inventory; start a new scan.")
        for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
            digest.update(chunk)
        if inventory._fingerprint(os.fstat(incoming.fileno())) != expected:
            raise ValueError("Source changed while hashing; start a new scan.")
    return digest.hexdigest()


def _split(group_id, seed, ratios):
    value = int(hashlib.sha256((seed + "\0" + group_id).encode("utf-8")).hexdigest(), 16)
    fraction = value / (1 << 256)
    train, validation, _ = ratios
    if fraction < train:
        return "train"
    if fraction < train + validation:
        return "validation"
    return "test"


def _ratios(values):
    parts = values.split(",")
    if len(parts) != 3:
        raise ValueError("Split ratios must be train,validation,test (for example 0.7,0.15,0.15).")
    try:
        parsed = tuple(float(part) for part in parts)
    except ValueError as error:
        raise ValueError("Split ratios must be numeric.") from error
    if any(not 0 < value < 1 for value in parsed) or abs(sum(parsed) - 1) > 1e-9:
        raise ValueError("Split ratios must each be between 0 and 1 and sum to 1.")
    return parsed


def _track_records(db, scan_id, roots):
    rows = db.execute("""SELECT * FROM entries WHERE scan_id=? AND kind='audio'
                         ORDER BY root_id,relative_path""", (scan_id,)).fetchall()
    tracks = {}
    excluded = []
    for row in rows:
        entry = dict(row)
        if entry["status"] != "done":
            excluded.append({"root_id": entry["root_id"], "path": entry["relative_path"],
                             "status": entry["status"], "error": entry["error"]})
            continue
        metadata = json.loads(entry["metadata_json"])
        digest = _hash_file(roots[entry["root_id"]], entry)
        track = tracks.setdefault(digest, {"audio_sha256": digest, "paths": [], "metadata": metadata})
        track["paths"].append({"root_id": entry["root_id"], "path": entry["relative_path"]})
    return list(tracks.values()), excluded


def build_manifest(database, scan_id, output, *, seed="autocue-v1", ratios=(0.7, 0.15, 0.15)):
    output = Path(output).expanduser()
    if output.is_symlink():
        raise ValueError("Dataset manifest output must not be a symlink.")
    output = output.resolve()
    if output.suffix.lower() != ".json":
        raise ValueError("Dataset manifest output must use a .json extension.")
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("Split seed must be a non-empty string.")
    ratios = tuple(ratios)
    if len(ratios) != 3 or any(type(value) not in (int, float) for value in ratios):
        raise ValueError("Exactly three numeric split ratios are required.")
    if any(not 0 < value < 1 for value in ratios) or abs(sum(ratios) - 1) > 1e-9:
        raise ValueError("Split ratios must each be between 0 and 1 and sum to 1.")

    with inventory._database(database) as db:
        summary = inventory._summary(db, scan_id)
        if summary["status"] not in {"complete", "complete_with_errors"}:
            raise ValueError("Inventory scan must be complete before freezing splits.")
        roots = {row["id"]: dict(row) for row in db.execute(
            "SELECT * FROM roots WHERE scan_id=?", (scan_id,))}
        inventory._check_output(output, [Path(root["path"]) for root in roots.values()])
        tracks, excluded = _track_records(db, scan_id, roots)
    if not tracks:
        raise ValueError("No successfully inventoried audio files are available to split.")

    groups = {}
    for track in tracks:
        metadata = track["metadata"]
        artist = _normalize(_text(metadata.get("artist")))
        title = _base_title(_text(metadata.get("title")))
        if artist and title:
            group_key = "metadata\0" + artist + "\0" + title
            method = "normalized_artist_title"
        else:
            group_key = "audio\0" + track["audio_sha256"]
            method = "audio_sha256"
        group_id = hashlib.sha256(group_key.encode("utf-8")).hexdigest()
        group = groups.setdefault(group_id, {
            "group_id": group_id, "grouping_method": method, "artist": artist or None,
            "base_title": title or None, "audio_sha256s": [], "track_count": 0,
            "split": _split(group_id, seed, ratios),
        })
        group["audio_sha256s"].append(track["audio_sha256"])
        group["track_count"] += 1
        track["group_id"] = group_id
        track["split"] = group["split"]
        track["grouping_method"] = method

    dataset = {
        "schema_version": SCHEMA_VERSION,
        "created_from_scan": scan_id,
        "seed": seed,
        "split_ratios": {"train": ratios[0], "validation": ratios[1], "test": ratios[2]},
        "grouping": {
            "exact_duplicates": "SHA-256 of complete file bytes",
            "alternate_mix_candidates": "normalized artist and title with recognized trailing version suffix removed",
            "review_required": True,
            "note": "Metadata groups are conservative split groups, not verified song identity; review before model evaluation.",
        },
        "source_roots": [{"root_id": root["id"], "path": root["path"]} for root in roots.values()],
        "counts": {
            "audio_files_seen": summary["counts"].get("audio", {}).get("done", 0)
                + summary["counts"].get("audio", {}).get("error", 0),
            "tracks_after_exact_deduplication": len(tracks),
            "exact_duplicate_files_removed": sum(len(track["paths"]) - 1 for track in tracks),
            "related_mix_groups": sum(group["track_count"] > 1 for group in groups.values()),
            "excluded_files": len(excluded),
        },
        "groups": sorted(groups.values(), key=lambda item: item["group_id"]),
        "tracks": sorted(tracks, key=lambda item: item["audio_sha256"]),
        "excluded_files": excluded,
    }
    encoded = (json.dumps(dataset, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".dataset-split-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary_name, output, follow_symlinks=False)
    finally:
        os.unlink(temporary_name)
    return {"path": str(output), "sha256": hashlib.sha256(encoded).hexdigest(),
            "counts": dataset["counts"], "split_counts": {
                name: sum(track["split"] == name for track in tracks)
                for name in ("train", "validation", "test")}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Dedicated AutoCue inventory SQLite file.")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="Hash tracks and freeze reproducible grouped splits.")
    freeze.add_argument("scan_id")
    freeze.add_argument("--out", required=True, help="New JSON output path; existing files are never overwritten.")
    freeze.add_argument("--seed", default="autocue-v1")
    freeze.add_argument("--ratios", default="0.7,0.15,0.15", metavar="TRAIN,VALIDATION,TEST")
    args = parser.parse_args(argv)
    try:
        ratios = _ratios(args.ratios)
        print(json.dumps(build_manifest(args.database, args.scan_id, args.out,
                                        seed=args.seed, ratios=ratios), allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print("Dataset split failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
