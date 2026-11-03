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


def match_command(args):
    """Match requested songs against library database."""
    from . import matcher
    
    # Load request list
    request_path = Path(args.request_file)
    if not request_path.exists():
        print(f"Error: Request file not found: {request_path}", file=sys.stderr)
        return 1
    
    # Parse input (JSON or CSV)
    if request_path.suffix.lower() == ".json":
        with open(request_path) as f:
            data = json.load(f)
            if isinstance(data, dict) and "tracks" in data:
                requests = data["tracks"]
            elif isinstance(data, list):
                requests = data
            else:
                print("Error: JSON must be a list or object with 'tracks' key", file=sys.stderr)
                return 1
    elif request_path.suffix.lower() == ".csv":
        import csv
        requests = []
        with open(request_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                requests.append({"artist": row.get("artist", ""), "title": row.get("title", "")})
    else:
        print(f"Error: Unsupported format. Use .json or .csv", file=sys.stderr)
        return 1
    
    # Load library
    with db.get_db(args.db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, path, title, artist, album, genre, bpm, key FROM tracks")
        library_tracks = [dict(row) for row in cursor.fetchall()]
    
    print(f"Loaded {len(library_tracks)} tracks from library")
    print(f"Matching {len(requests)} requested songs...")
    
    # Configure matching
    config = matcher.MatchConfig(
        prefer_clean=not args.prefer_dirty,
        prefer_intro=not args.no_prefer_intro,
        min_confidence=args.min_confidence,
        fuzzy_threshold=args.fuzzy_threshold,
    )
    
    # Match each request
    results = []
    for i, req in enumerate(requests, 1):
        result = matcher.match_track(req, library_tracks, config)
        results.append(result)
        
        status_sym = {"matched": "✓", "ambiguous": "~", "missing": "✗"}[result.status]
        print(f"[{i}/{len(requests)}] {status_sym} {req.get('artist', '')} - {req.get('title', '')} ({result.confidence:.2f})")
    
    # Output results - clean internal fields before serialization
    def clean_track(track):
        """Remove internal fields from track dict."""
        if track is None:
            return None
        return {k: v for k, v in track.items() if not k.startswith("_")}
    
    output = {
        "results": [
            {
                "request": r.request,
                "status": r.status,
                "confidence": r.confidence,
                "matched_track": clean_track(r.matched_track),
                "candidates": [clean_track(c) for c in r.candidates] if args.include_candidates else [],
                "reason": r.reason,
            }
            for r in results
        ],
        "summary": {
            "total": len(results),
            "matched": sum(1 for r in results if r.status == "matched"),
            "ambiguous": sum(1 for r in results if r.status == "ambiguous"),
            "missing": sum(1 for r in results if r.status == "missing"),
        }
    }
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults written to {args.output}")
    else:
        print("\n" + json.dumps(output, indent=2))
    
    return 0


def build_crate_plan_command(args):
    """Build a crate plan from match results."""
    from . import matcher
    
    # Load match results
    match_path = Path(args.match_file)
    if not match_path.exists():
        print(f"Error: Match file not found: {match_path}", file=sys.stderr)
        return 1
    
    with open(match_path) as f:
        match_data = json.load(f)
    
    # Reconstruct match results
    results = []
    for r in match_data["results"]:
        result = matcher.MatchResult(
            request=r["request"],
            status=r["status"],
            confidence=r["confidence"],
            matched_track=r.get("matched_track"),
            candidates=r.get("candidates", []),
            reason=r.get("reason"),
        )
        results.append(result)
    
    # Load do-not-play list if provided
    do_not_play_results = []
    if args.do_not_play:
        dnp_path = Path(args.do_not_play)
        if not dnp_path.exists():
            print(f"Warning: Do-not-play file not found: {dnp_path}", file=sys.stderr)
        else:
            # Parse do-not-play list
            if dnp_path.suffix.lower() == ".json":
                with open(dnp_path) as f:
                    dnp_data = json.load(f)
                    if isinstance(dnp_data, dict) and "tracks" in dnp_data:
                        dnp_requests = dnp_data["tracks"]
                    elif isinstance(dnp_data, list):
                        dnp_requests = dnp_data
                    else:
                        print("Warning: Do-not-play JSON must be a list or object with 'tracks' key", file=sys.stderr)
                        dnp_requests = []
            elif dnp_path.suffix.lower() == ".csv":
                import csv
                dnp_requests = []
                with open(dnp_path, newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        dnp_requests.append({"artist": row.get("artist", ""), "title": row.get("title", "")})
            else:
                print("Warning: Do-not-play must be .json or .csv", file=sys.stderr)
                dnp_requests = []
            
            # Match do-not-play against library
            with db.get_db(args.db) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, path, title, artist, album, genre, bpm, key FROM tracks")
                library_tracks = [dict(row) for row in cursor.fetchall()]
            
            config = matcher.MatchConfig(min_confidence=0.6)  # Lower threshold for do-not-play
            for dnp_req in dnp_requests:
                dnp_result = matcher.match_track(dnp_req, library_tracks, config)
                do_not_play_results.append(dnp_result)
    
    # Extract request order from original results
    request_order = [r.request for r in results]
    
    # Build plan
    plan = matcher.build_crate_plan(
        results,
        do_not_play_results,
        request_order,
        crate_name=args.crate_name
    )
    
    # Output plan
    output = {
        "crate_name": plan.metadata["crate_name"],
        "ordered_track_ids": plan.ordered_track_ids,
        "matched": [
            {
                "request": r.request,
                "track": r.matched_track,
                "confidence": r.confidence,
            }
            for r in plan.matched
        ],
        "ambiguous": [
            {
                "request": r.request,
                "track": r.matched_track,
                "candidates": r.candidates[:3],
                "confidence": r.confidence,
            }
            for r in plan.ambiguous
        ],
        "missing": [
            {
                "request": r.request,
                "reason": r.reason,
            }
            for r in plan.missing
        ],
        "do_not_play_found": [
            {
                "id": t["id"],
                "artist": t.get("artist"),
                "title": t.get("title"),
            }
            for t in plan.do_not_play_found
        ],
        "metadata": plan.metadata,
    }
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Crate plan written to {args.output}")
    else:
        print(json.dumps(output, indent=2))
    
    # Print summary
    print(f"\n=== Crate Plan Summary ===")
    print(f"Crate: {plan.metadata['crate_name']}")
    print(f"Matched: {plan.metadata['matched_count']}/{plan.metadata['total_requested']}")
    print(f"Ambiguous: {plan.metadata['ambiguous_count']}")
    print(f"Missing: {plan.metadata['missing_count']}")
    print(f"Do-not-play found: {plan.metadata['do_not_play_found']}")
    print(f"Tracks in crate: {len(plan.ordered_track_ids)}")
    
    return 0


def write_crate_command(args):
    """Write a new Serato crate file."""
    # Get adapter
    adapter = get_adapter("serato")
    if not adapter:
        print("Error: Serato adapter not found", file=sys.stderr)
        return 1
    
    # Resolve serato_dir early if provided, for use in both read and write
    resolved_serato_dir = None
    if hasattr(args, 'serato_dir') and args.serato_dir:
        resolved_serato_dir = Path(args.serato_dir).expanduser().resolve()
        if resolved_serato_dir.name != "_Serato_":
            resolved_serato_dir = resolved_serato_dir / "_Serato_"
    
    # Parse track list
    if args.from_file:
        # Read tracks from file (one path per line)
        try:
            with open(args.from_file, 'r') as f:
                tracks = [line.strip() for line in f if line.strip()]
        except OSError as e:
            print(f"Error reading track list file: {e}", file=sys.stderr)
            return 1
    elif args.from_crate:
        # Read tracks from existing crate
        tracks = adapter.read_crate(args.from_crate, serato_dir=resolved_serato_dir)
        if not tracks:
            print(f"Error: Crate '{args.from_crate}' not found or empty", file=sys.stderr)
            return 1
        print(f"Read {len(tracks)} tracks from existing crate '{args.from_crate}'")
    else:
        print("Error: Must specify either --from or --from-crate", file=sys.stderr)
        return 1
    
    if not tracks:
        print("Error: No tracks to write", file=sys.stderr)
        return 1
    
    # Execute write (using resolved_serato_dir from above)
    try:
        result = adapter.write_crate(
            name=args.name,
            tracks=tracks,
            serato_dir=resolved_serato_dir,
            overwrite=args.overwrite,
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
    
    # match command
    match_parser = subparsers.add_parser(
        "match",
        help="Match requested songs against library database"
    )
    match_parser.add_argument(
        "request_file",
        help="JSON or CSV file with requested tracks"
    )
    match_parser.add_argument(
        "--output", "-o",
        help="Output file for match results (default: stdout)"
    )
    match_parser.add_argument(
        "--prefer-dirty",
        action="store_true",
        help="Prefer dirty/explicit versions over clean"
    )
    match_parser.add_argument(
        "--no-prefer-intro",
        action="store_true",
        help="Don't prefer intro edits"
    )
    match_parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.7,
        help="Minimum confidence threshold (0.0-1.0, default: 0.7)"
    )
    match_parser.add_argument(
        "--fuzzy-threshold",
        type=float,
        default=0.8,
        help="Fuzzy matching threshold (0.0-1.0, default: 0.8)"
    )
    match_parser.add_argument(
        "--include-candidates",
        action="store_true",
        help="Include candidate tracks in output"
    )
    
    # build-crate-plan command
    crate_plan_parser = subparsers.add_parser(
        "build-crate-plan",
        help="Build a crate plan from match results"
    )
    crate_plan_parser.add_argument(
        "match_file",
        help="JSON file with match results from 'match' command"
    )
    crate_plan_parser.add_argument(
        "--output", "-o",
        help="Output file for crate plan (default: stdout)"
    )
    crate_plan_parser.add_argument(
        "--do-not-play",
        help="JSON or CSV file with do-not-play list"
    )
    crate_plan_parser.add_argument(
        "--crate-name",
        help="Name for the crate (default: Generated Crate)"
    )
    
    # write-crate command
    write_crate_parser = subparsers.add_parser(
        "write-crate",
        help="Write a new Serato crate file"
    )
    write_crate_parser.add_argument(
        "name",
        help="Crate name (supports nested with %% separator, e.g. 'House%%Deep')"
    )
    write_crate_parser.add_argument(
        "--from",
        dest="from_file",
        help="File containing track paths (one per line)"
    )
    write_crate_parser.add_argument(
        "--from-crate",
        help="Copy tracks from existing crate name"
    )
    write_crate_parser.add_argument(
        "--serato-dir",
        help="Path to Serato directory (defaults to search standard locations)"
    )
    write_crate_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the crate (default is dry-run)"
    )
    write_crate_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting existing crate file"
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
    elif args.command == "match":
        return match_command(args)
    elif args.command == "build-crate-plan":
        return build_crate_plan_command(args)
    elif args.command == "write-crate":
        return write_crate_command(args)
    
    return 1


if __name__ == "__main__":
    sys.exit(main())
