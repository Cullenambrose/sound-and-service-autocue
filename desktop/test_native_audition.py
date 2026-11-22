"""Exercise the production memory-bounded transport against a synthetic localhost server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"), "Requires macOS Swift")
class NativeAuditionTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="booth-audition-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        source = Path(__file__).with_name("StudioApp.swift").read_text()
        models = source[source.index("// Native models"):source.index("struct CrateSummary:")]
        transport = source[source.index("// Memory-only, bounded audio"):source.index("@MainActor\nfinal class NativeAuditionPlayer:")]
        harness = '''
let request = URLRequest(url: URL(string: CommandLine.arguments[1])!)
let child = Task { try await NativeAuditionDownload.load(request, maximum: 32) }
if CommandLine.arguments.count > 2 {
    Task { try? await Task.sleep(nanoseconds: 20_000_000); child.cancel() }
}
Task {
    do { let result = try await child.value; print(result.0.count); exit(0) }
    catch { print(error.localizedDescription); exit(1) }
}
RunLoop.main.run()
'''
        swift = cls.root / "main.swift"
        swift.write_text("import Foundation\nimport CryptoKit\n" + models + transport + harness)
        cls.binary = cls.root / "download"
        result = subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cls.root / "modules"), str(swift), "-o", str(cls.binary)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stderr)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/slow":
                    time.sleep(.3)
                self.send_response(206)
                if self.path == "/declared-large":
                    self.send_header("Content-Length", "1000000")
                elif self.path != "/unbounded-large":
                    self.send_header("Content-Length", "16")
                self.end_headers()
                payload = b"x" * (1000000 if self.path == "/declared-large" else 64 if self.path == "/unbounded-large" else 8 if self.path == "/truncated" else 16)
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)

    def download(self, path, cancel=False):
        url = f"http://127.0.0.1:{self.server.server_port}/{path}"
        return subprocess.run([str(self.binary), url] + (["cancel"] if cancel else []), capture_output=True, text=True, timeout=5)

    def test_bounded_bytes_return_without_creating_files(self):
        before = set(self.root.iterdir())
        result = self.download("normal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "16")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_declared_oversized_response_is_refused(self):
        result = self.download("declared-large")
        self.assertEqual(result.returncode, 1)
        self.assertIn("size limit", result.stdout)

    def test_unknown_length_response_cannot_overflow_buffer(self):
        result = self.download("unbounded-large")
        self.assertEqual(result.returncode, 1)
        self.assertIn("size limit", result.stdout)

    def test_loading_can_be_canceled(self):
        result = self.download("slow", cancel=True)
        self.assertEqual(result.returncode, 1)

    def test_truncated_response_is_not_returned_as_complete(self):
        self.assertEqual(self.download("truncated").returncode, 1)
