"""Continuous conversation HTTP regression with synthetic Provider responses."""
import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from contextox.api import create_app
from test_api import _asgi_request
from test_discussion import FakeProvider
from test_runtime import InlineThread
from contextox.models import SourceIdentity
from contextox.provider import ProviderCompletion, ProviderUsage


class ConversationApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="contextox-conversation-api-", dir="/private/tmp")
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(data_dir=__import__("pathlib").Path(self.directory.name), migrate_conversations=True)
        self.runtime = self.app.state.path2_runtime
        self.assertIsNotNone(self.runtime)
        self.runtime._thread_factory = InlineThread
        self.addCleanup(self.runtime.shutdown)
        self.ws = self.app.state.workspace_store.create_workspace("Synthetic conversation").workspace_id
        self.base = f"/api/workspaces/{self.ws}/conversations"

    def call(self, method, path, payload=None):
        code, body = asyncio.run(_asgi_request(self.app, method, path,
            b"" if payload is None else json.dumps(payload).encode()))
        return code, json.loads(body)

    def conversation(self):
        request = {"client_request_id": str(uuid4()), "title": "合成讨论"}
        code, conv = self.call("POST", self.base, request)
        self.assertEqual(code, 201, conv)
        self.assertEqual(self.call("POST", self.base, request), (200, conv))
        return conv

    def test_discuss_refresh_and_replay_remain_in_one_conversation(self):
        conv = self.conversation()
        path = self.base + "/" + conv["conversation_id"]
        request = {"client_request_id": str(uuid4()), "expected_state_version": conv["state_version"],
                   "content": "先聊聊订单口径", "provider_send_confirmed": True, "source_refs": []}
        provider = FakeProvider()
        with patch("contextox.agent.get_provider", return_value=provider):
            code, sent = self.call("POST", path + "/messages", request)
            self.assertEqual(code, 202, sent)
            code, result = self.call("GET", path + "/submissions/" + request["client_request_id"])
            self.assertEqual(code, 200, result)
            self.assertEqual(result["discussion_turn"]["status"], "succeeded")
            self.assertIsNone(result["conversation"]["mission_id"])
            code, replay = self.call("POST", path + "/messages", request)
            self.assertEqual(code, 200, replay)
            self.assertEqual(len(provider.calls), 1)
            code, page = self.call("GET", path + "/messages")
            self.assertEqual(code, 200, page)
            self.assertEqual(len(page["items"]), 2)
            self.assertEqual({m["role"] for m in page["items"]}, {"user", "assistant"})
            code, refreshed = self.call("GET", path)
            self.assertEqual(code, 200, refreshed)
            request2 = {**request, "client_request_id": str(uuid4()),
                        "expected_state_version": refreshed["state_version"], "content": "为什么需要退款规则？"}
            self.assertEqual(self.call("POST", path + "/messages", request2)[0], 202)
            self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.app.state.workspace_store.list_missions(self.ws), [])

    def test_changed_payload_stale_version_and_workspace_mismatch_are_rejected(self):
        conv = self.conversation()
        path = self.base + "/" + conv["conversation_id"]
        payload = {"client_request_id": str(uuid4()), "expected_state_version": 1,
                   "content": "先聊聊", "provider_send_confirmed": True}
        with patch("contextox.agent.get_provider", return_value=FakeProvider()):
            self.assertEqual(self.call("POST", path + "/messages", payload)[0], 202)
            code, body = self.call("POST", path + "/messages", {**payload, "content": "被修改的消息"})
            self.assertEqual(code, 409, body)
            self.assertEqual(body["code"], "idempotency_conflict")
            self.assertEqual(self.call("POST", path + "/messages", {**payload, "client_request_id": str(uuid4())})[0], 409)
        other = self.app.state.workspace_store.create_workspace("Other").workspace_id
        self.assertEqual(self.call("GET", path.replace(self.ws, other))[0], 404)

    def test_unknown_handoff_get_recovers_start_evidence_without_writing_or_dispatch(self):
        from test_conversation_handoff import HandoffTests
        from contextox import conversation_handoff as handoff
        fixture = HandoffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.app.state.workspace_store = fixture.store
        payload = fixture.payload()
        receipt, _ = fixture.submit(payload)
        handoff.claim_analysis(fixture.store, fixture.ws, fixture.cid, payload.client_request_id)
        sent, _ = fixture.store.send_task_message(fixture.ws, fixture.mid, receipt.send_request)
        fixture.store.mark_run_running(fixture.ws, fixture.mid, sent.run.run_id)
        fixture.store.fail_run(fixture.ws, fixture.mid, sent.run.run_id, "failed", "synthetic_after_start")
        unknown = handoff.record_analysis(fixture.store, fixture.ws, fixture.cid, payload.client_request_id,
            state="unknown", expected_analysis_state="claimed", run_id=sent.run.run_id,
            error_code="state_write_outcome_unknown")
        url = f"/api/workspaces/{fixture.ws}/conversations/{fixture.cid}/handoffs/{payload.client_request_id}"
        with patch("contextox.agent.run_agent") as dispatch:
            code, result = self.call("GET", url)
            self.assertEqual(code, 200, result)
            self.assertTrue(result["analysis_started"])
            self.assertEqual(result["run_id"], sent.run.run_id)
            self.assertIsNone(result["error_code"])
            dispatch.assert_not_called()
        self.assertEqual(handoff.read(fixture.store, fixture.ws, fixture.cid, payload.client_request_id), unknown)

    def test_explicit_instruction_creates_one_task_and_run_without_a_fake_attempt(self):
        conv = self.conversation()
        store = self.app.state.workspace_store
        revision, _ = store.import_source_revision(self.ws, "synthetic.csv", "text/csv",
                                                    b"region,amount\nEast,20\nWest,30\n")
        source = SourceIdentity.model_validate(revision.model_dump(include=set(SourceIdentity.model_fields)))
        path = self.base + "/" + conv["conversation_id"]
        payload = {"client_request_id": str(uuid4()), "expected_state_version": conv["state_version"],
            "content": "我想按地区统计订单金额。", "source_refs": [source.model_dump(mode="json")],
            "provider_send_confirmed": True}

        class StartProvider(FakeProvider):
            def complete(self, messages, **kwargs):
                self.calls.append((messages, kwargs))
                item = json.loads(messages[1]["content"])["input"]
                output = {"public_reply": "开始梳理资料中的金额。", "next_action": "start_task",
                    "title": "金额定义", "goal": {"text": item["content"],
                        "message_refs": [{"message_id": item["message_id"], "sha256": item["sha256"]}]}}
                return ProviderCompletion("synthetic", json.dumps(output), "", (), "stop", ProviderUsage(20, 20))

        def fake_run(db, ws, mid, rid, cancel, **kwargs):
            db.fail_run(ws, mid, rid, "failed", "synthetic_stopped")

        provider = StartProvider()
        with patch("contextox.agent.get_provider", return_value=provider), \
             patch("contextox.agent.run_agent", side_effect=fake_run) as run:
            code, sent = self.call("POST", path + "/messages", payload)
            self.assertEqual(code, 202, sent)
            code, result = self.call("GET", path + "/submissions/" + payload["client_request_id"])
            self.assertEqual(code, 200, result)
            self.assertIsNotNone(result["run"], result)
            self.assertEqual(self.call("POST", path + "/messages", payload)[0], 200)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(len(provider.calls), 1)
        missions = store.list_missions(self.ws)
        self.assertEqual(len(missions), 1)
        self.assertIsNone(missions[0].original_attempt_id)
        self.assertEqual(missions[0].conversation_origin.conversation_id, conv["conversation_id"])


if __name__ == "__main__":
    unittest.main()
