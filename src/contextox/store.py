"""Small, fail-closed SQLite store for local Path 2 state."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal
from uuid import RFC_4122, UUID, uuid4

from pydantic import ValidationError

from contextox.models import (
    ContextManifestInput,
    ContextPacketManifest,
    ContextSnapshot,
    DefinitionDraft,
    DomainToolCall,
    EvidenceLocator,
    Mission,
    MissionDraftAttempt,
    MissionDraftPayload,
    ProviderReceipt,
    RunEventEnvelope,
    RunEventInput,
    RunSnapshot,
    RunToolResult,
    SourceArtifact,
    SourceExcerpt,
    SourceRevision,
    TerminalReceipt,
    Workspace,
)
from contextox.sources import (
    MAX_FILE_BYTES,
    PARSER_VERSION,
    SourceInputError,
    parse_source,
    read_source_fragment,
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


class SourceNotFoundError(WorkspaceStoreError):
    code = "source_not_found"

    def __init__(self) -> None:
        super().__init__("Source revision was not found.")


class SourceImportOutcomeUnknownError(WorkspaceStoreError):
    code = "source_import_outcome_unknown"

    def __init__(self) -> None:
        super().__init__("Source import outcome is unknown.")


class Path2NotImplementedError(WorkspaceStoreError):
    code = "path2_not_implemented"

    def __init__(self) -> None:
        super().__init__("This Path 2 capability is not implemented.")


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


def _canonical_json(value: SourceArtifact) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _require_directory(path: Path, *, create: bool) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        if not create:
            raise WorkspaceStoreUnavailableError("Source storage is unavailable.")
        try:
            path.mkdir(mode=0o700)
            entry = path.lstat()
        except (FileExistsError, OSError) as exc:
            if isinstance(exc, FileExistsError):
                try:
                    entry = path.lstat()
                except OSError as nested:
                    raise WorkspaceStoreUnavailableError(
                        "Source storage is unavailable."
                    ) from nested
            else:
                raise WorkspaceStoreUnavailableError("Source storage is unavailable.") from exc
    except OSError as exc:
        raise WorkspaceStoreUnavailableError("Source storage is unavailable.") from exc
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise WorkspaceStoreUnavailableError("Source storage is unavailable.")


def _source_directory(data_dir: Path, workspace_id: str, source_id: str) -> Path:
    _require_directory(data_dir, create=False)
    sources_dir = data_dir / "sources"
    workspace_dir = sources_dir / workspace_id
    source_dir = workspace_dir / source_id
    for path in (sources_dir, workspace_dir, source_dir):
        _require_directory(path, create=True)
    return source_dir


def _source_path(data_dir: Path, revision: SourceRevision) -> Path:
    return (
        data_dir
        / "sources"
        / revision.workspace_id
        / revision.source_id
        / f"{revision.revision_id}.bin"
    )


def _write_temp_source_file(directory: Path, revision_id: str, content: bytes) -> Path:
    temporary = directory / f".{revision_id}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        raise WorkspaceStoreUnavailableError("Source storage is unavailable.") from exc
    return temporary


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
            raise WorkspaceStoreUnavailableError("Source storage is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_validated_source_file(path: Path, revision: SourceRevision) -> bytes:
    try:
        entry = path.lstat()
    except OSError as exc:
        raise WorkspaceStoreUnavailableError("Source storage is unavailable.") from exc
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISREG(entry.st_mode)
        or entry.st_size != revision.byte_size
    ):
        raise WorkspaceStoreUnavailableError("Source storage is unavailable.")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != entry.st_dev
            or opened.st_ino != entry.st_ino
            or opened.st_size != revision.byte_size
        ):
            raise WorkspaceStoreUnavailableError("Source storage is unavailable.")
        chunks: list[bytes] = []
        remaining = revision.byte_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
    except WorkspaceStoreError:
        raise
    except OSError as exc:
        raise WorkspaceStoreUnavailableError("Source storage is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(content) != revision.byte_size or hashlib.sha256(content).hexdigest() != revision.sha256:
        raise WorkspaceStoreUnavailableError("Source storage is unavailable.")
    return content


def _remove_owned_source_file(path: Path, revision: SourceRevision) -> None:
    try:
        _read_validated_source_file(path, revision)
        path.unlink()
        _fsync_directory(path.parent)
    except (WorkspaceStoreError, OSError):
        pass


def _begin_source_transaction(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")


def _insert_source_revision_row(
    connection: sqlite3.Connection,
    revision: SourceRevision,
    artifact_json: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_revisions
            (workspace_id, source_id, revision_id, original_name, media_type,
             byte_size, sha256, observed_at, effective_time, permission_status,
             parse_status, parser_version, artifact_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision.workspace_id,
            revision.source_id,
            revision.revision_id,
            revision.original_name,
            revision.media_type,
            revision.byte_size,
            revision.sha256,
            revision.observed_at.isoformat(),
            revision.effective_time.isoformat() if revision.effective_time else None,
            revision.permission_status,
            revision.parse_status,
            revision.parser_version,
            artifact_json,
        ),
    )


def _commit_source_transaction(connection: sqlite3.Connection) -> None:
    connection.commit()


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
    UNIQUE (workspace_id, source_id, revision_id, sha256),
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
    FOREIGN KEY (workspace_id, source_id, revision_id, sha256)
        REFERENCES source_revisions(workspace_id, source_id, revision_id, sha256)
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
    FOREIGN KEY (workspace_id, source_id, revision_id, sha256)
        REFERENCES source_revisions(workspace_id, source_id, revision_id, sha256)
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
    UNIQUE (workspace_id, mission_id, run_id, manifest_id, sha256),
    CHECK (
        (draft_id IS NULL AND draft_version IS NULL AND draft_sha256 IS NULL)
        OR (
            draft_id IS NOT NULL
            AND draft_version IS NOT NULL
            AND draft_sha256 IS NOT NULL
        )
    ),
    CHECK (draft_version IS NULL OR draft_version > 0),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (workspace_id, mission_id, draft_id, draft_version, draft_sha256)
        REFERENCES definition_drafts(
            workspace_id, mission_id, draft_id, version, sha256
        )
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
    CHECK (draft_version > 0),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (workspace_id, mission_id, draft_version, draft_sha256)
        REFERENCES definition_drafts(workspace_id, mission_id, version, sha256)
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
    UNIQUE (workspace_id, mission_id, draft_id, version, sha256),
    UNIQUE (workspace_id, mission_id, version, sha256),
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
        (
            attempt_id IS NOT NULL
            AND mission_id IS NULL
            AND run_id IS NULL
            AND context_manifest_id IS NULL
            AND context_manifest_sha256 IS NULL
            AND tool_schema_sha256 IS NULL
            AND turn_index = 1
        )
        OR (
            attempt_id IS NULL
            AND mission_id IS NOT NULL
            AND run_id IS NOT NULL
            AND context_manifest_id IS NOT NULL
            AND context_manifest_sha256 IS NOT NULL
            AND tool_schema_sha256 IS NOT NULL
        )
    ),
    FOREIGN KEY (workspace_id, attempt_id)
        REFERENCES mission_draft_attempts(workspace_id, attempt_id),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (
        workspace_id, mission_id, run_id,
        context_manifest_id, context_manifest_sha256
    ) REFERENCES context_manifests(
        workspace_id, mission_id, run_id, manifest_id, sha256
    )
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
        (draft_id IS NULL AND draft_version IS NULL AND draft_sha256 IS NULL)
        OR (
            draft_id IS NOT NULL
            AND draft_version IS NOT NULL
            AND draft_sha256 IS NOT NULL
        )
    ),
    CHECK (draft_version IS NULL OR draft_version > 0),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (workspace_id, mission_id, draft_id, draft_version, draft_sha256)
        REFERENCES definition_drafts(
            workspace_id, mission_id, draft_id, version, sha256
        )
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
        """Validate the Workspace boundary for a Path 2 operation."""

        if not self._is_canonical_workspace_id(workspace_id):
            raise WorkspaceNotFoundError()
        if self.get_workspace(workspace_id) is None:
            raise WorkspaceNotFoundError()

    def import_source_revision(
        self,
        workspace_id: str,
        original_name: str,
        media_type: str,
        content: bytes,
    ) -> tuple[SourceRevision, SourceArtifact]:
        """Persist one immutable, parser-backed Source revision."""

        self._require_path2_workspace(workspace_id)
        if not isinstance(content, bytes):
            raise SourceInputError("source_content_type_invalid")
        if len(content) > MAX_FILE_BYTES:
            raise SourceInputError("source_file_too_large")

        source_id = str(uuid4())
        revision_id = str(uuid4())
        observed_at = datetime.now(timezone.utc)
        try:
            pending = SourceRevision(
                workspace_id=workspace_id,
                source_id=source_id,
                revision_id=revision_id,
                original_name=original_name,
                media_type=media_type,
                byte_size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                observed_at=observed_at,
                effective_time=None,
                permission_status="read_allowed",
                parse_status="pending",
                parser_version=PARSER_VERSION,
            )
        except ValidationError as exc:
            raise SourceInputError("source_metadata_invalid") from exc
        artifact = parse_source(pending, content)
        revision = pending.model_copy(update={"parse_status": artifact.parse_status})
        artifact_json = _canonical_json(artifact)

        directory = _source_directory(self.data_dir, workspace_id, source_id)
        final_path = directory / f"{revision_id}.bin"
        try:
            final_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise WorkspaceStoreUnavailableError("Source storage is unavailable.") from exc
        else:
            raise WorkspaceStoreUnavailableError("Source storage is unavailable.")

        temporary = _write_temp_source_file(directory, revision_id, content)
        try:
            try:
                os.replace(temporary, final_path)
            except OSError as exc:
                try:
                    _read_validated_source_file(final_path, revision)
                except WorkspaceStoreError:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
                    _remove_owned_source_file(final_path, revision)
                    raise WorkspaceStoreUnavailableError(
                        "Source storage is unavailable."
                    ) from exc
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    raise WorkspaceStoreUnavailableError(
                        "Source storage is unavailable."
                    ) from cleanup_error
            _fsync_directory(directory)
            _read_validated_source_file(final_path, revision)
        except WorkspaceStoreError:
            try:
                temporary.unlink()
            except OSError:
                pass
            _remove_owned_source_file(final_path, revision)
            raise

        connection: sqlite3.Connection | None = None
        transaction_started = False
        commit_error: BaseException | None = None
        try:
            connection = self._open_connection()
            _begin_source_transaction(connection)
            transaction_started = True
            workspace_exists = connection.execute(
                "SELECT 1 FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if workspace_exists is None:
                raise WorkspaceNotFoundError()
            _insert_source_revision_row(connection, revision, artifact_json)
            try:
                _commit_source_transaction(connection)
            except (sqlite3.DatabaseError, OSError) as exc:
                commit_error = exc
            else:
                transaction_started = False
        except WorkspaceStoreError:
            if transaction_started and connection is not None:
                try:
                    connection.rollback()
                except sqlite3.DatabaseError:
                    pass
            _remove_owned_source_file(final_path, revision)
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            if transaction_started and connection is not None:
                try:
                    connection.rollback()
                except sqlite3.DatabaseError:
                    pass
            _remove_owned_source_file(final_path, revision)
            raise _store_error(exc) from exc
        finally:
            if connection is not None:
                connection.close()

        if commit_error is not None:
            outcome = self._reconcile_source_import(revision, artifact_json, final_path)
            if outcome == "committed":
                return revision, artifact
            if outcome == "absent":
                _remove_owned_source_file(final_path, revision)
                raise _store_error(commit_error) from commit_error
            raise SourceImportOutcomeUnknownError() from commit_error

        _read_validated_source_file(final_path, revision)
        return revision, artifact

    def _reconcile_source_import(
        self,
        revision: SourceRevision,
        artifact_json: str,
        final_path: Path,
    ) -> Literal["committed", "absent", "unknown"]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT workspace_id, source_id, revision_id, original_name,
                           media_type, byte_size, sha256, observed_at,
                           effective_time, permission_status, parse_status,
                           parser_version, artifact_json
                    FROM source_revisions
                    WHERE workspace_id = ? AND revision_id = ?
                    """,
                    (revision.workspace_id, revision.revision_id),
                ).fetchone()
            if row is None:
                return "absent"
            stored_revision, stored_artifact, stored_json = _row_to_source(row)
            if (
                stored_revision != revision
                or stored_artifact != SourceArtifact.model_validate_json(artifact_json)
                or stored_json != artifact_json
            ):
                return "unknown"
            _read_validated_source_file(final_path, revision)
            return "committed"
        except (WorkspaceStoreError, ValidationError, ValueError, TypeError):
            return "unknown"

    def list_source_revisions(self, workspace_id: str) -> list[SourceRevision]:
        self._require_path2_workspace(workspace_id)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT workspace_id, source_id, revision_id, original_name,
                           media_type, byte_size, sha256, observed_at,
                           effective_time, permission_status, parse_status,
                           parser_version, artifact_json
                    FROM source_revisions
                    WHERE workspace_id = ?
                    ORDER BY observed_at ASC, source_id ASC, revision_id ASC
                    """,
                    (workspace_id,),
                ).fetchall()
            return [_row_to_source(row)[0] for row in rows]
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def _get_source(
        self,
        workspace_id: str,
        revision_id: str,
    ) -> tuple[SourceRevision, SourceArtifact, bytes]:
        self._require_path2_workspace(workspace_id)
        if not self._is_canonical_workspace_id(revision_id):
            raise SourceNotFoundError()
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT workspace_id, source_id, revision_id, original_name,
                           media_type, byte_size, sha256, observed_at,
                           effective_time, permission_status, parse_status,
                           parser_version, artifact_json
                    FROM source_revisions
                    WHERE workspace_id = ? AND revision_id = ?
                    """,
                    (workspace_id, revision_id),
                ).fetchone()
            if row is None:
                raise SourceNotFoundError()
            revision, artifact, _ = _row_to_source(row)
            content = _read_validated_source_file(_source_path(self.data_dir, revision), revision)
            return revision, artifact, content
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def get_source_artifact(
        self,
        workspace_id: str,
        revision_id: str,
    ) -> SourceArtifact:
        _, artifact, _ = self._get_source(workspace_id, revision_id)
        return artifact

    def read_source_excerpt(
        self,
        workspace_id: str,
        revision_id: str,
        locator: EvidenceLocator,
    ) -> SourceExcerpt:
        revision, _, content = self._get_source(workspace_id, revision_id)
        return read_source_fragment(revision, content, locator)

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


def _row_to_source(
    row: tuple[object, ...],
) -> tuple[SourceRevision, SourceArtifact, str]:
    if len(row) != 13 or not isinstance(row[12], str):
        raise WorkspaceStoreUnavailableError()
    revision = SourceRevision(
        workspace_id=row[0],
        source_id=row[1],
        revision_id=row[2],
        original_name=row[3],
        media_type=row[4],
        byte_size=row[5],
        sha256=row[6],
        observed_at=_parse_created_at(row[7]),
        effective_time=_parse_created_at(row[8]) if row[8] is not None else None,
        permission_status=row[9],
        parse_status=row[10],
        parser_version=row[11],
    )
    artifact = SourceArtifact.model_validate_json(row[12])
    identity = (
        revision.workspace_id,
        revision.source_id,
        revision.revision_id,
        revision.sha256,
    )
    artifact_identity = (
        artifact.source_ref.workspace_id,
        artifact.source_ref.source_id,
        artifact.source_ref.revision_id,
        artifact.source_ref.sha256,
    )
    if (
        identity != artifact_identity
        or revision.parser_version != artifact.parser_version
        or revision.parse_status != artifact.parse_status
        or row[12] != _canonical_json(artifact)
    ):
        raise WorkspaceStoreUnavailableError()
    return revision, artifact, row[12]
