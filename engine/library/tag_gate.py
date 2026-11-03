"""Safe write gate with protection checks and audio integrity verification."""
import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from .adapters.base import DJSoftwareAdapter, WritePlan
from . import db


# Protected tracks that must NEVER be written to
DEFAULT_PROTECTED = [
    {
        "path": "Happy.mp3",  # Track #1
        "reason": "Protected test track: Happy",
    },
    {
        "path": "Into The Mystic.mp3",  # Track #2
        "reason": "Protected test track: Into The Mystic",
    },
]


def compute_audio_sha256(path: str, adapter: DJSoftwareAdapter) -> str:
    """Compute SHA-256 of audio stream only, excluding tags.
    
    For MP3: uses mp3_audio_range to exclude ID3 tags.
    For other formats: full file hash (tags are typically at end or embedded).
    """
    path_obj = Path(path)
    
    if path_obj.suffix.lower() == ".mp3":
        # Import here to avoid circular dependency
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from serato_writer import mp3_audio_sha256
        return mp3_audio_sha256(path)
    
    # For other formats, hash the entire file
    # (tags in M4A/FLAC are typically structured so audio remains stable)
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_protected(path: str, protected: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Check if a path matches the protected list.
    
    Args:
        path: File path to check.
        protected: List of protected entries with 'path' and 'reason'.
        
    Returns:
        The matching protection entry if protected, None otherwise.
    """
    path_obj = Path(path).resolve()
    
    for entry in protected:
        # Match by filename
        if path_obj.name == entry.get("path"):
            return entry
        # Could also match by audio_sha256 if provided
        if "audio_sha256" in entry:
            try:
                actual_sha = compute_audio_sha256(str(path_obj), None)
                if actual_sha == entry["audio_sha256"]:
                    return entry
            except Exception:
                pass
    
    return None


def create_backup(path: str, backup_root: Optional[str] = None) -> str:
    """Create a timestamped backup of the file.
    
    Args:
        path: File to backup.
        backup_root: Root directory for backups. Defaults to ~/.autocue/backups.
        
    Returns:
        Path to the backup file.
    """
    if backup_root is None:
        backup_root = str(Path.home() / ".autocue" / "backups")
    
    backup_dir = Path(backup_root) / time.strftime("%Y%m%d")
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    original_name = Path(path).name
    backup_name = f"{timestamp}-{unique_id}-{original_name}"
    backup_path = backup_dir / backup_name
    
    # Byte-for-byte copy
    shutil.copy2(path, backup_path)
    
    # Verify backup
    with open(path, "rb") as original, open(backup_path, "rb") as backup:
        original_data = original.read()
        backup_data = backup.read()
        if original_data != backup_data:
            backup_path.unlink()
            raise RuntimeError(
                f"Backup verification failed: backup does not match original for {path}"
            )
    
    return str(backup_path)


def verify_markers_lockstep(path: str) -> bool:
    """Verify that Markers2 and Markers_ are in lockstep after write.
    
    Args:
        path: Path to the file to verify.
        
    Returns:
        True if verification passes.
        
    Raises:
        RuntimeError if markers are out of sync.
    """
    # Import here to avoid circular dependency
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from serato_writer import read_serato_frames
    import serato_tags
    
    try:
        frames = read_serato_frames(path)
        markers2_data = frames.get("Serato Markers2")
        markers_v1_data = frames.get("Serato Markers_")
        
        if not markers2_data or not markers_v1_data:
            # If either is missing, they're not in lockstep
            raise RuntimeError("Markers2 and Markers_ are not both present")
        
        # Parse both and verify first 5 cues match
        markers2 = serato_tags.decode_markers2(markers2_data)
        markers_v1 = serato_tags.decode_markers_v1(markers_v1_data)
        
        markers2_cues = {c["index"]: c for c in markers2["cues"] if c["index"] < 5}
        markers_v1_entries = {e["slot"]: e for e in markers_v1["entries"] if e["slot"] < 5}
        
        # Check that positions match for first 5 cues
        for idx in range(5):
            m2_cue = markers2_cues.get(idx)
            m1_entry = markers_v1_entries.get(idx)
            
            if m2_cue and m1_entry:
                if m2_cue["position_ms"] != m1_entry["position_ms"]:
                    raise RuntimeError(
                        f"Markers mismatch at cue {idx}: "
                        f"Markers2={m2_cue['position_ms']}ms, "
                        f"Markers_={m1_entry['position_ms']}ms"
                    )
            elif m2_cue or m1_entry:
                # One has a cue, the other doesn't
                raise RuntimeError(f"Markers presence mismatch at cue {idx}")
        
        return True
        
    except Exception as e:
        raise RuntimeError(f"Markers lockstep verification failed: {e}")


def tag_write(
    path: str,
    adapter: DJSoftwareAdapter,
    plan: WritePlan,
    *,
    apply: bool = False,
    protected: Optional[List[Dict[str, Any]]] = None,
    backup_root: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Safely write tags to a file with comprehensive protection and verification.
    
    Args:
        path: Path to the audio file.
        adapter: The DJ software adapter to use.
        plan: The write plan to execute.
        apply: If False (default), dry-run only. If True, actually write.
        protected: List of protected files. Defaults to DEFAULT_PROTECTED.
        backup_root: Root directory for backups.
        db_path: Database path for audit logging.
        
    Returns:
        Dict with results including diff, verification data, and audit log ID.
        
    Raises:
        RuntimeError: If any safety check fails.
    """
    if protected is None:
        protected = DEFAULT_PROTECTED
    
    path = str(Path(path).resolve())
    
    # 1. Check protected list
    protection = is_protected(path, protected)
    if protection:
        raise RuntimeError(
            f"Refusing to write to protected track: {protection.get('reason', 'protected')}"
        )
    
    # 2. Check if DJ software is running
    if adapter.detect_running():
        raise RuntimeError(
            f"Refusing to write: {adapter.name} is currently running. "
            f"Close {adapter.name} before modifying track tags."
        )
    
    # 3. Dry-run mode: return the diff without writing
    if not apply:
        return {
            "dry_run": True,
            "path": path,
            "adapter": adapter.name,
            "plan_summary": plan.summary(),
            "cue_count": len(plan.cues),
            "cues": [
                {
                    "index": c.index,
                    "label": c.label,
                    "time_ms": c.time_ms,
                    "color": c.color,
                }
                for c in plan.cues
            ],
        }
    
    # 4. Create byte-for-byte backup
    backup_path = create_backup(path, backup_root)
    
    # 5. Compute audio SHA-256 before
    audio_sha_before = compute_audio_sha256(path, adapter)
    
    # Read current cues for before summary
    try:
        current_cues, _, _, _ = adapter.read_track(path)
        before_summary = f"{len(current_cues)} cues"
    except Exception:
        before_summary = "unknown"
    
    # 6. Apply the mutation
    try:
        write_result = adapter.apply_write(plan)
    except Exception as e:
        # Restore backup on write failure
        shutil.copy2(backup_path, path)
        raise RuntimeError(f"Write failed, backup restored: {e}")
    
    # 7. Compute audio SHA-256 after
    audio_sha_after = compute_audio_sha256(path, adapter)
    
    # 8. Verify audio stream unchanged
    if audio_sha_after != audio_sha_before:
        # Restore backup
        shutil.copy2(backup_path, path)
        # Verify restoration
        restored_sha = compute_audio_sha256(path, adapter)
        if restored_sha != audio_sha_before:
            raise RuntimeError(
                f"CRITICAL: Audio changed and restore failed! "
                f"Original backup at {backup_path}"
            )
        raise RuntimeError(
            f"Audio data changed during write (before={audio_sha_before[:8]}, "
            f"after={audio_sha_after[:8]}). Backup restored."
        )
    
    # 9. Verify Markers2/Markers_ lockstep for Serato
    if adapter.name == "serato":
        try:
            verify_markers_lockstep(path)
        except Exception as e:
            # Restore backup
            shutil.copy2(backup_path, path)
            restored_sha = compute_audio_sha256(path, adapter)
            if restored_sha != audio_sha_before:
                raise RuntimeError(
                    f"CRITICAL: Markers lockstep failed and restore failed! "
                    f"Original backup at {backup_path}"
                )
            raise RuntimeError(f"Markers lockstep verification failed, backup restored: {e}")
    
    # 10. Write audit log
    after_summary = f"{len(plan.cues)} cues"
    change_id = None
    if db_path:
        try:
            with db.get_db(db_path) as conn:
                change_id = db.log_change(
                    conn,
                    path=path,
                    adapter=adapter.name,
                    operation="write_cues",
                    before_summary=before_summary,
                    after_summary=after_summary,
                    backup_path=backup_path,
                    audio_sha256_before=audio_sha_before,
                    audio_sha256_after=audio_sha_after,
                )
        except Exception as e:
            # Log failure is not fatal, but report it
            print(f"Warning: Failed to log change to database: {e}")
    
    return {
        "success": True,
        "path": path,
        "adapter": adapter.name,
        "backup_path": backup_path,
        "audio_sha256_before": audio_sha_before,
        "audio_sha256_after": audio_sha_after,
        "before_summary": before_summary,
        "after_summary": after_summary,
        "change_id": change_id,
        "write_result": write_result,
    }
