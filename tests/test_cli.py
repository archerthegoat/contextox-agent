import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "contextox", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class CliTests(unittest.TestCase):
    def test_doctor_reports_n2a_scope_and_store_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-cli-no-assets-") as directory:
            result = run_cli(
                "doctor",
                "--json",
                "--static-dir",
                directory,
                "--data-dir",
                directory,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["scope"], "n2a")
        self.assertEqual(report["status"], "partial")
        statuses = {check["key"]: check["status"] for check in report["checks"]}
        self.assertEqual(statuses["python"], "ready")
        self.assertEqual(statuses["provider"], "not_implemented")
        self.assertEqual(statuses["workbench_assets"], "not_run")
        self.assertEqual(statuses["workspace_store_configured"], "ready")
        self.assertEqual(statuses["workspace_store_open"], "not_run")
        self.assertEqual(statuses["workspace_store_schema"], "not_run")
        self.assertEqual(statuses["workspace_store_readwrite"], "not_run")

    def test_doctor_does_not_create_missing_data_directory_or_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-cli-") as directory:
            missing = Path(directory) / "missing-data"
            result = run_cli("doctor", "--json", "--data-dir", str(missing))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(missing.exists())
            report = json.loads(result.stdout)
            statuses = {check["key"]: check["status"] for check in report["checks"]}
            self.assertEqual(statuses["workspace_store_configured"], "blocked")
            self.assertEqual(statuses["workspace_store_open"], "blocked")


    def test_openapi_command_writes_explicit_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-cli-") as directory:
            output = Path(directory) / "openapi.json"
            result = run_cli("openapi", "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["info"]["title"],
                "ContextOx Workbench API",
            )
