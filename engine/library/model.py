"""Core dataclasses for the library layer. Platform-independent track, cue, grid, and crate models."""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import json


@dataclass
class AudioFile:
    """Audio file metadata and fingerprint."""
    path: str
    size: int
    mtime: float
    audio_sha256: str
    format: str


@dataclass
class Cue:
    """A cue point or hotcue."""
    index: int
    label: str
    time_ms: int
    color: Optional[str] = None
    kind: Optional[str] = None


@dataclass
class Grid:
    """Beat grid representation."""
    bpm: float
    anchor_s: float
    markers: List[Dict[str, Any]] = field(default_factory=list)
    source: Optional[str] = None
    variable_tempo: bool = False


@dataclass
class Crate:
    """A playlist or crate."""
    name: str
    source: str
    external_id: Optional[str] = None


@dataclass
class Analysis:
    """Analysis payload."""
    version: str
    payload: Dict[str, Any]
    created_at: float


@dataclass
class Job:
    """Background job status."""
    job_id: str
    status: str
    created_at: float
    updated_at: float
    track_count: int = 0
    completed_count: int = 0
    error_count: int = 0


@dataclass
class Change:
    """Audit log entry."""
    change_id: str
    path: str
    adapter: str
    operation: str
    before_summary: Optional[str]
    after_summary: Optional[str]
    backup_path: Optional[str]
    audio_sha256_before: Optional[str]
    audio_sha256_after: Optional[str]
    timestamp: float


@dataclass
class Track:
    """Core track model with metadata and vendor-agnostic fields."""
    path: str
    file: Optional[AudioFile] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    bpm: Optional[float] = None
    key: Optional[str] = None
    duration_s: Optional[float] = None
    cues: List[Cue] = field(default_factory=list)
    grid: Optional[Grid] = None
    adapter_data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        result = {
            "path": self.path,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "genre": self.genre,
            "bpm": self.bpm,
            "key": self.key,
            "duration_s": self.duration_s,
            "cues": [
                {
                    "index": c.index,
                    "label": c.label,
                    "time_ms": c.time_ms,
                    "color": c.color,
                    "kind": c.kind,
                }
                for c in self.cues
            ],
        }
        if self.grid:
            result["grid"] = {
                "bpm": self.grid.bpm,
                "anchor_s": self.grid.anchor_s,
                "markers": self.grid.markers,
                "source": self.grid.source,
                "variable_tempo": self.grid.variable_tempo,
            }
        if self.file:
            result["file"] = {
                "path": self.file.path,
                "size": self.file.size,
                "mtime": self.file.mtime,
                "audio_sha256": self.file.audio_sha256,
                "format": self.file.format,
            }
        if self.adapter_data:
            result["adapter_data"] = self.adapter_data
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Track":
        """Deserialize from dict."""
        track = cls(path=data["path"])
        track.title = data.get("title")
        track.artist = data.get("artist")
        track.album = data.get("album")
        track.genre = data.get("genre")
        track.bpm = data.get("bpm")
        track.key = data.get("key")
        track.duration_s = data.get("duration_s")
        
        if "cues" in data:
            track.cues = [
                Cue(
                    index=c["index"],
                    label=c["label"],
                    time_ms=c["time_ms"],
                    color=c.get("color"),
                    kind=c.get("kind"),
                )
                for c in data["cues"]
            ]
        
        if "grid" in data:
            g = data["grid"]
            track.grid = Grid(
                bpm=g["bpm"],
                anchor_s=g["anchor_s"],
                markers=g.get("markers", []),
                source=g.get("source"),
                variable_tempo=g.get("variable_tempo", False),
            )
        
        if "file" in data:
            f = data["file"]
            track.file = AudioFile(
                path=f["path"],
                size=f["size"],
                mtime=f["mtime"],
                audio_sha256=f["audio_sha256"],
                format=f["format"],
            )
        
        track.adapter_data = data.get("adapter_data")
        return track
