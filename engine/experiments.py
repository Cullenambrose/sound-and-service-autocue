"""Switchable algorithm experiments. Each one re-plans the same cached analysis and is stored beside the baseline.

An experiment prepares overrides for rules_v6.build_plan. It never edits the baseline plan, so the review page
can show every variant's cues side by side and the report can score them against the cues you grade.
"""
import copy
import math
import os
import shutil
import subprocess
from pathlib import Path
from timing import bar_to_time, grid_origin, time_to_bar

BASELINE = "baseline"

CATALOG = [
    {"id": "baseline", "label": "Rules-only v7", "default": True, "heavy": False,
     "summary": "The current engine, unchanged."},
    {"id": "phrase_changes", "label": "Phrase changes", "default": True, "heavy": False,
     "summary": "Adds drops wherever the full mix gets clearly louder at a phrase boundary, not only after a long breakdown."},
    {"id": "fuzzy_lyrics", "label": "Fuzzy lyric matching", "default": True, "heavy": False,
     "summary": "Matches mis-heard transcript words to lyrics by similarity, not exact spelling."},
    {"id": "kick_grid", "label": "Kick-snapped grid", "default": True, "heavy": False,
     "summary": "Shifts the bar grid onto the measured kick transients (20-200 Hz)."},
    {"id": "stem_energy", "label": "Stem energy sections", "default": True, "heavy": False,
     "summary": "Uses drums + bass + vocal stem energy for the energy-based chorus and outro fallbacks."},
    {"id": "roformer_stems", "label": "RoFormer vocals", "default": False, "heavy": True,
     "summary": "Separates vocals with a Mel-Band RoFormer model for cleaner vocal timing. Slow; needs audio-separator."},
]
IDS = [item["id"] for item in CATALOG]
ROFORMER_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
ROFORMER_TIMEOUT_S = int(os.environ.get("AUTOCUE_ROFORMER_TIMEOUT", "900"))


class Unavailable(RuntimeError):
    """The experiment cannot run on this song; its message says why and how to fix it."""


def default_enabled():
    return [item["id"] for item in CATALOG if item["default"]]


def normalize_enabled(value):
    if value is None:
        return default_enabled()
    if not isinstance(value, list) or any(item not in IDS for item in value):
        raise ValueError("experiments must be a list of known experiment ids")
    return [BASELINE] + [item for item in IDS if item in value and item != BASELINE]


def _resample(values, source_hop, target_hop, count=None):
    import numpy as np
    values = np.asarray(values, dtype=float)
    if not len(values):
        return values
    count = count or int(len(values) * source_hop / target_hop)
    source_times = np.arange(len(values)) * source_hop
    return np.interp(np.arange(count) * target_hop, source_times, values)


def _fuzzy_lyrics(context):
    if not context.get("synced_lyrics"):
        raise Unavailable("no synced lyrics were found for this song")
    if not context.get("words"):
        raise Unavailable("no WhisperX word timings; install whisperx")
    config = {**context["config"], "fuzzy_lyrics": True}
    return {"config": config}


def _kick_grid(context):
    if context["analysis"]["grid"].get("source") == "serato_beatgrid":
        raise Unavailable("imported Serato beatgrid retained; kick-grid experiment cannot replace it")
    import numpy as np
    low, fps = context.get("low_energy_raw"), float(context.get("fps") or 50)
    downbeats = context["analysis"].get("downbeats") or []
    if low is None or len(downbeats) < 8:
        raise Unavailable("needs low-band energy and at least 8 downbeats")
    low = np.asarray(low, dtype=float)
    flux = np.maximum(np.diff(np.log1p(low), prepend=0.0), 0.0)
    offsets = []
    radius = int(round(0.12 * fps))
    for beat in downbeats:
        centre = int(round(beat * fps))
        if centre - radius < 0 or centre + radius >= len(flux):
            continue
        window = flux[centre - radius:centre + radius + 1]
        if window.max() <= 0:
            continue
        offsets.append((int(window.argmax()) - radius) / fps)
    if len(offsets) < 8:
        raise Unavailable("too few clear kick transients near downbeats")
    shift = float(np.median(offsets))
    spread = float(np.median(np.abs(np.asarray(offsets) - shift)))
    if spread > 0.04:
        raise Unavailable(f"kick offsets are inconsistent (spread {spread * 1000:.0f} ms)")
    analysis = dict(context["analysis"])
    grid = dict(analysis["grid"])
    grid["t1"] = round(grid["t1"] + shift, 4)
    grid["kick_shift_ms"] = round(shift * 1000, 1)
    analysis["grid"] = grid
    return {"analysis": analysis, "note": f"grid shifted {shift * 1000:+.0f} ms"}


def _stem_energy(context):
    import numpy as np
    stems, hop = context.get("stems"), float(context.get("hop_s") or 0.05)
    fps = float(context.get("fps") or 50)
    if not stems or not all(key in stems for key in ("drums", "bass", "vocals")):
        raise Unavailable("Demucs stem energies are unavailable; install demucs")
    count = len(context["energy"])
    mixed = sum(_resample(stems[key], hop, 1 / fps, count) for key in ("drums", "bass", "vocals"))
    return {"energy": np.asarray(mixed, dtype=float).tolist()}


def detect_energy_jumps(rms, low, fps, grid, duration_s, first_bar=0, phrase_bars=8, minimum_jump=1.3):
    """Phrase-aligned small-to-big changes in the full mix (drops) from per-bar loudness and low end."""
    bar_s, t1 = float(grid["bar_s"]), grid_origin(grid)
    count = max(0, int((duration_s - t1) / bar_s))

    def bar_mean(values, bar):
        if values is None or t1 + bar * bar_s < 0:
            return None
        left = max(0, int((t1 + bar * bar_s) * fps))
        right = min(len(values), int((t1 + (bar + 1) * bar_s) * fps))
        return sum(values[left:right]) / (right - left) if right > left else None

    def level(bar):
        parts = [bar_mean(rms, bar), bar_mean(low, bar)]
        return parts if None not in parts else None

    jumps = []
    for bar in range(first_bar + phrase_bars, count - 2, phrase_bars):
        best = None
        for candidate in (bar - 1, bar, bar + 1):
            before = [level(b) for b in range(candidate - 4, candidate)]
            after = [level(b) for b in range(candidate, candidate + 2)]
            if None in before or None in after:
                continue
            ratios = []
            for index in range(2):
                low_before = sorted(row[index] for row in before)[len(before) // 2]
                high_after = sum(row[index] for row in after) / len(after)
                ratios.append(high_after / low_before if low_before > 0 else 0.0)
            score = 0.6 * ratios[0] + 0.4 * ratios[1]
            if best is None or score > best[0]:
                best = (score, ratios)
        if best and best[0] >= minimum_jump:
            jumps.append({
                "bar": bar, "time_s": round(t1 + bar * bar_s, 3),
                "confidence": round(min(0.95, 0.6 + (best[0] - minimum_jump) * 0.5), 2),
                "reason": f"loudness x{best[1][0]:.2f} and low end x{best[1][1]:.2f} over the previous 4 bars",
                "breakdown_start_bar": max(first_bar, bar - 4),
            })
    return jumps


def _first_bar_with_preroll(context):
    import rules_v6
    grid = context["analysis"]["grid"]
    return rules_v6._first_bar(grid, float(grid["bar_s"]), context.get("energy"), float(context.get("fps") or 50))


def _phrase_changes(context):
    rms, low = context.get("energy"), context.get("low_energy_raw")
    if not rms or not low:
        raise Unavailable("needs full-mix and low-band energy")
    grid = context["analysis"]["grid"]
    first_bar = _first_bar_with_preroll(context)
    jumps = detect_energy_jumps(rms, low, float(context.get("fps") or 50), grid, context["duration_s"], first_bar)
    if not jumps:
        raise Unavailable("no clear phrase-aligned energy jumps")
    merged = {drop["bar"]: drop for drop in context.get("detected_drops") or []}
    for jump in jumps:
        merged.setdefault(jump["bar"], jump)
    return {"detected_drops": sorted(merged.values(), key=lambda drop: drop["bar"]),
            "note": f"{len(jumps)} phrase-aligned energy jumps: bars " + ", ".join(str(j["bar"]) for j in jumps)}


def _separator_command():
    root = Path(__file__).resolve().parent.parent
    for candidate in (os.environ.get("AUTOCUE_SEPARATOR"), root / ".venv-separator" / "bin" / "audio-separator",
                      shutil.which("audio-separator")):
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise Unavailable("audio-separator is not installed. Run ./setup.sh --separator "
                      "(or: python3 -m venv .venv-separator && .venv-separator/bin/pip install audio-separator)")


def _roformer_timeout(context):
    # About 2x real time on Apple Silicon (MPS) at overlap 2; allow generous headroom.
    return max(ROFORMER_TIMEOUT_S, int(float(context.get("duration_s") or 0) * 5))


def _roformer(context):
    import numpy as np
    command = _separator_command()
    paths = context.get("paths")
    if paths is None or context.get("decoded_wav") is None:
        raise Unavailable("no decoded audio to separate")
    target = paths.root / "work" / "roformer_vocals.wav"
    if not target.exists():
        output = paths.root / "work" / "roformer"
        output.mkdir(parents=True, exist_ok=True)
        done = subprocess.run([command, str(context["decoded_wav"]), "--model_filename", ROFORMER_MODEL,
                               "--single_stem", "Vocals", "--output_format", "WAV", "--output_dir", str(output),
                               "--mdxc_overlap", "2", "--mdxc_batch_size", "4"],
                              capture_output=True, text=True, timeout=_roformer_timeout(context))
        found = sorted(output.glob("*Vocals*.wav"))
        if done.returncode or not found:
            raise Unavailable("RoFormer failed: " + (done.stderr or done.stdout or "no vocal file").strip()[-300:])
        found[0].replace(target)
        shutil.rmtree(output, ignore_errors=True)
    import analyze
    data, rate = analyze.read_mono(target)
    step = int(round(float(context.get("hop_s") or 0.05) * rate))
    count = len(data) // step
    envelope = np.sqrt((data[:count * step].reshape(count, step) ** 2).mean(1))
    stems = dict(context.get("stems") or {})
    stems["vocals"] = envelope.tolist()
    return {"stems": stems}


RUNNERS = {
    "phrase_changes": _phrase_changes,
    "fuzzy_lyrics": _fuzzy_lyrics,
    "kick_grid": _kick_grid,
    "stem_energy": _stem_energy,
    "roformer_stems": _roformer,
}


def summarize(plan, label, detail=""):
    grid = plan.get("grid", {})
    return {
        "label": label, "status": "ok", "detail": detail, "map_type": plan.get("map_type"),
        "t1": grid.get("t1"), "bar_s": grid.get("bar_s"),
        "offset_ms": grid.get("offset_ms", 0),
        "flags": plan.get("flags", []),
        "cues": {slot: {key: cue.get(key) for key in ("name", "bar", "time_s", "unsnapped", "confidence", "reason", "meaning")}
                 for slot, cue in plan.get("cues", {}).items()},
    }


def run_variants(base_kwargs, context, baseline_plan, enabled, build_plan):
    """Return {id: summary}. `base_kwargs` are the exact build_plan arguments used for the baseline."""
    rules_plan = (baseline_plan.get("gemini") or {}).get("baseline") or baseline_plan
    variants = {BASELINE: summarize(rules_plan, "Rules-only v7")}
    if baseline_plan.get("gemini", {}).get("used"):
        variants["gemini"] = summarize(baseline_plan, "Gemini-assisted v7")
    labels = {item["id"]: item["label"] for item in CATALOG}
    context = {**context, "detected_drops": baseline_plan.get("drops") or []}
    for variant in enabled:
        if variant == BASELINE or variant not in RUNNERS:
            continue
        try:
            overrides = dict(RUNNERS[variant](context))
            note = overrides.pop("note", "")
            kwargs = {**base_kwargs, **overrides}
            plan = build_plan(**kwargs)
            variants[variant] = summarize(plan, labels[variant], note)
        except Unavailable as reason:
            variants[variant] = {"label": labels[variant], "status": "unavailable", "detail": str(reason), "cues": {}}
        except Exception as error:
            variants[variant] = {"label": labels[variant], "status": "failed", "detail": str(error), "cues": {}}
    return variants


def bar_energy_table(stems, low_energy_raw, rms, hop_s, fps, grid, duration_s):
    """Per-bar energies saved with each song so later analysis does not need the audio again."""
    if not rms or grid["bar_s"] <= 0:
        return []
    bar_count = max(0, int(math.ceil(time_to_bar(duration_s, grid))))
    table = []

    def mean(values, step, bar):
        if not values:
            return None
        left = max(0, int(bar_to_time(bar, grid) / step))
        right = max(left + 1, int(bar_to_time(bar + 1, grid) / step))
        chunk = values[left:right]
        return round(float(sum(chunk) / len(chunk)), 5) if chunk else None

    for bar in range(max(0, int(math.ceil(time_to_bar(0, grid) - 1e-6))), bar_count):
        table.append({
            "bar": bar,
            "rms": mean(rms, 1 / fps, bar),
            "low": mean(low_energy_raw, 1 / fps, bar),
            "drums": mean((stems or {}).get("drums"), hop_s, bar),
            "bass": mean((stems or {}).get("bass"), hop_s, bar),
            "vocals": mean((stems or {}).get("vocals"), hop_s, bar),
        })
    return table


def _truth_time(cue, grid):
    if cue.get("bar") is None or grid.get("t1") is None:
        return None
    if cue.get("unsnapped"):
        time_s = cue.get("time_s")
        if type(time_s) not in (int, float) or not math.isfinite(time_s) or time_s < 0:
            raise ValueError("reviewed un-snapped timestamp is invalid")
        return time_s
    return bar_to_time(cue["bar"], grid)


def review_answer(plan, slots=None):
    selected = slots if slots is not None else plan.get("reviewed_slots", list(plan.get("cues", {})))
    return {
        "source": "human_review", "map_type": plan.get("map_type"),
        "grid": copy.deepcopy(plan.get("grid", {})),
        "cues": {slot: {**{key: cue.get(key) for key in ("bar", "name", "meaning", "time_s", "unsnapped")},
                        "map_type": plan.get("map_type")}
                 for slot, cue in plan.get("cues", {}).items() if slot in selected},
    }


def merge_review_answer(plan, previous, slots):
    answer = review_answer(plan, slots)
    for slot, cue in (previous or {}).get("cues", {}).items():
        if slot in answer["cues"]:
            continue
        retained = copy.deepcopy(cue)
        if retained.get("bar") is not None:
            retained["bar"] = time_to_bar(bar_to_time(retained["bar"], previous["grid"]), plan["grid"])
        retained.setdefault("map_type", previous.get("map_type"))
        answer["cues"][slot] = retained
    return answer


def scoring_variants(plan, *, allow_current=True):
    variants = copy.deepcopy(plan.get("variants") or {})
    gemini = plan.get("gemini") or {}
    if gemini.get("used"):
        if gemini.get("baseline"):
            variants[BASELINE] = summarize(gemini["baseline"], "Rules-only v7")
        else:
            # Old assisted baselines cannot be relabelled as unassisted evidence.
            variants[BASELINE] = {"status": "unavailable", "label": "Rules-only v7", "cues": {},
                                  "detail": "Historical assisted plan has no independent rules-only snapshot."}
        variants.setdefault("gemini", summarize(plan, "Gemini-assisted v7") if allow_current else {
            "status": "unavailable", "label": "Gemini-assisted v7", "cues": {},
            "detail": "Historical reviewed plan has no saved pre-review assisted prediction."})
    elif BASELINE not in variants:
        variants[BASELINE] = summarize(plan, "Rules-only v7") if allow_current else {
            "status": "unavailable", "label": "Rules-only v7", "cues": {},
            "detail": "Historical reviewed plan has no saved pre-review automatic prediction."}
    return variants


def _variant_time(cue, variant, fallback_grid):
    if cue.get("bar") is None:
        if cue.get("time_s") is not None:
            raise ValueError("empty prediction has a timestamp")
        return None
    if type(cue["bar"]) not in (int, float) or not math.isfinite(cue["bar"]):
        raise ValueError("prediction position is not finite")
    time_s = cue.get("time_s")
    if time_s is not None:
        if type(time_s) not in (int, float) or not math.isfinite(time_s):
            raise ValueError("prediction timestamp is not finite")
        return time_s
    grid = variant.get("grid") or {
        key: variant.get(key, fallback_grid.get(key)) for key in ("t1", "bar_s")}
    if not variant.get("grid"):
        grid["offset_ms"] = variant.get("offset_ms", fallback_grid.get("offset_ms", 0))
    return bar_to_time(cue["bar"], grid)


def score_tracks(tracks):
    """Score frozen predictions against explicit human targets; omitted targets remain unknown."""
    results, per_track, skipped, unavailable, map_comparison = {}, [], [], [], {}
    cost = {"requests": 0, "cache_hits": 0, "new_cost_usd": 0.0, "unknown_cost_tracks": 0,
            "scope": "Saved analyzed runs on scored tracks, not a provider billing ledger."}
    for track in tracks:
        plan = track["plan"]
        answer = plan.get("answer_key")
        if not answer and track.get("reviewed"):
            answer = review_answer(plan)
        if not answer:
            skipped.append({"hash": track.get("hash"), "name": track["name"], "reason": "no human-reviewed answer key"})
            continue
        grid = plan.get("grid", {})
        key_grid = answer.get("grid") or grid
        variants = plan.get("variants") or {}
        try:
            beat = (bar_to_time(1, key_grid) - bar_to_time(0, key_grid)) / 4
        except ValueError as error:
            skipped.append({"hash": track.get("hash"), "name": track["name"], "reason": str(error)})
            continue
        if not variants:
            skipped.append({"hash": track.get("hash"), "name": track["name"], "reason": "no saved automatic predictions"})
            continue
        unavailable.extend({"hash": track.get("hash"), "name": track["name"], "variant": key,
                            "reason": variant.get("detail") or variant.get("status", "unknown status")}
                           for key, variant in variants.items() if variant.get("status") != "ok")
        key_map = answer.get("map_type") or plan.get("map_type")
        row = {"hash": track.get("hash"), "name": track["name"], "map_type": key_map, "slots": {}}
        for slot in "ABCDEFGH":
            if slot not in answer.get("cues", {}):
                continue
            target = answer["cues"][slot]
            if "bar" not in target:
                row["slots"][slot] = {"error": "review target has no explicit position/empty decision", "variants": {}}
                continue
            try:
                truth = _truth_time(target, key_grid)
            except ValueError as error:
                row["slots"][slot] = {"error": "invalid review target: " + str(error), "variants": {}}
                continue
            row["slots"][slot] = {"truth": truth, "variants": {}}
            for key, variant in variants.items():
                if variant.get("status") != "ok":
                    continue
                cue = variant.get("cues", {}).get(slot)
                meaning = (cue or {}).get("meaning")
                target_map = target.get("map_type") or key_map
                incompatible = (bool(target.get("meaning") and meaning and target["meaning"] != meaning)
                                or (not (target.get("meaning") and meaning) and slot in "CDEF"
                                    and variant.get("map_type") is not None and target_map is not None
                                    and variant["map_type"] != target_map))
                if incompatible:
                    row["slots"][slot]["variants"][key] = {
                        "time_s": None, "right": None, "failure": "incompatible cue meaning/map"}
                    continue
                failure = None
                time_s = None
                try:
                    if cue is None:
                        raise ValueError("automatic prediction missing this slot")
                    time_s = _variant_time(cue, variant, grid)
                except ValueError as error:
                    failure = str(error)
                error_s = abs(time_s - truth) if time_s is not None and truth is not None else None
                right = not failure and ((time_s is None and truth is None) or (
                    error_s is not None and error_s <= beat + 1e-6))
                if not right and not failure:
                    failure = ("false positive" if truth is None else "missing cue" if time_s is None else "timing error")
                row["slots"][slot]["variants"][key] = {
                    "time_s": time_s, "right": right, "error_s": error_s, "failure": failure}
                entry = results.setdefault(key, {"label": variant.get("label", key), "right": 0, "total": 0,
                                                 "positioned_right": 0, "positioned_total": 0,
                                                 "empty_right": 0, "empty_total": 0,
                                                 "within_bar": 0, "missing": 0, "false_positives": 0,
                                                 "by_slot": {}, "by_map": {}, "wins": 0, "losses": 0,
                                                 "timing_errors_s": []})
                entry["total"] += 1
                entry["right"] += int(right)
                entry["positioned_total" if truth is not None else "empty_total"] += 1
                entry["positioned_right" if truth is not None else "empty_right"] += int(right)
                entry["missing"] += int(truth is not None and time_s is None)
                entry["false_positives"] += int(truth is None and time_s is not None)
                entry["within_bar"] += int(error_s is not None and error_s <= beat * 4 + 1e-6)
                if error_s is not None:
                    entry["timing_errors_s"].append(error_s)
                slot_entry = entry["by_slot"].setdefault(slot, {"right": 0, "total": 0})
                slot_entry["total"] += 1
                slot_entry["right"] += int(right)
                map_entry = entry["by_map"].setdefault(str(target_map), {
                    "right": 0, "total": 0, "positioned_right": 0, "positioned_total": 0})
                map_entry["total"] += 1
                map_entry["right"] += int(right)
                if truth is not None:
                    map_entry["positioned_total"] += 1
                    map_entry["positioned_right"] += int(right)
            base = row["slots"][slot]["variants"].get(BASELINE)
            for key, value in row["slots"][slot]["variants"].items():
                if (base and base["right"] is not None and value["right"] is not None
                        and key != BASELINE and value["right"] != base["right"]):
                    results[key]["wins" if value["right"] else "losses"] += 1
        if any(slot.get("variants") for slot in row["slots"].values()):
            per_track.append(row)
            if key_map in ("VOCAL", "DROP"):
                for key, variant in variants.items():
                    if variant.get("status") == "ok" and variant.get("map_type") in ("VOCAL", "DROP"):
                        comparison = map_comparison.setdefault(key, {"right": 0, "total": 0})
                        comparison["total"] += 1
                        comparison["right"] += int(variant["map_type"] == key_map)
            gemini = plan.get("gemini") or {}
            if gemini.get("used"):
                cost["requests"] += int(not gemini.get("cached", False))
                cost["cache_hits"] += int(gemini.get("cached", False))
                new_cost = gemini.get("new_cost_usd")
                if type(new_cost) in (int, float) and math.isfinite(new_cost) and new_cost >= 0:
                    cost["new_cost_usd"] += new_cost
                else:
                    cost["unknown_cost_tracks"] += 1
        else:
            skipped.append({"hash": track.get("hash"), "name": track["name"],
                            "reason": "no valid reviewed targets with saved predictions",
                            "cue_errors": {slot: details["error"] for slot, details in row["slots"].items()
                                           if details.get("error")}})
    for entry in results.values():
        entry["accuracy"] = round(entry["right"] / entry["total"], 3) if entry["total"] else None
        entry["positioned_accuracy"] = (round(entry["positioned_right"] / entry["positioned_total"], 3)
                                        if entry["positioned_total"] else None)
        errors = sorted(entry.pop("timing_errors_s"))
        entry["mean_error_s"] = round(sum(errors) / len(errors), 4) if errors else None
        entry["median_error_s"] = round((errors[(len(errors) - 1) // 2] + errors[len(errors) // 2]) / 2, 4) if errors else None
    cost["new_cost_usd"] = round(cost["new_cost_usd"], 6)
    return {"schema_version": 2, "variants": results, "tracks": per_track,
            "graded_songs": len(per_track), "skipped": skipped, "gemini_cost": cost,
            "unavailable_predictions": unavailable,
            "map_comparison": map_comparison,
            "policy": "Human review only; omitted targets unknown; explicit empty targets scored separately; incompatible meanings excluded."}
