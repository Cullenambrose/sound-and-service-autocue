"""Transparent rule-based score terms for local next-song recommendations."""
from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Optional, Tuple

from .phase import PhaseState
from .types import SetlistContext, SongIdentity

GENRES = (
    "Pop", "Hip Hop", "R&B", "Rock", "Dance / EDM", "House", "Latin", "Country",
    "Throwback", "Slow / Ballad",
)

_MAJOR_CAMELOT = {
    "ab": "1B", "g#": "1B", "eb": "2B", "d#": "2B", "bb": "3B", "a#": "3B",
    "f": "4B", "c": "5B", "g": "6B", "d": "7B", "a": "8B", "e": "9B",
    "b": "10B", "cb": "10B", "f#": "11B", "gb": "11B", "db": "12B", "c#": "12B",
}
_MINOR_CAMELOT = {
    "fm": "1A", "cm": "2A", "gm": "3A", "dm": "4A", "am": "5A", "em": "6A",
    "bm": "7A", "f#m": "8A", "gbm": "8A", "c#m": "9A", "dbm": "9A",
    "g#m": "10A", "abm": "10A", "d#m": "11A", "ebm": "11A", "a#m": "12A", "bbm": "12A",
}


@dataclass(frozen=True)
class SetlistSettings:
    """Weights are additive score points; hard exclusions are not configurable.

    bpm_window is a relative fraction around the playing tempo (0.08 = 8%).
    Half/double-time is off by default. Transition weights are normalized as
    outgoing probabilities. Energy values accept either 0..1 or 0..100.
    """
    must_play_weight: float = 100.0
    play_if_possible_weight: float = 30.0
    song_cue_weight: float = 25.0
    guest_request_weight: float = 35.0
    floor_filler_weight: float = 18.0
    markov_weight: float = 20.0
    bpm_weight: float = 12.0
    camelot_weight: float = 8.0
    energy_slope_weight: float = 10.0
    genre_continuity_weight: float = 6.0
    bpm_window: float = 0.08
    half_double_time: bool = False

    def __post_init__(self):
        if not math.isfinite(self.bpm_window) or self.bpm_window <= 0:
            raise ValueError("bpm_window must be a positive fraction.")
        for name in (
            "must_play_weight", "play_if_possible_weight", "song_cue_weight",
            "guest_request_weight", "floor_filler_weight", "markov_weight",
            "bpm_weight", "camelot_weight", "energy_slope_weight",
            "genre_continuity_weight",
        ):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite.")


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bpm(track: Mapping[str, Any]) -> Optional[float]:
    value = _number(track.get("bpm", track.get("tempo")))
    return value if value and value > 0 else None


def _energy(track: Mapping[str, Any]) -> Optional[float]:
    value = _number(track.get("energy"))
    if value is None:
        return None
    if 1 < value <= 100:
        value /= 100
    return min(1.0, max(0.0, value))


def _genre(track: Mapping[str, Any]) -> Optional[str]:
    raw = track.get("genre")
    if not isinstance(raw, str):
        return None
    return next((genre for genre in GENRES if genre.casefold() == raw.strip().casefold()), None)


def camelot_key(track: Mapping[str, Any]) -> Optional[str]:
    """Read an explicit Camelot key or convert common musical-key tag spellings."""
    for field in ("camelot", "camelot_key", "key"):
        raw = track.get(field)
        if not isinstance(raw, str):
            continue
        text = raw.strip().casefold()
        match = re.search(r"\b(1[0-2]|[1-9])\s*([ab])\b", text)
        if match:
            return f"{int(match.group(1))}{match.group(2).upper()}"
        normalized = re.sub(r"[\s_-]+", "", text).replace("major", "").replace("minor", "m")
        normalized = normalized.replace("♭", "b").replace("♯", "#")
        if normalized in _MAJOR_CAMELOT:
            return _MAJOR_CAMELOT[normalized]
        if normalized in _MINOR_CAMELOT:
            return _MINOR_CAMELOT[normalized]
    return None


def _bpm_fit(candidate: Mapping[str, Any], playing: Mapping[str, Any], settings: SetlistSettings):
    candidate_bpm, playing_bpm = _bpm(candidate), _bpm(playing)
    if candidate_bpm is None or playing_bpm is None:
        return None
    targets = [playing_bpm]
    if settings.half_double_time:
        targets.extend((playing_bpm / 2, playing_bpm * 2))
    best = None
    for target in targets:
        deviation = abs(candidate_bpm - target) / target
        if deviation <= settings.bpm_window:
            fit = 1.0 - deviation / settings.bpm_window
            best = fit if best is None else max(best, fit)
    return best if best is not None else 0.0


def _camelot_fit(candidate: Mapping[str, Any], playing: Mapping[str, Any]) -> Optional[float]:
    candidate_key, playing_key = camelot_key(candidate), camelot_key(playing)
    if candidate_key is None or playing_key is None:
        return None
    candidate_number, candidate_mode = int(candidate_key[:-1]), candidate_key[-1]
    playing_number, playing_mode = int(playing_key[:-1]), playing_key[-1]
    if candidate_key == playing_key:
        return 1.0
    if candidate_number == playing_number and candidate_mode != playing_mode:
        return 0.8
    if candidate_mode == playing_mode and (candidate_number - playing_number) % 12 in (1, 11):
        return 0.7
    return 0.0


def _energy_fit(candidate: Mapping[str, Any], playing: Optional[Mapping[str, Any]],
                phase: PhaseState) -> Optional[float]:
    candidate_energy = _energy(candidate)
    if candidate_energy is None or phase.energy_target is None:
        return None
    target_fit = max(0.0, 1.0 - abs(candidate_energy - phase.energy_target) / 0.6)
    if playing is None:
        return target_fit
    current_energy = _energy(playing)
    if current_energy is None:
        return target_fit
    desired_delta = {"warm_up": 0.12, "peak": 0.0, "last_dance": -0.12}.get(
        phase.energy_stage, 0.0
    )
    slope_fit = max(0.0, 1.0 - abs((candidate_energy - current_energy) - desired_delta) / 0.4)
    return target_fit * 0.5 + slope_fit * 0.5


def _markov_probability(identity: SongIdentity, playing_identity: Optional[SongIdentity],
                        context: SetlistContext) -> Optional[float]:
    if playing_identity is None:
        return None
    outgoing = context.played_next.get(playing_identity, {})
    if not outgoing:
        return None
    total = sum(max(0.0, _number(weight) or 0.0) for weight in outgoing.values())
    if total <= 0:
        return None
    weight = max(0.0, _number(outgoing.get(identity)) or 0.0)
    return weight / total if weight else 0.0


def score_candidate(
    identity: SongIdentity,
    track: Mapping[str, Any],
    context: SetlistContext,
    phase: PhaseState,
    settings: SetlistSettings,
    *,
    now_playing: Optional[Mapping[str, Any]],
    playing_identity: Optional[SongIdentity],
    must_play: bool,
    play_if_possible: bool,
    relevant_song_cue: bool,
    guest_requested: bool,
    floor_filler: bool,
    explain_song_cue: bool = True,
) -> Tuple[float, Tuple[str, ...]]:
    """Score one eligible track and explain every applied rule term."""
    score = 0.0
    reasons = []
    boosts = (
        (must_play, settings.must_play_weight, "Must-play request"),
        (play_if_possible, settings.play_if_possible_weight, "Play-if-possible request"),
        (relevant_song_cue, settings.song_cue_weight,
         f"Song cue fits {phase.name.replace('_', ' ')}" if explain_song_cue else None),
        (guest_requested, settings.guest_request_weight, "Live guest request"),
        (floor_filler, settings.floor_filler_weight, "DJ-memory floor-filler"),
    )
    for enabled, weight, reason in boosts:
        if enabled:
            score += weight
            if reason is not None:
                reasons.append(reason)

    if now_playing is not None:
        transition_probability = _markov_probability(identity, playing_identity, context)
        if transition_probability is not None:
            score += settings.markov_weight * transition_probability
            if transition_probability > 0:
                reasons.append("Played-next transition memory")

        bpm_fit = _bpm_fit(track, now_playing, settings)
        if bpm_fit is not None:
            score += settings.bpm_weight * bpm_fit
            reasons.append("BPM fits preferred transition window" if bpm_fit else
                           "BPM outside preferred window; still eligible")

        key_fit = _camelot_fit(track, now_playing)
        if key_fit is not None:
            score += settings.camelot_weight * key_fit
            reasons.append("Camelot-compatible key" if key_fit else "No close Camelot key match")

        playing_genre, candidate_genre = _genre(now_playing), _genre(track)
        if playing_genre and candidate_genre:
            if playing_genre == candidate_genre:
                score += settings.genre_continuity_weight
                reasons.append(f"Genre continuity: {candidate_genre}")
            else:
                score -= settings.genre_continuity_weight * 0.25
                reasons.append(f"Genre shift: {playing_genre} to {candidate_genre}")

    energy_fit = _energy_fit(track, now_playing, phase)
    if energy_fit is not None:
        score += settings.energy_slope_weight * energy_fit
        if phase.energy_stage:
            reasons.append(f"{phase.energy_stage.replace('_', ' ').title()} energy target")
        else:
            reasons.append("Energy fits phase target")

    if not reasons:
        reasons.append(f"Eligible local-library track for {phase.name.replace('_', ' ')} phase")
    return score, tuple(reasons)
