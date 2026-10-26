"""Read the beatgrid Serato stored in an MP3's ID3 tags and reconcile it with the detected bar grid.

A track analyzed in Serato may carry a "Serato BeatGrid" GEOB frame. Prefer that imported grid when
its tempo agrees with detection, without assuming the DJ manually verified it.
"""
import logging
import math
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

SERATO_TEMPO_TOLERANCE = 0.015  # relative tempo disagreement allowed between Serato and the detected grid
MULTI_MARKER_TOLERANCE = 0.005  # relative tempo spread allowed across a multi-marker Serato grid


def parse_beatgrid(data):
    """Decode a Serato BeatGrid frame into [{pos, beats_to_next}, ..., {pos, bpm}]; None if malformed."""
    if not isinstance(data, bytes) or len(data) < 7 or data[:2] != b"\x01\x00":
        return None
    count = struct.unpack(">I", data[2:6])[0]
    if count < 1 or len(data) < 6 + count * 8:
        return None
    markers, offset = [], 6
    for index in range(count):
        position = struct.unpack(">f", data[offset:offset + 4])[0]
        if index == count - 1:
            markers.append({"pos": position, "bpm": struct.unpack(">f", data[offset + 4:offset + 8])[0]})
        else:
            markers.append({"pos": position, "beats_to_next": struct.unpack(">I", data[offset + 4:offset + 8])[0]})
        offset += 8
    # Variable-tempo grids are valid input, even though the current planner cannot adopt them.
    if not all(math.isfinite(marker["pos"]) and marker["pos"] >= 0 for marker in markers):
        return None
    if not math.isfinite(markers[-1]["bpm"]) or markers[-1]["bpm"] <= 0:
        return None
    if any(marker.get("beats_to_next", 1) <= 0 for marker in markers):
        return None
    if any(right["pos"] <= left["pos"] for left, right in zip(markers, markers[1:])):
        return None
    return markers


def read_beatgrid(path, on_diagnostic=None):
    """Beatgrid markers from an MP3/M4A's Serato tag, or None when the file has none."""
    def report(message):
        if on_diagnostic:
            on_diagnostic(message)
        else:
            logger.warning(message)
    suffix = Path(path).suffix.lower()
    if suffix not in (".mp3", ".m4a"):
        report("Serato beatgrid import currently supports MP3 and M4A tags only; using detected grid.")
        return None
    try:
        from mutagen import MutagenError
        from mutagen.id3 import ID3, ID3NoHeaderError
        from mutagen.mp4 import MP4
    except ImportError:
        report("Serato beatgrid import requires mutagen; using detected grid.")
        return None
    if suffix == ".m4a":
        from serato_tags import MP4_BEATGRID_KEY, vorbis_decode
        try:
            values = (MP4(str(path)).tags or {}).get(MP4_BEATGRID_KEY, [])
        except (MutagenError, OSError):
            report("Could not read Serato beatgrid tag; using detected grid.")
            return None
        if not values:
            return None
        try:
            markers = parse_beatgrid(vorbis_decode(bytes(values[0]).decode("ascii"), "Serato BeatGrid"))
        except (ValueError, UnicodeError):
            markers = None
        if markers is None:
            report("Malformed or unsupported Serato beatgrid tag; using detected grid.")
        return markers
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        return None
    except (MutagenError, OSError):
        report("Could not read Serato beatgrid tag; using detected grid.")
        return None
    for frame in tags.getall("GEOB"):
        if frame.desc == "Serato BeatGrid":
            markers = parse_beatgrid(frame.data)
            if markers is None:
                report("Malformed or unsupported Serato beatgrid tag; using detected grid.")
            return markers
    return None


def grid_from_markers(markers):
    """(t1, bar_s, note) for a constant-tempo Serato grid, or (None, None, reason). Bar = 4 beats from marker 1."""
    if not markers:
        return None, None, "no beatgrid markers"
    try:
        if any(type(marker["pos"]) not in (int, float) for marker in markers):
            return None, None, "Serato beatgrid positions must be numeric"
        positions = [float(marker["pos"]) for marker in markers]
        if any(not math.isfinite(position) or position < 0 for position in positions):
            return None, None, "Serato beatgrid positions must be finite and nonnegative"
        if any(right <= left for left, right in zip(positions, positions[1:])):
            return None, None, "Serato beatgrid markers are out of order"
        final_bpm = float(markers[-1]["bpm"])
        if not math.isfinite(final_bpm) or final_bpm <= 0:
            return None, None, "Serato beatgrid tempo is invalid"
        if any(type(marker.get("beats_to_next")) is not int or not 0 < marker["beats_to_next"] <= 0xFFFFFFFF
               for marker in markers[:-1]):
            return None, None, "Serato beatgrid beat counts are invalid"
    except (ValueError, TypeError, KeyError):
        return None, None, "Serato beatgrid markers are malformed"
    first = markers[0]
    if len(markers) == 1:
        bpm = final_bpm
    else:
        tempos = []
        for current, following in zip(markers, markers[1:]):
            beats = current.get("beats_to_next")
            if not beats or following["pos"] <= current["pos"]:
                return None, None, "Serato beatgrid markers are out of order"
            tempos.append(60.0 * beats / (following["pos"] - current["pos"]))
        tempos.append(final_bpm)
        if max(tempos) - min(tempos) > MULTI_MARKER_TOLERANCE * min(tempos):
            return None, None, "Serato beatgrid changes tempo; keeping the detected grid"
        bpm = tempos[0]
    if not 60.0 <= bpm <= 220.0:
        return None, None, f"Serato beatgrid tempo {bpm:.1f} BPM is implausible"
    return float(first["pos"]), 240.0 / float(bpm), ""


def reconcile(detected, markers):
    """Return (grid, note). A valid constant-tempo Serato BeatGrid is always the canonical bar ruler.

    Bar k starts at pos0 + k * 240 / bpm. The grid phase is never shifted toward detected downbeats or legacy
    (e.g. Mixed In Key) cue points; detected-tempo disagreement is only reported.
    """
    t1, bar_s, reason = grid_from_markers(markers)
    if t1 is None:
        return detected, reason
    detected_bar = float(detected["bar_s"])
    detected_start = float(detected["t1"])
    if not math.isfinite(detected_bar) or detected_bar <= 0 or not math.isfinite(detected_start):
        raise ValueError("Detected grid must have finite timing and a positive bar duration.")
    ratio = bar_s / detected_bar
    agrees = abs(ratio - 1.0) <= SERATO_TEMPO_TOLERANCE
    grid = dict(detected)
    grid.update({"source": "serato_beatgrid", "t1": round(t1, 6), "bar_s": round(bar_s, 8),
                 "bpm": round(240.0 / bar_s, 4), "offset_ms": 0.0,
                 "provenance": {"format": "Serato BeatGrid", "marker_count": len(markers),
                                "user_verified": False, "detected_tempo_agrees": agrees,
                                "detected_grid": dict(detected)}})
    if not agrees:
        return grid, (f"used the Serato beatgrid as the bar ruler ({240.0 / bar_s:.2f} BPM); the detected tempo "
                      f"({240.0 / detected_bar:.1f} BPM) disagrees, so review the grid in Serato")
    shift = ((t1 - float(detected["t1"]) + bar_s / 2) % bar_s) - bar_s / 2
    return grid, f"used the Serato beatgrid (first beat {shift:+.2f}s from the detected downbeat)"
