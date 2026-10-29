"""v6.1 canonical Pop/Vocal cue evidence: BeatGrid ruler, un-snapped vocal START, chorus exit, and OUT.

All functions are pure: they take envelopes, word timings and a grid, and never open audio files.
"""
import math

from timing import bar_to_time, time_to_bar

VOCAL_ONSET_WINDOW_S = (-1.2, 0.5)
FADE_DROP_DB = 6.0
PHRASE_BARS = 4
OUT_LEAD_BARS = 8
EXIT_GO_BARS = 8
EXIT_MIX_BARS = 4


def beatgrid_ruler(pos0, bpm):
    """Canonical bar ruler from a Serato BeatGrid: bar_time(k) = pos0 + k * 4 * 60 / bpm."""
    pos0, bpm = float(pos0), float(bpm)
    if not (math.isfinite(pos0) and math.isfinite(bpm)) or bpm <= 0:
        raise ValueError("BeatGrid pos0 and bpm must be finite, with a positive bpm.")
    return {"source": "serato_beatgrid", "t1": pos0, "bar_s": 4.0 * 60.0 / bpm, "bpm": bpm, "offset_ms": 0.0}


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)] if ordered else 0.0


def _valid_envelope(values, hop_s):
    return (values is not None and len(values) > 0 and type(hop_s) in (int, float)
            and math.isfinite(hop_s) and hop_s > 0)


def _mean(values, left, right):
    left, right = max(0, left), min(len(values), right)
    return sum(values[left:right]) / (right - left) if right > left else 0.0


def vocal_threshold(values):
    return max(1e-5, min(0.04, 0.15 * max(_percentile(values, 0.95), max(values) * 0.1)))


def envelope_profile(values, hop_s, duration_s):
    """Validate whole-track coverage and retain activity prefixes for repeated window checks."""
    result = {"status": "unverified", "reason": "stem unavailable or invalid"}
    if (type(duration_s) not in (int, float) or not math.isfinite(duration_s) or duration_s <= 0
            or not _valid_envelope(values, hop_s)):
        return result
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values):
        return {**result, "reason": "stem contains invalid energy values"}
    coverage = len(values) * hop_s
    if coverage < duration_s - 1e-6:
        return {**result, "reason": "stem coverage is incomplete", "coverage_s": coverage}
    values = values[:math.ceil(duration_s / hop_s)]
    threshold = vocal_threshold(values)
    prefix = [0]
    for value in values:
        prefix.append(prefix[-1] + int(value >= threshold))
    return {"status": "analyzed", "hop_s": hop_s, "duration_s": duration_s,
            "threshold": threshold, "active_prefix": prefix}


def mix_window_evidence(vocal_profile, drum_profile, grid, first_bar, last_bar):
    """Whole-window vocal absence and per-bar drum activity, not a phrase/seam certification."""
    begin, end = bar_to_time(first_bar, grid), bar_to_time(last_bar, grid)
    result = {"status": "unverified", "start_bar": first_bar, "end_bar": last_bar,
              "start_s": round(begin, 3), "end_s": round(end, 3)}
    if vocal_profile["status"] != "analyzed":
        return {**result, "reason": "vocal " + vocal_profile["reason"]}
    if begin < 0 or end <= begin or end > vocal_profile["duration_s"] + 1e-6:
        return {**result, "reason": "window lies outside covered audio"}

    def active(profile, left, right):
        hop = profile["hop_s"]
        first = max(0, math.floor(left / hop + 1e-9))
        last = min(len(profile["active_prefix"]) - 1, math.ceil(right / hop - 1e-9))
        return profile["active_prefix"][last] - profile["active_prefix"][first]

    frames = active(vocal_profile, begin, end)
    result["active_vocal_frames"] = frames
    if frames:
        return {**result, "status": "vocal_overlap",
                "reason": "detected vocal-stem activity inside the window (speech or singing may be present)"}
    if drum_profile["status"] != "analyzed":
        return {**result, "reason": "drum " + drum_profile["reason"]}
    if end > drum_profile["duration_s"] + 1e-6:
        return {**result, "reason": "window lies outside covered drum audio"}
    for bar in range(first_bar, last_bar):
        if not active(drum_profile, bar_to_time(bar, grid), bar_to_time(bar + 1, grid)):
            return {**result, "status": "no_drums", "reason": f"no detected drum activity in bar {bar}"}
    return {**result, "status": "candidate",
            "reason": "no detected vocal activity; drums detected in every bar; listen for leakage, missed vocals and loop seam"}


def intro_loop_evidence(grid, first_bar, start_bar, duration_s, stems, hop_s,
                        intro_beats=(32, 16, 8, 4), out_bar=None, outro_first_bar=None):
    """Assess intro leads and propose an outro-derived Cue Loop without inventing Serato serialization."""
    profiles = {key: envelope_profile((stems or {}).get(key), hop_s, duration_s) for key in ("vocals", "drums")}

    def check(begin, end):
        return mix_window_evidence(profiles["vocals"], profiles["drums"], grid, begin, end)

    windows = [check(start_bar - beats // 4, start_bar)
               for beats in intro_beats if start_bar - beats // 4 >= first_bar]
    best = next((window for window in windows if window["status"] == "candidate"), None)
    intro = {"status": "candidate" if best else
             "unverified" if any(profile["status"] != "analyzed" for profile in profiles.values()) else "unavailable",
             "windows": windows, "selected": best,
             "reason": best["reason"] if best else "no supported fully vocal-free intro window found"}
    fallback = {"status": "not_needed" if best else "unverified" if intro["status"] == "unverified" else "unavailable",
                "target_slot": "A", "start_slot": "B", "proposed_start_bar": first_bar,
                "applied": False, "reason": "Cue Loop fallback has not been applied"}
    if best or intro["status"] == "unverified":
        return {"intro": intro, "intro_loop_fallback": fallback}
    last_bar = min(int(math.floor(time_to_bar(duration_s, grid) + 1e-6)),
                   out_bar if out_bar is not None else int(math.floor(time_to_bar(duration_s, grid) + 1e-6)))
    # Search only a detected outro, or the final 32 bars after START when no section exists.
    first = max(start_bar, first_bar, outro_first_bar if outro_first_bar is not None else last_bar - 32)
    for beats in intro_beats:
        count = beats // 4
        for begin in range(first, last_bar - count + 1):
            window = check(begin, begin + count)
            if window["status"] == "candidate":
                fallback.update(status="needs_serato_fixture", loop={**window, "bars": count, "beats": beats},
                                reason="outro loop candidate found; Serato 5 Cue Loop linkage is not yet verified; no cues or loop tags changed")
                return {"intro": intro, "intro_loop_fallback": fallback}
    fallback["reason"] = "no supported fully vocal-free drum loop found near the outro"
    return {"intro": intro, "intro_loop_fallback": fallback}


def first_sung_word(words, vocals, hop_s, duration_s):
    """Start time of the first WhisperX word that the vocal stem confirms is actually sung."""
    threshold = vocal_threshold(vocals) if _valid_envelope(vocals, hop_s) else None
    for word in sorted((w for w in words or [] if isinstance(w, dict)),
                       key=lambda w: w.get("start") if isinstance(w.get("start"), (int, float)) else math.inf):
        start, end = word.get("start"), word.get("end")
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float))
                or not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration_s):
            continue
        if threshold is None:
            return float(start)
        left, right = int(start / hop_s), int(math.ceil(end / hop_s))
        if any(value >= threshold for value in vocals[max(0, left):min(len(vocals), right)]):
            return float(start)
    return None


def _rising_edges(values, hop_s, threshold, left_s, right_s, quiet_s=0.1, sustain_s=0.1):
    quiet, sustain = max(1, round(quiet_s / hop_s)), max(1, round(sustain_s / hop_s))
    first, last = max(1, int(math.floor(left_s / hop_s))), min(len(values) - 1, int(math.ceil(right_s / hop_s)))
    edges = []
    for index in range(first, last + 1):
        if (values[index] >= threshold and values[index - 1] < threshold
                and _mean(values, index - quiet, index) < threshold
                and _mean(values, index, index + sustain) >= threshold):
            edges.append(index * hop_s)
    return edges


def refine_vocal_onset(word_start, grid, vocals=None, hop_s=None):
    """Exact un-snapped START time near the first sung word, plus its structural anchor bar.

    The anchor is the downbeat nearest the first word; the vocal energy onset is searched within
    [-1.2 s, +0.5 s] of that downbeat so pickups before the bar keep their real millisecond position.
    """
    anchor = int(round(time_to_bar(word_start, grid)))
    downbeat = bar_to_time(anchor, grid)
    time_s, source = float(word_start), "whisperx_first_word"
    if _valid_envelope(vocals, hop_s):
        left = downbeat + VOCAL_ONSET_WINDOW_S[0]
        right = min(downbeat + VOCAL_ONSET_WINDOW_S[1], word_start + 0.25)
        edges = _rising_edges(vocals, hop_s, vocal_threshold(vocals), left, right)
        if edges:
            time_s = min(edges, key=lambda edge: abs(edge - word_start))
            source = "vocal_stem_energy_onset"
    time_s = round(max(0.0, time_s), 3)
    return {"time_s": time_s, "anchor_bar": anchor, "downbeat_s": round(downbeat, 3),
            "offset_from_downbeat_s": round(time_s - downbeat, 3), "source": source}


def first_vocal_onset(vocals, hop_s, min_sustain_s=0.5):
    """First sustained vocal-stem onset for tracks without usable word timings."""
    if not _valid_envelope(vocals, hop_s):
        return None
    threshold = vocal_threshold(vocals)
    edges = _rising_edges(vocals, hop_s, threshold, hop_s, len(vocals) * hop_s, sustain_s=min_sustain_s)
    return edges[0] if edges else None


def _bar_level(values, step_s, grid, bar, count):
    if not values or step_s <= 0:
        return None
    left = int(bar_to_time(bar, grid) / step_s)
    right = int(bar_to_time(bar + count, grid) / step_s)
    left, right = max(0, left), min(len(values), right)
    return sum(values[left:right]) / (right - left) if right > left else None


def refine_chorus_exit(candidate_bar, grid, start_bar, bar_count, vocals=None, hop_s=0.05, energy=None, fps=50):
    """Confirm or move the lyric chorus end to the 4-bar phrase boundary with the clearest vocal/RMS drop-off."""
    if candidate_bar is None:
        return None, {"status": "unavailable", "reason": "no chorus end candidate"}

    def score(bar):
        parts, detail = [], {}
        for name, values, step in (("vocal", vocals, hop_s), ("rms", energy, 1 / fps if fps else 0)):
            before = _bar_level(values, step, grid, bar - PHRASE_BARS, PHRASE_BARS)
            after = _bar_level(values, step, grid, bar, PHRASE_BARS)
            if before is None or after is None or before <= 0:
                continue
            ratio = before / max(after, before * 1e-3)
            detail[name + "_drop_ratio"] = round(ratio, 3)
            parts.append(math.log(ratio) * (1.0 if name == "vocal" else 0.5))
        return (sum(parts) if parts else None), detail

    base, base_detail = score(candidate_bar)
    evidence = {"status": "kept", "candidate_bar": candidate_bar, **base_detail}
    if base is None:
        evidence.update(status="unverified", reason="no vocal or RMS envelope to confirm the chorus end")
        return candidate_bar, evidence
    best, best_score, best_detail = candidate_bar, base, base_detail
    for bar in (candidate_bar - PHRASE_BARS, candidate_bar + PHRASE_BARS):
        if bar - PHRASE_BARS < start_bar or bar + PHRASE_BARS > bar_count:
            continue
        value, detail = score(bar)
        if value is not None and value > best_score + 0.25 and detail.get("vocal_drop_ratio", 0) >= 1.5:
            best, best_score, best_detail = bar, value, detail
    if best != candidate_bar:
        evidence.update(status="moved", moved_to=best, **{"chosen_" + key: value for key, value in best_detail.items()})
        evidence["reason"] = "moved to the adjacent 4-bar boundary with a stronger vocal/RMS drop-off"
    elif max(base_detail.values(), default=0) >= 1.5:
        evidence["reason"] = "chorus end supported by a 4-bar vocal/RMS drop-off; phrase accuracy still requires review"
    else:
        evidence.update(status="unverified", reason="candidate retained without a clear 4-bar vocal/RMS drop-off")
    return best, evidence


def detect_out(energy, fps, grid, start_bar, duration_s, vocals=None, hop_s=0.05, drums=None):
    """Place OUT/GO-8OUT for drum-outro edits versus fade-outs.

    Re-Drum / intro-outro edits play at full volume to their last bar: OUT is that final downbeat.
    Fade-outs: OUT is the last 4-bar phrase boundary before bar RMS falls more than 6 dB into the tail.
    """
    result = {"kind": "unknown", "out_bar": None, "go_bar": None, "reason": "full-mix energy unavailable"}
    if not energy or fps <= 0:
        return result
    full_bars = int(math.floor(time_to_bar(duration_s, grid) + 1e-6))
    levels = {}
    for bar in range(max(0, start_bar), full_bars):
        level = _bar_level(energy, 1 / fps, grid, bar, 1)
        if level is not None:
            levels[bar] = level
    body = [levels[bar] for bar in sorted(levels)]
    if len(body) < OUT_LEAD_BARS + 1:
        result["reason"] = "too few full bars after START"
        return result
    reference = _percentile(body, 0.5)
    threshold = reference * 10 ** (-FADE_DROP_DB / 20)
    drop_bar = full_bars
    for bar in range(full_bars - 1, start_bar - 1, -1):
        if levels.get(bar) is None or levels[bar] >= threshold:
            break
        drop_bar = bar
    tail = [levels[bar] for bar in range(drop_bar, full_bars) if bar in levels]
    fading = len(tail) >= 3 and all(right <= left * 1.1 for left, right in zip(tail, tail[1:]))
    if fading:
        out_bar = start_bar + ((drop_bar - start_bar) // PHRASE_BARS) * PHRASE_BARS
        kind = "fade_out"
        reason = (f"fade-out: bar RMS falls more than {FADE_DROP_DB:g} dB from bar {drop_bar}; "
                  f"OUT on the last full-volume {PHRASE_BARS}-bar phrase boundary")
    else:
        out_bar = drop_bar
        window = mix_window_evidence(envelope_profile(vocals, hop_s, duration_s),
                                     envelope_profile(drums, hop_s, duration_s),
                                     grid, out_bar - OUT_LEAD_BARS, out_bar)
        kind = ("drum_outro" if window["status"] == "candidate" else
                "hard_end" if window["status"] == "vocal_overlap" else "unverified_end")
        reason = ("drum-outro candidate: full-volume music runs to the final bar; listen before using the 8-bar mix-out"
                  if kind == "drum_outro" else
                  "OUT at the final full bar; " + window["reason"])
    go_bar = out_bar - OUT_LEAD_BARS
    if go_bar < start_bar:
        return {"kind": kind, "out_bar": None, "go_bar": None,
                "reason": "not enough bars after START for an 8-bar mix-out", "drop_bar": drop_bar}
    return {"kind": kind, "out_bar": out_bar, "go_bar": go_bar, "drop_bar": drop_bar,
            "mix_window_evidence": window if not fading else None,
            "reference_rms": round(reference, 6), "reason": reason,
            "confidence": 0.7 if kind == "drum_outro" else 0.6 if kind == "fade_out" else 0.45}
