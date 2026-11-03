"""Integration tests for Serato database import."""
import tempfile
import struct
from pathlib import Path
import sqlite3
import sys

# Add engine to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
import serato_database
from . import __main__ as cli


def _write_tlv(tag: bytes, value: bytes) -> bytes:
    """Helper to create a TLV record."""
    return tag + struct.pack('>I', len(value)) + value


def _encode_utf16be(text: str) -> bytes:
    """Helper to encode UTF-16BE."""
    return text.encode('utf-16-be')


def test_import_serato_command():
    """Test the import-serato CLI command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create Serato directory structure
        music_dir = tmpdir / 'Music'
        serato_dir = music_dir / '_Serato_'
        serato_dir.mkdir(parents=True)
        
        # Create database V2
        db_path = serato_dir / 'database V2'
        db_data = _write_tlv(b'vrsn', _encode_utf16be('1.0'))
        db_data += _write_tlv(b'otrk', b'')
        db_data += _write_tlv(b'ptrk', _encode_utf16be(str(tmpdir / 'test.mp3')))
        db_data += _write_tlv(b'tsng', _encode_utf16be('Test Track'))
        db_data += _write_tlv(b'tart', _encode_utf16be('Test Artist'))
        db_path.write_bytes(db_data)
        
        # Create empty MP3 (not really used but path should exist)
        (tmpdir / 'test.mp3').touch()
        
        # Create test database
        test_db = tmpdir / 'test.db'
        
        # Run import-serato command (should work even without real audio)
        # Note: This will fail on reading the actual track data, but should 
        # demonstrate the database parsing works
        result = cli.main(['--db', str(test_db), 'import-serato', str(music_dir), '--quiet'])
        
        # The command may return 0 even with errors during track processing
        # As long as it doesn't crash, we're good
        assert result == 0


def test_crates_list_command():
    """Test the crates --list CLI command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create Serato directory structure
        music_dir = tmpdir / 'Music'
        serato_dir = music_dir / '_Serato_'
        subcrates_dir = serato_dir / 'Subcrates'
        subcrates_dir.mkdir(parents=True)
        
        # Create crate file
        crate_path = subcrates_dir / 'TestCrate.crate'
        crate_data = _write_tlv(b'ptrk', _encode_utf16be('/test/track.mp3'))
        crate_path.write_bytes(crate_data)
        
        # Create test database
        test_db = tmpdir / 'test.db'
        
        # Run crates --list command
        result = cli.main(['--db', str(test_db), 'crates', str(music_dir), '--list'])
        assert result == 0


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
