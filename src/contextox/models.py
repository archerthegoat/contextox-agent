from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    scope: Literal["n1"]
    checks: list[DoctorCheck]


class AgentRunResult(ContextOxModel):
    status: Literal["not_implemented"]
    detail: str
