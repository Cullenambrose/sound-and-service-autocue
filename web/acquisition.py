"""Synthetic metadata intake only; never opens audio or provider catalogs."""
from pathlib import PurePosixPath


PROGRESS = {"not_started": "Not started", "searching": "Searching",
            "acquired": "Acquired externally; not verified"}


def synthetic_track(value):
    if not isinstance(value, dict) or set(value) - {"id", "title", "artist", "path", "version"}:
        raise ValueError("Select one synthetic track object: id, title, artist, path, optional version.")
    for key in ("id", "title", "artist", "path"):
        field = value.get(key)
        if (not isinstance(field, str) or not field.strip() or len(field) > 500
                or any(ord(char) < 32 or ord(char) == 127 for char in field)):
            raise ValueError(f"Synthetic track {key} must be nonempty text without control characters.")
    path = value["path"]
    if (not path.startswith("/Demo/") or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/")[1:])
            or str(PurePosixPath(path)) != path):
        raise ValueError("Synthetic track paths must stay under /Demo/ without traversal.")
    if "version" in value and (
        not isinstance(value["version"], str) or len(value["version"]) > 100
        or any(ord(char) < 32 or ord(char) == 127 for char in value["version"])
    ):
        raise ValueError("Synthetic version must be text without control characters.")
    return dict(value)
