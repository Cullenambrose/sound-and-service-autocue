"""Pure, bounded whole-set planning over caller-supplied exact file records."""
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
import json
import math

from library.matcher import MatchConfig, extract_version_tags, rank_candidates

from .inputs import song_identity
from .phase import select_phase
from .scoring import SetlistSettings, score_candidate
from .types import SetlistContext


PLANNER_VERSION = "1.1"
MAX_TRACKS = 60
MAX_INPUT_TRACKS = 10_000
MAX_TARGET_MINUTES = 1440
MAX_TOLERANCE_MINUTES = 1440
MAX_CANDIDATES = 120
BEAM_WIDTH = 128
MAX_EXPANSIONS = 200_000
MAX_ALTERNATIVES = 3
MAX_PHASES = 12
PHASES = {"ceremony", "cocktail", "dinner", "first_dance", "open_dancing", "generic"}


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _file_id(value):
    return (type(value) is int or
            (type(value) is str and bool(value.strip())))


def _id_key(value):
    return json.dumps(value, ensure_ascii=False)


def validate_policy(policy):
    """Validate a strict policy and return an independent, default-filled dict.

    Invalid shape/types/ranges raise ValueError; valid but unsatisfiable constraints
    are diagnosed by plan_set instead. IDs are exact, non-boolean integers or
    nonblank strings; integer 1 and string "1" are different files.
    """
    allowed = {
        "target_minutes", "tolerance_minutes", "max_tracks", "bpm_min", "bpm_max",
        "clean_only", "must_include", "exclude", "pinned", "energy_target", "phases", "use_feedback",
    }
    if not isinstance(policy, dict) or set(policy) - allowed:
        raise ValueError("policy must be a dict containing only supported fields")
    for field in ("target_minutes", "tolerance_minutes", "max_tracks"):
        if field not in policy:
            raise ValueError(f"{field} is required")
    result = dict(policy)
    for field, minimum, maximum in (
        ("target_minutes", 0, MAX_TARGET_MINUTES),
        ("tolerance_minutes", 0, MAX_TOLERANCE_MINUTES),
        ("bpm_min", 0, 1000), ("bpm_max", 0, 1000),
        ("energy_target", 0, 1),
    ):
        if field not in result:
            continue
        value = result[field]
        if (not _number(value) or value < minimum or value > maximum
                or (field in {"target_minutes", "bpm_min", "bpm_max"} and value == 0)):
            raise ValueError(f"{field} must be finite and in the supported range")
    if type(result["max_tracks"]) is not int or not 1 <= result["max_tracks"] <= MAX_TRACKS:
        raise ValueError(f"max_tracks must be an integer in 1..{MAX_TRACKS}")
    if ("bpm_min" in result and "bpm_max" in result
            and result["bpm_min"] > result["bpm_max"]):
        raise ValueError("bpm_min cannot exceed bpm_max")
    result.setdefault("clean_only", False)
    if type(result["clean_only"]) is not bool:
        raise ValueError("clean_only must be boolean")
    if "use_feedback" in result and type(result["use_feedback"]) is not bool:
        raise ValueError("use_feedback must be boolean")
    for field in ("must_include", "exclude"):
        values = result.get(field, [])
        limit = MAX_TRACKS if field == "must_include" else MAX_INPUT_TRACKS
        if (not isinstance(values, list) or len(values) > limit
                or any(not _file_id(value) for value in values)
                or len(set(values)) != len(values)):
            raise ValueError(f"{field} must be a bounded list of unique exact file IDs")
        result[field] = sorted(values, key=_id_key)
    pins = result.get("pinned", [])
    if not isinstance(pins, list) or len(pins) > MAX_TRACKS:
        raise ValueError("pinned must be a list of at most 60 pins")
    normalized = []
    for pin in pins:
        if (not isinstance(pin, dict) or set(pin) != {"position", "file_id"}
                or type(pin["position"]) is not int
                or not 0 <= pin["position"] < result["max_tracks"]
                or not _file_id(pin["file_id"])):
            raise ValueError("each pin needs a valid 0-based position and exact file_id")
        normalized.append(dict(pin))
    result["pinned"] = sorted(normalized, key=lambda pin: (pin["position"], _id_key(pin["file_id"])))
    if "phases" in result:
        phases = result["phases"]
        if not isinstance(phases, list) or not 1 <= len(phases) <= MAX_PHASES:
            raise ValueError(f"phases must contain 1..{MAX_PHASES} ordered phase policies")
        normalized_phases = []
        for phase in phases:
            if (not isinstance(phase, dict) or set(phase) - {
                    "phase", "label", "target_minutes", "tolerance_minutes",
                    "bpm_min", "bpm_max", "clean_only", "energy_target", "must_include"}
                    or not isinstance(phase.get("phase"), str) or phase["phase"] not in PHASES):
                raise ValueError("each phase needs a supported phase and only phase-policy fields")
            label = phase.get("label", phase["phase"])
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                raise ValueError("phase label must be nonblank text of at most 80 characters")
            phase_policy = validate_policy({
                key: value for key, value in phase.items() if key not in {"phase", "label"}
            } | {"max_tracks": result["max_tracks"]})
            normalized_phases.append({
                key: value for key, value in phase_policy.items()
                if key not in {"max_tracks", "exclude", "pinned"}
            } | {"phase": phase["phase"], "label": label.strip()})
        if sum((Fraction(str(phase["target_minutes"])) for phase in normalized_phases),
               Fraction(0)) != Fraction(str(result["target_minutes"])):
            raise ValueError("phase targets must sum exactly to target_minutes")
        result["phases"] = normalized_phases
    return result


def _duration(track):
    value = track.get("duration_s")
    return Fraction(str(value)) if _number(value) and value > 0 else None


def _banned(track, policy):
    if track["id"] in policy.get("exclude", []):
        return "excluded"
    if _duration(track) is None:
        return "unknown_duration"
    if "bpm_min" in policy or "bpm_max" in policy:
        bpm = track.get("bpm")
        if not _number(bpm) or bpm <= 0:
            return "unknown_bpm"
        if bpm < policy.get("bpm_min", 0) or bpm > policy.get("bpm_max", math.inf):
            return "bpm_out_of_bounds"
    if policy["clean_only"]:
        versions = [extract_version_tags(track[field] or "")[1]
                    for field in ("title", "version")]
        if any(version.is_dirty for version in versions):
            return "dirty"
        if not any(version.is_clean for version in versions):
            return "clean_unconfirmed"
    return None


def _records(tracks):
    if isinstance(tracks, (str, bytes, Mapping)):
        raise ValueError("tracks must be an iterable of exact file mappings")
    try:
        tracks = iter(tracks)
    except TypeError as exc:
        raise ValueError("tracks must be an iterable of exact file mappings") from exc
    records = []
    ids = set()
    for track in tracks:
        if len(records) >= MAX_INPUT_TRACKS:
            raise ValueError(f"tracks exceeds {MAX_INPUT_TRACKS} input records")
        if not isinstance(track, Mapping):
            raise ValueError("each track must be a mapping")
        record = dict(track)
        if not {"id", "title", "artist", "version", "path"} <= record.keys():
            raise ValueError("each track needs id, title, artist, version and path")
        if not _file_id(record["id"]) or record["id"] in ids:
            raise ValueError("track IDs must be valid and unique")
        if (any(not isinstance(record[field], str) or not record[field].strip()
                for field in ("title", "artist", "path"))
                or (record["version"] is not None and not isinstance(record["version"], str))
                or not song_identity(record["title"], record["artist"]).title):
            raise ValueError("tracks need nonblank identity/path and string-or-null version")
        try:
            json.dumps(record, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("track records must be finite JSON data") from exc
        ids.add(record["id"])
        records.append(record)
    return sorted(records, key=lambda record: _id_key(record["id"]))


@dataclass(frozen=True)
class _State:
    path: tuple
    used: frozenset
    duration: Fraction
    score: float
    reasons: tuple
    phase_index: int = 0
    phase_duration: Fraction = Fraction(0)
    phase_ends: tuple = ()


def plan_set(tracks, policy):
    """Return feasible exact-file alternatives, or explicit conflict/search evidence.

    No event interpretation, filesystem access, clock, random source, or writes.
    The caller must prefilter event scope and song-level/event prohibitions.
    """
    policy = validate_policy(policy)
    records = _records(tracks)
    target = Fraction(str(policy["target_minutes"])) * 60
    tolerance = Fraction(str(policy["tolerance_minutes"])) * 60
    lower, upper = max(Fraction(0), target - tolerance), target + tolerance
    diagnostics = {
        "status": None, "search_complete": True, "truncated_by": [],
        "input_count": len(records), "eligible_count": 0, "candidate_count": 0,
        "filtered": {}, "expansions": 0,
        "limits": {"input_tracks": MAX_INPUT_TRACKS, "candidates": MAX_CANDIDATES,
                   "beam_width": BEAM_WIDTH, "expansions": MAX_EXPANSIONS,
                   "alternatives": MAX_ALTERNATIVES, "max_tracks": MAX_TRACKS},
    }
    unresolved = []

    def finish(alternatives):
        diagnostics["search_complete"] = not diagnostics["truncated_by"]
        diagnostics["status"] = (
            "feasible" if alternatives else
            "search_exhausted" if diagnostics["truncated_by"] else "infeasible"
        )
        return {"planner_version": PLANNER_VERSION, "feasible": bool(alternatives),
                "alternatives": alternatives, "diagnostics": diagnostics,
                "unresolved": unresolved}

    def conflict(code, **details):
        unresolved.append({"code": code, **details})

    by_id = {record["id"]: record for record in records}
    filtered = {record["id"]: _banned(record, policy) for record in records}
    diagnostics["filtered"] = dict(sorted(Counter(
        reason for reason in filtered.values() if reason
    ).items()))
    eligible = [record for record in records if filtered[record["id"]] is None]
    diagnostics["eligible_count"] = len(eligible)
    phases = policy.get("phases", [policy | {"phase": "generic", "label": "Set"}])
    phase_bounds = [
        (max(Fraction(0), Fraction(str(phase["target_minutes"])) * 60 -
             Fraction(str(phase["tolerance_minutes"])) * 60),
         (Fraction(str(phase["target_minutes"])) +
          Fraction(str(phase["tolerance_minutes"]))) * 60)
        for phase in phases
    ]
    required = (set(policy["must_include"]) | {pin["file_id"] for pin in policy["pinned"]}
                | {file_id for phase in phases for file_id in phase["must_include"]})
    phase_required = {}
    if "phases" in policy:
        for phase_index, phase in enumerate(phases):
            for file_id in phase["must_include"]:
                if file_id in phase_required:
                    conflict("file_required_in_multiple_phases", file_id=file_id)
                phase_required[file_id] = phase_index
                if file_id in by_id and _banned(by_id[file_id], phase):
                    conflict("phase_required_file_ineligible", phase_index=phase_index,
                             file_id=file_id, reason=_banned(by_id[file_id], phase))
    pins = {}
    pin_positions = {}
    for pin in policy["pinned"]:
        position, file_id = pin["position"], pin["file_id"]
        if position in pins and pins[position] != file_id:
            conflict("pin_position_conflict", position=position)
        if file_id in pin_positions and pin_positions[file_id] != position:
            conflict("file_pinned_twice", file_id=file_id)
        pins[position] = file_id
        pin_positions[file_id] = position
    required_identities = {}
    for file_id in sorted(required, key=_id_key):
        if file_id not in by_id:
            conflict("required_file_missing", file_id=file_id)
            continue
        if filtered[file_id]:
            conflict("required_file_ineligible", file_id=file_id, reason=filtered[file_id])
        record = by_id[file_id]
        identity = song_identity(record["title"], record["artist"])
        if identity in required_identities and required_identities[identity] != file_id:
            conflict("required_song_repeated", file_ids=sorted(
                [required_identities[identity], file_id], key=_id_key))
        required_identities[identity] = file_id
    if len(required) > policy["max_tracks"]:
        conflict("required_capacity", required_count=len(required))
    if len(phases) > policy["max_tracks"]:
        conflict("phase_capacity", phase_count=len(phases))
    if unresolved:
        return finish([])

    # Other edits of a required song cannot ever satisfy its exact-file obligation.
    eligible = [record for record in eligible
                if required_identities.get(song_identity(record["title"], record["artist"]),
                                           record["id"]) == record["id"]]
    min_count = max(len(phases), max(pins, default=-1) + 1, len(required))
    groups = defaultdict(list)
    for record in eligible:
        groups[song_identity(record["title"], record["artist"])].append(record)
    maxima = sorted((max(_duration(record) for record in group)
                     for group in groups.values()), reverse=True)
    if (len(groups) < min_count or sum(maxima[:policy["max_tracks"]], Fraction(0)) < lower
            or sum((_duration(by_id[file_id]) for file_id in required), Fraction(0)) > upper):
        conflict("duration_or_capacity_impossible")
        return finish([])

    contexts = [SetlistContext(explicit_phase=phase["phase"],
                              energy_target=phase.get("energy_target", policy.get("energy_target")))
                for phase in phases]
    scoring_phases = [select_phase(context) for context in contexts]
    settings = SetlistSettings()

    def score(record, previous, phase_index=0):
        points, reasons = score_candidate(
            song_identity(record["title"], record["artist"]), record, contexts[phase_index],
            scoring_phases[phase_index], settings,
            now_playing=previous,
            playing_identity=(song_identity(previous["title"], previous["artist"])
                              if previous is not None else None),
            must_play=False, play_if_possible=False, relevant_song_cue=False,
            guest_requested=False, floor_filler=False,
        )
        rating = record.get("dj_rating", 0)
        if policy.get("use_feedback") and type(rating) is int and rating in (-1, 1):
            return points + rating * 5, tuple(reasons) + ("Explicit DJ preference: " + str(rating),)
        return points, reasons

    preferences = {}
    for group in groups.values():
        representative = min(group, key=lambda record: _id_key(record["id"]))
        ranked = rank_candidates(group, representative["artist"], representative["title"],
                                 MatchConfig())
        preferences.update((record["id"], index) for index, (record, _) in enumerate(ranked))
    eligible.sort(key=lambda record: (-max(score(record, None, index)[0] for index in range(len(phases))),
                                     preferences[record["id"]],
                                     _id_key(record["id"])))
    if len(eligible) > MAX_CANDIDATES:
        diagnostics["truncated_by"].append("candidate_limit")
        selected = [record for record in eligible if record["id"] in required]
        other = [record for record in eligible if record["id"] not in required]
        slots = MAX_CANDIDATES - len(selected)
        selected.extend(other[:slots // 2])
        remaining = sorted(other[slots // 2:],
                           key=lambda record: (_duration(record), _id_key(record["id"])))
        slots = MAX_CANDIDATES - len(selected)
        selected.extend(remaining[index * len(remaining) // slots] for index in range(slots))
        eligible = selected
    candidates = sorted(eligible, key=lambda record: _id_key(record["id"]))
    diagnostics["candidate_count"] = len(candidates)
    identities = [song_identity(record["title"], record["artist"]) for record in candidates]
    durations = [_duration(record) for record in candidates]
    index_by_id = {record["id"]: index for index, record in enumerate(candidates)}
    required_indices = frozenset(index_by_id[file_id] for file_id in required)
    pin_indices = {position: index_by_id[file_id] for position, file_id in pins.items()}
    reserved = {index_by_id[file_id]: position for file_id, position in pin_positions.items()}
    max_duration = max(durations, default=Fraction(0))
    # Reserve enough breadth budget to reach the final requested position.
    horizon_choices = sum(1 if position in pin_indices else len(candidates)
                          for position in range(policy["max_tracks"]))
    if len(phases) > 1:
        horizon_choices *= 2
    beam_width = min(BEAM_WIDTH, max(1, MAX_EXPANSIONS // max(1, horizon_choices)))
    diagnostics["limits"]["beam_width"] = beam_width
    score_cache = {}
    found = {}
    beam = [_State((), frozenset(), Fraction(0), 0.0, ())]
    budget_exhausted = False

    def order(state):
        return (abs(state.duration - target), -state.score, state.path, state.phase_ends)

    def phase_options(state):
        yield state.phase_index, state.phase_duration, state.phase_ends
        phase_lower, phase_upper = phase_bounds[state.phase_index]
        start = state.phase_ends[-1] if state.phase_ends else 0
        pending = set(phases[state.phase_index]["must_include"]) - {
            candidates[index]["id"] for index in state.path[start:]
        }
        if (state.phase_index + 1 < len(phases) and len(state.path) > start and not pending
                and phase_lower <= state.phase_duration <= phase_upper):
            yield state.phase_index + 1, Fraction(0), state.phase_ends + (len(state.path),)

    for position in range(policy["max_tracks"]):
        next_states = []
        expanded = [(state, phase_index, phase_duration, phase_ends)
                    for state in beam
                    for phase_index, phase_duration, phase_ends in phase_options(state)]
        for state, phase_index, phase_duration, phase_ends in expanded:
            choices = ([pin_indices[position]] if position in pin_indices
                       else range(len(candidates)))
            for index in choices:
                if diagnostics["expansions"] >= MAX_EXPANSIONS:
                    budget_exhausted = True
                    break
                diagnostics["expansions"] += 1
                if (identities[index] in state.used
                        or reserved.get(index, position) != position
                        or (candidates[index]["id"] in phase_required
                            and phase_required[candidates[index]["id"]] != phase_index)
                        or ("phases" in policy and _banned(candidates[index], phases[phase_index]))):
                    continue
                duration = state.duration + durations[index]
                local_duration = phase_duration + durations[index]
                path = state.path + (index,)
                pending = required_indices.difference(path)
                slots_left = policy["max_tracks"] - len(path)
                if (duration > upper or local_duration > phase_bounds[phase_index][1]
                        or slots_left < len(phases) - phase_index - 1
                        or len(pending) > slots_left
                        or duration + max_duration * slots_left < lower
                        or duration + sum((durations[i] for i in pending), Fraction(0)) > upper):
                    continue
                previous = state.path[-1] if state.path else None
                cache_key = (phase_index, previous, index)
                if cache_key not in score_cache:
                    score_cache[cache_key] = score(
                        candidates[index], candidates[previous] if previous is not None else None,
                        phase_index)
                points, reasons = score_cache[cache_key]
                child = _State(path, state.used | {identities[index]}, duration,
                               state.score + points, state.reasons + (reasons,),
                               phase_index, local_duration, phase_ends)
                if (len(path) >= min_count and not pending and lower <= duration <= upper
                        and phase_index == len(phases) - 1
                        and phase_bounds[phase_index][0] <= local_duration):
                    found[(path, phase_ends)] = child
                    if len(found) > MAX_ALTERNATIVES:
                        del found[max(found, key=lambda key: order(found[key]))]
                if slots_left and duration < upper:
                    next_states.append(child)
            if budget_exhausted:
                break
        if budget_exhausted:
            diagnostics["truncated_by"].append("expansion_limit")
            break
        next_states.sort(key=lambda state: (
            len(required_indices.difference(state.path)),
            max(Fraction(0), lower - state.duration),
            -state.score, state.path, state.phase_ends,
        ))
        if len(next_states) > beam_width and "beam_width" not in diagnostics["truncated_by"]:
            diagnostics["truncated_by"].append("beam_width")
        beam = next_states[:beam_width]
        if not beam:
            break

    alternatives = []
    for state in sorted(found.values(), key=order):
        chosen = [candidates[index] for index in state.path]
        # Recheck the public contract independently of search heuristics.
        chosen_ids = {record["id"] for record in chosen}
        chosen_identities = {song_identity(record["title"], record["artist"])
                             for record in chosen}
        if (not 1 <= len(chosen) <= policy["max_tracks"]
                or len(chosen_ids) != len(chosen) or len(chosen_identities) != len(chosen)
                or not required <= chosen_ids
                or any(position >= len(chosen) or chosen[position]["id"] != file_id
                       for position, file_id in pins.items())
                or any(_banned(record, policy) for record in chosen)
                or not lower <= sum((_duration(record) for record in chosen), Fraction(0)) <= upper):
            raise AssertionError("planner produced an invalid alternative")
        alternatives.append({
            "tracks": chosen, "duration_s": float(state.duration),
            "delta_s": float(state.duration - target), "score": state.score,
            "reasons": ["All hard constraints satisfied; duration uses full file lengths.",
                        "Ordered by duration fit, then shared transition/energy score."],
            "track_reasons": [
                {"file_id": record["id"], "reasons": list(reasons)}
                for record, reasons in zip(chosen, state.reasons)
            ],
        })
        if "phases" in policy:
            schedule = []
            start = 0
            elapsed = Fraction(0)
            for phase_index, end in enumerate(state.phase_ends + (len(chosen),)):
                phase_tracks = chosen[start:end]
                phase_policy = phases[phase_index]
                duration = sum((_duration(record) for record in phase_tracks), Fraction(0))
                if (not phase_tracks or not phase_bounds[phase_index][0] <= duration <= phase_bounds[phase_index][1]
                        or any(_banned(record, phase_policy) for record in phase_tracks)
                        or not set(phase_policy["must_include"]) <= {record["id"] for record in phase_tracks}):
                    raise AssertionError("planner produced an invalid phase")
                schedule.append({
                    "phase_index": phase_index, "phase": phase_policy["phase"],
                    "label": phase_policy["label"], "start_position": start, "end_position": end,
                    "start_s": float(elapsed), "end_s": float(elapsed + duration),
                    "duration_s": float(duration),
                    "delta_s": float(duration - Fraction(str(phase_policy["target_minutes"])) * 60),
                    "energy_target": scoring_phases[phase_index].energy_target,
                    "file_ids": [record["id"] for record in phase_tracks],
                })
                start = end
                elapsed += duration
            alternatives[-1]["phases"] = schedule
    if not alternatives:
        conflict("search_exhausted" if diagnostics["truncated_by"] else "no_feasible_set")
    return finish(alternatives)
