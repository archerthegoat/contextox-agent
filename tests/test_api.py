import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from contextox.api import create_app
from contextox.models import EventEnvelope, HealthResponse, WorkspaceError, WorkbenchSnapshot


def _endpoint(app, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


async def _asgi_request(app, method: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
    messages: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("testclient", 1234),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    chunks = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
    return start["status"], b"".join(chunks)


class ApiTests(unittest.TestCase):
    def test_openapi_contains_n2a_public_seams(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-no-assets-") as directory:
            schema = create_app(static_dir=Path(directory)).openapi()
            paths = schema["paths"]
            self.assertTrue(
                {
                    "/api/health",
                    "/api/readiness",
                    "/api/workbench",
                    "/api/events",
                    "/api/workspaces",
                    "/api/workspaces/{workspace_id}",
                }
                <= set(paths)
            )
            event_content = paths["/api/events"]["get"]["responses"]["200"]["content"]
            self.assertEqual(set(event_content), {"text/event-stream"})
            self.assertEqual(
                event_content["text/event-stream"]["schema"], {"type": "string"}
            )
            self.assertTrue(schema["components"]["schemas"]["EventEnvelope"]["required"])
            self.assertEqual(
                set(schema["components"]["schemas"]["Workspace"]["properties"]),
                {"workspace_id", "display_name", "created_at"},
            )
            self.assertEqual(set(paths["/api/workspaces"]), {"get", "post"})
            self.assertEqual(set(paths["/api/workspaces/{workspace_id}"]), {"get"})

    def test_health_and_snapshot_keep_partial_boundary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-no-assets-") as directory:
            app = create_app(static_dir=Path(directory))
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
        with tempfile.TemporaryDirectory(prefix="contextox-api-no-assets-") as directory:
            app = create_app(static_dir=Path(directory))
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

    def test_workspace_create_list_get_and_unknown_ids(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-workspaces-") as directory:
            app = create_app(static_dir=Path(directory), data_dir=Path(directory))
            create = lambda name: asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    "/api/workspaces",
                    json.dumps({"display_name": name}, ensure_ascii=False).encode(),
                )
            )
            first_status, first_body = create("  同名 Workspace  ")
            second_status, second_body = create("同名 Workspace")
            self.assertEqual(first_status, 201)
            self.assertEqual(second_status, 201)
            first = json.loads(first_body)
            second = json.loads(second_body)
            self.assertNotEqual(first["workspace_id"], second["workspace_id"])
            self.assertEqual(first["display_name"], second["display_name"], "同名 Workspace")
            list_status, list_body = asyncio.run(_asgi_request(app, "GET", "/api/workspaces"))
            self.assertEqual(list_status, 200)
            listed = json.loads(list_body)
            self.assertEqual([workspace["workspace_id"] for workspace in listed], [
                first["workspace_id"], second["workspace_id"]
            ])
            get_status, get_body = asyncio.run(
                _asgi_request(app, "GET", f"/api/workspaces/{first['workspace_id']}")
            )
            self.assertEqual(get_status, 200)
            self.assertEqual(json.loads(get_body), first)

            malformed_status, malformed_body = asyncio.run(
                _asgi_request(app, "GET", "/api/workspaces/not-a-uuid")
            )
            unknown_status, unknown_body = asyncio.run(
                _asgi_request(
                    app,
                    "GET",
                    "/api/workspaces/00000000-0000-4000-8000-000000000000",
                )
            )
            malformed = WorkspaceError.model_validate_json(malformed_body)
            unknown = WorkspaceError.model_validate_json(unknown_body)
            self.assertEqual((malformed_status, malformed.code), (404, "workspace_not_found"))
            self.assertEqual((unknown_status, unknown.code), (404, "workspace_not_found"))
            self.assertNotEqual(malformed.request_id, unknown.request_id)

    def test_workspace_request_validation_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-workspaces-") as directory:
            app = create_app(static_dir=Path(directory), data_dir=Path(directory))
            invalid_payloads = (
                {"display_name": 42},
                {"display_name": "\nname"},
                {"display_name": "   "},
                {"display_name": "name", "extra": True},
            )
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    status, body = asyncio.run(
                        _asgi_request(app, "POST", "/api/workspaces", json.dumps(payload).encode())
                    )
                    error = WorkspaceError.model_validate_json(body)
                    self.assertEqual(status, 422)
                    self.assertEqual(error.code, "invalid_workspace_name")
                    self.assertNotIn(str(directory), error.message)
            status, body = asyncio.run(
                _asgi_request(app, "POST", "/api/workspaces", b"not-json")
            )
            self.assertEqual(status, 422)
            self.assertEqual(WorkspaceError.model_validate_json(body).code, "invalid_workspace_name")

    def test_workspace_store_unavailable_uses_private_error_envelope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-no-store-") as directory:
            app = create_app(static_dir=Path(directory))
            status, body = asyncio.run(_asgi_request(app, "GET", "/api/workspaces"))
            error = WorkspaceError.model_validate_json(body)
            self.assertEqual(status, 503)
            self.assertEqual(error.code, "workspace_store_unavailable")
            self.assertNotIn("sqlite", error.message.lower())
            self.assertNotIn(str(directory), body.decode())
