"""Opt-in polling of indexed roots and a durable app-owned acquisition inbox."""
import json
import logging
import sqlite3
import time
import uuid

from . import catalog, scan_jobs, studio_service

LOG = logging.getLogger(__name__)


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS studio_inbox (
            file_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            status TEXT NOT NULL, revision INTEGER NOT NULL,
            discovered_at REAL NOT NULL, changed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS studio_monitors (
            root_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, interval_s INTEGER NOT NULL,
            revision INTEGER NOT NULL, next_scan REAL NOT NULL,
            last_job TEXT, error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS studio_exclusions (
            file_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, reason TEXT NOT NULL,
            excluded INTEGER NOT NULL, revision INTEGER NOT NULL
        );
    """)
    studio_service.initialize_search_cache(db)


def reconcile(db):
    now = time.time()
    for row in db.execute("SELECT id,content_hash,metadata_json,error,available FROM library_files").fetchall():
        fingerprint = catalog.digest(dict(row))
        old = db.execute("SELECT fingerprint FROM studio_inbox WHERE file_id=?", (row["id"],)).fetchone()
        if not old:
            db.execute("INSERT INTO studio_inbox VALUES(?,?,'new',1,?,?)",
                       (row["id"], fingerprint, now, now))
        elif old["fingerprint"] != fingerprint:
            db.execute("""UPDATE studio_inbox SET fingerprint=?,status='new',revision=revision+1,changed_at=?
                          WHERE file_id=?""", (fingerprint, now, row["id"]))


def state(db, *, limit=100):
    rows = db.execute("""
        SELECT i.*,f.content_hash,f.metadata_json,f.available,f.error,f.root_id,f.relative_path
        FROM studio_inbox i JOIN library_files f ON f.id=i.file_id
        ORDER BY CASE i.status WHEN 'new' THEN 0 ELSE 1 END,i.changed_at DESC,i.file_id LIMIT ?
    """, (limit,)).fetchall()
    groups = {row["content_hash"]: row["n"] for row in db.execute("""
        SELECT content_hash,COUNT(*) n FROM library_files
        WHERE available=1 AND content_hash IS NOT NULL AND error='' GROUP BY content_hash HAVING COUNT(*)>1
    """)}
    items = []
    for row in rows:
        item = dict(row)
        metadata = json.loads(item.pop("metadata_json"))
        item["metadata"] = metadata
        item["missing_metadata"] = [field for field in ("title", "artist", "bpm", "key", "duration_s")
                                    if metadata.get(field) in (None, "")]
        item["exact_hash_group_count"] = groups.get(row["content_hash"], 1)
        items.append(item)
    return {"items": items, "limit": limit,
            "counts": {row["status"]: row["n"] for row in db.execute(
                "SELECT status,COUNT(*) n FROM studio_inbox GROUP BY status")},
            "monitors": [dict(row) for row in db.execute("""
                SELECT m.*,r.path FROM studio_monitors m JOIN library_roots r ON r.id=m.root_id ORDER BY m.root_id
            """)],
            "roots": [dict(row) for row in db.execute("SELECT id,path,scan_revision FROM library_roots ORDER BY path")],
            "exclusions": [dict(row) for row in db.execute("""SELECT e.*,f.content_hash AS current_content_hash
                FROM studio_exclusions e JOIN library_files f ON f.id=e.file_id ORDER BY file_id""")],
            "note": "Published index evidence only. Exact hashes do not prove an edit is disposable. "
                    "Inbox review does not certify music/cleanliness or fulfill an event request."}


def review(db, body, dataset):
    studio_service.exact_fields(body, {"request_id", "dataset_id", "file_id", "base_revision", "status"})
    if body["status"] not in ("new", "reviewed", "ignored"):
        raise ValueError("Inbox status must be new, reviewed or ignored.")
    if type(body["base_revision"]) is not int or body["base_revision"] < 1:
        raise ValueError("Inbox revision must be positive.")
    prior = studio_service._begin_request(db, body, dataset)
    if prior:
        return prior
    row = db.execute("SELECT revision FROM studio_inbox WHERE file_id=?", (body["file_id"],)).fetchone()
    if not row or row["revision"] != body["base_revision"]:
        raise catalog.Conflict("Inbox evidence changed; refresh before reviewing.")
    revision = row["revision"] + 1
    db.execute("UPDATE studio_inbox SET status=?,revision=? WHERE file_id=?",
               (body["status"], revision, body["file_id"]))
    return studio_service._receipt(db, body, {"file_id": body["file_id"], "status": body["status"], "revision": revision})


def monitor(db, body, dataset):
    studio_service.exact_fields(body, {"request_id", "dataset_id", "root_id", "base_revision", "enabled", "interval_s"})
    if type(body["enabled"]) is not bool or type(body["interval_s"]) is not int or not 60 <= body["interval_s"] <= 86400:
        raise ValueError("Monitor needs explicit enabled boolean and interval_s in 60..86400.")
    if type(body["base_revision"]) is not int or body["base_revision"] < 0:
        raise ValueError("Monitor base_revision must be nonnegative.")
    prior = studio_service._begin_request(db, body, dataset)
    if prior:
        return prior
    if not db.execute("SELECT 1 FROM library_roots WHERE id=?", (body["root_id"],)).fetchone():
        raise ValueError("Only an explicitly indexed root can be monitored.")
    row = db.execute("SELECT revision FROM studio_monitors WHERE root_id=?", (body["root_id"],)).fetchone()
    revision = row["revision"] if row else 0
    if revision != body["base_revision"]:
        raise catalog.Conflict("Monitor changed; refresh before changing it.")
    db.execute("""INSERT INTO studio_monitors(root_id,enabled,interval_s,revision,next_scan)
                  VALUES(?,?,?,?,?) ON CONFLICT(root_id) DO UPDATE SET
                  enabled=excluded.enabled,interval_s=excluded.interval_s,revision=excluded.revision,
                  next_scan=excluded.next_scan,error=''""",
               (body["root_id"], int(body["enabled"]), body["interval_s"], revision + 1, time.time()))
    return studio_service._receipt(db, body, {"root_id": body["root_id"], "enabled": body["enabled"],
                                             "revision": revision + 1})


def exclude(db, body, dataset):
    studio_service.exact_fields(body, {"request_id", "dataset_id", "file_id", "content_hash",
                                      "base_revision", "excluded", "reason"})
    if type(body["excluded"]) is not bool or type(body["base_revision"]) is not int or body["base_revision"] < 0:
        raise ValueError("Exclusion needs a boolean and nonnegative base_revision.")
    catalog.text(body["reason"], "reason", required=True)
    prior = studio_service._begin_request(db, body, dataset)
    if prior:
        return prior
    file = db.execute("SELECT content_hash FROM library_files WHERE id=?", (body["file_id"],)).fetchone()
    if not file or not file["content_hash"] or file["content_hash"] != body["content_hash"]:
        raise catalog.Conflict("Exact-file exclusion evidence changed; refresh.")
    row = db.execute("SELECT revision FROM studio_exclusions WHERE file_id=?", (body["file_id"],)).fetchone()
    revision = row["revision"] if row else 0
    if revision != body["base_revision"]:
        raise catalog.Conflict("Exclusion changed; refresh.")
    db.execute("""INSERT INTO studio_exclusions VALUES(?,?,?,?,?)
                  ON CONFLICT(file_id) DO UPDATE SET content_hash=excluded.content_hash,
                  reason=excluded.reason,excluded=excluded.excluded,revision=excluded.revision""",
               (body["file_id"], body["content_hash"], body["reason"], int(body["excluded"]), revision + 1))
    return studio_service._receipt(db, body, {"file_id": body["file_id"], "excluded": body["excluded"],
                                             "revision": revision + 1})


def tick(connect, dataset, now=None):
    now = time.time() if now is None else now
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        reconcile(db)
        due = [dict(row) for row in db.execute("""SELECT m.*,r.path FROM studio_monitors m
            JOIN library_roots r ON r.id=m.root_id WHERE m.enabled=1 AND m.next_scan<=?""", (now,))]
    for item in due:
        try:
            with connect() as db:
                # Another pending scan is not an error: wait for it, never queue duplicates.
                prior = db.execute("SELECT status,error FROM catalog_scan_jobs WHERE id=?", (item["last_job"],)).fetchone()
                if prior and prior["status"] in ("queued", "running", "cancelling"):
                    continue
                receipt = scan_jobs.submit(db, {"dataset_id": dataset, "request_id": "monitor:" + uuid.uuid4().hex,
                                                "path": item["path"]}, dataset)
                current = db.execute("SELECT enabled,revision FROM studio_monitors WHERE root_id=?",
                                     (item["root_id"],)).fetchone()
                if not current or not current["enabled"] or current["revision"] != item["revision"]:
                    db.rollback()
                    continue
                db.execute("UPDATE studio_monitors SET last_job=?,next_scan=?,error=? WHERE root_id=? AND enabled=1",
                           (receipt["job_id"], now + item["interval_s"], prior["error"] if prior else "", item["root_id"]))
        except (ValueError, OSError) as error:
            LOG.warning("Selected-folder monitor admission failed: %s", error)
            with connect() as db:
                db.execute("UPDATE studio_monitors SET error=?,next_scan=? WHERE root_id=?",
                           (str(error), now + item["interval_s"], item["root_id"]))


def worker(connect, dataset, stop):
    while not stop.is_set():
        try:
            tick(connect, dataset)
        except (OSError, ValueError, sqlite3.Error) as error:
            LOG.exception("Inbox/monitor reconciliation failed: %s", error)
        stop.wait(5)
