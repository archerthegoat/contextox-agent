"""Deterministic, bounded source parsing and evidence inspection.

The functions in this module deliberately operate only on caller-supplied
bytes.  They do not open files, access the Store, make network requests, or
decide whether an observed relationship or field meaning is business truth.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Any

from pydantic import TypeAdapter, ValidationError

from contextox.models import (
    ColumnProfile,
    CsvRowsLocator,
    EvidenceLocator,
    EvidenceRef,
    JsonPointerLocator,
    Key,
    RelationshipProfile,
    SampleCell,
    SampleRow,
    SourceArtifact,
    SourceExcerpt,
    SourceIdentity,
    SourceIssue,
    SourceRevision,
    TableKey,
    TableProfile,
    TextLinesLocator,
)


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TABLE_ROWS = 5_000
MAX_TABLE_COLUMNS = 100
MAX_SAMPLE_ROWS = 5
MAX_SAMPLE_CELL_CHARS = 256
MAX_EXCERPT_CHARS = 8_192
MAX_NAMED_JSON_TABLES = 16
MAX_JSON_POINTER_CHARS = 4_096
PARSER_VERSION = "path2-w1-v0.1"


class SourceInputError(ValueError):
    """A bounded input or locator error safe for the W0 error boundary."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail or code
        super().__init__(f"{code}: {self.detail}")


@dataclass(frozen=True)
class _Cell:
    kind: str
    text: str | None


@dataclass(frozen=True)
class _JsonNumber:
    kind: str
    raw: str


@dataclass
class _Table:
    table_id: str
    columns: list[str]
    rows: list[dict[str, _Cell]]
    locator_kind: str
    row_numbers: list[int]
    issues: list[SourceIssue]


@dataclass
class _ParsedDocument:
    tables: list[_Table]
    text_line_count: int | None
    issues: list[SourceIssue]
    status: str


_KEY_ADAPTER = TypeAdapter(Key)
_INTEGER_TOKEN = re.compile(r"^[+-]?\d+$")
_DECIMAL_TOKEN = re.compile(
    r"^[+-]?(?:(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)$"
)
_ARRAY_INDEX = re.compile(r"^(?:0|[1-9]\d*)$")
_CSV_FIELD_LIMIT_LOCK = Lock()


def _issue(
    code: str,
    message: str,
    locator: EvidenceLocator | None = None,
) -> SourceIssue:
    return SourceIssue(code=code, locator=locator, message=message)


def _identity_values(revision: SourceRevision) -> dict[str, str]:
    return {
        "workspace_id": revision.workspace_id,
        "source_id": revision.source_id,
        "revision_id": revision.revision_id,
        "sha256": revision.sha256,
    }


def _source_identity(revision: SourceRevision) -> SourceIdentity:
    return SourceIdentity(**_identity_values(revision))


def _same_identity(
    source_ref: SourceIdentity | EvidenceRef,
    revision: SourceRevision,
) -> bool:
    values = _identity_values(revision)
    return all(getattr(source_ref, key) == value for key, value in values.items())


def _validate_source_input(
    revision: SourceRevision,
    content: bytes,
    *,
    allow_oversized: bool,
) -> bool:
    if not isinstance(revision, SourceRevision):
        raise SourceInputError("invalid_revision")
    if revision.permission_status != "read_allowed":
        raise SourceInputError("source_permission_not_allowed")
    if not isinstance(content, bytes):
        raise SourceInputError("source_content_type_invalid")
    if revision.byte_size != len(content):
        raise SourceInputError("source_byte_size_mismatch")
    oversized = len(content) > MAX_FILE_BYTES
    if oversized and not allow_oversized:
        raise SourceInputError("source_file_too_large")
    if hashlib.sha256(content).hexdigest() != revision.sha256:
        raise SourceInputError("source_hash_mismatch")
    return oversized


def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceInputError("source_utf8_invalid") from exc


def _artifact(
    revision: SourceRevision,
    *,
    status: str,
    tables: list[TableProfile] | None = None,
    text_line_count: int | None = None,
    issues: list[SourceIssue] | None = None,
) -> SourceArtifact:
    return SourceArtifact(
        source_ref=_source_identity(revision),
        parser_version=PARSER_VERSION,
        parse_status=status,
        tables=tables or [],
        text_line_count=text_line_count,
        issues=issues or [],
    )


def _invalid_utf8_artifact(revision: SourceRevision) -> SourceArtifact:
    return _artifact(
        revision,
        status="failed",
        issues=[_issue("source_utf8_invalid", "Source bytes are not valid UTF-8.")],
    )


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _valid_key(value: object) -> bool:
    try:
        _KEY_ADAPTER.validate_python(value)
    except ValidationError:
        return False
    return True


def _csv_cell(value: str) -> _Cell:
    if not value:
        return _Cell("string", value)
    if _INTEGER_TOKEN.fullmatch(value):
        digits = value.lstrip("+-")
        if len(digits) == 1 or not digits.startswith("0"):
            return _Cell("integer", value)
        return _Cell("string", value)
    if _DECIMAL_TOKEN.fullmatch(value):
        mantissa = value
        for marker in ("e", "E"):
            if marker in mantissa:
                mantissa = mantissa.split(marker, 1)[0]
                break
        integer_part = mantissa.lstrip("+-").split(".", 1)[0]
        if len(integer_part) > 1 and integer_part.startswith("0"):
            return _Cell("string", value)
        try:
            number = Decimal(value)
        except InvalidOperation:
            return _Cell("string", value)
        if number.is_finite():
            return _Cell("decimal", value)
    return _Cell("string", value)


def _read_csv_records(text: str) -> tuple[list[list[str]], bool]:
    """Read bounded CSV text while restoring the process-wide csv limit."""

    with _CSV_FIELD_LIMIT_LOCK:
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(MAX_FILE_BYTES)
        try:
            with io.StringIO(text, newline="") as stream:
                reader = csv.reader(stream, strict=True)
                records: list[list[str]] = []
                try:
                    for record in reader:
                        records.append(record)
                except csv.Error:
                    return records, True
                return records, False
        finally:
            csv.field_size_limit(previous_limit)


def _csv_rows_locator(row_start: int, row_end: int) -> CsvRowsLocator:
    return CsvRowsLocator(
        kind="csv_rows",
        row_start=row_start,
        row_end=row_end,
        column=None,
    )


def _json_pointer_locator(pointer: str) -> JsonPointerLocator:
    return JsonPointerLocator(kind="json_pointer", pointer=pointer)


def _text_lines_locator(line_start: int, line_end: int) -> TextLinesLocator:
    return TextLinesLocator(
        kind="text_lines",
        line_start=line_start,
        line_end=line_end,
    )


def _evidence_ref(revision: SourceRevision, locator: EvidenceLocator) -> EvidenceRef:
    return EvidenceRef(**_identity_values(revision), locator=locator)


def _escape_json_pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _append_json_pointer(pointer: str, segment: str) -> str:
    return f"{pointer}/{_escape_json_pointer_segment(segment)}"


def _table_locator(table: _Table, row_count: int) -> EvidenceLocator | None:
    if table.locator_kind == "csv":
        if row_count == 0:
            return None
        return _csv_rows_locator(1, row_count)
    return _json_pointer_locator(table.table_id)


def _row_locator(table: _Table, row_number: int) -> EvidenceLocator:
    if table.locator_kind == "csv":
        return _csv_rows_locator(row_number, row_number)
    return _json_pointer_locator(_append_json_pointer(table.table_id, str(row_number - 1)))


def _numeric_bounds(values: list[_Cell]) -> tuple[str | None, str | None]:
    numeric: list[tuple[Decimal, str]] = []
    for value in values:
        if value.kind not in {"integer", "decimal"} or value.text is None:
            continue
        if len(value.text) > 4096:
            continue
        try:
            parsed = Decimal(value.text)
        except InvalidOperation:
            continue
        if parsed.is_finite():
            numeric.append((parsed, value.text))
    if not numeric:
        return None, None
    minimum = min(numeric, key=lambda item: item[0])[1]
    maximum = max(numeric, key=lambda item: item[0])[1]
    return minimum, maximum


def _profile_table(table: _Table, revision: SourceRevision) -> TableProfile:
    columns: list[ColumnProfile] = []
    for name in table.columns:
        values = [row[name] for row in table.rows]
        observed_types: list[str] = []
        distinct_values: set[tuple[str, str | None]] = set()
        missing_count = 0
        null_count = 0
        for value in values:
            if value.kind == "missing":
                missing_count += 1
                continue
            if value.kind == "null":
                null_count += 1
                continue
            if value.kind not in observed_types:
                observed_types.append(value.kind)
            distinct_values.add((value.kind, value.text))
        numeric_min, numeric_max = _numeric_bounds(values)
        columns.append(
            ColumnProfile(
                name=name,
                observed_types=observed_types,
                missing_count=missing_count,
                null_count=null_count,
                distinct_count=len(distinct_values),
                numeric_min=numeric_min,
                numeric_max=numeric_max,
            )
        )

    seen_rows: set[tuple[tuple[str, str, str | None], ...]] = set()
    duplicate_row_count = 0
    for row in table.rows:
        identity = tuple(
            (name, row[name].kind, row[name].text) for name in table.columns
        )
        if identity in seen_rows:
            duplicate_row_count += 1
        else:
            seen_rows.add(identity)

    sample_rows: list[SampleRow] = []
    for row_number, row in list(
        zip(table.row_numbers, table.rows, strict=True)
    )[:MAX_SAMPLE_ROWS]:
        cells: list[SampleCell] = []
        for name in table.columns:
            value = row[name]
            if value.kind in {"missing", "null"}:
                text = None
                truncated = False
            else:
                text, truncated = _bounded_text(
                    value.text or "",
                    MAX_SAMPLE_CELL_CHARS,
                )
            cells.append(
                SampleCell(
                    column_name=name,
                    value_kind=value.kind,
                    text=text,
                    truncated=truncated,
                )
            )
        sample_rows.append(
            SampleRow(
                row_number=row_number,
                cells=cells,
                source_refs=[_evidence_ref(revision, _row_locator(table, row_number))],
            )
        )

    table_locator = _table_locator(table, len(table.rows))
    source_refs = (
        [_evidence_ref(revision, table_locator)]
        if table_locator is not None
        else []
    )
    return TableProfile(
        table_id=table.table_id,
        row_count=len(table.rows),
        columns=columns,
        duplicate_row_count=duplicate_row_count,
        sample_rows=sample_rows,
        source_refs=source_refs,
    )


def _parse_csv(text: str) -> _ParsedDocument:
    if text == "":
        return _ParsedDocument(
            tables=[_Table("", [], [], "csv", [], [])],
            text_line_count=None,
            issues=[],
            status="ready",
        )

    try:
        records, malformed = _read_csv_records(text)
    except csv.Error:
        return _ParsedDocument(
            tables=[],
            text_line_count=None,
            issues=[_issue("csv_malformed", "CSV content is malformed.")],
            status="failed",
        )
    if malformed and len(records) <= 1:
        return _ParsedDocument(
            tables=[],
            text_line_count=None,
            issues=[_issue("csv_malformed", "CSV content is malformed.")],
            status="failed",
        )
    if not records:
        return _ParsedDocument(
            tables=[_Table("", [], [], "csv", [], [])],
            text_line_count=None,
            issues=[],
            status="ready",
        )
    header = records[0]

    if (
        not header
        or len(header) > MAX_TABLE_COLUMNS
        or any(not _valid_key(name) for name in header)
        or len(set(header)) != len(header)
    ):
        code = "csv_column_limit" if len(header) > MAX_TABLE_COLUMNS else "csv_header_invalid"
        return _ParsedDocument(
            tables=[],
            text_line_count=None,
            issues=[_issue(code, "CSV header is empty, duplicated, or unsupported.")],
            status="blocked" if code == "csv_column_limit" else "failed",
        )

    rows: list[dict[str, _Cell]] = []
    issues: list[SourceIssue] = []
    if malformed:
        issues.append(_issue("csv_malformed", "CSV content is malformed."))
    row_number = 0
    for record in records[1:]:
        row_number += 1
        if row_number > MAX_TABLE_ROWS:
            return _ParsedDocument(
                tables=[],
                text_line_count=None,
                issues=[
                    _issue(
                        "csv_row_limit",
                        "CSV table exceeds the row limit.",
                    )
                ],
                status="blocked",
            )
        if len(record) != len(header):
            issues.append(
                _issue(
                    "csv_row_width_mismatch",
                    "CSV row width does not match the header.",
                    _csv_rows_locator(row_number, row_number),
                )
            )
            break
        rows.append({name: _csv_cell(value) for name, value in zip(header, record)})

    table = _Table("", header, rows, "csv", list(range(1, len(rows) + 1)), issues)
    return _ParsedDocument(
        tables=[table],
        text_line_count=None,
        issues=issues,
        status="partial" if issues else "ready",
    )


class _JsonFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json_integer(raw: str) -> _JsonNumber:
    return _JsonNumber("integer", raw)


def _json_decimal(raw: str) -> _JsonNumber:
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise _JsonFailure("json_number_invalid") from exc
    if not parsed.is_finite():
        raise _JsonFailure("json_non_finite_number")
    return _JsonNumber("decimal", raw)


def _json_constant(_: str) -> _JsonNumber:
    raise _JsonFailure("json_non_finite_number")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonFailure("json_duplicate_key")
        result[key] = value
    return result


def _load_json(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_json_object,
            parse_int=_json_integer,
            parse_float=_json_decimal,
            parse_constant=_json_constant,
        )
    except _JsonFailure:
        raise
    except RecursionError as exc:
        raise _JsonFailure("json_nesting_limit") from exc
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _JsonFailure("json_invalid") from exc


def _serialize_json_value(value: Any) -> str:
    if isinstance(value, _JsonNumber):
        return value.raw
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_serialize_json_value(item) for item in value) + "]"
    if isinstance(value, dict):
        pairs = [
            json.dumps(key, ensure_ascii=False, separators=(",", ":"))
            + ":"
            + _serialize_json_value(item)
            for key, item in value.items()
        ]
        return "{" + ",".join(pairs) + "}"
    raise _JsonFailure("json_value_unsupported")


def _serialize_json(value: Any) -> str:
    try:
        return _serialize_json_value(value)
    except RecursionError as exc:
        raise _JsonFailure("json_nesting_limit") from exc


def _json_cell(value: Any) -> _Cell:
    if value is None:
        return _Cell("null", None)
    if isinstance(value, bool):
        return _Cell("boolean", "true" if value else "false")
    if isinstance(value, _JsonNumber):
        return _Cell(value.kind, value.raw)
    if isinstance(value, str):
        return _Cell("string", value)
    if isinstance(value, (dict, list)):
        return _Cell("json", _serialize_json(value))
    raise _JsonFailure("json_value_unsupported")


def _json_record_table(
    table_id: str,
    records: Any,
) -> tuple[_Table | None, list[SourceIssue]]:
    if len(table_id) > MAX_JSON_POINTER_CHARS:
        return None, [
            _issue("json_pointer_limit", "JSON table locator exceeds the limit.")
        ]
    if records and len(table_id) + 2 > MAX_JSON_POINTER_CHARS:
        return None, [
            _issue("json_pointer_limit", "JSON row locator exceeds the limit.")
        ]
    table_locator = _json_pointer_locator(table_id)
    if not isinstance(records, list):
        return None, [
            _issue(
                "json_unsupported_fragment",
                "JSON fragment is not a record array.",
                table_locator,
            )
        ]
    if len(records) > MAX_TABLE_ROWS:
        return None, [
            _issue(
                "json_row_limit",
                "JSON table exceeds the row limit.",
                table_locator,
            )
        ]

    valid_records: list[tuple[int, dict[str, Any]]] = []
    issues: list[SourceIssue] = []
    for index, record in enumerate(records):
        row_pointer = _append_json_pointer(table_id, str(index))
        row_locator = _json_pointer_locator(row_pointer)
        if not isinstance(record, dict):
            issues.append(
                _issue(
                    "json_record_not_object",
                    "JSON table records must be objects.",
                    row_locator,
                )
            )
            continue
        if any(not _valid_key(name) for name in record):
            return None, [
                _issue(
                    "json_column_invalid",
                    "JSON record contains an unsupported column name.",
                    row_locator,
                )
            ]
        valid_records.append((index, record))

    columns: list[str] = []
    for _, record in valid_records:
        for name in record:
            if name not in columns:
                columns.append(name)
    if len(columns) > MAX_TABLE_COLUMNS:
        return None, [
            _issue(
                "json_column_limit",
                "JSON table exceeds the column limit.",
                table_locator,
            )
        ]

    rows: list[dict[str, _Cell]] = []
    row_numbers: list[int] = []
    for source_index, record in valid_records:
        row: dict[str, _Cell] = {
            name: _Cell("missing", None) for name in columns
        }
        try:
            for name, value in record.items():
                row[name] = _json_cell(value)
        except _JsonFailure:
            issues.append(
                _issue(
                    "json_value_unsupported",
                    "JSON record contains an unsupported value.",
                )
            )
            continue
        rows.append(row)
        row_numbers.append(source_index + 1)
    if records and not rows:
        return None, issues or [
            _issue(
                "json_record_not_object",
                "JSON table contains no usable records.",
                table_locator,
            )
        ]
    return _Table(table_id, columns, rows, "json", row_numbers, issues), issues


def _parse_json(text: str) -> _ParsedDocument:
    try:
        root = _load_json(text)
    except _JsonFailure as exc:
        return _ParsedDocument(
            tables=[],
            text_line_count=None,
            issues=[_issue(exc.code, "JSON content is invalid or unsupported.")],
            status="failed",
        )

    if isinstance(root, list):
        table, issues = _json_record_table("", root)
        if table is None:
            return _ParsedDocument(
                tables=[],
                text_line_count=None,
                issues=issues,
                status="blocked",
            )
        return _ParsedDocument(
            tables=[table],
            text_line_count=None,
            issues=issues,
            status="partial" if issues else "ready",
        )

    if isinstance(root, dict):
        named_arrays = [
            (name, value)
            for name, value in root.items()
            if isinstance(value, list)
        ]
        if len(named_arrays) > MAX_NAMED_JSON_TABLES:
            return _ParsedDocument(
                tables=[],
                text_line_count=None,
                issues=[
                    _issue(
                        "json_table_limit",
                        "JSON file exceeds the named-table limit.",
                    )
                ],
                status="blocked",
            )
        tables: list[_Table] = []
        issues: list[SourceIssue] = []
        for name, value in root.items():
            pointer = _append_json_pointer("", name)
            if not isinstance(value, list):
                if len(pointer) > MAX_JSON_POINTER_CHARS:
                    issues.append(
                        _issue(
                            "json_pointer_limit",
                            "JSON fragment locator exceeds the limit.",
                        )
                    )
                    continue
                issues.append(
                    _issue(
                        "json_unsupported_fragment",
                        "JSON object member is not an approved record array.",
                        _json_pointer_locator(pointer),
                    )
                )
                continue
            table, table_issues = _json_record_table(pointer, value)
            issues.extend(table_issues)
            if table is not None:
                tables.append(table)
        if not tables:
            if not issues:
                issues.append(
                    _issue(
                        "json_unsupported_root",
                        "JSON object does not contain an approved table.",
                    )
                )
            return _ParsedDocument(
                tables=[],
                text_line_count=None,
                issues=issues,
                status="blocked",
            )
        return _ParsedDocument(
            tables=tables,
            text_line_count=None,
            issues=issues,
            status="partial" if issues else "ready",
        )

    return _ParsedDocument(
        tables=[],
        text_line_count=None,
        issues=[
            _issue(
                "json_unsupported_root",
                "JSON root is not an approved record array.",
            )
        ],
        status="blocked",
    )


def _parse_text(text: str) -> _ParsedDocument:
    return _ParsedDocument(
        tables=[],
        text_line_count=len(text.splitlines()),
        issues=[],
        status="ready",
    )


def _parse_document(revision: SourceRevision, text: str) -> _ParsedDocument:
    if revision.media_type == "text/csv":
        return _parse_csv(text)
    if revision.media_type == "application/json":
        return _parse_json(text)
    if revision.media_type in {"text/markdown", "text/plain"}:
        return _parse_text(text)
    return _ParsedDocument(
        tables=[],
        text_line_count=None,
        issues=[
            _issue(
                "unsupported_media_type",
                "Source media type is not supported.",
            )
        ],
        status="blocked",
    )


def parse_source(revision: SourceRevision, content: bytes) -> SourceArtifact:
    """Validate and deterministically profile one source revision."""

    oversized = _validate_source_input(revision, content, allow_oversized=True)
    if oversized:
        return _artifact(
            revision,
            status="blocked",
            issues=[_issue("source_file_too_large", "Source file exceeds the size limit.")],
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return _invalid_utf8_artifact(revision)

    document = _parse_document(revision, text)
    tables = [_profile_table(table, revision) for table in document.tables]
    return _artifact(
        revision,
        status=document.status,
        tables=tables,
        text_line_count=document.text_line_count,
        issues=document.issues,
    )


def _require_locator_for_media(
    revision: SourceRevision,
    locator: EvidenceLocator,
) -> None:
    if not isinstance(locator, (CsvRowsLocator, JsonPointerLocator, TextLinesLocator)):
        raise SourceInputError("invalid_locator")
    if isinstance(locator, CsvRowsLocator):
        if (
            locator.kind != "csv_rows"
            or type(locator.row_start) is not int
            or type(locator.row_end) is not int
            or locator.row_start < 1
            or locator.row_end < 1
            or locator.row_start > locator.row_end
            or (locator.column is not None and not _valid_key(locator.column))
        ):
            raise SourceInputError("invalid_locator")
    elif isinstance(locator, JsonPointerLocator):
        if (
            locator.kind != "json_pointer"
            or not isinstance(locator.pointer, str)
            or len(locator.pointer) > MAX_JSON_POINTER_CHARS
        ):
            raise SourceInputError("invalid_locator")
    elif (
        locator.kind != "text_lines"
        or type(locator.line_start) is not int
        or type(locator.line_end) is not int
        or locator.line_start < 1
        or locator.line_end < 1
        or locator.line_start > locator.line_end
    ):
        raise SourceInputError("invalid_locator")
    if revision.media_type == "text/csv" and isinstance(locator, CsvRowsLocator):
        return
    if revision.media_type == "application/json" and isinstance(locator, JsonPointerLocator):
        return
    if (
        revision.media_type in {"text/markdown", "text/plain"}
        and isinstance(locator, TextLinesLocator)
    ):
        return
    raise SourceInputError("locator_media_type_mismatch")


def _render_csv_rows(
    table: _Table,
    rows: list[dict[str, _Cell]],
    column: str | None,
) -> str:
    selected_columns = [column] if column is not None else table.columns
    rendered_rows: list[str] = []
    for row in rows:
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow([row[name].text or "" for name in selected_columns])
        rendered = stream.getvalue()
        if rendered.endswith("\n"):
            rendered = rendered[:-1]
        rendered_rows.append(rendered)
    return "\n".join(rendered_rows)


def _decode_json_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise SourceInputError("json_pointer_invalid")
    segments: list[str] = []
    for raw_segment in pointer[1:].split("/"):
        segment: list[str] = []
        index = 0
        while index < len(raw_segment):
            character = raw_segment[index]
            if character != "~":
                segment.append(character)
                index += 1
                continue
            if index + 1 >= len(raw_segment) or raw_segment[index + 1] not in "01":
                raise SourceInputError("json_pointer_invalid")
            segment.append("/" if raw_segment[index + 1] == "1" else "~")
            index += 2
        segments.append("".join(segment))
    return segments


def _resolve_json_pointer(root: Any, pointer: str) -> Any:
    current = root
    for segment in _decode_json_pointer(pointer):
        if isinstance(current, dict):
            if segment not in current:
                raise SourceInputError("json_pointer_out_of_bounds")
            current = current[segment]
            continue
        if isinstance(current, list):
            if not _ARRAY_INDEX.fullmatch(segment):
                raise SourceInputError("json_pointer_invalid")
            position = int(segment)
            if position >= len(current):
                raise SourceInputError("json_pointer_out_of_bounds")
            current = current[position]
            continue
        raise SourceInputError("json_pointer_out_of_bounds")
    return current


def read_source_fragment(
    revision: SourceRevision,
    content: bytes,
    locator: EvidenceLocator,
) -> SourceExcerpt:
    """Read one bounded, explicitly located excerpt from source bytes."""

    _validate_source_input(revision, content, allow_oversized=False)
    _require_locator_for_media(revision, locator)
    text = _decode_utf8(content)

    if isinstance(locator, CsvRowsLocator):
        document = _parse_csv(text)
        if document.status != "ready" or len(document.tables) != 1:
            raise SourceInputError("source_parse_failed")
        table = document.tables[0]
        if locator.column is not None and locator.column not in table.columns:
            raise SourceInputError("locator_column_not_found")
        if locator.row_end > len(table.rows):
            raise SourceInputError("locator_out_of_bounds")
        excerpt = _render_csv_rows(
            table,
            table.rows[locator.row_start - 1 : locator.row_end],
            locator.column,
        )
    elif isinstance(locator, JsonPointerLocator):
        try:
            root = _load_json(text)
            value = _resolve_json_pointer(root, locator.pointer)
            excerpt = _serialize_json(value)
        except _JsonFailure as exc:
            raise SourceInputError(exc.code) from exc
    else:
        lines = text.splitlines()
        if locator.line_end > len(lines):
            raise SourceInputError("locator_out_of_bounds")
        excerpt = "\n".join(lines[locator.line_start - 1 : locator.line_end])

    excerpt, truncated = _bounded_text(excerpt, MAX_EXCERPT_CHARS)
    return SourceExcerpt(
        source_ref=_evidence_ref(revision, locator),
        text=excerpt,
        truncated=truncated,
    )


def _validate_table_key(
    table_key: TableKey,
    revision: SourceRevision,
    content: bytes,
) -> None:
    if not isinstance(table_key, TableKey):
        raise SourceInputError("invalid_table_key")
    _validate_source_input(revision, content, allow_oversized=False)
    if not _same_identity(table_key.source_ref, revision):
        raise SourceInputError("source_identity_mismatch")
    if (
        any(not _valid_key(column) for column in table_key.columns)
        or len(set(table_key.columns)) != len(table_key.columns)
    ):
        raise SourceInputError("invalid_table_key")


def _join_key(
    row: dict[str, _Cell],
    columns: list[str],
) -> tuple[tuple[str, str | None], ...] | None:
    values: list[tuple[str, str | None]] = []
    for column in columns:
        value = row[column]
        if value.kind in {"missing", "null"}:
            return None
        values.append((value.kind, value.text))
    return tuple(values)


def _relationship_cardinality(
    left_counts: Counter[tuple[tuple[str, str | None], ...]],
    right_counts: Counter[tuple[tuple[str, str | None], ...]],
) -> str:
    shared = set(left_counts).intersection(right_counts)
    if not shared:
        return "unknown"
    left_many = any(left_counts[key] > 1 for key in shared)
    right_many = any(right_counts[key] > 1 for key in shared)
    if left_many and right_many:
        return "many_to_many"
    if left_many:
        return "many_to_one"
    if right_many:
        return "one_to_many"
    return "one_to_one"


def inspect_relationship(
    left: TableKey,
    left_revision: SourceRevision,
    left_content: bytes,
    right: TableKey,
    right_revision: SourceRevision,
    right_content: bytes,
) -> RelationshipProfile:
    """Inspect exact-key relationship counts without normalizing or joining rows."""

    if not isinstance(left, TableKey) or not isinstance(right, TableKey):
        raise SourceInputError("invalid_table_key")
    if left.source_ref.workspace_id != right.source_ref.workspace_id:
        raise SourceInputError("workspace_mismatch")
    _validate_table_key(left, left_revision, left_content)
    _validate_table_key(right, right_revision, right_content)
    if len(left.columns) == 0 or len(right.columns) == 0:
        raise SourceInputError("join_columns_empty")
    if len(set(left.columns)) != len(left.columns) or len(set(right.columns)) != len(right.columns):
        raise SourceInputError("join_columns_duplicated")

    left_text = _decode_utf8(left_content)
    right_text = _decode_utf8(right_content)
    left_document = _parse_document(left_revision, left_text)
    right_document = _parse_document(right_revision, right_text)
    if not left_document.tables or not right_document.tables:
        raise SourceInputError("table_not_found")
    left_table = next(
        (table for table in left_document.tables if table.table_id == left.table_id),
        None,
    )
    right_table = next(
        (table for table in right_document.tables if table.table_id == right.table_id),
        None,
    )
    if left_table is None or right_table is None:
        raise SourceInputError("table_not_found")
    if any(column not in left_table.columns for column in left.columns):
        raise SourceInputError("join_column_not_found")
    if any(column not in right_table.columns for column in right.columns):
        raise SourceInputError("join_column_not_found")
    if left_document.status in {"blocked", "failed"} or right_document.status in {
        "blocked",
        "failed",
    }:
        raise SourceInputError("source_parse_failed")

    left_counts: Counter[tuple[tuple[str, str | None], ...]] = Counter()
    right_counts: Counter[tuple[tuple[str, str | None], ...]] = Counter()
    left_null_keys = 0
    right_null_keys = 0
    for row in left_table.rows:
        key = _join_key(row, left.columns)
        if key is None:
            left_null_keys += 1
        else:
            left_counts[key] += 1
    for row in right_table.rows:
        key = _join_key(row, right.columns)
        if key is None:
            right_null_keys += 1
        else:
            right_counts[key] += 1

    shared = set(left_counts).intersection(right_counts)
    unmatched_left_rows = sum(
        count for key, count in left_counts.items() if key not in right_counts
    )
    unmatched_right_rows = sum(
        count for key, count in right_counts.items() if key not in left_counts
    )
    prospective_join_rows = sum(
        left_counts[key] * right_counts[key] for key in shared
    )
    limitations = [
        "Join keys use exact typed values with no normalization.",
        "Rows with missing or null key components are excluded from matching.",
        "Relationship counts are observations and do not approve a business relationship.",
    ]
    if left_document.status == "partial" or right_document.status == "partial":
        limitations.append("One or more source parse issues limit the observed rows.")
    if not shared:
        limitations.append("No matching non-null keys were observed; cardinality is unknown.")

    source_refs: list[EvidenceRef] = []
    left_locator = _table_locator(left_table, len(left_table.rows))
    right_locator = _table_locator(right_table, len(right_table.rows))
    if left_locator is not None:
        source_refs.append(_evidence_ref(left_revision, left_locator))
    if right_locator is not None:
        source_refs.append(_evidence_ref(right_revision, right_locator))

    return RelationshipProfile(
        left=left,
        right=right,
        left_rows=len(left_table.rows),
        right_rows=len(right_table.rows),
        left_distinct_keys=len(left_counts),
        right_distinct_keys=len(right_counts),
        left_null_keys=left_null_keys,
        right_null_keys=right_null_keys,
        matched_distinct_keys=len(shared),
        unmatched_left_rows=unmatched_left_rows,
        unmatched_right_rows=unmatched_right_rows,
        prospective_join_rows=prospective_join_rows,
        observed_cardinality=_relationship_cardinality(left_counts, right_counts),
        source_refs=source_refs,
        limitations=limitations,
    )
