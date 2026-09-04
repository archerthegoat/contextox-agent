"""Small, fail-closed SQLite store for the N2a Workspace foundation."""

from __future__ import annotations

import sqlite3
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal
from uuid import RFC_4122, UUID, uuid4

from contextox.models import (
    ContextManifestInput,
    ContextPacketManifest,
    ContextSnapshot,
    DefinitionDraft,
    DomainToolCall,
    Mission,
    MissionDraftAttempt,
    MissionDraftPayload,
    ProviderReceipt,
    RunEventEnvelope,
    RunEventInput,
    RunSnapshot,
    RunToolResult,
    TerminalReceipt,
    Workspace,
)


DB_FILENAME = "contextox.sqlite3"
SCHEMA_VERSION = 2
V1_SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 1000


class WorkspaceStoreError(RuntimeError):
    """Base error whose public ``code`` is safe to expose to a local client."""

    code = "workspace_store_unavailable"

    def __init__(self, detail: str = "Workspace store is unavailable.") -> None:
        super().__init__(detail)
        self.detail = detail


class WorkspaceStoreBusyError(WorkspaceStoreError):
    code = "workspace_store_busy"

    def __init__(self) -> None:
        super().__init__("Workspace store is busy.")


class WorkspaceStoreUnavailableError(WorkspaceStoreError):
    code = "workspace_store_unavailable"


class WorkspaceSchemaUnsupportedError(WorkspaceStoreError):
    code = "workspace_schema_unsupported"

    def __init__(self) -> None:
        super().__init__("Workspace store schema is unsupported.")


class WorkspaceCreateOutcomeUnknownError(WorkspaceStoreError):
    code = "workspace_create_outcome_unknown"

    def __init__(self) -> None:
        super().__init__("Workspace creation outcome is unknown.")


class WorkspaceNotFoundError(WorkspaceStoreError):
    code = "workspace_not_found"

    def __init__(self) -> None:
        super().__init__("Workspace was not found.")


class Path2NotImplementedError(WorkspaceStoreError):
    code = "path2_not_implemented"

    def __init__(self) -> None:
        super().__init__("This Path 2 capability is not implemented in W0.2.")


class InvalidWorkspaceNameError(ValueError):
    """Raised when a direct Store caller supplies an invalid display name."""


@dataclass(frozen=True)
class StoreDiagnostic:
    """A bounded, non-mutating doctor result for one Store boundary."""

    key: str
    status: str
    detail: str
    actual: str | None = None
    expected: str | None = None


_EXPECTED_V1_COLUMNS = (
    ("workspace_id", "TEXT", 0, 1),
    ("display_name", "TEXT", 1, 0),
    ("created_at", "TEXT", 1, 0),
)


def normalize_workspace_name(value: object) -> str:
    """Validate and normalize the user-visible Workspace name.

    The contract deliberately uses Unicode code points rather than byte or
    grapheme length.  Control and format characters are rejected before
    trimming so a hidden newline cannot be smuggled through the boundary.
    """

    if not isinstance(value, str):
        raise InvalidWorkspaceNameError("display_name must be a string")
    if any(
        unicodedata.category(character) in {"Cc", "Cf"}
        for character in value
        if character != " "
    ):
        raise InvalidWorkspaceNameError("display_name contains a control character")
    normalized = value.strip()
    if not 1 <= len(normalized) <= 80:
        raise InvalidWorkspaceNameError("display_name must contain 1 to 80 characters")
    return normalized


def canonical_data_dir(data_dir: Path | str) -> Path:
    """Resolve an explicitly configured data directory and require a dir."""

    path = Path(data_dir).expanduser().resolve()
    try:
        is_directory = path.is_dir()
    except OSError as exc:
        raise WorkspaceStoreUnavailableError() from exc
    if not is_directory:
        raise WorkspaceStoreUnavailableError("Workspace data directory is unavailable.")
    return path


def _database_path(data_dir: Path) -> Path:
    return data_dir / DB_FILENAME


def _connect_existing_database(
    path: Path,
    *,
    mode: Literal["ro", "rw"] = "rw",
) -> sqlite3.Connection:
    """Open an already validated database without creating a missing file."""

    return sqlite3.connect(
        f"{path.as_uri()}?mode={mode}",
        uri=True,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )


def _validate_database_entry(path: Path, *, missing_ok: bool = True) -> bool:
    """Validate the exact DB entry without following a symlink.

    ``Path.is_file`` follows symlinks, so use ``lstat`` for the security and
    fail-closed boundary.  The return value says whether the entry exists.
    """

    try:
        entry = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return False
        raise WorkspaceStoreUnavailableError("Workspace database is unavailable.")
    except OSError as exc:
        raise WorkspaceStoreUnavailableError() from exc
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
        raise WorkspaceStoreUnavailableError("Workspace database is unavailable.")
    return True


def _is_busy_error(error: BaseException) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "database is locked",
            "database table is locked",
            "database is busy",
            "busy",
            "locked",
        )
    )


def _store_error(error: BaseException) -> WorkspaceStoreError:
    if isinstance(error, WorkspaceStoreError):
        return error
    if isinstance(error, sqlite3.OperationalError) and _is_busy_error(error):
        return WorkspaceStoreBusyError()
    if isinstance(error, (sqlite3.DatabaseError, OSError)):
        return WorkspaceStoreUnavailableError()
    return WorkspaceStoreUnavailableError()


def _objects(connection: sqlite3.Connection) -> list[tuple[str, str, str, str | None]]:
    return connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()


_EXPECTED_V1_WORKSPACES_SQL = """
CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


_EXPECTED_SOURCE_REVISIONS_SQL = """
CREATE TABLE source_revisions (
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    original_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    effective_time TEXT,
    permission_status TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    PRIMARY KEY (workspace_id, source_id, revision_id),
    UNIQUE (workspace_id, revision_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
)
"""


_EXPECTED_MISSIONS_SQL = """
CREATE TABLE missions (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    completion_criteria_json TEXT NOT NULL,
    scope_notes_json TEXT NOT NULL,
    original_attempt_id TEXT NOT NULL,
    PRIMARY KEY (workspace_id, mission_id),
    FOREIGN KEY (workspace_id, original_attempt_id)
        REFERENCES mission_draft_attempts(workspace_id, attempt_id)
)
"""


_EXPECTED_MISSION_SOURCES_SQL = """
CREATE TABLE mission_sources (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (workspace_id, mission_id, source_id, revision_id),
    UNIQUE (workspace_id, mission_id, ordinal),
    FOREIGN KEY (workspace_id, mission_id)
        REFERENCES missions(workspace_id, mission_id),
    FOREIGN KEY (workspace_id, source_id, revision_id)
        REFERENCES source_revisions(workspace_id, source_id, revision_id)
)
"""


_EXPECTED_MISSION_DRAFT_ATTEMPTS_SQL = """
CREATE TABLE mission_draft_attempts (
    workspace_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    original_input TEXT NOT NULL,
    status TEXT NOT NULL,
    candidate_json TEXT,
    candidate_version INTEGER,
    candidate_sha256 TEXT,
    provider_receipt_id TEXT,
    mission_id TEXT,
    error_code TEXT,
    PRIMARY KEY (workspace_id, attempt_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
    FOREIGN KEY (workspace_id, provider_receipt_id)
        REFERENCES provider_receipts(workspace_id, receipt_id),
    FOREIGN KEY (workspace_id, mission_id)
        REFERENCES missions(workspace_id, mission_id)
)
"""


_EXPECTED_RUNS_SQL = """
CREATE TABLE runs (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    last_sequence INTEGER NOT NULL,
    final_output TEXT,
    error_code TEXT,
    PRIMARY KEY (workspace_id, mission_id, run_id),
    UNIQUE (workspace_id, mission_id, client_request_id),
    FOREIGN KEY (workspace_id, mission_id)
        REFERENCES missions(workspace_id, mission_id)
)
"""


_EXPECTED_RUN_SOURCES_SQL = """
CREATE TABLE run_sources (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (workspace_id, mission_id, run_id, source_id, revision_id),
    UNIQUE (workspace_id, mission_id, run_id, ordinal),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (workspace_id, source_id, revision_id)
        REFERENCES source_revisions(workspace_id, source_id, revision_id)
)
"""


_EXPECTED_MISSION_MESSAGES_SQL = """
CREATE TABLE mission_messages (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    original_attempt_id TEXT,
    run_id TEXT,
    PRIMARY KEY (workspace_id, mission_id, message_id),
    FOREIGN KEY (workspace_id, mission_id)
        REFERENCES missions(workspace_id, mission_id),
    FOREIGN KEY (workspace_id, original_attempt_id)
        REFERENCES mission_draft_attempts(workspace_id, attempt_id),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id)
)
"""


_EXPECTED_RUN_EVENTS_SQL = """
CREATE TABLE run_events (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    public_payload_json TEXT NOT NULL,
    PRIMARY KEY (workspace_id, mission_id, run_id, sequence),
    UNIQUE (workspace_id, mission_id, run_id, event_id),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id)
)
"""


_EXPECTED_CONTEXT_MANIFESTS_SQL = """
CREATE TABLE context_manifests (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    mission_state_version INTEGER NOT NULL,
    turn_index INTEGER NOT NULL,
    draft_id TEXT,
    draft_version INTEGER,
    draft_sha256 TEXT,
    source_refs_json TEXT NOT NULL,
    clarification_ids_json TEXT NOT NULL,
    tool_receipt_ids_json TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    excluded_reasons_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (workspace_id, mission_id, run_id, manifest_id),
    UNIQUE (workspace_id, mission_id, run_id, turn_index),
    UNIQUE (workspace_id, mission_id, run_id, sha256),
    CHECK (
        (draft_id IS NULL AND draft_version IS NULL)
        OR (draft_id IS NOT NULL AND draft_version IS NOT NULL)
    ),
    CHECK (draft_version IS NULL OR draft_version > 0),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (workspace_id, mission_id, draft_id, draft_version)
        REFERENCES definition_drafts(workspace_id, mission_id, draft_id, version)
)
"""


_EXPECTED_CLARIFICATION_REQUESTS_SQL = """
CREATE TABLE clarification_requests (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    clarification_id TEXT NOT NULL,
    draft_version INTEGER NOT NULL,
    draft_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    PRIMARY KEY (workspace_id, mission_id, run_id, clarification_id),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id)
)
"""


_EXPECTED_DEFINITION_DRAFTS_SQL = """
CREATE TABLE definition_drafts (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    semantic_approval TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    relationships_json TEXT NOT NULL,
    unresolved_items_json TEXT NOT NULL,
    PRIMARY KEY (workspace_id, mission_id, draft_id, version),
    UNIQUE (workspace_id, mission_id, version),
    UNIQUE (workspace_id, mission_id, sha256),
    CHECK (version > 0),
    FOREIGN KEY (workspace_id, mission_id)
        REFERENCES missions(workspace_id, mission_id)
)
"""


_EXPECTED_PROVIDER_RECEIPTS_SQL = """
CREATE TABLE provider_receipts (
    workspace_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    attempt_id TEXT,
    mission_id TEXT,
    run_id TEXT,
    turn_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    p0_sha256 TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_hit_tokens INTEGER,
    cache_miss_tokens INTEGER,
    context_manifest_id TEXT,
    context_manifest_sha256 TEXT,
    tool_schema_sha256 TEXT,
    error_code TEXT,
    PRIMARY KEY (workspace_id, receipt_id),
    UNIQUE (workspace_id, attempt_id),
    UNIQUE (workspace_id, mission_id, run_id, turn_index),
    CHECK (turn_index > 0),
    CHECK (
        (attempt_id IS NOT NULL AND mission_id IS NULL AND run_id IS NULL)
        OR (attempt_id IS NULL AND mission_id IS NOT NULL AND run_id IS NOT NULL)
    ),
    FOREIGN KEY (workspace_id, attempt_id)
        REFERENCES mission_draft_attempts(workspace_id, attempt_id),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (workspace_id, mission_id, run_id, context_manifest_id)
        REFERENCES context_manifests(workspace_id, mission_id, run_id, manifest_id)
)
"""


_EXPECTED_TOOL_RECEIPTS_SQL = """
CREATE TABLE tool_receipts (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    call_id TEXT NOT NULL,
    name TEXT NOT NULL,
    arguments_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    error_code TEXT,
    PRIMARY KEY (workspace_id, receipt_id),
    UNIQUE (workspace_id, mission_id, run_id, ordinal),
    UNIQUE (workspace_id, mission_id, run_id, call_id),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id)
)
"""


_EXPECTED_TERMINAL_RECEIPTS_SQL = """
CREATE TABLE terminal_receipts (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    terminal_tool TEXT NOT NULL,
    outcome TEXT NOT NULL,
    draft_id TEXT,
    draft_version INTEGER,
    draft_sha256 TEXT,
    clarification_ids_json TEXT NOT NULL,
    provider_receipt_ids_json TEXT NOT NULL,
    tool_receipt_ids_json TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    PRIMARY KEY (workspace_id, receipt_id),
    UNIQUE (workspace_id, mission_id, run_id),
    CHECK (
        (draft_id IS NULL AND draft_version IS NULL)
        OR (draft_id IS NOT NULL AND draft_version IS NOT NULL)
    ),
    CHECK (draft_version IS NULL OR draft_version > 0),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (workspace_id, mission_id, draft_id, draft_version)
        REFERENCES definition_drafts(workspace_id, mission_id, draft_id, version)
)
"""


# The complete v2 table set is deliberately frozen here.  Source issues and
# the parsed artifact live in source_revisions.artifact_json; separate source
# binding/artifact/issue tables are not part of this checkpoint.
_EXPECTED_V2_TABLES: tuple[tuple[str, str], ...] = (
    ("workspaces", _EXPECTED_V1_WORKSPACES_SQL),
    ("source_revisions", _EXPECTED_SOURCE_REVISIONS_SQL),
    ("mission_draft_attempts", _EXPECTED_MISSION_DRAFT_ATTEMPTS_SQL),
    ("missions", _EXPECTED_MISSIONS_SQL),
    ("mission_sources", _EXPECTED_MISSION_SOURCES_SQL),
    ("mission_messages", _EXPECTED_MISSION_MESSAGES_SQL),
    ("runs", _EXPECTED_RUNS_SQL),
    ("run_sources", _EXPECTED_RUN_SOURCES_SQL),
    ("provider_receipts", _EXPECTED_PROVIDER_RECEIPTS_SQL),
    ("context_manifests", _EXPECTED_CONTEXT_MANIFESTS_SQL),
    ("definition_drafts", _EXPECTED_DEFINITION_DRAFTS_SQL),
    ("clarification_requests", _EXPECTED_CLARIFICATION_REQUESTS_SQL),
    ("tool_receipts", _EXPECTED_TOOL_RECEIPTS_SQL),
    ("terminal_receipts", _EXPECTED_TERMINAL_RECEIPTS_SQL),
    ("run_events", _EXPECTED_RUN_EVENTS_SQL),
)


_EXPECTED_V2_INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "runs_one_active_per_mission",
        "runs",
        """
        CREATE UNIQUE INDEX runs_one_active_per_mission
        ON runs (workspace_id, mission_id)
        WHERE status IN ('queued', 'running')
        """,
    ),
)


def _normalize_schema_sql(sql: object) -> str:
    """Normalize only insignificant whitespace and SQL keyword case."""

    if not isinstance(sql, str):
        return ""
    return " ".join(sql.split()).casefold()


def _schema_is_exact_v1(connection: sqlite3.Connection) -> bool:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != V1_SCHEMA_VERSION:
        return False
    objects = _objects(connection)
    if len(objects) != 1:
        return False
    object_type, name, table_name, sql = objects[0]
    if object_type != "table" or name != "workspaces" or table_name != "workspaces":
        return False
    columns = tuple(
        (
            row[1],  # name
            (row[2] or "").upper(),  # declared type
            row[3],  # not null
            row[5],  # primary-key ordinal
        )
        for row in connection.execute("PRAGMA table_info(workspaces)").fetchall()
    )
    return columns == _EXPECTED_V1_COLUMNS and _normalize_schema_sql(sql) == _normalize_schema_sql(
        _EXPECTED_V1_WORKSPACES_SQL
    )


def _schema_is_exact(connection: sqlite3.Connection) -> bool:
    """Return whether a database is exactly the frozen v2 schema."""

    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        return False
    expected = {
        ("table", name, name): _normalize_schema_sql(sql)
        for name, sql in _EXPECTED_V2_TABLES
    }
    expected.update(
        {
            ("index", name, table_name): _normalize_schema_sql(sql)
            for name, table_name, sql in _EXPECTED_V2_INDEXES
        }
    )
    actual = {
        (object_type, name, table_name): _normalize_schema_sql(sql)
        for object_type, name, table_name, sql in _objects(connection)
    }
    return actual == expected


def _configure_connection(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise _store_error(exc) from exc
    if foreign_keys != 1 or busy_timeout != BUSY_TIMEOUT_MS:
        raise WorkspaceStoreUnavailableError()


def _validate_connection_schema(connection: sqlite3.Connection) -> None:
    try:
        if not _schema_is_exact(connection):
            raise WorkspaceSchemaUnsupportedError()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    except WorkspaceStoreError:
        raise
    except sqlite3.DatabaseError as exc:
        raise _store_error(exc) from exc
    if integrity != "ok" or foreign_key_violations:
        raise WorkspaceStoreUnavailableError()


def _create_v2_tables(
    connection: sqlite3.Connection,
    *,
    include_workspaces: bool,
) -> None:
    tables = _EXPECTED_V2_TABLES if include_workspaces else _EXPECTED_V2_TABLES[1:]
    for _, sql in tables:
        connection.execute(sql)
    for _, _, sql in _EXPECTED_V2_INDEXES:
        connection.execute(sql)


def _create_schema(connection: sqlite3.Connection) -> None:
    """Create a new empty database using the complete frozen v2 schema."""

    _create_v2_tables(connection, include_workspaces=True)
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Add only the v2 tables to an exact v1 database in one transaction."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        _create_v2_tables(connection, include_workspaces=False)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.DatabaseError:
            pass
        raise


class WorkspaceStore:
    """A short-connection, fail-closed SQLite Store for local Path 2 data."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.db_path = _database_path(data_dir)

    @classmethod
    def open(cls, data_dir: Path | str) -> "WorkspaceStore":
        """Open a supported store, atomically initializing a new empty DB."""

        canonical = canonical_data_dir(data_dir)
        store = cls(canonical)
        existing = _validate_database_entry(store.db_path)
        if not existing:
            try:
                with store.db_path.open("xb"):
                    pass
            except FileExistsError:
                # Another local opener won the creation race.  Validate the
                # resulting entry below instead of replacing it.
                existing = _validate_database_entry(store.db_path, missing_ok=False)
            except OSError as exc:
                raise WorkspaceStoreUnavailableError() from exc

        connection: sqlite3.Connection | None = None
        try:
            _validate_database_entry(store.db_path, missing_ok=False)
            connection = _connect_existing_database(store.db_path, mode="rw")
            connection.isolation_level = None
            _configure_connection(connection)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            objects = _objects(connection)
            if version == 0 and not objects:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    _create_schema(connection)
                    connection.commit()
                except sqlite3.OperationalError as exc:
                    try:
                        connection.rollback()
                    except sqlite3.DatabaseError:
                        pass
                    raise _store_error(exc) from exc
                except sqlite3.DatabaseError as exc:
                    try:
                        connection.rollback()
                    except sqlite3.DatabaseError:
                        pass
                    raise _store_error(exc) from exc
            elif version == V1_SCHEMA_VERSION and _schema_is_exact_v1(connection):
                try:
                    _migrate_v1_to_v2(connection)
                except WorkspaceStoreError:
                    raise
                except sqlite3.OperationalError as exc:
                    raise _store_error(exc) from exc
                except sqlite3.DatabaseError as exc:
                    raise _store_error(exc) from exc
            elif version != SCHEMA_VERSION or not _schema_is_exact(connection):
                raise WorkspaceSchemaUnsupportedError()
            _validate_connection_schema(connection)
        except (sqlite3.DatabaseError, OSError) as exc:
            raise _store_error(exc) from exc
        finally:
            if connection is not None:
                connection.close()
        return store

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        _validate_database_entry(self.db_path, missing_ok=False)
        connection: sqlite3.Connection | None = None
        try:
            connection = _connect_existing_database(self.db_path, mode="rw")
            connection.isolation_level = None
            _configure_connection(connection)
            _validate_connection_schema(connection)
            yield connection
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            raise _store_error(exc) from exc
        finally:
            if connection is not None:
                connection.close()

    def create_workspace(self, display_name: object) -> Workspace:
        normalized_name = normalize_workspace_name(display_name)
        workspace_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        created_at_text = created_at.isoformat()
        connection: sqlite3.Connection | None = None
        transaction_started = False
        try:
            connection = self._open_connection()
            connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            connection.execute(
                "INSERT INTO workspaces (workspace_id, display_name, created_at) VALUES (?, ?, ?)",
                (workspace_id, normalized_name, created_at_text),
            )
            try:
                connection.commit()
            except sqlite3.DatabaseError as exc:
                if _is_busy_error(exc):
                    raise WorkspaceStoreBusyError() from exc
                raise WorkspaceCreateOutcomeUnknownError() from exc
            transaction_started = False
        except (WorkspaceStoreError, InvalidWorkspaceNameError):
            if transaction_started and connection is not None:
                try:
                    connection.rollback()
                except sqlite3.DatabaseError:
                    pass
            raise
        except sqlite3.IntegrityError as exc:
            if transaction_started and connection is not None:
                try:
                    connection.rollback()
                except sqlite3.DatabaseError:
                    pass
            raise WorkspaceStoreUnavailableError() from exc
        except (sqlite3.DatabaseError, OSError) as exc:
            if transaction_started and connection is not None:
                try:
                    connection.rollback()
                except sqlite3.DatabaseError:
                    pass
            raise _store_error(exc) from exc
        finally:
            if connection is not None:
                connection.close()
        return Workspace(
            workspace_id=workspace_id,
            display_name=normalized_name,
            created_at=created_at,
        )

    def list_workspaces(self) -> list[Workspace]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT workspace_id, display_name, created_at
                    FROM workspaces
                    ORDER BY created_at ASC, workspace_id ASC
                    """
                ).fetchall()
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            raise _store_error(exc) from exc
        try:
            return [_row_to_workspace(row) for row in rows]
        except (TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT workspace_id, display_name, created_at
                    FROM workspaces
                    WHERE workspace_id = ?
                    """,
                    (workspace_id,),
                ).fetchone()
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            raise _store_error(exc) from exc
        if row is None:
            return None
        try:
            return _row_to_workspace(row)
        except (TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def probe_readwrite(self) -> None:
        """Acquire and roll back a write lock without persistent mutation."""

        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            raise _store_error(exc) from exc

    @staticmethod
    def _is_canonical_workspace_id(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            parsed = UUID(value)
        except (AttributeError, ValueError):
            return False
        return parsed.version == 4 and parsed.variant == RFC_4122 and str(parsed) == value

    def _require_path2_workspace(self, workspace_id: str) -> None:
        """Validate the Workspace boundary before exposing a not-implemented seam."""

        if not self._is_canonical_workspace_id(workspace_id):
            raise WorkspaceNotFoundError()
        if self.get_workspace(workspace_id) is None:
            raise WorkspaceNotFoundError()

    def get_run_snapshot(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def get_context_snapshot(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
    ) -> ContextSnapshot:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def record_context_manifest(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        manifest: ContextManifestInput,
    ) -> ContextPacketManifest:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def mark_run_running(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def validate_run_tool_batch(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        calls: list[DomainToolCall],
    ) -> None:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def execute_run_tool(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        call: DomainToolCall,
    ) -> RunToolResult:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def record_provider_receipt(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        receipt: ProviderReceipt,
    ) -> ProviderReceipt:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def append_run_event(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        event: RunEventInput,
    ) -> RunEventEnvelope:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def fail_run(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        status: Literal["blocked", "failed", "partial"],
        code: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def save_run_final_output(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        content: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def get_mission_draft_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> MissionDraftAttempt:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def save_mission_draft_result(
        self,
        workspace_id: str,
        attempt_id: str,
        candidate: MissionDraftPayload,
        receipt: ProviderReceipt,
    ) -> MissionDraftAttempt:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def fail_mission_draft_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
        status: Literal["blocked", "failed", "cancelled"],
        code: str,
        receipt: ProviderReceipt | None,
    ) -> MissionDraftAttempt:
        self._require_path2_workspace(workspace_id)
        raise Path2NotImplementedError()

    def _open_connection(self) -> sqlite3.Connection:
        _validate_database_entry(self.db_path, missing_ok=False)
        connection: sqlite3.Connection | None = None
        try:
            connection = _connect_existing_database(self.db_path, mode="rw")
            connection.isolation_level = None
            _configure_connection(connection)
            _validate_connection_schema(connection)
            return connection
        except WorkspaceStoreError:
            if connection is not None:
                connection.close()
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            if connection is not None:
                connection.close()
            raise _store_error(exc) from exc

    @classmethod
    def diagnose(cls, data_dir: Path | str) -> list[StoreDiagnostic]:
        """Inspect Store readiness without creating a directory or database."""

        try:
            canonical = Path(data_dir).expanduser().resolve()
        except OSError:
            canonical = Path(data_dir)
        try:
            configured = canonical.is_dir()
        except OSError:
            configured = False
        if not configured:
            unavailable = StoreDiagnostic(
                key="workspace_store_open",
                status="blocked",
                detail="The configured Workspace data directory is unavailable.",
                actual="missing_or_not_directory",
                expected="directory",
            )
            return [
                StoreDiagnostic(
                    key="workspace_store_configured",
                    status="blocked",
                    detail="The configured Workspace data directory is unavailable.",
                    actual="missing_or_not_directory",
                    expected="directory",
                ),
                unavailable,
                StoreDiagnostic(
                    key="workspace_store_schema",
                    status="not_run",
                    detail="Schema inspection requires an available database.",
                ),
                StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="not_run",
                    detail="Read/write probe requires an available database.",
                ),
            ]

        db_path = _database_path(canonical)
        configured_check = StoreDiagnostic(
            key="workspace_store_configured",
            status="ready",
            detail="The configured Workspace data directory is available.",
            actual="directory",
            expected="directory",
        )
        try:
            exists = _validate_database_entry(db_path)
        except WorkspaceStoreError:
            return [
                configured_check,
                StoreDiagnostic(
                    key="workspace_store_open",
                    status="blocked",
                    detail="The Workspace database is unavailable.",
                    actual="invalid_database_entry",
                    expected="regular_file",
                ),
                StoreDiagnostic(
                    key="workspace_store_schema",
                    status="not_run",
                    detail="Schema inspection requires an open database.",
                ),
                StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="not_run",
                    detail="Read/write probe requires an open database.",
                ),
            ]
        if not exists:
            return [
                configured_check,
                StoreDiagnostic(
                    key="workspace_store_open",
                    status="not_run",
                    detail="The Workspace database is not initialized; doctor will not create it.",
                    actual="absent",
                    expected="regular_file",
                ),
                StoreDiagnostic(
                    key="workspace_store_schema",
                    status="not_run",
                    detail="Schema inspection requires an initialized database.",
                ),
                StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="not_run",
                    detail="Read/write probe requires an initialized database.",
                ),
            ]

        read_connection: sqlite3.Connection | None = None
        open_check = StoreDiagnostic(
            key="workspace_store_open",
            status="ready",
            detail="The Workspace database can be opened read-only for inspection.",
            actual="open",
            expected="open",
        )
        schema_check: StoreDiagnostic
        readwrite_check: StoreDiagnostic
        try:
            read_connection = _connect_existing_database(db_path, mode="ro")
            read_connection.isolation_level = None
            _configure_connection(read_connection)
            version = read_connection.execute("PRAGMA user_version").fetchone()[0]
            objects = _objects(read_connection)
            if version != SCHEMA_VERSION or not _schema_is_exact(read_connection):
                schema_check = StoreDiagnostic(
                    key="workspace_store_schema",
                    status="blocked",
                    detail="The Workspace database schema is unsupported.",
                    actual=f"user_version={version}; objects={len(objects)}",
                    expected=f"user_version={SCHEMA_VERSION}; exact v2 table set",
                )
                readwrite_check = StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="not_run",
                    detail="Read/write probe requires a supported schema.",
                )
            else:
                integrity = read_connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise WorkspaceStoreUnavailableError()
                foreign_key_violations = read_connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if foreign_key_violations:
                    schema_check = StoreDiagnostic(
                        key="workspace_store_schema",
                        status="blocked",
                        detail="The Workspace database has invalid parent references.",
                        actual="foreign_key_violation",
                        expected="no foreign key violations",
                    )
                    readwrite_check = StoreDiagnostic(
                        key="workspace_store_readwrite",
                        status="not_run",
                        detail="Read/write probe requires valid parent references.",
                    )
                else:
                    schema_check = StoreDiagnostic(
                        key="workspace_store_schema",
                        status="ready",
                        detail="The Workspace database uses schema version 2.",
                        actual=f"user_version={SCHEMA_VERSION}",
                        expected=f"user_version={SCHEMA_VERSION}; exact v2 table set",
                    )
        except WorkspaceSchemaUnsupportedError:
            schema_check = StoreDiagnostic(
                key="workspace_store_schema",
                status="blocked",
                detail="The Workspace database schema is unsupported.",
                actual="unsupported",
                expected=f"user_version={SCHEMA_VERSION}; exact v2 table set",
            )
            readwrite_check = StoreDiagnostic(
                key="workspace_store_readwrite",
                status="not_run",
                detail="Read/write probe requires a supported schema.",
            )
        except WorkspaceStoreBusyError:
            open_check = StoreDiagnostic(
                key="workspace_store_open",
                status="blocked",
                detail="The Workspace database is busy.",
                actual="busy",
                expected="open",
            )
            schema_check = StoreDiagnostic(
                key="workspace_store_schema",
                status="not_run",
                detail="Schema inspection could not complete while the database was busy.",
            )
            readwrite_check = StoreDiagnostic(
                key="workspace_store_readwrite",
                status="blocked",
                detail="The Workspace database is busy.",
                actual="busy",
                expected="begin_immediate_rollback",
            )
        except WorkspaceStoreError:
            open_check = StoreDiagnostic(
                key="workspace_store_open",
                status="blocked",
                detail="The Workspace database is unavailable.",
                actual="unreadable",
                expected="open",
            )
            schema_check = StoreDiagnostic(
                key="workspace_store_schema",
                status="not_run",
                detail="Schema inspection requires an open database.",
            )
            readwrite_check = StoreDiagnostic(
                key="workspace_store_readwrite",
                status="not_run",
                detail="Read/write probe requires an open database.",
            )
        except (sqlite3.DatabaseError, OSError) as exc:
            mapped = _store_error(exc)
            if isinstance(mapped, WorkspaceStoreBusyError):
                open_check = StoreDiagnostic(
                    key="workspace_store_open",
                    status="blocked",
                    detail="The Workspace database is busy.",
                    actual="busy",
                    expected="open",
                )
                readwrite_check = StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="blocked",
                    detail="The Workspace database is busy.",
                    actual="busy",
                    expected="begin_immediate_rollback",
                )
            else:
                open_check = StoreDiagnostic(
                    key="workspace_store_open",
                    status="blocked",
                    detail="The Workspace database is unavailable.",
                    actual="unreadable",
                    expected="open",
                )
                readwrite_check = StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="not_run",
                    detail="Read/write probe requires an open database.",
                )
            schema_check = StoreDiagnostic(
                key="workspace_store_schema",
                status="not_run",
                detail="Schema inspection requires an open database.",
            )
        finally:
            if read_connection is not None:
                read_connection.close()

        if schema_check.status == "ready":
            write_connection: sqlite3.Connection | None = None
            try:
                write_connection = _connect_existing_database(db_path, mode="rw")
                write_connection.isolation_level = None
                _configure_connection(write_connection)
                write_connection.execute("BEGIN IMMEDIATE")
                write_connection.rollback()
                readwrite_check = StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="ready",
                    detail="A bounded write-lock probe succeeded and rolled back.",
                    actual="begin_immediate_rollback",
                    expected="begin_immediate_rollback",
                )
            except WorkspaceStoreBusyError:
                readwrite_check = StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="blocked",
                    detail="The Workspace database is busy.",
                    actual="busy",
                    expected="begin_immediate_rollback",
                )
            except WorkspaceStoreError:
                readwrite_check = StoreDiagnostic(
                    key="workspace_store_readwrite",
                    status="blocked",
                    detail="The Workspace database is unavailable for writing.",
                    actual="unavailable",
                    expected="begin_immediate_rollback",
                )
            except (sqlite3.DatabaseError, OSError) as exc:
                mapped = _store_error(exc)
                if isinstance(mapped, WorkspaceStoreBusyError):
                    readwrite_check = StoreDiagnostic(
                        key="workspace_store_readwrite",
                        status="blocked",
                        detail="The Workspace database is busy.",
                        actual="busy",
                        expected="begin_immediate_rollback",
                    )
                else:
                    readwrite_check = StoreDiagnostic(
                        key="workspace_store_readwrite",
                        status="blocked",
                        detail="The Workspace database is unavailable for writing.",
                        actual="unavailable",
                        expected="begin_immediate_rollback",
                    )
            finally:
                if write_connection is not None:
                    write_connection.close()
        return [configured_check, open_check, schema_check, readwrite_check]


def _parse_created_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc
    if parsed.tzinfo is None:
        raise WorkspaceStoreUnavailableError()
    return parsed


def _row_to_workspace(row: tuple[object, object, object]) -> Workspace:
    return Workspace(
        workspace_id=row[0],
        display_name=row[1],
        created_at=_parse_created_at(row[2]),
    )
