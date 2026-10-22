import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

from mutagen import File
from mutagen.id3 import GEOB, ID3
from mutagen.mp4 import MP4FreeForm

import imported_anchors
import library_inventory as inventory
from serato_tags import FLAC_KEY, MP4_KEY, VORBIS_HEADER, marker2_entries
from web import fixtures


def envelope(entries, version=b"\x01\x01"):
    raw = b"\x01\x01"
    for name, payload in entries:
        raw += name.encode("ascii") + b"\0" + len(payload).to_bytes(4, "big") + payload
    return version + base64.b64encode(raw) + b"\0"


def cue(position=500, index=0, name="Existing cue"):
    return b"\0" + bytes([index]) + position.to_bytes(4, "big") + b"\0\xCC\x22\x11\0\0" + name.encode() + b"\0"


def wrapped(data):
    return base64.b64encode(VORBIS_HEADER + data).decode("ascii").rstrip("=")


def id3_audio(fields):
    tags = ID3()
    for field, raw in fields:
        tags.add(GEOB(encoding=0, mime="application/octet-stream", desc=field, data=raw))
    return SimpleNamespace(tags=tags)


class ImportedAnchorTests(unittest.TestCase):
    def test_cues_loops_and_unknown_entries_preserve_original_evidence(self):
        loop = b"\0\x02" + struct.pack(">II", 100, 900) + b"\0" * 10 + "Loop é".encode() + b"\0"
        data = envelope([("CUE", cue(name="DROP é")), ("LOOP", loop), ("FUTURE", b"\xff\x00")])
        result = imported_anchors.read_annotations(id3_audio([("Serato Markers2", data)]), 1)
        source = result["sources"][0]
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(source["records"][0]["time_s"], .5)
        self.assertEqual(source["records"][0]["color"], "#CC2211")
        self.assertEqual(source["records"][0]["name"], "DROP é")
        self.assertEqual(source["records"][1]["start_s"], .1)
        self.assertEqual(source["records"][1]["end_s"], .9)
        self.assertEqual(source["records"][2]["type"], "FUTURE")
        self.assertEqual(base64.b64decode(source["raw_b64"]), data)
        self.assertEqual(source["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(source["producer"], "unknown")
        self.assertFalse(source["human_verified"])
        self.assertEqual(source["label_role"], "unverified_import")
        self.assertNotIn("meaning", source["records"][0])

    def test_unknown_versions_and_malformed_payloads_are_explicit_and_preserved(self):
        invalid = [
            envelope([("CUE", cue())], version=b"\x02\x00"),
            b"\x01\x01" + base64.b64encode(b"\x02\x00"),
            b"\x01\x01!not base64",
            envelope([("CUE", b"\0\x01")]),
            envelope([("CUE", cue()[:-1])]),
            envelope([("CUE", cue()[:-1] + b"\xff\0")]),
            envelope([("CUE", cue(position=2000))]),
            envelope([("CUE", cue()), ("CUE", cue())]),
            envelope([("LOOP", b"\0\0" + struct.pack(">II", 900, 100) + b"\0" * 11)]),
            b"\x01\x01" + base64.b64encode(b"\x01\x01CUE\0\0\0\0\x20xx"),
        ]
        for data in invalid:
            with self.subTest(data=data):
                result = imported_anchors.read_annotations(id3_audio([("Serato Markers2", data)]), 1)
                source = result["sources"][0]
                self.assertEqual(result["error_count"], 1)
                self.assertEqual(source["status"], "error")
                self.assertTrue(source["error"])
                self.assertEqual(base64.b64decode(source["raw_b64"]), data)
                self.assertEqual(source["records"], [])

    def test_unset_timestamp_is_not_an_event_at_file_end(self):
        result = imported_anchors.read_annotations(
            id3_audio([("Serato Markers2", envelope([("CUE", cue(position=0xFFFFFFFF))]))]), 1)
        self.assertEqual(result["error_count"], 0)
        self.assertIsNone(result["sources"][0]["records"][0]["time_s"])

    def test_variable_tempo_beatgrid_is_preserved_without_adopting_it(self):
        data = b"\x01\x00" + struct.pack(">I", 2) + struct.pack(">fIff", 0, 4, 2, 140) + b"\0"
        result = imported_anchors.read_annotations(id3_audio([("Serato BeatGrid", data)]), 10)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["sources"][0]["records"],
                         [{"pos": 0, "beats_to_next": 4}, {"pos": 2, "bpm": 140}])
        for bad in (data[:8], b"\x02\x00" + data[2:]):
            self.assertEqual(imported_anchors.read_annotations(
                id3_audio([("Serato BeatGrid", bad)]), 10)["error_count"], 1)

    def test_cuepoints_json_is_preserved_without_guessing_units_or_creator(self):
        document = {"CuePoints": [{"Position": 1.5, "Energy": 7}]}
        raw = json.dumps(document).encode()
        for data in (raw, base64.b64encode(raw).rstrip(b"=")):
            result = imported_anchors.read_annotations(id3_audio([("CuePoints", data)]), 10)
            source = result["sources"][0]
            self.assertEqual(source["document"], document)
            self.assertEqual(source["status"], "unsupported")
            self.assertEqual(result["unsupported_count"], 1)
            self.assertEqual(source["records"], [])
            self.assertEqual(source["producer"], "unknown")
        for data in (b"bad!", b'{"position": NaN}', b'{"position": 1e999}', b'"scalar"'):
            self.assertEqual(imported_anchors.read_annotations(
                id3_audio([("CuePoints", data)]), 10)["error_count"], 1)

    def test_legacy_conflicts_and_oversized_tags_are_not_silently_dropped(self):
        result = imported_anchors.read_annotations(id3_audio([("Serato Markers_", b"old")]), 10)
        self.assertEqual(result["unsupported_count"], 1)
        tags = mock.Mock()
        tags.getall.return_value = [SimpleNamespace(desc="Serato Markers2", data=envelope([]))] * 2
        result = imported_anchors.read_annotations(SimpleNamespace(tags=tags), 10)
        self.assertEqual(result["error_count"], 2)
        self.assertEqual([source["occurrence"] for source in result["sources"]], [1, 2])
        with mock.patch.object(imported_anchors, "MAX_TAG_BYTES", 4):
            result = imported_anchors.read_annotations(id3_audio([("CuePoints", b"[1,2,3]")]), 10)
        source = result["sources"][0]
        self.assertEqual(result["error_count"], 1)
        self.assertNotIn("raw_b64", source)
        self.assertEqual(source["size_bytes"], 7)
        self.assertTrue(source["sha256"])

    def test_all_native_supported_containers_round_trip_read_only(self):
        data = envelope([("CUE", cue(position=10))])
        with tempfile.TemporaryDirectory() as directory:
            for extension, make in (("mp3", fixtures.mp3), ("aiff", fixtures.aiff),
                                    ("flac", fixtures.flac), ("m4a", fixtures.m4a), ("wav", None)):
                with self.subTest(extension=extension):
                    path = Path(directory) / ("sample." + extension)
                    if make:
                        path.write_bytes(make())
                    else:
                        with wave.open(str(path), "wb") as audio:
                            audio.setnchannels(1)
                            audio.setsampwidth(2)
                            audio.setframerate(8000)
                            audio.writeframes(b"\0\0" * 8000)
                    audio = File(path)
                    if audio.tags is None:
                        audio.add_tags()
                    if extension == "flac":
                        audio[FLAC_KEY] = [wrapped(data)]
                    elif extension == "m4a":
                        audio[MP4_KEY] = [MP4FreeForm(wrapped(data).encode("ascii"))]
                    else:
                        audio.tags.add(GEOB(encoding=0, mime="application/octet-stream",
                                            desc="Serato Markers2", data=data))
                    audio.save()
                    original, mtime = path.read_bytes(), path.stat().st_mtime_ns
                    with path.open("rb") as incoming:
                        metadata = inventory.read_metadata(incoming)
                    result = metadata["imported_anchors"]
                    self.assertEqual(result["error_count"], 0)
                    self.assertEqual(result["sources"][0]["records"][0]["time_s"], .01)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_bad_wrappers_remain_errors_and_keep_raw_values(self):
        for field, raw in ((FLAC_KEY, "not base64!"), (MP4_KEY, b"\xff"),
                           (FLAC_KEY, "!" + wrapped(envelope([]))),
                           (FLAC_KEY, base64.b64encode(b"wrong header").decode())):
            result = imported_anchors.read_annotations(SimpleNamespace(tags={field: [raw]}), 10)
            self.assertEqual(result["error_count"], 1)
            self.assertIn("raw_b64", result["sources"][0])

    def test_inventory_persists_evidence_and_reports_annotation_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "music"
            root.mkdir()
            path = root / "tagged.mp3"
            path.write_bytes(fixtures.mp3())
            audio = File(path)
            audio.add_tags()
            audio.tags.add(GEOB(encoding=0, mime="application/octet-stream",
                                desc="Serato Markers2", data=b"bad version"))
            audio.save()
            original = path.read_bytes()
            database = Path(directory) / "inventory.sqlite3"
            scan = inventory.create_scan(database, [root])
            result = inventory.resume_scan(database, scan)
            self.assertEqual(result["status"], "complete_with_errors")
            self.assertEqual(result["counts"]["audio"], {"done": 1})
            self.assertEqual(result["error_count"], 0)
            self.assertEqual(result["annotation_error_count"], 1)
            self.assertEqual(result["annotation_issues"][0]["field"], "Serato Markers2")
            self.assertTrue(result["annotation_issues"][0]["diagnostic"])
            self.assertNotIn("raw_b64", json.dumps(result))
            with sqlite3.connect(database) as db:
                metadata = json.loads(db.execute(
                    "SELECT metadata_json FROM entries WHERE kind='audio'").fetchone()[0])
            source = metadata["imported_anchors"]["sources"][0]
            self.assertEqual(base64.b64decode(source["raw_b64"]), b"bad version")
            self.assertEqual(path.read_bytes(), original)

    def test_id3_versions_preserve_unicode_labels_without_claiming_authorship(self):
        with tempfile.TemporaryDirectory() as directory:
            for version in (3, 4):
                path = Path(directory) / f"id3-v{version}.mp3"
                path.write_bytes(fixtures.mp3())
                audio = File(path)
                audio.add_tags()
                audio.tags.add(GEOB(encoding=0, mime="application/octet-stream",
                                    desc="Serato Markers2",
                                    data=envelope([("CUE", cue(position=10, name="Mixed In Key é"))])))
                audio.save(v2_version=version)
                original = path.read_bytes()
                with path.open("rb") as incoming:
                    source = inventory.read_metadata(incoming)["imported_anchors"]["sources"][0]
                self.assertEqual(source["status"], "parsed")
                self.assertEqual(source["records"][0]["name"], "Mixed In Key é")
                self.assertEqual(source["producer"], "unknown")
                self.assertEqual(path.read_bytes(), original)

    def test_annotation_issue_output_is_bounded_and_unsupported_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "music"
            root.mkdir()
            for index in range(21):
                path = root / f"tagged-{index:02}.mp3"
                path.write_bytes(fixtures.mp3())
                audio = File(path)
                audio.add_tags()
                audio.tags.add(GEOB(encoding=0, mime="application/octet-stream",
                                    desc="CuePoints", data=b'{"unknown_units": 100}'))
                audio.save()
            database = Path(directory) / "inventory.sqlite3"
            scan = inventory.create_scan(database, [root])
            result = inventory.resume_scan(database, scan)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["annotation_unsupported_count"], 21)
            self.assertEqual(result["annotation_error_count"], 0)
            self.assertEqual(len(result["annotation_issues"]), 20)
            self.assertTrue(result["annotation_issues_truncated"])
            self.assertEqual(result["annotation_issues"][0]["status"], "unsupported")
            self.assertNotIn("raw_b64", json.dumps(result))

    def test_shared_envelope_keeps_unknown_fields_for_writer(self):
        entries = [("UNKNOWN", b"\xff\0"), ("CUE", cue())]
        self.assertEqual(marker2_entries(envelope(entries)), entries)


if __name__ == "__main__":
    unittest.main()
