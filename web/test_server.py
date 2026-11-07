import base64
import concurrent.futures
from contextlib import contextmanager
import hashlib
import csv
import io
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from unittest.mock import patch
import urllib.error
import urllib.request
from pathlib import Path

from web import fixtures, server


def _invalid_waveform_points_url(url):
    return url.replace("&points=512", "&points=invalid", 1)


class WaveformURLTests(unittest.TestCase):
    def test_invalid_points_preserves_host_port_path_and_other_query_values(self):
        url = "http://127.0.0.1:51234/api/waveform/512?detail=1&start=1.25&end=3.5&points=512"
        invalid_url = _invalid_waveform_points_url(url)
        self.assertEqual(
            invalid_url,
            "http://127.0.0.1:51234/api/waveform/512?detail=1&start=1.25&end=3.5&points=invalid",
        )
        self.assertEqual(urllib.parse.urlsplit(invalid_url).netloc, "127.0.0.1:51234")


class WebServerTests(unittest.TestCase):
    def saved_export_fixture(self):
        source, decision, body, request = self.export_fixture(duplicates=True)
        _, download = self.export_preview(request)
        code, result = self.catalog_post("personal/export/download", download)
        self.assertEqual(code, 200, result)
        return source, decision, body, request, result

    def saved_export_get(self, result, dataset, **headers):
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        query = urllib.parse.urlencode({"export_id": result["preview_id"], "dataset_id": dataset})
        request = urllib.request.Request(base + "/api/catalog/personal/exports/download?" + query, headers=headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), json.load(error)
        with response:
            return response.status, dict(response.headers), response.read()

    def test_saved_exports_recover_after_source_removal_and_newer_crate_revision_without_audio_reads(self):
        source, _, body, request, result = self.saved_export_fixture()
        source.unlink()
        self.assertEqual(self.catalog_post("personal/save", {**body, "request_id": "newer",
            "base_revision": 1, "name": "New revision", "entries": []})[0], 200)
        server.initialize()
        with patch.object(server.catalog, "file_hash", side_effect=AssertionError("Must not rehash audio")), \
                patch.object(server.catalog, "file_record", side_effect=AssertionError("Must not stat audio")):
            history = self.catalog_get("personal/exports")
            code, headers, data = self.saved_export_get(result, request["dataset_id"])
        self.assertEqual(history["total"], 1)
        item = history["exports"][0]
        self.assertEqual((item["state"], item["crate"]["revision"], item["entries"]), ("historical", 1, 2))
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertIn(result["filename"], headers["Content-Disposition"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(int(headers["Content-Length"]), len(data))
        self.assertEqual(data, base64.b64decode(result["bundle_base64"]))

    def test_saved_exports_detect_corruption_missing_members_and_forged_playlist(self):
        _, _, _, request, result = self.saved_export_fixture()
        path = server.DATA / "exports" / result["filename"]
        original = path.read_bytes()
        for mutation in ("truncated", "missing", "extra", "forged"):
            if mutation == "truncated":
                path.write_bytes(original[:20])
            else:
                output = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(output, "w") as archive:
                    for name in source.namelist():
                        if mutation == "missing" and name == "playlist.csv":
                            continue
                        archive.writestr(name, b"#EXTM3U\n/forged.wav\n" if
                                         mutation == "forged" and name == "playlist.m3u8" else source.read(name))
                    if mutation == "extra":
                        archive.writestr("../unexpected", b"never extracted")
                path.write_bytes(output.getvalue())
            item = self.catalog_get("personal/exports")["exports"][0]
            self.assertEqual(item["state"], "unavailable", mutation)
            self.assertIn("integrity", item["error"], mutation)
            self.assertEqual(self.saved_export_get(result, request["dataset_id"])[0], 400, mutation)
        path.write_bytes(original)
        self.assertEqual(self.saved_export_get(result, request["dataset_id"])[0], 200)

    def test_saved_exports_validate_manifest_id_and_dataset_without_exposing_foreign_metadata(self):
        _, _, _, request, result = self.saved_export_fixture()
        path = server.DATA / "exports" / result["filename"]
        document = result["manifest"]
        document = {**document, "dataset_id": "foreign-dataset"}
        path.write_bytes(server.playlist_export.bundle(document))
        self.assertEqual(self.saved_export_get(result, request["dataset_id"])[0], 400)
        foreign_id = server.catalog.digest(document)
        path.rename(path.parent / (foreign_id + ".zip"))
        item = self.catalog_get("personal/exports")["exports"][0]
        self.assertEqual(item["state"], "unavailable")
        self.assertIn("different dataset", item["error"])
        self.assertNotIn("crate", item)
        self.assertEqual(self.saved_export_get({**result, "preview_id": foreign_id}, request["dataset_id"])[0], 400)
        self.assertEqual(self.saved_export_get(result, "wrong-dataset")[0], 409)

    def test_saved_exports_reject_unsafe_paths_symlinks_nonregular_and_oversized_bundles(self):
        source, _, _, request, result = self.saved_export_fixture()
        for unsafe in ("../library.sqlite3", "A" * 64, "", "a" * 65, "a/b"):
            self.assertEqual(self.saved_export_get({**result, "preview_id": unsafe}, request["dataset_id"])[0], 400)
        path = server.DATA / "exports" / result["filename"]
        original = path.read_bytes()
        path.unlink()
        path.symlink_to(source)
        self.assertEqual(self.catalog_get("personal/exports")["exports"][0]["state"], "unavailable")
        self.assertEqual(self.saved_export_get(result, request["dataset_id"])[0], 503)
        path.unlink()
        path.mkdir()
        self.assertEqual(self.catalog_get("personal/exports")["exports"][0]["state"], "unavailable")
        self.assertIn(self.saved_export_get(result, request["dataset_id"])[0], (400, 503))
        path.rmdir()
        path.write_bytes(original)
        with patch.object(server.playlist_export, "MAX_BUNDLE_BYTES", 10):
            self.assertEqual(self.catalog_get("personal/exports")["exports"][0]["state"], "unavailable")
            self.assertEqual(self.saved_export_get(result, request["dataset_id"])[0], 400)
        exports = server.DATA / "exports"
        moved = server.DATA / "moved-exports"
        exports.rename(moved)
        exports.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.saved_export_get(result, request["dataset_id"])[0], 503)

    def test_saved_exports_paging_empty_history_and_disappearing_artifacts(self):
        empty = self.catalog_get("personal/exports")
        self.assertEqual(empty, {"exports": [], "offset": 0, "total": 0})
        _, _, _, request, result = self.saved_export_fixture()
        directory = server.DATA / "exports"
        path = directory / result["filename"]
        for index in range(21):
            (directory / (f"{index:064x}" + ".zip")).write_bytes(b"bad")
        first = self.catalog_get("personal/exports")
        second = self.catalog_get("personal/exports", offset=20)
        self.assertEqual((first["total"], len(first["exports"]), len(second["exports"])), (22, 20, 2))
        self.assertFalse({item["export_id"] for item in first["exports"]} &
                         {item["export_id"] for item in second["exports"]})
        self.assertEqual(self.catalog_get("personal/exports", offset=40)["exports"], [])
        path.unlink()
        self.assertEqual(self.saved_export_get(result, request["dataset_id"])[0], 404)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.catalog_get("personal/exports", offset=-1)
        self.assertEqual(failure.exception.code, 400)

    def test_saved_exports_reject_cross_site_downloads_and_preserve_existing_bundle_on_publication_failure(self):
        _, _, _, request, result = self.saved_export_fixture()
        for headers in ({"Origin": "https://example.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.saved_export_get(result, request["dataset_id"], **headers)[0], 403)
        path = server.DATA / "exports" / result["filename"]
        original = path.read_bytes()
        with patch.object(server.playlist_export.os, "replace", side_effect=OSError("Synthetic disk failure")):
            self.assertEqual(self.catalog_post("personal/export/download",
                {**request, "preview_id": result["preview_id"]})[0], 503)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])
        self.assertEqual(self.saved_export_get(result, request["dataset_id"])[2], original)

    def test_saved_exports_invalid_manifest_and_compressed_archives_remain_visible_errors(self):
        _, _, _, request, result = self.saved_export_fixture()
        directory = server.DATA / "exports"
        original = (directory / result["filename"]).read_bytes()
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        invalid = [{**result["manifest"], "crate": None},
                   {**result["manifest"], "schema_version": True},
                   {**result["manifest"], "included": True},
                   {**result["manifest"], "entries": [{**entry, "song": []}
                                                     for entry in result["manifest"]["entries"]]},
                   {**result["manifest"], "entries": []}]
        for document in invalid:
            token = server.catalog.digest(document)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, json.dumps(document).encode() if name == "manifest.json" else data)
            (directory / (token + ".zip")).write_bytes(output.getvalue())
            self.assertEqual(self.saved_export_get({**result, "preview_id": token}, request["dataset_id"])[0], 400)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        (directory / result["filename"]).write_bytes(output.getvalue())
        self.assertEqual(self.saved_export_get(result, request["dataset_id"])[0], 400)
        items = self.catalog_get("personal/exports")["exports"]
        self.assertTrue(all(item["state"] == "unavailable" and item["error"] for item in items))
        with patch.object(server.playlist_export, "MAX_BUNDLE_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                server.playlist_export.publish(directory, result["preview_id"], original)
        self.assertEqual(list(directory.glob("*.tmp")), [])

    def test_saved_export_bundle_limit_accepts_exact_64_mib_and_rejects_one_more_byte(self):
        directory = Path(self.temporary.name) / "size-boundary"
        token = "a" * 64
        limit = 64 * 1024 * 1024
        self.assertEqual(server.playlist_export.MAX_BUNDLE_BYTES, limit)
        exact = b"x" * limit
        filename = server.playlist_export.publish(directory, token, exact)
        self.assertEqual((directory / filename).stat().st_size, limit)
        with self.assertRaisesRegex(ValueError, "64 MiB"):
            server.playlist_export.publish(directory, token, exact + b"x")
        self.assertEqual((directory / filename).stat().st_size, limit)
        with self.assertRaisesRegex(server.playlist_export.InvalidBundle, "integrity"):
            server.playlist_export.saved_bundle(directory, token, "synthetic")
        with (directory / filename).open("ab") as stream:
            stream.write(b"x")
        with self.assertRaisesRegex(server.playlist_export.InvalidBundle, "64 MiB"):
            server.playlist_export.saved_bundle(directory, token, "synthetic")
        self.assertEqual(list(directory.glob("*.tmp")), [])

    def export_fixture(self, *, duplicates=False):
        imported, source, decision = self.catalog_fixture()
        self.assertEqual(self.catalog_post("decide", decision)[0], 200)
        body = {"dataset_id": decision["dataset_id"], "request_id": "export-create",
                "crate_id": "export-crate", "base_revision": 0, "name": 'Warmup, "test"',
                "entries": [{"id": "entry-1", "snapshot_id": imported["snapshot_id"], "song_id": "song-1"}]}
        if duplicates:
            body["entries"].append({**body["entries"][0], "id": "entry-2"})
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)
        request = {"dataset_id": body["dataset_id"], "crate_id": body["crate_id"], "base_revision": 1}
        return source, decision, body, request

    def export_preview(self, request):
        code, result = self.catalog_post("personal/export/preview", request)
        self.assertEqual(code, 200, result)
        return result, {**request, "preview_id": result["preview_id"]}

    def test_export_ordered_duplicates_round_trip_and_deterministic_retry_preserve_source(self):
        source, _, _, request = self.export_fixture(duplicates=True)
        before = (source.read_bytes(), server.catalog.signature(source.stat()))
        with patch.object(server.catalog, "file_hash", wraps=server.catalog.file_hash) as hashing:
            preview, download = self.export_preview(request)
        self.assertEqual(hashing.call_count, 1)
        self.assertEqual((preview["manifest"]["requested"], preview["manifest"]["included"]), (2, 2))
        code, exported = self.catalog_post("personal/export/download", download)
        self.assertEqual(code, 200, exported)
        self.assertEqual(self.catalog_post("personal/export/download", download), (code, exported))
        data = base64.b64decode(exported["bundle_base64"])
        self.assertEqual((server.DATA / "exports" / exported["filename"]).read_bytes(), data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest, preview["manifest"])
            self.assertEqual(archive.read("playlist.m3u8").decode().splitlines(), ["#EXTM3U", str(source.resolve()), str(source.resolve())])
            rows = list(csv.DictReader(io.StringIO(archive.read("playlist.csv").decode())))
            self.assertEqual([row["entry_id"] for row in rows], ["entry-1", "entry-2"])
            self.assertEqual([row["path"] for row in rows], [str(source.resolve())] * 2)
        self.assertEqual(before, (source.read_bytes(), server.catalog.signature(source.stat())))
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_export_preview_blocks_unreviewed_cleared_offline_and_empty_sets(self):
        source, decision, body, request = self.export_fixture()
        source.rename(source.with_suffix(".offline"))
        preview, download = self.export_preview(request)
        self.assertFalse(preview["manifest"]["ready"])
        self.assertEqual(preview["manifest"]["included"], 0)
        self.assertIn("stale", preview["manifest"]["entries"][0]["reason"])
        self.assertEqual(self.catalog_post("personal/export/download", download)[0], 409)
        self.assertEqual(self.catalog_post("decide", {**decision, "request_id": "export-clear", "base_revision": 1,
                                                    "file_id": None, "content_hash": None})[0], 200)
        preview, _ = self.export_preview(request)
        self.assertIn("missing", preview["manifest"]["entries"][0]["reason"])
        with server.connect() as db:
            db.execute("DELETE FROM catalog_match_decisions")
        preview, _ = self.export_preview(request)
        self.assertIn("unreviewed", preview["manifest"]["entries"][0]["reason"])
        self.assertEqual(self.catalog_post("personal/save", {**body, "request_id": "empty", "base_revision": 1,
                                                           "entries": []})[0], 200)
        preview, download = self.export_preview({**request, "base_revision": 2})
        self.assertIn("Empty", preview["manifest"]["blockers"][0])
        self.assertEqual(self.catalog_post("personal/export/download", download)[0], 409)
        self.assertFalse((server.DATA / "exports").exists())

    def test_export_hashes_bytes_even_when_index_signatures_agree(self):
        source, _, _, request = self.export_fixture()
        original = source.read_bytes()
        source.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        value = source.stat()
        with server.connect() as db:
            db.execute("UPDATE library_files SET size=?,mtime_ns=?,ctime_ns=?", server.catalog.signature(value))
        preview, download = self.export_preview(request)
        self.assertIn("bytes differ", preview["manifest"]["entries"][0]["reason"])
        self.assertEqual(self.catalog_post("personal/export/download", download)[0], 409)
        self.assertFalse((server.DATA / "exports").exists())

    def test_export_rejects_stale_preview_after_match_revision_changes(self):
        _, decision, _, request = self.export_fixture()
        _, download = self.export_preview(request)
        self.assertEqual(self.catalog_post("decide", {**decision, "request_id": "review-again", "base_revision": 1})[0], 200)
        code, result = self.catalog_post("personal/export/download", download)
        self.assertEqual(code, 409, result)
        self.assertIn("stale", result["error"])
        self.assertFalse((server.DATA / "exports").exists())

    def test_export_rejects_changed_crate_and_wrong_dataset_and_malformed_requests(self):
        _, _, body, request = self.export_fixture()
        _, download = self.export_preview(request)
        self.assertEqual(self.catalog_post("personal/export/preview", request, authenticated=False)[0], 403)
        for edit in ({"base_revision": True}, {"extra": 0}, {"crate_id": ""}):
            self.assertEqual(self.catalog_post("personal/export/preview", {**request, **edit})[0], 400)
        self.assertEqual(self.catalog_post("personal/export/download", request)[0], 400)
        self.assertEqual(self.catalog_post("personal/export/preview", {**request, "dataset_id": "other"})[0], 409)
        self.assertEqual(self.catalog_post("personal/save", {**body, "request_id": "rename-export",
                                                           "base_revision": 1, "name": "New name"})[0], 200)
        self.assertEqual(self.catalog_post("personal/export/download", download)[0], 409)
        self.assertFalse((server.DATA / "exports").exists())

    def test_export_rechecks_authoritative_state_without_holding_lock_during_hash(self):
        _, decision, _, request = self.export_fixture()
        original = server.catalog.file_hash
        def change_during_hash(path):
            # A competing writer can commit during hash I/O.
            with server.connect() as db:
                server.catalog.decide(db, {**decision, "request_id": "concurrent-review", "base_revision": 1,
                                          "file_id": None, "content_hash": None})
            return original(path)
        with patch.object(server.catalog, "file_hash", side_effect=change_during_hash):
            code, result = self.catalog_post("personal/export/preview", request)
        self.assertEqual(code, 409, result)
        self.assertFalse((server.DATA / "exports").exists())

    def test_export_detects_file_mutation_during_and_after_hash(self):
        source, _, _, request = self.export_fixture()
        original = server.catalog.file_hash
        def change_after_hash(path):
            result = original(path)
            path.write_bytes(path.read_bytes() + b"changed")
            return result
        with patch.object(server.catalog, "file_hash", side_effect=change_after_hash):
            code, result = self.catalog_post("personal/export/preview", request)
        self.assertEqual(code, 409, result)
        self.assertFalse((server.DATA / "exports").exists())
        self.assertTrue(source.exists())

    def test_export_atomic_publication_failure_has_no_partial_bundle(self):
        _, _, _, request = self.export_fixture()
        _, download = self.export_preview(request)
        with patch.object(server.playlist_export.os, "replace", side_effect=OSError("Synthetic full disk")):
            code, result = self.catalog_post("personal/export/download", download)
        self.assertEqual(code, 503, result)
        self.assertEqual(list((server.DATA / "exports").iterdir()), [])
        self.assertEqual(self.catalog_post("personal/export/download", download)[0], 200)

    def test_export_existing_bundle_does_not_bypass_fresh_source_verification(self):
        source, _, _, request = self.export_fixture()
        _, download = self.export_preview(request)
        self.assertEqual(self.catalog_post("personal/export/download", download)[0], 200)
        source.unlink()
        self.assertEqual(self.catalog_post("personal/export/download", download)[0], 409)
        self.assertEqual(len(list((server.DATA / "exports").glob("*.zip"))), 1)

    def test_export_rejects_index_root_change_and_symlink_source(self):
        source, _, _, request = self.export_fixture()
        _, download = self.export_preview(request)
        with server.connect() as db:
            db.execute("UPDATE library_roots SET scan_revision=scan_revision+1")
        self.assertEqual(self.catalog_post("personal/export/download", download)[0], 409)
        moved = source.with_suffix(".original")
        source.rename(moved)
        source.symlink_to(moved)
        preview, _ = self.export_preview(request)
        self.assertFalse(preview["manifest"]["ready"])

    def test_export_unicode_spaces_csv_escaping_and_line_delimiter_blocking(self):
        source, decision, _, request = self.export_fixture()
        renamed = source.parent / 'caf\u00e9, "warm up".wav'
        source.rename(renamed)
        self.scan_folder(source.parent)
        file = next(item for item in self.catalog_get("files")["files"] if item["state"] == "available")
        self.assertEqual(self.catalog_post("decide", {**decision, "request_id": "unicode-match", "base_revision": 1,
                                                    "file_id": file["id"], "content_hash": file["content_hash"]})[0], 200)
        with server.connect() as db:
            row = db.execute("SELECT metadata_json FROM catalog_songs LIMIT 1").fetchone()
            metadata = json.loads(row["metadata_json"])
            metadata.update(artist="=SUM(1,2)", title='Quoted, "title"\nwith newline')
            db.execute("UPDATE catalog_songs SET metadata_json=?", (json.dumps(metadata),))
        preview, download = self.export_preview(request)
        code, result = self.catalog_post("personal/export/download", download)
        self.assertEqual(code, 200, result)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(result["bundle_base64"]))) as archive:
            self.assertEqual(archive.read("playlist.m3u8").decode().splitlines()[1], str(renamed.resolve()))
            row = next(csv.DictReader(io.StringIO(archive.read("playlist.csv").decode())))
            self.assertEqual(row["path"], str(renamed.resolve()))
            self.assertEqual(row["artist"], "'=SUM(1,2)")
            self.assertEqual(row["title"], metadata["title"])
            self.assertEqual(preview["manifest"]["entries"][0]["song"]["artist"], "=SUM(1,2)")
        newline = renamed.parent / "bad\npath.wav"
        renamed.rename(newline)
        self.scan_folder(newline.parent)
        file = next(item for item in self.catalog_get("files")["files"] if item["state"] == "available")
        self.assertEqual(self.catalog_post("decide", {**decision, "request_id": "newline-match", "base_revision": 2,
                                                    "file_id": file["id"], "content_hash": file["content_hash"],
                                                    "override": True, "note": "Synthetic deliberate conflict"})[0], 200)
        preview, _ = self.export_preview(request)
        self.assertIn("delimiter", preview["manifest"]["entries"][0]["reason"])

    def test_export_rechecks_source_after_bundle_serialization_and_reports_unreadable_bytes(self):
        source, _, _, request = self.export_fixture()
        _, download = self.export_preview(request)
        with patch.object(server.catalog, "file_hash", side_effect=PermissionError("Synthetic read denied")):
            preview, _ = self.export_preview(request)
        self.assertIn("read denied", preview["manifest"]["entries"][0]["reason"])
        original = server.playlist_export.bundle
        def mutate_during_serialization(document):
            data = original(document)
            source.write_bytes(source.read_bytes() + b"changed")
            return data
        with patch.object(server.playlist_export, "bundle", side_effect=mutate_during_serialization):
            self.assertEqual(self.catalog_post("personal/export/download", download)[0], 409)
        self.assertFalse((server.DATA / "exports").exists())

    def personal_fixture(self):
        from web.test_catalog import snapshot
        code, imported = self.catalog_post("import", snapshot())
        self.assertEqual(code, 200)
        with server.connect() as db:
            dataset = server.review_store.dataset_id(db)
        body = {"dataset_id": dataset, "request_id": "personal-create", "crate_id": "personal-1",
                "base_revision": 0, "name": "Synthetic warmup",
                "entries": [{"id": "entry-1", "snapshot_id": imported["snapshot_id"], "song_id": "song-1"}]}
        return imported, body

    def test_personal_crates_concurrent_retries_preserve_catalog_and_ordered_duplicates(self):
        imported, body = self.personal_fixture()
        body["entries"].append({**body["entries"][0], "id": "entry-2"})
        with server.connect() as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM catalog_memberships")]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.catalog_post("personal/save", body), range(12)))
        self.assertTrue(all(reply == replies[0] for reply in replies))
        self.assertEqual(replies[0][0], 200, replies)
        self.assertEqual(replies[0][1]["revision"], 1)
        crate = self.catalog_get("personal/detail", crate_id=body["crate_id"])
        self.assertEqual([entry["id"] for entry in crate["entries"]], ["entry-1", "entry-2"])
        self.assertEqual([entry["match_state"] for entry in crate["entries"]], ["unreviewed"] * 2)
        self.assertEqual(self.catalog_get("personal")["crates"][0]["entries"], 2)
        with server.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM catalog_memberships")], before)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM personal_crate_receipts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        server.initialize()
        self.assertEqual(self.catalog_post("personal/save", body), replies[0])

    def test_personal_reorder_rename_remove_and_old_retry_never_restore_previous_state(self):
        _, body = self.personal_fixture()
        body["entries"].append({**body["entries"][0], "id": "entry-2"})
        first = self.catalog_post("personal/save", body)
        changed = {**body, "request_id": "reorder", "base_revision": 1,
                   "name": "Renamed", "entries": list(reversed(body["entries"]))}
        self.assertEqual(self.catalog_post("personal/save", changed)[0], 200)
        crate = self.catalog_get("personal/detail", crate_id=body["crate_id"])
        self.assertEqual(crate["name"], "Renamed")
        self.assertEqual([entry["id"] for entry in crate["entries"]], ["entry-2", "entry-1"])
        cleared = {**changed, "request_id": "clear-entries", "base_revision": 2, "entries": []}
        self.assertEqual(self.catalog_post("personal/save", cleared)[1]["revision"], 3)
        self.assertEqual(self.catalog_post("personal/save", body), first)
        current = self.catalog_get("personal/detail", crate_id=body["crate_id"])
        self.assertEqual((current["revision"], current["entries"]), (3, []))

    def test_personal_crate_conflicts_and_wrong_dataset_cannot_overwrite(self):
        _, body = self.personal_fixture()
        self.assertEqual(self.catalog_post("personal/save", body, authenticated=False)[0], 403)
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)
        for edit in ({"request_id": "stale", "name": "Stale edit"},
                     {"name": "Reused request"}, {"dataset_id": "other"}):
            self.assertEqual(self.catalog_post("personal/save", {**body, **edit})[0], 409)
        self.assertEqual(self.catalog_get("personal/detail", crate_id=body["crate_id"])["name"], body["name"])

    def test_personal_concurrent_different_edits_commit_only_one_revision(self):
        _, body = self.personal_fixture()
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)
        requests = [{**body, "request_id": f"tab-{index}", "base_revision": 1, "name": f"Edit {index}"}
                    for index in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda request: self.catalog_post("personal/save", request), requests))
        self.assertEqual(sorted(code for code, _ in replies), [200] + [409] * 7)
        current = self.catalog_get("personal/detail", crate_id=body["crate_id"])
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["name"], next(reply["name"] for code, reply in replies if code == 200))
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM personal_crate_receipts").fetchone()[0], 2)

    def test_personal_detail_uses_one_read_snapshot_when_membership_changes_during_resolution(self):
        imported, source, decision = self.catalog_fixture()
        self.assertEqual(self.catalog_post("decide", decision)[0], 200)
        body = {"dataset_id": decision["dataset_id"], "request_id": "create-crate",
                "crate_id": "personal-1", "base_revision": 0, "name": "Snapshot",
                "entries": [{"id": f"entry-{index}", "snapshot_id": imported["snapshot_id"], "song_id": "song-1"}
                            for index in range(2)]}
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)
        original = server.catalog.file_record
        written = False
        def concurrent_save(row, root):
            nonlocal written
            if not written:
                written = True
                with server.connect() as db:
                    server.personal_crates.save(db, {**body, "request_id": "clear-concurrently",
                                                "base_revision": 1, "entries": []}, body["dataset_id"])
            return original(row, root)
        with patch.object(server.catalog, "file_record", side_effect=concurrent_save):
            crate = self.catalog_get("personal/detail", crate_id=body["crate_id"])
        self.assertEqual(crate["revision"], 1)
        self.assertEqual(len(crate["entries"]), 2)
        latest = self.catalog_get("personal/detail", crate_id=body["crate_id"])
        self.assertEqual((latest["revision"], latest["entries"]), (2, []))

    def test_personal_malformed_and_unknown_memberships_reject_entire_save(self):
        _, body = self.personal_fixture()
        for changes in ({"base_revision": True}, {"name": " "}, {"entries": None},
                        {"entries": [body["entries"][0]] * 2},
                        {"entries": [{**body["entries"][0], "song_id": "unknown"}]},
                        {"entries": [{**body["entries"][0], "extra": "invalid"}]},
                        {"entries": [{**body["entries"][0], "id": None}]}):
            self.assertEqual(self.catalog_post("personal/save", {**body, **changes})[0], 400)
        with patch.object(server.personal_crates, "MAX_ENTRIES", 0):
            self.assertEqual(self.catalog_post("personal/save", body)[0], 400)
        self.assertEqual(self.catalog_get("personal")["crates"], [])
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)

    def test_personal_receipt_failure_rolls_back_existing_crate_order_and_revision(self):
        _, body = self.personal_fixture()
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)
        before = self.catalog_get("personal/detail", crate_id=body["crate_id"])
        with server.connect() as db:
            db.execute("""CREATE TRIGGER reject_personal_receipt BEFORE INSERT ON personal_crate_receipts
                          BEGIN SELECT RAISE(ABORT,'synthetic personal receipt failure'); END""")
        change = {**body, "request_id": "remove", "base_revision": 1, "entries": []}
        code, error = self.catalog_post("personal/save", change)
        self.assertEqual(code, 503)
        self.assertIn("synthetic personal receipt failure", error["error"])
        self.assertEqual(self.catalog_get("personal/detail", crate_id=body["crate_id"]), before)
        with server.connect() as db:
            db.execute("DROP TRIGGER reject_personal_receipt")
        self.assertEqual(self.catalog_post("personal/save", change)[1]["revision"], 2)

    def test_personal_declared_thousand_entry_limit_accepts_boundary_rejects_overflow(self):
        _, body = self.personal_fixture()
        body["entries"] = [{**body["entries"][0], "id": f"entry-{index}"} for index in range(1000)]
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)
        change = {**body, "request_id": "over-limit", "base_revision": 1,
                  "entries": [*body["entries"], {**body["entries"][0], "id": "entry-1000"}]}
        self.assertEqual(self.catalog_post("personal/save", change)[0], 400)
        self.assertEqual(self.catalog_get("personal")["crates"][0]["entries"], 1000)
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT revision FROM personal_crates").fetchone()[0], 1)

    def test_personal_membership_identity_cannot_retarget_and_snapshot_provenance_is_retained(self):
        imported, body = self.personal_fixture()
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)
        from web.test_catalog import snapshot
        document = snapshot()
        document["source"] = "Second immutable source"
        other = self.catalog_post("import", document)[1]["snapshot_id"]
        retargeted = {**body, "request_id": "retarget", "base_revision": 1,
                      "entries": [{**body["entries"][0], "snapshot_id": other}]}
        self.assertEqual(self.catalog_post("personal/save", retargeted)[0], 409)
        retargeted["entries"][0]["id"] = "new-membership"
        self.assertEqual(self.catalog_post("personal/save", retargeted)[0], 200)
        self.assertEqual(self.catalog_get("personal/detail", crate_id=body["crate_id"])["entries"][0]["snapshot_id"], other)

    def test_personal_live_matches_show_offline_and_cleared_without_changing_crate_revision(self):
        imported, source, decision = self.catalog_fixture()
        self.assertEqual(self.catalog_post("decide", decision)[0], 200)
        body = {"dataset_id": decision["dataset_id"], "request_id": "personal-create",
                "crate_id": "personal-1", "base_revision": 0, "name": "Readiness",
                "entries": [{"id": "entry-1", "snapshot_id": imported["snapshot_id"], "song_id": "song-1"}]}
        self.assertEqual(self.catalog_post("personal/save", body)[0], 200)
        get = lambda: self.catalog_get("personal/detail", crate_id=body["crate_id"])
        self.assertEqual(get()["entries"][0]["match_state"], "exact")
        source.parent.rename(source.parent.with_name("disconnected"))
        offline = get()
        self.assertEqual(offline["entries"][0]["match_state"], "stale")
        self.assertEqual(offline["revision"], 1)
        self.assertEqual(self.catalog_post("decide", {**decision, "request_id": "clear", "base_revision": 1,
                                                    "file_id": None, "content_hash": None})[0], 200)
        self.assertEqual(get()["entries"][0]["match_state"], "missing")

    def catalog_post(self, endpoint, body, *, authenticated=True):
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = urllib.request.Request(base + "/api/catalog/" + endpoint,
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Origin": base,
                     "X-AutoCue-Token": server.SESSION_TOKEN if authenticated else ""})
        return self.submit_review(request)

    def catalog_get(self, endpoint, **query):
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with urllib.request.urlopen(base + "/api/catalog/" + endpoint + "?" + urllib.parse.urlencode(query)) as response:
            return json.load(response)

    def scan_folder(self, folder):
        with server.connect() as db:
            dataset = server.review_store.dataset_id(db)
        code, receipt = self.catalog_post("scan", {"path": str(folder), "dataset_id": dataset,
                                                  "request_id": str(uuid.uuid4())})
        self.assertEqual(code, 202, receipt)
        self.assertTrue(server.scan_jobs.run_next(server.connect))
        job = next(item for item in self.catalog_get("scans")["jobs"] if item["id"] == receipt["job_id"])
        self.assertEqual(job["status"], "completed", job)
        return job["result"]

    def catalog_fixture(self, audio=None):
        from web.test_catalog import snapshot
        document = snapshot()
        code, imported = self.catalog_post("import", document)
        self.assertEqual(code, 200, imported)
        folder = Path(self.temporary.name) / "indexed-music"
        folder.mkdir()
        source = folder / "Clean.wav"
        source.write_bytes(audio if audio is not None else fixtures.tagged_wav())
        self.scan_folder(folder)
        file = self.catalog_get("files")["files"][0]
        with server.connect() as db:
            dataset = server.review_store.dataset_id(db)
        decision = {"request_id": "synthetic-match", "dataset_id": dataset,
                    "snapshot_id": imported["snapshot_id"], "song_id": "song-1",
                    "base_revision": 0, "file_id": file["id"], "content_hash": file["content_hash"],
                    "note": "Synthetic review"}
        return imported, source, decision

    def test_catalog_http_import_index_and_concurrent_receipt_retries_preserve_sources(self):
        imported, source, decision = self.catalog_fixture()
        original = source.read_bytes()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.catalog_post("decide", decision), range(12)))
        self.assertTrue(all(status == 200 and receipt == replies[0][1] for status, receipt in replies))
        songs = self.catalog_get("songs", snapshot_id=imported["snapshot_id"], crate_id="crate-1")["songs"]
        self.assertEqual([song["state"] for song in songs], ["exact", "exact"])
        self.assertEqual([song["revision"] for song in songs], [1, 1])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(list(server.UPLOADS.iterdir()), [])
        self.assertEqual(self.catalog_post("decide", {**decision, "request_id": "stale"})[0], 409)
        server.initialize()
        self.assertEqual(self.catalog_post("decide", decision), replies[0])

    def test_catalog_mutations_require_session_and_correct_dataset(self):
        from web.test_catalog import snapshot
        self.assertEqual(self.catalog_post("import", snapshot(), authenticated=False)[0], 403)
        imported, _, decision = self.catalog_fixture()
        self.assertEqual(self.catalog_post("decide", {**decision, "dataset_id": "other"})[0], 409)
        self.assertIsNone(self.catalog_get("songs", snapshot_id=imported["snapshot_id"],
                                         crate_id="crate-1")["songs"][0]["decision"])

    def test_scan_admission_is_async_idempotent_and_authenticates_cancellation(self):
        folder = Path(self.temporary.name) / "scan-source"
        folder.mkdir()
        (folder / "Clean.wav").write_bytes(fixtures.tagged_wav())
        with server.connect() as db:
            dataset = server.review_store.dataset_id(db)
        body = {"dataset_id": dataset, "request_id": "http-scan", "path": str(folder)}
        self.assertEqual(self.catalog_post("scan", body, authenticated=False)[0], 403)
        self.assertEqual(self.catalog_post("scan", {**body, "dataset_id": "other"})[0], 409)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.catalog_post("scan", body), range(8)))
        self.assertTrue(all(reply == replies[0] for reply in replies))
        self.assertEqual(replies[0][0], 202)
        self.assertEqual(self.catalog_get("files")["total"], 0)
        jobs = self.catalog_get("scans")["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "queued")
        cancellation = {"dataset_id": dataset, "job_id": jobs[0]["id"]}
        self.assertEqual(self.catalog_post("scans/cancel", cancellation, authenticated=False)[0], 403)
        self.assertEqual(self.catalog_post("scans/cancel", {**cancellation, "dataset_id": "other"})[0], 409)
        first = self.catalog_post("scans/cancel", cancellation)
        self.assertEqual(first[1]["job"]["status"], "cancelled")
        self.assertEqual(self.catalog_post("scans/cancel", cancellation), first)
        self.assertFalse(server.scan_jobs.run_next(server.connect))
        shutil.rmtree(folder)
        self.assertEqual(self.catalog_post("scan", body), replies[0])

    def test_verified_reassociation_retains_matches_cue_reviews_and_historical_staging_provenance(self):
        imported, source, body = self.staging_fixture()
        self.assertEqual(self.catalog_post("stage", body)[0], 200)
        file_hash = body["content_hash"]
        server._store_plan(file_hash, {"grid": {"t1": 0, "bar_s": 2},
                                      "cues": {"B": {"name": "START", "bar": 1}}}, "Needs a look")
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        review = self.review_request(base + "/api/tracks/" + file_hash,
            data=b'{"cues":{"B":{"name":"START","bar":2}}}', method="PUT",
            headers={"Content-Type": "application/json", "Origin": base, "X-AutoCue-Token": server.SESSION_TOKEN})
        self.assertEqual(self.submit_review(review)[0], 200)
        with server.connect() as db:
            track_before = dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone())
            provenance_before = dict(db.execute("SELECT * FROM catalog_staged_tracks").fetchone())
            decision_before = dict(db.execute("SELECT * FROM catalog_match_decisions").fetchone())
        root = self.catalog_get("roots")["roots"][0]
        moved = source.parent.with_name("relocated-library")
        source.parent.rename(moved)
        request = {"request_id": "http-reassociate", "dataset_id": body["dataset_id"], "path": str(moved),
                   "root_id": root["id"], "base_revision": root["scan_revision"]}
        self.assertEqual(self.catalog_post("scan", request, authenticated=False)[0], 403)
        self.assertEqual(self.catalog_post("scan", {**request, "dataset_id": "other"})[0], 409)
        reply = self.catalog_post("scan", request)
        self.assertEqual(reply[0], 202, reply)
        self.assertTrue(server.scan_jobs.run_next(server.connect))
        job = self.catalog_get("scans")["jobs"][0]
        self.assertEqual(job["status"], "completed", job)
        self.assertTrue(job["result"]["reassociated"])
        song = self.staged_song(imported)
        self.assertEqual(song["state"], "exact")
        self.assertEqual(song["selected_file"]["id"], body["file_id"])
        self.assertEqual(song["selected_file"]["path"], str((moved / source.name).resolve()))
        self.assertTrue(song["staged_track"]["reviewed"])
        with server.connect() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()), track_before)
            self.assertEqual(dict(db.execute("SELECT * FROM catalog_staged_tracks").fetchone()), provenance_before)
            self.assertEqual(dict(db.execute("SELECT * FROM catalog_match_decisions").fetchone()), decision_before)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_root_reassociations").fetchone()[0], 1)
        self.assertEqual(len(self.corrections()), 1)
        self.assertEqual(self.catalog_post("scan", request), reply)
        # A new explicit staging action uses the new indexed location, not the
        # original provenance path; the existing reviewed track stays untouched.
        self.assertEqual(self.catalog_post("stage", {**body, "request_id": "restage-after-move"})[0], 200)
        self.assertEqual((moved / source.name).read_bytes(), server.staged_audio_path(track_before).read_bytes())

    def test_scan_http_progress_and_review_save_remain_responsive_during_hashing(self):
        file_hash, review = self.seeded_review()
        folder = Path(self.temporary.name) / "scan-source"
        folder.mkdir()
        (folder / "Clean.wav").write_bytes(fixtures.tagged_wav())
        with server.connect() as db:
            dataset = server.review_store.dataset_id(db)
        self.assertEqual(self.catalog_post("scan", {"path": str(folder), "dataset_id": dataset,
                                                   "request_id": "slow-scan"})[0], 202)
        entered, release = threading.Event(), threading.Event()
        original = server.catalog.file_hash
        def delayed_hash(path, checkpoint):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(path, checkpoint)
        with patch.object(server.catalog, "file_hash", side_effect=delayed_hash), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(server.scan_jobs.run_next, server.connect)
            try:
                self.assertTrue(entered.wait(5))
                job = self.catalog_get("scans")["jobs"][0]
                self.assertEqual(job["status"], "running")
                self.assertEqual(job["progress"]["current_path"], "Clean.wav")
                self.assertEqual(self.submit_review(review)[0], 200)
                self.assertEqual(len(self.corrections()), 1)
            finally:
                release.set()
            self.assertTrue(work.result(timeout=5))
        self.assertEqual(self.catalog_get("scans")["jobs"][0]["status"], "completed")

    def test_real_server_startup_recovers_interrupted_and_runs_durable_queued_scan(self):
        first = Path(self.temporary.name) / "first-root"
        second = Path(self.temporary.name) / "second-root"
        for folder in (first, second):
            folder.mkdir()
            (folder / "Clean.wav").write_bytes(fixtures.tagged_wav())
        self.scan_folder(first)
        with server.connect() as db:
            dataset = server.review_store.dataset_id(db)
        _, interrupted = self.catalog_post("scan", {"path": str(first), "dataset_id": dataset,
                                                   "request_id": "interrupted-before-restart"})
        _, queued = self.catalog_post("scan", {"path": str(second), "dataset_id": dataset,
                                             "request_id": "queued-before-restart"})
        with server.connect() as db:
            db.execute("UPDATE catalog_scan_jobs SET status='running' WHERE id=?", (interrupted["job_id"],))
        lines = queue.Queue()
        env = {**os.environ, "AUTOCUE_WEB_DATA": str(server.DATA), "AUTOCUE_PORT": "0",
               "AUTOCUE_CACHE_DIR": str(Path(self.temporary.name) / "cache")}
        process = subprocess.Popen([sys.executable, "-u", "-m", "web.server"], cwd=server.ROOT,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        def read_output():
            for line in process.stdout:
                lines.put(line)
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            output = []
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                line = lines.get(timeout=max(.01, deadline - time.monotonic()))
                output.append(line)
                if line.startswith("AutoCue web app: "):
                    base = line.split(": ", 1)[1].strip()
                    break
            else:
                self.fail("Server failed to start: " + "".join(output))
            deadline = time.monotonic() + 10
            while True:
                with urllib.request.urlopen(base + "/api/catalog/scans", timeout=3) as response:
                    jobs = {job["id"]: job for job in json.load(response)["jobs"]}
                if jobs[queued["job_id"]]["status"] == "completed":
                    break
                self.assertLess(time.monotonic(), deadline, jobs)
                time.sleep(.05)
            self.assertEqual(jobs[interrupted["job_id"]]["status"], "interrupted")
            self.assertEqual(len(self.catalog_get("roots")["roots"]), 2)
            self.assertEqual(self.catalog_get("files")["total"], 2)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=3)
            process.stdout.close()

    def test_catalog_storage_failure_returns_503_and_retains_previous_decision(self):
        _, _, decision = self.catalog_fixture()
        with server.connect() as db:
            db.execute("""CREATE TRIGGER reject_match BEFORE INSERT ON catalog_match_receipts
                          BEGIN SELECT RAISE(ABORT,'synthetic receipt error'); END""")
        status, error = self.catalog_post("decide", decision)
        self.assertEqual(status, 503)
        self.assertIn("synthetic receipt error", error["error"])
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_match_decisions").fetchone()[0], 0)
            db.execute("DROP TRIGGER reject_match")
        self.assertEqual(self.catalog_post("decide", decision)[0], 200)

    def test_catalog_malformed_input_and_scan_errors_are_explicit(self):
        from web.test_catalog import snapshot
        document = snapshot()
        document["crates"][0]["songs"][0]["artist"] = None
        self.assertEqual(self.catalog_post("import", document)[0], 400)
        self.assertEqual(self.catalog_get("snapshots")["snapshots"], [])
        self.assertEqual(self.catalog_post("scan", {"path": ""})[0], 400)
        folder = Path(self.temporary.name) / "bad-tags"
        folder.mkdir()
        (folder / "bad.mp3").write_bytes(b"not audio")
        scanned = self.scan_folder(folder)
        self.assertEqual(len(scanned["errors"]), 1)
        self.assertEqual(self.catalog_get("files")["files"][0]["state"], "error")

    def staging_fixture(self):
        imported, source, decision = self.catalog_fixture()
        self.assertEqual(self.catalog_post("decide", decision)[0], 200)
        body = {key: decision[key] for key in ("dataset_id", "snapshot_id", "song_id", "file_id", "content_hash")}
        body.update(request_id="synthetic-stage", decision_revision=1)
        return imported, source, body

    def staged_song(self, imported):
        return self.catalog_get("songs", snapshot_id=imported["snapshot_id"], crate_id="crate-1")["songs"][0]

    def test_catalog_staging_retries_commit_one_copy_and_provenance_without_analysis_or_source_link(self):
        imported, source, body = self.staging_fixture()
        original, mtime = source.read_bytes(), source.stat().st_mtime_ns
        with patch.object(server, "create_job", side_effect=AssertionError("analysis queued")), \
                concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.catalog_post("stage", body), range(12)))
        self.assertTrue(all(status == 200 and receipt == replies[0][1] for status, receipt in replies), replies)
        receipt = replies[0][1]
        self.assertEqual(receipt["track_hash"], body["content_hash"])
        self.assertTrue(receipt["added"])
        with server.connect() as db:
            tracks = db.execute("SELECT * FROM tracks").fetchall()
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0]["source_path"], "")
            self.assertEqual(tracks[0]["source_size"], 0)
            self.assertIsNone(tracks[0]["plan_json"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_staging_receipts").fetchone()[0], 1)
            provenance = json.loads(db.execute("SELECT provenance_json FROM catalog_staged_tracks").fetchone()[0])
        self.assertEqual(provenance["source_path"], str(source.resolve()))
        self.assertEqual(provenance["decision"]["note"], "Synthetic review")
        self.assertEqual(provenance["track_hash"], body["content_hash"])
        self.assertEqual(server.staged_audio_path(tracks[0]).read_bytes(), original)
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), (original, mtime))
        self.assertEqual(len(list(server.UPLOADS.iterdir())), 1)
        self.assertEqual(self.staged_song(imported)["staged_track"]["state"], "available")
        self.assertEqual(self.catalog_post("stage", {**body, "decision_revision": 2})[0], 409)
        server.initialize()
        self.assertEqual(self.catalog_post("stage", body), replies[0])

    def test_catalog_staging_rejects_unreviewed_stale_missing_offline_and_wrong_dataset(self):
        imported, source, decision = self.catalog_fixture()
        body = {key: decision[key] for key in ("dataset_id", "snapshot_id", "song_id", "file_id", "content_hash")}
        body.update(request_id="stage", decision_revision=1)
        self.assertEqual(self.catalog_post("stage", body)[0], 409)
        self.assertEqual(self.catalog_post("decide", decision)[0], 200)
        self.assertEqual(self.catalog_post("stage", {**body, "dataset_id": "different"})[0], 409)
        self.assertEqual(self.catalog_post("stage", {**body, "decision_revision": True})[0], 400)
        moved = source.parent.with_name("offline-music")
        source.parent.rename(moved)
        self.assertEqual(self.catalog_post("stage", body)[0], 409)
        moved.rename(source.parent)
        original = source.read_bytes()
        source.write_bytes(original + b"changed")
        self.assertEqual(self.catalog_post("stage", body)[0], 409)
        self.assertEqual(list(server.UPLOADS.iterdir()), [])
        self.assertIsNone(self.staged_song(imported)["staged_track"])

    def test_catalog_staging_rechecks_copied_hash_and_match_revision_before_committing(self):
        _, source, body = self.staging_fixture()
        with patch.object(server.catalog, "file_status", return_value="available"):
            source.write_bytes(b"changed but a stale status was returned")
            status, error = self.catalog_post("stage", body)
        self.assertEqual(status, 409)
        self.assertIn("bytes", error["error"])
        self.assertEqual(list(server.UPLOADS.iterdir()), [])

    def test_catalog_match_changed_during_copy_does_not_stage_previous_decision(self):
        _, _, body = self.staging_fixture()
        original = server.prepared_import
        @contextmanager
        def copy_then_change(*args, **kwargs):
            with original(*args, **kwargs) as copied:
                with server.connect() as db:
                    db.execute("UPDATE catalog_match_decisions SET revision=revision+1")
                yield copied
        with patch.object(server, "prepared_import", side_effect=copy_then_change):
            self.assertEqual(self.catalog_post("stage", body)[0], 409)
        self.assertEqual(list(server.UPLOADS.iterdir()), [])
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_staging_receipts").fetchone()[0], 0)

    def test_catalog_source_mutated_during_copy_is_not_published(self):
        _, source, body = self.staging_fixture()
        original = server.prepared_import
        @contextmanager
        def copy_then_change(*args, **kwargs):
            with original(*args, **kwargs) as copied:
                source.write_bytes(source.read_bytes() + b"changed during copy")
                yield copied
        with patch.object(server, "prepared_import", side_effect=copy_then_change):
            self.assertEqual(self.catalog_post("stage", body)[0], 409)
        self.assertEqual(list(server.UPLOADS.iterdir()), [])
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)

    def test_catalog_staging_retries_are_historical_even_after_new_match_or_source_disconnect(self):
        imported, source, body = self.staging_fixture()
        reply = self.catalog_post("stage", body)
        self.assertEqual(reply[0], 200)
        decision = {key: body[key] for key in ("dataset_id", "snapshot_id", "song_id")}
        decision.update(request_id="clear-after-stage", base_revision=1, file_id=None, content_hash=None)
        self.assertEqual(self.catalog_post("decide", decision)[0], 200)
        source.parent.rename(source.parent.with_name("disconnected"))
        self.assertEqual(self.catalog_post("stage", body), reply)
        self.assertIsNone(self.staged_song(imported)["staged_track"])
        self.assertEqual(self.catalog_post("stage", {**body, "request_id": "new-attempt"})[0], 409)
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 1)

    def test_catalog_retained_staged_copy_can_be_reviewed_while_original_is_offline(self):
        imported, source, body = self.staging_fixture()
        self.assertEqual(self.catalog_post("stage", body)[0], 200)
        source.parent.rename(source.parent.with_name("disconnected"))
        item = self.staged_song(imported)
        self.assertEqual(item["state"], "stale")
        self.assertEqual(item["selected_file"]["state"], "offline")
        self.assertEqual(item["staged_track"]["state"], "available")
        server._store_plan(body["content_hash"], {"cues": {}, "grid": {"t1": 0, "bar_s": 2}}, "Needs a look")
        self.assertTrue(self.staged_song(imported)["staged_track"]["has_plan"])
        self.assertEqual(self.catalog_post("stage", {**body, "request_id": "not-a-retry"})[0], 409)

    def test_catalog_staging_requires_authentication_and_explicit_complete_request(self):
        _, _, body = self.staging_fixture()
        self.assertEqual(self.catalog_post("stage", body, authenticated=False)[0], 403)
        for invalid in ({key: value for key, value in body.items() if key != "content_hash"},
                        {**body, "extra": True}, {**body, "file_id": None}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.catalog_post("stage", invalid)[0], 400)
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)

    def test_catalog_staging_failure_rolls_back_track_provenance_and_receipt_and_retries(self):
        _, source, body = self.staging_fixture()
        with server.connect() as db:
            db.execute("""CREATE TRIGGER reject_staging BEFORE INSERT ON catalog_staging_receipts
                          BEGIN SELECT RAISE(ABORT,'synthetic staging failure'); END""")
        status, error = self.catalog_post("stage", body)
        self.assertEqual(status, 503)
        self.assertIn("synthetic staging failure", error["error"])
        with server.connect() as db:
            for table in ("tracks", "catalog_staged_tracks", "catalog_staging_receipts"):
                self.assertEqual(db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)
            db.execute("DROP TRIGGER reject_staging")
        # A promoted, verified content-addressed file may survive a DB rollback; retry adopts it.
        self.assertEqual(len(list(server.UPLOADS.iterdir())), 1)
        self.assertEqual(self.catalog_post("stage", body)[0], 200)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), body["content_hash"])

    def test_catalog_staging_preserves_existing_review_and_original_source_association(self):
        _, source, body = self.staging_fixture()
        other = Path(self.temporary.name) / "originally-linked.wav"
        other.write_bytes(source.read_bytes())
        file_hash, _ = server.copy_import(other, other.name, source_path=str(other))
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=?,reviewed=1,edited=1,error='keep' WHERE hash=?",
                       (json.dumps({"cues": {"B": {"bar": 3}}, "answer_key": {"B": {"bar": 3}}}), file_hash))
            before = dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone())
        code, receipt = self.catalog_post("stage", body)
        self.assertEqual(code, 200, receipt)
        self.assertFalse(receipt["added"])
        with server.connect() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()), before)
        self.assertEqual(other.read_bytes(), source.read_bytes())

    def test_catalog_old_staging_retry_never_resurrects_removed_track_or_recreates_provenance(self):
        imported, _, body = self.staging_fixture()
        reply = self.catalog_post("stage", body)
        self.assertEqual(reply[0], 200)
        server.remove_tracks([body["content_hash"]])
        self.assertEqual(self.catalog_post("stage", body), reply)
        self.assertEqual(self.staged_song(imported)["staged_track"]["state"], "removed")
        self.assertEqual(list(server.UPLOADS.iterdir()), [])
        code, receipt = self.catalog_post("stage", {**body, "request_id": "restage-intentionally"})
        self.assertEqual(code, 200, receipt)
        self.assertEqual(self.staged_song(imported)["staged_track"]["state"], "available")
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_staged_tracks").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_staging_receipts").fetchone()[0], 2)

    def test_catalog_staging_never_overwrites_corrupt_existing_copy_and_reports_copy_failure(self):
        imported, _, body = self.staging_fixture()
        self.assertEqual(self.catalog_post("stage", body)[0], 200)
        destination = server.UPLOADS / (body["content_hash"] + ".wav")
        destination.write_bytes(b"corrupted")
        code, error = self.catalog_post("stage", {**body, "request_id": "verify-again"})
        self.assertEqual(code, 400)
        self.assertIn("hash verification", error["error"])
        self.assertEqual(destination.read_bytes(), b"corrupted")
        self.assertEqual(len(list(server.UPLOADS.glob("*.tmp"))), 0)
        with patch.object(server.os, "replace", side_effect=OSError("synthetic disk full")):
            destination.unlink()
            code, error = self.catalog_post("stage", {**body, "request_id": "disk-full"})
        self.assertEqual(code, 400)
        self.assertIn("disk full", error["error"])
        self.assertEqual(self.staged_song(imported)["staged_track"]["state"], "unavailable")

    def test_catalog_staging_library_exposes_provenance_and_existing_review_saves_still_work(self):
        imported, _, body = self.staging_fixture()
        self.assertEqual(self.catalog_post("stage", body)[0], 200)
        file_hash = body["content_hash"]
        server._store_plan(file_hash, {"grid": {"t1": 0, "bar_s": 2},
                                      "cues": {"B": {"name": "START", "bar": 1}}}, "Needs a look")
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with urllib.request.urlopen(base + "/api/library") as response:
            library = json.load(response)["tracks"]
        self.assertEqual(library[0]["catalog_sources"][0]["song_id"], body["song_id"])
        self.assertFalse(library[0]["source_available"])
        request = self.review_request(base + "/api/tracks/" + file_hash,
            data=b'{"cues":{"B":{"name":"START","bar":2}}}', method="PUT",
            headers={"Content-Type": "application/json", "Origin": base, "X-AutoCue-Token": server.SESSION_TOKEN})
        self.assertEqual(self.submit_review(request)[0], 200)
        staged = self.staged_song(imported)["staged_track"]
        self.assertTrue(staged["has_plan"])
        self.assertTrue(staged["reviewed"])
        self.assertEqual(len(self.corrections()), 1)

    def test_catalog_match_to_real_prepared_worker_to_transactional_cue_review(self):
        imported, source, decision = self.catalog_fixture(fixtures.tagged_wav(seconds=48, rate=44100))
        original = source.read_bytes()
        self.assertEqual(self.catalog_post("decide", decision)[0], 200)
        body = {key: decision[key] for key in ("dataset_id", "snapshot_id", "song_id", "file_id", "content_hash")}
        body.update(request_id="prepared-stage", decision_revision=1)
        self.assertEqual(self.catalog_post("stage", body)[0], 200)
        beats = Path(self.temporary.name) / "prepared-beats.json"
        beats.write_text(json.dumps({"beats": [i * .5 for i in range(96)],
                                     "downbeats": [i * 2 for i in range(24)]}))
        def prepared(row, settings, override, cancelled):
            return server.autocue_worker.run({
                "schema_version": 1, "job_id": "synthetic-bridge-integration",
                "profile": "prepared", "source": str(server.staged_audio_path(row)),
                "track_hash": row["hash"], "beats_path": str(beats),
                "cache_dir": str(Path(self.temporary.name) / "cache"),
                "genre": "pop", "experiments": ["baseline"],
            }, cancelled=cancelled)["plan"]
        with patch.dict(os.environ, {}, clear=False), patch.object(server, "process_builtin", side_effect=prepared):
            os.environ.pop("AUTOCUE_PROCESSOR", None)
            server.process_track(body["content_hash"])
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = self.review_request(base + "/api/tracks/" + body["content_hash"],
            data=b'{"cues":{"B":{"name":"START","bar":1}}}', method="PUT",
            headers={"Content-Type": "application/json", "Origin": base, "X-AutoCue-Token": server.SESSION_TOKEN})
        self.assertEqual(self.submit_review(request)[0], 200)
        self.assertTrue(self.staged_song(imported)["staged_track"]["reviewed"])
        with server.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (body["content_hash"],)).fetchone()
            plan = json.loads(row["plan_json"])
            self.assertEqual(plan["provenance"]["profile"], "prepared")
            self.assertEqual(plan["cues"]["B"]["bar"], 1)
            self.assertEqual(plan["answer_key"]["cues"]["B"]["bar"], 1)
            self.assertEqual(row["source_path"], "")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(self.corrections()), 1)

    def review_request(self, url, data, **kwargs):
        body = json.loads(data)
        with server.connect() as db:
            revision = db.execute("SELECT review_revision FROM tracks WHERE hash=?",
                                  (url.rsplit("/", 1)[-1],)).fetchone()[0]
            body.update(request_id=str(uuid.uuid4()), dataset_id=server.review_store.dataset_id(db),
                        base_revision=revision)
        return urllib.request.Request(url, data=json.dumps(body).encode(), **kwargs)

    def corrections(self):
        with server.connect() as db:
            return server.review_store.export_events(db)

    def seeded_review(self, name="song.mp3"):
        source = Path(self.temporary.name) / name
        source.write_bytes(name.encode())
        file_hash, _ = server.copy_import(source, source.name)
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps({
                "grid": {"t1": 0.125, "bar_s": 2}, "cues": {"A": {"bar": 2, "name": "INTRO"}}
            }), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        return file_hash, self.review_request(base + f"/api/tracks/{file_hash}",
            data=b'{"cues":{"A":{"name":"INTRO","bar":3}}}', method="PUT",
            headers={"Content-Type": "application/json", "Origin": base,
                     "X-AutoCue-Token": server.SESSION_TOKEN})

    def submit_review(self, request):
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_concurrent_retries_return_one_committed_receipt_and_event(self):
        file_hash, request = self.seeded_review()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(self.submit_review, [request] * 16))
        self.assertTrue(all(status == 200 for status, _ in responses))
        self.assertTrue(all(receipt == responses[0][1] for _, receipt in responses))
        receipt = responses[0][1]
        self.assertEqual(receipt["track_hash"], file_hash)
        self.assertEqual(receipt["revision"], 2)
        self.assertEqual(receipt["sequence"], 1)
        self.assertEqual(len(self.corrections()), 1)
        server.initialize()
        self.assertEqual(self.submit_review(request), responses[0])

    def test_concurrent_tracks_keep_every_event_and_receipt(self):
        requests = [self.seeded_review(f"synthetic-{i}.mp3")[1] for i in range(12)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(self.submit_review, requests))
        self.assertEqual([status for status, _ in responses], [200] * 12)
        events = self.corrections()
        self.assertEqual([event["sequence"] for event in events], list(range(1, 13)))
        self.assertEqual(len({event["track_hash"] for event in events}), 12)
        self.assertEqual(len({event["event_id"] for event in events}), 12)

    def test_stale_review_and_reused_id_cannot_overwrite_corrections(self):
        file_hash, request = self.seeded_review()
        body = json.loads(request.data)
        self.assertEqual(self.submit_review(request)[0], 200)
        for edit in (
            {"request_id": str(uuid.uuid4()), "cues": {"A": {"bar": 4}}},
            {"cues": {"A": {"bar": 4}}},
            {"dataset_id": "different-library", "request_id": str(uuid.uuid4())},
        ):
            request.data = json.dumps({**body, **edit}).encode()
            status, error = self.submit_review(request)
            self.assertEqual(status, 409, error)
        with server.connect() as db:
            plan = json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])
        self.assertEqual(plan["cues"]["A"]["bar"], 3)
        self.assertEqual(len(self.corrections()), 1)

    def test_two_tabs_at_same_revision_do_not_silently_overwrite(self):
        _, request = self.seeded_review()
        other = urllib.request.Request(request.full_url,
            data=json.dumps({**json.loads(request.data), "request_id": str(uuid.uuid4()),
                             "cues": {"A": {"bar": 4}}}).encode(),
            method="PUT", headers=dict(request.header_items()))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(self.submit_review, [request, other]))
        self.assertEqual(sorted(status for status, _ in responses), [200, 409])
        self.assertEqual(len(self.corrections()), 1)

    def test_receipt_failure_rolls_back_events_revision_and_plan_then_retries(self):
        file_hash, request = self.seeded_review()
        with server.connect() as db:
            before = dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone())
            db.execute("""CREATE TRIGGER reject_receipt BEFORE INSERT ON review_receipts
                          BEGIN SELECT RAISE(ABORT, 'fixture commit failure'); END""")
        status, error = self.submit_review(request)
        self.assertEqual(status, 503)
        self.assertIn("fixture commit failure", error["error"])
        self.assertEqual(self.corrections(), [])
        with server.connect() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()), before)
            db.execute("DROP TRIGGER reject_receipt")
        self.assertEqual(self.submit_review(request)[0], 200)
        self.assertEqual(len(self.corrections()), 1)

    def test_reanalysis_invalidates_old_revision_and_protocol_is_required(self):
        file_hash, request = self.seeded_review()
        server._store_plan(file_hash, {"grid": {"t1": 0, "bar_s": 2}, "cues": {}}, "Needs a look")
        self.assertEqual(self.submit_review(request)[0], 409)
        body = json.loads(request.data)
        del body["request_id"]
        request.data = json.dumps(body).encode()
        self.assertEqual(self.submit_review(request)[0], 400)
        self.assertEqual(self.corrections(), [])

    def test_old_receipt_retry_does_not_restore_an_earlier_plan(self):
        file_hash, first = self.seeded_review()
        original = self.submit_review(first)
        next_request = self.review_request(first.full_url,
            data=b'{"cues":{"A":{"name":"INTRO","bar":4}}}', method="PUT",
            headers=dict(first.header_items()))
        self.assertEqual(self.submit_review(next_request)[0], 200)
        self.assertEqual(self.submit_review(first), original)
        with server.connect() as db:
            row = db.execute("SELECT review_revision,plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        self.assertEqual(row["review_revision"], 3)
        self.assertEqual(json.loads(row["plan_json"])["cues"]["A"]["bar"], 4)
        self.assertEqual(len(self.corrections()), 2)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        server.DATA = root / "data"
        server.UPLOADS = server.DATA / "audio"
        server.DB_PATH = server.DATA / "library.sqlite3"
        server.SESSION_TOKEN = "test-token"
        server.initialize()
        self.http = server.LocalHTTPServer(("127.0.0.1", 0), server.Handler)
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.http_thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.http_thread.join(timeout=2)
        self.temporary.cleanup()

    def test_parallel_catalog_startup_burst_does_not_reset_connections(self):
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        paths = ["/api/session", "/api/catalog/snapshots", "/api/catalog/roots",
                 "/catalog.js", "/catalog-state.js", "/api/library", "/api/settings", "/api/jobs"] * 3
        def fetch(path):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                response.read()
                return response.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            self.assertEqual(list(pool.map(fetch, paths)), [200] * len(paths))

    def test_import_hash_deduplicates_renamed_files_and_links_original(self):
        root = Path(self.temporary.name)
        first = root / "first.mp3"
        renamed = root / "renamed.mp3"
        first.write_bytes(b"same audio content")
        renamed.write_bytes(first.read_bytes())

        first_hash, added_first = server.copy_import(first, first.name, "crate/first.mp3", str(first))
        second_hash, added_second = server.copy_import(renamed, renamed.name, "crate/renamed.mp3", str(renamed))

        self.assertEqual(first_hash, hashlib.sha256(first.read_bytes()).hexdigest())
        self.assertEqual(first_hash, second_hash)
        self.assertTrue(added_first)
        self.assertFalse(added_second)
        with server.connect() as db:
            row = db.execute("SELECT source_path,audio_path FROM tracks WHERE hash=?", (first_hash,)).fetchone()
        self.assertEqual(row["source_path"], str(renamed))
        self.assertEqual(row["audio_path"], "audio/" + first_hash + ".mp3")
        self.assertEqual(first.read_bytes(), b"same audio content")

    def test_waveform_detail_endpoint_resolves_track_and_rejects_unknown_hash(self):
        import waveform
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"local audio")
        file_hash, _ = server.copy_import(source, source.name, source.name, str(source))
        url = f"http://127.0.0.1:{self.http.server_address[1]}/api/waveform/{file_hash}?detail=1&start=1.25&end=3.5&points=512"
        with patch.object(waveform, "query", return_value={"duration": 8, "columns": []}) as query:
            with urllib.request.urlopen(url) as response:
                self.assertEqual(json.load(response)["duration"], 8)
        self.assertEqual(Path(query.call_args.args[0]), (server.UPLOADS / (file_hash + ".mp3")).resolve())
        self.assertEqual(query.call_args.kwargs, {"start": 1.25, "end": 3.5, "points": 512})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url.replace(file_hash, "unknown"))
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(_invalid_waveform_points_url(url))
        self.assertEqual(error.exception.code, 400)
        with patch.object(waveform, "query", side_effect=RuntimeError("Waveform generation requires ffmpeg.")):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url)
            self.assertIn("requires ffmpeg", error.exception.read().decode())

    def test_remove_tracks_keeps_source_and_heals_moved_audio_paths(self):
        root = Path(self.temporary.name)
        source = root / "song.mp3"
        source.write_bytes(b"audio one")
        other = root / "other.mp3"
        other.write_bytes(b"audio two")
        first, _ = server.copy_import(source, source.name, "song.mp3", str(source))
        second, _ = server.copy_import(other, other.name, "other.mp3", str(other))
        with server.connect() as db:
            db.execute("UPDATE tracks SET audio_path=? WHERE hash=?", ("/old/location/" + first + ".mp3", first))
        server.initialize()
        with server.connect() as db:
            healed = db.execute("SELECT audio_path FROM tracks WHERE hash=?", (first,)).fetchone()[0]
        self.assertEqual(healed, "audio/" + first + ".mp3")

        self.assertEqual(server.remove_tracks([first])["removed"], 1)
        self.assertFalse((server.UPLOADS / (first + ".mp3")).exists())
        self.assertTrue(source.exists())
        self.assertEqual(server.remove_tracks(None)["removed"], 1)
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)

    def test_moved_library_prefers_local_copy_even_when_old_absolute_path_exists(self):
        root = Path(self.temporary.name)
        source = root / "song.mp3"
        source.write_bytes(b"original music")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        old_audio = server.UPLOADS / (file_hash + ".mp3")
        plan = {"grid": {"t1": 0, "bar_s": 2}, "cues": {}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET audio_path=?,plan_json=?,error=? WHERE hash=?",
                       (str(old_audio), json.dumps(plan), "Existing analysis error", file_hash))
        moved = root / "moved library"
        shutil.move(server.DATA, moved)
        old_audio.parent.mkdir(parents=True)
        old_audio.write_bytes(b"stale copy in previous location")
        server.DATA, server.UPLOADS, server.DB_PATH = moved, moved / "audio", moved / "library.sqlite3"
        server.initialize()
        server.initialize()
        with server.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        self.assertEqual(row["audio_path"], "audio/" + file_hash + ".mp3")
        self.assertEqual(row["error"], "Existing analysis error")
        self.assertEqual(row["source_path"], str(source))
        self.assertEqual(server.staged_audio_path(row), (server.UPLOADS / old_audio.name).resolve())
        self.assertEqual(server.track_record(row)["url"], "/media/" + file_hash)
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with urllib.request.urlopen(base + "/media/" + file_hash) as response:
            self.assertEqual(response.read(), b"original music")
        self.assertEqual(server.remove_tracks([file_hash]), {"removed": 1})
        self.assertEqual(old_audio.read_bytes(), b"stale copy in previous location")
        self.assertEqual(source.read_bytes(), b"original music")

    def test_legacy_absolute_path_inside_library_migrates_without_changing_audio(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"music")
        file_hash, _ = server.copy_import(source, source.name)
        staged = server.UPLOADS / (file_hash + ".mp3")
        with server.connect() as db:
            db.execute("UPDATE tracks SET audio_path=? WHERE hash=?", (str(staged), file_hash))
            db.executescript("""
                CREATE TABLE migration_updates(track_hash TEXT);
                CREATE TRIGGER audit_path_updates AFTER UPDATE ON tracks BEGIN
                    INSERT INTO migration_updates VALUES (NEW.hash);
                END;
            """)
        server.initialize()
        server.initialize()
        with server.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM migration_updates").fetchone()[0], 1)
        self.assertEqual(row["audio_path"], "audio/" + staged.name)
        self.assertEqual(staged.read_bytes(), source.read_bytes())

    def test_missing_staged_audio_is_reported_not_replaced_with_original(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"music")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        staged = server.UPLOADS / (file_hash + ".mp3")
        staged.unlink()
        with server.connect() as db:
            db.execute("UPDATE tracks SET audio_path=?,error='Prior failure' WHERE hash=?",
                       (str(Path(self.temporary.name) / staged.name), file_hash))
        server.initialize()
        server.initialize()
        with server.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        self.assertEqual(row["error"].count("Staged audio unavailable:"), 1)
        self.assertTrue(row["error"].startswith("Prior failure\n"))
        with self.assertRaisesRegex(FileNotFoundError, "missing from this library"):
            server.staged_audio_path(row)
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/media/" + file_hash)
        self.assertEqual(error.exception.code, 404)
        staged.write_bytes(source.read_bytes())
        server.initialize()
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT error FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0],
                             "Prior failure")

    def test_invalid_staged_paths_cannot_be_read_processed_or_deleted(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"music")
        file_hash, _ = server.copy_import(source, source.name)
        staged = server.UPLOADS / (file_hash + ".mp3")
        invalid_paths = ["../" + staged.name, "audio/../audio/" + staged.name,
                         str(source), "audio/another.mp3"]
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        for invalid in invalid_paths:
            with self.subTest(path=invalid):
                with server.connect() as db:
                    db.execute("UPDATE tracks SET audio_path=? WHERE hash=?", (invalid, file_hash))
                with self.assertRaisesRegex(ValueError, "Invalid staged audio path"):
                    server.remove_tracks([file_hash])
                with self.assertRaisesRegex(ValueError, "Invalid staged audio path"):
                    server.process_track(file_hash)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + "/media/" + file_hash)
                self.assertEqual(error.exception.code, 400)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + "/api/waveform/" + file_hash + "?detail=1")
                self.assertEqual(error.exception.code, 400)
        self.assertEqual(staged.read_bytes(), source.read_bytes())
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 1)

    def test_staged_symlinks_are_rejected_and_never_unlinked(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"music")
        file_hash, _ = server.copy_import(source, source.name)
        staged = server.UPLOADS / (file_hash + ".mp3")
        staged.unlink()
        staged.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            server.remove_tracks([file_hash])
        self.assertTrue(staged.is_symlink())
        self.assertEqual(source.read_bytes(), b"music")
        staged.unlink()
        server.UPLOADS.rmdir()
        server.UPLOADS.symlink_to(source.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "not a symlink"):
            server.copy_import(source, source.name)
        with self.assertRaisesRegex(ValueError, "not a symlink"):
            server.remove_tracks([file_hash])

    def test_delete_failure_keeps_failed_track_and_reports_partial_removal(self):
        root = Path(self.temporary.name)
        source = root / "song.mp3"
        source.write_bytes(b"one")
        first, _ = server.copy_import(source, source.name)
        source.write_bytes(b"two")
        second, _ = server.copy_import(source, source.name)
        original_unlink = Path.unlink

        def fail_one(path, *args, **kwargs):
            if path.name == first + ".mp3":
                raise PermissionError("test deletion denied")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_one):
            with self.assertRaisesRegex(RuntimeError, "Removed 1 tracks.*kept in library.*deletion denied"):
                server.remove_tracks(None)
        with server.connect() as db:
            self.assertEqual([row[0] for row in db.execute("SELECT hash FROM tracks")], [first])
        self.assertTrue((server.UPLOADS / (first + ".mp3")).exists())
        self.assertFalse((server.UPLOADS / (second + ".mp3")).exists())
        self.assertTrue(source.is_file())

    def test_processors_receive_absolute_resolved_staged_paths(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"music")
        file_hash, _ = server.copy_import(source, source.name)
        expected = str((server.UPLOADS / (file_hash + ".mp3")).resolve())
        analyze = server._analysis_import()
        plan = {"cues": {}}
        with patch.dict(os.environ, {}, clear=False), patch.object(analyze, "analyze", return_value=plan) as run, \
                patch.object(server, "_tags", return_value=("", "", "")) as tags:
            os.environ.pop("AUTOCUE_PROCESSOR", None)
            server.process_track(file_hash)
        self.assertEqual(str(run.call_args.args[0]), expected)
        tags.assert_called_once_with(expected)

        def external(argv, **kwargs):
            self.assertEqual(argv[1], expected)
            Path(argv[2]).write_text(json.dumps(plan))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.dict(os.environ, {"AUTOCUE_PROCESSOR": 'fake "{input}" "{output}"'}), \
                patch.object(subprocess, "run", side_effect=external) as run:
            server.process_track(file_hash)
        self.assertEqual(run.call_count, 1)

    def test_cancelled_analysis_preserves_reviewed_track_and_source_for_both_processors(self):
        file_hash, _ = self.seeded_review()
        with server.connect() as db:
            db.execute("UPDATE tracks SET reviewed=1,edited=1,error='previous error' WHERE hash=?", (file_hash,))
            before = dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone())
        source = server.staged_audio_path(before)
        original = source.read_bytes()
        for external in (False, True):
            stop = threading.Event()

            def built_in(*args):
                stop.set()
                return {"cues": {}}

            def custom(argv, **kwargs):
                stop.set()
                Path(argv[2]).write_text('{"cues":{}}')
                return subprocess.CompletedProcess(argv, 0, "", "")

            with self.subTest(external=external), \
                    patch.dict(os.environ, {"AUTOCUE_PROCESSOR": 'fake "{input}" "{output}"'} if external else {}), \
                    patch.object(server, "process_builtin", side_effect=built_in), \
                    patch.object(subprocess, "run", side_effect=custom):
                if not external:
                    os.environ.pop("AUTOCUE_PROCESSOR", None)
                with self.assertRaises(server.autocue_worker.analyze.AnalysisCancelled):
                    server.process_track(file_hash, stop.is_set)
            with server.connect() as db:
                self.assertEqual(dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()), before)
            self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.corrections(), [])

    def test_cancelled_active_job_does_not_count_track_completed_or_save_error(self):
        file_hash, _ = self.seeded_review()
        with server.connect() as db:
            db.execute("INSERT INTO jobs(id,status,track_hashes,completed,created_at,updated_at,message) "
                       "VALUES('cancel-test','queued',?,0,1,1,'queued')", (json.dumps([file_hash]),))
            before = dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone())
        jobs = queue.Queue()
        jobs.put("cancel-test")
        get = jobs.get
        calls = 0

        def next_job():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise StopIteration
            return get()

        def cancel_track(track, cancelled):
            self.assertEqual(track, file_hash)
            self.assertFalse(cancelled())
            server.cancelled_jobs.add("cancel-test")
            self.assertTrue(cancelled())
            raise server.autocue_worker.analyze.AnalysisCancelled("Synthetic cancellation")

        with patch.object(server, "worker_queue", jobs), patch.object(jobs, "get", side_effect=next_job), \
                patch.object(server, "process_track", side_effect=cancel_track):
            with self.assertRaises(StopIteration):
                server.worker()
        with server.connect() as db:
            job = db.execute("SELECT * FROM jobs WHERE id='cancel-test'").fetchone()
            self.assertEqual(job["status"], "cancelled")
            self.assertEqual(job["completed"], 0)
            self.assertEqual(dict(db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()), before)
        self.assertEqual(jobs.unfinished_tasks, 0)
        self.assertNotIn("cancel-test", server.cancelled_jobs)

    def test_library_exposes_outro_evidence_and_cue_warnings_for_review(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"music")
        file_hash, _ = server.copy_import(source, source.name)
        evidence = {"status": "analyzed", "last_vocal_end_s": 90.1, "ignored_words": 2,
                    "threshold": 0.003, "coverage_s": 100.0}
        mix_evidence = {"intro": {"status": "unavailable"}, "intro_loop_fallback": {
            "status": "needs_serato_fixture", "applied": False, "proposed_start_bar": 0,
            "loop": {"start_bar": 40, "end_bar": 48, "bars": 8, "beats": 32}}}
        plan = {"cues": {"B": {"bar": 8, "time_s": 15.094, "unsnapped": True},
                         "H": {"bar": None, "reason": "insufficient instrumental outro",
                               "flags": ["configured window unavailable"]}},
                "mix_window_evidence": mix_evidence,
                "outro_vocal_evidence": evidence, "no_vocal_free_outro": True}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with urllib.request.urlopen(base + "/api/library") as response:
            record = json.load(response)["tracks"][0]
        self.assertEqual(record["outro_vocal_evidence"], evidence)
        self.assertEqual(record["mix_window_evidence"], mix_evidence)
        self.assertEqual(record["cues"]["B"]["time_s"], 15.094)
        self.assertTrue(record["cues"]["B"]["unsnapped"])
        self.assertTrue(record["no_vocal_free_outro"])
        self.assertEqual(record["cues"]["H"]["reason"], "insufficient instrumental outro")
        self.assertEqual(record["cues"]["H"]["flags"], ["configured window unavailable"])

    def test_map_override_endpoint_stores_choice_and_queues_a_job(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"audio")
        file_hash, _ = server.copy_import(source, source.name, "song.mp3", str(source))
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=?, status='Cues ready' WHERE hash=?",
                       (json.dumps({"cues": {}, "map_type": "DROP"}), file_hash))
        url = f"http://127.0.0.1:{self.http.server_address[1]}/api/tracks/{file_hash}/map"
        def post(choice):
            request = urllib.request.Request(url, json.dumps({"map_type": choice}).encode(), method="POST",
                headers={"Content-Type": "application/json", "X-AutoCue-Token": server.SESSION_TOKEN,
                         "Origin": f"http://127.0.0.1:{self.http.server_address[1]}"})
            return urllib.request.urlopen(request)
        self.assertEqual(post("vocal").status, 200)
        with server.connect() as db:
            self.assertEqual(json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])["map_override"], "VOCAL")
        post("auto")
        with server.connect() as db:
            self.assertNotIn("map_override", json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0]))
        with self.assertRaises(urllib.error.HTTPError):
            post("bogus")

    def test_crate_parser_reads_tagged_path_and_reports_missing_song(self):
        root = Path(self.temporary.name)
        present = root / "present.mp3"
        present.write_bytes(b"local track")
        missing = root / "missing.wav"

        def chunk(tag, value):
            return tag + len(value).to_bytes(4, "big") + value

        crate = root / "test.crate"
        body = chunk(b"ptrk", str(present).encode()) + chunk(b"pfil", str(missing).encode())
        crate.write_bytes(chunk(b"otrk", body))

        songs = server.crate_songs(crate)
        self.assertEqual([song["path"] for song in songs], [str(present), str(missing)])
        self.assertEqual([song["missing"] for song in songs], [False, True])

    def test_marker2_update_preserves_loops_and_replaces_only_owned_cue_slots(self):
        old_cue = b"\x00\x01" + (5000).to_bytes(4, "big") + b"\x00\xff\x00\x00\x00\x00" + b"old\x00"
        loop = b"\x00\x00" + (4000).to_bytes(4, "big") + (8000).to_bytes(4, "big") + b"\xff" * 10 + b"loop\x00"
        old_entries = (
            b"CUE\x00" + len(old_cue).to_bytes(4, "big") + old_cue
            + b"LOOP\x00" + len(loop).to_bytes(4, "big") + loop
        )
        original_geob = b"\x01\x01" + base64.b64encode(b"\x01\x01" + old_entries) + b"\x00"
        updated = server._cue_binary({"A": {"bar": 4, "name": "INTRO", "color": "#CC0000"}}, {
            "grid": {"t1": 0, "bar_s": 2},
        }, original_geob)

        entries = server._marker2_entries(updated)
        cue_entries = [payload for name, payload in entries if name == "CUE"]
        self.assertEqual(sum(name == "LOOP" for name, _ in entries), 1)
        self.assertEqual(len(cue_entries), 1)
        self.assertEqual(cue_entries[0][1], 0)
        self.assertEqual(int.from_bytes(cue_entries[0][2:6], "big"), 8000)
        self.assertEqual(cue_entries[0][7:10], bytes.fromhex("CC0000"))
        self.assertTrue(cue_entries[0][12:].startswith(b"INTRO"))

    def test_cue_map_settings_are_normalized_persisted_and_returned(self):
        maps = server._cue_maps_module()
        settings = json.loads(json.dumps(maps.DEFAULT_CUE_MAPS))
        settings["mode"] = "DROP"
        settings["maps"]["DROP"]["F"]["label"] = "MAIN DROP"
        url = f"http://127.0.0.1:{self.http.server_address[1]}/api/settings"
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.http.server_address[1]}",
            "X-AutoCue-Token": server.SESSION_TOKEN,
        }
        request = urllib.request.Request(url, data=json.dumps({"cue_maps": settings}).encode(),
                                         method="POST", headers=headers)
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)

        with urllib.request.urlopen(url) as response:
            saved = json.load(response)
        self.assertEqual(saved["cue_maps"]["mode"], "DROP")
        self.assertEqual(saved["cue_maps"]["maps"]["DROP"]["F"]["label"], "MAIN DROP")
        self.assertEqual(saved["cue_map_catalog"]["palette"][0]["metadata"], "#CC0000")

        invalid = json.loads(json.dumps(settings))
        invalid["maps"]["DROP"]["F"]["color"] = []
        request = urllib.request.Request(url, data=json.dumps({"cue_maps": invalid}).encode(),
                                         method="POST", headers=headers)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code, 400)
        with urllib.request.urlopen(url) as response:
            self.assertEqual(json.load(response)["cue_maps"]["maps"]["DROP"]["F"]["color"],
                             "yellow")

    def test_experiment_settings_report_and_export(self):
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        headers = {"Content-Type": "application/json", "Origin": base,
                   "X-AutoCue-Token": server.SESSION_TOKEN}
        with urllib.request.urlopen(base + "/api/settings") as response:
            saved = json.load(response)
        self.assertIn("kick_grid", saved["experiments"])
        self.assertNotIn("roformer_stems", saved["experiments"])
        request = urllib.request.Request(base + "/api/settings", data=json.dumps(
            {"experiments": ["kick_grid"]}).encode(), method="POST", headers=headers)
        urllib.request.urlopen(request).close()
        with urllib.request.urlopen(base + "/api/settings") as response:
            self.assertEqual(json.load(response)["experiments"], ["baseline", "kick_grid"])
        bad = urllib.request.Request(base + "/api/settings", data=json.dumps(
            {"experiments": ["nope"]}).encode(), method="POST", headers=headers)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(bad)
        self.assertEqual(raised.exception.code, 400)

        source = Path(self.temporary.name) / "graded.mp3"
        source.write_bytes(b"graded")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        cue = lambda bar: {"bar": bar, "time_s": bar * 2.0}
        plan = {"map_type": "VOCAL", "grid": {"t1": 0, "bar_s": 2.0}, "cues": {"A": cue(8)},
                "variants": {"baseline": {"status": "ok", "label": "Baseline v6", "cues": {"A": cue(16)}},
                             "kick_grid": {"status": "ok", "label": "Kick", "cues": {"A": cue(8)}}}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=?, reviewed=1 WHERE hash=?", (json.dumps(plan), file_hash))
        with urllib.request.urlopen(base + "/api/experiments/report") as response:
            report = json.load(response)
        self.assertEqual(report["graded_songs"], 1)
        self.assertEqual(report["variants"]["kick_grid"]["wins"], 1)
        with urllib.request.urlopen(base + "/api/experiments/export") as response:
            exported = json.load(response)
        self.assertEqual(exported["tracks"][0]["plan"]["variants"]["baseline"]["cues"]["A"]["bar"], 16)
        self.assertEqual(exported["schema_version"], 2)
        self.assertEqual(exported["answer_keys"][0]["hash"], file_hash)
        self.assertEqual(exported["answer_keys"][0]["answer"]["cues"]["A"]["bar"], 8)
        self.assertEqual(exported["answer_keys"][0]["answer"]["source"], "human_review")
        self.assertNotIn("B", exported["answer_keys"][0]["answer"]["cues"])

    def test_reanalysis_keeps_reviewed_cues_as_the_scoring_answer_key(self):
        source = Path(self.temporary.name) / "again.mp3"
        source.write_bytes(b"again")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        cue = lambda bar: {"bar": bar, "time_s": bar * 2.0}
        reviewed = {"map_type": "VOCAL", "grid": {"t1": 0, "bar_s": 2.0}, "cues": {"A": cue(8)}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=?, reviewed=1, edited=1, status='Edited by you' WHERE hash=?",
                       (json.dumps(reviewed), file_hash))
        fresh = {"map_type": "VOCAL", "grid": {"t1": 0, "bar_s": 2.0}, "cues": {"A": cue(16)},
                 "variants": {"baseline": {"status": "ok", "label": "Baseline", "cues": {"A": cue(16)}},
                              "kick_grid": {"status": "ok", "label": "Kick", "cues": {"A": cue(8)}}}}
        server._store_plan(file_hash, fresh, "Cues ready")
        with server.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        self.assertEqual(row["reviewed"], 0)
        self.assertEqual(json.loads(row["plan_json"])["answer_key"]["cues"]["A"]["bar"], 8)
        report = server._experiments_module().score_tracks(server._graded_tracks())
        self.assertEqual(report["variants"]["kick_grid"]["wins"], 1)
        # a second re-analysis still keeps the original key
        server._store_plan(file_hash, dict(fresh), "Cues ready")
        with server.connect() as db:
            again = json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])
        self.assertEqual(again["answer_key"]["cues"]["A"]["bar"], 8)

    def test_review_freezes_automatic_predictions_before_edit_and_partial_reviews_merge(self):
        source = Path(self.temporary.name) / "partial.mp3"
        source.write_bytes(b"partial review")
        file_hash, _ = server.copy_import(source, source.name)
        original = {"map_type": "VOCAL", "grid": {"t1": 0, "bar_s": 2, "offset_ms": 800},
                    "cues": {"A": {"bar": 10, "name": "INTRO", "meaning": "intro_start"},
                             "B": {"bar": 20, "name": "START", "meaning": "start"}}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(original), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"

        def review(cues):
            request = self.review_request(base + f"/api/tracks/{file_hash}",
                data=json.dumps({"cues": cues}).encode(), method="PUT",
                headers={"Content-Type": "application/json", "Origin": base,
                         "X-AutoCue-Token": server.SESSION_TOKEN})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 200)

        review({"A": {"bar": 11, "name": "MY INTRO"}})
        with server.connect() as db:
            saved = json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])
        self.assertEqual(saved["variants"]["baseline"]["cues"]["A"]["bar"], 10)
        self.assertEqual(saved["answer_key"]["cues"]["A"]["bar"], 11)
        self.assertNotIn("B", saved["answer_key"]["cues"])
        self.assertEqual(saved["answer_key"]["cues"]["A"]["meaning"], "intro_start")
        self.assertEqual(saved["answer_key"]["cues"]["A"]["name"], "MY INTRO")
        with urllib.request.urlopen(base + "/api/experiments/report") as response:
            report = json.load(response)
        self.assertEqual(report["variants"]["baseline"]["total"], 1)
        self.assertEqual(report["variants"]["baseline"]["right"], 0)

        fresh = {"map_type": "DROP", "grid": {"t1": 1, "bar_s": 4},
                 "cues": {"A": {"bar": 99, "name": "INTRO", "meaning": "intro_start"},
                          "B": {"bar": 12, "name": "START", "meaning": "start"}}}
        server._store_plan(file_hash, fresh, "Cues ready")
        review({"B": {"bar": None, "name": "START"}})
        with server.connect() as db:
            latest = json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])
        answer = latest["answer_key"]
        self.assertAlmostEqual(server.bar_to_time(answer["cues"]["A"]["bar"], answer["grid"]), 21.2)
        self.assertEqual(answer["cues"]["A"]["name"], "MY INTRO")
        self.assertEqual(answer["cues"]["A"]["map_type"], "VOCAL")
        self.assertIsNone(answer["cues"]["B"]["bar"])
        self.assertEqual(latest["variants"]["baseline"]["cues"]["B"]["bar"], 12)

    def test_stored_ai_predictions_keep_rules_and_ai_snapshots_distinct_after_review(self):
        source = Path(self.temporary.name) / "ai.mp3"
        source.write_bytes(b"ai review")
        file_hash, _ = server.copy_import(source, source.name)
        rules = {"map_type": "DROP", "grid": {"t1": 0, "bar_s": 2},
                 "cues": {"D": {"bar": 24, "time_s": 48, "name": "FIRST DROP", "meaning": "first_drop_start"}}}
        assisted = {"map_type": "DROP", "grid": rules["grid"],
                    "cues": {"D": {"bar": 25, "time_s": 50, "name": "FIRST DROP", "meaning": "first_drop_start"}},
                    "gemini": {"used": True, "baseline": rules, "new_cost_usd": 0.01, "cached": False}}
        server._store_plan(file_hash, assisted, "Cues ready")
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = self.review_request(base + f"/api/tracks/{file_hash}",
            data=json.dumps({"cues": {"D": {"bar": 25, "name": "FIRST DROP"}}}).encode(), method="PUT",
            headers={"Content-Type": "application/json", "Origin": base,
                     "X-AutoCue-Token": server.SESSION_TOKEN})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
        with urllib.request.urlopen(base + "/api/experiments/export") as response:
            exported = json.load(response)
        report = exported["report"]
        self.assertEqual(report["variants"]["baseline"]["positioned_right"], 0)
        self.assertEqual(report["variants"]["gemini"]["positioned_right"], 1)
        self.assertEqual(report["variants"]["gemini"]["wins"], 1)
        self.assertEqual(report["gemini_cost"]["new_cost_usd"], 0.01)
        self.assertEqual(exported["tracks"][0]["plan"]["variants"]["baseline"]["cues"]["D"]["bar"], 24)
        self.assertEqual(exported["tracks"][0]["plan"]["variants"]["gemini"]["cues"]["D"]["bar"], 25)

    def test_legacy_review_without_predictions_cannot_self_score_on_resave(self):
        source = Path(self.temporary.name) / "old.mp3"
        source.write_bytes(b"old reviewed song")
        file_hash, _ = server.copy_import(source, source.name)
        plan = {"map_type": "VOCAL", "grid": {"t1": 0, "bar_s": 2},
                "cues": {"B": {"bar": 10, "name": "START"}}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=?,reviewed=1 WHERE hash=?", (json.dumps(plan), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = self.review_request(base + f"/api/tracks/{file_hash}",
            data=json.dumps({"cues": {"B": {"bar": 10, "name": "START"}}}).encode(), method="PUT",
            headers={"Content-Type": "application/json", "Origin": base,
                     "X-AutoCue-Token": server.SESSION_TOKEN})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
        with urllib.request.urlopen(base + "/api/experiments/report") as response:
            report = json.load(response)
        self.assertEqual(report["graded_songs"], 0)
        self.assertEqual(report["variants"], {})
        self.assertIn("no saved pre-review", report["unavailable_predictions"][0]["reason"])

    def test_map_aware_cue_fallbacks_replace_legacy_chorus_names(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"song")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        plan = {"map_type": "DROP", "grid": {"t1": 0, "bar_s": 2},
                "cues": {"C": {"bar": 4}, "D": {"bar": 8, "name": "CHORUS"}}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()

        record = server.track_record(row)
        self.assertEqual(record["cues"]["C"]["name"], "-32 DROP")
        self.assertEqual(record["cues"]["C"]["color_id"], "purple")
        self.assertEqual(record["cues"]["C"]["color"], "#6823B6")
        self.assertEqual(record["cues"]["D"]["name"], "FIRST DROP")
        updated = server._cue_binary(plan["cues"], plan)
        labels = {
            payload[1]: payload[12:].split(b"\x00", 1)[0].decode()
            for name, payload in server._marker2_entries(updated)
            if name == "CUE"
        }
        self.assertEqual(labels, {2: "-32 DROP", 3: "FIRST DROP"})

    def test_custom_label_that_matches_a_legacy_name_is_preserved(self):
        source = Path(self.temporary.name) / "custom.mp3"
        source.write_bytes(b"custom")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        plan = {
            "map_type": "DROP",
            "grid": {"t1": 0, "bar_s": 2},
            "cue_map": {"resolved": {}},
            "cues": {"D": {"bar": 8, "name": "LAST CHORUS"}},
        }
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()

        self.assertEqual(server.track_record(row)["cues"]["D"]["name"], "LAST CHORUS")
        written = server._cue_binary(plan["cues"], plan)
        cues = [payload for name, payload in server._marker2_entries(written) if name == "CUE"]
        self.assertTrue(cues[0][12:].startswith(b"LAST CHORUS"))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = self.review_request(base + f"/api/tracks/{file_hash}",
            data=b'{"cues":{"D":{"name":"LAST CHORUS","bar":"9"}}}', method="PUT",
            headers={"Content-Type": "application/json", "Origin": base,
                     "X-AutoCue-Token": server.SESSION_TOKEN})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
        with server.connect() as db:
            saved = json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])
        self.assertEqual(saved["cues"]["D"]["name"], "LAST CHORUS")

    def test_review_edit_persists_cue_and_marks_reviewed(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"song")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (
                json.dumps({"grid": {"t1": 0, "bar_s": 2}, "cues": {"A": {"bar": 2, "name": "INTRO"}}}),
                file_hash,
            ))
        url = f"http://127.0.0.1:{self.http.server_address[1]}/api/tracks/{file_hash}"
        payload = json.dumps({"cues": {"A": {"name": "INTRO", "bar": "3.25"}}}).encode()
        request = self.review_request(url, data=payload, method="PUT", headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.http.server_address[1]}",
            "X-AutoCue-Token": server.SESSION_TOKEN,
        })
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
        with server.connect() as db:
            row = db.execute("SELECT status,reviewed,plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        self.assertEqual(row["status"], "Edited by you")
        self.assertEqual(row["reviewed"], 1)
        self.assertEqual(json.loads(row["plan_json"])["cues"]["A"]["bar"], 3.25)
        events = self.corrections()
        self.assertEqual(events[0]["cue"], "A")
        self.assertEqual(events[0]["action"], "set_here")
        self.assertEqual(events[0]["to_beat"], 13)
        self.assertIn("saved_at", events[0])

    def test_mapped_review_relabels_countdowns_and_preserves_pickup_in_logs_and_answers(self):
        source = Path(self.temporary.name) / "phrasing.mp3"
        source.write_bytes(b"synthetic phrasing")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        plan = server._cue_maps_module().apply_cue_map({
            "map_type": "VOCAL", "bar_count": 100, "grid": {"t1": 0.017, "bar_s": 2},
            "cues": {slot: {} for slot in "ABCDEFGH"},
        }, None, {"start": {"bar": 8, "time_s": 15.094, "unsnapped": True},
                  "first_chorus_end": {"bar": 32}, "out": {"bar": 80}}, 0)
        plan["mix_window_evidence"] = {"intro": {"status": "candidate"}}
        plan["cues"]["A"]["mix_window_evidence"] = {"status": "candidate"}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = self.review_request(base + f"/api/tracks/{file_hash}", data=json.dumps({
            "cues": {"B": {"bar": 8, "name": "START"}, "F": {"bar": 36, "name": "EXIT 1st Chorus"}}
        }).encode(), method="PUT", headers={"Content-Type": "application/json", "Origin": base,
                                           "X-AutoCue-Token": server.SESSION_TOKEN})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
        with server.connect() as db:
            saved = json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])
        self.assertEqual(saved["cues"]["D"]["name"], "GO 12 bars (48 beats) > EXIT")
        self.assertEqual(saved["cues"]["D"]["bar"], 24)
        self.assertEqual(saved["answer_key"]["cues"]["B"]["time_s"], 15.094)
        self.assertNotIn("mix_window_evidence", saved)
        self.assertEqual(saved["cues"]["A"]["mix_window_evidence"]["status"], "unverified")
        events = self.corrections()
        self.assertEqual(events[0]["from_time"], 15.094)
        self.assertEqual(events[0]["to_time"], 15.094)
        server._cue_pads(saved["cues"], saved)
        saved["cues"]["D"]["name"] = "GO 8 bars (32 beats) > EXIT"
        with self.assertRaisesRegex(ValueError, "Invalid cue phrasing"):
            server._cue_pads(saved["cues"], saved)

    def test_event_annotations_keep_grid_provenance_and_uncertain_skip_states(self):
        source = Path(self.temporary.name) / "event-labels.mp3"
        source.write_bytes(b"event label fixture")
        file_hash, _ = server.copy_import(source, source.name)
        grid = {"t1": 0.35, "bar_s": 2.0, "offset_ms": 100}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?",
                       (json.dumps({"grid": grid, "cues": {}}), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        review_url = base + f"/api/tracks/{file_hash}/event-review"
        with urllib.request.urlopen(review_url) as response:
            self.assertEqual(json.load(response)["review"]["state"], "pending")

        def post(url, payload):
            request = urllib.request.Request(url, json.dumps(payload).encode(), method="POST", headers={
                "Content-Type": "application/json", "Origin": base,
                "X-AutoCue-Token": server.SESSION_TOKEN,
            })
            with urllib.request.urlopen(request) as response:
                return json.load(response)

        saved = post(base + f"/api/event-annotations/{file_hash}", {
            "event_type": "drop", "certainty": "uncertain", "time_s": 4.25, "note": "quiet entrance",
        })
        annotation = saved["annotations"][0]
        self.assertEqual(annotation["bar"], 2.0)
        self.assertEqual(annotation["grid"], grid)
        self.assertEqual(annotation["certainty"], "uncertain")
        self.assertEqual(annotation["source"], "human_review")
        self.assertTrue(annotation["created_at"].endswith("Z"))
        self.assertEqual(saved["review"]["state"], "in_progress")

        saved = post(base + f"/api/event-reviews/{file_hash}", {
            "state": "uncertain", "note": "Need a second listen",
        })
        self.assertEqual(saved["review"]["state"], "uncertain")
        saved = post(base + f"/api/event-reviews/{file_hash}", {
            "state": "skipped", "note": "Not useful for this task",
        })
        self.assertEqual(saved["review"]["state"], "skipped")
        with urllib.request.urlopen(base + "/api/event-reviews/export") as response:
            self.assertIn("attachment;", response.headers["Content-Disposition"])
            exported = json.load(response)
        self.assertEqual(exported["schema_version"], 1)
        self.assertEqual(exported["reviews"][0]["track_hash"], file_hash)
        self.assertEqual(exported["reviews"][0]["annotations"][0]["event_type"], "drop")
        self.assertTrue(exported["reviews"][0]["annotations"][0]["active"])

        post(base + f"/api/event-annotations/{annotation['id']}/retract", {})
        with urllib.request.urlopen(review_url) as response:
            current = json.load(response)
        self.assertEqual(current["review"]["state"], "in_progress")
        self.assertEqual(current["annotations"], [])
        with urllib.request.urlopen(base + "/api/event-reviews/export") as response:
            history = json.load(response)["reviews"][0]["annotations"][0]
        self.assertFalse(history["active"])
        self.assertTrue(history["retracted_at"].endswith("Z"))
        self.assertEqual(server.remove_tracks([file_hash]), {"removed": 1})
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_reviews").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_annotations").fetchone()[0], 0)
        self.assertTrue(source.is_file())

    def test_event_annotation_rejects_invalid_labels_times_and_missing_token(self):
        source = Path(self.temporary.name) / "invalid-event.mp3"
        source.write_bytes(b"invalid event fixture")
        file_hash, _ = server.copy_import(source, source.name)
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        url = base + f"/api/event-annotations/{file_hash}"

        def request(payload, token=True):
            headers = {"Content-Type": "application/json", "Origin": base}
            if token:
                headers["X-AutoCue-Token"] = server.SESSION_TOKEN
            return urllib.request.Request(url, json.dumps(payload, allow_nan=True).encode(),
                                          method="POST", headers=headers)

        valid = {"event_type": "build", "certainty": "certain", "time_s": 1.0}
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request(valid, token=False))
        self.assertEqual(raised.exception.code, 403)
        for payload in (
            {**valid, "event_type": "intro"},
            {**valid, "time_s": float("nan")},
            {**valid, "time_s": -0.1},
            {**valid, "certainty": "maybe"},
        ):
            with self.subTest(payload=payload), self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request(payload))
            self.assertEqual(raised.exception.code, 400)
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_annotations").fetchone()[0], 0)

    def test_event_labels_keep_seconds_when_no_grid_is_available(self):
        source = Path(self.temporary.name) / "gridless-event.mp3"
        source.write_bytes(b"gridless event fixture")
        file_hash, _ = server.copy_import(source, source.name)
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = urllib.request.Request(
            base + f"/api/event-annotations/{file_hash}",
            json.dumps({"event_type": "other", "certainty": "certain", "time_s": 3.125}).encode(),
            method="POST", headers={
                "Content-Type": "application/json", "Origin": base,
                "X-AutoCue-Token": server.SESSION_TOKEN,
            })
        with urllib.request.urlopen(request) as response:
            annotation = json.load(response)["annotations"][0]
        self.assertEqual(annotation["time_s"], 3.125)
        self.assertIsNone(annotation["bar"])
        self.assertEqual(annotation["grid"], {})

    def test_offset_review_corrections_and_serato_timestamp_agree_within_one_ms(self):
        source = Path(self.temporary.name) / "offset.mp3"
        source.write_bytes(b"offset test")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        for offset in (150, -150):
            with self.subTest(offset_ms=offset):
                plan = {"grid": {"t1": 2.1, "bar_s": 2, "offset_ms": offset},
                        "cues": {"A": {"bar": 2, "name": "INTRO", "time_s": 999},
                                 "H": {"bar": 49.175, "name": "EXIT-END"}}}
                with server.connect() as db:
                    db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
                request = self.review_request(base + f"/api/tracks/{file_hash}",
                    data=json.dumps({"cues": {"A": {"name": "INTRO", "bar": "3.25"},
                                              "H": {"name": "EXIT-END", "bar": "49.175"}}}).encode(),
                    method="PUT", headers={"Content-Type": "application/json", "Origin": base,
                                           "X-AutoCue-Token": server.SESSION_TOKEN})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                with server.connect() as db:
                    row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
                stored = json.loads(row["plan_json"])
                expected = 2.1 + 3.25 * 2 - offset / 1000
                self.assertEqual(server.track_record(row)["offset_ms"], offset)
                self.assertAlmostEqual(stored["cues"]["A"]["time_s"], expected)
                events = self.corrections()[-2:]
                self.assertAlmostEqual(events[0]["from_time"], 6.1 - offset / 1000)
                self.assertAlmostEqual(events[0]["to_time"], expected)
                self.assertEqual(events[0]["offset_ms"], offset)
                self.assertEqual(events[0]["to_beat"], 13)
                written = server._cue_binary(stored["cues"], stored)
                positions = {payload[1]: int.from_bytes(payload[2:6], "big")
                             for name, payload in server._marker2_entries(written) if name == "CUE"}
                self.assertLessEqual(abs(positions[0] / 1000 - events[0]["to_time"]), 0.001)
                self.assertLessEqual(abs(positions[7] / 1000 - events[1]["to_time"]), 0.001)
                self.assertEqual(stored["cues"]["H"]["name"], "EXIT-END")

    def test_invalid_offset_rejects_review_without_changing_plan_or_corrections(self):
        source = Path(self.temporary.name) / "invalid.mp3"
        source.write_bytes(b"invalid grid")
        file_hash, _ = server.copy_import(source, source.name)
        plan = {"grid": {"t1": 0, "bar_s": 2, "offset_ms": "invalid"},
                "cues": {"A": {"bar": 2, "name": "INTRO"}}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = self.review_request(base + f"/api/tracks/{file_hash}",
            data=b'{"cues":{"A":{"name":"INTRO","bar":"3"}}}', method="PUT",
            headers={"Content-Type": "application/json", "Origin": base,
                     "X-AutoCue-Token": server.SESSION_TOKEN})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code, 400)
        with server.connect() as db:
            stored = json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])
        self.assertEqual(stored, plan)
        self.assertFalse((server.DATA / "corrections.json").exists())
        with self.assertRaisesRegex(ValueError, "offset_ms"):
            server._cue_binary(plan["cues"], plan)

    def test_timing_script_is_served_before_editor_script(self):
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with urllib.request.urlopen(base + "/") as response:
            html = response.read().decode()
        self.assertLess(html.index('src="/timing.js"'), html.index('src="/app.js"'))
        with urllib.request.urlopen(base + "/timing.js") as response:
            self.assertIn("text/javascript", response.headers["Content-Type"])
            self.assertIn("AutoCueTiming", response.read().decode())

    def test_gemini_settings_status_never_returns_configured_key(self):
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fixture-secret-not-for-display"}):
            with urllib.request.urlopen(base + "/api/settings") as response:
                rendered = response.read().decode()
        self.assertNotIn("fixture-secret-not-for-display", rendered)
        status = json.loads(rendered)["gemini_status"]
        self.assertTrue(status["configured"])
        self.assertEqual(status["key_source"], "environment")
        self.assertFalse(status["access_verified"])

    def test_failed_review_storage_reports_error_and_rolls_back_plan(self):
        source = Path(self.temporary.name) / "storage.mp3"
        source.write_bytes(b"storage test")
        file_hash, _ = server.copy_import(source, source.name)
        plan = {"grid": {"t1": 0, "bar_s": 2}, "cues": {"A": {"bar": 2, "name": "INTRO"}}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = self.review_request(base + f"/api/tracks/{file_hash}",
            data=b'{"cues":{"A":{"name":"INTRO","bar":"3"}}}', method="PUT",
            headers={"Content-Type": "application/json", "Origin": base,
                     "X-AutoCue-Token": server.SESSION_TOKEN})
        with server.connect() as db:
            db.execute("""CREATE TRIGGER reject_review BEFORE INSERT ON cue_review_events
                          BEGIN SELECT RAISE(ABORT, 'Disk full'); END""")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code, 503)
        self.assertIn("Disk full", json.loads(raised.exception.read())["error"])
        with server.connect() as db:
            row = db.execute("SELECT plan_json,reviewed FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        self.assertEqual(json.loads(row["plan_json"]), plan)
        self.assertFalse(row["reviewed"])
        self.assertEqual(self.corrections(), [])
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0], 0)
        self.assertFalse((server.DATA / "corrections.json").exists())

    def test_state_changing_request_requires_local_session_token(self):
        url = f"http://127.0.0.1:{self.http.server_address[1]}/api/settings"
        payload = b'{"gemini":true}'
        request = urllib.request.Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.http.server_address[1]}",
        })
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code, 403)

    def test_mix_lengths_round_trip_and_invalid_values_are_rejected(self):
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with urllib.request.urlopen(base + "/api/settings") as response:
            settings = json.load(response)["cue_maps"]
        settings["mix_lengths"] = {"intro": [64, 32, 16], "go": [32, 16], "exit": [16, 8, 4]}
        settings["end_of_song_fallback"] = False
        settings["maps"]["VOCAL"]["A"]["label"] = "IN-{beats} ({bars} bars)"

        def save():
            return urllib.request.urlopen(urllib.request.Request(
                base + "/api/settings", data=json.dumps({"cue_maps": settings}).encode(),
                headers={"Content-Type": "application/json", "Origin": base,
                         "X-AutoCue-Token": server.SESSION_TOKEN}, method="POST"))

        with save() as response:
            self.assertEqual(response.status, 200)
        with urllib.request.urlopen(base + "/api/settings") as response:
            stored = json.load(response)["cue_maps"]
        self.assertEqual(stored, settings)
        settings["mix_lengths"]["go"] = [32, 7]
        with self.assertRaises(urllib.error.HTTPError) as raised:
            save()
        self.assertEqual(raised.exception.code, 400)

    def test_missing_analyzer_is_named_and_staged_audio_is_untouched(self):
        source = Path(self.temporary.name) / "song.wav"
        source.write_bytes(b"audio remains read-only")
        file_hash, _ = server.copy_import(source, source.name)
        with patch.dict(os.environ, {}, clear=False), patch("shutil.which", return_value=None):
            os.environ.pop("AUTOCUE_PROCESSOR", None)
            with self.assertRaisesRegex(RuntimeError, "ffmpeg is not installed.*brew install ffmpeg"):
                server.process_track(file_hash)
        staged = server.UPLOADS / (file_hash + ".wav")
        self.assertEqual(staged.read_bytes(), b"audio remains read-only")

    def _serato_home(self):
        home = Path(self.temporary.name) / "home"
        serato = home / "Music" / "_Serato_"
        (serato / "Subcrates").mkdir(parents=True, exist_ok=True)
        (serato / "database V2").write_bytes(b"read-only Serato database")
        return home

    def test_analysis_review_and_dual_tag_export_keep_grid_warnings_pickup_and_countdowns(self):
        """Real orchestration/HTTP/tag codecs, with only audio/model boundaries replaced by synthetic evidence."""
        import numpy as np
        import struct
        from mutagen.id3 import ID3, GEOB
        import analyze
        import serato_writer

        home = self._serato_home()
        song = home / "Music" / "Synthetic pipeline QA.mp3"
        song.write_bytes(fixtures.mp3())
        tags = ID3()
        tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato BeatGrid",
                      data=b"\x01\x00" + struct.pack(">Iff", 1, 0.017, 120) + b"\x00"))
        tags.save(song)
        original = song.read_bytes()
        file_hash, _ = server.copy_import(song, song.name, source_path=str(song))
        with server.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        duration, hop, fps = 160.617, 0.05, 50
        vocals = np.zeros(math.ceil(duration / hop), dtype=float)
        vocals[int(15.094 / hop):int(128.017 / hop)] = 0.3

        def features(_source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            values = np.full(math.ceil(duration * fps), 0.5)
            np.savez(target, dur=duration, fps=fps, rms=values, lo=values, peak=values)

        def stems(_source, paths):
            paths.stems_env.parent.mkdir(parents=True, exist_ok=True)
            np.savez(paths.stems_env, hop_s=hop, vocals=vocals,
                     drums=np.full(len(vocals), 0.3), bass=np.full(len(vocals), 0.3))
            paths.vocal_wav.parent.mkdir(parents=True, exist_ok=True)
            paths.vocal_wav.touch()

        cache = home / "synthetic-cache"
        settings = {"gemini": "false", "cue_maps": json.dumps({
            "mode": "VOCAL", "mix_lengths": {"intro": [16, 8, 4], "go": [16, 8, 4]}})}
        with patch.object(analyze, "cache_root", return_value=cache), \
                patch.object(analyze, "decode_wav", side_effect=lambda _src, dst:
                             (dst.parent.mkdir(parents=True, exist_ok=True), dst.touch())), \
                patch.object(analyze, "compute_features", side_effect=features), \
                patch.object(analyze, "_run_beats", return_value={
                    "beats": [0.017 + index * 0.5025 for index in range(318)],
                    "downbeats": [0.017 + index * 2.01 for index in range(80)]}), \
                patch.object(analyze, "_run_demucs", side_effect=stems), \
                patch.object(analyze, "_run_sections", return_value={"segments": [
                    {"label": "intro", "start": 0.017, "end": 16.017},
                    {"label": "chorus", "start": 48.017, "end": 64.017},
                    {"label": "outro", "start": 128.017, "end": 160.017},
                ]}), \
                patch.object(analyze, "_run_words", return_value={
                    "words": [{"word": "synthetic", "start": 15.15, "end": 15.45}]}), \
                patch.object(analyze, "_run_lyrics", return_value={"syncedLyrics": None}), \
                patch.object(analyze, "fine_vocal_envelope", return_value=([0.0] * 15094 + [0.3] * 5000, 0.001)), \
                patch.object(analyze, "environment_report", return_value={"synthetic": True}), \
                patch.object(analyze, "torch_device", return_value="cpu"), \
                patch("shutil.which", return_value="/synthetic/ffmpeg"):
            plan = server.process_builtin(row, settings, map_override="VOCAL")
        self.assertEqual(song.read_bytes(), original)
        self.assertEqual(plan["grid"]["source"], "serato_beatgrid")
        self.assertAlmostEqual(plan["grid"]["t1"], 0.017, places=5)
        self.assertEqual(plan["grid"]["bar_s"], 2)
        self.assertEqual(plan["grid_diagnostics"]["status"], "review_required")
        self.assertTrue(any(issue["code"] == "accumulated_drift" for issue in plan["grid_diagnostics"]["issues"]))
        self.assertEqual(plan["cues"]["A"]["name"], "Intro 4 bars (16 beats)")
        self.assertEqual(plan["cues"]["D"]["name"], "GO 4 bars (16 beats) > EXIT")
        self.assertEqual(plan["cues"]["B"]["time_s"], 15.094)
        self.assertTrue(plan["cues"]["B"]["unsnapped"])
        baseline = plan["variants"]["baseline"]
        self.assertEqual(baseline["grid_diagnostics"], plan["grid_diagnostics"])
        server._store_plan(file_hash, plan, "Needs a look")
        base = f"http://127.0.0.1:{self.http.server_address[1]}"

        def library_track():
            with urllib.request.urlopen(base + "/api/library") as response:
                return next(track for track in json.load(response)["tracks"] if track["hash"] == file_hash)

        public = library_track()
        self.assertEqual(public["grid_diagnostics"], plan["grid_diagnostics"])
        self.assertEqual(public["cues"]["B"]["time_s"], 15.094)
        self.assertTrue(public["cues"]["B"]["unsnapped"])
        edits = {slot: {"name": cue["name"], "bar": cue["bar"]} for slot, cue in public["cues"].items()}
        edits["F"]["bar"] = 36
        request = self.review_request(base + f"/api/tracks/{file_hash}", data=json.dumps({"cues": edits}).encode(),
                                        method="PUT", headers={"Content-Type": "application/json", "Origin": base,
                                                              "X-AutoCue-Token": server.SESSION_TOKEN})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
        public = library_track()
        self.assertEqual(public["cues"]["D"]["name"], "GO 8 bars (32 beats) > EXIT")
        self.assertEqual(public["grid_diagnostics"], plan["grid_diagnostics"])
        self.assertIsNone(public["mix_window_evidence"])
        self.assertEqual(public["cues"]["A"]["mix_window_evidence"]["status"], "unverified")
        with server.connect() as db:
            saved = json.loads(db.execute("SELECT plan_json FROM tracks WHERE hash=?", (file_hash,)).fetchone()[0])
        self.assertEqual(saved["answer_key"]["cues"]["B"]["time_s"], 15.094)
        self.assertEqual(saved["variants"]["baseline"], baseline)
        corrections = self.corrections()
        self.assertEqual(next(event["to_time"] for event in corrections if event["cue"] == "B"), 15.094)
        before_audio = serato_writer.mp3_audio_sha256(song)
        with patch.object(server.Path, "home", return_value=home), \
                patch.object(server, "_serato_open", return_value=False), \
                patch.object(server.sys, "platform", "darwin"):
            result = server.write_to_serato([file_hash])
            frames = serato_writer.read_serato_frames(song)
            decoded = server.serato_tags.decode_markers2(frames["Serato Markers2"])
            pads = {cue["index"]: cue for cue in decoded["cues"]}
            self.assertEqual(pads[1]["position_ms"], 15094)
            self.assertEqual(pads[3]["name"], "GO 8 bars (32 beats) > EXIT")
            self.assertEqual(pads[5]["position_ms"] - pads[3]["position_ms"], 16000)
            v1 = server.serato_tags.decode_markers_v1(frames["Serato Markers_"])
            self.assertEqual(v1["entries"][1]["position_ms"], 15094)
            self.assertEqual(serato_writer.mp3_audio_sha256(song), before_audio)
            server.undo_backup(result["backup_id"])
        self.assertEqual(song.read_bytes(), original)

    def _import_reviewed(self, home, name, data):
        song = home / "Music" / name
        song.write_bytes(data)
        file_hash, _ = server.copy_import(song, song.name, source_path=str(song))
        plan = {"grid": {"t1": 0, "bar_s": 2},
                "cues": {"A": {"name": "INTRO", "bar": 2, "confidence": 0.9},
                         "H": {"name": "EXIT", "bar": 8, "confidence": 0.9}}}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=?,reviewed=1,status='Cues ready' WHERE hash=?",
                       (json.dumps(plan), file_hash))
        return song, file_hash

    @unittest.skipUnless(sys.platform == "darwin", "Serato write/restore tests require macOS.")
    def test_write_undo_and_full_restore_for_every_supported_format(self):
        try:
            import mutagen  # noqa: F401
        except ImportError:
            self.skipTest("Mutagen is required for Serato tag write tests.")
        formats = {"mp3": fixtures.mp3(), "aiff": fixtures.aiff(), "flac": fixtures.flac(), "m4a": fixtures.m4a()}
        for kind, data in formats.items():
            with self.subTest(format=kind):
                home = self._serato_home()
                song, file_hash = self._import_reviewed(home, "song." + kind, data)
                before_audio = server._audio_hash(song)
                with patch.object(server.Path, "home", return_value=home), \
                        patch.object(server, "_serato_open", return_value=False):
                    result = server.write_to_serato([file_hash])
                    self.assertNotEqual(song.read_bytes(), data)
                    self.assertEqual(server._audio_hash(song), before_audio)
                    existing = server._existing_markers(song, kind)
                    names = [name for name, _ in server._marker2_entries(existing)]
                    self.assertEqual((names[0], names[-1], set(names[1:-1])), ("COLOR", "BPMLOCK", {"CUE"}))
                    if kind in ("mp3", "aiff"):
                        pads = server.serato_tags.decode_markers2(existing)["cues"]
                        v1 = server.serato_tags.decode_markers_v1(server._existing_v1_markers(song, kind))
                        self.assertEqual([(entry["slot"], entry["position_ms"], entry["color"])
                                          for entry in v1["entries"][:5] if entry["position_ms"] is not None],
                                         [(cue["index"], cue["position_ms"], cue["color"])
                                          for cue in pads if cue["index"] < 5])
                    server.undo_backup(result["backup_id"])
                    self.assertEqual(song.read_bytes(), data)
                    # Write again, then change the library and the Serato folder, then restore everything.
                    again = server.write_to_serato([file_hash])
                    (home / "Music" / "_Serato_" / "database V2").write_bytes(b"damaged later")
                    (home / "Music" / "_Serato_" / "extra").write_bytes(b"new file")
                    with self.assertRaisesRegex(ValueError, "Type RESTORE"):
                        server.restore_backup(again["backup_id"], "restore")
                    server.restore_backup(again["backup_id"], "RESTORE")
                self.assertEqual(song.read_bytes(), data)
                self.assertEqual((home / "Music" / "_Serato_" / "database V2").read_bytes(), b"read-only Serato database")
                self.assertFalse((home / "Music" / "_Serato_" / "extra").exists())
                with server.connect() as db:
                    row = db.execute("SELECT hash,status FROM tracks WHERE source_path=?", (str(song),)).fetchone()
                    status = db.execute("SELECT status FROM backups WHERE id=?", (again["backup_id"],)).fetchone()
                self.assertEqual(row["hash"], file_hash)
                self.assertEqual(row["status"], "Cues ready")
                self.assertEqual(status["status"], "restored")
                with server.connect() as db:
                    db.execute("DELETE FROM tracks")
                    db.execute("DELETE FROM backups")

    @unittest.skipUnless(sys.platform == "darwin", "Serato write tests require macOS.")
    def test_unsupported_format_is_reported_instead_of_failing_mid_write(self):
        home = self._serato_home()
        song, file_hash = self._import_reviewed(home, "song.ogg", b"OggS" + b"\x00" * 40)
        with patch.object(server.Path, "home", return_value=home), patch.object(server, "_serato_open", return_value=False):
            with self.assertRaisesRegex(ValueError, "Write not supported yet"):
                server.write_to_serato([file_hash])
        self.assertEqual(song.read_bytes(), b"OggS" + b"\x00" * 40)
        with server.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        self.assertFalse(server.track_record(row)["write_supported"])

    def test_flac_write_preserves_other_vorbis_fields_and_foreign_markers(self):
        try:
            import mutagen.flac
        except ImportError:
            self.skipTest("Mutagen is required.")
        home = self._serato_home()
        path = home / "Music" / "tagged.flac"
        path.write_bytes(fixtures.flac())
        audio = mutagen.flac.FLAC(path)
        audio["ARTIST"] = ["Keep Me"]
        loop = ("Loop", b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x02" + b"\x00" * 8)
        raw = bytearray(b"\x01\x01")
        raw += b"LOOP\x00" + len(loop[1]).to_bytes(4, "big") + loop[1]
        audio[server.FLAC_KEY] = [server._vorbis_encode(b"\x01\x01" + base64.b64encode(bytes(raw)) + b"\x00")]
        audio.save()
        server._write_markers2(path, {"A": {"bar": 1, "name": "INTRO"}}, {"grid": {"t1": 0, "bar_s": 2}})
        saved = mutagen.flac.FLAC(path)
        self.assertEqual(saved["ARTIST"], ["Keep Me"])
        names = [name for name, _ in server._marker2_entries(server._existing_markers(path, "flac"))]
        self.assertEqual(names, ["COLOR", "CUE", "LOOP", "BPMLOCK"])

    def test_nested_crates_unicode_names_and_external_drive_paths(self):
        root = Path(self.temporary.name)
        song = root / "Música ñandú – Ünï.mp3"
        song.write_bytes(b"x")

        def chunk(tag, value):
            return tag + len(value).to_bytes(4, "big") + value
        entry = chunk(b"otrk", chunk(b"ptrk", str(song).lstrip("/").encode("utf-16-be")))
        crate = root / "Parent%%Child.crate"
        crate.write_bytes(chunk(b"vrsn", "1.0/Serato ScratchLive Crate".encode("utf-16-be")) + entry)
        songs = server.crate_songs(crate)
        self.assertEqual([item["name"] for item in songs], [song.name])
        self.assertFalse(songs[0]["missing"])

        drive = root / "Volumes" / "DJ Drive"
        (drive / "_Serato_" / "Subcrates").mkdir(parents=True)
        self.assertEqual(server._resolve_crate_path("Music/a.mp3", Path("/Volumes/DJ Drive/_Serato_/Subcrates/x.crate")),
                         Path("/Volumes/DJ Drive/Music/a.mp3"))
        with patch.object(server, "crate_roots", return_value=[root]):
            crates = server.list_crates()
        self.assertEqual(crates[0]["name"], "Parent › Child")
        self.assertEqual(crates[0]["depth"], 1)

    def test_real_crate_fixtures_parse_when_supplied(self):
        folder = server.ROOT / "tests" / "fixtures" / "crates"
        crates = sorted(folder.glob("**/*.crate")) if folder.is_dir() else []
        if not crates:
            self.skipTest("Put copies of real .crate files in tests/fixtures/crates/ (gitignored).")
        for crate in crates:
            with self.subTest(crate=crate.name):
                before = hashlib.sha256(crate.read_bytes()).hexdigest()
                songs = server.crate_songs(crate)
                self.assertEqual(hashlib.sha256(crate.read_bytes()).hexdigest(), before)
                for song in songs:
                    self.assertTrue(song["path"].startswith("/"), song)
                    self.assertNotIn("\x00", song["path"])

    def test_running_job_is_requeued_with_completed_progress_after_restart(self):
        with server.connect() as db:
            db.execute("INSERT INTO jobs(id,status,track_hashes,completed,created_at,updated_at,message) "
                       "VALUES('persisted','running','[]',2,1,1,'working')")
        server.initialize()
        with server.connect() as db:
            job = db.execute("SELECT status,completed FROM jobs WHERE id='persisted'").fetchone()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["completed"], 2)


if __name__ == "__main__":
    unittest.main()
