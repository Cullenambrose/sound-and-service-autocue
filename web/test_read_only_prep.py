"""Generated audio/snapshots exercise the real-path pilot without a real library."""
import base64
import copy
import hashlib
import http.client
import io
import json
from pathlib import Path
import struct
import sys
import threading
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from web import catalog, serato_import
from web.fixtures import tagged_wav
from web.read_only_prep import ReadOnlyPrepFlow
from web.selected_files import SelectedFiles
from web.setlist_demo import demo_server
from web.setlist_flow import FlowConflict
from web.setlist_flow import canonical


EVENT = {"music_lists": {"must_play": [
    {"title": "Synthetic Song", "artist": "Invented Artist", "version": "Dirty"}]}}


@pytest.fixture
def inputs(tmp_path):
    clean, dirty = tmp_path / "Clean.wav", tmp_path / "Dirty.wav"
    clean.write_bytes(tagged_wav("Clean"))
    dirty.write_bytes(tagged_wav("Dirty"))
    selection = {"schema_version": 1, "library_files": [str(clean)],
                 "acquisition_files": [str(dirty)]}
    return selection, clean, dirty


def new_flow(tmp_path, selection, event=EVENT):
    return ReadOnlyPrepFlow(tmp_path / "session", selection=selection, event=event)


def command(flow, action, **fields):
    return flow.command({"request_id": f"pilot-{flow.state['revision']}-{action}",
                         "base_revision": flow.state["revision"], "mode": "prep",
                         "action": action, **fields})


def acquisition(flow, path):
    key = flow.view("prep")["acquisition_queue"][0]["key"]
    body = {"key": key, "track": {"path": str(path)}, "base_revision": flow.state["revision"]}
    return body, flow.acquisition_preview(body)


def upload(paths, metadata):
    def tlv(tag, data):
        return tag + struct.pack(">I", len(data)) + data
    def text(tag, value):
        return tlv(tag, value.encode("utf-16-be"))
    header = text(b"vrsn", "1.0/Serato ScratchLive")
    crate = header + b"".join(tlv(b"otrk", text(b"ptrk", str(path))) for path in paths)
    database = header + b"".join(tlv(b"otrk", text(b"ptrk", str(path)) + text(b"tsng", title)
                                                 + text(b"tart", artist))
                                 for path, title, artist in metadata)
    return {"crate_name": "Selected.crate", "volume_root": "/",
            "crate_base64": base64.b64encode(crate).decode(),
            "database_base64": base64.b64encode(database).decode()}


def test_selected_metadata_hash_and_preview_accept_restart_exact_export(tmp_path, inputs):
    selection, clean, dirty = inputs
    original = {path: path.read_bytes() for path in (clean, dirty)}
    with patch.object(Path, "rglob", side_effect=AssertionError("No folder scans")):
        flow = new_flow(tmp_path, selection)
        assert flow.library[0]["version"] == "Clean"
        assert flow.library[0]["verification"]["sha256"] == hashlib.sha256(original[clean]).hexdigest()
        before = copy.deepcopy(flow.state)
        body, preview = acquisition(flow, dirty)
        assert flow.state == before and not flow.state_path.exists()
        assert preview["status"] == "matched" and preview["fulfilled"]
        assert preview["track"]["version"] == "Dirty"
        view = command(flow, "acquisition_accept", key=body["key"], track=body["track"],
                       preview_id=preview["id"])
        assert view["requests"][0]["fulfilled"] and not view["needs_review"]
        command(flow, "save")
        command(flow, "export")
        assert flow.verify()["mismatches"] == 0
        saved = flow.saved_path.read_bytes(), flow.export_path.read_bytes()
        restored = new_flow(tmp_path, selection)
        assert canonical(restored.view("prep")) == canonical(flow.view("prep"))
        assert restored.verify()["mismatches"] == 0
        restored.command({"request_id": "pilot-live", "base_revision": restored.state["revision"],
                          "mode": "live", "action": "guest", "first_name": "Fixture", "song": "Synthetic Song"})
        assert (flow.saved_path.read_bytes(), flow.export_path.read_bytes()) == saved
    assert {path: path.read_bytes() for path in original} == original


def test_real_path_serato_order_duplicates_missing_and_conflicts(tmp_path, inputs):
    selection, clean, dirty = inputs
    selection["library_files"].append(selection["acquisition_files"].pop())
    missing, unknown, conflict = tmp_path / "Missing.wav", tmp_path / "Unknown.wav", tmp_path / "Conflict.wav"
    from mutagen.wave import WAVE
    from mutagen.id3 import TIT2
    stream = io.BytesIO(tagged_wav())
    audio = WAVE(stream)
    audio.tags.add(TIT2(encoding=3, text=["Distinct Song"]))
    audio.save(stream)
    conflict.write_bytes(stream.getvalue())
    selection["library_files"].append(str(conflict))
    flow = ReadOnlyPrepFlow(tmp_path / "other-session", selection=selection, event={})
    selected = upload([clean, dirty, clean, missing, unknown, conflict],
                      [(dirty, "Synthetic Song", "Invented Artist"),
                       (clean, "Synthetic Song", "Invented Artist"),
                       (missing, "Missing Song", "Invented Artist"),
                       (conflict, "Different Title", "Invented Artist")])
    original = copy.deepcopy(selected)
    with patch.object(Path, "open", side_effect=AssertionError("Preview must not open snapshot music paths")):
        preview = flow.serato_preview(selected)
    assert [row["path"] for row in preview["entries"]] == [str(path) for path in
                                                          (clean, dirty, clean, missing, unknown, conflict)]
    assert [row["status"] for row in preview["entries"]] == [
        "available", "available", "available", "missing_library_file", "missing_metadata", "metadata_conflict"]
    view = command(flow, "serato_import", upload=selected, preview_id=preview["id"])
    assert selected == original
    assert view["seed_rows"][1]["status"] == "blocked_or_version_conflict"
    assert view["seed_rows"][1]["path"] == str(dirty)
    # Preserve the selected version policy: never substitute Clean for Dirty.
    # Remove the held Dirty occurrence explicitly before saving Clean duplicates.
    view = command(flow, "seed_remove", entry_id=view["seed_rows"][1]["id"])
    for row in list(view["seed_rows"])[2:]:
        view = command(flow, "seed_remove", entry_id=row["id"])
    assert [row["track"]["path"] for row in view["crate_entries"]] == [str(clean), str(clean)]
    command(flow, "save")
    command(flow, "export")
    assert flow.export_path.read_text().splitlines() == ["#EXTM3U", str(clean), str(clean)]
    assert flow.verify()["mismatches"] == 0


@pytest.mark.parametrize("change", ["delete", "replace", "modify", "symlink"])
def test_changed_library_fails_closed_for_views_commands_export_and_restart(tmp_path, inputs, change):
    selection, clean, dirty = inputs
    flow = new_flow(tmp_path, selection, {})
    command(flow, "save")
    command(flow, "export")
    if change == "delete":
        clean.unlink()
    elif change == "replace":
        clean.unlink()
        clean.write_bytes(tagged_wav("Clean"))
    elif change == "modify":
        clean.write_bytes(tagged_wav("Dirty"))
    else:
        clean.unlink()
        clean.symlink_to(dirty)
    for operation in (lambda: flow.view("prep"), lambda: command(flow, "export"),
                      flow.verify, lambda: new_flow(tmp_path, selection, {})):
        with pytest.raises((ValueError, OSError)):
            operation()


def test_acquired_file_must_be_selected_and_tags_cannot_be_submitted(tmp_path, inputs):
    selection, clean, dirty = inputs
    flow = new_flow(tmp_path, selection)
    for value in ({"path": str(clean)}, {"path": str(dirty), "title": "Injected"},
                  {"path": str(tmp_path / "Unselected.wav")}, {"path": "/Demo/Invented.wav"}):
        with pytest.raises(ValueError):
            flow._acquisition_track(value)
    dirty.write_bytes(tagged_wav("Clean"))
    _, preview = acquisition(flow, dirty)
    assert preview["status"] == "version_unavailable" and not preview["fulfilled"]

def test_missing_acquired_file_never_activates_or_fulfills(tmp_path, inputs):
    selection, _, dirty = inputs
    flow = new_flow(tmp_path, selection)
    body, preview = acquisition(flow, dirty)
    dirty.unlink()
    with pytest.raises(OSError):
        command(flow, "acquisition_accept", key=body["key"], track=body["track"], preview_id=preview["id"])
    assert not flow.state["library_additions"]
    assert not flow.view("prep")["requests"][0]["fulfilled"]


def test_file_changes_invalidate_preview_and_persisted_additions(tmp_path, inputs):
    selection, clean, dirty = inputs
    flow = new_flow(tmp_path, selection)
    body, preview = acquisition(flow, dirty)
    dirty.write_bytes(tagged_wav("Clean"))
    with pytest.raises(FlowConflict, match="preview changed"):
        command(flow, "acquisition_accept", key=body["key"], track=body["track"], preview_id=preview["id"])
    assert not flow.state["library_additions"]
    dirty.write_bytes(tagged_wav("Dirty"))
    body, preview = acquisition(flow, dirty)
    command(flow, "acquisition_accept", key=body["key"], track=body["track"], preview_id=preview["id"])
    dirty.write_bytes(tagged_wav("Clean"))
    with pytest.raises(ValueError, match="changed"):
        new_flow(tmp_path, selection)


def test_session_binding_rejects_changed_event_selection_and_unbound_session(tmp_path, inputs):
    selection, _, _ = inputs
    flow = new_flow(tmp_path, selection)
    command(flow, "acquisition_progress", key=flow.view("prep")["acquisition_queue"][0]["key"], progress="searching")
    with pytest.raises(ValueError, match="changed"):
        new_flow(tmp_path, selection, {})
    different = {**selection, "acquisition_files": []}
    with pytest.raises(ValueError, match="changed"):
        new_flow(tmp_path, different)
    (flow.directory / "06-pilot-binding.json").unlink()
    with pytest.raises(ValueError, match="no pilot binding"):
        new_flow(tmp_path, selection)


@pytest.mark.parametrize("value", ["/", "/tmp/../Music.wav", "relative.wav", "/tmp/music\n.wav",
                                  "/tmp//music.wav", "/tmp/./music.wav"])
def test_manifest_rejects_unsafe_or_noncanonical_paths(value):
    with pytest.raises(ValueError):
        SelectedFiles({"schema_version": 1, "library_files": [value], "acquisition_files": []}).inspect(value)


def test_missing_tags_unsupported_audio_symlinks_and_persistence_failure_are_explicit(tmp_path, inputs):
    selection, clean, dirty = inputs
    flow = new_flow(tmp_path, selection)
    body, preview = acquisition(flow, dirty)
    with patch.object(flow, "_write", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            command(flow, "acquisition_accept", key=body["key"], track=body["track"], preview_id=preview["id"])
    assert not flow.state["library_additions"]
    with patch.object(catalog, "read_metadata", return_value={"title": "", "artist": "", "version": ""}):
        with pytest.raises(ValueError, match="title tags"):
            flow.selection.inspect(str(dirty), acquired=True)
    dirty.write_bytes(b"not audio")
    with pytest.raises(ValueError, match="metadata"):
        flow.selection.inspect(str(dirty), acquired=True)
    dirty.unlink()
    dirty.symlink_to(clean)
    with pytest.raises(ValueError, match="symlinks"):
        flow.selection.inspect(str(dirty), acquired=True)


def test_pilot_http_requires_token_exposes_selection_and_disables_synthetic_intake(tmp_path, inputs):
    selection, _, dirty = inputs
    flow = new_flow(tmp_path, selection)
    server = demo_server(flow)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection(*server.server_address)
    try:
        connection.request("GET", "/api/session")
        response = connection.getresponse()
        session = json.loads(response.read())
        assert not session["synthetic"] and session["read_only_pilot"]
        assert session["acquisition_paths"] == [str(dirty)]
        assert session["max_snapshot_bytes"] == 32 * 1024 * 1024
        connection.request("POST", "/api/acquisition/preview", body="{}")
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.request("POST", "/api/portable/preview", body="{}",
                           headers={"X-Setlist-Token": session["token"]})
        response = connection.getresponse()
        assert response.status == 400
        assert "disabled" in json.loads(response.read())["error"]
        body, preview = acquisition(flow, dirty)
        connection.request("POST", "/api/acquisition/preview", body=json.dumps(body),
                           headers={"X-Setlist-Token": session["token"]})
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())["id"] == preview["id"]
        connection.request("GET", "/api/serato/demo")
        response = connection.getresponse()
        assert response.status != 200
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join()


def test_larger_pilot_snapshot_limit_does_not_change_demo_limit(tmp_path, inputs):
    selection, clean, _ = inputs
    flow = new_flow(tmp_path, selection)
    body = upload([clean], [(clean, "Synthetic Song", "Invented Artist")])
    data = base64.b64decode(body["database_base64"])
    padding = b"x" * (512 * 1024)
    body["database_base64"] = base64.b64encode(data + b"xxxx" + struct.pack(">I", len(padding)) + padding).decode()
    with pytest.raises(ValueError, match="512 KiB"):
        serato_import.preview(body, flow.library)
    assert flow.serato_preview(body)["entries"][0]["status"] == "available"
