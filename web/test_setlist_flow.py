"""Synthetic-only acceptance checks for the isolated engine-powered Prep/Live flow."""
import copy
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import threading
import time
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))
from web import setlist_flow as workflow
from web.setlist_demo import demo_server
from setlist import context_from_event, rank_next_song, rank_prep_crate, SongRequest, SetlistContext


FIXTURES = ROOT / "engine/setlist/fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def command(flow, action, mode="prep", **fields):
    return flow.command({
        "request_id": f"test-{flow.state['revision']}-{action}",
        "base_revision": flow.state["revision"], "action": action, "mode": mode, **fields,
    })


def finish_prep(flow):
    for row in flow.view("prep")["needs_review"]:
        command(flow, "dismiss", key=row["key"])
    command(flow, "save")
    return command(flow, "export")


@pytest.fixture
def flow(tmp_path):
    return workflow.SetlistFlow(tmp_path)


def test_01_prep_calls_engine_and_keeps_expected_files_without_banned_songs(flow):
    with patch.object(workflow, "rank_prep_crate", wraps=rank_prep_crate) as ranker:
        view = flow.view("prep")
    assert ranker.called
    expected = {"mp-1": "trk-024", "mp-2": "trk-025", "mp-3": "trk-033",
                "mp-4": "trk-038", "mp-5": "trk-039", "mp-6": "trk-040", "mp-8": "trk-013"}
    rows = {row["request"]["id"]: row for row in view["requests"]}
    for request_id, file_id in expected.items():
        assert rows[request_id]["fulfilled"]
        assert rows[request_id]["selected_file_id"] == file_id
        item = next(item for item in view["recommendations"] if item["track"]["id"] == file_id)
        assert "Must-play request" in item["reasons"]
        assert item["track"]["path"] and item["track"]["artist"] and item["version_label"]
    banned = {"trk-016", "trk-017", "trk-019", "trk-020", "trk-021"}
    assert not banned.intersection(item["track"]["id"] for item in view["recommendations"])
    assert rows["mp-7"]["note"] == "Blocked by do-not-play"
    assert not rows["mp-7"]["can_confirm"]
    with pytest.raises(ValueError, match="unblocked"):
        command(flow, "confirm", key=rows["mp-7"]["key"], file_id="trk-019")


def review_flow(tmp_path, title, *, version=None, artist=None):
    event = {"music_lists": {"must_play": [
        {"id": "review", "title": title, "artist": artist, "version": version},
    ]}}
    library = [
        {"id": "off", "title": "Shake It Off", "artist": "Demo Artist", "path": "/Demo/Off.mp3"},
        {"id": "up", "title": "Shake It Up", "artist": "Demo Artist", "path": "/Demo/Up.mp3"},
        {"id": "september", "title": "September", "artist": "Earth, Wind & Fire",
         "path": "/Demo/September.mp3"},
        {"id": "clean", "title": "Midnight Signal (Clean)", "artist": "Kilo Avenue",
         "version": "Clean", "path": "/Demo/Midnight-Clean.mp3"},
        {"id": "other", "title": "Other", "artist": "Other Artist", "path": "/Demo/Other.mp3"},
    ]
    return workflow.SetlistFlow(tmp_path, event=event, library=library)


@pytest.mark.parametrize("title,artist,version,status,candidates", [
    ("Shake It", "Demo Artist", None, "ambiguous", {"off", "up"}),
    ("Shake It", None, None, "ambiguous", {"off", "up"}),
    ("Septemer", "Earth, Wind & Fire", None, "ambiguous", {"september"}),
    ("Not In Library", "Ghost Band", None, "missing", set()),
    ("Midnight Signal", "Kilo Avenue", "Radio Edit", "version_unavailable", {"clean"}),
])
def test_02_review_only_rows_add_nothing_until_exact_confirmation(
    tmp_path, title, artist, version, status, candidates,
):
    flow = review_flow(tmp_path, title, version=version, artist=artist)
    view = flow.view("prep")
    row = view["needs_review"][0]
    assert row["request"]["title"] == title and row["request"]["version"] == version
    assert row["status"] == status and not row["fulfilled"]
    assert {track["id"] for track in row["candidates"]} == candidates
    assert not candidates.intersection(item["track"]["id"] for item in view["recommendations"])
    assert all(not {"requested", "customer"}.intersection(item["badges"]) for item in view["recommendations"])
    assert all("Must-play request" not in item["reasons"] for item in view["recommendations"])
    if status == "missing":
        assert row["note"] == "Not in your library"
        command(flow, "dismiss", key=row["key"])
    else:
        if status == "version_unavailable":
            assert row["note"] == "Have Clean, not Radio Edit"
        selected = sorted(candidates)[0]
        confirmed = command(flow, "confirm", key=row["key"], file_id=selected)
        assert not confirmed["needs_review"]
        assert sum(item["track"]["id"] == selected for item in confirmed["recommendations"]) == 1
        assert confirmed["requests"][0]["fulfilled"]
        assert confirmed["requests"][0]["request"]["title"] == title
        assert confirmed["requests"][0]["selected_file_id"] == selected
        assert not (candidates - {selected}).intersection(
            item["track"]["id"] for item in confirmed["recommendations"]
        )


def test_02b_confirmation_keeps_exact_duplicate_file_and_normal_version_priority(tmp_path):
    library = [
        {"id": "a", "title": "Dance (Clean)", "artist": "Demo Artist", "version": "Clean",
         "path": "/Demo/a.mp3"},
        {"id": "z", "title": "Dance (Clean)", "artist": "Demo Artist", "version": "Clean",
         "path": "/Demo/z.mp3"},
        {"id": "dirty", "title": "Dance (Dirty)", "artist": "Demo Artist", "version": "Dirty",
         "path": "/Demo/Dirty.mp3"},
    ]
    event = {"music_lists": {"must_play": [{"title": "Danc", "artist": "Demo Artist"}]}}
    flow = workflow.SetlistFlow(tmp_path / "duplicate", event=event, library=library)
    row = flow.view("prep")["needs_review"][0]
    result = command(flow, "confirm", key=row["key"], file_id="z")
    assert result["recommendations"][0]["track"]["id"] == "z"
    event["music_lists"]["must_play"] = [{"title": "Dance (Dirty)", "artist": "Demo Artist"}]
    flow = workflow.SetlistFlow(tmp_path / "priority", event=event, library=library)
    result = command(flow, "guest", mode="live", first_name="Demo", song="Dance (Clean)")
    assert result["recommendations"][0]["track"]["id"] == "dirty"
    guest = next(row for row in result["requests"] if row["category"] == "guest_request")
    assert guest["status"] == "version_conflict" and not guest["fulfilled"]


def test_03_first_dance_and_summer_dirty_cues_use_engine_versions(flow, tmp_path):
    view = flow.view("prep")
    orbit = next(item for item in view["recommendations"] if item["track"]["id"] == "trk-004")
    assert "Song cue fits first dance" in orbit["reasons"]
    case = fixture("followup-cases.json")["cases"][2]
    summer = workflow.SetlistFlow(tmp_path / "summer", event=case["event"], library=case["library"])
    assert [item["track"]["id"] for item in summer.view("prep")["recommendations"]] == [
        "followup-summer-dirty"
    ]


def test_04_save_export_verify_exact_order_and_shuffle(flow, tmp_path):
    with pytest.raises(workflow.FlowConflict, match="every Needs review"):
        command(flow, "save")
    result = finish_prep(flow)
    assert result["prep_done"] and result["verification"]["mismatches"] == 0
    saved = flow.saved_path.read_bytes()
    paths = [entry["track"]["path"] for entry in json.loads(saved)["entries"]]
    assert flow.export_path.read_text().splitlines()[1:] == paths
    other = workflow.SetlistFlow(tmp_path / "shuffled", library=fixture("mock-library-shuffled.json")["tracks"])
    finish_prep(other)
    assert other.saved_path.read_bytes() == saved
    assert other.export_path.read_bytes() == flow.export_path.read_bytes()
    reopened = workflow.SetlistFlow(flow.directory)
    assert reopened.view("prep")["prep_done"]
    flow.export_path.write_bytes(b"synthetic corruption")
    assert flow.verify()["mismatches"] == 1
    assert not flow.view("prep")["prep_done"]


def hashes(flow):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (flow.saved_path, flow.export_path)}


def test_05_matched_live_request_uses_live_engine_without_touching_saved_prep(flow):
    finish_prep(flow)
    before = hashes(flow)
    with patch.object(workflow, "rank_next_song", wraps=rank_next_song) as ranker:
        result = command(flow, "guest", mode="live", first_name="Demo", song="Arcade Love")
    assert ranker.called
    guest = next(row for row in result["requests"] if (row["request"]["id"] or "").endswith("-guest"))
    assert guest["fulfilled"] and guest["selected_file_id"] == "trk-032"
    item = next(item for item in result["recommendations"] if item["track"]["id"] == "trk-032")
    assert "Live guest request" in item["reasons"]
    assert hashes(flow) == before
    assert flow.view("prep")["prep_done"]


@pytest.mark.parametrize("song,status", [("Shine", "ambiguous"), ("Not In Library", "missing")])
def test_06_guest_review_list_keeps_unfulfilled_requests_and_confirm_is_explicit(tmp_path, song, status):
    library = [{"id": "shine", "title": "Shine On Me", "artist": "Demo Artist",
                "path": "/Demo/Shine.mp3"}]
    flow = workflow.SetlistFlow(tmp_path, event={}, library=library)
    result = command(flow, "guest", mode="live", first_name="Demo", song=song)
    row = result["requests"][0]
    assert row["status"] == status and not row["fulfilled"]
    assert all(not item["badges"] for item in result["recommendations"])
    assert not any("Live guest request" in item["reasons"] for item in result["recommendations"])
    if status == "ambiguous":
        assert row["candidates"][0]["id"] == "shine"
        result = command(flow, "confirm", mode="live", key=row["key"], file_id="shine")
        assert result["requests"][0]["fulfilled"]
        assert "Live guest request" in result["recommendations"][0]["reasons"]
    else:
        assert row["note"] == "Not in your library"


def test_07_live_dirty_matches_prep_and_unavailable_requests_add_nothing(tmp_path, flow):
    case = fixture("followup-cases.json")["cases"][2]
    prep = workflow.SetlistFlow(tmp_path / "prep", event=case["event"], library=case["library"])
    live = workflow.SetlistFlow(tmp_path / "live", event={}, library=case["library"])
    result = command(live, "guest", mode="live", first_name="Demo", song="Summer (Dirty)")
    assert result["recommendations"][0]["track"]["id"] == prep.view("prep")["recommendations"][0]["track"]["id"]
    assert result["recommendations"][0]["track"]["id"] == "followup-summer-dirty"
    initial = flow.view("live")
    g1 = next(row for row in initial["requests"] if row["request"]["id"] == "g-1")
    assert g1["status"] == "version_unavailable" and not g1["fulfilled"]
    result = command(flow, "guest", mode="live", first_name="Demo", song="Midnight Signal (Radio Edit)")
    row = next(row for row in result["requests"] if (row["request"]["id"] or "").endswith("-guest"))
    assert row["status"] == "version_unavailable" and "not Radio Edit" in row["note"]
    assert result["recommendations"] == initial["recommendations"]


def test_08_bans_and_already_played_remain_excluded_even_after_rerequest(flow):
    result = command(flow, "guest", mode="live", first_name="Demo", song="Chicken Shuffle")
    row = next(row for row in result["requests"] if (row["request"]["id"] or "").endswith("-guest"))
    assert row["note"] == "Blocked by do-not-play" and not row["can_confirm"]
    assert all(item["track"]["id"] != "trk-019" for item in result["recommendations"])
    command(flow, "played", mode="live", file_id="trk-032")
    result = command(flow, "guest", mode="live", first_name="Demo", song="Arcade Love")
    row = next(row for row in result["requests"] if (row["request"]["id"] or "").endswith("-guest")
               and row["request"]["title"] == "Arcade Love")
    assert row["note"] == "Already played this event"
    assert not row["fulfilled"]
    assert all(item["track"]["id"] != "trk-032" for item in result["recommendations"])


def test_09_guest_requires_only_first_name_and_song_with_optional_email(flow):
    result = command(flow, "guest", mode="live", first_name="Demo", email="", song="Arcade Love")
    assert result["requests"][-1]["request"]["metadata"]["first_name"] == "Demo"
    assert result["requests"][-1]["request"]["metadata"]["email"] == ""
    with pytest.raises(ValueError, match="first name"):
        command(flow, "guest", mode="live", first_name="", song="Arcade Love")


def test_10_repeat_and_shuffled_results_are_identical(flow, tmp_path):
    other = workflow.SetlistFlow(tmp_path / "shuffled", library=fixture("mock-library-shuffled.json")["tracks"])
    for mode in ("prep", "live"):
        assert workflow.canonical(flow.view(mode)) == workflow.canonical(flow.view(mode))
        assert workflow.canonical(flow.view(mode)) == workflow.canonical(other.view(mode))


def test_prepared_library_preserves_complete_ranking_and_builds_only_once(flow):
    from dataclasses import asdict
    from setlist import engine

    with patch.object(engine, "_sorted_library", side_effect=AssertionError("library rebuilt")), \
            patch.object(engine, "_representatives", side_effect=AssertionError("representatives rebuilt")):
        for mode in ("prep", "live"):
            assert flow.view(mode)["recommendations"]
    for ranker in (rank_prep_crate, rank_next_song):
        context = context_from_event(flow.event, phase="first_dance",
                                     guest_requests=flow.state["guests"])
        expected = asdict(ranker(context, flow.library, limit=len(flow.library)))
        actual = asdict(ranker(context, flow.prepared_library, limit=len(flow.library)))
        assert actual == expected


def test_typed_diagnostics_reused_after_confirmation_and_invalidated_for_new_guests(tmp_path):
    flow = review_flow(tmp_path, "Shake It", artist="Demo Artist")
    row = flow.view("live")["needs_review"][0]
    with patch.object(flow, "_rank", wraps=flow._rank) as ranker:
        result = command(flow, "confirm", mode="live", key=row["key"], file_id="up")
        # One validation view plus the prospective response, never a second original rank.
        assert ranker.call_count == 2
        assert result["requests"][0]["request"]["title"] == "Shake It"
        assert result["requests"][0]["selected_file_id"] == "up"
    with patch.object(flow, "_rank", wraps=flow._rank) as ranker:
        result = command(flow, "guest", mode="live", first_name="Demo", song="Shake It")
        assert ranker.call_count == 1
    assert result["requests"][-1]["status"] == "ambiguous"
    assert result["requests"][-1]["request"]["title"] == "Shake It"
    cached = flow._typed_diagnostics["live"]
    with patch.object(flow, "_rank", wraps=flow._rank) as ranker:
        flow.view("live")
        assert ranker.call_count == 1
        assert flow._typed_diagnostics["live"] is cached


def test_guest_persists_once_and_retry_does_not_write_again(flow):
    body = {"request_id": "durable-once", "base_revision": 0, "mode": "live",
            "action": "guest", "first_name": "Demo", "song": "Arcade Love"}
    with patch.object(flow, "_write", wraps=flow._write) as writer:
        result = flow.command(body)
        assert writer.call_count == 1
        assert writer.call_args.args[0] == flow.state_path
        assert flow.command(body) == result
        assert writer.call_count == 1


def test_11_live_latency_benchmark_1000_tracks(tmp_path):
    library = fixture("mock-library.json")["tracks"]
    library += [{"id": f"bench-{i}", "title": f"Synthetic Benchmark Song {i}",
                 "artist": f"Synthetic Benchmark Artist {i}", "version": None,
                 "path": f"/Demo/Benchmark/{i}.mp3"}
                for i in range(1000 - len(library))]
    assert len(library) == 1000
    flow = workflow.SetlistFlow(tmp_path, event=fixture("mock-wedding-event.json"), library=library,
                                output_prefix="06")
    baseline = copy.deepcopy(flow.state)
    samples = []
    server = demo_server(flow)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
    try:
        connection.request("GET", "/api/session")
        response = connection.getresponse()
        token = json.loads(response.read())["token"]
        cold_ms = None
        for index in range(202):
            # Comparable submissions preserve the existing 100-guest limit.
            with flow.lock:
                flow.state = copy.deepcopy(baseline)
            body = {"request_id": f"latency-{index}", "base_revision": flow.state["revision"],
                    "mode": "live", "action": "guest", "first_name": "Demo", "song": "Arcade Love"}
            start = time.perf_counter()
            connection.request("POST", "/api/command", json.dumps(body),
                               {"X-Setlist-Token": token, "Content-Type": "application/json"})
            response = connection.getresponse()
            result = json.loads(response.read())
            elapsed = (time.perf_counter() - start) * 1000
            if index == 0:
                cold_ms = elapsed
            elif index > 1:
                samples.append(elapsed)
            assert response.status == 200 and result["requests"][-1]["fulfilled"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
    report = {
        "scope": "Loopback HTTP POST including engine, durable session save, JSON serialization and response read; seeded session reset outside timing",
        "warmup_requests": 1,
        "tracks": len(library), "samples": len(samples), "milliseconds": samples,
        "p50_ms": statistics.median(samples),
        "p95_ms": sorted(samples)[math.ceil(len(samples) * 0.95) - 1],
        "p99_ms": sorted(samples)[math.ceil(len(samples) * 0.99) - 1],
        "cold_ms": cold_ms, "max_ms": max(samples),
        "requests_under_target": sum(sample < 200 for sample in samples),
        "target_ms": 200,
    }
    report["target_met"] = report["p95_ms"] < report["target_ms"]
    print("\nLIVE_LATENCY " + json.dumps(report, sort_keys=True))
    output = os.environ.get("SETLIST_BENCHMARK_OUTPUT")
    if output:
        Path(output).write_text(json.dumps(report, indent=2) + "\n")
    assert all(math.isfinite(value) and value > 0 for value in samples)
    assert report["target_met"], report


def test_12_existing_21_demo_cases_and_six_original_fixture_hashes():
    from setlist.test_demo import DemoAcceptanceTests
    import unittest
    result = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(DemoAcceptanceTests).run(result)
    assert result.testsRun == 21 and result.wasSuccessful(), (result.failures, result.errors)
    original = {
        "README.md": "fce2864b294b7e714843c671baac7faac490cc22",
        "gen.py": "950e36b7b01968cccb0f28a0677d0c4632eb5e5b",
        "mock-event-no-guests.json": "b3ba8d53b7064a2b5673cb1d08434cf0672cfca8",
        "mock-library-shuffled.json": "f53e6823f1e9702f021b371b344abdfc1145acac",
        "mock-library.json": "a0637336ec14ea2db5ddd15aaf597bccbfd6a8cd",
        "mock-wedding-event.json": "c9ceb371e473885338326171a4ea9d57dd58242a",
    }
    for filename, expected in original.items():
        data = (FIXTURES / filename).read_bytes()
        assert hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() == expected


def test_invalid_confirmations_stale_commands_and_lost_ack_retries(flow):
    row = flow.view("prep")["needs_review"][0]
    with pytest.raises(ValueError, match="candidate"):
        command(flow, "confirm", key=row["key"], file_id="invented")
    body = {"request_id": "same-request", "base_revision": 0, "mode": "live",
            "action": "guest", "first_name": "Demo", "song": "Arcade Love"}
    first = flow.command(body)
    assert flow.command(body) == first
    assert sum(guest["id"] == "same-request" for guest in flow.state["guests"]) == 1
    with pytest.raises(workflow.FlowConflict, match="reused"):
        flow.command({**body, "song": "Other"})
    with pytest.raises(workflow.FlowConflict, match="Session changed"):
        flow.command({**body, "request_id": "stale"})


def test_engine_confirmation_cannot_retarget_another_identity_or_bypass_a_ban():
    library = [{"id": "exact", "title": "Song (Clean)", "artist": "Demo Artist", "version": "Clean"}]
    bad = SongRequest("Other", "Demo Artist", metadata={"confirmed_file_id": "exact"})
    with pytest.raises(ValueError, match="song identity"):
        rank_next_song(SetlistContext(guest_requests=(bad,)), library)
    wrong_version = SongRequest("Song (Dirty)", "Demo Artist",
                               metadata={"confirmed_file_id": "exact"})
    with pytest.raises(ValueError, match="song and version"):
        rank_next_song(SetlistContext(guest_requests=(wrong_version,)), library)
    request = SongRequest("Song (Clean)", "Demo Artist", metadata={"confirmed_file_id": "exact"})
    for ranker in (rank_prep_crate, rank_next_song):
        context = SetlistContext(must_play=(request,), do_not_play=(SongRequest("Song"),))
        assert not ranker(context, library).items


def test_confirmed_identity_can_resolve_artist_ambiguity_without_ignoring_the_file(tmp_path):
    library = [
        {"id": "a", "title": "Shared Song", "artist": "Artist A", "path": "/Demo/A.mp3"},
        {"id": "b", "title": "Shared Song", "artist": "Artist B", "path": "/Demo/B.mp3"},
    ]
    flow = workflow.SetlistFlow(tmp_path, event={"music_lists": {"must_play": [
        {"title": "Shared Song"},
    ]}}, library=library)
    row = flow.view("prep")["needs_review"][0]
    assert row["status"] == "ambiguous"
    result = command(flow, "confirm", key=row["key"], file_id="b")
    assert result["requests"][0]["fulfilled"]
    assert [item["track"]["id"] for item in result["recommendations"]] == ["b"]


def test_storage_failure_is_explicit_and_never_acknowledges_guest_success(flow):
    before = copy.deepcopy(flow.state)
    with patch.object(flow, "_write", side_effect=OSError("Synthetic disk full")):
        with pytest.raises(OSError, match="disk full"):
            command(flow, "guest", mode="live", first_name="Demo", song="Arcade Love")
    assert flow.state == before


def test_workflow_never_calls_tag_crate_writers_or_opens_demo_music(flow):
    original_open = Path.open

    def safe_open(path, *args, **kwargs):
        if str(path).startswith("/Demo/"):
            raise AssertionError("Synthetic music paths must never be opened.")
        return original_open(path, *args, **kwargs)

    with patch.object(Path, "open", safe_open), \
            patch("library.tag_gate.tag_write", side_effect=AssertionError("tag write")), \
            patch("library.adapters.serato.SeratoAdapter.write_crate",
                  side_effect=AssertionError("crate write")):
        finish_prep(flow)
        command(flow, "guest", mode="live", first_name="Demo", song="Arcade Love")


def test_http_boundary_uses_only_demo_routes_and_reports_failures(flow):
    server = demo_server(flow)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
    try:
        connection.request("GET", "/api/session")
        response = connection.getresponse()
        assert response.status == 200
        token = json.loads(response.read())["token"]
        connection.request("GET", "/api/prep")
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())["recommendations"]
        body = {"request_id": "http", "base_revision": 0, "mode": "live",
                "action": "guest", "first_name": "Demo", "song": "Arcade Love"}
        connection.request("POST", "/api/command", json.dumps(body))
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.request("POST", "/api/command", json.dumps(body),
                           {"X-Setlist-Token": token, "Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())["mode"] == "live"
        connection.request("GET", "/api/library")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
