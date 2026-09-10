import argparse
import importlib.metadata
import json
import platform
import socket
import sys
import webbrowser
from pathlib import Path
from typing import Sequence

from contextox import __version__
from contextox.api import create_app
from contextox.credentials import EnvFileError, load_env_file
from contextox.models import DoctorCheck, DoctorReport
from contextox.store import WorkspaceStore
from contextox.local_install import InstanceAlreadyRunning, data_directory, own_instance, static_directory


EXPECTED_PYTHON = "3.14.7"
EXPECTED_PACKAGES = {
    "fastapi": "0.141.1",
    "pydantic": "2.13.5",
    "uvicorn": "0.52.4",
}


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _doctor(static_dir: Path, data_dir: Path | None = None) -> DoctorReport:
    checks: list[DoctorCheck] = []
    python_actual = platform.python_version()
    checks.append(
        DoctorCheck(
            key="python",
            status="ready" if python_actual == EXPECTED_PYTHON else "blocked",
            detail=(
                "The managed Python runtime matches the approved pin."
                if python_actual == EXPECTED_PYTHON
                else "Use UV with the repository's .python-version before starting."
            ),
            actual=python_actual,
            expected=EXPECTED_PYTHON,
        )
    )
    for name, expected in EXPECTED_PACKAGES.items():
        actual = _package_version(name)
        checks.append(
            DoctorCheck(
                key=name,
                status="ready" if actual == expected else "blocked",
                detail=(
                    "The locked runtime dependency is available."
                    if actual == expected
                    else "Run `uv sync --locked` with the approved environment."
                ),
                actual=actual,
                expected=expected,
            )
        )
    app = create_app(static_dir=static_dir)
    paths = app.openapi().get("paths", {})
    required_paths = {
        "/api/health",
        "/api/readiness",
        "/api/workbench",
        "/api/events",
        "/api/workspaces",
        "/api/workspaces/{workspace_id}",
    }
    schema_ready = required_paths.issubset(paths)
    checks.append(
        DoctorCheck(
            key="schema",
            status="ready" if schema_ready else "blocked",
            detail=(
                "OpenAPI includes the local Workspace public seams."
                if schema_ready
                else "The generated API schema is missing a required public seam."
            ),
            actual=str(len(paths)),
            expected=str(len(required_paths)),
        )
    )
    assets_ready = (static_dir / "index.html").is_file()
    checks.append(
        DoctorCheck(
            key="workbench_assets",
            status="ready" if assets_ready else "not_run",
            detail=(
                "Built React assets are ready for local serving."
                if assets_ready
                else "Run `npm run build` in web/ before browser inspection."
            ),
            actual="present" if assets_ready else "absent",
            expected="present",
        )
    )
    if data_dir is None:
        store_checks = [
            DoctorCheck(
                key=key,
                status="not_run",
                detail="Pass --data-dir to inspect the Workspace store.",
            )
            for key in (
                "workspace_store_configured",
                "workspace_store_open",
                "workspace_store_schema",
                "workspace_store_readwrite",
            )
        ]
    else:
        store_checks = [
            DoctorCheck(
                key=diagnostic.key,
                status=diagnostic.status,
                detail=diagnostic.detail,
                actual=diagnostic.actual,
                expected=diagnostic.expected,
            )
            for diagnostic in WorkspaceStore.diagnose(data_dir)
        ]
    checks.extend(
        store_checks
        + [
            DoctorCheck(
                key="provider",
                status="not_run",
                detail="The Provider boundary is implemented. Doctor does not inspect credentials or make model calls; configuration and real execution remain unverified.",
            ),
            DoctorCheck(
                key="customer_data",
                status="not_run",
                detail="Authorized local source import and parsing are implemented. Doctor does not import or verify customer data; model sending requires a separate explicit scope.",
            ),
        ]
    )
    blocking = any(check.status == "blocked" for check in checks)
    return DoctorReport(
        status="blocked" if blocking else "partial",
        scope="n2a",
        checks=checks,
    )


def _print_doctor(report: DoctorReport, as_json: bool) -> None:
    if as_json:
        print(report.model_dump_json(indent=2))
        return
    print(f"status: {report.status}")
    print(f"scope: {report.scope}")
    for check in report.checks:
        print(f"{check.key}: {check.status}")
        print(f"  {check.detail}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextox",
        description="Local ContextOx Workbench commands.",
    )
    commands = parser.add_subparsers(dest="command")

    doctor = commands.add_parser("doctor", help="Inspect local runtime readiness.")
    doctor.add_argument("--json", action="store_true", help="Print JSON output.")
    doctor.add_argument(
        "--static-dir",
        type=Path,
        default=static_directory(),
        help=argparse.SUPPRESS,
    )
    doctor.add_argument(
        "--data-dir",
        type=Path,
        help="Inspect an existing Workspace data directory without creating it.",
    )

    start = commands.add_parser("start", help="Start the local Workbench server.")
    start.add_argument("--agent-profile", choices=("production", "demo-fast"), default="production",
                       help="Use production high (default) or explicitly select the non-thinking demo.")
    start.add_argument("--env-file", type=Path,
                       help="Read DEEPSEEK_API_KEY from this UTF-8 file; environment variables take priority.")
    start.add_argument("--migrate-profile-interpretations", action="store_true", help="Back up and migrate a stopped v5 store to profile interpretations v6.")
    start.add_argument("--migrate-clarification-answers", action="store_true", help="Back up and migrate a stopped store to clarification answers v5.")
    start.add_argument("--migrate-task-dialogue", action="store_true", help="Explicitly back up and migrate a stopped v3 store to task dialogue v4.")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8787)
    start.add_argument("--data-dir", type=Path, default=data_directory())
    start.add_argument("--static-dir", type=Path, default=static_directory())
    start.add_argument("--open-browser", action="store_true", help="Open the local Workbench after startup.")

    schema = commands.add_parser("openapi", help="Write the generated OpenAPI schema.")
    schema.add_argument("--output", type=Path)
    schema.add_argument("--static-dir", type=Path, default=static_directory())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        report = _doctor(
            args.static_dir.resolve(),
            args.data_dir.resolve() if args.data_dir else None,
        )
        _print_doctor(report, args.json)
        return 0 if report.status != "blocked" else 1
    if args.command == "openapi":
        document = create_app(static_dir=args.static_dir.resolve()).openapi()
        encoded = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
            args.output.resolve().write_text(encoded, encoding="utf-8")
        else:
            print(encoded, end="")
        return 0
    if args.command == "start":
        if args.host != "127.0.0.1":
            parser.error("ContextOx only binds to 127.0.0.1 in N1.")
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        try:
            load_env_file(args.env_file)
        except EnvFileError as error:
            parser.error(str(error))
        data_dir = args.data_dir.resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        if not data_dir.is_dir():
            parser.error("data-dir must resolve to a directory")
        try:
            with own_instance(data_dir, args.port):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        listener.bind(("127.0.0.1", args.port))
                    except OSError:
                        print(f"端口 {args.port} 已被占用。请使用 --port 指定其他端口。", file=sys.stderr)
                        return 3
                    listener.listen(128)
                    listener.setblocking(False)
                    return _serve(args, data_dir, listener)
        except InstanceAlreadyRunning as existing:
            if existing.port is not None:
                url = f"http://127.0.0.1:{existing.port}"
                print(f"数契已在运行：{url}")
                if args.open_browser:
                    webbrowser.open(url)
                return 0
            print("数契正在启动，请稍后重试。", file=sys.stderr)
            return 3
        except OSError:
            print("无法取得资料目录的本地运行锁。请检查目录权限。", file=sys.stderr)
            return 3
    parser.print_help()
    return 0


def _serve(args: argparse.Namespace, data_dir: Path, listener: socket.socket) -> int:
    import uvicorn

    app = create_app(
        static_dir=args.static_dir.resolve(), data_dir=data_dir,
        migrate_dialogue=args.migrate_task_dialogue,
        migrate_clarifications=args.migrate_clarification_answers,
        migrate_profiles=args.migrate_profile_interpretations,
        agent_profile=args.agent_profile,
    )

    class LocalServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                url = f"http://127.0.0.1:{args.port}"
                print(f"数契工作台：{url}\n按 Ctrl+C 停止服务。", flush=True)
                if args.open_browser:
                    webbrowser.open(url)

        async def shutdown(self, sockets=None):
            # End owned SSE responses before Uvicorn waits for connections.
            # Lifespan still owns Run cancellation and persisted receipts.
            app.state.stream_stop.set()
            app.state.global_stream_stop.set()
            runtime = getattr(app.state, "path2_runtime", None)
            if runtime is not None:
                runtime.wake_event_waiters()
            await super().shutdown(sockets=sockets)

    server = LocalServer(uvicorn.Config(
        app,
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        access_log=False,
    ))
    try:
        server.run(sockets=[listener])
    except KeyboardInterrupt:
        pass
    return 0 if server.started else 3
