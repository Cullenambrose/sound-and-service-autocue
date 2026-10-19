"""Build a local review-ui data.json showing v6 cues beside the saved v5 plan."""
import argparse
import csv
import json
import sys
import os
import time


def read_json(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def build(root, manifest, v5_dir, v6_dir, features_dir, output):
    import numpy as np

    with open(manifest, newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    tracks, skipped = [], []
    for row in rows:
        track_id = (row.get("id") or row.get("n") or "").strip().zfill(2)
        if not track_id:
            raise ValueError("manifest contains a row with an empty track ID")
        if os.path.basename(track_id) != track_id or track_id in (".", ".."):
            raise ValueError(f"manifest track ID is not a safe file name: {track_id!r}")
        v6_path = os.path.join(v6_dir, track_id + ".json")
        v5_path = os.path.join(v5_dir, track_id + ".json")
        feature_path = os.path.join(features_dir, track_id + ".npz")
        if not all(os.path.exists(path) for path in (v6_path, v5_path, feature_path)):
            skipped.append(f"{track_id}: missing v5/v6 plan or features")
            continue
        v6, v5 = read_json(v6_path), read_json(v5_path)
        analysis = read_json(os.path.join(root, "cues", "allin1", track_id + ".json"))
        grid = analysis["grid"]
        features = np.load(feature_path)
        peak = features["peak"]
        step = max(1, len(peak) // 1600)
        peaks = [round(float(value), 3) for value in peak[:step * (len(peak) // step)].reshape(-1, step).max(1)]
        cues = {}
        for slot, cue in v6["cues"].items():
            cues[slot] = {
                "name": cue["name"],
                "bar": cue.get("bar"),
                "beat": None if cue.get("bar") is None else cue["bar"] * 4,
                "source": "suggest" if cue.get("bar") is not None else "empty",
                "why": cue.get("reason", ""),
                "confidence": cue.get("confidence"),
                "flags": cue.get("flags", []),
            }
        v5_cues = {}
        for slot, cue in v5.get("cues", {}).items():
            if slot in "ABCDEFGH":
                v5_cues[slot] = {"name": cue.get("name"), "bar": cue.get("bar")}
        audio_file = row.get("file") or row.get("filename") or row.get("path") or row.get("laptop_path") or ""
        name = " - ".join(filter(None, (row.get("artist"), row.get("title"))))
        tracks.append({
            "id": track_id,
            "name": name or row.get("name") or os.path.basename(audio_file),
            "file": os.path.basename(audio_file),
            "t1": round(grid["t1"] - grid.get("offset_ms", 0) / 1000, 4),
            "bar_s": grid["bar_s"],
            "bpm": round(240 / grid["bar_s"], 2),
            "dur": float(features["dur"]),
            "peaks": peaks,
            "cues": cues,
            "v5_cues": v5_cues,
            "loops": [],
            "editable_slots": True,
            "cue_order": list("ABCDEFGH"),
            "map_type": v6["map_type"],
            "flags": v6.get("flags", []),
        })
    os.makedirs(output, exist_ok=True)
    target = os.path.join(output, "data.json")
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"built": time.strftime("%Y-%m-%d %H:%M"),
                   "engine": "AutoCue v6 beside v5", "tracks": tracks}, stream)
    os.replace(temporary, target)
    return len(tracks), skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Local analysis root")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--v5-dir", required=True)
    parser.add_argument("--v6-dir", required=True)
    parser.add_argument("--features-dir", required=True)
    parser.add_argument("--out", required=True, help="Local review data directory")
    args = parser.parse_args()
    count, skipped = build(args.root, args.manifest, args.v5_dir, args.v6_dir,
                           args.features_dir, args.out)
    print("built", count, "review tracks")
    for issue in skipped:
        print("SKIP", issue, file=sys.stderr)
    if skipped:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
