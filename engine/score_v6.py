"""Score v6 and v5 plans against local review-page corrections (never Serato/Mixed In Key)."""
import argparse
import datetime
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from timing import bar_to_time

SLOTS = "ABCDEFGH"


@dataclass(frozen=True)
class ReviewMark:
    beat: float
    time_s: float
    beat_s: float | None = None


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
            beat = float(event["to_beat"])
            if event.get("to_time") is not None:
                last[key] = ReviewMark(beat, float(event["to_time"]),
                                       float(event["bar_s"]) / 4 if event.get("bar_s") else None)
            elif event.get("t1") is not None and event.get("bar_s"):
                last[key] = ReviewMark(beat, bar_to_time(beat / 4, event), float(event["bar_s"]) / 4)
            else:
                last[key] = beat
        elif event.get("action") == "clear":
            last.pop(key, None)
    return last


def label_valid(map_type, slot, label):
    if map_type == "DROP":
        expected = {
            "A": r"INTRO(?: -|-)\d+", "B": r"START", "C": r"-(?:32|16|8) DROP", "D": r"FIRST DROP",
            "E": r"-(?:32|16|8) LAST DROP", "F": r"LAST DROP", "G": r"GO(?: -|-)\d+", "H": r"(?:OUTRO -\d+|EXIT-(?:\d+|END))",
        }
    else:
        expected = {
            "A": r"INTRO(?: -|-)\d+", "B": r"START", "C": r"EXIT -32", "D": r"EXIT -16",
            "E": r"EXIT/JUMP", "F": r"LAST CHORUS", "G": r"GO(?: -|-)\d+", "H": r"(?:OUTRO -\d+|EXIT-(?:\d+|END))",
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
        match = re.fullmatch(r"INTRO( -|-)(\d+)", intro["name"])
        multiplier = 4 if match and match.group(1) == "-" else 1
        if not match or (cues["B"]["bar"] - intro["bar"]) * multiplier != int(match.group(2)):
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
    for (track, slot), mark in marks.items():
        v6 = read_plan(v6_dir, track)
        if not v6:
            continue
        cue = v6.get("cues", {}).get(slot, {})
        grid = v6.get("grid", {})
        truth_beat = mark.beat if isinstance(mark, ReviewMark) else mark
        timed = isinstance(mark, ReviewMark) or bool(grid.get("bar_s") and grid.get("t1") is not None)
        truth = mark.time_s if isinstance(mark, ReviewMark) else (
            bar_to_time(truth_beat / 4, grid) if timed else truth_beat)
        tolerance = (mark.beat_s if isinstance(mark, ReviewMark) and mark.beat_s is not None
                     else float(grid["bar_s"]) / 4 if timed else 1)
        bar = cue.get("bar")
        prediction = None if bar is None else (bar_to_time(bar, grid) if timed else float(bar) * 4)
        v5 = read_plan(v5_dir, track) if v5_dir else None
        old = (v5 or {}).get("cues", {}).get(slot, {}).get("bar")
        old_grid = (v5 or {}).get("grid") or grid
        old_prediction = None if old is None else (
            bar_to_time(old, old_grid) if timed else float(old) * 4)
        row = by_slot[slot]
        row["n"] += 1
        row["v6_beat"] += int(prediction is not None and abs(prediction - truth) <= tolerance + 1e-6)
        row["v6_bar"] += int(prediction is not None and abs(prediction - truth) <= tolerance * 4 + 1e-6)
        row["v5_beat"] += int(old_prediction is not None and abs(old_prediction - truth) <= tolerance + 1e-6)
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


def library_report_markdown(report):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["# AutoCue v7 library evaluation", "", report["policy"], "",
             f"- Scored songs: {report['graded_songs']}",
             f"- Skipped songs: {len(report['skipped'])}",
             f"- Unavailable predictions: {len(report['unavailable_predictions'])}",
             "- Paired wins/losses exclude incompatible meanings; empty targets do not inflate positioned accuracy.",
             "",
             "| Variant | Positioned within 1 beat | Explicit empty correct | Mean error (s) | Fixes vs rules | Breaks vs rules |",
             "|---|---:|---:|---:|---:|---:|"]
    for key, row in report["variants"].items():
        lines.append(f"| {cell(row['label'])} | {row['positioned_right']}/{row['positioned_total']} | "
                     f"{row['empty_right']}/{row['empty_total']} | "
                     f"{row['mean_error_s'] if row['mean_error_s'] is not None else 'unknown'} | "
                     f"{row['wins'] if key != 'baseline' else '-'} | {row['losses'] if key != 'baseline' else '-'} |")
    if not report["variants"]:
        lines += ["", "No accuracy estimate is available: no scorable human-reviewed targets and predictions."]
    lines += ["", "## Map classification", "", "| Variant | Correct reviewed map |", "|---|---:|"]
    for key, value in report["map_comparison"].items():
        lines.append(f"| {cell(key)} | {value['right']}/{value['total']} |")
    lines += ["", "## Per-cue failures and exclusions", "",
              "| Song | Cue | Variant | Reason | Absolute error (s) |", "|---|---|---|---|---:|"]
    for track in report["tracks"]:
        for slot, details in track["slots"].items():
            if details.get("error"):
                lines.append(f"| {cell(track['name'])} | {slot} | - | {cell(details['error'])} | - |")
            for key, result in details["variants"].items():
                if result.get("failure"):
                    lines.append(f"| {cell(track['name'])} | {slot} | {cell(key)} | "
                                 f"{cell(result['failure'])} | {result.get('error_s', '-')} |")
    for item in report["skipped"]:
        lines.append(f"| {cell(item['name'])} | - | - | {cell(item['reason'])} | - |")
        for slot, error in item.get("cue_errors", {}).items():
            lines.append(f"| {cell(item['name'])} | {cell(slot)} | - | {cell(error)} | - |")
    for item in report["unavailable_predictions"]:
        lines.append(f"| {cell(item['name'])} | - | {cell(item['variant'])} | {cell(item['reason'])} | - |")
    cost = report["gemini_cost"]
    lines += ["", f"Recorded new Gemini cost: ${cost['new_cost_usd']:.6f}; "
              f"unknown-cost tracks: {cost['unknown_cost_tracks']}. {cost['scope']}", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v6-dir")
    parser.add_argument("--marks")
    parser.add_argument("--library-export", type=Path, help="Score the current web library's exported experiment JSON")
    parser.add_argument("--v5-dir")
    parser.add_argument("--report", help="Markdown report path")
    parser.add_argument("--variant", action="append", default=[], metavar="NAME=DIR",
                        help="Additional switch-ablation plan directory to score")
    args = parser.parse_args()
    if not args.report:
        version = "v7-library" if args.library_export else "v6"
        args.report = str(Path(__file__).resolve().parent.parent / "docs" /
                          f"SCORE-{version}-{datetime.date.today().isoformat()}.md")
    if args.library_export:
        if args.v6_dir or args.marks or args.v5_dir or args.variant:
            parser.error("--library-export cannot be combined with legacy plan/mark options")
        from experiments import score_tracks
        with args.library_export.open(encoding="utf-8") as source:
            exported = json.load(source)
        if not isinstance(exported, dict) or not isinstance(exported.get("tracks"), list):
            parser.error("library export must contain a tracks list")
        report = score_tracks(exported["tracks"])
        markdown = library_report_markdown(report)
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(markdown, encoding="utf-8")
        print(markdown, end="")
        return
    if not args.v6_dir or not args.marks:
        parser.error("provide --library-export or both --v6-dir and --marks")
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
