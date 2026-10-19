"""Configurable cue-map meanings, labels, and Serato-compatible colors."""
import copy


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
    "before_outro_16": {"label": "16 bars before outro", "source": "outro_start", "offset": -16},
    "before_outro_32": {"label": "32 bars before outro", "source": "outro_start", "offset": -32},
}

_VOCAL_DEFAULTS = {
    "A": ("intro_start", "INTRO -{bars}", "teal"),
    "B": ("start", "START", "green"),
    "C": ("before_first_chorus_exit_32", "EXIT -32", "purple"),
    "D": ("before_first_chorus_exit_16", "EXIT -16", "magenta"),
    "E": ("first_chorus_end", "EXIT/JUMP", "rose"),
    "F": ("last_chorus_start", "LAST CHORUS", "yellow"),
    "G": ("before_outro_32", "GO -32", "sky"),
    "H": ("outro_start", "OUTRO -{bars}", "orange"),
}
_DROP_DEFAULTS = {
    "A": ("intro_start", "INTRO -{bars}", "teal"),
    "B": ("start", "START", "green"),
    "C": ("before_first_drop_32", "-32 DROP", "purple"),
    "D": ("first_drop_start", "FIRST DROP", "magenta"),
    "E": ("before_last_drop_32", "-32 LAST DROP", "rose"),
    "F": ("last_drop_start", "LAST DROP", "yellow"),
    "G": ("before_outro_32", "GO -32", "sky"),
    "H": ("outro_start", "OUTRO -{bars}", "orange"),
}


def _map_defaults(values):
    return {
        slot: {"meaning": meaning, "label": label, "color": color, "enabled": True}
        for slot, (meaning, label, color) in values.items()
    }


DEFAULT_CUE_MAPS = {
    "version": 1,
    "mode": "AUTO",
    "maps": {"VOCAL": _map_defaults(_VOCAL_DEFAULTS), "DROP": _map_defaults(_DROP_DEFAULTS)},
}


def normalize_cue_maps(value=None):
    result = copy.deepcopy(DEFAULT_CUE_MAPS)
    if value is None:
        return result
    if not isinstance(value, dict):
        raise ValueError("Cue map settings must be an object.")
    mode = str(value.get("mode", result["mode"])).upper()
    if mode not in ("AUTO", "VOCAL", "DROP"):
        raise ValueError("Cue map mode must be Auto, Vocal, or Drop.")
    result["mode"] = mode
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
            meaning = candidate.get("meaning", result["maps"][map_type][slot]["meaning"])
            if not isinstance(meaning, str) or meaning not in MEANINGS:
                raise ValueError(f"Cue {slot} uses an unsupported meaning.")
            label = candidate.get("label", result["maps"][map_type][slot]["label"])
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                raise ValueError(f"Cue {slot} label must contain 1–80 characters.")
            if "{" in label.replace("{bars}", "") or "}" in label.replace("{bars}", ""):
                raise ValueError(f"Cue {slot} label may only use the {{bars}} token.")
            color = candidate.get("color", result["maps"][map_type][slot]["color"])
            if not isinstance(color, str) or color not in PALETTE_BY_ID:
                raise ValueError(f"Cue {slot} color must be selected from the Serato palette.")
            enabled = candidate.get("enabled", True)
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
    if "{bars}" not in template:
        return template
    if bars_kind == "to_start":
        count = max(0, context["start_bar"] - bar)
    elif bars_kind == "to_end":
        count = max(0, context["bar_count"] - bar)
    else:
        count = abs(MEANINGS[meaning]["offset"])
    return template.replace("{bars}", str(count))


def apply_cue_map(plan, settings, candidates, first_bar):
    settings = normalize_cue_maps(settings)
    map_type = plan["map_type"]
    slots = settings["maps"][map_type]
    context = {"start_bar": candidates["start"]["bar"], "bar_count": plan["bar_count"]}
    resolved = {}
    for slot, spec in slots.items():
        cue = plan["cues"][slot]
        color = PALETTE_BY_ID[spec["color"]]
        meaning = MEANINGS[spec["meaning"]]
        source = candidates.get(meaning["source"], {})
        bar = source.get("bar")
        if bar is not None:
            bar += meaning["offset"]
            if bar < first_bar or bar >= plan["bar_count"]:
                bar = None
        if not spec["enabled"]:
            bar = None
            cue.update({"confidence": 0.0, "reason": "disabled in cue map settings",
                        "flags": ["cue disabled by user"]})
        elif bar is None:
            cue.update({"confidence": 0.0, "reason": f"no evidence for {meaning['label'].casefold()}",
                        "flags": [f"{meaning['label']} unavailable"]})
        else:
            cue.update({
                "confidence": source.get("confidence", 0.0),
                "reason": source.get("reason", meaning["label"]),
                "flags": list(source.get("flags", [])),
            })
        cue.update({
            "name": _render_label(spec["label"], spec["meaning"], bar, context) if bar is not None
                    else spec["label"].replace("{bars}", "—"),
            "meaning": spec["meaning"],
            "color_id": color["id"],
            "color": color["metadata"],
            "display_color": color["display"],
            "bar": bar,
            "time_s": None if bar is None else round(
                plan["grid"]["t1"] + bar * plan["grid"]["bar_s"] -
                plan["grid"].get("offset_ms", 0) / 1000, 3),
        })
        cue["candidate_bars"] = [] if bar is None else (
            [candidate for candidate in range(bar - 2, bar + 3)
             if first_bar <= candidate < plan["bar_count"]]
            if meaning["offset"] == 0 and not (
                meaning["source"] == "last_drop_start" and len(plan.get("drops", [])) == 1
            ) else [bar]
        )
        resolved[slot] = {"meaning": spec["meaning"], "label": cue["name"],
                          "label_template": spec["label"], "color": color["metadata"],
                          "display_color": color["display"],
                          "enabled": spec["enabled"]}
    plan["cue_map"] = {
        "version": 1, "mode": settings["mode"], "resolved": resolved,
        "settings": copy.deepcopy(slots),
        "candidates": {
            key: {field: event.get(field) for field in ("bar", "confidence", "reason", "flags")}
            for key, event in candidates.items()
        },
    }
    return plan


def refresh_dynamic_labels(plan):
    start = plan.get("cues", {}).get("B", {}).get("bar")
    for slot, resolved in plan.get("cue_map", {}).get("resolved", {}).items():
        template = resolved.get("label_template", "")
        meaning = resolved.get("meaning")
        cue = plan.get("cues", {}).get(slot, {})
        if "{bars}" not in template or cue.get("bar") is None:
            continue
        context = {"start_bar": start or 0, "bar_count": plan.get("bar_count", 0)}
        cue["name"] = _render_label(template, meaning, cue["bar"], context)
        resolved["label"] = cue["name"]
