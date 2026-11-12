"""Terminal setup for exact selected inputs; no folder discovery or GUI dependencies."""
import json
from pathlib import Path

from . import pilot_storage
from .selected_files import SelectedFiles

CONFIG = "06-pilot-launch.json"


def output_directory(value, outputs):
    directory = Path(value).expanduser().absolute()
    if (directory.parent.resolve() != outputs.resolve() or not directory.name.startswith("06-")
            or directory.is_symlink() or directory.resolve() != directory):
        raise ValueError("Choose a non-symlink 06-* directory directly under ~/Desktop/MASTER-TODO/outputs.")
    return directory


def check_event(event):
    if not isinstance(event, dict):
        raise ValueError("Event must be a JSON object.")
    if not isinstance(event.get("event_name", ""), str):
        raise ValueError("Event name must be text.")
    from setlist import context_from_event
    try:
        context_from_event(event)
    except (TypeError, AttributeError, KeyError) as error:
        raise ValueError("Event does not follow the supported event schema.") from error


def configure(outputs, *, ask=input, show=print):
    show("Read-only Prep setup. Enter exact files, never folders. Type :cancel at any prompt to stop.")

    def prompt(label):
        value = ask(label).strip()
        if value == ":cancel":
            raise ValueError("Setup cancelled; no configuration was written and no server was started.")
        return value

    mode = prompt("New session or resume? [new/resume]: ").lower()
    if mode not in {"new", "resume"}:
        raise ValueError("Choose new or resume explicitly.")
    if mode == "resume":
        directory = output_directory(prompt("Existing 06-* output directory: "), outputs)
        document = load_configuration(directory)
        show("Resume selected: " + str(directory))
        return directory, document
    event_path = prompt("Existing event JSON path (blank to create an event): ")
    if event_path:
        event = json.loads(Path(event_path).expanduser().read_text())
    else:
        event = {"event_name": prompt("Event name: "), "music_lists": {"must_play": [], "do_not_play": []}}
        for category in ("must_play", "do_not_play"):
            show("Enter " + category.replace("_", " ") + " songs; blank title finishes.")
            while True:
                title = prompt("Song title: ")
                if not title:
                    break
                artist = prompt("Artist (blank if unknown): ")
                version = prompt("Version (blank if unspecified): ")
                event["music_lists"][category].append({"title": title, "artist": artist, "version": version})
    check_event(event)
    selection = {"schema_version": 1, "library_files": [], "acquisition_files": []}
    for field in ("library_files", "acquisition_files"):
        show("Enter exact " + field.replace("_", " ") + " paths, one per line; blank finishes.")
        while True:
            path = prompt("Absolute file path: ")
            if not path:
                break
            selection[field].append(path)
    selected = SelectedFiles(selection)
    for path in selected.library_paths:
        selected.inspect(path)
    directory = output_directory(prompt("New 06-* output directory: "), outputs)
    if directory.exists():
        raise ValueError("New setup must use a new directory. Choose resume for an existing session.")
    for path in selected.library_paths + selected.acquisition_paths:
        if Path(path).is_relative_to(directory):
            raise ValueError("Selected source files must stay outside the pilot output directory.")
    document = {"schema_version": 1, "selection": selection, "event": event}
    show(json.dumps({"output": str(directory), **document}, indent=2))
    if prompt("Write this configuration and launch? Type YES: ") != "YES":
        raise ValueError("Setup cancelled; no configuration was written and no server was started.")
    directory.mkdir()
    pilot_storage.atomic_write(directory / CONFIG, (json.dumps(document, indent=2) + "\n").encode())
    return directory, document


def load_configuration(directory):
    document = pilot_storage.read_document(directory / CONFIG)
    if set(document) != {"schema_version", "selection", "event"}:
        raise ValueError("Unsupported pilot launch configuration.")
    SelectedFiles(document["selection"])
    check_event(document["event"])
    return document
