import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError

from contextox import agent as agent_module
from contextox.api import create_app
from contextox.provider import ProviderUsage
from contextox.store import WorkspaceStoreUnavailableError
from contextox.models import (
    ClarificationRequest,
    ColumnProfile,
    ContextManifestInput,
    ContextSnapshot,
    DefinitionDraft,
    DefinitionField,
    EvidenceRef,
    EventEnvelope,
    FinishRunArguments,
    HealthResponse,
    Mission,
    MissionDraftPayload,
    ModelDeltaEventInput,
    ModelDeltaPayload,
    ProviderConfigSnapshot,
    ProviderReceipt,
    RelationshipCandidate,
    RunBudget,
    RunEventEnvelope,
    RunEventInput,
    RunSnapshot,
    RunToolResult,
    SampleCell,
    SampleRow,
    SourceIdentity,
    SourceArtifact,
    SourceBatchResult,
    SourceExcerpt,
    SourceRevision,
    TableKey,
    TableProfile,
    ToolReceipt,
    UnknownItem,
    WorkspaceError,
    WorkbenchSnapshot,
    DomainToolCall,
    canonical_sha256,
)


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


async def _asgi_finite_stream(
    app, path: str, headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, bytes]:
    messages: list[dict] = []
    received = False
    disconnected = asyncio.Event()

    async def receive() -> dict:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": headers or [],
        "client": ("testclient", 1234), "server": ("testserver", 80),
        "root_path": "", "state": {},
    }
    try:
        await app(scope, receive, send)
    finally:
        disconnected.set()
    start = next(message for message in messages if message["type"] == "http.response.start")
    chunks = [
        message.get("body", b"") for message in messages
        if message["type"] == "http.response.body"
    ]
    return start["status"], b"".join(chunks)


async def _asgi_chunked_request(
    app,
    method: str,
    path: str,
    chunks: list[bytes],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, bytes, list[dict]]:
    messages: list[dict] = []
    index = 0

    async def receive() -> dict:
        nonlocal index
        if index >= len(chunks):
            return {"type": "http.disconnect"}
        body = chunks[index]
        index += 1
        return {"type": "http.request", "body": body, "more_body": index < len(chunks)}

    async def send(message: dict) -> None:
        messages.append(message)

    headers = [(b"content-type", b"application/json")]
    if extra_headers:
        headers.extend(extra_headers)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 1234),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], body, messages


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _source_identity(workspace_id: str, number: int = 1) -> dict:
    return {
        "workspace_id": workspace_id,
        "source_id": _id(number),
        "revision_id": _id(number + 100),
        "sha256": "0" * 64,
    }


def _evidence_ref(workspace_id: str, number: int = 1) -> dict:
    return {
        **_source_identity(workspace_id, number),
        "locator": {"kind": "text_lines", "line_start": 1, "line_end": 1},
    }


def _source_upload(files: list[tuple[str, str, bytes]]) -> bytes:
    return json.dumps(
        {
            "files": [
                {
                    "original_name": name,
                    "media_type": media_type,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
                for name, media_type, content in files
            ],
            "local_read_confirmed": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _empty_draft(workspace_id: str, mission_id: str, draft_id: str) -> DefinitionDraft:
    payload = {"fields": [], "relationships": [], "unresolved_items": []}
    return DefinitionDraft(
        workspace_id=workspace_id,
        mission_id=mission_id,
        draft_id=draft_id,
        version=1,
        sha256=canonical_sha256(payload),
        status="draft",
        semantic_approval="pending",
        fields=[],
        relationships=[],
        unresolved_items=[],
    )


class SharedModelTests(unittest.TestCase):
    def test_ids_counts_and_extra_fields_are_strict(self) -> None:
        SourceIdentity.model_validate(_source_identity(_id(1)))
        with self.assertRaises(ValidationError):
            SourceIdentity.model_validate({**_source_identity(_id(1)), "extra": True})
        with self.assertRaises(ValidationError):
            SourceIdentity.model_validate({**_source_identity(_id(1)), "sha256": "A" * 64})
        with self.assertRaises(ValidationError):
            SourceIdentity.model_validate({**_source_identity(_id(1)), "source_id": "not-a-uuid"})

    def test_definition_unknowns_cover_each_missing_semantic_dimension(self) -> None:
        field = {
            "field_key": "order_id",
            "name": "order_id",
            "meaning": None,
            "value_type": "string",
            "grain": None,
            "source_columns": [],
            "rule": None,
            "time_basis": "order_time",
            "null_handling": "not_null",
            "evidence_status": "candidate",
            "source_refs": [],
            "unknowns": [{"property_path": "meaning", "reason": "needs owner input"}],
        }
        with self.assertRaises(ValidationError):
            DefinitionField.model_validate(field)
        field["unknowns"] = [
            {"property_path": path, "reason": "needs owner input"}
            for path in ("meaning", "grain", "rule")
        ]
        DefinitionField.model_validate(field)
        field["unknowns"].append({"property_path": "value_type", "reason": "not missing"})
        with self.assertRaises(ValidationError):
            DefinitionField.model_validate(field)
        field["unknowns"] = [
            {"property_path": path, "reason": "needs owner input"}
            for path in ("meaning", "grain", "rule")
        ] + [{"property_path": "meaning", "reason": "duplicate"}]
        with self.assertRaises(ValidationError):
            DefinitionField.model_validate(field)

        table = {"source_ref": _source_identity(_id(1)), "table_id": "orders", "columns": ["order_id"]}
        relationship = {
            "relationship_key": "orders_items",
            "left": table,
            "right": table,
            "observed_cardinality": "one_to_many",
            "join_rule": None,
            "grain_notes": None,
            "evidence_status": "observed",
            "source_refs": [],
            "risks": [],
            "unknowns": [],
        }
        with self.assertRaises(ValidationError):
            RelationshipCandidate.model_validate(relationship)

    def test_cas_budget_and_turn_bounds_reject_bool_old_hash_and_overflow(self) -> None:
        base = {"fields": [], "relationships": [], "unresolved_items": []}
        with self.assertRaises(ValidationError):
            from contextox.models import UpdateDefinitionDraftArguments

            UpdateDefinitionDraftArguments(
                expected_version=0,
                expected_sha256="0" * 64,
                **base,
            )
        with self.assertRaises(ValidationError):
            from contextox.models import UpdateDefinitionDraftArguments

            UpdateDefinitionDraftArguments(expected_version=1, expected_sha256=None, **base)
        with self.assertRaises(ValidationError):
            RunBudget(max_retries=False)
        with self.assertRaises(ValidationError):
            RunBudget(max_tool_calls=24.0)
        with self.assertRaises(ValidationError):
            ContextManifestInput(
                mission_state_version=1,
                turn_index=9,
                draft_id=None,
                draft_version=None,
                draft_sha256=None,
                source_refs=[],
                clarification_ids=[],
                tool_receipt_ids=[],
                budget=RunBudget(),
                excluded_reasons=[],
            )
        with self.assertRaises(ValidationError):
            ProviderReceipt(
                workspace_id=_id(1),
                receipt_id=_id(2),
                attempt_id=_id(3),
                mission_id=None,
                run_id=None,
                turn_index=2,
                created_at="2026-09-03T00:00:00+00:00",
                status="blocked",
                config={
                    "endpoint_id": "deepseek_chat_completions",
                    "model": "deepseek-v4-flash",
                    "thinking": "enabled",
                    "reasoning_effort": "high",
                },
                p0_sha256="0" * 64,
                input_tokens=None,
                output_tokens=None,
                cache_hit_tokens=None,
                cache_miss_tokens=None,
                context_manifest_id=None,
                context_manifest_sha256=None,
                tool_schema_sha256=None,
                error_code=None,
            )

    def test_aggregate_identity_checks_are_fail_closed(self) -> None:
        mission = Mission(
            workspace_id=_id(1),
            mission_id=_id(2),
            created_at="2026-09-03T00:00:00+00:00",
            state_version=1,
            status="active",
            title="Mission",
            goal="goal",
            completion_criteria=["criteria"],
            scope_notes=[],
            original_attempt_id=_id(3),
            source_refs=[],
        )
        run_other_workspace = RunSnapshot(
            workspace_id=_id(4),
            mission_id=_id(2),
            run_id=_id(5),
            status="queued",
            created_at="2026-09-03T00:00:00+00:00",
            started_at=None,
            finished_at=None,
            budget=RunBudget(),
            source_refs=[],
            draft=None,
            clarifications=[],
            last_sequence=0,
            terminal_receipt=None,
            final_output=None,
            error_code=None,
        )
        with self.assertRaises(ValidationError):
            ContextSnapshot(
                mission=mission,
                run=run_other_workspace,
                sources=[],
                draft=None,
                clarifications=[],
            )

        run_other_mission = RunSnapshot(
            workspace_id=_id(1),
            mission_id=_id(9),
            run_id=_id(5),
            status="queued",
            created_at="2026-09-03T00:00:00+00:00",
            started_at=None,
            finished_at=None,
            budget=RunBudget(),
            source_refs=[],
            draft=None,
            clarifications=[],
            last_sequence=0,
            terminal_receipt=None,
            final_output=None,
            error_code=None,
        )
        with self.assertRaises(ValidationError):
            ContextSnapshot(
                mission=mission,
                run=run_other_mission,
                sources=[],
                draft=None,
                clarifications=[],
            )

        run_same_identity = RunSnapshot(
            workspace_id=_id(1),
            mission_id=_id(2),
            run_id=_id(5),
            status="queued",
            created_at="2026-09-03T00:00:00+00:00",
            started_at=None,
            finished_at=None,
            budget=RunBudget(),
            source_refs=[],
            draft=None,
            clarifications=[],
            last_sequence=0,
            terminal_receipt=None,
            final_output=None,
            error_code=None,
        )
        wrong_run_clarification = ClarificationRequest(
            workspace_id=_id(1),
            mission_id=_id(2),
            run_id=_id(9),
            clarification_id=_id(10),
            draft_version=1,
            draft_sha256="0" * 64,
            status="awaiting_answer",
            questions=[
                {
                    "question": "Which owner should confirm this?",
                    "why_needed": "The owner is not evidenced.",
                    "expected_answer_type": "text",
                    "suggested_owner_role": None,
                    "related_definition_paths": [],
                    "evidence_requested": [],
                    "examples_or_options": [],
                    "blocking_impact": "blocking",
                    "source_refs": [],
                }
            ],
        )
        with self.assertRaises(ValidationError):
            ContextSnapshot(
                mission=mission,
                run=run_same_identity,
                sources=[],
                draft=None,
                clarifications=[wrong_run_clarification],
            )

        other_table = {
            "source_ref": _source_identity(_id(4)),
            "table_id": "orders",
            "columns": ["order_id"],
        }
        relationship = RelationshipCandidate(
            relationship_key="orders_items",
            left=other_table,
            right=other_table,
            observed_cardinality="unknown",
            join_rule=None,
            grain_notes=None,
            evidence_status="candidate",
            source_refs=[],
            risks=[],
            unknowns=[],
        )
        with self.assertRaises(ValidationError):
            DefinitionDraft(
                workspace_id=_id(1),
                mission_id=_id(2),
                draft_id=_id(6),
                version=1,
                sha256=canonical_sha256(
                    {"fields": [], "relationships": [relationship.model_dump(mode="json")], "unresolved_items": []}
                ),
                status="draft",
                semantic_approval="pending",
                fields=[],
                relationships=[relationship],
                unresolved_items=[],
            )

        receipt = ToolReceipt(
            workspace_id=_id(1),
            mission_id=_id(2),
            run_id=_id(5),
            receipt_id=_id(7),
            ordinal=1,
            call_id="call-1",
            name="update_definition_draft",
            arguments_sha256="0" * 64,
            status="succeeded",
            created_at="2026-09-03T00:00:00+00:00",
            source_refs=[],
            error_code=None,
        )
        with self.assertRaises(ValidationError):
            RunToolResult(
                call_id="call-1",
                status="succeeded",
                output=_empty_draft(_id(4), _id(2), _id(8)),
                tool_receipt=receipt,
                terminal_snapshot=None,
            )
        with self.assertRaises(ValidationError):
            RunToolResult(
                call_id="call-1",
                status="succeeded",
                output=_empty_draft(_id(1), _id(9), _id(8)),
                tool_receipt=receipt,
                terminal_snapshot=None,
            )
        with self.assertRaises(ValidationError):
            RunToolResult(
                call_id="call-1",
                status="succeeded",
                output=_empty_draft(_id(1), _id(2), _id(8)),
                tool_receipt=receipt,
                terminal_snapshot=run_other_workspace,
            )
        with self.assertRaises(ValidationError):
            RunToolResult(
                call_id="call-1",
                status="succeeded",
                output=_empty_draft(_id(1), _id(2), _id(8)),
                tool_receipt=receipt,
                terminal_snapshot=run_same_identity.model_copy(update={"run_id": _id(9)}),
            )

        table_receipt = receipt.model_copy(
            update={"call_id": "table-call", "name": "inspect_dataset"}
        )
        table_output = TableProfile(
            table_id="orders",
            row_count=1,
            columns=[
                ColumnProfile(
                    name="order_id",
                    observed_types=["integer"],
                    missing_count=0,
                    null_count=0,
                    distinct_count=1,
                    numeric_min="1",
                    numeric_max="1",
                )
            ],
            duplicate_row_count=0,
            sample_rows=[
                SampleRow(
                    row_number=1,
                    cells=[
                        SampleCell(
                            column_name="order_id",
                            value_kind="integer",
                            text="1",
                            truncated=False,
                        )
                    ],
                    source_refs=[_evidence_ref(_id(4), 10)],
                )
            ],
            source_refs=[],
        )
        with self.assertRaises(ValidationError):
            RunToolResult(
                call_id="table-call",
                status="succeeded",
                output=table_output,
                tool_receipt=table_receipt,
                terminal_snapshot=None,
            )

    def test_finish_run_accepts_many_distinct_evidence_locators(self) -> None:
        source = _source_identity(_id(1), 1)
        references = [
            {
                **source,
                "locator": {"kind": "text_lines", "line_start": line, "line_end": line},
            }
            for line in range(1, 10)
        ]
        finish = FinishRunArguments(
            outcome="partial",
            reason="The source is incomplete.",
            source_refs=references,
        )
        self.assertEqual(len(finish.source_refs), 9)


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
            self.assertEqual(
                {
                    "/api/workspaces/{workspace_id}/sources",
                    "/api/workspaces/{workspace_id}/sources/{revision_id}",
                    "/api/workspaces/{workspace_id}/sources/{revision_id}/read",
                    "/api/workspaces/{workspace_id}/mission-draft-attempts",
                    "/api/workspaces/{workspace_id}/mission-draft-attempts/{attempt_id}",
                    "/api/workspaces/{workspace_id}/mission-draft-attempts/{attempt_id}/confirm",
                    "/api/workspaces/{workspace_id}/missions",
                    "/api/workspaces/{workspace_id}/missions/{mission_id}",
                    "/api/workspaces/{workspace_id}/missions/{mission_id}/runs",
                    "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}",
                    "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}/cancel",
                    "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}/events",
                },
                set(paths) - {
                    "/api/health",
                    "/api/readiness",
                    "/api/workbench",
                    "/api/events",
                    "/api/workspaces",
                    "/api/workspaces/{workspace_id}",
                },
            )
            self.assertIn("DomainToolCall", schema["components"]["schemas"])
            self.assertIn("RunEventInput", schema["components"]["schemas"])
            self.assertIn("RunEventEnvelope", schema["components"]["schemas"])
            self.assertEqual(
                schema["components"]["schemas"]["DomainToolCall"]["discriminator"]["propertyName"],
                "name",
            )
            self.assertEqual(
                schema["components"]["schemas"]["RunEventEnvelope"]["discriminator"]["propertyName"],
                "event_type",
            )
            run_event_parameters = paths[
                "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}/events"
            ]["get"]["parameters"]
            self.assertEqual(
                next(parameter for parameter in run_event_parameters if parameter["name"] == "Last-Event-ID")[
                    "required"
                ],
                False,
            )
            self.assertEqual(set(paths["/api/workspaces"]), {"get", "post"})
            self.assertEqual(set(paths["/api/workspaces/{workspace_id}"]), {"get"})

            def collect_refs(value: object) -> list[str]:
                if isinstance(value, dict):
                    refs = []
                    if isinstance(value.get("$ref"), str):
                        refs.append(value["$ref"])
                    for item in value.values():
                        refs.extend(collect_refs(item))
                    return refs
                if isinstance(value, list):
                    refs = []
                    for item in value:
                        refs.extend(collect_refs(item))
                    return refs
                return []

            component_names = set(schema["components"]["schemas"])
            for reference in collect_refs(schema):
                if reference.startswith("#/components/schemas/"):
                    self.assertIn(reference.removeprefix("#/components/schemas/"), component_names)

    def test_public_models_are_strict_and_discriminated(self) -> None:
        with self.assertRaises(ValidationError):
            TypeAdapter(DomainToolCall).validate_python(
                {
                    "call_id": "read",
                    "name": "list_sources",
                    "arguments": {"revision_id": "not-an-id", "locator": {"kind": "json_pointer", "pointer": ""}},
                }
            )
        with self.assertRaises(ValidationError):
            RunEventInput(
                event_type="model_started",
                public_payload={"status": "running"},
            )
        with self.assertRaises(ValidationError):
            RunEventEnvelope(
                event_id="event",
                event_type="run_partial",
                occurred_at="2026-09-03T00:00:00+00:00",
                workspace_id="00000000-0000-4000-8000-000000000001",
                mission_id="00000000-0000-4000-8000-000000000002",
                run_id="00000000-0000-4000-8000-000000000003",
                sequence=1,
                public_payload={
                    "status": "failed",
                    "terminal_receipt_id": None,
                    "error_code": None,
                },
            )

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

    def test_source_routes_import_list_restart_artifact_excerpt_and_isolate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-sources-") as directory:
            data_dir = Path(directory)
            app = create_app(static_dir=data_dir, data_dir=data_dir)
            create_status, create_body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    "/api/workspaces",
                    json.dumps({"display_name": "Source API"}).encode(),
                )
            )
            self.assertEqual(create_status, 201)
            workspace_id = json.loads(create_body)["workspace_id"]
            fixtures = [
                ("orders.csv", "text/csv", b"id,name\n1,Ada\n"),
                ("orders.json", "application/json", b'{"orders":[{"id":1}]}'),
                ("notes.md", "text/markdown", "# Title\n正文".encode()),
                ("notes.txt", "text/plain", "alpha\nbeta".encode()),
            ]
            status, body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    f"/api/workspaces/{workspace_id}/sources",
                    _source_upload(fixtures),
                )
            )
            self.assertEqual(status, 200)
            batch = SourceBatchResult.model_validate_json(body)
            self.assertEqual([item.original_name for item in batch.items], [item[0] for item in fixtures])
            self.assertEqual([item.status for item in batch.items], ["accepted"] * 4)
            revisions = [item.revision for item in batch.items]
            self.assertTrue(all(revision is not None for revision in revisions))

            repeated_status, repeated_body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    f"/api/workspaces/{workspace_id}/sources",
                    _source_upload([("orders-copy.csv", "text/csv", fixtures[0][2])]),
                )
            )
            self.assertEqual(repeated_status, 200)
            repeated = SourceBatchResult.model_validate_json(repeated_body).items[0].revision
            self.assertIsNotNone(repeated)
            self.assertNotEqual(repeated.source_id, revisions[0].source_id)
            self.assertNotEqual(repeated.revision_id, revisions[0].revision_id)
            self.assertEqual(repeated.sha256, revisions[0].sha256)

            restarted = create_app(static_dir=data_dir, data_dir=data_dir)
            list_status, list_body = asyncio.run(
                _asgi_request(restarted, "GET", f"/api/workspaces/{workspace_id}/sources")
            )
            self.assertEqual(list_status, 200)
            listed = TypeAdapter(list[SourceRevision]).validate_json(list_body)
            self.assertEqual(len(listed), 5)
            self.assertEqual(
                listed,
                sorted(listed, key=lambda item: (item.observed_at, item.source_id, item.revision_id)),
            )

            csv_revision, json_revision, markdown_revision, text_revision = revisions
            artifact_status, artifact_body = asyncio.run(
                _asgi_request(
                    restarted,
                    "GET",
                    f"/api/workspaces/{workspace_id}/sources/{csv_revision.revision_id}",
                )
            )
            self.assertEqual(artifact_status, 200)
            artifact = SourceArtifact.model_validate_json(artifact_body)
            self.assertEqual(artifact.source_ref.sha256, csv_revision.sha256)
            excerpt_cases = (
                (
                    csv_revision,
                    {"kind": "csv_rows", "row_start": 1, "row_end": 1, "column": None},
                    "1,Ada",
                ),
                (json_revision, {"kind": "json_pointer", "pointer": "/orders/0/id"}, "1"),
                (markdown_revision, {"kind": "text_lines", "line_start": 2, "line_end": 2}, "正文"),
                (text_revision, {"kind": "text_lines", "line_start": 2, "line_end": 2}, "beta"),
            )
            for revision, locator, expected_text in excerpt_cases:
                with self.subTest(media_type=revision.media_type):
                    excerpt_status, excerpt_body = asyncio.run(
                        _asgi_request(
                            restarted,
                            "POST",
                            f"/api/workspaces/{workspace_id}/sources/{revision.revision_id}/read",
                            json.dumps({"locator": locator}).encode(),
                        )
                    )
                    self.assertEqual(excerpt_status, 200)
                    self.assertEqual(SourceExcerpt.model_validate_json(excerpt_body).text, expected_text)

            other_status, other_body = asyncio.run(
                _asgi_request(
                    restarted,
                    "POST",
                    "/api/workspaces",
                    json.dumps({"display_name": "Other"}).encode(),
                )
            )
            self.assertEqual(other_status, 201)
            other_workspace = json.loads(other_body)["workspace_id"]
            cross_status, cross_body = asyncio.run(
                _asgi_request(
                    restarted,
                    "GET",
                    f"/api/workspaces/{other_workspace}/sources/{csv_revision.revision_id}",
                )
            )
            cross_error = WorkspaceError.model_validate_json(cross_body)
            self.assertEqual((cross_status, cross_error.code), (404, "source_not_found"))
            self.assertNotIn(str(data_dir), cross_body.decode())
            unknown_status, unknown_body = asyncio.run(
                _asgi_request(restarted, "GET", "/api/workspaces/not-a-workspace/sources")
            )
            self.assertEqual(
                (unknown_status, WorkspaceError.model_validate_json(unknown_body).code),
                (404, "workspace_not_found"),
            )

    def test_source_batch_statuses_and_request_limits_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-source-limits-") as directory:
            data_dir = Path(directory)
            app = create_app(static_dir=data_dir, data_dir=data_dir)
            create_status, create_body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    "/api/workspaces",
                    json.dumps({"display_name": "Source limits"}).encode(),
                )
            )
            self.assertEqual(create_status, 201)
            workspace_id = json.loads(create_body)["workspace_id"]
            status_files = [
                ("ready.txt", "text/plain", b"ready"),
                ("partial.csv", "text/csv", b"a,b\n1\n"),
                (
                    "blocked.csv",
                    "text/csv",
                    (",".join(f"c{index}" for index in range(101)) + "\n").encode(),
                ),
                ("failed.txt", "text/plain", b"\xff"),
            ]
            status, body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    f"/api/workspaces/{workspace_id}/sources",
                    _source_upload(status_files),
                )
            )
            self.assertEqual(status, 200)
            batch = SourceBatchResult.model_validate_json(body)
            self.assertEqual([item.file_index for item in batch.items], [0, 1, 2, 3])
            self.assertEqual(
                [item.status for item in batch.items],
                ["accepted", "partial", "blocked", "failed"],
            )
            self.assertIsNone(batch.items[0].error)
            self.assertTrue(all(item.revision is not None for item in batch.items))
            self.assertTrue(all(item.error is not None for item in batch.items[1:]))

            real_import = app.state.workspace_store.import_source_revision

            def fail_one_item(
                item_workspace_id: str,
                original_name: str,
                media_type: str,
                content: bytes,
            ):
                if original_name == "store-fail.txt":
                    raise WorkspaceStoreUnavailableError()
                return real_import(item_workspace_id, original_name, media_type, content)

            with patch.object(
                app.state.workspace_store,
                "import_source_revision",
                side_effect=fail_one_item,
            ):
                partial_status, partial_body = asyncio.run(
                    _asgi_request(
                        app,
                        "POST",
                        f"/api/workspaces/{workspace_id}/sources",
                        _source_upload(
                            [
                                ("before.txt", "text/plain", b"before"),
                                ("store-fail.txt", "text/plain", b"failed"),
                                ("after.txt", "text/plain", b"after"),
                            ]
                        ),
                    )
                )
            self.assertEqual(partial_status, 200)
            partial_batch = SourceBatchResult.model_validate_json(partial_body)
            self.assertEqual(
                [item.status for item in partial_batch.items],
                ["accepted", "failed", "accepted"],
            )
            self.assertIsNone(partial_batch.items[1].revision)
            self.assertEqual(partial_batch.items[1].error.code, "workspace_store_unavailable")

            invalid_payloads = (
                {
                    "files": [
                        {
                            "original_name": "bad.bin",
                            "media_type": "application/octet-stream",
                            "content_base64": "",
                        }
                    ],
                    "local_read_confirmed": True,
                },
                {
                    "files": [
                        {
                            "original_name": "bad.txt",
                            "media_type": "text/plain",
                            "content_base64": "not base64!",
                        }
                    ],
                    "local_read_confirmed": True,
                },
                {
                    "files": [
                        {
                            "original_name": f"item-{index}.txt",
                            "media_type": "text/plain",
                            "content_base64": "",
                        }
                        for index in range(9)
                    ],
                    "local_read_confirmed": True,
                },
            )
            for payload in invalid_payloads:
                with self.subTest(kind=payload["files"][0]["original_name"]):
                    invalid_status, invalid_body = asyncio.run(
                        _asgi_request(
                            app,
                            "POST",
                            f"/api/workspaces/{workspace_id}/sources",
                            json.dumps(payload).encode(),
                        )
                    )
                    invalid_error = WorkspaceError.model_validate_json(invalid_body)
                    self.assertEqual((invalid_status, invalid_error.code), (422, "invalid_request"))

            too_large_status, too_large_body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    f"/api/workspaces/{workspace_id}/sources",
                    _source_upload([("large.txt", "text/plain", b"x" * (2 * 1024 * 1024 + 1))]),
                )
            )
            self.assertEqual(
                (too_large_status, WorkspaceError.model_validate_json(too_large_body).code),
                (422, "invalid_request"),
            )

            batch_over_limit = [
                (f"large-{index}.txt", "text/plain", b"x" * (2 * 1024 * 1024))
                for index in range(4)
            ] + [("one.txt", "text/plain", b"x")]
            batch_status, batch_body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    f"/api/workspaces/{workspace_id}/sources",
                    _source_upload(batch_over_limit),
                )
            )
            self.assertEqual(
                (batch_status, WorkspaceError.model_validate_json(batch_body).code),
                (422, "invalid_request"),
            )

    def test_mission_and_run_routes_wire_real_store_without_provider_network(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-path2-") as directory:
            app = create_app(static_dir=Path(directory), data_dir=Path(directory))
            status, body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    "/api/workspaces",
                    json.dumps({"display_name": "Path 2"}).encode(),
                )
            )
            self.assertEqual(status, 201)
            workspace_id = json.loads(body)["workspace_id"]
            object_id = "00000000-0000-4000-8000-000000000010"
            store = app.state.workspace_store
            runtime = app.state.path2_runtime
            draft_body = json.dumps(
                {"original_input": "梳理数据关系", "provider_send_confirmed": True},
                ensure_ascii=False,
            ).encode()

            with patch.object(
                runtime, "start_mission_draft",
                side_effect=lambda ws, original: store.create_mission_draft_attempt(ws, original),
            ):
                attempt_status, attempt_body = asyncio.run(_asgi_request(
                    app, "POST",
                    f"/api/workspaces/{workspace_id}/mission-draft-attempts",
                    draft_body,
                ))
            self.assertEqual(attempt_status, 202)
            attempt = json.loads(attempt_body)
            self.assertEqual(attempt["status"], "queued")
            attempt_id = attempt["attempt_id"]
            fetched_status, fetched_body = asyncio.run(_asgi_request(
                app, "GET",
                f"/api/workspaces/{workspace_id}/mission-draft-attempts/{attempt_id}",
            ))
            self.assertEqual((fetched_status, json.loads(fetched_body)["attempt_id"]),
                             (200, attempt_id))

            store.mark_mission_draft_running(workspace_id, attempt_id)
            candidate = MissionDraftPayload(
                title="数据关系", goal="梳理授权数据关系",
                completion_criteria=["形成候选定义"], scope_notes=[],
            )

            class Provider:
                config = {
                    "endpoint_id": "deepseek_chat_completions",
                    "model": "deepseek-v4-flash",
                    "thinking": "enabled",
                    "reasoning_effort": "high",
                }

            receipt = agent_module._make_receipt(
                provider=Provider(), workspace_id=workspace_id,
                attempt_id=attempt_id, mission_id=None, run_id=None,
                turn_index=1, status="succeeded",
                p0_sha256=agent_module.P0_DRAFT_SHA256,
                usage=ProviderUsage(input_tokens=1, output_tokens=1,
                                    cache_hit_tokens=0, cache_miss_tokens=1),
            )
            ready = store.save_mission_draft_result(
                workspace_id, attempt_id, candidate, receipt
            )
            confirm_body = json.dumps({
                "candidate_version": 1,
                "candidate_sha256": ready.candidate_sha256,
                "source_refs": [],
            }).encode()
            confirmed_status, confirmed_body = asyncio.run(_asgi_request(
                app, "POST",
                f"/api/workspaces/{workspace_id}/mission-draft-attempts/{attempt_id}/confirm",
                confirm_body,
            ))
            self.assertEqual(confirmed_status, 200)
            mission = Mission.model_validate_json(confirmed_body)

            list_status, list_body = asyncio.run(_asgi_request(
                app, "GET", f"/api/workspaces/{workspace_id}/missions"
            ))
            self.assertEqual((list_status, len(json.loads(list_body))), (200, 1))
            snapshot_status, _ = asyncio.run(_asgi_request(
                app, "GET",
                f"/api/workspaces/{workspace_id}/missions/{mission.mission_id}",
            ))
            self.assertEqual(snapshot_status, 200)

            run_body = json.dumps({
                "expected_state_version": 1, "source_refs": [],
                "provider_send_confirmed": True, "client_request_id": object_id,
            }).encode()
            with patch.object(
                runtime, "start_run", side_effect=store.start_run,
            ):
                run_status, run_body_response = asyncio.run(_asgi_request(
                    app, "POST",
                    f"/api/workspaces/{workspace_id}/missions/{mission.mission_id}/runs",
                    run_body,
                ))
            self.assertEqual(run_status, 202)
            run = RunSnapshot.model_validate_json(run_body_response)
            store.mark_run_running(workspace_id, mission.mission_id, run.run_id)
            store.append_run_event(
                workspace_id, mission.mission_id, run.run_id,
                ModelDeltaEventInput(
                    event_type="model_delta",
                    public_payload=ModelDeltaPayload(
                        turn_index=1, content="bounded live delta"
                    ),
                ),
            )
            fetched_run_status, _ = asyncio.run(_asgi_request(
                app, "GET",
                f"/api/workspaces/{workspace_id}/missions/{mission.mission_id}/runs/{run.run_id}",
            ))
            self.assertEqual(fetched_run_status, 200)
            cancelled_status, cancelled_body = asyncio.run(_asgi_request(
                app, "POST",
                f"/api/workspaces/{workspace_id}/missions/{mission.mission_id}/runs/{run.run_id}/cancel",
                b"{}",
            ))
            self.assertEqual(cancelled_status, 200)
            self.assertEqual(json.loads(cancelled_body)["status"], "cancelled")
            events_path = (
                f"/api/workspaces/{workspace_id}/missions/{mission.mission_id}"
                f"/runs/{run.run_id}/events"
            )
            event_status, event_body = asyncio.run(
                _asgi_finite_stream(app, events_path)
            )
            self.assertEqual(event_status, 200)
            self.assertIn(b"event: model_delta", event_body)
            self.assertIn(b"event: run_cancelled", event_body)
            self.assertNotIn("梳理数据关系", event_body.decode())
            resumed_status, resumed_body = asyncio.run(_asgi_finite_stream(
                app, events_path, [(b"last-event-id", b"2")]
            ))
            self.assertEqual((resumed_status, resumed_body), (200, b""))
            invalid_status, invalid_body, _ = asyncio.run(_asgi_chunked_request(
                app, "GET", events_path, [b""],
                [(b"last-event-id", b"02")],
            ))
            self.assertEqual(
                (invalid_status, WorkspaceError.model_validate_json(invalid_body).code),
                (422, "run_event_cursor_invalid"),
            )
            restarted_app = create_app(
                static_dir=Path(directory), data_dir=Path(directory)
            )
            replay_status, replay_body = asyncio.run(
                _asgi_finite_stream(restarted_app, events_path)
            )
            self.assertEqual(replay_status, 200)
            self.assertIn(b"contextox-snapshot-required missing=1-1", replay_body)
            self.assertNotIn(b"event: model_delta", replay_body)
            self.assertIn(b"event: run_cancelled", replay_body)

            response_status, response_body = asyncio.run(
                _asgi_request(app, "GET", "/api/workspaces/not-a-workspace/missions")
            )
            error = WorkspaceError.model_validate_json(response_body)
            self.assertEqual((response_status, error.code), (404, "workspace_not_found"))

    def test_path2_body_limit_is_enforced_at_real_asgi_boundary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-body-limit-") as directory:
            app = create_app(static_dir=Path(directory), data_dir=Path(directory))
            status, body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    "/api/workspaces",
                    json.dumps({"display_name": "Body limit"}).encode(),
                )
            )
            self.assertEqual(status, 201)
            workspace_id = json.loads(body)["workspace_id"]
            limit = 12 * 1024 * 1024
            chunks = [b"x" * limit, b"overflow-marker"]
            overflow_paths = (
                f"/api/workspaces/{workspace_id}/sources",
                f"/api/workspaces/{workspace_id}/mission-draft-attempts",
                f"/api/workspaces/{workspace_id}/missions/{_id(10)}/runs",
            )
            for path in overflow_paths:
                for headers in ([], [(b"content-length", b"1")]):
                    with self.subTest(path=path, headers=headers):
                        response_status, response_body, messages = asyncio.run(
                            _asgi_chunked_request(
                                app,
                                "POST",
                                path,
                                chunks,
                                headers,
                            )
                        )
                        error = WorkspaceError.model_validate_json(response_body)
                        self.assertEqual((response_status, error.code), (422, "invalid_request"))
                        self.assertNotIn("overflow-marker", response_body.decode())
                        self.assertEqual(
                            [message["type"] for message in messages],
                            ["http.response.start", "http.response.body"],
                        )

            response_status, response_body, _ = asyncio.run(
                _asgi_chunked_request(
                    app,
                    "GET",
                    f"/api/workspaces/{workspace_id}",
                    chunks,
                    [(b"content-length", b"1")],
                )
            )
            self.assertEqual(response_status, 200)
            self.assertEqual(json.loads(response_body)["workspace_id"], workspace_id)

    def test_path2_invalid_utf8_uses_bounded_error_envelope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-api-invalid-json-") as directory:
            app = create_app(static_dir=Path(directory), data_dir=Path(directory))
            status, body = asyncio.run(
                _asgi_request(
                    app,
                    "POST",
                    "/api/workspaces",
                    json.dumps({"display_name": "Invalid JSON"}).encode(),
                )
            )
            self.assertEqual(status, 201)
            workspace_id = json.loads(body)["workspace_id"]
            object_id = _id(10)
            paths = (
                f"/api/workspaces/{workspace_id}/sources",
                f"/api/workspaces/{workspace_id}/sources/{object_id}/read",
                f"/api/workspaces/{workspace_id}/mission-draft-attempts",
                f"/api/workspaces/{workspace_id}/mission-draft-attempts/{object_id}/confirm",
                f"/api/workspaces/{workspace_id}/missions/{object_id}/runs",
                f"/api/workspaces/{workspace_id}/missions/{object_id}/runs/{object_id}/cancel",
            )
            for path in paths:
                with self.subTest(path=path):
                    response_status, response_body = asyncio.run(
                        _asgi_request(app, "POST", path, b"\xff")
                    )
                    error = WorkspaceError.model_validate_json(response_body)
                    self.assertEqual((response_status, error.code), (422, "invalid_request"))
                    self.assertEqual(error.message, "Invalid request.")
                    self.assertNotIn("detail", response_body.decode())
                    self.assertNotIn("\ufffd", response_body.decode())
