import json
import unittest
from threading import Event
from unittest.mock import patch
from pydantic import ValidationError

import test_agent as fixtures

from contextox import agent
from contextox.model_tools import ContextPlanV1, SemanticProposalV1
from contextox.models import SemanticApplicationInput, SourceIdentity, TaskMessageSendRequest
from contextox.provider import ProviderCompletion, ProviderUsage
from contextox.semantic_controller import (
    EMPTY_TOOL_SCHEMA_SHA256,
    P0_SEMANTIC_PROPOSAL_SHA256,
    P0_SEMANTIC_PROPOSAL,
    PRE_COMPACT_SEMANTIC_PROPOSAL_SHA256,
    SEMANTIC_CONTEXT_MAX_BYTES,
    SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS,
    SemanticProposalFailure,
    build_context_plan,
    normalize_semantic_proposal,
    request_semantic_proposal,
    semantic_messages,
)


class ProposalProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "kwargs": kwargs})
        return ProviderCompletion(
            completion_id="proposal-completion",
            content=self.content,
            reasoning_content="not persisted",
            tool_calls=(),
            finish_reason="stop",
            usage=ProviderUsage(input_tokens=12, output_tokens=8),
        )


class SemanticProposalBoundaryTests(unittest.TestCase):
    def test_selected_markdown_and_explicit_excerpt_reach_the_request(self):
        for tail, expected_coverage in (("", "complete"), ("x" * 9000, "partial")):
            with self.subTest(coverage=expected_coverage), fixtures.PersistedAttemptTests().store_case() as (store, ws, attempt):
                ready = fixtures.PersistedAttemptTests().generate(store, ws, attempt)
                refs = []
                for name, media, content in (
                    ("amounts.csv", "text/csv", b"id,amount\n1,12\n2,20\n"),
                    ("people.csv", "text/csv", b"id,name\n1,A\n2,B\n"),
                    ("notes.md", "text/markdown", ("金额单位为元。\n核算口径待确认。\n" + tail).encode()),
                ):
                    revision, _ = store.import_source_revision(ws, name, media, content)
                    refs.append(SourceIdentity.model_validate(revision.model_dump(include=set(SourceIdentity.model_fields))))
                mission = store.confirm_mission_draft_attempt(ws, attempt.attempt_id, 1, ready.candidate_sha256, refs)
                request = TaskMessageSendRequest(kind="message", client_request_id=fixtures._id(880),
                    expected_state_version=mission.state_version, content="请读取说明和选中片段。",
                    references=[{"kind":"source_excerpt", "evidence_ref": {
                        **refs[2].model_dump(), "locator":{"kind":"text_lines", "line_start":2, "line_end":2}}}],
                    history_messages=[], source_refs=refs, provider_send_confirmed=True)
                receipt, _ = store.send_task_message(ws, mission.mission_id, request)
                snapshot = store.get_context_snapshot(ws, mission.mission_id, receipt.run.run_id)
                plan, adapter = build_context_plan(snapshot, store)
                payload = json.loads(semantic_messages(plan)[1]["content"])
                note = next(source for source in payload["sources"] if source["name"] == "notes.md")
                self.assertEqual(note["text_coverage"], expected_coverage)
                self.assertIn("金额单位为元。", note["excerpts"][0]["text"])
                self.assertEqual(note["excerpts"][1]["text"], "核算口径待确认。")
                evidence = adapter.resolve(note["excerpts"][1]["evidence_handle"], "evidence")
                self.assertEqual(evidence.revision_id, refs[2].revision_id)
                self.assertEqual(evidence.locator.line_start, 2)
                self.assertTrue(all(source["profile_pack"]["tables"] for source in payload["sources"][:2]))

    def test_demo_profile_is_explicit_and_uses_existing_non_thinking_payload(self):
        from contextox.cli import _build_parser
        self.assertEqual(_build_parser().parse_args(["start"]).agent_profile, "production")
        self.assertEqual(agent.get_provider().config.reasoning_effort, "high")
        profile = _build_parser().parse_args(["start", "--agent-profile", "demo-fast"]).agent_profile
        provider = agent.get_provider(agent_profile=profile)
        payload = provider.build_payload([], stream=False, tools=None, max_tokens=4096, user_id="ws-test")
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["thinking"], {"type":"disabled"})
        self.assertNotIn("reasoning_effort", payload)

    def test_prompt_schema_is_compact_and_keeps_nested_contract(self):
        # The product limit applies to the full request, not an obsolete
        # standalone schema size before candidate targets were added.
        self.assertIn('"SemanticFieldInput"', P0_SEMANTIC_PROPOSAL)
        self.assertIn('"required":["version","action","public_answer"]', P0_SEMANTIC_PROPOSAL)
        self.assertIn('Minimal valid example:', P0_SEMANTIC_PROPOSAL)
        self.assertNotEqual(
            P0_SEMANTIC_PROPOSAL_SHA256,
            PRE_COMPACT_SEMANTIC_PROPOSAL_SHA256,
        )

    def _running_semantic_case(self, store, ws, mission, refs):
        run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
        running = store.mark_run_running(ws, mission.mission_id, run.run_id)
        snapshot = store.get_context_snapshot(ws, mission.mission_id, run.run_id)
        manifest = store.record_context_manifest(
            ws,
            mission.mission_id,
            run.run_id,
            agent._context_manifest(snapshot, turn_index=1, tool_receipt_ids=[]),
        )
        store.set_run_phase(ws, mission.mission_id, run.run_id, "synthesize_once")
        receipt = agent._make_receipt(
            provider=fixtures.FakeProvider([]),
            workspace_id=ws,
            attempt_id=None,
            mission_id=mission.mission_id,
            run_id=run.run_id,
            turn_index=1,
            status="succeeded",
            p0_sha256=P0_SEMANTIC_PROPOSAL_SHA256,
            usage=fixtures._usage(),
            context_manifest=manifest,
            run_tool_schema_sha256=EMPTY_TOOL_SCHEMA_SHA256,
        )
        store.record_provider_receipt(ws, mission.mission_id, run.run_id, receipt)
        store.set_run_phase(ws, mission.mission_id, run.run_id, "validate")
        store.set_run_phase(ws, mission.mission_id, run.run_id, "apply")
        return running, snapshot

    def test_one_high_json_request_has_no_tool_schema(self):
        with fixtures.PersistedRunTests().store_case(
            with_sources=True, controller=True
        ) as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            snapshot = store.get_context_snapshot(ws, mission.mission_id, run.run_id)
            plan, _ = build_context_plan(snapshot, store)
            evidence = plan.sources[0]["profile_pack"]["tables"][0]["evidence_handles"]
            provider = ProposalProvider(json.dumps({
                "version": "v1",
                "action": "answer_only",
                "public_answer": "The authorized profile is available for review.",
                "fields": [],
                "relationships": [],
                "unresolved_items": [],
                "questions": [],
                "evidence_handles": evidence,
            }))

            proposal, completion = request_semantic_proposal(
                provider,
                plan,
                run.budget,
                user_id="ws-test",
                cancel_event=Event(),
            )

            self.assertEqual(proposal.action, "answer_only")
            self.assertEqual(completion.completion_id, "proposal-completion")
            self.assertEqual(len(provider.calls), 1)
            request = provider.calls[0]
            self.assertEqual([message["role"] for message in request["messages"]], ["system", "user"])
            self.assertFalse(request["kwargs"]["stream"])
            self.assertIsNone(request["kwargs"]["tools"])
            self.assertEqual(request["kwargs"]["max_context_bytes"], SEMANTIC_CONTEXT_MAX_BYTES)
            self.assertEqual(
                request["kwargs"]["timeouts"].total_ms,
                SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS,
            )
            self.assertEqual(
                request["kwargs"]["timeouts"].first_event_ms,
                SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS,
            )

    def test_context_exposes_cross_source_relationship_stats_only_through_handles(self):
        with fixtures.PersistedRunTests().store_case(
            with_sources=True, controller=True
        ) as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            snapshot = store.get_context_snapshot(ws, mission.mission_id, run.run_id)

            plan, adapter = build_context_plan(snapshot, store)

            self.assertEqual(len(plan.prospective_relationships), 1)
            relationship = plan.prospective_relationships[0]
            self.assertEqual(relationship["matched_distinct_keys"], 1)
            self.assertEqual(relationship["unmatched_left_rows"], 1)
            self.assertEqual(relationship["unmatched_right_rows"], 1)
            self.assertEqual(relationship["prospective_join_rows"], 1)
            self.assertEqual(relationship["observed_cardinality"], "one_to_one")
            self.assertEqual(len(relationship["evidence_handles"]), 2)
            self.assertEqual(
                adapter.resolve(relationship["left"], "table").columns,
                [],
            )
            for handle in relationship["left_column_handles"]:
                self.assertEqual(adapter.resolve(handle, "column").column, "id")
            serialized = json.dumps(relationship, ensure_ascii=False)
            self.assertNotIn(ws, serialized)
            self.assertNotIn(refs[0].revision_id, serialized)

    def test_invalid_proposal_fails_without_a_second_request(self):
        plan = ContextPlanV1(
            context_kind="semantic_context_v1",
            mission={"goal": "Synthetic"},
            message_context=None,
            sources=[],
            draft=None,
            clarifications=[],
            approved_answers=[],
        )
        provider = ProposalProvider('{"version":"v1","action":"answer_only"}')

        with self.assertRaisesRegex(SemanticProposalFailure, "semantic_proposal_invalid"):
            request_semantic_proposal(
                provider,
                plan,
                fixtures.RunBudget(),
                user_id="ws-test",
                cancel_event=Event(),
            )

        self.assertEqual(len(provider.calls), 1)

    def test_context_over_approved_message_budget_fails_before_provider(self):
        plan = ContextPlanV1(
            context_kind="semantic_context_v1",
            mission={"goal": "x" * (61 * 1024)},
            message_context=None,
            sources=[],
            draft=None,
            clarifications=[],
            approved_answers=[],
        )

        with self.assertRaisesRegex(SemanticProposalFailure, "context_too_broad"):
            semantic_messages(plan)

    def test_context_prunes_reproducible_profile_detail_before_rejecting(self):
        profile = {
            "limitations": ["x" * 30_000],
            "tables": [{
                "table_id": "",
                "evidence_handles": ["ev-required"],
                "columns": [{
                    "name": "customer_id",
                    "top_values": [{"text": "y" * 25_000}],
                    "type_counts": [{"value_kind": "string", "count": 100}],
                    "numeric_p25": None,
                    "numeric_p50": None,
                    "numeric_p75": None,
                    "text_length_min": 1,
                    "text_length_max": 100,
                }],
            }],
        }
        plan = ContextPlanV1(
            context_kind="semantic_context_v1",
            mission={"goal": "保留当前用户请求"},
            message_context={"input": {"content": "保留当前用户请求"}},
            sources=[{"profile_pack": profile}],
            prospective_relationships=[{
                "left": "table-left",
                "right": "table-right",
                "left_column_handles": ["column-left"],
                "right_column_handles": ["column-right"],
                "matched_distinct_keys": 1,
                "evidence_handles": ["relationship-evidence-required"],
            }],
            draft={"draft_token": "draft-required", "unresolved_items": ["待确认"]},
            clarifications=[],
            approved_answers=[],
        )

        messages = semantic_messages(plan)
        self.assertLessEqual(
            len(json.dumps(messages, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()),
            60 * 1024,
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["mission"]["goal"], "保留当前用户请求")
        self.assertEqual(payload["draft"]["draft_token"], "draft-required")
        self.assertEqual(
            payload["sources"][0]["profile_pack"]["tables"][0]["evidence_handles"],
            ["ev-required"],
        )
        self.assertEqual(
            payload["sources"][0]["profile_pack"]["tables"][0]["columns"][0]["top_values"],
            [],
        )
        self.assertEqual(
            payload["prospective_relationships"][0]["evidence_handles"],
            ["relationship-evidence-required"],
        )

    def test_draft_and_clarification_are_applied_in_one_transaction(self):
        with fixtures.PersistedRunTests().store_case(
            with_sources=True, controller=True
        ) as (store, ws, mission, refs):
            run, snapshot = self._running_semantic_case(store, ws, mission, refs)
            plan, adapter = build_context_plan(snapshot.model_copy(update={"run": run}), store)
            source = plan.sources[0]
            table = source["tables"][0]
            profile = source["profile_pack"]["tables"][0]
            column_handle = table["columns"][0]["column_handle"]
            evidence_handle = profile["evidence_handles"][0]
            missing = ["meaning", "grain", "rule", "time_basis", "null_handling"]
            semantics = {
                name: {"value": None, "unknown_reason": "Business decision is required."}
                for name in missing
            }
            semantics["value_type"] = {"value": "integer", "unknown_reason": None}
            proposal = SemanticProposalV1.model_validate({
                "version": "v1",
                "action": "draft_and_clarify",
                "public_answer": "已形成候选字段，并列出需要业务负责人确认的定义。",
                "fields": [{
                    "field_key": "candidate_id",
                    "name": "candidate_id",
                    "semantics": semantics,
                    "source_column_handles": [column_handle],
                    "evidence_status": "candidate",
                    "evidence_handles": [evidence_handle],
                }],
                "relationships": [],
                "unresolved_items": [],
                "questions": [{
                    "question": "请确认 candidate_id 的业务定义、粒度、规则、时间口径和空值处理。",
                    "why_needed": "这些维度无法从获准数据中确定。",
                    "expected_answer_type": "text",
                    "targets": [{"kind":"field", "key":"candidate_id", "property":"meaning"}],
                    "evidence_handles": [evidence_handle],
                }],
                "evidence_handles": [evidence_handle],
            })

            application = normalize_semantic_proposal(adapter, proposal)
            result = store.apply_semantic_proposal(
                ws, mission.mission_id, run.run_id, application
            )

            self.assertEqual(result.status, "waiting_for_human")
            self.assertEqual(result.final_output, proposal.public_answer)
            self.assertEqual(result.draft.version, 1)
            self.assertEqual([item.field_key for item in result.draft.fields], ["candidate_id"])
            self.assertEqual(len(result.clarifications), 1)
            question = result.clarifications[0].questions[0]
            self.assertIsNone(question.suggested_owner_role)
            self.assertEqual(question.evidence_requested, [])
            self.assertEqual(question.related_definition_paths, ["fields.candidate_id.meaning"])
            self.assertEqual(len(result.draft.fields[0].unknowns), 5)
            self.assertEqual(result.clarifications[0].draft_sha256, result.draft.sha256)
            self.assertEqual(result.terminal_receipt.terminal_tool, "create_clarification")
            self.assertEqual(len(result.terminal_receipt.tool_receipt_ids), 2)
            page = store.list_task_messages(ws, mission.mission_id)
            self.assertEqual(page.items[-1].content, proposal.public_answer)

    def test_incomplete_candidate_saves_but_cannot_be_submitted_and_unknown_handle_is_rejected(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run, snapshot = self._running_semantic_case(store, ws, mission, refs)
            plan, adapter = build_context_plan(snapshot, store)
            source = plan.sources[0]
            field = {"field_key":"candidate_id", "name":"候选标识",
                     "source_column_handles":[source["tables"][0]["columns"][0]["column_handle"]],
                     "evidence_status":"candidate", "evidence_handles":source["profile_pack"]["tables"][0]["evidence_handles"]}
            proposal = SemanticProposalV1(version="v1", action="draft_only", public_answer="已保存待完善草案。", fields=[field])
            for bad in (
                proposal.model_copy(update={"action":"draft_and_submit"}),
                proposal.model_copy(update={"evidence_handles":["evidence_unknown"]}),
            ):
                with self.assertRaises(SemanticProposalFailure):
                    normalize_semantic_proposal(adapter, bad)
            application = normalize_semantic_proposal(adapter, proposal)
            without_evidence = application.model_copy(update={"fields":[
                application.fields[0].model_copy(update={"source_refs":[]})]})
            with self.assertRaises(fixtures.Path2StateError) as denied:
                store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, without_evidence)
            self.assertEqual(denied.exception.code, "semantic_evidence_incomplete")
            with patch.object(store, "_execute_terminal_tool", side_effect=fixtures.WorkspaceStoreUnavailableError()):
                with self.assertRaises(fixtures.WorkspaceStoreUnavailableError):
                    store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, application)
            self.assertIsNone(store.get_run_snapshot(ws, mission.mission_id, run.run_id).draft)
            result = store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, application)
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.draft.semantic_approval, "pending")
            self.assertEqual(len(result.draft.fields[0].unknowns), 6)
            self.assertTrue(all(item.reason == "模型未提供，待补充" for item in result.draft.fields[0].unknowns))
            self.assertEqual(result.terminal_receipt.terminal_tool, "finish_run")
            self.assertEqual(result.clarifications, [])

    def test_question_paths_are_program_owned_and_candidate_targets_must_exist(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run, snapshot = self._running_semantic_case(store, ws, mission, refs)
            plan, adapter = build_context_plan(snapshot, store)
            source = plan.sources[0]
            field = {"field_key":"candidate_id", "name":"候选标识",
                "source_column_handles":[source["tables"][0]["columns"][0]["column_handle"]],
                "evidence_status":"candidate", "evidence_handles":source["profile_pack"]["tables"][0]["evidence_handles"]}
            payload = {"version":"v1", "action":"draft_and_clarify", "public_answer":"需要业务确认规则。",
                "fields":[field], "questions":[{"question":"标识是否保留前导零？", "why_needed":"确定关联规则。",
                    "expected_answer_type":"text", "targets":[{"kind":"field", "key":"candidate_id", "property":"rule"}]}]}
            application = normalize_semantic_proposal(adapter, SemanticProposalV1.model_validate(payload))
            self.assertEqual(application.questions[0].related_definition_paths, ["fields.candidate_id.rule"])
            self.assertNotIn("related_definition_paths", P0_SEMANTIC_PROPOSAL)
            payload["questions"][0]["related_definition_paths"] = ["fields.0.rule"]
            with self.assertRaises(ValidationError):
                SemanticProposalV1.model_validate(payload)
            del payload["questions"][0]["related_definition_paths"]
            payload["questions"][0]["targets"][0]["property"] = "semantics.rule"
            with self.assertRaises(ValidationError):
                SemanticProposalV1.model_validate(payload)
            payload["questions"][0]["targets"][0]["property"] = "rule"
            payload["questions"][0]["targets"][0]["key"] = "0"
            with self.assertRaisesRegex(SemanticProposalFailure, "semantic_definition_path_invalid"):
                normalize_semantic_proposal(adapter, SemanticProposalV1.model_validate(payload))
            payload["fields"].append({**field, "field_key":"0"})
            numeric_key = normalize_semantic_proposal(adapter, SemanticProposalV1.model_validate(payload))
            self.assertEqual(numeric_key.questions[0].related_definition_paths, ["fields.0.rule"])
            self.assertIsNone(store.get_run_snapshot(ws, mission.mission_id, run.run_id).draft)
            result = store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, application)
            self.assertEqual(result.status, "waiting_for_human")
            self.assertEqual(result.clarifications[0].questions[0].related_definition_paths, ["fields.candidate_id.rule"])

    def test_column_capabilities_supply_physical_evidence_without_duplicate_model_handles(self):
        with fixtures.PersistedAttemptTests().store_case() as (store, ws, attempt):
            ready = fixtures.PersistedAttemptTests().generate(store, ws, attempt)
            refs = []
            for name in ("left.csv", "right.csv"):
                revision, _ = store.import_source_revision(ws, name, "text/csv", b"id,value\n1,a\n2,b\n")
                refs.append(SourceIdentity.model_validate(revision.model_dump(include=set(SourceIdentity.model_fields))))
            revision, _ = store.import_source_revision(ws, "meaning.md", "text/markdown", "id 为标识。\n".encode())
            note_ref = SourceIdentity.model_validate(revision.model_dump(include=set(SourceIdentity.model_fields)))
            refs = [*refs, note_ref]
            mission = store.confirm_mission_draft_attempt(ws, attempt.attempt_id, 1, ready.candidate_sha256, refs)
            run, snapshot = self._running_semantic_case(store, ws, mission, refs)
            plan, adapter = build_context_plan(snapshot, store)
            note_handle = plan.sources[2]["excerpts"][0]["evidence_handle"]
            field = {"field_key":"candidate_id", "name":"候选标识",
                "source_column_handles":[plan.sources[0]["tables"][0]["columns"][0]["column_handle"]],
                "evidence_status":"candidate", "evidence_handles":[note_handle]}
            pair = plan.prospective_relationships[0]
            relation = {key:pair[key] for key in ("left", "right", "left_column_handles", "right_column_handles", "observed_cardinality")}
            relation.update(relationship_key="candidate_link", evidence_status="candidate", evidence_handles=[])
            proposal = SemanticProposalV1(version="v1", action="draft_only", public_answer="候选含本地引用。",
                fields=[field], relationships=[relation])
            application = normalize_semantic_proposal(adapter, proposal)
            field_refs = application.fields[0].source_refs
            self.assertEqual({ref.revision_id for ref in field_refs}, {refs[0].revision_id, note_ref.revision_id})
            self.assertEqual({ref.revision_id for ref in application.relationships[0].source_refs}, {ref.revision_id for ref in refs[:2]})
            physical = next(ref for ref in field_refs if ref.locator.kind == "csv_rows")
            self.assertEqual(physical.locator.column, "id")
            self.assertTrue(store.read_source_excerpt(ws, physical.revision_id, physical.locator).text)
            bad = proposal.model_copy(update={"fields":[proposal.fields[0].model_copy(update={"source_column_handles":["column_forged"]})]})
            with self.assertRaisesRegex(SemanticProposalFailure, "semantic_handle_invalid"):
                normalize_semantic_proposal(adapter, bad)
            self.assertIsNone(store.get_run_snapshot(ws, mission.mission_id, run.run_id).draft)
            result = store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, application)
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.draft.fields[0].source_refs, field_refs)

    def test_candidate_wrappers_preserve_unknowns_and_apply_without_model_correction(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run, snapshot = self._running_semantic_case(store, ws, mission, refs)
            plan, adapter = build_context_plan(snapshot, store)
            source = plan.sources[0]
            field = {"field_key":"candidate_id", "name":"候选标识",
                "source_column_handles":[source["tables"][0]["columns"][0]["column_handle"]],
                "evidence_status":"candidate", "evidence_handles":[], "semantics":{
                    "meaning":"客户标识", "value_type":{"value":"string"}, "grain":None,
                    "rule":{"value":"按原始字符连接", "unknown_reason":"是否去掉前导零待确认"},
                    "null_handling":{"value":None, "unknown_reason":None}}}
            pair = plan.prospective_relationships[0]
            relation = {key:pair[key] for key in ("left", "right", "left_column_handles", "right_column_handles", "observed_cardinality")}
            relation.update(relationship_key="candidate_link", evidence_status="candidate", evidence_handles=[],
                join_rule={"value":"按 id 相等连接", "unknown_reason":None}, grain_notes={"value":None, "unknown_reason":"粒度待确认"})
            payload = {"version":"v1", "action":"draft_only", "public_answer":"已保存带未决说明的候选。",
                "fields":[field], "relationships":[relation]}
            provider = ProposalProvider(json.dumps(payload, ensure_ascii=False))
            proposal, _ = request_semantic_proposal(provider, plan, run.budget, user_id="synthetic", cancel_event=Event())
            application = normalize_semantic_proposal(adapter, proposal)
            result = store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, application)
            candidate = result.draft.fields[0]
            self.assertEqual(candidate.meaning, "客户标识")
            self.assertEqual(candidate.value_type, "string")
            self.assertIsNone(candidate.rule)
            reasons = {item.property_path:item.reason for item in candidate.unknowns}
            self.assertIn("按原始字符连接", reasons["rule"])
            self.assertIn("是否去掉前导零待确认", reasons["rule"])
            self.assertEqual(reasons["null_handling"], "模型未提供，待补充")
            self.assertEqual(result.draft.relationships[0].join_rule, "按 id 相等连接")
            self.assertEqual(result.draft.relationships[0].unknowns[0].reason, "粒度待确认")
            self.assertEqual(result.status, "partial")
            self.assertEqual(len(provider.calls), 1)
            field["semantics"]["meaning"] = {"value":"客户标识", "unknown_reason":None, "unexpected":"must not be dropped"}
            provider = ProposalProvider(json.dumps(payload, ensure_ascii=False))
            with self.assertRaisesRegex(SemanticProposalFailure, "semantic_proposal_invalid"):
                request_semantic_proposal(provider, plan, run.budget, user_id="synthetic", cancel_event=Event())

    def test_same_context_source_table_and_column_labels_resolve_to_exact_evidence(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run, snapshot = self._running_semantic_case(store, ws, mission, refs)
            plan, adapter = build_context_plan(snapshot, store)
            source = plan.sources[0]
            handles = [source["source_handle"], source["tables"][0]["table_handle"], source["tables"][0]["columns"][0]["column_handle"]]
            self.assertTrue(all(len(handle) < 30 for handle in handles))
            proposal = SemanticProposalV1(version="v1", action="answer_only", public_answer="已核对 @" + handles[-1], evidence_handles=handles)
            application = normalize_semantic_proposal(adapter, proposal)
            self.assertEqual({ref.revision_id for ref in application.source_refs}, {refs[0].revision_id})
            self.assertTrue(any(ref.locator.column == "id" for ref in application.source_refs))
            self.assertIn("@left.csv/id", application.public_answer)
            other_plan, _ = build_context_plan(snapshot, store)
            self.assertNotEqual(other_plan.sources[0]["source_handle"], handles[0])
            for unknown in ("evidence_forged", other_plan.sources[0]["source_handle"]):
                with self.assertRaises(SemanticProposalFailure) as denied:
                    normalize_semantic_proposal(adapter, proposal.model_copy(update={"evidence_handles":[unknown]}))
                self.assertEqual(denied.exception.code, "semantic_handle_invalid")
                self.assertIn("unknown_handle", denied.exception.safe_errors[0])
                self.assertNotIn(unknown, denied.exception.safe_errors[0])
            wrong_kind = SemanticProposalV1(version="v1", action="draft_only", public_answer="无效候选",
                fields=[{"field_key":"wrong", "name":"wrong", "source_column_handles":[handles[0]], "evidence_status":"candidate"}])
            with self.assertRaises(SemanticProposalFailure) as denied:
                normalize_semantic_proposal(adapter, wrong_kind)
            self.assertIn("wrong_kind", denied.exception.safe_errors[0])
            self.assertIsNone(store.get_run_snapshot(ws, mission.mission_id, run.run_id).draft)
            result = store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, application)
            self.assertEqual(result.status, "partial")
            for ref in application.source_refs:
                self.assertTrue(store.read_source_excerpt(ws, ref.revision_id, ref.locator).text)

    def test_omitted_dimensions_preserve_existing_values_on_candidate_update(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run, snapshot = self._running_semantic_case(store, ws, mission, refs)
            plan, adapter = build_context_plan(snapshot, store)
            source = plan.sources[0]
            field = {"field_key":"candidate_id", "name":"标识",
                     "semantics":{"value_type":{"value":"integer", "unknown_reason":None}},
                     "source_column_handles":[source["tables"][0]["columns"][0]["column_handle"]],
                     "evidence_status":"candidate", "evidence_handles":source["profile_pack"]["tables"][0]["evidence_handles"]}
            first = SemanticProposalV1(version="v1", action="draft_only", public_answer="候选", fields=[field], unresolved_items=["保留未决事项"])
            result = store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, normalize_semantic_proposal(adapter, first))
            adapter.current_draft = result.draft
            field["semantics"] = {"meaning":{"value":"人工确认的标识", "unknown_reason":None}}
            updated = normalize_semantic_proposal(adapter, SemanticProposalV1(version="v1", action="draft_only", public_answer="更新候选", fields=[field]))
            self.assertEqual(updated.fields[0].value_type, "integer")
            self.assertEqual(updated.fields[0].meaning, "人工确认的标识")
            self.assertEqual(updated.unresolved_items, ["保留未决事项"])
            self.assertEqual(len(updated.fields[0].unknowns), 4)

    def test_store_rejects_stale_draft_cas_with_zero_domain_writes(self):
        with fixtures.PersistedRunTests().store_case(
            with_sources=True, controller=True
        ) as (store, ws, mission, refs):
            run, _snapshot = self._running_semantic_case(store, ws, mission, refs)
            application = SemanticApplicationInput(
                action="clarify_only",
                public_answer="需要澄清。",
                expected_version=1,
                expected_sha256="0" * 64,
                questions=[{
                    "question": "无效路径问题",
                    "why_needed": "用于验证事务回滚。",
                    "expected_answer_type": "text",
                    "suggested_owner_role": "业务负责人",
                    "related_definition_paths": ["fields.forged.meaning"],
                    "evidence_requested": ["批准定义"],
                    "examples_or_options": [],
                    "blocking_impact": "blocking",
                    "source_refs": [],
                }],
            )
            with self.assertRaises(fixtures.Path2StateError) as caught:
                store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, application)
            self.assertEqual(caught.exception.code, "state_conflict")
            result = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            self.assertEqual(result.status, "running")
            self.assertIsNone(result.draft)
            self.assertIsNone(result.final_output)
            self.assertIsNone(result.terminal_receipt)
            self.assertEqual(result.clarifications, [])

    def test_controller_dispatch_makes_one_request_and_finishes_without_model_tools(self):
        import contextox.store as store_module

        with fixtures.PersistedRunTests().store_case(
            with_sources=True, controller=True
        ) as (store, ws, mission, refs):
            run = store.start_run(
                ws, mission.mission_id, fixtures._start_request(mission, refs)
            )
            provider = fixtures.FakeProvider([ProviderCompletion(
                completion_id="one-semantic-request",
                content=json.dumps({
                    "version": "v1",
                    "action": "answer_only",
                    "public_answer": "已读取获准资料并返回本轮答复。",
                    "fields": [],
                    "relationships": [],
                    "unresolved_items": [],
                    "questions": [],
                    "evidence_handles": [],
                }),
                reasoning_content="private reasoning must not persist",
                tool_calls=(),
                finish_reason="stop",
                usage=fixtures._usage(),
            )])

            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())

            self.assertEqual(len(provider.calls), 1)
            self.assertFalse(provider.calls[0]["kwargs"]["stream"])
            self.assertIsNone(provider.calls[0]["kwargs"]["tools"])
            result = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.final_output, "已读取获准资料并返回本轮答复。")
            self.assertEqual(len(result.provider_receipts), 1)
            self.assertEqual(
                result.provider_receipts[0].tool_schema_sha256,
                EMPTY_TOOL_SCHEMA_SHA256,
            )
            self.assertEqual(len(result.terminal_receipt.tool_receipt_ids), 1)
            events = store.list_run_events(ws, mission.mission_id, run.run_id)
            self.assertEqual(
                [event.event_type for event in events],
                [
                    "run_started", "run_phase_changed", "model_started",
                    "model_completed", "run_phase_changed", "run_phase_changed",
                    "run_phase_changed", "run_partial",
                ],
            )
            phases = [
                event.public_payload.phase for event in events
                if event.event_type == "run_phase_changed"
            ]
            self.assertEqual(
                phases, ["synthesize_once", "validate", "apply", "terminal"]
            )
            model_start = next(
                event for event in events if event.event_type == "model_started"
            )
            self.assertEqual(model_start.public_payload.transport, "non_stream")
            self.assertIsNone(model_start.public_payload.fallback_of_turn_index)

    def test_invalid_json_records_the_single_call_and_leaves_domain_state_empty(self):
        import contextox.store as store_module

        with fixtures.PersistedRunTests().store_case(
            with_sources=True, controller=True
        ) as (store, ws, mission, refs):
            run = store.start_run(
                ws, mission.mission_id, fixtures._start_request(mission, refs)
            )
            provider = fixtures.FakeProvider([ProviderCompletion(
                completion_id="invalid-semantic-json",
                content="{",
                reasoning_content="private reasoning must not persist",
                tool_calls=(),
                finish_reason="stop",
                usage=fixtures._usage(),
            )])

            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())

            result = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual((result.status, result.error_code), (
                "failed", "semantic_proposal_invalid"
            ))
            self.assertEqual(len(result.provider_receipts), 1)
            self.assertIsNone(result.draft)
            self.assertEqual(result.clarifications, [])
            self.assertIsNone(result.terminal_receipt)
            self.assertIsNone(result.final_output)


if __name__ == "__main__":
    unittest.main()
