"""Verify a built backend from an explicit workspace outside the source checkout."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: python desktop/test_packaging.py APP WORKSPACE")
    app, workspace = (Path(value).resolve() for value in sys.argv[1:])
    workspace.mkdir(parents=True, exist_ok=True)
    data = workspace / "fixture-data"
    if data.exists():
        raise SystemExit("Fixture data already exists; choose an empty workspace.")
    executable = app / "Contents/Resources/backend/studio-backend"
    resources = app / "Contents/Resources"
    inventory = json.loads((resources / "release-inventory.json").read_text())
    assert inventory["commercial_rights_cleared"] is False and inventory["bundled_models"] == []
    assert any(component["name"] == "mutagen" and component["license_expression"] == "GPL-2.0-or-later"
               for component in inventory["components"])
    for component in inventory["components"]:
        for notice in component["notices"]:
            assert hashlib.sha256((resources / notice["path"]).read_bytes()).hexdigest() == notice["sha256"]
    assert any(item["path"].endswith("studio-backend") for item in inventory["native_binaries"])
    for item in inventory["native_binaries"]:
        binary = app / "Contents" / item["path"]
        assert binary.stat().st_size == item["bytes"]
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == item["sha256"]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTHON", "AUTOCUE_", "GEMINI_", "GEM_"))}
    subprocess.run([str(executable), "--data", str(data), "--check", "--demo"],
                   cwd=workspace, env=env, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=90)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    def start():
        return subprocess.Popen([str(executable), "--data", str(data), "--port", str(port)],
                                cwd=workspace, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_ready(process):
        for _ in range(150):
            try:
                with urllib.request.urlopen(base + "/api/session", timeout=1) as response:
                    return json.load(response)
            except (urllib.error.URLError, TimeoutError):
                if process.poll() is not None:
                    raise RuntimeError("Bundled backend exited before readiness")
                time.sleep(.1)
        raise RuntimeError("Bundled backend did not become ready")

    def stop(process):
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    process = start()
    try:
        session = wait_ready(process)
        for path, marker in (("/", b"share at most 3"), ("/studio.js", b"api/studio"),
                             ("/studio.css", b"color-scheme"), ("/catalog-state.js", b"")):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                assert marker in response.read(), path
        with urllib.request.urlopen(base + "/api/studio/state", timeout=5) as response:
            state = json.load(response)
        assert "ingestion" in state and "batches" in state and "ai_settings" in state
        assert "experimental" in state["capabilities"]["ai"]["note"].lower()
        assert "up to three" in state["capabilities"]["ai"]["note"]
        assert "two separate calls" in state["capabilities"]["ai"]["note"]
        assert "blocked before inference" in state["capabilities"]["ai"]["note"]

        def post(path, body):
            request = urllib.request.Request(
                base + "/api/studio/" + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "X-AutoCue-Token": session["token"]})
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)

        for endpoint in ("interpret", "interpret/plan"):
            try:
                post(endpoint, {"message": "Plan 18 minutes of House songs released in 2007."})
            except urllib.error.HTTPError as error:
                with error:
                    assert error.code == 400
                    assert "No AI request was sent" in json.load(error)["error"]
            else:
                raise AssertionError("Bundled AI must reject release filters before contacting any runtime.")
        rejected = {"dataset_id": session["dataset_id"], "request_id": "packaged-release-filter",
                    "conversation_id": "packaged-release-filter", "base_revision": 0,
                    "message": "Plan 18 minutes of House songs released in 2007.",
                    "action": "plan", "rule": {}, "share_evidence": False}
        try:
            post("assistant/chat", rejected)
        except urllib.error.HTTPError as error:
            with error:
                assert error.code == 400
                assert "No AI request was sent" in json.load(error)["error"]
        else:
            raise AssertionError("Bundled conversation must reject release filters before drafting.")
        assert post("assistant/load", {"conversation_id": rejected["conversation_id"]})["revision"] == 0

        tracks = post("search", {"rule": {"clean_only": True}})["tracks"]
        assert len(tracks) == 8
        assert "items" in post("inbox", {})
        settings = post("ai/configure", {"dataset_id": session["dataset_id"], "request_id": str(uuid.uuid4()),
                                         "model": "fixture-local", "base_revision": state["ai_settings"]["revision"]})
        assert settings["model"] == "fixture-local"
        batch = post("batch/preview", {"dataset_id": session["dataset_id"], "crates": [
            {"name": "Packaged fixture", "rule": {"clean_only": True, "limit": 2}}]})
        assert len(batch["proposals"][0]["tracks"]) == 2
        saved = post("batch/approve", {"dataset_id": session["dataset_id"], "request_id": str(uuid.uuid4()),
                                       "batch_id": batch["batch_id"]})
        crate_id = saved["crates"][0]["crate_id"]
        edit = post("edit/preview", {"dataset_id": session["dataset_id"], "operations": [
            {"action": "rename", "crate_id": crate_id, "base_revision": 1, "name": "Packaged reviewed edit"}]})
        edit_body = {"dataset_id": session["dataset_id"], "request_id": str(uuid.uuid4()), "edit_id": edit["edit_id"]}
        approved = post("edit/approve", edit_body)
        assert approved == post("edit/approve", edit_body)
        assert approved["crates"][0]["name"] == "Packaged reviewed edit"
        policy = {"target_minutes": 6, "tolerance_minutes": 0, "max_tracks": 2, "phases": [
            {"phase": "dinner", "target_minutes": 3, "tolerance_minutes": 0, "energy_target": .2},
            {"phase": "open_dancing", "target_minutes": 3, "tolerance_minutes": 0, "energy_target": .8}]}
        plan = post("plan", {"rule": {"clean_only": True, "limit": 4}, "event": {}, "policy": policy})
        assert plan["feasible"] and len(plan["alternatives"][0]["phases"]) == 2
        assert plan["alternatives"][0]["transition_evidence"]["status"] == "incomplete"
        tracks = plan["alternatives"][0]["tracks"]
        policy["pinned"] = [{"position": index, "file_id": track["id"]}
                            for index, track in enumerate(tracks)]
        playback = {
            "windows": [{"file_id": track["id"], "content_hash": track["content_hash"],
                         "entry_s": 10, "exit_s": 130, "reviewed": True} for track in tracks],
            "overlaps": [{"from_id": tracks[0]["id"], "to_id": tracks[1]["id"], "seconds": 16}],
        }
        plan = post("plan", {"rule": {"clean_only": True, "limit": 4}, "event": {},
                             "policy": policy, "playback": playback})
        report = plan["alternatives"][0]["transition_evidence"]
        assert report["status"] == "estimated" and report["estimated_elapsed_s"] == 224
        observed = post("history/record", {"request_id": str(uuid.uuid4()), "dataset_id": session["dataset_id"],
            "file_id": tracks[0]["id"], "played_at": "2026-11-12T12:00:00Z", "note": "Synthetic packaged play"})
        history_edit = post("history/preview", {"dataset_id": session["dataset_id"],
                                               "event_ids": [observed["request_id"]]})
        history_body = {"request_id": str(uuid.uuid4()), "dataset_id": session["dataset_id"],
                        "edit_id": history_edit["edit_id"], "event_ids": history_edit["event_ids"]}
        removed = post("history/approve", history_body)
        assert removed == post("history/approve", history_body)
        with urllib.request.urlopen(base + "/api/studio/state", timeout=5) as response:
            assert json.load(response)["history"] == []
        backup = post("backup", {})
        assert backup["manifest"]["table_counts"]["personal_crates"] == 1
        backup_path = workspace / "database-backup.zip"
        backup_path.write_bytes(base64.b64decode(backup["bundle_base64"], validate=True))
        restored = workspace / "restored-data"
        subprocess.run([str(executable), "--data", str(restored), "--restore", str(backup_path)],
                       cwd=workspace, env=env, check=True, stdout=subprocess.DEVNULL, timeout=30)
        subprocess.run([str(executable), "--data", str(restored), "--check"],
                       cwd=workspace, env=env, check=True, stdout=subprocess.DEVNULL, timeout=30)
        stop(process)
        process = start()
        session = wait_ready(process)
        with urllib.request.urlopen(base + "/api/studio/state", timeout=5) as response:
            assert json.load(response)["ai_settings"]["model"] == "fixture-local"
        undone = post("edit/undo", {**edit_body, "request_id": str(uuid.uuid4())})
        assert undone["crates"][0]["name"] == "Packaged fixture" and undone["crates"][0]["revision"] == 3
        history_undo_body = {**history_body, "request_id": str(uuid.uuid4())}
        history_undo = post("history/undo", history_undo_body)
        assert history_undo == post("history/undo", history_undo_body)
        with urllib.request.urlopen(base + "/api/studio/state", timeout=5) as response:
            assert json.load(response)["history"][0]["request_id"] == observed["request_id"]
        print("PASS: bundled UI, inbox, persistent AI settings, batch creation, edit/replay/restart/undo, planning, transitions, database backup/restore and reviewed history recovery")
    finally:
        stop(process)


if __name__ == "__main__":
    main()
