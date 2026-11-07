"""Deterministic event-phase and open-dancing energy-stage selection."""
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional, Tuple

from library.event_sync_schema import get_subcrate_for_timeline

from .types import SetlistContext, TimelineEntry

ENERGY_TARGETS = {"warm_up": 0.40, "peak": 0.82, "last_dance": 0.52}
_PLANNER_PHASES = {
    "processional": "ceremony",
    "entrance": "ceremony",
    "first dance": "first_dance",
    "parent dances": "first_dance",
    "cake": "dinner",
    "cake cutting": "dinner",
    "last dance": "open_dancing",
}


def _phase_from_text(*values: Optional[str]) -> str:
    normalized_values = [
        " ".join(value.casefold().replace("_", " ").replace("-", " ").split())
        for value in values if isinstance(value, str)
    ]
    text = " ".join(normalized_values)
    for planner_kind, phase in _PLANNER_PHASES.items():
        if planner_kind in text:
            return phase
    for value in normalized_values:
        mapped = get_subcrate_for_timeline(value)
        if mapped == "Ceremony":
            return "ceremony"
        if mapped == "Cocktail":
            return "cocktail"
        if mapped == "Dinner":
            return "dinner"
        if mapped == "First Dance":
            return "first_dance"
        if mapped == "Open Dancing":
            return "open_dancing"
    if "ceremony" in text:
        return "ceremony"
    if "cocktail" in text:
        return "cocktail"
    if "dinner" in text:
        return "dinner"
    if "dance" in text:
        return "open_dancing"
    return "generic"


def _clock_seconds(value) -> Optional[float]:
    if isinstance(value, datetime):
        value = value.timetz()
    if isinstance(value, time):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed_datetime = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        parsed_time = parsed_datetime.timetz()
    except ValueError:
        try:
            parsed_time = time.fromisoformat(raw)
        except ValueError:
            parsed_time = None
            for pattern in ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S"):
                try:
                    parsed_time = datetime.strptime(raw, pattern).time()
                    break
                except ValueError:
                    continue
            if parsed_time is None:
                return None
    return parsed_time.hour * 3600 + parsed_time.minute * 60 + parsed_time.second


def _row_interval(row: TimelineEntry) -> Tuple[Optional[float], Optional[float]]:
    start = _clock_seconds(row.start_time)
    end = _clock_seconds(row.end_time)
    if start is not None and end is None and row.duration_minutes is not None:
        end = start + max(0, row.duration_minutes) * 60
    return start, end


def _active_row(rows, at_seconds):
    timed = []
    active = []
    for index, row in enumerate(rows):
        start, end = _row_interval(row)
        if start is None:
            continue
        timed.append((start, index, row, end))
        if at_seconds is None:
            continue
        adjusted_now = at_seconds
        adjusted_end = end
        if end is not None and end < start:
            adjusted_end += 24 * 3600
            if adjusted_now < start:
                adjusted_now += 24 * 3600
        if start <= adjusted_now and (adjusted_end is None or adjusted_now < adjusted_end):
            active.append((index, row))
    if active:
        return active[-1][1]
    if not timed:
        return None
    if at_seconds is None:
        return None
    future = [entry for entry in timed if entry[0] > at_seconds]
    if future:
        return min(future, key=lambda entry: (entry[0], entry[1]))[2]
    return max(timed, key=lambda entry: (entry[0], entry[1]))[2]


def _timed_energy_stage(row: Optional[TimelineEntry], at) -> Optional[str]:
    if row is None:
        return None
    start, end = _row_interval(row)
    now = _clock_seconds(at)
    if start is None or end is None or now is None:
        return None
    if end < start:
        end += 24 * 3600
        if now < start:
            now += 24 * 3600
    duration = end - start
    if duration <= 0:
        return None
    progress = min(1.0, max(0.0, (now - start) / duration))
    if progress < 1 / 3:
        return "warm_up"
    if progress < 2 / 3:
        return "peak"
    return "last_dance"


def select_phase(context: SetlistContext, at=None, override: Optional[str] = None):
    """Choose active phase; without usable timing, use the first recognized row in source order."""
    explicit = override or context.explicit_phase
    if explicit:
        phase = _phase_from_text(explicit)
        phase_rows = [
            candidate for candidate in context.event_timelines
            if _phase_from_text(candidate.kind, candidate.name, candidate.title) == phase
        ]
        row = _active_row(phase_rows, _clock_seconds(at))
        if row is None:
            row = next(iter(phase_rows), None)
    else:
        at_seconds = _clock_seconds(at)
        row = _active_row(context.event_timelines, at_seconds)
        if row is None:
            row = next((candidate for candidate in context.event_timelines
                        if _phase_from_text(candidate.kind, candidate.name, candidate.title) != "generic"), None)
        phase = (_phase_from_text(row.kind, row.name, row.title) if row is not None else "generic")

    stage = None
    if phase == "open_dancing":
        stage = (context.energy_stage or "").strip().casefold().replace("-", "_").replace(" ", "_")
        if stage not in ENERGY_TARGETS:
            stage = _timed_energy_stage(row, at)
    target = context.energy_target
    if target is not None:
        try:
            target = float(target)
            if not 0 <= target <= 1:
                target = None
        except (TypeError, ValueError):
            target = None
    if target is None and stage:
        target = ENERGY_TARGETS[stage]

    cue_moments = {
        "ceremony": ("processional", "entrance", "recessional"),
        "cocktail": ("cocktail", "cocktail hour"),
        "dinner": ("cake", "cake cutting", "cake_cutting"),
        "first_dance": ("first dance", "first_dance", "parent dances", "parent_dances"),
        "open_dancing": ("last dance", "last_dance", "last_song", "bouquet toss", "bouquet_toss",
                         "garter toss", "garter_toss"),
        "generic": (),
    }
    return PhaseState(phase, stage, target, cue_moments.get(phase, ()))


@dataclass(frozen=True)
class PhaseState:
    """Immutable phase result consumed by scoring and ranking."""
    name: str
    energy_stage: Optional[str]
    energy_target: Optional[float]
    cue_moments: Tuple[str, ...]
