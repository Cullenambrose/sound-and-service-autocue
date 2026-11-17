"""Explicit event-level DJ assessments, separate from global track preferences."""
import json
import time
import uuid

from . import catalog, studio_history, studio_service

ASSESSMENTS = {"worked", "mixed", "needs_work", "cannot_tell"}
MAX_EVENTS = 500
MAX_NOTE_LENGTH = 1000


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS studio_event_reviews (
            id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, crate_id TEXT NOT NULL,
            assessment TEXT NOT NULL, note TEXT NOT NULL, event_ids_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
    """)


def save(db, body, dataset):
    studio_service.exact_fields(
        body, {"request_id", "dataset_id", "crate_id", "assessment", "note", "event_ids"}
    )
    previous = studio_service._begin_request(db, body, dataset)
    if previous:
        return previous
    catalog.text(body["crate_id"], "crate_id", required=True)
    catalog.text(body["assessment"], "assessment", required=True)
    if body["assessment"] not in ASSESSMENTS:
        raise ValueError("assessment must be worked, mixed, needs_work or cannot_tell.")
    catalog.text(body["note"], "note")
    if len(body["note"]) > MAX_NOTE_LENGTH:
        raise ValueError(f"note must not exceed {MAX_NOTE_LENGTH} characters.")
    ids = body["event_ids"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_EVENTS
            or any(not isinstance(key, str) or not key or len(key) > 256 for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Select 1..500 distinct exact play-observation IDs.")
    evidence = studio_service.crate_evidence(db, {"crate_id": body["crate_id"]}, dataset)
    planning = evidence["creation"].get("planning")
    if not isinstance(planning, dict) or not isinstance(planning.get("alternative"), dict):
        raise ValueError("The selected crate has no saved planned set to review.")
    for event_id in ids:
        observation = studio_history._event(db, event_id, dataset)
        if observation["removal"] and observation["removal"]["active"]:
            raise catalog.Conflict("A selected play observation was removed; refresh history.")
    review_id = str(uuid.uuid4())
    created_at = time.time()
    db.execute(
        "INSERT INTO studio_event_reviews VALUES(?,?,?,?,?,?,?)",
        (review_id, dataset, body["crate_id"], body["assessment"], body["note"],
         catalog.canonical(ids), created_at),
    )
    return studio_service._receipt(db, body, {
        "review_id": review_id, "crate_id": body["crate_id"],
        "assessment": body["assessment"], "event_ids": ids, "created_at": created_at,
    })


def history(db, dataset):
    return [{
        "review_id": row["id"], "crate_id": row["crate_id"],
        "assessment": row["assessment"], "note": row["note"],
        "event_ids": json.loads(row["event_ids_json"]), "created_at": row["created_at"],
    } for row in db.execute(
        "SELECT * FROM studio_event_reviews WHERE dataset_id=? ORDER BY created_at DESC,id DESC LIMIT 100",
        (dataset,),
    )]
