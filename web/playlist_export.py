"""Fail-closed ordered playlist bundles; original audio is only read."""
import base64
import csv
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile
import review_store

if __package__:
    from . import catalog, personal_crates
else:
    import catalog
    import personal_crates

MAX_BUNDLE_BYTES = 64 * 1024 * 1024
HISTORY_PAGE_SIZE = 20


class InvalidBundle(ValueError):
    pass


def export_directory(directory):
    if directory.is_symlink():
        raise OSError("App export directory must not be a symlink.")
    return directory


def saved_bundle(directory, export_id, dataset):
    if not isinstance(export_id, str) or not re.fullmatch(r"[0-9a-f]{64}", export_id):
        raise ValueError("export_id must be a lowercase SHA-256 manifest ID.")
    directory = export_directory(directory)
    path = directory / (export_id + ".zip")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise InvalidBundle("Saved export is not a regular file.")
        if info.st_size > MAX_BUNDLE_BYTES:
            raise InvalidBundle("Saved export exceeds the 64 MiB bundle limit.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(MAX_BUNDLE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_BUNDLE_BYTES:
        raise InvalidBundle("Saved export exceeds the 64 MiB bundle limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if sorted(member.filename for member in members) != ["manifest.json", "playlist.csv", "playlist.m3u8"]:
                raise InvalidBundle("Saved export must contain exactly the three supported files.")
            if any(member.compress_type != zipfile.ZIP_STORED or member.flag_bits & 1 for member in members):
                raise InvalidBundle("Saved export uses unsupported compression or encryption.")
            if sum(member.file_size for member in members) > MAX_BUNDLE_BYTES:
                raise InvalidBundle("Saved export expanded contents exceed the 64 MiB limit.")
            document = json.loads(archive.read("manifest.json"))
        if catalog.digest(document) != export_id:
            raise InvalidBundle("Saved manifest does not match the export ID.")
        if document["dataset_id"] != dataset:
            raise InvalidBundle("Saved export belongs to a different dataset.")
        crate = document["crate"]
        if (not isinstance(crate, dict) or type(crate["revision"]) is not int or crate["revision"] < 1
                or not isinstance(crate["id"], str) or not crate["id"].strip()
                or not isinstance(crate["name"], str) or not crate["name"].strip()):
            raise InvalidBundle("Saved manifest has invalid crate metadata.")
        entries = document["entries"]
        if (type(document["schema_version"]) is not int or document["schema_version"] != 1
                or document["ready"] is not True or not isinstance(entries, list) or not entries
                or not len(entries) <= personal_crates.MAX_ENTRIES
                or any(type(document[key]) is not int or document[key] != len(entries)
                       for key in ("requested", "eligible", "included"))
                or type(document["blocked"]) is not int or document["blocked"] != 0 or document["blockers"] != []
                or any(not isinstance(entry, dict) or not isinstance(entry.get("song"), dict)
                       or entry["eligible"] is not True or entry["reason"] for entry in entries)):
            raise InvalidBundle("Saved manifest is not a complete supported export.")
        if bundle(document) != data:
            raise InvalidBundle("Saved playlist/CSV/ZIP bytes do not match the manifest.")
    except (KeyError, TypeError, UnicodeError, zipfile.BadZipFile, ValueError, csv.Error) as error:
        raise InvalidBundle("Saved export failed integrity validation: " + str(error)) from error
    return data, document, info


def saved_exports(directory, dataset, offset=0):
    if type(offset) is not int or offset < 0:
        raise ValueError("Export history offset must be a nonnegative integer.")
    directory = export_directory(directory)
    if not directory.exists():
        return {"exports": [], "offset": offset, "total": 0}
    files = sorted(directory.glob("*.zip"), key=lambda path: (-path.lstat().st_mtime_ns, path.name))
    result = []
    for path in files[offset:offset + HISTORY_PAGE_SIZE]:
        item = {"export_id": path.stem, "filename": path.name}
        try:
            data, document, info = saved_bundle(directory, path.stem, dataset)
            item.update(state="historical", crate=document["crate"], entries=document["included"],
                        size_bytes=len(data), saved_at=info.st_mtime)
        except (OSError, ValueError) as error:
            item.update(state="unavailable", error=str(error) or type(error).__name__)
        result.append(item)
    return {"exports": result, "offset": offset, "total": len(files)}


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
    state = {"dataset_id": body["dataset_id"], "crate": crate, "roots": roots}
    if crate["id"].startswith("studio-") and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='studio_proposals'").fetchone():
        if __package__:
            from . import studio_service
        else:
            import studio_service
        state["studio_creation"] = studio_service.crate_evidence(
            db, {"crate_id": crate["id"]}, body["dataset_id"])
    return state


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
    document = {
        "schema_version": 1, "dataset_id": state["dataset_id"],
        "crate": {"id": crate["id"], "name": crate["name"], "revision": crate["revision"]},
        "verification": "human-reviewed SHA-256 bytes; not recording fingerprint or cue/tag approval",
        "ready": ready, "requested": len(entries), "eligible": eligible,
        "blocked": len(entries) - eligible, "included": len(entries) if ready else 0,
        "blockers": [] if entries else ["Empty crates cannot be exported."],
        "entries": entries, "roots": state["roots"],
        "file_identities": {key: list(identity) for key, (identity, error) in proofs.items() if not error},
    }
    if "studio_creation" in state:
        document["studio_creation"] = state["studio_creation"]
    return document


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
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("Export exceeds the 64 MiB bundle limit; use a smaller personal crate.")
    export_directory(directory)
    directory.mkdir(parents=True, exist_ok=True)
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
