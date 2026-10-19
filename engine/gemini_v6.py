"""Optional, cached low-confidence v6 tie-breaker. Sends cue candidates and timings, never audio."""
import argparse
import json
import os
import urllib.request

MODEL = "gemini-3.1-pro-preview"
PRICE_INPUT_PER_MILLION = 2.0
PRICE_OUTPUT_PER_MILLION = 12.0
MAX_OUTPUT_TOKENS = 500
DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "genre_params_v6.json")


def _request(plan, api_key):
    cues = {
        slot: {
            "label": cue["name"],
            "bar_candidates": cue.get("candidate_bars", []),
            "confidence": cue.get("confidence"),
            "reason": cue.get("reason"),
        }
        for slot, cue in plan["cues"].items()
    }
    context = {
        "map_type": plan["map_type"],
        "genre": plan.get("genre"),
        "drops": [{"bar": drop["bar"], "confidence": drop["confidence"]} for drop in plan.get("drops", [])],
        "aligned_lyric_chorus_spans": plan.get("lyric_choruses", []),
        "cues": cues,
    }
    prompt = (
        "Review these DJ cue candidates. For each cue with at least two candidate bars, select only a listed bar "
        "or null when the evidence is insufficient. Do not change cue labels or invent bars. Return JSON with "
        "cue keys and {pick: integer|null, why: short string}. Input: " + json.dumps(context, separators=(",", ":"))
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
                             "maxOutputTokens": MAX_OUTPUT_TOKENS},
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    usage = result.get("usageMetadata", {})
    text = "".join(part.get("text", "") for part in result["candidates"][0]["content"]["parts"])
    answer = json.loads(text)
    cost = (usage.get("promptTokenCount", 0) * PRICE_INPUT_PER_MILLION
            + usage.get("candidatesTokenCount", 0) * PRICE_OUTPUT_PER_MILLION) / 1_000_000
    return answer, cost, usage


def apply(plan, answer, confidence_threshold=0.6):
    picks = {}
    for slot, response in answer.items():
        if slot not in plan["cues"] or not isinstance(response, dict):
            continue
        cue = plan["cues"][slot]
        pick = response.get("pick")
        if type(pick) is not int or pick not in cue.get("candidate_bars", []):
            continue
        if (cue.get("confidence") or 0) >= min(0.85, confidence_threshold):
            continue
        cue["bar"] = pick
        grid = plan.get("grid", {})
        if grid:
            cue["time_s"] = round(grid["t1"] + pick * grid["bar_s"] - grid.get("offset_ms", 0) / 1000, 3)
        cue["reason"] = (cue.get("reason", "") + "; Gemini low-confidence tie-breaker: "
                         + str(response.get("why") or "selected a listed candidate"))
        cue.setdefault("flags", []).append("Gemini suggestion; verify in review")
        picks[slot] = {"bar": pick, "why": response.get("why", "")}

    grid = plan.get("grid", {})
    def set_bar(slot, bar, label=None):
        cue = plan["cues"][slot]
        cue["bar"] = bar
        cue["time_s"] = (None if bar is None or not grid else
                         round(grid["t1"] + bar * grid["bar_s"] - grid.get("offset_ms", 0) / 1000, 3))
        if label is not None:
            cue["name"] = label
    if plan.get("map_type") == "DROP":
        if "D" in picks:
            first_drop = plan["cues"]["D"]["bar"]
            if plan.get("drops"):
                plan["drops"][0]["bar"] = first_drop
                plan["drops"][0]["time_s"] = (None if not grid else
                                               round(grid["t1"] + first_drop * grid["bar_s"]
                                                     - grid.get("offset_ms", 0) / 1000, 3))
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
                                                round(grid["t1"] + last_drop * grid["bar_s"]
                                                      - grid.get("offset_ms", 0) / 1000, 3))
            last_before = last_drop - 32
            set_bar("E", last_before if last_before >= grid.get("first_bar", 0) else None, "-32 LAST DROP")
            plan["cues"]["E"]["candidate_bars"] = [] if plan["cues"]["E"]["bar"] is None else [plan["cues"]["E"]["bar"]]
            if plan["cues"]["E"]["bar"] is None:
                plan["cues"]["E"].setdefault("flags", []).append("32-bar lead-in falls before track start")
    else:
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
    if "B" in picks:
        intro = plan["cues"]["A"]["bar"]
        start = plan["cues"]["B"]["bar"]
        count = start - intro if intro is not None else 0
        set_bar("A", intro if count > 0 else None, f"INTRO -{max(0, count)}BS")
        if count <= 0:
            plan["cues"]["A"].setdefault("flags", []).append("no intro bar remains before START")
        if plan.get("map_type") == "VOCAL":
            for slot in ("C", "D"):
                bar = plan["cues"][slot]["bar"]
                if bar is not None and bar < start:
                    set_bar(slot, None)
                    plan["cues"][slot].setdefault("flags", []).append(f"{slot} lands before START after Gemini pick")
    if "H" in picks:
        outro_start = plan["cues"]["H"]["bar"]
        outro_bars = max(0, plan.get("bar_count", outro_start or 0) - outro_start) if outro_start is not None else 0
        plan["outro_bars"] = outro_bars
        set_bar("H", outro_start, f"OUTRO {outro_bars}")
        go = None if outro_start is None or outro_start - 32 < grid.get("first_bar", 0) else outro_start - 32
        set_bar("G", go, "GO -32")
    return picks


def _write_plan(path, plan):
    with open(path, "w", encoding="utf-8") as target:
        json.dump(plan, target, ensure_ascii=False, indent=2)


def run(plans_dir, cache_dir, output_dir, spend_cap, confidence_threshold):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required when Gemini tie-breaking is explicitly enabled")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    spent = sum(
        json.load(open(os.path.join(cache_dir, filename), encoding="utf-8")).get("cost_usd", 0)
        for filename in os.listdir(cache_dir) if filename.endswith(".json")
    )
    counts = {"requested": 0, "cached": 0, "skipped_confident": 0, "skipped_no_candidates": 0,
              "skipped_budget": 0, "cost_usd": spent}
    for filename in sorted(os.listdir(plans_dir)):
        if not filename.endswith(".json"):
            continue
        track_id = filename[:-5]
        with open(os.path.join(plans_dir, filename), encoding="utf-8") as source:
            plan = json.load(source)
        low_confidence = [cue for cue in plan["cues"].values()
                          if (cue.get("confidence") or 0) < confidence_threshold]
        uncertain = [cue for cue in low_confidence if len(cue.get("candidate_bars", [])) > 1]
        if not uncertain:
            counts["skipped_confident" if not low_confidence else "skipped_no_candidates"] += 1
            _write_plan(os.path.join(output_dir, filename), plan)
            continue
        cache_path = os.path.join(cache_dir, filename)
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as source:
                record = json.load(source)
            counts["cached"] += 1
        else:
            prompt_byte_upper_bound = len(json.dumps(plan, separators=(",", ":")).encode("utf-8")) + 1024
            reserved_cost = (prompt_byte_upper_bound * PRICE_INPUT_PER_MILLION
                             + MAX_OUTPUT_TOKENS * PRICE_OUTPUT_PER_MILLION) / 1_000_000
            if spent + reserved_cost > spend_cap:
                counts["skipped_budget"] += 1
                _write_plan(os.path.join(output_dir, filename), plan)
                continue
            answer, cost, usage = _request(plan, api_key)
            record = {"track_id": track_id, "answer": answer, "cost_usd": cost, "usage": usage}
            with open(cache_path, "w", encoding="utf-8") as target:
                json.dump(record, target, indent=2)
            spent += cost
            counts["requested"] += 1
            counts["cost_usd"] = round(spent, 6)
            if spent > spend_cap:
                raise RuntimeError(f"Gemini spend cap exceeded: ${spent:.4f} > ${spend_cap:.2f}")
        suggestions = apply(plan, record.get("answer", {}), confidence_threshold)
        plan["gemini"] = {"used": bool(suggestions), "model": MODEL, "suggestions": suggestions,
                          "cost_usd": record.get("cost_usd", 0)}
        _write_plan(os.path.join(output_dir, filename), plan)
    counts["cost_usd"] = round(spent, 6)
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
