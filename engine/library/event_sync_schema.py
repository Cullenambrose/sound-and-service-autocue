"""
Sound & Service booking site API schema.

This module isolates the HTTP schema for the S&S booking API.
All API interactions are tolerant of missing fields.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class MusicItem:
    """A single music track reference from the booking site."""
    id: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None


@dataclass
class MusicLists:
    """Music lists for an event."""
    must_play: List[MusicItem] = field(default_factory=list)
    play_if_possible: List[MusicItem] = field(default_factory=list)
    do_not_play: List[MusicItem] = field(default_factory=list)


@dataclass
class TimelineItem:
    """A single timeline entry (e.g., ceremony, cocktail, dinner)."""
    name: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_minutes: Optional[int] = None


@dataclass
class EventTimelines:
    """Event timeline structure."""
    items: List[TimelineItem] = field(default_factory=list)


@dataclass
class PlannerSongCue:
    """A planner-specified song cue (first dance, entrance, etc.)."""
    moment: str  # e.g., "first_dance", "entrance", "cake_cutting"
    title: Optional[str] = None
    artist: Optional[str] = None


@dataclass
class PlannerResponses:
    """Planner responses with song cues and lineup."""
    song_cues: List[PlannerSongCue] = field(default_factory=list)


@dataclass
class EventData:
    """Complete event data from the booking API."""
    inquiry_id: str
    event_date: str
    event_name: Optional[str] = None
    client_name: Optional[str] = None
    venue: Optional[str] = None
    locked: bool = False
    music_lists: Optional[MusicLists] = None
    music_preferences: Optional[Dict[str, Any]] = None
    event_timelines: Optional[EventTimelines] = None
    planner: Optional[PlannerResponses] = None


@dataclass
class EventPrepStatus:
    """Event preparation status to push back to the booking site."""
    inquiry_id: str
    matched_count: int
    missing_count: int
    missing_titles: List[Dict[str, str]]
    do_not_play_hits: int
    crate_name: str


def parse_event(data: Dict[str, Any]) -> EventData:
    """
    Parse event data from API response.
    Tolerant of missing fields.
    """
    # Parse music lists
    music_lists = None
    if "music_lists" in data:
        ml_data = data["music_lists"]
        music_lists = MusicLists(
            must_play=[
                MusicItem(
                    id=item.get("id"),
                    title=item.get("title"),
                    artist=item.get("artist"),
                )
                for item in ml_data.get("must_play", [])
            ],
            play_if_possible=[
                MusicItem(
                    id=item.get("id"),
                    title=item.get("title"),
                    artist=item.get("artist"),
                )
                for item in ml_data.get("play_if_possible", [])
            ],
            do_not_play=[
                MusicItem(
                    id=item.get("id"),
                    title=item.get("title"),
                    artist=item.get("artist"),
                )
                for item in ml_data.get("do_not_play", [])
            ],
        )
    
    # Parse event timelines
    event_timelines = None
    if "event_timelines" in data:
        timeline_data = data["event_timelines"]
        event_timelines = EventTimelines(
            items=[
                TimelineItem(
                    name=item.get("name", ""),
                    start_time=item.get("start_time"),
                    end_time=item.get("end_time"),
                    duration_minutes=item.get("duration_minutes"),
                )
                for item in timeline_data.get("items", [])
            ]
        )
    
    # Parse planner responses
    planner = None
    if "planner" in data:
        planner_data = data["planner"]
        planner = PlannerResponses(
            song_cues=[
                PlannerSongCue(
                    moment=cue.get("moment", ""),
                    title=cue.get("title"),
                    artist=cue.get("artist"),
                )
                for cue in planner_data.get("song_cues", [])
            ]
        )
    
    return EventData(
        inquiry_id=data.get("inquiry_id", ""),
        event_date=data.get("event_date", ""),
        event_name=data.get("event_name"),
        client_name=data.get("client_name"),
        venue=data.get("venue"),
        locked=data.get("locked", False),
        music_lists=music_lists,
        music_preferences=data.get("music_preferences"),
        event_timelines=event_timelines,
        planner=planner,
    )


def serialize_prep_status(status: EventPrepStatus) -> Dict[str, Any]:
    """Serialize event prep status for API POST."""
    return {
        "inquiry_id": status.inquiry_id,
        "matched_count": status.matched_count,
        "missing_count": status.missing_count,
        "missing_titles": status.missing_titles,
        "do_not_play_hits": status.do_not_play_hits,
        "crate_name": status.crate_name,
    }


# Timeline name mappings for Serato subcrates
TIMELINE_SUBCRATE_MAP = {
    "ceremony": "Ceremony",
    "cocktail hour": "Cocktail",
    "cocktail": "Cocktail",
    "dinner": "Dinner",
    "first dance": "First Dance",
    "open dancing": "Open Dancing",
    "dancing": "Open Dancing",
}

# Planner moment mappings to timeline subcrates
PLANNER_MOMENT_SUBCRATE_MAP = {
    "first_dance": "First Dance",
    "entrance": "Ceremony",
    "processional": "Ceremony",
    "recessional": "Ceremony",
    "cake_cutting": "Dinner",
    "parent_dances": "First Dance",
    "bouquet_toss": "Open Dancing",
    "garter_toss": "Open Dancing",
    "last_song": "Open Dancing",
}


def get_subcrate_for_timeline(timeline_name: str) -> Optional[str]:
    """Get Serato subcrate name for a timeline item."""
    return TIMELINE_SUBCRATE_MAP.get(timeline_name.lower())


def get_subcrate_for_planner_moment(moment: str) -> Optional[str]:
    """Get Serato subcrate name for a planner song cue moment."""
    return PLANNER_MOMENT_SUBCRATE_MAP.get(moment.lower())
