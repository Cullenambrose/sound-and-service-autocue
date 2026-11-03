"""Base adapter interface for DJ software integration."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

from ..model import Cue, Grid, Track


@dataclass
class WritePlan:
    """A plan for writing cues to a track. Pure data, no I/O."""
    path: str
    adapter: str
    cues: List[Cue]
    metadata: Dict[str, Any]
    
    def summary(self) -> str:
        """Human-readable summary of the plan."""
        return f"{len(self.cues)} cues for {self.path}"


class DJSoftwareAdapter(ABC):
    """Abstract base class for DJ software adapters."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter name (e.g., 'serato', 'rekordbox')."""
        pass
    
    @abstractmethod
    def detect_running(self) -> bool:
        """Check if the DJ software is currently running.
        
        Returns:
            True if the software is running, False otherwise.
        """
        pass
    
    @abstractmethod
    def read_track(self, path: str) -> Tuple[List[Cue], Optional[Grid], Dict[str, Any], Dict[str, Any]]:
        """Read track data from file.
        
        Args:
            path: Path to the audio file.
            
        Returns:
            Tuple of (cues, grid, tags, adapter_data).
            - cues: List of Cue objects
            - grid: Grid object or None
            - tags: Dict of metadata tags
            - adapter_data: Opaque vendor-specific data
        """
        pass
    
    @abstractmethod
    def plan_write(self, track: Track, cues: List[Cue]) -> WritePlan:
        """Plan a write operation without performing I/O.
        
        Args:
            track: The track to write to.
            cues: The cues to write.
            
        Returns:
            WritePlan describing the intended write.
        """
        pass
    
    @abstractmethod
    def apply_write(self, plan: WritePlan) -> Dict[str, Any]:
        """Apply a write plan. Only callable through tag_gate.
        
        Args:
            plan: The write plan to execute.
            
        Returns:
            Dict with write results (hashes, verification data, etc.).
        """
        pass
    
    @abstractmethod
    def list_crates(self) -> List[str]:
        """List available crate/playlist names.
        
        Returns:
            List of crate names.
        """
        pass
    
    @abstractmethod
    def read_crate(self, name: str) -> List[str]:
        """Read tracks in a crate.
        
        Args:
            name: The crate name.
            
        Returns:
            List of track paths.
        """
        pass
