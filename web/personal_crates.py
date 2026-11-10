"""Revisioned ordered personal crates, independent of immutable source catalogs."""
import json
import time

if __package__:
    from . import catalog
else:
    import catalog

MAX_ENTRIES = 1000


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS personal_crates (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS personal_crate_entries (
            crate_id TEXT NOT NULL REFERENCES personal_crates(id),
            id TEXT NOT NULL, position INTEGER NOT NULL,
            snapshot_id TEXT NOT NULL, song_id TEXT NOT NULL,
            PRIMARY KEY(crate_id,id), UNIQUE(crate_id,position),
            FOREIGN KEY(snapshot_id,song_id) REFERENCES catalog_songs(snapshot_id,source_id)
        );
        CREATE TABLE IF NOT EXISTS personal_crate_receipts (
            request_id TEXT PRIMARY KEY, body_json TEXT NOT NULL, receipt_json TEXT NOT NULL
        );
    """)


def listing(db):
    return [dict(row) for row in db.execute("""
        SELECT c.*,COUNT(e.id) AS entries FROM personal_crates c
        LEFT JOIN personal_crate_entries e ON e.crate_id=c.id
        GROUP BY c.id ORDER BY c.created_at,c.id
    """)]


def detail(db, crate_id):
    if not db.in_transaction:
        db.execute("BEGIN")
    row = db.execute("SELECT * FROM personal_crates WHERE id=?", (crate_id,)).fetchone()
    if not row:
        raise ValueError("Personal crate not found; refresh the crate list.")
    entries = []
    for entry in db.execute("""
        SELECT e.*,s.metadata_json FROM personal_crate_entries e JOIN catalog_songs s
        ON s.snapshot_id=e.snapshot_id AND s.source_id=e.song_id
        WHERE e.crate_id=? ORDER BY e.position
    """, (crate_id,)):
        decision = db.execute("SELECT * FROM catalog_match_decisions WHERE snapshot_id=? AND song_id=?",
                              (entry["snapshot_id"], entry["song_id"])).fetchone()
        state = "unreviewed"
        selected = None
        if decision:
            if decision["file_id"]:
                file = db.execute("SELECT * FROM library_files WHERE id=?", (decision["file_id"],)).fetchone()
                if file:
                    root = db.execute("SELECT * FROM library_roots WHERE id=?", (file["root_id"],)).fetchone()
                    selected = catalog.file_record(file, root)
            state = catalog.reviewed_match_state(decision, selected)
        entries.append({"id": entry["id"], "snapshot_id": entry["snapshot_id"], "song_id": entry["song_id"],
                        "song": json.loads(entry["metadata_json"]), "match_state": state,
                        "decision_revision": decision["revision"] if decision else 0,
                        "selected_file": selected})
    return {**dict(row), "entries": entries}


def save(db, body, dataset):
    fields = {"request_id", "dataset_id", "crate_id", "base_revision", "name", "entries"}
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError("Crate save requires request_id, dataset_id, crate_id, base_revision, name and ordered entries.")
    for key in ("request_id", "dataset_id", "crate_id", "name"):
        catalog.text(body[key], key, required=True)
    if type(body["base_revision"]) is not int or body["base_revision"] < 0:
        raise ValueError("base_revision must be a nonnegative integer.")
    if not isinstance(body["entries"], list) or len(body["entries"]) > MAX_ENTRIES:
        raise ValueError(f"Personal crates support at most {MAX_ENTRIES} entries.")
    seen = set()
    for entry in body["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"id", "snapshot_id", "song_id"}:
            raise ValueError("Each entry requires id, snapshot_id and song_id.")
        for key in entry:
            catalog.text(entry[key], key, required=True)
        if entry["id"] in seen:
            raise ValueError("Duplicate membership ID; repeated songs need separate entry IDs.")
        seen.add(entry["id"])
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Personal crate belongs to a different dataset; reload the app.")
    encoded = catalog.canonical(body)
    db.execute("BEGIN IMMEDIATE")
    receipt = db.execute("SELECT * FROM personal_crate_receipts WHERE request_id=?",
                         (body["request_id"],)).fetchone()
    if receipt:
        if receipt["body_json"] != encoded:
            raise catalog.Conflict("Crate request ID was already used with different content.")
        return json.loads(receipt["receipt_json"])
    current = db.execute("SELECT revision FROM personal_crates WHERE id=?", (body["crate_id"],)).fetchone()
    revision = current["revision"] if current else 0
    if revision != body["base_revision"]:
        raise catalog.Conflict("Personal crate changed in another tab; download pending changes and refresh before reconciling.")
    for entry in body["entries"]:
        if not db.execute("SELECT 1 FROM catalog_songs WHERE snapshot_id=? AND source_id=?",
                          (entry["snapshot_id"], entry["song_id"])).fetchone():
            raise ValueError("Crate entry references an unknown catalog song/version.")
        old = db.execute("SELECT snapshot_id,song_id FROM personal_crate_entries WHERE crate_id=? AND id=?",
                         (body["crate_id"], entry["id"])).fetchone()
        if old and (old["snapshot_id"], old["song_id"]) != (entry["snapshot_id"], entry["song_id"]):
            raise catalog.Conflict("An existing membership ID cannot be retargeted to a different song.")
    now = time.time()
    db.execute("""
        INSERT INTO personal_crates VALUES(?,?,1,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,revision=personal_crates.revision+1,updated_at=excluded.updated_at
    """, (body["crate_id"], body["name"], now, now))
    db.execute("DELETE FROM personal_crate_entries WHERE crate_id=?", (body["crate_id"],))
    db.executemany("INSERT INTO personal_crate_entries VALUES(?,?,?,?,?)",
                   [(body["crate_id"], entry["id"], index, entry["snapshot_id"], entry["song_id"])
                    for index, entry in enumerate(body["entries"])])
    receipt = {"ok": True, **body, "revision": revision + 1}
    db.execute("INSERT INTO personal_crate_receipts VALUES(?,?,?)",
               (body["request_id"], encoded, catalog.canonical(receipt)))
    return receipt
