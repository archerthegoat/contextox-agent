"""Single-request semantic proposal boundary for the deterministic R1 controller."""

from __future__ import annotations

import json
from threading import Event
from typing import Any, Protocol

from pydantic import ValidationError

from contextox.model_tools import ContextPlanV1, SemanticProposalV1, ToolAdapter
from contextox.models import ContextSnapshot, RunBudget, canonical_sha256
from contextox.provider import ProviderCompletion, ProviderTimeouts


SEMANTIC_CONTEXT_MAX_BYTES = 65_536
SEMANTIC_MESSAGE_MAX_BYTES = 60 * 1_024
SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS = 70_000
EMPTY_TOOL_SCHEMA_SHA256 = canonical_sha256({"tools": []})

_SCHEMA_TEXT = json.dumps(
    SemanticProposalV1.model_json_schema(),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
P0_SEMANTIC_PROPOSAL = """You are the semantic proposal stage of ContextOx (数契).
Return exactly one JSON object matching the supplied SemanticProposalV1 schema. Do not call tools.
Use only the current ContextPlanV1. Preserve unknown business meaning as null plus an explicit unknown reason.
Use only supplied opaque handles. Every observed source claim and candidate supported by source data must carry a matching evidence handle.
Task instructions and approved human answers are business provenance, not observed source evidence. Never invent approval or Mission completion.
Choose one action: answer_only, draft_and_clarify, draft_and_submit, or clarify_only. The application validates and applies the proposal atomically.
""" + _SCHEMA_TEXT
P0_SEMANTIC_PROPOSAL_SHA256 = canonical_sha256({"text": P0_SEMANTIC_PROPOSAL})


class SemanticProvider(Protocol):
    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> ProviderCompletion: ...


class SemanticProposalFailure(Exception):
    """Safe failure raised before any domain write occurs."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _source_plans(adapter: ToolAdapter, snapshot: ContextSnapshot, store: Any) -> list[dict[str, Any]]:
    artifacts = {
        ref.revision_id: store.get_source_artifact(snapshot.mission.workspace_id, ref.revision_id)
        for ref in snapshot.run.source_refs
    }
    plans: list[dict[str, Any]] = []
    for source in adapter.catalog:
        source_plan = dict(source)
        revision_id = adapter.resolve(source["source_handle"], "source").revision_id
        artifact = artifacts[revision_id]
        profiles: dict[str, dict[str, Any]] = {}
        for table in artifact.tables:
            profiles[table.table_id] = {
                "row_count": table.row_count,
                "duplicate_row_count": table.duplicate_row_count,
                "columns": [column.model_dump(mode="json") for column in table.columns],
                "evidence_handles": [adapter.evidence(ref) for ref in table.source_refs],
                "sample_rows": [
                    {
                        "row_number": row.row_number,
                        "cells": [cell.model_dump(mode="json") for cell in row.cells],
                        "evidence_handles": [adapter.evidence(ref) for ref in row.source_refs],
                    }
                    for row in table.sample_rows
                ],
            }
        source_plan["profiles"] = profiles
        plans.append(source_plan)
    return plans


def build_context_plan(
    snapshot: ContextSnapshot,
    store: Any,
) -> tuple[ContextPlanV1, ToolAdapter]:
    """Project authoritative state and bounded profiles into one model packet."""

    adapter = ToolAdapter(snapshot, store)
    current = adapter.context(snapshot)
    plan = ContextPlanV1(
        context_kind="semantic_context_v1",
        mission=current["mission"],
        message_context=current["message_context"],
        sources=_source_plans(adapter, snapshot, store),
        draft=current["draft"],
        clarifications=current["clarifications"],
        approved_answers=current["approved_answers"],
    )
    return plan, adapter


def semantic_messages(plan: ContextPlanV1) -> list[dict[str, Any]]:
    messages = [
        {"role": "system", "content": P0_SEMANTIC_PROPOSAL},
        {
            "role": "user",
            "content": json.dumps(
                plan.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        },
    ]
    size = len(
        json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    if size > SEMANTIC_MESSAGE_MAX_BYTES:
        raise SemanticProposalFailure("context_too_broad")
    return messages


def request_semantic_proposal(
    provider: SemanticProvider,
    plan: ContextPlanV1,
    budget: RunBudget,
    *,
    user_id: str,
    cancel_event: Event,
) -> tuple[SemanticProposalV1, ProviderCompletion]:
    """Request and validate one proposal without performing any domain operation."""

    messages = semantic_messages(plan)
    completion = provider.complete(
        messages,
        stream=False,
        tools=None,
        max_tokens=budget.max_output_tokens,
        user_id=user_id,
        timeouts=ProviderTimeouts(
            connect_ms=budget.connect_timeout_ms,
            first_event_ms=min(budget.first_event_timeout_ms, SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS),
            idle_ms=min(budget.idle_timeout_ms, SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS),
            total_ms=SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS,
        ),
        cancel_event=cancel_event,
        max_context_bytes=SEMANTIC_CONTEXT_MAX_BYTES,
    )
    if not isinstance(completion, ProviderCompletion):
        raise SemanticProposalFailure("provider_protocol_error")
    if completion.finish_reason != "stop" or completion.tool_calls:
        raise SemanticProposalFailure("provider_protocol_error")
    try:
        decoded = json.loads(completion.content)
        proposal = SemanticProposalV1.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise SemanticProposalFailure("semantic_proposal_invalid") from exc
    return proposal, completion
