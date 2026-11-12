"""Acquisition progress is not fulfillment; all reconciliation is synthetic."""
import copy
import http.client
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from web.acquisition import synthetic_track
from web.setlist_flow import FlowConflict, SetlistFlow
from web.setlist_demo import demo_server


TRACK = {"id": "acquired-radio", "title": "Synthetic Orbit", "artist": "Demo Band",
         "version": "Radio Edit", "path": "/Demo/Acquired/Orbit-Radio.mp3"}
EVENT = {"music_lists": {"must_play": [
    {"id": "pending", "title": "Synthetic Orbit", "artist": "Demo Band", "version": "Radio Edit"}]}}
LIBRARY = [{"id": "other", "title": "Unrelated Song", "artist": "Other Demo Band",
            "path": "/Demo/Other.mp3"}]


def command(flow, action, **fields):
    return flow.command({"request_id": f"acq-{flow.state['revision']}-{action}",
                         "base_revision": flow.state["revision"], "mode": "prep",
                         "action": action, **fields})


def preview(flow, track=None):
    row = flow.view("prep")["acquisition_queue"][0]
    return flow.acquisition_preview({"key": row["key"], "track": track or TRACK,
                                     "base_revision": flow.state["revision"]})


def accept(flow, track=None):
    row = flow.view("prep")["acquisition_queue"][0]
    result = preview(flow, track)
    return command(flow, "acquisition_accept", key=row["key"], track=result["track"],
                   preview_id=result["id"])


@pytest.fixture
def flow(tmp_path):
    return SetlistFlow(tmp_path, event=EVENT, library=LIBRARY, output_prefix="06")


def test_progress_is_durable_and_never_fulfills_or_changes_recommendations(flow):
    before = flow.view("prep")
    key = before["acquisition_queue"][0]["key"]
    for progress in ("searching", "acquired", "not_started"):
        view = command(flow, "acquisition_progress", key=key, progress=progress)
        assert view["acquisition_queue"][0]["progress"] == progress
        assert view["recommendations"] == before["recommendations"]
        assert view["requests"] == before["requests"]
        assert not view["requests"][0]["fulfilled"]
    reloaded = SetlistFlow(flow.directory, event=EVENT, library=LIBRARY, output_prefix="06")
    assert reloaded.view("prep") == flow.view("prep")


def test_preview_is_read_only_accept_rebuilds_snapshot_and_reloads(flow):
    before = copy.deepcopy(flow.state)
    with patch.object(Path, "open", side_effect=AssertionError("Must not open a referenced audio path")):
        result = preview(flow)
    assert flow.state == before
    assert not flow.state_path.exists()
    assert result["status"] == "matched"
    assert result["selected_file_id"] == TRACK["id"]
    assert result["changes"] == [{"title": "Synthetic Orbit", "before": "missing",
                                   "after": "matched", "selected_file_id": TRACK["id"]}]
    view = accept(flow)
    assert view["requests"][0]["fulfilled"]
    assert not view["needs_review"] and not view["acquisition_queue"]
    item = next(item for item in view["recommendations"] if item["track"]["id"] == TRACK["id"])
    assert "Must-play request" in item["reasons"]
    assert not view["saved"]
    reloaded = SetlistFlow(flow.directory, event=EVENT, library=LIBRARY, output_prefix="06")
    assert reloaded.view("prep") == view
    assert reloaded.view("live")["requests"][0]["fulfilled"]
    snapshot = reloaded._library_snapshot(reloaded.state)[1]
    reloaded.view("live")
    assert reloaded._library_snapshot(reloaded.state)[1] is snapshot


@pytest.mark.parametrize("title,version,status", [
    ("Synthetic Orbit", "Clean", "version_unavailable"),
    ("Synthetic Orbital", "Radio Edit", "ambiguous"),
    ("Entirely Different", "Radio Edit", "missing"),
])
def test_wrong_or_ambiguous_acquired_metadata_never_fulfills(flow, title, version, status):
    track = {**TRACK, "title": title, "version": version}
    result = preview(flow, track)
    assert result["status"] == status
    assert not result["fulfilled"]
    view = accept(flow, track)
    row = view["requests"][0]
    assert row["status"] == status and not row["fulfilled"]
    assert not any("Must-play request" in item["reasons"] for item in view["recommendations"])
    assert not any("requested" in item["badges"] for item in view["recommendations"])
    assert row["needs_review"]
    if status != "missing":
        assert row["candidates"][0]["id"] == TRACK["id"]


def test_ambiguous_reconciliation_still_requires_exact_file_confirmation(flow):
    track = {**TRACK, "title": "Synthetic Orbital"}
    row = accept(flow, track)["requests"][0]
    confirmed = command(flow, "confirm", key=row["key"], file_id=track["id"])
    assert confirmed["requests"][0]["fulfilled"]
    assert confirmed["requests"][0]["selected_file_id"] == track["id"]


def test_preview_and_accept_preserve_existing_saved_artifacts_and_require_resave(flow):
    # A previous saved set is immutable while a pending acquisition is reviewed.
    flow.state["saved"] = {"entries": [], "decisions": {}}
    flow._write(flow.saved_path, (json.dumps(flow.state["saved"], sort_keys=True,
                                            separators=(",", ":")) + "\n").encode())
    command(flow, "export")
    saved, exported = flow.saved_path.read_bytes(), flow.export_path.read_bytes()
    preview(flow)
    view = accept(flow)
    assert view["verification"]["mismatches"] == 0
    assert not view["saved_current"] and not view["prep_done"]
    assert (flow.saved_path.read_bytes(), flow.export_path.read_bytes()) == (saved, exported)
    command(flow, "save")
    view = command(flow, "export")
    assert view["prep_done"]
    assert TRACK["path"].encode() in flow.export_path.read_bytes()
    saved, exported = flow.saved_path.read_bytes(), flow.export_path.read_bytes()
    flow.command({"request_id": "guest-after-acquisition", "base_revision": flow.state["revision"],
                  "mode": "live", "action": "guest", "first_name": "Demo", "song": "Synthetic Orbit"})
    assert (flow.saved_path.read_bytes(), flow.export_path.read_bytes()) == (saved, exported)


def test_preview_and_accept_are_revision_bound_and_retry_safe(flow):
    key = flow.view("prep")["acquisition_queue"][0]["key"]
    result = preview(flow)
    command(flow, "acquisition_progress", key=key, progress="searching")
    body = {"request_id": "stale", "base_revision": 0, "mode": "prep",
            "action": "acquisition_accept", "key": key, "track": TRACK, "preview_id": result["id"]}
    with pytest.raises(FlowConflict, match="Session changed"):
        flow.command(body)
    body["base_revision"] = flow.state["revision"]
    with pytest.raises(FlowConflict, match="preview changed"):
        flow.command(body)
    body["preview_id"] = preview(flow)["id"]
    result = flow.command(body)
    with patch.object(flow, "_write", side_effect=AssertionError("Retry must not write again")):
        assert flow.command(body) == result
    assert flow.state["library_additions"] == [TRACK]


def test_failed_persistence_does_not_activate_preview_metadata(flow):
    result = preview(flow)
    key = flow.view("prep")["acquisition_queue"][0]["key"]
    with patch.object(flow, "_write", side_effect=OSError("Synthetic disk failure")):
        with pytest.raises(OSError, match="disk failure"):
            command(flow, "acquisition_accept", key=key, track=TRACK, preview_id=result["id"])
    assert flow.state["library_additions"] == []
    assert flow.view("prep")["requests"][0]["status"] == "missing"
    assert flow.view("live")["requests"][0]["status"] == "missing"


def test_acquired_song_still_obeys_live_do_not_play_and_played_exclusions(flow):
    accept(flow)
    flow.event["music_lists"]["do_not_play"] = [{"title": TRACK["title"], "artist": TRACK["artist"]}]
    view = flow.view("live")
    assert view["requests"][0]["status"] == "blocked"
    assert TRACK["id"] not in [item["track"]["id"] for item in view["recommendations"]]
    flow.event["music_lists"]["do_not_play"] = []
    flow.command({"request_id": "played-acquired", "base_revision": flow.state["revision"],
                  "mode": "live", "action": "played", "file_id": TRACK["id"]})
    assert flow.view("live")["requests"][0]["note"] == "Already played this event"


def test_acquisition_is_deterministic_with_shuffled_base_library(tmp_path):
    library = LIBRARY + [{"id": "extra", "title": "Another Demo", "artist": "Demo Other",
                          "path": "/Demo/Another.mp3"}]
    normal = SetlistFlow(tmp_path / "regular", event=EVENT, library=library)
    shuffled = SetlistFlow(tmp_path / "shuffled", event=EVENT, library=list(reversed(library)))
    assert preview(normal) == preview(shuffled)
    assert accept(normal) == accept(shuffled)
    assert normal.view("prep") == normal.view("prep")


@pytest.mark.parametrize("track", [
    None, [], {}, {**TRACK, "path": "/Music/Forbidden.mp3"},
    {**TRACK, "path": "/Demo/../Other.mp3"}, {**TRACK, "path": "/Demo//Other.mp3"},
    {**TRACK, "path": "/Demo/Folder\\Other.mp3"}, {**TRACK, "path": "/Demo/Other\n.mp3"},
    {**TRACK, "title": ""}, {**TRACK, "artist": 12}, {**TRACK, "version": []},
    {**TRACK, "download_url": "https://example.test/audio"},
])
def test_synthetic_metadata_rejects_unsafe_or_unknown_input(track):
    with pytest.raises(ValueError):
        synthetic_track(track)


def test_duplicate_ids_paths_and_invalid_progress_are_explicit_errors(flow):
    for track in ({**TRACK, "id": "other"}, {**TRACK, "path": LIBRARY[0]["path"]}):
        with pytest.raises(FlowConflict, match="already"):
            preview(flow, track)
    key = flow.view("prep")["acquisition_queue"][0]["key"]
    with pytest.raises(ValueError, match="Choose"):
        command(flow, "acquisition_progress", key=key, progress="fulfilled")
    with pytest.raises(FlowConflict, match="no longer pending"):
        command(flow, "acquisition_progress", key="not-a-request", progress="searching")


def test_old_sessions_migrate_without_changing_results(flow):
    before = flow.view("prep")
    legacy = {key: value for key, value in flow.state.items()
              if key not in {"acquisition_progress", "library_additions"}}
    flow.state_path.write_text(json.dumps(legacy))
    reloaded = SetlistFlow(flow.directory, event=EVENT, library=LIBRARY, output_prefix="06")
    assert reloaded.view("prep") == before


@pytest.mark.parametrize("progress", ["fulfilled", None, [], {}, 1])
def test_invalid_progress_is_a_clear_validation_error(flow, progress):
    key = flow.view("prep")["acquisition_queue"][0]["key"]
    with pytest.raises(ValueError, match="Choose"):
        command(flow, "acquisition_progress", key=key, progress=progress)
    assert flow.state["revision"] == 0


def test_acquisition_actions_are_prep_only_and_bound_metadata_intake(flow):
    key = flow.view("prep")["acquisition_queue"][0]["key"]
    with pytest.raises(ValueError, match="Prep-only"):
        flow.command({"request_id": "wrong-mode", "base_revision": 0, "mode": "live",
                      "action": "acquisition_progress", "key": key, "progress": "searching"})
    with pytest.raises(ValueError, match="key, track and base_revision"):
        flow.acquisition_preview({"track": TRACK})
    flow.state["library_additions"] = [
        {**TRACK, "id": f"extra-{index}", "title": f"ZZZ {index}", "artist": "Different Demo Artist",
         "path": f"/Demo/Extra-{index}.mp3"} for index in range(100)]
    with pytest.raises(ValueError, match="at most 100"):
        preview(flow)


def test_http_preview_requires_token_and_does_not_persist(flow):
    server = demo_server(flow)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
    try:
        key = flow.view("prep")["acquisition_queue"][0]["key"]
        body = {"key": key, "track": TRACK, "base_revision": 0}
        connection.request("POST", "/api/acquisition/preview", json.dumps(body))
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.request("GET", "/api/session")
        token = json.loads(connection.getresponse().read())["token"]
        connection.request("POST", "/api/acquisition/preview", json.dumps(body),
                           {"X-Setlist-Token": token, "Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200 and result["status"] == "matched"
        assert not flow.state_path.exists()
        body["track"] = {**TRACK, "path": "/Outside/Forbidden.mp3"}
        connection.request("POST", "/api/acquisition/preview", json.dumps(body),
                           {"X-Setlist-Token": token, "Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 400
        assert "under /Demo/" in json.loads(response.read())["error"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
