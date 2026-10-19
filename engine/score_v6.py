"""Score v6 and v5 plans against local review-page corrections (never Serato/Mixed In Key)."""
import argparse
import datetime
import json
import os
import re
from pathlib import Path

SLOTS = "ABCDEFGH"


def load_marks(path):
    with open(path, encoding="utf-8") as source:
        events = json.load(source)
    last = {}
    for event in sorted(events, key=lambda item: item.get("saved_at", "")):
        slot = event.get("cue")
        track = str(event.get("track", "")).zfill(2)
        if slot not in SLOTS:
            continue
        key = (track, slot)
        if event.get("action") in ("correct", "set_here") and event.get("to_beat") is not None:
            last[key] = float(event["to_beat"])
        elif event.get("action") == "clear":
            last.pop(key, None)
    return last


def label_valid(map_type, slot, label):
    if map_type == "DROP":
        expected = {
            "A": r"INTRO -\d+", "B": r"START", "C": r"-32 DROP", "D": r"FIRST DROP",
            "E": r"-32 LAST DROP", "F": r"LAST DROP", "G": r"GO -32", "H": r"OUTRO -\d+",
        }
    else:
        expected = {
            "A": r"INTRO -\d+", "B": r"START", "C": r"EXIT -32", "D": r"EXIT -16",
            "E": r"EXIT/JUMP", "F": r"LAST CHORUS", "G": r"GO -32", "H": r"OUTRO -\d+",
        }
    return bool(re.fullmatch(expected[slot], str(label or "")))


def exact_label_slots(plan):
    cues = plan.get("cues", {})
    if set(cues) != set(SLOTS):
        return {slot: False for slot in SLOTS}
    exact = {slot: label_valid(plan.get("map_type", "VOCAL"), slot, cues[slot].get("name"))
             for slot in SLOTS}
    intro = cues["A"]
    if intro.get("bar") is not None and cues["B"].get("bar") is not None:
        match = re.fullmatch(r"INTRO -(\d+)", intro["name"])
        if not match or cues["B"]["bar"] - intro["bar"] != int(match.group(1)):
            exact["A"] = False
    outro = re.fullmatch(r"OUTRO -(\d+)", cues["H"]["name"])
    if outro and plan.get("outro_bars") is not None:
        if int(outro.group(1)) != plan["outro_bars"]:
            exact["H"] = False
    resolved = plan.get("cue_map", {}).get("resolved", {})
    if resolved:
        for slot, spec in resolved.items():
            if slot in exact:
                exact[slot] = cues.get(slot, {}).get("name") == spec.get("label")
    return exact


def labels_exact(plan):
    return all(exact_label_slots(plan).values())


def read_plan(directory, track):
    if os.path.basename(track) != track or track in (".", ".."):
        raise ValueError(f"review mark track ID is not a safe file name: {track!r}")
    path = os.path.join(directory, track + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def score(v6_dir, marks, v5_dir=None, check_labels=True):
    by_slot = {slot: {"n": 0, "n_label": 0, "v6_beat": 0, "v6_bar": 0, "v5_beat": 0, "label": 0} for slot in SLOTS}
    for (track, slot), truth_beat in marks.items():
        v6 = read_plan(v6_dir, track)
        if not v6:
            continue
        cue = v6.get("cues", {}).get(slot, {})
        v6_beat = cue.get("bar")
        v6_beat = None if v6_beat is None else int(v6_beat) * 4
        v5 = read_plan(v5_dir, track) if v5_dir else None
        old = (v5 or {}).get("cues", {}).get(slot, {}).get("bar")
        old_beat = None if old is None else int(old) * 4
        row = by_slot[slot]
        row["n"] += 1
        row["v6_beat"] += int(v6_beat is not None and abs(v6_beat - truth_beat) <= 1)
        row["v6_bar"] += int(v6_beat is not None and abs(v6_beat - truth_beat) <= 4)
        row["v5_beat"] += int(old_beat is not None and abs(old_beat - truth_beat) <= 1)
    if check_labels:
        for filename in os.listdir(v6_dir):
            if not filename.endswith(".json"):
                continue
            with open(os.path.join(v6_dir, filename), encoding="utf-8") as source:
                plan = json.load(source)
            valid = exact_label_slots(plan)
            for slot in SLOTS:
                by_slot[slot]["label"] += int(valid[slot])
                by_slot[slot]["n_label"] += 1
    return by_slot


def report_markdown(results, marks_path, v6_dir, v5_dir):
    lines = [
        "# AutoCue v6 score",
        "",
        f"- Generated: {datetime.date.today().isoformat()}",
        f"- Ground truth: `{marks_path}` (review-page corrections only)",
        f"- Plan directories: {', '.join(f'{name}=`{directory}`' for name, directory in v6_dir.items())}",
        f"- v5 plans: `{v5_dir or 'not supplied'}`",
        "",
        "| Run | Cue | Review marks | Within 1 beat | Within 1 bar | v5 within 1 beat | Exact label format |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for run_name, slots in results.items():
        for slot, row in slots.items():
            n = row["n"]
            fmt = lambda key: f"{row[key]}/{n}" if n else "—"
            label = f"{row['label']}/{row['n_label']}" if row["n_label"] else "—"
            lines.append(f"| {run_name} | {slot} | {n} | {fmt('v6_beat')} | {fmt('v6_bar')} | "
                         f"{fmt('v5_beat')} | {label} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v6-dir", required=True)
    parser.add_argument("--marks", required=True)
    parser.add_argument("--v5-dir")
    default_report = Path(__file__).resolve().parent.parent / "docs" / f"SCORE-v6-{datetime.date.today().isoformat()}.md"
    parser.add_argument("--report", default=str(default_report), help="Markdown report path")
    parser.add_argument("--variant", action="append", default=[], metavar="NAME=DIR",
                        help="Additional switch-ablation plan directory to score")
    args = parser.parse_args()
    marks = load_marks(args.marks)
    directories = [("v6", args.v6_dir)]
    if args.v5_dir:
        directories.append(("v5", args.v5_dir))
    for variant in args.variant:
        name, separator, directory = variant.partition("=")
        if not separator or not name or not directory:
            parser.error("--variant must use NAME=DIR")
        directories.append((name, directory))
    results = {name: score(directory, marks, args.v5_dir, check_labels=(name != "v5"))
               for name, directory in directories}
    markdown = report_markdown(results, args.marks, dict(directories), args.v5_dir)
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as target:
        target.write(markdown)
    print(markdown, end="")


if __name__ == "__main__":
    main()
