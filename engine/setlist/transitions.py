"""Explicit play-window arithmetic and evidence cards, never a mix-safety verdict."""
from fractions import Fraction
import math

from .scoring import camelot_key


def _number(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return Fraction(str(value))


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
