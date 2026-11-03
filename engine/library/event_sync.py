"""
Event sync implementation for Sound & Service booking site.

Pulls upcoming events, matches tracks, builds Serato crates with timeline subcrates,
and pushes prep status back to the booking site.
"""
import json
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from urllib.parse import urljoin, quote

from . import db
from .matcher import match_track, build_crate_plan, MatchConfig, MatchResult
from .adapters.registry import get_adapter
from .event_sync_schema import (
    parse_event,
    serialize_prep_status,
    EventData,
    EventPrepStatus,
    MusicItem,
    get_subcrate_for_timeline,
    get_subcrate_for_planner_moment,
)


# Device token handling
def get_device_token() -> Optional[str]:
    """
    Get device token from macOS Keychain or environment variable.
    Never logs or writes the token to disk/DB.
    """
    # Try environment variable first
    token = os.environ.get("AUTOCUE_DEVICE_TOKEN")
    if token:
        return token
    
    # Try macOS Keychain via keyring
    try:
        import keyring
        token = keyring.get_password("autocue", "device_token")
        return token
    except ImportError:
        pass
    except Exception:
        pass
    
    return None


# HTTP client
def make_request(
    method: str,
    endpoint: str,
    base_url: str,
    token: str,
    json_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Make an HTTP request to the booking site API.
    
    Args:
        method: HTTP method (GET, POST)
        endpoint: API endpoint path
        base_url: Base URL for the API
        token: Device token
        json_data: Optional JSON payload for POST
        
    Returns:
        Parsed JSON response
        
    Raises:
        RuntimeError: On HTTP errors
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError(
            "requests library not installed. Install with: pip install requests"
        )
    
    url = urljoin(base_url, endpoint)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=30)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=json_data, timeout=30)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"HTTP request failed: {e}")


def fetch_events(base_url: str, token: str) -> List[EventData]:
    """Fetch upcoming events from the booking site API."""
    response = make_request("GET", "/api/v1/events/upcoming", base_url, token)
    
    events = []
    for event_data in response.get("events", []):
        try:
            event = parse_event(event_data)
            events.append(event)
        except Exception as e:
            print(f"Warning: Failed to parse event {event_data.get('inquiry_id')}: {e}")
    
    return events


def push_prep_status(
    base_url: str,
    token: str,
    status: EventPrepStatus,
) -> Dict[str, Any]:
    """Push event prep status back to the booking site."""
    payload = serialize_prep_status(status)
    return make_request("POST", "/api/v1/events/prep-status", base_url, token, payload)


# Event processing
def process_event(
    event: EventData,
    library_tracks: List[Dict[str, Any]],
    config: MatchConfig,
) -> Tuple[List[MatchResult], List[MatchResult], List[MatchResult]]:
    """
    Process an event: match must-play, play-if-possible, and do-not-play lists.
    
    Returns:
        Tuple of (must_play_results, play_if_possible_results, do_not_play_results)
    """
    must_play_results = []
    play_if_possible_results = []
    do_not_play_results = []
    
    # Match must-play list
    if event.music_lists and event.music_lists.must_play:
        for item in event.music_lists.must_play:
            if item.title or item.artist:
                request = {
                    "artist": item.artist or "",
                    "title": item.title or "",
                }
                result = match_track(request, library_tracks, config)
                must_play_results.append(result)
    
    # Match play-if-possible list
    if event.music_lists and event.music_lists.play_if_possible:
        for item in event.music_lists.play_if_possible:
            if item.title or item.artist:
                request = {
                    "artist": item.artist or "",
                    "title": item.title or "",
                }
                result = match_track(request, library_tracks, config)
                play_if_possible_results.append(result)
    
    # Match do-not-play list
    if event.music_lists and event.music_lists.do_not_play:
        # Use lower confidence threshold for do-not-play
        dnp_config = MatchConfig(min_confidence=0.6)
        for item in event.music_lists.do_not_play:
            if item.title or item.artist:
                request = {
                    "artist": item.artist or "",
                    "title": item.title or "",
                }
                result = match_track(request, library_tracks, dnp_config)
                do_not_play_results.append(result)
    
    return must_play_results, play_if_possible_results, do_not_play_results


def build_subcrates_mapping(
    event: EventData,
    matched_results: List[MatchResult],
    planner_results: List[MatchResult],
) -> Dict[str, List[int]]:
    """
    Build mapping of subcrate names to track IDs based on timeline and planner cues.
    
    Returns:
        Dict mapping subcrate name to list of track IDs
    """
    subcrates = {}
    
    # Map timeline items to track IDs
    # For now, we don't have explicit timeline-to-track mappings in the data,
    # so we'll just create empty subcrates for detected timelines
    if event.event_timelines:
        for timeline_item in event.event_timelines.items:
            subcrate_name = get_subcrate_for_timeline(timeline_item.name)
            if subcrate_name and subcrate_name not in subcrates:
                subcrates[subcrate_name] = []
    
    # Map planner song cues to subcrates
    for result in planner_results:
        if result.status in ("matched", "ambiguous") and result.matched_track:
            track_id = result.matched_track.get("id")
            if track_id:
                # Get moment from original request metadata
                # (This assumes we tagged the request with the moment)
                moment = result.request.get("moment")
                if moment:
                    subcrate_name = get_subcrate_for_planner_moment(moment)
                    if subcrate_name:
                        if subcrate_name not in subcrates:
                            subcrates[subcrate_name] = []
                        if track_id not in subcrates[subcrate_name]:
                            subcrates[subcrate_name].append(track_id)
    
    return subcrates


def build_event_crates(
    event: EventData,
    matched_results: List[MatchResult],
    play_if_possible_results: List[MatchResult],
    do_not_play_results: List[MatchResult],
    planner_results: List[MatchResult],
    db_conn,
) -> Tuple[List[str], Dict[str, List[str]]]:
    """
    Build crate plan for an event.
    
    Returns:
        Tuple of (main_crate_track_paths, subcrate_name_to_track_paths_dict)
    """
    # Combine must-play and play-if-possible for the main crate
    all_match_results = matched_results + play_if_possible_results
    
    # Build crate plan (excluding do-not-play)
    request_order = [r.request for r in all_match_results]
    plan = build_crate_plan(
        all_match_results,
        do_not_play_results,
        request_order,
        crate_name=f"S&S Events%%{event.event_date} {event.event_name or event.client_name or event.inquiry_id}"
    )
    
    # Get track paths from IDs
    main_crate_paths = []
    for track_id in plan.ordered_track_ids:
        cursor = db_conn.cursor()
        cursor.execute("SELECT path FROM tracks WHERE id = ?", (track_id,))
        row = cursor.fetchone()
        if row:
            main_crate_paths.append(row["path"])
    
    # Build subcrates mapping
    subcrates_mapping = build_subcrates_mapping(event, all_match_results, planner_results)
    
    # Convert subcrate track IDs to paths
    subcrate_paths = {}
    for subcrate_name, track_ids in subcrates_mapping.items():
        paths = []
        for track_id in track_ids:
            cursor = db_conn.cursor()
            cursor.execute("SELECT path FROM tracks WHERE id = ?", (track_id,))
            row = cursor.fetchone()
            if row:
                paths.append(row["path"])
        if paths:
            subcrate_paths[subcrate_name] = paths
    
    return main_crate_paths, subcrate_paths


def write_event_crates(
    event: EventData,
    main_crate_paths: List[str],
    subcrate_paths: Dict[str, List[str]],
    adapter,
    serato_dir: Optional[Path],
    apply: bool,
    db_path: str,
) -> List[Dict[str, Any]]:
    """
    Write Serato crates for an event (main crate + subcrates).
    
    Returns:
        List of write results
    """
    results = []
    
    # Main crate name: "S&S Events%%<date> <event>"
    main_crate_name = f"S&S Events%%{event.event_date} {event.event_name or event.client_name or event.inquiry_id}"
    
    # Write main crate
    if main_crate_paths:
        try:
            result = adapter.write_crate(
                name=main_crate_name,
                tracks=main_crate_paths,
                serato_dir=serato_dir,
                overwrite=True,
                apply=apply,
                db_path=db_path,
            )
            results.append({
                "crate": main_crate_name,
                "type": "main",
                "result": result,
            })
        except Exception as e:
            results.append({
                "crate": main_crate_name,
                "type": "main",
                "error": str(e),
            })
    
    # Write subcrates: "S&S Events%%<date> <event>%%<timeline>"
    for subcrate_name, tracks in subcrate_paths.items():
        full_subcrate_name = f"{main_crate_name}%%{subcrate_name}"
        try:
            result = adapter.write_crate(
                name=full_subcrate_name,
                tracks=tracks,
                serato_dir=serato_dir,
                overwrite=True,
                apply=apply,
                db_path=db_path,
            )
            results.append({
                "crate": full_subcrate_name,
                "type": "subcrate",
                "result": result,
            })
        except Exception as e:
            results.append({
                "crate": full_subcrate_name,
                "type": "subcrate",
                "error": str(e),
            })
    
    return results


def match_planner_cues(
    event: EventData,
    library_tracks: List[Dict[str, Any]],
    config: MatchConfig,
) -> List[MatchResult]:
    """Match planner song cues against library."""
    results = []
    
    if event.planner and event.planner.song_cues:
        for cue in event.planner.song_cues:
            if cue.title or cue.artist:
                request = {
                    "artist": cue.artist or "",
                    "title": cue.title or "",
                    "moment": cue.moment,  # Include moment for subcrate mapping
                }
                result = match_track(request, library_tracks, config)
                results.append(result)
    
    return results


def build_missing_titles_list(
    must_play_results: List[MatchResult],
    play_if_possible_results: List[MatchResult],
) -> List[Dict[str, str]]:
    """Build list of missing titles with ZipDJ search URLs."""
    missing = []
    
    for result in must_play_results + play_if_possible_results:
        if result.status == "missing":
            artist = result.request.get("artist", "")
            title = result.request.get("title", "")
            
            # Build ZipDJ search URL
            search_query = f"{artist} {title}".strip()
            zipdj_url = f"https://www.zipdj.com/search?q={quote(search_query)}"
            
            missing.append({
                "artist": artist,
                "title": title,
                "search_url": zipdj_url,
            })
    
    return missing


# Event cache management
def get_cache_path() -> Path:
    """Get path to event cache directory."""
    cache_dir = Path.home() / ".autocue" / "event_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def cache_event(event: EventData):
    """Cache event data locally."""
    cache_dir = get_cache_path()
    cache_file = cache_dir / f"{event.inquiry_id}.json"
    
    with open(cache_file, "w") as f:
        json.dump({
            "inquiry_id": event.inquiry_id,
            "event_date": event.event_date,
            "cached_at": time.time(),
        }, f)


def clear_old_events():
    """Clear cached events older than their event date."""
    cache_dir = get_cache_path()
    now = datetime.now()
    
    for cache_file in cache_dir.glob("*.json"):
        try:
            with open(cache_file) as f:
                data = json.load(f)
                event_date = datetime.fromisoformat(data.get("event_date", ""))
                
                # Clear if event date has passed
                if event_date < now:
                    cache_file.unlink()
        except Exception:
            pass


def sync_events(
    base_url: str,
    db_path: str,
    serato_dir: Optional[Path] = None,
    event_id: Optional[str] = None,
    apply: bool = False,
    locked_only: bool = True,
    push_status: bool = True,
) -> Dict[str, Any]:
    """
    Sync events from the booking site.
    
    Args:
        base_url: Base URL for the booking site API
        db_path: Path to the library database
        serato_dir: Optional Serato directory path
        event_id: Optional specific event ID to sync
        apply: If True, actually write crates (default: dry-run)
        locked_only: If True, only process couple-locked events (default: True)
        push_status: If True, push prep status back to booking site
        
    Returns:
        Dict with sync results
    """
    # Get device token
    token = get_device_token()
    if not token:
        raise RuntimeError(
            "Device token not found. Set AUTOCUE_DEVICE_TOKEN environment variable "
            "or store in macOS Keychain with: keyring set autocue device_token"
        )
    
    # Fetch events
    print("Fetching events from booking site...")
    events = fetch_events(base_url, token)
    print(f"Found {len(events)} upcoming events")
    
    # Filter to specific event if requested
    if event_id:
        events = [e for e in events if e.inquiry_id == event_id]
        if not events:
            raise RuntimeError(f"Event {event_id} not found")
    
    # Filter to locked events if requested
    if locked_only:
        original_count = len(events)
        events = [e for e in events if e.locked]
        print(f"Filtered to {len(events)} locked events (from {original_count} total)")
    
    # Load library
    with db.get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, path, title, artist, album, genre, bpm, key FROM tracks")
        library_tracks = [dict(row) for row in cursor.fetchall()]
    
    print(f"Loaded {len(library_tracks)} tracks from library")
    
    # Get Serato adapter
    adapter = get_adapter("serato")
    if not adapter:
        raise RuntimeError("Serato adapter not found")
    
    # Process each event
    results = []
    config = MatchConfig()
    
    for i, event in enumerate(events, 1):
        print(f"\n[{i}/{len(events)}] Processing event: {event.event_name or event.client_name or event.inquiry_id}")
        print(f"  Date: {event.event_date}")
        print(f"  Locked: {event.locked}")
        
        # Process event
        must_play_results, play_if_possible_results, do_not_play_results = process_event(
            event, library_tracks, config
        )
        
        # Match planner cues
        planner_results = match_planner_cues(event, library_tracks, config)
        
        # Build crates
        with db.get_db(db_path) as conn:
            main_crate_paths, subcrate_paths = build_event_crates(
                event,
                must_play_results,
                play_if_possible_results,
                do_not_play_results,
                planner_results,
                conn,
            )
        
        # Write crates
        crate_results = write_event_crates(
            event,
            main_crate_paths,
            subcrate_paths,
            adapter,
            serato_dir,
            apply,
            db_path,
        )
        
        # Count matches
        matched_count = sum(
            1 for r in must_play_results + play_if_possible_results
            if r.status in ("matched", "ambiguous")
        )
        missing_count = sum(
            1 for r in must_play_results + play_if_possible_results
            if r.status == "missing"
        )
        do_not_play_hits = sum(
            1 for r in do_not_play_results
            if r.status in ("matched", "ambiguous")
        )
        
        # Build missing titles list
        missing_titles = build_missing_titles_list(
            must_play_results,
            play_if_possible_results,
        )
        
        # Push status back to booking site
        main_crate_name = f"S&S Events%%{event.event_date} {event.event_name or event.client_name or event.inquiry_id}"
        
        push_result = None
        if push_status and apply:
            try:
                status = EventPrepStatus(
                    inquiry_id=event.inquiry_id,
                    matched_count=matched_count,
                    missing_count=missing_count,
                    missing_titles=missing_titles,
                    do_not_play_hits=do_not_play_hits,
                    crate_name=main_crate_name,
                )
                push_result = push_prep_status(base_url, token, status)
                print(f"  ✓ Pushed prep status to booking site")
            except Exception as e:
                print(f"  ⚠ Failed to push prep status: {e}")
        
        # Cache event
        cache_event(event)
        
        # Report
        print(f"  Matched: {matched_count}")
        print(f"  Missing: {missing_count}")
        print(f"  Do-not-play hits: {do_not_play_hits}")
        print(f"  Main crate: {len(main_crate_paths)} tracks")
        print(f"  Subcrates: {len(subcrate_paths)}")
        
        for crate_result in crate_results:
            if "error" in crate_result:
                print(f"  ✗ {crate_result['crate']}: {crate_result['error']}")
            elif crate_result["result"].get("dry_run"):
                print(f"  ~ {crate_result['crate']}: dry-run")
            else:
                print(f"  ✓ {crate_result['crate']}: written")
        
        results.append({
            "event": event,
            "matched_count": matched_count,
            "missing_count": missing_count,
            "do_not_play_hits": do_not_play_hits,
            "crate_results": crate_results,
            "push_result": push_result,
            "missing_titles": missing_titles,
        })
    
    # Clear old cached events
    clear_old_events()
    
    return {
        "events_processed": len(results),
        "results": results,
    }
