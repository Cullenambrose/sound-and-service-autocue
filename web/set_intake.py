"""Explicit portable synthetic metadata formats; never opens referenced paths."""
import csv
import io
import json

if __package__:
    from . import catalog, acquisition, serato_import
else:
    import catalog
    import acquisition
    import serato_import


def preview(body, library):
    if not isinstance(body, dict) or set(body) != {"format", "name", "text", "crate_id"}:
        raise ValueError("Portable preview requires format, name, text and crate_id.")
    if not isinstance(body["format"], str) or body["format"] not in {"csv", "m3u", "catalog"}:
        raise ValueError("Supported portable formats are csv, m3u and catalog.")
    name = catalog.text(body["name"], "source name", required=True)
    if not isinstance(body["text"], str) or not 0 < len(body["text"].encode()) <= 512 * 1024:
        raise ValueError("Select nonempty synthetic text no larger than 512 KiB.")
    tracks = []
    provenance = {}
    if body["format"] == "catalog":
        document = json.loads(body["text"])
        catalog.validate_snapshot(document)
        crate = next((crate for crate in document["crates"] if crate["id"] == body["crate_id"]), None)
        if not crate or not crate["available"]:
            raise ValueError("Choose an available crate ID from the selected catalog snapshot.")
        tracks = crate["songs"]
        provenance = {"catalog_sha256": catalog.digest(document), "crate_id": crate["id"]}
    elif body["format"] == "csv":
        reader = csv.DictReader(io.StringIO(body["text"]))
        if reader.fieldnames != ["id", "title", "artist", "path", "version"]:
            raise ValueError("CSV header must be exactly id,title,artist,path,version.")
        tracks = list(reader)
        if any(set(row) != set(reader.fieldnames) or any(value is None for value in row.values())
               for row in tracks):
            raise ValueError("Every CSV row must have exactly the five declared fields.")
    else:
        local = {}
        for track in library:
            local.setdefault(track["path"], []).append(track)
        for line in body["text"].splitlines():
            path = line.strip()
            if not path or path.startswith("#"):
                continue
            matches = local.get(path, [])
            track = matches[0] if len(matches) == 1 else None
            if track is None:
                # Unknown paths retain their reference, not guessed filename metadata.
                tracks.append({"id": catalog.digest(path), "title": "", "artist": "", "path": path})
            else:
                tracks.append({key: track[key] for key in ("id", "title", "artist", "path", "version")
                               if key in track and not (key == "version" and track[key] is None)})
    if not 0 < len(tracks) <= serato_import.MAX_MEMBERSHIPS:
        raise ValueError("Portable starting sets need 1 to 1000 memberships.")
    identity = catalog.digest(body)
    local = {}
    for track in library:
        local.setdefault(track["path"], []).append(track)
    entries, songs = [], []
    for index, value in enumerate(tracks):
        if not isinstance(value, dict):
            raise ValueError("Every source entry must be an object.")
        record = {key: value.get(key, "") for key in ("id", "title", "artist", "path", "version")}
        # M3U/catalog missing metadata is retained, but path validation is shared.
        acquisition.synthetic_track({**record, "title": record["title"] or "Unknown",
                                      "artist": record["artist"] or "Unknown"})
        candidates = local.get(record["path"], [])
        selected = candidates[0] if len(candidates) == 1 else None
        status = "missing_metadata" if not record["title"] or not record["artist"] else (
            "available" if selected and all((selected.get(key) or "") == record[key]
                                            for key in ("title", "artist", "version"))
            else "metadata_conflict" if candidates else "missing_library_file")
        song_id = catalog.digest(record)
        songs.append({**value, **record, "id": song_id, "source_status": status})
        entries.append({"id": f"{identity}:{index}", "position": index, "song_id": song_id,
                        "raw_path": record["path"], "path": record["path"], "title": record["title"],
                        "artist": record["artist"], "version": record["version"], "status": status,
                        "file_id": selected["id"] if status == "available" else None})
    document = {"schema_version": 1, "source": f"Synthetic {body['format']} import",
                "crates": [{"id": identity, "title": name, "available": True, "songs": songs}]}
    catalog.validate_snapshot(document)
    return {"id": identity, "crate_name": name, "entries": entries, "catalog_snapshot": document,
            "counts": {status: sum(entry["status"] == status for entry in entries) for status in
                       ("available", "missing_metadata", "metadata_conflict", "missing_library_file")},
            "source": {"format": body["format"], "input_sha256": catalog.digest(body), **provenance},
            "note": "Synthetic metadata only. Order and duplicates preserved; no referenced path was opened."}


def personal_snapshot(db, crate_id):
    """Read immutable metadata/order directly, without statting selected library files."""
    crate = db.execute("SELECT id,name,revision FROM personal_crates WHERE id=?", (crate_id,)).fetchone()
    if not crate:
        raise ValueError("Choose an existing personal crate.")
    songs = []
    for row in db.execute("""
        SELECT e.id,e.snapshot_id,e.song_id,s.metadata_json FROM personal_crate_entries e
        JOIN catalog_songs s ON s.snapshot_id=e.snapshot_id AND s.source_id=e.song_id
        WHERE e.crate_id=? ORDER BY e.position
    """, (crate_id,)):
        song = json.loads(row["metadata_json"])
        acquisition.synthetic_track({key: song.get(key, "") for key in ("id", "title", "artist", "path")})
        songs.append({**song, "id": row["id"], "origin": {
            "snapshot_id": row["snapshot_id"], "song_id": row["song_id"]}})
    if not songs:
        raise ValueError("Add synthetic catalog memberships before handing a personal set to Prep.")
    document = {"schema_version": 1, "source": "Synthetic personal-set handoff",
                "crates": [{"id": crate["id"], "title": crate["name"], "available": True,
                            "revision": crate["revision"], "songs": songs}]}
    catalog.validate_snapshot(document)
    return document
