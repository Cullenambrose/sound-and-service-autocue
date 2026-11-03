"""Rekordbox adapter (stub)."""
from typing import List, Dict, Any, Optional, Tuple

from ..model import Cue, Grid, Track
from .base import DJSoftwareAdapter, WritePlan
from .registry import register


@register
class RekordboxAdapter(DJSoftwareAdapter):
    """Adapter for Pioneer Rekordbox.
    
    This is a stub implementation. The full version will:
    - Read Rekordbox memory cues, hot cues, and beat grid
    - Write cues to Rekordbox XML database
    - Support both XML and rekordbox6 database formats
    - Check if Rekordbox is running before writes
    """
    
    @property
    def name(self) -> str:
        return "rekordbox"
    
    def detect_running(self) -> bool:
        """Check if Rekordbox is running. Not yet implemented."""
        raise NotImplementedError("Rekordbox process detection not yet implemented")
    
    def read_track(self, path: str) -> Tuple[List[Cue], Optional[Grid], Dict[str, Any], Dict[str, Any]]:
        """Read track data from Rekordbox. Not yet implemented."""
        raise NotImplementedError("Rekordbox track reading not yet implemented")
    
    def plan_write(self, track: Track, cues: List[Cue]) -> WritePlan:
        """Plan a Rekordbox write operation. Not yet implemented."""
        raise NotImplementedError("Rekordbox write planning not yet implemented")
    
    def apply_write(self, plan: WritePlan) -> Dict[str, Any]:
        """Apply a Rekordbox write plan. Not yet implemented."""
        raise NotImplementedError("Rekordbox write application not yet implemented")
    
    def list_crates(self) -> List[str]:
        """List Rekordbox playlists. Not yet implemented."""
        raise NotImplementedError("Rekordbox playlist listing not yet implemented")
    
    def read_crate(self, name: str) -> List[str]:
        """Read a Rekordbox playlist. Not yet implemented."""
        raise NotImplementedError("Rekordbox playlist reading not yet implemented")
