#!/usr/bin/env python3
"""Cue Review: tiny local server (Python 3 stdlib only). Run:  python3 serve.py   then open http://localhost:8765
- Audio is streamed READ-ONLY from your music folder (default ~/Music/Master Music). Nothing is ever written there.
- Never touches Serato. Your decisions are appended to corrections.json next to this file."""
import http.server, json, os, sys, time, urllib.parse, glob, mimetypes
HERE = os.path.dirname(os.path.abspath(__file__))
MUSIC = os.path.expanduser(os.environ.get("MUSIC_DIR", "~/Music/Master Music"))
LOG = os.path.abspath(os.environ.get("CORRECTIONS_FILE", os.path.join(HERE, "corrections.json")))
DATA = json.load(open(os.path.abspath(os.environ.get("DATA_FILE", os.path.join(HERE, "data.json")))))
FILES = {t["id"]: t["file"] for t in DATA["tracks"]}
def find(tid):
    name = FILES.get(tid)
    if not name: return None
    p = os.path.join(MUSIC, name)
    if os.path.isfile(p): return p
    m = [x for x in glob.glob(os.path.join(MUSIC, "*")) if os.path.basename(x).endswith(name)]
    return m[0] if m else None
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(s, *a, **k): super().__init__(*a, directory=HERE, **k)
    def log_message(s, *a): pass
    def do_GET(s):
        u = urllib.parse.urlparse(s.path)
        if u.path == "/data.json":
            return s._json(DATA)
        if u.path == "/corrections":
            return s._json(json.load(open(LOG)) if os.path.exists(LOG) else [])
        if u.path.startswith("/audio/"):
            p = find(u.path.split("/")[-1])
            if not p: return s.send_error(404, "track not found in " + MUSIC)
            size = os.path.getsize(p); rng = s.headers.get("Range"); a, b = 0, size - 1
            if rng and rng.startswith("bytes="):
                x, y = rng[6:].split("-"); a = int(x or 0); b = int(y) if y else size - 1
            s.send_response(206 if rng else 200); s.send_header("Content-Type", mimetypes.guess_type(p)[0] or "audio/mpeg"); s.send_header("Cache-Control", "private, max-age=3600"); s.send_header("Accept-Ranges", "bytes")
            s.send_header("Content-Length", str(b - a + 1))
            if rng: s.send_header("Content-Range", f"bytes {a}-{b}/{size}")
            s.end_headers()
            with open(p, "rb") as f:  # read-only
                f.seek(a); left = b - a + 1
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk: break
                    try: s.wfile.write(chunk)
                    except BrokenPipeError: break
                    left -= len(chunk)
            return
        if u.path in ("/corrections.json", "/serve.py"): return s.send_error(404)
        return super().do_GET()
    def do_POST(s):
        if s.path != "/log": return s.send_error(404)
        ev = json.loads(s.rfile.read(int(s.headers["Content-Length"])))
        ev["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        log = json.load(open(LOG)) if os.path.exists(LOG) else []
        log.append(ev); tmp = LOG + ".tmp"; json.dump(log, open(tmp, "w"), indent=1); os.replace(tmp, LOG)
        s._json({"ok": True, "n": len(log)})
    def _json(s, o):
        b = json.dumps(o).encode(); s.send_response(200); s.send_header("Content-Type", "application/json"); s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    print(f"Cue Review on http://localhost:{port}  (music read-only from {MUSIC})")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
