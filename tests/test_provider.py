import json
import os
import socket
import time
import unittest
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from unittest.mock import patch

from contextox.provider import (
    DEEPSEEK_ENDPOINT,
    DeepSeekProvider,
    ProviderAuthError,
    ProviderCancelledError,
    ProviderContextBudgetError,
    ProviderProtocolError,
    ProviderStreamInterruptedError,
    ProviderTimeoutUnknownError,
    ProviderTimeouts,
    ProviderUnavailableError,
    ProviderUsage,
)


class FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"", chunks: list[bytes] | None = None) -> None:
        self.status = status
        self.body = body
        self.chunks = chunks or []
        self.closed = False
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size == -1:
            return self.body
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def iter_bytes(self):
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


class ReadPathResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.status = 200
        self.chunks = list(chunks)
        self.read_sizes: list[int] = []
        self.closed = False

    def read1(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class TimeoutReadResponse:
    status = 200

    def __init__(self, cancel_event: Event | None = None) -> None:
        self.cancel_event = cancel_event
        self.read_calls = 0
        self.closed = False

    def read1(self, size: int) -> bytes:
        self.read_calls += 1
        if self.cancel_event is not None:
            self.cancel_event.set()
        raise socket.timeout()

    def close(self) -> None:
        self.closed = True


class SocketPairHTTPResponse:
    def __init__(
        self,
        chunks: list[tuple[float, bytes]],
        *,
        headers: bytes,
        keep_open: bool = False,
    ) -> None:
        self.client, self.server = socket.socketpair()
        self.response = HTTPResponse(self.client)
        self.chunks = list(chunks)
        self.headers = headers
        self.keep_open = keep_open
        self.done_sent = Event()
        self.release = Event()
        self.errors: list[str] = []
        self.thread = Thread(target=self._serve)

    def _serve(self) -> None:
        try:
            self.server.sendall(self.headers)
            for delay, chunk in self.chunks:
                if delay:
                    time.sleep(delay)
                self.server.sendall(chunk)
            self.done_sent.set()
            if self.keep_open:
                self.release.wait(2.0)
        except OSError as exc:
            self.errors.append(repr(exc))
        finally:
            self.server.close()

    def start(self) -> HTTPResponse:
        self.thread.start()
        self.response.begin()
        return self.response

    def close(self) -> None:
        self.release.set()
        self.response.close()
        self.thread.join(2.0)
        self.client.close()


def _start_loopback_server(callback):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            callback(self)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1.0
    thread = Thread(target=server.handle_request)
    thread.start()
    return server, thread


class FakeTransport:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests = []
        self.timeouts = []

    def open(self, request, timeout: float):
        self.requests.append(request)
        self.timeouts.append(timeout)
        return self.response


def _usage() -> dict[str, int]:
    return {
        "prompt_tokens": 7,
        "completion_tokens": 4,
        "prompt_cache_hit_tokens": 2,
        "prompt_cache_miss_tokens": 5,
        "total_tokens": 11,
    }


def _event(payload: object) -> bytes:
    return b"data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n\n"


class ProviderTests(unittest.TestCase):
    def test_nonstream_payload_is_fixed_and_has_no_tools(self) -> None:
        response = FakeResponse(
            body=json.dumps(
                {
                    "id": "completion-1",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"title":"Draft","goal":"Goal","completion_criteria":["Done"],"scope_notes":[]}',
                            },
                        }
                    ],
                    "usage": _usage(),
                }
            ).encode("utf-8")
        )
        transport = FakeTransport(response)
        provider = DeepSeekProvider(transport=transport)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            completion = provider.complete(
                [
                    {"role": "system", "content": "P0"},
                    {"role": "user", "content": "raw input"},
                ],
                stream=False,
                tools=None,
                user_id="ws-opaque",
                cancel_event=Event(),
            )

        request = transport.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, DEEPSEEK_ENDPOINT)
        self.assertNotIn("test-secret", request.data.decode("utf-8"))
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(completion.content[:10], '{"title":"')
        self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
        self.assertTrue(response.closed)

    def test_stream_parser_handles_split_sse_keepalive_reasoning_tool_and_usage(self) -> None:
        stream = b"".join(
            [
                b": keep-alive\n\n",
                _event(
                    {
                        "id": "completion-2",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "开",
                                    "reasoning_content": "思",
                                },
                                "finish_reason": None,
                            }
                        ],
                        "usage": None,
                    }
                ),
                _event(
                    {
                        "id": "completion-2",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {"name": "list_", "arguments": "{"},
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                        "usage": None,
                    }
                ),
                _event(
                    {
                        "id": "completion-2",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"name": "sources", "arguments": "}"},
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                        "usage": None,
                    }
                ),
                _event(
                    {
                        "id": "completion-2",
                        "choices": [
                            {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                        ],
                        "usage": _usage(),
                    }
                ),
                b"data: [DONE]\n\n",
            ]
        )
        split_at = stream.index("开".encode("utf-8")) + 1
        response = FakeResponse(chunks=[stream[:split_at], stream[split_at:split_at + 1], stream[split_at + 1 :]])
        transport = FakeTransport(response)
        content_deltas: list[str] = []
        provider = DeepSeekProvider(transport=transport)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            completion = provider.complete(
                [{"role": "system", "content": "P0"}],
                stream=True,
                tools=[{"type": "function", "function": {"name": "list_sources"}}],
                user_id="ws-opaque",
                cancel_event=Event(),
                on_content=content_deltas.append,
            )

        self.assertEqual(completion.completion_id, "completion-2")
        self.assertEqual(completion.content, "开")
        self.assertEqual(completion.reasoning_content, "思")
        self.assertEqual(completion.finish_reason, "tool_calls")
        self.assertEqual(completion.tool_calls[0].call_id, "call-1")
        self.assertEqual(completion.tool_calls[0].name, "list_sources")
        self.assertEqual(completion.tool_calls[0].arguments, "{}")
        self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
        self.assertEqual(content_deltas, ["开"])
        self.assertTrue(response.closed)

    def test_stream_without_done_is_interrupted_and_malformed_chunk_is_protocol_error(self) -> None:
        incomplete = _event(
            {
                "id": "completion-3",
                "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}],
                "usage": None,
            }
        )
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            with self.assertRaises(ProviderStreamInterruptedError):
                DeepSeekProvider(transport=FakeTransport(FakeResponse(chunks=[incomplete]))).complete(
                    [{"role": "system", "content": "P0"}],
                    stream=True,
                    tools=[],
                    user_id="ws-opaque",
                )

            malformed = FakeResponse(chunks=[b"data: {not-json}\n\n"])
            with self.assertRaises(ProviderProtocolError):
                DeepSeekProvider(transport=FakeTransport(malformed)).complete(
                    [{"role": "system", "content": "P0"}],
                    stream=True,
                    tools=[],
                    user_id="ws-opaque",
                )

    def test_missing_usage_is_returned_for_caller_to_block_and_http_errors_are_bounded(self) -> None:
        missing_usage = FakeResponse(
            body=json.dumps(
                {
                    "id": "completion-4",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "{}"},
                        }
                    ],
                }
            ).encode()
        )
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            completion = DeepSeekProvider(transport=FakeTransport(missing_usage)).complete(
                [{"role": "system", "content": "P0"}],
                stream=False,
                tools=None,
                user_id="ws-opaque",
            )
            self.assertIsNone(completion.usage)

            with self.assertRaises(ProviderAuthError):
                DeepSeekProvider(
                    transport=FakeTransport(FakeResponse(status=401))
                ).complete(
                    [{"role": "system", "content": "P0"}],
                    stream=False,
                    tools=None,
                    user_id="ws-opaque",
                )

    def test_context_limit_and_pre_cancel_do_not_send(self) -> None:
        transport = FakeTransport(FakeResponse(body=b"{}"))
        provider = DeepSeekProvider(transport=transport)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            with self.assertRaises(ProviderContextBudgetError):
                provider.complete(
                    [{"role": "user", "content": "x" * 100}],
                    stream=False,
                    tools=None,
                    user_id="ws-opaque",
                    max_context_bytes=10,
                )
            cancel_event = Event()
            cancel_event.set()
            with self.assertRaises(Exception) as context:
                provider.complete(
                    [{"role": "user", "content": "x"}],
                    stream=False,
                    tools=None,
                    user_id="ws-opaque",
                    cancel_event=cancel_event,
                )
            self.assertEqual(getattr(context.exception, "code", None), "cancelled")
        self.assertEqual(transport.requests, [])

    def test_read1_path_emits_small_sse_events_and_stops_after_done(self) -> None:
        event = _event(
            {
                "id": "completion-read1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "x"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": _usage(),
            }
        )
        split = len(event) // 2
        response = ReadPathResponse(
            [b": keepalive\n\n", event[:split], event[split:], b"data: [DONE]\n\n", b"must-not-read"]
        )
        provider = DeepSeekProvider(transport=lambda request, timeout: response)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            completion = provider.complete(
                [{"role": "user", "content": "x"}],
                stream=True,
                tools=[],
                user_id="ws-opaque",
            )

        self.assertEqual(completion.content, "x")
        self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
        self.assertEqual(response.read_sizes, [1024, 1024, 1024, 1024])
        self.assertTrue(response.closed)

    def test_read_path_rechecks_cancel_and_deadline_between_socket_reads(self) -> None:
        cancelled = Event()
        cancel_response = TimeoutReadResponse(cancel_event=cancelled)
        provider = DeepSeekProvider(transport=lambda request, timeout: cancel_response)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            with self.assertRaises(ProviderCancelledError):
                provider.complete(
                    [{"role": "user", "content": "x"}],
                    stream=True,
                    tools=[],
                    user_id="ws-opaque",
                    cancel_event=cancelled,
                )
        self.assertTrue(cancel_response.closed)

        deadline_response = TimeoutReadResponse()
        provider = DeepSeekProvider(transport=lambda request, timeout: deadline_response)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            with self.assertRaises(ProviderTimeoutUnknownError):
                provider.complete(
                    [{"role": "user", "content": "x"}],
                    stream=True,
                    tools=[],
                    user_id="ws-opaque",
                    timeouts=ProviderTimeouts(
                        connect_ms=1000,
                        first_event_ms=1000,
                        idle_ms=1000,
                        total_ms=5,
                    ),
                )
        self.assertTrue(deadline_response.closed)

    def test_nonstream_and_sse_receive_buffers_are_bounded(self) -> None:
        body_response = ReadPathResponse([b"x" * 1024])
        provider = DeepSeekProvider(transport=lambda request, timeout: body_response)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            with self.assertRaises(ProviderContextBudgetError):
                provider.complete(
                    [{"role": "user", "content": "x"}],
                    stream=False,
                    tools=None,
                    user_id="ws-opaque",
                    max_context_bytes=512,
                )
        self.assertEqual(body_response.read_sizes, [1024])
        self.assertTrue(body_response.closed)

        sse_response = ReadPathResponse([b"x" * 1024])
        provider = DeepSeekProvider(transport=lambda request, timeout: sse_response)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            with self.assertRaises(ProviderContextBudgetError):
                provider.complete(
                    [{"role": "user", "content": "x"}],
                    stream=True,
                    tools=[],
                    user_id="ws-opaque",
                    max_context_bytes=512,
                )
        self.assertEqual(sse_response.read_sizes, [1024])
        self.assertTrue(sse_response.closed)

    def test_real_httpresponse_read_path_allows_delayed_sse_and_stops_after_done(self) -> None:
        first = _event(
            {
                "id": "socketpair-completion",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "hello"},
                        "finish_reason": None,
                    }
                ],
                "usage": None,
            }
        )
        final = _event(
            {
                "id": "socketpair-completion",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": _usage(),
            }
        )
        source = SocketPairHTTPResponse(
            [(0.2, first), (0.2, final), (0.0, b"data: [DONE]\n\n")],
            headers=b"HTTP/1.0 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n",
            keep_open=True,
        )
        response = source.start()
        content_deltas: list[str] = []
        try:
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                completion = DeepSeekProvider(transport=lambda request, timeout: response).complete(
                    [{"role": "user", "content": "synthetic"}],
                    stream=True,
                    tools=[],
                    user_id="ws-opaque",
                    timeouts=ProviderTimeouts(
                        connect_ms=1000,
                        first_event_ms=1000,
                        idle_ms=1000,
                        total_ms=3000,
                    ),
                    on_content=content_deltas.append,
                )
            self.assertEqual(completion.content, "hello")
            self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
            self.assertEqual(content_deltas, ["hello"])
            self.assertTrue(source.done_sent.wait(0.5))
        finally:
            source.close()
        self.assertFalse(source.thread.is_alive())

    def test_real_httpresponse_nonstream_read_path_allows_delayed_chunks(self) -> None:
        body = json.dumps(
            {
                "id": "socketpair-nonstream",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
                "usage": _usage(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        split = len(body) // 2
        source = SocketPairHTTPResponse(
            [(0.2, body[:split]), (0.2, body[split:])],
            headers=(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode("ascii")
                + b"\r\nConnection: close\r\n\r\n"
            ),
        )
        response = source.start()
        try:
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                completion = DeepSeekProvider(transport=lambda request, timeout: response).complete(
                    [{"role": "user", "content": "synthetic"}],
                    stream=False,
                    tools=None,
                    user_id="ws-opaque",
                    timeouts=ProviderTimeouts(
                        connect_ms=1000,
                        first_event_ms=1000,
                        idle_ms=1000,
                        total_ms=3000,
                    ),
                )
            self.assertEqual(completion.content, "{}")
            self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
        finally:
            source.close()
        self.assertFalse(source.thread.is_alive())

    def test_real_httpresponse_first_and_idle_deadlines_bound_partial_streams(self) -> None:
        cases = (
            (
                [(0.0, b": keep-alive\n\n")],
                ProviderTimeouts(connect_ms=1000, first_event_ms=150, idle_ms=500, total_ms=700),
            ),
            (
                [(0.0, b": keep-alive\n\n")],
                ProviderTimeouts(connect_ms=1000, first_event_ms=700, idle_ms=700, total_ms=150),
            ),
            (
                [(0.0, b'data: {"id":"partial"')],
                ProviderTimeouts(connect_ms=1000, first_event_ms=150, idle_ms=500, total_ms=700),
            ),
            (
                [
                    (
                        0.0,
                        _event(
                            {
                                "id": "socketpair-idle",
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"role": "assistant", "content": "x"},
                                        "finish_reason": None,
                                    }
                                ],
                                "usage": None,
                            }
                        ),
                    ),
                    (0.0, b'data: {"id":"partial"'),
                ],
                ProviderTimeouts(connect_ms=1000, first_event_ms=700, idle_ms=150, total_ms=1000),
            ),
        )
        for chunks, timeouts in cases:
            source = SocketPairHTTPResponse(
                chunks,
                headers=b"HTTP/1.0 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n",
                keep_open=True,
            )
            response = source.start()
            try:
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                    with self.assertRaises(ProviderTimeoutUnknownError):
                        DeepSeekProvider(transport=lambda request, timeout: response).complete(
                            [{"role": "user", "content": "synthetic"}],
                            stream=True,
                            tools=[],
                            user_id="ws-opaque",
                            timeouts=timeouts,
                        )
            finally:
                source.close()
            self.assertFalse(source.thread.is_alive())

    def test_real_httpresponse_read_path_honors_cancellation(self) -> None:
        import contextox.provider as provider_module

        source = SocketPairHTTPResponse(
            [],
            headers=b"HTTP/1.0 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n",
            keep_open=True,
        )
        response = source.start()
        cancel_event = Event()
        wait_started = Event()
        provider_errors: list[BaseException] = []
        original_wait = provider_module._wait_for_socket_readable

        def observed_wait(sock, *, deadline, cancel_event):
            wait_started.set()
            return original_wait(sock, deadline=deadline, cancel_event=cancel_event)

        def run_provider() -> None:
            try:
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                    DeepSeekProvider(transport=lambda request, timeout: response).complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=1000,
                            first_event_ms=1000,
                            idle_ms=1000,
                            total_ms=3000,
                        ),
                        cancel_event=cancel_event,
                    )
            except BaseException as exc:
                provider_errors.append(exc)

        provider_thread = Thread(target=run_provider)
        try:
            with patch.object(provider_module, "_wait_for_socket_readable", observed_wait):
                provider_thread.start()
                self.assertTrue(wait_started.wait(1.0))
                cancel_event.set()
                provider_thread.join(1.0)
            self.assertFalse(provider_thread.is_alive())
            self.assertEqual(len(provider_errors), 1)
            self.assertIsInstance(provider_errors[0], ProviderCancelledError)
        finally:
            source.close()
            provider_thread.join(2.0)
        self.assertFalse(provider_thread.is_alive())

    def test_real_transport_half_http_chunk_is_bounded_by_first_event(self) -> None:
        import contextox.provider as provider_module

        state = {"requests": 0, "request_seen": Event(), "chunk_sent": Event(), "release": Event()}

        def callback(handler):
            state["requests"] += 1
            length = int(handler.headers.get("Content-Length", "0"))
            handler.rfile.read(length)
            state["request_seen"].set()
            handler.protocol_version = "HTTP/1.1"
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            handler.wfile.write(b"5\r")
            handler.wfile.flush()
            state["chunk_sent"].set()
            state["release"].wait(2.0)

        server, server_thread = _start_loopback_server(callback)
        try:
            with patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "http://127.0.0.1:%d/chat/completions" % server.server_port,
            ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                with self.assertRaises(ProviderTimeoutUnknownError):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=500,
                            first_event_ms=180,
                            idle_ms=180,
                            total_ms=800,
                        ),
                    )
            self.assertTrue(state["request_seen"].is_set())
            self.assertTrue(state["chunk_sent"].is_set())
            self.assertEqual(state["requests"], 1)
        finally:
            state["release"].set()
            server_thread.join(2.0)
            server.server_close()
        self.assertFalse(server_thread.is_alive())

    def test_real_transport_delayed_headers_use_first_event_not_connect_timeout(self) -> None:
        import contextox.provider as provider_module

        body = json.dumps(
            {
                "id": "delayed-headers",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
                "usage": _usage(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        state = {"requests": 0, "sent": False}

        def callback(handler):
            state["requests"] += 1
            length = int(handler.headers.get("Content-Length", "0"))
            handler.rfile.read(length)
            time.sleep(0.25)
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            handler.wfile.flush()
            state["sent"] = True

        server, server_thread = _start_loopback_server(callback)
        try:
            with patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "http://127.0.0.1:%d/chat/completions" % server.server_port,
            ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                completion = DeepSeekProvider().complete(
                    [{"role": "user", "content": "synthetic"}],
                    stream=False,
                    tools=None,
                    user_id="ws-opaque",
                    timeouts=ProviderTimeouts(
                        connect_ms=50,
                        first_event_ms=1000,
                        idle_ms=1000,
                        total_ms=1500,
                    ),
                )
            self.assertEqual(completion.content, "{}")
            self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
            self.assertEqual(state["requests"], 1)
            self.assertTrue(state["sent"])
        finally:
            server_thread.join(2.0)
            server.server_close()
        self.assertFalse(server_thread.is_alive())

    def test_real_transport_header_wait_honors_cancellation(self) -> None:
        import contextox.provider as provider_module

        state = {"requests": 0, "request_seen": Event(), "release": Event()}

        def callback(handler):
            state["requests"] += 1
            length = int(handler.headers.get("Content-Length", "0"))
            handler.rfile.read(length)
            state["request_seen"].set()
            state["release"].wait(2.0)

        server, server_thread = _start_loopback_server(callback)
        cancel_event = Event()
        provider_errors: list[BaseException] = []

        def run_provider() -> None:
            try:
                with patch.object(
                    provider_module,
                    "DEEPSEEK_ENDPOINT",
                    "http://127.0.0.1:%d/chat/completions" % server.server_port,
                ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=500,
                            first_event_ms=1000,
                            idle_ms=1000,
                            total_ms=1500,
                        ),
                        cancel_event=cancel_event,
                    )
            except BaseException as exc:
                provider_errors.append(exc)

        provider_thread = Thread(target=run_provider)
        provider_thread.start()
        try:
            self.assertTrue(state["request_seen"].wait(1.0))
            cancel_event.set()
            provider_thread.join(1.0)
            self.assertFalse(provider_thread.is_alive())
            self.assertEqual(len(provider_errors), 1)
            self.assertIsInstance(provider_errors[0], ProviderCancelledError)
            self.assertEqual(state["requests"], 1)
        finally:
            state["release"].set()
            provider_thread.join(2.0)
            server_thread.join(2.0)
            server.server_close()
        self.assertFalse(provider_thread.is_alive())
        self.assertFalse(server_thread.is_alive())

    def test_real_transport_header_wait_honors_total_deadline(self) -> None:
        import contextox.provider as provider_module

        state = {"requests": 0, "request_seen": Event(), "release": Event()}

        def callback(handler):
            state["requests"] += 1
            length = int(handler.headers.get("Content-Length", "0"))
            handler.rfile.read(length)
            state["request_seen"].set()
            state["release"].wait(2.0)

        server, server_thread = _start_loopback_server(callback)
        try:
            with patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "http://127.0.0.1:%d/chat/completions" % server.server_port,
            ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                with self.assertRaises(ProviderTimeoutUnknownError):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=500,
                            first_event_ms=1000,
                            idle_ms=1000,
                            total_ms=180,
                        ),
                    )
            self.assertTrue(state["request_seen"].is_set())
            self.assertEqual(state["requests"], 1)
        finally:
            state["release"].set()
            server_thread.join(2.0)
            server.server_close()
        self.assertFalse(server_thread.is_alive())

    def test_real_transport_does_not_follow_redirects_or_retry(self) -> None:
        import contextox.provider as provider_module

        state = {"requests": 0}

        def callback(handler):
            state["requests"] += 1
            length = int(handler.headers.get("Content-Length", "0"))
            handler.rfile.read(length)
            handler.send_response(302)
            handler.send_header("Location", "/next")
            handler.send_header("Content-Length", "0")
            handler.end_headers()

        server, server_thread = _start_loopback_server(callback)
        try:
            with patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "http://127.0.0.1:%d/chat/completions" % server.server_port,
            ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                with self.assertRaises(ProviderUnavailableError):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=False,
                        tools=None,
                        user_id="ws-opaque",
                    )
            self.assertEqual(state["requests"], 1)
        finally:
            server_thread.join(2.0)
            server.server_close()
        self.assertFalse(server_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
