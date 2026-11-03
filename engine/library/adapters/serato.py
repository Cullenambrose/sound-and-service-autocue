"""Serato DJ Pro adapter."""
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Callable

from ..model import Cue, Grid, Track
from .base import DJSoftwareAdapter, WritePlan
from .registry import register


# Import existing Serato modules
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import serato_tags
import serato_grid
import serato_writer


@register
class SeratoAdapter(DJSoftwareAdapter):
    """Adapter for Serato DJ Pro."""
    
    def __init__(self, process_checker: Optional[Callable[[], bool]] = None):
        """Initialize the Serato adapter.
        
        Args:
            process_checker: Optional callable to check if Serato is running.
                           Used for testing. Defaults to checking system processes.
        """
        self._process_checker = process_checker
    
    @property
    def name(self) -> str:
        return "serato"
    
    def detect_running(self) -> bool:
        """Check if Serato DJ Pro is currently running."""
        if self._process_checker:
            return self._process_checker()
        
        # Check for Serato DJ Pro process
        if sys.platform == "darwin":  # macOS
            try:
                result = subprocess.run(
                    ["pgrep", "-x", "Serato DJ Pro"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return result.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return False
        elif sys.platform == "win32":  # Windows
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq Serato DJ Pro.exe"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return "Serato DJ Pro.exe" in result.stdout
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return False
        
        return False
    
    def read_track(self, path: str) -> Tuple[List[Cue], Optional[Grid], Dict[str, Any], Dict[str, Any]]:
        """Read Serato track data.
        
        Reads:
        - Markers2 and Markers_ for cues
        - BeatGrid or Tempo Map for grid
        - Tags for metadata
        
        Returns:
            Tuple of (cues, grid, tags, adapter_data).
        """
        path = str(Path(path).resolve())
        
        # Read Serato frames
        try:
            frames = serato_writer.read_serato_frames(path)
        except Exception as e:
            raise ValueError(f"Failed to read Serato frames from {path}: {e}")
        
        # Parse cues from Markers2
        cues = []
        if frames.get("Serato Markers2"):
            try:
                parsed = serato_tags.parse_markers2(frames["Serato Markers2"])
                for idx, marker in enumerate(parsed.get("markers", [])):
                    cue = Cue(
                        index=idx,
                        label=marker.get("label", ""),
                        time_ms=int(marker.get("position_ms", 0)),
                        color=marker.get("color"),
                        kind="cue",
                    )
                    cues.append(cue)
            except Exception as e:
                # If parsing fails, continue without cues
                pass
        
        # Read grid
        grid = None
        grid_data = None
        variable_tempo = False
        
        try:
            # Try reading Tempo Map first (for variable tempo tracks)
            tempomap = serato_grid.read_tempomap(path)
            if tempomap and len(tempomap) > 0:
                # Variable tempo track
                variable_tempo = True
                first_marker = tempomap[0]
                grid = Grid(
                    bpm=first_marker.get("bpm", 120.0),
                    anchor_s=first_marker.get("pos", 0.0),
                    markers=tempomap,
                    source="serato_tempomap",
                    variable_tempo=True,
                )
                grid_data = {"tempomap": tempomap}
        except Exception:
            pass
        
        # If no tempo map, try BeatGrid
        if not grid:
            try:
                beatgrid = serato_grid.read_beatgrid(path)
                if beatgrid:
                    grid = Grid(
                        bpm=beatgrid.get("bpm", 120.0),
                        anchor_s=beatgrid.get("anchor_s", 0.0),
                        markers=[],
                        source="serato_beatgrid",
                        variable_tempo=False,
                    )
                    grid_data = {"beatgrid": beatgrid}
            except Exception:
                pass
        
        # Read basic tags using mutagen
        tags = {}
        try:
            from mutagen import File
            audio = File(path, easy=True)
            if audio and audio.tags:
                for key in ["title", "artist", "album", "genre", "bpm"]:
                    value = audio.tags.get(key)
                    if value:
                        tags[key] = value[0] if isinstance(value, list) else str(value)
        except Exception:
            pass
        
        # Adapter data stores original Serato frames
        adapter_data = {
            "serato_frames": frames,
            "variable_tempo": variable_tempo,
        }
        if grid_data:
            adapter_data.update(grid_data)
        
        return cues, grid, tags, adapter_data
    
    def plan_write(self, track: Track, cues: List[Cue]) -> WritePlan:
        """Plan a Serato cue write operation."""
        # Convert cues to the format expected by serato_writer
        pads = []
        for cue in cues:
            pad = {
                "index": cue.index,
                "slot": chr(65 + cue.index) if cue.index < 8 else "X",  # A-H
                "name": cue.label,
                "position_ms": cue.time_ms,
                "color": cue.color,
            }
            pads.append(pad)
        
        metadata = {
            "pads": pads,
            "adapter_data": track.adapter_data or {},
        }
        
        return WritePlan(
            path=track.path,
            adapter=self.name,
            cues=cues,
            metadata=metadata,
        )
    
    def apply_write(self, plan: WritePlan) -> Dict[str, Any]:
        """Apply a Serato write plan.
        
        This writes both Markers2 and Markers_ in lockstep, with verification.
        Should only be called through tag_gate.
        """
        pads = plan.metadata["pads"]
        
        # Use the existing serato_writer which already does:
        # - Byte-for-byte backup
        # - Audio SHA-256 verification
        # - Markers2/Markers_ lockstep write
        # - Verification of written tags
        result = serato_writer.write_mp3_pads(plan.path, pads)
        
        return {
            "original_sha256": result["original_sha256"],
            "written_sha256": result["written_sha256"],
            "audio_sha256_before": result["audio_sha256_before"],
            "audio_sha256_after": result["audio_sha256_after"],
            "markers2": result["markers2"],
            "markers_v1": result["markers_v1"],
        }
    
    def list_crates(self, serato_dir: Optional[Path] = None) -> List[str]:
        """List Serato crates from _Serato_/Subcrates directory.
        
        Args:
            serato_dir: Optional path to _Serato_ directory. If None, searches standard locations.
            
        Returns:
            List of crate names.
        """
        import serato_database
        
        if serato_dir is None:
            # Search standard locations
            serato_dirs = serato_database.find_serato_dirs(Path.home() / "Music")
            if not serato_dirs:
                return []
            serato_dir = serato_dirs[0]
        
        crate_files = serato_database.list_crates(serato_dir)
        return [crate.stem for crate in crate_files]
    
    def read_crate(self, name: str, serato_dir: Optional[Path] = None) -> List[str]:
        """Read a Serato crate and return track paths.
        
        Args:
            name: Crate name (without .crate extension)
            serato_dir: Optional path to _Serato_ directory. If None, searches standard locations.
            
        Returns:
            List of track file paths.
        """
        import serato_database
        
        if serato_dir is None:
            # Search standard locations
            serato_dirs = serato_database.find_serato_dirs(Path.home() / "Music")
            if not serato_dirs:
                return []
            serato_dir = serato_dirs[0]
        
        crate_path = serato_dir / "Subcrates" / f"{name}.crate"
        if not crate_path.exists():
            return []
        
        try:
            crate_data = serato_database.parse_crate(crate_path)
            return crate_data['tracks']
        except serato_database.SeratoParseError:
            return []
    
    def write_crate(
        self,
        name: str,
        tracks: List[str],
        serato_dir: Optional[Path] = None,
        *,
        overwrite: bool = False,
        apply: bool = False,
    ) -> Dict[str, Any]:
        """Write a Serato crate file.
        
        This follows the same safety patterns as tag_gate:
        - Refuses if Serato is running
        - Requires explicit --apply flag for actual writes
        - Creates backup before any overwrite
        - Returns dry-run preview by default
        
        Args:
            name: Crate name (supports nested with %% separator)
            tracks: List of track file paths to include
            serato_dir: Optional path to _Serato_ directory. If None, searches standard locations.
            overwrite: If True, allow overwriting existing .crate file
            apply: If True, actually write. If False (default), dry-run only.
            
        Returns:
            Dict with operation results and crate metadata.
            
        Raises:
            RuntimeError: If Serato is running or safety checks fail
            FileExistsError: If crate exists and overwrite=False
        """
        import serato_database
        import shutil
        import time
        
        # 1. Check if Serato is running
        if self.detect_running():
            raise RuntimeError(
                f"Refusing to write crate: {self.name} is currently running. "
                f"Close {self.name} before modifying crates."
            )
        
        # 2. Find Serato directory
        if serato_dir is None:
            serato_dirs = serato_database.find_serato_dirs(Path.home() / "Music")
            if not serato_dirs:
                raise RuntimeError(
                    "Could not find _Serato_ directory. "
                    "Specify serato_dir explicitly."
                )
            serato_dir = serato_dirs[0]
        
        serato_dir = Path(serato_dir)
        if not serato_dir.is_dir():
            raise RuntimeError(f"Serato directory not found: {serato_dir}")
        
        # 3. Build crate path
        subcrates_dir = serato_dir / "Subcrates"
        crate_path = subcrates_dir / f"{name}.crate"
        
        # 4. Check for existing crate
        crate_exists = crate_path.exists()
        backup_path = None
        
        if crate_exists and not overwrite:
            raise FileExistsError(
                f"Crate file already exists: {crate_path}. "
                f"Use overwrite=True to replace it."
            )
        
        # 5. Dry-run mode: return preview
        if not apply:
            return {
                "dry_run": True,
                "name": name,
                "path": str(crate_path),
                "track_count": len(tracks),
                "exists": crate_exists,
                "would_overwrite": crate_exists and overwrite,
                "tracks": tracks,
            }
        
        # 6. Create backup if overwriting
        if crate_exists:
            # Back up the entire Subcrates folder
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            backup_dir = Path.home() / ".autocue" / "backups" / "subcrates" / timestamp
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            backup_path = backup_dir / crate_path.name
            shutil.copy2(crate_path, backup_path)
            
            # Verify backup
            if not backup_path.exists():
                raise RuntimeError(f"Backup verification failed: {backup_path}")
        
        # 7. Write the crate
        try:
            result = serato_database.write_crate(
                crate_path,
                tracks,
                overwrite=overwrite,
            )
        except Exception as e:
            # Restore backup if write failed
            if backup_path and backup_path.exists():
                shutil.copy2(backup_path, crate_path)
            raise RuntimeError(f"Failed to write crate: {e}")
        
        # 8. Return success result
        return {
            "success": True,
            "name": result['name'],
            "path": result['path'],
            "track_count": result['track_count'],
            "file_size": result['file_size'],
            "backup_path": str(backup_path) if backup_path else None,
            "overwrote_existing": crate_exists,
        }
