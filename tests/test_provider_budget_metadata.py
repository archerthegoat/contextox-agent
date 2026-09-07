"""Budget failure identity must survive the production child boundary."""
import json
import os
import unittest
from unittest.mock import patch

from contextox import provider as p


_ORIGINAL_CHILD = p._provider_child_main


def _budget_child(connection, packet):
    # Keep the production child's exception serialization and supervisor intact.
    stage = json.loads(json.loads(packet)["body"])["messages"][0]["content"]
    def fail(*args, **kwargs):
        raise p.ProviderContextBudgetError(stage=stage, used_bytes=1001, limit_bytes=1000)
    with patch.object(p.DeepSeekProvider, "_complete_request", fail):
        _ORIGINAL_CHILD(connection, packet)


class BudgetMetadataTests(unittest.TestCase):
    def test_supervised_budget_error_preserves_stage_and_numbers(self):
        for stage in ("sse_wire", "sse_event", "stream_content", "request_body", "http_headers", "ipc_result"):
            with self.subTest(stage=stage), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic"}), patch.object(p, "_provider_child_main", _budget_child):
                with self.assertRaises(p.ProviderContextBudgetError) as caught:
                    p.DeepSeekProvider().complete(
                        [{"role": "user", "content": stage}], stream=True, tools=[], user_id="ws-synthetic",
                        timeouts=p.ProviderTimeouts(connect_ms=1000, first_event_ms=1000, idle_ms=1000, total_ms=2000),
                    )
                self.assertEqual((caught.exception.stage, caught.exception.used_bytes, caught.exception.limit_bytes), (stage, 1001, 1000))

    def test_oversized_child_result_preserves_ipc_failure_identity(self):
        class Connection:
            def __init__(self):
                self.messages = []
            def send_bytes(self, raw):
                self.messages.append(raw)
        connection = Connection()
        child = p._ChildIpc(connection, "call")
        child.send("result", completion={"content": "x" * p.IPC_MAX_MESSAGE_BYTES})
        self.assertEqual(len(connection.messages), 1)
        message = p._decode_ipc_message(connection.messages[0], expected_call_id="call", expected_sequence=0,
                                        connected=True, request_started=True, terminal=False,
                                        max_context_bytes=p.MAX_CONTEXT_BYTES)
        error = p._provider_error_from_code(message["code"], budget=message["budget"])
        self.assertEqual(error.stage, "ipc_result")
        self.assertEqual(error.limit_bytes, p.IPC_MAX_MESSAGE_BYTES)

    def decode(self, budget, **extra):
        message = dict(call_id="call", sequence=1, event_type="error", code="context_budget_exceeded", usage=None, budget=budget, **extra)
        return p._decode_ipc_message(json.dumps(message).encode(), expected_call_id="call", expected_sequence=0,
                                     connected=False, request_started=False, terminal=False, max_context_bytes=p.MAX_CONTEXT_BYTES)

    def test_metadata_rejects_unknown_fields_stages_and_non_strict_numbers(self):
        valid = {"stage": "sse_wire", "used_bytes": 1001, "limit_bytes": 1000}
        self.decode(valid)
        self.decode(dict(stage=None, used_bytes=None, limit_bytes=None))
        invalid = [None, {}, {**valid, "private_payload": "no"}, {**valid, "stage": "made_up"}, {**valid, "stage": []}]
        for key in ("used_bytes", "limit_bytes"):
            invalid.extend({**valid, key: number} for number in (True, -1, 2**31, 1.5, "1000"))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(p._ProviderIpcProtocolError):
                self.decode(value)
        with self.assertRaises(p._ProviderIpcProtocolError):
            self.decode(valid, private_payload="no")

    def test_budget_metadata_cannot_be_attached_to_other_error(self):
        raw = json.dumps(dict(call_id="call", sequence=1, event_type="error", code="provider_protocol_error", usage=None,
                              budget=dict(stage="sse_wire", used_bytes=1001, limit_bytes=1000))).encode()
        with self.assertRaises(p._ProviderIpcProtocolError):
            p._decode_ipc_message(raw, expected_call_id="call", expected_sequence=0, connected=False,
                                  request_started=False, terminal=False, max_context_bytes=p.MAX_CONTEXT_BYTES)


if __name__ == "__main__":
    unittest.main()
