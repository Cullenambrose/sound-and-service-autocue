"""Adapter registry for looking up DJ software adapters by name."""
from typing import Dict, Type, Optional
from .base import DJSoftwareAdapter


_ADAPTERS: Dict[str, Type[DJSoftwareAdapter]] = {}


def register(adapter_class: Type[DJSoftwareAdapter]):
    """Register an adapter class."""
    adapter = adapter_class()
    _ADAPTERS[adapter.name] = adapter_class
    return adapter_class


def get_adapter(name: str) -> Optional[DJSoftwareAdapter]:
    """Get an adapter instance by name.
    
    Args:
        name: Adapter name (e.g., 'serato', 'rekordbox').
        
    Returns:
        Adapter instance or None if not found.
    """
    adapter_class = _ADAPTERS.get(name)
    if adapter_class:
        return adapter_class()
    return None


def list_adapters() -> Dict[str, Type[DJSoftwareAdapter]]:
    """Get all registered adapters."""
    return _ADAPTERS.copy()
