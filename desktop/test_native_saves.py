"""Exercise the production private save writer against real temporary filesystem races."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"), "Requires macOS Swift")
class NativeSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="booth-native-save-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        source = Path(__file__).with_name("StudioApp.swift").read_text()
        writer = source[source.index("    private func writeNativeZIP("):source.index("    private func chooseNativeZIPDestination(")]
        writer = writer.replace("private func", "func", 1)
        harness = '''
func nativeWorkflowError(_ message: String) -> NSError {
    NSError(domain: "SaveTest", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
}
do {
    try writeNativeZIP(Data(CommandLine.arguments[2].utf8), to: URL(fileURLWithPath: CommandLine.arguments[1]), replaceExisting: false)
} catch { print(error.localizedDescription); exit(1) }
'''
        swift = cls.root / "main.swift"
        swift.write_text("import Foundation\nimport Darwin\n" + writer + harness)
        cls.binary = cls.root / "save"
        result = subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cls.root / "modules"), str(swift), "-o", str(cls.binary)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stderr)

    def save(self, destination, payload="synthetic private export"):
        return subprocess.run([str(self.binary), str(destination), payload], capture_output=True, text=True, timeout=10)

    def test_new_file_is_private_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            destination = Path(directory) / "Set.crate"
            self.assertEqual(self.save(destination).returncode, 0)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.read_text(), "synthetic private export")
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_existing_file_and_broken_symlink_are_never_replaced(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            existing = root / "existing.crate"
            existing.write_bytes(b"original")
            broken = root / "broken.crate"
            broken.symlink_to(root / "absent")
            self.assertEqual(self.save(existing).returncode, 1)
            self.assertEqual(self.save(broken).returncode, 1)
            self.assertEqual(existing.read_bytes(), b"original")
            self.assertTrue(broken.is_symlink())
            self.assertEqual({p.name for p in root.iterdir()}, {"existing.crate", "broken.crate"})

    def test_concurrent_creators_publish_exactly_one_complete_payload(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            destination = Path(directory) / "Race.crate"
            payloads = [str(index) * 10000 for index in range(8)]
            processes = [subprocess.Popen([str(self.binary), str(destination), payload], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for payload in payloads]
            results = [process.communicate(timeout=20) for process in processes]
            winners = [index for index, process in enumerate(processes) if process.returncode == 0]
            self.assertEqual(len(winners), 1, results)
            self.assertEqual(destination.read_text(), payloads[winners[0]])
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_failed_publication_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            destination = Path(directory) / "directory.crate"
            destination.mkdir()
            self.assertEqual(self.save(destination).returncode, 1)
            self.assertTrue(destination.is_dir())
            self.assertEqual(list(Path(directory).iterdir()), [destination])
