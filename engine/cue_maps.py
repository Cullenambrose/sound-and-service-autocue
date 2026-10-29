"""Configurable cue-map meanings, labels, and Serato-compatible colors."""
import copy
import math
import re
from timing import bar_to_time


SERATO_PALETTE = (
    {"id": "red", "name": "Red", "metadata": "#CC0000", "display": "#C02626"},
    {"id": "rust", "name": "Rust", "metadata": "#CC4400", "display": "#DB4E27"},
    {"id": "orange", "name": "Orange", "metadata": "#CC8800", "display": "#F8821A"},
    {"id": "yellow", "name": "Yellow", "metadata": "#CCCC00", "display": "#FAC313"},
    {"id": "lime", "name": "Lime", "metadata": "#88CC00", "display": "#4EB648"},
    {"id": "forest", "name": "Forest", "metadata": "#44CC00", "display": "#006838"},
    {"id": "green", "name": "Green", "metadata": "#00CC00", "display": "#1FAD26"},
    {"id": "spring", "name": "Spring", "metadata": "#00CC44", "display": "#8DC63F"},
    {"id": "sea", "name": "Sea", "metadata": "#00CC88", "display": "#2B3673"},
    {"id": "teal", "name": "Teal", "metadata": "#00CCCC", "display": "#1DBEBD"},
    {"id": "sky", "name": "Sky", "metadata": "#0088CC", "display": "#0F88CA"},
    {"id": "blue", "name": "Blue", "metadata": "#0044CC", "display": "#16308B"},
    {"id": "royal", "name": "Royal", "metadata": "#0000CC", "display": "#173BA2"},
    {"id": "indigo", "name": "Indigo", "metadata": "#4400CC", "display": "#5C3F97"},
    {"id": "purple", "name": "Purple", "metadata": "#8800CC", "display": "#6823B6"},
    {"id": "magenta", "name": "Magenta", "metadata": "#CC00CC", "display": "#CE359E"},
    {"id": "rose", "name": "Rose", "metadata": "#CC0088", "display": "#DC1D49"},
    {"id": "crimson", "name": "Crimson", "metadata": "#CC0044", "display": "#C71136"},
)
PALETTE_BY_ID = {color["id"]: color for color in SERATO_PALETTE}

MEANINGS = {
    "intro_start": {"label": "Start of intro", "source": "intro_start", "offset": 0, "bars": "to_start"},
    "start": {"label": "START", "source": "start", "offset": 0},
    "first_chorus_start": {"label": "Start of first chorus", "source": "first_chorus_start", "offset": 0},
    "first_chorus_end": {"label": "End of first chorus", "source": "first_chorus_end", "offset": 0},
    "last_chorus_start": {"label": "Start of last chorus", "source": "last_chorus_start", "offset": 0},
    "last_chorus_end": {"label": "End of last chorus", "source": "last_chorus_end", "offset": 0},
    "before_first_chorus_exit_16": {"label": "16 bars before first chorus ends", "source": "first_chorus_end", "offset": -16},
    "before_first_chorus_exit_32": {"label": "32 bars before first chorus ends", "source": "first_chorus_end", "offset": -32},
    "before_last_chorus_16": {"label": "16 bars before last chorus", "source": "last_chorus_start", "offset": -16},
    "before_last_chorus_32": {"label": "32 bars before last chorus", "source": "last_chorus_start", "offset": -32},
    "first_drop_start": {"label": "Start of first drop", "source": "first_drop_start", "offset": 0},
    "last_drop_start": {"label": "Start of last drop", "source": "last_drop_start", "offset": 0},
    "before_first_drop_16": {"label": "16 bars before first drop", "source": "first_drop_start", "offset": -16},
    "before_first_drop_32": {"label": "32 bars before first drop", "source": "first_drop_start", "offset": -32},
    "before_last_drop_16": {"label": "16 bars before last drop", "source": "last_drop_start", "offset": -16},
    "before_last_drop_32": {"label": "32 bars before last drop", "source": "last_drop_start", "offset": -32},
    "outro_start": {"label": "Start of outro", "source": "outro_start", "offset": 0, "bars": "to_end"},
    "outro_mix_start": {"label": "Start of outro (32-beat mix window)", "source": "outro_start", "offset": 0, "bars": "mix_window"},
    "before_outro_8": {"label": "32 beats (8 bars) before outro", "source": "outro_start", "offset": -8},
    "before_outro_16": {"label": "16 bars before outro", "source": "outro_start", "offset": -16},
    "before_outro_32": {"label": "32 bars before outro", "source": "outro_start", "offset": -32},
    "before_first_chorus_exit_8": {"label": "8 bars (32 beats) before first chorus ends", "source": "first_chorus_end", "offset": -8},
    "before_first_chorus_exit_4": {"label": "4 bars (16 beats) before first chorus ends", "source": "first_chorus_end", "offset": -4},
    "out": {"label": "OUT (end of the 8-bar mix-out or last full-volume phrase)", "source": "out", "offset": 0},
    "before_out_8": {"label": "8 bars (32 beats) before OUT", "source": "out", "offset": -8},
}

# v6.1 canonical Pop/Vocal 8-pad layout. Pad 3 (C) is intentionally empty.
_VOCAL_DEFAULTS = {
    "A": ("intro_start", "Intro {bars} bars ({beats} beats)", "red"),
    "B": ("start", "START", "orange"),
    "C": ("before_first_chorus_exit_32", "EXIT -32", "sky", False),
    "D": ("before_first_chorus_exit_8", "GO {bars} bars ({beats} beats) > EXIT", "yellow"),
    "E": ("before_first_chorus_exit_4", "Mix {bars} bars ({beats} beats) > EXIT", "magenta"),
    "F": ("first_chorus_end", "EXIT 1st Chorus", "green"),
    "G": ("before_out_8", "GO {bars} bars ({beats} beats) > OUT", "teal"),
    "H": ("out", "OUT", "purple"),
}
# The pre-v6.1 VOCAL layout, kept as a selectable custom map.
LEGACY_V7_VOCAL_DEFAULTS = {
    "A": ("intro_start", "INTRO-{beats}", "teal"),
    "B": ("start", "START", "green"),
    "C": ("before_first_chorus_exit_32", "EXIT -32", "purple"),
    "D": ("before_first_chorus_exit_16", "EXIT -16", "magenta"),
    "E": ("first_chorus_end", "EXIT/JUMP", "rose"),
    "F": ("last_chorus_start", "LAST CHORUS", "yellow"),
    "G": ("before_outro_8", "GO-{beats}", "sky"),
    "H": ("outro_mix_start", "EXIT-{beats}", "orange"),
}
_DROP_DEFAULTS = {
    "A": ("intro_start", "INTRO-{beats}", "teal"),
    "B": ("start", "START", "green"),
    "C": ("before_first_drop_32", "-32 DROP", "purple"),
    "D": ("first_drop_start", "FIRST DROP", "magenta"),
    "E": ("before_last_drop_32", "-32 LAST DROP", "rose"),
    "F": ("last_drop_start", "LAST DROP", "yellow"),
    "G": ("before_outro_8", "GO-{beats}", "sky"),
    "H": ("outro_mix_start", "EXIT-{beats}", "orange"),
}


def _map_defaults(values):
    return {
        slot: {"meaning": spec[0], "label": spec[1], "color": spec[2], "enabled": spec[3] if len(spec) > 3 else True}
        for slot, spec in values.items()
    }


def legacy_v7_vocal_map():
    return _map_defaults(LEGACY_V7_VOCAL_DEFAULTS)


DEFAULT_CUE_MAPS = {
    "version": 2,
    "mode": "AUTO",
    "mix_lengths": {"intro": [32, 16, 8, 4], "go": [32, 16, 8, 4], "exit": [32, 16, 8, 4]},
    "end_of_song_fallback": True,
    "maps": {"VOCAL": _map_defaults(_VOCAL_DEFAULTS), "DROP": _map_defaults(_DROP_DEFAULTS)},
}


def normalize_cue_maps(value=None):
    result = copy.deepcopy(DEFAULT_CUE_MAPS)
    if value is None:
        return result
    if not isinstance(value, dict):
        raise ValueError("Cue map settings must be an object.")
    version = value.get("version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported cue map settings version.")
    mode = str(value.get("mode", result["mode"])).upper()
    if mode not in ("AUTO", "VOCAL", "DROP"):
        raise ValueError("Cue map mode must be Auto, Vocal, or Drop.")
    result["mode"] = mode
    end_fallback = value.get("end_of_song_fallback", True)
    if not isinstance(end_fallback, bool):
        raise ValueError("End-of-song fallback must be true or false.")
    result["end_of_song_fallback"] = end_fallback
    lengths = value.get("mix_lengths", {})
    if not isinstance(lengths, dict) or set(lengths) - {"intro", "go", "exit"}:
        raise ValueError("Mix lengths must contain intro, go and exit beat preferences.")
    for key, beats in lengths.items():
        if (not isinstance(beats, list) or not beats or len(beats) > 16
                or any(type(beat) is not int or beat < 4 or beat > 256 or beat % 4 for beat in beats)
                or beats != sorted(set(beats), reverse=True)):
            raise ValueError(f"{key} mix lengths must be unique descending beat counts, multiples of 4 from 4 to 256.")
        result["mix_lengths"][key] = list(beats)
    maps = value.get("maps", {})
    if not isinstance(maps, dict):
        raise ValueError("Cue maps must be an object containing VOCAL and DROP maps.")
    for map_type in ("VOCAL", "DROP"):
        slots = maps.get(map_type, {})
        if not isinstance(slots, dict):
            raise ValueError(f"{map_type} cue map must be an object.")
        for slot in "ABCDEFGH":
            candidate = slots.get(slot)
            if candidate is None:
                continue
            if not isinstance(candidate, dict):
                raise ValueError(f"Cue {slot} settings must be an object.")
            if version < 2:
                legacy = {
                    "A": ("intro_start", "INTRO -{bars}"),
                    "G": ("before_outro_32", "GO -32"),
                    "H": ("outro_start", "OUTRO -{bars}"),
                }
                if slot in legacy and (candidate.get("meaning"), candidate.get("label")) == legacy[slot]:
                    candidate = {**candidate,
                                 "meaning": result["maps"][map_type][slot]["meaning"],
                                 "label": result["maps"][map_type][slot]["label"]}
            meaning = candidate.get("meaning", result["maps"][map_type][slot]["meaning"])
            if not isinstance(meaning, str) or meaning not in MEANINGS:
                raise ValueError(f"Cue {slot} uses an unsupported meaning.")
            label = candidate.get("label", result["maps"][map_type][slot]["label"])
            old_pop_labels = {
                ("intro_start", "Intro - {bars}BTS"),
                ("before_first_chorus_exit_8", "GO-8Exit"),
                ("before_first_chorus_exit_4", "Mix-4exit"),
                ("before_out_8", "GO-8OUT"),
            }
            if map_type == "VOCAL" and isinstance(label, str) and (meaning, label) in old_pop_labels:
                label = {
                    spec[0]: spec[1] for spec in _VOCAL_DEFAULTS.values()
                }[meaning]
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                raise ValueError(f"Cue {slot} label must contain 1–80 characters.")
            plain_label = label.replace("{bars}", "").replace("{beats}", "")
            if "{" in plain_label or "}" in plain_label:
                raise ValueError(f"Cue {slot} label may only use the {{bars}} and {{beats}} tokens.")
            color = candidate.get("color", result["maps"][map_type][slot]["color"])
            if not isinstance(color, str) or color not in PALETTE_BY_ID:
                raise ValueError(f"Cue {slot} color must be selected from the Serato palette.")
            enabled = candidate.get("enabled", result["maps"][map_type][slot]["enabled"])
            if not isinstance(enabled, bool):
                raise ValueError(f"Cue {slot} enabled value must be true or false.")
            result["maps"][map_type][slot] = {
                "meaning": meaning, "label": label, "color": color, "enabled": enabled,
            }
    return result


def cue_map_catalog():
    return {
        "meanings": [{"id": key, "label": value["label"]} for key, value in MEANINGS.items()],
        "palette": [dict(color) for color in SERATO_PALETTE],
    }


def _render_label(template, meaning, bar, context):
    bars_kind = MEANINGS[meaning].get("bars")
    if bars_kind == "mix_window" and context.get("end_fallback"):
        return template.replace("{bars}", "END").replace("{beats}", "END")
    if "count" in context:
        count = context["count"]
    elif bars_kind == "to_start":
        count = max(0, context["start_bar"] - bar)
    elif bars_kind == "to_end":
        count = max(0, context["bar_count"] - bar)
    elif bars_kind == "mix_window":
        count = preferred_mix_bars(context["mix_end_bar"] - bar, context["exit_lengths"]) or 0
    else:
        count = context.get("lead_bars", abs(MEANINGS[meaning]["offset"]))
    legacy_countdowns = {
        "EXIT -32", "EXIT -16", "-32 DROP", "-16 DROP",
        "-32 LAST DROP", "-16 LAST DROP", "GO-8Exit", "Mix-4exit", "GO-8OUT",
    }
    if template in legacy_countdowns and MEANINGS[meaning]["offset"] < 0:
        return re.sub(r"-\d+", f"-{count:g}", template, count=1)
    return template.replace("{bars}", f"{count:g}").replace("{beats}", f"{count * 4:g}")


def preferred_mix_bars(available, beats=(32, 16, 8, 4)):
    return next((beat // 4 for beat in beats if available >= beat / 4), None)


def intro_candidate(start_bar, first_bar, beats=(32, 16, 8, 4)):
    lead = None if start_bar is None else preferred_mix_bars(start_bar - first_bar, beats)
    return {
        "bar": None if lead is None else start_bar - lead,
        "confidence": 0.0 if lead is None else 0.7,
        "reason": (f"insufficient room for configured minimum {beats[-1]}-beat intro before START" if lead is None
                   else f"{lead}-bar ({lead * 4}-beat) intro lead-in before START"),
        "flags": ["configured intro mix window unavailable"] if lead is None else [],
    }


def validate_automatic_plan(plan):
    """Reject inconsistent automatic output; manual review edits do not run this guard."""
    errors = []
    mapping = plan["cue_map"]
    events = mapping["candidates"]
    first_bar = plan["grid"].get("first_bar", 0)
    end = plan.get("mix_end_bar", plan["bar_count"])
    for first, last in (("first_drop_start", "last_drop_start"),
                        ("first_chorus_start", "last_chorus_start"),
                        ("first_chorus_end", "last_chorus_end"),
                        ("first_chorus_start", "first_chorus_end"),
                        ("last_chorus_start", "last_chorus_end")):
        left, right = events.get(first, {}).get("bar"), events.get(last, {}).get("bar")
        if left is not None and right is not None:
            if left > right or (left == right and first.endswith("_start") and last.endswith("_end")):
                errors.append(first + " must precede " + last)
    for slot, spec in mapping["resolved"].items():
        cue = plan["cues"][slot]
        bar = cue["bar"]
        if not spec["enabled"] and bar is not None:
            errors.append(slot + " is disabled but has a position")
        if bar is None:
            if cue.get("time_s") is not None:
                errors.append(slot + " is empty but has a timestamp")
            continue
        if isinstance(bar, bool) or not isinstance(bar, (int, float)) or not math.isfinite(bar):
            errors.append(slot + " has an invalid position")
            continue
        at_end = spec.get("end_fallback") and spec["meaning"] == "outro_mix_start" or spec["meaning"] == "out"
        if bar < first_bar or bar > end or (bar == end and not at_end):
            errors.append(slot + " is outside permitted track bounds")
        expected_time = bar_to_time(bar, plan["grid"])
        if expected_time < -1e-6:
            errors.append(slot + " precedes the audio")
        time = cue.get("time_s")
        tolerance = 0.000501
        if cue.get("unsnapped") and spec["meaning"] == "start":
            tolerance = float(plan["grid"]["bar_s"]) / 2 + 0.000501
        if (not isinstance(time, (int, float)) or not math.isfinite(time)
                or abs(time - expected_time) > tolerance):
            errors.append(slot + " timestamp disagrees with its grid position")
        meaning = MEANINGS[spec["meaning"]]
        anchor = end if spec.get("end_fallback") else events.get(meaning["source"], {}).get("bar")
        if anchor is None or abs(bar - anchor - spec["offset"]) > 1e-6:
            errors.append(slot + " disagrees with its event anchor or derived offset")
        if meaning["source"] == "outro_start" and not spec.get("end_fallback"):
            boundary = plan.get("outro_vocal_free_bar")
            if anchor is not None and boundary is not None and anchor < boundary:
                errors.append(slot + " instrumental outro precedes detected vocals")
        if spec["meaning"] == "outro_mix_start" and not spec.get("end_fallback"):
            if preferred_mix_bars(end - bar, mapping["mix_lengths"]["exit"]) is None:
                errors.append(slot + " has insufficient instrumental mix time")
        if spec.get("end_fallback") and not plan.get("no_vocal_free_outro"):
            errors.append(slot + " uses an end fallback without evidence of a missing outro")
        if cue["name"] != spec["label"]:
            errors.append(slot + " label disagrees with its resolved meaning")
    if errors:
        raise ValueError("Invalid automatic cue plan: " + "; ".join(errors))
    validate_phrasing(plan)
    return plan


def apply_cue_map(plan, settings, candidates, first_bar):
    if "mix_window_evidence" in plan:
        invalidate_mix_window_evidence(plan)
    settings = normalize_cue_maps(settings)
    map_type = plan["map_type"]
    slots = settings["maps"][map_type]
    lengths = settings["mix_lengths"]
    candidates = copy.deepcopy(candidates)
    candidates["intro_start"] = intro_candidate(candidates["start"]["bar"], first_bar, lengths["intro"])
    context = {"start_bar": candidates["start"]["bar"], "bar_count": plan["bar_count"],
               "mix_end_bar": plan.get("mix_end_bar", plan["bar_count"]), "exit_lengths": lengths["exit"]}
    resolved = {}
    for slot, spec in slots.items():
        cue = plan["cues"][slot]
        color = PALETTE_BY_ID[spec["color"]]
        meaning = MEANINGS[spec["meaning"]]
        source = candidates.get(meaning["source"], {})
        end_fallback = (settings["end_of_song_fallback"] and plan.get("no_vocal_free_outro", False)
                        and spec["meaning"] in ("before_outro_8", "outro_mix_start"))
        if end_fallback:
            source = {
                "bar": context["mix_end_bar"], "confidence": 0.35,
                "reason": "end-of-song fallback; no instrumental outro",
                "flags": ["no instrumental outro: GO may contain vocals; EXIT is at song end with no mix time afterward"],
            }
        bar = source.get("bar")
        unavailable_reason = None
        label_template, lead_bars = spec["label"], abs(meaning["offset"])
        if bar is not None:
            anchor = bar
            bar = anchor + meaning["offset"]
            if meaning["offset"] < 0:
                floor = first_bar
                if meaning["source"] == "outro_start":
                    last = candidates.get("last_drop_start" if map_type == "DROP" else "last_chorus_start", {}).get("bar")
                    if last is not None:
                        floor = max(floor, last + 1)
                elif meaning["source"] == "last_drop_start":
                    first_drop = candidates.get("first_drop_start", {}).get("bar")
                    if first_drop is not None and first_drop < anchor:
                        floor = max(floor, first_drop + 1)
                elif spec["meaning"] in ("before_first_chorus_exit_8", "before_first_chorus_exit_4"):
                    floor = max(floor, context["start_bar"])
                # A lead-in that lands before the track, the first drop (for last-drop leads) or the last drop/chorus
                # (for the outro) shrinks to 16, then 8, bars; otherwise the cue stays empty.
                full = lengths["go"][0] // 4 if spec["meaning"] == "before_outro_8" else abs(meaning["offset"])
                bar = None
                flexible = spec["meaning"] in ("before_outro_8", "before_out_8",
                                               "before_first_chorus_exit_8", "before_first_chorus_exit_4")
                leads = ([beat // 4 for beat in lengths["go"] if beat // 4 <= full]
                         if flexible else (full, 16, 8))
                for lead in leads:
                    if (spec["meaning"] != "before_outro_8" and lead > full) or anchor - lead < floor:
                        continue
                    bar = anchor - lead
                    lead_bars = lead
                    if lead != full:
                        label_template = label_template.replace(f"-{full}", f"-{lead}")
                    break
                if bar is None:
                    unavailable_reason = f"insufficient room for {meaning['label'].casefold()} after the preceding event"
            at_song_end = end_fallback and spec["meaning"] == "outro_mix_start" or spec["meaning"] == "out"
            if bar is not None and (bar < first_bar or bar > context["mix_end_bar"]
                                    or (bar == context["mix_end_bar"] and not at_song_end)):
                bar = None
            if (bar is not None and meaning["offset"] < 0
                    and meaning["source"] in ("first_chorus_end", "last_chorus_start")
                    and bar < context["start_bar"]):
                bar = None
            if bar is not None and spec["meaning"] == "outro_mix_start" and not end_fallback and not preferred_mix_bars(context["mix_end_bar"] - bar, lengths["exit"]):
                bar = None
                unavailable_reason = f"insufficient instrumental outro for configured minimum {lengths['exit'][-1]}-beat EXIT window"
        if not spec["enabled"]:
            bar = None
            cue.update({"confidence": 0.0, "reason": "disabled in cue map settings",
                        "flags": ["cue disabled by user"]})
        elif bar is None:
            reason = unavailable_reason or (
                source.get("reason") if meaning["source"] == "intro_start" else None
            ) or f"no evidence for {meaning['label'].casefold()}"
            cue.update({"confidence": 0.0, "reason": reason,
                        "flags": [reason, f"{meaning['label']} unavailable"]})
        else:
            cue.update({
                "confidence": source.get("confidence", 0.0),
                "reason": source.get("reason", meaning["label"]),
                "flags": list(source.get("flags") or []),
            })
            requested_lead = lengths["go"][0] // 4 if spec["meaning"] == "before_outro_8" else abs(meaning["offset"])
            if meaning["offset"] < 0 and lead_bars != requested_lead:
                cue["reason"] += f"; shortened lead-in to {lead_bars} bars ({lead_bars * 4} beats)"
            if spec["meaning"] == "outro_mix_start" and not end_fallback:
                window = preferred_mix_bars(context["mix_end_bar"] - bar, lengths["exit"])
                cue["reason"] += f"; {window * 4}-beat mix window from the outro beginning"
        unsnapped = (bar is not None and spec["enabled"] and spec["meaning"] == "start" and source.get("unsnapped")
                     and isinstance(source.get("time_s"), (int, float)) and bar == source.get("bar"))
        cue.update({
            "name": _render_label(label_template, spec["meaning"], bar, {**context, "lead_bars": lead_bars, "end_fallback": end_fallback}) if bar is not None
                    else spec["label"].replace("{bars}", "—").replace("{beats}", "—"),
            "meaning": spec["meaning"],
            "color_id": color["id"],
            "color": color["metadata"],
            "display_color": color["display"],
            "bar": bar,
            "time_s": None if bar is None else (round(float(source["time_s"]), 3) if unsnapped
                                                else round(bar_to_time(bar, plan["grid"]), 3)),
        })
        if unsnapped:
            cue.update({"unsnapped": True, "start_anchor_bar": bar})
        else:
            cue.pop("unsnapped", None)
            cue.pop("start_anchor_bar", None)
        cue["candidate_bars"] = [] if bar is None else (
            [candidate for candidate in range(math.ceil(bar - 2), math.floor(bar + 2) + 1)
             if first_bar <= candidate < context["mix_end_bar"]]
            if not end_fallback and meaning["source"] != "intro_start" and meaning["offset"] == 0 and not (
                meaning["source"] == "last_drop_start" and len(plan.get("drops", [])) == 1
            ) else [bar]
        )
        if meaning["source"] == "outro_start" and plan.get("outro_vocal_free_bar") is not None and not end_fallback:
            cue["candidate_bars"] = [
                candidate for candidate in cue["candidate_bars"]
                if candidate - (-lead_bars if meaning["offset"] < 0 else 0) >= plan["outro_vocal_free_bar"]
            ]
        if spec["meaning"] == "outro_mix_start" and not end_fallback:
            cue["candidate_bars"] = [
                candidate for candidate in cue["candidate_bars"]
                if preferred_mix_bars(context["mix_end_bar"] - candidate, lengths["exit"]) is not None
            ]
        resolved[slot] = {"meaning": spec["meaning"], "label": cue["name"],
                          "label_template": spec["label"], "color": color["metadata"],
                          "display_color": color["display"],
                          "enabled": spec["enabled"]}
        resolved[slot]["offset"] = -lead_bars if meaning["offset"] < 0 else meaning["offset"]
        resolved[slot]["end_fallback"] = end_fallback
    plan["cue_map"] = {
        "version": 2, "mode": settings["mode"], "resolved": resolved, "mix_lengths": copy.deepcopy(lengths),
        "end_of_song_fallback": settings["end_of_song_fallback"],
        "settings": copy.deepcopy(slots),
        "candidates": {
            key: {**{field: event.get(field) for field in ("bar", "confidence", "reason")},
                  "flags": list(event.get("flags") or [])}
            for key, event in candidates.items()
        },
    }
    return validate_automatic_plan(plan)


def invalidate_mix_window_evidence(plan):
    """Positions changed without stem data: previous window checks no longer describe these cues."""
    plan.pop("mix_window_evidence", None)
    for cue in plan.get("cues", {}).values():
        if "mix_window_evidence" in cue:
            cue["mix_window_evidence"] = {"status": "unverified", "reason": "cue positions changed; reanalysis required"}
    warning = "Mix-window evidence invalidated by cue changes; reanalyze to assess the new windows."
    if warning not in plan.setdefault("flags", []):
        plan["flags"].append(warning)


def refresh_dynamic_labels(plan):
    start = plan.get("cues", {}).get("B", {}).get("bar")
    for slot, resolved in plan.get("cue_map", {}).get("resolved", {}).items():
        template = resolved.get("label_template", "")
        meaning = resolved.get("meaning")
        cue = plan.get("cues", {}).get(slot, {})
        if meaning not in MEANINGS or cue.get("bar") is None:
            continue
        if cue.get("name") != resolved.get("label"):
            resolved["label_template"] = cue.get("name", "")
            resolved["label"] = cue.get("name", "")
            template = resolved["label_template"]
        context = {"start_bar": start or 0, "bar_count": plan.get("bar_count", 0),
                   "mix_end_bar": plan.get("mix_end_bar", plan.get("bar_count", 0)),
                   "exit_lengths": plan["cue_map"].get("mix_lengths", {}).get("exit", [32, 16, 8, 4]),
                   "end_fallback": resolved.get("end_fallback", False),
                   "lead_bars": abs(resolved.get("offset", MEANINGS[meaning]["offset"]))}
        countdown = meaning == "intro_start" or MEANINGS[meaning]["offset"] < 0
        target = _countdown_target(plan, meaning, resolved.get("end_fallback", False)) if countdown else None
        if target is not None:
            context["count"] = target - cue["bar"]
            if MEANINGS[meaning]["offset"] < 0:
                resolved["offset"] = -context["count"]
        cue["name"] = _render_label(template, meaning, cue["bar"], context)
        resolved["label"] = cue["name"]


def _countdown_target(plan, meaning, end_fallback=False):
    if end_fallback:
        return plan.get("mix_end_bar", plan.get("bar_count"))
    source = "start" if meaning == "intro_start" else MEANINGS[meaning]["source"]
    mapping = plan.get("cue_map", {})
    for slot, spec in mapping.get("resolved", {}).items():
        anchor_meaning = MEANINGS.get(spec.get("meaning"), {})
        if (spec.get("enabled") and anchor_meaning.get("source") == source
                and anchor_meaning.get("offset") == 0):
            bar = plan.get("cues", {}).get(slot, {}).get("bar")
            if bar is not None or spec.get("meaning") == source:
                return bar
    return mapping.get("candidates", {}).get(source, {}).get("bar")


def phrasing_report(plan):
    """Audit stored plans without audio/model work; bars are structural, not proof of clean vocals."""
    errors, warnings, intervals = [], [], []
    mapping = plan.get("cue_map", {})
    if not mapping.get("resolved"):
        warnings.append("Unversioned cue meanings: legacy numeric labels cannot be trusted as beat counts; reanalyze.")
        if re.fullmatch(r"GO\s+-\d+", str(plan.get("cues", {}).get("G", {}).get("name", ""))):
            errors.append("G: legacy GO countdown has ambiguous units and target; reanalyze and review before export.")
        for left, right in (("A", "B"), ("G", "H")):
            begin, end = plan.get("cues", {}).get(left, {}), plan.get("cues", {}).get(right, {})
            if begin.get("bar") is not None and end.get("bar") is not None:
                count = end["bar"] - begin["bar"]
                intervals.append({"slot": left, "target_slot": right, "bars": count, "beats": count * 4,
                                  "target_inferred": True})
    for slot, cue in plan.get("cues", {}).items():
        bar = cue.get("bar")
        if bar is None:
            continue
        grid = plan.get("grid", {})
        expected = bar_to_time(bar, grid)
        timestamp = cue.get("time_s")
        valid_time = type(timestamp) in (int, float) and math.isfinite(timestamp) and timestamp >= 0
        if ((timestamp is not None and (not valid_time or
                (not cue.get("unsnapped") and abs(timestamp - expected) > 0.000501)))
                or (cue.get("unsnapped") and not valid_time)):
            errors.append(f"{slot}: stored timestamp disagrees with grid.")
        spec = mapping.get("resolved", {}).get(slot, {})
        meaning = spec.get("meaning")
        if meaning not in MEANINGS:
            continue
        if meaning != "intro_start" and MEANINGS[meaning]["offset"] >= 0:
            context = {"bar_count": plan.get("bar_count", 0),
                       "mix_end_bar": plan.get("mix_end_bar", plan.get("bar_count", 0)),
                       "exit_lengths": mapping.get("mix_lengths", {}).get("exit", [32, 16, 8, 4]),
                       "end_fallback": spec.get("end_fallback", False), "lead_bars": 0}
            expected_name = _render_label(spec.get("label_template", cue.get("name", "")), meaning, bar, context)
            if cue.get("name") != expected_name:
                errors.append(f"{slot}: label disagrees with its actual mix window.")
            continue
        target = _countdown_target(plan, meaning, spec.get("end_fallback", False))
        if target is None:
            errors.append(f"{slot}: countdown target is empty.")
            continue
        count = target - bar
        intervals.append({"slot": slot, "target_bar": target, "bars": count, "beats": count * 4})
        if count <= 0:
            errors.append(f"{slot}: countdown must precede its target.")
        if MEANINGS[meaning]["offset"] < 0 and abs(count + spec.get("offset", MEANINGS[meaning]["offset"])) > 1e-6:
            errors.append(f"{slot}: countdown distance disagrees with resolved offset.")
        context = {"count": count, "end_fallback": spec.get("end_fallback", False)}
        expected_name = _render_label(spec.get("label_template", cue.get("name", "")), meaning, bar, context)
        if cue.get("name") != expected_name:
            errors.append(f"{slot}: label {cue.get('name')!r} disagrees with {count:g} bars / {count * 4:g} beats.")
    start = next((cue for cue in plan.get("cues", {}).values()
                  if cue.get("meaning") == "start" and cue.get("unsnapped")), None)
    if start and type(start.get("time_s")) in (int, float) and math.isfinite(start["time_s"]):
        offset = start["time_s"] - bar_to_time(start["bar"], plan["grid"])
        if abs(offset) > 0.000501:
            warnings.append(f"START is {offset:+.3f}s from its structural anchor; intro label counts to the anchor, not exact vocal time.")
    warnings.append("Arithmetic only: clean vocal-free windows and musical phrase boundaries require audio review.")
    return {"errors": errors, "warnings": warnings, "intervals": intervals}


def validate_phrasing(plan):
    errors = phrasing_report(plan)["errors"]
    if errors:
        raise ValueError("Invalid cue phrasing: " + "; ".join(errors))
