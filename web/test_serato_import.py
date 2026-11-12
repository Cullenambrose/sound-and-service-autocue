"""Synthetic bytes only: import preview never opens or writes a media/crate path."""
import base64
import copy
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import struct
import sys
import threading
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from serato_database import parse_snapshot_bytes, SeratoParseError
from web import catalog, personal_crates, serato_import
from web.setlist_demo import demo_server
from web.setlist_flow import SetlistFlow, FlowConflict


def tlv(tag, value):
    return tag + struct.pack(">I", len(value)) + value


def text(tag, value):
    return tlv(tag, value.encode("utf-16-be"))


HEADER = text(b"vrsn", "1.0/Serato ScratchLive")


def upload(paths, tracks, *, nested=True):
    crate = HEADER + b"".join(
        tlv(b"otrk", text(b"ptrk", path)) if nested else tlv(b"otrk", b"") + text(b"ptrk", path)
        for path in paths)
    database = HEADER
    for path, title, artist in tracks:
        fields = text(b"ptrk", path) + text(b"tsng", title) + text(b"tart", artist)
        database += tlv(b"otrk", fields) if nested else tlv(b"otrk", b"") + fields
    return {"crate_name": "Demo%%Source.crate", "volume_root": "/",
            "crate_base64": base64.b64encode(crate).decode(),
            "database_base64": base64.b64encode(database).decode()}


@pytest.fixture
def library():
    return [
        {"id": "a", "path": "/Demo/a.mp3", "title": "Alpha", "artist": "Artist A"},
        {"id": "b", "path": "/Demo/b.mp3", "title": "Beta", "artist": "Artist B"},
        {"id": "c", "path": "/Demo/c.mp3", "title": "Gamma (Clean)", "artist": "Artist C"},
    ]


def simple_upload():
    return upload(["/Demo/b.mp3", "/Demo/a.mp3", "/Demo/b.mp3"],
                  [("/Demo/a.mp3", "Alpha", "Artist A"), ("/Demo/b.mp3", "Beta", "Artist B")])


def command(flow, action, **fields):
    return flow.command({"request_id": f"import-{flow.state['revision']}-{action}",
                         "base_revision": flow.state["revision"], "mode": "prep",
                         "action": action, **fields})


def accept(flow, body=None):
    body = body or simple_upload()
    preview = serato_import.preview(body, flow.library)
    return command(flow, "serato_import", upload=body, preview_id=preview["id"])


@pytest.mark.parametrize("nested", [True, False])
def test_nested_and_flat_formats_preserve_order_and_duplicates(library, nested):
    body = upload(["/Demo/b.mp3", "/Demo/a.mp3", "/Demo/b.mp3"],
                  [("/Demo/a.mp3", "Alpha", "Artist A"), ("/Demo/b.mp3", "Beta", "Artist B")],
                  nested=nested)
    before = copy.deepcopy(body)
    with patch.object(Path, "open", side_effect=AssertionError("No file reads")):
        preview = serato_import.preview(body, library)
    assert body == before
    assert [row["file_id"] for row in preview["entries"]] == ["b", "a", "b"]
    assert len({row["id"] for row in preview["entries"]}) == 3
    assert preview["counts"]["available"] == 3
    assert serato_import.preview(body, list(reversed(library))) == preview


@pytest.mark.parametrize("data", [
    b"", b"bad", HEADER + b"bad", HEADER + b"otrk" + struct.pack(">I", 999),
    HEADER + tlv(b"otrk", b"bad"), HEADER + tlv(b"otrk", b""),
    HEADER + tlv(b"otrk", text(b"ptrk", "/Demo/a.mp3") + text(b"ptrk", "/Demo/b.mp3")),
    HEADER + tlv(b"otrk", text(b"ptrk", "/Demo/../outside.mp3")),
    HEADER + tlv(b"otrk", text(b"ptrk", "Macintosh HD:Demo:..:outside.mp3")),
    HEADER + tlv(b"otrk", tlv(b"ptrk", b"\0")),
    HEADER + tlv(b"otrk", text(b"ptrk", "file://remote/Demo/a.mp3")),
])
def test_strict_import_rejects_malformed_or_unsafe_paths(data):
    with pytest.raises(SeratoParseError):
        parse_snapshot_bytes(data, kind="crate")


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "oops"])
def test_invalid_numeric_metadata_is_not_silently_discarded(value):
    data = HEADER + tlv(b"otrk", text(b"ptrk", "/Demo/a.mp3") + text(b"tbpm", value))
    with pytest.raises(SeratoParseError, match="bpm"):
        parse_snapshot_bytes(data, kind="database")


def test_explicit_volume_root_unicode_file_urls_and_unknown_metadata():
    data = HEADER + tlv(b"otrk", text(b"ptrk", "file:///Demo/Caf%C3%A9.mp3")
                        + tlv(b"zzzz", b"raw"))
    track = parse_snapshot_bytes(data, kind="database")["tracks"][0]
    assert track["path"] == "/Demo/Caf\u00e9.mp3"
    assert track["raw_fields"] == [{"tag": "zzzz", "hex": "726177"}]
    relative = HEADER + tlv(b"otrk", text(b"ptrk", "Demo/a.mp3"))
    assert parse_snapshot_bytes(relative, kind="crate", volume_root="/Volumes/Synthetic")[
        "tracks"][0]["path"] == "/Volumes/Synthetic/Demo/a.mp3"


def test_missing_metadata_missing_file_and_conflicts_are_distinct(library):
    body = upload(["/Demo/unknown.mp3", "/Demo/offline.mp3", "/Demo/a.mp3"],
                  [("/Demo/offline.mp3", "Offline", "Demo Artist"),
                   ("/Demo/a.mp3", "Different title", "Artist A")])
    preview = serato_import.preview(body, library)
    assert [row["status"] for row in preview["entries"]] == [
        "missing_metadata", "missing_library_file", "metadata_conflict"]
    assert all(row["file_id"] is None for row in preview["entries"])
    assert "no music paths are opened" in preview["note"]


def test_real_paths_smart_crates_bad_base64_and_conflicting_database_are_rejected(library):
    bodies = [
        upload(["/Users/Someone/song.mp3"], []),
        {**simple_upload(), "crate_name": "Demo.scrate"},
        {**simple_upload(), "crate_base64": "not base64"},
        {**simple_upload(), "volume_root": None},
        {**simple_upload(), "database_base64": "A" * 800000},
        upload(["/Demo/a.mp3"], [("/Demo/a.mp3", "Alpha", "A"), ("/Demo/a.mp3", "Beta", "B")]),
    ]
    for body in bodies:
        with pytest.raises(ValueError):
            serato_import.preview(body, library)


def test_preview_catalog_reuses_existing_snapshot_and_personal_set_contracts(library):
    document = serato_import.preview(simple_upload(), library)["catalog_snapshot"]
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        catalog.initialize(db)
        personal_crates.initialize(db)
        first = catalog.import_snapshot(db, document)
        db.commit()
        second = catalog.import_snapshot(db, document)
        db.commit()
        assert first["added"] and not second["added"]
        crate = document["crates"][0]
        entries = [{"id": f"membership-{index}", "snapshot_id": first["snapshot_id"], "song_id": song["id"]}
                   for index, song in enumerate(crate["songs"])]
        personal_crates.save(db, {"request_id": "seed", "dataset_id": "demo", "crate_id": "demo",
            "base_revision": 0, "name": crate["title"], "entries": entries}, "demo")
        db.commit()
        detail = personal_crates.detail(db, "demo")
        assert [row["song"]["path"] for row in detail["entries"]] == [
            "/Demo/b.mp3", "/Demo/a.mp3", "/Demo/b.mp3"]
        assert all(row["match_state"] == "unreviewed" for row in detail["entries"])


def test_seed_edit_save_export_and_reload_preserve_exact_membership_order(tmp_path, library):
    flow = SetlistFlow(tmp_path, event={}, library=library, output_prefix="06")
    result = accept(flow)
    assert [item["track"]["id"] for item in result["crate_entries"]] == ["b", "a", "b"]
    assert not flow.state["played"]
    last = result["seed_rows"][-1]["id"]
    result = command(flow, "seed_move", entry_id=last, direction="up")
    assert [item["track"]["id"] for item in result["crate_entries"]] == ["b", "b", "a"]
    command(flow, "seed_remove", entry_id=result["seed_rows"][0]["id"])
    command(flow, "seed_add", file_id="c")
    assert "Added by DJ" in flow.view("prep")["crate_entries"][-1]["reasons"]
    command(flow, "save")
    result = command(flow, "export")
    assert result["prep_done"] and result["verification"]["mismatches"] == 0
    assert flow.export_path.read_text().splitlines() == ["#EXTM3U", "/Demo/b.mp3", "/Demo/a.mp3", "/Demo/c.mp3"]
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (flow.saved_path, flow.export_path)}
    reloaded = SetlistFlow(tmp_path, event={}, library=list(reversed(library)), output_prefix="06")
    assert reloaded.view("prep")["prep_done"]
    reloaded.command({"request_id": "guest", "base_revision": reloaded.state["revision"], "mode": "live",
                      "action": "guest", "first_name": "Demo", "song": "Alpha"})
    assert before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (flow.saved_path, flow.export_path)}
    command(reloaded, "seed_remove", entry_id=reloaded.view("prep")["seed_rows"][0]["id"])
    assert not reloaded.view("prep")["prep_done"]
    assert not reloaded.view("prep")["saved_current"]


def test_seed_cannot_bypass_bans_or_event_version_selection_and_missing_blocks_save(tmp_path, library):
    event = {"music_lists": {"do_not_play": [{"title": "Beta"}],
                             "must_play": [{"title": "Gamma (Clean)", "artist": "Artist C"}]}}
    flow = SetlistFlow(tmp_path, event=event, library=library)
    body = upload(["/Demo/b.mp3", "/Demo/absent.mp3", "/Demo/a.mp3"],
                  [("/Demo/b.mp3", "Beta", "Artist B"), ("/Demo/a.mp3", "Alpha", "Artist A")])
    result = accept(flow, body)
    assert [item["track"]["id"] for item in result["crate_entries"]] == ["a", "c"]
    assert result["seed_rows"][0]["status"] == "blocked_or_version_conflict"
    with pytest.raises(FlowConflict, match="imported entries"):
        command(flow, "save")
    for row in result["seed_rows"][:2]:
        command(flow, "seed_remove", entry_id=row["id"])
    command(flow, "save")
    with pytest.raises(ValueError, match="eligible"):
        command(flow, "seed_add", file_id="b")


def test_changed_source_identity_stale_preview_and_import_retry(tmp_path, library):
    flow = SetlistFlow(tmp_path, event={}, library=library)
    body = simple_upload()
    preview = serato_import.preview(body, library)
    different = {**body, "crate_name": "Renamed.crate"}
    assert serato_import.preview(different, library)["id"] != preview["id"]
    with pytest.raises(FlowConflict, match="preview changed"):
        command(flow, "serato_import", upload=different, preview_id=preview["id"])
    request = {"request_id": "import", "base_revision": 0, "mode": "prep", "action": "serato_import",
               "upload": body, "preview_id": preview["id"]}
    with patch.object(flow, "_write", wraps=flow._write) as writer:
        result = flow.command(request)
        assert flow.command(request) == result
        assert writer.call_count == 1
    assert flow.state["receipts"]["import"].startswith("sha256:")
    assert body["crate_base64"] not in flow.state_path.read_text()


def test_failed_import_persistence_does_not_accept_source(tmp_path, library):
    flow = SetlistFlow(tmp_path, event={}, library=library)
    before = copy.deepcopy(flow.state)
    with patch.object(flow, "_write", side_effect=OSError("Synthetic disk failure")):
        with pytest.raises(OSError, match="disk failure"):
            accept(flow)
    assert flow.state == before
    assert not list(tmp_path.iterdir())


def test_imported_wrong_version_stays_visible_without_substitution(tmp_path):
    library = [
        {"id": "clean", "path": "/Demo/Clean.mp3", "title": "Summer (Clean)", "artist": "Demo"},
        {"id": "dirty", "path": "/Demo/Dirty.mp3", "title": "Summer (Dirty)", "artist": "Demo"},
    ]
    flow = SetlistFlow(tmp_path, library=library,
                       event={"music_lists": {"must_play": [{"title": "Summer (Dirty)", "artist": "Demo"}]}})
    result = accept(flow, upload(["/Demo/Clean.mp3"], [("/Demo/Clean.mp3", "Summer (Clean)", "Demo")]))
    assert result["seed_rows"][0]["status"] == "blocked_or_version_conflict"
    assert [item["track"]["id"] for item in result["crate_entries"]] == ["dirty"]
    with pytest.raises(FlowConflict, match="imported entries"):
        command(flow, "save")


def test_missing_lookup_does_not_change_fulfillment_or_playable_files(tmp_path, library):
    event = {"music_lists": {"must_play": [{"title": "Missing Demo Song", "artist": "Nobody"},
        {"title": "Gamma (Radio Edit)", "artist": "Artist C"}]}}
    flow = SetlistFlow(tmp_path, event=event, library=library)
    view = flow.view("prep")
    assert {row["status"] for row in view["acquisition_queue"]} == {"missing", "version_unavailable"}
    for row in view["acquisition_queue"]:
        assert len(row["providers"]) == 6 and row["query"]
        request = next(request for request in view["requests"] if request["key"] == row["key"])
        assert not request["fulfilled"] and request["selected_file_id"] is None
    assert "c" not in [item["track"]["id"] for item in view["recommendations"]]
    assert not flow.view("live")["acquisition_queue"]


def test_old_sessions_migrate_without_changing_saved_export(tmp_path, library):
    flow = SetlistFlow(tmp_path, event={}, library=library)
    command(flow, "save")
    command(flow, "export")
    saved, exported = flow.saved_path.read_bytes(), flow.export_path.read_bytes()
    state = json.loads(flow.state_path.read_bytes())
    del state["serato_source"]
    flow.state_path.write_text(json.dumps(state))
    reloaded = SetlistFlow(tmp_path, event={}, library=library)
    assert reloaded.view("prep")["prep_done"]
    assert reloaded.saved_path.read_bytes() == saved and reloaded.export_path.read_bytes() == exported


def test_http_preview_is_authenticated_read_only_and_acceptance_persists(tmp_path, library):
    flow = SetlistFlow(tmp_path, event={}, library=library)
    server = demo_server(flow)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
    try:
        body = simple_upload()
        connection.request("POST", "/api/serato/preview", json.dumps(body))
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.request("GET", "/api/session")
        token = json.loads(connection.getresponse().read())["token"]
        connection.request("POST", "/api/serato/preview", json.dumps(body),
                           {"X-Setlist-Token": token, "Content-Type": "application/json"})
        response = connection.getresponse()
        preview = json.loads(response.read())
        assert response.status == 200 and not list(tmp_path.iterdir())
        accept(flow, body)
        connection.request("GET", "/api/serato/catalog")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == preview["catalog_snapshot"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
