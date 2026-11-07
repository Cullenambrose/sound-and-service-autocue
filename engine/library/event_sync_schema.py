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
    version: Optional[str] = None
    moment: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MusicLists:
    """Music lists for an event."""
    must_play: List[MusicItem] = field(default_factory=list)
    play_if_possible: List[MusicItem] = field(default_factory=list)
    do_not_play: List[MusicItem] = field(default_factory=list)
    special_moments: List["PlannerSongCue"] = field(default_factory=list)


@dataclass
class TimelineItem:
    """A single timeline entry (e.g., ceremony, cocktail, dinner)."""
    name: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_minutes: Optional[int] = None
    kind: Optional[str] = None
    title: Optional[str] = None
    song: Optional[MusicItem] = None
    song_cue: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


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
    version: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlannerResponses:
    """Planner responses with song cues and lineup."""
    song_cues: List[PlannerSongCue] = field(default_factory=list)
    status: Optional[str] = None


@dataclass
class EventData:
    """Complete event data from the booking API."""
    inquiry_id: Optional[str]
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
    def as_mapping(value):
        return value if isinstance(value, dict) else {}

    def as_list(value):
        return value if isinstance(value, list) else []

    def music_item(item):
        item = as_mapping(item)
        known = {"id", "title", "artist", "version", "moment"}
        return MusicItem(
            id=item.get("id"),
            title=item.get("title"),
            artist=item.get("artist"),
            version=item.get("version"),
            moment=item.get("moment"),
            metadata={key: value for key, value in item.items() if key not in known},
        )

    def song_cues(value, default_moment=""):
        cues = []

        def append(item, moment=default_moment):
            item = as_mapping(item)
            if not (item.get("title") or item.get("artist")):
                return
            cues.append(PlannerSongCue(
                moment=item.get("moment") or moment or "",
                title=item.get("title"),
                artist=item.get("artist"),
                version=item.get("version"),
                metadata={key: entry for key, entry in item.items()
                          if key not in {"moment", "title", "artist", "version"}},
            ))

        def append_group(item, moment=default_moment):
            item = as_mapping(item)
            nested = item.get("songs")
            if isinstance(nested, list):
                for song in nested:
                    append(song, item.get("moment") or item.get("name") or moment)
            elif isinstance(item.get("song"), dict):
                append(item["song"], item.get("moment") or item.get("name") or moment)
            else:
                append(item, moment)

        if isinstance(value, dict):
            for moment, entries in value.items():
                if isinstance(entries, list):
                    for entry in entries:
                        append_group(entry, str(moment))
                else:
                    append_group(entries, str(moment))
        elif isinstance(value, list):
            for entry in value:
                entry = as_mapping(entry)
                moment = entry.get("moment") or entry.get("name") or entry.get("kind") or default_moment
                append_group(entry, moment)
        return cues

    music_lists = None
    if "music_lists" in data and data.get("music_lists") is not None:
        ml_data = as_mapping(data["music_lists"])
        music_lists = MusicLists(
            must_play=[music_item(item) for item in as_list(ml_data.get("must_play"))],
            play_if_possible=[music_item(item) for item in as_list(ml_data.get("play_if_possible"))],
            do_not_play=[music_item(item) for item in as_list(ml_data.get("do_not_play"))],
            special_moments=song_cues(ml_data.get("special_moments", [])),
        )
    
    # Parse event timelines
    event_timelines = None
    if "event_timelines" in data and data.get("event_timelines") is not None:
        timeline_data = as_mapping(data["event_timelines"])
        event_timelines = EventTimelines(
            items=[
                TimelineItem(
                    name=item.get("name", ""),
                    start_time=item.get("start_time"),
                    end_time=item.get("end_time"),
                    duration_minutes=item.get("duration_minutes"),
                    kind=item.get("kind"),
                    title=item.get("title"),
                    song=music_item(item["song"]) if isinstance(item.get("song"), dict) else None,
                    song_cue=as_mapping(item.get("songCue")),
                    metadata={key: value for key, value in item.items()
                              if key not in {"name", "start_time", "end_time", "duration_minutes",
                                             "kind", "title", "song", "songCue"}},
                )
                for item in as_list(timeline_data.get("items"))
                if isinstance(item, dict)
            ]
        )
    
    # Parse planner responses
    planner = None
    planner_data = as_mapping(data.get("planner"))
    planner_response_data = as_mapping(data.get("planner_responses"))
    if "planner" in data or "planner_responses" in data:
        planner = PlannerResponses(
            song_cues=(
                song_cues(planner_data.get("song_cues", []))
                + song_cues(planner_response_data.get("song_cues", []))
            ),
            status=planner_response_data.get("status", planner_data.get("status")),
        )

    inquiry_id = data.get("inquiry_id")
    if inquiry_id is not None:
        inquiry_id = str(inquiry_id).strip() or None
    planner_status = planner_response_data.get("status", planner_data.get("status"))
    locked_flag = data.get("locked")
    locked = locked_flag is True or (type(locked_flag) is int and locked_flag == 1) or (
        isinstance(locked_flag, str) and locked_flag.strip().casefold() in {"true", "1", "yes"}
    ) or (
        isinstance(planner_status, str) and planner_status.strip().casefold() in {"confirmed", "locked"}
    )
    
    return EventData(
        inquiry_id=inquiry_id,
        event_date=data.get("event_date", ""),
        event_name=data.get("event_name"),
        client_name=data.get("client_name"),
        venue=data.get("venue"),
        locked=locked,
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
