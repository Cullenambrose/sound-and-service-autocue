"""Optional, cached low-confidence v6 tie-breaker. Sends cue candidates and timings, never audio."""
import argparse
import copy
import json
import os
from cue_maps import MEANINGS, apply_cue_map, intro_candidate, refresh_dynamic_labels
from timing import bar_to_time

MODEL = "gemini-3.1-pro-preview"
PRICE_INPUT_PER_MILLION = 2.0
PRICE_OUTPUT_PER_MILLION = 12.0
MAX_OUTPUT_TOKENS = 500
DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "genre_params_v6.json")


def _apply_mapped(plan, answer, confidence_threshold):
    trial = copy.deepcopy(plan)
    mapping = trial["cue_map"]
    events = mapping["candidates"]
    grid = trial["grid"]
    first_bar = grid.get("first_bar", 0)
    changes, picks = {}, {}

    def reject(reason):
        plan.setdefault("flags", []).append("Gemini cue picks rejected: " + reason)
        return {}

    for slot, response in answer.items():
        cue = trial["cues"].get(slot)
        spec = mapping["resolved"].get(slot)
        if cue is None or spec is None or not isinstance(response, dict):
            continue
        meaning = MEANINGS[spec["meaning"]]
        if (meaning["source"] == "intro_start" or meaning["offset"] != 0
                or not spec["enabled"] or spec.get("end_fallback")
                or len(cue.get("candidate_bars", [])) < 2
                or (cue.get("confidence") or 0) >= min(0.85, confidence_threshold)):
            continue
        pick = response.get("pick")
        if type(pick) is not int or pick not in cue["candidate_bars"]:
            continue
        source = meaning["source"]
        if source in changes and changes[source] != pick:
            return reject("conflicting selections for " + source)
        changes[source] = pick
        picks[slot] = {"bar": pick, "why": str(response.get("why") or "")[:300]}
    if not picks:
        return {}

    # One detected event may supply both first/last pads.
    for first, last in (("first_drop_start", "last_drop_start"),
                        ("first_chorus_start", "last_chorus_start"),
                        ("first_chorus_end", "last_chorus_end")):
        if events.get(first, {}).get("bar") == events.get(last, {}).get("bar"):
            if first in changes and last in changes and changes[first] != changes[last]:
                return reject("conflicting selections for a shared first/last event")
            if first in changes:
                changes[last] = changes[first]
            elif last in changes:
                changes[first] = changes[last]

    for source, bar in changes.items():
        if source not in events or not first_bar <= bar < trial["bar_count"]:
            return reject("event outside the track")
        if (source == "outro_start" and trial.get("outro_vocal_free_bar") is not None
                and bar < trial["outro_vocal_free_bar"]):
            return reject("outro would precede the last detected vocal")
        events[source]["bar"] = bar
        events[source]["reason"] = (events[source].get("reason", "")
                                     + "; Gemini low-confidence tie-breaker")
        events[source]["flags"] = list(events[source].get("flags") or []) + [
            "Gemini suggestion; verify in review"]

    for first, last in (("first_drop_start", "last_drop_start"),
                        ("first_chorus_start", "last_chorus_start"),
                        ("first_chorus_end", "last_chorus_end"),
                        ("first_chorus_start", "first_chorus_end"),
                        ("last_chorus_start", "last_chorus_end")):
        left, right = events.get(first, {}).get("bar"), events.get(last, {}).get("bar")
        if (first in changes or last in changes) and left is not None and right is not None:
            if left > right or (left == right and first.endswith("_start") and last.endswith("_end")):
                return reject(first + " must precede " + last)

    for source in ("first_drop_start", "last_drop_start"):
        if source not in changes:
            continue
        original = plan["cue_map"]["candidates"][source]["bar"]
        for old_drop, drop in zip(plan.get("drops", []), trial.get("drops", [])):
            if old_drop["bar"] == original:
                drop["bar"] = changes[source]
                drop["time_s"] = round(bar_to_time(drop["bar"], grid), 3)
    for source, index, field in (("first_chorus_start", 0, "start_bar"),
                                 ("first_chorus_end", 0, "end_bar"),
                                 ("last_chorus_start", -1, "start_bar"),
                                 ("last_chorus_end", -1, "end_bar")):
        if source in changes and trial.get("chorus_spans"):
            trial["chorus_spans"][index][field] = changes[source]
    if "outro_start" in changes:
        trial["outro_bars"] = max(0, trial["bar_count"] - changes["outro_start"])

    settings = {"version": mapping["version"], "mode": mapping["mode"],
                "mix_lengths": mapping.get("mix_lengths", {}),
                "end_of_song_fallback": mapping.get("end_of_song_fallback", True),
                "maps": {trial["map_type"]: mapping["settings"]}}
    try:
        apply_cue_map(trial, settings, events, first_bar)
    except ValueError as error:
        return reject(str(error))
    if any(trial["cues"][slot]["bar"] != pick["bar"] for slot, pick in picks.items()):
        return reject("selection conflicts with cue-map constraints")
    for slot, pick in picks.items():
        if pick["why"]:
            trial["cues"][slot]["reason"] += ": " + pick["why"]
    plan.update(trial)
    return picks


def apply(plan, answer, confidence_threshold=0.6):
    if plan.get("cue_map"):
        return _apply_mapped(plan, answer, confidence_threshold)
    picks = {}
    for slot, response in answer.items():
        if slot not in plan["cues"] or not isinstance(response, dict):
            continue
        cue = plan["cues"][slot]
        if cue.get("meaning") == "intro_start" or (slot == "A" and not plan.get("cue_map")):
            continue
        if len(cue.get("candidate_bars", [])) < 2:
            continue
        pick = response.get("pick")
        if type(pick) is not int or pick not in cue.get("candidate_bars", []):
            continue
        if (cue.get("confidence") or 0) >= min(0.85, confidence_threshold):
            continue
        cue["bar"] = pick
        grid = plan.get("grid", {})
        if grid:
            cue["time_s"] = round(bar_to_time(pick, grid), 3)
        cue["reason"] = (cue.get("reason", "") + "; Gemini low-confidence tie-breaker: "
                         + str(response.get("why") or "selected a listed candidate"))
        cue.setdefault("flags", []).append("Gemini suggestion; verify in review")
        picks[slot] = {"bar": pick, "why": response.get("why", "")}

    grid = plan.get("grid", {})
    def set_bar(slot, bar, label=None):
        cue = plan["cues"][slot]
        cue["bar"] = bar
        cue["time_s"] = (None if bar is None or not grid else
                         round(bar_to_time(bar, grid), 3))
        if label is not None and not plan.get("cue_map"):
            cue["name"] = label
    if not plan.get("cue_map") and plan.get("map_type") == "DROP":
        if "D" in picks:
            first_drop = plan["cues"]["D"]["bar"]
            if plan.get("drops"):
                plan["drops"][0]["bar"] = first_drop
                plan["drops"][0]["time_s"] = (None if not grid else
                                               round(bar_to_time(first_drop, grid), 3))
            first_before = first_drop - 32
            set_bar("C", first_before if first_before >= grid.get("first_bar", 0) else None, "-32 DROP")
            plan["cues"]["C"]["candidate_bars"] = [] if plan["cues"]["C"]["bar"] is None else [plan["cues"]["C"]["bar"]]
            if plan["cues"]["C"]["bar"] is None:
                plan["cues"]["C"].setdefault("flags", []).append("32-bar lead-in falls before track start")
            if len(plan.get("drops", [])) == 1:
                set_bar("E", first_before if first_before >= grid.get("first_bar", 0) else None, "-32 LAST DROP")
                set_bar("F", first_drop, "LAST DROP")
                if plan["cues"]["E"]["bar"] is None:
                    plan["cues"]["E"].setdefault("flags", []).append("32-bar lead-in falls before track start")
        if "F" in picks:
            last_drop = plan["cues"]["F"]["bar"]
            if plan.get("drops"):
                plan["drops"][-1]["bar"] = last_drop
                plan["drops"][-1]["time_s"] = (None if not grid else
                                                round(bar_to_time(last_drop, grid), 3))
            last_before = last_drop - 32
            set_bar("E", last_before if last_before >= grid.get("first_bar", 0) else None, "-32 LAST DROP")
            plan["cues"]["E"]["candidate_bars"] = [] if plan["cues"]["E"]["bar"] is None else [plan["cues"]["E"]["bar"]]
            if plan["cues"]["E"]["bar"] is None:
                plan["cues"]["E"].setdefault("flags", []).append("32-bar lead-in falls before track start")
    elif not plan.get("cue_map"):
        if "E" in picks:
            exit_bar = plan["cues"]["E"]["bar"]
            start = plan["cues"]["B"]["bar"]
            for slot, bars, label in (("C", 32, "EXIT -32"), ("D", 16, "EXIT -16")):
                bar = exit_bar - bars
                set_bar(slot, bar if bar >= start else None, label)
                plan["cues"][slot]["candidate_bars"] = ([] if plan["cues"][slot]["bar"] is None
                                                        else [plan["cues"][slot]["bar"]])
                if plan["cues"][slot]["bar"] is None:
                    plan["cues"][slot].setdefault("flags", []).append(f"EXIT -{bars} lands before START")
    if not plan.get("cue_map") and "B" in picks:
        start = plan["cues"]["B"]["bar"]
        intro = intro_candidate(start, grid.get("first_bar", 0))["bar"]
        count = start - intro if intro is not None else 0
        set_bar("A", intro, f"INTRO -{count}")
        if count <= 0:
            plan["cues"]["A"].setdefault("flags", []).append("no intro bar remains before START")
        if plan.get("map_type") == "VOCAL":
            for slot in ("C", "D"):
                bar = plan["cues"][slot]["bar"]
                if bar is not None and bar < start:
                    set_bar(slot, None)
                    plan["cues"][slot].setdefault("flags", []).append(f"{slot} lands before START after Gemini pick")
    if not plan.get("cue_map") and "H" in picks:
        outro_start = plan["cues"]["H"]["bar"]
        outro_bars = max(0, plan.get("bar_count", outro_start or 0) - outro_start) if outro_start is not None else 0
        plan["outro_bars"] = outro_bars
        set_bar("H", outro_start, f"OUTRO -{outro_bars}")
        go = None if outro_start is None or outro_start - 32 < grid.get("first_bar", 0) else outro_start - 32
        set_bar("G", go, "GO -32")
    refresh_dynamic_labels(plan)
    return picks


def _write_plan(path, plan):
    with open(path, "w", encoding="utf-8") as target:
        json.dump(plan, target, ensure_ascii=False, indent=2)


def run(plans_dir, cache_dir, output_dir, spend_cap, confidence_threshold):
    import gemini_assist
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    counts = {"requested": 0, "cached": 0, "skipped_confident": 0, "skipped_no_candidates": 0,
              "skipped_budget": 0, "failed": 0, "cost_usd": 0.0}
    for filename in sorted(os.listdir(plans_dir)):
        if not filename.endswith(".json"):
            continue
        track_id = filename[:-5]
        with open(os.path.join(plans_dir, filename), encoding="utf-8") as source:
            plan = json.load(source)
        # This CLI has no analysis inputs to rebuild maps; retain the existing map.
        plan = gemini_assist.assist(plan, None, track_id, cache_dir, spend_cap=spend_cap,
                                    confidence_threshold=confidence_threshold, map_forced=True)
        result = plan["gemini"]
        if result.get("error"):
            counts["failed"] += 1
            if "spend cap" in result["error"]:
                counts["skipped_budget"] += 1
        elif not result.get("used"):
            counts["skipped_confident"] += 1
        else:
            counts["cached" if result["cached"] else "requested"] += 1
            counts["cost_usd"] += result["new_cost_usd"]
        _write_plan(os.path.join(output_dir, filename), plan)
    counts["cost_usd"] = round(counts["cost_usd"], 6)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--cache-dir", required=True, help="Local cache directory, outside Git")
    parser.add_argument("--out", required=True)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--spend-cap", type=float, help="May lower, but never raise, the configured $3 cap")
    parser.add_argument("--enable", action="store_true", help="Confirm Gemini spending is enabled")
    args = parser.parse_args()
    if not args.enable:
        parser.error("Gemini requests are disabled by default; pass --enable to opt in")
    with open(args.config, encoding="utf-8") as source:
        config = json.load(source)
    if not config.get("switches", {}).get("gemini_tiebreaker", False):
        parser.error("enable gemini_tiebreaker in the v6 config before requesting Gemini")
    configured_cap = float(config.get("gemini_spend_cap_usd", 3.0))
    spend_cap = configured_cap if args.spend_cap is None else args.spend_cap
    if spend_cap > configured_cap:
        parser.error(f"--spend-cap cannot exceed configured cap ${configured_cap:.2f}")
    threshold = float(config.get("gemini_confidence_threshold", 0.6))
    print(json.dumps(run(args.plans_dir, args.cache_dir, args.out, spend_cap, threshold), sort_keys=True))


if __name__ == "__main__":
    main()
