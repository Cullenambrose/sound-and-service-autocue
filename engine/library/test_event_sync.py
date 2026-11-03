"""Tests for event sync functionality."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import engine.library.db as db
import engine.library.event_sync as event_sync
import engine.library.event_sync_schema as event_sync_schema
from engine.library.matcher import MatchConfig, MatchResult


def get_library_tracks():
    """Sample library tracks for testing."""
    return [
        {
            "id": 1,
            "path": "/music/track1.mp3",
            "title": "Uptown Funk (Clean)",
            "artist": "Mark Ronson ft. Bruno Mars",
            "bpm": 115.0,
        },
        {
            "id": 2,
            "path": "/music/track2.mp3",
            "title": "Happy (Intro Edit)",
            "artist": "Pharrell Williams",
            "bpm": 160.0,
        },
        {
            "id": 3,
            "path": "/music/track3.mp3",
            "title": "Shape of You",
            "artist": "Ed Sheeran",
            "bpm": 96.0,
        },
        {
            "id": 4,
            "path": "/music/track4.mp3",
            "title": "Bad Romance",
            "artist": "Lady Gaga",
            "bpm": 119.0,
        },
        {
            "id": 5,
            "path": "/music/track5.mp3",
            "title": "Can't Stop the Feeling",
            "artist": "Justin Timberlake",
            "bpm": 113.0,
        },
    ]


class TestSchema(unittest.TestCase):
    """Test schema parsing with tolerance for missing fields."""
    
    def test_parse_complete_event(self):
        """Test parsing a complete event payload."""
        data = {
            "inquiry_id": "evt_123",
            "event_date": "2026-12-15",
            "event_name": "Smith-Jones Wedding",
            "client_name": "Sarah & John",
            "venue": "Grand Hotel",
            "locked": True,
            "music_lists": {
                "must_play": [
                    {"id": "1", "title": "Uptown Funk", "artist": "Mark Ronson"},
                    {"id": "2", "title": "Happy", "artist": "Pharrell Williams"},
                ],
                "play_if_possible": [
                    {"id": "3", "title": "Shape of You", "artist": "Ed Sheeran"},
                ],
                "do_not_play": [
                    {"id": "4", "title": "Bad Romance", "artist": "Lady Gaga"},
                ],
            },
            "event_timelines": {
                "items": [
                    {"name": "Ceremony", "start_time": "16:00", "duration_minutes": 30},
                    {"name": "Cocktail Hour", "start_time": "16:30", "duration_minutes": 60},
                    {"name": "Dinner", "start_time": "17:30", "duration_minutes": 90},
                    {"name": "First Dance", "start_time": "19:00", "duration_minutes": 5},
                    {"name": "Open Dancing", "start_time": "19:05", "duration_minutes": 180},
                ]
            },
            "planner": {
                "song_cues": [
                    {"moment": "first_dance", "title": "Perfect", "artist": "Ed Sheeran"},
                    {"moment": "processional", "title": "Canon in D", "artist": "Pachelbel"},
                ]
            },
        }
        
        event = event_sync_schema.parse_event(data)
        
        self.assertEqual(event.inquiry_id, "evt_123")
        self.assertEqual(event.event_date, "2026-12-15")
        self.assertEqual(event.event_name, "Smith-Jones Wedding")
        self.assertTrue(event.locked)
        self.assertEqual(len(event.music_lists.must_play), 2)
        self.assertEqual(len(event.music_lists.play_if_possible), 1)
        self.assertEqual(len(event.music_lists.do_not_play), 1)
        self.assertEqual(len(event.event_timelines.items), 5)
        self.assertEqual(len(event.planner.song_cues), 2)
    
    def test_parse_minimal_event(self):
        """Test parsing an event with minimal fields."""
        data = {
            "inquiry_id": "evt_456",
            "event_date": "2026-12-20",
        }
        
        event = event_sync_schema.parse_event(data)
        
        self.assertEqual(event.inquiry_id, "evt_456")
        self.assertEqual(event.event_date, "2026-12-20")
        self.assertIsNone(event.event_name)
        self.assertFalse(event.locked)
        self.assertIsNone(event.music_lists)
        self.assertIsNone(event.event_timelines)
        self.assertIsNone(event.planner)
    
    def test_parse_event_with_missing_music_fields(self):
        """Test parsing with missing title/artist in music items."""
        data = {
            "inquiry_id": "evt_789",
            "event_date": "2027-01-05",
            "music_lists": {
                "must_play": [
                    {"id": "1", "title": "Some Song"},  # Missing artist
                    {"id": "2", "artist": "Some Artist"},  # Missing title
                    {},  # Missing both
                ],
            },
        }
        
        event = event_sync_schema.parse_event(data)
        
        self.assertEqual(len(event.music_lists.must_play), 3)
        self.assertEqual(event.music_lists.must_play[0].title, "Some Song")
        self.assertIsNone(event.music_lists.must_play[0].artist)
        self.assertIsNone(event.music_lists.must_play[1].title)
        self.assertEqual(event.music_lists.must_play[1].artist, "Some Artist")
    
    def test_serialize_prep_status(self):
        """Test serializing prep status for API."""
        status = event_sync_schema.EventPrepStatus(
            inquiry_id="evt_123",
            matched_count=10,
            missing_count=2,
            missing_titles=[
                {"artist": "Artist A", "title": "Song A", "search_url": "https://zipdj.com/..."},
                {"artist": "Artist B", "title": "Song B", "search_url": "https://zipdj.com/..."},
            ],
            do_not_play_hits=1,
            crate_name="S&S Events%%2026-12-15 Smith Wedding",
        )
        
        payload = event_sync_schema.serialize_prep_status(status)
        
        self.assertEqual(payload["inquiry_id"], "evt_123")
        self.assertEqual(payload["matched_count"], 10)
        self.assertEqual(payload["missing_count"], 2)
        self.assertEqual(len(payload["missing_titles"]), 2)
        self.assertEqual(payload["do_not_play_hits"], 1)
    
    def test_timeline_subcrate_mapping(self):
        """Test timeline to subcrate name mapping."""
        self.assertEqual(event_sync_schema.get_subcrate_for_timeline("Ceremony"), "Ceremony")
        self.assertEqual(event_sync_schema.get_subcrate_for_timeline("cocktail hour"), "Cocktail")
        self.assertEqual(event_sync_schema.get_subcrate_for_timeline("Cocktail"), "Cocktail")
        self.assertEqual(event_sync_schema.get_subcrate_for_timeline("Dinner"), "Dinner")
        self.assertEqual(event_sync_schema.get_subcrate_for_timeline("First Dance"), "First Dance")
        self.assertEqual(event_sync_schema.get_subcrate_for_timeline("Open Dancing"), "Open Dancing")
        self.assertEqual(event_sync_schema.get_subcrate_for_timeline("Dancing"), "Open Dancing")
        self.assertIsNone(event_sync_schema.get_subcrate_for_timeline("Unknown"))
    
    def test_planner_moment_subcrate_mapping(self):
        """Test planner moment to subcrate name mapping."""
        self.assertEqual(event_sync_schema.get_subcrate_for_planner_moment("first_dance"), "First Dance")
        self.assertEqual(event_sync_schema.get_subcrate_for_planner_moment("entrance"), "Ceremony")
        self.assertEqual(event_sync_schema.get_subcrate_for_planner_moment("processional"), "Ceremony")
        self.assertEqual(event_sync_schema.get_subcrate_for_planner_moment("cake_cutting"), "Dinner")
        self.assertEqual(event_sync_schema.get_subcrate_for_planner_moment("last_song"), "Open Dancing")
        self.assertIsNone(event_sync_schema.get_subcrate_for_planner_moment("unknown"))


class TestTokenHandling(unittest.TestCase):
    """Test device token handling."""
    
    def test_get_token_from_env(self):
        """Test getting token from environment variable."""
        with mock.patch.dict(os.environ, {"AUTOCUE_DEVICE_TOKEN": "test_token_123"}):
            token = event_sync.get_device_token()
            self.assertEqual(token, "test_token_123")
    
    def test_get_token_from_keyring(self):
        """Test getting token from keyring (mocked)."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("keyring.get_password", return_value="keyring_token_456"):
                token = event_sync.get_device_token()
                self.assertEqual(token, "keyring_token_456")
    
    def test_get_token_not_found(self):
        """Test when token is not found."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("keyring.get_password", return_value=None):
                token = event_sync.get_device_token()
                self.assertIsNone(token)


class TestEventProcessing(unittest.TestCase):
    """Test event processing and matching."""
    
    def test_process_event_must_play(self):
        """Test processing must-play list."""
        library_tracks = get_library_tracks()
        event_data = {
            "inquiry_id": "evt_123",
            "event_date": "2026-12-15",
            "locked": True,
            "music_lists": {
                "must_play": [
                    {"title": "Uptown Funk", "artist": "Mark Ronson"},
                    {"title": "Happy", "artist": "Pharrell Williams"},
                ],
                "play_if_possible": [],
                "do_not_play": [],
            },
        }
        event = event_sync_schema.parse_event(event_data)
        config = MatchConfig()
        
        must_play, play_if_possible, do_not_play = event_sync.process_event(
            event, library_tracks, config
        )
        
        self.assertEqual(len(must_play), 2)
        self.assertEqual(must_play[0].status, "matched")
        self.assertEqual(must_play[0].matched_track["id"], 1)
        self.assertEqual(must_play[1].status, "matched")
        self.assertEqual(must_play[1].matched_track["id"], 2)
    
    def test_process_event_do_not_play_exclusion(self):
        """Test that do-not-play tracks are excluded."""
        library_tracks = get_library_tracks()
        event_data = {
            "inquiry_id": "evt_123",
            "event_date": "2026-12-15",
            "locked": True,
            "music_lists": {
                "must_play": [
                    {"title": "Uptown Funk", "artist": "Mark Ronson"},
                    {"title": "Bad Romance", "artist": "Lady Gaga"},  # Also in do-not-play
                ],
                "play_if_possible": [],
                "do_not_play": [
                    {"title": "Bad Romance", "artist": "Lady Gaga"},
                ],
            },
        }
        event = event_sync_schema.parse_event(event_data)
        config = MatchConfig()
        
        must_play, play_if_possible, do_not_play = event_sync.process_event(
            event, library_tracks, config
        )
        
        # Both tracks matched
        self.assertEqual(len(must_play), 2)
        self.assertEqual(must_play[0].matched_track["id"], 1)
        self.assertEqual(must_play[1].matched_track["id"], 4)
        
        # Do-not-play also matched
        self.assertEqual(len(do_not_play), 1)
        self.assertEqual(do_not_play[0].matched_track["id"], 4)
    
    def test_match_planner_cues(self):
        """Test matching planner song cues."""
        library_tracks = get_library_tracks()
        event_data = {
            "inquiry_id": "evt_123",
            "event_date": "2026-12-15",
            "locked": True,
            "planner": {
                "song_cues": [
                    {"moment": "first_dance", "title": "Shape of You", "artist": "Ed Sheeran"},
                    {"moment": "entrance", "title": "Can't Stop the Feeling", "artist": "Justin Timberlake"},
                ]
            },
        }
        event = event_sync_schema.parse_event(event_data)
        config = MatchConfig()
        
        planner_results = event_sync.match_planner_cues(event, library_tracks, config)
        
        self.assertEqual(len(planner_results), 2)
        self.assertEqual(planner_results[0].status, "matched")
        self.assertEqual(planner_results[0].matched_track["id"], 3)
        self.assertEqual(planner_results[0].request.get("moment"), "first_dance")
        self.assertEqual(planner_results[1].status, "matched")
        self.assertEqual(planner_results[1].matched_track["id"], 5)


class TestCrateBuilding(unittest.TestCase):
    """Test crate building logic."""
    
    def test_build_missing_titles_list(self):
        """Test building missing titles list with ZipDJ URLs."""
        must_play_results = [
            MatchResult(
                request={"artist": "Unknown Artist", "title": "Unknown Song"},
                status="missing",
                reason="Not found",
            ),
        ]
        play_if_possible_results = [
            MatchResult(
                request={"artist": "Another Artist", "title": "Another Song"},
                status="missing",
                reason="Not found",
            ),
        ]
        
        missing = event_sync.build_missing_titles_list(
            must_play_results,
            play_if_possible_results,
        )
        
        self.assertEqual(len(missing), 2)
        self.assertEqual(missing[0]["artist"], "Unknown Artist")
        self.assertEqual(missing[0]["title"], "Unknown Song")
        self.assertIn("zipdj.com/search?q=", missing[0]["search_url"])
        self.assertIn("Unknown+Artist+Unknown+Song", missing[0]["search_url"])


class TestHTTPClient(unittest.TestCase):
    """Test HTTP client with mocked requests."""
    
    @mock.patch("requests.get")
    def test_fetch_events(self, mock_get):
        """Test fetching events from API."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            "events": [
                {
                    "inquiry_id": "evt_123",
                    "event_date": "2026-12-15",
                    "event_name": "Test Event",
                    "locked": True,
                },
            ]
        }
        mock_response.raise_for_status = mock.Mock()
        mock_get.return_value = mock_response
        
        events = event_sync.fetch_events("https://test.example.com", "test_token")
        
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].inquiry_id, "evt_123")
        mock_get.assert_called_once()
    
    @mock.patch("requests.post")
    def test_push_prep_status(self, mock_post):
        """Test pushing prep status to API."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status = mock.Mock()
        mock_post.return_value = mock_response
        
        status = event_sync_schema.EventPrepStatus(
            inquiry_id="evt_123",
            matched_count=10,
            missing_count=2,
            missing_titles=[],
            do_not_play_hits=1,
            crate_name="Test Crate",
        )
        
        result = event_sync.push_prep_status("https://test.example.com", "test_token", status)
        
        self.assertTrue(result["success"])
        mock_post.assert_called_once()


class TestDryRun(unittest.TestCase):
    """Test dry-run behavior."""
    
    @mock.patch("engine.library.event_sync.fetch_events")
    @mock.patch("engine.library.event_sync.get_device_token")
    def test_dry_run_no_writes(self, mock_token, mock_fetch):
        """Test that dry-run mode makes no writes."""
        mock_token.return_value = "test_token"
        library_tracks = get_library_tracks()
        
        # Create temporary database
        fd, temp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.migrate(temp_db)
        
        event_data = {
            "inquiry_id": "evt_123",
            "event_date": "2026-12-15",
            "event_name": "Test Event",
            "locked": True,
            "music_lists": {
                "must_play": [
                    {"title": "Uptown Funk", "artist": "Mark Ronson"},
                ],
                "play_if_possible": [],
                "do_not_play": [],
            },
        }
        mock_fetch.return_value = [event_sync_schema.parse_event(event_data)]
        
        try:
            # Populate test database
            with db.get_db(temp_db) as conn:
                for track in library_tracks:
                    from engine.library.model import Track
                    t = Track(path=track["path"])
                    t.title = track["title"]
                    t.artist = track["artist"]
                    t.bpm = track["bpm"]
                    db.save_track(conn, t)
            
            # Mock the Serato adapter's write_crate to track calls
            with mock.patch("engine.library.adapters.serato.SeratoAdapter.write_crate") as mock_write:
                mock_write.return_value = {
                    "dry_run": True,
                    "name": "Test Crate",
                    "track_count": 1,
                }
                
                result = event_sync.sync_events(
                    base_url="https://test.example.com",
                    db_path=temp_db,
                    apply=False,  # Dry-run
                    locked_only=True,
                    push_status=False,
                )
                
                # Verify write_crate was called with apply=False
                self.assertTrue(mock_write.called)
                for call in mock_write.call_args_list:
                    self.assertFalse(call[1]["apply"])
        finally:
            os.unlink(temp_db)
    
    @mock.patch("engine.library.event_sync.push_prep_status")
    @mock.patch("engine.library.event_sync.fetch_events")
    @mock.patch("engine.library.event_sync.get_device_token")
    def test_dry_run_no_push(self, mock_token, mock_fetch, mock_push):
        """Test that dry-run mode doesn't push status."""
        mock_token.return_value = "test_token"
        library_tracks = get_library_tracks()
        
        # Create temporary database
        fd, temp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        
        event_data = {
            "inquiry_id": "evt_123",
            "event_date": "2026-12-15",
            "event_name": "Test Event",
            "locked": True,
            "music_lists": {
                "must_play": [
                    {"title": "Uptown Funk", "artist": "Mark Ronson"},
                ],
                "play_if_possible": [],
                "do_not_play": [],
            },
        }
        mock_fetch.return_value = [event_sync_schema.parse_event(event_data)]
        
        db.migrate(temp_db)
        
        try:
            # Populate test database
            with db.get_db(temp_db) as conn:
                for track in library_tracks:
                    from engine.library.model import Track
                    t = Track(path=track["path"])
                    t.title = track["title"]
                    t.artist = track["artist"]
                    t.bpm = track["bpm"]
                    db.save_track(conn, t)
            
            # Mock the Serato adapter's write_crate to track calls
            with mock.patch("engine.library.adapters.serato.SeratoAdapter.write_crate") as mock_write:
                mock_write.return_value = {"dry_run": True}
                
                result = event_sync.sync_events(
                    base_url="https://test.example.com",
                    db_path=temp_db,
                    apply=False,  # Dry-run
                    locked_only=True,
                    push_status=True,  # Would push, but dry-run prevents it
                )
                
                # Verify push was NOT called because apply=False
                mock_push.assert_not_called()
        finally:
            os.unlink(temp_db)


class TestLockedOnlyFilter(unittest.TestCase):
    """Test locked-only event filtering."""
    
    @mock.patch("engine.library.event_sync.fetch_events")
    @mock.patch("engine.library.event_sync.get_device_token")
    def test_locked_only_filters_unlocked_events(self, mock_token, mock_fetch):
        """Test that locked-only mode filters out unlocked events."""
        mock_token.return_value = "test_token"
        
        # Create temporary database
        fd, temp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.migrate(temp_db)
        
        events = [
            event_sync_schema.parse_event({
                "inquiry_id": "evt_locked",
                "event_date": "2026-12-15",
                "locked": True,
            }),
            event_sync_schema.parse_event({
                "inquiry_id": "evt_unlocked",
                "event_date": "2026-12-16",
                "locked": False,
            }),
        ]
        mock_fetch.return_value = events
        
        with mock.patch("engine.library.adapters.serato.SeratoAdapter.write_crate") as mock_write:
            mock_write.return_value = {"dry_run": True}
            
            result = event_sync.sync_events(
                base_url="https://test.example.com",
                db_path=temp_db,
                apply=False,
                locked_only=True,  # Filter to locked only
                push_status=False,
            )
            
            # Only 1 locked event should be processed
            self.assertEqual(result["events_processed"], 1)
        
        os.unlink(temp_db)
    
    @mock.patch("engine.library.event_sync.fetch_events")
    @mock.patch("engine.library.event_sync.get_device_token")
    def test_all_events_includes_unlocked(self, mock_token, mock_fetch):
        """Test that all-events mode includes unlocked events."""
        mock_token.return_value = "test_token"
        
        # Create temporary database
        fd, temp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.migrate(temp_db)
        
        events = [
            event_sync_schema.parse_event({
                "inquiry_id": "evt_locked",
                "event_date": "2026-12-15",
                "locked": True,
            }),
            event_sync_schema.parse_event({
                "inquiry_id": "evt_unlocked",
                "event_date": "2026-12-16",
                "locked": False,
            }),
        ]
        mock_fetch.return_value = events
        
        with mock.patch("engine.library.adapters.serato.SeratoAdapter.write_crate") as mock_write:
            mock_write.return_value = {"dry_run": True}
            
            result = event_sync.sync_events(
                base_url="https://test.example.com",
                db_path=temp_db,
                apply=False,
                locked_only=False,  # Include all events
                push_status=False,
            )
            
            # Both events should be processed
            self.assertEqual(result["events_processed"], 2)
        
        os.unlink(temp_db)


class TestEventCache(unittest.TestCase):
    """Test event cache management."""
    
    def test_cache_event(self):
        """Test caching event data."""
        event = event_sync_schema.EventData(
            inquiry_id="evt_123",
            event_date="2026-12-15",
            event_name="Test Event",
            locked=True,
        )
        
        event_sync.cache_event(event)
        
        cache_file = event_sync.get_cache_path() / "evt_123.json"
        self.assertTrue(cache_file.exists())
        
        with open(cache_file) as f:
            data = json.load(f)
            self.assertEqual(data["inquiry_id"], "evt_123")
            self.assertEqual(data["event_date"], "2026-12-15")
        
        # Cleanup
        cache_file.unlink()
    
    def test_clear_old_events(self):
        """Test clearing old cached events."""
        # Create a cache entry for an old event
        cache_dir = event_sync.get_cache_path()
        old_event_file = cache_dir / "evt_old.json"
        
        with open(old_event_file, "w") as f:
            json.dump({
                "inquiry_id": "evt_old",
                "event_date": "2020-01-01",  # Past date
                "cached_at": time.time(),
            }, f)
        
        event_sync.clear_old_events()
        
        # Old event should be removed
        self.assertFalse(old_event_file.exists())


if __name__ == "__main__":
    unittest.main()
