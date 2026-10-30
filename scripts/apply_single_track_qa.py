#!/usr/bin/env python3
"""Analyze ONE MP3 and write the v6.1 Pop/Vocal 8-pad cues to its Serato tags, with backups and verification.

Run on the DJ MacBook Pro from the repo root, with Serato DJ Pro closed:

    .venv/bin/python scripts/apply_single_track_qa.py \
        --track "/Users/cullenambrose/Music/Master Music/Van Morrison - Into The Mystic (Re-Drum) - 5B - 84.mp3"

Use --dry-run first to see the planned pads without touching the file.
"""
import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import serato_tags  # noqa: E402
import serato_writer  # noqa: E402

PROTECTED_NAMES = ("Happy (N And D Chorus Intro)",)
SERATO_PROCESSES = ("Serato", "Serato DJ Pro", "Serato DJ Lite")
DEFAULT_BACKUP_DIR = Path.home() / "Music" / "AutoCue Backups"


class Refused(Exception):
    pass


def check_track(path):
    if any(name.casefold() in path.name.casefold() for name in PROTECTED_NAMES):
        raise Refused(f"{path.name} is the read-only Track #1 reference fixture; refusing to touch it.")
    if path.suffix.lower() != ".mp3":
        raise Refused(f"{path.name}: only MP3 files are supported by this script.")
    if not path.is_file():
        raise Refused(f"Track not found: {path}")


def serato_running():
    if sys.platform != "darwin":
        return []
    running = []
    for name in SERATO_PROCESSES:
        try:
            result = subprocess.run(["pgrep", "-x", name], capture_output=True, check=False)
        except OSError as error:
            raise Refused(f"Could not check whether Serato is open ({error}); refusing to write.") from error
        if result.returncode == 0:
            running.append(name)
    return running


def check_serato_closed():
    running = serato_running()
    if running:
        raise Refused(f"{', '.join(running)} is open. Quit Serato completely, then run this again.")


def id3_text(path, frame):
    try:
        from mutagen.id3 import ID3
        values = ID3(str(path)).getall(frame)
    except Exception:
        return ""
    return str(values[0].text[0]).strip() if values and values[0].text else ""


def artist_title(path):
    artist, title = id3_text(path, "TPE1"), id3_text(path, "TIT2")
    if not (artist and title) and " - " in path.stem:
        guessed_artist, guessed_title = path.stem.split(" - ", 1)
        artist, title = artist or guessed_artist.strip(), title or guessed_title.split(" - ")[0].strip()
    return artist, title


def backup(path, backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{stamp} {path.name}"
    if target.exists():
        raise Refused(f"Backup already exists: {target}")
    original_sha = serato_writer.file_sha256(path)
    shutil.copy2(path, target)
    backup_sha = serato_writer.file_sha256(target)
    if backup_sha != original_sha:
        target.unlink(missing_ok=True)
        raise Refused("Backup SHA-256 does not match the original; nothing was written.")
    target.with_name(target.name + ".json").write_text(json.dumps({
        "original_path": str(path), "backup_path": str(target), "sha256": original_sha,
        "created": stamp}, indent=2) + "\n")
    return target, original_sha


def fmt_time(ms):
    minutes, rest = divmod(ms, 60000)
    return f"{minutes}:{rest / 1000:06.3f}"


def print_table(title, pads, bars):
    by_index = {pad["index"]: pad for pad in pads}
    name_width = max(18, max((len(pad["name"]) for pad in pads), default=0) + 2)
    print(f"\n{title}")
    print(f"{'Pad #':<6}{'Name':<{name_width}}{'Timestamp':>11}{'ms':>10}{'Bar':>6}  {'Hex Color':<9}")
    for index in range(8):
        pad = by_index.get(index)
        if pad is None:
            print(f"{index + 1:<6}{'None':<{name_width}}{'-':>11}{'-':>10}{'-':>6}  {'-':<9}")
            continue
        bar = bars.get(index)
        print(f"{index + 1:<6}{pad['name']:<{name_width}}{fmt_time(pad['position_ms']):>11}{pad['position_ms']:>10,}"
              f"{'-' if bar is None else f'{bar:g}':>6}  {pad['color']:<9}")


def analyze_track(path, genre):
    import analyze
    artist, title = artist_title(path)
    genre = genre or id3_text(path, "TCON") or "pop"

    def progress(step, state, detail):
        if state in ("running", "failed", "skipped"):
            print(f"  [{state}] {step} {detail}".rstrip(), flush=True)

    print(f"Analyzing {path.name} (artist={artist!r}, title={title!r}, genre={genre!r}) ...", flush=True)
    return analyze.analyze(path, artist, title, genre, progress=progress, gemini=False,
                           cue_maps={"version": 2, "mode": "VOCAL"})


def run(args):
    path = Path(args.track).expanduser()
    check_track(path)
    check_serato_closed()
    sha_before_analysis = serato_writer.file_sha256(path)
    plan = analyze_track(path, args.genre)
    if plan.get("cue_spec") != "v6.1-pop-vocal":
        raise Refused(f"Analysis did not produce the v6.1 Pop/Vocal layout (cue_spec={plan.get('cue_spec')!r}).")
    pads = serato_writer.plan_pads(plan)
    if not pads:
        raise Refused("Analysis produced no pads; nothing to write.")
    bars = {pad["index"]: pad["bar"] for pad in pads}
    grid = plan.get("grid", {})
    print(f"\nBeatGrid ruler: source={grid.get('source')} t1={grid.get('t1')} bpm={grid.get('bpm')}")
    diagnostics = plan.get("grid_diagnostics")
    print(f"Grid check: {diagnostics['status'] if diagnostics else 'unavailable; reanalyze for drift diagnostics'}")
    if grid.get("source") != "serato_beatgrid":
        print("  WARNING: no usable Serato BeatGrid tag; bars use the detected grid instead.")
    onset = plan.get("start_onset") or {}
    if onset:
        print(f"START onset: {onset.get('time_s')}s ({onset.get('source')}); anchor bar {onset.get('anchor_bar')}")
    print(f"EXIT evidence: {(plan.get('exit_evidence') or {}).get('reason')}")
    out = plan.get("out_detection") or {}
    print(f"OUT: {out.get('kind')} - {out.get('reason')}")
    for flag in plan.get("flags", []):
        print(f"  flag: {flag}")
    print_table("Planned pads", pads, bars)
    if args.dry_run:
        print("\nDry run: the file was not changed.")
        return 0

    check_serato_closed()
    if serato_writer.file_sha256(path) != sha_before_analysis:
        raise Refused(f"{path.name} changed during analysis; nothing was written.")
    backup_path, original_sha = backup(path, Path(args.backup_dir).expanduser())
    print(f"\nBackup: {backup_path} (SHA-256 verified {original_sha[:12]}...)")
    result = serato_writer.write_mp3_pads(path, pads)
    audio_after = serato_writer.mp3_audio_sha256(path)
    if audio_after != result["audio_sha256_before"]:
        raise RuntimeError(f"Audio stream hash changed after writing! Restore from {backup_path}")
    print(f"Audio stream SHA-256 unchanged: {audio_after[:12]}...")
    frames = serato_writer.read_serato_frames(path)
    decoded = serato_tags.decode_markers2(frames["Serato Markers2"])
    if decoded["inner"][:2] != b"\x01\x01":
        raise RuntimeError(f"Read-back Markers2 inner header is wrong. Restore from {backup_path}")
    read_back = [cue for cue in decoded["cues"] if cue["index"] < 8]
    expected = [{key: pad[key] for key in ("index", "position_ms", "color", "name")} for pad in pads]
    if read_back != expected:
        raise RuntimeError(f"Read-back Markers2 cues differ from the plan. Restore from {backup_path}")
    v1 = serato_tags.decode_markers_v1(frames["Serato Markers_"])
    v1_cues = [(entry["slot"], entry["position_ms"], entry["color"]) for entry in v1["entries"][:5]
               if entry["position_ms"] is not None]
    if v1_cues != [(pad["index"], pad["position_ms"], pad["color"]) for pad in pads if pad["index"] < 5]:
        raise RuntimeError(f"Read-back Markers_ cues disagree with Markers2. Restore from {backup_path}")
    print_table("Written pads (read back from GEOB:Serato Markers2)", read_back, bars)
    print(f"\nDone. To undo: cp \"{backup_path}\" \"{path}\"")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--track", required=True, help="Full path to the single MP3 to update")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and print the pads without writing")
    parser.add_argument("--genre", default="", help="Override the genre tag (default: ID3 genre, else pop)")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="Where timestamped backups go")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Refused as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
