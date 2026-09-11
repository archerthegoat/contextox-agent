"""Deterministic candidate-result coverage for the browser demo fixture."""

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import test_agent as fixtures

from contextox import agent
from contextox.clarifications import draft_ref, refs as answer_refs
from contextox.models import (
    ClarificationAnswerApproveRequest,
    ClarificationAnswerSaveRequest,
    DiscussionOutput,
    ApprovedAnswerSnapshot,
    TaskMessageSendRequest,
    canonical_sha256,
)
from contextox.store import WorkspaceStore
from scripts.agent_led_synthetic import (
    SyntheticProvider,
    _MISSING_QUESTION,
    _REFUND_QUESTION,
    _TIME_BASIS,
    _TIME_QUESTION,
    synthetic_run,
)


class _ImmediateEvent:
    def wait(self, timeout):
        return False


def _mission_with_sources(store):
    workspace_id = store.create_workspace("Synthetic agent-led result").workspace_id
    attempt = store.create_mission_draft_attempt(workspace_id, "按地区统计订单金额")
    store.mark_mission_draft_running(workspace_id, attempt.attempt_id)
    candidate = agent.MissionDraftPayload(
        title="按地区统计订单金额",
        goal="按地区统计订单金额",
        completion_criteria=["返回可核对的候选定义"],
        scope_notes=[],
    )
    receipt = agent._make_receipt(
        provider=fixtures.FakeProvider([]),
        workspace_id=workspace_id,
        attempt_id=attempt.attempt_id,
        mission_id=None,
        run_id=None,
        turn_index=1,
        status="succeeded",
        p0_sha256=agent.P0_DRAFT_SHA256,
        usage=fixtures._usage(),
    )
    ready = store.save_mission_draft_result(workspace_id, attempt.attempt_id, candidate, receipt)
    source_refs = []
    for name, content in (
        ("订单.csv", b"region,amount\nEast,20\nSouth,30\n"),
        ("退款.csv", b"region,refund\nEast,5\nSouth,0\n"),
    ):
        revision, _ = store.import_source_revision(workspace_id, name, "text/csv", content)
        source_refs.append(fixtures._source_identity_for_test(revision))
    mission = store.confirm_mission_draft_attempt(
        workspace_id,
        attempt.attempt_id,
        1,
        ready.candidate_sha256,
        source_refs,
    )
    return workspace_id, mission, source_refs


class AgentLedSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="contextox-agent-led-result-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore.open(Path(self.temp.name))
        self.workspace_id, self.mission, self.source_refs = _mission_with_sources(self.store)

    def test_discussion_suggestions_bind_answer_targets_and_explain_remaining_time_gap(self):
        provider = SyntheticProvider()
        request = {
            "run_id": str(uuid4()),
            "clarification_id": str(uuid4()),
            "questions": [
                {"question": _REFUND_QUESTION, "related_definition_paths": ["fields.amount.rule"]},
                {"question": _MISSING_QUESTION, "related_definition_paths": ["fields.amount.null_handling"]},
            ],
        }
        context = {
            "input": {"content": "退款扣除已退款金额，我是负责人，依据本条回答。"},
            "mission": {"clarification_cases": [{"request": request, "request_sha256": "a" * 64}]},
        }
        completion = provider.complete(
            [{"role": "system", "content": ""}, {"role": "user", "content": json.dumps(context)}],
            cancel_event=_ImmediateEvent(),
        )
        output = DiscussionOutput.model_validate_json(completion.content)
        self.assertEqual(
            [item.targets[0].property for item in output.answer_suggestions],
            ["rule", "null_handling"],
        )
        self.assertEqual([item.answer for item in output.answer_suggestions], ["扣除已退款金额", "缺失金额单独列出"])

        context["input"]["content"] = "再解释一下当前候选还保留哪些未知事项。"
        context["mission"]["clarification_cases"].append(
            {
                "request": {
                    "run_id": str(uuid4()),
                    "clarification_id": str(uuid4()),
                    "questions": [{"question": _TIME_QUESTION, "related_definition_paths": ["fields.amount.time_basis"]}],
                },
                "request_sha256": "b" * 64,
            }
        )
        completion = provider.complete(
            [{"role": "system", "content": ""}, {"role": "user", "content": json.dumps(context)}],
            cancel_event=_ImmediateEvent(),
        )
        output = DiscussionOutput.model_validate_json(completion.content)
        self.assertEqual(output.next_action, "discuss")
        self.assertIn("时间范围仍需业务确认", output.public_reply)
        self.assertEqual(output.answer_suggestions, [])

        context["input"]["content"] = (
            "时间范围使用当前两份资料的全部记录，不设日期筛选。"
            "我是合成业务负责人，依据就是本条合成验收回答。"
        )
        completion = provider.complete(
            [{"role": "system", "content": ""}, {"role": "user", "content": json.dumps(context)}],
            cancel_event=_ImmediateEvent(),
        )
        output = DiscussionOutput.model_validate_json(completion.content)
        self.assertEqual(len(output.answer_suggestions), 1)
        suggestion = output.answer_suggestions[0]
        self.assertEqual(suggestion.answer, _TIME_BASIS)
        self.assertEqual(suggestion.respondent, "合成业务负责人")
        self.assertEqual(suggestion.basis, "本条合成验收回答")
        self.assertEqual(suggestion.targets[0].property, "time_basis")

    def test_approved_continuations_create_v2_then_review_ready_v3_and_survive_restart(self):
        with self.subTest(stage="approved continuation"):
            first = self.store.start_run(
                self.workspace_id,
                self.mission.mission_id,
                fixtures._start_request(self.mission, self.source_refs),
                demo_fast=True,
            )
            synthetic_run(self.store, self.workspace_id, self.mission.mission_id, first.run_id, _ImmediateEvent())
            first = self.store.get_run_snapshot(self.workspace_id, self.mission.mission_id, first.run_id)
            self.assertEqual(first.status, "waiting_for_human")
            self.assertEqual(first.draft.version, 1)
            self.assertEqual([question.question for question in first.clarifications[0].questions], [_REFUND_QUESTION, _MISSING_QUESTION])

            page = self.store.list_clarification_cases(self.workspace_id, self.mission.mission_id)
            case = page.items[0]
            mission_state = self.store.get_mission_snapshot(self.workspace_id, self.mission.mission_id)
            payload = ClarificationAnswerSaveRequest(
                client_request_id=str(uuid4()),
                expected_latest_version=0,
                expected_state_version=mission_state.mission.state_version,
                request_sha256=canonical_sha256(case.request),
                review_draft=draft_ref(mission_state.draft),
                source_refs=self.source_refs,
                items=[
                    {
                        "question_index": 0,
                        "disposition": "answered",
                        "answer": "扣除已退款金额",
                        "respondent": "合成业务负责人",
                        "basis": "本条合成验收回答",
                        "evidence_refs": [],
                        "targets": [{"kind": "field", "key": "amount", "property": "rule"}],
                        "blocker": None,
                    },
                    {
                        "question_index": 1,
                        "disposition": "answered",
                        "answer": "缺失金额单独列出",
                        "respondent": "合成业务负责人",
                        "basis": "本条合成验收回答",
                        "evidence_refs": [],
                        "targets": [{"kind": "field", "key": "amount", "property": "null_handling"}],
                        "blocker": None,
                    },
                ],
            )
            saved, created = self.store.save_clarification_answer(
                self.workspace_id,
                self.mission.mission_id,
                first.run_id,
                case.request.clarification_id,
                payload,
            )
            self.assertTrue(created)
            self.assertEqual(self.store.get_run_snapshot(self.workspace_id, self.mission.mission_id, first.run_id).approved_answers, [])
            state = self.store.get_mission_snapshot(self.workspace_id, self.mission.mission_id).mission.state_version
            self.store.approve_clarification_answer(
                self.workspace_id,
                self.mission.mission_id,
                first.run_id,
                case.request.clarification_id,
                saved.answer.version,
                ClarificationAnswerApproveRequest(
                    client_request_id=str(uuid4()),
                    expected_state_version=state,
                    expected_answer_sha256=saved.answer.sha256,
                ),
            )
            approved_case = self.store.list_clarification_cases(self.workspace_id, self.mission.mission_id).items[0]
            approved = [
                ApprovedAnswerSnapshot(
                    request=approved_case.request,
                    answer=approved_case.latest_answer,
                    approval=approved_case.latest_approval,
                )
            ]
            state = self.store.get_mission_snapshot(self.workspace_id, self.mission.mission_id)
            request = TaskMessageSendRequest(
                kind="message",
                client_request_id=str(uuid4()),
                expected_state_version=state.mission.state_version,
                content="请依据本次批准的整份回答继续分析。",
                references=[],
                history_messages=[],
                source_refs=self.source_refs,
                provider_send_confirmed=True,
                approved_answers=answer_refs(approved),
                expected_draft=draft_ref(state.draft),
            )
            continuation, created = self.store.send_task_message(
                self.workspace_id,
                self.mission.mission_id,
                request,
                demo_fast=True,
            )
            self.assertTrue(created)
            replay, created = self.store.send_task_message(
                self.workspace_id,
                self.mission.mission_id,
                request,
                demo_fast=True,
            )
            self.assertFalse(created)
            self.assertEqual(replay.run.run_id, continuation.run.run_id)

            synthetic_run(
                self.store,
                self.workspace_id,
                self.mission.mission_id,
                continuation.run.run_id,
                _ImmediateEvent(),
            )
            result = self.store.get_run_snapshot(self.workspace_id, self.mission.mission_id, continuation.run.run_id)
            self.assertEqual(result.status, "waiting_for_human", result.error_code)
            self.assertEqual(result.draft.version, 2)
            amount = result.draft.fields[0]
            self.assertEqual(amount.rule, "订单金额合计减去同地区已退款金额合计")
            self.assertEqual(amount.null_handling, "缺失订单金额不进入净额，并单独列出")
            self.assertIsNone(amount.time_basis)
            self.assertEqual([column.column for column in amount.source_columns], ["amount", "refund"])
            self.assertEqual(result.draft.relationships[0].relationship_key, "orders_refunds_by_region")
            self.assertEqual(result.clarifications[0].questions[0].question, _TIME_QUESTION)
            self.assertIn("草案已更新为 v2", result.final_output)

            impact = self.store.get_answer_impact(self.workspace_id, self.mission.mission_id, result.run_id)
            self.assertEqual(impact.result_state, "available")
            self.assertEqual(impact.after_draft.version, 2)
            amount_change = next(change for change in impact.changes if change.kind == "field" and change.key == "amount")
            self.assertEqual({item.question_index for item in amount_change.question_refs}, {0, 1})
            self.assertTrue(any(change.kind == "relationship" for change in impact.changes))

            time_case = next(
                item
                for item in self.store.list_clarification_cases(
                    self.workspace_id, self.mission.mission_id
                ).items
                if [question.question for question in item.request.questions] == [_TIME_QUESTION]
            )
            mission_state = self.store.get_mission_snapshot(self.workspace_id, self.mission.mission_id)
            time_saved, created = self.store.save_clarification_answer(
                self.workspace_id,
                self.mission.mission_id,
                time_case.request.run_id,
                time_case.request.clarification_id,
                ClarificationAnswerSaveRequest(
                    client_request_id=str(uuid4()),
                    expected_latest_version=0,
                    expected_state_version=mission_state.mission.state_version,
                    request_sha256=canonical_sha256(time_case.request),
                    review_draft=draft_ref(mission_state.draft),
                    source_refs=self.source_refs,
                    items=[
                        {
                            "question_index": 0,
                            "disposition": "answered",
                            "answer": _TIME_BASIS,
                            "respondent": "合成业务负责人",
                            "basis": "本条合成验收回答",
                            "evidence_refs": [],
                            "targets": [{"kind": "field", "key": "amount", "property": "time_basis"}],
                            "blocker": None,
                        }
                    ],
                ),
            )
            self.assertTrue(created)
            state_version = self.store.get_mission_snapshot(
                self.workspace_id, self.mission.mission_id
            ).mission.state_version
            self.store.approve_clarification_answer(
                self.workspace_id,
                self.mission.mission_id,
                time_case.request.run_id,
                time_case.request.clarification_id,
                time_saved.answer.version,
                ClarificationAnswerApproveRequest(
                    client_request_id=str(uuid4()),
                    expected_state_version=state_version,
                    expected_answer_sha256=time_saved.answer.sha256,
                ),
            )
            approved = [
                ApprovedAnswerSnapshot(
                    request=item.request,
                    answer=item.latest_answer,
                    approval=item.latest_approval,
                )
                for item in self.store.list_clarification_cases(
                    self.workspace_id, self.mission.mission_id
                ).items
                if item.review_state == "approved"
            ]
            self.assertEqual(len(approved), 2)
            state = self.store.get_mission_snapshot(self.workspace_id, self.mission.mission_id)
            final_request = TaskMessageSendRequest(
                kind="message",
                client_request_id=str(uuid4()),
                expected_state_version=state.mission.state_version,
                content="请依据已批准的时间范围回答继续整理候选。",
                references=[],
                history_messages=[],
                source_refs=self.source_refs,
                provider_send_confirmed=True,
                approved_answers=answer_refs(approved),
                expected_draft=draft_ref(state.draft),
            )
            final_continuation, created = self.store.send_task_message(
                self.workspace_id,
                self.mission.mission_id,
                final_request,
                demo_fast=True,
            )
            self.assertTrue(created)
            replay, created = self.store.send_task_message(
                self.workspace_id,
                self.mission.mission_id,
                final_request,
                demo_fast=True,
            )
            self.assertFalse(created)
            self.assertEqual(replay.run.run_id, final_continuation.run.run_id)

            synthetic_run(
                self.store,
                self.workspace_id,
                self.mission.mission_id,
                final_continuation.run.run_id,
                _ImmediateEvent(),
            )
            final = self.store.get_run_snapshot(
                self.workspace_id,
                self.mission.mission_id,
                final_continuation.run.run_id,
            )
            self.assertEqual(final.status, "waiting_for_human", final.error_code)
            self.assertEqual(final.terminal_receipt.terminal_tool, "submit_for_review")
            self.assertEqual(final.draft.version, 3)
            self.assertEqual(final.draft.status, "in_review")
            self.assertEqual(final.draft.fields[0].time_basis, _TIME_BASIS)
            self.assertEqual(final.draft.fields[0].unknowns, [])
            self.assertEqual(final.draft.unresolved_items, [])
            self.assertEqual(final.clarifications, [])
            self.assertIn("当前没有待回答的业务口径", final.final_output)

            final_impact = self.store.get_answer_impact(
                self.workspace_id, self.mission.mission_id, final.run_id
            )
            self.assertEqual(final_impact.result_state, "available")
            self.assertEqual(final_impact.after_draft.version, 3)
            final_amount_change = next(
                change
                for change in final_impact.changes
                if change.kind == "field" and change.key == "amount"
            )
            self.assertTrue(any(ref.question_index == 0 for ref in final_amount_change.question_refs))

            reopened = WorkspaceStore.open(Path(self.temp.name))
            restored = reopened.get_run_snapshot(self.workspace_id, self.mission.mission_id, final.run_id)
            self.assertEqual(restored.draft, final.draft)
            self.assertEqual(restored.final_output, final.final_output)
            self.assertEqual(len(reopened.list_task_runs(self.workspace_id, self.mission.mission_id).items), 3)


if __name__ == "__main__":
    unittest.main()
