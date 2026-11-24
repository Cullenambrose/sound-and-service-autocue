"""Exercise the exact Foundation-only native ZIP verifier with independent ZIP fixtures.

The app currently has one Swift source file for both Xcode and standalone builds.
Extract its production models/verifier into a temporary CLI; do not duplicate the
verification logic or require an app session to test malformed archives.
"""
import csv
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"), "Requires macOS Swift")
class NativePlaylistVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="booth-native-zip-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        source = Path(__file__).with_name("StudioApp.swift").read_text()
        models = source[source.index("// Native models"):source.index("struct CrateSummary:")]
        verifier = source[source.index("enum NativePlaylistZIP {"):source.index("struct NativePlanApprovalView:")]
        harness = '''
let arguments = CommandLine.arguments
if arguments.count > 5 && arguments[1] == "--index-view" {
    do {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let inbox = try decoder.decode(NativeLibraryInbox.self, from: Data(arguments[2].utf8))
        let items = inbox.visibleItems(view: arguments[3], query: arguments[4], rootId: arguments[5])
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: items.map { ["id": $0.id, "title": $0.title] })); exit(0)
    } catch { print(error.localizedDescription); exit(1) }
}
if arguments.count > 3 && arguments[1] == "--cancel-scan" {
    do {
        let receipt = try JSONDecoder().decode(NativeScanCancellation.self, from: Data(arguments[2].utf8))
        print(try receipt.status(for: arguments[3])); exit(0)
    } catch { print(error.localizedDescription); exit(1) }
}
if arguments.count > 2 && arguments[1] == "--audition" {
    do {
        let spec = try JSONSerialization.jsonObject(with: Data(arguments[2].utf8)) as! [String: Any]
        let hash = spec["hash"] as! String
        let size = try NativeAuditionVerification.size(status: spec["probeStatus"] as! Int, etag: spec["probeTag"] as? String, range: spec["probeRange"] as? String, expectedHash: hash)
        try NativeAuditionVerification.verify(Data(base64Encoded: spec["data"] as! String)!, size: size, status: spec["status"] as! Int, etag: spec["tag"] as? String, range: spec["range"] as? String, expectedHash: hash)
        print("verified audio"); exit(0)
    } catch { print(error.localizedDescription); exit(1) }
}
if arguments.count > 4 && arguments[1] == "--intake" {
    do {
        let input = try JSONDecoder().decode(String.self, from: Data(arguments[2].utf8))
        FileHandle.standardOutput.write(try JSONEncoder().encode(NativeRequestIntake.parse(input, format: arguments[3], category: arguments[4]))); exit(0)
    }
    catch { print(error.localizedDescription); exit(1) }
}
if arguments.count > 3 && arguments[1] == "--append-requests" {
    do {
        let rows = try JSONDecoder().decode([NativeRequestImportRow].self, from: Data(arguments[3].utf8))
        print(try NativeRequestIntake.append(rows, to: arguments[2])); exit(0)
    } catch { print(error.localizedDescription); exit(1) }
}
if arguments.count > 5 && arguments[1] == "--policy" {
    do { print(try NativePlannerDraft.update(arguments[2], fileID: arguments[3], action: arguments[4], position: Int(arguments[5])!)); exit(0) }
    catch { print(error.localizedDescription); exit(1) }
}
if arguments.count > 2 && arguments[1] == "--filename" {
    print(NativeSeratoCrate.filename(for: arguments[2])); exit(0)
}
if arguments.count > 2 && arguments[1] == "--destination" {
    let destination = URL(fileURLWithPath: arguments[2])
    print(NativeSeratoCrate.isSubcratesDestination(destination))
    print(NativeSeratoCrate.completionMessage(for: destination)); exit(0)
}
let bundle = try Data(contentsOf: URL(fileURLWithPath: arguments[1]))
do {
    let expected = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: arguments[3]))) as? [String: Any]
    let checked = try NativePlaylistZIP.verify(bundle, expectedManifest: expected, datasetId: arguments[2])
    if arguments.count > 4 && arguments[4] == "--serato-crate" {
        FileHandle.standardOutput.write(try NativeSeratoCrate.data(checked))
    } else if arguments.count > 4 && arguments[4] == "--serato" {
        let playlist = try NativeSeratoPlaylist.data(checked)
        FileHandle.standardOutput.write(playlist)
    } else { print("verified") }
} catch { print(error.localizedDescription); exit(1) }
'''
        swift = cls.root / "main.swift"
        swift.write_text("import Foundation\nimport CryptoKit\n" + models + verifier + harness)
        cls.binary = cls.root / "verify"
        built = subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cls.root / "modules"),
                                str(swift), "-o", str(cls.binary)], capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError("Production native verifier did not compile:\n" + built.stderr)

    def manifest(self, paths=None):
        paths = paths or ['/Invented/alpha.wav', '/Invented/é, "quoted".wav']
        return {"schema_version": 1, "dataset_id": "synthetic-dataset", "crate": {
            "id": "synthetic-crate", "name": "Generated test crate", "revision": 1},
            "ready": True, "requested": len(paths), "eligible": len(paths), "blocked": 0,
            "included": len(paths), "blockers": [], "entries": [
                {"position": index, "eligible": True, "reason": "", "path": path}
                for index, path in enumerate(paths, 1)]}

    def bundle(self, manifest, playlist=None, csv_paths=None, extra=False):
        paths = [entry["path"] for entry in manifest["entries"]]
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["position", "path", "title"])
        for index, path in enumerate(paths if csv_paths is None else csv_paths, 1):
            writer.writerow([index, path, 'Generated "title", with\nnewline'])
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, value in {
                "manifest.json": json.dumps(manifest, ensure_ascii=False),
                "playlist.m3u8": playlist if playlist is not None else "#EXTM3U\n" + "".join(path + "\n" for path in paths),
                "playlist.csv": stream.getvalue(),
            }.items():
                archive.writestr(name, value.encode("utf-8"))
            if extra:
                archive.writestr("../not-an-output", "No extraction may occur")
        return result.getvalue()

    def verify(self, bundle, manifest, *, dataset="synthetic-dataset", accepted=False, serato=False):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            (root / "input.zip").write_bytes(bundle)
            (root / "expected.json").write_text(json.dumps(manifest, ensure_ascii=False))
            result = subprocess.run([str(self.binary), str(root / "input.zip"), dataset,
                                     str(root / "expected.json")] + (["--serato"] if serato else []), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)
            if accepted and serato:
                self.assertEqual(result.stdout, "#EXTM3U\n" + "".join(entry["path"] + "\n" for entry in manifest["entries"]))
            self.assertEqual({path.name for path in root.iterdir()}, {"input.zip", "expected.json"})

    def test_valid_unicode_quotes_and_multiline_csv(self):
        manifest = self.manifest()
        self.verify(self.bundle(manifest), manifest, accepted=True)

    def test_repeated_memberships_preserve_exact_order(self):
        manifest = self.manifest(["/Invented/a.wav", "/Invented/b.wav", "/Invented/a.wav"])
        self.verify(self.bundle(manifest), manifest, accepted=True)

    def test_wrong_dataset_rejected(self):
        manifest = self.manifest()
        self.verify(self.bundle(manifest), manifest, dataset="other-dataset")

    def test_changed_preview_manifest_rejected(self):
        manifest = self.manifest()
        expected = json.loads(json.dumps(manifest))
        expected["crate"]["revision"] = 2
        self.verify(self.bundle(manifest), expected)

    def test_reversed_playlist_rejected_even_with_valid_zip_crc(self):
        manifest = self.manifest()
        paths = [entry["path"] for entry in manifest["entries"]]
        self.verify(self.bundle(manifest, playlist="#EXTM3U\n" + "".join(path + "\n" for path in reversed(paths))), manifest)

    def test_reversed_csv_rejected_even_with_valid_zip_crc(self):
        manifest = self.manifest()
        self.verify(self.bundle(manifest, csv_paths=[entry["path"] for entry in reversed(manifest["entries"])]), manifest)

    def test_incomplete_export_rejected(self):
        manifest = self.manifest()
        manifest.update(ready=False, blocked=1, included=0)
        self.verify(self.bundle(manifest), manifest)

    def test_corrupt_payload_rejected(self):
        manifest = self.manifest()
        bundle = bytearray(self.bundle(manifest))
        bundle[60] ^= 1
        self.verify(bytes(bundle), manifest)

    def test_extra_member_and_truncation_rejected(self):
        manifest = self.manifest()
        self.verify(self.bundle(manifest, extra=True), manifest)
        self.verify(self.bundle(manifest)[:-3], manifest)

    def test_central_directory_identity_rejected(self):
        manifest = self.manifest()
        bundle = bytearray(self.bundle(manifest))
        start = bundle.index(b"PK\x01\x02")
        bundle[start + 42] ^= 1  # Wrong local-header offset.
        self.verify(bytes(bundle), manifest)

    def test_serato_playlist_preserves_unicode_and_repeated_memberships(self):
        manifest = self.manifest(["/Invented/Café first.mp3", "/Invented/second.m4a", "/Invented/Café first.mp3"])
        self.verify(self.bundle(manifest), manifest, accepted=True, serato=True)

    def test_serato_playlist_rejects_relative_paths(self):
        manifest = self.manifest(["relative/song.mp3"])
        self.verify(self.bundle(manifest), manifest, serato=True)

    def test_serato_playlist_rejects_multiline_path(self):
        manifest = self.manifest(["/Invented/song\nsecond.mp3"])
        self.verify(self.bundle(manifest), manifest, serato=True)

    def test_serato_playlist_rejects_wrong_positions(self):
        manifest = self.manifest()
        manifest["entries"][0]["position"] = 2
        self.verify(self.bundle(manifest), manifest, serato=True)

    def test_serato_crate_decodes_with_independent_backend_parser(self):
        from verify_serato_acceptance import parse_snapshot_bytes
        manifest = self.manifest(["/Invented/Café first.mp3", "/Invented/second.m4a", "/Invented/Café first.mp3"])
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            (root / "input.zip").write_bytes(self.bundle(manifest))
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
            result = subprocess.run([str(self.binary), str(root / "input.zip"), "synthetic-dataset", str(root / "manifest.json"), "--serato-crate"], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            decoded = parse_snapshot_bytes(result.stdout, kind="crate")
            self.assertEqual([track["path"] for track in decoded["tracks"]], [entry["path"] for entry in manifest["entries"]])

    def test_serato_filename_preserves_unicode_and_cannot_escape_destination(self):
        for name, expected in [("Café – dinner", "Café – dinner.crate"),
                               ("../Main:/Room\nSet", "Main Room Set.crate"),
                               ("...\n", "BOOTH BUDDY Set.crate"),
                               ("a" * 200, "a" * 100 + ".crate")]:
            result = subprocess.run([str(self.binary), "--filename", name], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), expected)

    def test_large_external_volume_playlist_keeps_order_and_duplicates(self):
        manifest = self.manifest([f"/Volumes/Synthetic DJ Drive/Café/{index % 9999}.m4a" for index in range(10000)])
        self.verify(self.bundle(manifest), manifest, accepted=True, serato=True)

    def test_unicode_filename_fits_filesystem_without_splitting_characters(self):
        for name in ["🎧" * 100, "家" * 100, "👨‍👩‍👧‍👦" * 100, "a" + "\u0301" * 300]:
            result = subprocess.run([str(self.binary), "--filename", name], capture_output=True, text=True, check=True)
            filename = result.stdout.strip()
            self.assertLessEqual(len(filename.encode("utf-8")), 246)
            self.assertTrue(filename.endswith(".crate"))
            with tempfile.TemporaryDirectory(dir=self.root) as directory:
                target = Path(directory) / filename
                target.write_bytes(b"synthetic filename probe")
                self.assertEqual(target.read_bytes(), b"synthetic filename probe")

    def test_subcrates_detection_and_instructions_agree_for_case_variants(self):
        for folder in ["Subcrates", "subcrates", "SUBCRATES"]:
            result = subprocess.check_output([str(self.binary), "--destination", f"/Volumes/Test/_Serato_/{folder}/Dinner.crate"], text=True)
            self.assertTrue(result.startswith("true\n"))
            self.assertIn("Open Serato and select Dinner", result)
        portable = subprocess.check_output([str(self.binary), "--destination", "/tmp/Other/Dinner.crate"], text=True)
        self.assertTrue(portable.startswith("false\n"))
        self.assertIn("quit Serato, move this new file", portable)

    def edit_policy(self, policy, file_id, action, position=1, accepted=True):
        result = subprocess.run([str(self.binary), "--policy", json.dumps(policy), file_id, action, str(position)], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, accepted, result.stdout)
        return json.loads(result.stdout) if accepted else result.stdout

    def test_visual_constraints_preserve_advanced_fields_and_pin_uses_zero_based_contract(self):
        policy = {"target_minutes": 30, "tolerance_minutes": 3, "max_tracks": 20, "energy_target": .6,
                  "phases": [{"phase": "dinner", "target_minutes": 30, "custom_evidence": "keep"}]}
        required = self.edit_policy(policy, "exact-a", "require")
        pinned = self.edit_policy(required, "exact-a", "pin", 3)
        self.assertEqual(pinned["pinned"], [{"file_id": "exact-a", "position": 2}])
        self.assertEqual(pinned["energy_target"], .6)
        self.assertEqual(pinned["phases"], policy["phases"])
        self.assertEqual(pinned["must_include"], ["exact-a"])

    def test_visual_constraints_refuse_conflicting_actions(self):
        required = self.edit_policy({"max_tracks": 20}, "a", "require")
        self.edit_policy(required, "a", "exclude", accepted=False)
        excluded = self.edit_policy({}, "a", "exclude")
        self.edit_policy(excluded, "a", "require", accepted=False)
        self.edit_policy(excluded, "a", "pin", accepted=False)
        self.edit_policy({"phases": [{"must_include": ["a"]}]}, "a", "exclude", accepted=False)

    def test_visual_pins_refuse_occupied_and_out_of_range_positions(self):
        policy = self.edit_policy({"max_tracks": 2}, "a", "pin", 2)
        self.edit_policy(policy, "b", "pin", 2, accepted=False)
        self.edit_policy(policy, "a", "pin", 3, accepted=False)
        moved = self.edit_policy(policy, "a", "pin", 1)
        self.assertEqual(moved["pinned"], [{"file_id": "a", "position": 0}])

    def test_clear_only_removes_selected_global_constraints(self):
        policy = {"must_include": ["a", "b"], "exclude": ["c"], "pinned": [{"file_id": "a", "position": 0}], "phases": [{"must_include": ["b"]}]}
        cleared = self.edit_policy(policy, "a", "clear")
        self.assertEqual(cleared, {**policy, "must_include": ["b"], "pinned": []})

    def test_visual_constraints_refuse_malformed_existing_arrays(self):
        self.edit_policy({"must_include": "not a list"}, "a", "require", accepted=False)
        self.edit_policy({"pinned": "not a list"}, "a", "pin", accepted=False)

    def intake(self, text, format="csv", category="must_play", accepted=True):
        result = subprocess.run([str(self.binary), "--intake", json.dumps(text), format, category], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, accepted, result.stdout)
        return json.loads(result.stdout) if accepted else result.stdout

    def append_requests(self, event, rows, accepted=True):
        result = subprocess.run([str(self.binary), "--append-requests", json.dumps(event), json.dumps(rows)], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, accepted, result.stdout)
        return json.loads(result.stdout) if accepted else result.stdout

    def test_intake_csv_matches_independent_parser_with_unicode_quotes_and_multiline_fields(self):
        text = 'title,artist,version,category\r\n"Café, first","The ""Quoted"" Artist","Live\r\nedit",must_play\r\nSecond,Other,,do_not_play\r\n'
        rows = self.intake(text)
        reference = list(csv.DictReader(io.StringIO(text.replace("\r\n", "\n"))))
        self.assertEqual([{key: row[key] for key in reference[0]} for row in rows], reference)

    def test_intake_title_lines_do_not_guess_artist_or_drop_repeats(self):
        rows = self.intake("Artist - Title\n\nArtist - Title\n", "lines", "play_if_possible")
        self.assertEqual([row["title"] for row in rows], ["Artist - Title"] * 2)
        self.assertTrue(all(row["artist"] == "" and row["category"] == "play_if_possible" for row in rows))

    def test_intake_tsv_and_utf8_bom(self):
        rows = self.intake('\ufefftitle\tartist\nUnicode 🎧\tSynthetic\n', "tsv")
        self.assertEqual(rows[0]["title"], "Unicode 🎧")
        self.assertEqual(rows[0]["artist"], "Synthetic")

    def test_intake_rejects_unknown_duplicate_and_missing_title_headers(self):
        for text in ["title,notes\na,private", "title,title\na,b", "artist\na"]:
            self.intake(text, accepted=False)

    def test_intake_rejects_malformed_quoting_and_row_width(self):
        for text in ['title\n"open', 'title\n"closed"suffix', 'title\nstray"quote', 'title,artist\na,b,c']:
            self.intake(text, accepted=False)

    def test_intake_rejects_unknown_category_and_blank_title(self):
        for text in ["title,category\na,mustplay", "title,artist\n,artist"]:
            self.intake(text, accepted=False)

    def test_intake_bounds_and_control_characters(self):
        self.intake("a" * 65537, "lines", accepted=False)
        self.intake("\n".join(["a"] * 201), "lines", accepted=False)
        self.intake("a" * 1025, "lines", accepted=False)
        self.intake("bad\x00title", "lines", accepted=False)
        self.intake("\n\n", "lines", accepted=False)

    def test_intake_append_preserves_existing_event_metadata_categories_and_repeats(self):
        event = {"event_name": "Synthetic event", "custom": {"keep": True}, "music_lists": {
            "must_play": [{"title": "Existing", "artist": "Original", "metadata": {"keep": True}}],
            "special_moments": [{"moment": "first_dance", "title": "Keep"}], "client_playlists": [{"title": "Keep list"}]}}
        rows = self.intake("title,artist,version,category\nNew,Artist,Radio,must_play\nNew,Artist,Radio,must_play\nNo,Artist,,do_not_play")
        result = self.append_requests(event, rows)
        self.assertEqual(result["custom"], event["custom"])
        self.assertEqual(result["music_lists"]["special_moments"], event["music_lists"]["special_moments"])
        self.assertEqual(result["music_lists"]["client_playlists"], event["music_lists"]["client_playlists"])
        self.assertEqual(result["music_lists"]["must_play"][0], event["music_lists"]["must_play"][0])
        self.assertEqual(result["music_lists"]["must_play"][1:], [{"title": "New", "artist": "Artist", "version": "Radio"}] * 2)
        self.assertEqual(result["music_lists"]["do_not_play"], [{"title": "No", "artist": "Artist"}])

    def test_intake_append_refuses_malformed_existing_lists_and_edited_rows(self):
        rows = self.intake("Valid", "lines")
        self.append_requests({"music_lists": {"do_not_play": "invalid"}}, rows, accepted=False)
        self.append_requests({"music_lists": []}, rows, accepted=False)
        rows[0]["title"] = ""
        self.append_requests({}, rows, accepted=False)

    def audition_spec(self, **changes):
        data = b"Synthetic verified audition bytes"
        digest = hashlib.sha256(data).hexdigest()
        return {"hash": digest, "probeStatus": 206, "probeTag": f'"{digest}"', "probeRange": f"bytes 0-0/{len(data)}",
                "data": base64.b64encode(data).decode(), "status": 206, "tag": f'"{digest}"', "range": f"bytes 0-{len(data)-1}/{len(data)}", **changes}

    def check_audition(self, spec, accepted=False):
        result = subprocess.run([str(self.binary), "--audition", json.dumps(spec)], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, accepted, result.stdout)

    def test_audition_accepts_exact_hash_bound_complete_range(self):
        self.check_audition(self.audition_spec(), accepted=True)

    def test_audition_refuses_changed_or_partial_bytes(self):
        self.check_audition(self.audition_spec(data=base64.b64encode(b"different").decode()))
        self.check_audition(self.audition_spec(range="bytes 1-32/33"))
        self.check_audition(self.audition_spec(status=200))

    def test_audition_refuses_stale_hash_and_unbounded_sizes(self):
        self.check_audition(self.audition_spec(probeTag='"' + "0" * 64 + '"'))
        self.check_audition(self.audition_spec(probeRange="bytes 0-0/67108865"))
        self.check_audition(self.audition_spec(probeRange="bytes 0-0/0"))
        self.check_audition(self.audition_spec(probeStatus=200))

    def index_fixture(self):
        items = []
        for id, root, metadata, missing, available, group in [
                ("tagged", "root-a", {"title": "_Intro", "artist": "Artist"}, ["bpm"], 1, 1),
                ("untagged", "root-a", {}, ["title", "artist"], 1, 1),
                ("other", "root-b", {"title": "Other", "artist": "Another"}, [], 0, 2)]:
            items.append({"file_id": id, "root_id": root, "relative_path": id + ".wav", "status": "new", "revision": 1,
                          "metadata": metadata, "missing_metadata": missing, "available": available, "error": "", "exact_hash_group_count": group})
        return {"items": items, "counts": {"new": 30}, "limit": 100}

    def index_view(self, fixture, view="all", query="", root=""):
        result = subprocess.run([str(self.binary), "--index-view", json.dumps(fixture), view, query, root], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_index_view_exposes_missing_tags_without_inventing_titles(self):
        fixture = self.index_fixture()
        self.assertEqual(self.index_view(fixture, "search-tags"), [{"id": "untagged", "title": "untagged.wav"}])
        self.assertEqual(self.index_view(fixture, query="intro"), [{"id": "tagged", "title": "_Intro"}])
        self.assertEqual(len(self.index_view(fixture, "metadata")), 2)

    def test_index_filters_apply_only_to_loaded_scope_and_preserve_order(self):
        fixture = self.index_fixture()
        self.assertEqual([row["id"] for row in self.index_view(fixture)], ["tagged", "untagged", "other"])
        self.assertEqual([row["id"] for row in self.index_view(fixture, root="root-a")], ["tagged", "untagged"])
        self.assertEqual(self.index_view(fixture, root="absent"), [])
        self.assertEqual(self.index_view(fixture, "unsupported"), [])

    def test_index_unavailable_and_hash_groups_are_independent_metadata_filters(self):
        fixture = self.index_fixture()
        fixture["items"][2]["error"] = "Synthetic indexed decode error"
        for view in ["unavailable", "duplicates", "errors"]:
            self.assertEqual(self.index_view(fixture, view), [{"id": "other", "title": "Other"}])
        self.assertEqual(self.index_view(fixture, query="untagged.wav"), [{"id": "untagged", "title": "untagged.wav"}])

    def test_scan_cancel_receipts_require_exact_job_and_terminal_or_pending_cancellation(self):
        for state in ["cancelled", "cancelling", "completed", "failed", "interrupted", "running"]:
            for job in ["selected", "different"]:
                result = subprocess.run([str(self.binary), "--cancel-scan", json.dumps({"ok": True, "job": {"id": job, "status": state}}), "selected"], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, job == "selected" and state != "running", result.stdout)


if __name__ == "__main__":
    unittest.main()
