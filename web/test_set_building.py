"""Synthetic portable intake, source scope, phase planning and main-app handoff."""
import copy
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import threading
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from web import catalog, personal_crates, set_intake, set_planning
from web.setlist_flow import FlowConflict, SetlistFlow, canonical
from web.setlist_demo import mounted_handler
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LIBRARY = [
    {"id": "a", "title": "Alpha", "artist": "Demo A", "path": "/Demo/a.mp3", "duration_s": 180},
    {"id": "b", "title": "Beta", "artist": "Demo B", "path": "/Demo/b.mp3", "duration_s": None},
    {"id": "c", "title": "Gamma (Clean)", "artist": "Demo C", "path": "/Demo/c.mp3", "duration_s": 120},
    {"id": "d", "title": "Gamma (Dirty)", "artist": "Demo C", "path": "/Demo/d.mp3", "duration_s": 130},
]


def command(flow, action, **fields):
    return flow.command({"request_id": f"building-{flow.state['revision']}-{action}",
                         "base_revision": flow.state["revision"], "mode": "prep",
                         "action": action, **fields})


def csv_upload():
    return {"format": "csv", "name": "Synthetic Set", "crate_id": "",
            "text": "id,title,artist,path,version\nb,Beta,Demo B,/Demo/b.mp3,\n"
                    "a,Alpha,Demo A,/Demo/a.mp3,\nb,Beta,Demo B,/Demo/b.mp3,\n"}


def accepted(tmp_path, *, event=None, upload=None, library=None):
    flow = SetlistFlow(tmp_path, event=event or {}, library=library or LIBRARY, output_prefix="06")
    upload = upload or csv_upload()
    preview = set_intake.preview(upload, flow.library)
    command(flow, "portable_import", upload=upload, preview_id=preview["id"])
    return flow


def test_portable_csv_preserves_occurrences_and_input_bytes(tmp_path):
    body = csv_upload()
    before = copy.deepcopy(body)
    with patch.object(Path, "open", side_effect=AssertionError("No reference reads")):
        preview = set_intake.preview(body, LIBRARY)
    assert body == before
    assert [row["file_id"] for row in preview["entries"]] == ["b", "a", "b"]
    assert len({row["id"] for row in preview["entries"]}) == 3
    assert set_intake.preview(body, list(reversed(LIBRARY))) == preview
    flow = accepted(tmp_path)
    command(flow, "save")
    command(flow, "export")
    assert flow.export_path.read_text().splitlines() == ["#EXTM3U", "/Demo/b.mp3", "/Demo/a.mp3", "/Demo/b.mp3"]
    assert flow.state["receipts"]["building-0-portable_import"].startswith("sha256:")


def test_m3u_uses_exact_paths_and_never_guesses_missing_metadata():
    preview = set_intake.preview({"format": "m3u", "name": "Synthetic M3U", "crate_id": "",
                                 "text": "#EXTM3U\n/Demo/a.mp3\n/Demo/Unknown.mp3\n/Demo/a.mp3\n"}, LIBRARY)
    assert [row["status"] for row in preview["entries"]] == ["available", "missing_metadata", "available"]
    assert preview["entries"][1]["title"] == ""


@pytest.mark.parametrize("changes", [
    {"format": "spotify"}, {"text": "title,artist\nAlpha,Demo A"},
    {"text": "id,title,artist,path,version\nx,X,Demo,/Music/real.mp3,"},
    {"text": "id,title,artist,path,version\nx,X,Demo,/Demo/../real.mp3,"},
    {"text": "id,title,artist,path,version\nx,X,Demo,/Demo/x.mp3,,extra"},
    {"text": ""}, {"text": "x" * (512 * 1024 + 1)}, {"format": "m3u", "text": "#EXTM3U\n"},
])
def test_portable_intake_rejects_unsupported_or_unsafe_inputs(changes):
    with pytest.raises(ValueError):
        set_intake.preview({**csv_upload(), **changes}, LIBRARY)


def test_source_scope_never_mislabels_outside_request_missing_or_adds_it(tmp_path):
    event = {"music_lists": {"must_play": [{"title": "Gamma (Clean)", "artist": "Demo C"}]}}
    flow = accepted(tmp_path, event=event)
    full = flow.view("prep")
    assert full["requests"][0]["fulfilled"]
    view = command(flow, "prep_mode", value="source")
    assert [item["track"]["id"] for item in view["recommendations"]] == ["a", "b"]
    assert view["requests"][0]["status"] == "matched"
    assert view["out_of_pool"][0]["request"]["title"] == "Gamma (Clean)"
    assert "c" not in [item["track"]["id"] for item in view["crate_entries"]]
    with pytest.raises(FlowConflict, match="outside this source"):
        command(flow, "save")
    view = command(flow, "prep_mode", value="library")
    assert not view["out_of_pool"] and "c" in [item["track"]["id"] for item in view["crate_entries"]]
    command(flow, "save")
    command(flow, "export")
    saved = flow.saved_path.read_bytes(), flow.export_path.read_bytes()
    view = command(flow, "prep_mode", value="seed")
    assert [item["track"]["id"] for item in view["crate_entries"]] == ["b", "a", "b", "c"]
    assert not view["saved_current"]
    assert (flow.saved_path.read_bytes(), flow.export_path.read_bytes()) == saved


def test_source_scope_runs_engine_against_selected_versions_not_full_library_representative(tmp_path):
    upload = {**csv_upload(), "text": "id,title,artist,path,version\nd,Gamma (Dirty),Demo C,/Demo/d.mp3,\n"}
    flow = accepted(tmp_path, upload=upload,
                    event={"music_lists": {"must_play": [{"title": "Gamma", "artist": "Demo C"}]}})
    assert flow.view("prep")["requests"][0]["selected_file_id"] == "c"
    view = command(flow, "prep_mode", value="source")
    assert view["requests"][0]["selected_file_id"] == "d"
    assert not view["out_of_pool"]
    assert view["crate_entries"][0]["track"]["id"] == "d"


def test_source_scope_preserves_explicit_outside_confirmation(tmp_path):
    upload = {**csv_upload(), "text": "id,title,artist,path,version\na,Alpha,Demo A,/Demo/a.mp3,\n"}
    flow = accepted(tmp_path, upload=upload,
                    event={"music_lists": {"must_play": [{"title": "Bet", "artist": "Demo B"}]}})
    row = flow.view("prep")["requests"][0]
    assert row["status"] == "ambiguous"
    command(flow, "confirm", key=row["key"], file_id="b")
    view = command(flow, "prep_mode", value="source")
    assert view["out_of_pool"] and view["requests"][0]["selected_file_id"] == "b"
    assert [item["track"]["id"] for item in view["recommendations"]] == ["a"]


def test_phase_targets_unknown_durations_pins_and_explicit_sequence(tmp_path):
    flow = accepted(tmp_path)
    rows = flow.view("prep")["seed_rows"]
    command(flow, "seed_plan", entry_id=rows[0]["id"], phase="dinner", pinned=True)
    command(flow, "seed_plan", entry_id=rows[1]["id"], phase="ceremony", pinned=False)
    command(flow, "phase_target", phase="dinner", minutes=20)
    view = flow.view("prep")
    assert view["planning"]["dinner"] == {"known_seconds": 0, "unknown": 1, "entries": 1, "target_minutes": 20}
    assert view["planning"]["ceremony"]["known_seconds"] == 180
    before = copy.deepcopy(flow.state)
    preview = flow.sequence_preview({"base_revision": flow.state["revision"]})
    assert flow.state == before
    assert preview["order"][0] == rows[0]["id"]  # pinned slot stays fixed
    assert preview["order"] == [rows[0]["id"], rows[2]["id"], rows[1]["id"]]
    command(flow, "sequence_accept", preview_id=preview["id"])
    assert flow.state["serato_source"]["order"] == preview["order"]
    with pytest.raises(FlowConflict, match="Unpin"):
        command(flow, "seed_move", entry_id=rows[0]["id"], direction="down")
    with pytest.raises(FlowConflict, match="adjacent"):
        command(flow, "seed_move", entry_id=rows[2]["id"], direction="up")
    command(flow, "save")
    view = command(flow, "export")
    assert view["saved"]["planning"][rows[0]["id"]]["pinned"]
    assert view["saved"]["targets"]["dinner"] == 20
    reloaded = SetlistFlow(tmp_path, event={}, library=LIBRARY, output_prefix="06")
    assert canonical(reloaded.view("prep")) == canonical(view)
    assert reloaded.export_path.read_text().splitlines()[1:] == ["/Demo/b.mp3", "/Demo/b.mp3", "/Demo/a.mp3"]


@pytest.mark.parametrize("minutes", [-1, 1441, float("nan"), float("inf"), True, "20"])
def test_invalid_duration_targets_are_rejected(tmp_path, minutes):
    flow = accepted(tmp_path)
    with pytest.raises(ValueError, match="finite"):
        command(flow, "phase_target", phase="dinner", minutes=minutes)


def test_stale_sequence_preview_is_rejected(tmp_path):
    flow = accepted(tmp_path)
    preview = flow.sequence_preview({"base_revision": flow.state["revision"]})
    command(flow, "phase_target", phase="dinner", minutes=20)
    with pytest.raises(FlowConflict, match="Preview it again"):
        command(flow, "sequence_accept", preview_id=preview["id"])


def test_personal_handoff_uses_existing_store_without_library_reads(tmp_path):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    catalog.initialize(db)
    personal_crates.initialize(db)
    source = set_intake.preview(csv_upload(), LIBRARY)["catalog_snapshot"]
    imported = catalog.import_snapshot(db, source)
    db.commit()
    songs = source["crates"][0]["songs"]
    personal_crates.save(db, {"request_id": "save", "dataset_id": "demo", "crate_id": "personal",
        "base_revision": 0, "name": "Synthetic personal set", "entries": [
            {"id": f"membership-{i}", "snapshot_id": imported["snapshot_id"], "song_id": song["id"]}
            for i, song in enumerate(songs)]}, "demo")
    db.commit()
    with patch.object(Path, "open", side_effect=AssertionError("No audio reads")):
        document = set_intake.personal_snapshot(db, "personal")
        preview = set_intake.preview({"format": "catalog", "name": "Handoff", "crate_id": "personal",
                                     "text": json.dumps(document)}, LIBRARY)
    assert [entry["file_id"] for entry in preview["entries"]] == ["b", "a", "b"]
    assert document["crates"][0]["revision"] == 1
    assert document["crates"][0]["songs"][0]["origin"]["snapshot_id"] == imported["snapshot_id"]
    flow = accepted(tmp_path, upload={"format": "catalog", "name": "Handoff", "crate_id": "personal",
                                     "text": json.dumps(document)})
    assert [item["track"]["id"] for item in flow.view("prep")["crate_entries"]] == ["b", "a", "b"]
    db.close()


def test_main_mount_keeps_api_tokens_isolated_and_serves_correct_paths(tmp_path):
    class Main(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(204)
            self.end_headers()

    flow = accepted(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), mounted_handler(Main, flow))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 204
        response.read()
        connection.request("GET", "/setlist/")
        response = connection.getresponse()
        html = response.read().decode()
        assert response.status == 200 and 'src="/setlist/setlist.js"' in html
        assert 'href="/setlist/api/export"' in html
        connection.request("GET", "/setlist/api/session")
        token = json.loads(connection.getresponse().read())["token"]
        connection.request("GET", "/setlist/api/prep")
        view = json.loads(connection.getresponse().read())
        assert view["mode"] == "prep"
        body = {"request_id": "mount-save", "base_revision": flow.state["revision"], "mode": "prep", "action": "save"}
        connection.request("POST", "/setlist/api/command", json.dumps(body),
                           {"X-AutoCue-Token": token})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.request("POST", "/setlist/api/command", json.dumps(body),
                           {"X-Setlist-Token": token})
        response = connection.getresponse()
        assert response.status == 200
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_actual_main_server_exposes_synthetic_personal_handoff_without_file_reads(tmp_path):
    from web import server as app
    directory = tmp_path / "main"
    with patch.object(app, "SESSION_TOKEN", ""), \
            patch.object(app, "DATA", directory), patch.object(app, "DB_PATH", directory / "library.sqlite3"), \
            patch.object(app, "UPLOADS", directory / "audio"):
        app.initialize()
        document = set_intake.preview(csv_upload(), LIBRARY)["catalog_snapshot"]
        with app.connect() as db:
            snapshot = catalog.import_snapshot(db, document)
            db.commit()
            personal_crates.save(db, {"request_id": "main-save", "dataset_id": "demo", "crate_id": "main-personal",
                "base_revision": 0, "name": "Synthetic main set", "entries": [
                    {"id": f"entry-{i}", "snapshot_id": snapshot["snapshot_id"], "song_id": song["id"]}
                    for i, song in enumerate(document["crates"][0]["songs"])]}, "demo")
        flow = SetlistFlow(tmp_path / "flow", event={}, library=LIBRARY)
        httpd = app.LocalHTTPServer(("127.0.0.1", 0), mounted_handler(app.Handler, flow))
        thread = threading.Thread(target=httpd.serve_forever)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=30)
        try:
            with patch.object(catalog, "file_record", side_effect=AssertionError("Must not stat media")):
                connection.request("GET", "/api/catalog/personal/prep?crate_id=main-personal")
                response = connection.getresponse()
                handoff = json.loads(response.read())
                assert response.status == 200 and len(handoff["crates"][0]["songs"]) == 3
                assert handoff["crates"][0]["id"] == "main-personal"
            connection.request("GET", "/api/session")
            response = connection.getresponse()
            assert json.loads(response.read())["synthetic_prep"]
            with patch.object(app, "list_crates", side_effect=AssertionError("Must not scan Serato")):
                connection.request("GET", "/api/crates")
                response = connection.getresponse()
                assert response.status == 200 and json.loads(response.read())["crates"] == []
            connection.request("POST", "/api/rescan", "{}", {"X-AutoCue-Token": app.SESSION_TOKEN})
            response = connection.getresponse()
            assert response.status == 403
            response.read()
            connection.request("PUT", "/api/tracks/synthetic", "{}", {"X-AutoCue-Token": app.SESSION_TOKEN})
            response = connection.getresponse()
            assert response.status == 403
            response.read()
            connection.request("GET", "/setlist/api/session")
            token = json.loads(connection.getresponse().read())["token"]
            assert token != app.SESSION_TOKEN
        finally:
            connection.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join()


def test_duplicate_report_preserves_versions_and_does_not_mutate_library():
    from setlist import song_identity
    before = copy.deepcopy(LIBRARY)
    report = set_planning.duplicate_report(LIBRARY, song_identity)
    assert report["tracks"] == 4 and report["song_identities"] == 3
    assert report["multiple_file_songs"] == 1
    assert [track["id"] for track in report["groups"][0]] == ["c", "d"]
    assert LIBRARY == before


def test_modes_and_sequence_are_deterministic_across_library_shuffle(tmp_path):
    a = accepted(tmp_path / "a")
    b = accepted(tmp_path / "b", library=list(reversed(LIBRARY)))
    for mode in ("source", "library", "seed"):
        assert canonical(command(a, "prep_mode", value=mode)) == canonical(command(b, "prep_mode", value=mode))
    assert a.sequence_preview({"base_revision": a.state["revision"]}) == b.sequence_preview({"base_revision": b.state["revision"]})


def test_m3u_duplicate_library_paths_are_never_guessed():
    duplicate = {**LIBRARY[0], "id": "duplicate-path", "title": "Different Metadata"}
    body = {"format": "m3u", "name": "Duplicate path", "crate_id": "", "text": "/Demo/a.mp3\n"}
    normal = set_intake.preview(body, LIBRARY + [duplicate])
    assert normal == set_intake.preview(body, [duplicate] + list(reversed(LIBRARY)))
    assert normal["entries"][0]["status"] == "missing_metadata"


def test_m3u_accepts_library_records_with_null_optional_version():
    body = {"format": "m3u", "name": "Null optional version", "crate_id": "", "text": "/Demo/a.mp3\n"}
    result = set_intake.preview(body, [{**LIBRARY[0], "version": None}])
    assert result["entries"][0]["status"] == "available"
    assert result["entries"][0]["version"] == ""


def test_catalog_retains_existing_strict_version_schema():
    document = {"schema_version": 1, "source": "Synthetic optional version",
                "crates": [{"id": "null-version", "title": "Nullable version", "available": True,
                            "songs": [{**LIBRARY[0], "version": None}]}]}
    body = {"format": "catalog", "name": "Nullable version", "crate_id": "null-version",
            "text": json.dumps(document)}
    with pytest.raises(ValueError, match="song version must be text"):
        set_intake.preview(body, LIBRARY)


def test_catalog_handoff_preserves_immutable_origin_and_metadata():
    song = {**LIBRARY[0], "origin": {"snapshot_id": "original", "song_id": "first"},
            "bpm": 120, "key": "8A", "duration_s": 180}
    document = {"schema_version": 1, "source": "Synthetic provenance",
                "crates": [{"id": "origin", "title": "Original metadata", "available": True, "songs": [song]}]}
    before = copy.deepcopy(document)
    body = {"format": "catalog", "name": "Provenance", "crate_id": "origin", "text": json.dumps(document)}
    imported = set_intake.preview(body, LIBRARY)["catalog_snapshot"]["crates"][0]["songs"][0]
    for key in ("origin", "bpm", "key", "duration_s"):
        assert imported[key] == song[key]
    assert document == before


def test_synthetic_main_startup_never_starts_audio_or_scan_workers(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from web import server as app, setlist_demo
    directory = tmp_path / "06-main"
    monkeypatch.setenv("AUTOCUE_SYNTHETIC_PREP", str(tmp_path / "06-flow"))
    monkeypatch.setattr(setlist_demo, "OUTPUTS", tmp_path)
    monkeypatch.setattr(app, "DATA", directory)
    monkeypatch.setattr(app, "DB_PATH", directory / "library.sqlite3")
    monkeypatch.setattr(app, "UPLOADS", directory / "audio")
    monkeypatch.setattr(app, "SESSION_TOKEN", "")
    httpd = Mock(server_address=("127.0.0.1", 0))
    httpd.serve_forever.side_effect = KeyboardInterrupt
    monkeypatch.setattr(app, "LocalHTTPServer", Mock(return_value=httpd))
    with patch.object(app.threading, "Thread", side_effect=AssertionError("No workers")), \
            patch.object(app.scan_jobs, "recover", side_effect=AssertionError("No scan recovery")):
        app.main()
    httpd.server_close.assert_called_once()
