"""The discussion public seam is bounded, tool-free and never a task executor."""
import json
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from contextox import discussion
from contextox.models import ProviderConfigSnapshot
from contextox.provider import ProviderCompletion, ProviderUsage, ProviderError, ProviderToolCall


class FakeStore:
    def __init__(self):
        self.turn = SimpleNamespace(workspace_id=str(uuid4()), conversation_id=str(uuid4()),
            turn_id=str(uuid4()), request_sha256="a" * 64, status="queued", mission_id=None,
            config=ProviderConfigSnapshot(endpoint_id="deepseek_chat_completions",
                model="deepseek-v4-flash", thinking="disabled", reasoning_effort=None))
        self.context = SimpleNamespace(model_dump_json=lambda: '{"input":"解释资料"}')
        self.saved = None

    def get_discussion_turn(self, *_):
        return self.turn

    def claim_discussion_turn(self, *_):
        assert self.turn.status == "queued"
        self.turn.status = "running"
        return self.turn

    def discussion_context(self, *_):
        return self.context

    def finish_discussion_turn(self, ws, cid, tid, output, receipt):
        self.saved = (output, receipt)
        self.turn.status = "succeeded"
        return self.turn

    def fail_discussion_turn(self, ws, cid, tid, status, code, receipt=None):
        self.saved = (code, receipt)
        self.turn.status = status
        return self.turn


class FakeProvider:
    config = ProviderConfigSnapshot(endpoint_id="deepseek_chat_completions",
        model="deepseek-v4-flash", thinking="disabled", reasoning_effort=None)

    def __init__(self, output=None, usage=True):
        self.output = output or {"public_reply": "资料含义仍需核对", "next_action": "discuss"}
        self.usage = ProviderUsage(20, 10) if usage else None
        self.calls = []
        self.tools = ()
        self.finish_reason = "stop"
        self.error = None

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return ProviderCompletion("synthetic", json.dumps(self.output), "private reasoning omitted",
                                  self.tools, self.finish_reason, self.usage)


class DiscussionTests(unittest.TestCase):
    def run_turn(self, store, provider, cancel=None):
        with patch("contextox.agent.get_provider", return_value=provider):
            turn = store.turn
            return discussion.run_discussion(store, turn.workspace_id, turn.conversation_id,
                turn.turn_id, cancel or Event(), agent_profile="demo-fast")

    def test_single_bounded_tool_free_call_and_replay_no_call(self):
        store, provider = FakeStore(), FakeProvider()
        self.run_turn(store, provider)
        self.assertEqual(store.turn.status, "succeeded")
        self.assertEqual(store.saved[1].usage_status, "known")
        _, kwargs = provider.calls[0]
        self.assertEqual(kwargs["max_tokens"], 4096)
        self.assertLessEqual(kwargs["timeouts"].total_ms, 70000)
        self.assertFalse(kwargs["stream"])
        self.assertIsNone(kwargs["tools"])
        self.assertEqual(kwargs["max_context_bytes"], 65536)
        self.assertNotIn("private reasoning", store.saved[0].model_dump_json())
        self.run_turn(store, provider)
        self.assertEqual(len(provider.calls), 1)

    def test_missing_usage_cannot_authorize_start(self):
        store = FakeStore()
        provider = FakeProvider({"public_reply": "开始", "next_action": "start_task"}, usage=False)
        self.run_turn(store, provider)
        self.assertEqual(store.turn.status, "failed")
        self.assertEqual(store.saved[0], "provider_usage_missing")

    def test_waiting_discussion_never_unlocks_execution(self):
        store, provider = FakeStore(), FakeProvider({"public_reply": "开始", "next_action": "start_task"})
        store.turn.mission_id = str(uuid4())
        self.run_turn(store, provider)
        self.assertEqual(store.saved[0], "discussion_execution_not_allowed")

    def test_cancel_and_oversize_before_dispatch(self):
        store, provider, cancel = FakeStore(), FakeProvider(), Event()
        cancel.set()
        self.run_turn(store, provider, cancel)
        self.assertEqual(store.turn.status, "cancelled")
        self.assertFalse(provider.calls)
        store = FakeStore()
        store.context.model_dump_json = lambda: '"' + "x" * 65536 + '"'
        self.run_turn(store, provider)
        self.assertEqual(store.saved[0], "context_budget_exceeded")
        self.assertFalse(provider.calls)

    def test_rejects_tool_calls_bad_schema_and_unknown_outcome_without_retry(self):
        for mode in ("tool", "schema", "unknown"):
            with self.subTest(mode=mode):
                store, provider = FakeStore(), FakeProvider()
                if mode == "tool":
                    provider.tools = (ProviderToolCall("fake", "approve", "{}"),)
                elif mode == "schema":
                    provider.output = {"public_reply": "candidate", "invented_key": "not allowed"}
                else:
                    provider.error = ProviderError("provider_timeout_unknown", "failed")
                self.run_turn(store, provider)
                self.assertEqual(store.turn.status, "failed")
                self.assertEqual(len(provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
