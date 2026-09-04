import http.client
import json
import multiprocessing
import os
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from unittest.mock import patch

import contextox.provider as provider_module
from contextox.provider import (
    DEEPSEEK_ENDPOINT,
    DeepSeekProvider,
    ProviderBusyError,
    ProviderAuthError,
    ProviderCancelledError,
    ProviderContextBudgetError,
    ProviderProtocolError,
    ProviderStreamInterruptedError,
    ProviderTimeoutUnknownError,
    ProviderTimeouts,
    ProviderUnavailableError,
    ProviderUnreachableError,
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


class _TemporaryTLSMaterial:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="contextox-provider-tls-")
        self.cert_path = os.path.join(self.directory.name, "cert.pem")
        self.key_path = os.path.join(self.directory.name, "key.pem")
        openssl = next(
            (
                path
                for path in ("/usr/bin/openssl", "/opt/homebrew/bin/openssl")
                if os.path.isfile(path)
            ),
            None,
        )
        if openssl is None:
            self.close()
            raise RuntimeError("a system openssl executable is required for TLS tests")
        try:
            subprocess.run(
                [
                    openssl,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    self.key_path,
                    "-out",
                    self.cert_path,
                    "-days",
                    "1",
                    "-subj",
                    "/CN=127.0.0.1",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.directory.cleanup()


def _start_tls_http_server(cert_path: str, key_path: str, callback):
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            callback(self)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1.0
    original_get_request = server.get_request

    def get_request():
        sock, address = original_get_request()
        return server_context.wrap_socket(sock, server_side=True), address

    server.get_request = get_request
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


def _synthetic_call_id(packet_bytes: bytes) -> str:
    return json.loads(packet_bytes.decode("utf-8"))["call_id"]


def _synthetic_send(send_conn, call_id: str, sequence: int, event_type: str, **fields: object) -> None:
    message = {
        "call_id": call_id,
        "sequence": sequence,
        "event_type": event_type,
        **fields,
    }
    send_conn.send_bytes(
        json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _synthetic_completion() -> dict[str, object]:
    return {
        "completion_id": "synthetic-completion",
        "content": "visible",
        "reasoning_content": "must-stay-transient",
        "tool_calls": [],
        "finish_reason": "stop",
        "usage": {
            "input_tokens": 7,
            "output_tokens": 4,
            "cache_hit_tokens": 2,
            "cache_miss_tokens": 5,
        },
    }


def _child_emit_success(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "connected")
    _synthetic_send(send_conn, call_id, 2, "request_started")
    _synthetic_send(send_conn, call_id, 3, "activity")
    _synthetic_send(send_conn, call_id, 4, "content_delta", content="visible")
    _synthetic_send(send_conn, call_id, 5, "result", completion=_synthetic_completion())
    send_conn.close()


def _child_wait_before_connected(send_conn, packet_bytes: bytes) -> None:
    time.sleep(10.0)


def _child_wait_after_connected(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "connected")
    time.sleep(10.0)


def _child_wait_after_request_started(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "connected")
    _synthetic_send(send_conn, call_id, 2, "request_started")
    time.sleep(10.0)


def _child_send_activity_then_wait(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "connected")
    _synthetic_send(send_conn, call_id, 2, "request_started")
    _synthetic_send(send_conn, call_id, 3, "activity")
    time.sleep(10.0)


def _child_send_late_result_after_request(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "connected")
    _synthetic_send(send_conn, call_id, 2, "request_started")
    time.sleep(0.8)
    try:
        _synthetic_send(send_conn, call_id, 3, "content_delta", content="late-visible")
        _synthetic_send(send_conn, call_id, 4, "result", completion=_synthetic_completion())
    except OSError:
        return


def _child_crash_before_request(send_conn, packet_bytes: bytes) -> None:
    os._exit(3)


def _child_crash_after_request(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "connected")
    _synthetic_send(send_conn, call_id, 2, "request_started")
    os._exit(3)


def _child_send_illegal_event(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "illegal")
    time.sleep(10.0)


def _child_send_out_of_order(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "request_started")
    time.sleep(10.0)


def _child_send_duplicate_sequence(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "connected")
    try:
        _synthetic_send(send_conn, call_id, 1, "connected")
    except OSError:
        return
    time.sleep(10.0)


def _child_send_oversized(send_conn, packet_bytes: bytes) -> None:
    try:
        send_conn.send_bytes(b"x" * (provider_module.IPC_MAX_MESSAGE_BYTES + 1))
    except OSError:
        return
    time.sleep(10.0)


def _child_send_malformed_error_usage(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    _synthetic_send(send_conn, call_id, 1, "connected")
    _synthetic_send(send_conn, call_id, 2, "request_started")
    _synthetic_send(
        send_conn,
        call_id,
        3,
        "error",
        code="provider_protocol_error",
        usage={
            "input_tokens": "not-an-integer",
            "output_tokens": 1,
            "cache_hit_tokens": None,
            "cache_miss_tokens": None,
        },
    )
    send_conn.close()


def _child_send_malformed_result_usage(send_conn, packet_bytes: bytes) -> None:
    call_id = _synthetic_call_id(packet_bytes)
    completion = _synthetic_completion()
    completion["usage"] = {
        "input_tokens": -1,
        "output_tokens": 1,
        "cache_hit_tokens": None,
        "cache_miss_tokens": None,
    }
    _synthetic_send(send_conn, call_id, 1, "connected")
    _synthetic_send(send_conn, call_id, 2, "request_started")
    _synthetic_send(send_conn, call_id, 3, "result", completion=completion)
    send_conn.close()


def _child_run_stalled_transport(send_conn, packet_bytes: bytes, stall: str) -> None:
    ipc = None
    try:
        call_id, model, url, headers, body, stream, timeouts, max_context_bytes = (
            provider_module._provider_child_packet(packet_bytes)
        )
        ipc = provider_module._ChildIpc(send_conn, call_id)
        if stall == "dns":
            def blocked_getaddrinfo(*args, **kwargs):
                time.sleep(10.0)

            provider_module.socket.getaddrinfo = blocked_getaddrinfo
        elif stall == "tcp":
            def blocked_create_connection(*args, **kwargs):
                time.sleep(10.0)

            provider_module.socket.create_connection = blocked_create_connection
        else:
            raise AssertionError("unknown synthetic transport stall")
        request = provider_module.Request(url, data=body, headers=headers, method="POST")
        provider = DeepSeekProvider(model=model, transport=provider_module._UrllibTransport())
        provider._complete_request(
            request,
            stream=stream,
            timeouts=timeouts,
            cancel_event=None,
            max_context_bytes=max_context_bytes,
            on_content=lambda content: provider_module._send_child_content(ipc, content),
            on_phase=lambda event_type: ipc.send(event_type),
            on_activity=lambda: ipc.send("activity"),
        )
    except provider_module.ProviderError as exc:
        if ipc is not None:
            try:
                ipc.send("error", code=exc.code, usage=provider_module._usage_to_ipc(exc.usage))
            except provider_module._ChildIpcSendError:
                pass
    except Exception:
        if ipc is not None:
            try:
                ipc.send("error", code="provider_protocol_error", usage=None)
            except provider_module._ChildIpcSendError:
                pass
    finally:
        try:
            send_conn.close()
        except (OSError, ValueError):
            pass


def _child_blocking_dns(send_conn, packet_bytes: bytes) -> None:
    _child_run_stalled_transport(send_conn, packet_bytes, "dns")


def _child_blocking_tcp_connect(send_conn, packet_bytes: bytes) -> None:
    _child_run_stalled_transport(send_conn, packet_bytes, "tcp")


class _PartialTLSResponse:
    def __init__(self, cert_path: str, key_path: str, *, prefix_body: bytes, partial_body: bytes) -> None:
        self.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server_context.load_cert_chain(cert_path, key_path)
        self.listen = socket.socket()
        self.listen.bind(("127.0.0.1", 0))
        self.listen.listen(1)
        self.prefix_body = prefix_body
        self.partial_body = partial_body
        self.release = Event()
        self.partial_sent = Event()
        self.errors: list[str] = []
        self.thread = Thread(target=self._serve)

    @property
    def port(self) -> int:
        return int(self.listen.getsockname()[1])

    @staticmethod
    def _flush(outgoing: ssl.MemoryBIO, conn: socket.socket) -> None:
        while True:
            data = outgoing.read()
            if not data:
                return
            conn.sendall(data)

    def _serve(self) -> None:
        conn: socket.socket | None = None
        try:
            conn, _ = self.listen.accept()
            conn.settimeout(2.0)
            incoming = ssl.MemoryBIO()
            outgoing = ssl.MemoryBIO()
            tls = self.server_context.wrap_bio(incoming, outgoing, server_side=True)
            while True:
                try:
                    tls.do_handshake()
                    self._flush(outgoing, conn)
                    break
                except ssl.SSLWantReadError:
                    self._flush(outgoing, conn)
                    data = conn.recv(16384)
                    if not data:
                        return
                    incoming.write(data)
                except ssl.SSLWantWriteError:
                    self._flush(outgoing, conn)

            request = bytearray()
            while b"\r\n\r\n" not in request:
                try:
                    data = tls.read(16384)
                    if not data:
                        return
                    request.extend(data)
                except ssl.SSLWantReadError:
                    self._flush(outgoing, conn)
                    data = conn.recv(16384)
                    if not data:
                        return
                    incoming.write(data)
                except ssl.SSLWantWriteError:
                    self._flush(outgoing, conn)

            tls.write(
                b"HTTP/1.0 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: close\r\n\r\n"
            )
            self._flush(outgoing, conn)
            if self.prefix_body:
                tls.write(self.prefix_body)
                self._flush(outgoing, conn)
            tls.write(self.partial_body)
            encrypted = outgoing.read()
            if len(encrypted) <= 6:
                raise RuntimeError("partial TLS record is unexpectedly short")
            conn.sendall(encrypted[:6])
            self.partial_sent.set()
            self.release.wait(2.0)
            conn.sendall(encrypted[6:])
        except OSError as exc:
            if not self.release.is_set():
                self.errors.append(repr(exc))
        except BaseException as exc:
            self.errors.append(repr(exc))
        finally:
            if conn is not None:
                conn.close()
            self.listen.close()

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.release.set()
        self.thread.join(2.0)


class _BlockingSocketServer:
    def __init__(self) -> None:
        self.listen = socket.socket()
        self.listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen.bind(("127.0.0.1", 0))
        self.listen.listen(1)
        self.listen.settimeout(0.1)
        self.accepted = Event()
        self.release = Event()
        self.errors: list[str] = []
        self.thread = Thread(target=self._serve)

    @property
    def port(self) -> int:
        return int(self.listen.getsockname()[1])

    def _serve(self) -> None:
        conn: socket.socket | None = None
        try:
            while not self.release.is_set():
                try:
                    conn, _ = self.listen.accept()
                    self.accepted.set()
                    self.release.wait(2.0)
                    return
                except TimeoutError:
                    continue
        except OSError as exc:
            if not self.release.is_set():
                self.errors.append(repr(exc))
        finally:
            if conn is not None:
                conn.close()
            self.listen.close()

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.release.set()
        try:
            self.listen.close()
        except OSError:
            pass
        self.thread.join(2.0)


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tls_material = _TemporaryTLSMaterial()
        try:
            cls._tls_client_context = ssl.create_default_context(cafile=cls._tls_material.cert_path)
            if (
                not cls._tls_client_context.check_hostname
                or cls._tls_client_context.verify_mode != ssl.CERT_REQUIRED
            ):
                raise AssertionError("TLS tests must keep certificate and hostname verification enabled")
        except BaseException:
            cls._tls_material.close()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tls_material.close()
        super().tearDownClass()

    @staticmethod
    def _active_child_pids() -> set[int | None]:
        return {process.pid for process in multiprocessing.active_children()}

    def _wait_for_provider_slot(self, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if provider_module._PROVIDER_CHILD_SLOT.acquire(blocking=False):
                provider_module._PROVIDER_CHILD_SLOT.release()
            else:
                return
            time.sleep(0.01)
        self.fail("Provider child slot was not acquired")

    def test_default_transport_uses_spawn_child_and_only_emits_public_content(self) -> None:
        stream = (
            _event(
                {
                    "id": "spawn-completion",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": "visible",
                                "reasoning_content": "hidden",
                            },
                            "finish_reason": None,
                        }
                    ],
                    "usage": None,
                }
            )
            + _event(
                {
                    "id": "spawn-completion",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": _usage(),
                }
            )
            + b"data: [DONE]\n\n"
        )
        state = {"requests": 0, "body": b""}

        def callback(handler):
            state["requests"] += 1
            length = int(handler.headers.get("Content-Length", "0"))
            state["body"] = handler.rfile.read(length)
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(stream)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(stream)
            handler.wfile.flush()

        server, server_thread = _start_loopback_server(callback)
        before_children = self._active_child_pids()
        content_deltas: list[str] = []
        original_get_context = provider_module.multiprocessing.get_context
        try:
            with patch.object(
                provider_module.multiprocessing,
                "get_context",
                wraps=original_get_context,
            ) as get_context, patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "http://127.0.0.1:%d/chat/completions" % server.server_port,
            ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                completion = DeepSeekProvider().complete(
                    [{"role": "user", "content": "synthetic"}],
                    stream=True,
                    tools=[],
                    user_id="ws-opaque",
                    timeouts=ProviderTimeouts(
                        connect_ms=2000,
                        first_event_ms=2000,
                        idle_ms=2000,
                        total_ms=5000,
                    ),
                    on_content=content_deltas.append,
                )
            get_context.assert_called_once_with("spawn")
            self.assertEqual(completion.content, "visible")
            self.assertEqual(completion.reasoning_content, "hidden")
            self.assertEqual(content_deltas, ["visible"])
            self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
            self.assertEqual(state["requests"], 1)
            self.assertNotIn(b"test-secret", state["body"])
        finally:
            server_thread.join(2.0)
            server.server_close()
        self.assertFalse(server_thread.is_alive())
        self.assertEqual(self._active_child_pids(), before_children)

    def test_default_spawn_dns_and_tcp_connect_are_bounded(self) -> None:
        cases = (
            (_child_blocking_dns, "http://provider.invalid/chat/completions"),
            (_child_blocking_tcp_connect, "http://127.0.0.1:1/chat/completions"),
        )
        for target, endpoint in cases:
            with self.subTest(target=target.__name__):
                before_children = self._active_child_pids()
                started_at = time.monotonic()
                with patch.object(provider_module, "_provider_child_main", target), patch.object(
                    provider_module, "DEEPSEEK_ENDPOINT", endpoint
                ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                    with self.assertRaises(ProviderUnreachableError):
                        DeepSeekProvider().complete(
                            [{"role": "user", "content": "synthetic"}],
                            stream=True,
                            tools=[],
                            user_id="ws-opaque",
                            timeouts=ProviderTimeouts(
                                connect_ms=150,
                                first_event_ms=1000,
                                idle_ms=1000,
                                total_ms=1000,
                            ),
                        )
                self.assertLess(time.monotonic() - started_at, 1.5)
                self.assertEqual(self._active_child_pids(), before_children)

    def test_default_spawn_tls_handshake_is_bounded_by_connect_deadline(self) -> None:
        server = _BlockingSocketServer()
        before_children = self._active_child_pids()
        server.start()
        started_at = time.monotonic()
        try:
            with patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "https://127.0.0.1:%d/chat/completions" % server.port,
            ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                with self.assertRaises(ProviderUnreachableError):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=500,
                            first_event_ms=2000,
                            idle_ms=2000,
                            total_ms=3000,
                        ),
                    )
            self.assertTrue(server.accepted.wait(0.2))
            self.assertLess(time.monotonic() - started_at, 1.5)
        finally:
            server.close()
        self.assertFalse(server.thread.is_alive())
        self.assertEqual(server.errors, [])
        self.assertEqual(self._active_child_pids(), before_children)

    def test_default_spawn_request_send_and_first_response_are_bounded(self) -> None:
        server = _BlockingSocketServer()
        before_children = self._active_child_pids()
        server.start()
        started_at = time.monotonic()
        try:
            with patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "http://127.0.0.1:%d/chat/completions" % server.port,
            ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                with self.assertRaises(ProviderTimeoutUnknownError):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "x" * 240000}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=2000,
                            first_event_ms=250,
                            idle_ms=2000,
                            total_ms=2000,
                        ),
                    )
            self.assertTrue(server.accepted.wait(0.2))
            self.assertLess(time.monotonic() - started_at, 1.5)
        finally:
            server.close()
        self.assertFalse(server.thread.is_alive())
        self.assertEqual(server.errors, [])
        self.assertEqual(self._active_child_pids(), before_children)

    def test_default_spawn_http_error_is_bounded_and_not_retried(self) -> None:
        state = {"requests": 0}

        def callback(handler):
            state["requests"] += 1
            length = int(handler.headers.get("Content-Length", "0"))
            handler.rfile.read(length)
            body = b"private-upstream-error-body"
            handler.send_response(500)
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(body)
            handler.wfile.flush()

        server, server_thread = _start_loopback_server(callback)
        before_children = self._active_child_pids()
        try:
            with patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "http://127.0.0.1:%d/chat/completions" % server.server_port,
            ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                with self.assertRaises(ProviderUnavailableError) as context:
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=False,
                        tools=None,
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=2000,
                            first_event_ms=2000,
                            idle_ms=2000,
                            total_ms=3000,
                        ),
                    )
            self.assertEqual(str(context.exception), "provider_unavailable")
            self.assertEqual(state["requests"], 1)
        finally:
            server_thread.join(2.0)
            server.server_close()
        self.assertFalse(server_thread.is_alive())
        self.assertEqual(self._active_child_pids(), before_children)

    def test_injected_transport_stays_in_process(self) -> None:
        response = FakeResponse(
            body=json.dumps(
                {
                    "id": "in-process",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "{}"},
                        }
                    ],
                    "usage": _usage(),
                }
            ).encode()
        )
        with patch.object(provider_module.multiprocessing, "get_context", side_effect=AssertionError):
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                completion = DeepSeekProvider(transport=FakeTransport(response)).complete(
                    [{"role": "user", "content": "synthetic"}],
                    stream=False,
                    tools=None,
                    user_id="ws-opaque",
                )
        self.assertEqual(completion.content, "{}")

    def test_spawn_ipc_reconstructs_result_and_keeps_reasoning_out_of_content(self) -> None:
        content_deltas: list[str] = []
        before_children = self._active_child_pids()
        with patch.object(provider_module, "_provider_child_main", _child_emit_success), patch.dict(
            os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False
        ):
            completion = DeepSeekProvider().complete(
                [{"role": "user", "content": "synthetic"}],
                stream=True,
                tools=[],
                user_id="ws-opaque",
                timeouts=ProviderTimeouts(
                    connect_ms=3000,
                    first_event_ms=3000,
                    idle_ms=3000,
                    total_ms=5000,
                ),
                on_content=content_deltas.append,
            )
        self.assertEqual(content_deltas, ["visible"])
        self.assertEqual(completion.content, "visible")
        self.assertEqual(completion.reasoning_content, "must-stay-transient")
        self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
        self.assertEqual(self._active_child_pids(), before_children)

    def test_malformed_ipc_usage_is_protocol_error_and_child_is_reaped(self) -> None:
        cases = (_child_send_malformed_error_usage, _child_send_malformed_result_usage)
        for target in cases:
            with self.subTest(target=target.__name__):
                before_children = self._active_child_pids()
                with patch.object(provider_module, "_provider_child_main", target), patch.dict(
                    os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False
                ):
                    with self.assertRaises(ProviderProtocolError):
                        DeepSeekProvider().complete(
                            [{"role": "user", "content": "synthetic"}],
                            stream=True,
                            tools=[],
                            user_id="ws-opaque",
                            timeouts=ProviderTimeouts(
                                connect_ms=1000,
                                first_event_ms=1000,
                                idle_ms=1000,
                                total_ms=1500,
                            ),
                        )
                self.assertEqual(self._active_child_pids(), before_children)

    def test_child_startup_failure_is_unreachable_and_releases_slot(self) -> None:
        before_children = self._active_child_pids()
        with patch.object(
            provider_module.multiprocessing,
            "get_context",
            side_effect=RuntimeError("synthetic startup failure"),
        ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            with self.assertRaises(ProviderUnreachableError):
                DeepSeekProvider().complete(
                    [{"role": "user", "content": "synthetic"}],
                    stream=False,
                    tools=None,
                    user_id="ws-opaque",
                    timeouts=ProviderTimeouts(total_ms=500),
                )
        self.assertEqual(self._active_child_pids(), before_children)

    def test_spawn_supervisor_bounds_connect_first_idle_and_total_phases(self) -> None:
        cases = (
            (
                _child_wait_before_connected,
                ProviderTimeouts(connect_ms=120, first_event_ms=1000, idle_ms=1000, total_ms=1000),
                ProviderUnreachableError,
            ),
            (
                _child_wait_after_connected,
                ProviderTimeouts(connect_ms=3000, first_event_ms=120, idle_ms=1000, total_ms=250),
                ProviderUnreachableError,
            ),
            (
                _child_send_activity_then_wait,
                ProviderTimeouts(connect_ms=3000, first_event_ms=1000, idle_ms=120, total_ms=3000),
                ProviderTimeoutUnknownError,
            ),
            (
                _child_wait_after_request_started,
                ProviderTimeouts(connect_ms=3000, first_event_ms=3000, idle_ms=3000, total_ms=1200),
                ProviderTimeoutUnknownError,
            ),
        )
        for target, timeouts, expected in cases:
            with self.subTest(target=target.__name__):
                before_children = self._active_child_pids()
                started_at = time.monotonic()
                with patch.object(provider_module, "_provider_child_main", target), patch.dict(
                    os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False
                ):
                    with self.assertRaises(expected):
                        DeepSeekProvider().complete(
                            [{"role": "user", "content": "synthetic"}],
                            stream=True,
                            tools=[],
                            user_id="ws-opaque",
                            timeouts=timeouts,
                        )
                self.assertLess(time.monotonic() - started_at, 2.0)
                self.assertEqual(self._active_child_pids(), before_children)

    def test_cancel_and_deadline_win_over_late_queued_success(self) -> None:
        timeout_before_children = self._active_child_pids()
        timeout_content: list[str] = []
        with patch.object(
            provider_module, "_provider_child_main", _child_send_late_result_after_request
        ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
            with self.assertRaises(ProviderTimeoutUnknownError):
                DeepSeekProvider().complete(
                    [{"role": "user", "content": "synthetic"}],
                    stream=True,
                    tools=[],
                    user_id="ws-opaque",
                    timeouts=ProviderTimeouts(
                        connect_ms=3000,
                        first_event_ms=120,
                        idle_ms=3000,
                        total_ms=3000,
                    ),
                    on_content=timeout_content.append,
                )
        self.assertEqual(self._active_child_pids(), timeout_before_children)
        self.assertEqual(timeout_content, [])

        cancel_event = Event()
        errors: list[BaseException] = []
        cancel_content: list[str] = []

        def run_provider() -> None:
            try:
                with patch.object(
                    provider_module, "_provider_child_main", _child_send_late_result_after_request
                ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=3000,
                            first_event_ms=3000,
                            idle_ms=3000,
                            total_ms=3000,
                        ),
                        cancel_event=cancel_event,
                        on_content=cancel_content.append,
                    )
            except BaseException as exc:
                errors.append(exc)

        before_children = self._active_child_pids()
        provider_thread = Thread(target=run_provider)
        provider_thread.start()
        try:
            self._wait_for_provider_slot()
            time.sleep(0.4)
            cancel_event.set()
            provider_thread.join(1.0)
            self.assertFalse(provider_thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], ProviderCancelledError)
            self.assertEqual(errors[0].code, "provider_cancelled_outcome_unknown")
            self.assertEqual(cancel_content, [])
        finally:
            cancel_event.set()
            provider_thread.join(2.0)
        self.assertEqual(self._active_child_pids(), before_children)

    def test_cancel_before_send_and_after_request_started_are_distinct(self) -> None:
        for target, expected_code in (
            (_child_wait_before_connected, "cancelled"),
            (_child_wait_after_connected, "cancelled"),
            (_child_wait_after_request_started, "provider_cancelled_outcome_unknown"),
        ):
            cancel_event = Event()
            errors: list[BaseException] = []

            def run_provider() -> None:
                try:
                    with patch.object(provider_module, "_provider_child_main", target), patch.dict(
                        os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False
                    ):
                        DeepSeekProvider().complete(
                            [{"role": "user", "content": "synthetic"}],
                            stream=True,
                            tools=[],
                            user_id="ws-opaque",
                            timeouts=ProviderTimeouts(
                                connect_ms=2000,
                                first_event_ms=2000,
                                idle_ms=2000,
                                total_ms=5000,
                            ),
                            cancel_event=cancel_event,
                        )
                except BaseException as exc:
                    errors.append(exc)

            before_children = self._active_child_pids()
            provider_thread = Thread(target=run_provider)
            provider_thread.start()
            try:
                self._wait_for_provider_slot()
                time.sleep(0.4)
                cancel_event.set()
                provider_thread.join(1.0)
                self.assertFalse(provider_thread.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], ProviderCancelledError)
                self.assertEqual(errors[0].code, expected_code)
            finally:
                cancel_event.set()
                provider_thread.join(2.0)
            self.assertEqual(self._active_child_pids(), before_children)

    def test_global_provider_child_slot_is_one_and_wait_is_cancelable(self) -> None:
        first_cancel = Event()
        first_errors: list[BaseException] = []

        def run_first() -> None:
            try:
                with patch.object(provider_module, "_provider_child_main", _child_wait_after_request_started), patch.dict(
                    os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False
                ):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "first"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=2000,
                            first_event_ms=2000,
                            idle_ms=2000,
                            total_ms=5000,
                        ),
                        cancel_event=first_cancel,
                    )
            except BaseException as exc:
                first_errors.append(exc)

        before_children = self._active_child_pids()
        first_thread = Thread(target=run_first)
        first_thread.start()
        try:
            self._wait_for_provider_slot()
            second_cancel = Event()
            second_errors: list[BaseException] = []

            def run_second() -> None:
                try:
                    with patch.object(provider_module, "_provider_child_main", _child_wait_after_request_started), patch.dict(
                        os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False
                    ):
                        DeepSeekProvider().complete(
                            [{"role": "user", "content": "second"}],
                            stream=True,
                            tools=[],
                            user_id="ws-opaque",
                            timeouts=ProviderTimeouts(
                                connect_ms=2000,
                                first_event_ms=2000,
                                idle_ms=2000,
                                total_ms=150,
                            ),
                            cancel_event=second_cancel,
                        )
                except BaseException as exc:
                    second_errors.append(exc)

            second_thread = Thread(target=run_second)
            second_thread.start()
            second_thread.join(1.0)
            self.assertFalse(second_thread.is_alive())
            self.assertEqual(len(second_errors), 1)
            self.assertIsInstance(second_errors[0], ProviderBusyError)
            self.assertEqual(len(self._active_child_pids() - before_children), 1)

            cancel_wait_event = Event()
            cancel_wait_errors: list[BaseException] = []

            def run_cancel_wait() -> None:
                try:
                    with patch.object(provider_module, "_provider_child_main", _child_wait_after_request_started), patch.dict(
                        os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False
                    ):
                        DeepSeekProvider().complete(
                            [{"role": "user", "content": "cancel-wait"}],
                            stream=True,
                            tools=[],
                            user_id="ws-opaque",
                            timeouts=ProviderTimeouts(total_ms=5000),
                            cancel_event=cancel_wait_event,
                        )
                except BaseException as exc:
                    cancel_wait_errors.append(exc)

            cancel_wait_thread = Thread(target=run_cancel_wait)
            cancel_wait_thread.start()
            time.sleep(0.1)
            cancel_wait_event.set()
            cancel_wait_thread.join(1.0)
            self.assertFalse(cancel_wait_thread.is_alive())
            self.assertEqual(len(cancel_wait_errors), 1)
            self.assertIsInstance(cancel_wait_errors[0], ProviderCancelledError)
            self.assertEqual(cancel_wait_errors[0].code, "cancelled")
        finally:
            first_cancel.set()
            first_thread.join(2.0)
        self.assertEqual(len(first_errors), 1)
        self.assertEqual(first_errors[0].code, "provider_cancelled_outcome_unknown")
        self.assertEqual(self._active_child_pids(), before_children)

    def test_child_crash_and_ipc_protocol_failures_are_fail_closed(self) -> None:
        cases = (
            (_child_crash_before_request, ProviderUnreachableError),
            (_child_crash_after_request, ProviderTimeoutUnknownError),
            (_child_send_illegal_event, ProviderProtocolError),
            (_child_send_out_of_order, ProviderProtocolError),
            (_child_send_duplicate_sequence, ProviderProtocolError),
            (_child_send_oversized, ProviderProtocolError),
        )
        for target, expected in cases:
            before_children = self._active_child_pids()
            with patch.object(provider_module, "_provider_child_main", target), patch.dict(
                os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False
            ):
                with self.assertRaises(expected):
                    DeepSeekProvider().complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=1000,
                            first_event_ms=1000,
                            idle_ms=1000,
                            total_ms=1500,
                        ),
                    )
            self.assertEqual(self._active_child_pids(), before_children)

    def test_ipc_rejects_stale_duplicate_and_unknown_messages(self) -> None:
        valid_result = {
            "call_id": "call-1",
            "sequence": 1,
            "event_type": "result",
            "completion": _synthetic_completion(),
        }
        raw_result = json.dumps(valid_result, separators=(",", ":")).encode()
        with self.assertRaises(provider_module._ProviderIpcProtocolError):
            provider_module._decode_ipc_message(
                raw_result,
                expected_call_id="different-call",
                expected_sequence=0,
                connected=True,
                request_started=True,
                terminal=False,
                max_context_bytes=provider_module.MAX_CONTEXT_BYTES,
            )
        with self.assertRaises(provider_module._ProviderIpcProtocolError):
            provider_module._decode_ipc_message(
                raw_result,
                expected_call_id="call-1",
                expected_sequence=0,
                connected=True,
                request_started=True,
                terminal=True,
                max_context_bytes=provider_module.MAX_CONTEXT_BYTES,
            )
        connected = json.dumps(
            {"call_id": "call-1", "sequence": 1, "event_type": "connected"}, separators=(",", ":")
        ).encode()
        with self.assertRaises(provider_module._ProviderIpcProtocolError):
            provider_module._decode_ipc_message(
                connected,
                expected_call_id="call-1",
                expected_sequence=1,
                connected=True,
                request_started=False,
                terminal=False,
                max_context_bytes=provider_module.MAX_CONTEXT_BYTES,
            )

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
                    DeepSeekProvider(transport=provider_module._UrllibTransport()).complete(
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
                completion = DeepSeekProvider(transport=provider_module._UrllibTransport()).complete(
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
                    DeepSeekProvider(transport=provider_module._UrllibTransport()).complete(
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
                    DeepSeekProvider(transport=provider_module._UrllibTransport()).complete(
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
                    DeepSeekProvider(transport=provider_module._UrllibTransport()).complete(
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

    def test_real_tls_buffered_plaintext_supports_stream_and_nonstream(self) -> None:
        import contextox.provider as provider_module

        large_content = "x" * 5000
        stream_body = (
            _event(
                {
                    "id": "tls-buffered",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": large_content},
                            "finish_reason": None,
                        }
                    ],
                    "usage": None,
                }
            )
            + _event(
                {
                    "id": "tls-buffered",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": _usage(),
                }
            )
            + b"data: [DONE]\n\n"
        )
        nonstream_body = json.dumps(
            {
                "id": "tls-nonstream",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": large_content},
                    }
                ],
                "usage": _usage(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        cases = (
            (
                True,
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                + b"Content-Length: "
                + str(len(stream_body)).encode("ascii")
                + b"\r\nConnection: keep-alive\r\n\r\n"
                + stream_body,
            ),
            (
                False,
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                + b"Content-Length: "
                + str(len(nonstream_body)).encode("ascii")
                + b"\r\nConnection: keep-alive\r\n\r\n"
                + nonstream_body,
            ),
        )
        for stream, payload in cases:
            state = {"requests": 0, "sent": Event(), "release": Event(), "errors": []}

            def callback(handler):
                try:
                    state["requests"] += 1
                    length = int(handler.headers.get("Content-Length", "0"))
                    handler.rfile.read(length)
                    handler.connection.sendall(payload)
                    state["sent"].set()
                    state["release"].wait(2.0)
                except OSError as exc:
                    if not state["release"].is_set():
                        state["errors"].append(repr(exc))

            server, server_thread = _start_tls_http_server(
                self._tls_material.cert_path,
                self._tls_material.key_path,
                callback,
            )
            content_deltas: list[str] = []
            try:
                with patch.object(
                    http.client,
                    "_create_https_context",
                    return_value=self._tls_client_context,
                ), patch.object(
                    provider_module,
                    "DEEPSEEK_ENDPOINT",
                    "https://127.0.0.1:%d/chat/completions" % server.server_port,
                ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                    completion = DeepSeekProvider(transport=provider_module._UrllibTransport()).complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=stream,
                        tools=[] if stream else None,
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=500,
                            first_event_ms=1000,
                            idle_ms=1000,
                            total_ms=3000,
                        ),
                        on_content=content_deltas.append if stream else None,
                    )
                self.assertEqual(completion.content, large_content)
                self.assertEqual(completion.usage, ProviderUsage(7, 4, 2, 5))
                self.assertEqual(content_deltas, [large_content] if stream else [])
                self.assertTrue(state["sent"].is_set())
                self.assertEqual(state["requests"], 1)
            finally:
                state["release"].set()
                server_thread.join(2.0)
                server.server_close()
            self.assertFalse(server_thread.is_alive())
            self.assertEqual(state["errors"], [])

    def test_real_tls_partial_records_honor_first_idle_total_and_cancel(self) -> None:
        import contextox.provider as provider_module

        first_event = _event(
            {
                "id": "tls-partial",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "first"},
                        "finish_reason": None,
                    }
                ],
                "usage": None,
            }
        )
        timeout_cases = (
            (
                b"",
                b": keep-alive\n\n",
                ProviderTimeouts(connect_ms=500, first_event_ms=180, idle_ms=500, total_ms=800),
            ),
            (
                b"",
                b": keep-alive\n\n",
                ProviderTimeouts(connect_ms=500, first_event_ms=700, idle_ms=700, total_ms=180),
            ),
            (
                first_event,
                b'data: {"id":"partial"',
                ProviderTimeouts(connect_ms=500, first_event_ms=700, idle_ms=180, total_ms=800),
            ),
        )
        for prefix_body, partial_body, timeouts in timeout_cases:
            source = _PartialTLSResponse(
                self._tls_material.cert_path,
                self._tls_material.key_path,
                prefix_body=prefix_body,
                partial_body=partial_body,
            )
            source.start()
            try:
                started_at = time.monotonic()
                with patch.object(
                    http.client,
                    "_create_https_context",
                    return_value=self._tls_client_context,
                ), patch.object(
                    provider_module,
                    "DEEPSEEK_ENDPOINT",
                    "https://127.0.0.1:%d/chat/completions" % source.port,
                ), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                    with self.assertRaises(ProviderTimeoutUnknownError):
                        DeepSeekProvider(transport=provider_module._UrllibTransport()).complete(
                            [{"role": "user", "content": "synthetic"}],
                            stream=True,
                            tools=[],
                            user_id="ws-opaque",
                            timeouts=timeouts,
                        )
                elapsed = time.monotonic() - started_at
                self.assertLess(elapsed, 1.5)
                self.assertTrue(source.partial_sent.is_set())
            finally:
                source.close()
            self.assertFalse(source.thread.is_alive())
            self.assertEqual(source.errors, [])

        source = _PartialTLSResponse(
            self._tls_material.cert_path,
            self._tls_material.key_path,
            prefix_body=b"",
            partial_body=b": keep-alive\n\n",
        )
        source.start()
        cancel_event = Event()
        provider_errors: list[BaseException] = []

        def run_provider() -> None:
            try:
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}, clear=False):
                    DeepSeekProvider(transport=provider_module._UrllibTransport()).complete(
                        [{"role": "user", "content": "synthetic"}],
                        stream=True,
                        tools=[],
                        user_id="ws-opaque",
                        timeouts=ProviderTimeouts(
                            connect_ms=500,
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
            with patch.object(
                http.client,
                "_create_https_context",
                return_value=self._tls_client_context,
            ), patch.object(
                provider_module,
                "DEEPSEEK_ENDPOINT",
                "https://127.0.0.1:%d/chat/completions" % source.port,
            ):
                provider_thread.start()
                self.assertTrue(source.partial_sent.wait(1.0))
                cancel_event.set()
                provider_thread.join(1.0)
            self.assertFalse(provider_thread.is_alive())
            self.assertEqual(len(provider_errors), 1)
            self.assertIsInstance(provider_errors[0], ProviderCancelledError)
        finally:
            source.close()
            provider_thread.join(2.0)
        self.assertFalse(source.thread.is_alive())
        self.assertFalse(provider_thread.is_alive())
        self.assertEqual(source.errors, [])

    def test_ssl_want_write_path_waits_for_writable_socket(self) -> None:
        import contextox.provider as provider_module

        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(
            self._tls_material.cert_path,
            self._tls_material.key_path,
        )
        client_raw, server_raw = socket.socketpair()
        server = server_context.wrap_socket(
            server_raw,
            server_side=True,
            do_handshake_on_connect=False,
        )
        client = self._tls_client_context.wrap_socket(
            client_raw,
            server_hostname="127.0.0.1",
            do_handshake_on_connect=False,
        )
        server_errors: list[BaseException] = []

        def serve() -> None:
            try:
                server.do_handshake()
                server.sendall(b"ok")
            except BaseException as exc:
                server_errors.append(exc)

        server_thread = Thread(target=serve)
        server_thread.start()
        try:
            client.do_handshake()
        except BaseException:
            client.close()
            server.close()
            server_thread.join(2.0)
            raise
        client.setblocking(False)

        class WantWriteOnce:
            def __init__(self, sock):
                self.sock = sock
                self.first = True

            def fileno(self):
                return self.sock.fileno()

            def recv_into(self, buffer):
                if self.first:
                    self.first = False
                    raise ssl.SSLWantWriteError(ssl.SSL_ERROR_WANT_WRITE, "synthetic")
                return self.sock.recv_into(buffer)

        calls: list[bool] = []
        original_wait = provider_module._wait_for_socket_io

        def observed_wait(sock, *, deadline, cancel_event, wait_for_write=False):
            calls.append(wait_for_write)
            return original_wait(
                sock,
                deadline=deadline,
                cancel_event=cancel_event,
                wait_for_write=wait_for_write,
            )

        try:
            with patch.object(provider_module, "_wait_for_socket_io", observed_wait):
                buffer = bytearray(2)
                count = provider_module._recv_into_with_deadline(
                    WantWriteOnce(client),
                    buffer,
                    deadline=time.monotonic() + 1.0,
                    cancel_event=None,
                )
            self.assertEqual(count, 2)
            self.assertEqual(bytes(buffer), b"ok")
            self.assertIn(True, calls)
        finally:
            server_thread.join(2.0)
            client.close()
            server.close()
        self.assertFalse(server_thread.is_alive())
        self.assertEqual(server_errors, [])


if __name__ == "__main__":
    unittest.main()
