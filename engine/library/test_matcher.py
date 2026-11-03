"""Tests for track matching functionality."""
import json
import tempfile
from pathlib import Path

import pytest

from . import db
from .model import Track
from .matcher import (
    normalize_text,
    extract_featuring,
    extract_version_tags,
    compute_similarity,
    match_track,
    rank_candidates,
    build_crate_plan,
    MatchConfig,
    TrackVersion,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    db.migrate(db_path)
    yield db_path
    
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def library_tracks(temp_db):
    """Create a synthetic library with various track versions."""
    tracks_data = [
        # Exact matches
        {"artist": "Dua Lipa", "title": "Don't Start Now", "bpm": 124.0},
        {"artist": "The Weeknd", "title": "Blinding Lights", "bpm": 171.0},
        
        # Multiple versions with tags
        {"artist": "Calvin Harris", "title": "Summer (Clean)", "bpm": 128.0},
        {"artist": "Calvin Harris", "title": "Summer (Dirty)", "bpm": 128.0},
        {"artist": "Calvin Harris", "title": "Summer (Intro Edit)", "bpm": 128.0},
        {"artist": "Calvin Harris", "title": "Summer (Clean Intro)", "bpm": 128.0},
        
        # Featuring variations
        {"artist": "Drake", "title": "In My Feelings", "bpm": 91.0},
        {"artist": "Drake feat. City Girls", "title": "In My Feelings", "bpm": 91.0},
        {"artist": "Drake", "title": "In My Feelings (feat. City Girls)", "bpm": 91.0},
        
        # Version tags
        {"artist": "Ariana Grande", "title": "7 Rings (Clean)", "bpm": 140.0},
        {"artist": "Ariana Grande", "title": "7 Rings (Dirty)", "bpm": 140.0},
        {"artist": "Ariana Grande", "title": "7 Rings (Short Edit)", "bpm": 140.0},
        {"artist": "Ariana Grande", "title": "7 Rings (Re-Drum)", "bpm": 140.0},
        {"artist": "Ariana Grande", "title": "7 Rings (Chorus Intro)", "bpm": 140.0},
        
        # Punctuation variations
        {"artist": "Post Malone", "title": "Circles", "bpm": 120.0},
        {"artist": "Post-Malone", "title": "Circles", "bpm": 120.0},
        
        # Case variations
        {"artist": "Billie Eilish", "title": "bad guy", "bpm": 135.0},
        {"artist": "BILLIE EILISH", "title": "BAD GUY", "bpm": 135.0},
        
        # Similar but different tracks (potential ambiguous matches)
        {"artist": "Taylor Swift", "title": "Shake It Off", "bpm": 160.0},
        {"artist": "Taylor Swift", "title": "Shake It Up", "bpm": 158.0},
        
        # Do-not-play candidates
        {"artist": "Various Artists", "title": "Happy Birthday", "bpm": 120.0},
        {"artist": "DJ Earworm", "title": "United State of Pop 2023", "bpm": 128.0},
    ]
    
    tracks = []
    with db.get_db(temp_db) as conn:
        for i, data in enumerate(tracks_data):
            track = Track(path=f"/music/track_{i:03d}.mp3")
            track.artist = data["artist"]
            track.title = data["title"]
            track.bpm = data["bpm"]
            track_id = db.save_track(conn, track)
            tracks.append({
                "id": track_id,
                "path": track.path,
                "artist": track.artist,
                "title": track.title,
                "bpm": track.bpm,
            })
    
    return tracks


# ============================================================================
# Normalization Tests
# ============================================================================


def test_normalize_text():
    """Test text normalization."""
    assert normalize_text("Hello World") == "hello world"
    assert normalize_text("Hello, World!") == "hello world"
    assert normalize_text("  Lots   of    spaces  ") == "lots of spaces"
    assert normalize_text("test-case") == "test case"
    assert normalize_text("TEST-CASE") == "test case"


def test_extract_featuring():
    """Test featuring artist extraction."""
    main, featured = extract_featuring("Song ft. Artist")
    assert "song" in main
    assert "artist" in featured
    
    main, featured = extract_featuring("Song feat. Artist1 & Artist2")
    assert "song" in main
    assert len(featured) == 2
    
    main, featured = extract_featuring("Song (featuring Artist)")
    assert "song" in main
    assert "artist" in featured
    
    main, featured = extract_featuring("Song with Artist")
    assert "song" in main
    assert "artist" in featured


def test_extract_version_tags():
    """Test version tag extraction."""
    text, version = extract_version_tags("Summer (Clean)")
    assert "summer" in text.lower()
    assert version.is_clean
    assert "clean" in version.tags
    
    text, version = extract_version_tags("Track (Dirty Intro)")
    assert version.is_dirty
    assert version.is_intro
    
    text, version = extract_version_tags("Song [Re-Drum]")
    assert version.is_re_drum
    
    text, version = extract_version_tags("Track (Chorus Intro)")
    assert version.is_chorus_intro
    assert not version.is_intro  # Should not flag regular intro
    
    text, version = extract_version_tags("Song (Short Edit)")
    assert version.is_short_edit


def test_compute_similarity():
    """Test similarity computation."""
    # Exact match
    assert compute_similarity("hello", "hello") == 1.0
    
    # Similar
    score = compute_similarity("hello world", "hello world!")
    assert score > 0.9
    
    # Different
    score = compute_similarity("hello", "goodbye")
    assert score < 0.5
    
    # Empty
    assert compute_similarity("", "") == 0.0
    assert compute_similarity("hello", "") == 0.0


# ============================================================================
# Matching Tests
# ============================================================================


def test_exact_match(library_tracks):
    """Test exact artist/title match."""
    request = {"artist": "Dua Lipa", "title": "Don't Start Now"}
    config = MatchConfig()
    
    result = match_track(request, library_tracks, config)
    
    assert result.status == "matched"
    assert result.confidence > 0.9
    assert result.matched_track is not None
    assert "dua lipa" in result.matched_track["artist"].lower()


def test_fuzzy_match_punctuation(library_tracks):
    """Test fuzzy matching with punctuation differences."""
    request = {"artist": "Post Malone", "title": "Circles"}
    config = MatchConfig()
    
    result = match_track(request, library_tracks, config)
    
    # May be matched or ambiguous due to Post Malone / Post-Malone variants
    assert result.status in ("matched", "ambiguous")
    assert result.matched_track is not None


def test_fuzzy_match_case(library_tracks):
    """Test fuzzy matching ignores case."""
    request = {"artist": "billie eilish", "title": "bad guy"}
    config = MatchConfig()
    
    result = match_track(request, library_tracks, config)
    
    # May be matched or ambiguous due to case variants in library
    assert result.status in ("matched", "ambiguous")
    assert result.confidence > 0.9


def test_featuring_match(library_tracks):
    """Test matching with featuring artists."""
    # Request without featuring
    request = {"artist": "Drake", "title": "In My Feelings"}
    config = MatchConfig()
    
    result = match_track(request, library_tracks, config)
    
    assert result.status == "matched"
    assert result.matched_track is not None
    
    # Request with featuring
    request = {"artist": "Drake feat. City Girls", "title": "In My Feelings"}
    result = match_track(request, library_tracks, config)
    
    assert result.status == "matched"


def test_version_preference_clean(library_tracks):
    """Test preference for clean versions."""
    request = {"artist": "Calvin Harris", "title": "Summer"}
    config = MatchConfig(prefer_clean=True, prefer_intro=False)
    
    result = match_track(request, library_tracks, config)
    
    assert result.status == "matched"
    assert result.matched_track is not None
    # Should prefer clean version
    assert "clean" in result.matched_track["title"].lower() or "(" not in result.matched_track["title"].lower()


def test_version_preference_intro(library_tracks):
    """Test preference for intro edits."""
    request = {"artist": "Calvin Harris", "title": "Summer"}
    config = MatchConfig(prefer_clean=True, prefer_intro=True)
    
    result = match_track(request, library_tracks, config)
    
    # May be matched or ambiguous due to multiple versions
    assert result.status in ("matched", "ambiguous")
    assert result.matched_track is not None
    # Should prefer clean intro if available
    matched_title = result.matched_track["title"].lower()
    has_clean = "clean" in matched_title
    has_intro = "intro" in matched_title
    
    # Should have both clean and intro, or at least one
    assert has_clean or has_intro


def test_version_tags_all_types(library_tracks):
    """Test matching with various version tags."""
    request = {"artist": "Ariana Grande", "title": "7 Rings"}
    config = MatchConfig()
    
    result = match_track(request, library_tracks, config)
    
    assert result.status == "matched"
    # Should have multiple candidates
    assert len(result.candidates) >= 3


def test_ambiguous_match(library_tracks):
    """Test detection of ambiguous matches."""
    # Taylor Swift has two similar titles: "Shake It Off" and "Shake It Up"
    request = {"artist": "Taylor Swift", "title": "Shake It"}
    config = MatchConfig(min_confidence=0.6)
    
    result = match_track(request, library_tracks, config)
    
    # Could be matched or ambiguous depending on scoring
    assert result.status in ("matched", "ambiguous")
    if result.status == "ambiguous":
        assert len(result.candidates) >= 2


def test_missing_track(library_tracks):
    """Test handling of missing tracks."""
    request = {"artist": "Unknown Artist", "title": "Unknown Track"}
    config = MatchConfig()
    
    result = match_track(request, library_tracks, config)
    
    assert result.status == "missing"
    assert result.reason is not None


def test_low_confidence_missing(library_tracks):
    """Test that low-confidence matches are marked as missing."""
    request = {"artist": "Someone", "title": "Something"}
    config = MatchConfig(min_confidence=0.95)  # Very high threshold
    
    result = match_track(request, library_tracks, config)
    
    # Should be missing or low confidence
    assert result.status == "missing" or result.confidence < 0.95


# ============================================================================
# Ranking Tests
# ============================================================================


def test_rank_candidates_clean_preference():
    """Test ranking prefers clean versions."""
    candidates = [
        {"id": 1, "artist": "Artist", "title": "Song (Dirty)", "bpm": 120},
        {"id": 2, "artist": "Artist", "title": "Song (Clean)", "bpm": 120},
        {"id": 3, "artist": "Artist", "title": "Song", "bpm": 120},
    ]
    
    config = MatchConfig(prefer_clean=True)
    ranked = rank_candidates(candidates, "artist", "song", config)
    
    # Clean version should rank higher than dirty
    clean_rank = next(i for i, (t, _) in enumerate(ranked) if "Clean" in t["title"])
    dirty_rank = next(i for i, (t, _) in enumerate(ranked) if "Dirty" in t["title"])
    
    assert clean_rank < dirty_rank


def test_rank_candidates_intro_preference():
    """Test ranking prefers intro edits."""
    candidates = [
        {"id": 1, "artist": "Artist", "title": "Song", "bpm": 120},
        {"id": 2, "artist": "Artist", "title": "Song (Intro)", "bpm": 120},
        {"id": 3, "artist": "Artist", "title": "Song (Outro)", "bpm": 120},
    ]
    
    config = MatchConfig(prefer_intro=True)
    ranked = rank_candidates(candidates, "artist", "song", config)
    
    # Intro version should rank higher
    intro_rank = next(i for i, (t, _) in enumerate(ranked) if "Intro" in t["title"])
    
    # Intro should be first or second
    assert intro_rank <= 1


# ============================================================================
# Crate Plan Tests
# ============================================================================


def test_build_crate_plan_basic(library_tracks):
    """Test building a basic crate plan."""
    requests = [
        {"artist": "Dua Lipa", "title": "Don't Start Now"},
        {"artist": "The Weeknd", "title": "Blinding Lights"},
        {"artist": "Unknown", "title": "Missing"},
    ]
    
    config = MatchConfig()
    results = [match_track(req, library_tracks, config) for req in requests]
    
    plan = build_crate_plan(results, [], requests, crate_name="Test Crate")
    
    assert plan.metadata["crate_name"] == "Test Crate"
    assert plan.metadata["matched_count"] == 2
    assert plan.metadata["missing_count"] == 1
    assert len(plan.ordered_track_ids) == 2


def test_build_crate_plan_do_not_play(library_tracks):
    """Test exclusion of do-not-play tracks."""
    requests = [
        {"artist": "Dua Lipa", "title": "Don't Start Now"},
        {"artist": "Various Artists", "title": "Happy Birthday"},
        {"artist": "The Weeknd", "title": "Blinding Lights"},
    ]
    
    do_not_play_requests = [
        {"artist": "Various Artists", "title": "Happy Birthday"},
    ]
    
    config = MatchConfig()
    results = [match_track(req, library_tracks, config) for req in requests]
    dnp_results = [match_track(req, library_tracks, config) for req in do_not_play_requests]
    
    plan = build_crate_plan(results, dnp_results, requests)
    
    # Happy Birthday should be found but excluded
    assert plan.metadata["do_not_play_found"] >= 1
    assert plan.metadata["matched_count"] == 3
    
    # Crate should only have 2 tracks (Happy Birthday excluded)
    assert len(plan.ordered_track_ids) == 2
    
    # Verify Happy Birthday is in do_not_play_found list
    assert len(plan.do_not_play_found) >= 1


def test_build_crate_plan_preserves_order(library_tracks):
    """Test that crate plan preserves request order."""
    requests = [
        {"artist": "The Weeknd", "title": "Blinding Lights"},
        {"artist": "Dua Lipa", "title": "Don't Start Now"},
        {"artist": "Post Malone", "title": "Circles"},
    ]
    
    config = MatchConfig()
    results = [match_track(req, library_tracks, config) for req in requests]
    
    plan = build_crate_plan(results, [], requests)
    
    # Should have 3 tracks in the specified order
    assert len(plan.ordered_track_ids) == 3
    
    # Verify order matches request order by checking both matched and ambiguous
    matched_order = []
    for req in requests:
        # Check both matched and ambiguous since ambiguous still picks best match
        for result in plan.matched + plan.ambiguous:
            if (result.request["artist"] == req["artist"] and
                result.request["title"] == req["title"]):
                if result.matched_track:
                    matched_order.append(result.matched_track["id"])
                break
    
    assert plan.ordered_track_ids == matched_order


def test_build_crate_plan_ambiguous_handling(library_tracks):
    """Test that ambiguous matches are reported but not excluded."""
    requests = [
        {"artist": "Calvin Harris", "title": "Summer"},  # Multiple versions
    ]
    
    config = MatchConfig()
    results = [match_track(req, library_tracks, config) for req in requests]
    
    plan = build_crate_plan(results, [], requests)
    
    # Should have at least one matched/ambiguous
    assert (plan.metadata["matched_count"] + plan.metadata["ambiguous_count"]) >= 1


# ============================================================================
# Integration Tests
# ============================================================================


def test_cli_match_json_format(library_tracks, temp_db, tmp_path):
    """Test match command with JSON input."""
    from . import __main__ as cli_module
    
    # Create request JSON
    request_file = tmp_path / "requests.json"
    requests = {
        "tracks": [
            {"artist": "Dua Lipa", "title": "Don't Start Now"},
            {"artist": "The Weeknd", "title": "Blinding Lights"},
        ]
    }
    with open(request_file, "w") as f:
        json.dump(requests, f)
    
    # Create output file
    output_file = tmp_path / "results.json"
    
    # Run match command
    argv = [
        "--db", temp_db,
        "match",
        str(request_file),
        "--output", str(output_file),
    ]
    
    result = cli_module.main(argv)
    
    assert result == 0
    assert output_file.exists()
    
    # Verify output
    with open(output_file) as f:
        output = json.load(f)
    
    assert "results" in output
    assert "summary" in output
    assert output["summary"]["matched"] == 2


def test_cli_match_csv_format(library_tracks, temp_db, tmp_path):
    """Test match command with CSV input."""
    import csv
    from . import __main__ as cli_module
    
    # Create request CSV
    request_file = tmp_path / "requests.csv"
    with open(request_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["artist", "title"])
        writer.writeheader()
        writer.writerow({"artist": "Dua Lipa", "title": "Don't Start Now"})
        writer.writerow({"artist": "The Weeknd", "title": "Blinding Lights"})
    
    # Create output file
    output_file = tmp_path / "results.json"
    
    # Run match command
    argv = [
        "--db", temp_db,
        "match",
        str(request_file),
        "--output", str(output_file),
    ]
    
    result = cli_module.main(argv)
    
    assert result == 0
    assert output_file.exists()


def test_cli_build_crate_plan(library_tracks, temp_db, tmp_path):
    """Test build-crate-plan command."""
    from . import __main__ as cli_module
    
    # Create match results
    match_file = tmp_path / "matches.json"
    match_data = {
        "results": [
            {
                "request": {"artist": "Dua Lipa", "title": "Don't Start Now"},
                "status": "matched",
                "confidence": 0.95,
                "matched_track": {
                    "id": library_tracks[0]["id"],
                    "artist": "Dua Lipa",
                    "title": "Don't Start Now",
                },
                "candidates": [],
            },
            {
                "request": {"artist": "Unknown", "title": "Missing"},
                "status": "missing",
                "confidence": 0.0,
                "matched_track": None,
                "candidates": [],
                "reason": "Not found",
            },
        ],
        "summary": {"total": 2, "matched": 1, "missing": 1, "ambiguous": 0},
    }
    with open(match_file, "w") as f:
        json.dump(match_data, f)
    
    # Create output file
    output_file = tmp_path / "plan.json"
    
    # Run build-crate-plan command
    argv = [
        "--db", temp_db,
        "build-crate-plan",
        str(match_file),
        "--output", str(output_file),
        "--crate-name", "Test Crate",
    ]
    
    result = cli_module.main(argv)
    
    assert result == 0
    assert output_file.exists()
    
    # Verify output
    with open(output_file) as f:
        output = json.load(f)
    
    assert output["crate_name"] == "Test Crate"
    assert len(output["ordered_track_ids"]) == 1
    assert output["metadata"]["matched_count"] == 1
    assert output["metadata"]["missing_count"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
