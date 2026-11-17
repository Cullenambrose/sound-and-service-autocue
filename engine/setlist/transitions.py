"""Explicit play-window arithmetic and evidence cards, never a mix-safety verdict."""
from fractions import Fraction
import math
import re
from decimal import Decimal, localcontext

from .scoring import camelot_key


_CLOCK_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?$")
_MAX_PLAN_SECONDS = 172800


def _number(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return Fraction(str(value))


def validate_wall_clock_start(value):
    if not isinstance(value, str) or not _CLOCK_TIME.fullmatch(value):
        raise ValueError("event_start_time must be a local 24-hour HH:MM or HH:MM:SS clock time.")
    return value


def _clock_seconds(value):
    parts = validate_wall_clock_start(value).split(":")
    total = Fraction(int(parts[0]) * 3600 + int(parts[1]) * 60)
    if len(parts) == 3:
        total += Fraction(parts[2])
    return total


def _clock_at(total):
    day_offset = total.numerator // (total.denominator * 86400)
    within_day = total - day_offset * 86400
    hour = within_day.numerator // (within_day.denominator * 3600)
    within_hour = within_day - hour * 3600
    minute = within_hour.numerator // (within_hour.denominator * 60)
    second = within_hour - minute * 60
    whole_second = second.numerator // second.denominator
    fractional_second = second - whole_second
    clock = f"{hour:02d}:{minute:02d}:{whole_second:02d}"
    if fractional_second:
        with localcontext() as context:
            context.prec = 400
            fraction = Decimal(fractional_second.numerator) / Decimal(fractional_second.denominator)
        clock += "." + format(fraction, "f").partition(".")[2].rstrip("0")
    return clock, day_offset


def full_file_timeline(tracks):
    """Build relative no-overlap track intervals from exact full-file durations."""
    if not isinstance(tracks, list) or len(tracks) > 60:
        raise ValueError("tracks must be a list of at most 60 entries")
    timeline = []
    elapsed = Fraction(0)
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError("each track must be an object")
        file_id = _id(track.get("id"))
        duration = _number(track.get("duration_s"), "duration_s")
        if duration <= 0:
            raise ValueError("duration_s must be positive")
        end = elapsed + duration
        timeline.append({"file_id": file_id, "start_s": float(elapsed), "end_s": float(end)})
        elapsed = end
    return timeline


def project_wall_clock_timeline(timeline, start_time, basis):
    """Project relative intervals to a local clock without inferring date or time zone."""
    start_time = validate_wall_clock_start(start_time)
    if not isinstance(basis, str) or basis not in {"full_file_duration", "reviewed_playback_estimate"}:
        raise ValueError("basis must identify full-file duration or reviewed-playback estimate")
    if not isinstance(timeline, list) or len(timeline) > 60:
        raise ValueError("timeline must be a list of at most 60 entries")
    base_seconds = _clock_seconds(start_time)

    projected = []
    seen = set()

    previous_start = Fraction(-1)
    for item in timeline:
        if not isinstance(item, dict) or set(item) != {"file_id", "start_s", "end_s"}:
            raise ValueError("each timeline entry needs file_id, start_s and end_s")
        file_id = _id(item["file_id"])
        if file_id in seen:
            raise ValueError("timeline file IDs must be unique")
        seen.add(file_id)
        _number(item["start_s"], "start_s")
        _number(item["end_s"], "end_s")
        start = Fraction(str(item["start_s"]))
        end = Fraction(str(item["end_s"]))
        if start > _MAX_PLAN_SECONDS or end > _MAX_PLAN_SECONDS:
            raise ValueError("timeline offsets exceed the planner's 48-hour maximum")
        if end < start or start < previous_start:
            raise ValueError("timeline offsets must be ordered and each end must follow its start")
        previous_start = start
        start_clock, start_day = _clock_at(base_seconds + start)
        end_clock, end_day = _clock_at(base_seconds + end)
        projected.append({
            "file_id": file_id, "start_s": float(start), "end_s": float(end),
            "start_clock": start_clock, "start_day_offset": start_day,
            "end_clock": end_clock, "end_day_offset": end_day,
        })

    timing_note = (
        "Full-file clock projection with no overlap or trimming assumed."
        if basis == "full_file_duration" else
        "Clock projection of reviewed-window arithmetic; not measured playback."
    )
    return {
        "status": "estimated", "basis": basis, "event_local_start_time": start_time,
        "timeline": projected,
        "note": timing_note + " No event date, time zone, or live schedule correction is inferred.",
    }


def rebase_wall_clock_timeline(projection, current_file_id, actual_start_time, actual_day_offset=0):
    """Shift a forecast by a manually observed current-track start; never edits its order."""
    if (not isinstance(projection, dict)
            or set(projection) != {"status", "basis", "event_local_start_time", "timeline", "note"}
            or projection.get("status") != "estimated"):
        raise ValueError("Only a complete estimated wall-clock projection can be rebased.")
    if (not isinstance(projection.get("basis"), str)
            or projection["basis"] not in {"full_file_duration", "reviewed_playback_estimate"}):
        raise ValueError("Projection has an unsupported timing basis.")
    if not isinstance(current_file_id, str) or not current_file_id.strip():
        raise ValueError("current_file_id must be a nonblank exact file ID.")
    if type(actual_day_offset) is not int or not -1 <= actual_day_offset <= 2:
        raise ValueError("actual_day_offset must be an integer from -1 through 2.")
    actual_start_time = validate_wall_clock_start(actual_start_time)
    timeline = projection.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        raise ValueError("Projection has no track intervals to rebase.")
    if any(not isinstance(item, dict) or set(item) != {
            "file_id", "start_s", "end_s", "start_clock", "start_day_offset",
            "end_clock", "end_day_offset"} for item in timeline):
        raise ValueError("Projection contains malformed track intervals.")
    canonical = project_wall_clock_timeline([
        {"file_id": item["file_id"], "start_s": item["start_s"], "end_s": item["end_s"]}
        for item in timeline
    ], projection["event_local_start_time"], projection["basis"])
    if canonical != projection:
        raise ValueError("Projection changed; regenerate it before rebasing.")
    anchors = [item for item in timeline if isinstance(item, dict)
               and item.get("file_id") == current_file_id]
    if len(anchors) != 1:
        raise ValueError("current_file_id must identify one exact track in the projection.")
    anchor = anchors[0]
    if type(anchor.get("start_day_offset")) is not int:
        raise ValueError("Projected current-track day offset is invalid.")
    planned_start = _clock_seconds(anchor.get("start_clock")) + anchor["start_day_offset"] * 86400
    actual_start = _clock_seconds(actual_start_time) + actual_day_offset * 86400
    shift = actual_start - planned_start
    rebased = []
    for item in timeline:
        if (not isinstance(item, dict) or type(item.get("start_day_offset")) is not int
                or type(item.get("end_day_offset")) is not int):
            raise ValueError("Projection contains invalid day offsets.")
        start = _clock_seconds(item.get("start_clock")) + item["start_day_offset"] * 86400 + shift
        end = _clock_seconds(item.get("end_clock")) + item["end_day_offset"] * 86400 + shift
        start_clock, start_day = _clock_at(start)
        end_clock, end_day = _clock_at(end)
        rebased.append({**item, "start_clock": start_clock, "start_day_offset": start_day,
                        "end_clock": end_clock, "end_day_offset": end_day})
    new_start, new_day = _clock_at(_clock_seconds(projection["event_local_start_time"]) + shift)
    return {
        "status": "reforecast", "basis": projection["basis"],
        "event_local_start_time": new_start, "event_day_offset": new_day,
        "timeline": rebased,
        "anchor": {"file_id": current_file_id, "planned_start_clock": anchor["start_clock"],
                   "planned_day_offset": anchor["start_day_offset"],
                   "actual_start_clock": actual_start_time, "actual_day_offset": actual_day_offset},
        "shift_s": float(shift),
        "note": "Forecast shifted from a manually observed current-track start. Song order and durations are unchanged; "
                "this does not skip tracks, shorten playback, or move fixed event moments.",
    }


def _id(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("playback IDs must be nonblank strings")
    return value


def validate_playback(value):
    if not isinstance(value, dict) or set(value) != {"windows", "overlaps"}:
        raise ValueError("playback needs exactly windows and overlaps")
    if any(not isinstance(value[key], list) or len(value[key]) > 60 for key in value):
        raise ValueError("playback windows/overlaps must be lists of at most 60 entries")
    windows, overlaps = [], []
    seen = set()
    for window in value["windows"]:
        if not isinstance(window, dict) or set(window) != {
                "file_id", "content_hash", "entry_s", "exit_s", "reviewed"}:
            raise ValueError("each window needs file_id, content_hash, entry_s, exit_s and reviewed")
        file_id = _id(window["file_id"])
        if file_id in seen:
            raise ValueError("duplicate play-window file ID")
        seen.add(file_id)
        digest = window["content_hash"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ValueError("play-window content_hash must be a lowercase SHA-256")
        if type(window["reviewed"]) is not bool:
            raise ValueError("window reviewed must be boolean")
        if _number(window["entry_s"], "entry_s") >= _number(window["exit_s"], "exit_s"):
            raise ValueError("entry_s must be before exit_s")
        windows.append(dict(window))
    seen = set()
    for overlap in value["overlaps"]:
        if not isinstance(overlap, dict) or set(overlap) != {"from_id", "to_id", "seconds"}:
            raise ValueError("each overlap needs from_id, to_id and seconds")
        pair = (_id(overlap["from_id"]), _id(overlap["to_id"]))
        if pair[0] == pair[1] or pair in seen:
            raise ValueError("overlap pairs must be distinct ordered files without duplicates")
        seen.add(pair)
        _number(overlap["seconds"], "overlap seconds")
        overlaps.append(dict(overlap))
    return {"windows": windows, "overlaps": overlaps}


def transition_report(tracks, playback=None):
    """Report arithmetic only for complete reviewed, hash-bound, bounded evidence."""
    playback = validate_playback(playback if playback is not None else {"windows": [], "overlaps": []})
    windows = {item["file_id"]: item for item in playback["windows"]}
    overlaps = {(item["from_id"], item["to_id"]): item["seconds"] for item in playback["overlaps"]}
    ids = [track["id"] for track in tracks]
    pairs = list(zip(ids, ids[1:]))
    issues, selected, cards = [], [], []
    for track in tracks:
        file_id = track["id"]
        window = windows.get(file_id)
        reasons = []
        duration = track.get("duration_s")
        if not window:
            reasons.append("missing_play_window")
        else:
            if window["content_hash"] != track.get("content_hash"):
                reasons.append("window_hash_mismatch")
            if not window["reviewed"]:
                reasons.append("window_not_reviewed")
            if (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0):
                reasons.append("unknown_full_duration")
            elif Fraction(str(window["exit_s"])) > Fraction(str(duration)):
                reasons.append("window_outside_file")
        issues.extend({"code": reason, "file_id": file_id} for reason in reasons)
        selected.append({"file_id": file_id, "content_hash": track.get("content_hash"),
                         "window": window, "valid": not reasons,
                         "cue_evidence": track.get("cue_evidence"),
                         "warnings": reasons})
    spans = [Fraction(str(item["window"]["exit_s"])) - Fraction(str(item["window"]["entry_s"]))
             if item["valid"] else None for item in selected]
    seconds = []
    for index, (left, right) in enumerate(zip(tracks, tracks[1:])):
        pair = (left["id"], right["id"])
        overlap = overlaps.get(pair)
        warnings = ["audition_required", "phrase_and_vocal_compatibility_not_certified"]
        if overlap is None:
            issues.append({"code": "missing_explicit_overlap", "from_id": pair[0], "to_id": pair[1]})
        elif all(span is not None for span in spans[index:index + 2]):
            if Fraction(str(overlap)) > min(spans[index:index + 2]):
                issues.append({"code": "overlap_exceeds_window", "from_id": pair[0], "to_id": pair[1]})
        seconds.append(Fraction(str(overlap)) if overlap is not None else None)
        bpm_values = [track.get("bpm") for track in (left, right)]
        valid_bpm = all(type(bpm) in (int, float) and math.isfinite(bpm) and bpm > 0 for bpm in bpm_values)
        tempo_change = (bpm_values[1] / bpm_values[0] - 1) * 100 if valid_bpm else None
        if tempo_change is not None and not math.isfinite(tempo_change):
            tempo_change = None
            warnings.append("tempo_difference_out_of_range")
        if not valid_bpm:
            warnings.append("unknown_bpm")
        keys = [camelot_key(track) for track in (left, right)]
        if not all(keys):
            warnings.append("unknown_key")
        for side, track in (("outgoing", left), ("incoming", right)):
            evidence = track.get("cue_evidence")
            if not evidence:
                warnings.append(side + "_cue_analysis_missing")
            else:
                if not evidence["reviewed"]:
                    warnings.append(side + "_cue_analysis_unreviewed")
                if evidence.get("error"):
                    warnings.append(side + "_analysis_error")
                if evidence.get("flags"):
                    warnings.append(side + "_analysis_flags")
                if evidence.get("grid_diagnostics"):
                    warnings.append(side + "_grid_diagnostics_require_review")
                statuses = {
                    cue.get("mix_window_evidence", {}).get("status")
                    for cue in evidence.get("cues", {}).values()
                    if isinstance(cue, dict) and isinstance(cue.get("mix_window_evidence"), dict)
                }
                if "vocal_overlap" in statuses:
                    warnings.append(side + "_detected_vocal_activity_in_candidate_window")
                if evidence.get("mix_window_evidence"):
                    warnings.append(side + "_candidate_stem_windows_require_audition")
        cards.append({
            "from_id": pair[0], "to_id": pair[1],
            "from_version": left.get("version"), "to_version": right.get("version"),
            "bpm": bpm_values, "camelot_keys": keys, "tempo_change_percent": tempo_change,
            "overlap_s": overlap, "warnings": warnings,
            "assumptions": "Raw tagged/analyzed BPM, no tempo automation or key shift. Explicit overlap is a DJ plan, not measured playback.",
        })
    for index, span in enumerate(spans):
        incoming = seconds[index - 1] if index else Fraction(0)
        outgoing = seconds[index] if index < len(seconds) else Fraction(0)
        if span is not None and incoming is not None and outgoing is not None and incoming + outgoing > span:
            issues.append({"code": "combined_overlaps_exceed_window", "file_id": ids[index]})
    valid = bool(tracks) and not issues
    subtotal = sum(spans, Fraction(0)) if all(span is not None for span in spans) and tracks else None
    elapsed = subtotal - sum(seconds, Fraction(0)) if valid else None
    timeline = []
    if valid:
        start = Fraction(0)
        for index, (item, span) in enumerate(zip(selected, spans)):
            timeline.append({"file_id": item["file_id"], "start_s": float(start), "end_s": float(start + span)})
            start += span
            if index < len(seconds):
                start -= seconds[index]
    full = [track.get("duration_s") for track in tracks]
    full_known = all(type(duration) in (int, float) and math.isfinite(duration) and duration > 0 for duration in full)
    return {
        "schema_version": 1, "status": "estimated" if valid else "incomplete",
        "full_file_runtime_s": float(sum((Fraction(str(duration)) for duration in full), Fraction(0)))
        if full_known and tracks else None,
        "play_window_subtotal_s": float(subtotal) if subtotal is not None else None,
        "estimated_elapsed_s": float(elapsed) if elapsed is not None else None,
        "timeline": timeline, "tracks": selected, "transitions": cards, "issues": issues,
        "unused_window_ids": sorted(set(windows) - set(ids)),
        "unused_overlap_pairs": [list(pair) for pair in sorted(set(overlaps) - set(pairs))],
        "note": "Explicit reviewed-window arithmetic only. Not a musical, grid, vocal, phrase or live-playback certification. "
                "Full-track planner constraints are unchanged; this estimate does not certify their target as mixed elapsed time.",
    }
