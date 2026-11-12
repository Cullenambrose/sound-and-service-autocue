"""Opt-in loopback Prep pilot for explicitly selected files; no scans or source writes."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(ROOT / "engine"))

from . import catalog, serato_import, pilot_storage, pilot_setup
from .selected_files import SelectedFiles
from .setlist_demo import OUTPUTS, demo_server
from .setlist_flow import SetlistFlow, canonical, digest

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


class ReadOnlyPrepFlow(SetlistFlow):
    synthetic = False
    max_snapshot_bytes = MAX_SNAPSHOT_BYTES

    def __init__(self, directory, *, selection, event):
        self._validation_depth = 0
        self._checked_files = set()
        self.selection = SelectedFiles(selection)
        directory = Path(directory)
        pilot_setup.check_event(event)
        if any(Path(path).is_relative_to(directory) for path in
               self.selection.library_paths + self.selection.acquisition_paths):
            raise ValueError("Selected source files must stay outside the pilot output directory.")
        if (directory.is_symlink() or directory.resolve() != directory.absolute()
                or any((directory / name).is_symlink() for name in (
                    *pilot_storage.FILES, pilot_storage.JOURNAL))):
            raise ValueError("Pilot output and session artifacts must not use symlinks.")
        binding_path = directory / "06-pilot-binding.json"
        if (directory / pilot_storage.JOURNAL).exists():
            raise ValueError("An interrupted save/export needs explicit --recover before resuming; artifacts were not reset.")
        tracks = [self.selection.inspect(path) for path in self.selection.library_paths]
        binding = {"schema_version": 1, "selection": self.selection.identity,
                   "event": catalog.digest(event), "library": catalog.digest(tracks)}
        if binding_path.exists():
            if json.loads(binding_path.read_text()) != binding:
                raise ValueError("Pilot selection, event or verified library changed. Use a new output directory; do not overwrite the old session.")
        elif any((directory / name).exists() for name in (
                "06-session.json", "06-prep-saved.json", "06-prep-export.m3u8")):
            raise ValueError("Existing artifacts have no pilot binding. Use a new output directory.")
        if (directory / "06-session.json").exists():
            pilot_storage.validate_session(json.loads((directory / "06-session.json").read_text()))
        super().__init__(directory, library=tracks, event=event, output_prefix="06")
        pilot_storage.validate_session(self.state)
        self.selection.validate(self.state["library_additions"], content=True)
        if not binding_path.exists():
            self._write(binding_path, (canonical(binding) + "\n").encode())

    def session_info(self):
        return {"synthetic": False, "read_only_pilot": True,
                "max_snapshot_bytes": self.max_snapshot_bytes,
                "acquisition_paths": list(self.selection.acquisition_paths)}

    def _validate_files(self):
        self._validate_tracks(self.library + self.state["library_additions"])

    def _validate_tracks(self, tracks):
        pending = []
        for track in tracks:
            key = (track["path"], canonical(track.get("verification")))
            if not self._validation_depth or key not in self._checked_files:
                pending.append(track)
        self.selection.validate(pending)
        if self._validation_depth:
            self._checked_files.update((track["path"], canonical(track.get("verification"))) for track in pending)

    @contextmanager
    def _operation(self):
        with self.lock:
            if not self._validation_depth:
                if (self.directory / pilot_storage.JOURNAL).exists():
                    raise ValueError("Interrupted save/export is pending. Stop and use --recover.")
                self._checked_files.clear()
            self._validation_depth += 1
            try:
                yield
            finally:
                self._validation_depth -= 1
                if not self._validation_depth:
                    self._checked_files.clear()

    def _view(self, mode, state):
        self._validate_tracks(self.library + state["library_additions"])
        return super()._view(mode, state)

    def view(self, mode):
        with self._operation():
            self._validate_files()
            return super().view(mode)

    def command(self, body):
        with self._operation():
            self._validate_files()
            if isinstance(body, dict) and body.get("action") == "portable_import":
                raise ValueError("Portable synthetic intake is disabled in the read-only pilot. Use selected Serato snapshots.")
            journal = self.directory / pilot_storage.JOURNAL
            if journal.exists():
                raise ValueError("Interrupted save/export is pending. Stop and use --recover.")
            transactional = isinstance(body, dict) and body.get("action") in {"save", "export"}
            result = super().command(body)
            if transactional and journal.exists():
                journal.unlink()
                pilot_storage.directory_sync(self.directory)
            return result

    def _write(self, path, data):
        if path.is_symlink():
            raise ValueError("Pilot writes must not replace symlink artifacts.")
        if path in {self.saved_path, self.export_path}:
            journal = self.directory / pilot_storage.JOURNAL
            if not journal.exists():
                before = {"schema_version": 1, "before": pilot_storage.inventory(self.directory)}
                super()._write(journal, (canonical(before) + "\n").encode())
                pilot_storage.directory_sync(self.directory)
        super()._write(path, data)
        pilot_storage.directory_sync(self.directory)

    def serato_preview(self, body):
        with self._operation():
            self._validate_files()
            return serato_import.preview(body, self._library_snapshot(self.state)[0],
                                         synthetic=False, max_bytes=self.max_snapshot_bytes)

    def _acquisition_track(self, value):
        if not isinstance(value, dict) or set(value) != {"path"}:
            raise ValueError("Choose only a selected acquired-file path; metadata must come from its tags.")
        return self.selection.inspect(value["path"], acquired=True)

    def acquisition_preview(self, body):
        with self._operation():
            self._validate_files()
            result = super().acquisition_preview(body)
            result["id"] = digest(canonical({"preview": result["id"], "track": result["track"]}).encode())
            result["note_scope"] = (
                "Read-only file tags, content hash and availability verified. "
                "The shared engine decides song/version resolution; this does not certify licensing or audio quality. Saved Prep stays unchanged.")
            return result

    def verify(self, state=None):
        with self._operation():
            self._validate_files()
            return super().verify(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path,
                        help="JSON manifest listing exact library_files and acquisition_files, not folders.")
    parser.add_argument("--event", type=Path, help="Selected event JSON; no default demo event.")
    parser.add_argument("--output", type=Path, help="New or compatible 06-* output directory.")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--check", action="store_true", help="Verify selected inputs/session and exit without starting HTTP.")
    parser.add_argument("--setup", action="store_true", help="Guided exact-path setup; no handwritten manifest required.")
    parser.add_argument("--resume", type=Path, help="Resume a guided session from its output directory.")
    parser.add_argument("--recover", action="store_true", help="Explicitly restore pre-operation state from an interrupted save/export.")
    parser.add_argument("--backup", type=Path, help="Write a new checksummed app-artifact JSON bundle and exit.")
    parser.add_argument("--restore", type=Path, help="Verify and restore a bundle into a new --output directory, then exit.")
    args = parser.parse_args()
    try:
        if not 0 <= args.port <= 65535:
            raise ValueError("Port must be between 0 and 65535; 0 selects a temporary port.")
        if sum(bool(value) for value in (args.setup, args.resume, args.restore)) > 1:
            raise ValueError("Choose setup, resume or restore, not multiple modes.")
        if (args.setup or args.resume) and (args.selection or args.event or args.output):
            raise ValueError("Setup/resume supplies its configuration; do not combine it with explicit input flags.")
        if args.restore and (args.selection or args.event or args.recover or args.backup):
            raise ValueError("Restore uses the bundle's inputs; do not combine restore with other mutations.")
        if args.setup:
            directory, document = pilot_setup.configure(OUTPUTS)
            selection, event = document["selection"], document["event"]
        elif args.resume:
            directory = pilot_setup.output_directory(args.resume, OUTPUTS)
            document = pilot_setup.load_configuration(directory)
            selection, event = document["selection"], document["event"]
        else:
            if not args.output:
                raise ValueError("Choose --setup, --resume, or provide --output with --selection and --event.")
            directory = pilot_setup.output_directory(args.output, OUTPUTS)
            if args.restore:
                pilot_storage.restore(args.restore, directory)
                print("Verified backup restored to " + str(directory) + ". No server started.")
                return
            if not args.selection or not args.event:
                raise ValueError("Explicit launch requires --selection and --event.")
            selection = json.loads(args.selection.read_text())
            event = json.loads(args.event.read_text())
        if args.recover:
            pilot_storage.recover(directory, pilot_storage.atomic_write)
        flow = ReadOnlyPrepFlow(directory, selection=selection, event=event)
        flow.view("prep")
        flow.view("live")
        if args.backup:
            target = args.backup.expanduser().absolute()
            if target.parent.resolve() != OUTPUTS.resolve() or not target.name.startswith("06-"):
                raise ValueError("Backup must be a new 06-* file directly under outputs.")
            pilot_storage.backup(flow, selection, event, target)
            print("Verified app-artifact backup saved. No source media included. No server started.")
            return
    except (ValueError, OSError, EOFError) as error:
        parser.exit(1, "Read-only Prep could not start: " + str(error) + "\n")
    if args.check:
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", args.port))
        except OSError as error:
            parser.exit(1, "Pilot preflight port check failed: " + str(error) + "\n")
        print("Read-only selection and durable session verified. No server started.")
        return
    try:
        server = demo_server(flow, args.port)
    except OSError as error:
        parser.exit(1, "Could not bind the loopback pilot port: " + str(error) + "\n")
    print(f"Read-only Prep pilot: http://127.0.0.1:{server.server_port} (selected files only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
