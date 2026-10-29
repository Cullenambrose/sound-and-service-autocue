"""Read the beatgrid Serato stored in an MP3's ID3 tags and reconcile it with the detected bar grid.

A track analyzed in Serato may carry a "Serato BeatGrid" GEOB frame. Prefer a supported imported grid,
report disagreement without changing its phase, and never assume the DJ manually verified it.
"""
import logging
import math
import struct
from pathlib import Path
from statistics import median

from timing import grid_origin

logger = logging.getLogger(__name__)

SERATO_TEMPO_TOLERANCE = 0.015  # relative tempo disagreement allowed between Serato and the detected grid
MULTI_MARKER_TOLERANCE = 0.005  # relative tempo spread allowed across a multi-marker Serato grid
DIAGNOSTICS_VERSION = 1
ALIGNMENT_TOLERANCE_BEATS = 0.25
MIN_REGION_DOWNBEATS = 4


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


def diagnose(grid, downbeats, duration_s, markers=None):
    """Compare raw model observations to the chosen constant 4/4 ruler without altering it.

    Wrapped residuals measure local phase; accumulated nearest integer bar intervals expose drift
    through wraparound. Model observations are evidence, not proof of correct meter or phrasing.
    """
    origin = grid_origin(grid)
    if type(duration_s) not in (int, float) or not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("Grid diagnostics require finite positive duration.")
    bar_s = float(grid["bar_s"])
    beat_s = bar_s / 4
    limit = beat_s * ALIGNMENT_TOLERANCE_BEATS
    issues = []
    result = {"version": DIAGNOSTICS_VERSION, "status": "unverified",
              "scope": "constant 4/4 ruler versus model downbeats; not musical accuracy certification",
              "tolerance_beats": ALIGNMENT_TOLERANCE_BEATS, "tolerance_s": round(limit, 6),
              "grid_source": grid.get("source"), "regions": [], "issues": issues}

    def issue(code, message):
        issues.append({"code": code, "message": message})

    def exceeds(value):
        return value > limit + 1e-9

    if markers:
        marker_origin, marker_bar_s, reason = grid_from_markers(markers)
        if marker_origin is None:
            issue("unsupported_serato_grid", reason + "; constant-ruler phrasing is unverified.")
        elif len(markers) > 1:
            cumulative_beats, errors = 0, []
            for index, marker in enumerate(markers):
                if index:
                    cumulative_beats += markers[index - 1]["beats_to_next"]
                errors.append(marker["pos"] - (marker_origin + cumulative_beats * marker_bar_s / 4))
            tail_s = max(0, duration_s - markers[-1]["pos"])
            tail_error = errors[-1] + tail_s * (1 - marker_bar_s * markers[-1]["bpm"] / 240)
            maximum = max(abs(value) for value in errors + [tail_error])
            result["serato_marker_max_deviation_s"] = round(maximum, 6)
            if exceeds(maximum):
                issue("serato_marker_drift", "Flattening the imported markers exceeds a quarter beat over this track.")

    observations = list(downbeats) if downbeats is not None else []
    valid = [float(value) for value in observations
             if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= duration_s]
    invalid_count = len(observations) - len(valid)
    duplicate_count = len(valid) - len(set(valid))
    result.update(observed_downbeats=len(valid), ignored_downbeats=invalid_count + duplicate_count)
    if invalid_count or duplicate_count or any(right <= left for left, right in zip(valid, valid[1:])):
        issue("invalid_observations", "Invalid, duplicate or unordered downbeats were excluded or reordered; review detection.")
    times = sorted(set(valid))
    if len(times) < MIN_REGION_DOWNBEATS:
        issue("insufficient_downbeats", "Too few model downbeats to evaluate alignment.")
        if any(item["code"] not in ("insufficient_downbeats", "invalid_observations") for item in issues):
            result["status"] = "review_required"
        return result
    # Inspect raw intervals rather than fitted intervals, which can hide double-time.
    intervals = [right - left for left, right in zip(times, times[1:])]
    observed_bar_s = median(intervals)
    ratio = bar_s / observed_bar_s
    result.update(observed_bar_s=round(observed_bar_s, 6), bar_duration_ratio=round(ratio, 6))
    if abs(ratio / 2 - 1) <= SERATO_TEMPO_TOLERANCE:
        issue("possible_half_time_grid", "Chosen bars are about twice the observed duration; possible half-time BPM.")
    elif abs(ratio / 0.5 - 1) <= SERATO_TEMPO_TOLERANCE:
        issue("possible_double_time_grid", "Chosen bars are about half the observed duration; possible double-time BPM.")
    elif abs(ratio - 1) > SERATO_TEMPO_TOLERANCE:
        issue("tempo_disagreement", "Chosen ruler and median detected downbeat interval disagree by more than 1.5%.")

    residuals = [((time - origin + bar_s / 2) % bar_s) - bar_s / 2 for time in times]
    absolute = sorted(abs(value) for value in residuals)
    result["p95_phase_error_s"] = round(absolute[math.ceil(0.95 * len(absolute)) - 1], 6)
    result["aligned_fraction"] = round(sum(not exceeds(value) for value in absolute) / len(times), 4)
    for index, name in enumerate(("beginning", "middle", "end")):
        selected = [(time, residual) for time, residual in zip(times, residuals)
                    if index * duration_s / 3 <= time
                    and (time < (index + 1) * duration_s / 3 or index == 2)]
        region = {"name": name, "count": len(selected), "status": "unverified"}
        if len(selected) >= MIN_REGION_DOWNBEATS:
            phase = median(residual for _, residual in selected)
            p95 = sorted(abs(residual) for _, residual in selected)[math.ceil(0.95 * len(selected)) - 1]
            region.update(median_phase_s=round(phase, 6), p95_phase_error_s=round(p95, 6),
                          status="review_required" if exceeds(p95) else "aligned")
        else:
            issue("incomplete_coverage", f"Too few downbeats in the {name} third; whole-track fit is unverified.")
        result["regions"].append(region)
    if exceeds(absolute[math.ceil(0.95 * len(absolute)) - 1]):
        issue("phase_disagreement", "Detected downbeats depart from the chosen bar phase by more than a quarter beat.")

    # Only interpret accumulated drift when downbeat tempo is close to this ruler.
    if abs(ratio - 1) <= SERATO_TEMPO_TOLERANCE:
        accumulated, bar_index, reliable = [times[0] - origin - round((times[0] - origin) / bar_s) * bar_s], 0, True
        for left, right in zip(times, times[1:]):
            step = round((right - left) / bar_s)
            if step < 1 or abs((right - left) / bar_s - step) > 0.2:
                reliable = False
                break
            bar_index += step
            accumulated.append(accumulated[0] + right - times[0] - bar_index * bar_s)
        if reliable:
            count = min(max(MIN_REGION_DOWNBEATS, len(times) // 10), len(times) // 2)
            drift = median(accumulated[-count:]) - median(accumulated[:count])
            result["accumulated_drift_s"] = round(drift, 6)
            result["accumulated_drift_beats"] = round(drift / beat_s, 4)
            if exceeds(abs(drift)):
                issue("accumulated_drift", "Detected bar timing drifts more than a quarter beat from beginning to end.")
        else:
            issue("irregular_intervals", "Irregular detected intervals prevent a trustworthy accumulated-drift estimate.")
    result["status"] = ("review_required" if issues else "aligned")
    if issues and all(item["code"] == "incomplete_coverage" for item in issues):
        result["status"] = "unverified"
    return result
