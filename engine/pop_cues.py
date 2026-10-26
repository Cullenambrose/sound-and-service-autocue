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
    return (values is not None and len(values) > 0 and isinstance(hop_s, (int, float))
            and math.isfinite(hop_s) and hop_s > 0)


def _mean(values, left, right):
    left, right = max(0, left), min(len(values), right)
    return sum(values[left:right]) / (right - left) if right > left else 0.0


def vocal_threshold(values):
    return max(1e-5, 0.15 * max(_percentile(values, 0.95), max(values) * 0.1))


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
    evidence["reason"] = ("lyric chorus end confirmed by a 4-bar vocal/RMS drop-off" if best == candidate_bar
                          else "moved to the adjacent 4-bar boundary with a stronger vocal/RMS drop-off")
    return best, evidence


def detect_out(energy, fps, grid, start_bar, duration_s, vocals=None, hop_s=0.05):
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
        vocal_free = None
        if _valid_envelope(vocals, hop_s):
            level = _bar_level(vocals, hop_s, grid, out_bar - OUT_LEAD_BARS, OUT_LEAD_BARS)
            vocal_free = level is not None and level < vocal_threshold(vocals)
        kind = "drum_outro" if vocal_free in (True, None) else "hard_end"
        reason = ("clean edit: full-volume music runs to the final bar; OUT at the end of the 8-bar mix-out"
                  if kind == "drum_outro" else
                  "track ends at full volume with vocals in the last 8 bars; OUT at the final full bar")
    go_bar = out_bar - OUT_LEAD_BARS
    if go_bar < start_bar:
        return {"kind": kind, "out_bar": None, "go_bar": None,
                "reason": "not enough bars after START for an 8-bar mix-out", "drop_bar": drop_bar}
    return {"kind": kind, "out_bar": out_bar, "go_bar": go_bar, "drop_bar": drop_bar,
            "reference_rms": round(reference, 6), "reason": reason,
            "confidence": 0.7 if kind == "drum_outro" else 0.6 if kind == "fade_out" else 0.45}
