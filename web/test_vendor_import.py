import copy
import csv
import errno
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from web import catalog, vendor_import


class VendorImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "exports"
        self.root.mkdir()
        (self.root / vendor_import.REFERENCE).mkdir()
        self.reference = [{"_id": "crate-1", "Title": "Same title", "trackCount": 2,
                           "Category": [{"Category": "Synthetic"}]},
                          {"_id": "crate-2", "Title": "Same title", "trackCount": 2},
                          {"_id": "missing", "Title": "Unavailable", "trackCount": 10}]
        self.manifest = [{"id": "crate-1", "title": "Same title", "status": "saved",
                          "tracks": 2, "file": "first.csv"},
                         {"id": "crate-2", "title": "Same title", "status": "saved",
                          "tracks": 2, "file": "second.csv"},
                         {"id": "missing", "title": "Unavailable", "status": "failed",
                          "error": "HTTP Error 404: Not Found"}]
        self.write_references()
        self.csv_bytes = self.csv_data()
        for name in ("first.csv", "second.csv"):
            (self.root / name).write_bytes(self.csv_bytes)
        (self.root / "older duplicate.csv").write_text("deliberately not a supported CSV")

    def write_references(self):
        for name, value in (("Crate catalog reference.json", self.reference),
                            ("Crate export manifest.json", self.manifest)):
            (self.root / vendor_import.REFERENCE / name).write_text(json.dumps(value), encoding="utf-8")

    def csv_data(self):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerows([["", "Synthetic crate Hacked by: invented curator"], [],
                          ["", "SPOTIFY: https://example.invalid/synthetic"], vendor_import.HEADER,
                          ["1", 'Quoted, "title"\nwith newline', "Invented caf\u00e9", "0", "0A", "0", "", "42"],
                          ["2", 'Quoted, "title"\nwith newline', "Invented caf\u00e9", "0", "0A", "0", "", "42"]])
        return ("\ufeff" + stream.getvalue()).encode("utf-8")

    def test_manifest_selects_only_declared_exports_and_reconciles_idempotent_import(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        document, report = vendor_import.convert(str(self.root))
        self.assertEqual(report["catalog_entries"], 3)
        self.assertEqual(report["available"], 2)
        self.assertEqual(report["memberships"], 4)
        self.assertEqual(report["distinct_row_metadata"], 1)
        self.assertEqual(report["unreferenced_csv_files"], 1)
        self.assertEqual(report["unavailable"][0]["id"], "missing")
        self.assertEqual(document["crates"][0]["songs"][0]["bpm"], 0)
        self.assertEqual(document["crates"][0]["songs"][0]["danceability"], 0)
        self.assertIsNone(document["crates"][0]["songs"][0]["energy"])
        self.assertEqual(document["crates"][0]["songs"][0]["source_fields"]["BPM"], "0")
        self.assertEqual(document["crates"][0]["songs"][0]["version"], "")
        self.assertEqual(document["crates"][0]["songs"][0]["title"], 'Quoted, "title"\nwith newline')
        self.assertEqual(vendor_import.convert(str(self.root)), (document, report))
        with sqlite3.connect(Path(self.temporary.name) / "fixture.sqlite3") as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            catalog.initialize(db)
            first = catalog.import_snapshot(db, document)
            db.commit()
            second = catalog.import_snapshot(db, document)
            self.assertTrue(first["added"])
            self.assertFalse(second["added"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_memberships").fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_crates").fetchone()[0], 3)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_missing_duplicate_or_mismatched_reference_fails_closed(self):
        original_reference, original_manifest = copy.deepcopy(self.reference), copy.deepcopy(self.manifest)
        for mutate in (
                lambda: self.reference.append(self.reference[0]),
                lambda: self.manifest.append(self.manifest[0]),
                lambda: self.manifest.pop(),
                lambda: self.manifest[0].update(title="Wrong"),
                lambda: self.manifest[0].update(status="unexpected"),
                lambda: self.manifest[0].update(tracks=3),
                lambda: self.manifest[1].update(file="first.csv"),
                lambda: self.manifest[2].pop("error")):
            self.reference, self.manifest = copy.deepcopy(original_reference), copy.deepcopy(original_manifest)
            mutate()
            self.write_references()
            with self.assertRaises(ValueError):
                vendor_import.convert(str(self.root))

    def test_csv_errors_never_silently_drop_or_reorder_rows(self):
        for text in (
                "Title,Artist\nsong,artist\n",
                "#,Title,Artist,BPM,Key,Danceability,Energy,Mood\n2,song,artist,100,1A,0,0,0\n",
                "#,Title,Artist,BPM,Key,Danceability,Energy,Mood\n1,song,artist,nan,1A,0,0,0\n",
                "#,Title,Artist,BPM,Key,Danceability,Energy,Mood\n1,song,artist,-1,1A,0,0,0\n",
                "#,Title,Artist,BPM,Key,Danceability,Energy,Mood\n1,song,artist,100,1A,0,0,0,extra\n"):
            (self.root / "first.csv").write_text(text)
            with self.assertRaises(ValueError):
                vendor_import.convert(str(self.root))
        (self.root / "first.csv").write_bytes(self.csv_bytes + self.csv_bytes)
        with self.assertRaisesRegex(ValueError, "header"):
            vendor_import.convert(str(self.root))

    def test_missing_source_artist_is_retained_and_never_treated_as_tag_agreement(self):
        data = self.csv_bytes.replace("Invented caf\u00e9".encode(), b"")
        for name in ("first.csv", "second.csv"):
            (self.root / name).write_bytes(data)
        document, report = vendor_import.convert(str(self.root))
        song = document["crates"][0]["songs"][0]
        self.assertEqual(song["artist"], "")
        self.assertEqual(report["memberships_missing_artist"], 4)
        self.assertTrue(song["source_warnings"])
        candidates = catalog.candidates(song, [{"metadata": {"artist": "Some artist", "title": song["title"]},
                                               "path": "/synthetic.wav", "error": ""}])
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["metadata_equal"])
        self.assertIn("missing", candidates[0]["reason"])

    def test_missing_titles_are_preserved_without_candidate_substitutions(self):
        songs, _ = vendor_import.parse_csv(
            (",".join(vendor_import.HEADER) + "\n1,,Invented,0,,0,0,0\n2,,,0,,0,0,0\n").encode(),
            "unknown-title.csv")
        self.assertEqual(len(songs), 2)
        self.assertEqual([song["title"] for song in songs], ["", ""])
        self.assertNotEqual(songs[0]["id"], songs[1]["id"])
        self.assertEqual(catalog.candidates(songs[0], [{"metadata": {"title": "", "artist": "Invented"},
                                                     "path": "/synthetic.wav", "error": ""}]), [])
        self.assertTrue(all(song["source_warnings"] for song in songs))
        document = {"schema_version": 1, "source": "synthetic",
                    "crates": [{"id": "unknown", "title": "Unknowns", "available": True, "songs": songs}]}
        self.assertEqual(len(catalog.validate_snapshot(document)), 2)

    def test_paths_symlinks_and_missing_csv_are_rejected(self):
        for filename in ("../outside.csv", "/absolute.csv", "sub/file.csv", ""):
            self.manifest[0]["file"] = filename
            self.write_references()
            with self.assertRaises(ValueError):
                vendor_import.convert(str(self.root))

        self.manifest[0]["file"] = "first.csv"
        self.write_references()
        (self.root / "first.csv").unlink()
        with self.assertRaises(OSError):
            vendor_import.convert(str(self.root))
        (self.root / "first.csv").symlink_to(self.root / "second.csv")
        with self.assertRaises(ValueError):
            vendor_import.convert(str(self.root))

    def test_materialized_file_requires_identical_stable_reread(self):
        with patch.object(catalog, "signature", side_effect=[(1,), (2,), (3,), (3,)]):
            with self.assertWarnsRegex(UserWarning, "stable reread"):
                self.assertEqual(vendor_import.read_source(self.root, "first.csv"), self.csv_bytes)
        with patch.object(catalog, "signature", side_effect=[(1,), (2,), (3,), (4,)]):
            with self.assertRaisesRegex(ValueError, "changed"):
                vendor_import.read_source(self.root, "first.csv")
        original = Path.read_bytes
        calls = 0
        def mutate(path):
            nonlocal calls
            calls += 1
            return original(path) if calls == 1 else b"different contents"
        with patch.object(catalog, "signature", side_effect=[(1,), (2,)]), \
                patch.object(Path, "read_bytes", mutate):
            with self.assertRaisesRegex(ValueError, "changed"):
                vendor_import.read_source(self.root, "first.csv")

    def test_cloud_timeouts_retry_bounded_and_name_the_failed_source(self):
        timeout = OSError(errno.ETIMEDOUT, "Synthetic cloud timeout")
        with patch.object(vendor_import, "_read_source", side_effect=[timeout, self.csv_bytes]) as reader:
            with self.assertWarnsRegex(UserWarning, "retrying"):
                self.assertEqual(vendor_import.read_source(self.root, "first.csv"), self.csv_bytes)
            self.assertEqual(reader.call_count, 2)
        with patch.object(vendor_import, "_read_source", side_effect=timeout) as reader:
            with self.assertWarns(UserWarning), self.assertRaisesRegex(OSError, "first.csv"):
                vendor_import.read_source(self.root, "first.csv")
            self.assertEqual(reader.call_count, 3)

    def test_row_identity_excludes_position_but_preserves_version_and_metadata_differences(self):
        songs, _ = vendor_import.parse_csv(self.csv_bytes, "synthetic.csv")
        self.assertEqual(songs[0]["id"], songs[1]["id"])
        # A title/version change must not reuse a row identity.
        changed, _ = vendor_import.parse_csv(self.csv_bytes.replace(b"Quoted", b"Extended"), "synthetic.csv")
        self.assertNotEqual(songs[0]["id"], changed[0]["id"])

    def test_cli_writes_new_atomic_snapshot_outside_source_and_refuses_overwrite(self):
        output = Path(self.temporary.name) / "snapshot.json"
        command = [sys.executable, "-m", "web.vendor_import", "--directory", str(self.root),
                   "--output", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["memberships"], 4)
        original = output.read_bytes()
        again = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(output.read_bytes(), original)
        blocked = subprocess.run(command[:-1] + [str(self.root / "new.json")], capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertFalse((self.root / "new.json").exists())
        self.assertEqual(list(output.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
