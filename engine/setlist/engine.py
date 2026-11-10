"""Pure shared ranking for Prep and Live; all candidates come from caller input."""
from collections import defaultdict
from collections.abc import Mapping
import json
from typing import Any, Dict, Iterable, Optional, Tuple

from library.matcher import MatchConfig, extract_version_tags, match_track, normalize_text
from library.model import Track

from .inputs import song_identity
from .phase import PhaseState, select_phase
from .scoring import SetlistSettings, score_candidate
from .types import (
    MustPlayConflict,
    Recommendation,
    Recommendations,
    SetlistContext,
    SongIdentity,
    SongRequest,
)


_MATCH_CONFIG = MatchConfig(min_confidence=0.6, fuzzy_threshold=0.8)


def _track_dict(track: Any) -> Optional[Dict[str, Any]]:
    if isinstance(track, Mapping):
        return dict(track)
    if isinstance(track, Track):
        return track.to_dict()
    converter = getattr(track, "to_dict", None)
    if callable(converter):
        converted = converter()
        return dict(converted) if isinstance(converted, Mapping) else None
    return None


def _file_key(track: Mapping[str, Any]) -> str:
    file_record = track.get("file")
    file_path = file_record.get("path") if isinstance(file_record, Mapping) else None
    version = track.get("version")
    if not version:
        _, version_info = extract_version_tags(str(track.get("title") or ""))
        version = "|".join(sorted(version_info.tags))
    identifiers = (
        file_path or track.get("path") or "",
        track.get("library_file_id") or track.get("file_id") or "",
        track.get("id") or track.get("track_id") or "",
        version or "",
    )
    if any(identifiers):
        return "\x1f".join(str(value) for value in identifiers)
    try:
        return json.dumps(track, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(sorted((str(key), repr(value)) for key, value in track.items()))


def _clean_match_track(track: Mapping[str, Any]) -> Dict[str, Any]:
    # match_track adds these private ranking fields; the recommendation keeps source metadata.
    return {key: value for key, value in track.items()
            if key not in {"_artist_sim", "_title_sim", "_version"}}


def _version_label(track: Mapping[str, Any]) -> str:
    explicit = track.get("version")
    if explicit:
        normalized, version = extract_version_tags(str(explicit))
        tags = version.tags or ([normalize_text(normalized)] if normalized else [])
        if tags:
            return "|".join(sorted(tags))
    _, version = extract_version_tags(str(track.get("title") or ""))
    return "|".join(sorted(version.tags))


def _request_version(request: SongRequest) -> str:
    if request.version:
        return _version_label({"version": request.version})
    _, version = extract_version_tags(request.title or "")
    return "|".join(sorted(version.tags))


def _sorted_library(library: Iterable[Any]):
    records = []
    for source in library:
        track = _track_dict(source)
        if track is None:
            continue
        identity = song_identity(track.get("title"), track.get("artist"))
        if identity is not None:
            records.append((identity, track))
    records.sort(key=lambda pair: (pair[0].artist, pair[0].title, _file_key(pair[1])))
    return records


def _resolve(request: SongRequest, library_records) -> Optional[Tuple[SongIdentity, Dict[str, Any]]]:
    if not isinstance(request.title, str) or not request.title.strip():
        return None
    matched = match_track(
        {"artist": request.artist or "", "title": request.title},
        [track for _, track in library_records],
        _MATCH_CONFIG,
    )
    if matched.status not in {"matched", "ambiguous"} or not matched.matched_track:
        return None
    selected = _clean_match_track(matched.matched_track)
    identity = song_identity(selected.get("title"), selected.get("artist"))
    requested_version = _request_version(request)
    if identity is not None and requested_version:
        versioned_matches = [
            track for candidate_identity, track in library_records
            if candidate_identity == identity and _version_label(track) == requested_version
        ]
        if not versioned_matches:
            return None
        selected = min(versioned_matches, key=_file_key)
        identity = song_identity(selected.get("title"), selected.get("artist"))
    return (identity, selected) if identity is not None else None


def _representatives(library_records):
    groups = defaultdict(list)
    for identity, track in library_records:
        groups[identity].append(track)
    representatives = {}
    for identity, tracks in groups.items():
        tracks.sort(key=_file_key)
        source = tracks[0]
        result = match_track(
            {"artist": source.get("artist") or "", "title": source.get("title") or ""},
            tracks,
            _MATCH_CONFIG,
        )
        selected = result.matched_track if result.status in {"matched", "ambiguous"} else None
        representatives[identity] = _clean_match_track(selected or source)
    return representatives


def _moment_key(value: Optional[str]) -> str:
    return " ".join((value or "").casefold().replace("_", " ").replace("-", " ").split())


def _cue_identities(context: SetlistContext, phase: PhaseState):
    relevant = set()
    moments = {_moment_key(moment) for moment in phase.cue_moments}
    for cue in context.song_cues:
        identity = song_identity(cue.title, cue.artist)
        if identity and _moment_key(cue.moment) in moments:
            relevant.add(identity)
    return relevant


def _request_identities(requests, library_records):
    resolved = []
    unresolved = []
    for request in requests:
        result = _resolve(request, library_records)
        if result is None:
            unresolved.append(request)
        else:
            resolved.append((request, result[0], result[1]))
    return resolved, unresolved


def _normal_identity_set(values) -> frozenset:
    return frozenset(
        identity for value in values if isinstance(value, SongIdentity)
        if (identity := song_identity(value.title, value.artist))
    )


def _track_identity(track):
    if track is None:
        return None
    return song_identity(track.get("title"), track.get("artist"))


def _resolve_hard_excludes(context, library_records):
    do_not_play, _ = _request_identities(context.do_not_play, library_records)
    do_not_play_identities = {identity for _, identity, _ in do_not_play}
    played = _normal_identity_set(context.already_played)
    placed = _normal_identity_set(context.already_placed)
    return do_not_play_identities | played | placed, do_not_play_identities, played, placed


def _file_tie_key(track):
    return _file_key(track)


def _rank(
    context: SetlistContext,
    library: Iterable[Any],
    now_playing: Any,
    settings: SetlistSettings,
    at,
    limit: Optional[int],
    *,
    prep: bool,
    phase_override: Optional[str] = None,
) -> Recommendations:
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative or None.")
    phase = select_phase(context, at, phase_override)
    library_records = _sorted_library(library)
    representatives = _representatives(library_records)
    excluded, do_not_play_identities, already_played, already_placed = _resolve_hard_excludes(
        context, library_records
    )
    resolved_must, unresolved_must = _request_identities(context.must_play, library_records)
    resolved_play_if, _ = _request_identities(context.play_if_possible, library_records)
    resolved_guests, _ = _request_identities(context.guest_requests, library_records)

    required_tracks = {}
    conflicts = []
    for request, identity, exact_track in resolved_must:
        if identity in excluded:
            if identity in do_not_play_identities:
                reason = "do_not_play"
            elif identity in already_played:
                reason = "already_played"
            else:
                reason = "already_placed"
            conflicts.append(MustPlayConflict(request, identity, reason))
            continue
        required_tracks.setdefault(identity, exact_track)

    if prep:
        must_identities = set(required_tracks)
    else:
        must_identities = {identity for _, identity, _ in resolved_must if identity not in excluded}
    play_if_identities = {identity for _, identity, _ in resolved_play_if if identity not in excluded}
    guest_identities = {identity for _, identity, _ in resolved_guests if identity not in excluded}
    cue_identities = _cue_identities(context, phase)
    cue_identities.difference_update(excluded)
    fillers = _normal_identity_set(context.dj_memory_floor_fillers)
    crate_identities = _normal_identity_set(context.crate_identities)

    current_track = _track_dict(now_playing) if now_playing is not None else None
    current_identity = _track_identity(current_track)
    ranked = []
    for identity, representative in representatives.items():
        if identity in excluded:
            continue
        selected = required_tracks.get(identity, representative) if prep else representative
        score, reasons = score_candidate(
            identity,
            selected,
            context,
            phase,
            settings,
            now_playing=current_track,
            playing_identity=current_identity,
            must_play=identity in must_identities,
            play_if_possible=identity in play_if_identities,
            relevant_song_cue=identity in cue_identities,
            guest_requested=identity in guest_identities,
            floor_filler=identity in fillers,
        )
        badges = []
        if identity in must_identities or identity in play_if_identities or identity in cue_identities:
            badges.append("requested")
        if identity in guest_identities:
            badges.append("customer")
        if identity in crate_identities:
            badges.append("in_crate")
        ranked.append(Recommendation(identity, selected, score, reasons, tuple(badges)))

    ranked.sort(key=lambda item: (-item.score, item.song_identity.artist,
                                  item.song_identity.title, _file_tie_key(item.track)))
    if prep:
        required = [item for item in ranked if item.song_identity in required_tracks]
        other = [item for item in ranked if item.song_identity not in required_tracks]
        if limit is None:
            selected_recommendations = required + other
        else:
            # A too-small Prep limit expands to preserve every eligible must-play.
            selected_recommendations = required + other[:max(0, limit - len(required))]
    else:
        selected_recommendations = ranked if limit is None else ranked[:limit]
    return Recommendations(selected_recommendations, unresolved_must, conflicts, phase.name)


def rank_next_song(
    ctx: SetlistContext,
    library,
    now_playing=None,
    *,
    settings: Optional[SetlistSettings] = None,
    at=None,
    limit: int = 10,
) -> Recommendations:
    """Rank the next local-library songs; this function performs no external I/O."""
    return _rank(ctx, library, now_playing, settings or SetlistSettings(), at, limit, prep=False)


def rank_prep_crate(
    ctx: SetlistContext,
    library,
    *,
    settings: Optional[SetlistSettings] = None,
    phase: Optional[str] = None,
    now_playing=None,
    limit: Optional[int] = None,
) -> Recommendations:
    """Rank Prep candidates, retaining every resolvable, eligible must-play.

    A limit smaller than the number of eligible must-plays expands to the must-play
    count; additional candidates fill any remaining slots. Duplicate requests for
    one normalized song identity are represented once, using the first resolved
    must-play's exact local file/version.
    """
    return _rank(ctx, library, now_playing, settings or SetlistSettings(), None,
                 limit, prep=True, phase_override=phase)
