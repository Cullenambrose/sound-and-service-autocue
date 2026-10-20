"""Switchable v6 cue maps built from cached v5 analysis, local transcripts, and optional stems."""
import argparse
import csv
import json
import math
import os
import sys

from synced_lyrics import align_synced_lyrics
import cue_maps as cue_map_settings

TOOLS = os.path.dirname(os.path.abspath(__file__))
SWITCHES = (
    "synced_lyrics",
    "drop_detection",
    "genre_map",
    "gemini_tiebreaker",
)
DEFAULT_CONFIG = {
    "switches": {
        "synced_lyrics": True,
        "drop_detection": True,
        "genre_map": True,
        "gemini_tiebreaker": False,
    },
    "drop_genres": ["house", "edm", "dance", "electronic", "techno", "trance", "disco"],
    "drop_bpm_range": [118, 150],
    "drop_min_confidence": 0.65,
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
        start = int(round((float(section.get("start", section.get("start_s", 0))) - grid["t1"]) / grid["bar_s"]))
    if section.get("j1") is not None:
        end = int(round(section["j1"]))
    else:
        end = int(round((float(section.get("end", section.get("end_s", 0))) - grid["t1"]) / grid["bar_s"]))
    return start, end


def _bar_time(bar, grid):
    return round(grid["t1"] + bar * grid["bar_s"] - grid.get("offset_ms", 0) / 1000, 3)


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
    start = max(0, int((grid["t1"] + bar * grid["bar_s"]) / hop_s))
    end = max(start, int((grid["t1"] + (bar + count) * grid["bar_s"]) / hop_s))
    chunk = values[start:end]
    return sum(chunk) / len(chunk) if chunk else None


def detect_drops(stems, low_energy, hop_s, grid, duration_s, phrase_bars=8, recovery_ratio=1.8):
    """Find downbeat-aligned low-end returns after a sustained breakdown."""
    drums, bass = stems.get("drums"), stems.get("bass")
    if not drums or not bass or not low_energy:
        return []
    bar_count = max(0, int(math.ceil((duration_s - grid["t1"]) / grid["bar_s"])))
    first = max(0, int(math.ceil(-grid["t1"] / grid["bar_s"] - 1e-6)))
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
        if baseline > reference * 0.65 or low_baseline > low_reference * 0.65:
            continue
        ratio = current / baseline
        low_ratio = low / low_baseline
        if ratio < recovery_ratio or low_ratio < 1.25:
            continue
        confidence = min(0.95, 0.55 + min(ratio - recovery_ratio, 1.0) * 0.15
                         + min(low_ratio - 1.25, 1.0) * 0.1)
        breakdown_start = bar - 4
        for previous in range(max(first, bar - lookback), bar - 3):
            value = by_bar.get(previous)
            if value and value[0] <= reference * 0.65 and value[1] <= low_reference * 0.65:
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
    return kept


def _energy_choruses(energy, fps, grid, start_bar, end_bar, duration_s):
    if not energy or fps <= 0:
        return []
    bar_count = max(0, int(math.ceil((duration_s - grid["t1"]) / grid["bar_s"])))
    means = {}
    for bar in range(start_bar + 4, bar_count):
        first = max(0, int((grid["t1"] + bar * grid["bar_s"]) * fps))
        last = min(len(energy), int((grid["t1"] + (bar + 1) * grid["bar_s"]) * fps))
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
            first = int(round((span["start_s"] - grid["t1"]) / grid["bar_s"]))
            last = int(round((span["end_s"] - grid["t1"]) / grid["bar_s"]))
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
    bar_count = max(0, int(math.ceil((duration_s - grid["t1"]) / grid["bar_s"])))
    first_candidate = max(start_bar, bar_count - 32)
    means = {}
    for bar in range(first_candidate, bar_count):
        left = max(0, int((grid["t1"] + bar * grid["bar_s"]) * fps))
        right = min(len(energy), int((grid["t1"] + (bar + 1) * grid["bar_s"]) * fps))
        if right > left:
            values = energy[left:right]
            means[bar] = math.sqrt(sum(float(value) ** 2 for value in values) / len(values))
    vocal_means = {}
    if vocal_energy is not None and len(vocal_energy) and vocal_hop_s > 0:
        for bar in range(first_candidate, bar_count):
            left = max(0, int((grid["t1"] + bar * grid["bar_s"]) / vocal_hop_s))
            right = min(len(vocal_energy), int((grid["t1"] + (bar + 1) * grid["bar_s"]) / vocal_hop_s))
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
    if any(token in lowered for token in ("house", "edm", "dance", "electronic", "techno", "trance", "disco")):
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
    first = int(math.ceil(-grid["t1"] / bar_s - 1e-6))
    if first >= 0 or energy is None or len(energy) == 0 or fps <= 0:
        return 0
    typical = sorted(energy)[len(energy) // 2]
    while first < 0:
        start = max(0, int((grid["t1"] + first * bar_s) * fps))
        chunk = energy[start:int((grid["t1"] + (first + 1) * bar_s) * fps)]
        if chunk and typical > 0 and sum(chunk) / len(chunk) >= 0.5 * typical:
            break
        first += 1
    return first


def _snap_intro(bar, first_bar, bar_count):
    """Dance intros are 16 or 32 bars: snap a detected entry to whichever is closer."""
    length = 32 if bar - first_bar > 24 else 16
    return min(first_bar + length, max(first_bar, bar_count - 2))


def _electronic_start(energy, fps, vocals, hop_s, grid, first_bar, bar_count, minimum=16, maximum=32):
    """START for dance music: the first clear energy or vocal entry 16-32 bars into the track, else bar 32."""
    if not energy or fps <= 0:
        return None
    def mean(values, step, bar, count=1):
        return _bar_mean(values, step, grid, bar, count) if values else None
    for bar in range(first_bar + minimum, first_bar + maximum + 1):
        if bar + 2 > bar_count:
            break
        before, after = mean(energy, 1 / fps, bar - 4, 4), mean(energy, 1 / fps, bar, 2)
        if before and after and after >= 1.25 * before:
            return _snap_intro(bar, first_bar, bar_count), "energy rises after the intro"
        v_before, v_after = mean(vocals, hop_s, bar - 4, 4), mean(vocals, hop_s, bar, 2)
        if v_before is not None and v_after is not None and v_before < 0.02 and v_after >= 0.04:
            return _snap_intro(bar, first_bar, bar_count), "vocals enter after the intro"
    fallback = first_bar + maximum
    return (fallback, "no clear entry; dance intros are usually 32 bars") if fallback + 2 <= bar_count else None


def build_plan(track_id, analysis, plan, duration_s, genre="", words=None, synced_lyrics=None,
               stems=None, low_energy=None, hop_s=0.05, config=None, detected_drops=None,
               energy=None, fps=50, cue_maps=None):
    config = {**DEFAULT_CONFIG, **(config or {})}
    config["switches"] = {**DEFAULT_CONFIG["switches"], **config.get("switches", {})}
    grid = analysis["grid"]
    bar_s = float(grid["bar_s"])
    first_bar = _first_bar(grid, bar_s, energy, fps)
    bar_count = max(first_bar, int(math.ceil((duration_s - grid["t1"]) / bar_s)))
    old_cues = plan.get("cues", {})
    start = old_cues.get("B", {}).get("bar")
    if start is None:
        intro_ends = [_section_bars(section, grid)[1] for section in analysis.get("segments", [])
                      if "intro" in _normal_label(section)]
        start = intro_ends[0] if intro_ends else first_bar
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
        drops = detect_drops(stems, low_energy, hop_s, grid, duration_s,
                             config["drop_phrase_bars"], config["drop_recovery_ratio"])
    drop_confident = [drop for drop in drops if drop["confidence"] >= config["drop_min_confidence"]]
    genre_matches, genre_reason = _genre_is_drop(genre, bpm, config)
    cue_maps = cue_map_settings.normalize_cue_maps(cue_maps)
    start_reason = "retained v5 START on the existing downbeat grid"
    tagged_dance = any(token in (genre or "").casefold() for token in config["drop_genres"])
    if config["switches"]["genre_map"] and (tagged_dance or genre_bucket == "house_edm"):
        found = _electronic_start(energy, fps, (stems or {}).get("vocals"), hop_s, grid, first_bar, bar_count)
        if found and start - first_bar < 16:
            start, start_reason = found[0], "dance-music intro of at least 16 bars: " + found[1]
    is_drop = bool(drop_confident and (genre_matches if config["switches"]["genre_map"] else True))
    if cue_maps["mode"] == "DROP":
        is_drop = True
    elif cue_maps["mode"] == "VOCAL":
        is_drop = False
    map_type = "DROP" if is_drop else "VOCAL"
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
    lyric_choruses = []
    if lyric_alignment and lyric_alignment.get("status") == "aligned":
        for span in lyric_alignment.get("choruses", []):
            lyric_choruses.append({
                "start_bar": _snap_phrase(int(round((span["start_s"] - grid["t1"]) / bar_s)), start),
                "end_bar": _snap_phrase(int(round((span["end_s"] - grid["t1"]) / bar_s)), start),
                "confidence": span["confidence"],
                "source": "aligned_synced_lyrics",
            })
    chorus_spans, chorus_source = _choruses(analysis, grid, lyric_alignment, start, old_exit,
                                            energy, fps, duration_s)
    flags = []
    if lyric_alignment and lyric_alignment["status"] == "rejected":
        flags.append("synced lyric alignment rejected: " + lyric_alignment["reason"])
    if not config["switches"]["genre_map"]:
        flags.append("genre map switch disabled; confident drops select DROP map")
    if not genre:
        flags.append("genre tag unavailable; used BPM and default phrase settings")
    if is_drop and not genre_matches:
        flags.append("DROP selected from confident audio drop only")
    if len(drop_confident) == 1:
        flags.append("single drop: last-drop slots repeat first-drop slots")
    if not chorus_spans and not is_drop:
        flags.append("no chorus span found from lyrics or section labels")
    if fallback_outro:
        flags.append("outro inferred from sustained end-of-track energy/vocal fade; review recommended")

    cues = {}
    intro_start = first_bar
    for section in analysis.get("segments", analysis.get("sections", [])):
        if "intro" in _normal_label(section):
            intro_start, _ = _section_bars(section, grid)
            intro_start = first_bar if intro_start <= 0 else max(first_bar, intro_start)
            break
    intro_bars = max(0, start - intro_start)
    first_chorus = chorus_spans[0] if chorus_spans else None
    last_chorus = chorus_spans[-1] if chorus_spans else None
    intro_name = f"INTRO -{intro_bars}"
    cues["A"] = _cue(intro_name, intro_start if intro_bars else None, 0.7,
                     f"intro begins {intro_bars} bars before START" if intro_bars else "no measured intro before START",
                     [] if intro_bars else ["intro shorter than one bar"], grid)
    cues["B"] = _cue("START", start, old_cues.get("B", {}).get("confidence", 0.6),
                     start_reason, grid=grid)

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
                flags.append(f"{slot} lands before START")
                cue_bar = None
            cues[slot] = _cue(f"EXIT -{bars}", cue_bar, chorus_conf,
                              f"{bars} bars before EXIT/JUMP", cue_flags, grid)

    go = None if outro_start is None else outro_start - 32
    go_flags = []
    if go is not None and go < first_bar:
        go_flags.append("GO -32 falls before the track's first downbeat")
        flags.append("GO -32 falls before the track's first downbeat")
        go = None
    if fallback_outro:
        go_flags.append("outro inferred from energy/vocal fade")
    cues["G"] = _cue("GO -32", go, 0.38 if fallback_outro and go is not None else
                     0.6 if go is not None else 0.0,
                     "32 bars before the inferred energy/vocal fade" if fallback_outro else
                     "32 bars before the detected outro" if outro_start is not None else "outro section unavailable",
                     go_flags, grid)
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

    result = {
        "track_id": track_id,
        "schema_version": 2,
        "engine": {"name": "sound-and-service-autocue", "rules_version": "v6"},
        "version": "v6",
        "map_type": map_type,
        "genre": genre,
        "genre_bucket": genre_bucket,
        "genre_phrase_bars": genre_phrase_bars,
        "outro_bars": outro_bars,
        "bar_count": bar_count,
        "grid": {key: grid[key] for key in ("t1", "bar_s") if key in grid}
                | {"offset_ms": grid.get("offset_ms", 0), "first_bar": first_bar},
        "drops": drops,
        "breakdowns": [{"bar": drop.get("breakdown_start_bar", drop["bar"] - 4), "confidence": drop["confidence"],
                        "reason": "low-end energy floor before detected return"} for drop in drops],
        "lyric_choruses": lyric_choruses,
        "chorus_spans": chorus_spans,
        "lyric_alignment": lyric_alignment,
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
            "reason": drop_confident[-1]["reason"] if drop_confident else "no confident last drop detected",
        },
        "outro_start": cues["H"],
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
