"""Reviewed, reversible removal of explicit observations; original receipts stay durable."""
import json
import time

from . import catalog, studio_service

MAX_EVENTS = 500
NOTE = ("Logical play-history removal only. Original observations/receipts remain in the recovery journal; "
        "this is not permanent privacy erasure. Audio, saved crates and DJ preferences stay unchanged. "
        "Undo restores only these observations, not a prior library snapshot.")


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS studio_history_edits (
            id TEXT PRIMARY KEY, document_json TEXT NOT NULL,
            approval_json TEXT, undo_json TEXT
        );
    """)
    studio_service.initialize_search_cache(db)


def _event(db, event_id, dataset):
    row = db.execute("SELECT * FROM library_play_events WHERE request_id=?", (event_id,)).fetchone()
    if row is None:
        raise ValueError("Selected play observation no longer exists.")
    if row["dataset_id"] != dataset:
        raise catalog.Conflict("Selected play observation belongs to another dataset.")
    removed = db.execute("SELECT * FROM library_play_removals WHERE event_id=?", (event_id,)).fetchone()
    return {"event": dict(row), "removal": dict(removed) if removed else None}


def preview(db, body, dataset):
    studio_service.exact_fields(body, {"dataset_id", "event_ids"})
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("History selection belongs to another dataset.")
    ids = body["event_ids"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_EVENTS
            or any(not isinstance(key, str) or not key or len(key) > 256 for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Select 1..500 distinct exact play-observation IDs.")
    db.execute("BEGIN IMMEDIATE")
    before = [_event(db, key, dataset) for key in sorted(ids)]
    if any(item["removal"] and item["removal"]["active"] for item in before):
        raise catalog.Conflict("A selected observation was already removed; refresh history.")
    now = time.time()
    document = {"dataset_id": dataset, "before": before, "created_at": now,
                "expires_at": now + studio_service.PROPOSAL_SECONDS, "note": NOTE}
    edit_id = catalog.digest(document)
    db.execute("INSERT INTO studio_history_edits VALUES(?,?,NULL,NULL)", (edit_id, catalog.canonical(document)))
    return {"edit_id": edit_id, **document, "event_ids": [item["event"]["request_id"] for item in before]}


def _apply(db, body, dataset, *, undo):
    studio_service.exact_fields(body, {"request_id", "dataset_id", "edit_id", "event_ids"})
    prior = studio_service._begin_request(db, body, dataset)
    operation = "undo" if undo else "approve"
    if prior:
        if prior.get("operation") != operation:
            raise catalog.Conflict("History request ID belongs to another operation.")
        return prior
    row = db.execute("SELECT * FROM studio_history_edits WHERE id=?", (body["edit_id"],)).fetchone()
    if row is None:
        raise ValueError("History removal preview not found.")
    document = json.loads(row["document_json"])
    if document["dataset_id"] != dataset or catalog.digest(document) != body["edit_id"]:
        raise catalog.Conflict("History removal evidence changed or belongs to another dataset.")
    ids = [item["event"]["request_id"] for item in document["before"]]
    if body["event_ids"] != ids:
        raise catalog.Conflict("Reviewed observation IDs differ from the history journal.")
    if undo:
        if not row["approval_json"] or row["undo_json"]:
            raise catalog.Conflict("History edit is not approved or was already undone.")
    elif row["approval_json"] or document["expires_at"] < time.time():
        raise catalog.Conflict("History removal preview expired or was already approved; refresh.")
    for before in document["before"]:
        event_id = before["event"]["request_id"]
        current = _event(db, event_id, dataset)
        expected = {"event_id": event_id, "removal_id": body["edit_id"], "active": 1,
                    "revision": (before["removal"]["revision"] if before["removal"] else 0) + 1} if undo else before["removal"]
        if current["event"] != before["event"] or current["removal"] != expected:
            raise catalog.Conflict("An affected observation changed; no history was modified.")
    for before in document["before"]:
        event_id = before["event"]["request_id"]
        revision = (before["removal"]["revision"] if before["removal"] else 0) + (2 if undo else 1)
        db.execute("""INSERT INTO library_play_removals VALUES(?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET removal_id=excluded.removal_id,
            active=excluded.active,revision=excluded.revision""",
                   (event_id, body["edit_id"], int(not undo), revision))
    receipt = studio_service._receipt(db, body, {"edit_id": body["edit_id"], "operation": operation,
                                               "event_ids": ids, "note": NOTE})
    column = "undo_json" if undo else "approval_json"
    db.execute(f"UPDATE studio_history_edits SET {column}=? WHERE id=?",
               (catalog.canonical(receipt), body["edit_id"]))
    return receipt


def approve(db, body, dataset):
    return _apply(db, body, dataset, undo=False)


def undo(db, body, dataset):
    return _apply(db, body, dataset, undo=True)


def edits(db):
    return [{"edit_id": row["id"], "document": json.loads(row["document_json"]),
             "approval": json.loads(row["approval_json"]) if row["approval_json"] else None,
             "undo": json.loads(row["undo_json"]) if row["undo_json"] else None}
            for row in db.execute("SELECT * FROM studio_history_edits ORDER BY rowid DESC LIMIT 30")]
