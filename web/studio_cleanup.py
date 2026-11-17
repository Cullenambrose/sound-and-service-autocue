"""Reviewed, reversible repair of AutoCue catalog links between exact duplicates."""
import json
import time
from pathlib import Path

from . import catalog, studio_service

MAX_REFERENCES = 100


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS studio_file_repairs (
            id TEXT PRIMARY KEY, document_json TEXT NOT NULL,
            approval_json TEXT, undo_json TEXT
        )
    """)


def _file_proof(db, file_id):
    row = db.execute("SELECT * FROM library_files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        raise ValueError("Selected duplicate file is no longer indexed.")
    root = db.execute("SELECT * FROM library_roots WHERE id=?", (row["root_id"],)).fetchone()
    if root is None:
        raise catalog.Conflict("Selected duplicate file has no indexed root.")
    root_path = Path(root["path"])
    path = root_path / row["relative_path"]
    if (path.is_symlink() or path.resolve(strict=True) != path
            or not path.resolve(strict=True).is_relative_to(root_path)):
        raise catalog.Conflict("Selected duplicate file is symlinked or outside its indexed root.")
    state = catalog.file_status(row, root)
    if state != "available" or not row["content_hash"] or row["error"]:
        raise catalog.Conflict("Selected duplicate file is not currently available and verified.")
    before = path.stat()
    identity = (before.st_dev, before.st_ino, *catalog.signature(before))
    actual_hash = catalog.file_hash(path)
    after = path.stat()
    if (identity != (after.st_dev, after.st_ino, *catalog.signature(after))
            or actual_hash != row["content_hash"] or catalog.file_status(row, root) != "available"):
        raise catalog.Conflict("Selected duplicate file changed during byte verification.")
    return {
        "file_id": file_id,
        "root_id": row["root_id"],
        "relative_path": row["relative_path"],
        "content_hash": row["content_hash"],
        "identity": list(identity),
    }


def _decisions(db, file_id):
    return [dict(row) for row in db.execute("""
        SELECT snapshot_id,song_id,revision,file_id,content_hash,note,updated_at
        FROM catalog_match_decisions WHERE file_id=?
        ORDER BY snapshot_id,song_id
    """, (file_id,))]


def _current_decisions(db, expected):
    current = []
    for item in expected:
        row = db.execute("""
            SELECT snapshot_id,song_id,revision,file_id,content_hash,note,updated_at
            FROM catalog_match_decisions WHERE snapshot_id=? AND song_id=?
        """, (item["snapshot_id"], item["song_id"])).fetchone()
        if row is None:
            return []
        current.append(dict(row))
    return current


def preview(db, body, dataset):
    studio_service.exact_fields(body, {"dataset_id", "source_file_id", "keeper_file_id"})
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Reference repair belongs to a different dataset.")
    for field in ("source_file_id", "keeper_file_id"):
        catalog.text(body[field], field, required=True)
    if body["source_file_id"] == body["keeper_file_id"]:
        raise ValueError("Choose two distinct exact file IDs.")
    db.execute("BEGIN IMMEDIATE")
    source = _file_proof(db, body["source_file_id"])
    keeper = _file_proof(db, body["keeper_file_id"])
    if source["content_hash"] != keeper["content_hash"]:
        raise ValueError("Reference repair requires identical current whole-file SHA-256 hashes.")
    decisions = _decisions(db, source["file_id"])
    if not decisions:
        raise ValueError("The selected source file has no AutoCue catalog references to repair.")
    if len(decisions) > MAX_REFERENCES:
        raise ValueError(f"Reference repair is limited to {MAX_REFERENCES} reviewed catalog links.")
    if any(item["content_hash"] != source["content_hash"] for item in decisions):
        raise catalog.Conflict("A catalog link does not match the current duplicate hash; review it separately.")
    document = {
        "dataset_id": dataset,
        "source": source,
        "keeper": keeper,
        "before": decisions,
        "created_at": time.time(),
        "expires_at": time.time() + studio_service.PROPOSAL_SECONDS,
        "note": ("Repairs AutoCue catalog match links only. It does not move audio, "
                 "edit DJ databases/playlists, or repair external references."),
    }
    edit_id = catalog.digest(document)
    db.execute("INSERT INTO studio_file_repairs(id,document_json) VALUES(?,?)",
               (edit_id, catalog.canonical(document)))
    return {
        "edit_id": edit_id,
        "source_file_id": source["file_id"],
        "keeper_file_id": keeper["file_id"],
        "content_hash": source["content_hash"],
        "reference_count": len(decisions),
        "references": [{"snapshot_id": item["snapshot_id"], "song_id": item["song_id"],
                        "revision": item["revision"]} for item in decisions],
        "expires_at": document["expires_at"],
        "note": document["note"],
    }


def _apply(db, body, dataset, *, undo):
    studio_service.exact_fields(body, {
        "request_id", "dataset_id", "edit_id", "source_file_id", "keeper_file_id",
    })
    previous = studio_service._begin_request(db, body, dataset)
    operation = "undo" if undo else "approve"
    if previous:
        if previous.get("operation") != operation:
            raise catalog.Conflict("Reference-repair request ID belongs to another operation.")
        return previous
    row = db.execute("SELECT * FROM studio_file_repairs WHERE id=?", (body["edit_id"],)).fetchone()
    if row is None:
        raise ValueError("Reference-repair preview not found.")
    document = json.loads(row["document_json"])
    if catalog.digest(document) != body["edit_id"] or document["dataset_id"] != dataset:
        raise catalog.Conflict("Reference-repair evidence changed or belongs to another dataset.")
    if (body["source_file_id"] != document["source"]["file_id"]
            or body["keeper_file_id"] != document["keeper"]["file_id"]):
        raise catalog.Conflict("Acknowledged source/keeper IDs differ from the reviewed repair.")
    if undo:
        if not row["approval_json"] or row["undo_json"]:
            raise catalog.Conflict("Reference repair is not approved or was already undone.")
        approval = json.loads(row["approval_json"])
        expected = approval["after"]
    else:
        if row["approval_json"] or document["expires_at"] < time.time():
            raise catalog.Conflict("Reference-repair preview expired or was already approved.")
        approval = None
        expected = document["before"]
    if _file_proof(db, document["source"]["file_id"]) != document["source"] \
            or _file_proof(db, document["keeper"]["file_id"]) != document["keeper"]:
        raise catalog.Conflict("A duplicate file changed since preview; no catalog links were modified.")
    current = _current_decisions(db, expected)
    if current != expected:
        raise catalog.Conflict("Reviewed catalog links changed; no references were modified.")
    target_file_id = document["source"]["file_id"] if undo else document["keeper"]["file_id"]
    changed = []
    for item in expected:
        revision = item["revision"] + 1
        cursor = db.execute("""
            UPDATE catalog_match_decisions
            SET revision=?,file_id=?,updated_at=?
            WHERE snapshot_id=? AND song_id=? AND revision=? AND file_id=? AND content_hash=?
        """, (revision, target_file_id, time.time(), item["snapshot_id"], item["song_id"],
              item["revision"], item["file_id"], item["content_hash"]))
        if cursor.rowcount != 1:
            raise catalog.Conflict("A catalog link changed while applying the repair; no references were modified.")
        changed.append(dict(db.execute("""
            SELECT snapshot_id,song_id,revision,file_id,content_hash,note,updated_at
            FROM catalog_match_decisions WHERE snapshot_id=? AND song_id=?
        """, (item["snapshot_id"], item["song_id"])).fetchone()))
    receipt = studio_service._receipt(db, body, {
        "edit_id": body["edit_id"],
        "operation": operation,
        "source_file_id": document["source"]["file_id"],
        "keeper_file_id": document["keeper"]["file_id"],
        "references": changed,
        "note": "AutoCue catalog references restored." if undo else
                "Reviewed AutoCue catalog references now point to the exact-hash keeper. Audio and external crates were not changed.",
    })
    column = "undo_json" if undo else "approval_json"
    if undo:
        db.execute(f"UPDATE studio_file_repairs SET {column}=? WHERE id=?",
                   (catalog.canonical(receipt), body["edit_id"]))
    else:
        approval = {"receipt": receipt, "after": changed}
        db.execute("UPDATE studio_file_repairs SET approval_json=? WHERE id=?",
                   (catalog.canonical(approval), body["edit_id"]))
    return receipt


def approve(db, body, dataset):
    return _apply(db, body, dataset, undo=False)


def undo(db, body, dataset):
    return _apply(db, body, dataset, undo=True)


def history(db):
    return [{
        "edit_id": row["id"],
        "document": json.loads(row["document_json"]),
        "approval": json.loads(row["approval_json"]) if row["approval_json"] else None,
        "undo": json.loads(row["undo_json"]) if row["undo_json"] else None,
    } for row in db.execute("SELECT * FROM studio_file_repairs ORDER BY rowid DESC LIMIT 30")]
