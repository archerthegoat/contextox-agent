import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from contextox.store import (
    InvalidWorkspaceNameError,
    WorkspaceSchemaUnsupportedError,
    WorkspaceStore,
    WorkspaceStoreBusyError,
    WorkspaceStoreUnavailableError,
)


class StoreTests(unittest.TestCase):
    def test_initializes_exact_schema_and_persists_after_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextox-store-") as directory:
            data_dir = Path(directory)
            store = WorkspaceStore.open(data_dir)
            self.assertEqual(store.db_path, data_dir.resolve() / "contextox.sqlite3")
            self.assertEqual(store.list_workspaces(), [])
            with sqlite3.connect(store.db_path) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                self.assertEqual(
                    connection.execute("PRAGMA table_info(workspaces)").fetchall(),
                    [
                        (0, "workspace_id", "TEXT", 0, None, 1),
                        (1, "display_name", "TEXT", 1, None, 0),
                        (2, "created_at", "TEXT", 1, None, 0),
                    ],
                )
            first = store.create_workspace("  Client definition  ")
            second = store.create_workspace("Client definition")
            self.assertNotEqual(first.workspace_id, second.workspace_id)
            self.assertEqual(first.display_name, second.display_name, "Client definition")
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

            for label, version in (("older", 0), ("newer", 2)):
                with self.subTest(label=label):
                    candidate = root / label
                    candidate.mkdir()
                    supported = WorkspaceStore.open(candidate)
                    with sqlite3.connect(supported.db_path) as connection:
                        connection.execute(f"PRAGMA user_version={version}")
                    with self.assertRaises(WorkspaceSchemaUnsupportedError):
                        WorkspaceStore.open(candidate)

            unknown = root / "unknown"
            unknown.mkdir()
            with sqlite3.connect(unknown / "contextox.sqlite3") as connection:
                connection.execute("CREATE TABLE unexpected (value TEXT)")
            with self.assertRaises(WorkspaceSchemaUnsupportedError):
                WorkspaceStore.open(unknown)

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
