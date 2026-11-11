"""Shared pure-Python next-song ranking for Prep and Live.

The engine only transforms the event context and local-library records supplied
by its caller. Prep recommendations may hand selected track paths to the
existing crate planner/writer as a separate step. Tag changes must go through
``tag_gate``. Existing dry-run, locked-event, explicit-apply, and Serato-closed
safeguards remain downstream; this package calls neither crate nor tag writers.
"""
from .engine import PreparedLibrary, rank_next_song, rank_prep_crate
from .inputs import context_from_event, song_identity
from .scoring import GENRES, SetlistSettings
from .types import (
    CapacityOverflow,
    DoNotPlayDiagnostic,
    MustPlayConflict,
    Recommendation,
    Recommendations,
    RequestConflict,
    RequestDiagnostic,
    SetlistContext,
    SongCue,
    SongIdentity,
    SongRequest,
    TimelineEntry,
    VersionConflict,
)

__all__ = [
    "PreparedLibrary",
    "GENRES",
    "CapacityOverflow",
    "DoNotPlayDiagnostic",
    "MustPlayConflict",
    "Recommendation",
    "Recommendations",
    "RequestConflict",
    "RequestDiagnostic",
    "SetlistContext",
    "SetlistSettings",
    "SongCue",
    "SongIdentity",
    "SongRequest",
    "TimelineEntry",
    "VersionConflict",
    "context_from_event",
    "rank_next_song",
    "rank_prep_crate",
    "song_identity",
]
