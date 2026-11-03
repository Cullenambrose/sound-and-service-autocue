"""Comprehensive tests for the library package."""
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from . import db
from .model import Track, Cue, Grid, AudioFile
from .adapters.base import WritePlan
from .adapters.registry import get_adapter
from .tag_gate import (
    tag_write,
    is_protected,
    DEFAULT_PROTECTED,
    compute_audio_sha256,
    create_backup,
    verify_markers_lockstep,
)
from .jobs import JobQueue


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
def synthetic_mp3():
    """Create a tiny synthetic MP3 file for testing."""
    # Minimal valid MP3 frame (silence)
    mp3_data = (
        b"ID3\x04\x00\x00\x00\x00\x00\x00"  # ID3v2.4 header with 0 size
        b"\xff\xfb\x90\x00"  # MP3 frame header (MPEG1 Layer3 128kbps 44.1kHz)
        b"\x00" * 100  # Some audio data
    )
    
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3_data)
        path = f.name
    
    yield path
    
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def protected_mp3():
    """Create a protected MP3 file."""
    mp3_data = b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x00" + b"\x00" * 100
    
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, prefix="Happy") as f:
        f.write(mp3_data)
        path = f.name
    
    # Rename to match protected filename
    new_path = str(Path(path).parent / "Happy.mp3")
    os.rename(path, new_path)
    
    yield new_path
    
    Path(new_path).unlink(missing_ok=True)


# ============================================================================
# Model Tests
# ============================================================================


def test_track_serialization():
    """Test Track to_dict and from_dict."""
    track = Track(path="/test/track.mp3")
    track.title = "Test Track"
    track.artist = "Test Artist"
    track.bpm = 120.0
    track.cues = [
        Cue(index=0, label="Intro", time_ms=1000, color="#FF0000"),
        Cue(index=1, label="Verse", time_ms=5000, color="#00FF00"),
    ]
    track.grid = Grid(bpm=120.0, anchor_s=0.5, source="test")
    
    # Serialize
    data = track.to_dict()
    assert data["path"] == "/test/track.mp3"
    assert data["title"] == "Test Track"
    assert data["bpm"] == 120.0
    assert len(data["cues"]) == 2
    
    # Deserialize
    track2 = Track.from_dict(data)
    assert track2.path == track.path
    assert track2.title == track.title
    assert track2.bpm == track.bpm
    assert len(track2.cues) == 2
    assert track2.cues[0].label == "Intro"
    assert track2.grid.bpm == 120.0


# ============================================================================
# Database Tests
# ============================================================================


def test_migration_idempotent(temp_db):
    """Test that migrations are idempotent."""
    # Run migration twice
    db.migrate(temp_db)
    db.migrate(temp_db)
    
    # Check schema version
    with db.get_db(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM schema_version")
        version = cursor.fetchone()[0]
        assert version == db.SCHEMA_VERSION


def test_save_and_load_track(temp_db):
    """Test saving and loading a track."""
    track = Track(path="/test/track.mp3")
    track.title = "Test Track"
    track.artist = "Test Artist"
    track.bpm = 120.0
    track.cues = [
        Cue(index=0, label="Intro", time_ms=1000, color="#FF0000"),
    ]
    track.grid = Grid(bpm=120.0, anchor_s=0.5, source="test", variable_tempo=False)
    track.file = AudioFile(
        path="/test/track.mp3",
        size=1024,
        mtime=time.time(),
        audio_sha256="abc123",
        format="mp3",
    )
    
    # Save
    with db.get_db(temp_db) as conn:
        track_id = db.save_track(conn, track)
        assert track_id > 0
    
    # Load
    with db.get_db(temp_db) as conn:
        loaded = db.get_track(conn, "/test/track.mp3")
    
    assert loaded is not None
    assert loaded.title == "Test Track"
    assert loaded.bpm == 120.0
    assert len(loaded.cues) == 1
    assert loaded.cues[0].label == "Intro"
    assert loaded.grid.bpm == 120.0
    assert loaded.grid.variable_tempo is False
    assert loaded.file.audio_sha256 == "abc123"


def test_update_track(temp_db):
    """Test updating an existing track."""
    track = Track(path="/test/track.mp3")
    track.title = "Original"
    
    with db.get_db(temp_db) as conn:
        db.save_track(conn, track)
        
        # Update
        track.title = "Updated"
        track.cues = [Cue(index=0, label="New", time_ms=1000)]
        db.save_track(conn, track)
        
        # Load
        loaded = db.get_track(conn, "/test/track.mp3")
    
    assert loaded.title == "Updated"
    assert len(loaded.cues) == 1


def test_log_change(temp_db):
    """Test audit log."""
    with db.get_db(temp_db) as conn:
        change_id = db.log_change(
            conn,
            path="/test/track.mp3",
            adapter="serato",
            operation="write_cues",
            before_summary="0 cues",
            after_summary="3 cues",
            audio_sha256_before="abc",
            audio_sha256_after="abc",
        )
        
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM changes WHERE id = ?", (change_id,))
        change = cursor.fetchone()
        
        assert change is not None
        assert change["path"] == "/test/track.mp3"
        assert change["adapter"] == "serato"
        assert change["operation"] == "write_cues"


# ============================================================================
# Import Graph Test
# ============================================================================


def test_no_serato_imports_in_core():
    """Core model and db should not import Serato modules."""
    from . import model, db
    
    # Check imports
    import sys
    
    # Get all imported modules
    model_file = Path(model.__file__)
    db_file = Path(db.__file__)
    
    # Read the files
    model_content = model_file.read_text()
    db_content = db_file.read_text()
    
    # Check for Serato imports
    assert "serato" not in model_content.lower(), "Model should not import Serato modules"
    assert "serato" not in db_content.lower(), "DB should not import Serato modules"


# ============================================================================
# Tag Gate Tests
# ============================================================================


def test_protected_file_by_name(protected_mp3):
    """Test that protected files are blocked by name."""
    match = is_protected(protected_mp3, DEFAULT_PROTECTED)
    assert match is not None
    assert "Happy" in match["reason"]


def test_protected_file_apply_still_blocks(protected_mp3):
    """Test that protected files are blocked even with --apply."""
    adapter = Mock()
    adapter.name = "serato"
    adapter.detect_running = Mock(return_value=False)
    
    plan = WritePlan(
        path=protected_mp3,
        adapter="serato",
        cues=[],
        metadata={},
    )
    
    with pytest.raises(RuntimeError, match="protected"):
        tag_write(protected_mp3, adapter, plan, apply=True)


def test_serato_running_blocks_write(synthetic_mp3):
    """Test that writes are blocked when Serato is running."""
    adapter = Mock()
    adapter.name = "serato"
    adapter.detect_running = Mock(return_value=True)  # Serato is running
    
    plan = WritePlan(
        path=synthetic_mp3,
        adapter="serato",
        cues=[],
        metadata={},
    )
    
    with pytest.raises(RuntimeError, match="running"):
        tag_write(synthetic_mp3, adapter, plan, apply=True)


def test_dry_run_no_write(synthetic_mp3):
    """Test that dry-run mode doesn't write."""
    original_mtime = Path(synthetic_mp3).stat().st_mtime
    original_data = Path(synthetic_mp3).read_bytes()
    
    adapter = Mock()
    adapter.name = "test"
    adapter.detect_running = Mock(return_value=False)
    
    plan = WritePlan(
        path=synthetic_mp3,
        adapter="test",
        cues=[Cue(index=0, label="Test", time_ms=1000)],
        metadata={},
    )
    
    result = tag_write(synthetic_mp3, adapter, plan, apply=False)
    
    assert result["dry_run"] is True
    assert result["cue_count"] == 1
    
    # Verify file unchanged
    assert Path(synthetic_mp3).stat().st_mtime == original_mtime
    assert Path(synthetic_mp3).read_bytes() == original_data


def test_backup_created_and_verified(synthetic_mp3, temp_db):
    """Test that backups are created and byte-for-byte verified."""
    with tempfile.TemporaryDirectory() as backup_root:
        backup_path = create_backup(synthetic_mp3, backup_root)
        
        assert Path(backup_path).exists()
        
        # Verify byte-for-byte
        original = Path(synthetic_mp3).read_bytes()
        backup = Path(backup_path).read_bytes()
        assert original == backup


def test_audio_sha_mismatch_restores_backup(synthetic_mp3):
    """Test that audio SHA mismatch triggers restore."""
    adapter = Mock()
    adapter.name = "test"
    adapter.detect_running = Mock(return_value=False)
    
    # Mock apply_write to corrupt the file
    def corrupt_file(plan):
        # Append garbage to change audio hash
        with open(synthetic_mp3, "ab") as f:
            f.write(b"CORRUPTED DATA")
        return {}
    
    adapter.apply_write = Mock(side_effect=corrupt_file)
    
    plan = WritePlan(
        path=synthetic_mp3,
        adapter="test",
        cues=[],
        metadata={},
    )
    
    original_data = Path(synthetic_mp3).read_bytes()
    
    with tempfile.TemporaryDirectory() as backup_root:
        with pytest.raises(RuntimeError, match="Audio data changed"):
            tag_write(synthetic_mp3, adapter, plan, apply=True, backup_root=backup_root)
        
        # Verify file was restored
        restored_data = Path(synthetic_mp3).read_bytes()
        assert restored_data == original_data


def test_start_cue_time_preserved(temp_db):
    """Test that START cue time is preserved exactly (no grid snap)."""
    # This is tested by verifying that the cue time in the plan
    # matches the time we put in, with no snapping applied
    track = Track(path="/test/track.mp3")
    track.cues = [
        Cue(index=0, label="START", time_ms=1234, color="#FF0000"),  # Odd time, not on grid
    ]
    track.grid = Grid(bpm=120.0, anchor_s=0.0)
    
    adapter = get_adapter("serato")
    if adapter:
        plan = adapter.plan_write(track, track.cues)
        
        # Verify the time wasn't snapped
        assert plan.cues[0].time_ms == 1234


# ============================================================================
# Adapter Tests
# ============================================================================


def test_serato_adapter_registration():
    """Test that Serato adapter is registered."""
    adapter = get_adapter("serato")
    assert adapter is not None
    assert adapter.name == "serato"


def test_serato_detect_running_injectable():
    """Test that Serato process detection is injectable for tests."""
    from .adapters.serato import SeratoAdapter
    
    # Test with mock
    mock_checker = Mock(return_value=True)
    adapter = SeratoAdapter(process_checker=mock_checker)
    
    assert adapter.detect_running() is True
    mock_checker.assert_called_once()


def test_rekordbox_stub_raises():
    """Test that Rekordbox stub raises NotImplementedError."""
    adapter = get_adapter("rekordbox")
    assert adapter is not None
    
    with pytest.raises(NotImplementedError):
        adapter.detect_running()
    
    with pytest.raises(NotImplementedError):
        adapter.read_track("/test/track.mp3")


def test_ss_booking_stub_raises():
    """Test that SS Booking stub raises NotImplementedError."""
    adapter = get_adapter("ss_booking")
    assert adapter is not None
    
    with pytest.raises(NotImplementedError):
        adapter.list_crates()
    
    with pytest.raises(NotImplementedError):
        adapter.import_event_crate("test-event")


def test_stub_adapters_satisfy_abc():
    """Test that stub adapters satisfy the ABC."""
    from .adapters.base import DJSoftwareAdapter
    
    rekordbox = get_adapter("rekordbox")
    ss_booking = get_adapter("ss_booking")
    
    assert isinstance(rekordbox, DJSoftwareAdapter)
    assert isinstance(ss_booking, DJSoftwareAdapter)


# ============================================================================
# Job Queue Tests
# ============================================================================


def test_job_queue_create_and_status(temp_db):
    """Test creating a job and checking status."""
    queue = JobQueue(temp_db)
    
    tracks = ["/test/track1.mp3", "/test/track2.mp3"]
    job_id = queue.create_job(tracks)
    
    status = queue.get_job_status(job_id)
    assert status["job_id"] == job_id
    assert status["status"] == "pending"
    assert status["track_count"] == 2


def test_job_queue_process(temp_db):
    """Test processing a job."""
    queue = JobQueue(temp_db)
    
    tracks = ["/test/track1.mp3", "/test/track2.mp3"]
    job_id = queue.create_job(tracks)
    
    processed = []
    
    def processor(track_path):
        processed.append(track_path)
        return {"status": "ok"}
    
    status = queue.process_job(job_id, processor)
    
    assert status["status"] == "completed"
    assert status["completed_count"] == 2
    assert len(processed) == 2


def test_job_queue_error_handling(temp_db):
    """Test that one failing track doesn't stop others."""
    queue = JobQueue(temp_db)
    
    tracks = ["/test/track1.mp3", "/test/track2.mp3", "/test/track3.mp3"]
    job_id = queue.create_job(tracks)
    
    processed = []
    
    def processor(track_path):
        processed.append(track_path)
        if "track2" in track_path:
            raise ValueError("Simulated error")
        return {"status": "ok"}
    
    status = queue.process_job(job_id, processor)
    
    assert status["status"] == "completed_with_errors"
    assert status["completed_count"] == 3
    assert status["error_count"] == 1
    assert len(processed) == 3  # All tracks were attempted


def test_job_queue_resume_after_simulated_kill(temp_db):
    """Test resuming a job after simulated kill."""
    queue = JobQueue(temp_db)
    
    tracks = ["/test/track1.mp3", "/test/track2.mp3", "/test/track3.mp3"]
    job_id = queue.create_job(tracks)
    
    # Simulate partial processing by marking some tracks
    with db.get_db(temp_db) as conn:
        conn.execute(
            "UPDATE job_tracks SET status='completed' WHERE track_path=?",
            ("/test/track1.mp3",),
        )
        conn.execute(
            "UPDATE job_tracks SET status='processing' WHERE track_path=?",
            ("/test/track2.mp3",),
        )
        conn.execute(
            "UPDATE jobs SET status='paused' WHERE id=?",
            (job_id,),
        )
        conn.commit()
    
    # Resume
    processed = []
    
    def processor(track_path):
        processed.append(track_path)
        return {"status": "ok"}
    
    status = queue.resume_job(job_id, processor)
    
    # Should process track2 (was interrupted) and track3 (pending)
    assert status["status"] == "completed"
    assert len(processed) == 2


# ============================================================================
# Variable Tempo Test
# ============================================================================


@pytest.mark.skipif(
    not Path("engine/test_serato_grid.py").exists(),
    reason="Requires tempomap test fixture from PR #2"
)
def test_serato_reads_variable_tempo_tempomap(temp_db):
    """Test that Serato adapter reads Tempo Map and flags variable tempo."""
    # This test would use the YMMD fixture from PR #2 if available
    # For now, it's a placeholder showing the structure
    
    adapter = get_adapter("serato")
    assert adapter is not None
    
    # Would read a fixture file with a tempo map
    # cues, grid, tags, adapter_data = adapter.read_track(fixture_path)
    # assert grid is not None
    # assert grid.variable_tempo is True
    # assert grid.source == "serato_tempomap"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
