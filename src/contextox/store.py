"""Small, fail-closed SQLite store for local Path 2 state."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import stat
import unicodedata
from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from uuid import RFC_4122, UUID, uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from contextox.models import (
    TaskMessage, TaskMessagePage, TaskRunSummary, TaskRunPage,
    TaskMessageSendRequest, TaskMessageSendReceipt, MessageContext, MessageHistoryRef,
    ClarificationRequest,
    ContextManifestInput,
    ContextPacketManifest,
    ContextSnapshot,
    DefinitionDraft,
    DomainRejection,
    DomainToolCall,
    EvidenceLocator,
    EvidenceRef,
    Mission,
    MissionDraftAttempt,
    MissionDraftPayload,
    MissionDraftConfirmRequest,
    MissionSnapshot,
    ProviderReceipt,
    RelationshipProfile,
    RunBudget,
    RunEventEnvelope,
    RunEventInput,
    RunSnapshot,
    RunStartRequest,
    RunToolResult,
    SourceArtifact,
    SourceExcerpt,
    SourceIdentity,
    SourceRevision,
    TableProfile,
    TerminalReceipt,
    ToolReceipt,
    Workspace,
    canonical_sha256,
)
from contextox.sources import (
    MAX_FILE_BYTES,
    PARSER_VERSION,
    SourceInputError,
    inspect_relationship,
    parse_source,
    read_source_fragment,
)


DB_FILENAME = "contextox.sqlite3"
SCHEMA_VERSION = 4
V3_SCHEMA_VERSION = 3
V1_SCHEMA_VERSION = 1
V2_SCHEMA_VERSION = 2
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


class Path2StateError(WorkspaceStoreError):
    """A bounded lifecycle error with a contract-safe code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("The requested Path 2 operation could not be applied.")


class MissionDraftAttemptNotFoundError(Path2StateError):
    def __init__(self) -> None:
        super().__init__("mission_draft_attempt_not_found")


class MissionNotFoundError(Path2StateError):
    def __init__(self) -> None:
        super().__init__("mission_not_found")


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


def _canonical_json(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    def jsonable(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, list):
            return [jsonable(child) for child in item]
        if isinstance(item, tuple):
            return [jsonable(child) for child in item]
        if isinstance(item, dict):
            return {str(key): jsonable(child) for key, child in item.items()}
        return item

    payload = jsonable(value)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_value(value: object) -> object:
    if not isinstance(value, str):
        raise WorkspaceStoreUnavailableError()
    decoded = json.loads(value)
    if _canonical_json(decoded) != value:
        raise WorkspaceStoreUnavailableError()
    return decoded


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    # O_NOFOLLOW protects the leaf only. Reject substituted Source directories
    # as well, including on restart and confirmation readback.
    for parent in (path.parents[3], path.parents[2], path.parents[1], path.parent):
        _require_directory(parent, create=False)
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


_EXPECTED_V2_RUNS_SQL = """
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
    start_request_sha256 TEXT NOT NULL,
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


_EXPECTED_V2_DEFINITION_DRAFTS_SQL = """
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


# The complete v3 table set is deliberately frozen here.  Source issues and
# the parsed artifact live in source_revisions.artifact_json; separate source
# binding/artifact/issue tables are not part of this checkpoint.
_EXPECTED_V3_TABLES: tuple[tuple[str, str], ...] = (
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


_EXPECTED_MESSAGE_INPUTS_SQL = """
CREATE TABLE run_message_inputs (
    workspace_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    expected_state_version INTEGER NOT NULL CHECK (expected_state_version > 0),
    references_json TEXT NOT NULL,
    history_messages_json TEXT NOT NULL,
    PRIMARY KEY (workspace_id, mission_id, run_id),
    UNIQUE (workspace_id, mission_id, message_id),
    FOREIGN KEY (workspace_id, mission_id, run_id)
        REFERENCES runs(workspace_id, mission_id, run_id),
    FOREIGN KEY (workspace_id, mission_id, message_id)
        REFERENCES mission_messages(workspace_id, mission_id, message_id)
)
"""
_EXPECTED_V4_TABLES = (*_EXPECTED_V3_TABLES, ("run_message_inputs", _EXPECTED_MESSAGE_INPUTS_SQL))


_EXPECTED_V3_INDEXES: tuple[tuple[str, str, str], ...] = (
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


_EXPECTED_V2_TABLES: tuple[tuple[str, str], ...] = tuple(
    (
        name,
        _EXPECTED_V2_RUNS_SQL
        if name == "runs"
        else _EXPECTED_V2_DEFINITION_DRAFTS_SQL
        if name == "definition_drafts"
        else sql,
    )
    for name, sql in _EXPECTED_V3_TABLES
)
_EXPECTED_V2_INDEXES = _EXPECTED_V3_INDEXES


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


def _schema_matches(
    connection: sqlite3.Connection,
    version: int,
    tables: tuple[tuple[str, str], ...],
    indexes: tuple[tuple[str, str, str], ...],
) -> bool:
    if connection.execute("PRAGMA user_version").fetchone()[0] != version:
        return False
    expected = {
        ("table", name, name): _normalize_schema_sql(sql)
        for name, sql in tables
    }
    expected.update(
        {
            ("index", name, table_name): _normalize_schema_sql(sql)
            for name, table_name, sql in indexes
        }
    )
    actual = {
        (object_type, name, table_name): _normalize_schema_sql(sql)
        for object_type, name, table_name, sql in _objects(connection)
    }
    return actual == expected


def _schema_is_exact_v2(connection: sqlite3.Connection) -> bool:
    return _schema_matches(
        connection, V2_SCHEMA_VERSION, _EXPECTED_V2_TABLES, _EXPECTED_V2_INDEXES
    )


def _schema_is_exact(connection: sqlite3.Connection) -> bool:
    """Read exact v3 and v4 stores without silently migrating them."""
    return _schema_matches(connection, 3, _EXPECTED_V3_TABLES, _EXPECTED_V3_INDEXES) or _schema_matches(
        connection, 4, _EXPECTED_V4_TABLES, _EXPECTED_V3_INDEXES
    )


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


def _create_v3_tables(
    connection: sqlite3.Connection,
    *,
    include_workspaces: bool,
) -> None:
    tables = _EXPECTED_V3_TABLES if include_workspaces else _EXPECTED_V3_TABLES[1:]
    for _, sql in tables:
        connection.execute(sql)
    for _, _, sql in _EXPECTED_V3_INDEXES:
        connection.execute(sql)


def _create_schema(connection: sqlite3.Connection) -> None:
    """Create a new empty database using the complete frozen v4 schema."""

    _create_v3_tables(connection, include_workspaces=True)
    connection.execute(_EXPECTED_MESSAGE_INPUTS_SQL)
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _migrate_v1_to_v3(connection: sqlite3.Connection) -> None:
    """Add the v3 tables to an exact v1 database in one transaction."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        _create_v3_tables(connection, include_workspaces=False)
        connection.execute(f"PRAGMA user_version={V3_SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.DatabaseError:
            pass
        raise


_V2_EMPTY_LIFECYCLE_TABLES = (
    "runs",
    "run_sources",
    "context_manifests",
    "definition_drafts",
    "clarification_requests",
    "tool_receipts",
    "terminal_receipts",
    "run_events",
)


def _v2_lifecycle_is_empty(connection: sqlite3.Connection) -> bool:
    for table in _V2_EMPTY_LIFECYCLE_TABLES:
        if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            return False
    return connection.execute(
        "SELECT 1 FROM provider_receipts WHERE run_id IS NOT NULL LIMIT 1"
    ).fetchone() is None


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Upgrade only a v2 store with no Run or definition-draft history."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        if not _v2_lifecycle_is_empty(connection):
            raise WorkspaceSchemaUnsupportedError()
        connection.execute("DROP INDEX runs_one_active_per_mission")
        connection.execute("DROP TABLE definition_drafts")
        connection.execute("DROP TABLE runs")
        connection.execute(_EXPECTED_RUNS_SQL)
        connection.execute(_EXPECTED_DEFINITION_DRAFTS_SQL)
        for _, _, sql in _EXPECTED_V3_INDEXES:
            connection.execute(sql)
        connection.execute(f"PRAGMA user_version={V3_SCHEMA_VERSION}")
        if not _schema_is_exact(connection):
            raise WorkspaceSchemaUnsupportedError()
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise WorkspaceStoreUnavailableError()
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
        self._event_sink: Callable[[RunEventEnvelope], None] | None = None

    def set_event_sink(
        self, sink: Callable[[RunEventEnvelope], None] | None,
    ) -> None:
        self._event_sink = sink

    @classmethod
    def open(cls, data_dir: Path | str, *, migrate_dialogue: bool = False) -> "WorkspaceStore":
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
                    _migrate_v1_to_v3(connection)
                except WorkspaceStoreError:
                    raise
                except sqlite3.OperationalError as exc:
                    raise _store_error(exc) from exc
                except sqlite3.DatabaseError as exc:
                    raise _store_error(exc) from exc
            elif version == V2_SCHEMA_VERSION and _schema_is_exact_v2(connection):
                try:
                    _migrate_v2_to_v3(connection)
                except WorkspaceStoreError:
                    raise
                except sqlite3.OperationalError as exc:
                    raise _store_error(exc) from exc
                except sqlite3.DatabaseError as exc:
                    raise _store_error(exc) from exc
            elif not _schema_is_exact(connection):
                raise WorkspaceSchemaUnsupportedError()
            _validate_connection_schema(connection)
        except (sqlite3.DatabaseError, OSError) as exc:
            raise _store_error(exc) from exc
        finally:
            if connection is not None:
                connection.close()
        if migrate_dialogue:
            store.migrate_task_dialogue()
        store.recover_interrupted_runs()
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

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                try:
                    connection.commit()
                except sqlite3.DatabaseError as exc:
                    raise Path2StateError("state_write_outcome_unknown") from exc
            except BaseException:
                try:
                    connection.rollback()
                except sqlite3.DatabaseError:
                    pass
                raise

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
            if revision.permission_status != "read_allowed":
                raise Path2StateError("source_permission_denied")
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

    def migrate_task_dialogue(self) -> Path | None:
        """Explicit, stopped-instance migration; caller must own the local service.

        No startup, doctor, or read endpoint implicitly upgrades a private v3 DB.
        The complete backup remains private beneath the authorized data directory.
        """
        with self._connection() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] == 4:
                return None
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not _schema_matches(connection, 3, _EXPECTED_V3_TABLES, _EXPECTED_V3_INDEXES):
                    raise WorkspaceSchemaUnsupportedError()
                if connection.execute("SELECT 1 FROM runs WHERE status IN ('queued','running') LIMIT 1").fetchone() or connection.execute(
                    "SELECT 1 FROM mission_draft_attempts WHERE status IN ('queued','running') LIMIT 1"
                ).fetchone():
                    raise Path2StateError("run_already_active")
                backup = self.data_dir / ("dialogue-v3-backup-" + str(uuid4()))
                backup.mkdir(mode=0o700)
                # Separate read connection can snapshot while the write reservation excludes changes.
                with closing(sqlite3.connect(self.db_path)) as source, closing(sqlite3.connect(backup / DB_FILENAME)) as target:
                    source.backup(target)
                (backup / DB_FILENAME).chmod(0o600)
                manifest = {"schema_version": 3, "files": {}}
                for ws, revision_id in connection.execute("SELECT workspace_id, revision_id FROM source_revisions"):
                    revision, _ = _load_source_in_connection(connection, ws, revision_id)
                    source_path = _source_path(self.data_dir, revision)
                    content = _read_validated_source_file(source_path, revision)
                    relative = source_path.relative_to(self.data_dir)
                    destination = backup / relative
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    destination.write_bytes(content)
                    destination.chmod(0o600)
                    manifest["files"][str(relative)] = hashlib.sha256(content).hexdigest()
                manifest["files"][DB_FILENAME] = hashlib.sha256((backup / DB_FILENAME).read_bytes()).hexdigest()
                (backup / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
                (backup / "manifest.json").chmod(0o600)
                connection.execute(_EXPECTED_MESSAGE_INPUTS_SQL)
                connection.execute("PRAGMA user_version=4")
                _validate_connection_schema(connection)
                connection.commit()
                return backup
            except BaseException:
                connection.rollback()
                # Retain even an incomplete backup for audit; never restore over new writes.
                raise

    def list_task_messages(self, workspace_id: str, mission_id: str,
                           before_message_id: str | None = None, limit: int = 20) -> TaskMessagePage:
        self._require_path2_workspace(workspace_id)
        with self._connection() as connection:
            connection.execute("BEGIN")
            _load_mission(connection, workspace_id, mission_id)
            rows = _task_page_ids(connection, "mission_messages", "message_id",
                                  workspace_id, mission_id, before_message_id, limit)
            items: list[TaskMessage] = []
            more = len(rows) > limit
            for (message_id,) in rows[:limit]:
                message = self._task_message(connection, workspace_id, mission_id, message_id)
                candidate = TaskMessagePage(items=[message, *items], next_before_message_id=message_id)
                if len(candidate.model_dump_json().encode("utf-8")) > 262144:
                    if not items:
                        raise Path2StateError("message_page_item_too_large")
                    more = True
                    break
                items.insert(0, message)
            return TaskMessagePage(items=items, next_before_message_id=items[0].message_id if more and items else None)

    def list_task_runs(self, workspace_id: str, mission_id: str,
                       before_run_id: str | None = None, limit: int = 20) -> TaskRunPage:
        self._require_path2_workspace(workspace_id)
        with self._connection() as connection:
            connection.execute("BEGIN")
            _load_mission(connection, workspace_id, mission_id)
            rows = _task_page_ids(connection, "runs", "run_id", workspace_id,
                                  mission_id, before_run_id, limit)
            items = []
            for (run_id,) in reversed(rows[:limit]):
                run = _load_run(connection, workspace_id, mission_id, run_id)
                self._validate_source_identities(connection, workspace_id, run.source_refs)
                input_row = _message_input_row(connection, workspace_id, mission_id, run_id)
                items.append(TaskRunSummary(
                    **run.model_dump(include={"workspace_id", "mission_id", "run_id", "created_at",
                        "started_at", "finished_at", "status", "last_sequence", "error_code"}),
                    input_message_id=input_row[0] if input_row else None,
                    has_final_output=run.final_output is not None,
                ))
            return TaskRunPage(items=items, next_before_run_id=items[0].run_id if len(rows) > limit else None)

    def _task_message(self, connection: sqlite3.Connection, workspace_id: str,
                      mission_id: str, message_id: str) -> TaskMessage:
        row = connection.execute(
            "SELECT created_at, role, content, original_attempt_id, run_id FROM mission_messages "
            "WHERE workspace_id=? AND mission_id=? AND message_id=?",
            (workspace_id, mission_id, message_id),
        ).fetchone()
        if row is None:
            raise Path2StateError("message_not_found")
        refs: list[dict[str, Any]] = []
        if row[4] is not None:
            run = _load_run(connection, workspace_id, mission_id, row[4])
            self._validate_source_identities(connection, workspace_id, run.source_refs)
            link = _message_input_row(connection, workspace_id, mission_id, row[4])
            if link is not None:
                if row[1] == "user":
                    if link[0] != message_id or row[3] is not None:
                        raise WorkspaceStoreUnavailableError()
                    refs = _json_value(link[2])
                elif run.terminal_receipt is not None:
                    # Reference arrays cannot contain exact duplicates.
                    for evidence in run.terminal_receipt.source_refs:
                        ref = {"kind": "source_excerpt", "evidence_ref": evidence.model_dump(mode="json")}
                        if ref not in refs:
                            refs.append(ref)
                        if len(refs) == 8:
                            break
        payload = dict(workspace_id=workspace_id, mission_id=mission_id,
                       message_id=message_id, role=row[1], content=row[2], references=refs)
        try:
            return TaskMessage(**payload, created_at=_parse_created_at(row[0]),
                original_attempt_id=row[3], run_id=row[4], sha256=canonical_sha256(payload))
        except (ValueError, TypeError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def message_submission(self, workspace_id: str, mission_id: str,
                           client_request_id: str,
                           request: TaskMessageSendRequest | None = None) -> TaskMessageSendReceipt | None:
        self._require_path2_workspace(workspace_id)
        with self._connection() as connection:
            connection.execute("BEGIN")
            _load_mission(connection, workspace_id, mission_id)
            return self._message_submission(connection, workspace_id, mission_id, client_request_id, request)

    def _message_submission(self, connection: sqlite3.Connection, workspace_id: str,
                            mission_id: str, client_request_id: str,
                            request: TaskMessageSendRequest | None) -> TaskMessageSendReceipt | None:
        row = connection.execute(
            "SELECT run_id, start_request_sha256 FROM runs "
            "WHERE workspace_id=? AND mission_id=? AND client_request_id=?",
            (workspace_id, mission_id, client_request_id),
        ).fetchone()
        if row is None:
            return None
        link = _message_input_row(connection, workspace_id, mission_id, row[0])
        if link is None or (request is not None and row[1] != canonical_sha256(request)):
            raise Path2StateError("state_conflict")
        run = _load_run(connection, workspace_id, mission_id, row[0])
        context = self._message_context(connection, run, validate_current=False)
        if context is None:
            raise WorkspaceStoreUnavailableError()
        return TaskMessageSendReceipt(input_message=context.input, run=run)

    def send_task_message(self, workspace_id: str, mission_id: str,
                          request: TaskMessageSendRequest) -> tuple[TaskMessageSendReceipt, bool]:
        self._require_path2_workspace(workspace_id)
        request = TaskMessageSendRequest.model_validate(request.model_dump(mode="json"))
        with self._write_transaction() as connection:
            mission = _load_mission(connection, workspace_id, mission_id)
            replay = self._message_submission(connection, workspace_id, mission_id, request.client_request_id, request)
            if replay is not None:
                return replay, False
            if connection.execute("PRAGMA user_version").fetchone()[0] != 4:
                raise Path2StateError("task_dialogue_not_implemented")
            if mission.state_version != request.expected_state_version:
                raise Path2StateError("state_conflict")
            _check_message_start_state(connection, mission, allow_partial=True)
            refs = _validated_source_identities(workspace_id, request.source_refs)
            if any(ref not in mission.source_refs for ref in refs):
                raise Path2StateError("source_refs_invalid")
            self._validate_source_identities(connection, workspace_id, refs)
            run_id, message_id = str(uuid4()), str(uuid4())
            now = _utc_now().isoformat()
            connection.execute(
                "INSERT INTO runs (workspace_id, mission_id, run_id, client_request_id, created_at, "
                "started_at, finished_at, status, budget_json, last_sequence, final_output, error_code, start_request_sha256) "
                "VALUES (?, ?, ?, ?, ?, NULL, NULL, 'queued', ?, 0, NULL, NULL, ?)",
                (workspace_id, mission_id, run_id, request.client_request_id, now,
                 _canonical_json(RunBudget(max_output_tokens=16384)), canonical_sha256(request)),
            )
            connection.executemany(
                "INSERT INTO run_sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(workspace_id, mission_id, run_id, ordinal, ref.source_id, ref.revision_id, ref.sha256)
                 for ordinal, ref in enumerate(refs)],
            )
            connection.execute(
                "INSERT INTO mission_messages VALUES (?, ?, ?, ?, 'user', ?, NULL, ?)",
                (workspace_id, mission_id, message_id, now, request.content, run_id),
            )
            connection.execute(
                "INSERT INTO run_message_inputs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workspace_id, mission_id, run_id, message_id, request.expected_state_version,
                 _canonical_json(request.references), _canonical_json(request.history_messages)),
            )
            run = _load_run(connection, workspace_id, mission_id, run_id)
            context = self._message_context(connection, run, validate_current=True)
            if mission.status == "blocked":
                connection.execute(
                    "UPDATE missions SET status='active', state_version=state_version+1 "
                    "WHERE workspace_id=? AND mission_id=?", (workspace_id, mission_id),
                )
            _append_event_in_transaction(connection, run, "message_created", {"message_id": message_id, "role": "user"})
            return TaskMessageSendReceipt(input_message=context.input,
                run=_load_run(connection, workspace_id, mission_id, run_id)), True

    def _validate_message_references(self, connection: sqlite3.Connection, run: RunSnapshot,
                                     references: list, *, current: bool) -> None:
        for ref in references:
            if ref.kind == "source_excerpt":
                try:
                    self._validate_run_evidence_refs(connection, run, [ref.evidence_ref])
                except SourceInputError as exc:
                    raise Path2StateError(exc.code) from exc
            elif ref.kind == "source_column":
                self._validate_run_identity_refs(connection, run, [ref.source_ref])
                _, artifact, _ = self._run_source_material(connection, run, ref.source_ref.revision_id)
                if not any(table.table_id == ref.table_id and
                           any(column.name == ref.column_name for column in table.columns)
                           for table in artifact.tables):
                    raise Path2StateError("source_refs_invalid")
            else:
                row = connection.execute(
                    "SELECT fields_json, relationships_json FROM definition_drafts WHERE workspace_id=? AND mission_id=? "
                    "AND draft_id=? AND version=? AND sha256=?",
                    (run.workspace_id, run.mission_id, ref.draft_id, ref.draft_version, ref.draft_sha256),
                ).fetchone()
                latest = _load_latest_draft(connection, run.workspace_id, run.mission_id)
                if row is None or (current and (latest is None or
                    (latest.draft_id, latest.version, latest.sha256) !=
                    (ref.draft_id, ref.draft_version, ref.draft_sha256))):
                    raise Path2StateError("message_reference_stale")
                members = _json_value(row[0 if ref.kind == "draft_field" else 1])
                key = "field_key" if ref.kind == "draft_field" else "relationship_key"
                member = next((item for item in members if item[key] == getattr(ref, key)), None)
                if member is None:
                    raise Path2StateError("message_reference_stale")
                _check_payload_source_scope(member, run.source_refs)

    def _message_context(self, connection: sqlite3.Connection, run: RunSnapshot,
                         *, validate_current: bool = False) -> MessageContext | None:
        link = _message_input_row(connection, run.workspace_id, run.mission_id, run.run_id)
        if link is None:
            return None
        message = self._task_message(connection, run.workspace_id, run.mission_id, link[0])
        if message.role != "user" or message.original_attempt_id is not None or message.run_id != run.run_id:
            raise WorkspaceStoreUnavailableError()
        history_refs = TypeAdapter(list[MessageHistoryRef]).validate_python(_json_value(link[3]))
        history = [self._task_message(connection, run.workspace_id, run.mission_id, ref.message_id)
                   for ref in history_refs]
        input_order = connection.execute(
            "SELECT rowid FROM mission_messages WHERE workspace_id=? AND mission_id=? AND message_id=?",
            (run.workspace_id, run.mission_id, message.message_id),
        ).fetchone()[0]
        last = 0
        for ref, item in zip(history_refs, history):
            order = connection.execute(
                "SELECT rowid FROM mission_messages WHERE workspace_id=? AND mission_id=? AND message_id=?",
                (run.workspace_id, run.mission_id, item.message_id),
            ).fetchone()[0]
            if item.sha256 != ref.sha256 or not last < order < input_order:
                raise Path2StateError("state_conflict")
            last = order
            if item.run_id:
                old = _load_run(connection, run.workspace_id, run.mission_id, item.run_id)
                if any(source not in run.source_refs for source in old.source_refs):
                    raise Path2StateError("message_context_scope_mismatch")
            self._validate_message_references(connection, run, item.references, current=validate_current)
        row = connection.execute(
            "SELECT client_request_id, start_request_sha256 FROM runs WHERE workspace_id=? AND mission_id=? AND run_id=?",
            (run.workspace_id, run.mission_id, run.run_id),
        ).fetchone()
        rebuilt = TaskMessageSendRequest(kind="message", client_request_id=row[0],
            expected_state_version=link[1], content=message.content, references=message.references,
            history_messages=history_refs, source_refs=run.source_refs, provider_send_confirmed=True)
        if canonical_sha256(rebuilt) != row[1]:
            raise Path2StateError("state_conflict")
        self._validate_message_references(connection, run, message.references, current=validate_current)
        # The existing packet also carries the current draft and clarifications.
        _check_payload_source_scope(run.draft, run.source_refs)
        _check_payload_source_scope(run.clarifications, run.source_refs)
        result = MessageContext(input=message, history=history, request_sha256=row[1])
        if len(result.model_dump_json().encode("utf-8")) > 65536:
            raise Path2StateError("message_context_too_large")
        return result

    def is_message_run(self, workspace_id: str, mission_id: str, run_id: str) -> bool:
        self._require_path2_workspace(workspace_id)
        with self._connection() as connection:
            _load_run(connection, workspace_id, mission_id, run_id)
            return _message_input_row(connection, workspace_id, mission_id, run_id) is not None

    def get_run_snapshot(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                return _load_run(connection, workspace_id, mission_id, run_id)
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def start_run(
        self,
        workspace_id: str,
        mission_id: str,
        request: RunStartRequest,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        try:
            request = RunStartRequest.model_validate(request.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise Path2StateError("run_start_invalid") from exc
        refs = _validated_source_identities(workspace_id, request.source_refs)
        request_sha256 = canonical_sha256(request)
        try:
            with self._write_transaction() as connection:
                replay = connection.execute(
                    """
                    SELECT workspace_id, mission_id, run_id, client_request_id, created_at,
                           started_at, finished_at, status, budget_json, last_sequence,
                           final_output, error_code, start_request_sha256
                    FROM runs
                    WHERE workspace_id = ? AND mission_id = ? AND client_request_id = ?
                    """,
                    (workspace_id, mission_id, request.client_request_id),
                ).fetchone()
                if replay is not None:
                    if replay[12] != request_sha256:
                        raise Path2StateError("state_conflict")
                    return _run_from_row(connection, replay)

                mission = _load_mission(connection, workspace_id, mission_id)
                if mission.state_version != request.expected_state_version:
                    raise Path2StateError("state_conflict")
                _check_message_start_state(connection, mission, allow_partial=False)
                retrying_terminal_run = False
                if mission.status == "blocked":
                    latest = connection.execute(
                        """
                        SELECT status FROM runs
                        WHERE workspace_id=? AND mission_id=?
                        ORDER BY rowid DESC LIMIT 1
                        """,
                        (workspace_id, mission_id),
                    ).fetchone()
                    retrying_terminal_run = (
                        latest is not None and latest[0] in {"failed", "blocked"}
                    )
                elif mission.status != "active":
                    raise Path2StateError("state_conflict")
                if mission.status == "blocked" and not retrying_terminal_run:
                    raise Path2StateError("state_conflict")
                allowed = {
                    (ref.source_id, ref.revision_id, ref.sha256)
                    for ref in mission.source_refs
                }
                if any(
                    (ref.source_id, ref.revision_id, ref.sha256) not in allowed
                    for ref in refs
                ):
                    raise Path2StateError("source_refs_invalid")
                self._validate_source_identities(connection, workspace_id, refs)
                if connection.execute(
                    """
                    SELECT 1 FROM runs
                    WHERE workspace_id = ? AND mission_id = ?
                      AND status IN ('queued', 'running')
                    """,
                    (workspace_id, mission_id),
                ).fetchone() is not None:
                    raise Path2StateError("run_already_active")
                if retrying_terminal_run:
                    changed = connection.execute(
                        """
                        UPDATE missions SET status='active', state_version=state_version+1
                        WHERE workspace_id=? AND mission_id=?
                          AND status='blocked' AND state_version=?
                        """,
                        (workspace_id, mission_id, request.expected_state_version),
                    ).rowcount
                    if changed != 1:
                        raise Path2StateError("state_conflict")
                run_id = str(uuid4())
                created_at = _utc_now()
                budget = RunBudget(max_output_tokens=16384)
                connection.execute(
                    """
                    INSERT INTO runs
                        (workspace_id, mission_id, run_id, client_request_id,
                         created_at, started_at, finished_at, status, budget_json,
                         last_sequence, final_output, error_code, start_request_sha256)
                    VALUES (?, ?, ?, ?, ?, NULL, NULL, 'queued', ?, 0, NULL, NULL, ?)
                    """,
                    (workspace_id, mission_id, run_id, request.client_request_id,
                     created_at.isoformat(), _canonical_json(budget), request_sha256),
                )
                connection.executemany(
                    """
                    INSERT INTO run_sources
                        (workspace_id, mission_id, run_id, ordinal,
                         source_id, revision_id, sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (workspace_id, mission_id, run_id, ordinal,
                         ref.source_id, ref.revision_id, ref.sha256)
                        for ordinal, ref in enumerate(refs)
                    ],
                )
                return _load_run(connection, workspace_id, mission_id, run_id)
        except WorkspaceStoreError:
            raise
        except sqlite3.IntegrityError as exc:
            raise Path2StateError("state_conflict") from exc
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def find_run_start(
        self, workspace_id: str, mission_id: str, request: RunStartRequest,
    ) -> RunSnapshot | None:
        self._require_path2_workspace(workspace_id)
        try:
            request = RunStartRequest.model_validate(request.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise Path2StateError("run_start_invalid") from exc
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                _load_mission(connection, workspace_id, mission_id)
                row = connection.execute(
                    """
                    SELECT workspace_id, mission_id, run_id, client_request_id, created_at,
                           started_at, finished_at, status, budget_json, last_sequence,
                           final_output, error_code, start_request_sha256
                    FROM runs
                    WHERE workspace_id=? AND mission_id=? AND client_request_id=?
                    """,
                    (workspace_id, mission_id, request.client_request_id),
                ).fetchone()
                if row is None:
                    return None
                if row[12] != canonical_sha256(request):
                    raise Path2StateError("state_conflict")
                return _run_from_row(connection, row)
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def create_mission_draft_attempt(
        self,
        workspace_id: str,
        original_input: str,
    ) -> MissionDraftAttempt:
        self._require_path2_workspace(workspace_id)
        attempt = MissionDraftAttempt(
            workspace_id=workspace_id,
            attempt_id=str(uuid4()),
            created_at=_utc_now(),
            original_input=original_input,
            status="queued",
            candidate=None,
            candidate_version=None,
            candidate_sha256=None,
            provider_receipt_id=None,
            mission_id=None,
            error_code=None,
        )
        try:
            with self._write_transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO mission_draft_attempts
                        (workspace_id, attempt_id, created_at, original_input, status,
                         candidate_json, candidate_version, candidate_sha256,
                         provider_receipt_id, mission_id, error_code)
                    VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)
                    """,
                    (
                        attempt.workspace_id,
                        attempt.attempt_id,
                        attempt.created_at.isoformat(),
                        attempt.original_input,
                        attempt.status,
                    ),
                )
                return _load_attempt(connection, workspace_id, attempt.attempt_id)
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def mark_mission_draft_running(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> MissionDraftAttempt:
        self._require_path2_workspace(workspace_id)
        try:
            with self._write_transaction() as connection:
                attempt = _load_attempt(connection, workspace_id, attempt_id)
                if attempt.status != "queued":
                    raise Path2StateError("state_conflict")
                connection.execute(
                    """
                    UPDATE mission_draft_attempts SET status = 'running'
                    WHERE workspace_id = ? AND attempt_id = ? AND status = 'queued'
                    """,
                    (workspace_id, attempt_id),
                )
                return _load_attempt(connection, workspace_id, attempt_id)
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def confirm_mission_draft_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
        candidate_version: int,
        candidate_sha256: str,
        source_refs: list[SourceIdentity],
    ) -> Mission:
        self._require_path2_workspace(workspace_id)
        request = MissionDraftConfirmRequest(
            candidate_version=candidate_version,
            candidate_sha256=candidate_sha256,
            source_refs=source_refs,
        )
        refs = _validated_source_identities(workspace_id, request.source_refs)
        try:
            with self._write_transaction() as connection:
                attempt = _load_attempt(connection, workspace_id, attempt_id)
                if attempt.status == "confirmed":
                    mission = _load_mission(connection, workspace_id, attempt.mission_id or "")
                    if (
                        attempt.candidate_version != candidate_version
                        or attempt.candidate_sha256 != candidate_sha256
                        or mission.source_refs != refs
                    ):
                        raise Path2StateError("state_conflict")
                    return mission
                if (
                    attempt.status != "ready"
                    or attempt.candidate is None
                    or attempt.candidate_version != candidate_version
                    or attempt.candidate_sha256 != candidate_sha256
                ):
                    raise Path2StateError("state_conflict")
                self._validate_source_identities(connection, workspace_id, refs)
                mission_id = str(uuid4())
                created_at = _utc_now()
                connection.execute(
                    """
                    INSERT INTO missions
                        (workspace_id, mission_id, created_at, state_version, status,
                         title, goal, completion_criteria_json, scope_notes_json,
                         original_attempt_id)
                    VALUES (?, ?, ?, 1, 'active', ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        mission_id,
                        created_at.isoformat(),
                        attempt.candidate.title,
                        attempt.candidate.goal,
                        _canonical_json(attempt.candidate.completion_criteria),
                        _canonical_json(attempt.candidate.scope_notes),
                        attempt_id,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO mission_sources
                        (workspace_id, mission_id, ordinal, source_id, revision_id, sha256)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (workspace_id, mission_id, ordinal, ref.source_id, ref.revision_id, ref.sha256)
                        for ordinal, ref in enumerate(refs)
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO mission_messages
                        (workspace_id, mission_id, message_id, created_at, role,
                         content, original_attempt_id, run_id)
                    VALUES (?, ?, ?, ?, 'user', ?, ?, NULL)
                    """,
                    (workspace_id, mission_id, str(uuid4()), created_at.isoformat(), attempt.original_input, attempt_id),
                )
                connection.execute(
                    """
                    UPDATE mission_draft_attempts
                    SET status = 'confirmed', mission_id = ?, error_code = NULL
                    WHERE workspace_id = ? AND attempt_id = ? AND status = 'ready'
                    """,
                    (mission_id, workspace_id, attempt_id),
                )
                return _load_mission(connection, workspace_id, mission_id)
        except WorkspaceStoreError:
            raise
        except sqlite3.IntegrityError as exc:
            raise Path2StateError("state_conflict") from exc
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def list_missions(self, workspace_id: str) -> list[Mission]:
        self._require_path2_workspace(workspace_id)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                rows = connection.execute(
                    "SELECT * FROM missions WHERE workspace_id = ? ORDER BY created_at, mission_id",
                    (workspace_id,),
                ).fetchall()
                return [_mission_from_row(connection, row) for row in rows]
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def get_mission_snapshot(
        self, workspace_id: str, mission_id: str,
    ) -> MissionSnapshot:
        self._require_path2_workspace(workspace_id)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                mission = _load_mission(connection, workspace_id, mission_id)
                latest = connection.execute(
                    """
                    SELECT workspace_id, mission_id, run_id, client_request_id,
                           created_at, started_at, finished_at, status, budget_json,
                           last_sequence, final_output, error_code, start_request_sha256
                    FROM runs WHERE workspace_id = ? AND mission_id = ?
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (workspace_id, mission_id),
                ).fetchone()
                return MissionSnapshot(
                    mission=mission,
                    draft=_load_latest_draft(connection, workspace_id, mission_id),
                    clarifications=_load_clarifications(connection, workspace_id, mission_id),
                    latest_run=None if latest is None else _run_from_row(connection, latest),
                )
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def get_context_snapshot(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
    ) -> ContextSnapshot:
        self._require_path2_workspace(workspace_id)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                mission = _load_mission(connection, workspace_id, mission_id)
                run = _load_run(connection, workspace_id, mission_id, run_id)
                sources: list[SourceRevision] = []
                for ref in run.source_refs:
                    revision, _ = _load_source_in_connection(
                        connection, workspace_id, ref.revision_id
                    )
                    if _source_identity(revision) != ref:
                        raise Path2StateError("source_revision_mismatch")
                    if revision.permission_status != "read_allowed":
                        raise Path2StateError("source_permission_denied")
                    _read_validated_source_file(_source_path(self.data_dir, revision), revision)
                    sources.append(revision)
                return ContextSnapshot(
                    mission=mission, run=run, sources=sources,
                    draft=run.draft, clarifications=run.clarifications,
                    message_context=self._message_context(connection, run),
                )
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def record_context_manifest(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        manifest: ContextManifestInput,
    ) -> ContextPacketManifest:
        self._require_path2_workspace(workspace_id)
        try:
            manifest = ContextManifestInput.model_validate(manifest.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise Path2StateError("context_manifest_invalid") from exc
        try:
            with self._write_transaction() as connection:
                mission = _load_mission(connection, workspace_id, mission_id)
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if run.status != "running":
                    raise Path2StateError("state_conflict")
                latest_draft = _load_latest_draft(connection, workspace_id, mission_id)
                draft_values = (
                    (latest_draft.draft_id, latest_draft.version, latest_draft.sha256)
                    if latest_draft is not None else (None, None, None)
                )
                tool_receipts = _load_tool_receipts(
                    connection, workspace_id, mission_id, run_id
                )
                expected = (
                    mission.state_version,
                    draft_values,
                    run.source_refs,
                    [item.clarification_id for item in run.clarifications],
                    [item.receipt_id for item in tool_receipts],
                    run.budget,
                    [
                        "cross_mission_chat_not_loaded",
                        "unapproved_memory_not_loaded",
                        "unselected_sources_not_loaded",
                    ],
                )
                actual = (
                    manifest.mission_state_version,
                    (manifest.draft_id, manifest.draft_version, manifest.draft_sha256),
                    manifest.source_refs,
                    manifest.clarification_ids,
                    manifest.tool_receipt_ids,
                    manifest.budget,
                    manifest.excluded_reasons,
                )
                if actual != expected:
                    raise Path2StateError("context_manifest_invalid")
                existing = connection.execute(
                    """
                    SELECT workspace_id, mission_id, run_id, manifest_id,
                           mission_state_version, turn_index, draft_id, draft_version,
                           draft_sha256, source_refs_json, clarification_ids_json,
                           tool_receipt_ids_json, budget_json, excluded_reasons_json, sha256
                    FROM context_manifests
                    WHERE workspace_id = ? AND mission_id = ? AND run_id = ? AND turn_index = ?
                    """,
                    (workspace_id, mission_id, run_id, manifest.turn_index),
                ).fetchone()
                if existing is not None:
                    saved = _context_manifest_from_row(existing)
                    comparable = saved.model_dump(mode="json", exclude={"workspace_id", "mission_id", "run_id", "manifest_id", "sha256"})
                    if comparable != manifest.model_dump(mode="json"):
                        raise Path2StateError("state_conflict")
                    return saved
                values = {
                    **manifest.model_dump(mode="json"),
                    "workspace_id": workspace_id, "mission_id": mission_id,
                    "run_id": run_id, "manifest_id": str(uuid4()),
                }
                values["sha256"] = canonical_sha256(values)
                packet = ContextPacketManifest.model_validate(values)
                connection.execute(
                    """
                    INSERT INTO context_manifests
                        (workspace_id, mission_id, run_id, manifest_id,
                         mission_state_version, turn_index, draft_id, draft_version,
                         draft_sha256, source_refs_json, clarification_ids_json,
                         tool_receipt_ids_json, budget_json, excluded_reasons_json, sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (workspace_id, mission_id, run_id, packet.manifest_id,
                     packet.mission_state_version, packet.turn_index, packet.draft_id,
                     packet.draft_version, packet.draft_sha256,
                     _canonical_json(packet.source_refs),
                     _canonical_json(packet.clarification_ids),
                     _canonical_json(packet.tool_receipt_ids), _canonical_json(packet.budget),
                     _canonical_json(packet.excluded_reasons), packet.sha256),
                )
                saved = connection.execute(
                    """
                    SELECT workspace_id, mission_id, run_id, manifest_id,
                           mission_state_version, turn_index, draft_id, draft_version,
                           draft_sha256, source_refs_json, clarification_ids_json,
                           tool_receipt_ids_json, budget_json, excluded_reasons_json, sha256
                    FROM context_manifests WHERE workspace_id=? AND mission_id=?
                      AND run_id=? AND manifest_id=?
                    """,
                    (workspace_id, mission_id, run_id, packet.manifest_id),
                ).fetchone()
                if saved is None:
                    raise WorkspaceStoreUnavailableError()
                return _context_manifest_from_row(saved)
        except WorkspaceStoreError:
            raise
        except sqlite3.IntegrityError as exc:
            raise Path2StateError("state_conflict") from exc
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def mark_run_running(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        try:
            with self._write_transaction() as connection:
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if run.status != "queued":
                    return run
                started_at = _utc_now().isoformat()
                changed = connection.execute(
                    """
                    UPDATE runs SET status='running', started_at=?
                    WHERE workspace_id=? AND mission_id=? AND run_id=? AND status='queued'
                    """,
                    (started_at, workspace_id, mission_id, run_id),
                ).rowcount
                if changed != 1:
                    raise Path2StateError("state_conflict")
                return _load_run(connection, workspace_id, mission_id, run_id)
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def validate_run_tool_batch(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        calls: list[DomainToolCall],
    ) -> None:
        self._require_path2_workspace(workspace_id)
        try:
            calls = TypeAdapter(list[DomainToolCall]).validate_python(
                [call.model_dump(mode="json") for call in calls]
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise Path2StateError("tool_arguments_invalid") from exc
        if not calls or len(calls) > 24:
            raise Path2StateError("tool_arguments_invalid")
        if len({call.call_id for call in calls}) != len(calls):
            raise Path2StateError("tool_arguments_invalid")
        terminal_count = sum(
            call.name in {"create_clarification", "submit_for_review", "finish_run"}
            for call in calls
        )
        if terminal_count and len(calls) != 1:
            raise Path2StateError("terminal_tool_mixed_batch")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if run.status != "running":
                    raise Path2StateError("state_conflict")
                prior = _load_tool_receipts(connection, workspace_id, mission_id, run_id)
                if len(prior) + len(calls) > run.budget.max_tool_calls:
                    raise Path2StateError("tool_call_budget_exceeded")
                existing_ids = {item.call_id for item in prior}
                if any(call.call_id in existing_ids for call in calls):
                    raise Path2StateError("state_conflict")
                for call in calls:
                    self._validate_run_tool_call(connection, run, call)
        except WorkspaceStoreError:
            raise
        except SourceInputError as exc:
            raise Path2StateError(exc.code) from exc
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def execute_run_tool(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        call: DomainToolCall,
    ) -> RunToolResult:
        self._require_path2_workspace(workspace_id)
        try:
            call = TypeAdapter(DomainToolCall).validate_python(call.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise Path2StateError("tool_arguments_invalid") from exc
        try:
            with self._write_transaction() as connection:
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if run.status != "running":
                    raise Path2StateError("state_conflict")
                self._validate_run_tool_call(connection, run, call)
                receipts = _load_tool_receipts(connection, workspace_id, mission_id, run_id)
                if any(item.call_id == call.call_id for item in receipts):
                    raise Path2StateError("state_conflict")
                ordinal = len(receipts) + 1
                if ordinal > run.budget.max_tool_calls:
                    raise Path2StateError("tool_call_budget_exceeded")

                output: object
                status: Literal["succeeded", "rejected"] = "succeeded"
                error_code: str | None = None
                terminal_snapshot: RunSnapshot | None = None
                if call.name == "list_sources":
                    output = [
                        _load_source_in_connection(connection, workspace_id, ref.revision_id)[0]
                        for ref in run.source_refs
                    ]
                elif call.name == "read_source":
                    revision, artifact, content = self._run_source_material(
                        connection, run, call.arguments.revision_id
                    )
                    try:
                        output = read_source_fragment(
                            revision, content, call.arguments.locator
                        )
                    except SourceInputError as exc:
                        reasons = {
                            "json_pointer_out_of_bounds": (
                                "The JSON pointer does not exist. Start with the empty "
                                "pointer to inspect the root."
                            ),
                            "locator_column_not_found": (
                                "The requested CSV column does not exist. Read all "
                                "columns or inspect the table profile first."
                            ),
                            "locator_media_type_mismatch": (
                                "Use text_lines for text or Markdown, csv_rows for CSV, "
                                "and json_pointer for JSON."
                            ),
                            "locator_out_of_bounds": (
                                "The locator exceeds the selected source. Retry with a "
                                "smaller range beginning at 1."
                            ),
                        }
                        if exc.code not in reasons:
                            raise
                        reason = reasons[exc.code]
                        if exc.code == "locator_out_of_bounds":
                            locator_kind = call.arguments.locator.kind
                            count = None
                            if locator_kind == "text_lines":
                                count = artifact.text_line_count
                                unit = "text lines"
                            elif locator_kind == "csv_rows" and len(artifact.tables) == 1:
                                count = artifact.tables[0].row_count
                                unit = "CSV data rows (excluding the header)"
                            if count is not None:
                                bounds = (
                                    f"Use a range within 1..{count}, inclusive, "
                                    "and the existing per-read limits."
                                    if count else "There is no valid nonempty range."
                                )
                                reason = (
                                    f"The selected source has {count} {unit}. {bounds} "
                                    "The oversized request was rejected, not clipped. "
                                    "A rejected range does not show that the source body is missing."
                                )
                        status, error_code = "rejected", exc.code
                        output = DomainRejection(code=exc.code, reason=reason)
                elif call.name == "inspect_dataset":
                    revision_ids = (
                        [call.arguments.revision_id]
                        if call.arguments.kind == "table" else
                        [call.arguments.left.source_ref.revision_id,
                         call.arguments.right.source_ref.revision_id]
                    )
                    materials = [
                        self._run_source_material(connection, run, revision_id)
                        for revision_id in revision_ids
                    ]
                    # Parse failures must take precedence over a bad lookup.
                    if any(artifact.parse_status in {"blocked", "failed"}
                           for _, artifact, _ in materials):
                        raise Path2StateError("source_parse_failed")
                    try:
                        if call.arguments.kind == "table":
                            output = next(
                                (table for table in materials[0][1].tables
                                 if table.table_id == call.arguments.table_id), None
                            )
                            if output is None:
                                raise SourceInputError("table_not_found")
                        else:
                            output = inspect_relationship(
                                call.arguments.left, materials[0][0], materials[0][2],
                                call.arguments.right, materials[1][0], materials[1][2],
                            )
                    except SourceInputError as exc:
                        reasons = {
                            "table_not_found": "Inspect an available table ID first.",
                            "join_column_not_found": (
                                "Inspect both table profiles and select their exact column names."
                            ),
                            "join_columns_empty": "Select at least one column on each side.",
                            "invalid_table_key": "Select valid, distinct columns from each table profile.",
                        }
                        if exc.code not in reasons:
                            raise
                        reason = reasons[exc.code]
                        if exc.code == "table_not_found":
                            # Bound hints without cutting a table ID into a false locator.
                            for revision, artifact, _ in materials:
                                hint = f" Revision {revision.revision_id} table IDs: "
                                reason += hint
                                for table in artifact.tables:
                                    item = json.dumps(table.table_id, ensure_ascii=True) + "; "
                                    if len(reason) + len(item) > 3000:
                                        reason += "[remaining IDs omitted]"
                                        break
                                    reason += item
                                if not artifact.tables:
                                    reason += "none; "
                        status, error_code = "rejected", exc.code
                        output = DomainRejection(code=exc.code, reason=reason)
                elif call.name == "update_definition_draft":
                    latest = _load_latest_draft(connection, workspace_id, mission_id)
                    if latest is not None and latest.status == "in_review":
                        status, error_code = "rejected", "draft_in_review"
                        output = DomainRejection(
                            code=error_code,
                            reason="The current definition draft is already in review.",
                        )
                    elif (
                        (latest is None and call.arguments.expected_version != 0)
                        or (latest is not None and (
                            call.arguments.expected_version != latest.version
                            or call.arguments.expected_sha256 != latest.sha256
                        ))
                    ):
                        status, error_code = "rejected", "state_conflict"
                        output = DomainRejection(
                            code=error_code,
                            reason="The definition draft version or hash is stale.",
                        )
                    else:
                        # Each submitted key replaces that whole candidate. Omitted
                        # keys survive; only unresolved_items is a complete list.
                        fields = {item.field_key: item for item in latest.fields} if latest else {}
                        relationships = {
                            item.relationship_key: item for item in latest.relationships
                        } if latest else {}
                        fields.update({item.field_key: item for item in call.arguments.fields})
                        relationships.update({
                            item.relationship_key: item for item in call.arguments.relationships
                        })
                        if len(fields) > 100 or len(relationships) > 100:
                            raise Path2StateError("tool_arguments_invalid")
                        payload = {
                            "fields": [item.model_dump(mode="json") for item in fields.values()],
                            "relationships": [
                                item.model_dump(mode="json") for item in relationships.values()
                            ],
                            "unresolved_items": call.arguments.unresolved_items,
                        }
                        output = DefinitionDraft(
                            workspace_id=workspace_id, mission_id=mission_id,
                            draft_id=latest.draft_id if latest is not None else str(uuid4()),
                            version=1 if latest is None else latest.version + 1,
                            sha256=canonical_sha256(payload), status="draft",
                            semantic_approval="pending", **payload,
                        )
                        connection.execute(
                            """
                            INSERT INTO definition_drafts
                                (workspace_id, mission_id, draft_id, version, sha256,
                                 status, semantic_approval, fields_json,
                                 relationships_json, unresolved_items_json)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (workspace_id, mission_id, output.draft_id, output.version,
                             output.sha256, output.status, output.semantic_approval,
                             _canonical_json(output.fields),
                             _canonical_json(output.relationships),
                             _canonical_json(output.unresolved_items)),
                        )
                else:
                    output, terminal_snapshot = self._execute_terminal_tool(
                        connection, run, call, receipts, ordinal
                    )

                refs = _evidence_refs(output)
                tool_receipt = ToolReceipt(
                    workspace_id=workspace_id, mission_id=mission_id, run_id=run_id,
                    receipt_id=str(uuid4()), ordinal=ordinal, call_id=call.call_id,
                    name=call.name, arguments_sha256=canonical_sha256(call.arguments),
                    status=status, created_at=_utc_now(), source_refs=refs,
                    error_code=error_code,
                )
                if terminal_snapshot is None:
                    _insert_tool_receipt(connection, tool_receipt)
                    if isinstance(output, DefinitionDraft) and call.name == "update_definition_draft":
                        _append_event_in_transaction(
                            connection, run, "draft_updated",
                            {"draft_id": output.draft_id, "version": output.version,
                             "sha256": output.sha256},
                        )
                    saved_receipt = _load_tool_receipts(
                        connection, workspace_id, mission_id, run_id
                    )[-1]
                    if saved_receipt != tool_receipt:
                        raise WorkspaceStoreUnavailableError()
                else:
                    # The terminal transaction inserted the receipt with the same
                    # immutable identity before its TerminalReceipt.
                    saved_receipt = _load_tool_receipts(
                        connection, workspace_id, mission_id, run_id
                    )[-1]
                    if (
                        saved_receipt.call_id != call.call_id
                        or saved_receipt.name != call.name
                        or saved_receipt.arguments_sha256 != canonical_sha256(call.arguments)
                    ):
                        raise WorkspaceStoreUnavailableError()
                    tool_receipt = saved_receipt
                    terminal_snapshot = _load_run(
                        connection, workspace_id, mission_id, run_id
                    )
                return RunToolResult(
                    call_id=call.call_id, status=status, output=output,
                    tool_receipt=tool_receipt, terminal_snapshot=terminal_snapshot,
                )
        except WorkspaceStoreError:
            raise
        except StopIteration as exc:
            raise Path2StateError("table_not_found") from exc
        except SourceInputError as exc:
            raise Path2StateError(exc.code) from exc
        except sqlite3.IntegrityError as exc:
            raise Path2StateError("state_conflict") from exc
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def _run_source_material(
        self, connection: sqlite3.Connection, run: RunSnapshot, revision_id: str,
    ) -> tuple[SourceRevision, SourceArtifact, bytes]:
        selected = next(
            (ref for ref in run.source_refs if ref.revision_id == revision_id), None
        )
        if selected is None:
            raise Path2StateError("source_permission_denied")
        revision, artifact = _load_source_in_connection(
            connection, run.workspace_id, revision_id
        )
        if _source_identity(revision) != selected:
            raise Path2StateError("source_revision_mismatch")
        if revision.permission_status != "read_allowed":
            raise Path2StateError("source_permission_denied")
        content = _read_validated_source_file(
            _source_path(self.data_dir, revision), revision
        )
        return revision, artifact, content

    def _validate_run_evidence_refs(
        self, connection: sqlite3.Connection, run: RunSnapshot,
        refs: list[EvidenceRef],
    ) -> None:
        for ref in refs:
            revision, _, content = self._run_source_material(
                connection, run, ref.revision_id
            )
            if (
                ref.workspace_id != revision.workspace_id
                or ref.source_id != revision.source_id
                or ref.sha256 != revision.sha256
            ):
                raise Path2StateError("source_revision_mismatch")
            read_source_fragment(revision, content, ref.locator)

    def _validate_run_identity_refs(
        self, connection: sqlite3.Connection, run: RunSnapshot,
        refs: list[SourceIdentity],
    ) -> None:
        for ref in refs:
            revision, _, _ = self._run_source_material(
                connection, run, ref.revision_id
            )
            if _source_identity(revision) != ref:
                raise Path2StateError("source_revision_mismatch")

    def _validate_run_tool_call(
        self, connection: sqlite3.Connection, run: RunSnapshot,
        call: DomainToolCall,
    ) -> None:
        if call.name == "list_sources":
            for ref in run.source_refs:
                self._run_source_material(connection, run, ref.revision_id)
            return
        if call.name == "read_source":
            self._run_source_material(
                connection, run, call.arguments.revision_id
            )
            return
        if call.name == "inspect_dataset":
            if call.arguments.kind == "table":
                self._run_source_material(
                    connection, run, call.arguments.revision_id
                )
            else:
                self._validate_run_identity_refs(
                    connection, run,
                    [call.arguments.left.source_ref, call.arguments.right.source_ref],
                )
            return
        if call.name == "update_definition_draft":
            identities = [
                column.source_ref
                for field in call.arguments.fields for column in field.source_columns
            ] + [
                table.source_ref
                for relationship in call.arguments.relationships
                for table in (relationship.left, relationship.right)
            ]
            evidence = [
                ref for field in call.arguments.fields for ref in field.source_refs
            ] + [
                ref for relationship in call.arguments.relationships
                for ref in relationship.source_refs
            ]
            self._validate_run_identity_refs(connection, run, identities)
            self._validate_run_evidence_refs(connection, run, evidence)
            return
        if call.name in {"create_clarification", "submit_for_review"}:
            latest = _load_latest_draft(connection, run.workspace_id, run.mission_id)
            if (
                latest is None
                or latest.status != "draft"
                or latest.version != call.arguments.draft_version
                or latest.sha256 != call.arguments.draft_sha256
            ):
                raise Path2StateError("state_conflict")
            if call.name == "create_clarification":
                self._validate_run_evidence_refs(
                    connection, run,
                    [ref for question in call.arguments.questions for ref in question.source_refs],
                )
            return
        if call.name == "finish_run":
            self._validate_run_evidence_refs(
                connection, run, call.arguments.source_refs
            )
            return
        raise Path2StateError("capability_denied")

    def _execute_terminal_tool(
        self, connection: sqlite3.Connection, run: RunSnapshot,
        call: DomainToolCall, prior_receipts: list[ToolReceipt], ordinal: int,
    ) -> tuple[object, RunSnapshot]:
        workspace_id, mission_id, run_id = run.workspace_id, run.mission_id, run.run_id
        latest = _load_latest_draft(connection, workspace_id, mission_id)
        now = _utc_now()
        if call.name == "create_clarification":
            if latest is None:
                raise Path2StateError("state_conflict")
            output: object = ClarificationRequest(
                workspace_id=workspace_id, mission_id=mission_id, run_id=run_id,
                clarification_id=str(uuid4()), draft_version=latest.version,
                draft_sha256=latest.sha256, status="awaiting_answer",
                questions=call.arguments.questions,
            )
            connection.execute(
                """
                INSERT INTO clarification_requests
                    (workspace_id, mission_id, run_id, clarification_id,
                     draft_version, draft_sha256, status, questions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (workspace_id, mission_id, run_id, output.clarification_id,
                 output.draft_version, output.draft_sha256, output.status,
                 _canonical_json(output.questions)),
            )
            outcome = "waiting_for_human"
        elif call.name == "submit_for_review":
            if latest is None:
                raise Path2StateError("state_conflict")
            connection.execute(
                """
                UPDATE definition_drafts SET status='in_review'
                WHERE workspace_id=? AND mission_id=? AND draft_id=?
                  AND version=? AND sha256=? AND status='draft'
                """,
                (workspace_id, mission_id, latest.draft_id,
                 latest.version, latest.sha256),
            )
            output = latest.model_copy(update={"status": "in_review"})
            outcome = "waiting_for_human"
        elif call.name == "finish_run":
            output = None
            outcome = "partial"
        else:
            raise Path2StateError("capability_denied")

        output_refs = (
            list(call.arguments.source_refs)
            if call.name == "finish_run" else _evidence_refs(output)
        )
        tool_receipt = ToolReceipt(
            workspace_id=workspace_id, mission_id=mission_id, run_id=run_id,
            receipt_id=str(uuid4()), ordinal=ordinal, call_id=call.call_id,
            name=call.name, arguments_sha256=canonical_sha256(call.arguments),
            status="succeeded", created_at=now, source_refs=output_refs,
            error_code=None,
        )
        _insert_tool_receipt(connection, tool_receipt)
        clarification_ids = [
            item.clarification_id
            for item in _load_clarifications(connection, workspace_id, mission_id, run_id)
        ]
        provider_receipt_ids = [
            row[0] for row in connection.execute(
                """
                SELECT receipt_id FROM provider_receipts
                WHERE workspace_id=? AND mission_id=? AND run_id=? ORDER BY turn_index
                """,
                (workspace_id, mission_id, run_id),
            ).fetchall()
        ]
        terminal = TerminalReceipt(
            workspace_id=workspace_id, mission_id=mission_id, run_id=run_id,
            receipt_id=str(uuid4()), created_at=now, terminal_tool=call.name,
            outcome=outcome,
            draft_id=latest.draft_id if latest is not None else None,
            draft_version=latest.version if latest is not None else None,
            draft_sha256=latest.sha256 if latest is not None else None,
            clarification_ids=clarification_ids,
            provider_receipt_ids=provider_receipt_ids,
            tool_receipt_ids=[item.receipt_id for item in prior_receipts] + [tool_receipt.receipt_id],
            source_refs=output_refs,
        )
        connection.execute(
            """
            INSERT INTO terminal_receipts
                (workspace_id, mission_id, run_id, receipt_id, created_at,
                 terminal_tool, outcome, draft_id, draft_version, draft_sha256,
                 clarification_ids_json, provider_receipt_ids_json,
                 tool_receipt_ids_json, source_refs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, mission_id, run_id, terminal.receipt_id,
             terminal.created_at.isoformat(), terminal.terminal_tool, terminal.outcome,
             terminal.draft_id, terminal.draft_version, terminal.draft_sha256,
             _canonical_json(terminal.clarification_ids),
             _canonical_json(terminal.provider_receipt_ids),
             _canonical_json(terminal.tool_receipt_ids),
             _canonical_json(terminal.source_refs)),
        )
        connection.execute(
            """
            UPDATE runs SET status=?, finished_at=?, error_code=NULL
            WHERE workspace_id=? AND mission_id=? AND run_id=? AND status='running'
            """,
            (outcome, now.isoformat(), workspace_id, mission_id, run_id),
        )
        mission_status = "waiting_for_human" if outcome == "waiting_for_human" else "blocked"
        connection.execute(
            """
            UPDATE missions SET status=?, state_version=state_version+1
            WHERE workspace_id=? AND mission_id=? AND status='active'
            """,
            (mission_status, workspace_id, mission_id),
        )
        if call.name == "create_clarification":
            _append_event_in_transaction(
                connection, run, "clarification_requested",
                {"clarification_id": output.clarification_id,
                 "draft_version": output.draft_version,
                 "draft_sha256": output.draft_sha256},
            )
        if call.name == "finish_run":
            output = terminal
        return output, _load_run(connection, workspace_id, mission_id, run_id)

    def record_provider_receipt(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        receipt: ProviderReceipt,
    ) -> ProviderReceipt:
        self._require_path2_workspace(workspace_id)
        from contextox.agent import P0_RUN_SHA256, TOOL_SCHEMA_SHA256

        try:
            receipt = ProviderReceipt.model_validate(receipt.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise Path2StateError("provider_receipt_invalid") from exc
        if (
            receipt.workspace_id != workspace_id
            or receipt.attempt_id is not None
            or receipt.mission_id != mission_id
            or receipt.run_id != run_id
            or receipt.p0_sha256 != P0_RUN_SHA256
            or receipt.tool_schema_sha256 != TOOL_SCHEMA_SHA256
        ):
            raise Path2StateError("provider_receipt_invalid")
        try:
            with self._write_transaction() as connection:
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if run.status != "running" and not (
                    run.status == "cancelled" and receipt.status == "cancelled"
                ):
                    raise Path2StateError("state_conflict")
                manifest_row = connection.execute(
                    """
                    SELECT workspace_id, mission_id, run_id, manifest_id,
                           mission_state_version, turn_index, draft_id, draft_version,
                           draft_sha256, source_refs_json, clarification_ids_json,
                           tool_receipt_ids_json, budget_json, excluded_reasons_json, sha256
                    FROM context_manifests
                    WHERE workspace_id=? AND mission_id=? AND run_id=? AND manifest_id=?
                    """,
                    (workspace_id, mission_id, run_id, receipt.context_manifest_id),
                ).fetchone()
                if manifest_row is None:
                    raise Path2StateError("provider_receipt_invalid")
                manifest = _context_manifest_from_row(manifest_row)
                if (
                    manifest.turn_index != receipt.turn_index
                    or manifest.sha256 != receipt.context_manifest_sha256
                ):
                    raise Path2StateError("provider_receipt_invalid")
                existing = connection.execute(
                    """
                    SELECT receipt_id FROM provider_receipts
                    WHERE workspace_id=? AND mission_id=? AND run_id=? AND turn_index=?
                    """,
                    (workspace_id, mission_id, run_id, receipt.turn_index),
                ).fetchone()
                if existing is not None:
                    saved = _load_provider_receipt(connection, workspace_id, existing[0])
                    if saved != receipt:
                        raise Path2StateError("provider_receipt_conflict")
                    return saved
                _insert_provider_receipt(connection, receipt)
                saved = _load_provider_receipt(connection, workspace_id, receipt.receipt_id)
                if saved != receipt:
                    raise WorkspaceStoreUnavailableError()
                return saved
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def append_run_event(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        event: RunEventInput,
    ) -> RunEventEnvelope:
        self._require_path2_workspace(workspace_id)
        try:
            event = RunEventInput.model_validate(event.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise Path2StateError("run_event_invalid") from exc
        try:
            with self._write_transaction() as connection:
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if not _run_event_allowed(run, event):
                    raise Path2StateError("state_conflict")
                sequence = run.last_sequence + 1
                envelope = RunEventEnvelope.model_validate({
                    "event_id": str(sequence), "event_type": event.event_type,
                    "occurred_at": _utc_now(), "workspace_id": workspace_id,
                    "mission_id": mission_id, "run_id": run_id, "sequence": sequence,
                    "public_payload": event.public_payload.model_dump(mode="json"),
                })
                if event.event_type != "model_delta":
                    connection.execute(
                        """
                        INSERT INTO run_events
                            (workspace_id, mission_id, run_id, sequence, event_id,
                             event_type, occurred_at, public_payload_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (workspace_id, mission_id, run_id, sequence, envelope.event_id,
                         envelope.event_type, envelope.occurred_at.isoformat(),
                         _canonical_json(envelope.public_payload)),
                    )
                connection.execute(
                    """
                    UPDATE runs SET last_sequence=?
                    WHERE workspace_id=? AND mission_id=? AND run_id=? AND last_sequence=?
                    """,
                    (sequence, workspace_id, mission_id, run_id, run.last_sequence),
                )
        except WorkspaceStoreError:
            raise
        except sqlite3.IntegrityError as exc:
            raise Path2StateError("state_conflict") from exc
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc
        sink = self._event_sink
        if sink is not None:
            try:
                sink(envelope)
            except Exception:
                pass
        return envelope

    def list_run_events(
        self, workspace_id: str, mission_id: str, run_id: str,
        after_sequence: int = 0,
    ) -> list[RunEventEnvelope]:
        self._require_path2_workspace(workspace_id)
        if type(after_sequence) is not int or after_sequence < 0:
            raise Path2StateError("run_event_cursor_invalid")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                _load_run(connection, workspace_id, mission_id, run_id)
                rows = connection.execute(
                    """
                    SELECT event_id, event_type, occurred_at, workspace_id,
                           mission_id, run_id, sequence, public_payload_json
                    FROM run_events
                    WHERE workspace_id=? AND mission_id=? AND run_id=? AND sequence>?
                    ORDER BY sequence
                    """,
                    (workspace_id, mission_id, run_id, after_sequence),
                ).fetchall()
                return [_run_event_from_row(row) for row in rows]
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def fail_run(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        status: Literal["blocked", "failed", "partial"],
        code: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        if status not in {"blocked", "failed", "partial"}:
            raise Path2StateError("state_conflict")
        try:
            with self._write_transaction() as connection:
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if run.status in {"cancelled", "waiting_for_human", "partial", "completed"}:
                    return run
                if run.status in {"blocked", "failed"}:
                    if run.status != status or run.error_code != code:
                        raise Path2StateError("state_conflict")
                    return run
                if status == "partial":
                    raise Path2StateError("state_conflict")
                now = _utc_now().isoformat()
                connection.execute(
                    """
                    UPDATE runs SET status=?, finished_at=?, error_code=?
                    WHERE workspace_id=? AND mission_id=? AND run_id=?
                      AND status IN ('queued','running')
                    """,
                    (status, now, code, workspace_id, mission_id, run_id),
                )
                connection.execute(
                    """
                    UPDATE missions SET status='blocked', state_version=state_version+1
                    WHERE workspace_id=? AND mission_id=?
                      AND status NOT IN ('completed','cancelled')
                    """,
                    (workspace_id, mission_id),
                )
                return _load_run(connection, workspace_id, mission_id, run_id)
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def save_run_final_output(
        self,
        workspace_id: str,
        mission_id: str,
        run_id: str,
        content: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        if not isinstance(content, str) or not content or len(content) > 32768:
            raise Path2StateError("final_output_invalid")
        try:
            with self._write_transaction() as connection:
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if run.status not in {"waiting_for_human", "partial"} or run.terminal_receipt is None:
                    raise Path2StateError("state_conflict")
                if run.final_output is not None:
                    if run.final_output != content:
                        raise Path2StateError("state_conflict")
                    return run
                now = _utc_now().isoformat()
                connection.execute(
                    """
                    UPDATE runs SET final_output=?
                    WHERE workspace_id=? AND mission_id=? AND run_id=? AND final_output IS NULL
                    """,
                    (content, workspace_id, mission_id, run_id),
                )
                connection.execute(
                    """
                    INSERT INTO mission_messages
                        (workspace_id, mission_id, message_id, created_at, role,
                         content, original_attempt_id, run_id)
                    VALUES (?, ?, ?, ?, 'assistant', ?, NULL, ?)
                    """,
                    (workspace_id, mission_id, str(uuid4()), now, content, run_id),
                )
                return _load_run(connection, workspace_id, mission_id, run_id)
        except WorkspaceStoreError:
            raise
        except sqlite3.IntegrityError as exc:
            raise Path2StateError("state_conflict") from exc
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def cancel_run(
        self, workspace_id: str, mission_id: str, run_id: str,
    ) -> RunSnapshot:
        self._require_path2_workspace(workspace_id)
        published: RunEventEnvelope | None = None
        try:
            with self._write_transaction() as connection:
                run = _load_run(connection, workspace_id, mission_id, run_id)
                if run.status not in {"queued", "running"}:
                    stopped = run
                else:
                    now = _utc_now()
                    sequence = run.last_sequence + 1
                    connection.execute(
                        """
                        UPDATE runs SET status='cancelled', finished_at=?, error_code='cancelled',
                                        last_sequence=?
                        WHERE workspace_id=? AND mission_id=? AND run_id=?
                          AND status IN ('queued','running')
                        """,
                        (now.isoformat(), sequence, workspace_id, mission_id, run_id),
                    )
                    connection.execute(
                        """
                        UPDATE missions SET status='cancelled', state_version=state_version+1
                        WHERE workspace_id=? AND mission_id=? AND status!='cancelled'
                        """,
                        (workspace_id, mission_id),
                    )
                    payload = {"status": "cancelled", "terminal_receipt_id": None,
                               "error_code": "cancelled"}
                    published = RunEventEnvelope.model_validate({
                        "event_id": str(sequence), "event_type": "run_cancelled",
                        "occurred_at": now, "workspace_id": workspace_id,
                        "mission_id": mission_id, "run_id": run_id,
                        "sequence": sequence, "public_payload": payload,
                    })
                    connection.execute(
                        """
                        INSERT INTO run_events
                            (workspace_id, mission_id, run_id, sequence, event_id,
                             event_type, occurred_at, public_payload_json)
                        VALUES (?, ?, ?, ?, ?, 'run_cancelled', ?, ?)
                        """,
                        (workspace_id, mission_id, run_id, sequence, str(sequence),
                         now.isoformat(), _canonical_json(payload)),
                    )
                    stopped = _load_run(connection, workspace_id, mission_id, run_id)
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc
        if published is not None and self._event_sink is not None:
            try:
                self._event_sink(published)
            except Exception:
                pass
        return stopped

    def recover_interrupted_runs(self) -> int:
        recovered = 0
        try:
            with self._write_transaction() as connection:
                rows = connection.execute(
                    """
                    SELECT workspace_id, mission_id, run_id, last_sequence
                    FROM runs WHERE status IN ('queued','running') ORDER BY rowid
                    """
                ).fetchall()
                now = _utc_now()
                for workspace_id, mission_id, run_id, last_sequence in rows:
                    active = _load_run(connection, workspace_id, mission_id, run_id)
                    if active.status not in {"queued", "running"}:
                        raise WorkspaceStoreUnavailableError()
                    last_sequence = active.last_sequence
                    sequence = last_sequence + 1
                    connection.execute(
                        """
                        UPDATE runs SET status='failed', finished_at=?,
                                        error_code='interrupted_without_receipt', last_sequence=?
                        WHERE workspace_id=? AND mission_id=? AND run_id=?
                          AND status IN ('queued','running')
                        """,
                        (now.isoformat(), sequence, workspace_id, mission_id, run_id),
                    )
                    connection.execute(
                        """
                        UPDATE missions SET status='blocked', state_version=state_version+1
                        WHERE workspace_id=? AND mission_id=?
                          AND status NOT IN ('completed','cancelled','blocked')
                        """,
                        (workspace_id, mission_id),
                    )
                    payload = {"status": "failed", "terminal_receipt_id": None,
                               "error_code": "interrupted_without_receipt"}
                    connection.execute(
                        """
                        INSERT INTO run_events
                            (workspace_id, mission_id, run_id, sequence, event_id,
                             event_type, occurred_at, public_payload_json)
                        VALUES (?, ?, ?, ?, ?, 'run_failed', ?, ?)
                        """,
                        (workspace_id, mission_id, run_id, sequence, str(sequence),
                         now.isoformat(), _canonical_json(payload)),
                    )
                    recovered += 1
            return recovered
        except WorkspaceStoreError:
            raise
        except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError) as exc:
            raise WorkspaceStoreUnavailableError() from exc

    def get_mission_draft_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> MissionDraftAttempt:
        self._require_path2_workspace(workspace_id)
        with self._connection() as connection:
            connection.execute("BEGIN")
            return _load_attempt(connection, workspace_id, attempt_id)

    def save_mission_draft_result(
        self,
        workspace_id: str,
        attempt_id: str,
        candidate: MissionDraftPayload,
        receipt: ProviderReceipt,
    ) -> MissionDraftAttempt:
        self._require_path2_workspace(workspace_id)
        candidate = MissionDraftPayload.model_validate(candidate.model_dump(mode="json"))
        receipt = _validated_attempt_receipt(workspace_id, attempt_id, receipt)
        if receipt.status != "succeeded":
            raise Path2StateError("provider_receipt_invalid")
        with self._write_transaction() as connection:
            attempt = _load_attempt(connection, workspace_id, attempt_id)
            if attempt.status in {"ready", "confirmed"}:
                if attempt.provider_receipt_id != receipt.receipt_id:
                    raise Path2StateError("state_conflict")
                saved = _load_provider_receipt(connection, workspace_id, receipt.receipt_id)
                if attempt.candidate != candidate or saved != receipt:
                    raise Path2StateError("state_conflict")
                return attempt
            if attempt.status != "running":
                raise Path2StateError("state_conflict")
            _insert_provider_receipt(connection, receipt)
            connection.execute(
                """
                UPDATE mission_draft_attempts
                SET status = 'ready', candidate_json = ?, candidate_version = 1,
                    candidate_sha256 = ?, provider_receipt_id = ?, error_code = NULL
                WHERE workspace_id = ? AND attempt_id = ? AND status = 'running'
                """,
                (_canonical_json(candidate), canonical_sha256(candidate), receipt.receipt_id,
                 workspace_id, attempt_id),
            )
            return _load_attempt(connection, workspace_id, attempt_id)

    def fail_mission_draft_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
        status: Literal["blocked", "failed", "cancelled"],
        code: str,
        receipt: ProviderReceipt | None,
    ) -> MissionDraftAttempt:
        self._require_path2_workspace(workspace_id)
        if status not in {"blocked", "failed", "cancelled"}:
            raise Path2StateError("state_conflict")
        if receipt is not None:
            receipt = _validated_attempt_receipt(workspace_id, attempt_id, receipt)
            # A malformed candidate can fail after a successfully accounted call.
            if receipt.status not in {status, "succeeded"}:
                raise Path2StateError("provider_receipt_invalid")
        with self._write_transaction() as connection:
            attempt = _load_attempt(connection, workspace_id, attempt_id)
            if attempt.status not in {"queued", "running"}:
                if (
                    attempt.status == status and attempt.error_code == code
                    and attempt.provider_receipt_id == (receipt.receipt_id if receipt else None)
                ):
                    if receipt is not None:
                        if _load_provider_receipt(connection, workspace_id, receipt.receipt_id) != receipt:
                            raise Path2StateError("state_conflict")
                    return attempt
                raise Path2StateError("state_conflict")
            if attempt.status == "queued" and receipt is not None:
                raise Path2StateError("state_conflict")
            updated = MissionDraftAttempt.model_validate({
                **attempt.model_dump(mode="json"),
                "status": status, "error_code": code,
                "provider_receipt_id": receipt.receipt_id if receipt else None,
            })
            if receipt is not None:
                _insert_provider_receipt(connection, receipt)
            connection.execute(
                """
                UPDATE mission_draft_attempts
                SET status = ?, error_code = ?, provider_receipt_id = ?
                WHERE workspace_id = ? AND attempt_id = ? AND status IN ('queued', 'running')
                """,
                (updated.status, updated.error_code, updated.provider_receipt_id,
                 workspace_id, attempt_id),
            )
            return _load_attempt(connection, workspace_id, attempt_id)

    def _validate_source_identities(
        self, connection: sqlite3.Connection, workspace_id: str,
        refs: list[SourceIdentity],
    ) -> None:
        for ref in refs:
            row = connection.execute(
                """
                SELECT workspace_id, source_id, revision_id, original_name,
                       media_type, byte_size, sha256, observed_at, effective_time,
                       permission_status, parse_status, parser_version, artifact_json
                FROM source_revisions WHERE workspace_id = ? AND revision_id = ?
                """,
                (workspace_id, ref.revision_id),
            ).fetchone()
            if row is None:
                raise SourceNotFoundError()
            try:
                revision, _, _ = _row_to_source(row)
            except (TypeError, ValueError) as exc:
                raise WorkspaceStoreUnavailableError() from exc
            if SourceIdentity.model_validate(
                revision.model_dump(include=set(SourceIdentity.model_fields))
            ) != ref:
                raise Path2StateError("source_revision_mismatch")
            if revision.permission_status != "read_allowed":
                raise Path2StateError("source_permission_denied")
            _read_validated_source_file(_source_path(self.data_dir, revision), revision)

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
            if not _schema_is_exact(read_connection):
                schema_check = StoreDiagnostic(
                    key="workspace_store_schema",
                    status="blocked",
                    detail="The Workspace database schema is unsupported.",
                    actual=f"user_version={version}; objects={len(objects)}",
                    expected=f"user_version={SCHEMA_VERSION}; exact v3 or v4 table set",
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
                        detail=f"The Workspace database uses schema version {version}.",
                        actual=f"user_version={version}",
                        expected=f"user_version={SCHEMA_VERSION}; exact v3 or v4 table set",
                    )
        except WorkspaceSchemaUnsupportedError:
            schema_check = StoreDiagnostic(
                key="workspace_store_schema",
                status="blocked",
                detail="The Workspace database schema is unsupported.",
                actual="unsupported",
                expected=f"user_version={SCHEMA_VERSION}; exact v3 or v4 table set",
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


def _validated_source_identities(
    workspace_id: str, source_refs: list[SourceIdentity],
) -> list[SourceIdentity]:
    refs = TypeAdapter(list[SourceIdentity]).validate_python(
        [ref.model_dump(mode="json") for ref in source_refs]
    )
    if len(refs) > 8 or len({ref.revision_id for ref in refs}) != len(refs):
        raise Path2StateError("source_refs_invalid")
    if any(ref.workspace_id != workspace_id for ref in refs):
        raise Path2StateError("source_permission_denied")
    return refs


def _load_attempt(
    connection: sqlite3.Connection, workspace_id: str, attempt_id: str,
) -> MissionDraftAttempt:
    row = connection.execute(
        """
        SELECT workspace_id, attempt_id, created_at, original_input, status,
               candidate_json, candidate_version, candidate_sha256,
               provider_receipt_id, mission_id, error_code
        FROM mission_draft_attempts WHERE workspace_id = ? AND attempt_id = ?
        """,
        (workspace_id, attempt_id),
    ).fetchone()
    if row is None:
        raise MissionDraftAttemptNotFoundError()
    try:
        attempt = MissionDraftAttempt(
            workspace_id=row[0], attempt_id=row[1], created_at=_parse_created_at(row[2]),
            original_input=row[3], status=row[4],
            candidate=_json_value(row[5]) if row[5] is not None else None,
            candidate_version=row[6], candidate_sha256=row[7],
            provider_receipt_id=row[8], mission_id=row[9], error_code=row[10],
        )
        if attempt.candidate is not None and attempt.candidate_version != 1:
            raise WorkspaceStoreUnavailableError()
        if attempt.status not in {"ready", "confirmed"} and attempt.candidate is not None:
            raise WorkspaceStoreUnavailableError()
        if attempt.status in {"queued", "running", "ready", "confirmed"} and attempt.error_code is not None:
            raise WorkspaceStoreUnavailableError()
        if attempt.status in {"queued", "running"} and attempt.provider_receipt_id is not None:
            raise WorkspaceStoreUnavailableError()
        if attempt.status in {"ready", "confirmed"} and (
            attempt.candidate is None or attempt.provider_receipt_id is None
        ):
            raise WorkspaceStoreUnavailableError()
        if attempt.provider_receipt_id is not None:
            receipt = _load_provider_receipt(connection, workspace_id, attempt.provider_receipt_id)
            try:
                _validated_attempt_receipt(workspace_id, attempt_id, receipt)
            except Path2StateError as exc:
                raise WorkspaceStoreUnavailableError() from exc
            if attempt.status in {"ready", "confirmed"} and receipt.status != "succeeded":
                raise WorkspaceStoreUnavailableError()
            if attempt.status in {"blocked", "failed", "cancelled"} and receipt.status not in {
                attempt.status, "succeeded"
            }:
                raise WorkspaceStoreUnavailableError()
        if attempt.status == "confirmed":
            parent = connection.execute(
                "SELECT original_attempt_id FROM missions WHERE workspace_id=? AND mission_id=?",
                (workspace_id, attempt.mission_id),
            ).fetchone()
            if parent != (attempt_id,):
                raise WorkspaceStoreUnavailableError()
        return attempt
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _validated_attempt_receipt(
    workspace_id: str, attempt_id: str, receipt: ProviderReceipt,
) -> ProviderReceipt:
    from contextox.agent import P0_DRAFT_SHA256

    try:
        validated = ProviderReceipt.model_validate(receipt.model_dump(mode="json"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise Path2StateError("provider_receipt_invalid") from exc
    if (
        validated.workspace_id != workspace_id
        or validated.attempt_id != attempt_id
        or validated.p0_sha256 != P0_DRAFT_SHA256
    ):
        raise Path2StateError("provider_receipt_invalid")
    return validated


def _insert_provider_receipt(
    connection: sqlite3.Connection, receipt: ProviderReceipt,
) -> None:
    try:
        connection.execute(
            """
            INSERT INTO provider_receipts
                (workspace_id, receipt_id, attempt_id, mission_id, run_id,
                 turn_index, created_at, status, config_json, p0_sha256,
                 input_tokens, output_tokens, cache_hit_tokens, cache_miss_tokens,
                 context_manifest_id, context_manifest_sha256, tool_schema_sha256, error_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (receipt.workspace_id, receipt.receipt_id, receipt.attempt_id,
             receipt.mission_id, receipt.run_id, receipt.turn_index,
             receipt.created_at.isoformat(), receipt.status, _canonical_json(receipt.config),
             receipt.p0_sha256, receipt.input_tokens, receipt.output_tokens,
             receipt.cache_hit_tokens, receipt.cache_miss_tokens, receipt.context_manifest_id,
             receipt.context_manifest_sha256, receipt.tool_schema_sha256, receipt.error_code),
        )
    except sqlite3.IntegrityError as exc:
        raise Path2StateError("provider_receipt_conflict") from exc


def _load_provider_receipt(
    connection: sqlite3.Connection, workspace_id: str, receipt_id: str,
) -> ProviderReceipt:
    cursor = connection.execute(
        "SELECT * FROM provider_receipts WHERE workspace_id = ? AND receipt_id = ?",
        (workspace_id, receipt_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise WorkspaceStoreUnavailableError()
    try:
        data = dict(zip((column[0] for column in cursor.description), row, strict=True))
        data["config"] = _json_value(data.pop("config_json"))
        data["created_at"] = _parse_created_at(data["created_at"])
        return ProviderReceipt.model_validate(data)
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _mission_from_row(connection: sqlite3.Connection, row: tuple) -> Mission:
    try:
        attempt = _load_attempt(connection, row[0], row[9])
        if attempt.status != "confirmed" or attempt.mission_id != row[1]:
            raise WorkspaceStoreUnavailableError()
        refs = connection.execute(
            """
            SELECT workspace_id, source_id, revision_id, sha256, ordinal
            FROM mission_sources WHERE workspace_id = ? AND mission_id = ?
            ORDER BY ordinal
            """,
            (row[0], row[1]),
        ).fetchall()
        if [ref[4] for ref in refs] != list(range(len(refs))):
            raise WorkspaceStoreUnavailableError()
        return Mission(
            workspace_id=row[0], mission_id=row[1], created_at=_parse_created_at(row[2]),
            state_version=row[3], status=row[4], title=row[5], goal=row[6],
            completion_criteria=_json_value(row[7]), scope_notes=_json_value(row[8]),
            original_attempt_id=row[9],
            source_refs=[
                SourceIdentity(workspace_id=ref[0], source_id=ref[1],
                               revision_id=ref[2], sha256=ref[3])
                for ref in refs
            ],
        )
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _load_mission(
    connection: sqlite3.Connection, workspace_id: str, mission_id: str,
) -> Mission:
    row = connection.execute(
        "SELECT * FROM missions WHERE workspace_id = ? AND mission_id = ?",
        (workspace_id, mission_id),
    ).fetchone()
    if row is None:
        raise MissionNotFoundError()
    return _mission_from_row(connection, row)


def _source_identity(revision: SourceRevision) -> SourceIdentity:
    return SourceIdentity.model_validate(
        revision.model_dump(include=set(SourceIdentity.model_fields))
    )


def _load_source_in_connection(
    connection: sqlite3.Connection, workspace_id: str, revision_id: str,
) -> tuple[SourceRevision, SourceArtifact]:
    row = connection.execute(
        """
        SELECT workspace_id, source_id, revision_id, original_name,
               media_type, byte_size, sha256, observed_at, effective_time,
               permission_status, parse_status, parser_version, artifact_json
        FROM source_revisions WHERE workspace_id = ? AND revision_id = ?
        """,
        (workspace_id, revision_id),
    ).fetchone()
    if row is None:
        raise SourceNotFoundError()
    revision, artifact, _ = _row_to_source(row)
    return revision, artifact


def _run_source_refs(
    connection: sqlite3.Connection, workspace_id: str, mission_id: str, run_id: str,
) -> list[SourceIdentity]:
    rows = connection.execute(
        """
        SELECT workspace_id, source_id, revision_id, sha256, ordinal
        FROM run_sources
        WHERE workspace_id = ? AND mission_id = ? AND run_id = ?
        ORDER BY ordinal
        """,
        (workspace_id, mission_id, run_id),
    ).fetchall()
    if [row[4] for row in rows] != list(range(len(rows))):
        raise WorkspaceStoreUnavailableError()
    return [
        SourceIdentity(
            workspace_id=row[0], source_id=row[1], revision_id=row[2], sha256=row[3]
        )
        for row in rows
    ]


def _draft_from_row(row: tuple[object, ...]) -> DefinitionDraft:
    try:
        draft = DefinitionDraft(
            workspace_id=row[0], mission_id=row[1], draft_id=row[2], version=row[3],
            sha256=row[4], status=row[5], semantic_approval=row[6],
            fields=_json_value(row[7]), relationships=_json_value(row[8]),
            unresolved_items=_json_value(row[9]),
        )
        if _canonical_json(draft.fields) != row[7]:
            raise WorkspaceStoreUnavailableError()
        if _canonical_json(draft.relationships) != row[8]:
            raise WorkspaceStoreUnavailableError()
        if _canonical_json(draft.unresolved_items) != row[9]:
            raise WorkspaceStoreUnavailableError()
        return draft
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _load_latest_draft(
    connection: sqlite3.Connection, workspace_id: str, mission_id: str,
) -> DefinitionDraft | None:
    row = connection.execute(
        """
        SELECT workspace_id, mission_id, draft_id, version, sha256, status,
               semantic_approval, fields_json, relationships_json, unresolved_items_json
        FROM definition_drafts
        WHERE workspace_id = ? AND mission_id = ?
        ORDER BY version DESC LIMIT 1
        """,
        (workspace_id, mission_id),
    ).fetchone()
    return None if row is None else _draft_from_row(row)


def _clarification_from_row(row: tuple[object, ...]) -> ClarificationRequest:
    try:
        clarification = ClarificationRequest(
            workspace_id=row[0], mission_id=row[1], run_id=row[2],
            clarification_id=row[3], draft_version=row[4], draft_sha256=row[5],
            status=row[6], questions=_json_value(row[7]),
        )
        if _canonical_json(clarification.questions) != row[7]:
            raise WorkspaceStoreUnavailableError()
        return clarification
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _load_clarifications(
    connection: sqlite3.Connection, workspace_id: str, mission_id: str,
    run_id: str | None = None,
) -> list[ClarificationRequest]:
    sql = """
        SELECT workspace_id, mission_id, run_id, clarification_id,
               draft_version, draft_sha256, status, questions_json
        FROM clarification_requests
        WHERE workspace_id = ? AND mission_id = ?
    """
    params: tuple[str, ...] = (workspace_id, mission_id)
    if run_id is not None:
        sql += " AND run_id = ?"
        params = (workspace_id, mission_id, run_id)
    sql += " ORDER BY rowid"
    return [_clarification_from_row(row) for row in connection.execute(sql, params).fetchall()]


def _terminal_from_row(row: tuple[object, ...]) -> TerminalReceipt:
    try:
        receipt = TerminalReceipt(
            workspace_id=row[0], mission_id=row[1], run_id=row[2], receipt_id=row[3],
            created_at=_parse_created_at(row[4]), terminal_tool=row[5], outcome=row[6],
            draft_id=row[7], draft_version=row[8], draft_sha256=row[9],
            clarification_ids=_json_value(row[10]),
            provider_receipt_ids=_json_value(row[11]),
            tool_receipt_ids=_json_value(row[12]), source_refs=_json_value(row[13]),
        )
        if any(
            _canonical_json(value) != raw
            for value, raw in (
                (receipt.clarification_ids, row[10]),
                (receipt.provider_receipt_ids, row[11]),
                (receipt.tool_receipt_ids, row[12]),
                (receipt.source_refs, row[13]),
            )
        ):
            raise WorkspaceStoreUnavailableError()
        return receipt
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _load_terminal_receipt(
    connection: sqlite3.Connection, workspace_id: str, mission_id: str, run_id: str,
) -> TerminalReceipt | None:
    row = connection.execute(
        """
        SELECT workspace_id, mission_id, run_id, receipt_id, created_at,
               terminal_tool, outcome, draft_id, draft_version, draft_sha256,
               clarification_ids_json, provider_receipt_ids_json,
               tool_receipt_ids_json, source_refs_json
        FROM terminal_receipts
        WHERE workspace_id = ? AND mission_id = ? AND run_id = ?
        """,
        (workspace_id, mission_id, run_id),
    ).fetchone()
    return None if row is None else _terminal_from_row(row)


def _run_from_row(connection: sqlite3.Connection, row: tuple[object, ...]) -> RunSnapshot:
    try:
        run = RunSnapshot(
            workspace_id=row[0], mission_id=row[1], run_id=row[2],
            created_at=_parse_created_at(row[4]),
            started_at=_parse_created_at(row[5]) if row[5] is not None else None,
            finished_at=_parse_created_at(row[6]) if row[6] is not None else None,
            status=row[7], budget=_json_value(row[8]),
            source_refs=_run_source_refs(connection, row[0], row[1], row[2]),
            draft=_load_latest_draft(connection, row[0], row[1]),
            clarifications=_load_clarifications(connection, row[0], row[1], row[2]),
            last_sequence=row[9], terminal_receipt=_load_terminal_receipt(
                connection, row[0], row[1], row[2]
            ), final_output=row[10], error_code=row[11],
        )
        if _canonical_json(run.budget) != row[8]:
            raise WorkspaceStoreUnavailableError()
        active = run.status in {"queued", "running"}
        terminal = run.status in {
            "waiting_for_human", "partial", "completed", "blocked", "failed", "cancelled"
        }
        if run.status == "queued" and run.started_at is not None:
            raise WorkspaceStoreUnavailableError()
        if run.status == "running" and run.started_at is None:
            raise WorkspaceStoreUnavailableError()
        if active and run.finished_at is not None:
            raise WorkspaceStoreUnavailableError()
        if terminal and run.finished_at is None:
            raise WorkspaceStoreUnavailableError()
        if run.status in {"waiting_for_human", "partial"} and run.terminal_receipt is None:
            raise WorkspaceStoreUnavailableError()
        if run.status not in {"waiting_for_human", "partial"} and run.terminal_receipt is not None:
            raise WorkspaceStoreUnavailableError()
        if run.final_output is not None and run.terminal_receipt is None:
            raise WorkspaceStoreUnavailableError()
        if run.status in {"queued", "running", "waiting_for_human", "partial"}:
            if run.error_code is not None:
                raise WorkspaceStoreUnavailableError()
        elif run.status in {"blocked", "failed", "cancelled"} and run.error_code is None:
            raise WorkspaceStoreUnavailableError()
        from contextox.agent import SUPPORTED_RUN_HASH_PAIRS

        provider_rows = connection.execute(
            """
            SELECT receipt_id FROM provider_receipts
            WHERE workspace_id=? AND mission_id=? AND run_id=? ORDER BY turn_index
            """,
            (run.workspace_id, run.mission_id, run.run_id),
        ).fetchall()
        provider_receipts = [
            _load_provider_receipt(connection, run.workspace_id, item[0])
            for item in provider_rows
        ]
        if [item.turn_index for item in provider_receipts] != list(
            range(1, len(provider_receipts) + 1)
        ):
            raise WorkspaceStoreUnavailableError()
        for receipt in provider_receipts:
            manifest_row = connection.execute(
                """
                SELECT workspace_id, mission_id, run_id, manifest_id,
                       mission_state_version, turn_index, draft_id, draft_version,
                       draft_sha256, source_refs_json, clarification_ids_json,
                       tool_receipt_ids_json, budget_json, excluded_reasons_json, sha256
                FROM context_manifests
                WHERE workspace_id=? AND mission_id=? AND run_id=? AND manifest_id=?
                """,
                (run.workspace_id, run.mission_id, run.run_id,
                 receipt.context_manifest_id),
            ).fetchone()
            if manifest_row is None:
                raise WorkspaceStoreUnavailableError()
            manifest = _context_manifest_from_row(manifest_row)
            if (
                receipt.workspace_id != run.workspace_id
                or receipt.mission_id != run.mission_id
                or receipt.run_id != run.run_id
                or receipt.attempt_id is not None
                or (receipt.p0_sha256, receipt.tool_schema_sha256) not in SUPPORTED_RUN_HASH_PAIRS
                or receipt.turn_index != manifest.turn_index
                or receipt.context_manifest_sha256 != manifest.sha256
            ):
                raise WorkspaceStoreUnavailableError()
        selected = {
            (ref.workspace_id, ref.source_id, ref.revision_id, ref.sha256)
            for ref in run.source_refs
        }
        tool_receipts = _load_tool_receipts(
            connection, run.workspace_id, run.mission_id, run.run_id
        )
        if any(
            (ref.workspace_id, ref.source_id, ref.revision_id, ref.sha256) not in selected
            for receipt in tool_receipts for ref in receipt.source_refs
        ):
            raise WorkspaceStoreUnavailableError()
        if run.terminal_receipt is not None:
            terminal = run.terminal_receipt
            if terminal.outcome != run.status:
                raise WorkspaceStoreUnavailableError()
            provider_ids = [
                item[0] for item in connection.execute(
                    """
                    SELECT receipt_id FROM provider_receipts
                    WHERE workspace_id=? AND mission_id=? AND run_id=? ORDER BY turn_index
                    """,
                    (run.workspace_id, run.mission_id, run.run_id),
                ).fetchall()
            ]
            clarification_ids = [item.clarification_id for item in run.clarifications]
            if (
                terminal.provider_receipt_ids != provider_ids
                or terminal.tool_receipt_ids != [item.receipt_id for item in tool_receipts]
                or terminal.clarification_ids != clarification_ids
                or any(
                    (ref.workspace_id, ref.source_id, ref.revision_id, ref.sha256)
                    not in selected for ref in terminal.source_refs
                )
                or not tool_receipts
                or tool_receipts[-1].name != terminal.terminal_tool
            ):
                raise WorkspaceStoreUnavailableError()
            if terminal.draft_id is not None:
                draft_row = connection.execute(
                    """
                    SELECT 1 FROM definition_drafts
                    WHERE workspace_id=? AND mission_id=? AND draft_id=?
                      AND version=? AND sha256=?
                    """,
                    (run.workspace_id, run.mission_id, terminal.draft_id,
                     terminal.draft_version, terminal.draft_sha256),
                ).fetchone()
                if draft_row is None:
                    raise WorkspaceStoreUnavailableError()
        assistant_rows = connection.execute(
            """
            SELECT content FROM mission_messages
            WHERE workspace_id=? AND mission_id=? AND run_id=? AND role='assistant'
            """,
            (run.workspace_id, run.mission_id, run.run_id),
        ).fetchall()
        if run.final_output is None and assistant_rows:
            raise WorkspaceStoreUnavailableError()
        if run.final_output is not None and assistant_rows != [(run.final_output,)]:
            raise WorkspaceStoreUnavailableError()
        if (
            not isinstance(row[3], str)
            or not isinstance(row[12], str)
            or len(row[12]) != 64
            or any(character not in "0123456789abcdef" for character in row[12])
        ):
            raise WorkspaceStoreUnavailableError()
        return run
    except WorkspaceStoreError:
        raise
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _load_run(
    connection: sqlite3.Connection, workspace_id: str, mission_id: str, run_id: str,
) -> RunSnapshot:
    row = connection.execute(
        """
        SELECT workspace_id, mission_id, run_id, client_request_id, created_at,
               started_at, finished_at, status, budget_json, last_sequence,
               final_output, error_code, start_request_sha256
        FROM runs WHERE workspace_id = ? AND mission_id = ? AND run_id = ?
        """,
        (workspace_id, mission_id, run_id),
    ).fetchone()
    if row is None:
        raise Path2StateError("run_not_found")
    return _run_from_row(connection, row)


def _tool_receipt_from_row(row: tuple[object, ...]) -> ToolReceipt:
    try:
        receipt = ToolReceipt(
            workspace_id=row[0], mission_id=row[1], run_id=row[2], receipt_id=row[3],
            ordinal=row[4], call_id=row[5], name=row[6], arguments_sha256=row[7],
            status=row[8], created_at=_parse_created_at(row[9]),
            source_refs=_json_value(row[10]), error_code=row[11],
        )
        if _canonical_json(receipt.source_refs) != row[10]:
            raise WorkspaceStoreUnavailableError()
        return receipt
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _load_tool_receipts(
    connection: sqlite3.Connection, workspace_id: str, mission_id: str, run_id: str,
) -> list[ToolReceipt]:
    rows = connection.execute(
        """
        SELECT workspace_id, mission_id, run_id, receipt_id, ordinal, call_id,
               name, arguments_sha256, status, created_at, source_refs_json, error_code
        FROM tool_receipts
        WHERE workspace_id = ? AND mission_id = ? AND run_id = ?
        ORDER BY ordinal
        """,
        (workspace_id, mission_id, run_id),
    ).fetchall()
    receipts = [_tool_receipt_from_row(row) for row in rows]
    if [receipt.ordinal for receipt in receipts] != list(range(1, len(receipts) + 1)):
        raise WorkspaceStoreUnavailableError()
    return receipts


def _insert_tool_receipt(
    connection: sqlite3.Connection, receipt: ToolReceipt,
) -> None:
    connection.execute(
        """
        INSERT INTO tool_receipts
            (workspace_id, mission_id, run_id, receipt_id, ordinal, call_id,
             name, arguments_sha256, status, created_at, source_refs_json, error_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (receipt.workspace_id, receipt.mission_id, receipt.run_id, receipt.receipt_id,
         receipt.ordinal, receipt.call_id, receipt.name, receipt.arguments_sha256,
         receipt.status, receipt.created_at.isoformat(),
         _canonical_json(receipt.source_refs), receipt.error_code),
    )


def _append_event_in_transaction(
    connection: sqlite3.Connection, run: RunSnapshot, event_type: str,
    public_payload: dict[str, object],
) -> RunEventEnvelope:
    current = connection.execute(
        """
        SELECT last_sequence FROM runs
        WHERE workspace_id=? AND mission_id=? AND run_id=?
        """,
        (run.workspace_id, run.mission_id, run.run_id),
    ).fetchone()
    if current is None:
        raise Path2StateError("run_not_found")
    sequence = current[0] + 1
    occurred_at = _utc_now()
    envelope = RunEventEnvelope.model_validate({
        "event_id": str(sequence), "event_type": event_type,
        "occurred_at": occurred_at, "workspace_id": run.workspace_id,
        "mission_id": run.mission_id, "run_id": run.run_id,
        "sequence": sequence, "public_payload": public_payload,
    })
    connection.execute(
        """
        INSERT INTO run_events
            (workspace_id, mission_id, run_id, sequence, event_id,
             event_type, occurred_at, public_payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run.workspace_id, run.mission_id, run.run_id, sequence,
         envelope.event_id, envelope.event_type, occurred_at.isoformat(),
         _canonical_json(envelope.public_payload)),
    )
    connection.execute(
        """
        UPDATE runs SET last_sequence=?
        WHERE workspace_id=? AND mission_id=? AND run_id=? AND last_sequence=?
        """,
        (sequence, run.workspace_id, run.mission_id, run.run_id, current[0]),
    )
    return envelope


def _context_manifest_from_row(row: tuple[object, ...]) -> ContextPacketManifest:
    try:
        packet = ContextPacketManifest(
            workspace_id=row[0], mission_id=row[1], run_id=row[2], manifest_id=row[3],
            mission_state_version=row[4], turn_index=row[5], draft_id=row[6],
            draft_version=row[7], draft_sha256=row[8], source_refs=_json_value(row[9]),
            clarification_ids=_json_value(row[10]), tool_receipt_ids=_json_value(row[11]),
            budget=_json_value(row[12]), excluded_reasons=_json_value(row[13]), sha256=row[14],
        )
        for value, raw in (
            (packet.source_refs, row[9]), (packet.clarification_ids, row[10]),
            (packet.tool_receipt_ids, row[11]), (packet.budget, row[12]),
            (packet.excluded_reasons, row[13]),
        ):
            if _canonical_json(value) != raw:
                raise WorkspaceStoreUnavailableError()
        return packet
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _run_event_from_row(row: tuple[object, ...]) -> RunEventEnvelope:
    try:
        envelope = RunEventEnvelope.model_validate({
            "event_id": row[0], "event_type": row[1],
            "occurred_at": _parse_created_at(row[2]), "workspace_id": row[3],
            "mission_id": row[4], "run_id": row[5], "sequence": row[6],
            "public_payload": _json_value(row[7]),
        })
        if envelope.event_id != str(envelope.root.sequence):
            raise WorkspaceStoreUnavailableError()
        if _canonical_json(envelope.public_payload) != row[7]:
            raise WorkspaceStoreUnavailableError()
        return envelope
    except (TypeError, ValueError) as exc:
        raise WorkspaceStoreUnavailableError() from exc


def _run_event_allowed(run: RunSnapshot, event: RunEventInput) -> bool:
    event_type = event.event_type
    if event_type == "run_completed":
        return False
    required_status = {
        "run_started": "running",
        "clarification_requested": "waiting_for_human",
        "run_partial": "partial",
        "run_blocked": "blocked",
        "run_failed": "failed",
        "run_cancelled": "cancelled",
    }.get(event_type)
    if required_status is not None:
        return run.status == required_status
    if event_type == "tool_completed":
        return run.status in {"running", "waiting_for_human", "partial"}
    if event_type == "message_created":
        return run.status != "completed"
    return run.status == "running"


def _evidence_refs(value: object) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    if isinstance(value, SourceExcerpt):
        refs.append(value.source_ref)
    elif isinstance(value, TableProfile):
        refs.extend(value.source_refs)
        refs.extend(ref for row in value.sample_rows for ref in row.source_refs)
    elif isinstance(value, RelationshipProfile):
        refs.extend(value.source_refs)
    elif isinstance(value, DefinitionDraft):
        refs.extend(ref for field in value.fields for ref in field.source_refs)
        refs.extend(ref for relationship in value.relationships for ref in relationship.source_refs)
    elif isinstance(value, ClarificationRequest):
        refs.extend(ref for question in value.questions for ref in question.source_refs)
    elif isinstance(value, TerminalReceipt):
        refs.extend(value.source_refs)
    unique: list[EvidenceRef] = []
    seen: set[str] = set()
    for ref in refs:
        key = _canonical_json(ref)
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique


def _message_input_row(connection: sqlite3.Connection, workspace_id: str,
                       mission_id: str, run_id: str) -> tuple | None:
    if connection.execute("PRAGMA user_version").fetchone()[0] == 3:
        return None
    return connection.execute(
        "SELECT message_id, expected_state_version, references_json, history_messages_json "
        "FROM run_message_inputs WHERE workspace_id=? AND mission_id=? AND run_id=?",
        (workspace_id, mission_id, run_id),
    ).fetchone()


def _task_page_ids(connection: sqlite3.Connection, table: str, key: str,
                   workspace_id: str, mission_id: str, before: str | None, limit: int) -> list:
    if (table, key) not in {("runs", "run_id"), ("mission_messages", "message_id")}:
        raise ValueError("unsupported page")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise Path2StateError("page_limit_invalid")
    params: list = [workspace_id, mission_id]
    clause = ""
    if before is not None:
        row = connection.execute(
            f"SELECT rowid FROM {table} WHERE workspace_id=? AND mission_id=? AND {key}=?",
            (*params, before),
        ).fetchone()
        if row is None:
            raise Path2StateError("message_not_found" if table == "mission_messages" else "run_not_found")
        clause = " AND rowid < ?"
        params.append(row[0])
    return connection.execute(
        f"SELECT {key} FROM {table} WHERE workspace_id=? AND mission_id=?{clause} ORDER BY rowid DESC LIMIT ?",
        (*params, limit + 1),
    ).fetchall()


def _check_payload_source_scope(value: Any, selected: list[SourceIdentity]) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, list):
        for item in value:
            _check_payload_source_scope(item, selected)
    elif isinstance(value, dict):
        if {"workspace_id", "source_id", "revision_id", "sha256"} <= value.keys():
            source = SourceIdentity.model_validate({key: value[key] for key in SourceIdentity.model_fields})
            if source not in selected:
                raise Path2StateError("message_context_scope_mismatch")
        for item in value.values():
            _check_payload_source_scope(item, selected)


def _check_message_start_state(connection: sqlite3.Connection, mission: Mission,
                               *, allow_partial: bool) -> None:
    ws, mid = mission.workspace_id, mission.mission_id
    draft = _load_latest_draft(connection, ws, mid)
    if mission.status == "waiting_for_human" or (draft and draft.status == "in_review") or _load_clarifications(connection, ws, mid):
        raise Path2StateError("task_waiting_for_review")
    if mission.status not in {"active", "blocked"}:
        raise Path2StateError("state_conflict")
    rows = connection.execute(
        "SELECT run_id FROM runs WHERE workspace_id=? AND mission_id=? ORDER BY rowid DESC", (ws, mid)
    ).fetchall()
    if not rows:
        if mission.status != "active":
            raise Path2StateError("state_conflict")
        return
    latest = _load_run(connection, ws, mid, rows[0][0])
    if any(connection.execute(
        "SELECT 1 FROM runs WHERE workspace_id=? AND mission_id=? AND status IN ('queued','running')", (ws, mid)
    )):
        raise Path2StateError("run_already_active")
    if mission.status == "blocked" and latest.status not in (
        {"partial", "failed", "blocked", "cancelled"} if allow_partial else {"failed", "blocked"}
    ):
        raise Path2StateError("state_conflict")
    # Reconcile every model turn from durable events and receipts, never a UI status.
    for (run_id,) in rows:
        receipts = [_load_provider_receipt(connection, ws, row[0]) for row in connection.execute(
            "SELECT receipt_id FROM provider_receipts WHERE workspace_id=? AND mission_id=? AND run_id=?",
            (ws, mid, run_id),
        )]
        started = {
            _json_value(row[0])["turn_index"] for row in connection.execute(
                "SELECT public_payload_json FROM run_events WHERE workspace_id=? AND mission_id=? "
                "AND run_id=? AND event_type='model_started'", (ws, mid, run_id),
            )
        }
        if started - {receipt.turn_index for receipt in receipts} or any(
            receipt.input_tokens is None or receipt.output_tokens is None or
            "unknown" in (receipt.error_code or "") for receipt in receipts
        ):
            raise Path2StateError("previous_outcome_unresolved")
