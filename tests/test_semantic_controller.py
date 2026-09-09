import json
import unittest
from threading import Event

import test_agent as fixtures

from contextox.model_tools import ContextPlanV1
from contextox.provider import ProviderCompletion, ProviderUsage
from contextox.semantic_controller import (
    SEMANTIC_CONTEXT_MAX_BYTES,
    SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS,
    SemanticProposalFailure,
    build_context_plan,
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
    def test_one_high_json_request_has_no_tool_schema(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True) as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            snapshot = store.get_context_snapshot(ws, mission.mission_id, run.run_id)
            plan, _ = build_context_plan(snapshot, store)
            evidence = plan.sources[0]["profiles"][plan.sources[0]["tables"][0]["table_id"]][
                "evidence_handles"
            ]
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


if __name__ == "__main__":
    unittest.main()
