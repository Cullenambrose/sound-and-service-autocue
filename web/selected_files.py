"""Read-only verification of individually selected files; never enumerates folders."""
import hashlib
import math
from pathlib import Path
import stat

from mutagen import MutagenError

from . import catalog


def selected_path(value):
    if (not isinstance(value, str) or not value.startswith("/")
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "\\" in value or ".." in value.split("/")):
        raise ValueError("Select an absolute, single-line local path without traversal.")
    path = Path(value)
    if str(path) != value or path.resolve() != path:
        raise ValueError("Selected paths must be canonical and must not contain symlinks.")
    return path


def fingerprint(path):
    path = selected_path(str(path))
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Select an individual regular audio file, not a folder or device.")
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


class SelectedFiles:
    def __init__(self, manifest):
        if (not isinstance(manifest, dict)
                or set(manifest) != {"schema_version", "library_files", "acquisition_files"}
                or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1):
            raise ValueError("Selection requires schema_version 1, library_files and acquisition_files.")
        for field in ("library_files", "acquisition_files"):
            values = manifest[field]
            if not isinstance(values, list) or len(values) > 1000:
                raise ValueError("Select at most 1000 individual paths in each selection list.")
            for value in values:
                selected_path(value)
        paths = manifest["library_files"] + manifest["acquisition_files"]
        if not paths or len(paths) != len(set(paths)):
            raise ValueError("Select at least one path; duplicate selected paths are not allowed.")
        self.library_paths = tuple(manifest["library_files"])
        self.acquisition_paths = tuple(manifest["acquisition_files"])
        self.identity = catalog.digest(manifest)

    def inspect(self, value, *, acquired=False):
        allowed = self.acquisition_paths if acquired else self.library_paths
        if not isinstance(value, str) or value not in allowed:
            raise ValueError("File is not in the explicitly selected read-only list.")
        path = selected_path(value)
        if path.suffix.lower() not in catalog.AUDIO_EXTENSIONS:
            raise ValueError("Unsupported selected audio extension.")
        before = fingerprint(path)
        try:
            metadata = catalog.read_metadata(path)
        except MutagenError as error:
            raise ValueError("Could not read selected audio metadata: " + str(error)) from error
        for field in ("title", "artist", "version"):
            text = metadata[field]
            if (not isinstance(text, str) or len(text) > 4000
                    or any(ord(char) < 32 or ord(char) == 127 for char in text)
                    or (field != "version" and not text.strip())):
                raise ValueError(f"Selected file needs valid {field} tags; filenames are never metadata.")
        content_hash = catalog.file_hash(path)
        if before != fingerprint(path):
            raise ValueError("Selected file changed while being verified; select it again.")
        track = {key: metadata[key] for key in ("title", "artist", "version", "key", "duration_s")}
        bpm = metadata["bpm"]
        if isinstance(bpm, bool):
            raise ValueError("Selected file BPM tag must be a nonnegative finite number.")
        if bpm:
            try:
                number = float(bpm)
            except (TypeError, ValueError) as error:
                raise ValueError("Selected file BPM tag must be a nonnegative finite number.") from error
            if not math.isfinite(number) or number < 0:
                raise ValueError("Selected file BPM tag must be a nonnegative finite number.")
            track["bpm"] = number
        duration = metadata["duration_s"]
        if (duration is not None
                and (type(duration) not in (int, float)
                     or not math.isfinite(duration) or duration < 0)):
            raise ValueError("Selected file duration must be a nonnegative finite number or null.")
        track.update(id="selected:" + hashlib.sha256(value.encode()).hexdigest(),
                     path=value, verification={**before, "sha256": content_hash},
                     provenance="selected_file_tags")
        return track

    def validate(self, tracks, *, content=False):
        for track in tracks:
            path = track.get("path")
            acquired = path in self.acquisition_paths
            if path not in self.library_paths and not acquired:
                raise ValueError("Session contains an unselected file; use a new pilot session.")
            if content:
                if self.inspect(path, acquired=acquired) != track:
                    raise ValueError("Verified file changed; use a new pilot session and review it again.")
            else:
                expected = track.get("verification", {})
                actual = fingerprint(path)
                if actual != {key: expected.get(key) for key in actual}:
                    raise ValueError("Verified file changed or is unavailable; stop and review the selected file.")
