"""Tests for Serato database V2 and .crate parser."""
import struct
import tempfile
from pathlib import Path
import pytest

from serato_database import (
    parse_database_v2,
    parse_crate,
    SeratoParseError,
    _read_tlv_records,
    _decode_utf16be,
    _normalize_path,
)


def _write_tlv(tag: bytes, value: bytes) -> bytes:
    """Helper to create a TLV record."""
    assert len(tag) == 4, "Tag must be 4 bytes"
    length = len(value)
    return tag + struct.pack('>I', length) + value


def _encode_utf16be(text: str) -> bytes:
    """Helper to encode UTF-16BE."""
    return text.encode('utf-16-be')


class TestTLVRecords:
    """Test TLV record parsing."""
    
    def test_single_record(self):
        data = _write_tlv(b'test', b'value')
        records = _read_tlv_records(data)
        assert len(records) == 1
        assert records[0] == (b'test', b'value')
    
    def test_multiple_records(self):
        data = _write_tlv(b'aaa1', b'first')
        data += _write_tlv(b'bbb2', b'second')
        data += _write_tlv(b'ccc3', b'third')
        records = _read_tlv_records(data)
        assert len(records) == 3
        assert records[0] == (b'aaa1', b'first')
        assert records[1] == (b'bbb2', b'second')
        assert records[2] == (b'ccc3', b'third')
    
    def test_empty_value(self):
        data = _write_tlv(b'empt', b'')
        records = _read_tlv_records(data)
        assert len(records) == 1
        assert records[0] == (b'empt', b'')
    
    def test_malformed_truncated(self):
        """Malformed records should be skipped, not crash."""
        data = _write_tlv(b'good', b'value')
        data += b'bad'  # Only 3 bytes, not enough for tag
        records = _read_tlv_records(data)
        assert len(records) == 1
        assert records[0] == (b'good', b'value')
    
    def test_malformed_invalid_length(self):
        """Invalid length should be skipped."""
        data = _write_tlv(b'good', b'value')
        # Add a record with invalid (too large) length
        data += b'baad' + struct.pack('>I', 99999999)
        records = _read_tlv_records(data)
        assert len(records) == 1
        assert records[0] == (b'good', b'value')
    
    def test_filter_expected_tags(self):
        data = _write_tlv(b'keep', b'yes')
        data += _write_tlv(b'skip', b'no')
        data += _write_tlv(b'also', b'yes')
        records = _read_tlv_records(data, expected_tags=[b'keep', b'also'])
        assert len(records) == 2
        assert records[0] == (b'keep', b'yes')
        assert records[1] == (b'also', b'yes')


class TestUTF16Decode:
    """Test UTF-16BE decoding."""
    
    def test_ascii(self):
        text = _encode_utf16be('Hello')
        assert _decode_utf16be(text) == 'Hello'
    
    def test_unicode(self):
        text = _encode_utf16be('Café ☕')
        assert _decode_utf16be(text) == 'Café ☕'
    
    def test_with_nulls(self):
        text = _encode_utf16be('Test') + b'\x00\x00'
        assert _decode_utf16be(text) == 'Test'
    
    def test_fallback_utf8(self):
        """Should fall back to UTF-8 if UTF-16BE fails."""
        text = b'Regular UTF-8'
        assert _decode_utf16be(text) == 'Regular UTF-8'
    
    def test_invalid_bytes(self):
        """Should handle invalid bytes gracefully."""
        # UTF-16BE may decode to replacement characters
        text = b'\xff\xfe\xff\xff'
        result = _decode_utf16be(text)
        # Should not crash, result may be empty or replacement chars
        assert isinstance(result, str)


class TestPathNormalization:
    """Test path normalization."""
    
    def test_file_url(self):
        path = 'file:///Users/dj/Music/track.mp3'
        result = _normalize_path(path)
        assert result == '/Users/dj/Music/track.mp3'
    
    def test_file_url_encoded(self):
        path = 'file:///Users/dj/Music/track%20name.mp3'
        result = _normalize_path(path)
        assert result == '/Users/dj/Music/track name.mp3'
    
    def test_mac_volume_format(self):
        path = 'Macintosh HD:Users:dj:Music:track.mp3'
        result = _normalize_path(path)
        assert result == '/Users/dj/Music/track.mp3'
    
    def test_external_volume(self):
        path = 'DJ Drive:Music:track.mp3'
        result = _normalize_path(path)
        assert result == '/Volumes/DJ Drive/Music/track.mp3'
    
    def test_absolute_path(self):
        path = '/Users/dj/Music/track.mp3'
        result = _normalize_path(path)
        assert result == '/Users/dj/Music/track.mp3'
    
    def test_relative_path_boot_drive(self):
        base = Path('/Users/dj/Music/_Serato_/Subcrates/test.crate')
        path = 'Music/track.mp3'
        result = _normalize_path(path, base)
        assert result == '/Music/track.mp3'
    
    def test_relative_path_external_drive(self):
        base = Path('/Volumes/DJ Drive/_Serato_/Subcrates/test.crate')
        path = 'Music/track.mp3'
        result = _normalize_path(path, base)
        assert result == '/Volumes/DJ Drive/Music/track.mp3'


class TestDatabaseV2Parser:
    """Test database V2 parsing."""
    
    def test_empty_database(self):
        """Empty database should return no tracks."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
            f.write(b'')
        
        try:
            result = parse_database_v2(db_path)
            assert result['tracks'] == []
            assert result['version'] is None
        finally:
            db_path.unlink()
    
    def test_version_only(self):
        """Database with only version."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
            data = _write_tlv(b'vrsn', _encode_utf16be('1.0/Serato ScratchLive'))
            f.write(data)
        
        try:
            result = parse_database_v2(db_path)
            assert result['version'] == '1.0/Serato ScratchLive'
            assert result['tracks'] == []
        finally:
            db_path.unlink()
    
    def test_single_track(self):
        """Database with one track."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
            data = _write_tlv(b'vrsn', _encode_utf16be('1.0'))
            data += _write_tlv(b'otrk', b'')  # Start track
            data += _write_tlv(b'ptrk', _encode_utf16be('/Users/dj/track.mp3'))
            data += _write_tlv(b'tsng', _encode_utf16be('Test Track'))
            data += _write_tlv(b'tart', _encode_utf16be('Test Artist'))
            data += _write_tlv(b'talb', _encode_utf16be('Test Album'))
            data += _write_tlv(b'tgen', _encode_utf16be('Electronic'))
            data += _write_tlv(b'tbpm', _encode_utf16be('128.0'))
            data += _write_tlv(b'tkey', _encode_utf16be('Am'))
            data += _write_tlv(b'tlen', _encode_utf16be('180.5'))
            f.write(data)
        
        try:
            result = parse_database_v2(db_path)
            assert result['version'] == '1.0'
            assert len(result['tracks']) == 1
            
            track = result['tracks'][0]
            assert track['path'] == '/Users/dj/track.mp3'
            assert track['title'] == 'Test Track'
            assert track['artist'] == 'Test Artist'
            assert track['album'] == 'Test Album'
            assert track['genre'] == 'Electronic'
            assert track['bpm'] == 128.0
            assert track['key'] == 'Am'
            assert track['duration_s'] == 180.5
        finally:
            db_path.unlink()
    
    def test_multiple_tracks(self):
        """Database with multiple tracks."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
            data = _write_tlv(b'vrsn', _encode_utf16be('1.0'))
            
            # First track
            data += _write_tlv(b'otrk', b'')
            data += _write_tlv(b'ptrk', _encode_utf16be('/track1.mp3'))
            data += _write_tlv(b'tsng', _encode_utf16be('Track 1'))
            
            # Second track
            data += _write_tlv(b'otrk', b'')
            data += _write_tlv(b'ptrk', _encode_utf16be('/track2.mp3'))
            data += _write_tlv(b'tsng', _encode_utf16be('Track 2'))
            
            # Third track
            data += _write_tlv(b'otrk', b'')
            data += _write_tlv(b'ptrk', _encode_utf16be('/track3.mp3'))
            data += _write_tlv(b'tsng', _encode_utf16be('Track 3'))
            
            f.write(data)
        
        try:
            result = parse_database_v2(db_path)
            assert len(result['tracks']) == 3
            assert result['tracks'][0]['title'] == 'Track 1'
            assert result['tracks'][1]['title'] == 'Track 2'
            assert result['tracks'][2]['title'] == 'Track 3'
        finally:
            db_path.unlink()
    
    def test_track_without_path_skipped(self):
        """Track without path should be skipped."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
            data = _write_tlv(b'otrk', b'')
            data += _write_tlv(b'tsng', _encode_utf16be('No Path'))
            # No ptrk tag
            f.write(data)
        
        try:
            result = parse_database_v2(db_path)
            assert result['tracks'] == []
        finally:
            db_path.unlink()
    
    def test_malformed_bpm_ignored(self):
        """Malformed BPM should be ignored, not crash."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
            data = _write_tlv(b'otrk', b'')
            data += _write_tlv(b'ptrk', _encode_utf16be('/track.mp3'))
            data += _write_tlv(b'tbpm', _encode_utf16be('not-a-number'))
            f.write(data)
        
        try:
            result = parse_database_v2(db_path)
            assert len(result['tracks']) == 1
            assert 'bpm' not in result['tracks'][0]
        finally:
            db_path.unlink()


class TestCrateParser:
    """Test .crate file parsing."""
    
    def test_empty_crate(self):
        """Empty crate should return no tracks."""
        with tempfile.NamedTemporaryFile(suffix='.crate', delete=False) as f:
            crate_path = Path(f.name)
            f.write(b'')
        
        try:
            result = parse_crate(crate_path)
            assert result['name'] == crate_path.stem
            assert result['tracks'] == []
            assert result['track_count'] == 0
        finally:
            crate_path.unlink()
    
    def test_crate_with_tracks(self):
        """Crate with tracks."""
        with tempfile.NamedTemporaryFile(suffix='.crate', delete=False) as f:
            crate_path = Path(f.name)
            data = _write_tlv(b'vrsn', _encode_utf16be('1.0'))
            data += _write_tlv(b'ptrk', _encode_utf16be('/Users/dj/Music/track1.mp3'))
            data += _write_tlv(b'ptrk', _encode_utf16be('/Users/dj/Music/track2.mp3'))
            data += _write_tlv(b'pfil', _encode_utf16be('file:///Users/dj/Music/track3.mp3'))
            f.write(data)
        
        try:
            result = parse_crate(crate_path)
            assert result['track_count'] == 3
            assert len(result['tracks']) == 3
            assert result['tracks'][0] == '/Users/dj/Music/track1.mp3'
            assert result['tracks'][1] == '/Users/dj/Music/track2.mp3'
            assert result['tracks'][2] == '/Users/dj/Music/track3.mp3'
        finally:
            crate_path.unlink()
    
    def test_crate_deduplication(self):
        """Duplicate paths should be deduplicated."""
        with tempfile.NamedTemporaryFile(suffix='.crate', delete=False) as f:
            crate_path = Path(f.name)
            data = _write_tlv(b'ptrk', _encode_utf16be('/track.mp3'))
            data += _write_tlv(b'ptrk', _encode_utf16be('/track.mp3'))  # Duplicate
            data += _write_tlv(b'pfil', _encode_utf16be('/other.mp3'))
            f.write(data)
        
        try:
            result = parse_crate(crate_path)
            assert result['track_count'] == 2
            assert result['tracks'][0] == '/track.mp3'
            assert result['tracks'][1] == '/other.mp3'
        finally:
            crate_path.unlink()
    
    def test_nested_crate_name(self):
        """Nested crate names with %% separator."""
        with tempfile.NamedTemporaryFile(
            suffix='.crate', 
            prefix='House%%Deep House%%', 
            delete=False
        ) as f:
            crate_path = Path(f.name)
            # Rename to have the separator in the stem
            new_path = crate_path.parent / 'House%%Deep House.crate'
            crate_path.rename(new_path)
            crate_path = new_path
            
            crate_path.write_bytes(b'')
        
        try:
            result = parse_crate(crate_path)
            assert result['name'] == 'House › Deep House'
        finally:
            crate_path.unlink()
    
    def test_crate_with_malformed_records(self):
        """Crate with malformed records should not crash."""
        with tempfile.NamedTemporaryFile(suffix='.crate', delete=False) as f:
            crate_path = Path(f.name)
            data = _write_tlv(b'ptrk', _encode_utf16be('/good.mp3'))
            data += b'junk data that is not a valid TLV'
            data += _write_tlv(b'ptrk', _encode_utf16be('/also-good.mp3'))
            f.write(data)
        
        try:
            result = parse_crate(crate_path)
            # Should not crash, and should get at least the first valid track
            assert result['track_count'] >= 1
            assert '/good.mp3' in result['tracks']
        finally:
            crate_path.unlink()


class TestFixtures:
    """Test with realistic fixtures."""
    
    def test_database_fixture_matches_crate_count(self):
        """Create a realistic database and crate, verify track counts match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create _Serato_ directory structure
            serato_dir = tmpdir / 'Music' / '_Serato_'
            serato_dir.mkdir(parents=True)
            subcrates_dir = serato_dir / 'Subcrates'
            subcrates_dir.mkdir()
            
            # Create database V2 with 5 tracks
            db_path = serato_dir / 'database V2'
            db_data = _write_tlv(b'vrsn', _encode_utf16be('1.0/Serato'))
            
            tracks = [
                ('/Users/dj/Music/track1.mp3', 'Track 1', 'Artist 1'),
                ('/Users/dj/Music/track2.mp3', 'Track 2', 'Artist 2'),
                ('/Users/dj/Music/track3.mp3', 'Track 3', 'Artist 3'),
                ('/Users/dj/Music/track4.mp3', 'Track 4', 'Artist 4'),
                ('/Users/dj/Music/track5.mp3', 'Track 5', 'Artist 5'),
            ]
            
            for path, title, artist in tracks:
                db_data += _write_tlv(b'otrk', b'')
                db_data += _write_tlv(b'ptrk', _encode_utf16be(path))
                db_data += _write_tlv(b'tsng', _encode_utf16be(title))
                db_data += _write_tlv(b'tart', _encode_utf16be(artist))
            
            db_path.write_bytes(db_data)
            
            # Create crate with 3 of those tracks
            crate_path = subcrates_dir / 'Favorites.crate'
            crate_data = _write_tlv(b'vrsn', _encode_utf16be('1.0'))
            crate_data += _write_tlv(b'ptrk', _encode_utf16be('/Users/dj/Music/track1.mp3'))
            crate_data += _write_tlv(b'ptrk', _encode_utf16be('/Users/dj/Music/track3.mp3'))
            crate_data += _write_tlv(b'ptrk', _encode_utf16be('/Users/dj/Music/track5.mp3'))
            crate_path.write_bytes(crate_data)
            
            # Parse database
            db_result = parse_database_v2(db_path)
            assert len(db_result['tracks']) == 5
            
            # Parse crate
            crate_result = parse_crate(crate_path)
            assert crate_result['track_count'] == 3
            assert crate_result['name'] == 'Favorites'
            
            # Verify crate tracks are subset of database tracks
            db_paths = {t['path'] for t in db_result['tracks']}
            crate_paths = set(crate_result['tracks'])
            assert crate_paths.issubset(db_paths)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
