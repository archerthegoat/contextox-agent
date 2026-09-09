"""Bounded non-thinking interpretation of deterministic source profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from threading import Event
from typing import Any, Protocol

from pydantic import Field, ValidationError

from contextox.models import (
    ContextOxModel,
    ProfileColumnInterpretationV1,
    ProfileInterpretationV1,
    ProfilePackV1,
    ProfileRelationshipHintV1,
    Text,
    canonical_sha256,
)
from contextox.provider import (
    DeepSeekProvider,
    ProviderCancelledError,
    ProviderCompletion,
    ProviderError,
    ProviderTimeouts,
    ProviderUsage,
)


PROFILE_CHUNK_MAX_BYTES = 32 * 1024
PROFILE_CHUNK_MAX_COUNT = 4
PROFILE_PROVIDER_TIMEOUT_MS = 30_000
PROFILE_OUTPUT_TOKENS = 4096


class _ChunkOutput(ContextOxModel):
    columns: list[ProfileColumnInterpretationV1] = Field(max_length=100)
    relationship_hints: list[ProfileRelationshipHintV1] = Field(max_length=25)
    unknown_items: list[Text] = Field(max_length=25)


_OUTPUT_SCHEMA = json.dumps(
    _ChunkOutput.model_json_schema(),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
PROFILE_INTERPRETATION_P0 = """You interpret a deterministic ContextOx ProfilePack slice.
Return exactly one JSON object matching the supplied schema. Do not call tools.
Classify only supplied columns. Business meanings and relationships are candidates, never approved facts.
Preserve ambiguity in unknown_items. Do not invent rows, values, joins, approvals, or source content.
""" + _OUTPUT_SCHEMA
PROFILE_INTERPRETATION_P0_SHA256 = canonical_sha256(
    {"text": PROFILE_INTERPRETATION_P0}
)


class ProfileInterpretationProvider(Protocol):
    @property
    def config(self) -> Any: ...

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> ProviderCompletion: ...


class ProfileInterpretationFailure(Exception):
    def __init__(self, code: str, *, usage: ProviderUsage | None = None) -> None:
        self.code = code
        self.usage = usage
        super().__init__(code)


@dataclass(frozen=True)
class ProfileInterpretationResult:
    interpretation: ProfileInterpretationV1
    sent_bytes: int
    chunk_count: int
    input_tokens: int | None
    output_tokens: int | None


def get_profile_provider() -> DeepSeekProvider:
    return DeepSeekProvider(thinking="disabled", reasoning_effort=None)


def profile_provider_config():
    return get_profile_provider().config


def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": PROFILE_INTERPRETATION_P0},
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        },
    ]


def _wire_bytes(messages: list[dict[str, Any]]) -> int:
    return len(json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def _chunk_wire_bytes(payload: dict[str, Any]) -> int:
    """Reserve the largest bounded chunk metadata before accepting a unit."""

    return _wire_bytes(_messages({
        **payload,
        "chunk_index": PROFILE_CHUNK_MAX_COUNT,
        "covered_chunks": PROFILE_CHUNK_MAX_COUNT,
        "total_chunks": 999,
    }))


def profile_chunks(pack: ProfilePackV1) -> list[dict[str, Any]]:
    """Greedily split ordered column statistics into bounded Provider packets."""

    units = [
        {
            "table_id": table.table_id,
            "row_count": table.row_count,
            "duplicate_row_count": table.duplicate_row_count,
            "column": column.model_dump(mode="json"),
        }
        for table in pack.tables
        for column in table.columns
    ]
    base = {
        "profile_hash": pack.profile_hash,
        "parse_status": pack.parse_status,
        "recognized_table_rows_complete": pack.recognized_table_rows_complete,
        "stats_mode": pack.stats_mode,
        "limitations": pack.limitations,
    }
    if not units:
        payload = {**base, "items": []}
        if _chunk_wire_bytes(payload) > PROFILE_CHUNK_MAX_BYTES:
            raise ProfileInterpretationFailure("profile_context_too_broad")
        return [payload]

    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for unit in units:
        candidate = {**base, "items": [*current, unit]}
        if _chunk_wire_bytes(candidate) <= PROFILE_CHUNK_MAX_BYTES:
            current.append(unit)
            continue
        if not current:
            raise ProfileInterpretationFailure("profile_context_too_broad")
        chunks.append({**base, "items": current})
        current = [unit]
        if _chunk_wire_bytes({**base, "items": current}) > PROFILE_CHUNK_MAX_BYTES:
            raise ProfileInterpretationFailure("profile_context_too_broad")
    chunks.append({**base, "items": current})
    return chunks


def interpret_profile(
    provider: ProfileInterpretationProvider,
    pack: ProfilePackV1,
    *,
    user_id: str,
    cancel_event: Event,
) -> ProfileInterpretationResult:
    chunks = profile_chunks(pack)
    selected = chunks[:PROFILE_CHUNK_MAX_COUNT]
    allowed_columns = {
        (item["table_id"], item["column"]["name"])
        for chunk in selected
        for item in chunk["items"]
    }
    allowed_tables = {table.table_id for table in pack.tables}
    columns: list[ProfileColumnInterpretationV1] = []
    relationships: list[ProfileRelationshipHintV1] = []
    unknowns: list[str] = []
    sent_bytes = 0
    input_tokens = 0
    output_tokens = 0
    usage_complete = True
    for index, chunk in enumerate(selected, start=1):
        if cancel_event.is_set():
            raise ProfileInterpretationFailure("cancelled")
        payload = {
            **chunk,
            "chunk_index": index,
            "covered_chunks": len(selected),
            "total_chunks": len(chunks),
        }
        messages = _messages(payload)
        wire_bytes = _wire_bytes(messages)
        if wire_bytes > PROFILE_CHUNK_MAX_BYTES:
            raise ProfileInterpretationFailure("profile_context_too_broad")
        sent_bytes += wire_bytes
        completion = provider.complete(
            messages,
            stream=False,
            tools=None,
            max_tokens=PROFILE_OUTPUT_TOKENS,
            user_id=user_id,
            response_format={"type": "json_object"},
            timeouts=ProviderTimeouts(
                connect_ms=10_000,
                first_event_ms=PROFILE_PROVIDER_TIMEOUT_MS,
                idle_ms=PROFILE_PROVIDER_TIMEOUT_MS,
                total_ms=PROFILE_PROVIDER_TIMEOUT_MS,
            ),
            cancel_event=cancel_event,
            max_context_bytes=PROFILE_CHUNK_MAX_BYTES,
        )
        if (
            not isinstance(completion, ProviderCompletion)
            or completion.finish_reason != "stop"
            or completion.tool_calls
        ):
            raise ProfileInterpretationFailure(
                "profile_provider_protocol_error",
                usage=completion.usage if isinstance(completion, ProviderCompletion) else None,
            )
        try:
            output = _ChunkOutput.model_validate(json.loads(completion.content))
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise ProfileInterpretationFailure(
                "profile_interpretation_invalid", usage=completion.usage
            ) from exc
        if any((item.table_id, item.column_name) not in allowed_columns for item in output.columns):
            raise ProfileInterpretationFailure(
                "profile_interpretation_invalid", usage=completion.usage
            )
        if any(
            item.left_table_id not in allowed_tables
            or item.right_table_id not in allowed_tables
            for item in output.relationship_hints
        ):
            raise ProfileInterpretationFailure(
                "profile_interpretation_invalid", usage=completion.usage
            )
        columns.extend(output.columns)
        relationships.extend(output.relationship_hints)
        unknowns.extend(output.unknown_items)
        if completion.usage is None:
            usage_complete = False
        else:
            input_tokens += completion.usage.input_tokens
            output_tokens += completion.usage.output_tokens

    column_keys = [(item.table_id, item.column_name) for item in columns]
    if len(set(column_keys)) != len(column_keys):
        raise ProfileInterpretationFailure("profile_interpretation_invalid")
    relationship_keys = [canonical_sha256(item) for item in relationships]
    if len(set(relationship_keys)) != len(relationship_keys):
        raise ProfileInterpretationFailure("profile_interpretation_invalid")
    interpretation = ProfileInterpretationV1(
        version="v1",
        profile_hash=pack.profile_hash,
        partial=len(selected) < len(chunks),
        covered_chunks=len(selected),
        total_chunks=len(chunks),
        columns=columns,
        relationship_hints=relationships,
        unknown_items=list(dict.fromkeys(unknowns)),
    )
    return ProfileInterpretationResult(
        interpretation=interpretation,
        sent_bytes=sent_bytes,
        chunk_count=len(selected),
        input_tokens=input_tokens if usage_complete else None,
        output_tokens=output_tokens if usage_complete else None,
    )


def run_profile_interpretation(
    store: Any,
    workspace_id: str,
    revision_id: str,
    attempt_id: str,
    cancel_event: Event,
) -> None:
    attempt = store.mark_profile_interpretation_running(
        workspace_id, revision_id, attempt_id
    )
    if attempt.status != "running":
        return
    provider = get_profile_provider()
    if provider.config != attempt.config:
        store.fail_profile_interpretation_attempt(
            workspace_id, revision_id, attempt_id, "failed",
            "profile_provider_config_invalid",
        )
        return
    try:
        result = interpret_profile(
            provider,
            store.get_source_profile(workspace_id, revision_id),
            user_id=provider.opaque_user_id(workspace_id),
            cancel_event=cancel_event,
        )
    except ProviderCancelledError as exc:
        store.fail_profile_interpretation_attempt(
            workspace_id, revision_id, attempt_id, "cancelled", exc.code
        )
        return
    except ProviderError as exc:
        status = exc.run_status if exc.run_status in {"blocked", "failed", "cancelled"} else "failed"
        store.fail_profile_interpretation_attempt(
            workspace_id, revision_id, attempt_id, status, exc.code
        )
        return
    except ProfileInterpretationFailure as exc:
        status = "cancelled" if exc.code == "cancelled" else (
            "blocked" if exc.code == "profile_context_too_broad" else "failed"
        )
        store.fail_profile_interpretation_attempt(
            workspace_id, revision_id, attempt_id, status, exc.code
        )
        return
    store.save_profile_interpretation_result(
        workspace_id,
        revision_id,
        attempt_id,
        result.interpretation,
        sent_bytes=result.sent_bytes,
        chunk_count=result.chunk_count,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
