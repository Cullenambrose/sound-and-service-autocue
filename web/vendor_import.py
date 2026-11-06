"""Read-only adapter for the manifest-backed Crate Hackers CSV export."""
import argparse
import csv
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import warnings

from . import catalog

HEADER = ["#", "Title", "Artist", "BPM", "Key", "Danceability", "Energy", "Mood"]
REFERENCE = "Crate Hackers Reference"


def read_source(root, relative):
    for attempt in range(3):
        try:
            return _read_source(root, relative)
        except OSError as error:
            if error.errno != errno.ETIMEDOUT or attempt == 2:
                raise OSError(error.errno, f"Could not read declared source {relative}: {error.strerror}") from error
            warnings.warn(f"Timed out reading {relative}; retrying read-only ({attempt + 1}/2).")


def _read_source(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Export filenames must be nonempty relative paths.")
    path = root / relative
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or any(parent.is_symlink() for parent in
                                              [path, *path.parents] if parent != root.parent):
        raise ValueError("Export paths must remain inside the selected folder without symlinks.")
    before = path.stat()
    data = path.read_bytes()
    if catalog.signature(before) != catalog.signature(path.stat()):
        # Cloud-backed files can materialize on first read. Accept only an
        # identical second read whose own stat signature remains stable.
        before = path.stat()
        repeated = path.read_bytes()
        if repeated != data or catalog.signature(before) != catalog.signature(path.stat()):
            raise ValueError("Export changed while being read: " + relative)
        warnings.warn("Source stat changed on first read; identical stable reread verified: " + relative)
    return data


def number(value, field):
    if value == "":
        return None
    try:
        result = float(value)
    except ValueError as error:
        raise ValueError(f"{field} must be a nonnegative finite number or empty.") from error
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a nonnegative finite number or empty.")
    return result


def parse_csv(data, filename):
    import io
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True))
    headers = [index for index, row in enumerate(rows) if row == HEADER]
    if len(headers) != 1:
        raise ValueError(filename + ": expected exactly one supported track header.")
    start = headers[0]
    songs = []
    for line, row in enumerate(rows[start + 1:], start + 2):
        if not any(row):
            continue
        if len(row) != len(HEADER) or row[0] != str(len(songs) + 1):
            raise ValueError(f"{filename}:{line}: invalid columns or nonsequential track order.")
        raw = dict(zip(HEADER[1:], row[1:]))
        catalog.text(raw["Title"], "Title")
        catalog.text(raw["Artist"], "Artist")
        attributes = {field.lower(): number(raw[field], field)
                      for field in ("BPM", "Danceability", "Energy", "Mood")}
        # This CSV has no vendor song ID. Hash exact row metadata, excluding
        # membership order; never claim it identifies a recording or local file.
        unknown = [field for field in ("Artist", "Title") if not raw[field].strip()]
        songs.append({"id": "csv-row-" + catalog.digest(raw), "artist": raw["Artist"],
                      "title": raw["Title"], "version": "", "key": raw["Key"],
                      **attributes, "source_fields": raw,
                      "source_warnings": [f"Source {field.lower()} is missing; no automatic tag agreement."
                                          for field in unknown]})
    return songs, rows[:start]


def convert(directory, progress=None):
    requested = Path(directory).expanduser()
    if not requested.is_absolute() or requested.is_symlink():
        raise ValueError("Select an absolute nonsymlink export folder.")
    root = requested.resolve(strict=True)
    sources = {}

    def read(relative):
        data = read_source(root, relative)
        sources[relative] = hashlib.sha256(data).hexdigest()
        return data

    reference = json.loads(read(REFERENCE + "/Crate catalog reference.json"))
    manifest = json.loads(read(REFERENCE + "/Crate export manifest.json"))
    if not isinstance(reference, list) or not reference or not isinstance(manifest, list):
        raise ValueError("Catalog reference and manifest must be nonempty arrays.")
    by_id = {}
    for item in reference:
        if not isinstance(item, dict):
            raise ValueError("Invalid catalog reference entry.")
        key = catalog.text(item.get("_id"), "reference ID", required=True)
        if key in by_id:
            raise ValueError("Duplicate reference ID: " + key)
        by_id[key] = item
    crates, seen, filenames = [], set(), set()
    for item in manifest:
        if not isinstance(item, dict):
            raise ValueError("Invalid export manifest entry.")
        key = catalog.text(item.get("id"), "manifest ID", required=True)
        if key in seen or key not in by_id:
            raise ValueError("Duplicate or unknown manifest ID: " + key)
        seen.add(key)
        if progress:
            progress(len(seen), len(manifest), key)
        source = by_id[key]
        if item.get("title") != source.get("Title"):
            raise ValueError("Manifest/reference title mismatch for " + key)
        status = item.get("status")
        if status not in ("saved", "failed"):
            raise ValueError("Unsupported export status for " + key)
        crate = {"id": key, "title": source["Title"], "available": status == "saved",
                 "songs": [], "source_reference": source, "export_record": item}
        if status == "saved":
            filename = item.get("file")
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".csv"):
                raise ValueError("Manifest must name a direct CSV file for " + key)
            if filename in filenames:
                raise ValueError("Multiple manifest entries reference one CSV.")
            filenames.add(filename)
            try:
                songs, preamble = parse_csv(read(filename), filename)
            except (UnicodeError, csv.Error, ValueError) as error:
                raise ValueError(f"Crate {key}: {error}") from error
            if type(item.get("tracks")) is not int or len(songs) != item["tracks"]:
                raise ValueError("CSV/manifest membership count mismatch for " + key)
            crate.update(songs=songs, source_preamble=preamble)
        else:
            if not isinstance(item.get("error"), str) or not item["error"].strip():
                raise ValueError("Unavailable crate requires an explicit source error: " + key)
        crates.append(crate)
    if seen != set(by_id):
        raise ValueError("Manifest does not account for every catalog reference entry.")
    document = {"schema_version": 1, "source": "crate-hackers-manifest-csv",
                "crates": crates, "source_sha256": sources}
    songs = catalog.validate_snapshot(document)
    report = {"schema_version": 1, "snapshot_id": catalog.digest(document),
              "catalog_entries": len(crates), "available": sum(c["available"] for c in crates),
              "unavailable": [{"id": c["id"], "title": c["title"],
                               "reason": c["export_record"]["error"]} for c in crates if not c["available"]],
              "memberships": sum(len(c["songs"]) for c in crates), "distinct_row_metadata": len(songs),
              "declared_csv_files": len(filenames),
              "memberships_missing_artist": sum(not song["artist"].strip() for c in crates for song in c["songs"]),
              "memberships_missing_title": sum(not song["title"].strip() for c in crates for song in c["songs"]),
              "unreferenced_csv_files": len(list(root.glob("*.csv"))) - len(filenames)}
    return document, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, help="Explicit read-only export collection folder")
    parser.add_argument("--output", required=True, help="New schema-1 snapshot JSON path; never overwritten")
    args = parser.parse_args()
    output = Path(args.output).expanduser().absolute()
    temporary = None
    try:
        def progress(current, total, key):
            if current == 1 or current % 100 == 0 or current == total:
                print(f"Reading declared crate {current}/{total} ({key})", file=sys.stderr, flush=True)
        document, report = convert(args.directory, progress=progress)
        source = Path(args.directory).expanduser().resolve(strict=True)
        if output.resolve().is_relative_to(source):
            raise ValueError("Output must be outside the read-only export collection.")
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((catalog.canonical(document) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, "Catalog conversion failed; no snapshot published: " + str(error) + "\n")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
