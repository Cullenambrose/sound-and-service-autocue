"""Fail-closed ordered playlist bundles; original audio is only read."""
import base64
import csv
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import zipfile
import review_store

if __package__:
    from . import catalog, personal_crates
else:
    import catalog
    import personal_crates


def snapshot(db, body):
    if not db.in_transaction:
        db.execute("BEGIN")
    if review_store.dataset_id(db) != body["dataset_id"]:
        raise catalog.Conflict("Export belongs to another dataset; reload the app.")
    crate = personal_crates.detail(db, body["crate_id"])
    if crate["revision"] != body["base_revision"]:
        raise catalog.Conflict("Personal crate changed; refresh before previewing/exporting.")
    roots = {}
    for entry in crate["entries"]:
        file = entry["selected_file"]
        if file:
            root = db.execute("SELECT * FROM library_roots WHERE id=?", (file["root_id"],)).fetchone()
            roots[root["id"]] = dict(root)
    return {"dataset_id": body["dataset_id"], "crate": crate, "roots": roots}


def source_identity(file):
    path = Path(file["path"])
    if path.resolve(strict=True) != path:
        raise OSError("Symlinked source paths are not exportable.")
    value = path.stat()
    if not stat.S_ISREG(value.st_mode):
        raise OSError("Source is not a regular file.")
    if catalog.signature(value) != (file["size"], file["mtime_ns"], file["ctime_ns"]):
        raise OSError("Source changed since indexing; rescan and review.")
    return (value.st_dev, value.st_ino, *catalog.signature(value))


def verify(state):
    entries, proofs = [], {}
    for position, entry in enumerate(state["crate"]["entries"], 1):
        file = entry["selected_file"]
        reason = ""
        if entry["match_state"] != "exact":
            reason = "Reviewed match is " + entry["match_state"] + "; select/review an available file."
        elif any(character in file["path"] for character in "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029\0"):
            reason = "Source path contains a playlist line delimiter."
        else:
            key = file["id"]
            if key not in proofs:
                try:
                    before = source_identity(file)
                    actual = catalog.file_hash(Path(file["path"]))
                    if source_identity(file) != before:
                        raise OSError("Source changed during byte verification.")
                    if actual != file["content_hash"]:
                        raise OSError("Source bytes differ from the reviewed hash; rescan and review.")
                    proofs[key] = (before, "")
                except OSError as error:
                    proofs[key] = (None, str(error) or type(error).__name__)
            reason = proofs[key][1]
        entries.append({
            "position": position, "entry_id": entry["id"], "snapshot_id": entry["snapshot_id"],
            "song_id": entry["song_id"], "song": entry["song"],
            "decision_revision": entry["decision_revision"], "match_state": entry["match_state"],
            "file_id": file["id"] if file else None,
            "path": file["path"] if file else None,
            "content_hash": file["content_hash"] if file else None,
            "eligible": not bool(reason), "reason": reason,
        })
    return entries, proofs


def manifest(state, entries, proofs):
    crate = state["crate"]
    eligible = sum(entry["eligible"] for entry in entries)
    ready = bool(entries) and eligible == len(entries)
    return {
        "schema_version": 1, "dataset_id": state["dataset_id"],
        "crate": {"id": crate["id"], "name": crate["name"], "revision": crate["revision"]},
        "verification": "human-reviewed SHA-256 bytes; not recording fingerprint or cue/tag approval",
        "ready": ready, "requested": len(entries), "eligible": eligible,
        "blocked": len(entries) - eligible, "included": len(entries) if ready else 0,
        "blockers": [] if entries else ["Empty crates cannot be exported."],
        "entries": entries, "roots": state["roots"],
        "file_identities": {key: list(identity) for key, (identity, error) in proofs.items() if not error},
    }


def bundle(document):
    paths = [entry["path"] for entry in document["entries"]]
    m3u8 = "#EXTM3U\n" + "".join(path + "\n" for path in paths)
    columns = ["position", "entry_id", "snapshot_id", "song_id", "artist", "title", "version",
               "path", "file_id", "content_hash", "decision_revision"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for entry in document["entries"]:
        row = {key: entry["song"].get(key, "") if key in ("artist", "title", "version")
               else entry[key] for key in columns}
        # CSV is often opened in spreadsheets. Preserve raw metadata in the
        # manifest, but do not let untrusted catalog text become a formula.
        writer.writerow({key: "'" + value if isinstance(value, str) and
                         value.lstrip().startswith(("=", "+", "-", "@")) else value
                         for key, value in row.items()})
    text = stream.getvalue()
    if m3u8.splitlines()[1:] != paths or [row["path"] for row in csv.DictReader(io.StringIO(text))] != paths:
        raise ValueError("Generated playlist failed order/count validation.")
    files = {"playlist.m3u8": m3u8, "playlist.csv": text,
             "manifest.json": json.dumps(document, ensure_ascii=False, indent=2) + "\n"}
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name, value in files.items():
            archive.writestr(zipfile.ZipInfo(name), value.encode("utf-8"))
    data = result.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name, value in files.items():
            if archive.read(name).decode("utf-8") != value:
                raise ValueError("Generated export bundle failed round-trip validation.")
    return data


def publish(directory, token, data):
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise OSError("App export directory must not be a symlink.")
    destination = directory / (token + ".zip")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination.name


def prepare(connect, directory, body, *, download=False):
    fields = {"dataset_id", "crate_id", "base_revision"} | ({"preview_id"} if download else set())
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError("Export requires dataset_id, crate_id, base_revision and, for download, preview_id.")
    for key in fields - {"base_revision"}:
        catalog.text(body[key], key, required=True)
    if type(body["base_revision"]) is not int or body["base_revision"] < 1:
        raise ValueError("base_revision must be a positive integer.")
    with connect() as db:
        initial = snapshot(db, body)
    entries, proofs = verify(initial)
    document = manifest(initial, entries, proofs)
    token = catalog.digest(document)
    response = {"ok": True, "preview_id": token, "manifest": document}
    data = None
    if download:
        if body["preview_id"] != token:
            raise catalog.Conflict("Export preview is stale; preview and inspect the current set again.")
        if not document["ready"]:
            raise catalog.Conflict("Export blocked: every entry needs verified reviewed bytes; no partial playlist was written.")
        data = bundle(document)
    # No database lock is held during audio reads. Recheck all authoritative
    # state under a short writer gate before publishing an artifact.
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = snapshot(db, body)
        if catalog.canonical(current) != catalog.canonical(initial):
            raise catalog.Conflict("Crate, match or index changed during verification; preview again.")
        for entry in initial["crate"]["entries"]:
            file = entry["selected_file"]
            if file and file["id"] in proofs and not proofs[file["id"]][1]:
                try:
                    identity = source_identity(file)
                except OSError as error:
                    raise catalog.Conflict("Source unavailable before publication; preview again: " + str(error)) from error
                if identity != proofs[file["id"]][0]:
                    raise catalog.Conflict("Source changed before export publication; preview again.")
        if not download:
            return response
        filename = publish(directory, token, data)
        return {**response, "filename": filename, "bundle_base64": base64.b64encode(data).decode("ascii")}
