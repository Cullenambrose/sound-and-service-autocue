"""Atomic reviewed bulk organization over the existing exact-file crate service."""
import json
import time
import uuid

from . import catalog, personal_crates, studio_service

MAX_BATCH = 8
MAX_BATCH_ENTRIES = 500


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS studio_batches (
            id TEXT PRIMARY KEY, document_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS studio_edit_batches (
            id TEXT PRIMARY KEY, document_json TEXT NOT NULL,
            approval_json TEXT, undo_json TEXT
        );
    """)


def preview(db, body, dataset):
    studio_service.exact_fields(body, {"dataset_id", "crates"})
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Batch belongs to a different dataset.")
    crates = body["crates"]
    if not isinstance(crates, list) or not 1 <= len(crates) <= MAX_BATCH:
        raise ValueError(f"A batch needs 1..{MAX_BATCH} named crate rules.")
    names = set()
    for crate in crates:
        studio_service.exact_fields(crate, {"name", "rule"})
        catalog.text(crate["name"], "crate name", required=True)
        if crate["name"].casefold().strip() in names:
            raise ValueError("Batch crate names must be distinct.")
        names.add(crate["name"].casefold().strip())
        studio_service.validate_rule(crate["rule"])
    db.execute("BEGIN IMMEDIATE")
    proposals = [studio_service.propose(db, {"dataset_id": dataset, **crate}, dataset,
                                       release_snapshot=False) for crate in crates]
    if sum(len(proposal["tracks"]) for proposal in proposals) > MAX_BATCH_ENTRIES:
        raise ValueError(f"Batch exceeds {MAX_BATCH_ENTRIES} selected entries; use smaller rule limits.")
    document = {
        "dataset_id": dataset, "proposals": proposals, "created_at": time.time(),
        "note": "New static crates only; approval commits all or none. Shared tracks may occur in multiple crates. "
                "Existing rules/crates and original audio are not modified.",
    }
    batch_id = catalog.digest(document)
    db.execute("INSERT INTO studio_batches VALUES(?,?)", (batch_id, catalog.canonical(document)))
    return {"batch_id": batch_id, **document}


def approve(db, body, dataset):
    studio_service.exact_fields(body, {"request_id", "dataset_id", "batch_id"})
    previous = studio_service._begin_request(db, body, dataset)
    if previous:
        return previous
    row = db.execute("SELECT document_json FROM studio_batches WHERE id=?", (body["batch_id"],)).fetchone()
    if not row:
        raise ValueError("Batch preview not found.")
    document = json.loads(row["document_json"])
    if catalog.digest(document) != body["batch_id"] or document["dataset_id"] != dataset:
        raise catalog.Conflict("Batch evidence changed or belongs to a different dataset.")
    receipts = []
    for proposal in document["proposals"]:
        receipt = studio_service.approve(db, {
            "request_id": "batch:" + body["batch_id"] + ":" + proposal["proposal_id"],
            "dataset_id": dataset, "proposal_id": proposal["proposal_id"],
        }, dataset, begin=False)
        receipts.append(receipt)
    return studio_service._receipt(db, body, {
        "batch_id": body["batch_id"], "crates": receipts,
        "entries": sum(receipt["entries"] for receipt in receipts),
    })


def history(db):
    receipts = db.execute(
        "SELECT receipt_json FROM studio_receipts ORDER BY rowid DESC LIMIT 100").fetchall()
    return [value for row in receipts if "batch_id" in (value := json.loads(row["receipt_json"]))]


def _snapshot(db, crate_id):
    catalog.text(crate_id, "crate_id", required=True)
    crate = db.execute("SELECT id,name,revision FROM personal_crates WHERE id=?", (crate_id,)).fetchone()
    if not crate:
        raise ValueError("Existing personal crate not found; refresh.")
    return {**dict(crate), "entries": [dict(row) for row in db.execute(
        "SELECT id,snapshot_id,song_id FROM personal_crate_entries WHERE crate_id=? ORDER BY position", (crate_id,))]}


def edit_preview(db, body, dataset):
    studio_service.exact_fields(body, {"dataset_id", "operations"})
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Edit belongs to a different dataset.")
    operations = body["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_BATCH:
        raise ValueError("Use 1..8 explicit existing-crate operations.")
    db.execute("BEGIN IMMEDIATE")
    before, after = {}, {}
    seed = uuid.uuid4().hex
    def load(crate_id, revision):
        catalog.text(crate_id, "crate_id", required=True)
        if type(revision) is not int or revision < 1:
            raise ValueError("Existing crate revision must be positive.")
        if crate_id not in before:
            before[crate_id] = _snapshot(db, crate_id)
            after[crate_id] = json.loads(catalog.canonical(before[crate_id]))
        if before[crate_id]["revision"] != revision:
            raise catalog.Conflict("Crate revision changed; refresh before previewing.")
        return after[crate_id]
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError("Each edit must be an operation object.")
        kind = operation.get("action")
        if kind == "rename":
            studio_service.exact_fields(operation, {"action", "crate_id", "base_revision", "name"})
            catalog.text(operation["name"], "name", required=True)
            load(operation["crate_id"], operation["base_revision"])["name"] = operation["name"]
        elif kind in ("reorder", "remove"):
            studio_service.exact_fields(operation, {"action", "crate_id", "base_revision", "entry_ids"})
            crate = load(operation["crate_id"], operation["base_revision"])
            entries = {entry["id"]: entry for entry in crate["entries"]}
            selected = _entry_ids(operation["entry_ids"], entries)
            if kind == "reorder":
                if set(selected) != set(entries):
                    raise ValueError("Reorder must include every membership ID exactly once.")
                crate["entries"] = [entries[key] for key in selected]
            else:
                crate["entries"] = [entry for entry in crate["entries"] if entry["id"] not in selected]
        elif kind in ("merge", "move"):
            fields = {"action", "source_id", "source_revision", "target_id", "target_revision"}
            studio_service.exact_fields(operation, fields | ({"mode"} if kind == "merge" else {"entry_ids"}))
            if operation["source_id"] == operation["target_id"]:
                raise ValueError("Source and target crates must differ.")
            source = load(operation["source_id"], operation["source_revision"])
            target = load(operation["target_id"], operation["target_revision"])
            if kind == "merge":
                if operation["mode"] not in ("copy", "move"):
                    raise ValueError("Merge mode must be copy or move.")
                selected = [entry["id"] for entry in source["entries"]]
            else:
                selected = _entry_ids(operation["entry_ids"], {entry["id"]: entry for entry in source["entries"]})
            for entry in source["entries"]:
                if entry["id"] in selected:
                    target["entries"].append({**entry, "id": catalog.digest(
                        [seed, index, operation["source_id"], operation["target_id"], entry["id"]])})
            if kind == "move" or operation["mode"] == "move":
                source["entries"] = [entry for entry in source["entries"] if entry["id"] not in selected]
        else:
            raise ValueError("Edit action must be rename, merge, move, remove or reorder.")
    if len(before) > MAX_BATCH or sum(len(crate["entries"]) for crate in after.values()) > MAX_BATCH_ENTRIES:
        raise ValueError("Edit supports at most 8 affected crates and 500 resulting memberships.")
    if before == after:
        raise ValueError("No crate changes to preview.")
    document = {"dataset_id": dataset, "operations": operations, "before": before, "after": after,
                "created_at": time.time(), "expires_at": time.time() + studio_service.PROPOSAL_SECONDS,
                "note": "App-owned crate state only; original audio, external DJ references and match decisions stay unchanged. "
                        "Existing memberships may be unreviewed/offline. This preview is not fresh source-byte/playback verification. "
                        "Merges retain repeated song occurrences; moved-from crates remain, even when empty."}
    edit_id = catalog.digest(document)
    db.execute("INSERT INTO studio_edit_batches(id,document_json) VALUES(?,?)", (edit_id, catalog.canonical(document)))
    details = {crate_id: personal_crates.detail(db, crate_id)["entries"] for crate_id in before}
    return {"edit_id": edit_id, **document, "current_membership_evidence": details}


def _entry_ids(value, entries):
    if not isinstance(value, list) or not value or any(not isinstance(key, str) for key in value):
        raise ValueError("Select a nonempty list of exact membership IDs.")
    if len(set(value)) != len(value) or set(value) - set(entries):
        raise ValueError("Membership IDs must be distinct and belong to the selected source crate.")
    return value


def _edit(db, body, dataset, *, undo):
    studio_service.exact_fields(body, {"request_id", "dataset_id", "edit_id"}, {"expected_crates"})
    prior = studio_service._begin_request(db, body, dataset)
    if prior:
        if prior.get("operation") != ("undo" if undo else "approve"):
            raise catalog.Conflict("Request ID belongs to the other edit operation.")
        return prior
    row = db.execute("SELECT * FROM studio_edit_batches WHERE id=?", (body["edit_id"],)).fetchone()
    if not row:
        raise ValueError("Existing-crate edit preview not found.")
    document = json.loads(row["document_json"])
    if catalog.digest(document) != body["edit_id"] or document["dataset_id"] != dataset:
        raise catalog.Conflict("Edit evidence changed or belongs to another dataset.")
    if undo:
        if not row["approval_json"]:
            raise ValueError("Approve an edit before undoing it.")
        if row["undo_json"]:
            raise catalog.Conflict("Edit was already undone; inspect its recovery receipt.")
        expected = {key: {**crate, "revision": crate["revision"] + 1}
                    for key, crate in document["after"].items()}
        desired = document["before"]
    else:
        if row["approval_json"]:
            raise catalog.Conflict("Edit was already approved; inspect its receipt.")
        if document["expires_at"] < time.time():
            raise catalog.Conflict("Edit preview expired; preview again.")
        expected, desired = document["before"], document["after"]
    for key, crate in expected.items():
        if _snapshot(db, key) != crate:
            raise catalog.Conflict("An affected crate changed; edit/undo would overwrite newer work. No crates changed.")
    expected_receipts = [{"crate_id": key, "base_revision": expected[key]["revision"],
                         "revision": expected[key]["revision"] + 1,
                         "name": crate["name"], "entries": crate["entries"]}
                        for key, crate in desired.items()]
    if "expected_crates" in body and catalog.canonical(body["expected_crates"]) != catalog.canonical(expected_receipts):
        raise catalog.Conflict("Reviewed expected memberships differ from the edit journal; preview again.")
    receipts = []
    for key, crate in desired.items():
        receipt = personal_crates.save(db, {"request_id": "edit:" + body["request_id"] + ":" + key,
            "dataset_id": dataset, "crate_id": key, "base_revision": expected[key]["revision"],
            "name": crate["name"], "entries": crate["entries"]}, dataset, begin=False)
        receipts.append({field: receipt[field] for field in ("crate_id", "base_revision", "revision", "name", "entries")})
    receipt = studio_service._receipt(db, body, {"edit_id": body["edit_id"],
        "operation": "undo" if undo else "approve", "crates": receipts,
        "note": "Crate state restored." if undo else "All reviewed crate changes committed. Original audio unchanged."})
    column = "undo_json" if undo else "approval_json"
    db.execute(f"UPDATE studio_edit_batches SET {column}=? WHERE id=?", (catalog.canonical(receipt), body["edit_id"]))
    return receipt


def edit_approve(db, body, dataset):
    return _edit(db, body, dataset, undo=False)


def edit_undo(db, body, dataset):
    return _edit(db, body, dataset, undo=True)


def edit_history(db):
    return [{"edit_id": row["id"], "document": json.loads(row["document_json"]),
             "approval": json.loads(row["approval_json"]) if row["approval_json"] else None,
             "undo": json.loads(row["undo_json"]) if row["undo_json"] else None}
            for row in db.execute("SELECT * FROM studio_edit_batches ORDER BY rowid DESC LIMIT 30")]
