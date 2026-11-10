"""Adapters from event payloads and DJ memory into canonical setlist inputs."""
from collections.abc import Mapping
from typing import Any, Iterable, Optional

from library.event_sync_schema import EventData, parse_event
from library.matcher import extract_featuring, extract_version_tags, normalize_text

from .types import (
    SetlistContext,
    SongCue,
    SongIdentity,
    SongRequest,
    TimelineEntry,
)


def song_identity(title: Optional[str], artist: Optional[str]) -> Optional[SongIdentity]:
    """Return matcher-compatible identity, ignoring supported versions and feature artists."""
    if not isinstance(title, str) or not title.strip():
        return None
    title_without_version, _ = extract_version_tags(title)
    primary_title, _ = extract_featuring(title_without_version)
    primary_artist, _ = extract_featuring(artist if isinstance(artist, str) else "")
    return SongIdentity(artist=primary_artist, title=normalize_text(primary_title))


def _request(value: Any, *, moment: Optional[str] = None) -> SongRequest:
    if isinstance(value, SongRequest):
        return value
    if isinstance(value, Mapping):
        metadata = value.get("metadata")
        return SongRequest(
            id=value.get("id"),
            title=value.get("title"),
            artist=value.get("artist"),
            version=value.get("version"),
            moment=value.get("moment") or moment,
            metadata=metadata if isinstance(metadata, Mapping) else {},
        )
    return SongRequest(
        id=getattr(value, "id", None),
        title=getattr(value, "title", None),
        artist=getattr(value, "artist", None),
        version=getattr(value, "version", None),
        moment=getattr(value, "moment", None) or moment,
        metadata=getattr(value, "metadata", {}) or {},
    )


def _coerce_identity(value: Any) -> Optional[SongIdentity]:
    if isinstance(value, SongIdentity):
        return song_identity(value.title, value.artist)
    if isinstance(value, Mapping):
        return song_identity(value.get("title"), value.get("artist"))
    if isinstance(value, (tuple, list)) and len(value) == 2:
        # Identity tuples follow the dataclass field order: (artist, title).
        return song_identity(value[1], value[0])
    return None


def _identity_set(values: Iterable[Any]) -> frozenset:
    return frozenset(identity for value in (values or ()) if (identity := _coerce_identity(value)))


def _played_next(values: Optional[Mapping[Any, Mapping[Any, Any]]]):
    result = {}
    for source, destinations in (values or {}).items():
        source_identity = _coerce_identity(source)
        if source_identity is None or not isinstance(destinations, Mapping):
            continue
        mapped = {}
        for destination, weight in destinations.items():
            destination_identity = _coerce_identity(destination)
            if destination_identity is None:
                continue
            try:
                mapped[destination_identity] = max(0.0, float(weight))
            except (TypeError, ValueError):
                continue
        result[source_identity] = mapped
    return result


def context_from_event(
    event: Any,
    *,
    guest_requests: Iterable[Any] = (),
    already_played: Iterable[Any] = (),
    already_placed: Iterable[Any] = (),
    played_next: Optional[Mapping[Any, Mapping[Any, Any]]] = None,
    dj_memory_floor_fillers: Iterable[Any] = (),
    crate_identities: Iterable[Any] = (),
    phase: Optional[str] = None,
    energy_stage: Optional[str] = None,
    energy_target: Optional[float] = None,
) -> SetlistContext:
    """Adapt EventData or raw event JSON; Live-only inputs remain separate arguments."""
    parsed = parse_event(event) if isinstance(event, Mapping) else event
    if not isinstance(parsed, EventData):
        raise TypeError("event must be EventData or a raw event mapping")

    music_lists = parsed.music_lists
    must_play = tuple(_request(item) for item in (music_lists.must_play if music_lists else ()))
    play_if_possible = tuple(_request(item) for item in
                             (music_lists.play_if_possible if music_lists else ()))
    do_not_play = tuple(_request(item) for item in
                        (music_lists.do_not_play if music_lists else ()))

    cues = []
    if music_lists:
        cues.extend(
            SongCue(item.moment or "", item.title, item.artist, item.version, item.metadata)
            for item in music_lists.special_moments
        )
    if parsed.planner:
        cues.extend(
            SongCue(item.moment, item.title, item.artist, item.version, item.metadata)
            for item in parsed.planner.song_cues
        )

    timelines = []
    if parsed.event_timelines:
        for item in parsed.event_timelines.items:
            timeline_song = _request(item.song) if item.song is not None else None
            timelines.append(TimelineEntry(
                name=item.name,
                start_time=item.start_time,
                end_time=item.end_time,
                duration_minutes=item.duration_minutes,
                kind=item.kind,
                title=item.title,
                song=timeline_song,
                song_cue=item.song_cue,
                metadata=item.metadata,
            ))
            # songCue carries fade/version/seconds metadata, not a song reference.
            if timeline_song and (item.kind or "").strip().casefold() != "songcue":
                cues.append(SongCue(
                    moment=item.kind or item.name,
                    title=timeline_song.title,
                    artist=timeline_song.artist,
                    version=timeline_song.version,
                    metadata=timeline_song.metadata,
                ))

    inquiry_id = parsed.inquiry_id.strip() if isinstance(parsed.inquiry_id, str) else None
    return SetlistContext(
        inquiry_id=inquiry_id or None,
        locked=parsed.locked,
        must_play=must_play,
        play_if_possible=play_if_possible,
        do_not_play=do_not_play,
        song_cues=tuple(cues),
        guest_requests=tuple(_request(item) for item in guest_requests),
        event_timelines=tuple(timelines),
        already_played=_identity_set(already_played),
        already_placed=_identity_set(already_placed),
        played_next=_played_next(played_next),
        dj_memory_floor_fillers=_identity_set(dj_memory_floor_fillers),
        crate_identities=_identity_set(crate_identities),
        explicit_phase=phase,
        energy_stage=energy_stage,
        energy_target=energy_target,
    )
