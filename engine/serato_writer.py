"""Write v6.1 cue pads to MP3 Serato tags (Markers2 + Markers_ in lockstep) with audio-integrity checks."""
import hashlib
import math
import os
import shutil
import uuid
from pathlib import Path

import serato_tags
from timing import bar_to_time

SLOTS = "ABCDEFGH"


def mp3_audio_range(path):
    """(start, end) byte range of the MP3 audio stream: after the ID3v2 tag, before any ID3v1 trailer."""
    size = Path(path).stat().st_size
    with open(path, "rb") as source:
        header = source.read(10)
        start = 0
        if len(header) == 10 and header[:3] == b"ID3":
            tag_size = sum((header[index] & 0x7F) << shift for index, shift in zip(range(6, 10), (21, 14, 7, 0)))
            start = 10 + tag_size + (10 if header[5] & 0x10 else 0)
        end = size
        if size >= 128:
            source.seek(-128, os.SEEK_END)
            if source.read(3) == b"TAG":
                end -= 128
    return start, end


def mp3_audio_sha256(path):
    start, end = mp3_audio_range(path)
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        source.seek(start)
        remaining = max(0, end - start)
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cue_time_s(cue, grid):
    """Pad time: the stored un-snapped START time when flagged, otherwise the BeatGrid bar time."""
    time_s = cue.get("time_s")
    if cue.get("unsnapped") and isinstance(time_s, (int, float)) and math.isfinite(time_s):
        return float(time_s)
    return bar_to_time(float(cue["bar"]), grid)


def plan_pads(plan, default_colors=None):
    """Active pads from a plan as [{index, slot, name, position_ms, color, bar}] in slot order."""
    grid = plan.get("grid", {})
    pads = []
    for index, slot in enumerate(SLOTS):
        cue = (plan.get("cues") or {}).get(slot) or {}
        if cue.get("bar") is None:
            continue
        if not math.isfinite(float(cue["bar"])):
            raise ValueError(f"Cue {slot} has a non-finite bar position.")
        position = max(0, int(round(cue_time_s(cue, grid) * 1000)))
        color = cue.get("color") or (default_colors or {}).get(slot)
        pads.append({"index": index, "slot": slot, "name": str(cue.get("name") or slot)[:120],
                     "position_ms": position, "color": color, "bar": cue["bar"]})
    return pads


def _id3(path):
    from mutagen.id3 import ID3, ID3NoHeaderError
    try:
        return ID3(str(path))
    except ID3NoHeaderError:
        return ID3()


def read_serato_frames(path):
    """{"Serato Markers2": bytes|None, "Serato Markers_": bytes|None}; refuses duplicate frames."""
    tags = _id3(path)
    found = {}
    for desc in ("Serato Markers2", "Serato Markers_"):
        frames = tags.getall("GEOB:" + desc)
        if len(frames) > 1:
            raise RuntimeError(f"{Path(path).name} has multiple {desc} frames; writing is blocked.")
        found[desc] = frames[0].data if frames else None
    return found


def store_serato_frames(path, markers2, markers_v1):
    from mutagen.id3 import GEOB
    tags = _id3(path)
    version = 4 if getattr(tags, "version", (2, 3, 0))[1] == 4 else 3
    for desc, data in (("Serato Markers2", markers2), ("Serato Markers_", markers_v1)):
        tags.delall("GEOB:" + desc)
        tags.add(GEOB(encoding=0, mime="application/octet-stream", desc=desc, data=data))
    tags.save(str(path), v2_version=version)


def encode_for_file(pads, existing):
    markers2 = serato_tags.encode_markers2(pads, existing.get("Serato Markers2"))
    color = serato_tags.track_color_from_markers2(existing.get("Serato Markers2"))
    return markers2, serato_tags.encode_markers_v1(pads, color)


def write_mp3_pads(path, pads):
    """Atomically write both Serato cue tags, verifying the tags read back and the audio bytes are unchanged."""
    path = Path(path)
    if path.suffix.lower() != ".mp3":
        raise ValueError(f"{path.name}: only MP3 files are supported by this writer.")
    original_sha = file_sha256(path)
    audio_before = mp3_audio_sha256(path)
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.autocue{path.suffix}")
    try:
        shutil.copy2(path, temporary)
        markers2, markers_v1 = encode_for_file(pads, read_serato_frames(temporary))
        store_serato_frames(temporary, markers2, markers_v1)
        written = read_serato_frames(temporary)
        if written["Serato Markers2"] != markers2 or written["Serato Markers_"] != markers_v1:
            raise RuntimeError(f"Could not verify the written Serato cue tags in {path.name}.")
        audio_after = mp3_audio_sha256(temporary)
        if audio_after != audio_before:
            raise RuntimeError(f"Audio data changed while writing Serato cues to {path.name}; nothing was replaced.")
        if file_sha256(path) != original_sha:
            raise RuntimeError(f"{path.name} changed during the write; nothing was replaced.")
        written_sha = file_sha256(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"original_sha256": original_sha, "written_sha256": written_sha,
            "audio_sha256_before": audio_before, "audio_sha256_after": audio_after,
            "markers2": markers2, "markers_v1": markers_v1}
