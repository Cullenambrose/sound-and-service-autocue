"""Shared correction reducer. Nudges are drafts; correct/set_here confirm truth.

Committed events use dataset-local sequences. Legacy timestamp ties preserve input
order (sorted file order when loading files); they cannot recover missing history.
Clears remove confirmed truth, and a later confirmation re-marks the cue.
"""
import json
import math
from pathlib import Path

from project_config import research_root

SLOTS = "ABCDEFGH"


def ordered_events(items):
    items = list(items)
    if any(not isinstance(item, dict) for item in items):
        raise ValueError("Corrections must contain event objects.")
    seen = {}
    unique = []
    for event in items:
        identity = event.get("event_id")
        if identity:
            if identity in seen:
                if event != seen[identity]:
                    raise ValueError("Conflicting copies of correction event " + identity)
                continue
            seen[identity] = event
        unique.append(event)
    sequenced = [event for event in unique if event.get("sequence") is not None]
    if sequenced:
        if len(sequenced) != len(unique):
            raise ValueError("Do not mix sequenced and legacy corrections in one dataset.")
        if any(type(e["sequence"]) is not int or e["sequence"] < 1 for e in unique):
            raise ValueError("Correction sequence must be a positive integer.")
        if len({e["sequence"] for e in unique}) != len(unique):
            raise ValueError("Conflicting correction sequences.")
        return sorted(unique, key=lambda e: e["sequence"])
    return sorted(unique, key=lambda e: e.get("saved_at", ""))


def events(paths=None):
    paths = sorted((research_root() / "corrections").glob("corrections*.json")) if paths is None else paths
    result = []
    for path in paths:
        with Path(path).open(encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, list):
            raise ValueError(f"Corrections must be an array: {path}")
        result.extend(data)
    return result


def latest_events(ev=None, dataset_id=None):
    items = events() if ev is None else list(ev)
    if dataset_id is not None:
        items = [e for e in items if e.get("dataset_id") == dataset_id]
    elif len({e.get("dataset_id") for e in items}) > 1:
        raise ValueError("Select a dataset_id before combining correction datasets.")
    last = {}
    for event in ordered_events(items):
        track, cue = event.get("track_hash") or event.get("track"), event.get("cue")
        if not isinstance(track, str) or not track or not isinstance(cue, str) or cue not in SLOTS:
            raise ValueError("Correction requires a track identity and an A-H cue.")
        if len(cue) != 1:
            raise ValueError("Correction cue must be a single A-H slot.")
        if track.isdigit():
            track = track.zfill(2)
        action = event.get("action")
        if action in ("correct", "set_here"):
            beat = event.get("to_beat")
            if isinstance(beat, bool) or not isinstance(beat, (float, int)) or not math.isfinite(beat):
                raise ValueError("Confirmed correction requires a finite to_beat.")
            last[(track, cue)] = event
        elif action == "clear":
            last[(track, cue)] = event
    return last


def truth_beats(ev=None, dataset_id=None):
    return {key: event["to_beat"] for key, event in latest_events(ev, dataset_id).items()
            if event["action"] != "clear"}


def truth_bars(nn, ev=None, cues="ABCDGH", dataset_id=None):
    nn = nn.zfill(2) if nn.isdigit() else nn
    return {cue: round(beat / 4) for (track, cue), beat in truth_beats(ev, dataset_id).items()
            if track == nn and cue in cues}


def marked_songs(ev=None, dataset_id=None):
    return sorted({track for track, _ in truth_beats(ev, dataset_id)})
