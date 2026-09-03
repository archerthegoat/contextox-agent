"""Small, fail-closed DeepSeek Chat Completions boundary.

The provider boundary deliberately uses the Python standard library.  It owns
HTTP and protocol parsing only; ContextOx's Agent loop owns context selection,
permissions, tool execution, persistence, and terminal semantics.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from contextox.models import ProviderConfigSnapshot


DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
MAX_OUTPUT_TOKENS = 4096
MAX_CONTEXT_BYTES = 262144


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


def _response_status(response: object) -> int | None:
    status = getattr(response, "status", None)
    if type(status) is int:
        return status
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        value = getcode()
        return value if type(value) is int else None
    return None


class _UrllibTransport:
    """The only production transport; tests inject a local fake object."""

    def open(self, request: Request, timeout: float) -> object:
        return urlopen(request, timeout=timeout)


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
            response = self._open(request, timeouts, started_at)
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
                    on_content=on_content,
                )
            return self._read_nonstream(response, cancel_event=cancel_event)
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

    def _open(self, request: Request, timeouts: ProviderTimeouts, started_at: float) -> object:
        remaining_seconds = timeouts.total_ms / 1000 - (time.monotonic() - started_at)
        if remaining_seconds <= 0:
            raise ProviderTimeoutUnknownError()
        timeout_seconds = min(
            max(0.001, timeouts.connect_ms / 1000),
            remaining_seconds,
        )
        open_method = getattr(self.transport, "open", None)
        if callable(open_method):
            response = open_method(request, timeout_seconds)
        elif callable(self.transport):
            response = self.transport(request, timeout_seconds)
        else:
            raise ProviderUnreachableError()
        if response is None:
            raise ProviderUnreachableError()
        return response

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

    def _read_nonstream(self, response: object, *, cancel_event: Any | None) -> ProviderCompletion:
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderCancelledError()
        read = getattr(response, "read", None)
        if callable(read):
            raw = read()
        elif isinstance(response, (bytes, bytearray)):
            raw = bytes(response)
        else:
            raise ProviderProtocolError()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, (bytes, bytearray)):
            raise ProviderProtocolError()
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderCancelledError()
        payload = _strict_json_loads(bytes(raw).decode("utf-8"))
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

        try:
            for data in self._iter_sse_data(response):
                now = time.monotonic()
                if cancel_event is not None and cancel_event.is_set():
                    raise ProviderCancelledError()
                if now - started_at > timeouts.total_ms / 1000:
                    raise ProviderTimeoutUnknownError()
                if now - last_event_at > (
                    timeouts.first_event_ms if not saw_event else timeouts.idle_ms
                ) / 1000:
                    raise ProviderTimeoutUnknownError()
                if done:
                    raise ProviderProtocolError(usage=usage)
                if data == "[DONE]":
                    done = True
                    continue
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
        if content:
            content_parts.append(content)
        else:
            content_parts.append("")
        if reasoning:
            reasoning_parts.append(reasoning)
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
                        slot["name"] = slot.get("name", "") + name
                    if arguments is not None:
                        if not isinstance(arguments, str):
                            raise ProviderProtocolError()
                        slot["arguments"] = slot.get("arguments", "") + arguments
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
    def _iter_sse_data(response: object) -> Iterable[str]:
        """Yield complete SSE data events from arbitrarily split byte chunks."""

        iterator = getattr(response, "iter_bytes", None)
        if callable(iterator):
            chunks = iterator()
        else:
            read = getattr(response, "read", None)
            if callable(read):
                def read_chunks() -> Iterable[bytes]:
                    while True:
                        chunk = read(8192)
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
        for chunk in chunks:
            if isinstance(chunk, str):
                text = chunk
            elif isinstance(chunk, (bytes, bytearray)):
                text = decoder.decode(bytes(chunk), final=False)
            else:
                raise ProviderProtocolError()
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
