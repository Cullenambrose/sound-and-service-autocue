"""Synthetic format coverage for explicitly selected, read-only audio files."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web import catalog
from web.selected_files import SelectedFiles


TITLE = "検証 Track"
ARTIST = "Créated Artist"
VERSION = "Extended Mix — 夜"

FORMATS = {
    "wav": ("pcm_s16le", True),
    "mp3": ("libmp3lame", True),
    "flac": ("flac", False),
    "aiff": ("pcm_s16be", True),
    "m4a": ("aac", False),
    "ogg": ("libopus", False),
    "opus": ("libopus", False),
}


def _generate_tagged_formats(directory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required to generate the synthetic format matrix")

    from mutagen import File
    from mutagen.id3 import TBPM, TIT2, TIT3, TKEY, TPE1, TXXX

    paths = {}
    for extension, (codec, id3_tags) in FORMATS.items():
        path = directory / f"synthetic.{extension}"
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.25",
            "-c:a", codec,
        ]
        if extension == "m4a":
            command.extend(["-f", "ipod"])
        command.append(str(path))
        subprocess.run(command, check=True, capture_output=True, text=True)

        audio = File(path)
        if id3_tags:
            if audio.tags is None:
                audio.add_tags()
            for frame in (TIT2, TPE1, TIT3, TBPM, TKEY):
                audio.tags.delall(frame.__name__)
            audio.tags.add(TIT2(encoding=3, text=[TITLE]))
            audio.tags.add(TPE1(encoding=3, text=[ARTIST]))
            audio.tags.add(TIT3(encoding=3, text=[VERSION]))
            audio.tags.add(TBPM(encoding=3, text=["123.5"]))
            audio.tags.add(TKEY(encoding=3, text=["8A"]))
            if extension in ("wav", "aiff"):
                audio.tags.add(TXXX(encoding=3, desc="VERSION", text=[VERSION]))
        elif extension == "m4a":
            if audio.tags is None:
                audio.add_tags()
            audio.tags["\xa9nam"] = [TITLE]
            audio.tags["\xa9ART"] = [ARTIST]
            audio.tags["tmpo"] = [123]
        else:
            if audio.tags is None:
                audio.add_tags()
            audio.tags["title"] = [TITLE]
            audio.tags["artist"] = [ARTIST]
            audio.tags["version"] = [VERSION]
            audio.tags["bpm"] = ["123.5"]
            audio.tags["initialkey"] = ["8A"]
        audio.save()
        paths[extension] = path
    return paths


def _selection(path):
    return SelectedFiles({
        "schema_version": 1,
        "library_files": [str(path)],
        "acquisition_files": [],
    })


def test_generated_audio_formats_read_tags_through_selected_files(tmp_path):
    paths = _generate_tagged_formats(tmp_path)

    for extension, path in paths.items():
        track = _selection(path).inspect(str(path))
        assert track["title"] == TITLE, extension
        assert track["artist"] == ARTIST, extension
        assert track["duration_s"] > 0, extension
        assert track["version"] == ("" if extension == "m4a" else VERSION), extension
        assert track["bpm"] == (123.0 if extension == "m4a" else 123.5), extension
        assert track["provenance"] == "selected_file_tags"
        _selection(path).validate([track])


@pytest.mark.parametrize("field,value", [
    ("bpm", "not-a-number"),
    ("bpm", "nan"),
    ("bpm", float("inf")),
    ("bpm", -1),
    ("bpm", False),
    ("duration_s", float("nan")),
    ("duration_s", float("inf")),
    ("duration_s", -1),
    ("duration_s", True),
    ("duration_s", "1.25"),
])
def test_selected_file_rejects_malformed_numeric_tags_and_durations(tmp_path, monkeypatch, field, value):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"synthetic test payload")
    metadata = {
        "title": TITLE, "artist": ARTIST, "version": VERSION,
        "bpm": "", "key": "", "duration_s": 1.0,
    }
    metadata[field] = value
    monkeypatch.setattr(catalog, "read_metadata", lambda _: metadata)

    with pytest.raises(ValueError, match="BPM|duration"):
        _selection(path).inspect(str(path))


def test_selected_file_accepts_unknown_duration_but_rejects_bad_audio(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"synthetic test payload")
    metadata = {
        "title": TITLE, "artist": ARTIST, "version": "",
        "bpm": "", "key": "", "duration_s": None,
    }
    monkeypatch.setattr(catalog, "read_metadata", lambda _: metadata)
    assert _selection(path).inspect(str(path))["duration_s"] is None

    monkeypatch.undo()
    path.write_bytes(b"not an audio file")
    with pytest.raises(ValueError, match="metadata"):
        _selection(path).inspect(str(path))


def test_selected_file_changed_during_metadata_read_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"synthetic test payload")

    def change_after_read(value):
        metadata = {
            "title": TITLE, "artist": ARTIST, "version": VERSION,
            "bpm": "", "key": "", "duration_s": 1.0,
        }
        path.write_bytes(path.read_bytes() + b"changed")
        return metadata

    monkeypatch.setattr(catalog, "read_metadata", change_after_read)
    with pytest.raises(ValueError, match="changed while being verified"):
        _selection(path).inspect(str(path))
