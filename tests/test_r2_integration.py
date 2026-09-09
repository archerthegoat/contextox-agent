"""R1/R2 public integration seams; synthetic sources and Provider only."""
import json
import sqlite3
import unittest
from contextlib import closing
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import test_clarifications as fixtures
from test_g1_run import ScriptProvider, call
from contextox import agent, store as db
from contextox.models import UpdateDefinitionDraftCall
from contextox.model_tools import RunReferences


class R2IntegrationTests(unittest.TestCase):
    def case(self, with_sources=False):
        case = fixtures.ClarificationTests()
        case.with_sources = with_sources
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_approved_unknown_rejects_batch_then_new_question_keeps_old_blocker(self):
        case = self.case(with_sources=True)
        saved, _ = case.save()
        case.approve(saved.answer)
        (receipt, _), _ = case.send()

        def script(turn, packet, history):
            approved = packet["approved_answers"][0]
            self.assertEqual(approved["answer_sha256"], saved.answer.sha256)
            self.assertEqual(approved["items"][1]["disposition"], "unknown")
            if turn <= 2:
                semantics = {key: {"value": None, "unknown_reason": "Needs business definition"}
                             for key in fixtures.DIMS}
                semantics["rule"] = {"value": "30 days", "unknown_reason": None}
                if turn == 1:
                    semantics["null_handling"] = {"value": "drop missing rows", "unknown_reason": None}
                else:
                    self.assertIsNone(packet["draft"]["fields"][0]["semantics"]["rule"]["value"])
                    errors = [json.loads(m["content"])["error"] for m in history
                              if m["role"] == "tool"]
                    self.assertTrue(all(e["effect"] == "none" for e in errors))
                    self.assertTrue(any("approved_unknown_targets" in e["paths"] for e in errors))
                update = call(f"update-{turn}", "update_definition_draft", {
                    "draft_token": packet["draft_token"], "fields": [{
                        "field_key": "window", "name": "window", "semantics": semantics,
                        "source_column_handles": [], "evidence_status": "candidate", "evidence_handles": []}],
                    "relationships": [], "unresolved_items": []})
                return [update, call("rejected-list", "list_sources", {})] if turn == 1 else [update]
            obligations = packet["draft"]["clarification_obligations"]
            paths = {p for o in obligations for p in o["related_definition_paths"]}
            self.assertNotIn("fields.window.null_handling", paths)
            self.assertIn("fields.window.meaning", paths)
            # New questions still cover every unhandled definition gap.
            return [call("new-questions", "create_clarification", {
                "draft_token": packet["draft_token"], "questions": [{
                    "question": "Please define the remaining field dimensions.",
                    "why_needed": "The remaining definition cannot be finalized.",
                    "expected_answer_type": "text", "suggested_owner_role": "Business owner",
                    "related_definition_paths": [], "evidence_requested": ["Business definition"],
                    "examples_or_options": [], "blocking_impact": "blocking", "evidence_handles": [],
                    "covers_obligation_handles": [o["obligation_handle"] for o in obligations]}]})]

        provider = ScriptProvider(script)
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(case.store, case.ws, case.mid, receipt.run.run_id, Event())
        reopened = db.WorkspaceStore.open(case.store.data_dir)
        result = reopened.get_run_snapshot(case.ws, case.mid, receipt.run.run_id)
        self.assertEqual(result.status, "waiting_for_human", result.error_code)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(result.approved_answers[0].answer, saved.answer)
        self.assertEqual(result.draft.fields[0].rule, "30 days")
        self.assertIsNone(result.draft.fields[0].null_handling)
        with closing(sqlite3.connect(reopened.db_path)) as connection:
            tool_ids = connection.execute("SELECT call_id FROM tool_receipts WHERE run_id=? ORDER BY ordinal",
                                          (result.run_id,)).fetchall()
        self.assertEqual(tool_ids, [("update-2",), ("new-questions",)])
        impact = reopened.get_answer_impact(case.ws, case.mid, result.run_id)
        self.assertEqual(len(impact.remaining_blockers), 1)
        self.assertEqual(impact.remaining_blockers[0].clarification_id, case.q.clarification_id)
        self.assertNotIn("fields.window.null_handling", result.clarifications[0].questions[0].related_definition_paths)

    def test_relationship_unknown_aliases_preserved_without_allowing_guesses(self):
        case = self.case(with_sources=True)
        saved, _ = case.save()
        case.approve(saved.answer)
        (receipt, _), _ = case.send()
        case.store.mark_run_running(case.ws, case.mid, receipt.run.run_id)
        relation = {"relationship_key": "rel", "left": {"source_ref": case.sources[0], "table_id": "", "columns": ["id"]},
                    "right": {"source_ref": case.sources[1], "table_id": "", "columns": ["id"]},
                    "observed_cardinality": "unknown", "join_rule": None, "grain_notes": None,
                    "evidence_status": "unknown", "source_refs": [], "risks": [],
                    "unknowns": [{"property_path": "grain_notes", "reason": "Requires business grain"}]}

        def update(run_id, value):
            draft = case.store.get_run_snapshot(case.ws, case.mid, run_id).draft
            return case.store.execute_run_tool(case.ws, case.mid, run_id, UpdateDefinitionDraftCall(
                call_id=str(uuid4()), name="update_definition_draft", arguments={
                    "expected_version": draft.version, "expected_sha256": draft.sha256,
                    "fields": [], "relationships": [value], "unresolved_items": []}))

        update(receipt.run.run_id, relation)
        case.store.fail_run(case.ws, case.mid, receipt.run.run_id, "failed", "synthetic_checkpoint")
        payload = case.save_payload()
        from contextox.models import RelationshipAnswerTarget
        payload.items[1].targets = [RelationshipAnswerTarget(kind="relationship", key="rel", property="grain_notes")]
        saved, _ = case.save(payload)
        case.approve(saved.answer)
        (receipt, _), _ = case.send()
        case.store.mark_run_running(case.ws, case.mid, receipt.run.run_id)
        for path in ("grain_notes", "rel.grain_notes"):
            with self.subTest(valid_path=path):
                output = update(receipt.run.run_id, {**relation, "unknowns": [{"property_path": path, "reason": "Still unknown"}]}).output
                self.assertEqual(output.relationships[0].unknowns[0].property_path, path)
        for path, value in (("other.grain_notes", None), ("rel.grain_notes", "one row per customer")):
            with self.subTest(invalid_path=path, value=value):
                before = case.store.get_run_snapshot(case.ws, case.mid, receipt.run.run_id).draft
                with self.assertRaises(db.Path2StateError) as error:
                    update(receipt.run.run_id, {**relation, "grain_notes": value,
                           "unknowns": [{"property_path": path, "reason": "Still unknown"}]})
                self.assertEqual(error.exception.code, "approved_unknown_must_remain_unresolved")
                self.assertEqual(case.store.get_run_snapshot(case.ws, case.mid, receipt.run.run_id).draft, before)

    def test_unmapped_unknown_and_answered_but_unapplied_gaps_still_require_coverage(self):
        for bound in (False, True):
            with self.subTest(bound=bound):
                case = self.case()
                payload = case.save_payload()
                if not bound:
                    payload.items[1].targets = []
                saved, _ = case.save(payload)
                case.approve(saved.answer)
                (receipt, _), _ = case.send()
                snapshot = case.store.get_context_snapshot(case.ws, case.mid, receipt.run.run_id)
                refs = RunReferences(snapshot, [])
                paths = {p for obligation in refs.clarification_obligations()
                         for p in obligation["related_definition_paths"]}
                self.assertEqual("fields.window.null_handling" in paths, not bound)
                # An answered item is not proof that its candidate has been updated.
                self.assertIn("fields.window.rule", paths)
                self.assertEqual(len(snapshot.draft.fields[0].unknowns), 6)
