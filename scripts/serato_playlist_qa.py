#!/usr/bin/env python3
"""Generate a copy-only Serato playlist acceptance package; never read library music."""
import argparse
import base64
import json
from pathlib import Path
import shutil
import sys
import zipfile
import io

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

from scripts import cue_loop_fixture
from scripts.apply_single_track_qa import Refused, check_serato_closed
import serato_writer
from web import server


def create(directory):
    check_serato_closed()
    if directory.exists() or directory.is_symlink():
        raise Refused("Choose a new test directory; existing files are never overwritten.")
    directory.mkdir(parents=True)
    directory = directory.resolve(strict=True)
    original = cue_loop_fixture.create(directory / "generated")
    backup = directory / "backup"
    backup.mkdir()
    for path in (original, original.parent / cue_loop_fixture.MANIFEST):
        shutil.copyfile(path, backup / path.name)
        if serato_writer.file_sha256(path) != serato_writer.file_sha256(backup / path.name):
            raise Refused("Synthetic baseline backup failed verification.")
    music = directory / "playback-copies"
    music.mkdir()
    copies = [music / "01 - Caf\u00e9 QA.mp3", music / '02 - Quoted, "QA".mp3']
    (backup / "playback-copies").mkdir()
    for path in copies:
        shutil.copyfile(original, path)
        shutil.copyfile(path, backup / "playback-copies" / path.name)
        if serato_writer.file_sha256(path) != serato_writer.file_sha256(original):
            raise Refused("Synthetic playback copy failed verification.")
    server.DATA = directory / "app-data"
    server.UPLOADS = server.DATA / "audio"
    server.DB_PATH = server.DATA / "library.sqlite3"
    server.initialize()
    songs = [{"id": f"synthetic-{index}", "artist": "AutoCue Test Fixture",
              "title": "AutoCue Synthetic Cue Loop QA", "version": ""} for index in range(2)]
    with server.connect() as db:
        imported = server.catalog.import_snapshot(db, {"schema_version": 1, "source": "synthetic-serato-qa",
            "crates": [{"id": "synthetic", "title": "Synthetic only", "available": True, "songs": songs}]})
    with server.connect() as db:
        server.catalog.scan_folder(db, str(music))
    with server.connect() as db:
        dataset = server.review_store.dataset_id(db)
        files = list(db.execute("SELECT * FROM library_files ORDER BY relative_path"))
    for index, file in enumerate(files):
        with server.connect() as db:
            server.catalog.decide(db, {"dataset_id": dataset, "request_id": f"qa-match-{index}",
                "snapshot_id": imported["snapshot_id"], "song_id": songs[index]["id"], "base_revision": 0,
                "file_id": file["id"], "content_hash": file["content_hash"],
                "note": "Generated copy byte identity checked; not musical matching evidence."})
    with server.connect() as db:
        server.personal_crates.save(db, {"dataset_id": dataset, "request_id": "qa-crate", "crate_id": "qa",
            "base_revision": 0, "name": "AutoCue Serato 5 synthetic playlist QA",
            "entries": [{"id": f"entry-{position}", "snapshot_id": imported["snapshot_id"],
                         "song_id": songs[index]["id"]} for position, index in enumerate((0, 1, 0))]}, dataset)
    request = {"dataset_id": dataset, "crate_id": "qa", "base_revision": 1}
    preview = server.playlist_export.prepare(server.connect, server.DATA / "exports", request)
    exported = server.playlist_export.prepare(server.connect, server.DATA / "exports",
        {**request, "preview_id": preview["preview_id"]}, download=True)
    playlist = directory / "playlist"
    playlist.mkdir()
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported["bundle_base64"]))) as archive:
        for name in ("playlist.m3u8", "playlist.csv", "manifest.json"):
            with (playlist / name).open("xb") as stream:
                stream.write(archive.read(name))
    record = {"schema_version": 1, "origin": "original generated percussion only",
              "expected_order": [str(copies[index]) for index in (0, 1, 0)],
              "original": str(original.relative_to(directory)),
              "original_sha256": serato_writer.file_sha256(original),
              "audio_sha256": serato_writer.mp3_audio_sha256(original),
              "export_id": preview["preview_id"], "serato_acceptance": "not yet performed"}
    with (directory / "acceptance.json").open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return playlist / "playlist.m3u8"


def capture(directory, version, order_ok, playback_ok, duplicates_ok):
    check_serato_closed()
    if directory.is_symlink():
        raise Refused("Test package symlinks are not supported.")
    directory = directory.resolve(strict=True)
    record = json.loads((directory / "acceptance.json").read_text())
    if record.get("schema_version") != 1 or record.get("origin") != "original generated percussion only":
        raise Refused("Not a recognized generated acceptance package.")
    if record.get("original") != "generated/" + cue_loop_fixture.NAME:
        raise Refused("Original path is not the generated test file.")
    original = directory / record["original"]
    backup = directory / "backup" / original.name
    for path in (original, backup):
        if path.resolve(strict=True) != path or serato_writer.file_sha256(path) != record["original_sha256"]:
            raise Refused("Generated original or baseline backup changed; acceptance is blocked.")
    files = []
    for name in dict.fromkeys(record["expected_order"]):
        path = Path(name)
        if path.resolve(strict=True) != path or path.parent != directory / "playback-copies":
            raise Refused("Playback path is outside the generated disposable copies.")
        saved = directory / "backup" / "playback-copies" / path.name
        if saved.resolve(strict=True) != saved or serato_writer.file_sha256(saved) != record["original_sha256"]:
            raise Refused("Playback-copy backup changed; acceptance is blocked.")
        if serato_writer.mp3_audio_sha256(path) != record["audio_sha256"]:
            raise Refused("Playback audio changed; acceptance is blocked.")
        frames = serato_writer.read_serato_frames(path)
        files.append({"file": path.name, "file_sha256": serato_writer.file_sha256(path),
                      "audio_sha256": record["audio_sha256"],
                      "serato_frames": {key: base64.b64encode(value).decode("ascii") if value is not None else None
                                        for key, value in frames.items()}})
    report = {"schema_version": 1, "serato_version": version, "export_id": record["export_id"],
              "human_observations": {"order_ok": order_ok, "playback_ok": playback_ok, "duplicates_ok": duplicates_ok},
              "passed": order_ok and playback_ok and duplicates_ok, "files": files,
              "scope": "Playlist acceptance only; not cue accuracy, Cue Loop linkage or production approval."}
    output = directory / "serato-result.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "capture"):
        command = sub.add_parser(name)
        command.add_argument("--directory", required=True)
        if name == "capture":
            command.add_argument("--serato-version", required=True)
            for flag in ("order", "playback", "duplicates"):
                command.add_argument("--" + flag + "-ok", action="store_true")
    args = parser.parse_args()
    directory = Path(args.directory).expanduser().absolute()
    try:
        result = (create(directory) if args.command == "create" else
                  capture(directory, args.serato_version, args.order_ok, args.playback_ok, args.duplicates_ok))
        print(result)
    except (Refused, OSError, ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, "Synthetic Serato QA failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
