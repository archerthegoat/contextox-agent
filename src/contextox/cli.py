import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Sequence

from contextox import __version__
from contextox.api import create_app
from contextox.models import DoctorCheck, DoctorReport
from contextox.store import WorkspaceStore


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
                "The managed Python runtime matches the N1 pin."
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
                "OpenAPI includes the N2a public seams."
                if schema_ready
                else "The generated API schema is missing an N2a public seam."
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
                detail="Pass --data-dir to inspect the N2a Workspace store.",
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
                status="not_implemented",
                detail="No model provider is configured or called in N2a.",
            ),
            DoctorCheck(
                key="customer_data",
                status="not_implemented",
                detail="N2a accepts no customer files or private payloads.",
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

    doctor = commands.add_parser("doctor", help="Inspect N2a local readiness.")
    doctor.add_argument("--json", action="store_true", help="Print JSON output.")
    doctor.add_argument(
        "--static-dir",
        type=Path,
        default=Path.cwd() / "web" / "dist",
        help=argparse.SUPPRESS,
    )
    doctor.add_argument(
        "--data-dir",
        type=Path,
        help="Inspect an existing Workspace data directory without creating it.",
    )

    start = commands.add_parser("start", help="Start the local Workbench server.")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8787)
    start.add_argument("--data-dir", type=Path, default=Path.cwd() / ".contextox-agent")
    start.add_argument("--static-dir", type=Path, default=Path.cwd() / "web" / "dist")

    schema = commands.add_parser("openapi", help="Write the generated OpenAPI schema.")
    schema.add_argument("--output", type=Path)
    schema.add_argument("--static-dir", type=Path, default=Path.cwd() / "web" / "dist")
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
        data_dir = args.data_dir.resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        if not data_dir.is_dir():
            parser.error("data-dir must resolve to a directory")
        import uvicorn

        uvicorn.run(
            create_app(static_dir=args.static_dir.resolve(), data_dir=data_dir),
            host="127.0.0.1",
            port=args.port,
            log_level="info",
            access_log=False,
        )
        return 0
    parser.print_help()
    return 0
