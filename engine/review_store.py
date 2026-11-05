"""Transactional cue-review journal. The caller owns the plan/event transaction."""
import json
import hashlib
import uuid


class ReviewConflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def dataset_id(db):
    return db.execute("SELECT value FROM settings WHERE key='dataset_id'").fetchone()[0]


def analysis_id(file_hash, plan):
    proposal = {key: value for key, value in plan.items() if key not in ("answer_key", "analysis_id")}
    return hashlib.sha256(canonical({"track_hash": file_hash, "proposal": proposal}).encode()).hexdigest()


def initialize(db, legacy_path):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS review_receipts (
            request_id TEXT PRIMARY KEY,
            request_json TEXT NOT NULL,
            receipt_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cue_review_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            request_id TEXT,
            event_json TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(tracks)")}
    if "review_revision" not in columns:
        db.execute("ALTER TABLE tracks ADD COLUMN review_revision INTEGER NOT NULL DEFAULT 0")
    db.executescript("""
        CREATE TRIGGER IF NOT EXISTS advance_review_revision
        AFTER UPDATE OF plan_json ON tracks
        WHEN NEW.plan_json IS NOT OLD.plan_json
        BEGIN
            UPDATE tracks SET review_revision=OLD.review_revision+1 WHERE hash=NEW.hash;
        END;
    """)
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('dataset_id',?)",
               (str(uuid.uuid4()),))
    if not db.execute("SELECT 1 FROM settings WHERE key='corrections_migrated'").fetchone():
        if legacy_path.is_file():
            from marks import ordered_events
            with legacy_path.open(encoding="utf-8") as source:
                legacy = json.load(source)
            if not isinstance(legacy, list) or any(not isinstance(item, dict) for item in legacy):
                raise ValueError("Legacy corrections must be an array of event objects.")
            append_events(db, ordered_events(legacy), None)
        db.execute("INSERT INTO settings(key,value) VALUES('corrections_migrated','1')")


def append_events(db, events, request_id):
    sequence = 0
    for event in events:
        event = {**event, "dataset_id": dataset_id(db)}
        event_id = str(uuid.uuid4())
        event["event_id"] = event_id
        cursor = db.execute(
            "INSERT INTO cue_review_events(event_id,request_id,event_json) VALUES(?,?,?)",
            (event_id, request_id, canonical(event)),
        )
        sequence = cursor.lastrowid
    return sequence


def export_events(db):
    return [{**json.loads(row["event_json"]), "sequence": row["sequence"]}
            for row in db.execute("SELECT sequence,event_json FROM cue_review_events ORDER BY sequence")]


def replay(db, request_id, request):
    row = db.execute("SELECT request_json,receipt_json FROM review_receipts WHERE request_id=?",
                     (request_id,)).fetchone()
    if row:
        if row["request_json"] != canonical(request):
            raise ReviewConflict("Request ID was already used for a different review.")
        return json.loads(row["receipt_json"])
    return None


def acknowledge(db, request_id, request, file_hash, events):
    sequence = append_events(db, events, request_id)
    revision = db.execute("SELECT review_revision FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0]
    receipt = {"ok": True, "request_id": request_id, "dataset_id": dataset_id(db),
               "track_hash": file_hash, "revision": revision, "sequence": sequence}
    db.execute("INSERT INTO review_receipts VALUES(?,?,?)",
               (request_id, canonical(request), canonical(receipt)))
    return receipt
