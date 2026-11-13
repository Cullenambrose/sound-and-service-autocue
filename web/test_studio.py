import base64
from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

from web import catalog, fixtures, library_operations, personal_crates, studio_ai, studio_service, studio


class StudioTests(unittest.TestCase):
    def cue_fixture(self, **changes):
        self.db.execute("""CREATE TABLE IF NOT EXISTS tracks (
            hash TEXT PRIMARY KEY,plan_json TEXT,reviewed INTEGER,review_revision INTEGER,error TEXT)""")
        digest = self.search()["tracks"][0]["content_hash"]
        plan = {"grid": {"t1": 1, "bar_s": 2, "offset_ms": 100},
                "cues": {"A": {"bar": 4, "name": "Intro", "confidence": 0.7,
                              "mix_window_evidence": {"status": "vocal_overlap"}},
                         "H": {"time_s": 150, "name": "OUT", "confidence": 0.8}},
                "flags": ["Audition required"],
                "grid_diagnostics": {"status": "needs_review"},
                "mix_window_evidence": {"intro": {"status": "candidate", "selected": {
                    "start_s": 0, "end_s": 16, "status": "candidate",
                    "reason": "Synthetic stem fixture, not listening evidence"}}}, **changes}
        self.db.execute("INSERT OR REPLACE INTO tracks VALUES(?,?,1,1,NULL)",
                        (digest, json.dumps(plan)))
        return digest, plan

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.db = sqlite3.connect(self.root / "catalog.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        for module in (catalog, personal_crates, library_operations, studio_service):
            module.initialize(self.db)
        for name in ("One", "Two", "Three"):
            (self.music / (name + ".wav")).write_bytes(fixtures.tagged_wav(seconds=2))
        def metadata(path):
            return {"title": path.stem, "artist": "Invented", "version": "Clean" if path.stem != "Three" else "Dirty",
                    "bpm": "90" if path.stem == "One" else "100", "duration_s": 180, "genre": "R&B",
                    "energy": .4, "key": "8A", "provenance": "generated"}
        with self.db:
            catalog.scan_folder(self.db, str(self.music), metadata)
        self.dataset = "test-dataset"
        self.ids = {track["title"]: track["id"] for track in self.search()["tracks"]}

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def search(self, rule=None):
        with self.db:
            return studio_service.search(self.db, rule or {})

    @contextmanager
    def search_reader(self):
        reader = sqlite3.connect(self.root / "catalog.sqlite3")
        reader.row_factory = sqlite3.Row
        try:
            yield reader
        finally:
            reader.close()

    def test_search_snapshot_reuses_evidence_across_connections_not_returned_objects(self):
        with self.search_reader() as reader:
            first = studio_service.search(reader, {"clean_only": True})
        first["tracks"][0]["title"] = "Consumer mutation"
        first["tracks"][0]["cue_evidence"] = {"invented": True}
        with patch.object(studio_service, "indexed_library", side_effect=AssertionError("Unchanged snapshot")):
            for rule in ({}, {"q": "one"}, {"artist": "INVENTED"}, {"genre": "R&B"},
                         {"bpm_min": 90, "energy_max": .5, "sort": "energy"},
                         {"clean_only": True, "scope_ids": [self.ids["One"]]}):
                with self.search_reader() as reader:
                    actual = studio_service.search(reader, rule)
                tracks, revision = self._uncached_index()
                selected, total = studio_service.select_tracks(tracks, rule)
                self.assertEqual(actual["tracks"], selected)
                self.assertEqual((actual["total"], actual["library_revision"]), (total, revision))
        self.assertNotEqual(actual["tracks"][0]["title"], "Consumer mutation")
        self.assertLessEqual(len(studio_service._SEARCH_SNAPSHOTS), 2)

    def _uncached_index(self):
        # The patch in the reuse test targets only public cache misses.
        with self.db:
            return self._index_reader(self.db)

    _index_reader = staticmethod(studio_service.indexed_library)

    def test_search_snapshot_invalidates_every_evidence_write_family(self):
        from web import studio_ingest, studio_assistant
        digest, _ = self.cue_fixture()
        for module in (studio_ingest, studio_assistant):
            module.initialize(self.db)
        self.db.commit()
        statements = [
            ("UPDATE library_files SET metadata_json=json_set(metadata_json,'$.title','Changed') WHERE id=?",
             (self.ids["One"],)),
            ("UPDATE library_roots SET error='offline'", ()),
            ("UPDATE library_roots SET error=''", ()),
            ("UPDATE library_files SET available=0 WHERE id=?", (self.ids["Two"],)),
            ("UPDATE library_files SET available=1 WHERE id=?", (self.ids["Two"],)),
            ("INSERT INTO library_play_events VALUES('test','test',?,'2026-11-12T00:00:00Z','','{}','{}',0)",
             (self.ids["One"],)),
            ("UPDATE library_play_events SET played_at='2026-11-13T00:00:00Z'", ()),
            ("DELETE FROM library_play_events", ()),
            ("INSERT INTO studio_exclusions VALUES(?,?,'test',1,1)", (self.ids["One"], digest)),
            ("UPDATE studio_exclusions SET excluded=0,revision=2", ()),
            ("DELETE FROM studio_exclusions", ()),
            ("INSERT INTO studio_feedback VALUES(?,1,'test',1)", (self.ids["One"],)),
            ("UPDATE studio_feedback SET rating=-1,revision=2", ()),
            ("DELETE FROM studio_feedback", ()),
            ("UPDATE tracks SET reviewed=0,review_revision=2", ()),
            ("DELETE FROM tracks", ()),
        ]
        with self.search_reader() as reader:
            previous = studio_service.search(reader, {})
        for sql, values in statements:
            with self.subTest(sql=sql):
                with self.db:
                    self.db.execute(sql, values)
                with self.search_reader() as reader, patch.object(
                        studio_service, "indexed_library", wraps=self._index_reader) as index:
                    actual = studio_service.search(reader, {})
                    self.assertEqual(index.call_count, 1)
                expected = self.search()
                self.assertEqual(actual, expected)
                self.assertNotEqual(actual["library_revision"], previous["library_revision"])
                previous = actual

    def test_search_snapshot_preserves_read_transaction_and_never_caches_rollback(self):
        self.db.execute("PRAGMA journal_mode=WAL")
        with self.search_reader() as old_reader:
            before = studio_service.search(old_reader, {})
            with self.db:
                self.db.execute("UPDATE library_files SET metadata_json=json_set(metadata_json,'$.genre','House')")
            self.assertEqual(studio_service.search(old_reader, {}), before)
        with self.search_reader() as reader:
            after = studio_service.search(reader, {})
        self.assertNotEqual(after["library_revision"], before["library_revision"])
        with self.search_reader() as provisional:
            provisional.execute("BEGIN IMMEDIATE")
            provisional.execute("UPDATE library_files SET metadata_json=json_set(metadata_json,'$.genre','Discarded')")
            rejected = studio_service.search(provisional, {})
            self.assertTrue(all(item["genre"] == "Discarded" for item in rejected["tracks"]))
            provisional.rollback()
        with self.search_reader() as reader, patch.object(
                studio_service, "indexed_library", side_effect=AssertionError("Rollback poisoned cache")):
            self.assertEqual(studio_service.search(reader, {}), after)

    def test_search_snapshot_bypasses_late_untracked_tables_and_rejects_bad_metadata(self):
        with self.search_reader() as reader:
            studio_service.search(reader, {})
        self.cue_fixture()
        self.db.commit()
        for _ in range(2):
            with self.search_reader() as reader, patch.object(
                    studio_service, "indexed_library", wraps=self._index_reader) as index:
                self.assertIsNotNone(studio_service.search(reader, {})["tracks"][0]["cue_evidence"])
                self.assertEqual(index.call_count, 1)
        with self.db:
            self.db.execute("UPDATE library_files SET metadata_json='{\"title\":false}'")
        with self.search_reader() as reader, self.assertRaisesRegex(ValueError, "Invalid indexed"):
            studio_service.search(reader, {})

    def preview(self, **changes):
        with self.db:
            return studio_service.propose(self.db, {
                "dataset_id": self.dataset, "name": "Reviewed", "rule": {}, **changes}, self.dataset)

    def approve(self, preview, **changes):
        with self.db:
            return studio_service.approve(self.db, {
                "request_id": "approval", "dataset_id": self.dataset,
                "proposal_id": preview["proposal_id"], **changes}, self.dataset)

    def test_numeric_tag_filters_and_unknowns_clean_dirty_and_deterministic_order(self):
        self.assertEqual([t["title"] for t in self.search({"bpm_max": 90})["tracks"]], ["One"])
        self.assertEqual([t["title"] for t in self.search({"clean_only": True})["tracks"]], ["One", "Two"])
        with self.db:
            self.db.execute("UPDATE library_files SET metadata_json=json_set(metadata_json,'$.bpm',null) WHERE id=?",
                            (self.ids["One"],))
        self.assertEqual(self.search({"bpm_max": 90})["tracks"], [])
        self.assertEqual(self.search({"q": "one invented"})["tracks"][0]["id"], self.ids["One"])

    def test_rules_strict_validation(self):
        bad = [{"sql": "DROP"}, {"limit": True}, {"limit": 0}, {"bpm_max": True},
               {"bpm_min": 100, "bpm_max": 90}, {"energy_max": 3}, {"bpm_min": float("nan")},
               {"sort": []}, {"scope_ids": [{}]}, {"clean_only": 1}, {"never_played": "yes"}]
        for rule in bad:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                studio_service.validate_rule(rule)

    def test_search_does_not_probe_media_or_change_state(self):
        before = self.db.total_changes
        with patch.object(catalog, "file_record", side_effect=AssertionError("No media probes")), \
                patch.object(catalog, "file_hash", side_effect=AssertionError("No media hashing")):
            result = self.search()
        self.assertEqual(len(result["tracks"]), 3)
        self.assertEqual(before, self.db.total_changes)
        self.assertIn("Indexed metadata only", result["evidence"])

    def test_preview_approval_reuses_existing_crates_and_is_retry_safe(self):
        selected = [self.ids["Two"], self.ids["One"]]
        before = {path: path.read_bytes() for path in self.music.iterdir()}
        preview = self.preview(file_ids=selected)
        self.assertEqual(personal_crates.listing(self.db), [])
        receipt = self.approve(preview)
        with self.db:
            crate = personal_crates.detail(self.db, receipt["crate_id"])
        self.assertEqual([entry["song_id"] for entry in crate["entries"]], selected)
        self.assertTrue(all(entry["match_state"] == "exact" for entry in crate["entries"]))
        self.assertEqual(receipt, self.approve(preview))
        self.assertEqual(self.approve(preview, request_id="second-approval")["crate_id"], receipt["crate_id"])
        self.assertEqual(len(personal_crates.listing(self.db)), 1)
        self.assertEqual(before, {path: path.read_bytes() for path in self.music.iterdir()})
        with self.assertRaises(catalog.Conflict):
            self.approve(preview, dataset_id="wrong")

    def test_changed_source_blocks_approval_and_rolls_back_everything(self):
        preview = self.preview(file_ids=[self.ids["One"], self.ids["Two"]])
        (self.music / "Two.wav").write_bytes(b"changed")
        with self.assertRaises(catalog.Conflict):
            self.approve(preview)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM catalog_snapshots").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM catalog_match_decisions").fetchone()[0], 0)
        self.assertEqual(personal_crates.listing(self.db), [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_receipts").fetchone()[0], 0)

    def test_stale_index_expiration_tampered_id_and_injected_file_are_rejected(self):
        preview = self.preview()
        with self.assertRaises(ValueError):
            self.preview(file_ids=["not-retrieved"])
        with patch.object(studio_service.time, "time", return_value=preview["expires_at"] + 1):
            with self.assertRaises(catalog.Conflict):
                self.approve(preview)
        with self.db:
            self.db.execute("UPDATE library_roots SET scan_revision=scan_revision+1")
        with self.assertRaises(catalog.Conflict):
            self.approve(preview)
        with self.db:
            self.db.execute("UPDATE studio_proposals SET document_json='{}' WHERE id=?", (preview["proposal_id"],))
        with self.assertRaises(catalog.Conflict):
            self.approve(preview)

    def test_rule_revision_receipts_persist_and_freeze_does_not_change_rule(self):
        body = {"dataset_id": self.dataset, "request_id": "rule", "id": "dinner", "base_revision": 0,
                "name": "Dinner", "folder": "Events", "rule": {"clean_only": True, "bpm_max": 95}}
        with self.db:
            first = studio_service.save_rule(self.db, body, self.dataset)
        with self.db:
            self.assertEqual(first, studio_service.save_rule(self.db, body, self.dataset))
        for change in ({"request_id": "other"}, {"name": "Changed"}):
            with self.db, self.assertRaises(catalog.Conflict):
                studio_service.save_rule(self.db, {**body, **change}, self.dataset)
        before = studio_service.rules(self.db)
        self.approve(self.preview(rule=body["rule"]))
        self.assertEqual(before, studio_service.rules(self.db))
        self.assertEqual(self.search(body["rule"])["tracks"][0]["id"], self.ids["One"])
        reopened = sqlite3.connect(self.root / "catalog.sqlite3")
        reopened.row_factory = sqlite3.Row
        self.assertEqual(studio_service.rules(reopened), before)
        reopened.close()

    def test_history_changes_rediscovery_and_invalidates_old_proposal(self):
        preview = self.preview(rule={"never_played": True})
        with self.db:
            library_operations.record_play(self.db, {
                "request_id": "played", "dataset_id": self.dataset, "file_id": self.ids["One"],
                "played_at": "2026-11-12T18:00:00Z", "note": "Explicit observation"}, self.dataset)
        self.assertNotIn(self.ids["One"], [t["id"] for t in self.search({"never_played": True})["tracks"]])
        with self.assertRaises(catalog.Conflict):
            self.approve(preview)

    def test_planner_event_bans_must_versions_pins_and_snapshot(self):
        body = {"rule": {}, "policy": {"target_minutes": 6, "tolerance_minutes": 0, "max_tracks": 2},
                "event": {"music_lists": {"must_play": [{"title": "Two", "artist": "Invented", "version": "Clean"}],
                                         "do_not_play": [{"title": "Three", "artist": "Invented"}]}}}
        with self.db:
            planned = studio.dispatch(self.db, "plan", body, self.dataset)
        self.assertTrue(planned["feasible"], planned)
        self.assertEqual({track["id"] for track in planned["alternatives"][0]["tracks"]},
                         {self.ids["One"], self.ids["Two"]})
        with self.db:
            preview = studio.dispatch(self.db, "plan/propose", {
                **body, "dataset_id": self.dataset, "name": "Timed set", "alternative_index": 0}, self.dataset)
        self.assertEqual(preview["planning"]["alternative"]["duration_s"], 360)
        self.assertEqual(self.approve(preview)["entries"], 2)

    def test_phase_plan_evidence_survives_restart_and_reports_later_reorder(self):
        body = {"rule": {}, "event": {}, "policy": {
            "target_minutes": 6, "tolerance_minutes": 0, "max_tracks": 2,
            "phases": [
                {"phase": "dinner", "target_minutes": 3, "tolerance_minutes": 0,
                 "bpm_max": 95, "energy_target": 0.3},
                {"phase": "first_dance", "target_minutes": 3, "tolerance_minutes": 0,
                 "must_include": [self.ids["Two"]], "energy_target": 0.5},
            ]}}
        changes = self.db.total_changes
        with self.db:
            validated = studio.dispatch(self.db, "plan/validate", {"policy": body["policy"]}, self.dataset)
            self.assertEqual(self.db.total_changes, changes)
            planned = studio.dispatch(self.db, "plan", body, self.dataset)
            self.assertTrue(planned["feasible"], planned)
            preview = studio.dispatch(self.db, "plan/propose", {
                **body, "dataset_id": self.dataset, "name": "Phased set", "alternative_index": 0}, self.dataset)
        self.assertEqual(validated["policy"]["phases"][1]["must_include"], [self.ids["Two"]])
        receipt = self.approve(preview)
        reopened = sqlite3.connect(self.root / "catalog.sqlite3")
        reopened.row_factory = sqlite3.Row
        try:
            with reopened:
                evidence = studio.dispatch(reopened, "crate/evidence", {"crate_id": receipt["crate_id"]}, self.dataset)
                self.assertTrue(evidence["current_selection_matches"])
                self.assertEqual(evidence["creation"]["planning"], preview["planning"])
                self.assertEqual([phase["duration_s"] for phase in evidence["creation"]["planning"]["alternative"]["phases"]],
                                 [180, 180])
                with patch.object(studio_service.time, "time", return_value=preview["expires_at"] + 1):
                    self.assertEqual(studio.dispatch(reopened, "crate/evidence",
                        {"crate_id": receipt["crate_id"]}, self.dataset), evidence)
                crate = personal_crates.detail(reopened, receipt["crate_id"])
            with reopened:
                personal_crates.save(reopened, {"request_id": "reorder", "dataset_id": self.dataset,
                    "crate_id": crate["id"], "name": crate["name"], "base_revision": crate["revision"],
                    "entries": [{key: entry[key] for key in ("id", "snapshot_id", "song_id")}
                                for entry in reversed(crate["entries"])]}, self.dataset)
            with reopened:
                changed = studio.dispatch(reopened, "crate/evidence", {"crate_id": crate["id"]}, self.dataset)
            self.assertFalse(changed["current_selection_matches"])
            self.assertEqual(changed["creation"], evidence["creation"])
        finally:
            reopened.close()

    def test_existing_hash_bound_cues_are_read_without_media_probes_and_changes_stale_proposals(self):
        with self.db:
            digest, plan = self.cue_fixture()
        changes = self.db.total_changes
        with patch.object(catalog, "file_hash", side_effect=AssertionError("No media reads")), \
                patch.object(catalog, "file_record", side_effect=AssertionError("No media probes")):
            search = self.search()
        self.assertEqual(self.db.total_changes, changes)
        cue = search["tracks"][0]["cue_evidence"]
        self.assertEqual(cue["content_hash"], digest)
        self.assertEqual(cue["review_revision"], 1)
        self.assertAlmostEqual(cue["cues"]["A"]["time_s"], 8.9)
        self.assertEqual(cue["mix_window_evidence"], plan["mix_window_evidence"])
        self.assertEqual(cue["cues"]["A"]["mix_window_evidence"]["status"], "vocal_overlap")
        preview = self.preview()
        with self.db:
            self.db.execute("UPDATE tracks SET review_revision=2,reviewed=0")
        self.assertNotEqual(self.search()["library_revision"], search["library_revision"])
        with self.assertRaises(catalog.Conflict):
            self.approve(preview)
        current = self.search()["tracks"][0]["cue_evidence"]
        self.assertFalse(current["reviewed"])
        self.assertEqual(current["review_revision"], 2)

    def test_missing_or_unrelated_analysis_never_attaches_and_bad_stored_evidence_is_explicit(self):
        self.assertTrue(all(track["cue_evidence"] is None for track in self.search()["tracks"]))
        with self.db:
            digest, _ = self.cue_fixture()
            self.db.execute("UPDATE tracks SET hash=?", ("f" * 64,))
        self.assertTrue(all(track["cue_evidence"] is None for track in self.search()["tracks"]))
        with self.db:
            self.db.execute("UPDATE tracks SET hash=?,plan_json='[]'", (digest,))
        with self.assertRaisesRegex(ValueError, "Invalid stored cue plan"):
            self.search()
        with self.db:
            self.db.execute("UPDATE tracks SET plan_json=?", (json.dumps({"cues": {
                "A": {"bar": 4}}, "grid": {"bar_s": 0, "t1": 0}}),))
        cue = self.search()["tracks"][0]["cue_evidence"]["cues"]["A"]
        self.assertIsNone(cue["time_s"])
        self.assertIn("cannot be resolved", cue["timing_issue"])

    def test_transition_overlay_is_server_recomputed_and_full_track_feasibility_unchanged(self):
        with self.db:
            self.cue_fixture()
        tracks = self.search()["tracks"]
        selected = [next(track for track in tracks if track["id"] == self.ids[name])
                    for name in ("One", "Two")]
        playback = {"windows": [{"file_id": track["id"], "content_hash": track["content_hash"],
                                 "entry_s": 10, "exit_s": 130, "reviewed": True} for track in selected],
                    "overlaps": [{"from_id": selected[0]["id"], "to_id": selected[1]["id"], "seconds": 16}]}
        body = {"rule": {}, "event": {}, "policy": {"target_minutes": 6, "tolerance_minutes": 0, "max_tracks": 2,
                "pinned": [{"position": position, "file_id": track["id"]} for position, track in enumerate(selected)]},
                "playback": playback}
        with self.db:
            result = studio.dispatch(self.db, "plan", body, self.dataset)
            preview = studio.dispatch(self.db, "plan/propose", {
                **body, "dataset_id": self.dataset, "name": "Windows", "alternative_index": 0}, self.dataset)
        report = result["alternatives"][0]["transition_evidence"]
        self.assertEqual(report["estimated_elapsed_s"], 224)
        self.assertEqual(result["alternatives"][0]["duration_s"], 360)
        self.assertEqual(preview["planning"]["alternative"]["transition_evidence"], report)
        receipt = self.approve(preview)
        with self.db:
            self.db.execute("UPDATE tracks SET review_revision=2")
            evidence = studio.dispatch(self.db, "crate/evidence", {"crate_id": receipt["crate_id"]}, self.dataset)
        self.assertFalse(evidence["current_analysis_matches"])
        self.assertTrue(evidence["current_selection_matches"])
        self.assertEqual(evidence["creation"]["planning"]["alternative"]["transition_evidence"], report)
        playback["windows"][0]["content_hash"] = "b" * 64
        with self.db:
            stale = studio.dispatch(self.db, "plan", body, self.dataset)
        self.assertTrue(stale["feasible"])  # Full-file policy is a different claim.
        self.assertIsNone(stale["alternatives"][0]["transition_evidence"]["estimated_elapsed_s"])
        with self.db, self.assertRaises(ValueError):
            studio.dispatch(self.db, "plan/propose", {
                **body, "dataset_id": self.dataset, "name": "Injected", "alternative_index": 0,
                "transition_evidence": {"estimated_elapsed_s": 1}}, self.dataset)

    def test_phase_must_versions_and_repair_never_override_event_or_pins(self):
        body = {"rule": {}, "event": {"music_lists": {"do_not_play": [
            {"title": "Two", "artist": "Invented"}]}}, "policy": {
                "target_minutes": 6, "tolerance_minutes": 0, "max_tracks": 2,
                "phases": [
                    {"phase": "dinner", "target_minutes": 3, "tolerance_minutes": 0},
                    {"phase": "first_dance", "target_minutes": 3, "tolerance_minutes": 0,
                     "must_include": [self.ids["Two"]]},
                ]}}
        with self.db:
            result = studio.dispatch(self.db, "plan", body, self.dataset)
        self.assertFalse(result["feasible"])
        self.assertIn("required_file_missing", [item["code"] for item in result["unresolved"]])
        body["event"] = {}
        body["policy"]["exclude"] = [self.ids["One"]]
        body["policy"]["pinned"] = [{"position": 0, "file_id": self.ids["Three"]}]
        with self.db:
            repaired = studio.dispatch(self.db, "plan", body, self.dataset)
        self.assertTrue(repaired["feasible"], repaired)
        self.assertTrue(all(alt["tracks"][0]["id"] == self.ids["Three"] and
                            alt["phases"][1]["file_ids"] == [self.ids["Two"]]
                            for alt in repaired["alternatives"]))
        body["policy"]["exclude"].append(self.ids["Two"])
        with self.db:
            self.assertFalse(studio.dispatch(self.db, "plan", body, self.dataset)["feasible"])

    def test_creation_evidence_static_crates_wrong_dataset_and_journal_tamper(self):
        preview = self.preview()
        receipt = self.approve(preview)
        with self.db:
            evidence = studio.dispatch(self.db, "crate/evidence", {"crate_id": receipt["crate_id"]}, self.dataset)
        self.assertIsNone(evidence["creation"]["planning"])
        with self.db, self.assertRaises(catalog.Conflict):
            studio.dispatch(self.db, "crate/evidence", {"crate_id": receipt["crate_id"]}, "wrong-dataset")
        with self.db:
            self.db.execute("UPDATE studio_proposals SET document_json='{}' WHERE id=?", (preview["proposal_id"],))
        with self.db, self.assertRaises(catalog.Conflict):
            studio.dispatch(self.db, "crate/evidence", {"crate_id": receipt["crate_id"]}, self.dataset)
        with self.db, self.assertRaises(ValueError):
            studio.dispatch(self.db, "crate/evidence", {"crate_id": "other-crate"}, self.dataset)

    def test_unavailable_event_version_is_not_silently_substituted(self):
        with self.db, self.assertRaises(ValueError):
            studio.dispatch(self.db, "plan", {
                "rule": {}, "policy": {"target_minutes": 3, "tolerance_minutes": 0, "max_tracks": 2},
                "event": {"music_lists": {"must_play": [{"title": "One", "artist": "Invented", "version": "Dirty"}]}}},
                self.dataset)

    def test_planner_keeps_exact_alternate_files_when_no_event_version_requires_substitution(self):
        with self.db:
            self.db.execute("UPDATE library_files SET metadata_json=json_set(metadata_json,'$.title','Two') WHERE id=?",
                            (self.ids["Three"],))
        with self.db:
            result = studio.dispatch(self.db, "plan", {
                "rule": {}, "event": {}, "policy": {"target_minutes": 3, "tolerance_minutes": 0,
                    "max_tracks": 1, "pinned": [{"position": 0, "file_id": self.ids["Three"]}]}}, self.dataset)
        self.assertTrue(result["feasible"], result)
        self.assertEqual(result["alternatives"][0]["tracks"][0]["id"], self.ids["Three"])

    def test_live_reads_full_event_and_never_mutates_prep(self):
        receipt = self.approve(self.preview())
        with self.db:
            before = personal_crates.detail(self.db, receipt["crate_id"])
        changes = self.db.total_changes
        with self.db:
            live = studio.dispatch(self.db, "live", {"rule": {}, "event": {}, "phase": "dinner",
                                   "now_playing": self.ids["One"], "played_ids": []}, self.dataset)
        self.assertNotIn(self.ids["One"], [item["track"]["id"] for item in live["recommendations"]["items"]])
        self.assertEqual(self.db.total_changes, changes)
        with self.db:
            self.assertEqual(personal_crates.detail(self.db, receipt["crate_id"]), before)

    def test_provider_links_reuse_verified_registry_without_network_or_fulfillment(self):
        changes = self.db.total_changes
        with patch.object(urllib.request, "urlopen", side_effect=AssertionError("No provider request")):
            result = studio.dispatch(self.db, "providers",
                {"artist": "Invented & Artist", "title": "Sample / Song", "version": "Clean"}, self.dataset)
        self.assertEqual(len(result["providers"]), 25)
        self.assertIn("%26", next(item["url"] for item in result["providers"] if item["name"] == "DJcity"))
        self.assertEqual(self.db.total_changes, changes)
        self.assertTrue(all(item["url"].startswith("https://") for item in result["providers"]))


class LocalAITests(unittest.TestCase):
    def planning_intent(self):
        return {"action": "plan", "name": "Dinner then dancing", "rule": {"clean_only": True},
                "unsupported": [], "policy": {
                    "target_minutes": 30, "tolerance_minutes": 3, "max_tracks": 20,
                    "phases": [
                        {"phase": "dinner", "target_minutes": 10, "tolerance_minutes": 1, "energy_target": 0.3},
                        {"phase": "open_dancing", "target_minutes": 20, "tolerance_minutes": 1, "energy_target": 0.8},
                    ]}}

    def test_planning_intent_accepts_phase_policy_but_never_invents_file_obligations(self):
        intent = self.planning_intent()
        result = studio_ai.validate_plan_intent(intent)
        self.assertEqual(result["policy"]["target_minutes"], 30)
        self.assertEqual(result["policy"]["must_include"], [])
        self.assertEqual(result["policy"]["phases"][1]["energy_target"], 0.8)
        self.assertNotIn("must_include", intent["policy"])
        for field in ("must_include", "exclude", "pinned"):
            invalid = copy.deepcopy(intent)
            invalid["policy"][field] = []
            with self.subTest(field=field), self.assertRaises(ValueError):
                studio_ai.validate_plan_intent(invalid)
            invalid = copy.deepcopy(intent)
            invalid["policy"]["phases"][0][field] = []
            with self.subTest(phase_field=field), self.assertRaises(ValueError):
                studio_ai.validate_plan_intent(invalid)
        invalids = [
            intent | {"rule": {"scope_ids": ["invented-file"]}},
            intent | {"event": {"music_lists": {"do_not_play": []}}},
            intent | {"action": "save"},
            intent | {"unsupported": ["Missing intended duration"]},
            intent | {"policy": intent["policy"] | {"target_minutes": 31}},
        ]
        for invalid in invalids:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                studio_ai.validate_plan_intent(invalid)

    def test_local_planning_payload_and_invalid_envelopes_use_same_bounded_adapter(self):
        timing = {"target_minutes": 30, "tolerance_minutes": None, "max_tracks": None,
                  "phases": [{"phase": "dinner", "target_minutes": 10, "quote": "dinner 10"},
                             {"phase": "open_dancing", "target_minutes": 20, "quote": "dancing 20"}]}
        constraints = {"constraints": [{"field": "clean_only", "value": True, "quote": "tagged clean"}],
                       "unsupported": []}
        for contents in (timing, [], {"done": True, "message": []}):
            responses = iter([{"done": True, "message": {"content": json.dumps(timing)}},
                              {"done": True, "message": {"content": json.dumps(constraints)}}]
                             if contents == timing else [contents])
            @contextmanager
            def response(*args, **kwargs):
                yield io.BytesIO(json.dumps(next(responses)).encode())
            with patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": "already-installed"}), \
                    patch.object(urllib.request, "build_opener") as factory:
                factory.return_value.open.side_effect = response
                if contents == timing:
                    result = studio_ai.interpret_plan("Plan 30 minutes: dinner 10 and dancing 20, tagged clean")
                    sent = [call.args[0] for call in factory.return_value.open.call_args_list]
                    self.assertEqual(len(sent), 2)
                else:
                    with self.assertRaisesRegex(ValueError, "invalid response envelope"):
                        studio_ai.interpret_plan("Plan 30 minutes")
        for request in sent:
            payload = json.loads(request.data)
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
            self.assertEqual(payload["format"], "json")
            self.assertEqual(payload["options"]["num_ctx"], 4096)
            self.assertEqual(payload["options"]["repeat_penalty"], 1.1)
            self.assertNotIn("Output schema:", payload["messages"][0]["content"])
            self.assertNotIn("scope_ids", payload["messages"][0]["content"])
        self.assertEqual(result["intent"]["policy"]["phases"][0]["target_minutes"], 10)
        self.assertEqual(result["constraint_evidence"], constraints["constraints"])
        with patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": ""}), patch.object(urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "not configured"):
                studio_ai.interpret_plan("Plan 30 minutes")
            opener.assert_not_called()

    def test_not_configured_never_sends_and_invalid_intents_have_no_tools(self):
        with patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": ""}), patch.object(urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "not configured"):
                studio_ai.interpret("Search dinner songs")
            opener.assert_not_called()
        for invalid in (
            {"action": "delete", "name": "Bad", "rule": {}, "unsupported": []},
            {"action": "build", "name": "Bad", "rule": {"sql": "DELETE"}, "unsupported": []},
            {"action": "build", "name": "Bad", "rule": {}, "unsupported": ["exact 1990s years"]},
        ):
            with self.assertRaises(ValueError):
                studio_ai.validate_intent(invalid)
        with patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": "example:cloud"}), \
                patch.object(urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "Cloud-tagged"):
                studio_ai.interpret("Search dinner songs")
            opener.assert_not_called()

    def test_local_payload_is_bounded_contains_no_library_and_validates_result(self):
        intent = {"action": "build", "name": "Dinner", "rule": {"clean_only": True, "bpm_max": 95}, "unsupported": []}
        envelope = {"done": True, "message": {"content": json.dumps(intent)}}
        @contextmanager
        def response(*args, **kwargs):
            yield io.BytesIO(json.dumps(envelope).encode())
        with patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": "already-installed"}), \
                patch.object(urllib.request, "build_opener") as factory:
            factory.return_value.open.side_effect = response
            result = studio_ai.interpret("Build dinner")
            sent = factory.return_value.open.call_args[0][0]
        self.assertEqual(sent.full_url, "http://127.0.0.1:11434/api/chat")
        payload = json.loads(sent.data)
        self.assertEqual(set(payload), {"model", "stream", "format", "options", "messages"})
        self.assertNotIn("tracks", json.dumps(payload))
        self.assertEqual(result["intent"]["rule"]["bpm_max"], 95)

    def test_local_native_json_rejects_duplicate_fields_and_nonfinite_numbers(self):
        for content in (
            '{"action":"build","name":"Test","rule":{"limit":10,"limit":20},"unsupported":[]}',
            '{"action":"search","action":"build","name":"Test","rule":{},"unsupported":[]}',
            '{"action":"search","name":"Test","rule":{"energy_min":NaN},"unsupported":[]}',
            '{"action":"search","name":"Test","rule":{"bpm_max":Infinity},"unsupported":[]}',
        ):
            @contextmanager
            def response(*args, **kwargs):
                yield io.BytesIO(json.dumps({"done": True, "message": {"content": content}}).encode())
            with self.subTest(content=content), \
                    patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": "already-installed"}), \
                    patch.object(urllib.request, "build_opener") as factory:
                factory.return_value.open.side_effect = response
                with self.assertRaisesRegex(ValueError, "duplicate JSON keys|nonfinite JSON number"):
                    studio_ai.interpret("Build ten songs")

    def test_timing_extraction_preserves_explicit_values_and_rejects_inventions(self):
        message = "10 minutes cocktail then 20 minutes dinner, tolerance 0, at most 8 songs"
        value = {"target_minutes": None, "tolerance_minutes": 0, "max_tracks": 8, "phases": [
            {"phase": "cocktail", "target_minutes": 10, "quote": "10 minutes cocktail"},
            {"phase": "dinner", "target_minutes": 20, "quote": "20 minutes dinner"}]}
        policy = studio_ai._validate_timing(value, message)
        self.assertEqual((policy["target_minutes"], policy["tolerance_minutes"], policy["max_tracks"]), (30, 0, 8))
        self.assertEqual([phase["target_minutes"] for phase in policy["phases"]], [10, 20])
        self.assertEqual(studio_ai._validate_timing({"target_minutes": 17}, "17 minute dinner")["max_tracks"], 20)
        self.assertEqual(studio_ai._validate_timing({"target_minutes": 90}, "1.5 hour set")["target_minutes"], 90)
        self.assertEqual(studio_ai._validate_timing({"target_minutes": 30}, ".5 hours")["target_minutes"], 30)
        invalids = [
            (value | {"max_tracks": 19}, message),
            (value | {"target_minutes": 60}, message),
            (value | {"phases": [value["phases"][0] | {"quote": "made up"}]}, message),
            (value | {"phases": [value["phases"][0] | {"target_minutes": 15}]}, message),
            (value | {"phases": [value["phases"][0] | {"bpm_min": 120}]}, message),
            ({"target_minutes": True}, "1 minute"),
            ({"target_minutes": 30, "phases": None}, "30 minutes"),
            ({"target_minutes": 60}, "-1 hours"),
            (value | {"phases": [value["phases"][0] | {"tolerance_minutes": 9}]}, message),
        ]
        for invalid, prompt in invalids:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                studio_ai._validate_timing(invalid, prompt)
        with self.assertRaisesRegex(ValueError, "missing intended duration"):
            studio_ai._validate_timing({"target_minutes": None, "phases": []}, "clean dinner")

    def test_literal_constraints_preserve_phase_bounds_and_reject_bad_evidence(self):
        message = "10 minutes cocktail then 20 minutes dinner; clean House, 118 BPM minimum, energy 0"
        timing = studio_ai._validate_timing({"target_minutes": 30, "phases": [
            {"phase": "cocktail", "target_minutes": 10, "quote": "10 minutes cocktail"},
            {"phase": "dinner", "target_minutes": 20, "quote": "20 minutes dinner"}]}, message)
        constraints = [{"field": "genre", "value": "House", "quote": "House"},
                       {"field": "clean_only", "value": True, "quote": "clean"},
                       {"field": "bpm_min", "value": 118, "quote": "118 BPM minimum", "phase_index": 1},
                       {"field": "energy_target", "value": 0, "quote": "energy 0", "phase_index": 0}]
        result = studio_ai._validate_constraints({"constraints": constraints, "unsupported": []}, message, timing)
        self.assertEqual(result["rule"]["genre"], "House")
        self.assertTrue(result["rule"]["clean_only"])
        self.assertEqual(result["policy"]["phases"][1]["bpm_min"], 118)
        self.assertEqual(result["policy"]["phases"][0]["energy_target"], 0)
        self.assertEqual(result["constraint_evidence"], constraints)
        bad_items = [
            constraints[0] | {"value": "Techno"},
            constraints[0] | {"quote": "Techno"},
            constraints[0] | {"field": "scope_ids"},
            constraints[0] | {"value": None},
            constraints[0] | {"scope": "policy"},
            constraints[0] | {"phase_index": 0},
            constraints[2] | {"value": 125},
            constraints[2] | {"phase_index": 2},
            constraints[2] | {"phase_index": True},
            constraints[2] | {"value": "118"},
        ]
        for item in bad_items:
            with self.subTest(item=item), self.assertRaises(ValueError):
                studio_ai._validate_constraints({"constraints": [item], "unsupported": []}, message, timing)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            studio_ai._validate_constraints({"constraints": constraints * 2, "unsupported": []}, message, timing)
        with self.assertRaisesRegex(ValueError, "cannot enforce"):
            studio_ai._validate_constraints({"constraints": [], "unsupported": ["export"]}, message, timing)
        self.assertNotIn("bpm_min", timing["phases"][1])

    def test_failed_timing_does_not_send_music_extraction(self):
        @contextmanager
        def response(*args, **kwargs):
            yield io.BytesIO(json.dumps({"done": True, "message": {"content": json.dumps({
                "target_minutes": None, "phases": []})}}).encode())
        with patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": "already-installed"}), \
                patch.object(urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = response
            with self.assertRaisesRegex(ValueError, "missing intended duration"):
                studio_ai.interpret_plan("Plan a clean dinner set.")
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_explicit_release_date_filters_fail_before_model_or_configuration(self):
        prompts = [
            "Plan 18 minutes of House songs released in 2007.",
            "Search tracks RELEASED BEFORE 1999.",
            "Build songs released between 2000 and 2010.",
            "Find songs from the 1990s.",
            "Find music from 1980s.",
            "Find release-year:2007 songs.",
            "Find tracks with release date after 2007.",
            "Search q=Released in 2007",
        ]
        with patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": ""}), \
                patch.object(urllib.request, "build_opener") as opener:
            for prompt in prompts:
                for translate in (studio_ai.interpret, studio_ai.interpret_plan):
                    with self.subTest(prompt=prompt, translate=translate), \
                            self.assertRaisesRegex(studio_ai.UnsupportedRequest, "No AI request was sent"):
                        translate(prompt)
            opener.assert_not_called()
        for prompt in (
            "Plan 120 minutes of House.",
            "Find songs by artist Year of the Ox.",
            'Find artist "The 1975".',
            "Plan 20 minutes from my 2007 crate.",
        ):
            studio_ai.check_request(prompt)

    def test_phase_quotes_must_be_nonoverlapping_and_in_source_order(self):
        message = "10 minutes cocktail, 20 minutes dinner"
        phases = [{"phase": "cocktail", "target_minutes": 10, "quote": "10 minutes cocktail"},
                  {"phase": "dinner", "target_minutes": 20, "quote": "20 minutes dinner"}]
        for invalid in (list(reversed(phases)), phases[:1] * 2,
                        [phases[0] | {"quote": message}, phases[1]]):
            with self.subTest(phases=invalid), self.assertRaisesRegex(ValueError, "not grounded"):
                studio_ai._validate_timing({"target_minutes": 30, "phases": invalid}, message)
        repeated = "10 minutes dinner, then 10 minutes dinner"
        result = studio_ai._validate_timing({"target_minutes": None, "phases": [
            {"phase": "dinner", "target_minutes": 10, "quote": "10 minutes dinner"}] * 2}, repeated)
        self.assertEqual(result["target_minutes"], 20)
        self.assertEqual(len(result["phases"]), 2)

    def test_boolean_evidence_requires_relevant_words_and_strict_types(self):
        timing = studio_ai._validate_timing({"target_minutes": 20}, "20 minutes dinner")
        for item, prompt in (
            ({"field": "clean_only", "value": True, "quote": "dinner"}, "20 minutes dinner"),
            ({"field": "never_played", "value": True, "quote": "House"}, "20 minutes House"),
            ({"field": "use_feedback", "value": True, "quote": "House"}, "20 minutes House"),
            ({"field": "clean_only", "value": "true", "quote": "clean"}, "20 minutes clean"),
            ({"field": "clean_only", "value": 1, "quote": "clean"}, "20 minutes clean"),
        ):
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, "boolean constraint"):
                studio_ai._validate_constraints({"constraints": [item], "unsupported": []}, prompt, timing)
        for field, quote in (("clean_only", "non-clean"), ("never_played", "never played"),
                             ("use_feedback", "feedback")):
            result = studio_ai._validate_constraints({"constraints": [
                {"field": field, "value": False, "quote": quote}], "unsupported": []}, quote, timing)
            target = result["policy"] if field == "use_feedback" else result["rule"]
            self.assertIs(target[field], False)

    def test_runtime_http_error_reports_bounded_cause(self):
        error = urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", 500, "error", {},
                                       io.BytesIO(b'{"error":"token repeat limit reached"}' + b"x" * 3000))
        with patch.dict(os.environ, {"AUTOCUE_STUDIO_MODEL": "already-installed"}), \
                patch.object(urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(RuntimeError, "HTTP 500.*token repeat limit") as raised:
                studio_ai.interpret("Search House")
            self.assertLess(len(str(raised.exception)), 2200)


class StudioHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.patches = [patch.object(studio.server, name, value) for name, value in (
            ("DATA", self.data), ("UPLOADS", self.data / "audio"), ("DB_PATH", self.data / "library.sqlite3"))]
        for item in self.patches:
            item.start()
        studio.initialize()
        studio.create_demo()
        self.http = studio.server.LocalHTTPServer(("127.0.0.1", 0), studio.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        self.session = self.call("/api/session")[1]

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def call(self, path, body=None, headers=None):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
            headers=headers if headers is not None else {"Content-Type": "application/json",
                    "X-AutoCue-Token": studio.server.SESSION_TOKEN})
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
        with response:
            return response.status, json.load(response)

    def test_release_filter_veto_is_visible_over_http_without_inference(self):
        prompt = "Plan 18 minutes of House songs released in 2007."
        with patch.object(urllib.request, "build_opener") as model:
            for path in ("/api/studio/interpret", "/api/studio/interpret/plan"):
                with self.subTest(path=path):
                    code, response = self.call(path, {"message": prompt})
                    self.assertEqual(code, 400)
                    self.assertIn("release-year/date filtering", response["error"])
                    self.assertIn("No AI request was sent", response["error"])
            model.assert_not_called()
        with studio.server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM studio_conversations").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM studio_receipts").fetchone()[0], 0)

    def test_database_backup_over_http_is_session_gated_and_does_not_probe_audio(self):
        from web import studio_backup
        with patch.object(catalog, "file_hash", side_effect=AssertionError("No audio hashing")), \
                patch.object(catalog, "file_record", side_effect=AssertionError("No source probes")):
            code, result = self.call("/api/studio/backup", {})
        self.assertEqual(code, 200, result)
        bundle = base64.b64decode(result["bundle_base64"], validate=True)
        self.assertEqual(studio_backup.inspect(bundle)[1], result["manifest"])
        self.assertEqual(result["manifest"]["table_counts"]["library_files"], 12)
        self.assertIn("No audio", result["manifest"]["note"])
        for headers in ({}, {"X-AutoCue-Token": studio.server.SESSION_TOKEN,
                             "Origin": "https://elsewhere.invalid"}):
            self.assertEqual(self.call("/api/studio/backup", {}, headers)[0], 403)
        self.assertEqual(self.call("/api/studio/backup", {"path": "/not-allowed"})[0], 400)

    def test_reviewed_history_removal_and_undo_over_http_replay_and_search(self):
        dataset = self.session["dataset_id"]
        track = self.call("/api/studio/search", {"rule": {}})[1]["tracks"][0]
        observation = {"request_id": "http-history-play", "dataset_id": dataset, "file_id": track["id"],
                       "played_at": "2026-11-12T12:00:00Z", "note": "Synthetic observation"}
        self.assertEqual(self.call("/api/studio/history/record", observation)[0], 200)
        rule = {"scope_ids": [track["id"]], "never_played": True}
        self.assertEqual(self.call("/api/studio/search", {"rule": rule})[1]["total"], 0)
        code, preview = self.call("/api/studio/history/preview", {
            "dataset_id": dataset, "event_ids": [observation["request_id"]]})
        self.assertEqual(code, 200, preview)
        body = {"dataset_id": dataset, "request_id": "http-history-removal",
                "edit_id": preview["edit_id"], "event_ids": preview["event_ids"]}
        code, receipt = self.call("/api/studio/history/approve", body)
        self.assertEqual(code, 200, receipt)
        self.assertEqual(self.call("/api/studio/history/approve", body)[1], receipt)
        self.assertEqual(self.call("/api/studio/search", {"rule": rule})[1]["total"], 1)
        state = self.call("/api/studio/state")[1]
        self.assertEqual(state["history"], [])
        self.assertEqual(state["history_edits"][0]["approval"], receipt)
        undo_body = {**body, "request_id": "http-history-undo"}
        code, undo = self.call("/api/studio/history/undo", undo_body)
        self.assertEqual(code, 200, undo)
        self.assertEqual(self.call("/api/studio/history/undo", undo_body)[1], undo)
        self.assertEqual(self.call("/api/studio/search", {"rule": rule})[1]["total"], 0)
        self.assertEqual(self.call("/api/studio/history/approve", {**body, "request_id": "bad",
            "event_ids": ["other"]})[0], 409)
        self.assertEqual(self.call("/api/studio/history/preview", {
            "dataset_id": dataset, "event_ids": [observation["request_id"]]}, {})[0], 403)

    def test_end_to_end_save_export_history_live_isolation_and_gates(self):
        dataset = self.session["dataset_id"]
        search = self.call("/api/studio/search", {"rule": {"clean_only": True, "limit": 4}})[1]
        self.assertEqual(len(search["tracks"]), 4)
        preview = self.call("/api/studio/propose", {
            "dataset_id": dataset, "rule": {"clean_only": True, "limit": 4}, "name": "HTTP crate"})[1]
        code, saved = self.call("/api/studio/approve", {
            "dataset_id": dataset, "request_id": "http-save", "proposal_id": preview["proposal_id"]})
        self.assertEqual(code, 200, saved)
        body = {"dataset_id": dataset, "crate_id": saved["crate_id"], "base_revision": 1}
        code, exported = self.call("/api/catalog/personal/export/preview", body)
        self.assertEqual(code, 200, exported)
        self.assertTrue(exported["manifest"]["ready"], exported)
        code, bundle = self.call("/api/catalog/personal/export/download", {**body, "preview_id": exported["preview_id"]})
        self.assertEqual(code, 200, bundle)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(bundle["bundle_base64"]))) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(len(manifest["entries"]), 4)
        state = self.call("/api/studio/state")[1]
        self.assertFalse(state["capabilities"]["commercial_ready"])
        self.assertTrue(state["capabilities"]["release_gates"])
        self.assertEqual(state["crates"][0]["entries"], 4)
        with patch.object(catalog, "file_hash", side_effect=AssertionError("state must not hash music")):
            self.assertEqual(self.call("/api/studio/state")[0], 200)

    def test_phase_schedule_validation_plan_save_and_historical_evidence_over_http(self):
        body = {"rule": {"clean_only": True, "limit": 4}, "event": {}, "policy": {
            "target_minutes": 6, "tolerance_minutes": 0, "max_tracks": 2,
            "phases": [
                {"phase": "dinner", "target_minutes": 3, "tolerance_minutes": 0, "energy_target": 0.2},
                {"phase": "open_dancing", "target_minutes": 3, "tolerance_minutes": 0, "energy_target": 0.8},
            ]}}
        code, result = self.call("/api/studio/plan/validate", {"policy": body["policy"]})
        self.assertEqual(code, 200, result)
        code, planned = self.call("/api/studio/plan", body)
        self.assertEqual(code, 200, planned)
        self.assertTrue(planned["feasible"], planned)
        self.assertEqual(len(planned["alternatives"][0]["phases"]), 2)
        code, preview = self.call("/api/studio/plan/propose", {
            **body, "dataset_id": self.session["dataset_id"], "name": "HTTP phases", "alternative_index": 0})
        self.assertEqual(code, 200, preview)
        code, saved = self.call("/api/studio/approve", {
            "dataset_id": self.session["dataset_id"], "request_id": "phases", "proposal_id": preview["proposal_id"]})
        self.assertEqual(code, 200, saved)
        code, evidence = self.call("/api/studio/crate/evidence", {"crate_id": saved["crate_id"]})
        self.assertEqual(code, 200, evidence)
        self.assertTrue(evidence["current_selection_matches"])
        self.assertEqual(evidence["creation"]["planning"]["alternative"], planned["alternatives"][0])
        body["policy"]["phases"][0]["target_minutes"] = 4
        self.assertEqual(self.call("/api/studio/plan/validate", {"policy": body["policy"]})[0], 400)

    def test_ai_planning_translation_is_separate_from_search_generation_and_saves(self):
        intent = studio_ai.validate_plan_intent(LocalAITests().planning_intent())
        with studio.server.connect() as db:
            before = db.execute("SELECT COUNT(*) FROM studio_proposals").fetchone()[0]
        with patch.object(studio_ai, "interpret_plan", return_value={"intent": intent}), \
                patch.object(studio, "plan_set", side_effect=AssertionError("No generation")), \
                patch.object(studio_service, "search", side_effect=AssertionError("No search")):
            code, translated = self.call("/api/studio/interpret/plan", {"message": "A generated set brief"})
        self.assertEqual(code, 200, translated)
        self.assertEqual(translated["intent"]["policy"]["target_minutes"], 30)
        with studio.server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM studio_proposals").fetchone()[0], before)
            self.assertEqual(personal_crates.listing(db), [])
        self.assertEqual(self.call("/api/studio/interpret/plan", {"message": "Plan", "tracks": []})[0], 400)

    def test_transition_report_is_identical_in_preview_saved_journal_and_export_manifest(self):
        tracks = self.call("/api/studio/search", {"rule": {"clean_only": True, "limit": 2}})[1]["tracks"]
        playback = {"windows": [{"file_id": track["id"], "content_hash": track["content_hash"],
                                 "entry_s": 10, "exit_s": 130, "reviewed": True} for track in tracks],
                    "overlaps": [{"from_id": tracks[0]["id"], "to_id": tracks[1]["id"], "seconds": 16}]}
        body = {"rule": {"clean_only": True, "limit": 2}, "event": {}, "playback": playback,
                "policy": {"target_minutes": 6, "tolerance_minutes": 0, "max_tracks": 2,
                           "pinned": [{"position": index, "file_id": track["id"]}
                                      for index, track in enumerate(tracks)]}}
        code, planned = self.call("/api/studio/plan", body)
        self.assertEqual(code, 200, planned)
        report = planned["alternatives"][0]["transition_evidence"]
        self.assertEqual(report["estimated_elapsed_s"], 224)
        code, preview = self.call("/api/studio/plan/propose", {
            **body, "dataset_id": self.session["dataset_id"], "name": "Transition HTTP", "alternative_index": 0})
        self.assertEqual(code, 200, preview)
        code, saved = self.call("/api/studio/approve", {
            "dataset_id": self.session["dataset_id"], "request_id": "transitions",
            "proposal_id": preview["proposal_id"]})
        self.assertEqual(code, 200, saved)
        export_body = {"dataset_id": self.session["dataset_id"], "crate_id": saved["crate_id"], "base_revision": 1}
        code, exported = self.call("/api/catalog/personal/export/preview", export_body)
        self.assertEqual(code, 200, exported)
        self.assertEqual(exported["manifest"]["studio_creation"]["creation"]["planning"]["alternative"]["transition_evidence"],
                         report)
        code, downloaded = self.call("/api/catalog/personal/export/download", {
            **export_body, "preview_id": exported["preview_id"]})
        self.assertEqual(code, 200, downloaded)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(downloaded["bundle_base64"]))) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(len(archive.namelist()), 3)
            self.assertEqual(manifest, exported["manifest"])
            self.assertEqual(manifest["studio_creation"]["creation"]["planning"]["alternative"]["transition_evidence"], report)
        from web import playlist_export
        data, historical, _ = playlist_export.saved_bundle(
            self.data / "exports", exported["preview_id"], self.session["dataset_id"])
        self.assertEqual(data, base64.b64decode(downloaded["bundle_base64"]))
        self.assertEqual(historical, manifest)
        # A later cue review does not rewrite the saved creation evidence.
        with studio.server.connect() as db:
            db.execute("""INSERT INTO tracks(hash,name,audio_path,imported_at,plan_json,reviewed,review_revision)
                          VALUES(?,?,?,?,?,1,1)""",
                       (tracks[0]["content_hash"], "Synthetic", "", 0,
                        json.dumps({"cues": {"A": {"time_s": 0}}})))
        code, changed = self.call("/api/catalog/personal/export/preview", export_body)
        self.assertEqual(code, 200, changed)
        self.assertFalse(changed["manifest"]["studio_creation"]["current_analysis_matches"])
        self.assertEqual(changed["manifest"]["studio_creation"]["creation"]["planning"]["alternative"]["transition_evidence"], report)
        self.assertEqual(self.call("/api/catalog/personal/export/download", {
            **export_body, "preview_id": exported["preview_id"]})[0], 409)

    def test_cross_origin_no_token_and_source_mutations_are_blocked(self):
        for headers in ({}, {"X-AutoCue-Token": studio.server.SESSION_TOKEN, "Origin": "https://elsewhere.invalid"}):
            self.assertEqual(self.call("/api/studio/search", {"rule": {}}, headers)[0], 403)
        for endpoint in ("/api/settings", "/api/import", "/api/write", "/api/backup"):
            self.assertEqual(self.call(endpoint, {})[0], 403)
        self.assertEqual(self.call("/api/studio/state", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.call("/api/studio/state", headers={"Host": "127.0.0.1:1"})[0], 403)


if __name__ == "__main__":
    unittest.main()
