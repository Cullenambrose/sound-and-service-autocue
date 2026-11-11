"""Pure shared ranking for Prep and Live; all candidates come from caller input."""
from collections import defaultdict
from collections.abc import Mapping
import json
from typing import Any, Dict, Iterable, Optional, Tuple

from library.matcher import (
    MatchConfig, extract_version_tags, match_track, normalize_text, rank_candidates,
)
from library.model import Track

from .inputs import song_identity
from .phase import PhaseState, select_phase
from .scoring import SetlistSettings, score_candidate
from .types import (
    CapacityOverflow,
    DoNotPlayDiagnostic,
    MustPlayConflict,
    Recommendation,
    Recommendations,
    RequestConflict,
    RequestDiagnostic,
    SetlistContext,
    SongIdentity,
    SongRequest,
    VersionConflict,
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
    try:
        metadata = json.dumps(track, ensure_ascii=False, sort_keys=True, default=str,
                              separators=(",", ":"))
    except (TypeError, ValueError):
        metadata = repr(sorted((str(key), repr(value)) for key, value in track.items()))
    # Identifiers lead the key; complete metadata breaks identifier collisions deterministically.
    return json.dumps(identifiers, ensure_ascii=False, default=str) + "\x1f" + metadata


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


def _match(request: SongRequest, records):
    confirmed_file_id = request.metadata.get("confirmed_file_id")
    if confirmed_file_id:
        selected = [(identity, track) for identity, track in records
                    if track.get("id") == confirmed_file_id]
        if (len(selected) != 1
                or selected[0][0] != song_identity(request.title, request.artist)):
            raise ValueError("Confirmed file must uniquely belong to the requested song identity.")
        records = selected
    return match_track(
        {"artist": request.artist or "", "title": request.title or "",
         "version": request.version or ""},
        [track for _, track in records],
        _MATCH_CONFIG,
    )


def _candidate_identity(track):
    return song_identity(track.get("title"), track.get("artist"))


def _resolve_request(request: SongRequest, category: str, library_records):
    """Resolve song identity first, then select an exact local playback version."""
    matched = _match(request, library_records)
    alternatives = tuple(dict(item) for item in matched.candidates)
    if matched.status != "matched" or not matched.matched_track:
        return None, RequestDiagnostic(
            category, request, matched.status, matched.confidence, alternatives,
            reason=matched.reason, match_status=matched.status,
        )

    selected = _clean_match_track(matched.matched_track)
    identity = _candidate_identity(selected)
    if identity is None:
        return None, RequestDiagnostic(
            category, request, matched.status, matched.confidence, alternatives,
            reason=matched.reason, match_status=matched.status,
        )

    version = _request_version(request)
    confirmed_file_id = request.metadata.get("confirmed_file_id")
    if confirmed_file_id:
        confirmed = [
            track for candidate_identity, track in library_records
            if candidate_identity == identity and track.get("id") == confirmed_file_id
            and (not version or _version_label(track) == version)
        ]
        if len(confirmed) != 1:
            raise ValueError("Confirmed file must uniquely belong to the resolved song and version.")
        selected = confirmed[0]
        diagnostic = RequestDiagnostic(category, request, matched.status, matched.confidence,
                                       alternatives, identity, matched.reason, matched.status)
        return (identity, selected, version), diagnostic

    if version:
        matching_versions = [
            track for candidate_identity, track in library_records
            if candidate_identity == identity and _version_label(track) == version
        ]
        if not matching_versions:
            return None, RequestDiagnostic(
                category, request, "version_unavailable", matched.confidence,
                alternatives, identity,
                f"Requested version {version!r} is not available for the resolved song.",
                matched.status,
            )
        selected = rank_candidates(
            matching_versions, request.artist or "", request.title or "",
            _MATCH_CONFIG, request.version or "",
        )[0][0]
        diagnostic = RequestDiagnostic(category, request, matched.status, matched.confidence,
                                       alternatives, identity, matched.reason, matched.status)
        return (identity, selected, version), diagnostic

    diagnostic = RequestDiagnostic(category, request, matched.status, matched.confidence,
                                   alternatives, identity, matched.reason, matched.status)
    return (identity, selected, "",), diagnostic


def _representatives(library_records):
    groups = defaultdict(list)
    for identity, track in library_records:
        groups[identity].append(track)
    representatives = {}
    for identity, tracks in groups.items():
        tracks.sort(key=_file_key)
        source = tracks[0]
        if len(tracks) == 1:
            representatives[identity] = source
            continue
        # A library representative's title must not act as a requested playback version.
        title, _ = extract_version_tags(source.get("title") or "")
        result = match_track(
            {"artist": source.get("artist") or "", "title": title},
            tracks,
            _MATCH_CONFIG,
        )
        selected = result.matched_track if result.status in {"matched", "ambiguous"} else None
        representatives[identity] = _clean_match_track(selected or source)
    return representatives


def _moment_key(value: Optional[str]) -> str:
    return " ".join((value or "").casefold().replace("_", " ").replace("-", " ").split())


def _cue_requests(context: SetlistContext, phase: PhaseState):
    moments = {_moment_key(moment) for moment in phase.cue_moments}
    return tuple(
        SongRequest(title=cue.title, artist=cue.artist, version=cue.version,
                    moment=cue.moment, metadata=cue.metadata)
        for cue in context.song_cues if _moment_key(cue.moment) in moments
    )


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
    do_not_play_identities = set()
    diagnostics = []
    for request in context.do_not_play:
        requested = song_identity(request.title, None)
        if requested is None:
            diagnostics.append(DoNotPlayDiagnostic(request, (), not bool(request.artist)))
            continue
        artist_unspecified = not isinstance(request.artist, str) or not request.artist.strip()
        requested_artist = song_identity("artist", request.artist).artist if not artist_unspecified else None
        matches = tuple(sorted({
            identity for identity, _ in library_records
            if identity.title == requested.title
            and (artist_unspecified or identity.artist == requested_artist)
        }))
        do_not_play_identities.update(matches)
        diagnostics.append(DoNotPlayDiagnostic(request, matches, artist_unspecified))
    played = _normal_identity_set(context.already_played)
    placed = _normal_identity_set(context.already_placed)
    return (do_not_play_identities | played | placed, do_not_play_identities,
            played, placed, diagnostics)


def _diagnostic_alternative_identities(diagnostic):
    identities = set()
    for track in diagnostic.alternatives:
        identity = _candidate_identity(track)
        if identity:
            identities.add(identity)
    if diagnostic.identity:
        identities.add(diagnostic.identity)
    return identities


def _resolution_for_category(requests, category, library_records):
    resolved = []
    diagnostics = []
    for request in requests:
        result, diagnostic = _resolve_request(request, category, library_records)
        diagnostics.append(diagnostic)
        if result:
            resolved.append((request, *result))
    return resolved, diagnostics


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
    excluded, do_not_play_identities, already_played, already_placed, dnp_diagnostics = (
        _resolve_hard_excludes(context, library_records)
    )

    positive_requests = [
        ("must_play", context.must_play),
        ("song_cue", _cue_requests(context, phase)),
        ("play_if_possible", context.play_if_possible),
    ]
    if not prep:
        positive_requests.append(("guest_request", context.guest_requests))
    resolved_by_category = {}
    resolution_diagnostics = []
    for category, requests in positive_requests:
        resolved_by_category[category], diagnostics = _resolution_for_category(
            requests, category, library_records
        )
        resolution_diagnostics.extend(diagnostics)

    request_conflicts = []
    must_conflicts = []
    for diagnostic in resolution_diagnostics:
        candidate_identities = (
            {diagnostic.identity}
            if diagnostic.match_status == "matched" and diagnostic.identity
            else _diagnostic_alternative_identities(diagnostic)
        )
        for identity in sorted(candidate_identities.intersection(excluded)):
            if identity in do_not_play_identities:
                reason = "do_not_play"
            elif identity in already_played:
                reason = "already_played"
            else:
                reason = "already_placed"
            request_conflicts.append(RequestConflict(
                diagnostic.category, diagnostic.request, identity, reason
            ))
            if diagnostic.category == "must_play":
                must_conflicts.append(MustPlayConflict(diagnostic.request, identity, reason))

    def positive_version_groups():
        groups = defaultdict(list)
        for diagnostic in resolution_diagnostics:
            version = _request_version(diagnostic.request)
            if version and diagnostic.identity:
                groups[diagnostic.identity].append((diagnostic.request, version))
        return groups

    version_requests = positive_version_groups()
    selected_tracks = {}
    selected_versions = {}
    categories = ("must_play", "song_cue", "play_if_possible", "guest_request")
    for identity in representatives:
        if identity in excluded:
            continue
        for category in categories:
            if prep and category == "guest_request":
                continue
            entries = [entry for entry in resolved_by_category.get(category, ())
                       if entry[1] == identity]
            if category == "song_cue":
                entries = [entry for entry in entries if entry[3]]
            if not entries:
                continue
            versioned_entries = [entry for entry in entries if entry[3]]
            if versioned_entries:
                chosen_version = min(entry[3] for entry in versioned_entries)
                chosen_tracks = [entry[2] for entry in versioned_entries
                                 if entry[3] == chosen_version]
                selected_tracks[identity] = min(chosen_tracks, key=_file_key)
                selected_versions[identity] = chosen_version
            else:
                selected_tracks[identity] = min((entry[2] for entry in entries), key=_file_key)
                selected_versions[identity] = _version_label(selected_tracks[identity])
            break

    version_conflicts = []
    for identity, entries in sorted(version_requests.items()):
        requested_versions = tuple(sorted({version for _, version in entries}))
        if len(requested_versions) > 1:
            version_conflicts.append(VersionConflict(
                identity,
                tuple(request for request, _ in sorted(
                    entries, key=lambda item: (item[1], item[0].title or "", item[0].artist or "")
                )),
                requested_versions,
                selected_versions.get(identity) or None,
            ))

    def request_matches_selected(entry, identity):
        request, entry_identity, _track, version = entry
        if entry_identity != identity:
            return False
        return not version or version == selected_versions.get(identity, "")

    applicable_by_category = {}
    for category in categories:
        if prep and category == "guest_request":
            continue
        applicable_by_category[category] = {
            identity for identity in representatives if identity not in excluded
            and any(request_matches_selected(entry, identity)
                    for entry in resolved_by_category.get(category, ()))
        }

    for diagnostic in resolution_diagnostics:
        if (diagnostic.status != "matched" or diagnostic.identity in excluded
                or not diagnostic.identity):
            continue
        requested_version = _request_version(diagnostic.request)
        if requested_version and selected_versions.get(diagnostic.identity) != requested_version:
            request_conflicts.append(RequestConflict(
                diagnostic.category, diagnostic.request, diagnostic.identity,
                "version_conflict_not_selected",
            ))
            if diagnostic.category == "must_play":
                must_conflicts.append(MustPlayConflict(
                    diagnostic.request, diagnostic.identity, "version_conflict_not_selected"
                ))

    must_identities = applicable_by_category.get("must_play", set())
    play_if_identities = applicable_by_category.get("play_if_possible", set())
    guest_identities = applicable_by_category.get("guest_request", set())
    required_tracks = {
        identity: selected_tracks.get(identity, representatives[identity])
        for identity in must_identities
    } if prep else {}

    unresolved_must = [
        diagnostic.request for diagnostic in resolution_diagnostics
        if diagnostic.category == "must_play"
        and (diagnostic.status != "matched"
             or diagnostic.identity not in must_identities
             or diagnostic.identity in excluded)
    ]
    # Timeline relevance is song-level, even when its exact version is unavailable
    # or loses to a higher-priority request. Diagnostics report version fulfillment.
    cue_identities = {
        diagnostic.identity for diagnostic in resolution_diagnostics
        if diagnostic.category == "song_cue" and diagnostic.match_status == "matched"
        and diagnostic.identity and diagnostic.identity not in excluded
    }
    cue_label_identities = {
        diagnostic.identity for diagnostic in resolution_diagnostics
        if diagnostic.category == "song_cue" and diagnostic.status == "matched"
        and diagnostic.identity and diagnostic.identity not in excluded
    }
    fillers = _normal_identity_set(context.dj_memory_floor_fillers)
    crate_identities = _normal_identity_set(context.crate_identities)

    current_track = _track_dict(now_playing) if now_playing is not None else None
    current_identity = _track_identity(current_track)
    ranked = []
    for identity, representative in representatives.items():
        if identity in excluded:
            continue
        selected = selected_tracks.get(identity, representative)
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
            explain_song_cue=identity in cue_label_identities,
            guest_requested=identity in guest_identities,
            floor_filler=identity in fillers,
        )
        badges = []
        if (identity in must_identities or identity in play_if_identities
                or identity in cue_label_identities):
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
            selected_recommendations = required + other[:max(0, limit - len(required))]
    else:
        selected_recommendations = ranked if limit is None else ranked[:limit]
    capacity_overflow = None
    if (prep and limit is not None and len(selected_recommendations) > limit):
        capacity_overflow = CapacityOverflow(
            limit, len(required_tracks), len(selected_recommendations)
        )
    return Recommendations(
        items=selected_recommendations,
        unresolved_must_play=unresolved_must,
        conflicting_must_play=must_conflicts,
        phase=phase.name,
        resolution_diagnostics=resolution_diagnostics,
        do_not_play_diagnostics=dnp_diagnostics,
        conflicting_requests=request_conflicts,
        version_conflicts=version_conflicts,
        capacity_overflow=capacity_overflow,
    )


def rank_next_song(
    ctx: SetlistContext,
    library,
    now_playing=None,
    *,
    settings: Optional[SetlistSettings] = None,
    at=None,
    limit: int = 10,
) -> Recommendations:
    """Rank local songs for Live, including Live-only guest request preferences."""
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

    A limit smaller than the number of eligible must-play identities expands the
    result and reports ``capacity_overflow``; remaining slots use ranked candidates.
    Conflicting versions use the must-play, then play-if-possible priority and
    lexical selection policy documented with ``VersionConflict``.
    """
    return _rank(ctx, library, now_playing, settings or SetlistSettings(), None,
                 limit, prep=True, phase_override=phase)
