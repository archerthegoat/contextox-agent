import asyncio
import json
import unittest
from pathlib import Path

from contextox.api import create_app
from contextox.models import EventEnvelope, HealthResponse, WorkbenchSnapshot


def _endpoint(app, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


class ApiTests(unittest.TestCase):
    def test_openapi_contains_n1_public_seams(self) -> None:
        schema = create_app(static_dir=Path("/tmp/contextox-n1-no-assets")).openapi()
        paths = schema["paths"]
        self.assertTrue(
            {"/api/health", "/api/readiness", "/api/workbench", "/api/events"}
            <= set(paths)
        )
        self.assertTrue(schema["components"]["schemas"]["EventEnvelope"]["required"])


    def test_health_and_snapshot_keep_partial_boundary(self) -> None:
        app = create_app(static_dir=Path("/tmp/contextox-n1-no-assets"))
        health = _endpoint(app, "/api/health")()
        snapshot = _endpoint(app, "/api/workbench")()
        self.assertIsInstance(health, HealthResponse)
        self.assertEqual(health.status, "ready")
        self.assertEqual(health.product_status, "partial")
        self.assertIsInstance(snapshot, WorkbenchSnapshot)
        self.assertEqual(snapshot.status, "partial")
        self.assertEqual(
            {area.id for area in snapshot.areas},
            {"sources", "mission", "clarifications", "contract"},
        )
        self.assertTrue(any(lane.status == "pending" for lane in snapshot.evidence))


    def test_sse_emits_a_public_connection_envelope(self) -> None:
        app = create_app(static_dir=Path("/tmp/contextox-n1-no-assets"))
        response = _endpoint(app, "/api/events")()

        async def collect_first() -> str:
            iterator = response.body_iterator
            chunk = await anext(iterator)
            await iterator.aclose()
            return chunk

        chunk = asyncio.run(collect_first())
        self.assertIn("event: connected", chunk)
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        event = EventEnvelope.model_validate(json.loads(data_line.removeprefix("data: ")))
        self.assertEqual(event.sequence, 1)
        self.assertIsNone(event.workspace_id)
