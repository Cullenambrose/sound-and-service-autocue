"""Switchable v6 cue maps built from cached v5 analysis, local transcripts, and optional stems."""
import argparse
import csv
import json
import math
import os
import re
import sys

from synced_lyrics import align_synced_lyrics

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


def build_plan(track_id, analysis, plan, duration_s, genre="", words=None, synced_lyrics=None,
               stems=None, low_energy=None, hop_s=0.05, config=None, detected_drops=None,
               energy=None, fps=50):
    config = {**DEFAULT_CONFIG, **(config or {})}
    config["switches"] = {**DEFAULT_CONFIG["switches"], **config.get("switches", {})}
    grid = analysis["grid"]
    bar_s = float(grid["bar_s"])
    first_bar = max(0, int(math.ceil(-grid["t1"] / bar_s - 1e-6)))
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
            lyric_alignment = align_synced_lyrics(synced_lyrics, words, bar_s)
        else:
            lyric_alignment = {"status": "rejected", "reason": "WhisperX word timings are unavailable",
                               "choruses": [], "matched_lines": 0}
    elif synced_lyrics:
        lyric_alignment = {"status": "not_run", "reason": "synced lyrics switch is off",
                           "choruses": [], "matched_lines": 0}

    drops = list(detected_drops or [])
    if not drops and config["switches"]["drop_detection"] and stems and low_energy:
        drops = detect_drops(stems, low_energy, hop_s, grid, duration_s,
                             genre_phrase_bars, config["drop_recovery_ratio"])
    drop_confident = [drop for drop in drops if drop["confidence"] >= config["drop_min_confidence"]]
    genre_matches, genre_reason = _genre_is_drop(genre, bpm, config)
    is_drop = bool(drop_confident and (genre_matches if config["switches"]["genre_map"] else True))
    map_type = "DROP" if is_drop else "VOCAL"
    outro = next((section for section in analysis.get("segments", analysis.get("sections", []))
                  if "outro" in _normal_label(section)), None)
    if outro is not None:
        outro_start, _ = _section_bars(outro, grid)
        outro_start = max(first_bar, outro_start)
    else:
        outro_start = None
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

    cues = {}
    intro_start = first_bar
    for section in analysis.get("segments", analysis.get("sections", [])):
        if "intro" in _normal_label(section):
            intro_start, _ = _section_bars(section, grid)
            intro_start = max(first_bar, intro_start)
            break
    intro_bars = max(0, start - intro_start)
    intro_name = f"INTRO -{intro_bars}BS"
    cues["A"] = _cue(intro_name, intro_start if intro_bars else None, 0.7,
                     f"intro begins {intro_bars} bars before START" if intro_bars else "no measured intro before START",
                     [] if intro_bars else ["intro shorter than one bar"], grid)
    cues["B"] = _cue("START", start, old_cues.get("B", {}).get("confidence", 0.6),
                     "retained v5 START on the existing downbeat grid", grid=grid)

    if is_drop:
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
            cues[slot] = _cue(name, bar, first_drop["confidence"] if slot in ("C", "D") else last_drop["confidence"],
                              reason, cue_flags, grid)
    else:
        first_chorus = chorus_spans[0] if chorus_spans else None
        last_chorus = chorus_spans[-1] if chorus_spans else None
        chorus_end = first_chorus["end_bar"] if first_chorus else old_exit
        chorus_conf = first_chorus["confidence"] if first_chorus else 0.35
        cues["E"] = _cue("EXIT/JUMP", chorus_end, chorus_conf,
                         f"end of first chorus ({chorus_source})" if first_chorus else
                         "fallback to v5 EXIT; no chorus end was found",
                         [] if first_chorus else ["chorus end unavailable"], grid)
        jump_in = last_chorus["start_bar"] - 1 if last_chorus else None
        cues["F"] = _cue("JUMP IN", jump_in, last_chorus["confidence"] if last_chorus else 0.0,
                         "one bar before the last chorus" if last_chorus else "last chorus unavailable",
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
    cues["G"] = _cue("GO -32", go, 0.6 if go is not None else 0.0,
                     "32 bars before the detected outro" if outro_start is not None else "outro section unavailable",
                     go_flags, grid)
    outro_bars = max(0, bar_count - outro_start) if outro_start is not None else 0
    cues["H"] = _cue(f"OUTRO {outro_bars}", outro_start, 0.65 if outro_start is not None else 0.0,
                     f"detected outro start; {outro_bars} bars remain" if outro_start is not None else "outro section unavailable",
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

    return {
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
            stems = {key: env[key].tolist() for key in ("drums", "bass") if key in env}
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
