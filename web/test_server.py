import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
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
            urllib.request.urlopen(url.replace("512", "invalid"))
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
        self.assertEqual(run.call_args.args[0], expected)
        tags.assert_called_once_with(expected)

        def external(argv, **kwargs):
            self.assertEqual(argv[1], expected)
            Path(argv[2]).write_text(json.dumps(plan))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.dict(os.environ, {"AUTOCUE_PROCESSOR": 'fake "{input}" "{output}"'}), \
                patch.object(subprocess, "run", side_effect=external) as run:
            server.process_track(file_hash)
        self.assertEqual(run.call_count, 1)

    def test_library_exposes_outro_evidence_and_cue_warnings_for_review(self):
        source = Path(self.temporary.name) / "song.mp3"
        source.write_bytes(b"music")
        file_hash, _ = server.copy_import(source, source.name)
        evidence = {"status": "analyzed", "last_vocal_end_s": 90.1, "ignored_words": 2,
                    "threshold": 0.003, "coverage_s": 100.0}
        plan = {"cues": {"H": {"bar": None, "reason": "insufficient instrumental outro",
                               "flags": ["configured window unavailable"]}},
                "outro_vocal_evidence": evidence, "no_vocal_free_outro": True}
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        with urllib.request.urlopen(base + "/api/library") as response:
            record = json.load(response)["tracks"][0]
        self.assertEqual(record["outro_vocal_evidence"], evidence)
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
            request = urllib.request.Request(base + f"/api/tracks/{file_hash}",
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
        request = urllib.request.Request(base + f"/api/tracks/{file_hash}",
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
        request = urllib.request.Request(base + f"/api/tracks/{file_hash}",
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
        request = urllib.request.Request(base + f"/api/tracks/{file_hash}",
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

    def test_mapped_review_relabels_countdowns_and_preserves_pickup_in_logs_and_answers(self):
        source = Path(self.temporary.name) / "phrasing.mp3"
        source.write_bytes(b"synthetic phrasing")
        file_hash, _ = server.copy_import(source, source.name, source_path=str(source))
        plan = server._cue_maps_module().apply_cue_map({
            "map_type": "VOCAL", "bar_count": 100, "grid": {"t1": 0.017, "bar_s": 2},
            "cues": {slot: {} for slot in "ABCDEFGH"},
        }, None, {"start": {"bar": 8, "time_s": 15.094, "unsnapped": True},
                  "first_chorus_end": {"bar": 32}, "out": {"bar": 80}}, 0)
        with server.connect() as db:
            db.execute("UPDATE tracks SET plan_json=? WHERE hash=?", (json.dumps(plan), file_hash))
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        request = urllib.request.Request(base + f"/api/tracks/{file_hash}", data=json.dumps({
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
        events = json.loads((server.DATA / "corrections.json").read_text())
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
                request = urllib.request.Request(base + f"/api/tracks/{file_hash}",
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
                events = json.loads((server.DATA / "corrections.json").read_text())[-2:]
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
        request = urllib.request.Request(base + f"/api/tracks/{file_hash}",
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
        request = urllib.request.Request(base + f"/api/tracks/{file_hash}",
            data=b'{"cues":{"A":{"name":"INTRO","bar":"3"}}}', method="PUT",
            headers={"Content-Type": "application/json", "Origin": base,
                     "X-AutoCue-Token": server.SESSION_TOKEN})
        with patch.object(Path, "write_text", side_effect=OSError("Disk full")):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code, 400)
        self.assertIn("Disk full", json.loads(raised.exception.read())["error"])
        with server.connect() as db:
            row = db.execute("SELECT plan_json,reviewed FROM tracks WHERE hash=?", (file_hash,)).fetchone()
        self.assertEqual(json.loads(row["plan_json"]), plan)
        self.assertFalse(row["reviewed"])
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
