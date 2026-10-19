import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
from pathlib import Path

from web import fixtures, server


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
                    self.assertEqual(len(server._marker2_entries(existing)), 2)
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
        self.assertEqual(sorted(names), ["CUE", "LOOP"])

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
