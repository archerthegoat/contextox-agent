from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from contextox.models import (
    CsvRowsLocator,
    JsonPointerLocator,
    SourceIdentity,
    SourceRevision,
    TableKey,
    TextLinesLocator,
)
from contextox.sources import (
    MAX_EXCERPT_CHARS,
    MAX_FILE_BYTES,
    MAX_JSON_POINTER_CHARS,
    MAX_SAMPLE_CELL_CHARS,
    MAX_SAMPLE_ROWS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    SourceInputError,
    inspect_relationship,
    parse_source,
    read_source_fragment,
)


WORKSPACE_A = "00000000-0000-4000-8000-000000000001"
WORKSPACE_B = "00000000-0000-4000-8000-000000000002"
OBSERVED_AT = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _revision(
    content: bytes,
    media_type: str,
    *,
    workspace_id: str = WORKSPACE_A,
    number: int = 1,
    permission_status: str = "read_allowed",
    byte_size: int | None = None,
    sha256: str | None = None,
) -> SourceRevision:
    return SourceRevision(
        workspace_id=workspace_id,
        source_id=_id(number),
        revision_id=_id(number + 100),
        original_name=f"synthetic-{number}",
        media_type=media_type,
        byte_size=len(content) if byte_size is None else byte_size,
        sha256=hashlib.sha256(content).hexdigest() if sha256 is None else sha256,
        observed_at=OBSERVED_AT,
        effective_time=None,
        permission_status=permission_status,
        parse_status="pending",
        parser_version="fixture-input",
    )


def _source_ref(revision: SourceRevision) -> SourceIdentity:
    return SourceIdentity(
        workspace_id=revision.workspace_id,
        source_id=revision.source_id,
        revision_id=revision.revision_id,
        sha256=revision.sha256,
    )


def _table_key(
    revision: SourceRevision,
    columns: list[str],
    table_id: str = "",
) -> TableKey:
    return TableKey(
        source_ref=_source_ref(revision),
        table_id=table_id,
        columns=columns,
    )


def _assert_source_error(
    test: unittest.TestCase,
    code: str,
    function,
    *args,
) -> None:
    with test.assertRaises(SourceInputError) as raised:
        function(*args)
    test.assertEqual(raised.exception.code, code)


class SourceParsingTests(unittest.TestCase):
    def test_csv_profile_preserves_quoted_rows_numeric_lexemes_and_duplicate_rows(self) -> None:
        content = (
            b"id,name,amount,flag\n"
            b"00123,Alice,10.50,true\n"
            b"2,\"Bob, Jr.\",20,false\n"
            b"2,\"Bob, Jr.\",20,false\n"
        )
        revision = _revision(content, "text/csv")

        artifact = parse_source(revision, content)

        self.assertEqual(artifact.parse_status, "ready")
        self.assertEqual(artifact.text_line_count, None)
        self.assertEqual(len(artifact.tables), 1)
        table = artifact.tables[0]
        self.assertEqual(table.table_id, "")
        self.assertEqual(table.row_count, 3)
        self.assertEqual(table.duplicate_row_count, 1)
        self.assertEqual([column.name for column in table.columns], ["id", "name", "amount", "flag"])
        self.assertEqual(table.columns[0].observed_types, ["string", "integer"])
        self.assertEqual(table.columns[0].distinct_count, 2)
        self.assertEqual(table.columns[2].numeric_min, "10.50")
        self.assertEqual(table.columns[2].numeric_max, "20")
        self.assertEqual(len(table.sample_rows), 3)
        self.assertEqual(table.sample_rows[0].cells[0].value_kind, "string")
        self.assertEqual(table.sample_rows[0].cells[0].text, "00123")
        self.assertEqual(table.sample_rows[1].cells[1].text, "Bob, Jr.")
        self.assertEqual(table.sample_rows[0].source_refs[0].locator.kind, "csv_rows")

    def test_csv_multiline_record_uses_record_numbers_for_fragment_locator(self) -> None:
        content = b"id,note\n1,first\n2,\"line one\nline two\"\n"
        revision = _revision(content, "text/csv")

        artifact = parse_source(revision, content)
        excerpt = read_source_fragment(
            revision,
            content,
            CsvRowsLocator(kind="csv_rows", row_start=2, row_end=2, column="note"),
        )

        self.assertEqual(artifact.tables[0].row_count, 2)
        self.assertEqual(excerpt.text, '"line one\nline two"')
        self.assertFalse(excerpt.truncated)
        self.assertEqual(excerpt.source_ref.locator.row_start, 2)
        self.assertEqual(excerpt.source_ref.locator.row_end, 2)

    def test_csv_empty_source_and_header_only_source_are_deterministic(self) -> None:
        empty = b""
        empty_artifact = parse_source(_revision(empty, "text/csv"), empty)
        self.assertEqual(empty_artifact.parse_status, "ready")
        self.assertEqual(empty_artifact.tables[0].row_count, 0)
        self.assertEqual(empty_artifact.tables[0].columns, [])

        header = b"order_id,total\n"
        header_artifact = parse_source(_revision(header, "text/csv", number=2), header)
        table = header_artifact.tables[0]
        self.assertEqual(table.row_count, 0)
        self.assertEqual([column.name for column in table.columns], ["order_id", "total"])
        self.assertEqual(table.sample_rows, [])

    def test_csv_header_width_and_row_limit_fail_without_silent_truncation(self) -> None:
        invalid_header = b"id,id\n1,2\n"
        artifact = parse_source(
            _revision(invalid_header, "text/csv"), invalid_header
        )
        self.assertEqual(artifact.parse_status, "failed")
        self.assertEqual(artifact.tables, [])
        self.assertEqual(artifact.issues[0].code, "csv_header_invalid")

        width_mismatch = b"id,name\n1,Alice\n2\n"
        partial = parse_source(
            _revision(width_mismatch, "text/csv", number=2),
            width_mismatch,
        )
        self.assertEqual(partial.parse_status, "partial")
        self.assertEqual(partial.tables[0].row_count, 1)
        self.assertEqual(partial.issues[0].code, "csv_row_width_mismatch")
        self.assertEqual(partial.issues[0].locator.row_start, 2)

        over_limit = ("id\n" + "".join(f"{index}\n" for index in range(MAX_TABLE_ROWS + 1))).encode()
        blocked = parse_source(
            _revision(over_limit, "text/csv", number=3),
            over_limit,
        )
        self.assertEqual(blocked.parse_status, "blocked")
        self.assertEqual(blocked.tables, [])
        self.assertEqual(blocked.issues[0].code, "csv_row_limit")

        malformed = b"id,note\n1,\"unterminated\n"
        malformed_artifact = parse_source(
            _revision(malformed, "text/csv", number=4),
            malformed,
        )
        self.assertEqual(malformed_artifact.parse_status, "failed")
        self.assertEqual(malformed_artifact.issues[0].code, "csv_malformed")

        malformed_after_row = b"id,note\n1,valid\n2,\"unterminated\n"
        malformed_after_row_artifact = parse_source(
            _revision(malformed_after_row, "text/csv", number=41),
            malformed_after_row,
        )
        self.assertEqual(malformed_after_row_artifact.parse_status, "partial")
        self.assertEqual(malformed_after_row_artifact.tables[0].row_count, 1)
        self.assertEqual(malformed_after_row_artifact.issues[0].code, "csv_malformed")

        too_many_columns = (
            ",".join(f"column_{index}" for index in range(MAX_TABLE_COLUMNS + 1))
            + "\n"
        ).encode()
        too_many_columns_artifact = parse_source(
            _revision(too_many_columns, "text/csv", number=5),
            too_many_columns,
        )
        self.assertEqual(too_many_columns_artifact.parse_status, "blocked")
        self.assertEqual(too_many_columns_artifact.issues[0].code, "csv_column_limit")

    def test_json_profile_keeps_missing_null_empty_string_exact_numbers_and_partial_metadata(self) -> None:
        content = (
            b'{"orders":['
            b'{"id":"001","amount":12345678901234567890,"flag":true,"note":""},'
            b'{"id":"002","amount":1.2300,"flag":null},'
            b'{"id":"003","amount":-2,"note":{"nested":1.2300}}'
            b'],"metadata":{"source":"synthetic"}}'
        )
        revision = _revision(content, "application/json")

        artifact = parse_source(revision, content)

        self.assertEqual(artifact.parse_status, "partial")
        self.assertEqual(len(artifact.tables), 1)
        table = artifact.tables[0]
        self.assertEqual(table.table_id, "/orders")
        self.assertEqual(table.row_count, 3)
        self.assertEqual(table.columns[0].name, "id")
        amount = next(column for column in table.columns if column.name == "amount")
        self.assertEqual(amount.observed_types, ["integer", "decimal"])
        self.assertEqual(amount.numeric_min, "-2")
        self.assertEqual(amount.numeric_max, "12345678901234567890")
        flag = next(column for column in table.columns if column.name == "flag")
        self.assertEqual(flag.missing_count, 1)
        self.assertEqual(flag.null_count, 1)
        self.assertEqual(flag.distinct_count, 1)
        note = next(column for column in table.columns if column.name == "note")
        self.assertEqual(note.observed_types, ["string", "json"])
        self.assertEqual(table.sample_rows[0].cells[0].text, "001")
        self.assertEqual(table.sample_rows[0].cells[2].value_kind, "boolean")
        self.assertEqual(table.sample_rows[1].cells[2].value_kind, "null")
        self.assertIsNone(table.sample_rows[1].cells[2].text)
        self.assertEqual(table.sample_rows[2].cells[2].value_kind, "missing")
        self.assertIsNone(table.sample_rows[2].cells[2].text)
        self.assertEqual(artifact.issues[0].code, "json_unsupported_fragment")
        self.assertEqual(artifact.tables[0].source_refs[0].locator.pointer, "/orders")

    def test_json_pointer_supports_rfc6901_escaping_and_rejects_bad_or_missing_targets(self) -> None:
        content = b'{"a/b":[{"value":12345678901234567890}],"tilde~":[true]}'
        revision = _revision(content, "application/json")

        escaped = read_source_fragment(
            revision,
            content,
            JsonPointerLocator(kind="json_pointer", pointer="/a~1b/0/value"),
        )
        root = read_source_fragment(
            revision,
            content,
            JsonPointerLocator(kind="json_pointer", pointer=""),
        )

        self.assertEqual(escaped.text, "12345678901234567890")
        self.assertFalse(escaped.truncated)
        self.assertTrue(root.text.startswith('{"a/b":[{"value":12345678901234567890}]'))
        with self.assertRaises(ValidationError):
            JsonPointerLocator(kind="json_pointer", pointer="/a~2b")
        _assert_source_error(
            self,
            "json_pointer_invalid",
            read_source_fragment,
            revision,
            content,
            JsonPointerLocator.model_construct(kind="json_pointer", pointer="/a~2b"),
        )
        _assert_source_error(
            self,
            "json_pointer_invalid",
            read_source_fragment,
            revision,
            content,
            JsonPointerLocator(kind="json_pointer", pointer="/a~1b/01"),
        )
        _assert_source_error(
            self,
            "json_pointer_out_of_bounds",
            read_source_fragment,
            revision,
            content,
            JsonPointerLocator(kind="json_pointer", pointer="/a~1b/4"),
        )

    def test_json_rejects_duplicate_keys_nonfinite_numbers_and_unsupported_fragments(self) -> None:
        duplicate = b'{"orders":[{"id":1,"id":2}]}'
        duplicate_artifact = parse_source(
            _revision(duplicate, "application/json"), duplicate
        )
        self.assertEqual(duplicate_artifact.parse_status, "failed")
        self.assertEqual(duplicate_artifact.issues[0].code, "json_duplicate_key")

        nonfinite = b'{"orders":[{"value":NaN}]}'
        nonfinite_artifact = parse_source(
            _revision(nonfinite, "application/json", number=2), nonfinite
        )
        self.assertEqual(nonfinite_artifact.parse_status, "failed")
        self.assertEqual(nonfinite_artifact.issues[0].code, "json_non_finite_number")

        unsupported = b'{"metadata":{"note":"not a table"}}'
        unsupported_artifact = parse_source(
            _revision(unsupported, "application/json", number=3), unsupported
        )
        self.assertEqual(unsupported_artifact.parse_status, "blocked")
        self.assertEqual(unsupported_artifact.tables, [])
        self.assertEqual(unsupported_artifact.issues[0].code, "json_unsupported_fragment")

        named_tables = {
            f"table_{index}": []
            for index in range(17)
        }
        too_many = json.dumps(named_tables, separators=(",", ":")).encode()
        too_many_artifact = parse_source(
            _revision(too_many, "application/json", number=4), too_many
        )
        self.assertEqual(too_many_artifact.parse_status, "blocked")
        self.assertEqual(too_many_artifact.issues[0].code, "json_table_limit")

        invalid_syntax = b'{"orders":[}'
        invalid_syntax_artifact = parse_source(
            _revision(invalid_syntax, "application/json", number=5),
            invalid_syntax,
        )
        self.assertEqual(invalid_syntax_artifact.parse_status, "failed")
        self.assertEqual(invalid_syntax_artifact.issues[0].code, "json_invalid")

        scalar_records = b'{"orders":[1,2]}'
        scalar_records_artifact = parse_source(
            _revision(scalar_records, "application/json", number=6),
            scalar_records,
        )
        self.assertEqual(scalar_records_artifact.parse_status, "blocked")
        self.assertEqual(scalar_records_artifact.tables, [])
        self.assertEqual(scalar_records_artifact.issues[0].code, "json_record_not_object")

    def test_json_partial_record_errors_keep_only_explicitly_valid_rows(self) -> None:
        content = b'{"orders":[{"id":1},42,{"id":2}]}'
        revision = _revision(content, "application/json")

        artifact = parse_source(revision, content)

        self.assertEqual(artifact.parse_status, "partial")
        self.assertEqual(artifact.tables[0].row_count, 2)
        self.assertEqual(artifact.tables[0].sample_rows[1].row_number, 3)
        self.assertEqual(
            artifact.tables[0].sample_rows[1].source_refs[0].locator.pointer,
            "/orders/2",
        )
        self.assertEqual(artifact.issues[0].code, "json_record_not_object")
        self.assertEqual(artifact.issues[0].locator.pointer, "/orders/1")

    def test_json_long_table_pointer_rejects_unrepresentable_row_locators(self) -> None:
        table_name = "t" * (MAX_JSON_POINTER_CHARS - 3)
        content = (
            (
                '{"'
                + table_name
                + '":['
                + ",".join(["0"] * 10 + ['{"id":1}'])
                + "]}"
            ).encode()
        )
        revision = _revision(content, "application/json")

        artifact = parse_source(revision, content)

        self.assertEqual(artifact.parse_status, "blocked")
        self.assertEqual(artifact.tables, [])
        self.assertEqual(artifact.issues[0].code, "json_pointer_limit")

    def test_text_sources_report_lines_and_bound_fragment_display(self) -> None:
        content = "alpha\nbeta\ngamma".encode()
        revision = _revision(content, "text/markdown")
        artifact = parse_source(revision, content)
        excerpt = read_source_fragment(
            revision,
            content,
            TextLinesLocator(kind="text_lines", line_start=2, line_end=3),
        )

        self.assertEqual(artifact.parse_status, "ready")
        self.assertEqual(artifact.text_line_count, 3)
        self.assertEqual(artifact.tables, [])
        self.assertEqual(excerpt.text, "beta\ngamma")
        self.assertFalse(excerpt.truncated)

        long_content = ("x" * (MAX_EXCERPT_CHARS + 20)).encode()
        long_revision = _revision(long_content, "text/plain", number=2)
        long_excerpt = read_source_fragment(
            long_revision,
            long_content,
            TextLinesLocator(kind="text_lines", line_start=1, line_end=1),
        )
        self.assertEqual(len(long_excerpt.text), MAX_EXCERPT_CHARS)
        self.assertTrue(long_excerpt.truncated)

    def test_invalid_utf8_is_failed_and_fragment_cannot_decode_it(self) -> None:
        content = b"\xff\xfe"
        revision = _revision(content, "text/plain")
        artifact = parse_source(revision, content)
        self.assertEqual(artifact.parse_status, "failed")
        self.assertEqual(artifact.issues[0].code, "source_utf8_invalid")
        _assert_source_error(
            self,
            "source_utf8_invalid",
            read_source_fragment,
            revision,
            content,
            TextLinesLocator(kind="text_lines", line_start=1, line_end=1),
        )

    def test_sample_rows_and_cells_are_bounded_without_changing_row_count(self) -> None:
        long_cell = "z" * (MAX_SAMPLE_CELL_CHARS + 10)
        content = (
            "name\n"
            + "".join(f"{long_cell}{index}\n" for index in range(MAX_SAMPLE_ROWS + 1))
        ).encode()
        revision = _revision(content, "text/csv")

        artifact = parse_source(revision, content)

        table = artifact.tables[0]
        self.assertEqual(table.row_count, MAX_SAMPLE_ROWS + 1)
        self.assertEqual(len(table.sample_rows), MAX_SAMPLE_ROWS)
        self.assertEqual(len(table.sample_rows[0].cells[0].text), MAX_SAMPLE_CELL_CHARS)
        self.assertTrue(table.sample_rows[0].cells[0].truncated)


class SourceBoundaryTests(unittest.TestCase):
    def test_metadata_hash_size_content_type_and_permission_are_fail_closed(self) -> None:
        content = b"id\n1\n"
        revision = _revision(content, "text/csv")

        _assert_source_error(
            self,
            "source_byte_size_mismatch",
            parse_source,
            _revision(content, "text/csv", byte_size=len(content) + 1),
            content,
        )
        _assert_source_error(
            self,
            "source_hash_mismatch",
            parse_source,
            _revision(content, "text/csv", sha256="0" * 64, number=2),
            content,
        )
        _assert_source_error(
            self,
            "source_content_type_invalid",
            parse_source,
            revision,
            bytearray(content),
        )
        _assert_source_error(
            self,
            "source_permission_not_allowed",
            parse_source,
            _revision(
                content,
                "text/csv",
                permission_status="unknown",
                number=3,
            ),
            content,
        )

    def test_oversized_source_returns_blocked_artifact_without_truncating(self) -> None:
        content = b"x" * (MAX_FILE_BYTES + 1)
        revision = _revision(content, "text/plain")

        artifact = parse_source(revision, content)

        self.assertEqual(artifact.parse_status, "blocked")
        self.assertEqual(artifact.tables, [])
        self.assertEqual(artifact.issues[0].code, "source_file_too_large")
        _assert_source_error(
            self,
            "source_file_too_large",
            read_source_fragment,
            revision,
            content,
            TextLinesLocator(kind="text_lines", line_start=1, line_end=1),
        )

    def test_locator_media_type_and_ranges_are_rejected(self) -> None:
        content = b"id\n1\n"
        revision = _revision(content, "text/csv")
        _assert_source_error(
            self,
            "locator_media_type_mismatch",
            read_source_fragment,
            revision,
            content,
            TextLinesLocator(kind="text_lines", line_start=1, line_end=1),
        )
        _assert_source_error(
            self,
            "locator_out_of_bounds",
            read_source_fragment,
            revision,
            content,
            CsvRowsLocator(kind="csv_rows", row_start=2, row_end=2, column=None),
        )
        _assert_source_error(
            self,
            "locator_column_not_found",
            read_source_fragment,
            revision,
            content,
            CsvRowsLocator(kind="csv_rows", row_start=1, row_end=1, column="missing"),
        )
        text_content = b"one\ntwo"
        text_revision = _revision(text_content, "text/plain", number=2)
        _assert_source_error(
            self,
            "locator_out_of_bounds",
            read_source_fragment,
            text_revision,
            text_content,
            TextLinesLocator(kind="text_lines", line_start=2, line_end=3),
        )
        _assert_source_error(
            self,
            "invalid_locator",
            read_source_fragment,
            revision,
            content,
            CsvRowsLocator.model_construct(
                kind="csv_rows",
                row_start=2,
                row_end=1,
                column=None,
            ),
        )
        _assert_source_error(
            self,
            "invalid_locator",
            read_source_fragment,
            _revision(b"{}", "application/json", number=3),
            b"{}",
            JsonPointerLocator.model_construct(
                kind="json_pointer",
                pointer="/" + ("x" * 4096),
            ),
        )


class RelationshipInspectionTests(unittest.TestCase):
    def test_composite_keys_nulls_and_duplicate_join_expansion_are_counted(self) -> None:
        left_content = (
            b'[{"account":1,"region":"us"},{"account":1,"region":"us"},'
            b'{"account":1,"region":"eu"},{"account":2,"region":null}]'
        )
        right_content = (
            b'[{"account":1,"region":"us"},{"account":1,"region":"us"},'
            b'{"account":1,"region":"apac"},{"account":3,"region":"us"}]'
        )
        left_revision = _revision(left_content, "application/json")
        right_revision = _revision(right_content, "application/json", number=2)

        profile = inspect_relationship(
            _table_key(left_revision, ["account", "region"]),
            left_revision,
            left_content,
            _table_key(right_revision, ["account", "region"]),
            right_revision,
            right_content,
        )

        self.assertEqual(profile.left_rows, 4)
        self.assertEqual(profile.right_rows, 4)
        self.assertEqual(profile.left_distinct_keys, 2)
        self.assertEqual(profile.right_distinct_keys, 3)
        self.assertEqual(profile.left_null_keys, 1)
        self.assertEqual(profile.right_null_keys, 0)
        self.assertEqual(profile.matched_distinct_keys, 1)
        self.assertEqual(profile.unmatched_left_rows, 1)
        self.assertEqual(profile.unmatched_right_rows, 2)
        self.assertEqual(profile.prospective_join_rows, 4)
        self.assertEqual(profile.observed_cardinality, "many_to_many")
        self.assertEqual(len(profile.source_refs), 2)
        self.assertTrue(any("exact typed values" in item for item in profile.limitations))

    def test_relationship_cardinality_and_zero_match_unknown_are_deterministic(self) -> None:
        left_content = b'[{"id":1},{"id":2}]'
        right_content = b'[{"key":1},{"key":1},{"key":3}]'
        left_revision = _revision(left_content, "application/json")
        right_revision = _revision(right_content, "application/json", number=2)
        left = _table_key(left_revision, ["id"])
        right = _table_key(right_revision, ["key"])

        one_to_many = inspect_relationship(
            left,
            left_revision,
            left_content,
            right,
            right_revision,
            right_content,
        )
        self.assertEqual(one_to_many.observed_cardinality, "one_to_many")
        self.assertEqual(one_to_many.prospective_join_rows, 2)
        self.assertEqual(one_to_many.unmatched_right_rows, 1)

        no_match_content = b'[{"key":9}]'
        no_match_revision = _revision(
            no_match_content,
            "application/json",
            number=3,
        )
        no_match = inspect_relationship(
            left,
            left_revision,
            left_content,
            _table_key(no_match_revision, ["key"]),
            no_match_revision,
            no_match_content,
        )
        self.assertEqual(no_match.matched_distinct_keys, 0)
        self.assertEqual(no_match.observed_cardinality, "unknown")
        self.assertEqual(no_match.prospective_join_rows, 0)

    def test_relationship_does_not_normalize_key_types_or_ignore_identity_boundaries(self) -> None:
        left_content = b'[{"id":"1"}]'
        right_content = b'[{"id":1}]'
        left_revision = _revision(left_content, "application/json")
        right_revision = _revision(right_content, "application/json", number=2)
        profile = inspect_relationship(
            _table_key(left_revision, ["id"]),
            left_revision,
            left_content,
            _table_key(right_revision, ["id"]),
            right_revision,
            right_content,
        )
        self.assertEqual(profile.matched_distinct_keys, 0)
        self.assertEqual(profile.observed_cardinality, "unknown")

        _assert_source_error(
            self,
            "workspace_mismatch",
            inspect_relationship,
            _table_key(left_revision, ["id"]),
            left_revision,
            left_content,
            _table_key(_revision(right_content, "application/json", workspace_id=WORKSPACE_B, number=3), ["id"]),
            _revision(right_content, "application/json", workspace_id=WORKSPACE_B, number=3),
            right_content,
        )
        mismatched_key = _table_key(
            _revision(right_content, "application/json", number=4),
            ["id"],
        )
        _assert_source_error(
            self,
            "source_identity_mismatch",
            inspect_relationship,
            _table_key(left_revision, ["id"]),
            left_revision,
            left_content,
            mismatched_key,
            right_revision,
            right_content,
        )
        _assert_source_error(
            self,
            "join_columns_empty",
            inspect_relationship,
            _table_key(left_revision, []),
            left_revision,
            left_content,
            _table_key(right_revision, ["id"]),
            right_revision,
            right_content,
        )
        _assert_source_error(
            self,
            "join_column_not_found",
            inspect_relationship,
            _table_key(left_revision, ["missing"]),
            left_revision,
            left_content,
            _table_key(right_revision, ["id"]),
            right_revision,
            right_content,
        )


if __name__ == "__main__":
    unittest.main()
