import concurrent.futures
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import contextox.store as store_module
from contextox.store import (
    InvalidWorkspaceNameError,
    Path2NotImplementedError,
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
                connection.execute(
                    """
                    INSERT INTO mission_draft_attempts
                        (workspace_id, attempt_id, created_at, original_input, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (workspace_id, attempt_id, "2026-01-01T00:00:00+00:00", "input", "ready"),
                )
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
                run_values = (
                    workspace_id,
                    mission_id,
                    run_id,
                    "client-1",
                    "2026-01-01T00:00:00+00:00",
                    "queued",
                    "{}",
                    0,
                )
                connection.execute(
                    """
                    INSERT INTO runs
                        (workspace_id, mission_id, run_id, client_request_id, created_at,
                         status, budget_json, last_sequence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    run_values,
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
                        "00000000-0000-4000-8000-000000000014",
                        "client-3",
                        "2026-01-01T00:00:00+00:00",
                        "failed",
                        "{}",
                        0,
                    ),
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
