"""Read-only uploaded Serato snapshots, restricted to synthetic demo references."""
import base64
import binascii
import hashlib
import struct

from serato_database import parse_snapshot_bytes, SeratoParseError

if __package__:
    from . import catalog
else:
    import catalog

MAX_SNAPSHOT_BYTES = 512 * 1024
MAX_MEMBERSHIPS = 1000


def decode_upload(body, *, max_bytes=MAX_SNAPSHOT_BYTES):
    if not isinstance(body, dict) or set(body) != {"crate_name", "crate_base64", "database_base64", "volume_root"}:
        raise ValueError("Select a static .crate and database snapshot, a crate name and volume root.")
    name = catalog.text(body["crate_name"], "crate_name", required=True)
    if "/" in name or "\\" in name or not name.lower().endswith(".crate"):
        raise ValueError("Select a static .crate filename, not a path or smart crate.")
    if not isinstance(body["volume_root"], str):
        raise ValueError("volume_root must be text.")
    decoded = []
    for key in ("crate_base64", "database_base64"):
        value = body[key]
        if not isinstance(value, str) or len(value) > (max_bytes + 2) // 3 * 4:
            raise ValueError(f"Each uploaded snapshot must be at most {max_bytes // 1024} KiB.")
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError(f"{key} must contain valid base64.") from error
        if not data or len(data) > max_bytes:
            raise ValueError(f"Each uploaded snapshot must be nonempty and at most {max_bytes // 1024} KiB.")
        decoded.append(data)
    return name, *decoded


def preview(body, library, *, synthetic=True, max_bytes=MAX_SNAPSHOT_BYTES):
    name, crate_bytes, database_bytes = decode_upload(body, max_bytes=max_bytes)
    try:
        crate = parse_snapshot_bytes(crate_bytes, kind="crate", volume_root=body["volume_root"])
        database = parse_snapshot_bytes(database_bytes, kind="database", volume_root=body["volume_root"])
    except SeratoParseError as error:
        raise ValueError(str(error)) from error
    if len(crate["tracks"]) > MAX_MEMBERSHIPS:
        raise ValueError("Import supports at most 1000 crate memberships.")
    records = {}
    for track in database["tracks"]:
        path = track["path"]
        if synthetic and not path.startswith("/Demo/"):
            raise ValueError("Synthetic demo accepts /Demo/ references only; no real library import is enabled.")
        if path in records and records[path] != track:
            raise ValueError("Database contains conflicting metadata for the same path.")
        records[path] = track
    local = {}
    for track in library:
        local.setdefault(track.get("path"), []).append(track)
    crate_hash = hashlib.sha256(crate_bytes).hexdigest()
    database_hash = hashlib.sha256(database_bytes).hexdigest()
    source_id = catalog.digest({"crate_sha256": crate_hash, "database_sha256": database_hash,
                                "name": name, "volume_root": body["volume_root"]})
    entries, songs = [], []
    for position, membership in enumerate(crate["tracks"]):
        path = membership["path"]
        if synthetic and not path.startswith("/Demo/"):
            raise ValueError("Synthetic demo accepts /Demo/ references only; no real library import is enabled.")
        metadata = records.get(path)
        status = "missing_metadata" if not metadata or not metadata.get("title") or not metadata.get("artist") else "unverified"
        candidates = local.get(path, [])
        selected = candidates[0] if len(candidates) == 1 else None
        if status != "missing_metadata":
            if selected and selected.get("title") == metadata["title"] and selected.get("artist") == metadata["artist"]:
                status = "available"
            else:
                status = "metadata_conflict" if candidates else "missing_library_file"
        song_id = hashlib.sha256(path.encode()).hexdigest()
        song = {**(metadata or {}), "id": song_id, "title": (metadata or {}).get("title", ""),
                "artist": (metadata or {}).get("artist", ""), "version": "",
                "path": path, "source_status": status}
        songs.append(song)
        entries.append({
            "id": f"{source_id}:{position}", "position": position, "song_id": song_id,
            "raw_path": membership["raw_path"], "path": path,
            "title": song["title"], "artist": song["artist"], "status": status,
            "file_id": selected.get("id") if status == "available" else None,
        })
    document = {
        "schema_version": 1, "source": "Serato synthetic uploaded snapshot" if synthetic else "Selected Serato uploaded snapshot",
        "crate_sha256": crate_hash, "database_sha256": database_hash,
        "crates": [{"id": source_id, "title": name[:-6].replace("%%", " / "), "available": True,
                    "songs": songs}],
    }
    catalog.validate_snapshot(document)
    return {"id": source_id, "crate_name": name, "entries": entries, "catalog_snapshot": document,
            "counts": {status: sum(entry["status"] == status for entry in entries) for status in
                       ("available", "missing_metadata", "metadata_conflict", "missing_library_file")},
            "note": ("Order and duplicates preserved. Availability is checked against the supplied demo library only; no music paths are opened."
                     if synthetic else "Order and duplicates preserved. Only explicitly selected verified music files can be available. Unselected paths are not opened or guessed."),
            "source": {"crate_sha256": crate_hash, "database_sha256": database_hash}}


def demo_upload(library):
    """Invented TLV bytes for preview only; never creates a .crate or media file."""
    def tlv(tag, value):
        return tag + struct.pack(">I", len(value)) + value

    def text(tag, value):
        return tlv(tag, value.encode("utf-16-be"))

    tracks = {track["id"]: track for track in library}
    ids = ("trk-025", "trk-024", "trk-025", "trk-019")
    crate = text(b"vrsn", "1.0/Serato ScratchLive")
    for key in ids:
        crate += tlv(b"otrk", text(b"ptrk", tracks[key]["path"]))
    crate += tlv(b"otrk", text(b"ptrk", "/Demo/Unavailable/Unknown.mp3"))
    database = text(b"vrsn", "1.0/Serato ScratchLive")
    for track in library:
        database += tlv(b"otrk", text(b"ptrk", track["path"]) + text(b"tsng", track["title"])
                        + text(b"tart", track["artist"]))
    return {"crate_name": "Synthetic%%Starting Set.crate", "volume_root": "/",
            "crate_base64": base64.b64encode(crate).decode(),
            "database_base64": base64.b64encode(database).decode()}
