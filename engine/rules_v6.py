"""Switchable v6 cue maps built from cached v5 analysis, local transcripts, and optional stems."""
import argparse
import csv
import json
import math
import os
import sys

from synced_lyrics import align_synced_lyrics
from timing import bar_to_time, time_to_bar
import cue_maps as cue_map_settings
import pop_cues

TOOLS = os.path.dirname(os.path.abspath(__file__))
SWITCHES = (
    "synced_lyrics",
    "drop_detection",
    "genre_map",
    "gemini_tiebreaker",
    "pop_v61",
)
DEFAULT_CONFIG = {
    "switches": {
        "synced_lyrics": True,
        "drop_detection": True,
        "genre_map": True,
        "gemini_tiebreaker": False,
        "pop_v61": True,
    },
    "drop_genres": ["house", "edm", "dance", "electronic", "techno", "trance", "disco", "dubstep", "drum and bass", "dnb", "bass music", "electro", "garage"],
    "drop_bpm_range": [118, 150],
    "drop_min_confidence": 0.65, "vocal_led_ratio": 0.5, "late_drop_fraction": 0.5,
    "drop_phrase_bars": 8,
    "drop_recovery_ratio": 1.8,
    "genres": {
        "house_edm": {"phrase_bars": 32},
        "hiphop_rnb": {"phrase_bars": 16},
        "pop": {"phrase_bars": 8},
        "latin": {"phrase_bars": 16},
        "other": {"phrase_bars": 8},
    },
    "gemini_confidence_threshold": 0.6,
    "gemini_spend_cap_usd": 3.0,
}


def _load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def _normal_label(section):
    return str(section.get("label", section.get("lab", ""))).casefold().replace("_", " ")


def _section_bars(section, grid):
    if section.get("j0") is not None:
        start = int(round(section["j0"]))
    else:
        start = int(round(time_to_bar(section.get("start", section.get("start_s", 0)), grid)))
    if section.get("j1") is not None:
        end = int(round(section["j1"]))
    else:
        end = int(round(time_to_bar(section.get("end", section.get("end_s", 0)), grid)))
    return start, end


def _bar_time(bar, grid):
    return round(bar_to_time(bar, grid), 3)


def _cue(name, bar, confidence, reason, flags=None, grid=None):
    return {
        "name": name,
        "bar": bar,
        "time_s": None if bar is None or grid is None else _bar_time(bar, grid),
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "reason": reason,
        "flags": list(flags or []),
    }


def _bar_mean(values, hop_s, grid, bar, count=1):
    if bar < 0 or hop_s <= 0:
        return None
    start = max(0, int(bar_to_time(bar, grid) / hop_s))
    end = max(start, int(bar_to_time(bar + count, grid) / hop_s))
    chunk = values[start:end]
    return sum(chunk) / len(chunk) if chunk else None


def detect_drops(stems, low_energy, hop_s, grid, duration_s, phrase_bars=8, recovery_ratio=1.8, relaxed=False):
    """Find downbeat-aligned low-end returns after a sustained breakdown.

    `relaxed` accepts shallower breakdowns and smaller rises, for tracks where the strict pass finds too few drops.
    """
    baseline_limit, low_ratio_min, confidence_cap = (0.85, 1.1, 0.66) if relaxed else (0.65, 1.25, 0.95)
    if relaxed:
        recovery_ratio = min(recovery_ratio, 1.4)
    drums, bass = stems.get("drums"), stems.get("bass")
    if not drums or not bass or not low_energy:
        return []
    bar_count = max(0, int(math.ceil(time_to_bar(duration_s, grid))))
    first = max(0, int(math.ceil(time_to_bar(0, grid) - 1e-6)))
    bars = []
    for bar in range(first, bar_count):
        values = [_bar_mean(stems[key], hop_s, grid, bar) for key in ("drums", "bass")]
        low = _bar_mean(low_energy, hop_s, grid, bar)
        if None not in values and low is not None:
            bars.append((bar, sum(values) / 2, low))
    by_bar = {bar: (drum_bass, low) for bar, drum_bass, low in bars}
    candidates = []
    for bar, current, low in bars:
        if bar % max(1, phrase_bars) not in (0, 1):
            continue
        before = [by_bar[j] for j in range(bar - 4, bar) if j in by_bar]
        lookback = max(phrase_bars, 16)
        earlier = [by_bar[j] for j in range(max(first, bar - lookback), bar - 4) if j in by_bar]
        if len(before) < 3 or len(earlier) < 4:
            continue
        baseline = sorted(x[0] for x in before)[len(before) // 2]
        low_baseline = sorted(x[1] for x in before)[len(before) // 2]
        prior_windows = [earlier[index:index + 4] for index in range(len(earlier) - 3)]
        reference_window = max(prior_windows,
                               key=lambda chunk: sorted(row[0] for row in chunk)[len(chunk) // 2])
        reference = sorted(x[0] for x in reference_window)[len(reference_window) // 2]
        low_reference = sorted(x[1] for x in reference_window)[len(reference_window) // 2]
        if min(baseline, low_baseline, reference, low_reference) <= 0:
            continue
        if baseline > reference * baseline_limit or low_baseline > low_reference * baseline_limit:
            continue
        ratio = current / baseline
        low_ratio = low / low_baseline
        if ratio < recovery_ratio or low_ratio < low_ratio_min:
            continue
        confidence = min(confidence_cap, 0.55 + min(ratio - recovery_ratio, 1.0) * 0.15
                         + min(low_ratio - low_ratio_min, 1.0) * 0.1)
        breakdown_start = bar - 4
        for previous in range(max(first, bar - lookback), bar - 3):
            value = by_bar.get(previous)
            if value and value[0] <= reference * baseline_limit and value[1] <= low_reference * baseline_limit:
                breakdown_start = previous
                break
        candidates.append({
            "bar": bar,
            "time_s": _bar_time(bar, grid),
            "confidence": round(confidence, 2),
            "reason": "kick/bass and full-mix low-band energy return after a low-energy breakdown",
            "breakdown_start_bar": breakdown_start,
        })
    # Adjacent detections describe the same return; retain the strongest one.
    kept = []
    for candidate in candidates:
        if kept and candidate["bar"] - kept[-1]["bar"] <= 2:
            if candidate["confidence"] > kept[-1]["confidence"]:
                kept[-1] = candidate
        else:
            kept.append(candidate)
    if relaxed:
        for candidate in kept:
            candidate["reason"] = "softer kick/bass return after a shallow breakdown"
    return kept


def detect_drops_with_fallback(stems, low_energy, hop_s, grid, duration_s, phrase_bars=8, min_confidence=0.65,
                               recovery_ratio=1.8):
    """Strict detection, plus later shallow-breakdown returns when it finds exactly one confident drop."""
    drops = detect_drops(stems, low_energy, hop_s, grid, duration_s, phrase_bars, recovery_ratio)
    confident = [drop for drop in drops if drop["confidence"] >= min_confidence]
    # Shallow returns only supplement a confident drop; they never create a DROP map on their own.
    if len(confident) != 1:
        return drops
    for candidate in detect_drops(stems, low_energy, hop_s, grid, duration_s, phrase_bars, recovery_ratio,
                                   relaxed=True):
        if candidate["bar"] > confident[0]["bar"] and candidate["confidence"] >= min_confidence and all(abs(candidate["bar"] - drop["bar"]) >= 16 for drop in drops):
            drops.append(candidate)
    return sorted(drops, key=lambda drop: drop["bar"])


def _energy_choruses(energy, fps, grid, start_bar, end_bar, duration_s):
    if not energy or fps <= 0:
        return []
    bar_count = max(0, int(math.ceil(time_to_bar(duration_s, grid))))
    means = {}
    for bar in range(start_bar + 4, bar_count):
        first = max(0, int(bar_to_time(bar, grid) * fps))
        last = min(len(energy), int(bar_to_time(bar + 1, grid) * fps))
        if last > first:
            means[bar] = sum(energy[first:last]) / (last - first)
    candidates = []
    for bar, level in means.items():
        before = [means.get(j) for j in range(bar - 4, bar) if means.get(j) is not None]
        if (bar - start_bar) % 4 == 0 and len(before) == 4 and min(before) > 0:
            baseline = sorted(before)[2]
            if level >= baseline * 1.35:
                candidates.append({"start_bar": bar, "end_bar": min(bar_count, bar + 8),
                                   "confidence": 0.35, "source": "energy_jump"})
    return [span for span in candidates if end_bar is None or span["start_bar"] < end_bar]


def _choruses(analysis, grid, lyric_alignment, start_bar, end_bar, energy=None, fps=50, duration_s=0):
    spans = []
    if lyric_alignment and lyric_alignment.get("status") == "aligned":
        for span in lyric_alignment.get("choruses", []):
            first = int(round(time_to_bar(span["start_s"], grid)))
            last = int(round(time_to_bar(span["end_s"], grid)))
            first = _snap_phrase(first, start_bar)
            last = _snap_phrase(last, start_bar)
            if first >= start_bar + 4 and (end_bar is None or first < end_bar):
                spans.append({"start_bar": first, "end_bar": max(first + 1, last),
                              "confidence": span["confidence"], "source": "aligned_synced_lyrics"})
    if spans:
        return sorted(spans, key=lambda span: span["start_bar"]), "aligned_synced_lyrics"

    sections = []
    for section in analysis.get("segments", analysis.get("sections", [])):
        label = _normal_label(section)
        first, last = _section_bars(section, grid)
        if "chorus" in label and first >= start_bar + 4 and (end_bar is None or first < end_bar):
            sections.append({"start_bar": first, "end_bar": max(first + 1, last),
                             "confidence": 0.62, "source": "audio_section_labels"})
    if len(sections) > 1:
        for section in sections:
            section["confidence"] = 0.72
            section["source"] = "repeating_audio_sections"
        return sections, "repeating_audio_sections"
    if sections:
        return sections, "audio_section_labels"
    energy_spans = _energy_choruses(energy, fps, grid, start_bar, end_bar, duration_s)
    return energy_spans, "energy_jump"


def _snap_phrase(bar, anchor, phrase_bars=4):
    return int(anchor + round((bar - anchor) / phrase_bars) * phrase_bars)


def _detect_fade_outro(energy, fps, grid, duration_s, start_bar, phrase_bars, vocal_energy=None,
                       vocal_hop_s=0.05):
    """Find a sustained end-of-track fade in the final 32 bars, aligned to a phrase."""
    if energy is None or len(energy) == 0 or fps <= 0 or grid["bar_s"] <= 0:
        return None
    bar_count = max(0, int(math.ceil(time_to_bar(duration_s, grid))))
    first_candidate = max(start_bar, bar_count - 32)
    means = {}
    for bar in range(first_candidate, bar_count):
        left = max(0, int(bar_to_time(bar, grid) * fps))
        right = min(len(energy), int(bar_to_time(bar + 1, grid) * fps))
        if right > left:
            values = energy[left:right]
            means[bar] = math.sqrt(sum(float(value) ** 2 for value in values) / len(values))
    vocal_means = {}
    if vocal_energy is not None and len(vocal_energy) and vocal_hop_s > 0:
        for bar in range(first_candidate, bar_count):
            left = max(0, int(bar_to_time(bar, grid) / vocal_hop_s))
            right = min(len(vocal_energy), int(bar_to_time(bar + 1, grid) / vocal_hop_s))
            if right > left:
                values = vocal_energy[left:right]
                vocal_means[bar] = math.sqrt(sum(float(value) ** 2 for value in values) / len(values))

    # Require a real sustained drop against the preceding four-bar level. A single quiet
    # phrase or one final partial bar is not enough to claim a fade.
    for bar in range(first_candidate, bar_count - 1):
        previous = [means.get(index) for index in range(bar - 4, bar) if means.get(index) is not None]
        tail = [means.get(index) for index in range(bar, bar_count) if means.get(index) is not None]
        if len(previous) < 3 or len(tail) < 2:
            continue
        baseline = sorted(previous)[len(previous) // 2]
        tail_level = sum(tail) / len(tail)
        head_level = sum(tail[:2]) / min(2, len(tail))
        full_mix_fade = (baseline > 1e-6 and head_level <= baseline * 0.98 and tail_level <= baseline * 0.68
                         and min(tail[-2:]) <= baseline * 0.55)

        vocal_previous = [vocal_means.get(index) for index in range(bar - 4, bar)
                          if vocal_means.get(index) is not None]
        vocal_tail = [vocal_means.get(index) for index in range(bar, bar_count)
                      if vocal_means.get(index) is not None]
        vocal_fade = False
        if len(vocal_previous) >= 3 and len(vocal_tail) >= 2:
            vocal_baseline = sorted(vocal_previous)[len(vocal_previous) // 2]
            vocal_head = sum(vocal_tail[:2]) / min(2, len(vocal_tail))
            vocal_fade = (vocal_baseline > 1e-6 and vocal_head <= vocal_baseline * 0.98
                          and sum(vocal_tail) / len(vocal_tail) <= vocal_baseline * 0.5
                          and min(vocal_tail[-2:]) <= vocal_baseline * 0.35)
        if not (full_mix_fade or vocal_fade):
            continue

        snapped = _snap_phrase(bar, start_bar, max(1, phrase_bars))
        if snapped >= bar_count:
            snapped = start_bar + ((bar_count - 1 - start_bar) // max(1, phrase_bars)) * max(1, phrase_bars)
        if snapped < first_candidate or snapped < start_bar:
            continue
        return snapped
    return None


def _genre_is_drop(genre, bpm, config):
    lowered = (genre or "").casefold()
    if any(token in lowered for token in config["drop_genres"]):
        return True, "genre tag matches configured DROP genre"
    low, high = config["drop_bpm_range"]
    if low <= bpm <= high:
        return True, "BPM is in configured DROP range"
    return False, "genre and BPM do not indicate a DROP map"


def _genre_bucket(genre):
    lowered = (genre or "").casefold()
    if any(token in lowered for token in ("house", "edm", "dance", "electronic", "techno", "trance", "disco", "dubstep", "drum and bass", "dnb", "bass music", "electro", "garage")):
        return "house_edm"
    if any(token in lowered for token in ("hip hop", "hip-hop", "rap", "r&b", "rnb", "trap")):
        return "hiphop_rnb"
    if "latin" in lowered or any(token in lowered for token in ("reggaeton", "salsa", "bachata", "cumbia", "merengue")):
        return "latin"
    if "pop" in lowered:
        return "pop"
    return "other"


def _first_bar(grid, bar_s, energy, fps):
    """First bar index. A full bar of audible music before the first detected beat counts as a bar."""
    first = int(math.ceil(time_to_bar(0, grid) - 1e-6))
    if first >= 0:
        return first
    if energy is None or len(energy) == 0 or fps <= 0:
        return 0
    typical = sorted(energy)[len(energy) // 2]
    while first < 0:
        start = max(0, int(bar_to_time(first, grid) * fps))
        chunk = energy[start:int(bar_to_time(first + 1, grid) * fps)]
        if chunk and typical > 0 and sum(chunk) / len(chunk) >= 0.5 * typical:
            break
        first += 1
    return first


def _vocal_ratio(stems, hop_s, grid, start, bar_count):
    """Share of bars from START to the end with clearly audible vocals, or None without a vocal stem."""
    vocals = (stems or {}).get("vocals")
    if not vocals or start >= bar_count:
        return None
    means = [_bar_mean(vocals, hop_s, grid, bar) for bar in range(max(0, start), bar_count)]
    means = [value for value in means if value is not None]
    return sum(value >= 0.04 for value in means) / len(means) if means else None


def _electronic_start(energy, fps, vocals, hop_s, grid, first_bar, bar_count, maximum=32):
    """Find an observed entry when no existing START or opening intro boundary is available."""
    if (not energy or fps <= 0) and not vocals:
        return None
    def mean(values, step, bar, count=1):
        return _bar_mean(values, step, grid, bar, count) if values and step > 0 else None
    for bar in range(first_bar + 1, min(first_bar + maximum, bar_count - 2) + 1):
        if bar + 2 > bar_count:
            break
        lookback = min(4, bar - first_bar)
        energy_hop = 1 / fps if fps > 0 else 0
        before = mean(energy, energy_hop, bar - lookback, lookback)
        current, after = mean(energy, energy_hop, bar), mean(energy, energy_hop, bar, 2)
        if before and current and after and min(current, after) >= 1.25 * before:
            return bar, "energy rises after the intro"
        v_before = mean(vocals, hop_s, bar - lookback, lookback)
        v_current, v_after = mean(vocals, hop_s, bar), mean(vocals, hop_s, bar, 2)
        if (v_before is not None and v_current is not None and v_after is not None
                and v_before < 0.02 and min(v_current, v_after) >= 0.04):
            return bar, "vocals enter after the intro"
    return None


def _outro_vocal_evidence(vocals, hop_s, duration_s, words):
    evidence = {"status": "unverified", "last_vocal_end_s": None, "ignored_words": 0}
    if vocals is None or not len(vocals) or not math.isfinite(hop_s) or hop_s <= 0:
        evidence["reason"] = "vocal stem unavailable or invalid"
        return evidence
    if any(not math.isfinite(value) or value < 0 for value in vocals):
        evidence["reason"] = "vocal stem contains invalid energy values"
        return evidence
    evidence["coverage_s"] = len(vocals) * hop_s
    if evidence["coverage_s"] < duration_s - 1e-6:
        evidence["reason"] = "vocal stem coverage is incomplete"
        return evidence
    values = list(vocals[:math.ceil(duration_s / hop_s)])
    ordered = sorted(values)
    reference = max(ordered[int((len(ordered) - 1) * 0.95)], max(values) * 0.1)
    # Scale down for quiet stems, but never raise the previous absolute audibility ceiling.
    threshold = max(1e-5, min(0.04, reference * 0.15))
    active = [index for index, value in enumerate(values) if value >= threshold]
    last_end = min(duration_s, (active[-1] + 1) * hop_s) if active else 0.0
    if active and active[-1] == len(values) - 1:
        last_end = duration_s
    evidence.update(status="analyzed", threshold=threshold, last_stem_activity_s=last_end)
    for word in words or []:
        if not isinstance(word, dict):
            evidence["ignored_words"] += 1
            continue
        start, end = word.get("start"), word.get("end")
        valid = (isinstance(start, (int, float)) and not isinstance(start, bool)
                 and isinstance(end, (int, float)) and not isinstance(end, bool)
                 and math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration_s)
        if valid:
            left = max(0, math.floor(max(start, end - 0.25) / hop_s))
            right = min(len(values), math.ceil(end / hop_s))
            valid = any(value >= threshold for value in values[left:right])
        if not valid:
            evidence["ignored_words"] += 1
            continue
        last_end = max(last_end, end)
    evidence["last_vocal_end_s"] = last_end
    evidence["reason"] = "whole-track stem activity; leakage and reverb may extend the detected boundary"
    return evidence


def build_plan(track_id, analysis, plan, duration_s, genre="", words=None, synced_lyrics=None,
               stems=None, low_energy=None, hop_s=0.05, config=None, detected_drops=None,
               energy=None, fps=50, cue_maps=None, vocal_onset_env=None, vocal_onset_hop_s=None):
    config = {**DEFAULT_CONFIG, **(config or {})}
    config["switches"] = {**DEFAULT_CONFIG["switches"], **config.get("switches", {})}
    grid = analysis["grid"]
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("Track duration must be finite and positive.")
    bar_s = float(grid["bar_s"])
    first_bar = _first_bar(grid, bar_s, energy, fps)
    bar_count = max(first_bar, int(math.ceil(time_to_bar(duration_s, grid))))
    old_cues = plan.get("cues", {})
    start = old_cues.get("B", {}).get("bar")
    start_reason = "retained existing START on the beat grid"
    start_has_evidence = start is not None and first_bar <= start < bar_count
    retained_start = start_has_evidence
    if not start_has_evidence:
        start = None
    if start is None:
        intro_ends = [
            end for section in analysis.get("segments", analysis.get("sections", []))
            for begin, end in [_section_bars(section, grid)]
            if "intro" in _normal_label(section) and begin <= first_bar + 1
            and first_bar < end < bar_count
        ]
        start = intro_ends[0] if intro_ends else first_bar
        start_has_evidence = bool(intro_ends)
        start_reason = ("opening intro section ends" if intro_ends
                        else "START uncertain: no opening intro boundary; using the first playable bar")
    start = first_bar if start is None else int(start)
    old_exit = old_cues.get("H", {}).get("bar")
    bpm = float(grid.get("bpm") or 240 / bar_s)
    genre_bucket = _genre_bucket(genre)
    param_bucket = genre_bucket if config["switches"]["genre_map"] else "other"
    genre_params = config.get("genres", {}).get(param_bucket, {})
    genre_phrase_bars = int(genre_params.get("phrase_bars", config["drop_phrase_bars"]))
    lyric_alignment = None
    if config["switches"]["synced_lyrics"] and synced_lyrics:
        if words:
            lyric_alignment = align_synced_lyrics(synced_lyrics, words, bar_s,
                                                  fuzzy=bool(config.get("fuzzy_lyrics")))
        else:
            lyric_alignment = {"status": "rejected", "reason": "WhisperX word timings are unavailable",
                               "choruses": [], "matched_lines": 0}
    elif synced_lyrics:
        lyric_alignment = {"status": "not_run", "reason": "synced lyrics switch is off",
                           "choruses": [], "matched_lines": 0}

    drops = list(detected_drops or [])
    if not drops and config["switches"]["drop_detection"] and stems and low_energy:
        drops = detect_drops_with_fallback(stems, low_energy, hop_s, grid, duration_s,
                                           config["drop_phrase_bars"], config["drop_min_confidence"],
                                           config["drop_recovery_ratio"])
    drops.sort(key=lambda drop: drop["bar"])
    drop_confident = [drop for drop in drops if drop["confidence"] >= config["drop_min_confidence"]]
    genre_matches, genre_reason = _genre_is_drop(genre, bpm, config)
    cue_maps = cue_map_settings.normalize_cue_maps(cue_maps)
    tagged_dance = any(token in (genre or "").casefold() for token in config["drop_genres"])
    if (not start_has_evidence and config["switches"]["genre_map"]
            and (tagged_dance or genre_bucket == "house_edm")):
        found = _electronic_start(energy, fps, (stems or {}).get("vocals"), hop_s, grid, first_bar, bar_count)
        if found:
            start, start_reason = found
            start_has_evidence = True
    is_drop = bool(drop_confident and (genre_matches if config["switches"]["genre_map"] else True))
    vocal_ratio = _vocal_ratio(stems, hop_s, grid, start, bar_count)
    map_reason = "manual map choice"
    if cue_maps["mode"] == "DROP":
        is_drop = True
    elif cue_maps["mode"] == "VOCAL":
        is_drop = False
    else:
        map_reason = "confident drop and dance genre/BPM" if is_drop else "no confident drop"
        if is_drop and vocal_ratio is not None and vocal_ratio >= config["vocal_led_ratio"] and \
                min(drop["bar"] for drop in drop_confident) >= config["late_drop_fraction"] * bar_count:
            is_drop = False
            map_reason = (f"vocals in {vocal_ratio:.0%} of bars and the only drop is in the last half of the "
                          "track, so it reads as vocal-led")
    map_type = "DROP" if is_drop else "VOCAL"
    pop_layout = bool(config["switches"].get("pop_v61")) and map_type == "VOCAL"
    stem_vocals = (stems or {}).get("vocals")
    start_onset = None
    if pop_layout and not retained_start:
        onset_env, onset_hop = ((vocal_onset_env, vocal_onset_hop_s) if vocal_onset_env is not None
                                and vocal_onset_hop_s else (stem_vocals, hop_s))
        word_start = pop_cues.first_sung_word(words, stem_vocals, hop_s, duration_s)
        onset_source = "WhisperX first sung word"
        if word_start is None:
            word_start = pop_cues.first_vocal_onset(stem_vocals, hop_s)
            onset_source = "first sustained vocal-stem onset (no usable word timings)"
        if word_start is not None:
            found = pop_cues.refine_vocal_onset(word_start, grid, onset_env, onset_hop)
            if first_bar <= found["anchor_bar"] < bar_count:
                start_onset = {**found, "evidence": onset_source}
                start, start_has_evidence = found["anchor_bar"], True
                start_reason = (f"lead vocal onset at {found['time_s']:.3f}s ({onset_source}); "
                                f"structural anchor bar {found['anchor_bar']}")
    outro = next((section for section in analysis.get("segments", analysis.get("sections", []))
                  if "outro" in _normal_label(section)), None)
    fallback_outro = False
    if outro is not None:
        outro_start, _ = _section_bars(outro, grid)
        outro_start = max(first_bar, outro_start)
    else:
        outro_start = _detect_fade_outro(
            energy, fps, grid, duration_s, start, genre_phrase_bars,
            (stems or {}).get("vocals"), hop_s,
        )
        fallback_outro = outro_start is not None
    outro_vocal_flags = []
    vocal_free_bar = None
    vocals = (stems or {}).get("vocals")
    vocal_evidence = _outro_vocal_evidence(vocals, hop_s, duration_s, words)
    if vocal_evidence["status"] == "analyzed":
        last_vocal_end = vocal_evidence["last_vocal_end_s"]
        if vocal_evidence["ignored_words"]:
            outro_vocal_flags.append("ignored invalid or stem-unsupported word timings at the vocal boundary")
        outro_vocal_flags.append("vocal boundary uses stem activity; leakage/reverb require listening review")
        vocal_free_bar = int(math.ceil(time_to_bar(last_vocal_end, grid) - 1e-9))
        if outro_start is None and last_vocal_end > 0:
            outro_start = max(start, vocal_free_bar)
            fallback_outro = True
            outro_vocal_flags.append("outro inferred after the final detected vocal")
        if outro_start is not None and vocal_free_bar > outro_start:
            outro_start = vocal_free_bar
            outro_vocal_flags.append("outro moved after the final detected vocal; no later vocal returns allowed")
        if (outro_start is not None
                and bar_to_time(outro_start + 1, grid) > duration_s):
            outro_start = None
            outro_vocal_flags.append("no full vocal-free outro bar remains")
    else:
        outro_vocal_flags.append("vocal-free outro unverified: " + vocal_evidence["reason"])
    lyric_choruses = []
    if lyric_alignment and lyric_alignment.get("status") == "aligned":
        for span in lyric_alignment.get("choruses", []):
            lyric_choruses.append({
                "start_bar": _snap_phrase(int(round(time_to_bar(span["start_s"], grid))), start),
                "end_bar": _snap_phrase(int(round(time_to_bar(span["end_s"], grid))), start),
                "confidence": span["confidence"],
                "source": "aligned_synced_lyrics",
            })
    chorus_spans, chorus_source = _choruses(analysis, grid, lyric_alignment, start, old_exit,
                                            energy, fps, duration_s)
    exit_evidence = None
    if pop_layout and chorus_spans:
        first_span = chorus_spans[0]
        refined, exit_evidence = pop_cues.refine_chorus_exit(
            first_span["end_bar"], grid, start, bar_count, stem_vocals, hop_s, energy, fps)
        next_start = chorus_spans[1]["start_bar"] if len(chorus_spans) > 1 else None
        if (refined != first_span["end_bar"] and refined > first_span["start_bar"]
                and (next_start is None or refined <= next_start)):
            chorus_spans[0] = {**first_span, "end_bar": refined}
        elif refined != first_span["end_bar"]:
            exit_evidence.update(status="kept", reason="drop-off candidate would overlap a neighbouring chorus")
        exit_evidence["chorus_source"] = chorus_source
    out_detection = pop_cues.detect_out(energy, fps, grid, start, duration_s, stem_vocals, hop_s)
    flags = list(outro_vocal_flags)
    if not start_has_evidence:
        flags.append(start_reason)
    if lyric_alignment and lyric_alignment["status"] == "rejected":
        flags.append("synced lyric alignment rejected: " + lyric_alignment["reason"])
    if not config["switches"]["genre_map"]:
        flags.append("genre map switch disabled; confident drops select DROP map")
    if not genre:
        flags.append("genre tag unavailable; used BPM and default phrase settings")
    if is_drop and not genre_matches:
        flags.append("DROP selected from confident audio drop only")
    if map_reason.startswith("vocals in"):
        flags.append("vocal-led track with only a late drop; used the VOCAL map (switch to DROP if this is wrong)")
    if len(drop_confident) == 1:
        flags.append("single drop: last-drop slots repeat first-drop slots")
    if not chorus_spans and not is_drop:
        flags.append("no chorus span found from lyrics or section labels")
    if fallback_outro and not pop_layout:
        flags.append("outro inferred from sustained end-of-track energy/vocal fade; review recommended")
    if pop_layout and out_detection.get("kind") != "drum_outro":
        flags.append(f"OUT ({out_detection.get('kind')}): {out_detection.get('reason')}; review recommended")

    cues = {}
    intro = cue_map_settings.intro_candidate(start, first_bar, cue_maps["mix_lengths"]["intro"])
    intro_start = intro["bar"]
    intro_bars = 0 if intro_start is None else start - intro_start
    first_chorus = chorus_spans[0] if chorus_spans else None
    last_chorus = chorus_spans[-1] if chorus_spans else None
    intro_name = f"INTRO -{intro_bars}"
    cues["A"] = _cue(intro_name, intro_start, intro["confidence"],
                     intro["reason"], intro["flags"], grid)
    cues["B"] = _cue("START", start,
                     old_cues.get("B", {}).get("confidence", 0.6) if start_has_evidence else 0.35,
                     start_reason, [] if start_has_evidence else [start_reason], grid=grid)
    if start_onset is not None:
        cues["B"].update({"time_s": start_onset["time_s"], "unsnapped": True,
                          "confidence": 0.75 if start_onset["source"] == "vocal_stem_energy_onset" else 0.65})

    if is_drop:
        if drop_confident:
            first_drop, last_drop = drop_confident[0], drop_confident[-1]
            for slot, name, bar, reason in (
                ("C", "-32 DROP", first_drop["bar"] - 32, "32 bars before first confident drop"),
                ("D", "FIRST DROP", first_drop["bar"], first_drop["reason"]),
                ("E", "-32 LAST DROP", last_drop["bar"] - 32, "32 bars before last confident drop"),
                ("F", "LAST DROP", last_drop["bar"], last_drop["reason"]),
            ):
                cue_flags = []
                if bar < first_bar:
                    cue_flags.append("requested cue is before the track's first downbeat")
                    flags.append(f"{name} falls before the track's first downbeat")
                    bar = None
                if len(drop_confident) == 1 and slot in ("E", "F"):
                    reason = "single drop: repeated first-drop slot by default"
                cues[slot] = _cue(name, bar,
                                  first_drop["confidence"] if slot in ("C", "D") else last_drop["confidence"],
                                  reason, cue_flags, grid)
        else:
            for slot, name in (("C", "-32 DROP"), ("D", "FIRST DROP"),
                               ("E", "-32 LAST DROP"), ("F", "LAST DROP")):
                cues[slot] = _cue(name, None, 0.0, "no confident drop detected",
                                  ["drop unavailable"], grid)
    else:
        chorus_end = first_chorus["end_bar"] if first_chorus else old_exit
        chorus_conf = first_chorus["confidence"] if first_chorus else 0.35
        cues["E"] = _cue("EXIT/JUMP", chorus_end, chorus_conf,
                         f"end of first chorus ({chorus_source})" if first_chorus else
                         "fallback to v5 EXIT; no chorus end was found",
                         [] if first_chorus else ["chorus end unavailable"], grid)
        last_chorus_start = last_chorus["start_bar"] if last_chorus else None
        cues["F"] = _cue("LAST CHORUS", last_chorus_start,
                         last_chorus["confidence"] if last_chorus else 0.0,
                         "start of the last chorus" if last_chorus else "last chorus unavailable",
                         [] if last_chorus else ["last chorus unavailable"], grid)
        exit_bar = chorus_end
        for slot, bars in (("C", 32), ("D", 16)):
            cue_bar = None if exit_bar is None else exit_bar - bars
            cue_flags = []
            if cue_bar is not None and cue_bar < start:
                cue_flags.append(f"{slot} lands before START")
                if not pop_layout:
                    flags.append(f"{slot} lands before START")
                cue_bar = None
            cues[slot] = _cue(f"EXIT -{bars}", cue_bar, chorus_conf,
                              f"{bars} bars before EXIT/JUMP", cue_flags, grid)

    cues["G"] = _cue("GO", None, 0.0, "resolved from cue map mix preferences", [], grid)
    outro_bars = max(0, bar_count - outro_start) if outro_start is not None else 0
    cues["H"] = _cue(f"OUTRO {outro_bars}", outro_start,
                     0.42 if fallback_outro else 0.65 if outro_start is not None else 0.0,
                     f"energy/vocal fade begins near the end; {outro_bars} bars remain" if fallback_outro else
                     f"detected outro start; {outro_bars} bars remain" if outro_start is not None else
                     "outro section unavailable",
                     ["outro inferred from energy/vocal fade"] if fallback_outro else
                     [] if outro_start is not None else ["outro unavailable"], grid)

    cues = {slot: cues[slot] for slot in "ABCDEFGH"}
    # Only anchor cues can vary freely; dependent labels remain derived from their anchors.
    if is_drop:
        anchors = {"B", "D", "F", "H"}
        if len(drop_confident) == 1:
            anchors.discard("F")
    else:
        anchors = {"B", "E", "F", "H"}
    for slot, cue in cues.items():
        bar = cue["bar"]
        cue["candidate_bars"] = [] if bar is None else sorted(
            {candidate for candidate in (bar - 2, bar - 1, bar, bar + 1, bar + 2)
             if first_bar <= candidate < bar_count}
        ) if slot in anchors else ([] if bar is None else [bar])

    cues["H"]["flags"] = list(dict.fromkeys(cues["H"]["flags"] + outro_vocal_flags))
    if vocal_free_bar is None and cues["H"]["bar"] is not None:
        cues["H"]["confidence"] = min(cues["H"]["confidence"], 0.4)
    result = {
        "track_id": track_id,
        "schema_version": 2,
        "engine": {"name": "sound-and-service-autocue", "rules_version": "v7"},
        "version": "v7",
        "map_type": map_type,
        "map_choice": {"mode": cue_maps["mode"], "reason": map_reason,
                       "vocal_ratio": None if vocal_ratio is None else round(vocal_ratio, 2)},
        "genre": genre,
        "genre_bucket": genre_bucket,
        "genre_phrase_bars": genre_phrase_bars,
        "outro_bars": outro_bars,
        "bar_count": bar_count,
        "mix_end_bar": time_to_bar(duration_s, grid),
        "outro_vocal_free_bar": vocal_free_bar,
        "outro_vocal_evidence": vocal_evidence,
        "no_vocal_free_outro": vocal_free_bar is not None and outro_start is None,
        "grid": {key: grid[key] for key in ("t1", "bar_s", "source", "fit", "provenance") if key in grid}
                | {"bpm": bpm}
                | {"offset_ms": grid.get("offset_ms", 0), "first_bar": first_bar},
        "drops": drops,
        "breakdowns": [{"bar": drop.get("breakdown_start_bar", drop["bar"] - 4), "confidence": drop["confidence"],
                        "reason": "low-end energy floor before detected return"} for drop in drops],
        "lyric_choruses": lyric_choruses,
        "chorus_spans": chorus_spans,
        "lyric_alignment": lyric_alignment,
        "cue_spec": "v6.1-pop-vocal" if pop_layout else "configurable",
        "phrasing_version": "v6.2",
        "start_anchor_bar": start,
        "start_onset": start_onset,
        "exit_evidence": exit_evidence,
        "out_detection": out_detection,
        "genre_reason": genre_reason,
        "flags": list(dict.fromkeys(flags)),
        "cue_order": list("ABCDEFGH"),
        "cues": cues,
        "switches": dict(config["switches"]),
        "gemini": {"used": False, "reason": "not run by the v6 builder; use the explicit opt-in tie-breaker"},
    }
    candidate_events = {
        "intro_start": {
            "bar": intro_start if intro_bars else None,
            "confidence": 0.7,
            "reason": cues["A"]["reason"],
            "flags": cues["A"]["flags"],
        },
        "start": cues["B"],
        "first_chorus_start": {
            "bar": first_chorus["start_bar"] if first_chorus else None,
            "confidence": first_chorus["confidence"] if first_chorus else 0.0,
            "reason": "start of first detected chorus",
        },
        "first_chorus_end": {
            "bar": first_chorus["end_bar"] if first_chorus else None,
            "confidence": first_chorus["confidence"] if first_chorus else 0.0,
            "reason": "end of first detected chorus",
        },
        "last_chorus_start": {
            "bar": last_chorus["start_bar"] if last_chorus else None,
            "confidence": last_chorus["confidence"] if last_chorus else 0.0,
            "reason": "start of last detected chorus",
        },
        "last_chorus_end": {
            "bar": last_chorus["end_bar"] if last_chorus else None,
            "confidence": last_chorus["confidence"] if last_chorus else 0.0,
            "reason": "end of last detected chorus",
        },
        "first_drop_start": {
            "bar": drop_confident[0]["bar"] if drop_confident else None,
            "confidence": drop_confident[0]["confidence"] if drop_confident else 0.0,
            "reason": drop_confident[0]["reason"] if drop_confident else "no confident first drop detected",
        },
        "last_drop_start": {
            "bar": drop_confident[-1]["bar"] if drop_confident else None,
            "confidence": drop_confident[-1]["confidence"] if drop_confident else 0.0,
            "reason": (("single detected drop: shared first/last event; " if len(drop_confident) == 1 else "")
                       + drop_confident[-1]["reason"]) if drop_confident else "no confident last drop detected",
            "flags": ["single drop: shares the first-drop event"] if len(drop_confident) == 1 else [],
        },
        "outro_start": cues["H"],
        "out": {
            "bar": out_detection.get("out_bar"),
            "confidence": out_detection.get("confidence", 0.0),
            "reason": out_detection.get("reason", ""),
            "flags": [] if out_detection.get("out_bar") is not None else ["OUT unavailable"],
        },
    }
    result = cue_map_settings.apply_cue_map(result, cue_maps, candidate_events, first_bar)
    if cue_maps["mode"] != "AUTO":
        result["flags"].append(f"cue map manually selected: {map_type}")
    return result


def _manifest_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def run(root, output_dir, manifest, lyrics_dir, words_dir, genre_path, config_path, switches=None):
    config = _load_json(config_path, DEFAULT_CONFIG)
    config["switches"] = {**DEFAULT_CONFIG["switches"], **config.get("switches", {})}
    if switches:
        config["switches"].update(switches)
    tags = _load_json(genre_path, {})
    os.makedirs(output_dir, exist_ok=True)
    results, failures = {}, []
    for row in _manifest_rows(manifest):
        track_id = (row.get("id") or row.get("n") or "").strip().zfill(2)
        if not track_id:
            raise ValueError("manifest contains a row with an empty track ID")
        if os.path.basename(track_id) != track_id or track_id in (".", ".."):
            raise ValueError(f"manifest track ID is not a safe file name: {track_id!r}")
        analysis_path = os.path.join(root, "cues", "allin1", track_id + ".json")
        plan_path = os.path.join(root, "cues_v2", "allin1", "v5_go32", track_id + ".json")
        feature_path = os.path.join(root, "features", track_id + ".npz")
        if not all(os.path.exists(path) for path in (analysis_path, plan_path, feature_path)):
            failures.append(f"{track_id}: missing cached analysis/plan/features")
            continue
        import numpy as np
        analysis, plan = _load_json(analysis_path), _load_json(plan_path)
        feature = np.load(feature_path)
        duration = float(feature["dur"])
        stem_path = os.path.join(root, "out", "allin1", track_id + ".stems_env.npz")
        stems, low_energy, hop = None, None, 0.05
        if os.path.exists(stem_path):
            env = np.load(stem_path)
            stems = {key: env[key].tolist() for key in ("drums", "bass", "vocals") if key in env}
            hop = float(env["hop_s"])
        low_energy = feature["lo"].tolist() if "lo" in feature else None
        words = _load_json(os.path.join(words_dir, track_id + ".json"))
        lyrics_record = _load_json(os.path.join(lyrics_dir, track_id + ".json"))
        genre = tags.get(track_id, "")
        if isinstance(genre, dict):
            genre = genre.get("genre", "")
        result = build_plan(track_id, analysis, plan, duration, genre, (words or {}).get("words"),
                            (lyrics_record or {}).get("syncedLyrics"), stems, low_energy, hop, config,
                            energy=feature["rms"].tolist(), fps=float(feature["fps"]))
        with open(os.path.join(output_dir, track_id + ".json"), "w", encoding="utf-8") as target:
            json.dump(result, target, ensure_ascii=False, indent=2)
        results[track_id] = result
    return results, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Local analysis root (never modifies source audio)")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lyrics-dir", required=True)
    parser.add_argument("--words-dir", required=True)
    parser.add_argument("--genre-tags", required=True)
    parser.add_argument("--config", default=os.path.join(TOOLS, "..", "config", "genre_params_v6.json"))
    parser.add_argument("--off", default="", help="Comma-separated switches to disable")
    parser.add_argument("--only", help="Enable only this switch")
    args = parser.parse_args()
    if args.only and args.only not in SWITCHES:
        parser.error("--only must name one of: " + ", ".join(SWITCHES))
    disabled = set(filter(None, args.off.split(",")))
    unknown = disabled.difference(SWITCHES)
    if unknown:
        parser.error("--off contains unknown switches: " + ", ".join(sorted(unknown)))
    switches = {name: name not in disabled for name in SWITCHES}
    if args.only:
        switches = {name: name == args.only for name in SWITCHES}
    results, failures = run(args.root, args.out, args.manifest, args.lyrics_dir, args.words_dir,
                            args.genre_tags, args.config, switches)
    print(f"built {len(results)} v6 plans")
    lyric_status = [result.get("lyric_alignment", {}).get("status", "not_available")
                    for result in results.values()]
    print("synced lyrics:", sum(status != "not_available" for status in lyric_status),
          "aligned:", lyric_status.count("aligned"), "rejected:", lyric_status.count("rejected"))
    print("maps: DROP", sum(result["map_type"] == "DROP" for result in results.values()),
          "VOCAL", sum(result["map_type"] == "VOCAL" for result in results.values()))
    for failure in failures:
        print("SKIP", failure, file=sys.stderr)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
