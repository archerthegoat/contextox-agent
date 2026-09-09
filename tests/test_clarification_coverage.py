"""Coverage is structural, not proof of the semantic quality of a question."""
import json
import unittest

import test_agent as fixtures
import test_g1_run as g1
from test_g1_run import call, update
from test_model_tools import unknown_field
from contextox import agent
from contextox.model_tools import CandidateRejected, ClarificationInput, FieldInput, HandleDenied, RunReferences
from contextox.models import DefinitionDraft, DefinitionField, canonical_sha256


def question(handles):
    return {"covers_obligation_handles": handles,
            "question": "Provide the missing policies for the listed dimensions, or identify the next evidence owner.",
            "why_needed": "Definitions cannot yet be finalized.", "expected_answer_type": "text",
            "suggested_owner_role": "Business owner", "related_definition_paths": [],
            "evidence_requested": ["Approved policy and counterexamples"], "examples_or_options": [],
            "blocking_impact": "blocking", "evidence_handles": []}


class CoverageInputTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixtures._context_snapshot()
        self.refs = RunReferences(self.snapshot, [])
        fields = [DefinitionField.model_validate(self.refs.field(FieldInput.model_validate(unknown_field(key))))
                  for key in ("a", "b")]
        payload = {"fields": [f.model_dump(mode="json") for f in fields], "relationships": [], "unresolved_items": ["Policy needed", "Policy needed"]}
        self.draft = DefinitionDraft(workspace_id=self.snapshot.mission.workspace_id,
            mission_id=self.snapshot.mission.mission_id, draft_id=fixtures._id(9), version=1,
            sha256=canonical_sha256(payload), status="draft", semantic_approval="pending", **payload)
        self.public = self.refs.public(self.draft)
        self.handles = [o["obligation_handle"] for o in self.public["clarification_obligations"]]

    def normalize(self, questions):
        return self.refs.clarification_arguments(ClarificationInput(
            draft_token=self.refs.draft_token, questions=questions))

    def test_complete_grouping_preserves_order_and_domain_shape(self):
        self.assertEqual(len(self.handles), 14)  # Equal wording is not semantic deduplication.
        self.assertEqual(self.refs.public(self.draft), self.public)
        extra = question([])
        extra["question"] = "Additional question"
        result = self.normalize([question(self.handles), extra])
        self.assertEqual(result["questions"][1]["question"], "Additional question")
        self.assertNotIn("covers_obligation_handles", result["questions"][0])
        self.assertEqual(len(result["questions"][0]["related_definition_paths"]), 14)
        self.assertIn("fields.a.null_handling", result["questions"][0]["related_definition_paths"])
        self.assertEqual(result["draft_sha256"], self.draft.sha256)

    def test_omission_reports_only_bounded_opaque_handles(self):
        with self.assertRaises(CandidateRejected) as caught:
            self.normalize([question([])])
        self.assertEqual(caught.exception.missing_handles, self.handles[:5])
        self.assertNotIn("Policy", str(caught.exception))
        with self.assertRaises(CandidateRejected) as caught:
            self.normalize([question(self.handles[:-1])])
        self.assertEqual(caught.exception.missing_handles, self.handles[-1:])

    def test_extra_paths_must_resolve_in_exact_current_draft(self):
        for path in ("fields.nonexistent.rule", "fields.a.nonexistent", "Fields.a.rule",
                     "unresolved_items.2", "relationships.nonexistent.join_rule",
                     "fields.0.unknowns.99"):
            with self.subTest(path=path):
                q = question(self.handles)
                q["related_definition_paths"] = [path]
                with self.assertRaises(CandidateRejected) as caught:
                    self.normalize([q])
                self.assertEqual(caught.exception.paths, ["questions.related_definition_paths"])
                self.assertNotIn(path, str(caught.exception))

    def test_known_property_and_object_links_remain_valid(self):
        q = question(self.handles)
        # name is known and has no clarification obligation.
        q["related_definition_paths"] = ["fields.a.name", "fields.b", "unresolved_items.0"]
        result = self.normalize([q])
        self.assertEqual(result["questions"][0]["related_definition_paths"][:3], q["related_definition_paths"])

    def test_unknown_dimensions_require_blocking_but_optional_questions_do_not(self):
        obligations = self.public["clarification_obligations"]
        self.assertEqual(sum(o["required_blocking_impact"] == "blocking" for o in obligations), 12)
        self.assertTrue(all(o["required_blocking_impact"] is None for o in obligations[-2:]))
        for obligation in obligations[:12]:
            with self.subTest(path=obligation["related_definition_paths"]):
                q = question([obligation["obligation_handle"]]); q["blocking_impact"] = "non_blocking"
                with self.assertRaises(CandidateRejected) as caught:
                    self.normalize([question(self.handles), q])
                self.assertEqual(caught.exception.paths, ["questions.blocking_impact"])
                q["covers_obligation_handles"] = []
                q["related_definition_paths"] = obligation["related_definition_paths"]
                with self.assertRaises(CandidateRejected):
                    self.normalize([question(self.handles), q])
        extra = question([]); extra["blocking_impact"] = "non_blocking"
        extra["related_definition_paths"] = ["fields.a.name"]
        result = self.normalize([question(self.handles), extra])
        self.assertEqual(result["questions"][1]["blocking_impact"], "non_blocking")

    def test_duplicate_stale_forged_and_foreign_handles(self):
        with self.assertRaises(CandidateRejected):
            self.normalize([question(self.handles + self.handles[:1])])
        for handle in ("forged", RunReferences(self.snapshot, []).public(self.draft)["clarification_obligations"][0]["obligation_handle"]):
            with self.assertRaises(HandleDenied):
                self.normalize([question(self.handles + [handle])])
        self.refs.public(self.draft.model_copy(update={"version": 2}))
        with self.assertRaises(CandidateRejected):
            self.normalize([question(self.handles)])

    def test_covered_questions_require_nonblank_handoff(self):
        for key, value in (("question", " "), ("why_needed", ""), ("suggested_owner_role", None),
                           ("evidence_requested", []), ("evidence_requested", [" "])):
            with self.subTest(key=key):
                q = question(self.handles); q[key] = value
                with self.assertRaises(CandidateRejected):
                    self.normalize([q])

    def test_relationship_unknowns_and_long_paths_remain_bounded(self):
        from contextox.models import RelationshipCandidate, SourceIdentity, TableKey, UnknownItem
        source = SourceIdentity(workspace_id=self.snapshot.mission.workspace_id,
            source_id=fixtures._id(20), revision_id=fixtures._id(21), sha256="a" * 64)
        table = TableKey(source_ref=source, table_id="table", columns=[])
        relationship = RelationshipCandidate(relationship_key="r" * 128, left=table, right=table,
            observed_cardinality="unknown", join_rule=None, grain_notes=None, evidence_status="unknown",
            source_refs=[], risks=[], unknowns=[UnknownItem(property_path="p" * 128, reason="Owner decision")])
        field = self.draft.fields[0].model_copy(update={"field_key": "f" * 128})
        self.refs.current_draft = self.draft.model_copy(update={"fields": [field], "relationships": [relationship]})
        obligations = self.refs.clarification_obligations()
        self.assertEqual(len(obligations), 9)
        self.assertIn("relationships.0.unknowns.0", [o["related_definition_paths"][0] for o in obligations])
        result = self.normalize([question([o["obligation_handle"] for o in obligations])])
        self.assertTrue(all(len(path) <= 128 for path in result["questions"][0]["related_definition_paths"]))

    def test_relationship_qualified_property_does_not_repeat_object_key(self):
        from contextox.models import RelationshipCandidate, SourceIdentity, TableKey, UnknownItem
        source = SourceIdentity(workspace_id=self.snapshot.mission.workspace_id,
            source_id=fixtures._id(20), revision_id=fixtures._id(21), sha256="a" * 64)
        table = TableKey(source_ref=source, table_id="table", columns=[])
        for property_path in ("rel.production_key_semantics", "production_key_semantics"):
            with self.subTest(property_path=property_path):
                relation = RelationshipCandidate(relationship_key="rel", left=table, right=table,
                    observed_cardinality="unknown", join_rule=None, grain_notes=None, evidence_status="unknown",
                    source_refs=[], risks=[], unknowns=[UnknownItem(property_path=property_path, reason="Not supplied")])
                self.refs.current_draft = self.draft.model_copy(update={"fields": [], "relationships": [relation], "unresolved_items": []})
                obligations = self.refs.clarification_obligations()
                result = self.normalize([question([o["obligation_handle"] for o in obligations])])
                self.assertEqual(result["questions"][0]["related_definition_paths"],
                                 ["relationships.rel.production_key_semantics"])
                self.assertEqual(relation.unknowns[0].property_path, property_path)
                q = question([o["obligation_handle"] for o in obligations])
                self.assertIsNone(obligations[0]["required_blocking_impact"])
                q["blocking_impact"] = "non_blocking"
                self.assertEqual(self.normalize([q])["questions"][0]["blocking_impact"], "non_blocking")
                q["related_definition_paths"] = ["relationships.rel.rel.production_key_semantics"]
                with self.assertRaises(CandidateRejected):
                    self.normalize([q])

    def test_exact_historical_pair_remains_supported_without_cross_product(self):
        old = ("d4f6eb2efe8878d07a06ee9d9eb0f60e81cde55882a81d92d164b645f213d3db",
               "acaf4fda820b343181fcb19d5efa739b75f54cfa8cb15529f1d3ced74c64657d")
        self.assertIn(old, agent.SUPPORTED_RUN_HASH_PAIRS)
        self.assertNotIn((old[0], agent.TOOL_SCHEMA_SHA256), agent.SUPPORTED_RUN_HASH_PAIRS)
        self.assertNotIn((agent.P0_RUN_SHA256, old[1]), agent.SUPPORTED_RUN_HASH_PAIRS)


class CoverageRunTests(unittest.TestCase):
    def execute(self, script, verify):
        g1.G1RunTests().execute(script, verify)

    def test_inconsistent_impact_rejects_then_corrects_without_changing_unknowns(self):
        def script(n, p, history):
            if n == 1:
                # The public request carries the general evidence contract, not evaluator answers.
                self.assertIn("A candidate label cannot qualify an embedded fact", history[0]["content"])
                self.assertIn("population, aggregation", history[0]["content"])
                return [update(p)]
            if n == 3:
                error = json.loads(history[-1]["content"])["error"]
                self.assertEqual(error["effect"], "none")
                self.assertEqual(error["paths"], ["questions.blocking_impact"])
                self.assertIn("definition finalization", error["expected_shape"])
                self.assertEqual(p["clarifications"], [])
            q = question([o["obligation_handle"] for o in p["draft"]["clarification_obligations"]])
            q["blocking_impact"] = "non_blocking" if n == 2 else "blocking"
            return [call(f"clarify-{n}", "create_clarification", {"draft_token": p["draft_token"], "questions": [q]})]
        def verify(r, calls, receipts):
            self.assertEqual(r.status, "waiting_for_human")
            self.assertEqual(receipts, [("update", 1), ("clarify-3", 2)])
            self.assertEqual(len(r.clarifications), 1)
            self.assertEqual(r.clarifications[0].questions[0].blocking_impact, "blocking")
            self.assertEqual(len(r.draft.fields[0].unknowns), 6)
            self.assertIsNone(r.draft.fields[0].null_handling)
            self.assertEqual(len(calls), 3)
        self.execute(script, verify)

    def test_repeated_impact_conflicts_stop_with_no_clarification(self):
        def script(n, p, h):
            if n == 1:
                return [update(p)]
            q = question([o["obligation_handle"] for o in p["draft"]["clarification_obligations"]])
            q["blocking_impact"] = "non_blocking"
            return [call(f"clarify-{n}", "create_clarification", {"draft_token": p["draft_token"], "questions": [q]})]
        def verify(r, calls, receipts):
            self.assertEqual(r.status, "failed")
            self.assertEqual(r.error_code, "tool_arguments_invalid")
            self.assertEqual(r.clarifications, [])
            self.assertIsNotNone(r.draft)
            self.assertEqual(receipts, [("update", 1)])
            self.assertEqual(len(calls), 4)
        self.execute(script, verify)

    def test_historical_unresolved_path_stays_readable_without_rewrite(self):
        from threading import Event
        from unittest.mock import patch
        with fixtures.PersistedRunTests().store_case() as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            class HistoricalProvider(fixtures.FakeProvider):
                def complete(self, messages, **kwargs):
                    draft = store.get_run_snapshot(ws, mission.mission_id, run.run_id).draft
                    if draft is None:
                        name = "update_definition_draft"
                        args = {"expected_version": 0, "expected_sha256": None,
                                "fields": [], "relationships": [], "unresolved_items": ["Owner needed"]}
                    else:
                        name = "create_clarification"
                        q = question([])
                        q.pop("covers_obligation_handles"); q.pop("evidence_handles")
                        q.update(source_refs=[], related_definition_paths=["fields.nonexistent.rule"])
                        args = {"draft_version": draft.version, "draft_sha256": draft.sha256, "questions": [q]}
                    self.completions = [fixtures._completion("Synthetic historical result", (
                        fixtures.ProviderToolCall(name, name, json.dumps(args)),))]
                    return super().complete(messages, **kwargs)
            with patch.object(agent, "get_provider", return_value=HistoricalProvider([])):
                fixtures.run_legacy_fixture_agent(store, ws, mission.mission_id, run.run_id, Event())
            before = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            self.assertEqual(before.status, "waiting_for_human")
            self.assertEqual(before.clarifications[0].questions[0].related_definition_paths, ["fields.nonexistent.rule"])
            reopened = fixtures.WorkspaceStore.open(store.data_dir)
            self.assertEqual(reopened.get_run_snapshot(ws, mission.mission_id, run.run_id), before)

    def test_invalid_path_has_no_write_then_corrected_request_persists(self):
        def script(n, p, history):
            if n == 1:
                return [update(p)]
            if n == 3:
                error = json.loads(history[-1]["content"])["error"]
                self.assertEqual(error["effect"], "none")
                self.assertEqual(error["paths"], ["questions.related_definition_paths"])
                self.assertIn("current draft", error["expected_shape"])
                self.assertEqual(p["clarifications"], [])
            q = question([o["obligation_handle"] for o in p["draft"]["clarification_obligations"]])
            q["related_definition_paths"] = ["fields.nonexistent.rule" if n == 2 else "fields.candidate.name"]
            return [call(f"clarify-{n}", "create_clarification", {"draft_token": p["draft_token"], "questions": [q]})]
        def verify(r, calls, receipts):
            self.assertEqual(r.status, "waiting_for_human")
            self.assertEqual(len(r.clarifications), 1)
            self.assertEqual(receipts, [("update", 1), ("clarify-3", 2)])
            self.assertIn("fields.candidate.name", r.clarifications[0].questions[0].related_definition_paths)
            self.assertNotIn("fields.nonexistent.rule", r.clarifications[0].questions[0].related_definition_paths)
            self.assertEqual(len(calls), 3)
        self.execute(script, verify)

    def test_old_pair_persisted_run_reopens_without_rewriting_receipts(self):
        pairs = (
            (agent.PRE_CONTEXT_CHECKPOINT_P0_RUN_SHA256, agent.TOOL_SCHEMA_SHA256),
            (agent.PRE_CITATION_INDEX_P0_RUN_SHA256, agent.TOOL_SCHEMA_SHA256),
            ("32a2a89ad05548171db0963bf8543a1b5c1df3e916798306f7867bc68ac5a3af", "902fb158bb36fbfdc7bc021db1739400a3ca6f4b2aa87df2dcca0437a29f8c4e"),
            ("d4f6eb2efe8878d07a06ee9d9eb0f60e81cde55882a81d92d164b645f213d3db", "acaf4fda820b343181fcb19d5efa739b75f54cfa8cb15529f1d3ced74c64657d"),
            ("fd4d113705de9c1bd504759f4d55454d88cf8d966287c9b41c34a83af923707a", "902fb158bb36fbfdc7bc021db1739400a3ca6f4b2aa87df2dcca0437a29f8c4e"),
        )
        for prompt, tools in pairs:
            with self.subTest(prompt=prompt):
                self.assertIn((prompt, tools), agent.SUPPORTED_RUN_HASH_PAIRS)
                self._assert_historical_pair_reopens(prompt, tools)

    def test_question_citations_remain_explicit_across_sources_and_restart(self):
        from threading import Event
        from unittest.mock import patch
        evidence = []

        def script(turn, packet, history):
            if turn == 1:
                self.assertIn("its own supporting evidence_handles", history[0]["content"])
                return [call(f"read-{i}", "read_source", {
                    "source_handle": source["source_handle"],
                    "locator": {"kind": "csv_rows", "row_start": 1,
                                "row_end": source["tables"][0]["row_count"], "column": None},
                }) for i, source in enumerate(packet["sources"])]
            if turn == 2:
                self.assertEqual([message["role"] for message in history], ["system", "user"])
                tool_evidence = [item["result"]["evidence_handle"]
                                 for item in packet["evidence_bundle"]]
                self.assertEqual(
                    [item["evidence_handle"] for item in packet["coverage"]],
                    tool_evidence,
                )
                self.assertEqual(
                    [item["source_name"] for item in packet["coverage"]],
                    [item["name"] for item in packet["sources"]],
                )
                self.assertTrue(all("text" not in item for item in packet["coverage"]))
                evidence.extend(tool_evidence)
                args = {"draft_token": packet["draft_token"],
                        "fields": [unknown_field("candidate")],
                        "relationships": [], "unresolved_items": []}
                args["fields"][0]["evidence_handles"] = evidence
                return [call("update", "update_definition_draft", args)]
            complete = question([o["obligation_handle"]
                                 for o in packet["draft"]["clarification_obligations"]])
            complete["evidence_handles"] = list(reversed(evidence))
            request = question([])
            request["question"] = "Please identify the owner to review this candidate."
            request["related_definition_paths"] = ["fields.candidate.name"]
            request["evidence_handles"] = evidence[:1]
            return [call("clarify", "create_clarification", {
                "draft_token": packet["draft_token"], "questions": [complete, request]})]

        with fixtures.PersistedRunTests().store_case(with_sources=True) as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            provider = g1.ScriptProvider(script)
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
            before = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            self.assertEqual(before.status, "waiting_for_human")
            field_refs = before.draft.fields[0].source_refs
            self.assertEqual(len(field_refs), 2)
            self.assertEqual(len({r.revision_id for r in field_refs}), 2)
            published = before.clarifications[0].questions
            self.assertEqual(published[0].source_refs, list(reversed(field_refs)))
            self.assertEqual(published[1].source_refs, field_refs[:1])
            self.assertEqual(len(before.terminal_receipt.source_refs), 2)
            reopened = fixtures.WorkspaceStore.open(store.data_dir)
            self.assertEqual(reopened.get_run_snapshot(ws, mission.mission_id, run.run_id), before)
            self.assertEqual(len(provider.calls), 3)

    def _assert_historical_pair_reopens(self, old_prompt, old_tools):
        from threading import Event
        from unittest.mock import patch
        with fixtures.PersistedRunTests().store_case() as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            provider = fixtures.FakeProvider([fixtures._completion("Synthetic historical reply", (
                fixtures.ProviderToolCall("finish", "finish_run", json.dumps({"outcome": "partial",
                    "reason": "Historical synthetic evidence", "source_refs": []})),))])
            with patch.object(agent, "P0_RUN_SHA256", old_prompt), patch.object(agent, "TOOL_SCHEMA_SHA256", old_tools), patch.object(agent, "get_provider", return_value=provider):
                fixtures.run_legacy_fixture_agent(store, ws, mission.mission_id, run.run_id, Event())
                before = store.get_run_snapshot(ws, mission.mission_id, run.run_id).model_dump(mode="json")
            self.assertEqual(before["status"], "partial")
            reopened = fixtures.WorkspaceStore.open(store.data_dir)
            self.assertEqual(reopened.get_run_snapshot(ws, mission.mission_id, run.run_id).model_dump(mode="json"), before)

    def test_missing_policy_rejects_without_terminal_write_then_corrects(self):
        def script(n, p, history):
            if n == 1:
                return [update(p)]
            handles = [o["obligation_handle"] for o in p["draft"]["clarification_obligations"]]
            if n == 3:
                error = json.loads(history[-1]["content"])["error"]
                self.assertEqual(error["effect"], "none")
                self.assertEqual(error["missing_obligation_handles"], [o["obligation_handle"]
                    for o in p["draft"]["clarification_obligations"]
                    if o["related_definition_paths"] == ["fields.candidate.null_handling"]])
                self.assertEqual(p["clarifications"], [])
                self.assertEqual(len(p["draft"]["fields"]), 1)
            return [call(f"clarify-{n}", "create_clarification", {"draft_token": p["draft_token"],
                "questions": [question([handle for handle, item in zip(handles, p["draft"]["clarification_obligations"])
                    if item["related_definition_paths"] != ["fields.candidate.null_handling"]] if n == 2 else handles)]})]
        def verify(r, calls, receipts):
            self.assertEqual(r.status, "waiting_for_human")
            self.assertEqual(len(r.clarifications), 1)
            self.assertEqual(receipts, [("update", 1), ("clarify-3", 2)])
            paths = r.clarifications[0].questions[0].related_definition_paths
            self.assertIn("fields.candidate.null_handling", paths)
            self.assertIn("unresolved_items.0", paths)
            self.assertEqual(len(calls), 3)
        self.execute(script, verify)

    def test_repeated_omissions_exhaust_existing_recovery_without_clarification(self):
        def script(n, p, h):
            if n == 1:
                return [update(p)]
            return [call(f"clarify-{n}", "create_clarification", {"draft_token": p["draft_token"],
                "questions": [question([])]})]
        def verify(r, calls, receipts):
            self.assertEqual(r.status, "failed")
            self.assertEqual(r.error_code, "tool_arguments_invalid")
            self.assertEqual(r.clarifications, [])
            self.assertIsNotNone(r.draft)
            self.assertEqual(receipts, [("update", 1)])
            self.assertEqual(len(calls), 4)
        self.execute(script, verify)
