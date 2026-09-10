"""Run-local model inputs; persistent domain objects remain authoritative."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from contextox.models import (
    Cardinality, ColumnRef, ContextOxModel, ContextSnapshot, DefinitionDraft, DefinitionField,
    DomainToolCall, UnknownItem, RelationshipCandidate,
    EvidenceLocator, EvidenceRef, Key, SourceArtifact,
    SourceExcerpt, SourceIdentity, SourceRevision, StatementEvidenceStatus,
    TableKey, Text, AnswerType, FinalOutput, SemanticAction,
)
from contextox.sources import recognized_table_rows_complete

Handle = Annotated[str, Field(strict=True, min_length=1, max_length=64,
                              pattern=r"^[A-Za-z0-9_-]+$")]
Reason = Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class SemanticValue(ContextOxModel):
    value: Text | None
    unknown_reason: Reason | None

    @model_validator(mode="after")
    def exclusive(self) -> SemanticValue:
        if (self.value is None) == (self.unknown_reason is None):
            raise ValueError("provide either a value or an unknown reason")
        if self.unknown_reason is not None and not self.unknown_reason.strip():
            raise ValueError("unknown reason cannot be blank")
        return self


class SemanticKey(SemanticValue):
    value: Key | None


class FieldSemantics(ContextOxModel):
    meaning: SemanticValue
    value_type: SemanticKey
    grain: SemanticValue
    rule: SemanticValue
    time_basis: SemanticValue
    null_handling: SemanticValue


class FieldInput(ContextOxModel):
    field_key: Key
    name: Key
    semantics: FieldSemantics
    source_column_handles: list[Handle]
    evidence_status: StatementEvidenceStatus
    evidence_handles: list[Handle]


class QuestionInput(ContextOxModel):
    covers_obligation_handles: list[Handle]
    question: Text
    why_needed: Text
    expected_answer_type: AnswerType
    suggested_owner_role: Key | None
    related_definition_paths: list[Key]
    evidence_requested: list[Text]
    examples_or_options: list[Text]
    blocking_impact: Literal["blocking", "non_blocking"]
    evidence_handles: list[Handle]


class ListInput(ContextOxModel):
    pass


class ReadInput(ContextOxModel):
    source_handle: Handle
    locator: EvidenceLocator


class InspectTableInput(ContextOxModel):
    kind: Literal["table"]
    table_handle: Handle


class JoinColumns(ContextOxModel):
    left_column_handles: list[Handle] = Field(min_length=1, max_length=100)
    right_column_handles: list[Handle] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def paired(self) -> JoinColumns:
        if len(self.left_column_handles) != len(self.right_column_handles):
            raise ValueError("join columns must have equal arity")
        if any(len(set(items)) != len(items) for items in
               (self.left_column_handles, self.right_column_handles)):
            raise ValueError("join columns must be distinct")
        return self


class InspectRelationshipInput(JoinColumns):
    kind: Literal["relationship"]
    left_table_handle: Handle
    right_table_handle: Handle


InspectInput = Annotated[InspectTableInput | InspectRelationshipInput, Field(discriminator="kind")]


class RelationshipInput(JoinColumns):
    relationship_key: Key
    left: Handle
    right: Handle
    observed_cardinality: Cardinality
    join_rule: Text | None
    grain_notes: Text | None
    evidence_status: StatementEvidenceStatus
    evidence_handles: list[Handle]
    risks: list[Text]
    unknowns: list[UnknownItem]


class DraftTokenInput(ContextOxModel):
    draft_token: Handle


class UpdateInput(DraftTokenInput):
    fields: list[FieldInput] = Field(max_length=100)
    relationships: list[RelationshipInput] = Field(max_length=100)
    unresolved_items: list[Text]


class ClarificationInput(DraftTokenInput):
    questions: list[QuestionInput] = Field(min_length=1, max_length=20)


class FinishInput(ContextOxModel):
    outcome: Literal["partial"]
    reason: Text
    evidence_handles: list[Handle]


class ContextPlanV1(ContextOxModel):
    """Bounded, stage-specific application context sent to the semantic model."""

    context_kind: Literal["semantic_context_v1"]
    mission: dict[str, Any]
    message_context: dict[str, Any] | None
    sources: list[dict[str, Any]] = Field(max_length=8)
    prospective_relationships: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    draft: dict[str, Any] | None
    clarifications: list[dict[str, Any]] = Field(max_length=20)
    approved_answers: list[dict[str, Any]] = Field(max_length=50)


class CandidateSemantics(FieldSemantics):
    """Omitted candidate dimensions remain unknown; legacy tool inputs stay strict."""

    meaning: SemanticValue = Field(default_factory=lambda: SemanticValue(value=None, unknown_reason="模型未提供，待补充"))
    value_type: SemanticKey = Field(default_factory=lambda: SemanticKey(value=None, unknown_reason="模型未提供，待补充"))
    grain: SemanticValue = Field(default_factory=lambda: SemanticValue(value=None, unknown_reason="模型未提供，待补充"))
    rule: SemanticValue = Field(default_factory=lambda: SemanticValue(value=None, unknown_reason="模型未提供，待补充"))
    time_basis: SemanticValue = Field(default_factory=lambda: SemanticValue(value=None, unknown_reason="模型未提供，待补充"))
    null_handling: SemanticValue = Field(default_factory=lambda: SemanticValue(value=None, unknown_reason="模型未提供，待补充"))


class SemanticFieldInput(FieldInput):
    semantics: CandidateSemantics = Field(default_factory=CandidateSemantics)


class SemanticRelationshipInput(RelationshipInput):
    join_rule: Text | None = None
    grain_notes: Text | None = None
    risks: list[Text] = Field(default_factory=list, max_length=100)
    unknowns: list[UnknownItem] = Field(default_factory=list, max_length=100)


class SemanticFieldQuestionTarget(ContextOxModel):
    kind: Literal["field"]
    key: Key
    property: Literal["meaning", "value_type", "grain", "rule", "time_basis", "null_handling"] | None = None


class SemanticRelationshipQuestionTarget(ContextOxModel):
    kind: Literal["relationship"]
    key: Key
    property: Literal["join_rule", "grain_notes", "risks"] | None = None


SemanticQuestionTarget = Annotated[
    SemanticFieldQuestionTarget | SemanticRelationshipQuestionTarget, Field(discriminator="kind")
]


class SemanticQuestionInput(ContextOxModel):
    """Question paths target the projected draft produced by this proposal."""

    question: Text
    why_needed: Text
    expected_answer_type: AnswerType
    suggested_owner_role: Key | None = None
    targets: list[SemanticQuestionTarget] = Field(default_factory=list, max_length=100)
    related_definition_paths: list[Key] = Field(default_factory=list, max_length=100)
    evidence_requested: list[Text] = Field(default_factory=list, max_length=100)
    examples_or_options: list[Text] = Field(default_factory=list, max_length=100)
    blocking_impact: Literal["blocking", "non_blocking"] = "blocking"
    evidence_handles: list[Handle] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_paths(self) -> SemanticQuestionInput:
        if not self.targets and not self.related_definition_paths:
            raise ValueError("a question needs a draft target")
        if len(set(self.related_definition_paths)) != len(self.related_definition_paths):
            raise ValueError("related_definition_paths must be unique")
        if len(set(self.evidence_handles)) != len(self.evidence_handles):
            raise ValueError("evidence_handles must be unique")
        return self


class SemanticProposalV1(ContextOxModel):
    """One complete model proposal; the application owns every durable effect."""

    version: Literal["v1"]
    action: SemanticAction
    public_answer: FinalOutput
    fields: list[SemanticFieldInput] = Field(default_factory=list, max_length=100)
    relationships: list[SemanticRelationshipInput] = Field(default_factory=list, max_length=100)
    unresolved_items: list[Text] = Field(default_factory=list, max_length=100)
    questions: list[SemanticQuestionInput] = Field(default_factory=list, max_length=20)
    evidence_handles: list[Handle] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_action_shape(self) -> SemanticProposalV1:
        if not self.public_answer.strip():
            raise ValueError("public_answer must be nonblank")
        if len(set(self.evidence_handles)) != len(self.evidence_handles):
            raise ValueError("evidence_handles must be unique")
        if self.action == "answer_only" and (
            self.fields or self.relationships or self.unresolved_items or self.questions
        ):
            raise ValueError("answer_only cannot contain draft or clarification changes")
        if self.action == "clarify_only" and (
            self.fields or self.relationships or self.unresolved_items or not self.questions
        ):
            raise ValueError("clarify_only requires questions and no draft changes")
        if self.action == "draft_and_clarify" and not self.questions:
            raise ValueError("draft_and_clarify requires questions")
        if self.action in {"draft_and_submit", "draft_only"} and self.questions:
            raise ValueError("this action cannot contain questions")
        return self


MODEL_ARGUMENT_TYPES = {
    "list_sources": ListInput, "read_source": ReadInput, "inspect_dataset": InspectInput,
    "update_definition_draft": UpdateInput, "create_clarification": ClarificationInput,
    "submit_for_review": DraftTokenInput, "finish_run": FinishInput,
}


class HandleDenied(Exception):
    """No details about other sources or runs may be disclosed."""


class ContextBundleExceeded(Exception):
    """A completed tool result cannot fit in the bounded Run working set."""


class CandidateRejected(Exception):
    def __init__(self, code: str, paths: list[str] | None = None, indices: list[int] | None = None,
                 missing_handles: list[str] | None = None):
        self.missing_handles = (missing_handles or [])[:5]
        self.code = code
        self.paths = (paths or [])[:5]
        self.indices = indices or []
        super().__init__(code)


def _plain(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, ContextOxModel) else value


class RunReferences:
    """Opaque capabilities minted from the selected immutable context only.

    Registration does not grant permission: the Store revalidates every resolved
    source and domain operation. No registry is restored after process restart.
    """

    supports_context_checkpoints = True

    def __init__(self, snapshot: ContextSnapshot, artifacts: list[SourceArtifact]):
        self.scope = (snapshot.mission.workspace_id, snapshot.mission.mission_id,
                      snapshot.run.run_id)
        self.selected = {r.revision_id: r for r in snapshot.run.source_refs}
        # Exact approved unknown targets already have a persistent question/blocker.
        # They remain unresolved; only the obligation to ask them again is removed.
        self.approved_unknown_targets = {
            ("fields" if target.kind == "field" else "relationships", target.key, target.property)
            for approved in snapshot.approved_answers for item in approved.answer.items
            if item.disposition == "unknown" for target in item.targets
        }
        self.values: dict[str, tuple[str, Any]] = {}
        self.reverse: dict[tuple[str, str], str] = {}
        self.catalog: list[dict[str, Any]] = []
        self.coverage: list[dict[str, Any]] = []
        self.evidence_bundle: list[dict[str, Any]] = []
        self._evidence_bundle_keys: set[str] = set()
        # Leave at least half the request budget for P0, current authoritative
        # state, active user input and tool schemas. Individual source/tool
        # outputs are already bounded by their Pydantic contracts.
        self._evidence_bundle_max_bytes = snapshot.run.budget.max_context_bytes // 2
        source_by_id = {r.revision_id: r for r in snapshot.sources}
        if set(self.selected) != {a.source_ref.revision_id for a in artifacts}:
            raise HandleDenied()
        for artifact in artifacts:
            self._source(artifact.source_ref)
            revision = source_by_id.get(artifact.source_ref.revision_id)
            if revision is None:
                raise HandleDenied()
            self._source(SourceIdentity(**{k: getattr(revision, k)
                                          for k in SourceIdentity.model_fields}))
            tables = []
            for table in artifact.tables:
                key = TableKey(source_ref=artifact.source_ref, table_id=table.table_id, columns=[])
                tables.append({"table_handle": self.register("table", key),
                               "table_id": table.table_id, "row_count": table.row_count,
                               "columns": [{"name": col.name, "column_handle": self.register(
                                   "column", ColumnRef(source_ref=artifact.source_ref,
                                                       table_id=table.table_id, column=col.name))}
                                           for col in table.columns]})
            self.catalog.append({"source_handle": self.register("source", artifact.source_ref),
                                 "name": revision.original_name, "media_type": revision.media_type,
                                 "parse_status": artifact.parse_status,
                                 "recognized_table_rows_complete": (
                                     recognized_table_rows_complete(artifact)
                                     if artifact.tables else None
                                 ),
                                 "text_line_count": artifact.text_line_count, "tables": tables})
        self.current_draft = snapshot.draft or snapshot.run.draft
        self.draft_token = self.register("draft", self.draft_pair(self.current_draft))

    def _source(self, ref: SourceIdentity | EvidenceRef) -> SourceIdentity:
        selected = self.selected.get(ref.revision_id)
        identity = SourceIdentity(**{k: getattr(ref, k) for k in SourceIdentity.model_fields})
        if selected != identity or identity.workspace_id != self.scope[0]:
            raise HandleDenied()
        return identity

    @staticmethod
    def draft_pair(draft: DefinitionDraft | None) -> dict[str, Any]:
        return {"version": draft.version if draft else 0,
                "sha256": draft.sha256 if draft else None}

    def register(self, kind: str, value: Any) -> str:
        data = _plain(value)
        key = (kind, json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        if key not in self.reverse:
            handle = f"{kind}_{uuid4().hex}"
            self.reverse[key] = handle
            self.values[handle] = (kind, value)
        return self.reverse[key]

    def resolve(self, handle: str, kind: str) -> Any:
        entry = self.values.get(handle)
        if entry is None or entry[0] != kind:
            raise HandleDenied()
        return entry[1]

    def field(self, field: FieldInput) -> dict[str, Any]:
        result = {"field_key": field.field_key, "name": field.name,
                  "source_columns": [_plain(self.resolve(h, "column")) for h in field.source_column_handles],
                  "evidence_status": field.evidence_status,
                  "source_refs": [_plain(self.resolve(h, "evidence")) for h in field.evidence_handles],
                  "unknowns": []}
        for key in FieldSemantics.model_fields:
            dimension = getattr(field.semantics, key)
            result[key] = dimension.value
            if dimension.value is None:
                result["unknowns"].append({"property_path": key, "reason": dimension.unknown_reason})
        return result

    def validate_update(self, fields: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> None:
        """Validate the complete upsert before any member of a batch executes."""
        for name, items, key in (("fields", fields, "field_key"),
                                 ("relationships", relationships, "relationship_key")):
            submitted = [item[key] for item in items]
            previous = getattr(self.current_draft, name) if self.current_draft else []
            if len(set(submitted)) != len(submitted):
                raise CandidateRejected("duplicate_candidate_key", [name])
            if len({getattr(item, key) for item in previous} | set(submitted)) > 100:
                raise CandidateRejected("candidate_limit_exceeded", [name])

    def draft_version(self, token: str) -> dict[str, Any]:
        pair = self.resolve(token, "draft")
        if pair != self.draft_pair(self.current_draft):
            raise CandidateRejected("draft_version_conflict", ["draft_token"])
        return dict(pair)

    def read_arguments(self, value: ReadInput) -> dict[str, Any]:
        ref = self.resolve(value.source_handle, "source")
        self._source(ref)
        return {"revision_id": ref.revision_id, "locator": _plain(value.locator)}

    def finish_arguments(self, value: FinishInput) -> dict[str, Any]:
        return {"outcome": value.outcome, "reason": value.reason,
                "source_refs": [_plain(self.resolve(h, "evidence")) for h in value.evidence_handles]}

    def review_arguments(self, value: DraftTokenInput) -> dict[str, Any]:
        pair = self.draft_version(value.draft_token)
        return {"draft_version": pair["version"], "draft_sha256": pair["sha256"]}

    def clarification_obligations(self) -> list[dict[str, Any]]:
        draft = self.current_draft
        if draft is None:
            return []
        entries = []
        for collection, key in (("fields", "field_key"), ("relationships", "relationship_key")):
            for index, obj in enumerate(getattr(draft, collection)):
                for unknown_index, unknown in enumerate(obj.unknowns):
                    object_key = getattr(obj, key)
                    property_path = unknown.property_path
                    if collection == "relationships":
                        # Relationship unknowns may already qualify their property by key.
                        property_path = property_path.removeprefix(f"{object_key}.")
                    if ((collection, object_key, property_path) in self.approved_unknown_targets
                            and getattr(obj, property_path, None) is None):
                        continue
                    path = f"{collection}.{object_key}.{property_path}"
                    # Domain paths are bounded Keys; indexes remain scoped to this exact draft.
                    if len(path) > 128:
                        path = f"{collection}.{index}.unknowns.{unknown_index}"
                    entries.append((path, unknown.reason, collection == "fields"))
        entries.extend((f"unresolved_items.{index}", reason, False)
                       for index, reason in enumerate(draft.unresolved_items))
        return [{"obligation_handle": self.register("obligation", {
                    "draft": self.draft_pair(draft), "ordinal": index, "path": path}),
                 "related_definition_paths": [path], "reason": reason,
                 "required_blocking_impact": "blocking" if field_gap else None}
                for index, (path, reason, field_gap) in enumerate(entries)]

    def clarification_arguments(self, value: ClarificationInput) -> dict[str, Any]:
        result = self.review_arguments(value)
        obligations = {item["obligation_handle"]: item for item in self.clarification_obligations()}
        blocking_paths = {path for item in obligations.values()
                          if item["required_blocking_impact"] == "blocking"
                          for path in item["related_definition_paths"]}
        # Resolve against this exact draft, never arbitrary strings or historical requests.
        valid_paths = {path for item in obligations.values() for path in item["related_definition_paths"]}
        if self.current_draft is not None:
            for collection, key in (("fields", "field_key"), ("relationships", "relationship_key")):
                for obj in getattr(self.current_draft, collection):
                    root = f"{collection}.{getattr(obj, key)}"
                    valid_paths.add(root)
                    valid_paths.update(f"{root}.{prop}" for prop in type(obj).model_fields)
        covered: set[str] = set()
        questions = []
        for question in value.questions:
            handles = question.covers_obligation_handles
            if len(set(handles)) != len(handles):
                raise CandidateRejected("duplicate_obligation", ["questions.covers_obligation_handles"])
            paths = list(question.related_definition_paths)
            if any(path not in valid_paths for path in paths):
                raise CandidateRejected("unknown_definition_path", ["questions.related_definition_paths"])
            for handle in handles:
                self.resolve(handle, "obligation")  # Forged or cross-Run capabilities fail closed.
                if handle not in obligations:
                    raise CandidateRejected("draft_version_conflict", ["draft_token"])
                paths.extend(obligations[handle]["related_definition_paths"])
            if question.blocking_impact != "blocking" and blocking_paths.intersection(paths):
                raise CandidateRejected("clarification_impact_conflict", ["questions.blocking_impact"])
            if handles and (not question.suggested_owner_role
                            or not question.suggested_owner_role.strip()
                            or not question.question.strip() or not question.why_needed.strip()
                            or not question.evidence_requested
                            or any(not evidence.strip() for evidence in question.evidence_requested)):
                raise CandidateRejected("incomplete_handoff", ["questions.suggested_owner_role",
                                        "questions.evidence_requested", "questions.question", "questions.why_needed"])
            covered.update(handles)
            item = question.model_dump(mode="json", exclude={"evidence_handles", "covers_obligation_handles"})
            item["related_definition_paths"] = list(dict.fromkeys(paths))
            item["source_refs"] = [_plain(self.resolve(h, "evidence")) for h in question.evidence_handles]
            questions.append(item)
        missing = [handle for handle in obligations if handle not in covered]
        if missing:
            raise CandidateRejected("clarification_coverage_incomplete", ["questions.covers_obligation_handles"],
                                    missing_handles=missing)
        return {**result, "questions": questions}

    def join_key(self, table_handle: str, columns: list[str]) -> TableKey:
        table = self.resolve(table_handle, "table")
        names = []
        for handle in columns:
            col = self.resolve(handle, "column")
            if (col.source_ref, col.table_id) != (table.source_ref, table.table_id):
                raise CandidateRejected("join_column_table_mismatch",
                                        ["left_column_handles", "right_column_handles"])
            names.append(col.column)
        return table.model_copy(update={"columns": names})

    def relationship(self, value: RelationshipInput) -> dict[str, Any]:
        result = value.model_dump(mode="json", exclude={"left", "right", "left_column_handles",
                                                        "right_column_handles", "evidence_handles"})
        return {**result, "left": _plain(self.join_key(value.left, value.left_column_handles)),
                "right": _plain(self.join_key(value.right, value.right_column_handles)),
                "source_refs": [_plain(self.resolve(h, "evidence")) for h in value.evidence_handles]}

    def context(self, snapshot: ContextSnapshot) -> dict[str, Any]:
        self.check_snapshot(snapshot)
        return {"context_kind": "authorized_context_packet",
                "mission": {"title": snapshot.mission.title, "goal": snapshot.mission.goal,
                            "completion_criteria": snapshot.mission.completion_criteria,
                            "scope_notes": snapshot.mission.scope_notes},
                "message_context": self.public(snapshot.message_context),
                "sources": self.catalog,
                "draft_token": self.draft_token,
                "draft": self.public(self.current_draft),
                "clarifications": self.public(snapshot.clarifications),
                "approved_answers": [{"origin_run_id": a.answer.origin_run_id, "clarification_id": a.answer.clarification_id,
                    "answer_version": a.answer.version, "answer_sha256": a.answer.sha256,
                    "questions": self.public(a.request.questions), "items": self.public(a.answer.items)}
                    for a in snapshot.approved_answers],
                "coverage": self.coverage,
                "evidence_bundle": self.evidence_bundle,
                "budget": _plain(snapshot.run.budget)}

    def check_snapshot(self, snapshot: ContextSnapshot) -> None:
        if (snapshot.mission.workspace_id, snapshot.mission.mission_id, snapshot.run.run_id) != self.scope:
            raise HandleDenied()
        if {r.revision_id: r for r in snapshot.run.source_refs} != self.selected:
            raise HandleDenied()
        self.current_draft = snapshot.draft or snapshot.run.draft
        self.draft_token = self.register("draft", self.draft_pair(self.current_draft))

    def evidence(self, ref: EvidenceRef) -> str:
        self._source(ref)
        return self.register("evidence", ref)

    def column(self, ref: ColumnRef) -> str:
        self._source(ref.source_ref)
        key = ("column", json.dumps(_plain(ref), ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")))
        handle = self.reverse.get(key)
        if handle is None:
            raise HandleDenied()
        return handle

    def table(self, ref: TableKey) -> str:
        self._source(ref.source_ref)
        empty = ref.model_copy(update={"columns": []})
        key = ("table", json.dumps(_plain(empty), ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")))
        handle = self.reverse.get(key)
        if handle is None:
            raise HandleDenied()
        for name in ref.columns:
            self.column(ColumnRef(source_ref=ref.source_ref, table_id=ref.table_id, column=name))
        return handle

    def public(self, value: Any) -> Any:
        """Project only identities; retain business statements and uncertainty."""
        if isinstance(value, EvidenceRef):
            return {"evidence_handle": self.evidence(value), "locator": _plain(value.locator),
                    "source_handle": self.register("source", self._source(value))}
        if isinstance(value, SourceIdentity):
            return {"source_handle": self.register("source", self._source(value))}
        if isinstance(value, SourceRevision):
            ref = SourceIdentity(**{k: getattr(value, k) for k in SourceIdentity.model_fields})
            self._source(ref)
            return next(item for item in self.catalog
                        if item["source_handle"] == self.register("source", ref))
        if isinstance(value, ColumnRef):
            return {"column_handle": self.column(value), "column": value.column}
        if isinstance(value, TableKey):
            return {"table_handle": self.table(value), "columns": list(value.columns)}
        if isinstance(value, DefinitionDraft):
            if (value.workspace_id, value.mission_id) != self.scope[:2]:
                raise HandleDenied()
            self.current_draft = value
            self.draft_token = self.register("draft", self.draft_pair(value))
            return {"draft_token": self.draft_token, "draft_version": value.version,
                    "draft_sha256": value.sha256, "status": value.status,
                    "fields": self.public(value.fields), "relationships": self.public(value.relationships),
                    "unresolved_items": value.unresolved_items,
                    "clarification_obligations": self.clarification_obligations()}
        if isinstance(value, DefinitionField):
            reasons = {item.property_path: item.reason for item in value.unknowns}
            return {"field_key": value.field_key, "name": value.name,
                    "semantics": {key: {"value": getattr(value, key),
                                        "unknown_reason": reasons.get(key)}
                                  for key in FieldSemantics.model_fields},
                    "source_column_handles": [self.column(col) for col in value.source_columns],
                    "evidence_status": value.evidence_status,
                    "evidence_handles": [self.evidence(ref) for ref in value.source_refs]}
        if isinstance(value, RelationshipCandidate):
            result = {key: self.public(getattr(value, key)) for key in
                      ("relationship_key", "observed_cardinality", "join_rule", "grain_notes",
                       "evidence_status", "risks", "unknowns")}
            for side in ("left", "right"):
                table = getattr(value, side)
                result[side] = self.table(table)
                result[f"{side}_column_handles"] = [self.column(ColumnRef(
                    source_ref=table.source_ref, table_id=table.table_id, column=name))
                    for name in table.columns]
            result["evidence_handles"] = [self.evidence(ref) for ref in value.source_refs]
            return result
        if isinstance(value, SourceExcerpt):
            public_ref = self.public(value.source_ref)
            catalog_entry = next(
                item
                for item in self.catalog
                if item["source_handle"] == public_ref["source_handle"]
            )
            item = {"source_handle": public_ref["source_handle"],
                    "source_name": catalog_entry["name"],
                    "evidence_handle": public_ref["evidence_handle"],
                    "locator": public_ref["locator"], "status": "read",
                    "truncated": value.truncated}
            if item not in self.coverage:
                self.coverage.append(item)
            return {**public_ref, "text": value.text, "truncated": value.truncated}
        if isinstance(value, ContextOxModel):
            return {key: self.public(getattr(value, key)) for key in type(value).model_fields}
        if isinstance(value, list):
            return [self.public(item) for item in value]
        if isinstance(value, dict):
            return {key: self.public(item) for key, item in value.items()}
        if isinstance(value, datetime):
            return TypeAdapter(datetime).dump_python(value, mode="json")
        return value

    def remember_evidence(self, tool_name: str, value: Any) -> Any:
        """Retain one bounded public result across provider session checkpoints."""

        public_value = self.public(value)
        if tool_name not in {"read_source", "inspect_dataset"}:
            return public_value
        entry = {"tool_name": tool_name, "result": public_value}
        key = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key in self._evidence_bundle_keys:
            return public_value
        projected = [*self.evidence_bundle, entry]
        encoded = json.dumps(
            projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > self._evidence_bundle_max_bytes:
            raise ContextBundleExceeded()
        self.evidence_bundle.append(entry)
        self._evidence_bundle_keys.add(key)
        return public_value


class ToolAdapter(RunReferences):
    def __init__(self, snapshot: ContextSnapshot, store: Any):
        artifacts = [store.get_source_artifact(snapshot.mission.workspace_id, ref.revision_id)
                     for ref in snapshot.run.source_refs]
        super().__init__(snapshot, artifacts)

    def normalize(self, completion: Any, decode: Any) -> list[Any]:
        calls = []
        errors: list[str] = []
        indices: list[int] = []
        missing_handles: list[str] = []
        updates = sum(call.name == "update_definition_draft" for call in completion.tool_calls)
        if updates > 1:
            raise CandidateRejected("multiple_draft_updates", ["fields"],
                                    list(range(len(completion.tool_calls))))
        for index, call in enumerate(completion.tool_calls):
            try:
                value = TypeAdapter(MODEL_ARGUMENT_TYPES[call.name]).validate_python(decode(call.arguments))
                if isinstance(value, ListInput):
                    args = {}
                elif isinstance(value, ReadInput):
                    args = self.read_arguments(value)
                elif isinstance(value, InspectTableInput):
                    table = self.resolve(value.table_handle, "table")
                    args = {"kind": "table", "revision_id": table.source_ref.revision_id,
                            "table_id": table.table_id}
                elif isinstance(value, InspectRelationshipInput):
                    args = {"kind": "relationship",
                            "left": _plain(self.join_key(value.left_table_handle, value.left_column_handles)),
                            "right": _plain(self.join_key(value.right_table_handle, value.right_column_handles))}
                elif isinstance(value, UpdateInput):
                    pair = self.draft_version(value.draft_token)
                    fields = [self.field(item) for item in value.fields]
                    relationships = [self.relationship(item) for item in value.relationships]
                    self.validate_update(fields, relationships)
                    args = {"expected_version": pair["version"], "expected_sha256": pair["sha256"],
                            "fields": fields, "relationships": relationships,
                            "unresolved_items": value.unresolved_items}
                elif isinstance(value, ClarificationInput):
                    args = self.clarification_arguments(value)
                elif isinstance(value, DraftTokenInput):
                    args = self.review_arguments(value)
                else:
                    args = self.finish_arguments(value)
                calls.append(TypeAdapter(DomainToolCall).validate_python(
                    {"call_id": call.call_id, "name": call.name, "arguments": args}))
            except HandleDenied:
                raise
            except (ValidationError, ValueError, TypeError, CandidateRejected) as exc:
                indices.append(index)
                if isinstance(exc, CandidateRejected):
                    errors.extend(exc.paths or ["arguments"])
                    missing_handles.extend(exc.missing_handles)
                elif isinstance(exc, ValidationError):
                    # Never echo untrusted keys, values, validator messages or reprs.
                    allowed = {"field_key", "name", "semantics", "meaning", "value_type", "grain",
                               "rule", "time_basis", "null_handling", "value", "unknown_reason",
                               "fields", "relationships", "questions", "draft_token", "locator",
                               "evidence_handles", "source_column_handles", "left_column_handles",
                               "right_column_handles", "reason", "unresolved_items", "evidence_status",
                               "covers_obligation_handles", "suggested_owner_role", "evidence_requested"}
                    for error in exc.errors(include_input=False, include_context=False)[:5]:
                        parts = [str(p) if type(p) is int else p if p in allowed else "item"
                                 for p in error["loc"]]
                        errors.append(".".join(parts)[:160] or "arguments")
                else:
                    errors.append("arguments")
        if indices:
            raise CandidateRejected("tool_arguments_invalid_no_effect", errors, indices, missing_handles)
        return calls

    def output(self, result: Any, call: Any) -> Any:
        if call.name == "read_source" and result.status == "rejected":
            ref = self.selected[call.arguments.revision_id]
            item = {"source_handle": self.register("source", self._source(ref)),
                    "locator": _plain(call.arguments.locator), "status": "failed",
                    "error_code": result.output.code}
            if item not in self.coverage:
                self.coverage.append(item)
        if isinstance(result.output, DefinitionDraft) and call.name == "update_definition_draft":
            self.current_draft = result.output
            self.draft_token = self.register("draft", self.draft_pair(result.output))
            return {"draft_token": self.draft_token, "updated_field_keys": [f.field_key for f in call.arguments.fields],
                    "updated_relationship_keys": [r.relationship_key for r in call.arguments.relationships],
                    "clarification_obligations": self.clarification_obligations()}
        if result.status == "succeeded":
            return self.remember_evidence(call.name, result.output)
        return self.public(result.output)
