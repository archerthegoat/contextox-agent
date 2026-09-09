"""Single-request semantic proposal boundary for the deterministic R1 controller."""

from __future__ import annotations

import json
from threading import Event
from typing import Any, Protocol

from pydantic import ValidationError

from contextox.model_tools import (
    CandidateRejected,
    ContextPlanV1,
    HandleDenied,
    SemanticProposalV1,
    ToolAdapter,
)
from contextox.models import (
    ClarificationQuestion,
    ColumnRef,
    ContextSnapshot,
    DefinitionField,
    EvidenceRef,
    RelationshipCandidate,
    RunBudget,
    SemanticApplicationInput,
    canonical_sha256,
)
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

    def __init__(self, code: str, completion: ProviderCompletion | None = None) -> None:
        self.code = code
        self.completion = completion
        super().__init__(code)


def _resolved_evidence(adapter: ToolAdapter, handles: list[str]) -> list[EvidenceRef]:
    return [EvidenceRef.model_validate(adapter.resolve(handle, "evidence")) for handle in handles]


def _projected_draft(
    adapter: ToolAdapter,
    proposal: SemanticProposalV1,
) -> tuple[list[DefinitionField], list[RelationshipCandidate], list[str]]:
    current = adapter.current_draft
    fields = {item.field_key: item for item in current.fields} if current else {}
    relationships = {
        item.relationship_key: item for item in current.relationships
    } if current else {}
    normalized_fields = [DefinitionField.model_validate(adapter.field(item)) for item in proposal.fields]
    normalized_relationships = [
        RelationshipCandidate.model_validate(adapter.relationship(item))
        for item in proposal.relationships
    ]
    adapter.validate_update(
        [item.model_dump(mode="json") for item in normalized_fields],
        [item.model_dump(mode="json") for item in normalized_relationships],
    )
    fields.update({item.field_key: item for item in normalized_fields})
    relationships.update({item.relationship_key: item for item in normalized_relationships})
    unresolved = list(proposal.unresolved_items)
    return list(fields.values()), list(relationships.values()), unresolved


def _question_contract(
    adapter: ToolAdapter,
    fields: list[DefinitionField],
    relationships: list[RelationshipCandidate],
    unresolved_items: list[str],
    proposal: SemanticProposalV1,
) -> list[ClarificationQuestion]:
    valid_paths: set[str] = set()
    obligations: dict[str, bool] = {}
    for collection, items, key_name in (
        ("fields", fields, "field_key"),
        ("relationships", relationships, "relationship_key"),
    ):
        for object_index, item in enumerate(items):
            item_key = getattr(item, key_name)
            root = f"{collection}.{item_key}"
            if len(root) <= 128:
                valid_paths.add(root)
            valid_paths.update(
                candidate
                for name in type(item).model_fields
                if len(candidate := f"{root}.{name}") <= 128
            )
            for unknown_index, unknown in enumerate(item.unknowns):
                property_path = unknown.property_path
                if collection == "relationships":
                    property_path = property_path.removeprefix(f"{item_key}.")
                if (
                    (collection, item_key, property_path)
                    in adapter.approved_unknown_targets
                    and getattr(item, property_path, None) is None
                ):
                    continue
                path = f"{root}.{property_path}"
                if len(path) > 128:
                    path = f"{collection}.{object_index}.unknowns.{unknown_index}"
                valid_paths.add(path)
                obligations[path] = collection == "fields"
    for index, _item in enumerate(unresolved_items):
        path = f"unresolved_items.{index}"
        valid_paths.add(path)
        obligations[path] = False

    if proposal.action == "draft_and_submit" and obligations:
        raise SemanticProposalFailure("semantic_clarification_required")
    if proposal.action == "draft_and_clarify" and not obligations:
        raise SemanticProposalFailure("semantic_proposal_invalid")

    covered: set[str] = set()
    questions: list[ClarificationQuestion] = []
    for question in proposal.questions:
        paths = list(question.related_definition_paths)
        if any(path not in valid_paths for path in paths):
            raise SemanticProposalFailure("semantic_definition_path_invalid")
        covered_obligations = set(paths).intersection(obligations)
        if covered_obligations and (
            not question.suggested_owner_role
            or not question.suggested_owner_role.strip()
            or not question.evidence_requested
            or any(not item.strip() for item in question.evidence_requested)
        ):
            raise SemanticProposalFailure("semantic_handoff_incomplete")
        if (
            question.blocking_impact != "blocking"
            and any(obligations[path] for path in covered_obligations)
        ):
            raise SemanticProposalFailure("semantic_clarification_impact_conflict")
        covered.update(covered_obligations)
        values = question.model_dump(mode="json", exclude={"evidence_handles"})
        values["source_refs"] = [
            item.model_dump(mode="json")
            for item in _resolved_evidence(adapter, question.evidence_handles)
        ]
        questions.append(ClarificationQuestion.model_validate(values))
    if set(obligations) - covered:
        raise SemanticProposalFailure("semantic_clarification_coverage_incomplete")
    return questions


def normalize_semantic_proposal(
    adapter: ToolAdapter,
    proposal: SemanticProposalV1,
) -> SemanticApplicationInput:
    """Resolve opaque capabilities and validate the complete projected result."""

    try:
        if proposal.action in {"draft_and_clarify", "draft_and_submit"}:
            fields, relationships, unresolved_items = _projected_draft(adapter, proposal)
        else:
            current = adapter.current_draft
            fields = []
            relationships = []
            unresolved_items = []
            if proposal.action == "clarify_only" and current is None:
                raise SemanticProposalFailure("semantic_draft_required")
        if proposal.action == "answer_only":
            questions: list[ClarificationQuestion] = []
        else:
            if proposal.action == "clarify_only":
                projected_fields = list(adapter.current_draft.fields)
                projected_relationships = list(adapter.current_draft.relationships)
                projected_unresolved = list(adapter.current_draft.unresolved_items)
            else:
                projected_fields = fields
                projected_relationships = relationships
                projected_unresolved = unresolved_items
            questions = _question_contract(
                adapter,
                projected_fields,
                projected_relationships,
                projected_unresolved,
                proposal,
            )
        pair = adapter.draft_pair(adapter.current_draft)
        return SemanticApplicationInput(
            action=proposal.action,
            public_answer=proposal.public_answer,
            expected_version=pair["version"],
            expected_sha256=pair["sha256"],
            fields=fields,
            relationships=relationships,
            unresolved_items=unresolved_items,
            questions=questions,
            source_refs=_resolved_evidence(adapter, proposal.evidence_handles),
        )
    except SemanticProposalFailure:
        raise
    except HandleDenied as exc:
        raise SemanticProposalFailure("semantic_handle_invalid") from exc
    except (CandidateRejected, ValidationError, TypeError, ValueError) as exc:
        raise SemanticProposalFailure("semantic_proposal_invalid") from exc


def _source_plans(adapter: ToolAdapter, snapshot: ContextSnapshot, store: Any) -> list[dict[str, Any]]:
    packs = {
        ref.revision_id: store.get_source_profile(snapshot.mission.workspace_id, ref.revision_id)
        for ref in snapshot.run.source_refs
    }
    plans: list[dict[str, Any]] = []
    for source in adapter.catalog:
        source_plan = dict(source)
        revision_id = adapter.resolve(source["source_handle"], "source").revision_id
        pack = packs[revision_id]
        profile = pack.model_dump(mode="json", exclude={"source_ref", "tables", "relationships"})
        profile["tables"] = [
            {
                **table.model_dump(mode="json", exclude={"source_refs"}),
                "evidence_handles": [adapter.evidence(ref) for ref in table.source_refs],
            }
            for table in pack.tables
        ]
        profile["relationships"] = []
        source_plan["profile_pack"] = profile
        interpretation = store.get_cached_profile_interpretation(
            snapshot.mission.workspace_id, revision_id, pack.profile_hash
        )
        source_plan["profile_interpretation"] = (
            None if interpretation is None
            else interpretation.model_dump(mode="json")
        )
        plans.append(source_plan)
    return plans


def _relationship_plans(
    adapter: ToolAdapter,
    snapshot: ContextSnapshot,
    store: Any,
) -> list[dict[str, Any]]:
    profiles = store.get_prospective_relationship_profiles(
        snapshot.mission.workspace_id,
        [ref.revision_id for ref in snapshot.run.source_refs],
    )
    plans: list[dict[str, Any]] = []
    for profile in profiles:
        left_columns = [
            adapter.column(
                ColumnRef(
                    source_ref=profile.left.source_ref,
                    table_id=profile.left.table_id,
                    column=column,
                )
            )
            for column in profile.left.columns
        ]
        right_columns = [
            adapter.column(
                ColumnRef(
                    source_ref=profile.right.source_ref,
                    table_id=profile.right.table_id,
                    column=column,
                )
            )
            for column in profile.right.columns
        ]
        plans.append(
            {
                **profile.model_dump(
                    mode="json", exclude={"left", "right", "source_refs"}
                ),
                "left": adapter.table(profile.left),
                "right": adapter.table(profile.right),
                "left_column_handles": left_columns,
                "right_column_handles": right_columns,
                "evidence_handles": [
                    adapter.evidence(ref) for ref in profile.source_refs
                ],
            }
        )
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
        prospective_relationships=_relationship_plans(adapter, snapshot, store),
        draft=current["draft"],
        clarifications=current["clarifications"],
        approved_answers=current["approved_answers"],
    )
    return plan, adapter


def _render_semantic_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": P0_SEMANTIC_PROPOSAL},
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


def _messages_size(messages: list[dict[str, Any]]) -> int:
    return len(
        json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def semantic_messages(plan: ContextPlanV1) -> list[dict[str, Any]]:
    payload = plan.model_dump(mode="json")
    messages = _render_semantic_messages(payload)
    if _messages_size(messages) <= SEMANTIC_MESSAGE_MAX_BYTES:
        return messages

    # Remove reproducible presentation detail before rejecting the request.
    # Table/column identities, bounds, unknowns, handles, the current request,
    # draft token and all business provenance remain intact.
    for source in payload["sources"]:
        profile = source.get("profile_pack") or {}
        profile["limitations"] = []
        for table in profile.get("tables", []):
            for column in table.get("columns", []):
                column["top_values"] = []
        interpretation = source.get("profile_interpretation")
        if interpretation:
            for column in interpretation.get("columns", []):
                column["anomalies"] = []
    messages = _render_semantic_messages(payload)
    if _messages_size(messages) <= SEMANTIC_MESSAGE_MAX_BYTES:
        return messages

    for source in payload["sources"]:
        profile = source.get("profile_pack") or {}
        for table in profile.get("tables", []):
            for column in table.get("columns", []):
                column["type_counts"] = []
                for key in (
                    "numeric_p25", "numeric_p50", "numeric_p75",
                    "text_length_min", "text_length_max",
                ):
                    column[key] = None
    messages = _render_semantic_messages(payload)
    if _messages_size(messages) > SEMANTIC_MESSAGE_MAX_BYTES:
        raise SemanticProposalFailure("context_too_broad")
    return messages


def request_semantic_proposal(
    provider: SemanticProvider,
    plan: ContextPlanV1,
    budget: RunBudget,
    *,
    user_id: str,
    cancel_event: Event,
    messages: list[dict[str, Any]] | None = None,
) -> tuple[SemanticProposalV1, ProviderCompletion]:
    """Request and validate one proposal without performing any domain operation."""

    messages = messages or semantic_messages(plan)
    completion = provider.complete(
        messages,
        stream=False,
        tools=None,
        max_tokens=budget.max_output_tokens,
        user_id=user_id,
        timeouts=ProviderTimeouts(
            connect_ms=budget.connect_timeout_ms,
            # A non-streaming response has no earlier progress event: its first
            # event is the complete response. Keep that deadline aligned with
            # the approved single-request total instead of truncating it at the
            # legacy streaming first-event limit.
            first_event_ms=SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS,
            idle_ms=min(budget.idle_timeout_ms, SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS),
            total_ms=SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS,
        ),
        cancel_event=cancel_event,
        max_context_bytes=SEMANTIC_CONTEXT_MAX_BYTES,
    )
    if not isinstance(completion, ProviderCompletion):
        raise SemanticProposalFailure("provider_protocol_error", completion)
    if completion.finish_reason != "stop" or completion.tool_calls:
        raise SemanticProposalFailure("provider_protocol_error", completion)
    try:
        decoded = json.loads(completion.content)
        proposal = SemanticProposalV1.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise SemanticProposalFailure("semantic_proposal_invalid", completion) from exc
    return proposal, completion
