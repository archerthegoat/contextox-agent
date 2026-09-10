"""Public Run/Store regressions for bounded generation recovery; no network."""
import json
import unittest
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import test_agent as fixtures
from contextox import agent
from contextox.models import (SemanticApplicationInput, TaskMessageSendRequest,
    ClarificationAnswerSaveRequest, ClarificationAnswerApproveRequest, canonical_sha256)
from contextox.clarifications import draft_ref, refs as answer_refs
from contextox.provider import ProviderCompletion, ProviderTimeoutUnknownError, ProviderCancelledError


def completed(content):
    return ProviderCompletion(completion_id="synthetic-demo", content=content, reasoning_content=None,
                              tool_calls=(), finish_reason="stop", usage=fixtures._usage())


ANSWER = json.dumps({"version":"v1", "action":"answer_only", "public_answer":"本轮合成答复。"})


class DemoProvider(fixtures.FakeProvider):
    def __init__(self, outcomes, durations=()):
        super().__init__(outcomes)
        self.config = agent.get_provider(agent_profile="demo-fast").config
        self.elapsed = 0
        self.durations = list(durations)

    def complete(self, messages, **kwargs):
        if self.durations:
            self.elapsed += self.durations.pop(0)
        if isinstance(self.completions[0], Exception):
            self.calls.append({"messages":messages, "kwargs":kwargs})
            raise self.completions.pop(0)
        return super().complete(messages, **kwargs)


class DemoRecoveryTests(unittest.TestCase):
    def test_two_rounds_apply_an_approved_answer_and_keep_clickable_source_identity(self):
        class TaskProvider(DemoProvider):
            def complete(self, messages, **kwargs):
                packet = json.loads(messages[1]["content"])
                source = packet["sources"][0]
                evidence = source["profile_pack"]["tables"][0]["evidence_handles"]
                approved = packet["approved_answers"]
                field = {"field_key":"customer_id", "name":"客户标识",
                    "source_column_handles":[source["tables"][0]["columns"][0]["column_handle"]],
                    "evidence_status":"candidate", "evidence_handles":evidence,
                    "semantics": {"rule":{"value":approved[0]["items"][0]["answer"], "unknown_reason":None}}
                                 if approved else {"value_type":{"value":"string", "unknown_reason":None}}}
                relation = packet["prospective_relationships"][0]
                payload = {"version":"v1", "action":"draft_only" if approved else "draft_and_clarify",
                    "public_answer":"已更新候选，引用 @" + evidence[0], "fields":[field], "evidence_handles":evidence}
                if not approved:
                    payload["relationships"] = [{**{name:relation[name] for name in (
                        "left", "right", "left_column_handles", "right_column_handles", "observed_cardinality", "evidence_handles")},
                        "relationship_key":"customer_link", "evidence_status":"candidate"}]
                    payload["questions"] = [{"question":"客户标识是否保留前导零？", "why_needed":"需要业务决定匹配规则。",
                        "expected_answer_type":"text", "targets":[{"kind":"field", "key":"customer_id", "property":"rule"}]}]
                self.completions.append(completed(json.dumps(payload, ensure_ascii=False)))
                return super().complete(messages, **kwargs)

        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, sources):
            mid = mission.mission_id
            first, _ = store.send_task_message(ws, mid, self.request(store, ws, mid, sources), demo_fast=True)
            provider = TaskProvider([])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, ws, mid, first.run.run_id, Event())
            result = store.get_run_snapshot(ws, mid, first.run.run_id)
            self.assertEqual(result.status, "waiting_for_human", result.error_code)
            self.assertEqual(len(result.draft.relationships), 1)
            question = result.clarifications[0]
            snapshot = store.get_mission_snapshot(ws, mid)
            saved, _ = store.save_clarification_answer(ws, mid, result.run_id, question.clarification_id,
                ClarificationAnswerSaveRequest(client_request_id=str(uuid4()), expected_latest_version=0,
                    expected_state_version=snapshot.mission.state_version, request_sha256=canonical_sha256(question),
                    review_draft=draft_ref(snapshot.draft), source_refs=sources, items=[{
                        "question_index":0, "disposition":"answered", "answer":"客户标识保留前导零。",
                        "respondent":"合成案例审核人", "basis":"合成业务规则", "evidence_refs":[],
                        "targets":[{"kind":"field", "key":"customer_id", "property":"rule"}], "blocker":None}]))
            store.approve_clarification_answer(ws, mid, result.run_id, question.clarification_id, saved.answer.version,
                ClarificationAnswerApproveRequest(client_request_id=str(uuid4()),
                    expected_state_version=store.get_mission_snapshot(ws, mid).mission.state_version,
                    expected_answer_sha256=saved.answer.sha256))
            from contextox.models import ApprovedAnswerSnapshot
            case = store.list_clarification_cases(ws, mid).items[0]
            approved = ApprovedAnswerSnapshot(request=case.request, answer=case.latest_answer, approval=case.latest_approval)
            request = self.request(store, ws, mid, sources).model_copy(update={
                "approved_answers":answer_refs([approved]), "expected_draft":draft_ref(result.draft)})
            second, _ = store.send_task_message(ws, mid, request, demo_fast=True)
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, ws, mid, second.run.run_id, Event())
            changed = store.get_run_snapshot(ws, mid, second.run.run_id)
            self.assertEqual(changed.status, "partial", changed.error_code)
            self.assertEqual(changed.draft.version, result.draft.version + 1)
            self.assertEqual(changed.draft.fields[0].rule, "客户标识保留前导零。")
            self.assertEqual(changed.draft.fields[0].value_type, "string")
            self.assertEqual(changed.draft.relationships, result.draft.relationships)
            self.assertEqual(len(provider.calls), 2)
            messages = store.list_task_messages(ws, mid).items[-4:]
            self.assertEqual([message.role for message in messages], ["user","assistant","user","assistant"])
            self.assertNotIn("evidence_", messages[-1].content)
            self.assertIn("@", messages[-1].content)
            ref = messages[-1].references[0].evidence_ref
            self.assertEqual(ref.revision_id, sources[0].revision_id)
            self.assertTrue(store.read_source_excerpt(ws, ref.revision_id, ref.locator).text)
            impact = store.get_answer_impact(ws, mid, changed.run_id)
            self.assertTrue(any(change.question_refs for change in impact.changes))
            self.assertNotEqual(store.get_mission_snapshot(ws, mid).mission.status, "completed")

    def test_known_format_failure_has_one_correction_with_frozen_context_and_shared_deadline(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs), demo_fast=True)
            provider = DemoProvider([completed('{"private-output-marker":1}'), completed(ANSWER)], [20, 10])
            with patch.object(agent, "get_provider", return_value=provider), patch.object(agent.time, "monotonic", side_effect=lambda:provider.elapsed):
                agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
            result = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            self.assertEqual(result.status, "partial")
            self.assertEqual(len(provider.calls), 2)
            first, second = provider.calls
            self.assertEqual(second["messages"][:2], first["messages"])
            self.assertNotIn("private-output-marker", json.dumps(second["messages"]))
            self.assertEqual(first["kwargs"]["timeouts"].total_ms, 70000)
            self.assertLessEqual(second["kwargs"]["timeouts"].total_ms, 55000)
            self.assertEqual(result.provider_receipts[0].error_code, "semantic_format_invalid")
            self.assertEqual(len(result.terminal_receipt.provider_receipt_ids), 2)
            self.assertTrue(all(call["kwargs"]["tools"] is None for call in provider.calls))

    def test_second_invalid_response_timeout_and_cancellation_do_not_loop_or_write(self):
        cases = ([completed("{"), completed("{")], [ProviderTimeoutUnknownError()], [ProviderCancelledError(outcome_unknown=True)])
        for outcomes in cases:
            with self.subTest(count=len(outcomes)), fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
                run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs), demo_fast=True)
                provider = DemoProvider(outcomes)
                with patch.object(agent, "get_provider", return_value=provider):
                    agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
                result = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
                self.assertEqual(len(provider.calls), len(outcomes))
                self.assertIsNone(result.draft)
                self.assertIsNone(result.final_output)
                self.assertIsNone(result.terminal_receipt)
                self.assertEqual(result.clarifications, [])

    def test_format_fence_is_removed_without_an_extra_request(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs), demo_fast=True)
            provider = DemoProvider([completed("```json\n" + ANSWER + "\n```")])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(store.get_run_snapshot(ws, mission.mission_id, run.run_id).status, "partial")

    def test_exhausted_deadline_and_unknown_handle_cannot_trigger_correction(self):
        for response, duration, code in (
            ("{", 75, "elapsed_budget_exceeded"),
            (json.dumps({"version":"v1", "action":"answer_only", "public_answer":"引用", "evidence_handles":["evidence_unknown"]}), 0, "semantic_handle_invalid"),
        ):
            with self.subTest(code=code), fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
                run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs), demo_fast=True)
                provider = DemoProvider([completed(response), completed(ANSWER)], [duration])
                with patch.object(agent, "get_provider", return_value=provider), patch.object(agent.time, "monotonic", side_effect=lambda:provider.elapsed):
                    agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
                result = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
                self.assertEqual(result.error_code, code)
                self.assertEqual(len(provider.calls), 1)
                self.assertIsNone(result.draft)
                self.assertIsNone(result.final_output)

    def request(self, store, ws, mid, refs, origin=None):
        mission = store.get_mission_snapshot(ws, mid).mission
        return TaskMessageSendRequest(kind="message", client_request_id=str(uuid4()),
            expected_state_version=mission.state_version, content="继续分析已选合成资料。", references=[],
            history_messages=[], source_refs=refs, provider_send_confirmed=True, regenerate_from_run_id=origin)

    def test_explicit_regeneration_keeps_unknown_receipt_and_does_not_block_later_messages(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            mid = mission.mission_id
            run = store.start_run(ws, mid, fixtures._start_request(mission, refs), demo_fast=True)
            with patch.object(agent, "get_provider", return_value=DemoProvider([ProviderTimeoutUnknownError()])):
                agent.run_agent(store, ws, mid, run.run_id, Event())
            failed = store.get_run_snapshot(ws, mid, run.run_id)
            self.assertTrue(failed.regeneration_allowed)
            for origin, code in ((None, "previous_outcome_unresolved"), (str(uuid4()), "regeneration_not_allowed")):
                with self.assertRaises(fixtures.Path2StateError) as raised:
                    store.send_task_message(ws, mid, self.request(store, ws, mid, refs, origin), demo_fast=True)
                self.assertEqual(raised.exception.code, code)
            request = self.request(store, ws, mid, refs, run.run_id)
            receipt, created = store.send_task_message(ws, mid, request, demo_fast=True)
            self.assertTrue(created)
            replay, created = store.send_task_message(ws, mid, request, demo_fast=True)
            self.assertFalse(created)
            self.assertEqual(replay.run.run_id, receipt.run.run_id)
            with patch.object(agent, "get_provider", return_value=DemoProvider([completed(ANSWER)])):
                agent.run_agent(store, ws, mid, receipt.run.run_id, Event())
            self.assertEqual(store.get_run_snapshot(ws, mid, run.run_id).provider_receipts, failed.provider_receipts)
            # A late old result cannot apply after the new run has finished.
            with self.assertRaises(fixtures.Path2StateError):
                store.apply_semantic_proposal(ws, mid, run.run_id, SemanticApplicationInput(
                    action="answer_only", public_answer="迟到结果", expected_version=0, expected_sha256=None))
            following, created = store.send_task_message(ws, mid, self.request(store, ws, mid, refs), demo_fast=True)
            self.assertTrue(created)
            self.assertEqual(following.run.status, "queued")
            restarted = fixtures.WorkspaceStore.open(store.data_dir)
            self.assertEqual(restarted.message_submission(ws, mid, request.client_request_id).run.run_id, receipt.run.run_id)
            self.assertNotIn("迟到结果", [message.content for message in restarted.list_task_messages(ws, mid).items])

    def test_apply_outcome_unknown_cannot_use_generation_recovery(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs), demo_fast=True)
            with patch.object(agent, "get_provider", return_value=DemoProvider([completed(ANSWER)])), patch.object(
                store, "apply_semantic_proposal", side_effect=fixtures.Path2StateError("state_write_outcome_unknown")):
                agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
            result = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            self.assertFalse(result.regeneration_allowed)
            with self.assertRaises(fixtures.Path2StateError) as raised:
                store.send_task_message(ws, mission.mission_id, self.request(store, ws, mission.mission_id, refs, run.run_id), demo_fast=True)
            self.assertEqual(raised.exception.code, "regeneration_not_allowed")
            with self.assertRaises(fixtures.Path2StateError) as raised:
                store.send_task_message(ws, mission.mission_id, self.request(store, ws, mission.mission_id, refs), demo_fast=True)
            self.assertEqual(raised.exception.code, "previous_outcome_unresolved")


if __name__ == "__main__":
    unittest.main()
