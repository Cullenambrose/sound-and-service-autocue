"""Opt-in loopback Prep pilot for explicitly selected files; no scans or source writes."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(ROOT / "engine"))

from . import catalog, serato_import
from .selected_files import SelectedFiles
from .setlist_demo import OUTPUTS, demo_server
from .setlist_flow import SetlistFlow, canonical, digest

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


class ReadOnlyPrepFlow(SetlistFlow):
    synthetic = False
    max_snapshot_bytes = MAX_SNAPSHOT_BYTES

    def __init__(self, directory, *, selection, event):
        self.selection = SelectedFiles(selection)
        directory = Path(directory)
        if (directory.is_symlink() or directory.resolve() != directory.absolute()
                or any((directory / name).is_symlink() for name in (
                    "06-session.json", "06-prep-saved.json", "06-prep-export.m3u8", "06-pilot-binding.json"))):
            raise ValueError("Pilot output and session artifacts must not use symlinks.")
        binding_path = directory / "06-pilot-binding.json"
        tracks = [self.selection.inspect(path) for path in self.selection.library_paths]
        binding = {"schema_version": 1, "selection": self.selection.identity,
                   "event": catalog.digest(event), "library": catalog.digest(tracks)}
        if binding_path.exists():
            if json.loads(binding_path.read_text()) != binding:
                raise ValueError("Pilot selection, event or verified library changed. Use a new output directory; do not overwrite the old session.")
        elif any((directory / name).exists() for name in (
                "06-session.json", "06-prep-saved.json", "06-prep-export.m3u8")):
            raise ValueError("Existing artifacts have no pilot binding. Use a new output directory.")
        super().__init__(directory, library=tracks, event=event, output_prefix="06")
        self.selection.validate(self.state["library_additions"], content=True)
        if not binding_path.exists():
            self._write(binding_path, (canonical(binding) + "\n").encode())

    def session_info(self):
        return {"synthetic": False, "read_only_pilot": True,
                "max_snapshot_bytes": self.max_snapshot_bytes,
                "acquisition_paths": list(self.selection.acquisition_paths)}

    def _validate_files(self):
        self.selection.validate(self.library + self.state["library_additions"])

    def _view(self, mode, state):
        self.selection.validate(self.library + state["library_additions"])
        return super()._view(mode, state)

    def view(self, mode):
        with self.lock:
            self._validate_files()
            return super().view(mode)

    def command(self, body):
        with self.lock:
            self._validate_files()
            if isinstance(body, dict) and body.get("action") == "portable_import":
                raise ValueError("Portable synthetic intake is disabled in the read-only pilot. Use selected Serato snapshots.")
            return super().command(body)

    def serato_preview(self, body):
        with self.lock:
            self._validate_files()
            return serato_import.preview(body, self._library_snapshot(self.state)[0],
                                         synthetic=False, max_bytes=self.max_snapshot_bytes)

    def _acquisition_track(self, value):
        if not isinstance(value, dict) or set(value) != {"path"}:
            raise ValueError("Choose only a selected acquired-file path; metadata must come from its tags.")
        return self.selection.inspect(value["path"], acquired=True)

    def acquisition_preview(self, body):
        with self.lock:
            self._validate_files()
            result = super().acquisition_preview(body)
            result["id"] = digest(canonical({"preview": result["id"], "track": result["track"]}).encode())
            result["note_scope"] = (
                "Read-only file tags, content hash and availability verified. "
                "The shared engine decides song/version resolution; this does not certify licensing or audio quality. Saved Prep stays unchanged.")
            return result

    def verify(self, state=None):
        with self.lock:
            self._validate_files()
            return super().verify(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True,
                        help="JSON manifest listing exact library_files and acquisition_files, not folders.")
    parser.add_argument("--event", type=Path, required=True, help="Selected event JSON; no default demo event.")
    parser.add_argument("--output", type=Path, required=True, help="New or compatible 06-* output directory.")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--check", action="store_true", help="Verify selected inputs/session and exit without starting HTTP.")
    args = parser.parse_args()
    directory = args.output.expanduser().absolute()
    if (directory.parent.resolve() != OUTPUTS.resolve() or not directory.name.startswith("06-")
            or directory.is_symlink()):
        parser.error("Choose a non-symlink 06-* directory directly under ~/Desktop/MASTER-TODO/outputs.")
    try:
        selection = json.loads(args.selection.read_text())
        event = json.loads(args.event.read_text())
        if not isinstance(event, dict):
            raise ValueError("Selected event must be a JSON object.")
        flow = ReadOnlyPrepFlow(directory, selection=selection, event=event)
        flow.view("prep")
        flow.view("live")
    except (ValueError, OSError) as error:
        parser.exit(1, "Read-only Prep could not start: " + str(error) + "\n")
    if args.check:
        print("Read-only selection and durable session verified. No server started.")
        return
    server = demo_server(flow, args.port)
    print(f"Read-only Prep pilot: http://127.0.0.1:{server.server_port} (selected files only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
