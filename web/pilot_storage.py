"""Bounded app-artifact backup and explicit interrupted-operation recovery."""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import tempfile

FILES = ("06-pilot-binding.json", "06-session.json", "06-prep-saved.json", "06-prep-export.m3u8",
         "06-pilot-launch.json")
JOURNAL = "06-pending-write.json"
LIMIT = 128 * 1024 * 1024


def atomic_write(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix="06-storage-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_sync(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def directory_sync(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def inventory(directory):
    result = {}
    for name in FILES:
        path = directory / name
        if path.is_symlink():
            raise ValueError("Pilot artifact must not be a symlink: " + name)
        if path.exists():
            if not path.is_file() or path.stat().st_size > LIMIT:
                raise ValueError("Pilot artifact is not a bounded regular file: " + name)
            data = path.read_bytes()
            result[name] = {"sha256": hashlib.sha256(data).hexdigest(),
                            "base64": base64.b64encode(data).decode()}
    return result


def decode_inventory(value):
    if not isinstance(value, dict) or set(value) - set(FILES):
        raise ValueError("Backup contains unknown artifact names.")
    result = {}
    total = 0
    for name, record in value.items():
        if not isinstance(record, dict) or set(record) != {"sha256", "base64"}:
            raise ValueError("Invalid backup artifact record.")
        if not isinstance(record["base64"], str):
            raise ValueError("Invalid backup artifact encoding.")
        try:
            data = base64.b64decode(record["base64"], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Invalid backup artifact encoding.") from error
        total += len(data)
        if total > LIMIT or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("Backup artifact checksum or size check failed.")
        result[name] = data
    return result


def read_document(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError("Backup/journal must be a regular bounded non-symlink file.")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported backup/journal schema version.")
    return value


def recover(directory, writer):
    path = directory / JOURNAL
    document = read_document(path)
    if set(document) != {"schema_version", "before"}:
        raise ValueError("Invalid interrupted-operation journal.")
    before = decode_inventory(document["before"])
    if "06-pilot-binding.json" not in before:
        raise ValueError("Interrupted-operation journal has no binding.")
    if (directory / "06-pilot-binding.json").read_bytes() != before["06-pilot-binding.json"]:
        raise ValueError("Recovery journal binding does not match this session.")
    for name in FILES:
        target = directory / name
        if target.is_symlink():
            raise ValueError("Cannot recover through a symlink.")
    for name in FILES:
        target = directory / name
        if name in before:
            writer(target, before[name])
        else:
            target.unlink(missing_ok=True)
    path.unlink()
    directory_sync(directory)


def validate_session(state):
    if not isinstance(state, dict):
        raise ValueError("Pilot session must be an object.")
    if set(state) != {
            "revision", "prep_decisions", "live_decisions", "played", "guests",
            "saved", "exported", "receipts", "serato_source",
            "acquisition_progress", "library_additions", "prep_mode"}:
        raise ValueError("Unsupported pilot session fields; preserve it and use compatible code.")
    if type(state.get("revision")) is not int or state["revision"] < 0:
        raise ValueError("Pilot session revision must be a nonnegative integer.")
    for key in ("prep_decisions", "live_decisions", "receipts", "acquisition_progress"):
        if not isinstance(state.get(key), dict):
            raise ValueError("Invalid pilot session field: " + key)
    for key in ("played", "guests", "library_additions"):
        if not isinstance(state.get(key), list) or not all(isinstance(row, dict) for row in state[key]):
            raise ValueError("Invalid pilot session field: " + key)
    if len(state["guests"]) > 100 or len(state["library_additions"]) > 100:
        raise ValueError("Pilot session exceeds guest or acquisition limits.")
    if not all(isinstance(value, str) for value in state["receipts"].values()):
        raise ValueError("Invalid request receipts.")
    for row in state["guests"]:
        if not isinstance(row.get("title"), str) or not row["title"].strip():
            raise ValueError("Invalid restored guest request.")
    for row in state["library_additions"]:
        if not all(isinstance(row.get(key), str) for key in ("id", "path", "title", "artist", "version")):
            raise ValueError("Invalid restored acquired-file metadata.")
    if state.get("prep_mode") not in {"seed", "source", "library"}:
        raise ValueError("Invalid pilot build mode.")
    for key in ("saved", "serato_source"):
        if state.get(key) is not None and not isinstance(state[key], dict):
            raise ValueError("Invalid pilot session field: " + key)
    if state.get("exported") is not None and not isinstance(state["exported"], str):
        raise ValueError("Invalid export receipt.")
    for decisions in (state["prep_decisions"], state["live_decisions"]):
        for key, decision in decisions.items():
            if (not isinstance(decision, dict) or decision.get("action") not in {"confirm", "dismiss"}
                    or (decision["action"] == "confirm" and not isinstance(decision.get("file_id"), str))):
                raise ValueError("Invalid restored request decision: " + key)
    saved = state["saved"]
    if saved is not None and (not isinstance(saved.get("entries"), list)
                              or not isinstance(saved.get("decisions"), dict)):
        raise ValueError("Invalid saved Prep artifact in session.")
    if saved is not None:
        for entry in saved["entries"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("track"), dict):
                raise ValueError("Invalid saved playlist entry.")
            if not all(isinstance(entry["track"].get(key), str) for key in ("id", "title", "artist", "path")):
                raise ValueError("Invalid saved playlist file.")
    source = state["serato_source"]
    if source is not None:
        if (not isinstance(source.get("entries"), list) or not isinstance(source.get("order"), list)
                or not all(isinstance(value, str) for value in source["order"])
                or not all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in source["entries"])):
            raise ValueError("Invalid imported source in session.")
        ids = [row["id"] for row in source["entries"]]
        if len(ids) != len(set(ids)) or len(source["order"]) != len(set(source["order"])) or set(source["order"]) - set(ids):
            raise ValueError("Invalid source membership order in session.")
        if not isinstance(source.get("source"), dict) or not isinstance(source.get("crate_name"), str):
            raise ValueError("Invalid source provenance in session.")


def backup(flow, selection, event, path):
    with flow.lock:
        if (flow.directory / JOURNAL).exists():
            raise ValueError("Recover the interrupted operation before backing up.")
        flow.selection.validate(flow.library + flow.state["library_additions"], content=True)
        files = inventory(flow.directory)
        if flow.state["saved"]:
            from .setlist_flow import canonical
            expected = (canonical(flow.state["saved"]) + "\n").encode()
            if decode_inventory(files).get("06-prep-saved.json") != expected:
                raise ValueError("Saved artifact diverges from session; recover or explicitly save again.")
        if flow.state["exported"] and flow.verify()["mismatches"] != 0:
            raise ValueError("Export integrity check failed; do not back up inconsistent state.")
        document = {"schema_version": 1, "selection": selection, "event": event, "artifacts": files}
        data = (json.dumps(document, sort_keys=True) + "\n").encode()
        if len(data) > LIMIT or path.exists() or path.is_symlink():
            raise ValueError("Choose a new backup path; maximum bundle size is 128 MiB.")
        atomic_write(path, data)


def restore(bundle, directory):
    document = read_document(bundle)
    if set(document) != {"schema_version", "selection", "event", "artifacts"}:
        raise ValueError("Invalid backup fields.")
    artifacts = decode_inventory(document["artifacts"])
    if "06-pilot-binding.json" not in artifacts or directory.exists() or directory.is_symlink():
        raise ValueError("Restore requires a binding and a new output directory.")
    if "06-session.json" in artifacts:
        validate_session(json.loads(artifacts["06-session.json"]))
    stage = Path(tempfile.mkdtemp(prefix="06-restore-", dir=directory.parent))
    success = False
    try:
        for name, data in artifacts.items():
            atomic_write(stage / name, data)
        from .read_only_prep import ReadOnlyPrepFlow
        flow = ReadOnlyPrepFlow(stage, selection=document["selection"], event=document["event"])
        flow.view("prep")
        if flow.state["exported"] and flow.verify()["mismatches"] != 0:
            raise ValueError("Restored export does not match the durable session.")
        os.rename(stage, directory)
        directory_sync(directory.parent)
        success = True
    finally:
        if not success:
            for name in FILES:
                (stage / name).unlink(missing_ok=True)
            stage.rmdir()
    return document
