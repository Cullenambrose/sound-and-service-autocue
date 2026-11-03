#!/usr/bin/env python3
"""Generate original synthetic percussion and capture read-only Serato Cue Loop tag snapshots."""
import argparse
import array
import json
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))
import serato_writer
from scripts.apply_single_track_qa import Refused, check_serato_closed

NAME = "AutoCue Synthetic Cue Loop QA.mp3"
MANIFEST = "fixture.json"
LABELS = ("baseline", "plain-cue", "cue-loop-32", "cue-loop-16", "cue-loop-slot-b")


def create(directory):
    check_serato_closed()
    encoder = shutil.which("ffmpeg")
    if not encoder:
        raise Refused("ffmpeg is required to generate the synthetic MP3.")
    directory.mkdir(parents=True, exist_ok=True)
    target, manifest = directory / NAME, directory / MANIFEST
    if target.exists() or manifest.exists() or target.is_symlink() or manifest.is_symlink():
        raise Refused("Fixture already exists; choose a fresh directory. Nothing was overwritten.")
    rate = 16000
    samples = array.array("h")
    for index in range(40 * rate):
        beat_time = (index / rate) % 0.5
        value = 14000 * math.sin(2 * math.pi * 100 * beat_time) * math.exp(-beat_time * 45)
        samples.append(round(value))
    if sys.byteorder != "little":
        samples.byteswap()
    with tempfile.TemporaryDirectory(prefix="cue-loop-", dir=directory) as work:
        wav, mp3 = Path(work) / "clicks.wav", Path(work) / "clicks.mp3"
        with wave.open(str(wav), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(samples.tobytes())
        result = subprocess.run([encoder, "-v", "error", "-i", str(wav), "-codec:a", "libmp3lame",
                                 "-b:a", "128k", "-metadata", "title=AutoCue Synthetic Cue Loop QA",
                                 "-metadata", "artist=AutoCue Test Fixture", str(mp3)],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise Refused("Synthetic MP3 encoding failed: " + result.stderr.strip())
        with target.open("xb") as destination, mp3.open("rb") as source:
            shutil.copyfileobj(source, destination)
    record = {"schema_version": 1, "file": NAME, "bpm": 120, "duration_s": 40,
              "origin": "original algorithmically generated percussion; not library music",
              "audio_sha256": serato_writer.mp3_audio_sha256(target)}
    with manifest.open("x", encoding="utf-8") as destination:
        json.dump(record, destination, indent=2)
        destination.write("\n")
    return target


def snapshot(directory, label):
    """Only snapshot this generator's fixture with matching immutable audio identity."""
    import base64
    from mutagen.id3 import ID3

    check_serato_closed()
    if label not in LABELS:
        raise Refused("Unknown fixture snapshot label.")
    manifest_path, track = directory / MANIFEST, directory / NAME
    if manifest_path.is_symlink() or track.is_symlink():
        raise Refused("Fixture symlinks are not supported.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("file") != NAME or manifest.get("schema_version") != 1:
        raise Refused("Not a recognized synthetic fixture manifest.")
    original_sha = serato_writer.file_sha256(track)
    audio_sha = serato_writer.mp3_audio_sha256(track)
    if audio_sha != manifest.get("audio_sha256"):
        raise Refused("Audio does not match the generated fixture; refusing to capture another track.")
    frames = [{"description": frame.desc, "mime": frame.mime,
               "data_base64": base64.b64encode(frame.data).decode("ascii")}
              for frame in ID3(str(track)).getall("GEOB")]
    if serato_writer.file_sha256(track) != original_sha:
        raise Refused("Fixture changed during capture; snapshot was not saved.")
    output = directory / f"{label}.json"
    with output.open("x", encoding="utf-8") as destination:
        json.dump({"schema_version": 1, "label": label, "file": NAME,
                   "file_sha256": original_sha, "audio_sha256": audio_sha, "geob_frames": frames},
                  destination, indent=2)
        destination.write("\n")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "snapshot"):
        sub = commands.add_parser(command)
        sub.add_argument("--directory", type=Path, required=True)
        if command == "snapshot":
            sub.add_argument("--label", choices=LABELS, required=True)
    args = parser.parse_args(argv)
    directory = args.directory.expanduser()
    try:
        result = create(directory) if args.command == "create" else snapshot(directory, args.label)
    except (Refused, OSError, ValueError) as error:
        parser.exit(2, f"Fixture operation refused: {error}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
