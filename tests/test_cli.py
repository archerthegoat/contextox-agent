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
    def test_doctor_reports_n1_scope_and_pending_boundaries(self) -> None:
        result = run_cli("doctor", "--json", "--static-dir", "/tmp/contextox-n1-no-assets")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["scope"], "n1")
        self.assertEqual(report["status"], "partial")
        statuses = {check["key"]: check["status"] for check in report["checks"]}
        self.assertEqual(statuses["python"], "ready")
        self.assertEqual(statuses["provider"], "not_implemented")
        self.assertEqual(statuses["workbench_assets"], "not_run")


    def test_openapi_command_writes_explicit_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-cli-") as directory:
            output = Path(directory) / "openapi.json"
            result = run_cli("openapi", "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["info"]["title"],
                "ContextOx Workbench API",
            )
