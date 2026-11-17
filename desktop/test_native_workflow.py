"""Exercise the exact Foundation-only native ZIP verifier with independent ZIP fixtures.

The app currently has one Swift source file for both Xcode and standalone builds.
Extract its production models/verifier into a temporary CLI; do not duplicate the
verification logic or require an app session to test malformed archives.
"""
import csv
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
        swift.write_text("import Foundation\n" + models + verifier + harness)
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


if __name__ == "__main__":
    unittest.main()
