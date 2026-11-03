#!/usr/bin/env python3
"""Read-only four-track regression check. Analysis runs on copies outside Music; no tag writer is used."""
import argparse
import fnmatch
import json
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import analyze
import serato_grid
from timing import bar_to_time, grid_origin

TRACKS = (
    ("*Into The Mystic*Re-Drum*.mp3", {"start_ms": 23221, "exit_bar": 40, "out_bar": 81}),
    ("*You Make My Dreams*.m4a", {"serato_bpm": 84}),
    ("*Mambo*5*MMP Intro*.mp3", {"start_ms": 22743, "exit_bar": 30, "out_bar": 80}),
    ("*Le Freak*Clean*.mp3", {}),
)


def snapshot(paths):
    return {str(path): analyze.file_hash(path) for path in sorted(paths) if path.is_file()}


def report_plan(plan, expected):
    grid, cues = plan["grid"], plan["cues"]
    print(f"Ruler: source={grid.get('source')} pos0={grid_origin(grid):.6f}s BPM={grid['bpm']:.4f}")
    start = cues["B"]
    onset = plan.get("start_onset")
    print("Vocal onset evidence:", json.dumps(onset))
    if start.get("bar") is not None and start.get("time_s") is not None:
        downbeat = bar_to_time(start["bar"], grid)
        print(f"START: bar={start['bar']} downbeat={downbeat:.6f}s "
              f"delta={start['time_s'] - downbeat:+.6f}s unsnapped={start.get('unsnapped', False)}")
    print("Grid diagnostics:", json.dumps(plan.get("grid_diagnostics"), sort_keys=True))
    for note in (plan.get("pipeline") or {}).get("notes", []):
        print("Pipeline:", note)
    print(f"{'Pad':<5} {'Time (s)':>12} {'ms':>9} {'Bar':>8}")
    for index, slot in enumerate("ABCDEFGH", 1):
        cue = cues.get(slot) or {}
        bar, time = cue.get("bar"), cue.get("time_s")
        if bar is None or time is None:
            print(f"{index} {slot:<3} {'empty':>12} {'-':>9} {'-':>8}")
        else:
            print(f"{index} {slot:<3} {time:>12.3f} {round(time * 1000):>9} {bar:>8g}")
    actual = {"start_ms": None if start.get("time_s") is None else round(start["time_s"] * 1000),
              "exit_bar": (cues.get("F") or {}).get("bar"), "out_bar": (cues.get("H") or {}).get("bar")}
    for key, value in expected.items():
        if key == "serato_bpm":
            if grid.get("source") != "serato_beatgrid" or abs(grid["bpm"] - value) > 0.01:
                raise ValueError(f"Expected Serato {value} BPM; got {grid.get('source')} {grid['bpm']}")
        elif actual[key] != value:
            raise ValueError(f"Regression: expected {key}={value}, got {actual[key]}")
    if any(cues.get(slot, {}).get("bar") is None for slot in ("B", "F", "H")):
        raise ValueError("Incomplete plan: START, EXIT or OUT is missing.")


def run(args):
    music = Path.home().joinpath("Music").resolve()
    library = args.library.expanduser().resolve()
    cache = (args.cache_dir or analyze.cache_root()).expanduser().resolve()
    if cache == music or music in cache.parents:
        raise ValueError("Analysis cache must be outside ~/Music.")
    if args.verify_music_tree and music not in library.parents and library != music:
        raise ValueError("--verify-music-tree requires the source library to be under ~/Music.")
    tracks = []
    for pattern, expected in TRACKS:
        matches = sorted(path for path in library.iterdir() if fnmatch.fnmatch(path.name, pattern) and path.is_file())
        if len(matches) != 1:
            raise ValueError(f"Expected one {pattern!r} in {library}; found {len(matches)}.")
        tracks.append((matches[0], expected))
    paths = lambda: music.rglob("*") if args.verify_music_tree else (path for path, _ in tracks)
    print("Hashing all files under ~/Music." if args.verify_music_tree else "Hashing the four source files.")
    before = snapshot(paths())
    failed = False
    try:
        with tempfile.TemporaryDirectory(prefix="autocue-readonly-") as directory:
            work = Path(directory).resolve()
            if work == music or music in work.parents:
                raise ValueError("Temporary copies must be outside ~/Music; set TMPDIR accordingly.")
            for source, expected in tracks:
                print(f"\n=== {source.name} ===", flush=True)
                copy = work / source.name
                shutil.copyfile(source, copy)
                if analyze.file_hash(copy) != before[str(source)]:
                    raise ValueError(f"Source changed while copying: {source}")
                if source.suffix.lower() == ".m4a":
                    from mutagen.mp4 import MP4
                    audio = MP4(copy)
                    print("MP4 tag keys:", list((audio.tags or {}).keys()))
                markers = serato_grid.read_beatgrid(copy, lambda message: print("BeatGrid:", message))
                print("Imported BeatGrid markers:", json.dumps(markers))
                try:
                    plan = analyze.analyze(copy, cache_dir=cache, gemini=False)
                    words = analyze.Paths(cache / analyze.file_hash(copy)).words
                    if words.exists():
                        record = json.loads(words.read_text())
                        first = next(iter(record.get("words") or []), None)
                        print("First WhisperX aligned word:", json.dumps(first))
                    else:
                        print("WhisperX word cache unavailable.")
                    report_plan(plan, expected)
                except (RuntimeError, ValueError) as error:
                    print(f"FAILED: {error}", file=sys.stderr)
                    failed = True
    finally:
        after = snapshot(paths())
        changes = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        if changes:
            raise RuntimeError("Music SHA-256 verification FAILED: " + ", ".join(changes))
        print(f"\nSHA-256 unchanged: {len(before)} files verified.")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path.home() / "Music" / "Master Music")
    parser.add_argument("--cache-dir", type=Path, help="Reuse a cache outside Music; defaults to the normal analysis cache")
    parser.add_argument("--verify-music-tree", action="store_true", help="Hash ALL ~/Music files before/after (can be slow)")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(2, f"Read-only verification failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
