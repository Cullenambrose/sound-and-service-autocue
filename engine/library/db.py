"""SQLite database layer with schema migrations for the library."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict, Any, List

from .model import Track, Cue, Grid, AudioFile, Crate, Analysis, Change


SCHEMA_VERSION = 1

MIGRATIONS = {
    1: """
    CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
    INSERT INTO schema_version VALUES (1);
    
    CREATE TABLE tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT UNIQUE NOT NULL,
        title TEXT,
        artist TEXT,
        album TEXT,
        genre TEXT,
        bpm REAL,
        key TEXT,
        duration_s REAL,
        adapter_data TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX idx_tracks_path ON tracks(path);
    
    CREATE TABLE files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        audio_sha256 TEXT NOT NULL,
        format TEXT NOT NULL
    );
    CREATE INDEX idx_files_track ON files(track_id);
    
    CREATE TABLE tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT NOT NULL
    );
    CREATE INDEX idx_tags_track ON tags(track_id);
    
    CREATE TABLE cues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        index_num INTEGER NOT NULL,
        label TEXT NOT NULL,
        time_ms INTEGER NOT NULL,
        color TEXT,
        kind TEXT
    );
    CREATE INDEX idx_cues_track ON cues(track_id);
    
    CREATE TABLE grids (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        bpm REAL NOT NULL,
        anchor_s REAL NOT NULL,
        markers TEXT,
        source TEXT,
        variable_tempo INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_grids_track ON grids(track_id);
    
    CREATE TABLE crates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        source TEXT NOT NULL,
        external_id TEXT
    );
    
    CREATE TABLE crate_tracks (
        crate_id INTEGER NOT NULL REFERENCES crates(id) ON DELETE CASCADE,
        track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        position INTEGER,
        PRIMARY KEY (crate_id, track_id)
    );
    CREATE INDEX idx_crate_tracks_crate ON crate_tracks(crate_id);
    CREATE INDEX idx_crate_tracks_track ON crate_tracks(track_id);
    
    CREATE TABLE analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        version TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX idx_analysis_track ON analysis(track_id);
    
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        track_count INTEGER NOT NULL DEFAULT 0,
        completed_count INTEGER NOT NULL DEFAULT 0,
        error_count INTEGER NOT NULL DEFAULT 0,
        config TEXT
    );
    
    CREATE TABLE job_tracks (
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        track_path TEXT NOT NULL,
        status TEXT NOT NULL,
        error TEXT,
        started_at REAL,
        completed_at REAL,
        PRIMARY KEY (job_id, track_path)
    );
    CREATE INDEX idx_job_tracks_job ON job_tracks(job_id);
    
    CREATE TABLE changes (
        id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        adapter TEXT NOT NULL,
        operation TEXT NOT NULL,
        before_summary TEXT,
        after_summary TEXT,
        backup_path TEXT,
        audio_sha256_before TEXT,
        audio_sha256_after TEXT,
        timestamp REAL NOT NULL
    );
    CREATE INDEX idx_changes_path ON changes(path);
    CREATE INDEX idx_changes_timestamp ON changes(timestamp);
    """
}


@contextmanager
def get_db(db_path: str):
    """Context manager for database connection."""
    db_path = Path(db_path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    
    try:
        yield conn
    finally:
        conn.close()


def migrate(db_path: str):
    """Run migrations to bring the database to the latest schema version."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        
        # Check if schema_version table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if not cursor.fetchone():
            current_version = 0
        else:
            cursor.execute("SELECT version FROM schema_version")
            result = cursor.fetchone()
            current_version = result[0] if result else 0
        
        # Run migrations
        for version in sorted(MIGRATIONS.keys()):
            if version > current_version:
                conn.executescript(MIGRATIONS[version])
                conn.commit()
        
        # Verify final version
        cursor.execute("SELECT version FROM schema_version")
        final_version = cursor.fetchone()[0]
        if final_version != SCHEMA_VERSION:
            raise RuntimeError(
                f"Migration failed: expected version {SCHEMA_VERSION}, got {final_version}"
            )


def save_track(conn: sqlite3.Connection, track: Track) -> int:
    """Save or update a track in the database. Returns track ID."""
    cursor = conn.cursor()
    now = time.time()
    
    # Check if track exists
    cursor.execute("SELECT id FROM tracks WHERE path = ?", (track.path,))
    existing = cursor.fetchone()
    
    adapter_data_json = json.dumps(track.adapter_data) if track.adapter_data else None
    
    if existing:
        track_id = existing[0]
        cursor.execute(
            """UPDATE tracks 
               SET title=?, artist=?, album=?, genre=?, bpm=?, key=?, 
                   duration_s=?, adapter_data=?, updated_at=?
               WHERE id=?""",
            (
                track.title,
                track.artist,
                track.album,
                track.genre,
                track.bpm,
                track.key,
                track.duration_s,
                adapter_data_json,
                now,
                track_id,
            ),
        )
        # Delete existing related records
        cursor.execute("DELETE FROM cues WHERE track_id=?", (track_id,))
        cursor.execute("DELETE FROM grids WHERE track_id=?", (track_id,))
        cursor.execute("DELETE FROM files WHERE track_id=?", (track_id,))
    else:
        cursor.execute(
            """INSERT INTO tracks 
               (path, title, artist, album, genre, bpm, key, duration_s, adapter_data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                track.path,
                track.title,
                track.artist,
                track.album,
                track.genre,
                track.bpm,
                track.key,
                track.duration_s,
                adapter_data_json,
                now,
                now,
            ),
        )
        track_id = cursor.lastrowid
    
    # Save file info
    if track.file:
        cursor.execute(
            """INSERT INTO files (track_id, path, size, mtime, audio_sha256, format)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                track_id,
                track.file.path,
                track.file.size,
                track.file.mtime,
                track.file.audio_sha256,
                track.file.format,
            ),
        )
    
    # Save cues
    for cue in track.cues:
        cursor.execute(
            """INSERT INTO cues (track_id, index_num, label, time_ms, color, kind)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (track_id, cue.index, cue.label, cue.time_ms, cue.color, cue.kind),
        )
    
    # Save grid
    if track.grid:
        markers_json = json.dumps(track.grid.markers) if track.grid.markers else None
        cursor.execute(
            """INSERT INTO grids (track_id, bpm, anchor_s, markers, source, variable_tempo)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                track_id,
                track.grid.bpm,
                track.grid.anchor_s,
                markers_json,
                track.grid.source,
                1 if track.grid.variable_tempo else 0,
            ),
        )
    
    conn.commit()
    return track_id


def get_track(conn: sqlite3.Connection, path: str) -> Optional[Track]:
    """Load a track from the database by path."""
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM tracks WHERE path = ?", (path,))
    row = cursor.fetchone()
    if not row:
        return None
    
    track = Track(path=row["path"])
    track.title = row["title"]
    track.artist = row["artist"]
    track.album = row["album"]
    track.genre = row["genre"]
    track.bpm = row["bpm"]
    track.key = row["key"]
    track.duration_s = row["duration_s"]
    
    if row["adapter_data"]:
        track.adapter_data = json.loads(row["adapter_data"])
    
    track_id = row["id"]
    
    # Load file
    cursor.execute("SELECT * FROM files WHERE track_id = ?", (track_id,))
    file_row = cursor.fetchone()
    if file_row:
        track.file = AudioFile(
            path=file_row["path"],
            size=file_row["size"],
            mtime=file_row["mtime"],
            audio_sha256=file_row["audio_sha256"],
            format=file_row["format"],
        )
    
    # Load cues
    cursor.execute("SELECT * FROM cues WHERE track_id = ? ORDER BY index_num", (track_id,))
    track.cues = [
        Cue(
            index=row["index_num"],
            label=row["label"],
            time_ms=row["time_ms"],
            color=row["color"],
            kind=row["kind"],
        )
        for row in cursor.fetchall()
    ]
    
    # Load grid
    cursor.execute("SELECT * FROM grids WHERE track_id = ?", (track_id,))
    grid_row = cursor.fetchone()
    if grid_row:
        markers = json.loads(grid_row["markers"]) if grid_row["markers"] else []
        track.grid = Grid(
            bpm=grid_row["bpm"],
            anchor_s=grid_row["anchor_s"],
            markers=markers,
            source=grid_row["source"],
            variable_tempo=bool(grid_row["variable_tempo"]),
        )
    
    return track


def log_change(
    conn: sqlite3.Connection,
    path: str,
    adapter: str,
    operation: str,
    before_summary: Optional[str] = None,
    after_summary: Optional[str] = None,
    backup_path: Optional[str] = None,
    audio_sha256_before: Optional[str] = None,
    audio_sha256_after: Optional[str] = None,
) -> str:
    """Log a change to the audit log. Returns change ID."""
    change_id = uuid.uuid4().hex
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO changes 
           (id, path, adapter, operation, before_summary, after_summary, 
            backup_path, audio_sha256_before, audio_sha256_after, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            change_id,
            path,
            adapter,
            operation,
            before_summary,
            after_summary,
            backup_path,
            audio_sha256_before,
            audio_sha256_after,
            time.time(),
        ),
    )
    conn.commit()
    return change_id
