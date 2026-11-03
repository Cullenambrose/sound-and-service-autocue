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
    
    return 1


if __name__ == "__main__":
    sys.exit(main())
