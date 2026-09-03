import json
import os
import unittest
from threading import Event
from unittest.mock import patch

from contextox.provider import (
    DEEPSEEK_ENDPOINT,
    DeepSeekProvider,
    ProviderAuthError,
    ProviderContextBudgetError,
    ProviderProtocolError,
    ProviderStreamInterruptedError,
    ProviderUsage,
)


class FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"", chunks: list[bytes] | None = None) -> None:
        self.status = status
        self.body = body
        self.chunks = chunks or []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size != -1:
            raise AssertionError("stream tests should use iter_bytes")
        return self.body

    def iter_bytes(self):
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


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


if __name__ == "__main__":
    unittest.main()
