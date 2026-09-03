import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import RFC_4122, UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from contextox import __version__
from contextox.models import (
    EventEnvelope,
    EvidenceLane,
    HealthCheck,
    HealthResponse,
    ReadinessResponse,
    Workspace,
    WorkspaceCreateRequest,
    WorkspaceError,
    WorkbenchArea,
    WorkbenchSnapshot,
)
from contextox.store import (
    InvalidWorkspaceNameError,
    WorkspaceCreateOutcomeUnknownError,
    WorkspaceSchemaUnsupportedError,
    WorkspaceStore,
    WorkspaceStoreBusyError,
    WorkspaceStoreError,
    WorkspaceStoreUnavailableError,
)


DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


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
            status="not_implemented",
            detail="N2a does not admit or read files.",
        ),
        HealthCheck(
            key="provider",
            status="not_implemented",
            detail="N2a does not configure or call a model provider.",
        ),
    ]


def _readiness(app: FastAPI) -> ReadinessResponse:
    checks = _readiness_checks(app)
    store_check = next(check for check in checks if check.key == "workspace_store")
    return ReadinessResponse(
        status="blocked" if store_check.status == "blocked" else "partial",
        label=(
            "N2a Workspace foundation ready; source, Mission, and Provider capabilities remain partial."
            if store_check.status == "ready"
            else "N2a Workspace foundation is unavailable; source, Mission, and Provider capabilities remain partial."
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


def _workspace_store(app: FastAPI) -> WorkspaceStore:
    store = getattr(app.state, "workspace_store", None)
    if store is not None:
        return store
    error = getattr(app.state, "workspace_store_error", None)
    if isinstance(error, WorkspaceStoreError):
        raise error
    raise WorkspaceStoreUnavailableError()


def _workspace_error(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    request_id = f"req_{uuid4().hex}"
    envelope = WorkspaceError(code=code, message=message, request_id=request_id)
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def _workspace_store_error_response(
    request: Request,
    error: BaseException,
) -> JSONResponse:
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


def _format_sse(event: EventEnvelope) -> str:
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


def _areas() -> list[WorkbenchArea]:
    return [
        WorkbenchArea(
            id="sources",
            label="Sources",
            description="授权资料、结构与证据的入口。",
            status="not_implemented",
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
            detail="N1 does not call a provider.",
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


def create_app(
    *,
    static_dir: Path | None = None,
    data_dir: Path | None = None,
) -> FastAPI:
    resolved_static_dir = (static_dir or DEFAULT_STATIC_DIR).resolve()
    app = FastAPI(
        title="ContextOx Workbench API",
        version=__version__,
        summary="Local-first business-definition Workbench shell",
        description=(
            "N2a exposes a local Workspace foundation. It does not ingest files, "
            "call a model provider, or persist customer material."
        ),
    )
    app.state.static_dir = resolved_static_dir
    app.state.data_dir = data_dir.resolve() if data_dir else None
    app.state.workspace_store = None
    app.state.workspace_store_error = None
    if app.state.data_dir is not None:
        try:
            app.state.workspace_store = WorkspaceStore.open(app.state.data_dir)
        except WorkspaceStoreError as error:
            app.state.workspace_store_error = error
        except (OSError, sqlite3.Error) as error:
            app.state.workspace_store_error = WorkspaceStoreUnavailableError()

    @app.exception_handler(RequestValidationError)
    async def workspace_request_validation(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
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
            code="invalid_workspace_name",
            message="Invalid Workspace request.",
        )

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
                "N2a is a truthful local Workspace foundation: no customer files, "
                "model provider, arbitrary SQL/shell, or approval action is available."
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
        if not _is_canonical_uuid4(workspace_id):
            return _workspace_error(
                request,
                status_code=404,
                code="workspace_not_found",
                message="Workspace was not found.",
            )
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

    return app
