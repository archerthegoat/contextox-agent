from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import RFC_4122, UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


HealthStatus = Literal["ready", "not_implemented", "not_run", "blocked"]
ReadinessStatus = Literal["ready", "partial", "blocked"]
EvidenceStatus = Literal["pass", "not_run", "pending", "not_verified"]


def _validate_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("id must be a canonical UUIDv4")
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError("id must be a canonical UUIDv4") from exc
    if parsed.version != 4 or parsed.variant != RFC_4122 or str(parsed) != value:
        raise ValueError("id must be a canonical UUIDv4")
    return value


def _validate_hash(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("hash must be a lowercase SHA-256")
    return value


def _validate_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("key must be a string")
    if not 1 <= len(value) <= 128:
        raise ValueError("key must contain 1 to 128 characters")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError("key contains a control character")
    return value


def _validate_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC timezone")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("timestamp must be UTC")
    return value


ID = Annotated[
    StrictStr,
    Field(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
    BeforeValidator(_validate_id),
]
Hash = Annotated[
    StrictStr,
    Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    BeforeValidator(_validate_hash),
]
Key = Annotated[
    StrictStr,
    Field(min_length=1, max_length=128),
    BeforeValidator(_validate_key),
]
Text = Annotated[StrictStr, Field(max_length=4096)]
Count = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
UTC = Annotated[datetime, AfterValidator(_validate_utc)]
ShortTitle = Annotated[StrictStr, Field(min_length=1, max_length=160)]
RawInput = Annotated[StrictStr, Field(min_length=1, max_length=16384)]
FinalOutput = Annotated[StrictStr, Field(max_length=32768)]


class ContextOxModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def canonical_sha256(value: BaseModel | dict[str, Any]) -> str:
    """Return the contract's deterministic JSON-mode SHA-256."""

    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class HealthCheck(ContextOxModel):
    key: str = Field(min_length=1)
    status: HealthStatus
    detail: str = Field(min_length=1)


class HealthResponse(ContextOxModel):
    status: Literal["ready", "blocked"]
    product_status: Literal["partial"]
    service: Literal["contextox-workbench"]
    version: str
    bind_address: Literal["127.0.0.1"]
    checks: list[HealthCheck]


class ReadinessResponse(ContextOxModel):
    status: ReadinessStatus
    label: str
    checks: list[HealthCheck]


class WorkbenchArea(ContextOxModel):
    id: Literal["sources", "mission", "clarifications", "contract"]
    label: str
    description: str
    status: Literal["ready", "not_implemented"]


class EvidenceLane(ContextOxModel):
    key: Literal[
        "static",
        "automated",
        "runtime",
        "real_model",
        "human_acceptance",
        "user_value",
    ]
    label: str
    status: EvidenceStatus
    detail: str


class WorkbenchSnapshot(ContextOxModel):
    product: Literal["ContextOx Workbench"]
    architecture: Literal["local-python-react"]
    status: Literal["partial"]
    notice: str
    readiness: ReadinessResponse
    areas: list[WorkbenchArea]
    evidence: list[EvidenceLane]


class Workspace(ContextOxModel):
    workspace_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )
    display_name: str = Field(min_length=1, max_length=80)
    created_at: datetime

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace_id(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (AttributeError, ValueError) as exc:
            raise ValueError("workspace_id must be a canonical UUIDv4") from exc
        if (
            parsed.version != 4
            or str(parsed) != value
            or parsed.variant != RFC_4122
        ):
            raise ValueError("workspace_id must be a canonical UUIDv4")
        return value

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if any(
            unicodedata.category(character) in {"Cc", "Cf"}
            for character in value
            if character != " "
        ):
            raise ValueError("display_name contains a control character")
        normalized = value.strip()
        if not 1 <= len(normalized) <= 80:
            raise ValueError("display_name must contain 1 to 80 characters")
        return normalized

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class WorkspaceCreateRequest(ContextOxModel):
    display_name: str

    @field_validator("display_name", mode="before")
    @classmethod
    def validate_display_name_input(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("display_name must be a string")
        if any(
            unicodedata.category(character) in {"Cc", "Cf"}
            for character in value
            if character != " "
        ):
            raise ValueError("display_name contains a control character")
        normalized = value.strip()
        if not 1 <= len(normalized) <= 80:
            raise ValueError("display_name must contain 1 to 80 characters")
        return normalized


class WorkspaceError(ContextOxModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    request_id: str = Field(min_length=1)


class EventEnvelope(ContextOxModel):
    event_id: str = Field(min_length=1)
    event_type: Literal["connected"]
    occurred_at: datetime
    workspace_id: str | None = None
    mission_id: str | None = None
    run_id: str | None = None
    sequence: int = Field(ge=1)
    public_payload: dict[str, str]


class DoctorCheck(ContextOxModel):
    key: str = Field(min_length=1)
    status: HealthStatus
    detail: str = Field(min_length=1)
    actual: str | None = None
    expected: str | None = None


class DoctorReport(ContextOxModel):
    status: ReadinessStatus
    scope: Literal["n2a"]
    checks: list[DoctorCheck]


class AgentRunResult(ContextOxModel):
    status: Literal["not_implemented"]
    detail: str


class SourceIdentity(ContextOxModel):
    workspace_id: ID
    source_id: ID
    revision_id: ID
    sha256: Hash


MediaType = Literal["text/csv", "application/json", "text/markdown", "text/plain"]
PermissionStatus = Literal["read_allowed", "denied", "unknown"]
ParseStatus = Literal["pending", "ready", "partial", "blocked", "failed"]


class SourceRevision(ContextOxModel):
    workspace_id: ID
    source_id: ID
    revision_id: ID
    original_name: Key
    media_type: MediaType
    byte_size: Count
    sha256: Hash
    observed_at: UTC
    effective_time: UTC | None
    permission_status: PermissionStatus
    parse_status: ParseStatus
    parser_version: Key


class CsvRowsLocator(ContextOxModel):
    kind: Literal["csv_rows"]
    row_start: PositiveInt
    row_end: PositiveInt
    column: Key | None

    @model_validator(mode="after")
    def validate_range(self) -> CsvRowsLocator:
        if self.row_start > self.row_end:
            raise ValueError("row_start must be less than or equal to row_end")
        return self


class JsonPointerLocator(ContextOxModel):
    kind: Literal["json_pointer"]
    pointer: StrictStr = Field(max_length=4096)

    @field_validator("pointer")
    @classmethod
    def validate_pointer(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("JSON pointer must be empty or start with '/'")
        for index, character in enumerate(value):
            if character != "~":
                continue
            if index + 1 >= len(value) or value[index + 1] not in "01":
                raise ValueError("JSON pointer contains an invalid escape")
        return value


class TextLinesLocator(ContextOxModel):
    kind: Literal["text_lines"]
    line_start: PositiveInt
    line_end: PositiveInt

    @model_validator(mode="after")
    def validate_range(self) -> TextLinesLocator:
        if self.line_start > self.line_end:
            raise ValueError("line_start must be less than or equal to line_end")
        return self


EvidenceLocator = Annotated[
    CsvRowsLocator | JsonPointerLocator | TextLinesLocator,
    Field(discriminator="kind"),
]


class EvidenceRef(ContextOxModel):
    workspace_id: ID
    source_id: ID
    revision_id: ID
    sha256: Hash
    locator: EvidenceLocator


class SourceIssue(ContextOxModel):
    code: Key
    locator: EvidenceLocator | None
    message: Text


ValueKind = Literal["missing", "null", "string", "integer", "decimal", "boolean", "json"]


class SampleCell(ContextOxModel):
    column_name: Key
    value_kind: ValueKind
    text: Annotated[StrictStr | None, Field(max_length=256)]
    truncated: StrictBool

    @model_validator(mode="after")
    def validate_text_semantics(self) -> SampleCell:
        if self.value_kind in {"missing", "null"} and self.text is not None:
            raise ValueError("missing and null cells must have null text")
        if self.value_kind not in {"missing", "null"} and self.text is None:
            raise ValueError("observed cells must have text")
        if self.truncated and self.text is None:
            raise ValueError("truncated cells must have text")
        return self


class SampleRow(ContextOxModel):
    row_number: PositiveInt
    cells: list[SampleCell] = Field(max_length=100)
    source_refs: list[EvidenceRef]


class ColumnProfile(ContextOxModel):
    name: Key
    observed_types: list[Key]
    missing_count: Count
    null_count: Count
    distinct_count: Count
    numeric_min: Text | None
    numeric_max: Text | None


class TableProfile(ContextOxModel):
    table_id: StrictStr = Field(max_length=4096)
    row_count: Count
    columns: list[ColumnProfile] = Field(max_length=100)
    duplicate_row_count: Count
    sample_rows: list[SampleRow] = Field(max_length=5)
    source_refs: list[EvidenceRef]


class SourceArtifact(ContextOxModel):
    source_ref: SourceIdentity
    parser_version: Key
    parse_status: Literal["ready", "partial", "blocked", "failed"]
    tables: list[TableProfile]
    text_line_count: Count | None
    issues: list[SourceIssue]

    @model_validator(mode="after")
    def validate_workspace_scope(self) -> SourceArtifact:
        workspace_ids = {
            self.source_ref.workspace_id,
            *(reference.workspace_id for table in self.tables for reference in table.source_refs),
            *(
                reference.workspace_id
                for table in self.tables
                for row in table.sample_rows
                for reference in row.source_refs
            ),
        }
        if len(workspace_ids) > 1:
            raise ValueError("SourceArtifact evidence must belong to one Workspace")
        if self.source_ref.workspace_id not in workspace_ids:
            raise ValueError("SourceArtifact evidence must match source_ref")
        return self


class SourceExcerpt(ContextOxModel):
    source_ref: EvidenceRef
    text: StrictStr = Field(max_length=8192)
    truncated: StrictBool


class TableKey(ContextOxModel):
    source_ref: SourceIdentity
    table_id: StrictStr = Field(max_length=4096)
    columns: list[Key]


Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many", "unknown"]


class RelationshipProfile(ContextOxModel):
    left: TableKey
    right: TableKey
    left_rows: Count
    right_rows: Count
    left_distinct_keys: Count
    right_distinct_keys: Count
    left_null_keys: Count
    right_null_keys: Count
    matched_distinct_keys: Count
    unmatched_left_rows: Count
    unmatched_right_rows: Count
    prospective_join_rows: Count
    observed_cardinality: Cardinality
    source_refs: list[EvidenceRef]
    limitations: list[Text]

    @model_validator(mode="after")
    def validate_workspace_scope(self) -> RelationshipProfile:
        workspace_ids = {
            self.left.source_ref.workspace_id,
            self.right.source_ref.workspace_id,
            *(reference.workspace_id for reference in self.source_refs),
        }
        if len(workspace_ids) > 1:
            raise ValueError("relationship evidence must belong to one Workspace")
        return self


class MissionDraftPayload(ContextOxModel):
    title: ShortTitle
    goal: Text
    completion_criteria: list[Text] = Field(min_length=1, max_length=20)
    scope_notes: list[Text] = Field(max_length=20)


MissionDraftStatus = Literal[
    "queued", "running", "ready", "confirmed", "blocked", "failed", "cancelled"
]


class MissionDraftAttempt(ContextOxModel):
    workspace_id: ID
    attempt_id: ID
    created_at: UTC
    original_input: RawInput
    status: MissionDraftStatus
    candidate: MissionDraftPayload | None
    candidate_version: PositiveInt | None
    candidate_sha256: Hash | None
    provider_receipt_id: ID | None
    mission_id: ID | None
    error_code: Key | None

    @model_validator(mode="after")
    def validate_candidate_and_status(self) -> MissionDraftAttempt:
        candidate_values = (self.candidate_version, self.candidate_sha256)
        if self.candidate is None:
            if any(value is not None for value in candidate_values):
                raise ValueError("candidate version and hash require a candidate")
        else:
            if any(value is None for value in candidate_values):
                raise ValueError("candidate requires version and hash")
            if self.candidate_sha256 != canonical_sha256(self.candidate):
                raise ValueError("candidate_sha256 does not match candidate")
        if self.status == "ready":
            if (
                self.candidate is None
                or self.candidate_version is None
                or self.candidate_sha256 is None
                or self.provider_receipt_id is None
            ):
                raise ValueError("ready attempt requires candidate and provider receipt")
        if self.status == "confirmed" and self.mission_id is None:
            raise ValueError("confirmed attempt requires mission_id")
        if self.status != "confirmed" and self.mission_id is not None:
            raise ValueError("mission_id is only valid for a confirmed attempt")
        if self.status in {"failed", "blocked", "cancelled"} and self.mission_id is not None:
            raise ValueError("failed attempts cannot contain mission_id")
        return self


MissionStatus = Literal["active", "waiting_for_human", "blocked", "completed", "cancelled"]


class Mission(ContextOxModel):
    workspace_id: ID
    mission_id: ID
    created_at: UTC
    state_version: PositiveInt
    status: MissionStatus
    title: ShortTitle
    goal: Text
    completion_criteria: list[Text]
    scope_notes: list[Text]
    original_attempt_id: ID
    source_refs: list[SourceIdentity] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_source_workspace(self) -> Mission:
        if any(reference.workspace_id != self.workspace_id for reference in self.source_refs):
            raise ValueError("Mission source references must match workspace_id")
        return self


class RunBudget(ContextOxModel):
    max_model_turns: Literal[8] = 8
    max_tool_calls: Literal[24] = 24
    max_elapsed_ms: Literal[300000] = 300000
    max_output_tokens: Literal[4096] = 4096
    max_retries: Literal[0] = 0
    connect_timeout_ms: Literal[10000] = 10000
    first_event_timeout_ms: Literal[60000] = 60000
    idle_timeout_ms: Literal[30000] = 30000
    total_timeout_ms: Literal[120000] = 120000
    max_context_bytes: Literal[262144] = 262144

    @model_validator(mode="before")
    @classmethod
    def validate_fixed_values(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        expected = {
            "max_model_turns": 8,
            "max_tool_calls": 24,
            "max_elapsed_ms": 300000,
            "max_output_tokens": 4096,
            "max_retries": 0,
            "connect_timeout_ms": 10000,
            "first_event_timeout_ms": 60000,
            "idle_timeout_ms": 30000,
            "total_timeout_ms": 120000,
            "max_context_bytes": 262144,
        }
        for name, expected_value in expected.items():
            if name in values and (type(values[name]) is not int or values[name] != expected_value):
                raise ValueError(f"{name} is a fixed RunBudget value")
        return values


RunStatus = Literal[
    "queued", "running", "waiting_for_human", "partial", "completed", "blocked", "failed", "cancelled"
]


class RunSnapshot(ContextOxModel):
    workspace_id: ID
    mission_id: ID
    run_id: ID
    status: RunStatus
    created_at: UTC
    started_at: UTC | None
    finished_at: UTC | None
    budget: RunBudget
    source_refs: list[SourceIdentity] = Field(max_length=8)
    draft: DefinitionDraft | None
    clarifications: list[ClarificationRequest]
    last_sequence: Count
    terminal_receipt: TerminalReceipt | None
    final_output: FinalOutput | None
    error_code: Key | None

    @model_validator(mode="after")
    def validate_nested_scope(self) -> RunSnapshot:
        if any(reference.workspace_id != self.workspace_id for reference in self.source_refs):
            raise ValueError("Run source references must match workspace_id")
        if self.draft is not None and (
            self.draft.workspace_id != self.workspace_id or self.draft.mission_id != self.mission_id
        ):
            raise ValueError("Run draft identity does not match the Run")
        for clarification in self.clarifications:
            if (
                clarification.workspace_id != self.workspace_id
                or clarification.mission_id != self.mission_id
                or clarification.run_id != self.run_id
            ):
                raise ValueError("Run clarification identity does not match the Run")
        if self.terminal_receipt is not None and (
            self.terminal_receipt.workspace_id != self.workspace_id
            or self.terminal_receipt.mission_id != self.mission_id
            or self.terminal_receipt.run_id != self.run_id
        ):
            raise ValueError("Run terminal receipt identity does not match the Run")
        return self


class MissionSnapshot(ContextOxModel):
    mission: Mission
    draft: DefinitionDraft | None
    clarifications: list[ClarificationRequest]
    latest_run: RunSnapshot | None

    @model_validator(mode="after")
    def validate_nested_scope(self) -> MissionSnapshot:
        if self.draft is not None and (
            self.draft.workspace_id != self.mission.workspace_id
            or self.draft.mission_id != self.mission.mission_id
        ):
            raise ValueError("Mission draft identity does not match the Mission")
        for clarification in self.clarifications:
            if (
                clarification.workspace_id != self.mission.workspace_id
                or clarification.mission_id != self.mission.mission_id
            ):
                raise ValueError("Mission clarification identity does not match the Mission")
        if self.latest_run is not None and (
            self.latest_run.workspace_id != self.mission.workspace_id
            or self.latest_run.mission_id != self.mission.mission_id
        ):
            raise ValueError("Mission Run identity does not match the Mission")
        return self


StatementEvidenceStatus = Literal["observed", "candidate", "conflict", "unknown"]


class ColumnRef(ContextOxModel):
    source_ref: SourceIdentity
    table_id: StrictStr = Field(max_length=4096)
    column: Key


class UnknownItem(ContextOxModel):
    property_path: Key
    reason: Text


class DefinitionField(ContextOxModel):
    field_key: Key
    name: Key
    meaning: Text | None
    value_type: Key | None
    grain: Text | None
    source_columns: list[ColumnRef]
    rule: Text | None
    time_basis: Text | None
    null_handling: Text | None
    evidence_status: StatementEvidenceStatus
    source_refs: list[EvidenceRef]
    unknowns: list[UnknownItem]

    @model_validator(mode="after")
    def validate_evidence_and_unknowns(self) -> DefinitionField:
        semantic_values = (
            self.meaning,
            self.value_type,
            self.grain,
            self.rule,
            self.time_basis,
            self.null_handling,
        )
        missing_paths = {
            property_name
            for property_name, value in zip(
                (
                    "meaning",
                    "value_type",
                    "grain",
                    "rule",
                    "time_basis",
                    "null_handling",
                ),
                semantic_values,
            )
            if value is None
        }
        unknown_paths = {unknown.property_path for unknown in self.unknowns}
        if unknown_paths != missing_paths or len(unknown_paths) != len(self.unknowns):
            raise ValueError("unknowns must match each missing semantic dimension exactly once")
        if self.evidence_status == "observed" and not self.source_refs:
            raise ValueError("observed definitions require source_refs")
        return self


class RelationshipCandidate(ContextOxModel):
    relationship_key: Key
    left: TableKey
    right: TableKey
    observed_cardinality: Cardinality
    join_rule: Text | None
    grain_notes: Text | None
    evidence_status: StatementEvidenceStatus
    source_refs: list[EvidenceRef]
    risks: list[Text]
    unknowns: list[UnknownItem]

    @model_validator(mode="after")
    def validate_workspace_scope(self) -> RelationshipCandidate:
        workspace_ids = {
            self.left.source_ref.workspace_id,
            self.right.source_ref.workspace_id,
            *(reference.workspace_id for reference in self.source_refs),
        }
        if len(workspace_ids) > 1:
            raise ValueError("relationship candidate evidence must belong to one Workspace")
        if self.evidence_status == "observed" and not self.source_refs:
            raise ValueError("observed relationships require source_refs")
        return self


class DefinitionDraft(ContextOxModel):
    workspace_id: ID
    mission_id: ID
    draft_id: ID
    version: PositiveInt
    sha256: Hash
    status: Literal["draft", "in_review"]
    semantic_approval: Literal["pending"]
    fields: list[DefinitionField] = Field(max_length=100)
    relationships: list[RelationshipCandidate] = Field(max_length=100)
    unresolved_items: list[Text]

    @model_validator(mode="after")
    def validate_draft_hash_and_keys(self) -> DefinitionDraft:
        field_keys = [field.field_key for field in self.fields]
        relationship_keys = [relationship.relationship_key for relationship in self.relationships]
        if len(field_keys) != len(set(field_keys)):
            raise ValueError("field_key values must be unique")
        if len(relationship_keys) != len(set(relationship_keys)):
            raise ValueError("relationship_key values must be unique")
        payload = {
            "fields": [field.model_dump(mode="json") for field in self.fields],
            "relationships": [relationship.model_dump(mode="json") for relationship in self.relationships],
            "unresolved_items": self.unresolved_items,
        }
        if self.sha256 != canonical_sha256(payload):
            raise ValueError("sha256 does not match the DefinitionDraft payload")
        nested_workspace_ids = {
            reference.workspace_id
            for field in self.fields
            for reference in field.source_refs
        } | {
            reference.workspace_id
            for relationship in self.relationships
            for reference in relationship.source_refs
        }
        if len(nested_workspace_ids) > 1 or (
            nested_workspace_ids and self.workspace_id not in nested_workspace_ids
        ):
            raise ValueError("DefinitionDraft evidence must match workspace_id")
        if any(
            source_column.source_ref.workspace_id != self.workspace_id
            for field in self.fields
            for source_column in field.source_columns
        ):
            raise ValueError("DefinitionDraft source columns must match workspace_id")
        if any(
            table.source_ref.workspace_id != self.workspace_id
            for relationship in self.relationships
            for table in (relationship.left, relationship.right)
        ):
            raise ValueError("DefinitionDraft relationship tables must match workspace_id")
        return self


AnswerType = Literal["text", "number", "boolean", "date", "enum", "range", "field_selection"]


class ClarificationQuestion(ContextOxModel):
    question: Text
    why_needed: Text
    expected_answer_type: AnswerType
    suggested_owner_role: Key | None
    related_definition_paths: list[Key]
    evidence_requested: list[Text]
    examples_or_options: list[Text]
    blocking_impact: Literal["blocking", "non_blocking"]
    source_refs: list[EvidenceRef]


class ClarificationRequest(ContextOxModel):
    workspace_id: ID
    mission_id: ID
    run_id: ID
    clarification_id: ID
    draft_version: PositiveInt
    draft_sha256: Hash
    status: Literal["awaiting_answer"]
    questions: list[ClarificationQuestion] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_workspace_scope(self) -> ClarificationRequest:
        references = [
            reference
            for question in self.questions
            for reference in question.source_refs
        ]
        if any(reference.workspace_id != self.workspace_id for reference in references):
            raise ValueError("Clarification evidence must match workspace_id")
        return self


class ProviderConfigSnapshot(ContextOxModel):
    endpoint_id: Literal["deepseek_chat_completions"]
    model: Literal["deepseek-v4-flash", "deepseek-v4-pro"]
    thinking: Literal["enabled"]
    reasoning_effort: Literal["high"]


ProviderReceiptStatus = Literal["succeeded", "blocked", "failed", "cancelled"]


class ProviderReceipt(ContextOxModel):
    workspace_id: ID
    receipt_id: ID
    attempt_id: ID | None
    mission_id: ID | None
    run_id: ID | None
    turn_index: PositiveInt
    created_at: UTC
    status: ProviderReceiptStatus
    config: ProviderConfigSnapshot
    p0_sha256: Hash
    input_tokens: Count | None
    output_tokens: Count | None
    cache_hit_tokens: Count | None
    cache_miss_tokens: Count | None
    context_manifest_id: ID | None
    context_manifest_sha256: Hash | None
    tool_schema_sha256: Hash | None
    error_code: Key | None

    @model_validator(mode="after")
    def validate_receipt_mode(self) -> ProviderReceipt:
        if (self.attempt_id is None) == (self.run_id is None):
            raise ValueError("ProviderReceipt must reference exactly one attempt or run")
        if self.attempt_id is not None:
            if self.turn_index != 1:
                raise ValueError("attempt receipts must use turn_index 1")
            if self.mission_id is not None:
                raise ValueError("attempt receipts cannot reference a mission")
            if (
                self.context_manifest_id is not None
                or self.context_manifest_sha256 is not None
                or self.tool_schema_sha256 is not None
            ):
                raise ValueError("attempt receipts cannot contain run manifest metadata")
        else:
            if self.turn_index > 8:
                raise ValueError("Run receipts cannot exceed eight model turns")
            if self.mission_id is None:
                raise ValueError("run receipts require mission_id")
            if (
                self.context_manifest_id is None
                or self.context_manifest_sha256 is None
                or self.tool_schema_sha256 is None
            ):
                raise ValueError("run receipts require manifest and tool schema hashes")
        if self.status == "succeeded" and (
            self.input_tokens is None or self.output_tokens is None
        ):
            raise ValueError("succeeded ProviderReceipt requires input and output usage")
        if (self.context_manifest_id is None) != (self.context_manifest_sha256 is None):
            raise ValueError("context manifest id and hash must be paired")
        return self


DomainToolName = Literal[
    "list_sources",
    "read_source",
    "inspect_dataset",
    "update_definition_draft",
    "create_clarification",
    "submit_for_review",
    "finish_run",
]


class ToolReceipt(ContextOxModel):
    workspace_id: ID
    mission_id: ID
    run_id: ID
    receipt_id: ID
    ordinal: PositiveInt
    call_id: Key
    name: DomainToolName
    arguments_sha256: Hash
    status: Literal["succeeded", "rejected", "blocked", "failed"]
    created_at: UTC
    source_refs: list[EvidenceRef]
    error_code: Key | None

    @model_validator(mode="after")
    def validate_workspace_scope(self) -> ToolReceipt:
        if any(reference.workspace_id != self.workspace_id for reference in self.source_refs):
            raise ValueError("ToolReceipt source references must match workspace_id")
        return self


TerminalTool = Literal["create_clarification", "submit_for_review", "finish_run"]


class TerminalReceipt(ContextOxModel):
    workspace_id: ID
    mission_id: ID
    run_id: ID
    receipt_id: ID
    created_at: UTC
    terminal_tool: TerminalTool
    outcome: Literal["waiting_for_human", "partial"]
    draft_id: ID | None
    draft_version: PositiveInt | None
    draft_sha256: Hash | None
    clarification_ids: list[ID]
    provider_receipt_ids: list[ID]
    tool_receipt_ids: list[ID]
    source_refs: list[EvidenceRef]

    @model_validator(mode="after")
    def validate_terminal(self) -> TerminalReceipt:
        draft_values = (self.draft_id, self.draft_version, self.draft_sha256)
        if any(value is not None for value in draft_values) and not all(
            value is not None for value in draft_values
        ):
            raise ValueError("draft id, version, and hash must be all present or all null")
        expected_outcome = "partial" if self.terminal_tool == "finish_run" else "waiting_for_human"
        if self.outcome != expected_outcome:
            raise ValueError("terminal tool and outcome do not match")
        if any(reference.workspace_id != self.workspace_id for reference in self.source_refs):
            raise ValueError("TerminalReceipt source references must match workspace_id")
        return self


class ContextManifestInput(ContextOxModel):
    mission_state_version: PositiveInt
    turn_index: PositiveInt
    draft_id: ID | None
    draft_version: PositiveInt | None
    draft_sha256: Hash | None
    source_refs: list[SourceIdentity] = Field(max_length=8)
    clarification_ids: list[ID]
    tool_receipt_ids: list[ID]
    budget: RunBudget
    excluded_reasons: list[Key]

    @model_validator(mode="after")
    def validate_draft_reference(self) -> ContextManifestInput:
        if self.turn_index > 8:
            raise ValueError("Context manifests cannot exceed eight model turns")
        values = (self.draft_id, self.draft_version, self.draft_sha256)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError("draft id, version, and hash must be all present or all null")
        return self


class ContextPacketManifest(ContextManifestInput):
    workspace_id: ID
    mission_id: ID
    run_id: ID
    manifest_id: ID
    sha256: Hash

    @model_validator(mode="after")
    def validate_manifest_hash(self) -> ContextPacketManifest:
        if any(reference.workspace_id != self.workspace_id for reference in self.source_refs):
            raise ValueError("Context manifest source references must match workspace_id")
        if self.sha256 != canonical_sha256(self.model_dump(mode="json", exclude={"sha256"})):
            raise ValueError("sha256 does not match the ContextPacketManifest payload")
        return self


class ContextSnapshot(ContextOxModel):
    mission: Mission
    run: RunSnapshot
    sources: list[SourceRevision]
    draft: DefinitionDraft | None
    clarifications: list[ClarificationRequest]

    @model_validator(mode="after")
    def validate_nested_scope(self) -> ContextSnapshot:
        mission = self.mission
        run = self.run
        if run.workspace_id != mission.workspace_id or run.mission_id != mission.mission_id:
            raise ValueError("ContextSnapshot Run identity does not match the Mission")
        if any(source.workspace_id != mission.workspace_id for source in self.sources):
            raise ValueError("ContextSnapshot sources must match the Mission Workspace")
        if self.draft is not None and (
            self.draft.workspace_id != mission.workspace_id
            or self.draft.mission_id != mission.mission_id
        ):
            raise ValueError("ContextSnapshot draft identity does not match the Mission")
        for clarification in self.clarifications:
            if (
                clarification.workspace_id != mission.workspace_id
                or clarification.mission_id != mission.mission_id
                or clarification.run_id != run.run_id
            ):
                raise ValueError("ContextSnapshot clarification identity does not match the Run")
        return self


class DomainRejection(ContextOxModel):
    code: Key
    reason: Text


class ListSourcesArguments(ContextOxModel):
    pass


class ReadSourceArguments(ContextOxModel):
    revision_id: ID
    locator: EvidenceLocator


class InspectTableArguments(ContextOxModel):
    kind: Literal["table"]
    revision_id: ID
    table_id: StrictStr = Field(max_length=4096)


class InspectRelationshipArguments(ContextOxModel):
    kind: Literal["relationship"]
    left: TableKey
    right: TableKey


InspectDatasetArguments = Annotated[
    InspectTableArguments | InspectRelationshipArguments,
    Field(discriminator="kind"),
]


class UpdateDefinitionDraftArguments(ContextOxModel):
    expected_version: Count
    expected_sha256: Hash | None
    fields: list[DefinitionField] = Field(max_length=100)
    relationships: list[RelationshipCandidate] = Field(max_length=100)
    unresolved_items: list[Text]

    @model_validator(mode="after")
    def validate_cas_pair(self) -> UpdateDefinitionDraftArguments:
        if self.expected_version == 0 and self.expected_sha256 is not None:
            raise ValueError("expected_sha256 must be null for the initial draft")
        if self.expected_version > 0 and self.expected_sha256 is None:
            raise ValueError("expected_sha256 is required after the initial draft")
        return self


class CreateClarificationArguments(ContextOxModel):
    draft_version: PositiveInt
    draft_sha256: Hash
    questions: list[ClarificationQuestion] = Field(min_length=1, max_length=20)


class SubmitForReviewArguments(ContextOxModel):
    draft_version: PositiveInt
    draft_sha256: Hash


class FinishRunArguments(ContextOxModel):
    outcome: Literal["partial"]
    reason: Text
    source_refs: list[EvidenceRef]


class ListSourcesCall(ContextOxModel):
    call_id: Key
    name: Literal["list_sources"]
    arguments: ListSourcesArguments


class ReadSourceCall(ContextOxModel):
    call_id: Key
    name: Literal["read_source"]
    arguments: ReadSourceArguments


class InspectDatasetCall(ContextOxModel):
    call_id: Key
    name: Literal["inspect_dataset"]
    arguments: InspectDatasetArguments


class UpdateDefinitionDraftCall(ContextOxModel):
    call_id: Key
    name: Literal["update_definition_draft"]
    arguments: UpdateDefinitionDraftArguments


class CreateClarificationCall(ContextOxModel):
    call_id: Key
    name: Literal["create_clarification"]
    arguments: CreateClarificationArguments


class SubmitForReviewCall(ContextOxModel):
    call_id: Key
    name: Literal["submit_for_review"]
    arguments: SubmitForReviewArguments


class FinishRunCall(ContextOxModel):
    call_id: Key
    name: Literal["finish_run"]
    arguments: FinishRunArguments


DomainToolCall = Annotated[
    ListSourcesCall
    | ReadSourceCall
    | InspectDatasetCall
    | UpdateDefinitionDraftCall
    | CreateClarificationCall
    | SubmitForReviewCall
    | FinishRunCall,
    Field(discriminator="name"),
]


RunToolOutput = (
    list[SourceRevision]
    | SourceExcerpt
    | TableProfile
    | RelationshipProfile
    | DefinitionDraft
    | ClarificationRequest
    | TerminalReceipt
    | DomainRejection
)


class RunToolResult(ContextOxModel):
    call_id: Key
    status: Literal["succeeded", "rejected"]
    output: RunToolOutput
    tool_receipt: ToolReceipt
    terminal_snapshot: RunSnapshot | None

    @model_validator(mode="after")
    def validate_output_for_tool(self) -> RunToolResult:
        if self.call_id != self.tool_receipt.call_id:
            raise ValueError("call_id must match the ToolReceipt")
        receipt = self.tool_receipt
        if self.terminal_snapshot is not None and (
            self.terminal_snapshot.workspace_id != receipt.workspace_id
            or self.terminal_snapshot.mission_id != receipt.mission_id
            or self.terminal_snapshot.run_id != receipt.run_id
        ):
            raise ValueError("terminal_snapshot identity does not match the ToolReceipt")
        if self.status == "rejected":
            if not isinstance(self.output, DomainRejection):
                raise ValueError("rejected tool results require DomainRejection output")
            if self.tool_receipt.status != "rejected":
                raise ValueError("rejected tool results require a rejected ToolReceipt")
            return self
        if isinstance(self.output, DomainRejection):
            raise ValueError("succeeded tool results cannot contain DomainRejection output")
        if self.tool_receipt.status != "succeeded":
            raise ValueError("succeeded tool results require a succeeded ToolReceipt")
        expected = {
            "list_sources": (list,),
            "read_source": (SourceExcerpt,),
            "inspect_dataset": (TableProfile, RelationshipProfile),
            "update_definition_draft": (DefinitionDraft,),
            "create_clarification": (ClarificationRequest,),
            "submit_for_review": (DefinitionDraft,),
            "finish_run": (TerminalReceipt,),
        }[self.tool_receipt.name]
        if not isinstance(self.output, expected):
            raise ValueError("tool result output does not match the tool name")
        if isinstance(self.output, list):
            if any(item.workspace_id != receipt.workspace_id for item in self.output):
                raise ValueError("tool output Workspace does not match the ToolReceipt")
        elif isinstance(self.output, SourceExcerpt):
            if self.output.source_ref.workspace_id != receipt.workspace_id:
                raise ValueError("tool output evidence does not match the ToolReceipt")
        elif isinstance(self.output, TableProfile):
            table_evidence = [
                *self.output.source_refs,
                *(
                    reference
                    for row in self.output.sample_rows
                    for reference in row.source_refs
                ),
            ]
            if any(reference.workspace_id != receipt.workspace_id for reference in table_evidence):
                raise ValueError("tool output evidence does not match the ToolReceipt")
        elif isinstance(self.output, RelationshipProfile):
            relationship_workspace_ids = {
                self.output.left.source_ref.workspace_id,
                self.output.right.source_ref.workspace_id,
                *(reference.workspace_id for reference in self.output.source_refs),
            }
            if relationship_workspace_ids != {receipt.workspace_id}:
                raise ValueError("tool output evidence does not match the ToolReceipt")
        elif isinstance(self.output, DefinitionDraft):
            if (
                self.output.workspace_id != receipt.workspace_id
                or self.output.mission_id != receipt.mission_id
            ):
                raise ValueError("tool output identity does not match the ToolReceipt")
        elif isinstance(self.output, ClarificationRequest):
            if (
                self.output.workspace_id != receipt.workspace_id
                or self.output.mission_id != receipt.mission_id
                or self.output.run_id != receipt.run_id
            ):
                raise ValueError("tool output identity does not match the ToolReceipt")
        elif isinstance(self.output, TerminalReceipt):
            if (
                self.output.workspace_id != receipt.workspace_id
                or self.output.mission_id != receipt.mission_id
                or self.output.run_id != receipt.run_id
            ):
                raise ValueError("tool output identity does not match the ToolReceipt")
        return self


class RunStartedPayload(ContextOxModel):
    status: Literal["running"]


class MessageCreatedPayload(ContextOxModel):
    message_id: ID
    role: Literal["user", "assistant"]


class ModelStartedPayload(ContextOxModel):
    turn_index: PositiveInt


class ModelDeltaPayload(ContextOxModel):
    turn_index: PositiveInt
    content: StrictStr = Field(max_length=4096)


class ModelCompletedPayload(ContextOxModel):
    turn_index: PositiveInt
    provider_receipt_id: ID


class ToolRequestedPayload(ContextOxModel):
    call_id: Key
    name: DomainToolName
    ordinal: PositiveInt


class ToolCompletedPayload(ContextOxModel):
    call_id: Key
    tool_receipt_id: ID
    status: Literal["succeeded", "rejected"]


class ToolFailedPayload(ContextOxModel):
    call_id: Key
    error_code: Key


class DraftUpdatedPayload(ContextOxModel):
    draft_id: ID
    version: PositiveInt
    sha256: Hash


class ClarificationRequestedPayload(ContextOxModel):
    clarification_id: ID
    draft_version: PositiveInt
    draft_sha256: Hash


class RunTerminalPayload(ContextOxModel):
    status: RunStatus
    terminal_receipt_id: ID | None
    error_code: Key | None


class RunCompletedPayload(RunTerminalPayload):
    status: Literal["completed"]


class RunPartialPayload(RunTerminalPayload):
    status: Literal["partial"]


class RunBlockedPayload(RunTerminalPayload):
    status: Literal["blocked"]


class RunFailedPayload(RunTerminalPayload):
    status: Literal["failed"]


class RunCancelledPayload(RunTerminalPayload):
    status: Literal["cancelled"]


RunEventType = Literal[
    "run_started",
    "message_created",
    "model_started",
    "model_delta",
    "model_completed",
    "tool_requested",
    "tool_started",
    "tool_completed",
    "tool_failed",
    "draft_updated",
    "clarification_requested",
    "run_completed",
    "run_partial",
    "run_blocked",
    "run_failed",
    "run_cancelled",
]


class RunStartedEventInput(ContextOxModel):
    event_type: Literal["run_started"]
    public_payload: RunStartedPayload


class MessageCreatedEventInput(ContextOxModel):
    event_type: Literal["message_created"]
    public_payload: MessageCreatedPayload


class ModelStartedEventInput(ContextOxModel):
    event_type: Literal["model_started"]
    public_payload: ModelStartedPayload


class ModelDeltaEventInput(ContextOxModel):
    event_type: Literal["model_delta"]
    public_payload: ModelDeltaPayload


class ModelCompletedEventInput(ContextOxModel):
    event_type: Literal["model_completed"]
    public_payload: ModelCompletedPayload


class ToolRequestedEventInput(ContextOxModel):
    event_type: Literal["tool_requested"]
    public_payload: ToolRequestedPayload


class ToolStartedEventInput(ContextOxModel):
    event_type: Literal["tool_started"]
    public_payload: ToolRequestedPayload


class ToolCompletedEventInput(ContextOxModel):
    event_type: Literal["tool_completed"]
    public_payload: ToolCompletedPayload


class ToolFailedEventInput(ContextOxModel):
    event_type: Literal["tool_failed"]
    public_payload: ToolFailedPayload


class DraftUpdatedEventInput(ContextOxModel):
    event_type: Literal["draft_updated"]
    public_payload: DraftUpdatedPayload


class ClarificationRequestedEventInput(ContextOxModel):
    event_type: Literal["clarification_requested"]
    public_payload: ClarificationRequestedPayload


class RunCompletedEventInput(ContextOxModel):
    event_type: Literal["run_completed"]
    public_payload: RunCompletedPayload


class RunPartialEventInput(ContextOxModel):
    event_type: Literal["run_partial"]
    public_payload: RunPartialPayload


class RunBlockedEventInput(ContextOxModel):
    event_type: Literal["run_blocked"]
    public_payload: RunBlockedPayload


class RunFailedEventInput(ContextOxModel):
    event_type: Literal["run_failed"]
    public_payload: RunFailedPayload


class RunCancelledEventInput(ContextOxModel):
    event_type: Literal["run_cancelled"]
    public_payload: RunCancelledPayload


RunEventInputUnion = Annotated[
    RunStartedEventInput
    | MessageCreatedEventInput
    | ModelStartedEventInput
    | ModelDeltaEventInput
    | ModelCompletedEventInput
    | ToolRequestedEventInput
    | ToolStartedEventInput
    | ToolCompletedEventInput
    | ToolFailedEventInput
    | DraftUpdatedEventInput
    | ClarificationRequestedEventInput
    | RunCompletedEventInput
    | RunPartialEventInput
    | RunBlockedEventInput
    | RunFailedEventInput
    | RunCancelledEventInput,
    Field(discriminator="event_type"),
]


class RunEventInput(RootModel[RunEventInputUnion]):
    @property
    def event_type(self) -> str:
        return self.root.event_type

    @property
    def public_payload(self) -> ContextOxModel:
        return self.root.public_payload

    def __init__(self, **data: Any) -> None:
        if "root" not in data:
            data = {"root": data}
        super().__init__(**data)


class _RunEventEnvelopeBase(ContextOxModel):
    event_id: Key
    occurred_at: UTC
    workspace_id: ID
    mission_id: ID
    run_id: ID
    sequence: PositiveInt


class RunStartedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["run_started"]
    public_payload: RunStartedPayload


class MessageCreatedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["message_created"]
    public_payload: MessageCreatedPayload


class ModelStartedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["model_started"]
    public_payload: ModelStartedPayload


class ModelDeltaEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["model_delta"]
    public_payload: ModelDeltaPayload


class ModelCompletedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["model_completed"]
    public_payload: ModelCompletedPayload


class ToolRequestedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["tool_requested"]
    public_payload: ToolRequestedPayload


class ToolStartedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["tool_started"]
    public_payload: ToolRequestedPayload


class ToolCompletedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["tool_completed"]
    public_payload: ToolCompletedPayload


class ToolFailedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["tool_failed"]
    public_payload: ToolFailedPayload


class DraftUpdatedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["draft_updated"]
    public_payload: DraftUpdatedPayload


class ClarificationRequestedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["clarification_requested"]
    public_payload: ClarificationRequestedPayload


class RunCompletedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["run_completed"]
    public_payload: RunCompletedPayload


class RunPartialEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["run_partial"]
    public_payload: RunPartialPayload


class RunBlockedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["run_blocked"]
    public_payload: RunBlockedPayload


class RunFailedEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["run_failed"]
    public_payload: RunFailedPayload


class RunCancelledEventEnvelope(_RunEventEnvelopeBase):
    event_type: Literal["run_cancelled"]
    public_payload: RunCancelledPayload


RunEventEnvelopeUnion = Annotated[
    RunStartedEventEnvelope
    | MessageCreatedEventEnvelope
    | ModelStartedEventEnvelope
    | ModelDeltaEventEnvelope
    | ModelCompletedEventEnvelope
    | ToolRequestedEventEnvelope
    | ToolStartedEventEnvelope
    | ToolCompletedEventEnvelope
    | ToolFailedEventEnvelope
    | DraftUpdatedEventEnvelope
    | ClarificationRequestedEventEnvelope
    | RunCompletedEventEnvelope
    | RunPartialEventEnvelope
    | RunBlockedEventEnvelope
    | RunFailedEventEnvelope
    | RunCancelledEventEnvelope,
    Field(discriminator="event_type"),
]


class RunEventEnvelope(RootModel[RunEventEnvelopeUnion]):
    @property
    def event_id(self) -> str:
        return self.root.event_id

    @property
    def event_type(self) -> str:
        return self.root.event_type

    @property
    def occurred_at(self) -> datetime:
        return self.root.occurred_at

    @property
    def public_payload(self) -> ContextOxModel:
        return self.root.public_payload

    def __init__(self, **data: Any) -> None:
        if "root" not in data:
            data = {"root": data}
        super().__init__(**data)


class SourceUploadFile(ContextOxModel):
    original_name: Key
    media_type: MediaType
    content_base64: StrictStr = Field(max_length=12 * 1024 * 1024)

    @field_validator("content_base64")
    @classmethod
    def validate_base64(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 must be valid base64") from exc
        if len(decoded) > 2 * 1024 * 1024:
            raise ValueError("source file exceeds the 2 MiB limit")
        return value


class SourceUploadRequest(ContextOxModel):
    files: list[SourceUploadFile] = Field(min_length=1, max_length=8)
    local_read_confirmed: StrictBool

    @model_validator(mode="after")
    def validate_upload(self) -> SourceUploadRequest:
        if not self.local_read_confirmed:
            raise ValueError("local_read_confirmed must be true")
        total = 0
        for file in self.files:
            try:
                total += len(base64.b64decode(file.content_base64, validate=True))
            except (binascii.Error, ValueError) as exc:
                raise ValueError("content_base64 must be valid base64") from exc
        if total > 8 * 1024 * 1024:
            raise ValueError("source batch exceeds the 8 MiB limit")
        return self


class SourceImportItem(ContextOxModel):
    file_index: Count
    original_name: Key
    status: Literal["accepted", "partial", "blocked", "failed"]
    revision: SourceRevision | None
    error: WorkspaceError | None

    @model_validator(mode="after")
    def validate_import_result(self) -> SourceImportItem:
        if self.status == "accepted" and self.revision is None:
            raise ValueError("accepted source imports require a revision")
        if self.status != "accepted" and self.revision is not None and self.error is None:
            raise ValueError("partial or failed source imports need an error when a revision exists")
        return self


class SourceBatchResult(ContextOxModel):
    items: list[SourceImportItem]


class SourceExcerptRequest(ContextOxModel):
    locator: EvidenceLocator


class MissionDraftAttemptCreateRequest(ContextOxModel):
    original_input: RawInput
    provider_send_confirmed: StrictBool

    @model_validator(mode="after")
    def validate_provider_confirmation(self) -> MissionDraftAttemptCreateRequest:
        if not self.provider_send_confirmed:
            raise ValueError("provider_send_confirmed must be true")
        return self


class MissionDraftConfirmRequest(ContextOxModel):
    candidate_version: PositiveInt
    candidate_sha256: Hash
    source_refs: list[SourceIdentity] = Field(max_length=8)


class RunStartRequest(ContextOxModel):
    expected_state_version: PositiveInt
    source_refs: list[SourceIdentity] = Field(max_length=8)
    provider_send_confirmed: StrictBool
    client_request_id: ID

    @model_validator(mode="after")
    def validate_provider_confirmation(self) -> RunStartRequest:
        if not self.provider_send_confirmed:
            raise ValueError("provider_send_confirmed must be true")
        return self


class CancelRunRequest(ContextOxModel):
    pass


# Resolve the forward references used by the nested shared models at import
# time so OpenAPI generation and direct model validation are deterministic.
RunSnapshot.model_rebuild()
MissionSnapshot.model_rebuild()
RunToolResult.model_rebuild()
