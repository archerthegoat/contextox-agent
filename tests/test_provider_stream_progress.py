"""Bounded stream observations survive failure without changing deadline semantics."""
import json
import multiprocessing
import os
import threading
import time
import unittest
from unittest.mock import patch

from contextox import provider as p
from test_provider import FakeResponse, FakeTransport, _event, _usage, _synthetic_call_id


_ORIGINAL_CHILD = p._provider_child_main
_PRIVATE = "synthetic-private-秘密"


def _chunk(delta=None, finish=None):
    return {"id": "synthetic", "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
            "usage": _usage() if finish else None}


def _run_blocked_child(connection, packet, after_done):
    class Response(FakeResponse):
        def iter_bytes(self):
            yield _event(_chunk({"reasoning_content": _PRIVATE}))
            if after_done:
                yield _event(_chunk(finish="stop")) + b"data: [DONE]\n\n"
            else:
                time.sleep(10)  # Parent must retain snapshot without child cleanup.

        def close(self):
            if after_done:
                time.sleep(10)

    def open_response(self, request, timeouts, started_at, **kwargs):
        kwargs["on_phase"]("connected")
        kwargs["on_phase"]("request_started")
        return Response()

    with patch.object(p.DeepSeekProvider, "_open", open_response):
        _ORIGINAL_CHILD(connection, packet)


def _blocked_stream_child(connection, packet):
    _run_blocked_child(connection, packet, False)


def _blocked_close_child(connection, packet):
    _run_blocked_child(connection, packet, True)


def _progress_loop(connection, packet, activity):
    ipc = p._ChildIpc(connection, _synthetic_call_id(packet))
    ipc.send("connected")
    ipc.send("request_started")
    if activity:
        ipc.send("activity")
    progress = p._StreamProgress(time.monotonic(), lambda snapshot: ipc.send("progress", snapshot=snapshot))
    while True:
        if activity == "continuous":
            ipc.send("activity")
        progress.wire(1)
        progress.emit(force=True)
        time.sleep(0.03)


def _progress_without_activity(connection, packet):
    _progress_loop(connection, packet, False)


def _progress_after_activity(connection, packet):
    _progress_loop(connection, packet, True)


def _progress_with_activity(connection, packet):
    _progress_loop(connection, packet, "continuous")


class StreamProgressTests(unittest.TestCase):
    def run_provider(self, provider, **kwargs):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": _PRIVATE}):
            return provider.complete([{"role": "user", "content": _PRIVATE}],
                                     stream=True, tools=[], user_id=_PRIVATE, **kwargs)

    def diagnostic(self, logs):
        items = [r.provider_stream_progress_diagnostic for r in logs.records
                 if hasattr(r, "provider_stream_progress_diagnostic")]
        self.assertEqual(len(items), 1)
        self.assertNotIn(_PRIVATE, json.dumps(items, ensure_ascii=False) + " ".join(logs.output))
        return items[0]

    def test_split_utf8_counts_and_end_markers_without_private_text(self):
        arguments = '{"值":"秘密"}'
        payload = (b": keepalive\n\n" + _event(_chunk({"reasoning_content": _PRIVATE}))
                   + _event(_chunk({"content": "答复", "tool_calls": [{"index": 0, "id": "c",
                       "type": "function", "function": {"name": "synthetic", "arguments": arguments}}]}))
                   + _event(_chunk(finish="tool_calls")) + b"data: [DONE]\n\n")
        transport = FakeTransport(FakeResponse(chunks=[payload[i:i + 1] for i in range(len(payload))]))
        with self.assertLogs("contextox.provider", level="INFO") as logs:
            result = self.run_provider(p.DeepSeekProvider(transport=transport))
        diagnostic = self.diagnostic(logs)
        snapshot = diagnostic["snapshot"]
        self.assertEqual(snapshot["wire_bytes"], len(payload))
        self.assertEqual(snapshot["valid_sse_events"], 3)
        self.assertEqual(snapshot["reasoning_bytes"], len(_PRIVATE.encode()))
        self.assertEqual(snapshot["content_bytes"], len("答复".encode()))
        self.assertEqual(snapshot["tool_argument_bytes"], len(arguments.encode()))
        self.assertTrue(snapshot["finish_reason_seen"] and snapshot["done_seen"])
        self.assertLessEqual(snapshot["first_wire_ms"], snapshot["first_valid_sse_ms"])
        self.assertEqual(result.tool_calls[0].arguments, arguments)
        self.assertIsNotNone(result.usage)

    def test_real_child_snapshot_survives_supervisor_kill(self):
        before = {c.pid for c in multiprocessing.active_children()}
        with patch.object(p, "_provider_child_main", _blocked_stream_child), \
                self.assertLogs("contextox.provider", level="INFO") as logs:
            with self.assertRaises(p.ProviderTimeoutUnknownError) as caught:
                self.run_provider(p.DeepSeekProvider(), timeouts=p.ProviderTimeouts(2000, 2000, 2000, 800))
        self.assertEqual(caught.exception.diagnostic["phase"], "total")
        self.assertIsNone(caught.exception.usage)
        snapshot = self.diagnostic(logs)["snapshot"]
        self.assertEqual(snapshot["valid_sse_events"], 1)
        self.assertEqual(snapshot["reasoning_bytes"], len(_PRIVATE.encode()))
        self.assertFalse(snapshot["finish_reason_seen"] or snapshot["done_seen"])
        self.assertEqual({c.pid for c in multiprocessing.active_children()}, before)

    def test_progress_does_not_extend_first_idle_or_total_deadlines(self):
        cases = [("first_response", _progress_without_activity, p.ProviderTimeouts(2000, 600, 2000, 3000)),
                 ("idle", _progress_after_activity, p.ProviderTimeouts(2000, 2000, 300, 3000)),
                 ("total", _progress_with_activity, p.ProviderTimeouts(2000, 2000, 2000, 700))]
        for phase, target, limits in cases:
            with self.subTest(phase=phase), patch.object(p, "_provider_child_main", target), \
                    self.assertLogs("contextox.provider", level="INFO") as logs:
                start = time.monotonic()
                with self.assertRaises(p.ProviderTimeoutUnknownError) as caught:
                    self.run_provider(p.DeepSeekProvider(), timeouts=limits)
                self.assertEqual(caught.exception.diagnostic["phase"], phase)
                self.assertLess(time.monotonic() - start, 1.6)
                self.assertGreater(self.diagnostic(logs)["snapshot"]["wire_bytes"], 0)

    def test_done_is_observed_before_blocked_response_close(self):
        with patch.object(p, "_provider_child_main", _blocked_close_child), \
                self.assertLogs("contextox.provider", level="INFO") as logs:
            with self.assertRaises(p.ProviderTimeoutUnknownError):
                self.run_provider(p.DeepSeekProvider(), timeouts=p.ProviderTimeouts(2000, 2000, 2000, 800))
        snapshot = self.diagnostic(logs)["snapshot"]
        self.assertTrue(snapshot["done_seen"] and snapshot["finish_reason_seen"])
        self.assertEqual(snapshot["valid_sse_events"], 2)

    def test_cancellation_still_wins_during_progress(self):
        cancel = threading.Event()
        timer = threading.Timer(0.5, cancel.set)
        before = {c.pid for c in multiprocessing.active_children()}
        with patch.object(p, "_provider_child_main", _progress_with_activity), \
                self.assertLogs("contextox.provider", level="INFO") as logs:
            timer.start()
            try:
                with self.assertRaises(p.ProviderCancelledError) as caught:
                    self.run_provider(p.DeepSeekProvider(), cancel_event=cancel,
                                      timeouts=p.ProviderTimeouts(2000, 2000, 2000, 3000))
            finally:
                timer.cancel()
                timer.join()
        self.assertEqual(caught.exception.code, "provider_cancelled_outcome_unknown")
        self.assertIsNotNone(self.diagnostic(logs)["snapshot"])
        self.assertEqual({c.pid for c in multiprocessing.active_children()}, before)

    def test_partial_or_invalid_stream_preserves_counts_and_error(self):
        for suffix, error in ((b"", p.ProviderStreamInterruptedError),
                              (b"data: not-json\n\n", p.ProviderProtocolError)):
            with self.subTest(error=error):
                payload = _event(_chunk({"content": _PRIVATE})) + suffix
                provider = p.DeepSeekProvider(transport=FakeTransport(FakeResponse(chunks=[payload])))
                with self.assertLogs("contextox.provider", level="INFO") as logs:
                    with self.assertRaises(error):
                        self.run_provider(provider)
                snapshot = self.diagnostic(logs)["snapshot"]
                self.assertEqual(snapshot["wire_bytes"], len(payload))
                self.assertEqual(snapshot["valid_sse_events"], 1)
                self.assertFalse(snapshot["done_seen"])

    def test_ipc_rejects_non_numeric_or_extra_progress_before_logging(self):
        valid = p._StreamProgress(time.monotonic(), None).values
        bad = [{**valid, "private": _PRIVATE}, {**valid, "wire_bytes": True},
               {**valid, "wire_bytes": -1}, {**valid, "wire_bytes": 2**31},
               {**valid, "first_wire_ms": _PRIVATE}, {**valid, "done_seen": 1}]
        for value in [valid, *bad]:
            raw = json.dumps({"call_id": "call", "sequence": 1, "event_type": "progress", "snapshot": value}).encode()
            kwargs = dict(expected_call_id="call", expected_sequence=0, connected=True,
                          request_started=True, terminal=False, max_context_bytes=p.MAX_CONTEXT_BYTES)
            if value is valid:
                p._decode_ipc_message(raw, **kwargs)
                with self.assertRaises(p._ProviderIpcProtocolError):
                    p._decode_ipc_message(raw, **{**kwargs, "request_started": False})
            else:
                with self.assertRaises(p._ProviderIpcProtocolError):
                    p._decode_ipc_message(raw, **kwargs)


if __name__ == "__main__":
    unittest.main()
