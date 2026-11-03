"""Integration tests for Serato crate writer via SeratoAdapter.

These tests verify:
1. Safety checks (Serato running, backup creation)
2. Dry-run mode
3. Integration with SeratoAdapter
4. CLI command
"""
import tempfile
import struct
import json
from pathlib import Path
import pytest
import sys

# Add engine to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
import serato_database
from library import __main__ as cli
from library.adapters.serato import SeratoAdapter


def _encode_utf16be(text: str) -> bytes:
    """Helper to encode UTF-16BE."""
    return text.encode('utf-16-be')


def _write_tlv(tag: bytes, value: bytes) -> bytes:
    """Helper to create a TLV record."""
    return tag + struct.pack('>I', len(value)) + value


class TestSeratoAdapterWriteCrate:
    """Test SeratoAdapter.write_crate method."""
    
    def test_refuses_when_serato_running(self):
        """Test that write_crate refuses if Serato is running."""
        adapter = SeratoAdapter(process_checker=lambda: True)  # Mock: Serato is running
        
        with tempfile.TemporaryDirectory() as tmpdir:
            serato_dir = Path(tmpdir) / "_Serato_"
            serato_dir.mkdir()
            (serato_dir / "Subcrates").mkdir()
            
            with pytest.raises(RuntimeError, match="is currently running"):
                adapter.write_crate(
                    name="TestCrate",
                    tracks=['/track.mp3'],
                    serato_dir=serato_dir,
                    apply=True,
                )
    
    def test_dry_run_mode_returns_preview(self):
        """Test that dry-run mode returns preview without writing."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            serato_dir = Path(tmpdir) / "_Serato_"
            serato_dir.mkdir()
            (serato_dir / "Subcrates").mkdir()
            
            tracks = ['/track1.mp3', '/track2.mp3']
            result = adapter.write_crate(
                name="DryRun",
                tracks=tracks,
                serato_dir=serato_dir,
                apply=False,  # Dry-run
            )
            
            # Should return preview
            assert result['dry_run'] is True
            assert result['name'] == 'DryRun'
            assert result['track_count'] == 2
            assert result['exists'] is False
            assert result['tracks'] == tracks
            
            # File should NOT exist
            crate_path = serato_dir / "Subcrates" / "DryRun.crate"
            assert not crate_path.exists()
    
    def test_apply_writes_crate(self):
        """Test that apply=True actually writes the crate."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            serato_dir = Path(tmpdir) / "_Serato_"
            serato_dir.mkdir()
            (serato_dir / "Subcrates").mkdir()
            
            tracks = ['/track1.mp3', '/track2.mp3']
            result = adapter.write_crate(
                name="Applied",
                tracks=tracks,
                serato_dir=serato_dir,
                apply=True,
            )
            
            # Should return success
            assert result['success'] is True
            assert result['name'] == 'Applied'
            assert result['track_count'] == 2
            assert result['overwrote_existing'] is False
            
            # File should exist
            crate_path = serato_dir / "Subcrates" / "Applied.crate"
            assert crate_path.exists()
            
            # Verify by reading back
            parsed = serato_database.parse_crate(crate_path)
            assert parsed['track_count'] == 2
    
    def test_refuses_overwrite_without_flag(self):
        """Test that existing crate cannot be overwritten without flag."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            serato_dir = Path(tmpdir) / "_Serato_"
            serato_dir.mkdir()
            subcrates_dir = serato_dir / "Subcrates"
            subcrates_dir.mkdir()
            
            # Create initial crate
            adapter.write_crate(
                name="Existing",
                tracks=['/track1.mp3'],
                serato_dir=serato_dir,
                apply=True,
            )
            
            # Try to overwrite without flag
            with pytest.raises(FileExistsError, match="already exists"):
                adapter.write_crate(
                    name="Existing",
                    tracks=['/track2.mp3'],
                    serato_dir=serato_dir,
                    overwrite=False,
                    apply=True,
                )
    
    def test_creates_backup_on_overwrite(self):
        """Test that backup is created when overwriting."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            serato_dir = Path(tmpdir) / "_Serato_"
            serato_dir.mkdir()
            subcrates_dir = serato_dir / "Subcrates"
            subcrates_dir.mkdir()
            
            # Create initial crate
            adapter.write_crate(
                name="Backup",
                tracks=['/track1.mp3'],
                serato_dir=serato_dir,
                apply=True,
            )
            
            # Overwrite with flag
            result = adapter.write_crate(
                name="Backup",
                tracks=['/track2.mp3', '/track3.mp3'],
                serato_dir=serato_dir,
                overwrite=True,
                apply=True,
            )
            
            # Should have created backup
            assert result['backup_path'] is not None
            backup_dir = Path(result['backup_path'])
            assert backup_dir.exists()
            assert backup_dir.is_dir()
            
            # Verify backup contains original data (backup_path is now the Subcrates directory)
            backup_crate = backup_dir / "Backup.crate"
            assert backup_crate.exists()
            backup_parsed = serato_database.parse_crate(backup_crate)
            assert backup_parsed['track_count'] == 1
            
            # Verify new crate has new data
            crate_path = serato_dir / "Subcrates" / "Backup.crate"
            new_parsed = serato_database.parse_crate(crate_path)
            assert new_parsed['track_count'] == 2
    
    def test_finds_serato_dir_automatically(self):
        """Test that Serato directory is found automatically if not specified."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create _Serato_ under a Music directory that might be found
            music_dir = Path(tmpdir) / "Music"
            serato_dir = music_dir / "_Serato_"
            serato_dir.mkdir(parents=True)
            (serato_dir / "Subcrates").mkdir()
            
            # Mock the find_serato_dirs to return our test directory
            original_find = serato_database.find_serato_dirs
            serato_database.find_serato_dirs = lambda root: [serato_dir]
            
            try:
                result = adapter.write_crate(
                    name="AutoFind",
                    tracks=['/track.mp3'],
                    serato_dir=None,  # Don't specify
                    apply=True,
                )
                
                assert result['success'] is True
                
                crate_path = serato_dir / "Subcrates" / "AutoFind.crate"
                assert crate_path.exists()
                
            finally:
                # Restore original function
                serato_database.find_serato_dirs = original_find
    
    def test_nested_crate_name(self):
        """Test writing nested crate with %% separator."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            serato_dir = Path(tmpdir) / "_Serato_"
            serato_dir.mkdir()
            (serato_dir / "Subcrates").mkdir()
            
            result = adapter.write_crate(
                name="House%%Deep",
                tracks=['/track.mp3'],
                serato_dir=serato_dir,
                apply=True,
            )
            
            assert result['name'] == 'House › Deep'
            
            crate_path = serato_dir / "Subcrates" / "House%%Deep.crate"
            assert crate_path.exists()
    
    def test_always_backs_up_entire_subcrates_folder(self):
        """Test that ENTIRE Subcrates folder is backed up before ANY write."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            serato_dir = Path(tmpdir) / "_Serato_"
            serato_dir.mkdir()
            subcrates_dir = serato_dir / "Subcrates"
            subcrates_dir.mkdir()
            
            # Create some existing crates
            existing_crates = ["Existing1.crate", "Existing2.crate", "Existing3.crate"]
            for crate_name in existing_crates:
                crate_path = subcrates_dir / crate_name
                # Write a simple crate
                import serato_database
                serato_database.write_crate(crate_path, ['/existing.mp3'])
            
            # Write a NEW crate (not overwriting)
            result = adapter.write_crate(
                name="NewCrate",
                tracks=['/new.mp3'],
                serato_dir=serato_dir,
                apply=True,
            )
            
            # Verify backup was created
            assert result['backup_path'] is not None
            backup_dir = Path(result['backup_path'])
            assert backup_dir.exists()
            
            # Verify ALL existing crates were backed up
            for crate_name in existing_crates:
                backup_crate = backup_dir / crate_name
                assert backup_crate.exists(), f"Expected backup of {crate_name}"
    
    def test_backup_verification_byte_for_byte(self):
        """Test that backups are verified byte-for-byte."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            serato_dir = Path(tmpdir) / "_Serato_"
            serato_dir.mkdir()
            subcrates_dir = serato_dir / "Subcrates"
            subcrates_dir.mkdir()
            
            # Create an existing crate
            import serato_database
            existing_crate = subcrates_dir / "Existing.crate"
            serato_database.write_crate(existing_crate, ['/track1.mp3', '/track2.mp3'])
            
            # Write a new crate
            result = adapter.write_crate(
                name="NewCrate",
                tracks=['/new.mp3'],
                serato_dir=serato_dir,
                apply=True,
            )
            
            # Verify backup_verified flag is True
            assert result['backup_verified'] is True
            
            # Verify backup exists and matches original
            backup_dir = Path(result['backup_path'])
            backup_crate = backup_dir / "Existing.crate"
            assert backup_crate.exists()
            
            # Verify byte-for-byte match using filecmp
            import filecmp
            assert filecmp.cmp(existing_crate, backup_crate, shallow=False)
    
    def test_audit_log_on_crate_write(self):
        """Test that every applied crate write logs to audit database."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            serato_dir = tmpdir / "_Serato_"
            serato_dir.mkdir()
            subcrates_dir = serato_dir / "Subcrates"
            subcrates_dir.mkdir()
            
            # Create test database
            test_db = tmpdir / "test.db"
            from library import db
            db.migrate(str(test_db))
            
            # Write a new crate
            result = adapter.write_crate(
                name="TestCrate",
                tracks=['/track1.mp3', '/track2.mp3'],
                serato_dir=serato_dir,
                apply=True,
                db_path=str(test_db),
            )
            
            # Verify audit log was created
            assert result['change_id'] is not None
            
            # Verify audit record in database
            with db.get_db(str(test_db)) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM changes WHERE id = ?",
                    (result['change_id'],)
                )
                change = cursor.fetchone()
                
                assert change is not None
                assert change['adapter'] == 'serato'
                assert change['operation'] == 'write_crate'
                assert 'TestCrate.crate' in change['path']
                assert '2 tracks' in change['after_summary']
                assert change['backup_path'] is not None
    
    def test_audit_log_distinguishes_new_vs_overwrite(self):
        """Test that audit log distinguishes new crates from overwrites."""
        adapter = SeratoAdapter(process_checker=lambda: False)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            serato_dir = tmpdir / "_Serato_"
            serato_dir.mkdir()
            subcrates_dir = serato_dir / "Subcrates"
            subcrates_dir.mkdir()
            
            test_db = tmpdir / "test.db"
            from library import db
            db.migrate(str(test_db))
            
            # Write initial crate
            result1 = adapter.write_crate(
                name="Test",
                tracks=['/track1.mp3'],
                serato_dir=serato_dir,
                apply=True,
                db_path=str(test_db),
            )
            
            # Overwrite it
            result2 = adapter.write_crate(
                name="Test",
                tracks=['/track1.mp3', '/track2.mp3'],
                serato_dir=serato_dir,
                overwrite=True,
                apply=True,
                db_path=str(test_db),
            )
            
            # Verify both are logged
            with db.get_db(str(test_db)) as conn:
                cursor = conn.cursor()
                
                # First write (new crate)
                cursor.execute("SELECT * FROM changes WHERE id = ?", (result1['change_id'],))
                change1 = cursor.fetchone()
                assert 'new crate' in change1['before_summary']
                
                # Second write (overwrite)
                cursor.execute("SELECT * FROM changes WHERE id = ?", (result2['change_id'],))
                change2 = cursor.fetchone()
                assert 'existing crate' in change2['before_summary']


class TestCLIWriteCrate:
    """Test write-crate CLI command."""
    
    def test_cli_help(self):
        """Test that write-crate command is available."""
        # argparse --help exits with SystemExit(0)
        with pytest.raises(SystemExit) as exc_info:
            cli.main(['--help'])
        assert exc_info.value.code == 0
    
    def test_cli_dry_run(self):
        """Test CLI dry-run mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            serato_dir = tmpdir / "_Serato_"
            serato_dir.mkdir()
            (serato_dir / "Subcrates").mkdir()
            
            # Create track list file
            track_list = tmpdir / "tracks.txt"
            track_list.write_text("/track1.mp3\n/track2.mp3\n")
            
            # Create test database
            test_db = tmpdir / "test.db"
            
            # Run dry-run
            result = cli.main([
                '--db', str(test_db),
                'write-crate', 'TestCrate',
                '--from', str(track_list),
                '--serato-dir', str(serato_dir),
                # No --apply, should be dry-run
            ])
            
            assert result == 0
            
            # Crate should NOT exist
            crate_path = serato_dir / "Subcrates" / "TestCrate.crate"
            assert not crate_path.exists()
    
    def test_cli_apply_writes(self):
        """Test CLI with --apply actually writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            serato_dir = tmpdir / "_Serato_"
            serato_dir.mkdir()
            (serato_dir / "Subcrates").mkdir()
            
            # Create track list file
            track_list = tmpdir / "tracks.txt"
            track_list.write_text("/track1.mp3\n/track2.mp3\n")
            
            # Create test database
            test_db = tmpdir / "test.db"
            
            # Run with --apply
            result = cli.main([
                '--db', str(test_db),
                'write-crate', 'Applied',
                '--from', str(track_list),
                '--serato-dir', str(serato_dir),
                '--apply',
            ])
            
            assert result == 0
            
            # Crate should exist
            crate_path = serato_dir / "Subcrates" / "Applied.crate"
            assert crate_path.exists()
            
            # Verify content
            parsed = serato_database.parse_crate(crate_path)
            assert parsed['track_count'] == 2
    
    def test_cli_from_crate(self):
        """Test CLI --from-crate option."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            serato_dir = tmpdir / "_Serato_"
            serato_dir.mkdir()
            subcrates_dir = serato_dir / "Subcrates"
            subcrates_dir.mkdir()
            
            # Create source crate manually
            source_crate = subcrates_dir / "Source.crate"
            crate_data = _write_tlv(b'vrsn', _encode_utf16be('1.0'))
            crate_data += _write_tlv(b'ptrk', _encode_utf16be('/track1.mp3'))
            crate_data += _write_tlv(b'ptrk', _encode_utf16be('/track2.mp3'))
            source_crate.write_bytes(crate_data)
            
            # Create test database
            test_db = tmpdir / "test.db"
            
            # Copy from existing crate
            result = cli.main([
                '--db', str(test_db),
                'write-crate', 'Copy',
                '--from-crate', 'Source',
                '--serato-dir', str(serato_dir),
                '--apply',
            ])
            
            assert result == 0
            
            # New crate should exist with same tracks
            copy_crate = subcrates_dir / "Copy.crate"
            assert copy_crate.exists()
            
            parsed = serato_database.parse_crate(copy_crate)
            assert parsed['track_count'] == 2
    
    def test_cli_requires_from_or_from_crate(self):
        """Test that CLI requires either --from or --from-crate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            serato_dir = tmpdir / "_Serato_"
            serato_dir.mkdir()
            (serato_dir / "Subcrates").mkdir()
            
            test_db = tmpdir / "test.db"
            
            # Should fail without --from or --from-crate
            result = cli.main([
                '--db', str(test_db),
                'write-crate', 'Test',
                '--serato-dir', str(serato_dir),
            ])
            
            assert result != 0
    
    def test_cli_overwrite_flag(self):
        """Test CLI --overwrite flag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            serato_dir = tmpdir / "_Serato_"
            serato_dir.mkdir()
            subcrates_dir = serato_dir / "Subcrates"
            subcrates_dir.mkdir()
            
            # Create track list file
            track_list = tmpdir / "tracks.txt"
            track_list.write_text("/track1.mp3\n")
            
            test_db = tmpdir / "test.db"
            
            # Create initial crate
            cli.main([
                '--db', str(test_db),
                'write-crate', 'Overwrite',
                '--from', str(track_list),
                '--serato-dir', str(serato_dir),
                '--apply',
            ])
            
            # Update track list
            track_list.write_text("/track1.mp3\n/track2.mp3\n")
            
            # Try without --overwrite (should fail)
            result = cli.main([
                '--db', str(test_db),
                'write-crate', 'Overwrite',
                '--from', str(track_list),
                '--serato-dir', str(serato_dir),
                '--apply',
            ])
            assert result != 0
            
            # Try with --overwrite (should succeed)
            result = cli.main([
                '--db', str(test_db),
                'write-crate', 'Overwrite',
                '--from', str(track_list),
                '--serato-dir', str(serato_dir),
                '--apply',
                '--overwrite',
            ])
            assert result == 0
            
            # Verify updated
            crate_path = subcrates_dir / "Overwrite.crate"
            parsed = serato_database.parse_crate(crate_path)
            assert parsed['track_count'] == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
