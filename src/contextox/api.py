import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from contextox import __version__
from contextox.models import (
    EventEnvelope,
    EvidenceLane,
    HealthCheck,
    HealthResponse,
    ReadinessResponse,
    WorkbenchArea,
    WorkbenchSnapshot,
)


DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


def _readiness_checks() -> list[HealthCheck]:
    return [
        HealthCheck(
            key="api",
            status="ready",
            detail="FastAPI is serving the local contract.",
        ),
        HealthCheck(
            key="workspace_store",
            status="not_implemented",
            detail="Workspace persistence is reserved for the source-admission checkpoint.",
        ),
        HealthCheck(
            key="source_admission",
            status="not_implemented",
            detail="No files are admitted or read by the N1 shell.",
        ),
        HealthCheck(
            key="provider",
            status="not_implemented",
            detail="No model provider is configured or called in N1.",
        ),
    ]


def _readiness() -> ReadinessResponse:
    return ReadinessResponse(
        status="partial",
        label="N1 shell ready; product capabilities remain intentionally partial.",
        checks=_readiness_checks(),
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
            "N1 exposes only a local readiness shell. It does not ingest files, "
            "call a model provider, or persist customer material."
        ),
    )
    app.state.static_dir = resolved_static_dir
    app.state.data_dir = data_dir.resolve() if data_dir else None

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
        return _readiness()

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
                "N1 is a truthful local shell: no customer files, model provider, "
                "arbitrary SQL/shell, or approval action is available yet."
            ),
            readiness=_readiness(),
            areas=_areas(),
            evidence=_evidence(),
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

    return app
