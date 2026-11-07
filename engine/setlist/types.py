"""Typed, I/O-free values shared by Prep and Live song ranking."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple


@dataclass(frozen=True, order=True)
class SongIdentity:
    """Canonical song-level identity; deliberately excludes file/version identity."""
    artist: str
    title: str


@dataclass(frozen=True)
class SongRequest:
    """An original requested song reference, retaining source fields and metadata."""
    title: Optional[str] = None
    artist: Optional[str] = None
    version: Optional[str] = None
    id: Optional[str] = None
    moment: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class SongCue:
    """A song attached to a planner or event moment."""
    moment: str
    title: Optional[str] = None
    artist: Optional[str] = None
    version: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class TimelineEntry:
    """Canonical event timeline row used for phase selection."""
    name: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_minutes: Optional[int] = None
    kind: Optional[str] = None
    title: Optional[str] = None
    song: Optional[SongRequest] = None
    song_cue: Mapping[str, Any] = field(default_factory=dict, compare=False)
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class SetlistContext:
    """Event and DJ memory supplied to the shared, pure recommendation engine."""
    inquiry_id: Optional[str] = None
    locked: bool = False
    must_play: Tuple[SongRequest, ...] = ()
    play_if_possible: Tuple[SongRequest, ...] = ()
    do_not_play: Tuple[SongRequest, ...] = ()
    song_cues: Tuple[SongCue, ...] = ()
    guest_requests: Tuple[SongRequest, ...] = ()
    event_timelines: Tuple[TimelineEntry, ...] = ()
    already_played: frozenset[SongIdentity] = field(default_factory=frozenset)
    already_placed: frozenset[SongIdentity] = field(default_factory=frozenset)
    played_next: Mapping[SongIdentity, Mapping[SongIdentity, float]] = field(default_factory=dict)
    dj_memory_floor_fillers: frozenset[SongIdentity] = field(default_factory=frozenset)
    crate_identities: frozenset[SongIdentity] = field(default_factory=frozenset)
    explicit_phase: Optional[str] = None
    energy_stage: Optional[str] = None
    energy_target: Optional[float] = None


@dataclass(frozen=True)
class MustPlayConflict:
    """A must-play request excluded by a higher-priority hard rule."""
    request: SongRequest
    song_identity: SongIdentity
    reason: str


@dataclass(frozen=True)
class Recommendation:
    """One local file/version selected to represent a song identity."""
    song_identity: SongIdentity
    track: Mapping[str, Any]
    score: float
    reasons: Tuple[str, ...]
    badges: Tuple[str, ...] = ()


@dataclass
class Recommendations:
    """Ranked local recommendations plus explicit must-play resolution outcomes."""
    items: List[Recommendation] = field(default_factory=list)
    unresolved_must_play: List[SongRequest] = field(default_factory=list)
    conflicting_must_play: List[MustPlayConflict] = field(default_factory=list)
    phase: str = "generic"

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]
