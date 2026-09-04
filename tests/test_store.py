import concurrent.futures
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import contextox.store as store_module
from contextox.models import CsvRowsLocator, JsonPointerLocator, TextLinesLocator
from contextox.sources import SourceInputError
from contextox.store import (
    InvalidWorkspaceNameError,
    Path2NotImplementedError,
    SourceImportOutcomeUnknownError,
    SourceNotFoundError,
    WorkspaceSchemaUnsupportedError,
    WorkspaceStore,
    WorkspaceStoreBusyError,
    WorkspaceStoreError,
    WorkspaceNotFoundError,
    WorkspaceStoreUnavailableError,
)


EXPECTED_V2_TABLE_NAMES = (
    "workspaces",
    "source_revisions",
    "mission_draft_attempts",
    "missions",
    "mission_sources",
    "mission_messages",
    "runs",
    "run_sources",
    "provider_receipts",
    "context_manifests",
    "definition_drafts",
    "clarification_requests",
    "tool_receipts",
    "terminal_receipts",
    "run_events",
)

V1_WORKSPACES_SQL = """
CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def _write_exact_v1(data_dir: Path) -> list[tuple[str, str, str]]:
    rows = [
        (
            "00000000-0000-4000-8000-000000000001",
            "First Workspace",
            "2026-01-01T00:00:00+00:00",
        ),
        (
            "00000000-0000-4000-8000-000000000002",
            "Second Workspace",
            "2026-01-02T00:00:00+00:00",
        ),
    ]
    with closing(sqlite3.connect(data_dir / "contextox.sqlite3")) as connection, connection:
        connection.execute(V1_WORKSPACES_SQL)
        connection.executemany(
            "INSERT INTO workspaces (workspace_id, display_name, created_at) VALUES (?, ?, ?)",
            rows,
        )
        connection.execute("PRAGMA user_version=1")
    return rows


def _schema_objects(db_path: Path) -> list[tuple[str, str, str, str | None]]:
    with closing(sqlite3.connect(db_path)) as connection, connection:
        return connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()


def _normalize_sql(sql: str | None) -> str:
    return " ".join((sql or "").split()).casefold()


def _insert_attempt(
    connection: sqlite3.Connection,
    workspace_id: str,
    attempt_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO mission_draft_attempts
            (workspace_id, attempt_id, created_at, original_input, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (workspace_id, attempt_id, "2026-01-01T00:00:00+00:00", "input", "ready"),
    )


def _insert_mission(
    connection: sqlite3.Connection,
    workspace_id: str,
    mission_id: str,
    attempt_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO missions
            (workspace_id, mission_id, created_at, state_version, status,
             title, goal, completion_criteria_json, scope_notes_json,
             original_attempt_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            mission_id,
            "2026-01-01T00:00:00+00:00",
            1,
            "active",
            "Title",
            "Goal",
            "[]",
            "[]",
            attempt_id,
        ),
    )


def _insert_run(
    connection: sqlite3.Connection,
    workspace_id: str,
    mission_id: str,
    run_id: str,
    client_request_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO runs
            (workspace_id, mission_id, run_id, client_request_id, created_at,
             status, budget_json, last_sequence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            mission_id,
            run_id,
            client_request_id,
            "2026-01-01T00:00:00+00:00",
            "failed",
            "{}",
            0,
        ),
    )


def _insert_source_revision(
    connection: sqlite3.Connection,
    workspace_id: str,
    source_id: str,
    revision_id: str,
    sha256: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_revisions
            (workspace_id, source_id, revision_id, original_name, media_type,
             byte_size, sha256, observed_at, permission_status, parse_status,
             parser_version, artifact_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            source_id,
            revision_id,
            f"{source_id}.csv",
            "text/csv",
            1,
            sha256,
            "2026-01-01T00:00:00+00:00",
            "allowed",
            "succeeded",
            "v1",
            "{}",
        ),
    )


def _raw_source_path(store: WorkspaceStore, revision) -> Path:
    return (
        store.data_dir
        / "sources"
        / revision.workspace_id
        / revision.source_id
        / f"{revision.revision_id}.bin"
    )


class StoreTests(unittest.TestCase):
    def test_initializes_exact_v2_schema_and_persists_after_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            data_dir = Path(directory)
            store = WorkspaceStore.open(data_dir)
            self.assertEqual(store.db_path, data_dir.resolve() / "contextox.sqlite3")
            self.assertEqual(store.list_workspaces(), [])
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                self.assertEqual(
                    [
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                        ).fetchall()
                    ],
                    sorted(EXPECTED_V2_TABLE_NAMES),
                )
                self.assertTrue(store_module._schema_is_exact(connection))
                active_indexes = connection.execute(
                    "PRAGMA index_list(runs)"
                ).fetchall()
                self.assertIn(
                    "runs_one_active_per_mission",
                    {row[1] for row in active_indexes},
                )
                active_index = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE type='index' AND name='runs_one_active_per_mission'
                    """
                ).fetchone()[0]
                self.assertIn("where status in ('queued', 'running')", active_index.lower())
            first = store.create_workspace("  Client definition  ")
            second = store.create_workspace("Client definition")
            self.assertNotEqual(first.workspace_id, second.workspace_id)
            self.assertEqual(first.display_name, "Client definition")
            self.assertEqual(second.display_name, "Client definition")
            self.assertEqual(UUID(first.workspace_id).version, 4)
            self.assertEqual(first.created_at.tzinfo is not None, True)
            expected = sorted(
                [first, second], key=lambda workspace: (workspace.created_at, workspace.workspace_id)
            )
            self.assertEqual(store.list_workspaces(), expected)
            self.assertEqual(store.get_workspace(first.workspace_id), first)
            self.assertIsNone(store.get_workspace("not-a-workspace"))

            restarted = WorkspaceStore.open(data_dir)
            self.assertEqual(restarted.list_workspaces(), expected)

    def test_active_run_partial_unique_index_is_scoped_to_mission(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Run constraints").workspace_id
            mission_id = "00000000-0000-4000-8000-000000000010"
            attempt_id = "00000000-0000-4000-8000-000000000011"
            run_id = "00000000-0000-4000-8000-000000000012"
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_attempt(connection, workspace_id, attempt_id)
                _insert_mission(connection, workspace_id, mission_id, attempt_id)
                connection.execute(
                    """
                    INSERT INTO runs
                        (workspace_id, mission_id, run_id, client_request_id, created_at,
                         status, budget_json, last_sequence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        mission_id,
                        run_id,
                        "client-1",
                        "2026-01-01T00:00:00+00:00",
                        "queued",
                        "{}",
                        0,
                    ),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO runs
                            (workspace_id, mission_id, run_id, client_request_id, created_at,
                             status, budget_json, last_sequence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            workspace_id,
                            mission_id,
                            "00000000-0000-4000-8000-000000000013",
                            "client-2",
                            "2026-01-01T00:00:00+00:00",
                            "running",
                            "{}",
                            0,
                        ),
                    )
                _insert_run(
                    connection,
                    workspace_id,
                    mission_id,
                    "00000000-0000-4000-8000-000000000014",
                    "client-3",
                )

    def test_definition_draft_history_and_nullable_references_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Draft constraints").workspace_id
            mission_id = "mission-draft"
            attempt_id = "attempt-draft"
            run_id = "run-draft-null"
            second_run_id = "run-draft-versioned"
            draft_id = "draft-1"
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_attempt(connection, workspace_id, attempt_id)
                _insert_mission(connection, workspace_id, mission_id, attempt_id)
                _insert_run(connection, workspace_id, mission_id, run_id, "draft-null")
                _insert_run(
                    connection,
                    workspace_id,
                    mission_id,
                    second_run_id,
                    "draft-versioned",
                )

                insert_draft_sql = """
                    INSERT INTO definition_drafts
                        (workspace_id, mission_id, draft_id, version, sha256, status,
                         semantic_approval, fields_json, relationships_json,
                         unresolved_items_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                connection.execute(
                    insert_draft_sql,
                    (
                        workspace_id,
                        mission_id,
                        draft_id,
                        1,
                        "draft-sha-1",
                        "draft",
                        "pending",
                        "[]",
                        "[]",
                        "[]",
                    ),
                )
                connection.execute(
                    insert_draft_sql,
                    (
                        workspace_id,
                        mission_id,
                        draft_id,
                        2,
                        "draft-sha-2",
                        "draft",
                        "pending",
                        "[]",
                        "[]",
                        "[]",
                    ),
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT draft_id, version, sha256
                        FROM definition_drafts
                        WHERE workspace_id = ? AND mission_id = ?
                        ORDER BY version
                        """,
                        (workspace_id, mission_id),
                    ).fetchall(),
                    [(draft_id, 1, "draft-sha-1"), (draft_id, 2, "draft-sha-2")],
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        insert_draft_sql,
                        (
                            workspace_id,
                            mission_id,
                            "another-draft",
                            2,
                            "draft-sha-another",
                            "draft",
                            "pending",
                            "[]",
                            "[]",
                            "[]",
                        ),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        insert_draft_sql,
                        (
                            workspace_id,
                            mission_id,
                            "zero-draft",
                            0,
                            "draft-sha-zero",
                            "draft",
                            "pending",
                            "[]",
                            "[]",
                            "[]",
                        ),
                    )

                insert_manifest_sql = """
                    INSERT INTO context_manifests
                        (workspace_id, mission_id, run_id, manifest_id,
                         mission_state_version, turn_index, draft_id, draft_version,
                         draft_sha256, source_refs_json, clarification_ids_json,
                         tool_receipt_ids_json, budget_json, excluded_reasons_json,
                         sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                manifest_tail = ("[]", "[]", "[]", "{}", "[]")
                connection.execute(
                    insert_manifest_sql,
                    (
                        workspace_id,
                        mission_id,
                        run_id,
                        "manifest-null",
                        1,
                        1,
                        None,
                        None,
                        None,
                        *manifest_tail,
                        "manifest-sha-null",
                    ),
                )
                connection.execute(
                    insert_manifest_sql,
                    (
                        workspace_id,
                        mission_id,
                        second_run_id,
                        "manifest-versioned",
                        1,
                        1,
                        draft_id,
                        2,
                        "draft-sha-2",
                        *manifest_tail,
                        "manifest-sha-versioned",
                    ),
                )
                invalid_draft_refs = (
                    (draft_id, None, None),
                    (None, 2, None),
                    (None, None, "draft-sha-2"),
                    (draft_id, 2, None),
                    (draft_id, None, "draft-sha-2"),
                    (None, 2, "draft-sha-2"),
                    ("wrong-draft-id", 2, "draft-sha-2"),
                    (draft_id, 2, "wrong-draft-sha"),
                    (draft_id, 3, "draft-sha-3"),
                )
                for index, (invalid_id, invalid_version, invalid_sha256) in enumerate(
                    invalid_draft_refs
                ):
                    with self.subTest(
                        table="context_manifests",
                        draft_id=invalid_id,
                        draft_version=invalid_version,
                        draft_sha256=invalid_sha256,
                    ):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(
                                insert_manifest_sql,
                                (
                                    workspace_id,
                                    mission_id,
                                    run_id,
                                    f"manifest-invalid-{index}",
                                    1,
                                    2,
                                    invalid_id,
                                    invalid_version,
                                    invalid_sha256,
                                    *manifest_tail,
                                    f"manifest-invalid-sha-{index}",
                                ),
                            )

                insert_terminal_sql = """
                    INSERT INTO terminal_receipts
                        (workspace_id, mission_id, run_id, receipt_id, created_at,
                         terminal_tool, outcome, draft_id, draft_version, draft_sha256,
                         clarification_ids_json, provider_receipt_ids_json,
                         tool_receipt_ids_json, source_refs_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                terminal_prefix = (
                    workspace_id,
                    mission_id,
                    run_id,
                    "terminal-null",
                    "2026-01-01T00:00:00+00:00",
                    "finish_run",
                    "partial",
                )
                connection.execute(
                    insert_terminal_sql,
                    (*terminal_prefix, None, None, None, "[]", "[]", "[]", "[]"),
                )
                for index, (invalid_id, invalid_version, invalid_sha256) in enumerate(
                    invalid_draft_refs
                ):
                    with self.subTest(
                        table="terminal_receipts",
                        draft_id=invalid_id,
                        draft_version=invalid_version,
                        draft_sha256=invalid_sha256,
                    ):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(
                                insert_terminal_sql,
                                (
                                    workspace_id,
                                    mission_id,
                                    second_run_id,
                                    f"terminal-invalid-{index}",
                                    "2026-01-01T00:00:00+00:00",
                                    "finish_run",
                                    "partial",
                                    invalid_id,
                                    invalid_version,
                                    invalid_sha256,
                                    "[]",
                                    "[]",
                                    "[]",
                                    "[]",
                                ),
                            )
                connection.execute(
                    insert_terminal_sql,
                    (
                        workspace_id,
                        mission_id,
                        second_run_id,
                        "terminal-versioned",
                        "2026-01-01T00:00:00+00:00",
                        "finish_run",
                        "partial",
                        draft_id,
                        2,
                        "draft-sha-2",
                        "[]",
                        "[]",
                        "[]",
                        "[]",
                    ),
                )

                insert_clarification_sql = """
                    INSERT INTO clarification_requests
                        (workspace_id, mission_id, run_id, clarification_id,
                         draft_version, draft_sha256, status, questions_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """
                for index, (invalid_version, invalid_sha256) in enumerate(
                    ((2, "wrong-draft-sha"), (1, "draft-sha-2"), (0, "draft-sha-2"))
                ):
                    with self.subTest(
                        table="clarification_requests",
                        draft_version=invalid_version,
                        draft_sha256=invalid_sha256,
                    ):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(
                                insert_clarification_sql,
                                (
                                    workspace_id,
                                    mission_id,
                                    second_run_id,
                                    f"clarification-invalid-{index}",
                                    invalid_version,
                                    invalid_sha256,
                                    "awaiting_answer",
                                    "[]",
                                ),
                            )
                connection.execute(
                    insert_clarification_sql,
                    (
                        workspace_id,
                        mission_id,
                        second_run_id,
                        "clarification-exact",
                        2,
                        "draft-sha-2",
                        "awaiting_answer",
                        "[]",
                    ),
                )

    def test_source_reference_ordinals_preserve_order_and_reject_duplicates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Source order").workspace_id
            mission_id = "mission-sources"
            attempt_id = "attempt-sources"
            run_id = "run-sources"
            sources = (
                ("source-a", "revision-a", "sha-a"),
                ("source-b", "revision-b", "sha-b"),
                ("source-c", "revision-c", "sha-c"),
            )
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_attempt(connection, workspace_id, attempt_id)
                _insert_mission(connection, workspace_id, mission_id, attempt_id)
                _insert_run(connection, workspace_id, mission_id, run_id, "source-order")
                for source_id, revision_id, sha256 in sources:
                    _insert_source_revision(
                        connection,
                        workspace_id,
                        source_id,
                        revision_id,
                        sha256,
                    )

                insert_mission_source_sql = """
                    INSERT INTO mission_sources
                        (workspace_id, mission_id, ordinal, source_id, revision_id, sha256)
                    VALUES (?, ?, ?, ?, ?, ?)
                """
                connection.execute(
                    insert_mission_source_sql,
                    (workspace_id, mission_id, 1, *sources[1]),
                )
                connection.execute(
                    insert_mission_source_sql,
                    (workspace_id, mission_id, 0, *sources[0]),
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT ordinal, source_id, revision_id
                        FROM mission_sources
                        WHERE workspace_id = ? AND mission_id = ?
                        ORDER BY ordinal
                        """,
                        (workspace_id, mission_id),
                    ).fetchall(),
                    [(0, "source-a", "revision-a"), (1, "source-b", "revision-b")],
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        insert_mission_source_sql,
                        (
                            workspace_id,
                            mission_id,
                            2,
                            "source-c",
                            "revision-c",
                            "wrong-sha",
                        ),
                    )
                for values in (
                    (workspace_id, mission_id, 0, *sources[2]),
                    (workspace_id, mission_id, 2, *sources[0]),
                    (workspace_id, mission_id, -1, *sources[2]),
                ):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(insert_mission_source_sql, values)

                insert_run_source_sql = """
                    INSERT INTO run_sources
                        (workspace_id, mission_id, run_id, ordinal,
                         source_id, revision_id, sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """
                connection.execute(
                    insert_run_source_sql,
                    (workspace_id, mission_id, run_id, 1, *sources[1]),
                )
                connection.execute(
                    insert_run_source_sql,
                    (workspace_id, mission_id, run_id, 0, *sources[0]),
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT ordinal, source_id, revision_id
                        FROM run_sources
                        WHERE workspace_id = ? AND mission_id = ? AND run_id = ?
                        ORDER BY ordinal
                        """,
                        (workspace_id, mission_id, run_id),
                    ).fetchall(),
                    [(0, "source-a", "revision-a"), (1, "source-b", "revision-b")],
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        insert_run_source_sql,
                        (
                            workspace_id,
                            mission_id,
                            run_id,
                            2,
                            "source-c",
                            "revision-c",
                            "wrong-sha",
                        ),
                    )
                for values in (
                    (workspace_id, mission_id, run_id, 0, *sources[2]),
                    (workspace_id, mission_id, run_id, 2, *sources[0]),
                    (workspace_id, mission_id, run_id, -1, *sources[2]),
                ):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(insert_run_source_sql, values)

    def test_provider_receipt_requires_one_valid_parent_shape_and_positive_turn(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Provider receipt parent").workspace_id
            mission_id = "mission-provider"
            run_id = "run-provider"
            manifest_id = "manifest-provider"
            manifest_sha256 = "manifest-provider-sha"
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                for attempt_id in ("attempt-provider-1", "attempt-provider-2"):
                    _insert_attempt(connection, workspace_id, attempt_id)
                _insert_mission(connection, workspace_id, mission_id, "attempt-provider-1")
                _insert_run(connection, workspace_id, mission_id, run_id, "provider-parent")
                connection.execute(
                    """
                    INSERT INTO context_manifests
                        (workspace_id, mission_id, run_id, manifest_id,
                         mission_state_version, turn_index, draft_id, draft_version,
                         draft_sha256, source_refs_json, clarification_ids_json,
                         tool_receipt_ids_json, budget_json, excluded_reasons_json,
                         sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        mission_id,
                        run_id,
                        manifest_id,
                        1,
                        1,
                        None,
                        None,
                        None,
                        "[]",
                        "[]",
                        "[]",
                        "{}",
                        "[]",
                        manifest_sha256,
                    ),
                )

                insert_receipt_sql = """
                    INSERT INTO provider_receipts
                        (workspace_id, receipt_id, attempt_id, mission_id, run_id,
                         turn_index, created_at, status, config_json, p0_sha256,
                         context_manifest_id, context_manifest_sha256,
                         tool_schema_sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """

                def receipt_values(
                    receipt_id: str,
                    attempt_id: str | None,
                    parent_mission_id: str | None,
                    parent_run_id: str | None,
                    turn_index: int,
                    parent_manifest_id: str | None = None,
                    parent_manifest_sha256: str | None = None,
                    tool_schema_sha256: str | None = None,
                ) -> tuple[object, ...]:
                    return (
                        workspace_id,
                        receipt_id,
                        attempt_id,
                        parent_mission_id,
                        parent_run_id,
                        turn_index,
                        "2026-01-01T00:00:00+00:00",
                        "succeeded",
                        "{}",
                        "p0-sha",
                        parent_manifest_id,
                        parent_manifest_sha256,
                        tool_schema_sha256,
                    )

                connection.execute(
                    insert_receipt_sql,
                    receipt_values("receipt-attempt", "attempt-provider-1", None, None, 1),
                )
                invalid_shapes = (
                    receipt_values("receipt-null", None, None, None, 1),
                    receipt_values(
                        "receipt-mixed",
                        "attempt-provider-2",
                        mission_id,
                        run_id,
                        1,
                        manifest_id,
                        manifest_sha256,
                        "tool-schema-sha",
                    ),
                    receipt_values("receipt-mission-only", None, mission_id, None, 1),
                    receipt_values("receipt-run-only", None, None, run_id, 1),
                    receipt_values(
                        "receipt-zero-turn",
                        None,
                        mission_id,
                        run_id,
                        0,
                        manifest_id,
                        manifest_sha256,
                        "tool-schema-sha",
                    ),
                    receipt_values(
                        "receipt-attempt-turn-two",
                        "attempt-provider-2",
                        None,
                        None,
                        2,
                    ),
                    receipt_values(
                        "receipt-attempt-with-context",
                        "attempt-provider-2",
                        None,
                        None,
                        1,
                        manifest_id,
                        manifest_sha256,
                        "tool-schema-sha",
                    ),
                    receipt_values(
                        "receipt-run-missing-manifest-id",
                        None,
                        mission_id,
                        run_id,
                        1,
                        None,
                        manifest_sha256,
                        "tool-schema-sha",
                    ),
                    receipt_values(
                        "receipt-run-missing-manifest-hash",
                        None,
                        mission_id,
                        run_id,
                        1,
                        manifest_id,
                        None,
                        "tool-schema-sha",
                    ),
                    receipt_values(
                        "receipt-run-missing-tool-hash",
                        None,
                        mission_id,
                        run_id,
                        1,
                        manifest_id,
                        manifest_sha256,
                        None,
                    ),
                    receipt_values(
                        "receipt-run-wrong-manifest-id",
                        None,
                        mission_id,
                        run_id,
                        1,
                        "wrong-manifest",
                        manifest_sha256,
                        "tool-schema-sha",
                    ),
                    receipt_values(
                        "receipt-run-wrong-manifest-hash",
                        None,
                        mission_id,
                        run_id,
                        1,
                        manifest_id,
                        "wrong-manifest-sha",
                        "tool-schema-sha",
                    ),
                )
                for values in invalid_shapes:
                    with self.subTest(receipt_id=values[1]):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(insert_receipt_sql, values)
                connection.execute(
                    insert_receipt_sql,
                    receipt_values(
                        "receipt-run",
                        None,
                        mission_id,
                        run_id,
                        1,
                        manifest_id,
                        manifest_sha256,
                        "tool-schema-sha",
                    ),
                )

                self.assertEqual(
                    connection.execute(
                        """
                        SELECT receipt_id, attempt_id, mission_id, run_id, turn_index
                        FROM provider_receipts
                        ORDER BY receipt_id
                        """
                    ).fetchall(),
                    [
                        ("receipt-attempt", "attempt-provider-1", None, None, 1),
                        ("receipt-run", None, mission_id, run_id, 1),
                    ],
                )

    def test_migrates_exact_v1_and_preserves_multiple_workspaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            data_dir = Path(directory)
            rows = _write_exact_v1(data_dir)
            before_objects = _schema_objects(data_dir / "contextox.sqlite3")
            self.assertEqual(
                [
                    (object_type, name, table_name, _normalize_sql(sql))
                    for object_type, name, table_name, sql in before_objects
                ],
                [
                    (
                        "table",
                        "workspaces",
                        "workspaces",
                        _normalize_sql(V1_WORKSPACES_SQL),
                    )
                ],
            )

            migrated = WorkspaceStore.open(data_dir)

            self.assertEqual(
                migrated.list_workspaces(),
                [migrated.get_workspace(row[0]) for row in rows],
            )
            with closing(sqlite3.connect(data_dir / "contextox.sqlite3")) as connection, connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertTrue(store_module._schema_is_exact(connection))
                self.assertEqual(
                    connection.execute(
                        "SELECT workspace_id, display_name, created_at FROM workspaces "
                        "ORDER BY created_at, workspace_id"
                    ).fetchall(),
                    rows,
                )
            self.assertEqual(
                [
                    row[1]
                    for row in _schema_objects(data_dir / "contextox.sqlite3")
                    if row[0] == "table"
                ],
                sorted(EXPECTED_V2_TABLE_NAMES),
            )

    def test_migration_ddl_failure_rolls_back_and_leaves_exact_v1(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            data_dir = Path(directory)
            rows = _write_exact_v1(data_dir)

            def fail_after_first_table(
                connection: sqlite3.Connection,
                *,
                include_workspaces: bool,
            ) -> None:
                self.assertFalse(include_workspaces)
                connection.execute(store_module._EXPECTED_SOURCE_REVISIONS_SQL)
                raise sqlite3.OperationalError("injected migration DDL failure")

            with patch.object(
                store_module,
                "_create_v2_tables",
                side_effect=fail_after_first_table,
            ):
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    WorkspaceStore.open(data_dir)

            with closing(sqlite3.connect(data_dir / "contextox.sqlite3")) as connection, connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(
                    [
                        (object_type, name, table_name, _normalize_sql(sql))
                        for object_type, name, table_name, sql in _schema_objects(
                            data_dir / "contextox.sqlite3"
                        )
                    ],
                    [
                        (
                            "table",
                            "workspaces",
                            "workspaces",
                            _normalize_sql(V1_WORKSPACES_SQL),
                        )
                    ],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT workspace_id, display_name, created_at FROM workspaces "
                        "ORDER BY created_at, workspace_id"
                    ).fetchall(),
                    rows,
                )

    def test_v2_reopen_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            data_dir = Path(directory)
            store = WorkspaceStore.open(data_dir)
            workspace = store.create_workspace("Reopen")
            before_objects = _schema_objects(store.db_path)
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                before_version = connection.execute("PRAGMA user_version").fetchone()[0]
                before_rows = connection.execute(
                    "SELECT workspace_id, display_name, created_at FROM workspaces"
                ).fetchall()

            reopened = WorkspaceStore.open(data_dir)

            self.assertEqual(reopened.get_workspace(workspace.workspace_id), workspace)
            self.assertEqual(_schema_objects(store.db_path), before_objects)
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    before_version,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT workspace_id, display_name, created_at FROM workspaces"
                    ).fetchall(),
                    before_rows,
                )

    def test_direct_store_name_validation_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            for value in ("", "   ", "x" * 81, "line\nbreak", "\tname", "name\u200b"):
                with self.subTest(value=repr(value)):
                    with self.assertRaises(InvalidWorkspaceNameError):
                        store.create_workspace(value)
            with self.assertRaises(InvalidWorkspaceNameError):
                store.create_workspace(123)

    def test_concurrent_creates_have_distinct_ids(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                created = list(
                    executor.map(
                        store.create_workspace,
                        ["same name"] * 16,
                    )
                )
            ids = {workspace.workspace_id for workspace in created}
            self.assertEqual(len(ids), 16)
            self.assertEqual(len(store.list_workspaces()), 16)

    def test_busy_probe_is_bounded_and_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            lock = sqlite3.connect(store.db_path, timeout=0.1, isolation_level=None)
            try:
                lock.execute("BEGIN EXCLUSIVE")
                with self.assertRaises(WorkspaceStoreBusyError):
                    store.probe_readwrite()
            finally:
                lock.rollback()
                lock.close()

    def test_fail_closed_for_corrupt_unknown_and_unsupported_databases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            root = Path(directory)
            corrupt = root / "corrupt"
            corrupt.mkdir()
            corrupt_db = corrupt / "contextox.sqlite3"
            corrupt_db.write_bytes(b"not a sqlite database")
            with self.assertRaises(WorkspaceStoreUnavailableError):
                WorkspaceStore.open(corrupt)

            for label, version in (("older", 0), ("newer", 3)):
                with self.subTest(label=label):
                    candidate = root / label
                    candidate.mkdir()
                    supported = WorkspaceStore.open(candidate)
                    with closing(sqlite3.connect(supported.db_path)) as connection, connection:
                        connection.execute(f"PRAGMA user_version={version}")
                    with self.assertRaises(WorkspaceSchemaUnsupportedError):
                        WorkspaceStore.open(candidate)

            unknown = root / "unknown"
            unknown.mkdir()
            with closing(sqlite3.connect(unknown / "contextox.sqlite3")) as connection, connection:
                connection.execute("CREATE TABLE unexpected (value TEXT)")
            with self.assertRaises(WorkspaceSchemaUnsupportedError):
                WorkspaceStore.open(unknown)

    def test_fail_closed_for_unknown_v1_table_constraints(self) -> None:
        definitions = (
            "display_name TEXT NOT NULL UNIQUE",
            "display_name TEXT NOT NULL DEFAULT 'default'",
            "display_name TEXT NOT NULL CHECK(length(display_name) > 0)",
        )
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            root = Path(directory)
            for index, display_name_definition in enumerate(definitions):
                with self.subTest(definition=display_name_definition):
                    candidate = root / f"unknown-{index}"
                    candidate.mkdir()
                    connection = sqlite3.connect(candidate / "contextox.sqlite3")
                    with closing(connection), connection:
                        connection.execute(
                            f"""
                            CREATE TABLE workspaces (
                                workspace_id TEXT PRIMARY KEY,
                                {display_name_definition},
                                created_at TEXT NOT NULL
                            )
                            """
                        )
                        connection.execute("PRAGMA user_version=1")
                    with self.assertRaises(WorkspaceSchemaUnsupportedError):
                        WorkspaceStore.open(candidate)

    def test_fail_closed_for_v2_missing_extra_altered_and_newer_schema(self) -> None:
        mutations = (
            (
                "missing",
                lambda connection: connection.execute("DROP TABLE run_events"),
            ),
            (
                "extra",
                lambda connection: connection.execute("CREATE TABLE unexpected (value TEXT)"),
            ),
            (
                "altered",
                lambda connection: connection.execute(
                    "ALTER TABLE runs ADD COLUMN unexpected TEXT"
                ),
            ),
            (
                "newer",
                lambda connection: connection.execute("PRAGMA user_version=3"),
            ),
        )
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            root = Path(directory)
            for label, mutate in mutations:
                with self.subTest(label=label):
                    candidate = root / label
                    candidate.mkdir()
                    WorkspaceStore.open(candidate)
                    connection = sqlite3.connect(candidate / "contextox.sqlite3")
                    with closing(connection), connection:
                        mutate(connection)
                    with self.assertRaises(WorkspaceSchemaUnsupportedError):
                        WorkspaceStore.open(candidate)

    def test_fail_closed_for_v2_extra_index(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            data_dir = Path(directory)
            store = WorkspaceStore.open(data_dir)
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute("CREATE INDEX unexpected_index ON runs(status)")
            with self.assertRaises(WorkspaceSchemaUnsupportedError):
                WorkspaceStore.open(data_dir)

    def test_fail_closed_for_symlink_and_nonregular_database_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            root = Path(directory)
            target_dir = root / "target"
            target_dir.mkdir()
            target_store = WorkspaceStore.open(target_dir)

            symlink_dir = root / "symlink"
            symlink_dir.mkdir()
            (symlink_dir / "contextox.sqlite3").symlink_to(target_store.db_path)
            with self.assertRaises(WorkspaceStoreUnavailableError):
                WorkspaceStore.open(symlink_dir)

            nonregular_dir = root / "nonregular"
            nonregular_dir.mkdir()
            (nonregular_dir / "contextox.sqlite3").mkdir()
            with self.assertRaises(WorkspaceStoreUnavailableError):
                WorkspaceStore.open(nonregular_dir)

    def test_diagnose_never_initializes_missing_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            missing = Path(directory) / "not-created"
            diagnostics = WorkspaceStore.diagnose(missing)
            self.assertEqual(
                [diagnostic.key for diagnostic in diagnostics],
                [
                    "workspace_store_configured",
                    "workspace_store_open",
                    "workspace_store_schema",
                    "workspace_store_readwrite",
                ],
            )
            self.assertFalse(missing.exists())
            self.assertEqual(diagnostics[0].status, "blocked")

    def test_doctor_blocks_v1_without_migrating_and_reports_v2_ready(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            root = Path(directory)
            v1_dir = root / "v1"
            v1_dir.mkdir()
            v1_rows = _write_exact_v1(v1_dir)
            before_objects = _schema_objects(v1_dir / "contextox.sqlite3")

            v1_diagnostics = {
                diagnostic.key: diagnostic
                for diagnostic in WorkspaceStore.diagnose(v1_dir)
            }

            self.assertEqual(v1_diagnostics["workspace_store_schema"].status, "blocked")
            self.assertEqual(v1_diagnostics["workspace_store_readwrite"].status, "not_run")
            with closing(sqlite3.connect(v1_dir / "contextox.sqlite3")) as connection, connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute(
                        "SELECT workspace_id, display_name, created_at FROM workspaces "
                        "ORDER BY created_at, workspace_id"
                    ).fetchall(),
                    v1_rows,
                )
            self.assertEqual(_schema_objects(v1_dir / "contextox.sqlite3"), before_objects)

            v2_dir = root / "v2"
            v2_dir.mkdir()
            WorkspaceStore.open(v2_dir)
            v2_diagnostics = {
                diagnostic.key: diagnostic
                for diagnostic in WorkspaceStore.diagnose(v2_dir)
            }
            self.assertEqual(v2_diagnostics["workspace_store_schema"].status, "ready")
            self.assertEqual(v2_diagnostics["workspace_store_open"].actual, "open")
            self.assertEqual(v2_diagnostics["workspace_store_readwrite"].status, "ready")
            self.assertEqual(v2_diagnostics["workspace_store_schema"].actual, "user_version=2")

    def test_open_and_doctor_fail_closed_for_foreign_key_violations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            data_dir = Path(directory)
            store = WorkspaceStore.open(data_dir)
            workspace_id = store.create_workspace("Broken parent").workspace_id
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute(
                    """
                    INSERT INTO missions
                        (workspace_id, mission_id, created_at, state_version, status,
                         title, goal, completion_criteria_json, scope_notes_json,
                         original_attempt_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        "mission-with-missing-attempt",
                        "2026-01-01T00:00:00+00:00",
                        1,
                        "active",
                        "Title",
                        "Goal",
                        "[]",
                        "[]",
                        "missing-attempt",
                    ),
                )
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertNotEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )

            with self.assertRaises(WorkspaceStoreUnavailableError):
                WorkspaceStore.open(data_dir)

            diagnostics = {
                diagnostic.key: diagnostic
                for diagnostic in WorkspaceStore.diagnose(data_dir)
            }
            self.assertEqual(diagnostics["workspace_store_open"].status, "ready")
            self.assertEqual(diagnostics["workspace_store_schema"].status, "blocked")
            self.assertEqual(
                diagnostics["workspace_store_schema"].actual,
                "foreign_key_violation",
            )
            self.assertEqual(diagnostics["workspace_store_readwrite"].status, "not_run")

    def test_exact_schema_with_wal_mode_is_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode=WAL").fetchone()[0],
                    "wal",
                )
            reopened = WorkspaceStore.open(directory)
            self.assertEqual(reopened.list_workspaces(), [])
            diagnostics = {
                diagnostic.key: diagnostic.status
                for diagnostic in WorkspaceStore.diagnose(directory)
            }
            self.assertEqual(diagnostics["workspace_store_open"], "ready")
            self.assertEqual(diagnostics["workspace_store_schema"], "ready")
            self.assertEqual(diagnostics["workspace_store_readwrite"], "ready")

    def test_diagnose_fails_closed_when_database_disappears_after_entry_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            db_path = store.db_path
            original_connect = store_module.sqlite3.connect
            disappeared = False

            def disappear_then_connect(database: object, *args: object, **kwargs: object):
                nonlocal disappeared
                if not disappeared:
                    disappeared = True
                    db_path.unlink()
                return original_connect(database, *args, **kwargs)

            with patch.object(
                store_module.sqlite3,
                "connect",
                side_effect=disappear_then_connect,
            ):
                diagnostics = WorkspaceStore.diagnose(directory)

            statuses = {diagnostic.key: diagnostic.status for diagnostic in diagnostics}
            self.assertFalse(db_path.exists())
            self.assertEqual(statuses["workspace_store_open"], "blocked")
            self.assertEqual(statuses["workspace_store_schema"], "not_run")
            self.assertEqual(statuses["workspace_store_readwrite"], "not_run")

    def test_diagnose_keeps_read_evidence_when_write_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            WorkspaceStore.open(directory)
            original_connect = store_module._connect_existing_database

            def fail_write_probe(path: Path, *, mode: str = "rw") -> sqlite3.Connection:
                if mode == "rw":
                    raise WorkspaceStoreUnavailableError("simulated write probe failure")
                return original_connect(path, mode="ro")

            with patch.object(
                store_module,
                "_connect_existing_database",
                side_effect=fail_write_probe,
            ):
                diagnostics = WorkspaceStore.diagnose(directory)

            statuses = {diagnostic.key: diagnostic.status for diagnostic in diagnostics}
            self.assertEqual(statuses["workspace_store_open"], "ready")
            self.assertEqual(statuses["workspace_store_schema"], "ready")
            self.assertEqual(statuses["workspace_store_readwrite"], "blocked")

    def test_missing_database_after_entry_validation_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            store = WorkspaceStore.open(directory)
            db_path = store.db_path
            original_connect = store_module.sqlite3.connect
            disappeared = False

            def disappear_then_connect(database: object, *args: object, **kwargs: object):
                nonlocal disappeared
                if not disappeared:
                    disappeared = True
                    db_path.unlink()
                return original_connect(database, *args, **kwargs)

            with patch.object(
                store_module.sqlite3,
                "connect",
                side_effect=disappear_then_connect,
            ):
                with self.assertRaises(WorkspaceStoreError):
                    store.list_workspaces()
            self.assertFalse(db_path.exists())

    def test_failed_initialization_rolls_back_partial_v2_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            db_path = Path(directory) / "contextox.sqlite3"

            def create_one_table_then_fail(connection: sqlite3.Connection) -> None:
                connection.execute(store_module._EXPECTED_V1_WORKSPACES_SQL)
                raise sqlite3.OperationalError("injected initialization DDL failure")

            with patch.object(
                store_module,
                "_create_schema",
                side_effect=create_one_table_then_fail,
            ):
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    WorkspaceStore.open(directory)
            self.assertTrue(db_path.is_file())
            with closing(sqlite3.connect(db_path)) as connection, connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(_schema_objects(db_path), [])

            initialized = WorkspaceStore.open(directory)
            with closing(sqlite3.connect(initialized.db_path)) as connection, connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertTrue(store_module._schema_is_exact(connection))

    def test_source_persistence_supports_four_media_types_restart_and_isolation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-sources-") as directory:
            store = WorkspaceStore.open(directory)
            first_workspace = store.create_workspace("Sources A").workspace_id
            second_workspace = store.create_workspace("Sources B").workspace_id
            fixtures = (
                ("../../orders.csv", "text/csv", b"id,name\n1,Ada\n"),
                ("orders.json", "application/json", b'{"orders":[{"id":1}]}'),
                ("notes.md", "text/markdown", "# Title\n正文".encode()),
                ("notes.txt", "text/plain", "alpha\nbeta".encode()),
            )
            imported = [
                store.import_source_revision(first_workspace, name, media_type, content)
                for name, media_type, content in fixtures
            ]
            for (revision, artifact), (_, _, content) in zip(imported, fixtures, strict=True):
                self.assertEqual(artifact.parse_status, "ready")
                self.assertEqual(revision.sha256, artifact.source_ref.sha256)
                self.assertEqual(_raw_source_path(store, revision).read_bytes(), content)
                self.assertNotIn(revision.original_name, str(_raw_source_path(store, revision)))

            repeated, _ = store.import_source_revision(
                first_workspace, "orders-again.csv", "text/csv", fixtures[0][2]
            )
            self.assertNotEqual(repeated.source_id, imported[0][0].source_id)
            self.assertNotEqual(repeated.revision_id, imported[0][0].revision_id)
            self.assertEqual(repeated.sha256, imported[0][0].sha256)

            restarted = WorkspaceStore.open(directory)
            listed = restarted.list_source_revisions(first_workspace)
            self.assertEqual(
                listed,
                sorted(listed, key=lambda item: (item.observed_at, item.source_id, item.revision_id)),
            )
            self.assertEqual({item.revision_id for item in listed}, {
                *(revision.revision_id for revision, _ in imported),
                repeated.revision_id,
            })
            csv_revision = imported[0][0]
            json_revision = imported[1][0]
            text_revision = imported[3][0]
            self.assertEqual(
                restarted.read_source_excerpt(
                    first_workspace,
                    csv_revision.revision_id,
                    CsvRowsLocator(kind="csv_rows", row_start=1, row_end=1, column=None),
                ).text,
                "1,Ada",
            )
            self.assertEqual(
                restarted.read_source_excerpt(
                    first_workspace,
                    json_revision.revision_id,
                    JsonPointerLocator(kind="json_pointer", pointer="/orders/0/id"),
                ).text,
                "1",
            )
            self.assertEqual(
                restarted.read_source_excerpt(
                    first_workspace,
                    text_revision.revision_id,
                    TextLinesLocator(kind="text_lines", line_start=2, line_end=2),
                ).text,
                "beta",
            )
            with self.assertRaises(SourceNotFoundError):
                restarted.get_source_artifact(second_workspace, csv_revision.revision_id)
            with self.assertRaises(SourceNotFoundError):
                restarted.get_source_artifact(first_workspace, second_workspace)

    def test_source_parser_statuses_and_invalid_metadata_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-source-status-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Source status").workspace_id
            cases = (
                ("partial.csv", "text/csv", b"a,b\n1\n", "partial"),
                (
                    "blocked.csv",
                    "text/csv",
                    (",".join(f"c{index}" for index in range(101)) + "\n").encode(),
                    "blocked",
                ),
                ("failed.txt", "text/plain", b"\xff", "failed"),
            )
            for name, media_type, content, expected in cases:
                with self.subTest(expected=expected):
                    revision, artifact = store.import_source_revision(
                        workspace_id, name, media_type, content
                    )
                    self.assertEqual((revision.parse_status, artifact.parse_status), (expected, expected))
                    self.assertTrue(artifact.issues)
                    self.assertEqual(
                        store.get_source_artifact(workspace_id, revision.revision_id), artifact
                    )

            before = len(store.list_source_revisions(workspace_id))
            with self.assertRaises(SourceInputError):
                store.import_source_revision(
                    workspace_id, "unsupported.bin", "application/octet-stream", b"bytes"
                )
            with self.assertRaises(SourceInputError):
                store.import_source_revision(
                    workspace_id,
                    "oversized.txt",
                    "text/plain",
                    b"x" * (2 * 1024 * 1024 + 1),
                )
            self.assertEqual(len(store.list_source_revisions(workspace_id)), before)

    def test_source_reads_fail_closed_for_tampered_missing_and_unsafe_entries(self) -> None:
        mutations = ("hash", "size", "missing", "directory", "symlink")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="contextox-store-source-entry-"
            ) as directory:
                store = WorkspaceStore.open(directory)
                workspace_id = store.create_workspace("Source entry").workspace_id
                revision, _ = store.import_source_revision(
                    workspace_id, "source.txt", "text/plain", b"alpha\nbeta"
                )
                raw_path = _raw_source_path(store, revision)
                if mutation == "hash":
                    raw_path.write_bytes(b"alpha\nBETA")
                elif mutation == "size":
                    raw_path.write_bytes(b"short")
                elif mutation == "missing":
                    raw_path.unlink()
                elif mutation == "directory":
                    raw_path.unlink()
                    raw_path.mkdir()
                else:
                    target = Path(directory) / "outside-source.bin"
                    target.write_bytes(b"alpha\nbeta")
                    raw_path.unlink()
                    os.symlink(target, raw_path)
                restarted = WorkspaceStore.open(directory)
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    restarted.get_source_artifact(workspace_id, revision.revision_id)
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    restarted.read_source_excerpt(
                        workspace_id,
                        revision.revision_id,
                        TextLinesLocator(kind="text_lines", line_start=1, line_end=1),
                    )

    def test_source_import_failure_cleanup_and_commit_reconciliation(self) -> None:
        definite_failures = (
            ("write", "_write_temp_source_file", WorkspaceStoreUnavailableError()),
            ("begin", "_begin_source_transaction", sqlite3.OperationalError("begin failed")),
            ("insert", "_insert_source_revision_row", sqlite3.OperationalError("insert failed")),
            ("commit", "_commit_source_transaction", sqlite3.OperationalError("commit failed")),
        )
        for label, target, error in definite_failures:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="contextox-store-source-failure-"
            ) as directory:
                store = WorkspaceStore.open(directory)
                workspace_id = store.create_workspace("Failure").workspace_id
                with patch.object(store_module, target, side_effect=error):
                    with self.assertRaises(WorkspaceStoreError):
                        store.import_source_revision(
                            workspace_id, "source.txt", "text/plain", b"content"
                        )
                self.assertEqual(store.list_source_revisions(workspace_id), [])
                self.assertEqual(list((Path(directory) / "sources").rglob("*.bin")), [])

        with tempfile.TemporaryDirectory(prefix="contextox-store-source-replace-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Replace failure").workspace_id
            with patch.object(store_module.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    store.import_source_revision(
                        workspace_id, "source.txt", "text/plain", b"content"
                    )
            self.assertEqual(store.list_source_revisions(workspace_id), [])
            self.assertEqual(list((Path(directory) / "sources").rglob("*.bin")), [])

        with tempfile.TemporaryDirectory(prefix="contextox-store-source-reconcile-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Reconcile success").workspace_id
            real_commit = store_module._commit_source_transaction

            def commit_then_raise(connection: sqlite3.Connection) -> None:
                real_commit(connection)
                raise sqlite3.OperationalError("commit acknowledgement lost")

            with patch.object(
                store_module, "_commit_source_transaction", side_effect=commit_then_raise
            ):
                revision, _ = store.import_source_revision(
                    workspace_id, "source.txt", "text/plain", b"content"
                )
            self.assertEqual(
                [item.revision_id for item in store.list_source_revisions(workspace_id)],
                [revision.revision_id],
            )

        with tempfile.TemporaryDirectory(prefix="contextox-store-source-unknown-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Reconcile unknown").workspace_id
            with patch.object(
                store_module,
                "_commit_source_transaction",
                side_effect=sqlite3.OperationalError("commit unknown"),
            ), patch.object(store, "_reconcile_source_import", return_value="unknown"):
                with self.assertRaises(SourceImportOutcomeUnknownError):
                    store.import_source_revision(
                        workspace_id, "source.txt", "text/plain", b"content"
                    )
            self.assertEqual(store.list_source_revisions(workspace_id), [])
            self.assertEqual(len(list((Path(directory) / "sources").rglob("*.bin"))), 1)

    def test_path2_store_seams_check_workspace_before_not_implemented(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-path2-") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Path 2").workspace_id
            mission_id = "00000000-0000-4000-8000-000000000002"
            run_id = "00000000-0000-4000-8000-000000000003"
            attempt_id = "00000000-0000-4000-8000-000000000004"
            calls = (
                lambda: store.get_run_snapshot(workspace_id, mission_id, run_id),
                lambda: store.get_context_snapshot(workspace_id, mission_id, run_id),
                lambda: store.record_context_manifest(workspace_id, mission_id, run_id, None),
                lambda: store.mark_run_running(workspace_id, mission_id, run_id),
                lambda: store.validate_run_tool_batch(workspace_id, mission_id, run_id, []),
                lambda: store.execute_run_tool(workspace_id, mission_id, run_id, None),
                lambda: store.record_provider_receipt(workspace_id, mission_id, run_id, None),
                lambda: store.append_run_event(workspace_id, mission_id, run_id, None),
                lambda: store.fail_run(workspace_id, mission_id, run_id, "failed", "failure"),
                lambda: store.save_run_final_output(workspace_id, mission_id, run_id, "partial"),
                lambda: store.get_mission_draft_attempt(workspace_id, attempt_id),
                lambda: store.save_mission_draft_result(workspace_id, attempt_id, None, None),
                lambda: store.fail_mission_draft_attempt(workspace_id, attempt_id, "failed", "failure", None),
            )
            for call in calls:
                with self.subTest(call=call):
                    with self.assertRaises(Path2NotImplementedError):
                        call()

            with self.assertRaises(WorkspaceNotFoundError):
                store.get_run_snapshot(
                    "00000000-0000-4000-8000-000000000099", mission_id, run_id
                )
            with self.assertRaises(WorkspaceNotFoundError):
                store.get_run_snapshot("not-a-workspace", mission_id, run_id)

            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                self.assertEqual(
                    [
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                        ).fetchall()
                    ],
                    sorted(EXPECTED_V2_TABLE_NAMES),
                )
            self.assertEqual(len(store.list_workspaces()), 1)
