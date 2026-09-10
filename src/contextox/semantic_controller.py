"""Single-request semantic proposal boundary for the deterministic R1 controller."""

from __future__ import annotations

import json
import re
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
    TextLinesLocator,
    canonical_sha256,
)
from contextox.provider import ProviderCompletion, ProviderTimeouts


SEMANTIC_CONTEXT_MAX_BYTES = 65_536
SEMANTIC_MESSAGE_MAX_BYTES = 60 * 1_024
SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS = 70_000
EMPTY_TOOL_SCHEMA_SHA256 = canonical_sha256({"tools": []})

_PROMPT_SCHEMA_KEYS = frozenset({
    "$defs", "$ref", "additionalProperties", "anyOf", "const", "enum",
    "items", "oneOf", "properties", "required", "type",
})


def _prompt_schema(value: Any, *, named_members: bool = False) -> Any:
    """Keep only structural JSON Schema guidance needed by the model.

    Pydantic remains the complete validator. Property and definition names are
    retained while presentation metadata and numeric/string size constraints
    stay in the deterministic application boundary.
    """

    if isinstance(value, list):
        return [_prompt_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    if named_members:
        return {key: _prompt_schema(item) for key, item in value.items()}
    return {
        key: _prompt_schema(
            item,
            named_members=key in {"$defs", "properties"},
        )
        for key, item in value.items()
        if key in _PROMPT_SCHEMA_KEYS
    }


_SCHEMA_TEXT = json.dumps(
    _prompt_schema(SemanticProposalV1.model_json_schema()),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
_EXAMPLE_TEXT = json.dumps(
    {
        "version": "v1",
        "action": "answer_only",
        "public_answer": "Concise source-grounded answer.",
        "fields": [],
        "relationships": [],
        "unresolved_items": [],
        "questions": [],
        "evidence_handles": [],
    },
    ensure_ascii=False,
    separators=(",", ":"),
)
P0_SEMANTIC_PROPOSAL = """You are the semantic proposal stage of ContextOx (数契).
Return exactly one JSON object matching the supplied SemanticProposalV1 schema. Do not call tools.
Use only the current ContextPlanV1. Preserve unknown business meaning as null plus an explicit unknown reason.
Use only supplied opaque handles. Every observed source claim and candidate supported by source data must carry a matching evidence handle.
Task instructions and approved human answers are business provenance, not observed source evidence. Never invent approval or Mission completion.
Use selected source excerpts as evidence. Partial/not_read coverage means uninspected content, not proof that the source lacks a fact.
For draft requests, return the requested candidate fields and relationships: draft_and_clarify with up to three key questions, otherwise draft_only. Use answer_only for questions needing no draft change. Reserve draft_and_submit for explicit review requests.
Send only new or changed fields/relationships; omitted existing items and dimensions are preserved. Approved human answers resolve their target definitions as business provenance. Keep unrelated unknowns visible without asking every question now.
Questions may use targets [{kind: field or relationship, key: candidate key, property: definition dimension}]; the program builds exact paths. Owner and handoff metadata may be omitted. Public prose uses brief @source/table/column labels; put exact handles in evidence_handles, not prose.
The application validates and applies the proposal atomically. Omitted dimensions on new fields remain unknown.
Schema: """ + _SCHEMA_TEXT + "\nMinimal valid example: " + _EXAMPLE_TEXT
P0_SEMANTIC_PROPOSAL_SHA256 = canonical_sha256({"text": P0_SEMANTIC_PROPOSAL})
PRE_COMPACT_SEMANTIC_PROPOSAL_SHA256 = "2ee5d7b89eaf5cf61be546cca69479410119e98d11dcbf081fdc8f7e08b81e16"
SUPPORTED_SEMANTIC_HASH_PAIRS = frozenset({
    (P0_SEMANTIC_PROPOSAL_SHA256, EMPTY_TOOL_SCHEMA_SHA256),
    (PRE_COMPACT_SEMANTIC_PROPOSAL_SHA256, EMPTY_TOOL_SCHEMA_SHA256),
    ("a2a1c67e0ea2e50ca9911b1c64f6c09f256065a835874a09c187b5b2eba4ab06", EMPTY_TOOL_SCHEMA_SHA256),
    ("c71b1305093186514f78ca66280ffb2c4e7bd0ca99f0f71751b04af3d86d5358", EMPTY_TOOL_SCHEMA_SHA256),
    ("7617399876810be036c322ce0cdd2d56e610f958eadf1ad21194bb21b348e790", EMPTY_TOOL_SCHEMA_SHA256),
})


class SemanticProvider(Protocol):
    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> ProviderCompletion: ...


class SemanticProposalFailure(Exception):
    """Safe failure raised before any domain write occurs."""

    def __init__(self, code: str, completion: ProviderCompletion | None = None,
                 safe_errors: list[str] | None = None) -> None:
        self.code = code
        self.completion = completion
        self.safe_errors = safe_errors or []
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
    normalized_fields = []
    for item in proposal.fields:
        value = adapter.field(item)
        refs = [*value["source_refs"], *(ref.model_dump(mode="json") for ref in adapter.column_evidence(item.source_column_handles))]
        value["source_refs"] = list({canonical_sha256(ref):ref for ref in refs}.values())
        previous = fields.get(item.field_key)
        if previous is not None:
            omitted = set(type(item.semantics).model_fields) - item.semantics.model_fields_set
            for name in omitted:
                value[name] = getattr(previous, name)
            value["unknowns"] = [unknown for unknown in value["unknowns"] if unknown["property_path"] not in omitted]
            value["unknowns"].extend(unknown.model_dump(mode="json") for unknown in previous.unknowns
                                     if unknown.property_path in omitted)
        normalized_fields.append(DefinitionField.model_validate(value))
    normalized_relationships = []
    for item in proposal.relationships:
        value = adapter.relationship(item)
        refs = [*value["source_refs"], *(ref.model_dump(mode="json") for ref in adapter.column_evidence(
            [*item.left_column_handles, *item.right_column_handles]))]
        value["source_refs"] = list({canonical_sha256(ref):ref for ref in refs}.values())
        previous = relationships.get(item.relationship_key)
        if previous is not None:
            for name in ("join_rule", "grain_notes", "risks", "unknowns"):
                if name not in item.model_fields_set:
                    value[name] = previous.model_dump(mode="json")[name]
        for name in ("join_rule", "grain_notes"):
            paths = {name, f"{item.relationship_key}.{name}"}
            if value[name] is not None:
                value["unknowns"] = [unknown for unknown in value["unknowns"] if unknown["property_path"] not in paths]
            elif not any(unknown["property_path"] in paths for unknown in value["unknowns"]):
                value["unknowns"].append({"property_path":name, "reason":"模型未提供，待补充"})
        normalized_relationships.append(RelationshipCandidate.model_validate(value))
    adapter.validate_update(
        [item.model_dump(mode="json") for item in normalized_fields],
        [item.model_dump(mode="json") for item in normalized_relationships],
    )
    fields.update({item.field_key: item for item in normalized_fields})
    relationships.update({item.relationship_key: item for item in normalized_relationships})
    unresolved = (list(proposal.unresolved_items) if "unresolved_items" in proposal.model_fields_set
                  else list(current.unresolved_items) if current else [])
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
    questions: list[ClarificationQuestion] = []
    for question_index, question in enumerate(proposal.questions):
        paths = []
        for target in question.targets:
            collection = "fields" if target.kind == "field" else "relationships"
            path = f"{collection}.{target.key}"
            if target.property is not None:
                path += f".{target.property}"
            paths.append(path)
        paths = list(dict.fromkeys(paths))
        if any(path not in valid_paths for path in paths):
            raise SemanticProposalFailure("semantic_definition_path_invalid", safe_errors=[
                f"questions.{question_index}.targets:unknown_candidate_target"])
        values = question.model_dump(mode="json", exclude={"evidence_handles", "targets"})
        values["related_definition_paths"] = paths
        values["source_refs"] = [
            item.model_dump(mode="json")
            for item in _resolved_evidence(adapter, question.evidence_handles)
        ]
        questions.append(ClarificationQuestion.model_validate(values))
    return questions


def normalize_semantic_proposal(
    adapter: ToolAdapter,
    proposal: SemanticProposalV1,
) -> SemanticApplicationInput:
    """Resolve opaque capabilities and validate the complete projected result."""

    try:
        if proposal.action in {"draft_and_clarify", "draft_and_submit", "draft_only"}:
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
        public_answer, embedded_refs = _public_answer(adapter, proposal.public_answer)
        evidence = [*_resolved_evidence(adapter, proposal.evidence_handles), *embedded_refs,
                    *(ref for field in fields for ref in field.source_refs),
                    *(ref for relationship in relationships for ref in relationship.source_refs)]
        evidence = list({canonical_sha256(ref): ref for ref in evidence}.values())
        return SemanticApplicationInput(
            action=proposal.action,
            public_answer=public_answer,
            expected_version=pair["version"],
            expected_sha256=pair["sha256"],
            fields=fields,
            relationships=relationships,
            unresolved_items=unresolved_items,
            questions=questions,
            source_refs=evidence,
        )
    except SemanticProposalFailure:
        raise
    except HandleDenied as exc:
        raise SemanticProposalFailure("semantic_handle_invalid") from exc
    except (CandidateRejected, ValidationError, TypeError, ValueError) as exc:
        raise SemanticProposalFailure("semantic_proposal_invalid") from exc


def _public_answer(adapter: ToolAdapter, text: str) -> tuple[str, list[EvidenceRef]]:
    """Turn current opaque capabilities into display labels, retaining evidence."""
    evidence: list[EvidenceRef] = []

    def replace(match: re.Match) -> str:
        handle = match.group(0).removeprefix("@")
        kind = handle.split("_", 1)[0]
        value = adapter.resolve(handle, kind)
        if kind == "draft":
            return "@草案"
        ref = value if kind in {"source", "evidence"} else value.source_ref
        source_handle = adapter.register("source", adapter._source(ref))
        name = next(source["name"] for source in adapter.catalog if source["source_handle"] == source_handle)
        if kind == "evidence":
            evidence.append(value)
            locator = value.locator
            if locator.kind == "json_pointer":
                name += locator.pointer or "/"
            elif locator.kind == "csv_rows" and locator.column:
                name += "/" + locator.column
            else:
                start, end = ((locator.line_start, locator.line_end) if locator.kind == "text_lines"
                              else (locator.row_start, locator.row_end))
                name += f"/行{start}–{end}"
        elif kind == "column":
            name += (value.table_id or "") + "/" + value.column
        elif kind == "table":
            name += value.table_id
        return "@" + name

    rendered = re.sub(r"@?\b(?:evidence|source|column|table|draft)_[a-f0-9]{32}\b", replace, text)
    return rendered, evidence


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
        # Removing read tools must not remove the selected text source body.
        locators = []
        if source["text_line_count"] and source["media_type"] in {"text/plain", "text/markdown"}:
            locators.append(TextLinesLocator(kind="text_lines", line_start=1,
                                            line_end=source["text_line_count"]))
        if snapshot.message_context is not None:
            for message in [snapshot.message_context.input, *snapshot.message_context.history]:
                if message.role != "user":
                    continue
                for reference in message.references:
                    if reference.kind == "source_excerpt" and reference.evidence_ref.revision_id == revision_id:
                        adapter._source(reference.evidence_ref)
                        locators.append(reference.evidence_ref.locator)
        excerpts = []
        seen: set[str] = set()
        for locator in locators:
            key = canonical_sha256(locator)
            if key in seen:
                continue
            seen.add(key)
            excerpt = store.read_source_excerpt(snapshot.mission.workspace_id, revision_id, locator)
            excerpts.append({**adapter.public(excerpt.source_ref), "text": excerpt.text,
                             "truncated": excerpt.truncated})
        source_plan["excerpts"] = excerpts
        source_plan["text_coverage"] = (
            "partial" if any(item["truncated"] for item in excerpts)
            else "complete" if locators and isinstance(locators[0], TextLinesLocator)
            and locators[0].line_start == 1 and locators[0].line_end == source["text_line_count"]
            else "selected_excerpts" if excerpts else "not_read"
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
    timeout_ms: int = SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS,
) -> tuple[SemanticProposalV1, ProviderCompletion]:
    """Request and validate one proposal without performing any domain operation."""

    messages = messages or semantic_messages(plan)
    if _messages_size(messages) > SEMANTIC_MESSAGE_MAX_BYTES:
        raise SemanticProposalFailure("context_too_broad")
    timeout_ms = min(timeout_ms, SEMANTIC_PROVIDER_TOTAL_TIMEOUT_MS)
    completion = provider.complete(
        messages,
        stream=False,
        tools=None,
        max_tokens=budget.max_output_tokens,
        user_id=user_id,
        timeouts=ProviderTimeouts(
            connect_ms=min(budget.connect_timeout_ms, timeout_ms),
            # A non-streaming response has no earlier progress event: its first
            # event is the complete response. Keep that deadline aligned with
            # the approved single-request total instead of truncating it at the
            # legacy streaming first-event limit.
            first_event_ms=timeout_ms,
            idle_ms=min(budget.idle_timeout_ms, timeout_ms),
            total_ms=timeout_ms,
        ),
        cancel_event=cancel_event,
        max_context_bytes=SEMANTIC_CONTEXT_MAX_BYTES,
    )
    if not isinstance(completion, ProviderCompletion):
        raise SemanticProposalFailure("provider_protocol_error", completion)
    if completion.finish_reason != "stop" or completion.tool_calls:
        raise SemanticProposalFailure("provider_protocol_error", completion)
    try:
        content = completion.content.strip()
        for fence in ("```json\n", "```\n"):
            if content.startswith(fence) and content.endswith("\n```"):
                content = content[len(fence):-4].strip()
                break
        decoded = json.loads(content)
        proposal = SemanticProposalV1.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        errors = ["json_invalid"]
        if isinstance(exc, ValidationError):
            # Error locations can contain model-provided object keys. Only
            # schema-owned names and numeric indices may leave this boundary.
            schema = SemanticProposalV1.model_json_schema()
            names = set(schema.get("properties", {}))
            for definition in schema.get("$defs", {}).values():
                names.update(definition.get("properties", {}))
            errors = [".".join(str(part) if isinstance(part, int) or part in names else "item"
                               for part in error["loc"]) + ":" + error["type"]
                      for error in exc.errors(include_input=False, include_context=False, include_url=False)[:8]]
        raise SemanticProposalFailure("semantic_proposal_invalid", completion, errors) from exc
    return proposal, completion
