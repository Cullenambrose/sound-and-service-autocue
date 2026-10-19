import base64
import hashlib
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
from pathlib import Path

from web import server


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        server.DATA = root / "data"
        server.UPLOADS = server.DATA / "audio"
        server.DB_PATH = server.DATA / "library.sqlite3"
        server.SESSION_TOKEN = "test-token"
        server.initialize()
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.http_thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.http_thread.join(timeout=2)
        self.temporary.cleanup()

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
            row = db.execute("SELECT source_path FROM tracks WHERE hash=?", (first_hash,)).fetchone()
        self.assertEqual(row["source_path"], str(renamed))
        self.assertEqual(first.read_bytes(), b"same audio content")

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
        updated = server._cue_binary({"A": {"bar": 4, "name": "INTRO"}}, {
            "grid": {"t1": 0, "bar_s": 2},
        }, original_geob)

        entries = server._marker2_entries(updated)
        cue_entries = [payload for name, payload in entries if name == "CUE"]
        self.assertEqual(sum(name == "LOOP" for name, _ in entries), 1)
        self.assertEqual(len(cue_entries), 1)
        self.assertEqual(cue_entries[0][1], 0)
        self.assertEqual(int.from_bytes(cue_entries[0][2:6], "big"), 8000)
        self.assertTrue(cue_entries[0][12:].startswith(b"INTRO"))

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
        request = urllib.request.Request(url, data=payload, method="PUT", headers={
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
        events = json.loads((server.DATA / "corrections.json").read_text())
        self.assertEqual(events[0]["cue"], "A")
        self.assertEqual(events[0]["action"], "set_here")
        self.assertEqual(events[0]["to_beat"], 13)
        self.assertIn("saved_at", events[0])

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

    def test_verified_serato_write_and_undo_restore_exact_mp3_bytes(self):
        try:
            import mutagen.mp3
        except ImportError:
            self.skipTest("Mutagen is required for Serato tag write tests.")

        home = Path(self.temporary.name) / "home"
        music = home / "Music"
        serato = music / "_Serato_"
        serato.mkdir(parents=True)
        (serato / "Subcrates").mkdir()
        (serato / "database V2").write_bytes(b"read-only Serato database")
        song_path = music / "source.mp3"
        frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
        song_path.write_bytes(frame * 3)
        original_bytes = song_path.read_bytes()
        file_hash, _ = server.copy_import(song_path, song_path.name, source_path=str(song_path))
        with server.connect() as db:
            db.execute(
                "UPDATE tracks SET plan_json=?,reviewed=1,status='Cues ready' WHERE hash=?",
                (json.dumps({
                    "grid": {"t1": 0, "bar_s": 2},
                    "cues": {"A": {"name": "INTRO", "bar": 2, "confidence": 0.9}},
                }), file_hash),
            )

        with patch.object(server.Path, "home", return_value=home), patch.object(server, "_serato_open", return_value=False):
            result = server.write_to_serato([file_hash])
            self.assertNotEqual(song_path.read_bytes(), original_bytes)
            self.assertEqual(server._mp3_audio_hash(song_path), hashlib.sha256(frame * 3).hexdigest())
            backup = Path(result["backup_path"])
            with server.connect() as db:
                backup_row = db.execute("SELECT status FROM backups WHERE id=?", (result["backup_id"],)).fetchone()
                written_row = db.execute("SELECT hash,status FROM tracks WHERE source_path=?", (str(song_path),)).fetchone()
            self.assertEqual(backup_row["status"], "written")
            self.assertEqual(written_row["hash"], hashlib.sha256(song_path.read_bytes()).hexdigest())
            self.assertEqual(written_row["status"], "In Serato")
            with server.zipfile.ZipFile(backup) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("serato/0-Music/_Serato_/database V2", archive.namelist())
            server.undo_backup(result["backup_id"])

        self.assertEqual(song_path.read_bytes(), original_bytes)
        self.assertEqual(hashlib.sha256(song_path.read_bytes()).hexdigest(), file_hash)
        with server.connect() as db:
            restored_row = db.execute("SELECT hash,status FROM tracks WHERE source_path=?", (str(song_path),)).fetchone()
        self.assertEqual(restored_row["hash"], file_hash)
        self.assertEqual(restored_row["status"], "Cues ready")

    def test_processor_gap_fails_explicitly_without_changing_staged_audio(self):
        source = Path(self.temporary.name) / "song.wav"
        source.write_bytes(b"audio remains read-only")
        file_hash, _ = server.copy_import(source, source.name)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOCUE_PROCESSOR", None)
            with self.assertRaisesRegex(RuntimeError, "No raw-audio processor is configured"):
                server.process_track(file_hash)
        staged = server.UPLOADS / (file_hash + ".wav")
        self.assertEqual(staged.read_bytes(), b"audio remains read-only")

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
