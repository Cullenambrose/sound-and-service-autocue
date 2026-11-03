"""CLI entry point for the library module."""
import argparse
import json
import sys
from pathlib import Path

from . import db
from .adapters.registry import get_adapter
from .tag_gate import tag_write


def scan_command(args):
    """Scan a directory and import tracks into the database."""
    db.migrate(args.db)
    
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory", file=sys.stderr)
        return 1
    
    adapter = get_adapter(args.adapter or "serato")
    if not adapter:
        print(f"Error: Unknown adapter '{args.adapter}'", file=sys.stderr)
        return 1
    
    # Find audio files
    audio_extensions = {".mp3", ".m4a", ".flac", ".wav", ".aiff", ".aif"}
    tracks = []
    for ext in audio_extensions:
        tracks.extend(directory.rglob(f"*{ext}"))
    
    print(f"Found {len(tracks)} audio files in {directory}")
    
    with db.get_db(args.db) as conn:
        for i, track_path in enumerate(tracks, 1):
            try:
                print(f"[{i}/{len(tracks)}] Scanning {track_path.name}...", end="")
                
                # Read track data
                cues, grid, tags, adapter_data = adapter.read_track(str(track_path))
                
                # Create track object
                from .model import Track, AudioFile
                track = Track(path=str(track_path))
                track.title = tags.get("title")
                track.artist = tags.get("artist")
                track.album = tags.get("album")
                track.genre = tags.get("genre")
                track.bpm = float(tags["bpm"]) if "bpm" in tags else None
                track.cues = cues
                track.grid = grid
                track.adapter_data = adapter_data
                
                # Save to database
                db.save_track(conn, track)
                print(f" ✓ ({len(cues)} cues)")
                
            except Exception as e:
                print(f" ✗ {e}")
    
    print("Scan complete")
    return 0


def show_command(args):
    """Show track information from the database."""
    path = str(Path(args.path).resolve())
    
    with db.get_db(args.db) as conn:
        track = db.get_track(conn, path)
        
        if not track:
            print(f"Track not found in database: {path}", file=sys.stderr)
            return 1
        
        print(json.dumps(track.to_dict(), indent=2))
    
    return 0


def write_cues_command(args):
    """Write cues to a track file."""
    path = str(Path(args.path).resolve())
    
    # Load track from database
    with db.get_db(args.db) as conn:
        track = db.get_track(conn, path)
    
    if not track:
        print(f"Track not found in database: {path}", file=sys.stderr)
        return 1
    
    # Get adapter
    adapter = get_adapter(args.adapter or "serato")
    if not adapter:
        print(f"Error: Unknown adapter '{args.adapter}'", file=sys.stderr)
        return 1
    
    # Plan the write
    plan = adapter.plan_write(track, track.cues)
    
    # Execute write through the gate
    try:
        result = tag_write(
            path,
            adapter,
            plan,
            apply=args.apply,
            db_path=args.db,
        )
        
        print(json.dumps(result, indent=2))
        
        if result.get("dry_run"):
            print("\nDry-run mode. Use --apply to write.")
        else:
            print("\nWrite successful!")
        
        return 0
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def import_serato_command(args):
    """Import tracks from Serato database V2."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import serato_database
    
    db.migrate(args.db)
    
    serato_dir = Path(args.serato_dir).expanduser().resolve()
    
    # Look for _Serato_ directory
    if serato_dir.name != "_Serato_":
        serato_dir = serato_dir / "_Serato_"
    
    if not serato_dir.is_dir():
        print(f"Error: Serato directory not found: {serato_dir}", file=sys.stderr)
        return 1
    
    db_path = serato_dir / "database V2"
    if not db_path.is_file():
        print(f"Error: Serato database not found: {db_path}", file=sys.stderr)
        return 1
    
    print(f"Reading Serato database: {db_path}")
    
    try:
        database = serato_database.parse_database_v2(db_path)
    except serato_database.SeratoParseError as e:
        print(f"Error parsing database: {e}", file=sys.stderr)
        return 1
    
    tracks = database['tracks']
    print(f"Found {len(tracks)} tracks in database")
    
    if database.get('version'):
        print(f"Database version: {database['version']}")
    
    adapter = get_adapter("serato")
    if not adapter:
        print("Error: Serato adapter not found", file=sys.stderr)
        return 1
    
    missing_count = 0
    imported_count = 0
    error_count = 0
    
    with db.get_db(args.db) as conn:
        for i, track_data in enumerate(tracks, 1):
            track_path = track_data['path']
            
            # Check if file exists
            if not Path(track_path).is_file():
                if not args.quiet:
                    print(f"[{i}/{len(tracks)}] ⚠ Missing: {track_path}")
                missing_count += 1
                continue
            
            try:
                if not args.quiet:
                    print(f"[{i}/{len(tracks)}] Importing {Path(track_path).name}...", end="")
                
                # Read detailed track data from file
                cues, grid, tags, adapter_data = adapter.read_track(track_path)
                
                # Create track object, preferring database metadata
                from .model import Track
                track = Track(path=track_path)
                track.title = track_data.get('title') or tags.get('title')
                track.artist = track_data.get('artist') or tags.get('artist')
                track.album = track_data.get('album') or tags.get('album')
                track.genre = track_data.get('genre') or tags.get('genre')
                track.bpm = track_data.get('bpm') or (float(tags['bpm']) if 'bpm' in tags else None)
                track.key = track_data.get('key') or tags.get('key')
                track.duration_s = track_data.get('duration_s')
                track.cues = cues
                track.grid = grid
                track.adapter_data = adapter_data
                
                # Save to database
                db.save_track(conn, track)
                imported_count += 1
                
                if not args.quiet:
                    print(f" ✓ ({len(cues)} cues)")
                
            except Exception as e:
                error_count += 1
                if not args.quiet:
                    print(f" ✗ {e}")
    
    print(f"\nImport complete:")
    print(f"  Imported: {imported_count}")
    print(f"  Missing:  {missing_count}")
    print(f"  Errors:   {error_count}")
    
    return 0


def crates_command(args):
    """List or import Serato crates."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import serato_database
    
    db.migrate(args.db)
    
    serato_dir = Path(args.serato_dir).expanduser().resolve()
    
    # Look for _Serato_ directory
    if serato_dir.name != "_Serato_":
        serato_dir = serato_dir / "_Serato_"
    
    if not serato_dir.is_dir():
        print(f"Error: Serato directory not found: {serato_dir}", file=sys.stderr)
        return 1
    
    subcrates_dir = serato_dir / "Subcrates"
    if not subcrates_dir.is_dir():
        print(f"Error: Subcrates directory not found: {subcrates_dir}", file=sys.stderr)
        return 1
    
    crate_files = serato_database.list_crates(serato_dir)
    
    if not crate_files:
        print("No crates found")
        return 0
    
    if args.list:
        # Just list crates
        print(f"Found {len(crate_files)} crates:")
        for crate_file in crate_files:
            try:
                crate_data = serato_database.parse_crate(crate_file)
                print(f"  {crate_data['name']}: {crate_data['track_count']} tracks")
            except serato_database.SeratoParseError as e:
                print(f"  {crate_file.stem}: (parse error: {e})")
        return 0
    
    # Import crates and their membership
    print(f"Importing {len(crate_files)} crates...")
    
    with db.get_db(args.db) as conn:
        for i, crate_file in enumerate(crate_files, 1):
            try:
                crate_data = serato_database.parse_crate(crate_file)
                crate_name = crate_data['name']
                tracks = crate_data['tracks']
                
                print(f"[{i}/{len(crate_files)}] {crate_name}: {len(tracks)} tracks", end="")
                
                # Create or update crate
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO crates (name, source, external_id) VALUES (?, ?, ?)",
                    (crate_name, 'serato', str(crate_file))
                )
                crate_id = cursor.lastrowid
                
                # Clear existing membership
                cursor.execute("DELETE FROM crate_tracks WHERE crate_id = ?", (crate_id,))
                
                # Add tracks
                missing = 0
                for pos, track_path in enumerate(tracks):
                    # Get track ID
                    cursor.execute("SELECT id FROM tracks WHERE path = ?", (track_path,))
                    track_row = cursor.fetchone()
                    
                    if track_row:
                        cursor.execute(
                            "INSERT INTO crate_tracks (crate_id, track_id, position) VALUES (?, ?, ?)",
                            (crate_id, track_row[0], pos)
                        )
                    else:
                        missing += 1
                
                conn.commit()
                
                if missing > 0:
                    print(f" (⚠ {missing} missing)")
                else:
                    print(" ✓")
                
            except serato_database.SeratoParseError as e:
                print(f" ✗ {e}")
    
    print("Crate import complete")
    return 0


def main(argv=None):
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Platform-neutral library management for DJ software"
    )
    parser.add_argument(
        "--db",
        default="~/.autocue/library.db",
        help="Database path (default: ~/.autocue/library.db)",
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan directory and import tracks")
    scan_parser.add_argument("directory", help="Directory to scan")
    scan_parser.add_argument("--adapter", default="serato", help="Adapter to use")
    
    # show command
    show_parser = subparsers.add_parser("show", help="Show track information")
    show_parser.add_argument("path", help="Track path")
    
    # write-cues command
    write_parser = subparsers.add_parser("write-cues", help="Write cues to track")
    write_parser.add_argument("path", help="Track path")
    write_parser.add_argument("--apply", action="store_true", help="Actually write (default is dry-run)")
    write_parser.add_argument("--adapter", default="serato", help="Adapter to use")
    
    # import-serato command
    import_serato_parser = subparsers.add_parser(
        "import-serato", 
        help="Import tracks from Serato database V2"
    )
    import_serato_parser.add_argument(
        "serato_dir", 
        help="Path to Serato directory (containing _Serato_ folder or the _Serato_ folder itself)"
    )
    import_serato_parser.add_argument(
        "--quiet", 
        action="store_true", 
        help="Only show summary, not per-track progress"
    )
    
    # crates command
    crates_parser = subparsers.add_parser(
        "crates",
        help="List or import Serato crates"
    )
    crates_parser.add_argument(
        "serato_dir",
        help="Path to Serato directory (containing _Serato_ folder or the _Serato_ folder itself)"
    )
    crates_parser.add_argument(
        "--list",
        action="store_true",
        help="Just list crates without importing"
    )
    
    args = parser.parse_args(argv)
    
    # Expand db path
    args.db = str(Path(args.db).expanduser())
    
    # Route to command handler
    if args.command == "scan":
        return scan_command(args)
    elif args.command == "show":
        return show_command(args)
    elif args.command == "write-cues":
        return write_cues_command(args)
    elif args.command == "import-serato":
        return import_serato_command(args)
    elif args.command == "crates":
        return crates_command(args)
    
    return 1


if __name__ == "__main__":
    sys.exit(main())
