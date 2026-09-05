import asyncio
import base64
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import RFC_4122, UUID, uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, TypeAdapter
from starlette.exceptions import HTTPException as StarletteHTTPException

from contextox import __version__
from contextox.models import (
    AgentRunResult,
    CancelRunRequest,
    ClarificationRequest,
    ContextManifestInput,
    ContextPacketManifest,
    ContextSnapshot,
    DefinitionDraft,
    DomainRejection,
    DomainToolCall,
    DraftUpdatedPayload,
    EventEnvelope,
    EvidenceLane,
    EvidenceLocator,
    FinishRunArguments,
    HealthCheck,
    HealthResponse,
    InspectDatasetArguments,
    Mission,
    MissionDraftAttempt,
    MissionDraftAttemptCreateRequest,
    MissionDraftConfirmRequest,
    MissionDraftPayload,
    MissionSnapshot,
    ProviderConfigSnapshot,
    ProviderReceipt,
    ReadinessResponse,
    RelationshipProfile,
    RunEventEnvelope,
    RunEventInput,
    RunSnapshot,
    RunStartRequest,
    RunStartedPayload,
    RunToolResult,
    SourceArtifact,
    SourceBatchResult,
    SourceExcerpt,
    SourceExcerptRequest,
    SourceImportItem,
    SourceIdentity,
    SourceIssue,
    SourceRevision,
    SourceUploadFile,
    SourceUploadRequest,
    TableKey,
    TableProfile,
    TerminalReceipt,
    ToolReceipt,
    UpdateDefinitionDraftArguments,
    Workspace,
    WorkspaceCreateRequest,
    WorkspaceError,
    WorkbenchArea,
    WorkbenchSnapshot,
    ClarificationQuestion,
    CsvRowsLocator,
    DefinitionField,
    FinishRunCall,
    InspectDatasetCall,
    InspectRelationshipArguments,
    InspectTableArguments,
    JsonPointerLocator,
    ListSourcesArguments,
    ListSourcesCall,
    MessageCreatedPayload,
    ModelCompletedPayload,
    ModelDeltaPayload,
    ModelStartedPayload,
    ReadSourceArguments,
    ReadSourceCall,
    RelationshipCandidate,
    RunTerminalPayload,
    SampleCell,
    SampleRow,
    SubmitForReviewArguments,
    SubmitForReviewCall,
    TextLinesLocator,
    ToolCompletedPayload,
    ToolFailedPayload,
    ToolRequestedPayload,
    UnknownItem,
    UpdateDefinitionDraftCall,
    CreateClarificationArguments,
    CreateClarificationCall,
)
from contextox.store import (
    InvalidWorkspaceNameError,
    Path2StateError,
    SourceImportOutcomeUnknownError,
    SourceNotFoundError,
    WorkspaceCreateOutcomeUnknownError,
    WorkspaceNotFoundError,
    WorkspaceSchemaUnsupportedError,
    WorkspaceStore,
    WorkspaceStoreBusyError,
    WorkspaceStoreError,
    WorkspaceStoreUnavailableError,
)
from contextox.sources import SourceInputError
from contextox.runtime import Path2Runtime


DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"
PATH2_BODY_LIMIT_BYTES = 12 * 1024 * 1024


class _RequestBodyTooLarge(Exception):
    pass


async def _send_request_body_error(send: Any) -> None:
    envelope = WorkspaceError(
        code="invalid_request",
        message="Request body exceeds the 12 MiB limit.",
        request_id=f"req_{uuid4().hex}",
    )
    body = envelope.model_dump_json().encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 422,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


class _Path2BodyLimitMiddleware:
    """Bound new-path bodies while preserving the existing N2a routes."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not _is_path2_http_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                int(value)
                for key, value in scope.get("headers", [])
                if key.lower() == b"content-length" and value.isdigit()
            ),
            None,
        )
        if content_length is not None and content_length > PATH2_BODY_LIMIT_BYTES:
            await _send_request_body_error(send)
            return

        total = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > PATH2_BODY_LIMIT_BYTES:
                    scope["_contextox_body_too_large"] = True
                    raise _RequestBodyTooLarge()
            return message

        response_started = False

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _RequestBodyTooLarge:
            if not response_started:
                await _send_request_body_error(send)


def _is_path2_http_path(path: object) -> bool:
    if not isinstance(path, str):
        return False
    parts = path.split("/")
    if len(parts) < 5 or parts[1:3] != ["api", "workspaces"]:
        return False
    suffix = parts[4:]
    if suffix in (["sources"], ["mission-draft-attempts"]):
        return True
    if len(suffix) == 2 and suffix[0] in {"sources", "mission-draft-attempts"}:
        return True
    if len(suffix) == 3:
        if suffix[0] == "sources":
            return suffix[2] == "read"
        if suffix[0] == "mission-draft-attempts":
            return suffix[2] == "confirm"
        if suffix[0] == "missions" and suffix[2] == "runs":
            return True
        return False
    if suffix == ["missions"] or (len(suffix) == 2 and suffix[0] == "missions"):
        return True
    if len(suffix) == 4 and suffix[0] == "missions" and suffix[2] == "runs":
        return True
    if len(suffix) == 5 and suffix[0] == "missions" and suffix[2] == "runs":
        return suffix[4] in {"cancel", "events"}
    return False


def _readiness_checks(app: FastAPI) -> list[HealthCheck]:
    store_error = getattr(app.state, "workspace_store_error", None)
    if getattr(app.state, "workspace_store", None) is not None:
        workspace_store_check = HealthCheck(
            key="workspace_store",
            status="ready",
            detail="SQLite Workspace persistence is available.",
        )
    elif isinstance(store_error, WorkspaceSchemaUnsupportedError):
        workspace_store_check = HealthCheck(
            key="workspace_store",
            status="blocked",
            detail="Workspace persistence is blocked by an unsupported schema.",
        )
    elif isinstance(store_error, WorkspaceStoreBusyError):
        workspace_store_check = HealthCheck(
            key="workspace_store",
            status="blocked",
            detail="Workspace persistence is temporarily busy.",
        )
    else:
        workspace_store_check = HealthCheck(
            key="workspace_store",
            status="blocked" if store_error is not None else "not_run",
            detail=(
                "Workspace persistence is unavailable."
                if store_error is not None
                else "Workspace persistence is not configured for this app instance."
            ),
        )
    return [
        HealthCheck(
            key="api",
            status="ready",
            detail="FastAPI is serving the local contract.",
        ),
        workspace_store_check,
        HealthCheck(
            key="source_admission",
            status="ready" if getattr(app.state, "workspace_store", None) is not None else "not_run",
            detail=(
                "Workspace-scoped local Source import and read APIs are available."
                if getattr(app.state, "workspace_store", None) is not None
                else "Source APIs require an available Workspace store."
            ),
        ),
        HealthCheck(
            key="provider",
            status="not_run",
            detail=(
                "Provider readiness is checked only by an explicitly confirmed "
                "Mission draft attempt or Run."
            ),
        ),
    ]


def _readiness(app: FastAPI) -> ReadinessResponse:
    checks = _readiness_checks(app)
    store_check = next(check for check in checks if check.key == "workspace_store")
    return ReadinessResponse(
        status="blocked" if store_check.status == "blocked" else "partial",
        label=(
            "Path 2 persistence and bounded Mission runtime are available; Provider evidence is not run."
            if store_check.status == "ready"
            else "Path 2 Workspace persistence is unavailable; Source, Mission, and Provider are blocked."
        ),
        checks=checks,
    )


def _event() -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt_{uuid4().hex}",
        event_type="connected",
        occurred_at=datetime.now(timezone.utc),
        sequence=1,
        public_payload={
            "message": "SSE connected to the local Workbench shell.",
            "reconnect": "Read the current snapshot; deltas are not persisted in N1.",
        },
    )


def _format_sse(event: EventEnvelope | RunEventEnvelope) -> str:
    return (
        f"id: {event.event_id}\n"
        f"event: {event.event_type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


async def _event_stream() -> AsyncIterator[str]:
    yield _format_sse(_event())
    try:
        while True:
            await asyncio.sleep(15)
            yield ": contextox-sse-heartbeat\n\n"
    except asyncio.CancelledError:
        return


async def _run_event_stream(
    store: WorkspaceStore,
    runtime: Path2Runtime,
    workspace_id: str,
    mission_id: str,
    run_id: str,
    after_sequence: int,
) -> AsyncIterator[str]:
    sequence = after_sequence
    terminal_statuses = {
        "waiting_for_human", "partial", "blocked", "failed", "cancelled", "completed"
    }
    try:
        while True:
            persisted, buffered, snapshot = await asyncio.gather(
                asyncio.to_thread(
                    store.list_run_events,
                    workspace_id, mission_id, run_id, sequence,
                ),
                asyncio.to_thread(
                    runtime.buffered_events,
                    workspace_id, mission_id, run_id, sequence,
                ),
                asyncio.to_thread(
                    store.get_run_snapshot, workspace_id, mission_id, run_id,
                ),
            )
            by_sequence = {event.root.sequence: event for event in buffered}
            by_sequence.update({event.root.sequence: event for event in persisted})
            events = [by_sequence[key] for key in sorted(by_sequence)]
            if events and events[0].root.sequence > sequence + 1:
                yield (
                    f": contextox-snapshot-required missing={sequence + 1}-"
                    f"{events[0].root.sequence - 1}\n\n"
                )
            for event in events:
                yield _format_sse(event)
                sequence = event.root.sequence
            if not events and snapshot.last_sequence > sequence:
                late = await asyncio.to_thread(
                    runtime.buffered_events,
                    workspace_id, mission_id, run_id, sequence,
                )
                if late:
                    continue
                yield (
                    f": contextox-snapshot-required missing={sequence + 1}-"
                    f"{snapshot.last_sequence}\n\n"
                )
                sequence = snapshot.last_sequence
            if snapshot.status in terminal_statuses and sequence >= snapshot.last_sequence:
                return
            if not events:
                await asyncio.to_thread(
                    runtime.wait_for_change,
                    workspace_id, mission_id, run_id, sequence, 15.0,
                )
                yield ": contextox-sse-heartbeat\n\n"
    except asyncio.CancelledError:
        return


def _areas() -> list[WorkbenchArea]:
    return [
        WorkbenchArea(
            id="sources",
            label="Sources",
            description="授权资料、结构与证据的入口。",
            status="ready",
        ),
        WorkbenchArea(
            id="mission",
            label="Mission",
            description="任务阶段、工具收据与公开事件。",
            status="not_implemented",
        ),
        WorkbenchArea(
            id="clarifications",
            label="Clarifications",
            description="把未知变成可回答、可路由的问题。",
            status="not_implemented",
        ),
        WorkbenchArea(
            id="contract",
            label="Contract",
            description="有来源、版本与审批边界的业务定义。",
            status="not_implemented",
        ),
    ]


def _evidence() -> list[EvidenceLane]:
    return [
        EvidenceLane(
            key="static",
            label="Static contract",
            status="pass",
            detail="Pydantic is the API source of truth.",
        ),
        EvidenceLane(
            key="automated",
            label="Automated checks",
            status="not_run",
            detail="Run the repository's locked verification commands.",
        ),
        EvidenceLane(
            key="runtime",
            label="Runtime readback",
            status="not_run",
            detail="A local smoke run is not implied by this static snapshot.",
        ),
        EvidenceLane(
            key="real_model",
            label="Real model",
            status="not_run",
            detail="No real Provider run is part of this runtime snapshot.",
        ),
        EvidenceLane(
            key="human_acceptance",
            label="Human acceptance",
            status="pending",
            detail="A human must inspect the exact build and record PASS.",
        ),
        EvidenceLane(
            key="user_value",
            label="User value",
            status="not_verified",
            detail="No FDE pilot or comparison evidence exists yet.",
        ),
    ]


def _workspace_store(app: FastAPI) -> WorkspaceStore:
    store = getattr(app.state, "workspace_store", None)
    if store is not None:
        return store
    error = getattr(app.state, "workspace_store_error", None)
    if isinstance(error, WorkspaceStoreError):
        raise error
    raise WorkspaceStoreUnavailableError()


def _path2_runtime(app: FastAPI) -> Path2Runtime:
    runtime = getattr(app.state, "path2_runtime", None)
    if isinstance(runtime, Path2Runtime):
        return runtime
    raise WorkspaceStoreUnavailableError()


def _workspace_error(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    del request
    request_id = f"req_{uuid4().hex}"
    envelope = WorkspaceError(code=code, message=message, request_id=request_id)
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def _workspace_store_error_response(
    request: Request,
    error: BaseException,
) -> JSONResponse:
    if isinstance(error, WorkspaceNotFoundError):
        return _workspace_error(
            request,
            status_code=404,
            code="workspace_not_found",
            message="Workspace was not found.",
        )
    if isinstance(error, SourceNotFoundError):
        return _workspace_error(
            request,
            status_code=404,
            code="source_not_found",
            message="Source revision was not found.",
        )
    if isinstance(error, Path2StateError):
        not_found = error.code in {
            "mission_draft_attempt_not_found", "mission_not_found", "run_not_found"
        }
        return _workspace_error(
            request,
            status_code=404 if not_found else 422,
            code=error.code,
            message=(
                "The requested Path 2 object was not found."
                if not_found else "The requested Path 2 operation could not be applied."
            ),
        )
    if isinstance(error, InvalidWorkspaceNameError):
        return _workspace_error(
            request,
            status_code=422,
            code="invalid_workspace_name",
            message=(
                "Workspace name must be 1–80 Unicode characters after trimming "
                "and must not contain control characters."
            ),
        )
    if isinstance(error, WorkspaceCreateOutcomeUnknownError):
        return _workspace_error(
            request,
            status_code=503,
            code="workspace_create_outcome_unknown",
            message=(
                "Workspace creation outcome is unknown; reconcile the workspace list "
                "before retrying."
            ),
        )
    if isinstance(error, SourceImportOutcomeUnknownError):
        return _workspace_error(
            request,
            status_code=503,
            code="source_import_outcome_unknown",
            message=(
                "Source import outcome is unknown; reconcile the Source list before retrying."
            ),
        )
    if isinstance(error, WorkspaceStoreBusyError):
        return _workspace_error(
            request,
            status_code=503,
            code="workspace_store_busy",
            message="Workspace store is busy; try again after the current local operation finishes.",
        )
    if isinstance(error, WorkspaceSchemaUnsupportedError):
        return _workspace_error(
            request,
            status_code=503,
            code="workspace_schema_unsupported",
            message="Workspace store schema is unsupported.",
        )
    return _workspace_error(
        request,
        status_code=503,
        code="workspace_store_unavailable",
        message="Workspace store is unavailable.",
    )


def _is_canonical_uuid4(value: str) -> bool:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        return False
    return parsed.version == 4 and parsed.variant == RFC_4122 and str(parsed) == value


def _invalid_workspace_path(request: Request, workspace_id: str) -> JSONResponse | None:
    if _is_canonical_uuid4(workspace_id):
        return None
    return _workspace_error(
        request,
        status_code=404,
        code="workspace_not_found",
        message="Workspace was not found.",
    )


def _invalid_object_path(request: Request, object_id: str) -> JSONResponse | None:
    if _is_canonical_uuid4(object_id):
        return None
    return _workspace_error(
        request,
        status_code=422,
        code="invalid_request",
        message="Invalid request.",
    )


def _source_error(code: str, message: str) -> WorkspaceError:
    return WorkspaceError(
        code=code,
        message=message,
        request_id=f"req_{uuid4().hex}",
    )


def _source_input_error_response(request: Request, error: SourceInputError) -> JSONResponse:
    del error
    return _workspace_error(
        request,
        status_code=422,
        code="invalid_request",
        message="Invalid Source request.",
    )


_SHARED_OPENAPI_MODELS: tuple[type[BaseModel], ...] = (
    SourceIdentity,
    SourceRevision,
    CsvRowsLocator,
    JsonPointerLocator,
    TextLinesLocator,
    SourceIssue,
    SourceExcerpt,
    SourceArtifact,
    TableKey,
    TableProfile,
    RelationshipProfile,
    MissionDraftPayload,
    MissionDraftAttempt,
    Mission,
    RunSnapshot,
    MissionSnapshot,
    DefinitionField,
    RelationshipCandidate,
    DefinitionDraft,
    ClarificationQuestion,
    ClarificationRequest,
    ProviderConfigSnapshot,
    ProviderReceipt,
    ToolReceipt,
    TerminalReceipt,
    ContextManifestInput,
    ContextPacketManifest,
    ContextSnapshot,
    DomainRejection,
    ListSourcesArguments,
    ReadSourceArguments,
    InspectTableArguments,
    InspectRelationshipArguments,
    UpdateDefinitionDraftArguments,
    CreateClarificationArguments,
    SubmitForReviewArguments,
    FinishRunArguments,
    ListSourcesCall,
    ReadSourceCall,
    InspectDatasetCall,
    UpdateDefinitionDraftCall,
    CreateClarificationCall,
    SubmitForReviewCall,
    FinishRunCall,
    RunToolResult,
    RunStartedPayload,
    MessageCreatedPayload,
    ModelStartedPayload,
    ModelDeltaPayload,
    ModelCompletedPayload,
    ToolRequestedPayload,
    ToolCompletedPayload,
    ToolFailedPayload,
    DraftUpdatedPayload,
    RunTerminalPayload,
    RunEventInput,
    RunEventEnvelope,
    SourceUploadFile,
    SourceUploadRequest,
    SourceImportItem,
    SourceBatchResult,
    SourceExcerptRequest,
    MissionDraftAttemptCreateRequest,
    MissionDraftConfirmRequest,
    RunStartRequest,
    CancelRunRequest,
    AgentRunResult,
)

_SHARED_OPENAPI_ALIASES: tuple[tuple[str, Any], ...] = (
    ("EvidenceLocator", EvidenceLocator),
    ("InspectDatasetArguments", InspectDatasetArguments),
    ("DomainToolCall", DomainToolCall),
)


def _rewrite_schema_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_schema_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_schema_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return "#/components/schemas/" + value.removeprefix("#/$defs/")
    return value


def _register_schema(schema: dict[str, Any], name: str, annotation: Any) -> None:
    raw = (
        annotation.model_json_schema(ref_template="#/$defs/{model}")
        if isinstance(annotation, type) and issubclass(annotation, BaseModel)
        else TypeAdapter(annotation).json_schema(ref_template="#/$defs/{model}")
    )
    definitions = raw.pop("$defs", {})
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for definition_name, definition in definitions.items():
        components.setdefault(definition_name, _rewrite_schema_refs(definition))
    components[name] = _rewrite_schema_refs(raw)


def _install_shared_openapi(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = get_openapi(
                title=app.title,
                version=app.version,
                summary=app.summary,
                description=app.description,
                routes=app.routes,
            )
            for model in _SHARED_OPENAPI_MODELS:
                _register_schema(app.openapi_schema, model.__name__, model)
            for name, annotation in _SHARED_OPENAPI_ALIASES:
                _register_schema(app.openapi_schema, name, annotation)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def create_app(
    *,
    static_dir: Path | None = None,
    data_dir: Path | None = None,
) -> FastAPI:
    resolved_static_dir = (static_dir or DEFAULT_STATIC_DIR).resolve()

    @asynccontextmanager
    async def lifespan(current_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runtime = getattr(current_app.state, "path2_runtime", None)
            if isinstance(runtime, Path2Runtime):
                await asyncio.to_thread(runtime.shutdown)

    app = FastAPI(
        title="ContextOx Workbench API",
        version=__version__,
        summary="Local-first business-definition Workbench shell",
        description=(
            "Path 2 exposes local governed Workspace, Source, Mission, and Run behavior. "
            "Provider work starts only after the matching explicit send confirmation."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(_Path2BodyLimitMiddleware)
    app.state.static_dir = resolved_static_dir
    app.state.data_dir = data_dir.resolve() if data_dir else None
    app.state.workspace_store = None
    app.state.workspace_store_error = None
    app.state.path2_runtime = None
    if app.state.data_dir is not None:
        try:
            app.state.workspace_store = WorkspaceStore.open(app.state.data_dir)
        except WorkspaceStoreError as error:
            app.state.workspace_store_error = error
        except (OSError, sqlite3.Error):
            app.state.workspace_store_error = WorkspaceStoreUnavailableError()
    if app.state.workspace_store is not None:
        app.state.path2_runtime = Path2Runtime(app.state.workspace_store)

    @app.exception_handler(RequestValidationError)
    async def workspace_request_validation(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        path_parts = request.url.path.split("/")
        if len(path_parts) > 3 and path_parts[1:3] == ["api", "workspaces"]:
            path_workspace_id = path_parts[3]
            if not _is_canonical_uuid4(path_workspace_id):
                return _workspace_error(
                    request,
                    status_code=404,
                    code="workspace_not_found",
                    message="Workspace was not found.",
                )
        if request.url.path == "/api/workspaces" and request.method == "POST":
            return _workspace_error(
                request,
                status_code=422,
                code="invalid_workspace_name",
                message=(
                    "Workspace name must be 1–80 Unicode characters after trimming "
                    "and must not contain control characters."
                ),
            )
        return _workspace_error(
            request,
            status_code=422,
            code="invalid_request",
            message="Invalid request.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def path2_http_exception(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        if request.scope.get("_contextox_body_too_large"):
            return _workspace_error(
                request,
                status_code=422,
                code="invalid_request",
                message="Request body exceeds the 12 MiB limit.",
            )
        if error.status_code == 400 and _is_path2_http_path(request.url.path):
            return _workspace_error(
                request,
                status_code=422,
                code="invalid_request",
                message="Invalid request.",
            )
        return await http_exception_handler(request, error)

    @app.get("/api/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ready",
            product_status="partial",
            service="contextox-workbench",
            version=__version__,
            bind_address="127.0.0.1",
            checks=[
                HealthCheck(
                    key="process",
                    status="ready",
                    detail="The local API process is responding.",
                ),
                HealthCheck(
                    key="workbench_assets",
                    status=(
                        "ready"
                        if (resolved_static_dir / "index.html").is_file()
                        else "not_run"
                    ),
                    detail=(
                        "Built Workbench assets are available."
                        if (resolved_static_dir / "index.html").is_file()
                        else "Build web/ before expecting browser assets."
                    ),
                ),
            ],
        )

    @app.get("/api/readiness", response_model=ReadinessResponse, tags=["system"])
    def readiness() -> ReadinessResponse:
        return _readiness(app)

    @app.get(
        "/api/workbench",
        response_model=WorkbenchSnapshot,
        tags=["workbench"],
    )
    def workbench() -> WorkbenchSnapshot:
        return WorkbenchSnapshot(
            product="ContextOx Workbench",
            architecture="local-python-react",
            status="partial",
            notice=(
                "Path 2 admits explicitly authorized local Sources. Mission, model provider, "
                "arbitrary SQL/shell, and approval actions remain unavailable."
            ),
            readiness=_readiness(app),
            areas=_areas(),
            evidence=_evidence(),
        )

    @app.post(
        "/api/workspaces",
        response_model=Workspace,
        status_code=201,
        responses={422: {"model": WorkspaceError}, 503: {"model": WorkspaceError}},
        tags=["workspaces"],
    )
    def create_workspace(
        payload: WorkspaceCreateRequest,
        request: Request,
    ) -> Workspace | JSONResponse:
        try:
            return _workspace_store(app).create_workspace(payload.display_name)
        except (WorkspaceStoreError, InvalidWorkspaceNameError) as error:
            return _workspace_store_error_response(request, error)

    @app.get(
        "/api/workspaces",
        response_model=list[Workspace],
        responses={503: {"model": WorkspaceError}},
        tags=["workspaces"],
    )
    def list_workspaces(request: Request) -> list[Workspace] | JSONResponse:
        try:
            return _workspace_store(app).list_workspaces()
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.get(
        "/api/workspaces/{workspace_id}",
        response_model=Workspace,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["workspaces"],
    )
    def get_workspace(
        workspace_id: str,
        request: Request,
    ) -> Workspace | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        try:
            workspace = _workspace_store(app).get_workspace(workspace_id)
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)
        if workspace is None:
            return _workspace_error(
                request,
                status_code=404,
                code="workspace_not_found",
                message="Workspace was not found.",
            )
        return workspace

    @app.post(
        "/api/workspaces/{workspace_id}/sources",
        response_model=SourceBatchResult,
        status_code=200,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["sources"],
    )
    def upload_sources(
        workspace_id: str,
        payload: SourceUploadRequest,
        request: Request,
    ) -> SourceBatchResult | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        try:
            store = _workspace_store(app)
            if store.get_workspace(workspace_id) is None:
                raise WorkspaceNotFoundError()
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

        items: list[SourceImportItem] = []
        for file_index, source_file in enumerate(payload.files):
            try:
                content = base64.b64decode(source_file.content_base64, validate=True)
                revision, artifact = store.import_source_revision(
                    workspace_id,
                    source_file.original_name,
                    source_file.media_type,
                    content,
                )
            except SourceInputError:
                items.append(
                    SourceImportItem(
                        file_index=file_index,
                        original_name=source_file.original_name,
                        status="failed",
                        revision=None,
                        error=_source_error("invalid_source", "Source input is invalid."),
                    )
                )
                continue
            except WorkspaceNotFoundError as error:
                return _workspace_store_error_response(request, error)
            except WorkspaceStoreError as error:
                items.append(
                    SourceImportItem(
                        file_index=file_index,
                        original_name=source_file.original_name,
                        status="failed",
                        revision=None,
                        error=_source_error(error.code, error.detail),
                    )
                )
                continue

            if artifact.parse_status == "ready":
                status = "accepted"
                item_error = None
            else:
                status = artifact.parse_status
                issue = artifact.issues[0] if artifact.issues else None
                item_error = _source_error(
                    issue.code if issue is not None else "source_parse_failed",
                    issue.message if issue is not None else "Source parsing did not complete.",
                )
            items.append(
                SourceImportItem(
                    file_index=file_index,
                    original_name=source_file.original_name,
                    status=status,
                    revision=revision,
                    error=item_error,
                )
            )
        return SourceBatchResult(items=items)

    @app.get(
        "/api/workspaces/{workspace_id}/sources",
        response_model=list[SourceRevision],
        responses={
            404: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["sources"],
    )
    def fetch_sources(
        workspace_id: str,
        request: Request,
    ) -> list[SourceRevision] | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        try:
            return _workspace_store(app).list_source_revisions(workspace_id)
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.get(
        "/api/workspaces/{workspace_id}/sources/{revision_id}",
        response_model=SourceArtifact,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["sources"],
    )
    def fetch_source_artifact(
        workspace_id: str,
        revision_id: str,
        request: Request,
    ) -> SourceArtifact | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        invalid = _invalid_object_path(request, revision_id)
        if invalid is not None:
            return invalid
        try:
            return _workspace_store(app).get_source_artifact(workspace_id, revision_id)
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.post(
        "/api/workspaces/{workspace_id}/sources/{revision_id}/read",
        response_model=SourceExcerpt,
        status_code=200,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["sources"],
    )
    def read_source_excerpt(
        workspace_id: str,
        revision_id: str,
        payload: SourceExcerptRequest,
        request: Request,
    ) -> SourceExcerpt | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        invalid = _invalid_object_path(request, revision_id)
        if invalid is not None:
            return invalid
        try:
            return _workspace_store(app).read_source_excerpt(
                workspace_id,
                revision_id,
                payload.locator,
            )
        except SourceInputError as error:
            return _source_input_error_response(request, error)
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.post(
        "/api/workspaces/{workspace_id}/mission-draft-attempts",
        response_model=MissionDraftAttempt,
        status_code=202,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["missions"],
    )
    def create_mission_draft_attempt(
        workspace_id: str,
        payload: MissionDraftAttemptCreateRequest,
        request: Request,
    ) -> MissionDraftAttempt | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        try:
            return _path2_runtime(app).start_mission_draft(
                workspace_id, payload.original_input
            )
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.get(
        "/api/workspaces/{workspace_id}/mission-draft-attempts/{attempt_id}",
        response_model=MissionDraftAttempt,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["missions"],
    )
    def fetch_mission_draft_attempt(
        workspace_id: str,
        attempt_id: str,
        request: Request,
    ) -> MissionDraftAttempt | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        invalid = _invalid_object_path(request, attempt_id)
        if invalid is not None:
            return invalid
        try:
            return _workspace_store(app).get_mission_draft_attempt(
                workspace_id, attempt_id
            )
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.post(
        "/api/workspaces/{workspace_id}/mission-draft-attempts/{attempt_id}/confirm",
        response_model=Mission,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["missions"],
    )
    def confirm_mission_draft_attempt(
        workspace_id: str,
        attempt_id: str,
        payload: MissionDraftConfirmRequest,
        request: Request,
    ) -> Mission | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        invalid = _invalid_object_path(request, attempt_id)
        if invalid is not None:
            return invalid
        try:
            return _workspace_store(app).confirm_mission_draft_attempt(
                workspace_id, attempt_id, payload.candidate_version,
                payload.candidate_sha256, payload.source_refs,
            )
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.get(
        "/api/workspaces/{workspace_id}/missions",
        response_model=list[Mission],
        responses={
            404: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["missions"],
    )
    def fetch_missions(
        workspace_id: str,
        request: Request,
    ) -> list[Mission] | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        try:
            return _workspace_store(app).list_missions(workspace_id)
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.get(
        "/api/workspaces/{workspace_id}/missions/{mission_id}",
        response_model=MissionSnapshot,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["missions"],
    )
    def fetch_mission_snapshot(
        workspace_id: str,
        mission_id: str,
        request: Request,
    ) -> MissionSnapshot | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        invalid = _invalid_object_path(request, mission_id)
        if invalid is not None:
            return invalid
        try:
            return _workspace_store(app).get_mission_snapshot(
                workspace_id, mission_id
            )
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.post(
        "/api/workspaces/{workspace_id}/missions/{mission_id}/runs",
        response_model=RunSnapshot,
        status_code=202,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["runs"],
    )
    def start_run(
        workspace_id: str,
        mission_id: str,
        payload: RunStartRequest,
        request: Request,
    ) -> RunSnapshot | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        invalid = _invalid_object_path(request, mission_id)
        if invalid is not None:
            return invalid
        try:
            return _path2_runtime(app).start_run(workspace_id, mission_id, payload)
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.get(
        "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}",
        response_model=RunSnapshot,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["runs"],
    )
    def fetch_run_snapshot(
        workspace_id: str,
        mission_id: str,
        run_id: str,
        request: Request,
    ) -> RunSnapshot | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        for object_id in (mission_id, run_id):
            invalid = _invalid_object_path(request, object_id)
            if invalid is not None:
                return invalid
        try:
            return _workspace_store(app).get_run_snapshot(
                workspace_id, mission_id, run_id
            )
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.post(
        "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}/cancel",
        response_model=RunSnapshot,
        responses={
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["runs"],
    )
    def cancel_run(
        workspace_id: str,
        mission_id: str,
        run_id: str,
        _payload: CancelRunRequest,
        request: Request,
    ) -> RunSnapshot | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        for object_id in (mission_id, run_id):
            invalid = _invalid_object_path(request, object_id)
            if invalid is not None:
                return invalid
        try:
            return _path2_runtime(app).cancel_run(workspace_id, mission_id, run_id)
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)

    @app.get(
        "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}/events",
        response_model=RunEventEnvelope,
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "A public Run SSE stream.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            },
            404: {"model": WorkspaceError},
            422: {"model": WorkspaceError},
            503: {"model": WorkspaceError},
        },
        tags=["runs"],
    )
    def run_events(
        workspace_id: str,
        mission_id: str,
        run_id: str,
        request: Request,
        _last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse | JSONResponse:
        invalid = _invalid_workspace_path(request, workspace_id)
        if invalid is not None:
            return invalid
        for object_id in (mission_id, run_id):
            invalid = _invalid_object_path(request, object_id)
            if invalid is not None:
                return invalid
        try:
            store = _workspace_store(app)
            runtime = _path2_runtime(app)
            snapshot = store.get_run_snapshot(workspace_id, mission_id, run_id)
            if _last_event_id is None:
                after_sequence = 0
            elif (
                not _last_event_id.isdigit()
                or str(int(_last_event_id)) != _last_event_id
            ):
                raise Path2StateError("run_event_cursor_invalid")
            else:
                after_sequence = int(_last_event_id)
            if after_sequence > snapshot.last_sequence:
                raise Path2StateError("run_event_cursor_invalid")
        except WorkspaceStoreError as error:
            return _workspace_store_error_response(request, error)
        return StreamingResponse(
            _run_event_stream(
                store, runtime, workspace_id, mission_id, run_id, after_sequence
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        "/api/events",
        response_model=EventEnvelope,
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "A connected SSE stream with public event envelopes.",
                "content": {
                    "text/event-stream": {"schema": {"type": "string"}},
                },
            }
        },
        tags=["events"],
    )
    def events() -> StreamingResponse:
        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if (resolved_static_dir / "index.html").is_file():
        app.mount(
            "/",
            StaticFiles(directory=resolved_static_dir, html=True),
            name="workbench-assets",
        )
    else:

        @app.get("/", include_in_schema=False)
        def missing_assets() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_run",
                    "detail": "Workbench assets are not built. Run npm run build in web/.",
                },
            )

    _install_shared_openapi(app)
    return app
