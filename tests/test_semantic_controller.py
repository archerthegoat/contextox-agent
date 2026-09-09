import json
import unittest
from threading import Event
from unittest.mock import patch

import test_agent as fixtures

from contextox import agent
from contextox.model_tools import ContextPlanV1, SemanticProposalV1
from contextox.models import SemanticApplicationInput
from contextox.provider import ProviderCompletion, ProviderUsage
from contextox.semantic_controller import (
    EMPTY_TOOL_SCHEMA_SHA256,
    P0_SEMANTIC_PROPOSAL_SHA256,
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
                    "suggested_owner_role": "业务负责人",
                    "related_definition_paths": [f"fields.candidate_id.{name}" for name in missing],
                    "evidence_requested": ["批准的字段定义或数据字典"],
                    "examples_or_options": [],
                    "blocking_impact": "blocking",
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
            self.assertEqual(result.clarifications[0].draft_sha256, result.draft.sha256)
            self.assertEqual(result.terminal_receipt.terminal_tool, "create_clarification")
            self.assertEqual(len(result.terminal_receipt.tool_receipt_ids), 2)
            page = store.list_task_messages(ws, mission.mission_id)
            self.assertEqual(page.items[-1].content, proposal.public_answer)

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
