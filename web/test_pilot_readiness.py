"""Generated-only recovery, setup and local deployment acceptance."""
import copy
import base64
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from web import pilot_setup, pilot_storage
from web.pilot_preflight import check_runtime
from web.fixtures import tagged_wav
from web.read_only_prep import ReadOnlyPrepFlow
from web.setlist_flow import canonical


@pytest.fixture
def ready(tmp_path):
    media = tmp_path / "Generated.wav"
    media.write_bytes(tagged_wav())
    selection = {"schema_version": 1, "library_files": [str(media)], "acquisition_files": []}
    event = {"event_name": "Generated readiness"}
    flow = ReadOnlyPrepFlow(tmp_path / "06-session", selection=selection, event=event)
    return flow, selection, event, media


def command(flow, action, **fields):
    return flow.command({"request_id": f"ready-{flow.state['revision']}-{action}",
                         "base_revision": flow.state["revision"], "mode": "prep",
                         "action": action, **fields})


@pytest.mark.parametrize("stage", ["saved", "export", "session-before", "session-after"])
def test_interrupted_multi_artifact_operation_requires_explicit_recovery(ready, stage):
    flow, selection, event, media = ready
    command(flow, "save")
    command(flow, "export")
    original = pilot_storage.inventory(flow.directory)
    source = media.read_bytes()
    before = canonical(flow.state)
    writer = flow._write
    target = flow.export_path if stage == "export" else flow.saved_path if stage == "saved" else flow.state_path
    def interrupt(path, data):
        if path == target:
            if stage.endswith("after"):
                writer(path, data)
            raise OSError("generated interruption")
        writer(path, data)
    with patch.object(flow, "_write", side_effect=interrupt):
        with pytest.raises(OSError, match="interruption"):
            command(flow, "export" if stage == "export" else "save")
    # An interruption before the artifact writer starts has no partial writes.
    pending = (flow.directory / pilot_storage.JOURNAL).exists()
    if pending:
        with pytest.raises(ValueError, match="recover"):
            flow.view("prep")
        with pytest.raises(ValueError, match="recover"):
            ReadOnlyPrepFlow(flow.directory, selection=selection, event=event)
        pilot_storage.recover(flow.directory, pilot_storage.atomic_write)
    assert pilot_storage.inventory(flow.directory) == original
    restored = ReadOnlyPrepFlow(flow.directory, selection=selection, event=event)
    assert canonical(restored.state) == before
    assert restored.verify()["mismatches"] == 0
    assert media.read_bytes() == source


@pytest.mark.parametrize("point", ["fsync", "replace"])
def test_storage_atomic_failure_never_reports_success_or_leaves_scratch(tmp_path, point):
    target = tmp_path / "06-state.json"
    target.write_bytes(b"before")
    with patch("web.pilot_storage.os." + point, side_effect=OSError("generated disk failure")):
        with pytest.raises(OSError, match="disk failure"):
            pilot_storage.atomic_write(target, b"after")
    assert target.read_bytes() == b"before"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("boundary", [1, 2, 3])
def test_directory_fsync_failure_blocks_until_exact_prior_state_is_recovered(ready, boundary):
    flow, selection, event, _ = ready
    command(flow, "save")
    command(flow, "export")
    before = pilot_storage.inventory(flow.directory)
    sync = pilot_storage.directory_sync
    calls = 0
    def interrupt(directory):
        nonlocal calls
        calls += 1
        if calls == boundary:
            raise OSError("generated directory fsync failure")
        sync(directory)
    with patch("web.pilot_storage.directory_sync", side_effect=interrupt):
        with pytest.raises(OSError, match="fsync"):
            command(flow, "save")
    assert (flow.directory / pilot_storage.JOURNAL).exists()
    with pytest.raises(ValueError, match="recover"):
        flow.view("prep")
    pilot_storage.recover(flow.directory, pilot_storage.atomic_write)
    restored = ReadOnlyPrepFlow(flow.directory, selection=selection, event=event)
    assert pilot_storage.inventory(flow.directory) == before
    assert restored.verify()["mismatches"] == 0


def test_backup_restore_preserves_bytes_and_checks_real_selected_evidence(ready, tmp_path):
    flow, selection, event, media = ready
    command(flow, "save")
    command(flow, "export")
    bundle = tmp_path / "06-backup.json"
    pilot_storage.backup(flow, selection, event, bundle)
    document = json.loads(bundle.read_text())
    assert set(document["artifacts"]) <= set(pilot_storage.FILES)
    assert not any(name.endswith(".wav") for name in document["artifacts"])
    destination = tmp_path / "06-restored"
    pilot_storage.restore(bundle, destination)
    restored = ReadOnlyPrepFlow(destination, selection=selection, event=event)
    assert pilot_storage.inventory(destination) == pilot_storage.inventory(flow.directory)
    assert restored.verify()["mismatches"] == 0
    assert canonical(restored.state) == canonical(flow.state)
    with pytest.raises(ValueError, match="new output"):
        pilot_storage.restore(bundle, destination)
    media.unlink()
    with pytest.raises(OSError):
        pilot_storage.restore(bundle, tmp_path / "06-offline")
    assert not (tmp_path / "06-offline").exists()
    assert not list(tmp_path.glob("06-restore-*"))


@pytest.mark.parametrize("change", ["checksum", "traversal", "version", "incomplete", "unknown"])
def test_tampered_backup_never_activates(ready, tmp_path, change):
    flow, selection, event, _ = ready
    command(flow, "save")
    bundle = tmp_path / "06-backup.json"
    pilot_storage.backup(flow, selection, event, bundle)
    document = json.loads(bundle.read_text())
    if change == "checksum":
        document["artifacts"]["06-session.json"]["sha256"] = "0" * 64
    elif change == "traversal":
        document["artifacts"]["../outside"] = document["artifacts"]["06-session.json"]
    elif change == "version":
        document["schema_version"] = 99
    elif change == "incomplete":
        del document["artifacts"]["06-pilot-binding.json"]
    else:
        document["unknown"] = True
    bundle.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        pilot_storage.restore(bundle, tmp_path / "06-rejected")
    assert not (tmp_path / "06-rejected").exists()


@pytest.mark.parametrize("field,value", [
    ("revision", -1), ("revision", True), ("guests", [{}]), ("receipts", []),
    ("prep_mode", "unexpected"), ("saved", {"entries": [None], "decisions": {}}),
    ("prep_decisions", {"x": {"action": "unexpected"}}), ("library_additions", [{}]),
    ("serato_source", {"entries": [], "order": ["missing"]}),
])
def test_corrupt_session_is_explicit_and_untouched(ready, field, value):
    flow, selection, event, _ = ready
    state = copy.deepcopy(flow.state)
    state[field] = value
    data = json.dumps(state).encode()
    flow.state_path.write_bytes(data)
    with pytest.raises(ValueError):
        ReadOnlyPrepFlow(flow.directory, selection=selection, event=event)
    assert flow.state_path.read_bytes() == data


def test_lost_ack_retry_preserves_receipt_and_durable_state(ready):
    flow, selection, event, _ = ready
    body = {"request_id": "lost-ack", "base_revision": 0, "mode": "live",
            "action": "guest", "first_name": "Generated", "song": "Synthetic Song"}
    flow.command(body)
    restored = ReadOnlyPrepFlow(flow.directory, selection=selection, event=event)
    with patch.object(restored, "_write", side_effect=AssertionError("Retry must not save twice")):
        restored.command(body)
    assert len(restored.state["guests"]) == 1
    assert restored.state["revision"] == 1


def test_file_validation_is_reused_within_command_not_across_requests(ready):
    flow, _, _, media = ready
    original = flow.selection.validate
    with patch.object(flow.selection, "validate", wraps=original) as validate:
        command(flow, "guest", first_name="Generated", song="Synthetic Song", mode="live")
    # Empty nested validations are harmless; each file is validated only once.
    assert sum(len(call.args[0]) for call in validate.call_args_list) == 1
    media.write_bytes(tagged_wav("Dirty"))
    with pytest.raises(ValueError, match="changed"):
        flow.view("live")


@pytest.mark.parametrize("field", ["crate_base64", "database_base64"])
def test_full_pilot_snapshot_boundary_is_inclusive_and_preview_is_read_only(ready, field):
    from web import serato_import
    from web.read_only_prep import MAX_SNAPSHOT_BYTES
    from web.test_read_only_prep import upload
    flow, _, _, media = ready
    body = upload([media], [(media, "Synthetic Song", "Invented Artist")])
    raw = base64.b64decode(body[field])
    padding = b"x" * (MAX_SNAPSHOT_BYTES - len(raw) - 8)
    raw += b"xxxx" + struct.pack(">I", len(padding)) + padding
    assert len(raw) == MAX_SNAPSHOT_BYTES
    body[field] = base64.b64encode(raw).decode()
    before = pilot_storage.inventory(flow.directory)
    assert flow.serato_preview(body)["entries"][0]["status"] == "available"
    assert pilot_storage.inventory(flow.directory) == before
    with pytest.raises(ValueError, match="512 KiB"):
        serato_import.preview(body, flow.library)
    body[field] = base64.b64encode(raw + b"x").decode()
    with pytest.raises(ValueError, match="32768 KiB"):
        flow.serato_preview(body)
    assert pilot_storage.inventory(flow.directory) == before


def test_guided_setup_review_confirmation_resume_and_cancellation(ready, tmp_path):
    _, selection, event, media = ready
    output = tmp_path / "06-guided"
    answers = iter(["new", "", "Generated", "", "", str(media), "", "", str(output), "YES"])
    shown = []
    directory, document = pilot_setup.configure(tmp_path, ask=lambda _: next(answers), show=shown.append)
    assert directory == output and document["selection"] == selection
    assert document["event"]["event_name"] == "Generated"
    assert str(media) in "\n".join(shown)
    resumed = iter(["resume", str(output)])
    assert pilot_setup.configure(tmp_path, ask=lambda _: next(resumed), show=lambda _: None) == (directory, document)
    with pytest.raises(ValueError, match="cancelled"):
        pilot_setup.configure(tmp_path, ask=lambda _: ":cancel", show=lambda _: None)
    assert not (tmp_path / "06-cancelled").exists()


def test_setup_decline_and_existing_output_write_nothing(ready, tmp_path):
    _, _, _, media = ready
    output = tmp_path / "06-declined"
    answers = iter(["new", "", "Generated", "", "", str(media), "", "", str(output), "NO"])
    with pytest.raises(ValueError, match="cancelled"):
        pilot_setup.configure(tmp_path, ask=lambda _: next(answers), show=lambda _: None)
    assert not output.exists()


def test_launcher_and_cli_errors_are_explicit_and_default_launcher_unchanged():
    result = subprocess.run(["sh", str(ROOT / "run-read-only.sh"), "--help"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0 and "--setup" in result.stdout and "--recover" in result.stdout
    result = subprocess.run([sys.executable, "-m", "web.read_only_prep"], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode != 0 and "--setup" in result.stderr
    for port in ("-1", "65536"):
        result = subprocess.run([sys.executable, "-m", "web.read_only_prep", "--port", port],
                                capture_output=True, text=True, cwd=ROOT)
        assert result.returncode == 1 and "Port must be between" in result.stderr
        assert "Traceback" not in result.stderr


def test_dependency_preflight_never_installs_or_silently_substitutes():
    with pytest.raises(ValueError, match="3.10"):
        check_runtime(version=(3, 9), find_spec=lambda _: object())
    with pytest.raises(ValueError, match="Missing mutagen"):
        check_runtime(version=(3, 10), find_spec=lambda _: None)
    check_runtime(version=(3, 10), find_spec=lambda _: object())


@pytest.mark.parametrize("busy", [True, False])
def test_preflight_rejects_live_listener_but_accepts_recently_closed_http_port(ready, monkeypatch, tmp_path, busy):
    from web import read_only_prep
    flow, selection, event, _ = ready
    (flow.directory / pilot_setup.CONFIG).write_text(json.dumps({
        "schema_version": 1, "selection": selection, "event": event}))
    monkeypatch.setattr(read_only_prep, "OUTPUTS", tmp_path)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(sys, "argv", ["pilot", "--resume", str(flow.directory),
                                         "--check", "--port", str(port)])
        if busy:
            with pytest.raises(SystemExit) as error:
                read_only_prep.main()
            assert error.value.code == 1
            return
        with socket.create_connection(("127.0.0.1", port)) as client:
            accepted, _ = listener.accept()
            accepted.close()
            assert client.recv(1) == b""
    read_only_prep.main()


@pytest.mark.parametrize("change", ["missing", "unknown"])
def test_unsupported_session_fields_fail_explicitly_without_reset(ready, change):
    flow, selection, event, _ = ready
    state = copy.deepcopy(flow.state)
    if change == "missing":
        del state["saved"]
    else:
        state["future_version"] = 2
    data = json.dumps(state).encode()
    flow.state_path.write_bytes(data)
    with pytest.raises(ValueError, match="Unsupported"):
        ReadOnlyPrepFlow(flow.directory, selection=selection, event=event)
    assert flow.state_path.read_bytes() == data


def test_setup_rejects_bad_mode_and_unsupported_configuration(tmp_path):
    with pytest.raises(ValueError, match="Choose new or resume"):
        pilot_setup.configure(tmp_path, ask=lambda _: "maybe", show=lambda _: None)
    directory = tmp_path / "06-future"
    directory.mkdir()
    config = directory / pilot_setup.CONFIG
    config.write_text('{"schema_version":99}')
    original = config.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        pilot_setup.load_configuration(directory)
    assert config.read_bytes() == original


def test_recovery_rejects_corrupt_journal_and_symlinks_without_touching_artifacts(ready):
    flow, _, _, media = ready
    before = pilot_storage.inventory(flow.directory)
    journal = flow.directory / pilot_storage.JOURNAL
    journal.write_text('{"schema_version":1,"before":{"../escape":{}}}')
    with pytest.raises(ValueError, match="unknown"):
        pilot_storage.recover(flow.directory, pilot_storage.atomic_write)
    assert pilot_storage.inventory(flow.directory) == before
    assert journal.exists()
    journal.unlink()
    journal.symlink_to(media)
    with pytest.raises(ValueError, match="non-symlink"):
        pilot_storage.recover(flow.directory, pilot_storage.atomic_write)


def test_interrupted_recovery_can_be_repeated(ready):
    flow, _, _, _ = ready
    command(flow, "save")
    before = pilot_storage.inventory(flow.directory)
    journal = flow.directory / pilot_storage.JOURNAL
    journal.write_text(json.dumps({"schema_version":1,"before":before}))
    def failed_write(*_):
        raise OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        pilot_storage.recover(flow.directory, failed_write)
    assert journal.exists()
    pilot_storage.recover(flow.directory, pilot_storage.atomic_write)
    assert pilot_storage.inventory(flow.directory) == before
