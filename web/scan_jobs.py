"""Durable read-only indexing with atomic publication and recoverable lifecycle."""
import json
import logging
from pathlib import Path
import sqlite3
import time
import uuid

if __package__:
    from . import catalog
else:
    import catalog

LOG = logging.getLogger(__name__)


class ScanCancelled(Exception):
    pass


class ScanInterrupted(Exception):
    pass


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS catalog_scan_jobs (
            id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
            body_json TEXT NOT NULL, receipt_json TEXT NOT NULL,
            path TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
            status TEXT NOT NULL, progress_json TEXT NOT NULL,
            result_json TEXT, error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
    """)


def submit(db, body, dataset):
    ordinary = {"request_id", "dataset_id", "path"}
    if not isinstance(body, dict) or set(body) not in (ordinary, ordinary | {"root_id", "base_revision"}):
        raise ValueError("Scan requires request_id, dataset_id and path; reassociation also requires root_id and base_revision.")
    reassociation = "root_id" in body
    if reassociation:
        catalog.text(body["root_id"], "root_id", required=True)
        if type(body["base_revision"]) is not int or body["base_revision"] < 0:
            raise ValueError("base_revision must be a nonnegative integer.")
    for key in ("request_id", "dataset_id", "path"):
        catalog.text(body[key], key, required=True)
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Scan belongs to a different dataset; reload the app.")
    encoded = catalog.canonical(body)

    def replay():
        prior = db.execute("SELECT * FROM catalog_scan_jobs WHERE request_id=?", (body["request_id"],)).fetchone()
        if prior:
            if prior["body_json"] != encoded:
                raise catalog.Conflict("Scan request ID was already used with different content.")
            return json.loads(prior["receipt_json"])
        return None

    prior = replay()
    if prior:
        return prior
    requested = Path(body["path"]).expanduser()
    if not requested.is_absolute():
        raise ValueError("Select an absolute folder path.")
    path = requested.resolve()
    stat = path.stat()
    if not path.is_dir():
        raise ValueError("Library index requires a folder, not a file.")
    if reassociation:
        original = db.execute("SELECT * FROM library_roots WHERE id=?", (body["root_id"],)).fetchone()
        if not original or original["scan_revision"] != body["base_revision"]:
            raise catalog.Conflict("Indexed folder revision changed; refresh before reassociating.")
        if catalog.root_status(original)[0] != "offline":
            raise catalog.Conflict("Reassociation is only for offline indexed folders.")
    db.execute("BEGIN IMMEDIATE")
    prior = replay()
    if prior:
        return prior
    for root in db.execute("SELECT * FROM library_roots"):
        if reassociation and root["id"] == body["root_id"]:
            if root["scan_revision"] != body["base_revision"]:
                raise catalog.Conflict("Indexed folder revision changed during admission.")
            continue
        existing = Path(root["path"])
        if path == existing:
            if reassociation:
                raise catalog.Conflict("Replacement folder is already assigned to another indexed root.")
            if (stat.st_dev, stat.st_ino) != (root["device"], root["inode"]):
                raise catalog.Conflict("Folder identity changed; select the original folder/volume.")
        elif path.is_relative_to(existing) or existing.is_relative_to(path):
            raise catalog.Conflict("Selected folder overlaps an indexed root.")
    for job in db.execute("SELECT path,body_json FROM catalog_scan_jobs WHERE status IN ('queued','running','cancelling')"):
        existing = Path(job["path"])
        prior_body = json.loads(job["body_json"])
        if path.is_relative_to(existing) or existing.is_relative_to(path) or (
                reassociation and prior_body.get("root_id") == body["root_id"]):
            raise catalog.Conflict("An overlapping folder scan is already pending; inspect its progress.")
    job_id = uuid.uuid4().hex
    receipt = {"ok": True, **body, "job_id": job_id}
    now = time.time()
    db.execute("""
        INSERT INTO catalog_scan_jobs
        (id,request_id,body_json,receipt_json,path,device,inode,status,progress_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,'queued',?,?,?)
    """, (job_id, body["request_id"], encoded, catalog.canonical(receipt), str(path),
          stat.st_dev, stat.st_ino, catalog.canonical({"phase": "queued", "scanned": 0, "reused": 0,
          "errors": 0, "skipped": 0, "current_path": ""}), now, now))
    return receipt


def record(row):
    body = json.loads(row["body_json"])
    return {"id": row["id"], "path": row["path"], "status": row["status"],
            "progress": json.loads(row["progress_json"]), "error": row["error"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            **({"root_id": body["root_id"], "base_revision": body["base_revision"],
                "operation": "reassociate"} if "root_id" in body else {"operation": "scan"})}


def jobs(db):
    rows = db.execute("""
        SELECT * FROM catalog_scan_jobs WHERE status IN ('queued','running','cancelling')
        UNION ALL SELECT * FROM (
            SELECT * FROM catalog_scan_jobs WHERE status NOT IN ('queued','running','cancelling')
            ORDER BY created_at DESC LIMIT 20
        ) ORDER BY created_at DESC,id
    """).fetchall()
    return [record(row) for row in rows]


def cancel(db, body, dataset):
    if not isinstance(body, dict) or set(body) != {"dataset_id", "job_id"}:
        raise ValueError("Scan cancellation requires dataset_id and job_id.")
    catalog.text(body["job_id"], "job_id", required=True)
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Scan belongs to a different dataset; reload the app.")
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM catalog_scan_jobs WHERE id=?", (body["job_id"],)).fetchone()
    if not row:
        raise ValueError("Scan job not found.")
    if row["status"] in ("queued", "running"):
        status = "cancelled" if row["status"] == "queued" else "cancelling"
        db.execute("UPDATE catalog_scan_jobs SET status=?,updated_at=? WHERE id=?",
                   (status, time.time(), row["id"]))
    return {"ok": True, "job": record(db.execute(
        "SELECT * FROM catalog_scan_jobs WHERE id=?", (row["id"],)).fetchone())}


def recover(db):
    # Only called at process startup, never while a worker owns an active scan.
    db.execute("""UPDATE catalog_scan_jobs SET status=CASE WHEN status='cancelling'
                  THEN 'cancelled' ELSE 'interrupted' END,
                  error='Server stopped before scan publication; prior index preserved.',updated_at=?
                  WHERE status IN ('running','cancelling')""", (time.time(),))


def run_next(connect, stopping=lambda: False):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # A single active scan protects disk throughput, even with multiple callers.
        if db.execute("SELECT 1 FROM catalog_scan_jobs WHERE status IN ('running','cancelling')").fetchone():
            return False
        job = db.execute("SELECT * FROM catalog_scan_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not job:
            return False
        db.execute("UPDATE catalog_scan_jobs SET status='running',updated_at=? WHERE id=?",
                   (time.time(), job["id"]))
    last_progress, last_phase = 0, ""

    def checkpoint():
        with connect() as state:
            status = state.execute("SELECT status FROM catalog_scan_jobs WHERE id=?", (job["id"],)).fetchone()[0]
        if status != "running":
            raise ScanCancelled("Scan cancelled; prior index preserved.")
        if stopping():
            raise ScanInterrupted("Server stopping; prior index preserved.")

    def progress(value):
        nonlocal last_progress, last_phase
        now = time.monotonic()
        if value["phase"] != last_phase or now - last_progress >= 0.25:
            with connect() as state:
                state.execute("UPDATE catalog_scan_jobs SET progress_json=?,updated_at=? WHERE id=?",
                              (catalog.canonical(value), time.time(), job["id"]))
            last_progress, last_phase = now, value["phase"]

    try:
        with connect() as db:
            body = json.loads(job["body_json"])
            reassociation = ({"root_id": body["root_id"], "base_revision": body["base_revision"],
                              "job_id": job["id"]} if "root_id" in body else None)
            result = catalog.scan_folder(db, job["path"], checkpoint=checkpoint, progress=progress,
                                         expected_identity=(job["device"], job["inode"]), reassociation=reassociation)
            if result["state"] != "online":
                raise OSError(result["error"])
            # scan_folder holds BEGIN IMMEDIATE here: cancellation cannot interleave
            # between the index commit and this terminal job record.
            db.execute("UPDATE catalog_scan_jobs SET status='completed',result_json=?,updated_at=? WHERE id=?",
                       (catalog.canonical(result), time.time(), job["id"]))
    except (ScanCancelled, ScanInterrupted, OSError, ValueError, TypeError,
            RuntimeError, sqlite3.Error, catalog.MutagenError) as error:
        if isinstance(error, ScanCancelled):
            status = "cancelled"
        elif isinstance(error, ScanInterrupted):
            status = "interrupted"
        else:
            status = "failed"
            LOG.exception("Catalog scan %s failed", job["id"])
        with connect() as db:
            db.execute("UPDATE catalog_scan_jobs SET status=?,error=?,updated_at=? WHERE id=?",
                       (status, str(error) or type(error).__name__, time.time(), job["id"]))
    return True


def worker(connect, stop):
    while not stop.is_set():
        try:
            worked = run_next(connect, stop.is_set)
        except sqlite3.Error:
            LOG.exception("Catalog scan worker storage failure")
            worked = False
        if not worked:
            stop.wait(0.5)
