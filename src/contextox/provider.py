"""Small, fail-closed DeepSeek Chat Completions boundary.

The provider boundary deliberately uses the Python standard library.  It owns
HTTP and protocol parsing only; ContextOx's Agent loop owns context selection,
permissions, tool execution, persistence, and terminal semantics.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import select
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from contextox.models import ProviderConfigSnapshot


DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
MAX_OUTPUT_TOKENS = 4096
MAX_CONTEXT_BYTES = 262144
READ_CHUNK_BYTES = 1024
READ_POLL_INTERVAL_MS = 100


class ProviderError(RuntimeError):
    """A bounded provider failure with no upstream body or secret attached."""

    def __init__(
        self,
        code: str,
        run_status: str,
        *,
        usage: ProviderUsage | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.run_status = run_status
        self.usage = usage


class ProviderNotConfiguredError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_not_configured", "blocked")


class ProviderUnreachableError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_unreachable", "blocked")


class ProviderAuthError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_auth_failed", "blocked")


class ProviderBalanceError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_balance_insufficient", "blocked")


class ProviderRateLimitError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_rate_limited", "blocked")


class ProviderRequestError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_request_invalid", "failed")


class ProviderUnavailableError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_unavailable", "failed")


class ProviderTimeoutUnknownError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_timeout_unknown", "failed")


class ProviderStreamInterruptedError(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_stream_interrupted", "failed")


class ProviderProtocolError(ProviderError):
    def __init__(self, *, usage: ProviderUsage | None = None) -> None:
        super().__init__("provider_protocol_error", "failed", usage=usage)


class ProviderCancelledError(ProviderError):
    def __init__(self) -> None:
        super().__init__("cancelled", "cancelled")


class ProviderContextBudgetError(ProviderError):
    def __init__(self) -> None:
        super().__init__("context_budget_exceeded", "blocked")


@dataclass(frozen=True)
class ProviderUsage:
    """The public usage subset accepted by ``ProviderReceipt``."""

    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None


@dataclass(frozen=True)
class ProviderToolCall:
    """A normalized complete tool call assembled from a provider response."""

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderCompletion:
    """A sanitized provider result; no raw response is retained."""

    completion_id: str
    content: str
    reasoning_content: str
    tool_calls: tuple[ProviderToolCall, ...]
    finish_reason: str | None
    usage: ProviderUsage | None


@dataclass(frozen=True)
class ProviderTimeouts:
    connect_ms: int = 10000
    first_event_ms: int = 60000
    idle_ms: int = 30000
    total_ms: int = 120000


def _strict_nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("provider usage must be a non-negative integer")
    return value


def _usage_from_payload(value: object) -> ProviderUsage:
    if not isinstance(value, dict):
        raise ValueError("provider usage must be an object")
    input_tokens = _strict_nonnegative_int(value.get("prompt_tokens"))
    output_tokens = _strict_nonnegative_int(value.get("completion_tokens"))
    cache_hit = value.get("prompt_cache_hit_tokens")
    cache_miss = value.get("prompt_cache_miss_tokens")
    if cache_hit is not None:
        cache_hit = _strict_nonnegative_int(cache_hit)
    if cache_miss is not None:
        cache_miss = _strict_nonnegative_int(cache_miss)
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=cache_miss,
    )


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _strict_json_loads(value: str) -> object:
    return json.loads(
        value,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_pairs,
    )


def _text_or_empty(value: object, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value


def _bounded_text_append(
    parts: list[str],
    value: str,
    *,
    max_bytes: int,
    used_bytes: list[int],
) -> None:
    if not value:
        return
    value_bytes = len(value.encode("utf-8"))
    if used_bytes[0] + value_bytes > max_bytes:
        raise ProviderContextBudgetError()
    parts.append(value)
    used_bytes[0] += value_bytes


def _bounded_text_concat(
    current: str,
    value: str,
    *,
    max_bytes: int,
    used_bytes: list[int],
) -> str:
    if not value:
        return current
    value_bytes = len(value.encode("utf-8"))
    if used_bytes[0] + value_bytes > max_bytes:
        raise ProviderContextBudgetError()
    used_bytes[0] += value_bytes
    return current + value


def _response_status(response: object) -> int | None:
    status = getattr(response, "status", None)
    if type(status) is int:
        return status
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        value = getcode()
        return value if type(value) is int else None
    return None


def _wait_for_socket_readable(
    sock: socket.socket,
    *,
    deadline: float,
    cancel_event: Any | None,
) -> None:
    """Poll a socket without poisoning ``socket.SocketIO`` on a timeout."""

    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderCancelledError()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTimeoutUnknownError()
        try:
            readable, _, _ = select.select(
                [sock],
                [],
                [],
                min(READ_POLL_INTERVAL_MS / 1000, remaining),
            )
        except InterruptedError:
            continue
        if readable:
            return


def _response_socket(response: object) -> socket.socket | None:
    candidate: object | None = response
    seen: set[int] = set()
    for _ in range(12):
        if candidate is None or id(candidate) in seen:
            return None
        seen.add(id(candidate))
        if isinstance(candidate, socket.socket):
            return candidate
        next_candidate = None
        for attribute in ("_contextox_socket", "_sock", "sock", "raw", "fp"):
            value = getattr(candidate, attribute, None)
            if value is not None and id(value) not in seen:
                next_candidate = value
                break
        candidate = next_candidate
    return None


def _set_response_read_context(
    response: object,
    *,
    deadline: float,
    cancel_event: Any | None,
) -> bool:
    candidate: object | None = response
    seen: set[int] = set()
    for _ in range(12):
        if candidate is None or id(candidate) in seen:
            return False
        seen.add(id(candidate))
        setter = getattr(candidate, "set_read_context", None)
        if callable(setter):
            setter(deadline, cancel_event)
            return True
        next_candidate = None
        for attribute in ("raw", "fp", "_sock", "sock", "_contextox_socket"):
            value = getattr(candidate, attribute, None)
            if value is not None and id(value) not in seen:
                next_candidate = value
                break
        candidate = next_candidate
    return False


def _response_has_buffered_bytes(response: object, sock: socket.socket) -> bool:
    """Inspect an existing HTTPResponse buffer without blocking or timing out."""

    file_object = getattr(response, "fp", None)
    peek = getattr(file_object, "peek", None)
    if not callable(peek):
        return False
    previous_timeout = sock.gettimeout()
    sock.setblocking(False)
    try:
        try:
            return bool(peek(1))
        except BlockingIOError:
            return False
    finally:
        sock.settimeout(previous_timeout)


class _DeadlineSocketRaw(io.RawIOBase):
    """A bounded raw reader used below ``http.client.HTTPResponse``."""

    def __init__(self, sock: socket.socket, header_prefix: bytes, body_prefix: bytes) -> None:
        super().__init__()
        self._sock = sock
        self._header_prefix = memoryview(header_prefix)
        self._body_prefix = memoryview(body_prefix)
        self._body_enabled = False
        self._deadline: float | None = None
        self._cancel_event: Any | None = None

    def readable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._sock.fileno()

    def activate_body(self) -> None:
        self._body_enabled = True

    def set_read_context(self, deadline: float, cancel_event: Any | None) -> None:
        self._deadline = deadline
        self._cancel_event = cancel_event

    def readinto(self, buffer: Any) -> int | None:
        self._checkClosed()
        self._checkReadable()
        if not buffer:
            return 0
        if self._header_prefix:
            count = min(len(buffer), len(self._header_prefix))
            buffer[:count] = self._header_prefix[:count]
            self._header_prefix = self._header_prefix[count:]
            return count
        if not self._body_enabled:
            return None
        if self._body_prefix:
            count = min(len(buffer), len(self._body_prefix))
            buffer[:count] = self._body_prefix[:count]
            self._body_prefix = self._body_prefix[count:]
            return count
        if self._deadline is not None:
            _wait_for_socket_readable(
                self._sock,
                deadline=self._deadline,
                cancel_event=self._cancel_event,
            )
        try:
            return self._sock.recv_into(buffer)
        except BlockingIOError:
            return None

    def close(self) -> None:
        if not self.closed:
            try:
                self._sock.close()
            finally:
                super().close()


class _ResponseSocket:
    """Feed parsed headers to HTTPResponse, then read the live body."""

    def __init__(self, sock: socket.socket, header_prefix: bytes, body_prefix: bytes) -> None:
        self._sock = sock
        self._header_prefix = header_prefix
        self._body_prefix = body_prefix
        self._raw: _DeadlineSocketRaw | None = None

    def makefile(self, mode: str) -> io.BufferedReader:
        if self._raw is not None:
            raise OSError("response file is already open")
        self._raw = _DeadlineSocketRaw(self._sock, self._header_prefix, self._body_prefix)
        return io.BufferedReader(self._raw, buffer_size=READ_CHUNK_BYTES)

    def activate_body(self) -> None:
        if self._raw is None:
            raise OSError("response file is not open")
        self._raw.activate_body()

    def close(self) -> None:
        self._sock.close()


class _OwnedHTTPResponse(http.client.HTTPResponse):
    """An HTTPResponse whose connection is owned by the private transport."""

    def __init__(self, sock: _ResponseSocket, connection: http.client.HTTPConnection) -> None:
        self._contextox_connection = connection
        super().__init__(sock)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._contextox_connection.close()


def _read_response_headers(
    sock: socket.socket,
    *,
    deadline: float,
    cancel_event: Any | None,
    max_context_bytes: int,
) -> tuple[bytes, bytes]:
    """Read complete HTTP header blocks without a short socket timeout."""

    received = bytearray()
    block_start = 0
    interim_count = 0
    while True:
        marker = received.find(b"\r\n\r\n", block_start)
        while marker < 0:
            _wait_for_socket_readable(sock, deadline=deadline, cancel_event=cancel_event)
            try:
                chunk = sock.recv(READ_CHUNK_BYTES)
            except BlockingIOError:
                continue
            if not chunk:
                raise ProviderUnavailableError()
            if len(received) + len(chunk) > max_context_bytes:
                raise ProviderContextBudgetError()
            received.extend(chunk)
            marker = received.find(b"\r\n\r\n", block_start)

        block_end = marker + 4
        line_end = received.find(b"\r\n", block_start, marker)
        status_line = bytes(received[block_start:line_end]) if line_end >= 0 else b""
        status_code: int | None = None
        status_parts = status_line.split(None, 2)
        if len(status_parts) >= 2 and status_parts[0].startswith(b"HTTP/"):
            try:
                status_code = int(status_parts[1])
            except ValueError:
                status_code = None
        if status_code == 100:
            interim_count += 1
            if interim_count > 100:
                raise ProviderProtocolError()
            block_start = block_end
            continue
        return bytes(received[:block_end]), bytes(received[block_end:])


class _UrllibTransport:
    """The only production transport; tests inject a local fake object."""

    def open(
        self,
        request: Request,
        timeout: float,
        *,
        connect_deadline: float | None = None,
        first_event_deadline: float | None = None,
        total_deadline: float | None = None,
        cancel_event: Any | None = None,
        max_context_bytes: int = MAX_CONTEXT_BYTES,
    ) -> object:
        parsed = urlsplit(request.full_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ProviderUnavailableError()
        try:
            port = parsed.port
        except ValueError as exc:
            raise ProviderUnavailableError() from exc

        now = time.monotonic()
        connect_deadline = now + timeout if connect_deadline is None else connect_deadline
        first_event_deadline = now + timeout if first_event_deadline is None else first_event_deadline
        total_deadline = now + timeout if total_deadline is None else total_deadline
        connection_type = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_type(parsed.hostname, port, timeout=timeout)
        response: _OwnedHTTPResponse | None = None
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise ProviderCancelledError()
            connection.connect()
            sock = connection.sock
            if sock is None:
                raise ProviderUnreachableError()
            if cancel_event is not None and cancel_event.is_set():
                raise ProviderCancelledError()
            now = time.monotonic()
            if now >= connect_deadline:
                raise ProviderUnreachableError()
            if now >= first_event_deadline or now >= total_deadline:
                raise ProviderTimeoutUnknownError()
            sock.settimeout(min(first_event_deadline, total_deadline) - now)
            selector = parsed.path or "/"
            if parsed.query:
                selector += "?" + parsed.query
            connection.request(
                request.get_method(),
                selector,
                body=request.data,
                headers=dict(request.header_items()),
            )
            sock.settimeout(None)
            header_deadline = min(first_event_deadline, total_deadline)
            header_prefix, body_prefix = _read_response_headers(
                sock,
                deadline=header_deadline,
                cancel_event=cancel_event,
                max_context_bytes=max_context_bytes,
            )
            response_socket = _ResponseSocket(sock, header_prefix, body_prefix)
            response = _OwnedHTTPResponse(response_socket, connection)
            response.begin()
            response_socket.activate_body()
            response._contextox_socket = sock
            return response
        except ProviderError:
            if response is not None:
                response.close()
            else:
                connection.close()
            raise
        except (TimeoutError, socket.timeout) as exc:
            if response is not None:
                response.close()
            else:
                connection.close()
            if time.monotonic() >= min(first_event_deadline, total_deadline):
                raise ProviderTimeoutUnknownError() from exc
            raise ProviderUnreachableError() from exc
        except (http.client.HTTPException, OSError, ValueError) as exc:
            if response is not None:
                response.close()
            else:
                connection.close()
            raise ProviderUnavailableError() from exc


class DeepSeekProvider:
    """Fixed DeepSeek Chat Completions adapter with no automatic retry."""

    def __init__(self, *, model: str = DEFAULT_MODEL, transport: Any | None = None) -> None:
        if model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
            raise ValueError("model must be an approved DeepSeek model")
        self.model = model
        self.transport = transport if transport is not None else _UrllibTransport()

    @property
    def config(self) -> ProviderConfigSnapshot:
        return ProviderConfigSnapshot(
            endpoint_id="deepseek_chat_completions",
            model=self.model,
            thinking="enabled",
            reasoning_effort="high",
        )

    @staticmethod
    def opaque_user_id(workspace_id: str) -> str:
        """Return a stable isolation id without sending a business name."""

        import hashlib

        return "ws-" + hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()

    def build_payload(
        self,
        messages: list[dict[str, Any]],
        *,
        stream: bool,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        user_id: str,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build the exact HTTP JSON payload without sending it."""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "max_tokens": max_tokens,
            "stream": stream,
            "user_id": user_id,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if tools is not None:
            payload["tools"] = tools
        return payload

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        stream: bool,
        tools: list[dict[str, Any]] | None,
        max_tokens: int = MAX_OUTPUT_TOKENS,
        user_id: str,
        timeouts: ProviderTimeouts = ProviderTimeouts(),
        cancel_event: Any | None = None,
        max_context_bytes: int = MAX_CONTEXT_BYTES,
        on_content: Callable[[str], None] | None = None,
    ) -> ProviderCompletion:
        """Make one provider request and normalize its response.

        ``complete`` never retries.  A missing usage object is represented as
        ``usage=None`` so the caller can persist a bounded receipt before
        stopping the current attempt or Run.
        """

        if cancel_event is not None and cancel_event.is_set():
            raise ProviderCancelledError()
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ProviderNotConfiguredError()
        if type(max_tokens) is not int or max_tokens < 1 or max_tokens > MAX_OUTPUT_TOKENS:
            raise ValueError("max_tokens exceeds the fixed provider budget")
        if type(max_context_bytes) is not int or max_context_bytes < 1:
            raise ValueError("max_context_bytes must be positive")

        payload = self.build_payload(
            messages,
            stream=stream,
            tools=tools,
            max_tokens=max_tokens,
            user_id=user_id,
            response_format={"type": "json_object"} if not stream and tools is None else None,
        )
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProviderProtocolError() from exc
        if len(body) > max_context_bytes:
            raise ProviderContextBudgetError()

        request = Request(
            DEEPSEEK_ENDPOINT,
            data=body,
            headers={
                "Accept": "text/event-stream" if stream else "application/json",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started_at = time.monotonic()
        try:
            response = self._open(
                request,
                timeouts,
                started_at,
                cancel_event=cancel_event,
                max_context_bytes=max_context_bytes,
            )
        except ProviderError:
            raise
        except HTTPError as exc:
            raise self._http_error(exc.code) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise ProviderUnreachableError() from exc

        try:
            status = _response_status(response)
            if status != 200:
                raise self._http_error(status)
            if stream:
                return self._read_stream(
                    response,
                    started_at=started_at,
                    timeouts=timeouts,
                    cancel_event=cancel_event,
                    max_context_bytes=max_context_bytes,
                    on_content=on_content,
                )
            return self._read_nonstream(
                response,
                started_at=started_at,
                timeouts=timeouts,
                cancel_event=cancel_event,
                max_context_bytes=max_context_bytes,
            )
        except ProviderError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutUnknownError() from exc
        except UnicodeDecodeError as exc:
            raise ProviderProtocolError() from exc
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError() from exc
        except OSError as exc:
            if stream:
                raise ProviderStreamInterruptedError() from exc
            raise ProviderUnreachableError() from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def _open(
        self,
        request: Request,
        timeouts: ProviderTimeouts,
        started_at: float,
        *,
        cancel_event: Any | None,
        max_context_bytes: int,
    ) -> object:
        elapsed = time.monotonic() - started_at
        remaining_seconds = timeouts.total_ms / 1000 - elapsed
        first_event_remaining = timeouts.first_event_ms / 1000 - elapsed
        if remaining_seconds <= 0 or first_event_remaining <= 0:
            raise ProviderTimeoutUnknownError()
        if timeouts.connect_ms <= 0:
            raise ProviderUnreachableError()
        timeout_seconds = min(
            timeouts.connect_ms / 1000,
            first_event_remaining,
            remaining_seconds,
        )
        if timeout_seconds <= 0:
            raise ProviderTimeoutUnknownError()
        open_method = getattr(self.transport, "open", None)
        if isinstance(self.transport, _UrllibTransport) and callable(open_method):
            deadline = started_at + timeouts.total_ms / 1000
            response = open_method(
                request,
                timeout_seconds,
                connect_deadline=min(started_at + timeouts.connect_ms / 1000, deadline),
                first_event_deadline=min(started_at + timeouts.first_event_ms / 1000, deadline),
                total_deadline=deadline,
                cancel_event=cancel_event,
                max_context_bytes=max_context_bytes,
            )
        elif callable(open_method):
            response = open_method(request, timeout_seconds)
        elif callable(self.transport):
            response = self.transport(request, timeout_seconds)
        else:
            raise ProviderUnreachableError()
        if response is None:
            raise ProviderUnreachableError()
        return response

    @staticmethod
    def _response_reader(response: object) -> Callable[[int], object]:
        read1 = getattr(response, "read1", None)
        if callable(read1):
            return read1
        read = getattr(response, "read", None)
        if callable(read):
            return read
        raise ProviderProtocolError()

    @staticmethod
    def _set_response_read_timeout(response: object, timeout_seconds: float) -> None:
        """Apply a short socket deadline to the standard-library response.

        ``HTTPResponse`` exposes its socket through a small CPython wrapper
        chain.  The traversal is deliberately bounded and best-effort for
        injected test responses; production urllib responses expose
        ``settimeout`` on the underlying socket.
        """

        candidate: object | None = response
        seen: set[int] = set()
        for _ in range(8):
            if candidate is None or id(candidate) in seen:
                return
            seen.add(id(candidate))
            setter = getattr(candidate, "settimeout", None)
            if callable(setter):
                setter(timeout_seconds)
                return
            next_candidate = None
            for attribute in ("_sock", "sock", "raw", "fp"):
                value = getattr(candidate, attribute, None)
                if value is not None and id(value) not in seen:
                    next_candidate = value
                    break
            candidate = next_candidate

    def _read_response_chunk(
        self,
        response: object,
        *,
        started_at: float,
        last_event_at: float,
        saw_event: bool,
        timeouts: ProviderTimeouts,
        cancel_event: Any | None,
    ) -> object:
        read = self._response_reader(response)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ProviderCancelledError()
            is_closed = getattr(response, "isclosed", None)
            if callable(is_closed) and is_closed():
                return b""
            now = time.monotonic()
            total_remaining = timeouts.total_ms / 1000 - (now - started_at)
            phase_timeout = timeouts.idle_ms if saw_event else timeouts.first_event_ms
            phase_remaining = phase_timeout / 1000 - (now - last_event_at)
            if total_remaining <= 0 or phase_remaining <= 0:
                raise ProviderTimeoutUnknownError()
            read_timeout = min(
                total_remaining,
                phase_remaining,
                READ_POLL_INTERVAL_MS / 1000,
            )
            response_sock = _response_socket(response)
            if response_sock is not None:
                response_deadline = min(
                    started_at + timeouts.total_ms / 1000,
                    last_event_at + phase_timeout / 1000,
                )
                response_sock.settimeout(None)
                context_set = _set_response_read_context(
                    response,
                    deadline=response_deadline,
                    cancel_event=cancel_event,
                )
                if not context_set and not _response_has_buffered_bytes(response, response_sock):
                    _wait_for_socket_readable(
                        response_sock,
                        deadline=response_deadline,
                        cancel_event=cancel_event,
                    )
            else:
                self._set_response_read_timeout(response, read_timeout)
            try:
                return read(READ_CHUNK_BYTES)
            except (TimeoutError, socket.timeout):
                if response_sock is not None:
                    raise ProviderTimeoutUnknownError()
                continue

    @staticmethod
    def _http_error(status: int | None) -> ProviderError:
        if status == 401:
            return ProviderAuthError()
        if status == 402:
            return ProviderBalanceError()
        if status == 429:
            return ProviderRateLimitError()
        if status in {400, 422}:
            return ProviderRequestError()
        if status in {500, 503}:
            return ProviderUnavailableError()
        return ProviderUnavailableError()

    def _read_nonstream(
        self,
        response: object,
        *,
        started_at: float,
        timeouts: ProviderTimeouts,
        cancel_event: Any | None,
        max_context_bytes: int,
    ) -> ProviderCompletion:
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderCancelledError()
        if isinstance(response, (bytes, bytearray)):
            raw = bytes(response)
            if len(raw) > max_context_bytes:
                raise ProviderContextBudgetError()
        else:
            body = bytearray()
            saw_data = False
            last_data_at = started_at
            while True:
                chunk = self._read_response_chunk(
                    response,
                    started_at=started_at,
                    last_event_at=last_data_at,
                    saw_event=saw_data,
                    timeouts=timeouts,
                    cancel_event=cancel_event,
                )
                if isinstance(chunk, str):
                    try:
                        chunk = chunk.encode("utf-8")
                    except UnicodeEncodeError as exc:
                        raise ProviderProtocolError() from exc
                if not isinstance(chunk, (bytes, bytearray)):
                    raise ProviderProtocolError()
                chunk_bytes = bytes(chunk)
                if not chunk_bytes:
                    break
                if len(body) + len(chunk_bytes) > max_context_bytes:
                    raise ProviderContextBudgetError()
                body.extend(chunk_bytes)
                saw_data = True
                last_data_at = time.monotonic()
            raw = bytes(body)
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderCancelledError()
        payload = _strict_json_loads(raw.decode("utf-8"))
        return self._completion_from_nonstream(payload)

    def _completion_from_nonstream(self, payload: object) -> ProviderCompletion:
        if not isinstance(payload, dict):
            raise ProviderProtocolError()
        completion_id = payload.get("id")
        if not isinstance(completion_id, str) or not completion_id:
            raise ProviderProtocolError()
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProviderProtocolError()
        choice = choices[0]
        if type(choice.get("index")) is not int or choice["index"] != 0:
            raise ProviderProtocolError()
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str):
            raise ProviderProtocolError()
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderProtocolError()
        if message.get("role") != "assistant":
            raise ProviderProtocolError()
        content = _text_or_empty(message.get("content"), "content")
        reasoning = _text_or_empty(message.get("reasoning_content"), "reasoning_content")
        raw_calls = message.get("tool_calls", [])
        calls = self._complete_tool_calls(raw_calls)
        raw_usage = payload.get("usage")
        usage = None if raw_usage is None else _usage_from_payload(raw_usage)
        return ProviderCompletion(
            completion_id=completion_id,
            content=content,
            reasoning_content=reasoning,
            tool_calls=calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    @staticmethod
    def _complete_tool_calls(raw_calls: object) -> tuple[ProviderToolCall, ...]:
        if raw_calls is None:
            return ()
        if not isinstance(raw_calls, list):
            raise ProviderProtocolError()
        calls: list[ProviderToolCall] = []
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                raise ProviderProtocolError()
            if raw_call.get("type") != "function":
                raise ProviderProtocolError()
            call_id = raw_call.get("id")
            function = raw_call.get("function")
            if not isinstance(call_id, str) or not call_id or not isinstance(function, dict):
                raise ProviderProtocolError()
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, str):
                raise ProviderProtocolError()
            calls.append(ProviderToolCall(call_id=call_id, name=name, arguments=arguments))
        return tuple(calls)

    def _read_stream(
        self,
        response: object,
        *,
        started_at: float,
        timeouts: ProviderTimeouts,
        cancel_event: Any | None,
        max_context_bytes: int,
        on_content: Callable[[str], None] | None,
    ) -> ProviderCompletion:
        completion_id: str | None = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_slots: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: ProviderUsage | None = None
        saw_event = False
        done = False
        last_event_at = started_at
        content_cursor = 0
        final_usage_present = False
        transcript_bytes = [0]

        def read_chunk() -> object:
            return self._read_response_chunk(
                response,
                started_at=started_at,
                last_event_at=last_event_at,
                saw_event=saw_event,
                timeouts=timeouts,
                cancel_event=cancel_event,
            )

        try:
            for data in self._iter_sse_data(
                response,
                read_chunk=read_chunk,
                max_bytes=max_context_bytes,
            ):
                now = time.monotonic()
                if cancel_event is not None and cancel_event.is_set():
                    raise ProviderCancelledError()
                if now - started_at >= timeouts.total_ms / 1000:
                    raise ProviderTimeoutUnknownError()
                if now - last_event_at >= (
                    timeouts.first_event_ms if not saw_event else timeouts.idle_ms
                ) / 1000:
                    raise ProviderTimeoutUnknownError()
                if done:
                    raise ProviderProtocolError(usage=usage)
                if data == "[DONE]":
                    done = True
                    break
                if not data:
                    continue
                saw_event = True
                last_event_at = now
                chunk = _strict_json_loads(data)
                chunk_id, chunk_usage = self._consume_stream_chunk(
                    chunk,
                    tool_slots=tool_slots,
                    content_parts=content_parts,
                    reasoning_parts=reasoning_parts,
                    max_bytes=max_context_bytes,
                    used_bytes=transcript_bytes,
                )
                if completion_id is None:
                    completion_id = chunk_id
                elif completion_id != chunk_id:
                    raise ProviderProtocolError(usage=usage)
                if chunk_usage is not None:
                    usage = chunk_usage
                if tool_slots.get(-1, {}).get("finish_reason") is not None:
                    final_usage_present = chunk_usage is not None
                if on_content is not None and len(content_parts) > content_cursor:
                    # The callback receives only the newly appended delta.  A
                    # cursor avoids exposing or repeating prior content.
                    for pending in content_parts[content_cursor:]:
                        if pending:
                            on_content(pending)
                    content_cursor = len(content_parts)
        except ProviderError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutUnknownError() from exc
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError() from exc
        except OSError as exc:
            raise ProviderStreamInterruptedError() from exc

        if not done:
            now = time.monotonic()
            if now - started_at >= timeouts.total_ms / 1000 or now - last_event_at >= (
                timeouts.first_event_ms if not saw_event else timeouts.idle_ms
            ) / 1000:
                raise ProviderTimeoutUnknownError()
            raise ProviderStreamInterruptedError()
        if not saw_event or completion_id is None:
            raise ProviderProtocolError(usage=usage)
        finish_reason = tool_slots.get(-1, {}).get("finish_reason")
        if finish_reason is None:
            raise ProviderProtocolError(usage=usage)
        if not final_usage_present:
            usage = None
        calls = self._finish_stream_tool_calls(tool_slots)
        return ProviderCompletion(
            completion_id=completion_id,
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            tool_calls=calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    @staticmethod
    def _consume_stream_chunk(
        chunk: object,
        *,
        tool_slots: dict[int, dict[str, str]],
        content_parts: list[str],
        reasoning_parts: list[str],
        max_bytes: int,
        used_bytes: list[int],
    ) -> tuple[str, ProviderUsage | None]:
        if not isinstance(chunk, dict):
            raise ProviderProtocolError()
        chunk_id = chunk.get("id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ProviderProtocolError()
        choices = chunk.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProviderProtocolError()
        choice = choices[0]
        if type(choice.get("index")) is not int or choice["index"] != 0:
            raise ProviderProtocolError()
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            raise ProviderProtocolError()
        role = delta.get("role")
        if role is not None and role != "assistant":
            raise ProviderProtocolError()
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise ProviderProtocolError()
        existing_reason = tool_slots.setdefault(-1, {}).get("finish_reason")
        if finish_reason is not None:
            if existing_reason is not None and existing_reason != finish_reason:
                raise ProviderProtocolError()
            tool_slots[-1]["finish_reason"] = finish_reason
        content = delta.get("content")
        reasoning = delta.get("reasoning_content")
        if content is not None and not isinstance(content, str):
            raise ProviderProtocolError()
        if reasoning is not None and not isinstance(reasoning, str):
            raise ProviderProtocolError()
        _bounded_text_append(
            content_parts,
            content or "",
            max_bytes=max_bytes,
            used_bytes=used_bytes,
        )
        _bounded_text_append(
            reasoning_parts,
            reasoning or "",
            max_bytes=max_bytes,
            used_bytes=used_bytes,
        )
        raw_tool_calls = delta.get("tool_calls")
        if raw_tool_calls is not None:
            if not isinstance(raw_tool_calls, list):
                raise ProviderProtocolError()
            for raw_call in raw_tool_calls:
                if not isinstance(raw_call, dict):
                    raise ProviderProtocolError()
                index = raw_call.get("index")
                if type(index) is not int or index < 0:
                    raise ProviderProtocolError()
                slot = tool_slots.setdefault(index, {})
                call_id = raw_call.get("id")
                if call_id is not None:
                    if not isinstance(call_id, str):
                        raise ProviderProtocolError()
                    previous = slot.get("call_id")
                    if previous is not None and previous != call_id:
                        raise ProviderProtocolError()
                    slot["call_id"] = call_id
                call_type = raw_call.get("type")
                if call_type is not None and call_type != "function":
                    raise ProviderProtocolError()
                function = raw_call.get("function")
                if function is not None:
                    if not isinstance(function, dict):
                        raise ProviderProtocolError()
                    name = function.get("name")
                    arguments = function.get("arguments")
                    if name is not None:
                        if not isinstance(name, str):
                            raise ProviderProtocolError()
                        slot["name"] = _bounded_text_concat(
                            slot.get("name", ""),
                            name,
                            max_bytes=max_bytes,
                            used_bytes=used_bytes,
                        )
                    if arguments is not None:
                        if not isinstance(arguments, str):
                            raise ProviderProtocolError()
                        slot["arguments"] = _bounded_text_concat(
                            slot.get("arguments", ""),
                            arguments,
                            max_bytes=max_bytes,
                            used_bytes=used_bytes,
                        )
        raw_usage = chunk.get("usage")
        if raw_usage is None:
            return chunk_id, None
        try:
            return chunk_id, _usage_from_payload(raw_usage)
        except ValueError as exc:
            raise ProviderProtocolError() from exc

    @staticmethod
    def _finish_stream_tool_calls(
        tool_slots: dict[int, dict[str, str]],
    ) -> tuple[ProviderToolCall, ...]:
        indexes = sorted(index for index in tool_slots if index >= 0)
        if indexes and indexes != list(range(len(indexes))):
            raise ProviderProtocolError()
        calls: list[ProviderToolCall] = []
        for index in indexes:
            slot = tool_slots[index]
            call_id = slot.get("call_id")
            name = slot.get("name")
            arguments = slot.get("arguments")
            if not call_id or name is None or arguments is None:
                raise ProviderProtocolError()
            calls.append(ProviderToolCall(call_id=call_id, name=name, arguments=arguments))
        return tuple(calls)

    @staticmethod
    def _iter_sse_data(
        response: object,
        *,
        read_chunk: Callable[[], object] | None = None,
        max_bytes: int | None = None,
    ) -> Iterable[str]:
        """Yield complete SSE data events from arbitrarily split byte chunks."""

        iterator = getattr(response, "iter_bytes", None)
        if callable(iterator):
            chunks = iterator()
        else:
            read = getattr(response, "read", None)
            if callable(read) or read_chunk is not None:
                def read_chunks() -> Iterable[bytes]:
                    while True:
                        chunk = read_chunk() if read_chunk is not None else read(READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        yield chunk

                chunks = read_chunks()
            elif isinstance(response, Iterable):
                chunks = response
            else:
                raise ProviderProtocolError()

        decoder = __import__("codecs").getincrementaldecoder("utf-8")()
        buffer = ""
        data_lines: list[str] = []
        received_bytes = 0
        for chunk in chunks:
            if isinstance(chunk, str):
                try:
                    chunk_bytes = len(chunk.encode("utf-8"))
                except UnicodeEncodeError as exc:
                    raise ProviderProtocolError() from exc
                text = chunk
            elif isinstance(chunk, (bytes, bytearray)):
                raw_chunk = bytes(chunk)
                chunk_bytes = len(raw_chunk)
                text = decoder.decode(raw_chunk, final=False)
            else:
                raise ProviderProtocolError()
            if max_bytes is not None and received_bytes + chunk_bytes > max_bytes:
                raise ProviderContextBudgetError()
            received_bytes += chunk_bytes
            buffer += text
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line.endswith("\r"):
                    line = line[:-1]
                if line == "":
                    if data_lines:
                        yield "\n".join(data_lines)
                        data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    value = line[5:]
                    data_lines.append(value[1:] if value.startswith(" ") else value)
                    continue
                raise ProviderProtocolError()
        buffer += decoder.decode(b"", final=True)
        if buffer:
            if buffer.endswith("\r"):
                buffer = buffer[:-1]
            if buffer.startswith("data:"):
                value = buffer[5:]
                data_lines.append(value[1:] if value.startswith(" ") else value)
            elif not buffer.startswith(":"):
                raise ProviderProtocolError()
        if data_lines:
            yield "\n".join(data_lines)
