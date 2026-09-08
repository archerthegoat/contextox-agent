"""Safe timeout diagnostics must survive child IPC without changing outcomes."""
import json
import os
import unittest
from unittest.mock import patch

from contextox import provider as p
from test_provider import (SocketPairHTTPResponse, _child_send_activity_then_wait,
                           _child_wait_after_request_started)


_ORIGINAL_CHILD = p._provider_child_main
_DIAGNOSTIC = {"phase": "idle", "origin": "response_reader", "elapsed_ms": 80, "limit_ms": 60}


def _timeout_child(connection, packet):
    def fail(*args, **kwargs):
        kwargs["on_phase"]("connected")
        kwargs["on_phase"]("request_started")
        raise p.ProviderTimeoutUnknownError(**_DIAGNOSTIC)
    with patch.object(p.DeepSeekProvider, "_complete_request", fail):
        _ORIGINAL_CHILD(connection, packet)


class TimeoutMetadataTests(unittest.TestCase):
    def test_child_diagnostic_reaches_caller_with_one_safe_log_and_unknown_usage(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-private-marker"}), \
                patch.object(p, "_provider_child_main", _timeout_child), \
                self.assertLogs("contextox.provider", level="WARNING") as logs:
            with self.assertRaises(p.ProviderTimeoutUnknownError) as caught:
                p.DeepSeekProvider().complete(
                    [{"role": "user", "content": "synthetic-private-marker"}],
                    stream=False, tools=None, user_id="ws-synthetic-private-marker",
                    timeouts=p.ProviderTimeouts(2000, 2000, 2000, 3000),
                )
        self.assertEqual(caught.exception.diagnostic, _DIAGNOSTIC)
        self.assertEqual(caught.exception.code, "provider_timeout_unknown")
        self.assertEqual(caught.exception.run_status, "failed")
        self.assertIsNone(caught.exception.usage)
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].provider_timeout_diagnostic, {"mode": "nonstream", **_DIAGNOSTIC})
        self.assertNotIn("synthetic-private-marker", " ".join(logs.output))

    def test_supervisor_records_the_guard_that_fired(self):
        cases = (
            ("first_response", _child_wait_after_request_started, p.ProviderTimeouts(2000, 800, 2000, 3000), 800),
            ("idle", _child_send_activity_then_wait, p.ProviderTimeouts(2000, 2000, 250, 3000), 250),
            ("total", _child_wait_after_request_started, p.ProviderTimeouts(2000, 3000, 2000, 800), 800),
        )
        for phase, target, timeouts, limit in cases:
            with self.subTest(phase=phase), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic"}), \
                    patch.object(p, "_provider_child_main", target), \
                    self.assertLogs("contextox.provider", level="WARNING") as logs:
                with self.assertRaises(p.ProviderTimeoutUnknownError) as caught:
                    p.DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=False, tools=None, user_id="ws-synthetic", timeouts=timeouts,
                    )
                diagnostic = caught.exception.diagnostic
                self.assertEqual((diagnostic["phase"], diagnostic["origin"], diagnostic["limit_ms"]),
                                 (phase, "supervisor", limit))
                self.assertGreaterEqual(diagnostic["elapsed_ms"], limit)
                self.assertEqual(len(logs.records), 1)

    def test_response_reader_records_deadline_without_supervisor_race(self):
        cases = (
            ("first_response", b" \r\n", p.ProviderTimeouts(1000, 150, 80, 700)),
            ("idle", b" {", p.ProviderTimeouts(1000, 700, 150, 1000)),
            ("total", b" \r\n", p.ProviderTimeouts(1000, 700, 80, 150)),
        )
        for phase, prefix, timeouts in cases:
            with self.subTest(phase=phase):
                source = SocketPairHTTPResponse([(0, prefix)], keep_open=True,
                    headers=b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                response = source.start()
                try:
                    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic"}), \
                            self.assertLogs("contextox.provider", level="WARNING") as logs:
                        with self.assertRaises(p.ProviderTimeoutUnknownError) as caught:
                            p.DeepSeekProvider(transport=lambda request, timeout: response).complete(
                                [{"role": "user", "content": "synthetic"}], stream=False,
                                tools=None, user_id="ws-synthetic", timeouts=timeouts,
                            )
                    self.assertEqual(caught.exception.diagnostic["phase"], phase)
                    self.assertEqual(caught.exception.diagnostic["origin"], "response_reader")
                    self.assertEqual(len(logs.records), 1)
                finally:
                    source.close()
                self.assertFalse(source.thread.is_alive())

    def decode(self, **fields):
        message = {"call_id": "call", "sequence": 1, "event_type": "error",
                   "code": "provider_timeout_unknown", "usage": None, **fields}
        return p._decode_ipc_message(json.dumps(message).encode(), expected_call_id="call",
            expected_sequence=0, connected=True, request_started=True, terminal=False,
            max_context_bytes=p.MAX_CONTEXT_BYTES)

    def test_metadata_is_strict_and_old_shape_stays_unknown(self):
        self.decode()
        self.decode(timeout=None)
        self.decode(timeout=_DIAGNOSTIC)
        self.assertIsNone(p._provider_error_from_code("provider_timeout_unknown").diagnostic)
        invalid = [{}, [], {**_DIAGNOSTIC, "private_payload": "synthetic-private-marker"}]
        for key in ("phase", "origin"):
            invalid.extend({**_DIAGNOSTIC, key: value} for value in ("synthetic-private-marker", [], None))
        for key in ("elapsed_ms", "limit_ms"):
            invalid.extend({**_DIAGNOSTIC, key: value} for value in (True, -1, 2**31, 1.5, "80"))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(p._ProviderIpcProtocolError):
                self.decode(timeout=value)
        with self.assertRaises(p._ProviderIpcProtocolError):
            self.decode(timeout=_DIAGNOSTIC, private_payload="synthetic-private-marker")
        with self.assertRaises(p._ProviderIpcProtocolError):
            self.decode(code="provider_protocol_error", timeout=_DIAGNOSTIC)

    def test_unknown_or_mutated_diagnostic_never_echoes_private_text(self):
        errors = [p.ProviderTimeoutUnknownError(phase="synthetic-private-marker", origin="supervisor"),
                  p.ProviderTimeoutUnknownError(**_DIAGNOSTIC)]
        errors[1].diagnostic["phase"] = "synthetic-private-marker"
        for error in errors:
            with self.subTest(error=error.diagnostic), \
                    patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-private-marker"}), \
                    patch.object(p.DeepSeekProvider, "_complete_request", side_effect=error), \
                    self.assertLogs("contextox.provider", level="WARNING") as logs:
                with self.assertRaises(p.ProviderTimeoutUnknownError):
                    p.DeepSeekProvider(transport=object()).complete(
                        [{"role": "user", "content": "synthetic-private-marker"}],
                        stream=False, tools=None, user_id="ws-synthetic-private-marker",
                    )
                self.assertEqual(len(logs.records), 1)
                self.assertEqual(logs.records[0].provider_timeout_diagnostic["phase"], "unknown")
                self.assertNotIn("synthetic-private-marker", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
