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
TEMPOMAP_RECORD_SIZE = 33  # bytes per Serato Tempo Map marker


def parse_tempomap(data):
    """Decode a Serato Tempo Map into [{pos, bpm}, ...]; None if malformed.
    
    Tempo Map format (Serato DJ Pro 5.0+):
    - Version: 4 bytes (e.g., 0x02000001)
    - Markers: 33-byte records
      - 8 bytes: position in seconds (double) - only for first marker
      - 4 bytes: beat count (int) - only for first marker  
      - 8 bytes: sample offset from first marker (long) - 0 for first, relative for others
      - 8 bytes: BPM at this marker (double)
      - 8 bytes: beat duration (double) - typically 0.25 (quarter note)
      - 1 byte: flags/unknown
    
    This represents variable-tempo tracks (e.g., live drums). Unlike BeatGrid, which assumes constant
    tempo, Tempo Map markers can have different BPMs.
    """
    if not isinstance(data, bytes) or len(data) < 37:  # version(4) + one marker(33)
        return None
    
    version = struct.unpack(">I", data[:4])[0]
    if version not in (0x02000001,):  # Only known version so far
        return None
    
    markers = []
    offset = 4
    first_marker_pos = None
    
    while offset + TEMPOMAP_RECORD_SIZE <= len(data):
        try:
            pos_field = struct.unpack(">d", data[offset:offset+8])[0]
            beat_count = struct.unpack(">I", data[offset+8:offset+12])[0]
            samples = struct.unpack(">Q", data[offset+12:offset+20])[0]
            bpm = struct.unpack(">d", data[offset+20:offset+28])[0]
            beat_dur = struct.unpack(">d", data[offset+28:offset+36])[0]
            # byte at offset+36 is flags, currently unused
            
            # First marker uses pos_field; subsequent use samples relative to first
            if not markers:
                first_marker_pos = pos_field
                actual_pos = pos_field
            else:
                actual_pos = first_marker_pos + (samples / 44100.0)  # Assuming 44.1kHz
            
            if not (math.isfinite(actual_pos) and actual_pos >= 0 and
                    math.isfinite(bpm) and bpm > 0 and
                    math.isfinite(beat_dur) and beat_dur > 0):
                return None
            
            markers.append({"pos": actual_pos, "bpm": bpm, "beat_count": beat_count if not markers else None})
            
        except (struct.error, ValueError):
            return None
        
        offset += TEMPOMAP_RECORD_SIZE
    
    if not markers:
        return None
    
    return markers


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
        from serato_tags import MP4_BEATGRID_KEY, MP4_TEMPOMAP_KEY, vorbis_decode
        try:
            tags = MP4(str(path)).tags
            if tags is None:
                report(f"M4A file has no tags; using detected grid.")
                return None
        except (MutagenError, OSError) as err:
            report(f"Could not read M4A tags ({type(err).__name__}); using detected grid.")
            return None
        
        # Try beatgrid first, then fall back to tempomap
        values = tags.get(MP4_BEATGRID_KEY, [])
        if values:
            try:
                # MP4FreeForm is a bytes subclass; ensure we have bytes for vorbis_decode
                raw_value = values[0]
                if hasattr(raw_value, '__bytes__'):
                    raw_value = bytes(raw_value)
                payload = vorbis_decode(raw_value, "Serato BeatGrid")
            except (ValueError, UnicodeError, TypeError) as err:
                report(f"Serato beatgrid decode failed ({MP4_BEATGRID_KEY}, {type(err).__name__}): "
                       f"invalid base64 text or envelope header; trying tempomap.")
                values = []  # Fall through to tempomap
            else:
                markers = parse_beatgrid(payload)
                if markers is None:
                    report(f"Serato beatgrid parse failed ({MP4_BEATGRID_KEY}): malformed binary; trying tempomap.")
                else:
                    return markers
        
        # Try tempomap if beatgrid wasn't found or failed
        values = tags.get(MP4_TEMPOMAP_KEY, [])
        if not values:
            report(f"Neither beatgrid ({MP4_BEATGRID_KEY}) nor tempomap ({MP4_TEMPOMAP_KEY}) found; "
                   "using detected grid.")
            return None
        try:
            raw_value = values[0]
            if hasattr(raw_value, '__bytes__'):
                raw_value = bytes(raw_value)
            payload = vorbis_decode(raw_value, "Serato Tempo Map")
        except (ValueError, UnicodeError, TypeError) as err:
            report(f"Serato tempomap decode failed ({MP4_TEMPOMAP_KEY}, {type(err).__name__}): "
                   f"invalid base64 text or envelope header; using detected grid.")
            return None
        markers = parse_tempomap(payload)
        if markers is None:
            report(f"Serato tempomap parse failed ({MP4_TEMPOMAP_KEY}): malformed or unsupported binary; "
                   "using detected grid.")
        return markers
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        return None
    except (MutagenError, OSError):
        report("Could not read Serato tags; using detected grid.")
        return None
    
    # Try BeatGrid first
    for frame in tags.getall("GEOB"):
        if frame.desc == "Serato BeatGrid":
            markers = parse_beatgrid(frame.data)
            if markers is None:
                report("Malformed or unsupported Serato beatgrid tag; trying tempomap.")
            else:
                return markers
            break
    
    # Fall back to Tempo Map if BeatGrid wasn't found or failed
    for frame in tags.getall("GEOB"):
        if frame.desc == "Serato Tempo Map":
            markers = parse_tempomap(frame.data)
            if markers is None:
                report("Malformed or unsupported Serato tempomap tag; using detected grid.")
            return markers
    
    return None


def grid_from_markers(markers):
    """(t1, bar_s, note) for a constant-tempo Serato grid, or (None, None, reason). Bar = 4 beats from marker 1.
    
    Handles both BeatGrid (with beats_to_next) and Tempo Map (with direct BPM per marker) formats.
    Variable-tempo maps are rejected as unsupported.
    """
    if not markers:
        return None, None, "no beatgrid markers"
    
    # Detect format: BeatGrid has beats_to_next, Tempo Map has bpm on all markers
    is_tempomap = "beats_to_next" not in markers[0] and all("bpm" in m for m in markers)
    
    try:
        if any(type(marker["pos"]) not in (int, float) for marker in markers):
            return None, None, "Serato marker positions must be numeric"
        positions = [float(marker["pos"]) for marker in markers]
        if any(not math.isfinite(position) or position < 0 for position in positions):
            return None, None, "Serato marker positions must be finite and nonnegative"
        if any(right <= left for left, right in zip(positions, positions[1:])):
            return None, None, "Serato markers are out of order"
        
        if is_tempomap:
            # Tempo Map: all markers have BPM directly
            bpms = [float(marker["bpm"]) for marker in markers]
            if any(not math.isfinite(bpm) or bpm <= 0 for bpm in bpms):
                return None, None, "Serato tempo map BPMs are invalid"
            if not 60.0 <= min(bpms) and max(bpms) <= 220.0:
                return None, None, f"Serato tempo map BPMs ({min(bpms):.1f}-{max(bpms):.1f}) are implausible"
            
            # Check if essentially constant tempo
            avg_bpm = sum(bpms) / len(bpms)
            if max(bpms) - min(bpms) > MULTI_MARKER_TOLERANCE * avg_bpm:
                return None, None, (f"Serato tempo map changes tempo (range {min(bpms):.1f}-{max(bpms):.1f} BPM, "
                                    f"{len(markers)} markers); keeping the detected grid")
            bpm = bpms[0]
        else:
            # BeatGrid: validate beats_to_next fields
            final_bpm = float(markers[-1]["bpm"])
            if not math.isfinite(final_bpm) or final_bpm <= 0:
                return None, None, "Serato beatgrid tempo is invalid"
            if any(type(marker.get("beats_to_next")) is not int or not 0 < marker["beats_to_next"] <= 0xFFFFFFFF
                   for marker in markers[:-1]):
                return None, None, "Serato beatgrid beat counts are invalid"
            
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
            return None, None, f"Serato tempo {bpm:.1f} BPM is implausible"
    
    except (ValueError, TypeError, KeyError):
        return None, None, "Serato markers are malformed"
    
    first = markers[0]
    return float(first["pos"]), 240.0 / float(bpm), ""


def reconcile(detected, markers):
    """Return (grid, note). A valid constant-tempo Serato BeatGrid is always the canonical bar ruler.

    Bar k starts at pos0 + k * 240 / bpm. The grid phase is never shifted toward detected downbeats or legacy
    (e.g. Mixed In Key) cue points; detected-tempo disagreement is only reported.
    
    When the detected tempo is approximately 2x or 0.5x the Serato tempo, the Serato grid is trusted,
    as this indicates a likely detection error (double-time or half-time detection).
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
    # Check if detected is double-time or half-time relative to Serato
    # ratio = 2.0 means bar_s is 2x detected_bar, so Serato BPM is 0.5x detected → detected is double-time
    # ratio = 0.5 means bar_s is 0.5x detected_bar, so Serato BPM is 2x detected → detected is half-time
    is_double_time = abs(ratio / 2.0 - 1.0) <= SERATO_TEMPO_TOLERANCE
    is_half_time = abs(ratio / 0.5 - 1.0) <= SERATO_TEMPO_TOLERANCE
    grid = dict(detected)
    grid.update({"source": "serato_beatgrid", "t1": round(t1, 6), "bar_s": round(bar_s, 8),
                 "bpm": round(240.0 / bar_s, 4), "offset_ms": 0.0,
                 "provenance": {"format": "Serato BeatGrid", "marker_count": len(markers),
                                "user_verified": False, "detected_tempo_agrees": agrees,
                                "detected_grid": dict(detected)}})
    if not agrees:
        serato_bpm = 240.0 / bar_s
        detected_bpm = 240.0 / detected_bar
        if is_double_time:
            return grid, (f"used the Serato beatgrid as the bar ruler ({serato_bpm:.2f} BPM); the detected tempo "
                          f"({detected_bpm:.1f} BPM) is approximately double-time, likely a detection error")
        elif is_half_time:
            return grid, (f"used the Serato beatgrid as the bar ruler ({serato_bpm:.2f} BPM); the detected tempo "
                          f"({detected_bpm:.1f} BPM) is approximately half-time, likely a detection error")
        else:
            return grid, (f"used the Serato beatgrid as the bar ruler ({serato_bpm:.2f} BPM); the detected tempo "
                          f"({detected_bpm:.1f} BPM) disagrees, so review the grid in Serato")
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
