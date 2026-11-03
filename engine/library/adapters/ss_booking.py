"""Sound & Service booking site event crate source adapter (stub)."""
from typing import List, Dict, Any, Optional, Tuple

from ..model import Cue, Grid, Track, Crate
from .base import DJSoftwareAdapter, WritePlan
from .registry import register


@register
class EventCrateSource(DJSoftwareAdapter):
    """Adapter for importing event crates from Sound & Service booking site.
    
    This is a stub implementation. The full version will:
    - Import event crates (event id, date, client, must-play/do-not-play lists)
    - Convert track references to core Crate rows
    - Support JSON fixture format for testing
    
    This comes BEFORE Rekordbox on the roadmap.
    """
    
    @property
    def name(self) -> str:
        return "ss_booking"
    
    def detect_running(self) -> bool:
        """Not applicable for a booking site adapter."""
        return False
    
    def read_track(self, path: str) -> Tuple[List[Cue], Optional[Grid], Dict[str, Any], Dict[str, Any]]:
        """Read track data. Not implemented."""
        raise NotImplementedError("Event crate source does not read individual tracks")
    
    def plan_write(self, track: Track, cues: List[Cue]) -> WritePlan:
        """Plan a write operation. Not implemented."""
        raise NotImplementedError("Event crate source does not write to tracks")
    
    def apply_write(self, plan: WritePlan) -> Dict[str, Any]:
        """Apply a write plan. Not implemented."""
        raise NotImplementedError("Event crate source does not write to tracks")
    
    def list_crates(self) -> List[str]:
        """List available event crates. Not yet implemented."""
        raise NotImplementedError("Event crate listing not yet implemented")
    
    def read_crate(self, name: str) -> List[str]:
        """Read an event crate. Not yet implemented."""
        raise NotImplementedError("Event crate reading not yet implemented")
    
    def import_event_crate(self, event_id: str, fixture_data: Optional[Dict[str, Any]] = None) -> Crate:
        """Import an event crate from the booking site.
        
        Args:
            event_id: The event identifier.
            fixture_data: Optional JSON fixture data for testing.
            
        Returns:
            Crate object with imported data.
        """
        raise NotImplementedError("Event crate import not yet implemented")
