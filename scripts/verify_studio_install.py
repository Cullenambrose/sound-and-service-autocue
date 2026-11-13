"""Exercise the installed Studio console command and static assets outside checkout."""
import base64
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import web.studio


def main():
    checkout = Path(__file__).resolve().parents[1]
    if Path(web.studio.__file__).resolve().is_relative_to(checkout):
        raise RuntimeError("Verify a non-editable wheel, not checkout/PYTHONPATH imports.")
    command = Path(sys.executable).parent / ("autocue-studio.exe" if os.name == "nt" else "autocue-studio")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("AUTOCUE_", "GEMINI_", "GEM_")) and key != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="autocue-studio-installed-") as directory:
        root = Path(directory).resolve()
        check = subprocess.run([str(command), "--data", str(root / "data"), "--check", "--demo"],
                               cwd=root, env=environment, capture_output=True, text=True, timeout=60)
        if check.returncode:
            raise RuntimeError(check.stderr or check.stdout)
        result = json.loads(check.stdout)
        assert result["commercial_ready"] is False and result["release_gates"]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with (root / "server.log").open("w+") as log:
            process = subprocess.Popen([str(command), "--data", str(root / "data"), "--port", str(port)],
                                       cwd=root, env=environment, stdout=log, stderr=log)
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base + "/api/session", timeout=1) as response:
                            session = json.load(response)
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if process.poll() is not None:
                            log.seek(0)
                            raise RuntimeError(log.read())
                        time.sleep(.1)
                else:
                    raise RuntimeError("Installed Studio did not become responsive.")
                for path, marker in (("/", b"at most three bounded local-model calls"), ("/studio.js", b"api/studio"),
                                     ("/studio.css", b"color-scheme")):
                    with urllib.request.urlopen(base + path) as response:
                        assert marker in response.read(), path
                request = urllib.request.Request(base + "/api/studio/search",
                    data=json.dumps({"rule": {"clean_only": True}}).encode(),
                    headers={"Content-Type": "application/json", "X-AutoCue-Token": session["token"]})
                with urllib.request.urlopen(request) as response:
                    assert len(json.load(response)["tracks"]) == 8
                policy = {"target_minutes": 6, "tolerance_minutes": 0, "max_tracks": 2, "phases": [
                    {"phase": "dinner", "target_minutes": 3, "tolerance_minutes": 0, "energy_target": 0.2},
                    {"phase": "open_dancing", "target_minutes": 3, "tolerance_minutes": 0, "energy_target": 0.8},
                ]}
                request = urllib.request.Request(base + "/api/studio/plan",
                    data=json.dumps({"rule": {"clean_only": True, "limit": 4},
                                     "event": {}, "policy": policy}).encode(),
                    headers={"Content-Type": "application/json", "X-AutoCue-Token": session["token"]})
                with urllib.request.urlopen(request) as response:
                    plan = json.load(response)
                    assert plan["feasible"] and len(plan["alternatives"][0]["phases"]) == 2, plan
                    report = plan["alternatives"][0]["transition_evidence"]
                    assert report["status"] == "incomplete" and report["estimated_elapsed_s"] is None, report
                tracks = plan["alternatives"][0]["tracks"]
                policy["pinned"] = [{"position": index, "file_id": track["id"]}
                                    for index, track in enumerate(tracks)]
                playback = {
                    "windows": [{"file_id": track["id"], "content_hash": track["content_hash"],
                                 "entry_s": 10, "exit_s": 130, "reviewed": True} for track in tracks],
                    "overlaps": [{"from_id": tracks[0]["id"], "to_id": tracks[1]["id"], "seconds": 16}],
                }
                request = urllib.request.Request(base + "/api/studio/plan",
                    data=json.dumps({"rule": {"clean_only": True, "limit": 4}, "event": {},
                                     "policy": policy, "playback": playback}).encode(),
                    headers={"Content-Type": "application/json", "X-AutoCue-Token": session["token"]})
                with urllib.request.urlopen(request) as response:
                    report = json.load(response)["alternatives"][0]["transition_evidence"]
                    assert report["status"] == "estimated" and report["estimated_elapsed_s"] == 224, report
                def post(endpoint, body):
                    request = urllib.request.Request(base + "/api/studio/" + endpoint,
                        data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
                                                               "X-AutoCue-Token": session["token"]})
                    with urllib.request.urlopen(request) as response:
                        return json.load(response)
                for endpoint in ("interpret", "interpret/plan"):
                    try:
                        post(endpoint, {"message": "Plan 18 minutes of House songs released in 2007."})
                    except urllib.error.HTTPError as error:
                        with error:
                            assert error.code == 400
                            assert "No AI request was sent" in json.load(error)["error"]
                    else:
                        raise AssertionError("Installed Studio must reject release filters before inference.")
                with urllib.request.urlopen(base + "/api/studio/state") as response:
                    state = json.load(response)
                assert state["ingestion"]["counts"]["new"] == 12 and state["ai_settings"]["revision"] == 0
                item = state["ingestion"]["items"][0]
                review = {"dataset_id": session["dataset_id"], "request_id": "installed-inbox",
                          "file_id": item["file_id"], "base_revision": item["revision"], "status": "reviewed"}
                assert post("inbox/review", review) == post("inbox/review", review)
                batch = post("batch/preview", {"dataset_id": session["dataset_id"], "crates": [
                    {"name": "Installed clean", "rule": {"clean_only": True, "limit": 4}},
                    {"name": "Installed House", "rule": {"genre": "House", "limit": 3}}]})
                approval = {"dataset_id": session["dataset_id"], "request_id": "installed-batch",
                            "batch_id": batch["batch_id"]}
                receipt = post("batch/approve", approval)
                assert receipt == post("batch/approve", approval)
                assert len(receipt["crates"]) == 2 and receipt["entries"] == 7
                crate_id = receipt["crates"][0]["crate_id"]
                edit = post("edit/preview", {"dataset_id": session["dataset_id"], "operations": [
                    {"action": "rename", "crate_id": crate_id, "base_revision": 1, "name": "Installed reviewed rename"}]})
                edit_body = {"dataset_id": session["dataset_id"], "request_id": "installed-edit", "edit_id": edit["edit_id"]}
                approved = post("edit/approve", edit_body)
                assert approved == post("edit/approve", edit_body) and approved["crates"][0]["revision"] == 2
                undo_body = {**edit_body, "request_id": "installed-undo"}
                undone = post("edit/undo", undo_body)
                assert undone == post("edit/undo", undo_body) and undone["crates"][0]["revision"] == 3
                assert undone["crates"][0]["name"] == "Installed clean"
                assert post("assistant/load", {"conversation_id": "installed"})["revision"] == 0
                configured = post("ai/configure", {"dataset_id": session["dataset_id"], "request_id": "installed-model",
                                                  "base_revision": 0, "model": "qwen3:1.7b"})
                assert configured["model"] == "qwen3:1.7b"
                observed = post("history/record", {"request_id": "installed-play", "dataset_id": session["dataset_id"],
                    "file_id": item["file_id"], "played_at": "2026-11-12T12:00:00Z", "note": "Synthetic play"})
                history_edit = post("history/preview", {"dataset_id": session["dataset_id"],
                                                       "event_ids": [observed["request_id"]]})
                history_body = {"request_id": "installed-history-removal", "dataset_id": session["dataset_id"],
                                "edit_id": history_edit["edit_id"], "event_ids": history_edit["event_ids"]}
                removed = post("history/approve", history_body)
                assert removed == post("history/approve", history_body)
                backup = post("backup", {})
                assert backup["manifest"]["table_counts"]["personal_crates"] == 2
                backup_path = root / "backup.zip"
                backup_path.write_bytes(base64.b64decode(backup["bundle_base64"], validate=True))
                restored = root / "restored"
                for arguments in (("--restore", str(backup_path)), ("--check",)):
                    completed = subprocess.run([str(command), "--data", str(restored), *arguments],
                                               cwd=root, env=environment, capture_output=True, text=True, timeout=30)
                    if completed.returncode:
                        raise RuntimeError(completed.stderr or completed.stdout)
                with closing(sqlite3.connect(restored / "library.sqlite3")) as restored_db:
                    assert restored_db.execute("SELECT COUNT(*) FROM personal_crates").fetchone()[0] == 2
                    assert restored_db.execute("SELECT COUNT(*) FROM library_play_removals WHERE active=1").fetchone()[0] == 1
                history_undo_body = {**history_body, "request_id": "installed-history-undo"}
                assert post("history/undo", history_undo_body) == post("history/undo", history_undo_body)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    print("Installed Studio: search/planning, inbox, batch creation, edit/undo/replay, settings, UI, backup/restore and reviewed history recovery verified outside checkout.")


if __name__ == "__main__":
    main()
