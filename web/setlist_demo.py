#!/usr/bin/env python3
"""Loopback-only synthetic Prep/Live demo; no library app, scans or music I/O."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(ROOT / "engine"))
if __package__:
    from .setlist_flow import FlowConflict, SetlistFlow
    from . import serato_import, set_intake
else:
    from setlist_flow import FlowConflict, SetlistFlow
    import serato_import
    import set_intake

WEB = Path(__file__).resolve().parent
OUTPUTS = Path.home() / "Desktop/MASTER-TODO/outputs"
STATIC = {
    "/": ("setlist.html", "text/html"),
    "/setlist.js": ("setlist.js", "text/javascript"),
    "/setlist.css": ("setlist.css", "text/css"),
}


def demo_server(flow, port=0, *, handler_only=False, mount_prefix=""):
    token = secrets.token_hex(32)

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, data, content_type="application/json"):
            if content_type == "application/json":
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {"error": "Use the loopback demo URL."})
            path = urlparse(self.path).path
            if mount_prefix:
                path = path.removeprefix(mount_prefix)
            try:
                if path in STATIC:
                    filename, content_type = STATIC[path]
                    data = (WEB / filename).read_bytes()
                    if filename == "setlist.html" and mount_prefix:
                        for target in ("/setlist.js", "/setlist.css", "/api/export", "/api/serato/catalog"):
                            data = data.replace(f'"{target}"'.encode(), f'"{mount_prefix}{target}"'.encode())
                    return self.send(200, data, content_type)
                if path == "/api/session":
                    return self.send(200, {"token": token, "synthetic": True})
                if path in {"/api/prep", "/api/live"}:
                    return self.send(200, flow.view(path.rsplit("/", 1)[1]))
                if path == "/api/serato/demo":
                    with flow.lock:
                        return self.send(200, serato_import.demo_upload(flow._library_snapshot(flow.state)[0]))
                if path == "/api/serato/catalog":
                    with flow.lock:
                        source = flow.state["serato_source"]
                        if source is None:
                            raise FlowConflict("Accept a Serato snapshot before downloading its catalog.")
                        return self.send(200, source["catalog_snapshot"])
                if path == "/api/export":
                    with flow.lock:
                        if flow.verify()["mismatches"] != 0:
                            raise FlowConflict("Save, export and verify the Prep crate first.")
                        data = flow.export_path.read_bytes()
                    return self.send(200, data, "audio/x-mpegurl")
                return self.send(404, {"error": "Unknown synthetic demo route."})
            except (ValueError, OSError) as error:
                return self.send(409 if isinstance(error, FlowConflict) else 503, {"error": str(error)})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (not self.allowed_host()
                    or origin not in {None, f"http://127.0.0.1:{self.server.server_port}"}
                    or self.headers.get("X-Setlist-Token") != token):
                return self.send(403, {"error": "Reload the local demo to establish a session."})
            try:
                path = self.path.removeprefix(mount_prefix) if mount_prefix else self.path
                if path not in {"/api/command", "/api/serato/preview", "/api/acquisition/preview",
                                     "/api/portable/preview", "/api/sequence/preview"}:
                    return self.send(404, {"error": "Unknown synthetic demo route."})
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1500000:
                    raise ValueError("JSON commands must be between 1 and 1500000 bytes.")
                body = json.loads(self.rfile.read(length))
                if path == "/api/serato/preview":
                    with flow.lock:
                        return self.send(200, serato_import.preview(body, flow._library_snapshot(flow.state)[0]))
                if path == "/api/acquisition/preview":
                    return self.send(200, flow.acquisition_preview(body))
                if path == "/api/sequence/preview":
                    return self.send(200, flow.sequence_preview(body))
                if path == "/api/portable/preview":
                    with flow.lock:
                        return self.send(200, set_intake.preview(body, flow._library_snapshot(flow.state)[0]))
                if length > 8192 and not (isinstance(body, dict) and body.get("action") in {"serato_import", "portable_import"}):
                    raise ValueError("Non-upload JSON commands must be at most 8192 bytes.")
                return self.send(200, flow.command(body))
            except FlowConflict as error:
                return self.send(409, {"error": str(error)})
            except (ValueError, TypeError) as error:
                return self.send(400, {"error": str(error)})
            except OSError as error:
                return self.send(503, {"error": "Could not persist synthetic workflow: " + str(error)})

    if handler_only:
        return Handler
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def mounted_handler(base, flow):
    """Mount the unchanged synthetic workspace beside the existing app routes."""
    workspace = demo_server(flow, handler_only=True, mount_prefix="/setlist")

    class Combined(workspace, base):
        synthetic_workspace = True

        def do_GET(self):
            return (workspace.do_GET(self) if self.path.startswith("/setlist/")
                    else base.do_GET(self))

        def do_POST(self):
            return (workspace.do_POST(self) if self.path.startswith("/setlist/")
                    else base.do_POST(self))

    return Combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--output", type=Path, default=OUTPUTS / "06-flow-demo")
    parser.add_argument("--shuffled", action="store_true", help="Use the same demo library in shuffled order.")
    args = parser.parse_args()
    directory = args.output.expanduser().absolute()
    if (directory.parent.resolve() != OUTPUTS.resolve() or not directory.name.startswith(("05-", "06-"))
            or directory.is_symlink()):
        parser.error("Output must be a non-symlink 05-* or 06-* directory directly in ~/Desktop/MASTER-TODO/outputs.")
    library = None
    if args.shuffled:
        library = json.loads((ROOT / "engine/setlist/fixtures/mock-library-shuffled.json").read_text())["tracks"]
    flow = SetlistFlow(directory, library=library, output_prefix=directory.name[:2])
    flow.view("prep")
    flow.view("live")
    server = demo_server(flow, args.port)
    print(f"Synthetic Prep/Live demo: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
