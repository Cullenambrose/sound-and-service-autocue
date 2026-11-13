from contextlib import contextmanager
import copy
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from web import catalog, personal_crates, scan_jobs, studio_assistant, studio_ingest, studio_organize, studio_service
from web import test_studio as studio_tests


class ProductTests(unittest.TestCase):
    search = studio_tests.StudioTests.search
    preview = studio_tests.StudioTests.preview
    approve = studio_tests.StudioTests.approve

    def setUp(self):
        studio_tests.StudioTests.setUp(self)
        for module in (studio_ingest, studio_organize, studio_assistant, scan_jobs):
            module.initialize(self.db)
        with self.db:
            studio_ingest.reconcile(self.db)

    tearDown = studio_tests.StudioTests.tearDown

    def action(self, function, **fields):
        with self.db:
            return function(self.db, {"request_id": "request", "dataset_id": self.dataset, **fields}, self.dataset)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "catalog.sqlite3")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def test_inbox_review_retry_reopen_and_changed_evidence(self):
        state = studio_ingest.state(self.db)
        self.assertEqual(state["counts"], {"new": 3})
        item = state["items"][0]
        self.assertEqual(item["exact_hash_group_count"], 3)
        receipt = self.action(studio_ingest.review, file_id=item["file_id"], base_revision=1, status="reviewed")
        self.assertEqual(self.action(studio_ingest.review, file_id=item["file_id"], base_revision=1, status="reviewed"), receipt)
        with self.connect() as db:
            self.assertEqual(studio_ingest.state(db)["counts"]["reviewed"], 1)
        with self.db:
            self.db.execute("UPDATE library_files SET content_hash=? WHERE id=?", ("b" * 64, item["file_id"]))
            studio_ingest.reconcile(self.db)
        changed = next(row for row in studio_ingest.state(self.db)["items"] if row["file_id"] == item["file_id"])
        self.assertEqual(changed["status"], "new")
        self.assertEqual(changed["revision"], 3)
        with self.assertRaises(catalog.Conflict):
            self.action(studio_ingest.review, file_id=item["file_id"], base_revision=2, status="reviewed",
                        request_id="other")

    def test_monitor_explicit_opt_in_incremental_job_and_disable(self):
        with self.db:
            root_id = self.db.execute("SELECT id FROM library_roots").fetchone()[0]
        studio_ingest.tick(self.connect, self.dataset)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM catalog_scan_jobs").fetchone()[0], 0)
        self.db.commit()
        self.action(studio_ingest.monitor, root_id=root_id, base_revision=0, enabled=True, interval_s=60)
        studio_ingest.tick(self.connect, self.dataset, now=10**12)
        studio_ingest.tick(self.connect, self.dataset, now=10**12 + 120)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM catalog_scan_jobs").fetchone()[0], 1)
        self.db.commit()
        self.assertTrue(scan_jobs.run_next(self.connect))
        job = scan_jobs.jobs(self.db)[0]
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["result"]["reused"], 3)
        self.db.commit()
        self.action(studio_ingest.monitor, request_id="disable", root_id=root_id, base_revision=1,
                    enabled=False, interval_s=60)
        studio_ingest.tick(self.connect, self.dataset, now=10**12 + 300)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM catalog_scan_jobs").fetchone()[0], 1)

    def test_monitor_admission_failure_is_visible_with_backoff(self):
        root_id = self.db.execute("SELECT id FROM library_roots").fetchone()[0]
        self.db.commit()
        self.action(studio_ingest.monitor, root_id=root_id, base_revision=0, enabled=True, interval_s=60)
        with patch.object(scan_jobs, "submit", side_effect=OSError("Synthetic disconnected volume")):
            studio_ingest.tick(self.connect, self.dataset, now=10**12)
        monitor = studio_ingest.state(self.db)["monitors"][0]
        self.assertIn("disconnected", monitor["error"])
        self.assertEqual(monitor["next_scan"], 10**12 + 60)

    def test_bulk_all_or_none_retry_and_history(self):
        with self.db:
            batch = studio_organize.preview(self.db, {"dataset_id": self.dataset, "crates": [
                {"name": "Clean", "rule": {"clean_only": True}}, {"name": "Dinner", "rule": {"bpm_max": 90}}
            ]}, self.dataset)
        receipt = self.action(studio_organize.approve, batch_id=batch["batch_id"])
        self.assertEqual(len(receipt["crates"]), 2)
        self.assertEqual(len(personal_crates.listing(self.db)), 2)
        self.db.commit()
        self.assertEqual(self.action(studio_organize.approve, batch_id=batch["batch_id"]), receipt)
        self.assertEqual(studio_organize.history(self.db)[0], receipt)

    def create_edit_crates(self):
        with self.db:
            batch = studio_organize.preview(self.db, {"dataset_id": self.dataset, "crates": [
                {"name": "Source", "rule": {"limit": 3}}, {"name": "Target", "rule": {"limit": 1}}
            ]}, self.dataset)
        receipt = self.action(studio_organize.approve, request_id="setup", batch_id=batch["batch_id"])
        return [crate["crate_id"] for crate in receipt["crates"]]

    def preview_edit(self, operations):
        with self.db:
            return studio_organize.edit_preview(self.db, {"dataset_id": self.dataset, "operations": operations}, self.dataset)

    def test_edit_atomic_rename_move_and_restart_safe_undo_receipts(self):
        source, target = self.create_edit_crates()
        before = {key: studio_organize._snapshot(self.db, key) for key in (source, target)}
        self.db.commit()
        selected = before[source]["entries"][0]["id"]
        edit = self.preview_edit([
            {"action": "rename", "crate_id": target, "base_revision": 1, "name": "Renamed"},
            {"action": "move", "source_id": source, "source_revision": 1, "target_id": target,
             "target_revision": 1, "entry_ids": [selected]}])
        approved = self.action(studio_organize.edit_approve, edit_id=edit["edit_id"])
        self.assertEqual(self.action(studio_organize.edit_approve, edit_id=edit["edit_id"]), approved)
        self.assertEqual(len(studio_organize._snapshot(self.db, source)["entries"]), 2)
        self.assertEqual(studio_organize._snapshot(self.db, target)["name"], "Renamed")
        self.db.commit()
        with self.connect() as db:
            undone = studio_organize.edit_undo(db, {"dataset_id": self.dataset, "request_id": "undo",
                "edit_id": edit["edit_id"]}, self.dataset)
        self.assertEqual(self.action(studio_organize.edit_undo, request_id="undo", edit_id=edit["edit_id"]), undone)
        for key in before:
            self.assertEqual(studio_organize._snapshot(self.db, key), {**before[key], "revision": 3})
        self.assertTrue(studio_organize.edit_history(self.db)[0]["undo"])

    def test_edit_merge_retains_occurrences_reorder_remove_and_undo(self):
        source, target = self.create_edit_crates()
        edit = self.preview_edit([{"action": "merge", "source_id": source, "source_revision": 1,
                                  "target_id": target, "target_revision": 1, "mode": "copy"}])
        original = edit["before"][target]["entries"]
        self.action(studio_organize.edit_approve, edit_id=edit["edit_id"])
        merged = studio_organize._snapshot(self.db, target)
        self.assertEqual(len(merged["entries"]), 4)
        self.assertEqual(len({entry["id"] for entry in merged["entries"]}), 4)
        self.assertEqual(sum(entry["song_id"] == original[0]["song_id"] for entry in merged["entries"]), 2)
        ids = [entry["id"] for entry in merged["entries"]]
        self.db.commit()
        change = self.preview_edit([
            {"action": "reorder", "crate_id": target, "base_revision": 2, "entry_ids": ids[::-1]},
            {"action": "remove", "crate_id": target, "base_revision": 2, "entry_ids": [ids[0]]}])
        self.action(studio_organize.edit_approve, request_id="second", edit_id=change["edit_id"])
        self.assertEqual([entry["id"] for entry in studio_organize._snapshot(self.db, target)["entries"]], ids[:0:-1])
        self.db.commit()
        with self.assertRaises(catalog.Conflict):
            self.action(studio_organize.edit_undo, request_id="undo-old", edit_id=edit["edit_id"])
        self.action(studio_organize.edit_undo, request_id="undo-second", edit_id=change["edit_id"])
        self.assertEqual(studio_organize._snapshot(self.db, target)["entries"], merged["entries"])

    def test_edit_stale_second_crate_rolls_back_every_change(self):
        source, target = self.create_edit_crates()
        edit = self.preview_edit([{"action": "merge", "source_id": source, "source_revision": 1,
                                  "target_id": target, "target_revision": 1, "mode": "move"}])
        before = studio_organize._snapshot(self.db, source)
        with self.db:
            self.db.execute("UPDATE personal_crates SET name='Newer',revision=revision+1 WHERE id=?", (target,))
        with self.assertRaises(catalog.Conflict):
            self.action(studio_organize.edit_approve, edit_id=edit["edit_id"])
        self.assertEqual(studio_organize._snapshot(self.db, source), before)
        self.assertIsNone(studio_organize.edit_history(self.db)[0]["approval"])

    def test_edit_failed_second_save_rolls_back_first_write_and_receipts(self):
        source, target = self.create_edit_crates()
        edit = self.preview_edit([{"action": "merge", "source_id": source, "source_revision": 1,
                                  "target_id": target, "target_revision": 1, "mode": "move"}])
        save = personal_crates.save
        def fail_second(db, body, dataset, **kwargs):
            if body["crate_id"] == target:
                raise ValueError("Synthetic save failure")
            return save(db, body, dataset, **kwargs)
        with patch.object(personal_crates, "save", side_effect=fail_second):
            with self.assertRaisesRegex(ValueError, "Synthetic"):
                self.action(studio_organize.edit_approve, edit_id=edit["edit_id"])
        self.assertEqual(studio_organize._snapshot(self.db, source), edit["before"][source])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM personal_crate_receipts WHERE request_id LIKE 'edit:%'").fetchone()[0], 0)

    def test_edit_preview_expiry_tamper_and_invalid_occurrences_fail_closed(self):
        source, target = self.create_edit_crates()
        for operation in (
            {"action": "reorder", "crate_id": source, "base_revision": 1, "entry_ids": ["missing"]},
            {"action": "move", "source_id": source, "source_revision": 1, "target_id": source,
             "target_revision": 1, "entry_ids": ["missing"]},
            {"action": "rename", "crate_id": source, "base_revision": 1, "name": "Source"},
        ):
            with self.assertRaises(ValueError):
                self.preview_edit([operation])
        edit = self.preview_edit([{"action": "rename", "crate_id": source, "base_revision": 1, "name": "New"}])
        with patch.object(studio_organize.time, "time", return_value=edit["expires_at"] + 1):
            with self.assertRaisesRegex(catalog.Conflict, "expired"):
                self.action(studio_organize.edit_approve, edit_id=edit["edit_id"])
        with self.db:
            self.db.execute("UPDATE studio_edit_batches SET document_json=? WHERE id=?",
                            (json.dumps(edit["before"]), edit["edit_id"]))
        with self.assertRaises(catalog.Conflict):
            self.action(studio_organize.edit_approve, edit_id=edit["edit_id"])

    def test_expected_edit_memberships_are_bound_to_the_reviewed_journal(self):
        source, target = self.create_edit_crates()
        edit = self.preview_edit([{"action": "rename", "crate_id": source, "base_revision": 1, "name": "Reviewed"}])
        crate = edit["after"][source]
        expected = [{"crate_id": source, "base_revision": 1, "revision": 2, "name": crate["name"],
                     "entries": crate["entries"]}]
        with self.assertRaises(catalog.Conflict):
            self.action(studio_organize.edit_approve, edit_id=edit["edit_id"],
                        expected_crates=[{**expected[0], "name": "Wrong"}])
        receipt = self.action(studio_organize.edit_approve, edit_id=edit["edit_id"], expected_crates=expected)
        self.assertEqual(receipt["crates"], expected)

    def test_existing_crate_edit_checks_exact_500_membership_boundary(self):
        source, target = self.create_edit_crates()
        entry = studio_organize._snapshot(self.db, source)["entries"][0]
        with self.db:
            personal_crates.save(self.db, {"dataset_id": self.dataset, "request_id": "expand",
                "crate_id": source, "base_revision": 1, "name": "Source", "entries": [
                    {**entry, "id": f"occurrence-{index}"} for index in range(500)]}, self.dataset)
        edit = self.preview_edit([{"action": "rename", "crate_id": source, "base_revision": 2, "name": "Exactly 500"}])
        self.assertEqual(len(edit["after"][source]["entries"]), 500)
        with self.db:
            personal_crates.save(self.db, {"dataset_id": self.dataset, "request_id": "expand-more",
                "crate_id": source, "base_revision": 2, "name": "Source", "entries": [
                    {**entry, "id": f"occurrence-{index}"} for index in range(501)]}, self.dataset)
        with self.assertRaisesRegex(ValueError, "500"):
            self.preview_edit([{"action": "rename", "crate_id": source, "base_revision": 3, "name": "Too many"}])

    def test_bulk_source_change_rolls_back_first_crate_and_all_receipts(self):
        with self.db:
            batch = studio_organize.preview(self.db, {"dataset_id": self.dataset, "crates": [
                {"name": "First", "rule": {"q": "One"}}, {"name": "Second", "rule": {"q": "Two"}}
            ]}, self.dataset)
        (self.music / "Two.wav").write_bytes(b"changed")
        with self.assertRaises(catalog.Conflict):
            self.action(studio_organize.approve, batch_id=batch["batch_id"])
        self.assertEqual(personal_crates.listing(self.db), [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_receipts").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM catalog_snapshots").fetchone()[0], 0)

    def test_batch_empty_rule_does_not_leave_partial_previews(self):
        with self.assertRaises(ValueError):
            with self.db:
                studio_organize.preview(self.db, {"dataset_id": self.dataset, "crates": [
                    {"name": "First", "rule": {}}, {"name": "Empty", "rule": {"q": "nonexistent"}}
                ]}, self.dataset)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_proposals").fetchone()[0], 0)

    def test_reversible_exclusion_keeps_sources_crates_and_receipts(self):
        file_id = self.ids["One"]
        record = next(track for track in self.search()["tracks"] if track["id"] == file_id)
        old = self.preview()
        original = (self.music / "One.wav").read_bytes()
        body = {"file_id": file_id, "content_hash": record["content_hash"], "base_revision": 0,
                "excluded": True, "reason": "Manual duplicate organization"}
        self.action(studio_ingest.exclude, **body)
        self.assertNotIn(file_id, [track["id"] for track in self.search()["tracks"]])
        with self.assertRaises(catalog.Conflict):
            self.approve(old)
        self.action(studio_ingest.exclude, **(body | {"request_id": "restore", "base_revision": 1, "excluded": False}))
        self.assertIn(file_id, [track["id"] for track in self.search()["tracks"]])
        self.assertEqual((self.music / "One.wav").read_bytes(), original)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_receipts").fetchone()[0], 2)

    def test_feedback_is_explicit_opt_in_not_a_hard_rule(self):
        before = [track["id"] for track in self.search()["tracks"]]
        self.action(studio_assistant.feedback, file_id=self.ids["Two"], base_revision=0, rating=1, note="My preference")
        self.assertEqual([track["id"] for track in self.search()["tracks"]], before)
        self.assertEqual(self.search({"sort": "preference"})["tracks"][0]["id"], self.ids["Two"])
        from setlist.planning import plan_set
        records = self.search()["tracks"]
        policy = {"target_minutes": 3, "tolerance_minutes": 0, "max_tracks": 1, "use_feedback": True}
        result = plan_set(records, policy)
        self.assertEqual(result["alternatives"][0]["tracks"][0]["id"], self.ids["Two"])
        policy["exclude"] = [self.ids["Two"]]
        self.assertNotIn(self.ids["Two"], [alt["tracks"][0]["id"] for alt in plan_set(records, policy)["alternatives"]])

    def chat_body(self):
        return {"conversation_id": "conversation", "base_revision": 0, "message": "Find clean dinner tracks",
                "rule": {}, "share_evidence": True}

    def draft(self):
        return {"action": "search", "name": "Dinner", "rule": {"clean_only": True},
                "policy": None, "crates": [], "citations": [self.ids["One"]], "unsupported": []}

    def test_chat_multiturn_receipt_grounded_citations_and_no_paths(self):
        captured = []
        def translate(message, schema, validator, instruction, *, model=None):
            if set(schema["properties"]) == {"action"}:
                return {"intent": validator({"action": "search"})}
            captured.append(instruction)
            return {"intent": validator(self.draft() if "action" in schema["properties"] else {
                "rule": {"bpm_max": 100}, "unsupported": []})}
        with patch.object(studio_assistant.studio_ai, "_interpret", side_effect=translate) as model:
            first = self.action(studio_assistant.chat, **self.chat_body())
            repeated = self.action(studio_assistant.chat, **self.chat_body())
            self.assertEqual(first, repeated)
            self.assertEqual(model.call_count, 2)
            second = self.action(studio_assistant.chat, **(self.chat_body() | {
                "request_id": "second", "base_revision": 1, "message": "Now make it slower"}))
        self.assertEqual(second["revision"], 2)
        self.assertEqual(first["turn"]["facts"]["eligible_count"], 3)
        self.assertNotIn(str(self.music), captured[0])
        self.assertNotIn("content_hash", captured[0])
        self.assertIn("Prior planning state only", captured[1])
        self.assertNotIn("Find clean dinner tracks", captured[1])
        self.assertNotIn("Optional untrusted indexed examples", captured[1])
        self.assertEqual(second["turn"]["draft"]["rule"],
                         studio_service.validate_rule({"clean_only": True, "bpm_max": 100}))
        self.assertEqual(personal_crates.listing(self.db), [])
        with self.connect() as db:
            self.assertEqual(studio_assistant.conversation(db, "conversation")["revision"], 2)

    def test_chat_rejects_invented_citations_and_unconsented_retrieval(self):
        for share, citation in ((True, "invented"), (False, self.ids["One"])):
            with patch.object(studio_assistant.studio_ai, "_interpret", return_value={
                "intent": self.draft() | {"citations": [citation]}}):
                with self.assertRaisesRegex(ValueError, "outside"):
                    self.action(studio_assistant.chat, **(self.chat_body() | {"share_evidence": share}))
        self.assertEqual(studio_assistant.conversation(self.db, "conversation")["revision"], 0)

    def test_release_year_guard_precedes_every_chat_tool_and_preserves_receipts(self):
        from web import studio_ai
        with patch.object(studio_ai, "_interpret", return_value={"intent": self.draft()}) as model, \
                patch.object(studio_assistant.studio_service, "search") as search:
            for mode in ("auto", "search", "build", "plan", "organize"):
                with self.subTest(mode=mode), self.assertRaises(studio_ai.UnsupportedRequest):
                    self.action(studio_assistant.chat, **(self.chat_body() | {
                        "action": mode, "message": "Plan 18 minutes of House released in 2007."}))
            model.assert_not_called()
            search.assert_not_called()
        self.assertEqual(studio_assistant.conversation(self.db, "conversation")["revision"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_receipts").fetchone()[0], 0)
        with patch.object(studio_ai, "_interpret", return_value={"intent": self.draft()}):
            first = self.action(studio_assistant.chat, **(self.chat_body() | {"action": "search"}))
        with patch.object(studio_ai, "_interpret") as model:
            self.assertEqual(self.action(studio_assistant.chat, **(self.chat_body() | {"action": "search"})), first)
            with self.assertRaises(studio_ai.UnsupportedRequest):
                self.action(studio_assistant.chat, **(self.chat_body() | {
                    "request_id": "year-followup", "base_revision": 1, "message": "Now only songs released after 1999."}))
            model.assert_not_called()
        self.assertEqual(studio_assistant.conversation(self.db, "conversation")["revision"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_receipts").fetchone()[0], 1)

    def test_patch_only_turns_retain_filters_and_reject_extra_keys_without_saving(self):
        responses = iter([
            self.draft() | {"action": "build", "rule": {
                "genre": "House", "clean_only": True, "bpm_min": 113, "bpm_max": 127, "limit": 23}},
            {"rule": {"limit": 11}, "unsupported": []},
            {"clear_rule_fields": ["bpm_max"], "unsupported": []},
            {"rule": {"clean_only": False}, "unsupported": []},
            {"rule": {}, "unsupported": [], "action": "delete"},
        ])
        schemas = []
        def translate(message, schema, validator, instruction, **kwargs):
            schemas.append(schema)
            if len(schemas) > 1:
                self.assertNotIn('"bpm_min":113', instruction)
                self.assertNotIn('"genre":"House"', instruction)
                self.assertNotIn("indexed examples", instruction)
            return {"intent": validator(next(responses))}
        with patch.object(studio_assistant.studio_ai, "_interpret", side_effect=translate):
            for index in range(4):
                result = self.action(studio_assistant.chat, **(self.chat_body() | {
                    "action": "build", "request_id": f"patch-{index}", "base_revision": index,
                    "message": f"Turn {index}"}))
            with self.assertRaises(ValueError):
                self.action(studio_assistant.chat, **(self.chat_body() | {
                    "request_id": "bad-patch", "base_revision": 4, "action": "build"}))
        self.assertNotIn("action", schemas[1]["properties"])
        self.assertEqual(result["turn"]["draft"]["rule"], studio_service.validate_rule({
            "genre": "House", "clean_only": False, "bpm_min": 113, "limit": 11}))
        self.assertEqual(studio_assistant.conversation(self.db, "conversation")["revision"], 4)

    def test_model_citations_are_bounded_to_supplied_examples_not_full_receipt(self):
        found = self.search()
        found["tracks"].append(found["tracks"][0] | {"id": "fourth-evidence-record"})
        found["total"] = 4
        def translate(message, schema, validator, instruction, **kwargs):
            self.assertNotIn("fourth-evidence-record", instruction)
            return {"intent": validator(self.draft() | {"citations": ["fourth-evidence-record"]})}
        with patch.object(studio_assistant.studio_service, "search", return_value=found), \
                patch.object(studio_assistant.studio_ai, "_interpret", side_effect=translate):
            with self.assertRaisesRegex(ValueError, "outside the evidence supplied"):
                self.action(studio_assistant.chat, **(self.chat_body() | {"action": "search"}))
        self.assertEqual(studio_assistant.conversation(self.db, "conversation")["revision"], 0)

    def test_semantic_evaluator_rejects_invented_planning_constraints(self):
        from scripts import evaluate_studio_ai
        from web import studio_ai
        def intent(rule, policy):
            return {"intent": studio_ai.validate_plan_intent({
                "action": "plan", "name": "Evaluation", "rule": rule, "policy": policy, "unsupported": []})}
        outputs = [
            intent({"clean_only": True}, {"target_minutes": 30}),
            intent({"clean_only": True}, {"target_minutes": 30, "phases": [
                {"phase": "cocktail", "target_minutes": 10, "tolerance_minutes": 1},
                {"phase": "open_dancing", "target_minutes": 20, "tolerance_minutes": 1}]}),
            intent({"clean_only": True, "genre": "House"}, {
                "target_minutes": 24, "tolerance_minutes": 0, "max_tracks": 8, "bpm_min": 120}),
        ]
        @contextmanager
        def response(*args, **kwargs):
            yield io.BytesIO(b'{"license":"fixture only"}')
        with patch.object(studio_ai, "interpret_plan", side_effect=outputs), \
                patch.object(evaluate_studio_ai.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = response
            report = evaluate_studio_ai.evaluate("fixture", {
                "simple-duration", "ordered-phase-budgets", "unseen-plan-duration-clean-genre"})
        self.assertEqual([result["passed"] for result in report["results"]], [True, False, False])
        self.assertEqual((report["passed"], report["total"]), (1, 3))

    def test_evaluator_attributes_release_filter_veto_to_application_not_model(self):
        from scripts import evaluate_studio_ai
        from web import studio_ai
        @contextmanager
        def response(*args, **kwargs):
            yield io.BytesIO(b'{"license":"fixture only"}')
        with patch.object(evaluate_studio_ai.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = response
            report = evaluate_studio_ai.evaluate("fixture", {"heldout-plan-unsupported-year"})
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["results"][0]["rejection_source"], "application-capability-check")
        self.assertIn("No AI request was sent", report["results"][0]["error"])
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_planning_patch_schema_and_context_preserve_nondefault_budgets(self):
        first = self.draft() | {"action": "plan", "policy": {
            "target_minutes": 30, "tolerance_minutes": 0, "max_tracks": 8,
            "phases": [{"phase": "cocktail", "target_minutes": 10, "tolerance_minutes": 0},
                       {"phase": "dinner", "target_minutes": 20, "tolerance_minutes": 0}]}}
        def translate(message, schema, validator, instruction, **kwargs):
            self.assertNotIn("required", schema["properties"]["policy"])
            self.assertNotIn("must_include", instruction)
            self.assertNotIn("pinned", instruction)
            self.assertIn('"target_minutes":30', instruction)
            return {"intent": validator({"policy": {"energy_target": 0}, "unsupported": []})}
        from web import studio_ai
        with patch.object(studio_assistant.studio_ai, "interpret_plan", return_value={
                "intent": studio_ai.validate_plan_intent({
                    key: item for key, item in first.items() if key in studio_ai.PLAN_SCHEMA["properties"]})}), \
                patch.object(studio_assistant.studio_ai, "_interpret", side_effect=translate):
            self.action(studio_assistant.chat, **(self.chat_body() | {"action": "plan", "message": "First"}))
            result = self.action(studio_assistant.chat, **(self.chat_body() | {
                "action": "plan", "message": "Second", "base_revision": 1, "request_id": "plan-patch"}))
        self.assertEqual(result["turn"]["draft"]["policy"]["energy_target"], 0)
        self.assertEqual(result["turn"]["draft"]["policy"]["tolerance_minutes"], 0)
        self.assertEqual(result["turn"]["draft"]["policy"]["max_tracks"], 8)
        self.assertEqual([phase["target_minutes"] for phase in result["turn"]["draft"]["policy"]["phases"]], [10, 20])

    def test_new_conversational_plan_uses_two_shared_extractors_then_one_patch(self):
        captured = []
        def translate(message, schema, validator, instruction, **kwargs):
            captured.append((schema, instruction, kwargs))
            self.assertNotIn("indexed examples", instruction)
            self.assertNotIn(str(self.music), instruction)
            if "target_minutes" in schema["properties"]:
                self.assertFalse(kwargs["include_schema"])
                return {"intent": validator({"target_minutes": 18, "phases": [],
                                            "max_tracks": 6, "tolerance_minutes": 0})}
            if "constraints" in schema["properties"]:
                self.assertFalse(kwargs["include_schema"])
                return {"intent": validator({"constraints": [
                    {"field": "clean_only", "value": True, "quote": "clean"},
                    {"field": "genre", "value": "House", "quote": "House"}], "unsupported": []}),
                    "provider": "fixture", "note": "Fixture only"}
            return {"intent": validator({"policy": {"target_minutes": 24}, "unsupported": []})}
        with patch.object(studio_assistant.studio_ai, "_interpret", side_effect=translate):
            body = self.chat_body() | {"action": "plan", "message": "18 minute clean House set, 6 songs, tolerance 0"}
            first = self.action(studio_assistant.chat, **body)
            self.assertEqual(self.action(studio_assistant.chat, **body), first)
            self.assertEqual(len(captured), 2)
            second = self.action(studio_assistant.chat, **(body | {
                "message": "Make it 24 minutes", "request_id": "followup-plan", "base_revision": 1}))
        self.assertEqual(len(captured), 3)
        self.assertEqual(first["turn"]["draft"]["rule"],
                         studio_service.validate_rule({"clean_only": True, "genre": "House"}))
        self.assertEqual(len(first["turn"]["constraint_evidence"]), 2)
        self.assertEqual(first["turn"]["draft"]["citations"], [])
        self.assertEqual(len(first["turn"]["retrieved"]), 3)
        self.assertEqual(second["turn"]["draft"]["policy"]["target_minutes"], 24)
        self.assertEqual(second["turn"]["draft"]["policy"]["tolerance_minutes"], 0)
        self.assertEqual(second["turn"]["draft"]["policy"]["max_tracks"], 6)
        self.assertEqual(second["turn"]["draft"]["rule"], first["turn"]["draft"]["rule"])
        self.assertEqual(personal_crates.listing(self.db), [])

    def test_failed_second_plan_extraction_saves_neither_turn_nor_receipt(self):
        def translate(message, schema, validator, instruction, **kwargs):
            if "target_minutes" in schema["properties"]:
                return {"intent": validator({"target_minutes": 18, "phases": []})}
            return {"intent": validator({"constraints": [
                {"field": "genre", "value": "Techno", "quote": "Techno"}], "unsupported": []})}
        with patch.object(studio_assistant.studio_ai, "_interpret", side_effect=translate):
            with self.assertRaisesRegex(ValueError, "quote is not present"):
                self.action(studio_assistant.chat, **(self.chat_body() | {
                    "action": "plan", "message": "18 minute dinner set"}))
        self.assertEqual(studio_assistant.conversation(self.db, "conversation")["revision"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_receipts").fetchone()[0], 0)

    def test_persistent_model_configuration_revision_retry_and_cloud_rejection(self):
        receipt = self.action(studio_assistant.configure_model, model="qwen2.5:7b", base_revision=0)
        self.assertEqual(self.action(studio_assistant.configure_model, model="qwen2.5:7b", base_revision=0), receipt)
        with self.connect() as db:
            self.assertEqual(studio_assistant.model_settings(db)["model"], "qwen2.5:7b")
        for model in ("remote:cloud", "remote-cloud", "has spaces"):
            with self.assertRaises(ValueError):
                self.action(studio_assistant.configure_model, request_id="other", model=model, base_revision=1)

    def test_chat_releases_writer_during_inference_and_rejects_changed_evidence(self):
        def translate(*args, **kwargs):
            with self.connect() as other:
                studio_assistant.feedback(other, {"request_id": "during-model", "dataset_id": self.dataset,
                    "file_id": self.ids["One"], "rating": 1, "note": "", "base_revision": 0}, self.dataset)
            return {"intent": self.draft()}
        with patch.object(studio_assistant.studio_ai, "_interpret", side_effect=translate):
            with self.assertRaisesRegex(catalog.Conflict, "Library evidence changed"):
                self.action(studio_assistant.chat, **self.chat_body())
        self.assertEqual(studio_assistant.conversation(self.db, "conversation")["revision"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM studio_receipts").fetchone()[0], 1)

    def test_chat_concurrent_request_collision_does_not_accept_another_body(self):
        def translate(*args, **kwargs):
            with self.connect() as other:
                studio_assistant.feedback(other, {"request_id": "request", "dataset_id": self.dataset,
                    "file_id": self.ids["One"], "rating": 1, "note": "", "base_revision": 0}, self.dataset)
            return {"intent": self.draft()}
        with patch.object(studio_assistant.studio_ai, "_interpret", side_effect=translate):
            with self.assertRaisesRegex(catalog.Conflict, "reused"):
                self.action(studio_assistant.chat, **self.chat_body())

    def test_restoring_exclusion_after_changed_bytes_uses_current_evidence(self):
        track = self.search()["tracks"][0]
        self.action(studio_ingest.exclude, file_id=track["id"], content_hash=track["content_hash"],
                    base_revision=0, excluded=True, reason="Review")
        with self.db:
            self.db.execute("UPDATE library_files SET content_hash=? WHERE id=?", ("c" * 64, track["id"]))
        item = studio_ingest.state(self.db)["exclusions"][0]
        self.assertEqual(item["current_content_hash"], "c" * 64)
        self.db.commit()
        self.action(studio_ingest.exclude, request_id="restore", file_id=track["id"],
                    content_hash=item["current_content_hash"], base_revision=1, excluded=False, reason="Restore")

    def test_organize_draft_validation_and_unsupported_fail_closed(self):
        draft = self.draft() | {"action": "organize", "crates": [{"name": "Clean", "rule": {"clean_only": True}}]}
        self.assertEqual(studio_assistant._validate_draft(draft)["action"], "organize")
        for change in ({"crates": []}, {"unsupported": ["Delete files"]},
                       {"crates": [{"name": "Invented", "rule": {"scope_ids": ["x"]}}]}):
            with self.assertRaises(ValueError):
                studio_assistant._validate_draft(draft | change)

    def test_refinement_inherits_unmentioned_fields_and_explicitly_clears_only_selected_bounds(self):
        previous = studio_assistant._validate_draft(self.draft() | {"action": "build", "rule": {
            "genre": "House", "clean_only": True, "bpm_min": 118, "bpm_max": 124, "limit": 20}})
        patch = self.draft() | {"action": "build", "rule": {"limit": 10, "genre": None, "clean_only": None},
                               "citations": []}
        refined = studio_assistant._validate_draft(patch, previous)
        self.assertEqual(refined["rule"], previous["rule"] | {"limit": 10})
        cleared = studio_assistant._validate_draft(patch | {"clear_rule_fields": ["bpm_max"]}, previous)
        self.assertNotIn("bpm_max", cleared["rule"])
        self.assertEqual(cleared["rule"]["bpm_min"], 118)
        with self.assertRaises(ValueError):
            studio_assistant._validate_draft(patch | {"clear_rule_fields": ["limit"]}, previous)

    def test_native_json_preserves_false_and_zero_and_rejects_ambiguous_types(self):
        from web import studio_ai
        intent = studio_ai.validate_intent({"action": "search", "name": "Test", "unsupported": [],
            "rule": {"clean_only": False, "energy_min": 0, "bpm_max": None}})
        self.assertIs(intent["rule"]["clean_only"], False)
        self.assertEqual(intent["rule"]["energy_min"], 0)
        self.assertNotIn("bpm_max", intent["rule"])
        for rule in ({"clean_only": "false"}, {"energy_min": "0"},
                     [{"field": "energy_min", "value": "0"}]):
            with self.assertRaises(ValueError):
                studio_ai.validate_intent({"action": "search", "name": "Test", "unsupported": [], "rule": rule})

    def test_planning_followup_retains_nondefault_budget_phases_and_hard_bounds(self):
        old = studio_assistant._validate_draft(self.draft() | {"action": "plan", "rule": {"clean_only": True},
            "policy": {"target_minutes": 30, "tolerance_minutes": 0, "max_tracks": 6, "bpm_max": 128,
                "phases": [{"phase": "cocktail", "target_minutes": 10, "tolerance_minutes": 0},
                           {"phase": "open_dancing", "target_minutes": 20, "tolerance_minutes": 0}]}})
        patch = {"action": "plan", "name": "Refined", "rule": {}, "policy": {
            "energy_target": 0.7}, "crates": [], "citations": [], "unsupported": []}
        refined = studio_assistant._validate_draft(patch, old)
        for key in ("tolerance_minutes", "max_tracks", "bpm_max", "phases"):
            self.assertEqual(refined["policy"][key], old["policy"][key])
        self.assertEqual(refined["policy"]["energy_target"], 0.7)
