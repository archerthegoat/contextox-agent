"""Explicit effort survives HTTP supervision and persisted receipt readback."""
from contextlib import closing
import json
import os
import sqlite3
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from contextox import agent, provider as p
from contextox.models import ProviderConfigSnapshot
from contextox.store import WorkspaceStore, WorkspaceStoreUnavailableError
from test_provider import FakeResponse, FakeTransport, _event, _start_loopback_server, _usage


def _response(content):
    return json.dumps({"id": "synthetic", "choices": [{"index": 0,
        "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": _usage()}).encode()


class ProviderEffortTests(unittest.TestCase):
    def test_default_factory_remains_high_and_unknown_efforts_fail_closed(self):
        self.assertEqual(agent.get_provider().config.reasoning_effort, "high")
        legacy = {"endpoint_id": "deepseek_chat_completions", "model": "deepseek-v4-flash",
                  "thinking": "enabled", "reasoning_effort": "high"}
        self.assertEqual(ProviderConfigSnapshot.model_validate(legacy).model_dump(), legacy)
        for effort in ("medium", "xhigh", "", None, True, [], {}):
            with self.subTest(effort=effort):
                with self.assertRaises(ValueError):
                    p.DeepSeekProvider(reasoning_effort=effort)
                with self.assertRaises(ValueError):
                    ProviderConfigSnapshot.model_validate(legacy | {"reasoning_effort": effort})

    def test_supervised_child_sends_exact_effort_in_both_response_modes(self):
        for effort in ("low", "high", "max"):
            for stream in (False, True):
                with self.subTest(effort=effort, stream=stream):
                    requests = []
                    body = (_event({"id": "synthetic", "choices": [{"index": 0,
                        "delta": {"content": "synthetic answer"}, "finish_reason": "stop"}],
                        "usage": _usage()}) + b"data: [DONE]\n\n") if stream else _response("synthetic answer")

                    def serve(handler):
                        requests.append(json.loads(handler.rfile.read(int(handler.headers["Content-Length"]))))
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream" if stream else "application/json")
                        handler.send_header("Content-Length", str(len(body)))
                        handler.end_headers()
                        handler.wfile.write(body)

                    server, thread = _start_loopback_server(serve)
                    try:
                        with patch.object(p, "DEEPSEEK_ENDPOINT", f"http://127.0.0.1:{server.server_port}/chat/completions"), \
                                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic"}):
                            provider = p.DeepSeekProvider(reasoning_effort=effort)
                            answer = provider.complete([{"role": "user", "content": "synthetic"}],
                                stream=stream, tools=[] if stream else None, user_id="ws-synthetic",
                                timeouts=p.ProviderTimeouts(3000, 3000, 3000, 5000))
                        self.assertEqual(answer.content, "synthetic answer")
                        self.assertEqual(len(requests), 1)
                        self.assertEqual(requests[0]["reasoning_effort"], effort)
                        self.assertEqual(provider.config.reasoning_effort, effort)
                    finally:
                        thread.join(2)
                        server.server_close()
                    self.assertFalse(thread.is_alive())

    def test_success_and_failure_receipts_keep_actual_effort_after_restart(self):
        candidate = json.dumps({"title": "Synthetic", "goal": "Define synthetic customer ID",
            "completion_criteria": ["Return a candidate definition"], "scope_notes": []})
        for effort in ("low", "high", "max"):
            for success in (True, False):
                with self.subTest(effort=effort, success=success), \
                        tempfile.TemporaryDirectory(prefix="contextox-effort-", dir="/private/tmp") as directory:
                    store = WorkspaceStore.open(directory)
                    workspace = store.create_workspace("Synthetic").workspace_id
                    attempt = store.create_mission_draft_attempt(workspace, "Define synthetic customer ID")
                    transport = FakeTransport(FakeResponse(body=_response(candidate)) if success
                                              else FakeResponse(status=503))
                    provider = p.DeepSeekProvider(reasoning_effort=effort, transport=transport)
                    with patch.object(agent, "get_provider", return_value=provider), \
                            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic"}):
                        agent.generate_mission_draft(store, workspace, attempt.attempt_id, Event())
                        agent.generate_mission_draft(store, workspace, attempt.attempt_id, Event())
                    self.assertEqual(len(transport.requests), 1)
                    self.assertEqual(json.loads(transport.requests[0].data)["reasoning_effort"], effort)
                    saved = store.get_mission_draft_attempt(workspace, attempt.attempt_id)
                    self.assertEqual(saved.status, "ready" if success else "failed")
                    restarted = WorkspaceStore.open(directory)
                    self.assertEqual(restarted.get_mission_draft_attempt(workspace, attempt.attempt_id), saved)
                    with closing(sqlite3.connect(store.db_path)) as connection:
                        rows = connection.execute("SELECT config_json, status FROM provider_receipts").fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual((json.loads(rows[0][0])["reasoning_effort"], rows[0][1]),
                                     (effort, "succeeded" if success else "failed"))
                    # Unknown persisted configuration must fail through the public read seam.
                    invalid = json.loads(rows[0][0]) | {"reasoning_effort": "unsupported"}
                    with closing(sqlite3.connect(store.db_path)) as connection, connection:
                        connection.execute("UPDATE provider_receipts SET config_json=?", (json.dumps(invalid),))
                    with self.assertRaises(WorkspaceStoreUnavailableError):
                        restarted.get_mission_draft_attempt(workspace, attempt.attempt_id)


if __name__ == "__main__":
    unittest.main()
