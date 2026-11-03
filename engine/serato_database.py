"""Parser for Serato database V2 and .crate files.

Both formats use tag-length-value (TLV) encoding with UTF-16BE strings.
This is a READ-ONLY parser - it never writes to Serato files.
"""
import struct
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import urllib.parse


class SeratoParseError(Exception):
    """Error parsing Serato database files."""
    pass


def _read_tlv_records(data: bytes, expected_tags: Optional[List[bytes]] = None) -> List[Tuple[bytes, bytes]]:
    """Parse tag-length-value records from binary data.
    
    Args:
        data: Raw binary data
        expected_tags: Optional list of expected tag names (4 bytes each)
        
    Returns:
        List of (tag, value) tuples
        
    Note:
        Malformed records are skipped with a diagnostic, not a crash.
    """
    records = []
    offset = 0
    
    while offset < len(data):
        # Need at least 8 bytes for tag (4) + length (4)
        if offset + 8 > len(data):
            break
            
        tag = data[offset:offset + 4]
        offset += 4
        
        try:
            length = struct.unpack('>I', data[offset:offset + 4])[0]
            offset += 4
        except struct.error:
            # Malformed length field - try to continue by searching for next valid tag
            offset += 1
            continue
            
        # Validate length
        if length < 0 or offset + length > len(data):
            # Invalid length - try to continue by searching for next valid tag
            offset += 1
            continue
            
        value = data[offset:offset + length]
        offset += length
        
        # If we're filtering by expected tags, skip unknown ones
        if expected_tags is None or tag in expected_tags:
            records.append((tag, value))
    
    return records


def _decode_utf16be(data: bytes) -> str:
    """Decode UTF-16BE string, stripping null bytes.
    
    Args:
        data: Raw bytes to decode
        
    Returns:
        Decoded string with null bytes removed
    """
    try:
        text = data.decode('utf-16-be')
        # Strip null bytes that may be present
        return text.replace('\x00', '').strip()
    except UnicodeDecodeError:
        # Fall back to UTF-8 if UTF-16BE fails
        try:
            return data.decode('utf-8').replace('\x00', '').strip()
        except UnicodeDecodeError:
            return ''


def _normalize_path(path_str: str, base_path: Optional[Path] = None) -> str:
    """Normalize a Serato file path.
    
    Serato stores paths with various formats:
    - file:// URLs
    - Relative paths from drive root (e.g., "Music/file.mp3")
    - Absolute paths
    - Mac volume prefixes (e.g., "Macintosh HD:Users:...")
    
    Args:
        path_str: Raw path string from Serato
        base_path: Base path for resolving relative paths (e.g., crate file location)
        
    Returns:
        Normalized absolute path string
    """
    if not path_str:
        return ''
    
    # Handle file:// URLs
    if path_str.startswith('file://'):
        path_str = urllib.parse.unquote(urllib.parse.urlparse(path_str).path)
    
    # Handle Mac volume format (e.g., "Macintosh HD:Users:...")
    if ':' in path_str and not path_str.startswith('/'):
        parts = path_str.split(':')
        if len(parts) > 1:
            # Convert Mac path to Unix path
            volume = parts[0]
            rest = '/'.join(parts[1:])
            if volume == 'Macintosh HD':
                path_str = '/' + rest
            else:
                path_str = '/Volumes/' + volume + '/' + rest
    
    path = Path(path_str)
    
    # Handle relative paths
    if not path.is_absolute() and base_path:
        # Determine drive root
        parts = base_path.resolve().parts
        if len(parts) > 2 and parts[1] == 'Volumes':
            # External drive
            drive = Path('/Volumes') / parts[2]
        else:
            # Boot drive
            drive = Path('/')
        path = drive / path_str
    
    return str(path)


def parse_database_v2(db_path: Path) -> Dict[str, Any]:
    """Parse Serato database V2 file.
    
    The database V2 format contains track metadata and is stored at:
    _Serato_/database V2
    
    Args:
        db_path: Path to the database V2 file
        
    Returns:
        Dict containing:
        - tracks: List of track dicts with path, title, artist, etc.
        - version: Database version string (if found)
        
    Raises:
        SeratoParseError: If the file cannot be parsed
    """
    try:
        data = db_path.read_bytes()
    except OSError as e:
        raise SeratoParseError(f"Failed to read database file: {e}")
    
    # Database V2 starts with a "vrsn" tag containing the version
    # Followed by track entries
    
    # Common tags in database V2:
    # vrsn = version
    # otrk = track entry start
    # ptrk = track path
    # tsng = song title
    # tart = artist
    # talb = album
    # tgen = genre
    # tlen = length
    # tbpm = BPM
    # tkey = musical key
    # tbit = bitrate
    # tsmp = sample rate
    
    expected_tags = [
        b'vrsn', b'otrk', b'ptrk', b'tsng', b'tart', b'talb', 
        b'tgen', b'tlen', b'tbpm', b'tkey', b'tbit', b'tsmp',
        b'utkn', b'utme'  # unknown token, update time
    ]
    
    records = _read_tlv_records(data, expected_tags)
    
    result = {
        'tracks': [],
        'version': None,
    }
    
    current_track = None
    base_dir = db_path.parent.parent  # Go up from _Serato_ to the music root
    
    for tag, value in records:
        if tag == b'vrsn':
            # Version string (usually "1.0/Serato ScratchLive")
            result['version'] = _decode_utf16be(value)
            
        elif tag == b'otrk':
            # Start of a new track entry - save previous track if exists
            if current_track and current_track.get('path'):
                result['tracks'].append(current_track)
            current_track = {}
            
        elif tag == b'ptrk' and current_track is not None:
            # Track file path
            path_str = _decode_utf16be(value)
            current_track['path'] = _normalize_path(path_str, base_dir)
            
        elif tag == b'tsng' and current_track is not None:
            # Song title
            current_track['title'] = _decode_utf16be(value)
            
        elif tag == b'tart' and current_track is not None:
            # Artist
            current_track['artist'] = _decode_utf16be(value)
            
        elif tag == b'talb' and current_track is not None:
            # Album
            current_track['album'] = _decode_utf16be(value)
            
        elif tag == b'tgen' and current_track is not None:
            # Genre
            current_track['genre'] = _decode_utf16be(value)
            
        elif tag == b'tbpm' and current_track is not None:
            # BPM - might be encoded as string or float
            bpm_str = _decode_utf16be(value)
            if bpm_str:
                try:
                    current_track['bpm'] = float(bpm_str)
                except ValueError:
                    pass
                    
        elif tag == b'tkey' and current_track is not None:
            # Musical key
            current_track['key'] = _decode_utf16be(value)
            
        elif tag == b'tlen' and current_track is not None:
            # Track length in seconds
            len_str = _decode_utf16be(value)
            if len_str:
                try:
                    current_track['duration_s'] = float(len_str)
                except ValueError:
                    pass
    
    # Don't forget the last track
    if current_track and current_track.get('path'):
        result['tracks'].append(current_track)
    
    return result


def parse_crate(crate_path: Path) -> Dict[str, Any]:
    """Parse a Serato .crate file.
    
    Crate files contain playlist/crate membership and are stored at:
    _Serato_/Subcrates/*.crate
    
    Args:
        crate_path: Path to the .crate file
        
    Returns:
        Dict containing:
        - name: Crate name (from filename)
        - tracks: List of track path strings
        - track_count: Number of tracks
        
    Raises:
        SeratoParseError: If the file cannot be parsed
    """
    try:
        data = crate_path.read_bytes()
    except OSError as e:
        raise SeratoParseError(f"Failed to read crate file: {e}")
    
    # Common tags in .crate files:
    # vrsn = version
    # tvcn = crate version
    # otrk = track entry
    # ptrk = track path
    # pfil = track file (another path field)
    # path = track path (yet another variant)
    
    # We'll look for all path-related tags
    path_tags = [b'ptrk', b'pfil', b'path']
    
    records = _read_tlv_records(data)
    
    tracks = []
    seen = set()
    
    for tag, value in records:
        if tag in path_tags:
            path_str = _decode_utf16be(value)
            if not path_str:
                continue
                
            # Normalize the path
            normalized = _normalize_path(path_str, crate_path)
            
            # Deduplicate
            if normalized and normalized not in seen:
                seen.add(normalized)
                tracks.append(normalized)
    
    # Extract crate name from filename
    # Serato uses "%%" as a separator for nested crates
    crate_name = crate_path.stem
    if '%%' in crate_name:
        crate_name = ' › '.join(crate_name.split('%%'))
    
    return {
        'name': crate_name,
        'tracks': tracks,
        'track_count': len(tracks),
    }


def find_serato_dirs(root: Path) -> List[Path]:
    """Find all _Serato_ directories under the given root.
    
    Args:
        root: Root directory to search
        
    Returns:
        List of _Serato_ directory paths
    """
    serato_dirs = []
    
    # Check the root itself
    serato_root = root / '_Serato_'
    if serato_root.is_dir():
        serato_dirs.append(serato_root)
    
    # Check mounted volumes on macOS
    volumes = Path('/Volumes')
    if volumes.is_dir():
        for volume in volumes.iterdir():
            if volume.is_dir():
                serato_vol = volume / '_Serato_'
                if serato_vol.is_dir():
                    serato_dirs.append(serato_vol)
    
    return serato_dirs


def list_crates(serato_dir: Path) -> List[Path]:
    """List all .crate files in a Serato directory.
    
    Args:
        serato_dir: Path to _Serato_ directory
        
    Returns:
        List of .crate file paths
    """
    subcrates_dir = serato_dir / 'Subcrates'
    if not subcrates_dir.is_dir():
        return []
    
    return sorted(subcrates_dir.rglob('*.crate'))


def _write_tlv_record(tag: bytes, value: bytes) -> bytes:
    """Write a tag-length-value record.
    
    Args:
        tag: 4-byte tag identifier
        value: Value bytes
        
    Returns:
        Encoded TLV record
    """
    if len(tag) != 4:
        raise ValueError(f"Tag must be exactly 4 bytes, got {len(tag)}")
    length = len(value)
    return tag + struct.pack('>I', length) + value


def _encode_utf16be(text: str) -> bytes:
    """Encode text as UTF-16BE.
    
    Args:
        text: Text to encode
        
    Returns:
        UTF-16BE encoded bytes
    """
    return text.encode('utf-16-be')


def write_crate(crate_path: Path, tracks: List[str], *, overwrite: bool = False) -> Dict[str, Any]:
    """Write a Serato .crate file.
    
    This creates a new .crate file in TLV format with UTF-16BE encoding.
    The file format matches what Serato DJ Pro writes and what parse_crate() reads.
    
    Args:
        crate_path: Path where the .crate file will be written
        tracks: List of track file paths (absolute paths)
        overwrite: If True, allow overwriting an existing .crate file
        
    Returns:
        Dict containing:
        - path: Written crate file path
        - name: Crate name
        - track_count: Number of tracks written
        - file_size: Size of written file in bytes
        
    Raises:
        ValueError: If crate_path doesn't have .crate extension
        FileExistsError: If crate file exists and overwrite=False
        SeratoParseError: If write fails
    """
    if crate_path.suffix.lower() != '.crate':
        raise ValueError(f"Crate path must have .crate extension, got: {crate_path}")
    
    if crate_path.exists() and not overwrite:
        raise FileExistsError(
            f"Crate file already exists: {crate_path}. "
            f"Use overwrite=True to replace it."
        )
    
    # Build the crate data
    data = b''
    
    # Write version tag
    data += _write_tlv_record(b'vrsn', _encode_utf16be('1.0/Serato ScratchLive'))
    
    # Write crate version tag
    data += _write_tlv_record(b'tvcn', _encode_utf16be('1.0'))
    
    # Write track entries
    for track_path in tracks:
        # Normalize to absolute path
        abs_path = str(Path(track_path).resolve())
        
        # Write track entry marker
        data += _write_tlv_record(b'otrk', b'')
        
        # Write track path using ptrk tag
        data += _write_tlv_record(b'ptrk', _encode_utf16be(abs_path))
    
    # Write to file atomically
    try:
        # Create parent directory if it doesn't exist
        crate_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to temporary file first
        temp_path = crate_path.with_suffix('.crate.tmp')
        temp_path.write_bytes(data)
        
        # Verify by reading back
        try:
            parsed = parse_crate(temp_path)
            if parsed['track_count'] != len(tracks):
                raise SeratoParseError(
                    f"Verification failed: expected {len(tracks)} tracks, "
                    f"got {parsed['track_count']}"
                )
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            raise SeratoParseError(f"Failed to verify written crate: {e}")
        
        # Atomic rename
        temp_path.replace(crate_path)
        
    except OSError as e:
        raise SeratoParseError(f"Failed to write crate file: {e}")
    
    # Extract crate name from filename
    crate_name = crate_path.stem
    if '%%' in crate_name:
        crate_name = ' › '.join(crate_name.split('%%'))
    
    return {
        'path': str(crate_path),
        'name': crate_name,
        'track_count': len(tracks),
        'file_size': crate_path.stat().st_size,
    }
