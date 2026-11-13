"""Read-only catalog health and explicit, durable play-history operations."""
from datetime import datetime, timedelta, timezone
import json

if __package__:
    from . import catalog
else:
    import catalog

MAX_DUPLICATE_GROUPS = 100
MAX_HISTORY_LIMIT = 500
MAX_ID_LENGTH = 256
MAX_NOTE_LENGTH = 1000
MAX_ERROR_DETAILS = 100


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS library_play_events (
            request_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            played_at TEXT NOT NULL,
            note TEXT NOT NULL,
            body_json TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            recorded_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS library_play_events_file_played
            ON library_play_events(file_id, played_at);
        CREATE INDEX IF NOT EXISTS library_play_events_played
            ON library_play_events(played_at DESC, request_id);
        CREATE TABLE IF NOT EXISTS library_play_removals (
            event_id TEXT PRIMARY KEY REFERENCES library_play_events(request_id),
            removal_id TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
            revision INTEGER NOT NULL
        );
    """)


def _indexed_state(root, file):
    if root["error"]:
        return "offline_at_last_check"
    if not file["available"]:
        return "missing_at_last_scan"
    if file["error"]:
        return "index_error"
    return "available_at_last_scan"


def _file_summary(root, file, metadata):
    return {
        "file_id": file["id"],
        "root_id": file["root_id"],
        "relative_path": file["relative_path"],
        "indexed_state": _indexed_state(root, file),
        "available_at_last_scan": bool(file["available"]),
        "content_hash": file["content_hash"],
        "metadata": metadata,
        "error": file["error"],
    }


def _metadata(file):
    try:
        value = json.loads(file["metadata_json"])
        if isinstance(value, dict):
            return value, ""
        return {}, "Indexed metadata is not an object."
    except (TypeError, ValueError):
        return {}, "Indexed metadata is malformed JSON."


def _bounded_groups(grouped, *, evidence, description, limit=MAX_DUPLICATE_GROUPS):
    selected = sorted(grouped.items(), key=lambda item: item[0])
    groups = []
    for key, records in selected[:limit]:
        groups.append({
            "evidence": evidence,
            "description": description,
            "identity": key,
            "file_ids": [record["file_id"] for record in records],
            "files": records,
        })
    return groups, max(0, len(selected) - len(groups))


def health(db):
    """Report the indexed database snapshot without touching any indexed path."""
    required = {
        "catalog_snapshots", "catalog_crates", "catalog_songs",
        "library_roots", "library_files",
    }
    present = {row["name"] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = sorted(required - present)
    if missing:
        return {
            "state": "catalog_unavailable",
            "basis": "indexed_snapshot_only",
            "counts": {},
            "root_states": {},
            "errors": [{"scope": "catalog", "error": "Missing catalog tables: " + ", ".join(missing)}],
            "duplicate_groups": [],
            "related_edit_groups": [],
            "group_limits": {"max_groups": MAX_DUPLICATE_GROUPS},
        }

    roots = {row["id"]: dict(row) for row in db.execute(
        "SELECT id,path,scanned_at,error FROM library_roots ORDER BY id")}
    files = [dict(row) for row in db.execute(
        "SELECT id,root_id,relative_path,content_hash,metadata_json,error,available "
        "FROM library_files ORDER BY root_id,relative_path,id")]
    metadata_by_id = {}
    metadata_errors = {}
    for file in files:
        metadata, error = _metadata(file)
        metadata_by_id[file["id"]] = metadata
        if error:
            metadata_errors[file["id"]] = error

    exact, related = {}, {}
    for file in files:
        root = roots.get(file["root_id"], {"error": "Indexed root record is missing."})
        record = _file_summary(root, file, metadata_by_id[file["id"]])
        if file["id"] in metadata_errors:
            record["metadata_error"] = metadata_errors[file["id"]]
        if file["content_hash"]:
            exact.setdefault(file["content_hash"], []).append(record)
        metadata = metadata_by_id[file["id"]]
        artist, title = metadata.get("artist"), metadata.get("title")
        if isinstance(artist, str) and artist.strip() and isinstance(title, str) and title.strip():
            # This is the same conservative family identity used to find catalog
            # candidates; it groups edits but never certifies audio equivalence.
            identity = catalog.normalize(artist) + "\u0000" + catalog.base_title(title)
            related.setdefault(identity, []).append(record)

    exact = {key: value for key, value in exact.items() if len(value) > 1}
    related = {key: value for key, value in related.items() if len(value) > 1}
    duplicate_groups, duplicate_omitted = _bounded_groups(
        exact, evidence="whole_file_sha256",
        description="Identical indexed whole-file hashes prove the bytes matched when indexed; they do not prove audio equivalence or make any copy disposable.")
    related_groups, related_omitted = _bounded_groups(
        related, evidence="catalog_matcher_identity",
        description="Normalized artist and matcher base-title agree. This is metadata-family evidence only; edits and exact file versions remain distinct.")

    root_states = {"last_scan_online": 0, "offline_at_last_check": 0, "not_scanned": 0}
    for root in roots.values():
        if root["error"]:
            root_states["offline_at_last_check"] += 1
        elif root["scanned_at"] is None:
            root_states["not_scanned"] += 1
        else:
            root_states["last_scan_online"] += 1
    files_offline = sum(
        1 for file in files
        if roots.get(file["root_id"], {}).get("error"))
    errors = []
    for root in roots.values():
        if root["error"]:
            errors.append({"scope": "root", "root_id": root["id"], "error": root["error"]})
    for file in files:
        if file["error"]:
            errors.append({"scope": "file", "file_id": file["id"], "error": file["error"]})
        if file["id"] in metadata_errors:
            errors.append({"scope": "metadata", "file_id": file["id"],
                           "error": metadata_errors[file["id"]]})
    error_count = len(errors)

    return {
        "state": "ok",
        "basis": "indexed_snapshot_only",
        "fresh_validation_performed": False,
        "counts": {
            "snapshots": db.execute("SELECT COUNT(*) FROM catalog_snapshots").fetchone()[0],
            "crates": db.execute("SELECT COUNT(*) FROM catalog_crates").fetchone()[0],
            "songs": db.execute("SELECT COUNT(*) FROM catalog_songs").fetchone()[0],
            "roots": len(roots),
            "files": len(files),
            "available_at_last_scan": sum(bool(file["available"]) for file in files),
            "missing_at_last_scan": sum(not bool(file["available"]) for file in files),
            "files_under_roots_offline_at_last_check": files_offline,
            "file_index_errors": sum(bool(file["error"]) for file in files),
            "metadata_errors": len(metadata_errors),
            "files_without_hash": sum(not file["content_hash"] for file in files),
            "error_count": error_count,
            "exact_hash_duplicate_groups": len(exact),
            "related_edit_groups": len(related),
        },
        "root_states": root_states,
        "errors": errors[:MAX_ERROR_DETAILS],
        "errors_omitted": max(0, error_count - MAX_ERROR_DETAILS),
        "duplicate_groups": duplicate_groups,
        "duplicate_groups_omitted": duplicate_omitted,
        "related_edit_groups": related_groups,
        "related_edit_groups_omitted": related_omitted,
        "group_limits": {"max_groups_per_kind": MAX_DUPLICATE_GROUPS},
    }


def _utc_timestamp(value):
    if not isinstance(value, str) or len(value) > 64 or "T" not in value:
        raise ValueError("played_at must be an ISO 8601 timestamp with an explicit UTC timezone.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise ValueError("played_at must be a valid ISO 8601 timestamp with an explicit UTC timezone.") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("played_at must include a UTC timezone (Z or +00:00).")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def record_play(db, body, dataset, *, begin=True):
    """Record a caller-supplied play observation; never infer or poll playback."""
    fields = {"request_id", "dataset_id", "file_id", "played_at", "note"}
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError("Play history requires exactly request_id, dataset_id, file_id, played_at and note.")
    for key in ("request_id", "dataset_id", "file_id"):
        catalog.text(body[key], key, required=True)
        if len(body[key]) > MAX_ID_LENGTH:
            raise ValueError(f"{key} must be at most {MAX_ID_LENGTH} characters.")
    catalog.text(body["note"], "note")
    if len(body["note"]) > MAX_NOTE_LENGTH:
        raise ValueError(f"note must be at most {MAX_NOTE_LENGTH} characters.")
    catalog.text(dataset, "dataset", required=True)
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Play event belongs to a different dataset; reload the app.")

    normalized_body = {**body, "played_at": _utc_timestamp(body["played_at"])}
    encoded = catalog.canonical(normalized_body)
    if begin and not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    previous = db.execute("SELECT body_json,receipt_json FROM library_play_events WHERE request_id=?",
                          (body["request_id"],)).fetchone()
    if previous:
        if previous["body_json"] != encoded:
            raise catalog.Conflict("Play request ID was already used with different content.")
        return json.loads(previous["receipt_json"])
    if not db.execute("SELECT 1 FROM library_files WHERE id=?", (body["file_id"],)).fetchone():
        raise ValueError("Play history references an unknown indexed file ID; select an exact file.")

    receipt = {"ok": True, **normalized_body}
    db.execute("""
        INSERT INTO library_play_events
        (request_id,dataset_id,file_id,played_at,note,body_json,receipt_json,recorded_at)
        VALUES(?,?,?,?,?,?,?,?)
    """, (normalized_body["request_id"], normalized_body["dataset_id"], normalized_body["file_id"],
          normalized_body["played_at"], normalized_body["note"], encoded, catalog.canonical(receipt),
          datetime.now(timezone.utc).timestamp()))
    return receipt


def play_history(db, limit=100):
    if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_LIMIT:
        raise ValueError(f"limit must be an integer from 1 through {MAX_HISTORY_LIMIT}.")
    return [{
        "request_id": row["request_id"],
        "dataset_id": row["dataset_id"],
        "file_id": row["file_id"],
        "played_at": row["played_at"],
        "note": row["note"],
    } for row in db.execute("""
        SELECT request_id,dataset_id,file_id,played_at,note
        FROM library_play_events e
        WHERE NOT EXISTS (SELECT 1 FROM library_play_removals r
                          WHERE r.event_id=e.request_id AND r.active=1)
        ORDER BY played_at DESC,request_id DESC LIMIT ?
    """, (limit,))]


def history_summary(db):
    """Return {file_id: {play_count, last_played}} for observed play events."""
    return {
        row["file_id"]: {
            "play_count": row["play_count"],
            "last_played": row["last_played"],
        }
        for row in db.execute("""
            SELECT file_id,COUNT(*) AS play_count,MAX(played_at) AS last_played
            FROM library_play_events e
            WHERE NOT EXISTS (SELECT 1 FROM library_play_removals r
                              WHERE r.event_id=e.request_id AND r.active=1)
            GROUP BY file_id ORDER BY file_id
        """)
    }
