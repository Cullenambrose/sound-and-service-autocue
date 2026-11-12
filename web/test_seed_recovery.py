"""Imported references remain immutable until an explicit exact-file reconnect."""
import copy
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from web import serato_import, setlist_flow
from web.setlist_flow import FlowConflict, SetlistFlow
from web.test_serato_import import upload

TRACK = {"id": "recovered", "title": "Synthetic Orbit (Radio Edit)", "artist": "Demo Band",
         "path": "/Demo/Acquired/Orbit.mp3"}
BASE = [{"id": "other", "title": "ZZZ", "artist": "Other Artist", "path": "/Demo/Other.mp3"}]
OLD = "/Demo/Old/Orbit.mp3"


def command(flow, action, **fields):
    return flow.command({"request_id": f"recovery-{flow.state['revision']}-{action}",
                         "base_revision": flow.state["revision"], "mode": "prep",
                         "action": action, **fields})


def imported(tmp_path, *, library=None, event=None, title=TRACK["title"], metadata=True):
    flow = SetlistFlow(tmp_path, event=event or {}, library=library or BASE, output_prefix="06")
    body = upload([OLD, BASE[0]["path"], OLD], [
        *([(OLD, title, TRACK["artist"])] if metadata else []),
        (BASE[0]["path"], BASE[0]["title"], BASE[0]["artist"])])
    preview = serato_import.preview(body, flow.library)
    command(flow, "serato_import", upload=body, preview_id=preview["id"])
    return flow


def acquire(flow, track=TRACK):
    key = flow.view("prep")["acquisition_queue"][0]["key"]
    result = flow.acquisition_preview({"key": key, "track": track,
                                      "base_revision": flow.state["revision"]})
    command(flow, "acquisition_accept", key=key, track=track, preview_id=result["id"])
    return result


def reconnect(flow, index=0):
    row = flow.view("prep")["seed_rows"][index]
    return command(flow, "seed_reconnect", entry_id=row["id"], file_id=TRACK["id"])


def test_missing_source_acquisition_requires_each_duplicate_reconnect_and_preserves_evidence(tmp_path):
    flow = imported(tmp_path)
    original = copy.deepcopy(flow.state["serato_source"])
    queue = flow.view("prep")["acquisition_queue"]
    assert len(queue) == 2 and len({row["key"] for row in queue}) == 2
    result = acquire(flow)
    assert result["status"] == "ready_to_reconnect" and not result["fulfilled"]
    before = flow.view("prep")
    assert [row["status"] for row in before["seed_rows"]] == [
        "missing_library_file", "available", "missing_library_file"]
    assert [item["track"]["id"] for item in before["crate_entries"]] == ["other"]
    assert not before["acquisition_queue"]
    with pytest.raises(FlowConflict, match="imported entries"):
        command(flow, "save")
    reconnect(flow)
    assert flow.view("prep")["seed_rows"][2]["status"] == "missing_library_file"
    view = reconnect(flow, 2)
    assert [item["track"]["id"] for item in view["crate_entries"]] == ["recovered", "other", "recovered"]
    source = flow.state["serato_source"]
    for key in ("entries", "order", "source", "catalog_snapshot"):
        assert source[key] == original[key]
    assert view["seed_rows"][0]["path"] == OLD
    assert view["seed_rows"][0]["replacement"]["path"] == TRACK["path"]
    assert all("Reconnected by DJ" in item["reasons"] for item in view["crate_entries"][::2])
    command(flow, "save")
    view = command(flow, "export")
    assert view["prep_done"] and view["verification"]["mismatches"] == 0
    assert flow.export_path.read_text().splitlines() == [
        "#EXTM3U", TRACK["path"], BASE[0]["path"], TRACK["path"]]
    reloaded = SetlistFlow(tmp_path, event={}, library=BASE, output_prefix="06")
    assert setlist_flow.canonical(reloaded.view("prep")) == setlist_flow.canonical(view)
    assert not reloaded.state["played"]


@pytest.mark.parametrize("title,status", [
    ("Synthetic Orbit (Clean)", "version_unavailable"),
    ("Synthetic Orbital (Radio Edit)", "ambiguous"),
    ("Entirely Different (Radio Edit)", "missing"),
])
def test_wrong_version_ambiguous_or_different_acquired_file_cannot_reconnect(tmp_path, title, status):
    flow = imported(tmp_path)
    result = acquire(flow, {**TRACK, "title": title})
    assert result["status"] == status
    row = flow.view("prep")["seed_rows"][0]
    assert not row["reconnect_candidates"]
    with pytest.raises(ValueError, match="exact-version"):
        reconnect(flow)
    assert not flow.state["serato_source"].get("replacements")


def test_existing_exact_file_can_reconnect_without_acquisition_or_request_boost(tmp_path):
    flow = imported(tmp_path, library=BASE + [TRACK])
    before = flow.view("prep")
    row = before["seed_rows"][0]
    assert [item["id"] for item in row["reconnect_candidates"]] == ["recovered"]
    assert not before["requests"]
    result = reconnect(flow)
    item = result["crate_entries"][0]
    assert item["score"] == next(item["score"] for item in before["recommendations"]
                                  if item["track"]["id"] == "recovered")
    assert not {"requested", "customer"} & set(item["badges"])


@pytest.mark.parametrize("event", [
    {"music_lists": {"do_not_play": [{"title": "Synthetic Orbit", "artist": "Demo Band"}]}},
    {"music_lists": {"must_play": [{"title": "Synthetic Orbit (Clean)", "artist": "Demo Band"}]}},
])
def test_reconnect_cannot_bypass_bans_or_event_version_selection(tmp_path, event):
    clean = {**TRACK, "id": "clean", "title": "Synthetic Orbit (Clean)", "path": "/Demo/Clean.mp3"}
    flow = imported(tmp_path, library=BASE + [TRACK, clean], event=event)
    assert flow.view("prep")["seed_rows"][0]["reconnect_candidates"] == []
    with pytest.raises(ValueError, match="exact-version"):
        reconnect(flow)


def test_missing_or_conflicting_source_metadata_is_not_guessed(tmp_path):
    flow = imported(tmp_path, library=BASE + [TRACK], metadata=False)
    row = flow.view("prep")["seed_rows"][0]
    assert row["status"] == "missing_metadata" and not row["reconnect_candidates"]
    assert not flow.view("prep")["acquisition_queue"]
    with pytest.raises(FlowConflict, match="no longer waiting"):
        reconnect(flow)


def test_reconnect_retry_stale_revision_and_failed_write_are_safe(tmp_path):
    flow = imported(tmp_path, library=BASE + [TRACK])
    row = flow.view("prep")["seed_rows"][0]
    body = {"request_id": "reconnect-once", "base_revision": flow.state["revision"],
            "mode": "prep", "action": "seed_reconnect", "entry_id": row["id"], "file_id": TRACK["id"]}
    before = copy.deepcopy(flow.state)
    with patch.object(flow, "_write", side_effect=OSError("Synthetic disk failure")):
        with pytest.raises(OSError):
            flow.command(body)
    assert flow.state == before
    result = flow.command(body)
    with patch.object(flow, "_write", side_effect=AssertionError("Retry must not write")):
        assert flow.command(body) == result
    body["request_id"] = "stale"
    with pytest.raises(FlowConflict, match="Session changed"):
        flow.command(body)
    with pytest.raises(FlowConflict, match="no longer waiting"):
        reconnect(flow)
    body.update(request_id="live", base_revision=flow.state["revision"], mode="live")
    with pytest.raises(ValueError, match="Prep-only"):
        flow.command(body)


def test_reconnected_files_remain_excluded_in_live_and_saved_bytes_stay_unchanged(tmp_path):
    flow = imported(tmp_path, library=BASE + [TRACK])
    reconnect(flow)
    reconnect(flow, 2)
    command(flow, "save")
    command(flow, "export")
    before = flow.saved_path.read_bytes(), flow.export_path.read_bytes()
    flow.command({"request_id": "played", "base_revision": flow.state["revision"], "mode": "live",
                  "action": "played", "file_id": TRACK["id"]})
    assert TRACK["id"] not in [item["track"]["id"] for item in flow.view("live")["recommendations"]]
    assert (flow.saved_path.read_bytes(), flow.export_path.read_bytes()) == before


def test_shuffle_and_repeated_resolution_are_deterministic_and_cached(tmp_path):
    a = imported(tmp_path / "a", library=BASE + [TRACK])
    b = imported(tmp_path / "b", library=[TRACK] + BASE)
    assert a.view("prep") == b.view("prep")
    with patch.object(setlist_flow, "rank_prep_crate", wraps=setlist_flow.rank_prep_crate) as ranker:
        a.view("prep")
        # One event ranking; source diagnostics were cached across duplicate memberships.
        assert ranker.call_count == 1
    assert reconnect(a) == reconnect(b)


def test_reconnect_preserves_saved_bytes_and_requires_save_again(tmp_path):
    flow = SetlistFlow(tmp_path, event={}, library=BASE + [TRACK], output_prefix="06")
    command(flow, "save")
    command(flow, "export")
    saved = flow.saved_path.read_bytes(), flow.export_path.read_bytes()
    body = upload([OLD], [(OLD, TRACK["title"], TRACK["artist"])])
    source = serato_import.preview(body, flow.library)
    command(flow, "serato_import", upload=body, preview_id=source["id"])
    result = reconnect(flow)
    assert not result["saved_current"] and not result["prep_done"]
    assert (flow.saved_path.read_bytes(), flow.export_path.read_bytes()) == saved
    command(flow, "save")
    result = command(flow, "export")
    assert result["prep_done"]
    assert flow.export_path.read_text().splitlines() == ["#EXTM3U", TRACK["path"]]


def test_unspecified_source_version_uses_engine_clean_preference(tmp_path):
    clean = {**TRACK, "id": "clean", "title": "Synthetic Orbit (Clean)", "path": "/Demo/Clean.mp3"}
    dirty = {**TRACK, "id": "dirty", "title": "Synthetic Orbit (Dirty)", "path": "/Demo/Dirty.mp3"}
    flow = imported(tmp_path, library=BASE + [dirty, clean], title="Synthetic Orbit")
    row = flow.view("prep")["seed_rows"][0]
    assert [track["id"] for track in row["reconnect_candidates"]] == ["clean"]
    with pytest.raises(ValueError, match="exact-version"):
        command(flow, "seed_reconnect", entry_id=row["id"], file_id="dirty")
    command(flow, "seed_reconnect", entry_id=row["id"], file_id="clean")
    assert flow.view("prep")["crate_entries"][0]["track"]["id"] == "clean"
