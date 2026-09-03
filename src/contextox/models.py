import unicodedata
from datetime import datetime
from typing import Literal
from uuid import RFC_4122, UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


HealthStatus = Literal["ready", "not_implemented", "not_run", "blocked"]
ReadinessStatus = Literal["ready", "partial", "blocked"]
EvidenceStatus = Literal["pass", "not_run", "pending", "not_verified"]


class ContextOxModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    workspace_id: str = Field(min_length=36, max_length=36)
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
