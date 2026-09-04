import json
import unittest
from threading import Event
from unittest.mock import patch

from contextox import agent
from contextox.models import (
    ClarificationRequest,
    ContextPacketManifest,
    ContextSnapshot,
    DomainRejection,
    Mission,
    MissionDraftAttempt,
    ProviderConfigSnapshot,
    ProviderReceipt,
    RunSnapshot,
    RunToolResult,
    TerminalReceipt,
    ToolReceipt,
    canonical_sha256,
)
from contextox.provider import (
    ProviderCancelledError,
    ProviderCompletion,
    ProviderToolCall,
    ProviderUsage,
)
from contextox.store import WorkspaceStoreError


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _usage() -> ProviderUsage:
    return ProviderUsage(input_tokens=9, output_tokens=5, cache_hit_tokens=1, cache_miss_tokens=8)


class ControlledClock:
    def __init__(self, values: list[float], default: float | None = None) -> None:
        self.values = list(values)
        self.default = values[-1] if default is None else default

    def monotonic(self) -> float:
        if self.values:
            return self.values.pop(0)
        return self.default


def _attempt() -> MissionDraftAttempt:
    return MissionDraftAttempt(
        workspace_id=_id(1),
        attempt_id=_id(2),
        created_at="2026-09-03T00:00:00+00:00",
        original_input="Map the order and customer fields.",
        status="queued",
        candidate=None,
        candidate_version=None,
        candidate_sha256=None,
        provider_receipt_id=None,
        mission_id=None,
        error_code=None,
    )


def _context_snapshot(status: str = "queued") -> ContextSnapshot:
    mission = Mission(
        workspace_id=_id(1),
        mission_id=_id(3),
        created_at="2026-09-03T00:00:00+00:00",
        state_version=1,
        status="active",
        title="Definition mapping",
        goal="Map authorized tables.",
        completion_criteria=["Produce a candidate draft"],
        scope_notes=[],
        original_attempt_id=_id(2),
        source_refs=[],
    )
    run = RunSnapshot(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        status=status,
        created_at="2026-09-03T00:00:00+00:00",
        started_at=None,
        finished_at=None,
        budget={
            "max_model_turns": 8,
            "max_tool_calls": 24,
            "max_elapsed_ms": 300000,
            "max_output_tokens": 4096,
            "max_retries": 0,
            "connect_timeout_ms": 10000,
            "first_event_timeout_ms": 60000,
            "idle_timeout_ms": 30000,
            "total_timeout_ms": 120000,
            "max_context_bytes": 262144,
        },
        source_refs=[],
        draft=None,
        clarifications=[],
        last_sequence=0,
        terminal_receipt=None,
        final_output=None,
        error_code=None,
    )
    return ContextSnapshot(mission=mission, run=run, sources=[], draft=None, clarifications=[])


class FakeProvider:
    def __init__(self, completions: list[ProviderCompletion], cancel_event: Event | None = None) -> None:
        self.completions = list(completions)
        self.calls: list[dict] = []
        self.cancel_event = cancel_event
        self.config = ProviderConfigSnapshot(
            endpoint_id="deepseek_chat_completions",
            model="deepseek-v4-flash",
            thinking="enabled",
            reasoning_effort="high",
        )

    @staticmethod
    def opaque_user_id(workspace_id: str) -> str:
        return "ws-test"

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "kwargs": kwargs})
        if self.cancel_event is not None:
            self.cancel_event.set()
            raise ProviderCancelledError()
        completion = self.completions.pop(0)
        callback = kwargs.get("on_content")
        if callback is not None and completion.content:
            callback(completion.content)
        return completion


class FakeStore:
    def __init__(
        self,
        *,
        context: ContextSnapshot | None = None,
        attempt: MissionDraftAttempt | None = None,
        validation_error: WorkspaceStoreError | None = None,
    ) -> None:
        self.context = context
        self.attempt = attempt
        self.validation_error = validation_error
        self.context_calls = 0
        self.mark_calls = 0
        self.manifests = []
        self.receipts: list[ProviderReceipt] = []
        self.events = []
        self.validated_batches = []
        self.executed_calls = []
        self.failures = []
        self.saved_outputs = []
        self.saved_attempts = []
        self.tool_results: dict[str, RunToolResult] = {}

    def get_mission_draft_attempt(self, workspace_id: str, attempt_id: str):
        return self.attempt

    def save_mission_draft_result(self, workspace_id, attempt_id, candidate, receipt):
        self.saved_attempts.append((workspace_id, attempt_id, candidate, receipt))
        return self.attempt

    def fail_mission_draft_attempt(self, workspace_id, attempt_id, status, code, receipt):
        self.failures.append(("attempt", status, code, receipt))
        return self.attempt

    def get_context_snapshot(self, workspace_id: str, mission_id: str, run_id: str):
        self.context_calls += 1
        return self.context

    def mark_run_running(self, workspace_id: str, mission_id: str, run_id: str):
        self.mark_calls += 1
        run_data = self.context.run.model_dump(mode="python")
        run_data.update({"status": "running"})
        running = RunSnapshot(**run_data)
        self.context = self.context.model_copy(update={"run": running})
        return running

    def record_context_manifest(self, workspace_id, mission_id, run_id, manifest):
        self.manifests.append(manifest)
        payload = manifest.model_dump(mode="json")
        payload.update(
            {
                "workspace_id": workspace_id,
                "mission_id": mission_id,
                "run_id": run_id,
                "manifest_id": _id(30 + len(self.manifests)),
            }
        )
        payload["sha256"] = canonical_sha256(payload)
        return ContextPacketManifest.model_validate(payload)

    def record_provider_receipt(self, workspace_id, mission_id, run_id, receipt):
        self.receipts.append(receipt)
        return receipt

    def append_run_event(self, workspace_id, mission_id, run_id, event):
        self.events.append(event)
        return event

    def validate_run_tool_batch(self, workspace_id, mission_id, run_id, calls):
        self.validated_batches.append(list(calls))
        if self.validation_error is not None:
            raise self.validation_error

    def execute_run_tool(self, workspace_id, mission_id, run_id, call):
        self.executed_calls.append(call)
        return self.tool_results[call.call_id]

    def fail_run(self, workspace_id, mission_id, run_id, status, code):
        self.failures.append(("run", status, code))
        run_data = self.context.run.model_dump(mode="python")
        run_data.update({"status": status, "error_code": code})
        stopped = RunSnapshot(**run_data)
        self.context = self.context.model_copy(update={"run": stopped})
        return stopped

    def save_run_final_output(self, workspace_id, mission_id, run_id, content):
        self.saved_outputs.append(content)
        return self.context.run


def _completion(content: str, calls: tuple[ProviderToolCall, ...] = ()) -> ProviderCompletion:
    return ProviderCompletion(
        completion_id="completion-1",
        content=content,
        reasoning_content="hidden reasoning",
        tool_calls=calls,
        finish_reason="tool_calls" if calls else "stop",
        usage=_usage(),
    )


def _tool_result(call, *, terminal: bool = False, rejected: bool = False, ordinal: int = 1) -> RunToolResult:
    status = "rejected" if rejected else "succeeded"
    output = DomainRejection(code="not_ready", reason="The domain precondition is not met.") if rejected else []
    terminal_snapshot = None
    if terminal:
        terminal_receipt = TerminalReceipt(
            workspace_id=_id(1),
            mission_id=_id(3),
            run_id=_id(4),
            receipt_id=_id(80),
            created_at="2026-09-03T00:00:00+00:00",
            terminal_tool="finish_run",
            outcome="partial",
            draft_id=None,
            draft_version=None,
            draft_sha256=None,
            clarification_ids=[],
            provider_receipt_ids=[],
            tool_receipt_ids=[],
            source_refs=[],
        )
        output = terminal_receipt
        run_data = _context_snapshot().run.model_dump(mode="python")
        run_data.update({"status": "partial", "terminal_receipt": terminal_receipt})
        terminal_snapshot = RunSnapshot(**run_data)
    receipt = ToolReceipt(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        receipt_id=_id(60 + (1 if terminal else 0)),
        ordinal=ordinal,
        call_id=call.call_id,
        name=call.name,
        arguments_sha256=canonical_sha256(call.arguments),
        status=status,
        created_at="2026-09-03T00:00:00+00:00",
        source_refs=[],
        error_code="not_ready" if rejected else None,
    )
    return RunToolResult(
        call_id=call.call_id,
        status=status,
        output=output,
        tool_receipt=receipt,
        terminal_snapshot=terminal_snapshot,
    )


def _clarification_result(call) -> RunToolResult:
    clarification = ClarificationRequest(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        clarification_id=_id(81),
        draft_version=1,
        draft_sha256="0" * 64,
        status="awaiting_answer",
        questions=[
            {
                "question": "Which grain should be used?",
                "why_needed": "The authorized evidence does not settle the grain.",
                "expected_answer_type": "text",
                "suggested_owner_role": None,
                "related_definition_paths": [],
                "evidence_requested": [],
                "examples_or_options": [],
                "blocking_impact": "blocking",
                "source_refs": [],
            }
        ],
    )
    terminal_receipt = TerminalReceipt(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        receipt_id=_id(82),
        created_at="2026-09-03T00:00:00+00:00",
        terminal_tool="create_clarification",
        outcome="waiting_for_human",
        draft_id=None,
        draft_version=None,
        draft_sha256=None,
        clarification_ids=[clarification.clarification_id],
        provider_receipt_ids=[],
        tool_receipt_ids=[],
        source_refs=[],
    )
    run_data = _context_snapshot().run.model_dump(mode="python")
    run_data.update({"status": "waiting_for_human", "terminal_receipt": terminal_receipt})
    receipt = ToolReceipt(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        receipt_id=_id(83),
        ordinal=1,
        call_id=call.call_id,
        name=call.name,
        arguments_sha256=canonical_sha256(call.arguments),
        status="succeeded",
        created_at="2026-09-03T00:00:00+00:00",
        source_refs=[],
        error_code=None,
    )
    return RunToolResult(
        call_id=call.call_id,
        status="succeeded",
        output=clarification,
        tool_receipt=receipt,
        terminal_snapshot=RunSnapshot(**run_data),
    )


class AgentTests(unittest.TestCase):
    def test_draft_sends_only_p0_and_original_input_and_saves_candidate(self) -> None:
        store = FakeStore(attempt=_attempt())
        provider = FakeProvider(
            [
                _completion(
                    '{"title":"Order mapping","goal":"Map fields","completion_criteria":["Draft"],"scope_notes":[]}'
                )
            ]
        )
        with patch.object(agent, "get_provider", return_value=provider):
            agent.generate_mission_draft(store, _id(1), _id(2), Event())

        self.assertEqual(len(provider.calls), 1)
        call = provider.calls[0]
        self.assertFalse(call["kwargs"]["stream"])
        self.assertIsNone(call["kwargs"]["tools"])
        self.assertEqual([message["role"] for message in call["messages"]], ["system", "user"])
        self.assertEqual(call["messages"][1]["content"], store.attempt.original_input)
        self.assertEqual(len(store.saved_attempts), 1)
        self.assertEqual(store.saved_attempts[0][2].title, "Order mapping")
        self.assertEqual(store.saved_attempts[0][3].status, "succeeded")
        self.assertIsNone(store.saved_attempts[0][3].mission_id)

    def test_draft_missing_usage_blocks_without_candidate_and_invalid_json_fails(self) -> None:
        for completion, expected_status, expected_code in (
            (_completion("{}"), "blocked", "provider_usage_unknown"),
            (
                ProviderCompletion(
                    completion_id="completion-2",
                    content="not-json",
                    reasoning_content="hidden",
                    tool_calls=(),
                    finish_reason="stop",
                    usage=_usage(),
                ),
                "failed",
                "provider_protocol_error",
            ),
        ):
            store = FakeStore(attempt=_attempt())
            if expected_status == "blocked":
                completion = ProviderCompletion(
                    completion_id="completion-3",
                    content="{}",
                    reasoning_content="hidden",
                    tool_calls=(),
                    finish_reason="stop",
                    usage=None,
                )
            provider = FakeProvider([completion])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.generate_mission_draft(store, _id(1), _id(2), Event())
            self.assertEqual(len(store.saved_attempts), 0)
            self.assertEqual(store.failures[0][1:3], (expected_status, expected_code))

    def test_run_serializes_tools_and_stops_on_partial_terminal_without_reasoning_leak(self) -> None:
        list_call = ProviderToolCall("call-list", "list_sources", "{}")
        finish_call = ProviderToolCall(
            "call-finish",
            "finish_run",
            '{"outcome":"partial","reason":"Evidence is incomplete.","source_refs":[]}',
        )
        provider = FakeProvider([_completion("Inspecting.", (list_call,)), _completion("Partial.", (finish_call,))])
        store = FakeStore(context=_context_snapshot())
        list_domain_call = None
        finish_domain_call = None
        # The fake result keys are the normalized provider call ids.
        with patch.object(agent, "get_provider", return_value=provider):
            # Build result objects through the same public model seam that the Store returns.
            from contextox.models import ListSourcesCall, FinishRunCall

            list_domain_call = ListSourcesCall(call_id="call-list", name="list_sources", arguments={})
            finish_domain_call = FinishRunCall(
                call_id="call-finish",
                name="finish_run",
                arguments={
                    "outcome": "partial",
                    "reason": "Evidence is incomplete.",
                    "source_refs": [],
                },
            )
            store.tool_results = {
                "call-list": _tool_result(list_domain_call),
                "call-finish": _tool_result(finish_domain_call, terminal=True, ordinal=2),
            }
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(provider.calls[0]["kwargs"]["tools"]), 7)
        self.assertEqual({item["function"]["name"] for item in provider.calls[0]["kwargs"]["tools"]}, agent._TOOL_NAMES)
        self.assertEqual(len(store.validated_batches), 2)
        self.assertEqual([call.call_id for call in store.executed_calls], ["call-list", "call-finish"])
        self.assertEqual(len(store.manifests), 2)
        self.assertEqual(store.manifests[1].tool_receipt_ids, [_id(60)])
        self.assertEqual(store.saved_outputs, ["Inspecting.Partial."])
        self.assertFalse(any("hidden reasoning" in repr(event) for event in store.events))
        self.assertFalse(any("hidden reasoning" in repr(receipt) for receipt in store.receipts))
        self.assertFalse(store.failures)
        self.assertTrue(any(event.event_type == "run_partial" for event in store.events))

    def test_run_shortens_provider_timeouts_to_remaining_elapsed_budget(self) -> None:
        provider = FakeProvider([_completion("No terminal.")])
        store = FakeStore(context=_context_snapshot())
        clock = ControlledClock([0.0, 299.0, 299.0, 299.0, 299.0])
        with patch.object(agent, "get_provider", return_value=provider), patch.object(
            agent.time, "monotonic", side_effect=clock.monotonic
        ):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())

        timeouts = provider.calls[0]["kwargs"]["timeouts"]
        self.assertEqual(
            (timeouts.connect_ms, timeouts.first_event_ms, timeouts.idle_ms, timeouts.total_ms),
            (1000, 1000, 1000, 1000),
        )
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_result_missing"))

    def test_run_stops_before_a_later_tool_when_elapsed_budget_is_consumed(self) -> None:
        first = ProviderToolCall("call-1", "list_sources", "{}")
        second = ProviderToolCall("call-2", "list_sources", "{}")
        from contextox.models import ListSourcesCall

        store = FakeStore(context=_context_snapshot())
        store.tool_results = {
            "call-1": _tool_result(
                ListSourcesCall(call_id="call-1", name="list_sources", arguments={}), ordinal=1
            ),
            "call-2": _tool_result(
                ListSourcesCall(call_id="call-2", name="list_sources", arguments={}), ordinal=2
            ),
        }
        original_execute = store.execute_run_tool
        clock = ControlledClock([0.0, 0.0, 0.0, 299.0, 299.0], default=299.0)

        def execute(workspace_id, mission_id, run_id, call):
            result = original_execute(workspace_id, mission_id, run_id, call)
            if call.call_id == "call-1":
                clock.default = 300.001
            return result

        store.execute_run_tool = execute
        provider = FakeProvider([_completion("", (first, second))])
        with patch.object(agent, "get_provider", return_value=provider), patch.object(
            agent.time, "monotonic", side_effect=clock.monotonic
        ):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())

        self.assertEqual([call.call_id for call in store.executed_calls], ["call-1"])
        self.assertEqual(store.failures[0][1:], ("blocked", "elapsed_budget_exceeded"))

    def test_run_enforces_fixed_eight_turn_and_twenty_four_tool_budgets(self) -> None:
        eight_store = FakeStore(context=_context_snapshot())
        eight_calls = []
        eight_completions = []
        from contextox.models import ListSourcesCall

        for index in range(1, 9):
            call_id = f"turn-call-{index}"
            raw_call = ProviderToolCall(call_id, "list_sources", "{}")
            domain_call = ListSourcesCall(call_id=call_id, name="list_sources", arguments={})
            eight_calls.append(raw_call)
            eight_completions.append(_completion("", (raw_call,)))
            eight_store.tool_results[call_id] = _tool_result(domain_call, ordinal=index)
        eight_provider = FakeProvider(eight_completions)
        with patch.object(agent, "get_provider", return_value=eight_provider):
            agent.run_agent(eight_store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(eight_provider.calls), 8)
        self.assertEqual(len(eight_store.executed_calls), 8)
        self.assertEqual(eight_store.failures[0][1:], ("blocked", "model_turn_budget_exceeded"))

        tool_store = FakeStore(context=_context_snapshot())
        tool_calls = []
        for index in range(1, 25):
            call_id = f"tool-call-{index}"
            raw_call = ProviderToolCall(call_id, "list_sources", "{}")
            tool_calls.append(raw_call)
            tool_store.tool_results[call_id] = _tool_result(
                ListSourcesCall(call_id=call_id, name="list_sources", arguments={}),
                ordinal=index,
            )
        tool_provider = FakeProvider([_completion("", tuple(tool_calls))])
        with patch.object(agent, "get_provider", return_value=tool_provider):
            agent.run_agent(tool_store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(tool_provider.calls), 1)
        self.assertEqual(len(tool_store.executed_calls), 24)
        self.assertEqual(tool_store.failures[0][1:], ("blocked", "tool_call_budget_exceeded"))

    def test_invalid_mixed_terminal_batch_is_not_executed(self) -> None:
        calls = (
            ProviderToolCall("call-list", "list_sources", "{}"),
            ProviderToolCall(
                "call-finish",
                "finish_run",
                '{"outcome":"partial","reason":"incomplete","source_refs":[]}',
            ),
        )
        provider = FakeProvider([_completion("", calls)])
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.executed_calls, [])
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_tool_mixed_batch"))

    def test_tool_result_arguments_hash_must_match_the_normalized_call(self) -> None:
        from contextox.models import ListSourcesCall

        raw_call = ProviderToolCall("call-list", "list_sources", "{}")
        domain_call = ListSourcesCall(call_id="call-list", name="list_sources", arguments={})
        result = _tool_result(domain_call)
        bad_receipt = result.tool_receipt.model_copy(update={"arguments_sha256": "0" * 64})
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {raw_call.call_id: result.model_copy(update={"tool_receipt": bad_receipt})}
        provider = FakeProvider([_completion("", (raw_call,))])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual([call.call_id for call in store.executed_calls], ["call-list"])
        self.assertEqual(store.failures[0][1:], ("failed", "tool_result_invalid"))

    def test_successful_terminal_without_snapshot_cannot_continue_the_run(self) -> None:
        from contextox.models import FinishRunCall

        raw_call = ProviderToolCall(
            "call-finish",
            "finish_run",
            '{"outcome":"partial","reason":"incomplete","source_refs":[]}',
        )
        domain_call = FinishRunCall(
            call_id="call-finish",
            name="finish_run",
            arguments={"outcome": "partial", "reason": "incomplete", "source_refs": []},
        )
        result = _tool_result(domain_call, terminal=True).model_copy(update={"terminal_snapshot": None})
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {raw_call.call_id: result}
        provider = FakeProvider([_completion("", (raw_call,))])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(store.saved_outputs, [])
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_result_invalid"))

    def test_terminal_clarification_draft_binding_must_match_request(self) -> None:
        from contextox.models import CreateClarificationCall

        arguments = {
            "draft_version": 2,
            "draft_sha256": "1" * 64,
            "questions": [
                {
                    "question": "Which grain should be used?",
                    "why_needed": "The authorized evidence does not settle the grain.",
                    "expected_answer_type": "text",
                    "suggested_owner_role": None,
                    "related_definition_paths": [],
                    "evidence_requested": [],
                    "examples_or_options": [],
                    "blocking_impact": "blocking",
                    "source_refs": [],
                }
            ],
        }
        raw_call = ProviderToolCall(
            "call-clarify",
            "create_clarification",
            json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        domain_call = CreateClarificationCall(
            call_id=raw_call.call_id,
            name=raw_call.name,
            arguments=arguments,
        )
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {raw_call.call_id: _clarification_result(domain_call)}
        provider = FakeProvider([_completion("Need a decision.", (raw_call,))])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.saved_outputs, [])
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_result_invalid"))

    def test_rejected_terminal_tool_can_continue_without_a_terminal_snapshot(self) -> None:
        from contextox.models import FinishRunCall

        rejected_raw = ProviderToolCall(
            "call-rejected-finish",
            "finish_run",
            '{"outcome":"partial","reason":"not yet","source_refs":[]}',
        )
        rejected_domain = FinishRunCall(
            call_id=rejected_raw.call_id,
            name=rejected_raw.name,
            arguments={"outcome": "partial", "reason": "not yet", "source_refs": []},
        )
        success_raw = ProviderToolCall(
            "call-finish",
            "finish_run",
            '{"outcome":"partial","reason":"now complete enough","source_refs":[]}',
        )
        success_domain = FinishRunCall(
            call_id=success_raw.call_id,
            name=success_raw.name,
            arguments={"outcome": "partial", "reason": "now complete enough", "source_refs": []},
        )
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {
            rejected_raw.call_id: _tool_result(rejected_domain, rejected=True, ordinal=1),
            success_raw.call_id: _tool_result(success_domain, terminal=True, ordinal=2),
        }
        provider = FakeProvider(
            [
                _completion("The terminal precondition is not met.", (rejected_raw,)),
                _completion("Partial.", (success_raw,)),
            ]
        )
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(store.saved_outputs, ["The terminal precondition is not met.Partial."])
        self.assertFalse(store.failures)

    def test_business_rejection_continues_to_a_single_terminal_batch(self) -> None:
        list_call = ProviderToolCall("call-list", "list_sources", "{}")
        finish_call = ProviderToolCall(
            "call-finish",
            "finish_run",
            '{"outcome":"partial","reason":"still incomplete","source_refs":[]}',
        )
        from contextox.models import FinishRunCall, ListSourcesCall

        list_domain_call = ListSourcesCall(call_id="call-list", name="list_sources", arguments={})
        finish_domain_call = FinishRunCall(
            call_id="call-finish",
            name="finish_run",
            arguments={"outcome": "partial", "reason": "still incomplete", "source_refs": []},
        )
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {
            "call-list": _tool_result(list_domain_call, rejected=True, ordinal=1),
            "call-finish": _tool_result(finish_domain_call, terminal=True, ordinal=2),
        }
        provider = FakeProvider(
            [_completion("The source precondition is not met.", (list_call,)), _completion("Partial.", (finish_call,))]
        )
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())

        self.assertEqual([call.call_id for call in store.executed_calls], ["call-list", "call-finish"])
        completed = [event for event in store.events if event.event_type == "tool_completed"]
        self.assertEqual([event.public_payload.status for event in completed], ["rejected", "succeeded"])
        self.assertEqual(store.saved_outputs, ["The source precondition is not met.Partial."])
        self.assertFalse(store.failures)

    def test_store_permission_and_old_hash_failures_are_not_downgraded(self) -> None:
        for code, expected_status in (("permission_unknown", "blocked"), ("state_conflict", "failed")):
            error = WorkspaceStoreError(code)
            error.code = code
            store = FakeStore(context=_context_snapshot(), validation_error=error)
            call = ProviderToolCall("call-list", "list_sources", "{}")
            provider = FakeProvider([_completion("", (call,))])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, _id(1), _id(3), _id(4), Event())
            self.assertEqual(store.executed_calls, [])
            self.assertEqual(store.failures[0][1:], (expected_status, code))

    def test_clarification_terminal_uses_snapshot_receipt_and_waits_for_human(self) -> None:
        call = ProviderToolCall(
            "call-clarify",
            "create_clarification",
            '{"draft_version":1,"draft_sha256":"' + "0" * 64 + '","questions":[{"question":"Which grain should be used?","why_needed":"The authorized evidence does not settle the grain.","expected_answer_type":"text","suggested_owner_role":null,"related_definition_paths":[],"evidence_requested":[],"examples_or_options":[],"blocking_impact":"blocking","source_refs":[]}]}',
        )
        from contextox.models import CreateClarificationCall

        normalized = CreateClarificationCall(
            call_id=call.call_id,
            name=call.name,
            arguments={
                "draft_version": 1,
                "draft_sha256": "0" * 64,
                "questions": [
                    {
                        "question": "Which grain should be used?",
                        "why_needed": "The authorized evidence does not settle the grain.",
                        "expected_answer_type": "text",
                        "suggested_owner_role": None,
                        "related_definition_paths": [],
                        "evidence_requested": [],
                        "examples_or_options": [],
                        "blocking_impact": "blocking",
                        "source_refs": [],
                    }
                ],
            },
        )
        provider = FakeProvider([_completion("Need an owner decision.", (call,))])
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {call.call_id: _clarification_result(normalized)}
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.saved_outputs, ["Need an owner decision."])
        self.assertFalse(store.failures)
        self.assertFalse(any(event.event_type == "run_partial" for event in store.events))

    def test_run_missing_usage_is_blocked_without_tool_execution(self) -> None:
        completion = ProviderCompletion(
            completion_id="completion-4",
            content="",
            reasoning_content="hidden reasoning",
            tool_calls=(ProviderToolCall("call-list", "list_sources", "{}"),),
            finish_reason="tool_calls",
            usage=None,
        )
        provider = FakeProvider([completion])
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.executed_calls, [])
        self.assertEqual(store.failures[0][1:], ("blocked", "provider_usage_unknown"))
        self.assertEqual(store.receipts[0].status, "blocked")

    def test_public_model_delta_total_bytes_are_bounded(self) -> None:
        provider = FakeProvider([_completion("x" * (262144 + 1))])
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.failures[0][1:], ("blocked", "context_budget_exceeded"))
        self.assertEqual(store.receipts[0].status, "blocked")
        deltas = [event for event in store.events if event.event_type == "model_delta"]
        self.assertTrue(deltas)
        self.assertTrue(all(len(event.public_payload.content.encode("utf-8")) <= 4096 for event in deltas))
        self.assertEqual(store.saved_outputs, [])

    def test_natural_stop_fails_without_persisting_final_output(self) -> None:
        provider = FakeProvider([_completion("No terminal.")])
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.saved_outputs, [])
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_result_missing"))

    def test_cancelled_snapshot_and_external_cancel_do_not_call_provider_or_tools(self) -> None:
        cancelled_store = FakeStore(context=_context_snapshot(status="cancelled"))
        cancelled_provider = FakeProvider([])
        with patch.object(agent, "get_provider", return_value=cancelled_provider):
            agent.run_agent(cancelled_store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(cancelled_provider.calls, [])
        self.assertEqual(cancelled_store.mark_calls, 0)

        event = Event()
        event.set()
        queued_store = FakeStore(context=_context_snapshot())
        queued_provider = FakeProvider([])
        with patch.object(agent, "get_provider", return_value=queued_provider):
            agent.run_agent(queued_store, _id(1), _id(3), _id(4), event)
        self.assertEqual(queued_provider.calls, [])
        self.assertEqual(queued_store.mark_calls, 0)

    def test_provider_cancel_during_stream_records_cancelled_receipt_and_stops(self) -> None:
        event = Event()
        provider = FakeProvider([], cancel_event=event)
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), event)
        self.assertEqual(len(store.receipts), 1)
        self.assertEqual(store.receipts[0].status, "cancelled")
        self.assertEqual(store.receipts[0].error_code, "cancelled")
        self.assertEqual(store.failures, [])
        self.assertEqual(store.executed_calls, [])

    def test_nonrunning_mark_readback_prevents_starting_a_provider(self) -> None:
        class WaitingMarkStore(FakeStore):
            def mark_run_running(self, workspace_id, mission_id, run_id):
                self.mark_calls += 1
                return _context_snapshot(status="waiting_for_human").run

        store = WaitingMarkStore(context=_context_snapshot())
        provider = FakeProvider([])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(provider.calls, [])
        self.assertEqual(store.events, [])

    def test_failure_event_respects_cancelled_or_terminal_store_readback(self) -> None:
        class StateRaceStore(FakeStore):
            def __init__(self, returned_status, **kwargs):
                super().__init__(**kwargs)
                self.returned_status = returned_status

            def fail_run(self, workspace_id, mission_id, run_id, status, code):
                self.failures.append(("run", status, code))
                return _context_snapshot(status=self.returned_status).run

        for returned_status in ("cancelled", "waiting_for_human", "partial"):
            store = StateRaceStore(returned_status, context=_context_snapshot())
            agent._stop_run(store, _id(1), _id(3), _id(4), "failed", "provider_protocol_error")
            self.assertFalse(any(event.event_type == "run_failed" for event in store.events))

    def test_nonrunning_context_snapshot_stops_before_the_next_provider_turn(self) -> None:
        from contextox.models import ListSourcesCall

        class WaitingAfterFirstStore(FakeStore):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.waiting = _context_snapshot(status="waiting_for_human")

            def get_context_snapshot(self, workspace_id, mission_id, run_id):
                self.context_calls += 1
                return self.context if self.context_calls == 1 else self.waiting

        raw_call = ProviderToolCall("call-list", "list_sources", "{}")
        domain_call = ListSourcesCall(call_id="call-list", name="list_sources", arguments={})
        store = WaitingAfterFirstStore(context=_context_snapshot())
        store.tool_results = {raw_call.call_id: _tool_result(domain_call, ordinal=1)}
        provider = FakeProvider([_completion("Evidence.", (raw_call,))])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(store.failures, [])


if __name__ == "__main__":
    unittest.main()
