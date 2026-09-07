import unittest

from pydantic import ValidationError

import test_agent as fixtures
from contextox.model_tools import CandidateRejected, FieldInput, HandleDenied, RunReferences
from contextox.models import ColumnRef, DefinitionDraft, DefinitionField, CsvRowsLocator, canonical_sha256


def unknown_field(key="candidate"):
    return {"field_key": key, "name": key,
            "semantics": {dim: {"value": None, "unknown_reason": "Requires the business owner."}
                          for dim in ("meaning", "value_type", "grain", "rule", "time_basis", "null_handling")},
            "source_column_handles": [], "evidence_status": "unknown", "evidence_handles": []}


class ModelInputTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixtures._context_snapshot()
        self.refs = RunReferences(self.snapshot, [])

    def test_semantic_conversion_preserves_values_and_exact_unknowns(self):
        raw = unknown_field()
        raw["semantics"]["meaning"] = {"value": "A candidate amount", "unknown_reason": None}
        raw["semantics"]["value_type"] = {"value": "decimal", "unknown_reason": None}
        field = DefinitionField.model_validate(self.refs.field(FieldInput.model_validate(raw)))
        self.assertEqual(field.meaning, "A candidate amount")
        self.assertEqual(field.value_type, "decimal")
        self.assertEqual({x.property_path for x in field.unknowns},
                         {"grain", "rule", "time_basis", "null_handling"})
        self.assertEqual(self.refs.public(field), raw)

    def test_invalid_shapes_and_key_values_cannot_be_coerced(self):
        for bad in ({"value": None, "unknown_reason": None},
                    {"value": "yes", "unknown_reason": "also unknown"},
                    {"value": None, "unknown_reason": " "},
                    {"value": 3, "unknown_reason": None}):
            with self.subTest(bad=bad):
                raw = unknown_field()
                raw["semantics"]["rule"] = bad
                with self.assertRaises(ValidationError):
                    FieldInput.model_validate(raw)
        raw = unknown_field()
        raw["semantics"]["value_type"] = {"value": "a" * 129, "unknown_reason": None}
        with self.assertRaises(ValidationError):
            FieldInput.model_validate(raw)
        raw = unknown_field()
        del raw["semantics"]["meaning"]
        with self.assertRaises(ValidationError):
            FieldInput.model_validate(raw)
        raw = unknown_field()
        raw["approved"] = True
        with self.assertRaises(ValidationError):
            FieldInput.model_validate(raw)

    def test_handles_are_typed_run_local_and_draft_tokens_pin_versions(self):
        token = self.refs.draft_token
        self.assertEqual(self.refs.resolve(token, "draft"), {"version": 0, "sha256": None})
        other = RunReferences(self.snapshot, [])
        for refs, handle, kind in ((other, token, "draft"), (self.refs, token, "source"),
                                   (self.refs, "fabricated", "evidence")):
            with self.assertRaises(HandleDenied):
                refs.resolve(handle, kind)
        field = DefinitionField.model_validate(self.refs.field(FieldInput.model_validate(unknown_field())))
        payload = {"fields": [field.model_dump(mode="json")], "relationships": [], "unresolved_items": []}
        draft = DefinitionDraft(workspace_id=self.snapshot.mission.workspace_id,
                                mission_id=self.snapshot.mission.mission_id, draft_id=fixtures._id(9),
                                version=1, sha256=canonical_sha256(payload), status="draft",
                                semantic_approval="pending", **payload)
        self.refs.public(draft)
        self.assertNotEqual(self.refs.draft_token, token)
        self.assertEqual(self.refs.resolve(token, "draft")["version"], 0)
        self.assertEqual(self.refs.resolve(self.refs.draft_token, "draft")["version"], 1)
        with self.assertRaises(CandidateRejected):
            self.refs.draft_version(token)
        self.assertEqual(self.refs.draft_version(self.refs.draft_token)["version"], 1)

    def test_merged_candidate_limit_is_checked_before_execution(self):
        fields = [self.refs.field(FieldInput.model_validate(unknown_field(f"f-{i}"))) for i in range(100)]
        payload = {"fields": fields, "relationships": [], "unresolved_items": []}
        self.refs.current_draft = DefinitionDraft(
            workspace_id=self.snapshot.mission.workspace_id, mission_id=self.snapshot.mission.mission_id,
            draft_id=fixtures._id(9), version=1, sha256=canonical_sha256(payload),
            status="draft", semantic_approval="pending", **payload)
        self.refs.validate_update([fields[0]], [])
        with self.assertRaises(CandidateRejected):
            self.refs.validate_update([self.refs.field(FieldInput.model_validate(unknown_field("new")))], [])
        with self.assertRaises(CandidateRejected):
            self.refs.validate_update([fields[0], fields[0]], [])


class SourceRegistryTests(unittest.TestCase):
    def test_draft_cannot_publish_unselected_or_nonexistent_columns(self):
        setup = fixtures.PersistedRunTests()
        with setup.store_case(with_sources=True) as (store, ws, mission, selected):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, selected))
            snapshot = store.get_context_snapshot(ws, mission.mission_id, run.run_id)
            artifacts = [store.get_source_artifact(ws, ref.revision_id) for ref in selected]
            registry = RunReferences(snapshot, artifacts)
            column = ColumnRef(source_ref=selected[0], table_id=artifacts[0].tables[0].table_id, column="id")
            self.assertEqual(registry.resolve(registry.column(column), "column"), column)
            bad_refs = [column.model_copy(update={"column": "nonexistent"}),
                        column.model_copy(update={"table_id": "/nonexistent"}),
                        column.model_copy(update={"source_ref": selected[0].model_copy(
                            update={"workspace_id": fixtures._id(999)})}),
                        column.model_copy(update={"source_ref": selected[0].model_copy(
                            update={"revision_id": fixtures._id(999)})})]
            field = DefinitionField.model_validate(registry.field(FieldInput.model_validate(unknown_field())))
            for bad in bad_refs:
                with self.subTest(column=bad.column):
                    before = len(registry.values)
                    with self.assertRaises(HandleDenied):
                        registry.public(field.model_copy(update={"source_columns": [bad]}))
                    self.assertEqual(len(registry.values), before)

    def test_catalog_and_evidence_derive_only_from_selected_sources(self):
        setup = fixtures.PersistedRunTests()
        with setup.store_case(with_sources=True) as (store, ws, mission, selected):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, selected))
            snapshot = store.get_context_snapshot(ws, mission.mission_id, run.run_id)
            artifacts = [store.get_source_artifact(ws, ref.revision_id) for ref in selected]
            registry = RunReferences(snapshot, artifacts)
            self.assertEqual(len(registry.catalog), len(selected))
            for item, artifact in zip(registry.catalog, artifacts):
                self.assertEqual([t["row_count"] for t in item["tables"]], [t.row_count for t in artifact.tables])
                self.assertEqual(item["text_line_count"], artifact.text_line_count)
            source = selected[0]
            locator = CsvRowsLocator(kind="csv_rows", row_start=1, row_end=2, column=None)
            excerpt = store.read_source_excerpt(ws, source.revision_id, locator)
            result = registry.public(excerpt)
            self.assertEqual(registry.resolve(result["evidence_handle"], "evidence"), excerpt.source_ref)
            self.assertEqual(len(registry.coverage), 1)
            registry.public(excerpt)
            self.assertEqual(len(registry.coverage), 1)
            wrong = excerpt.source_ref.model_copy(update={"workspace_id": fixtures._id(999)})
            with self.assertRaises(HandleDenied):
                registry.evidence(wrong)
            with self.assertRaises(HandleDenied):
                RunReferences(snapshot, [])

    def test_seven_line_and_empty_text_catalogs_report_actual_bounds(self):
        setup = fixtures.PersistedRunTests()
        with setup.store_case() as (store, ws, mission, selected):
            revisions = []
            for name, content in (("brief.md", b"one\ntwo\nthree\nfour\nfive\nsix\nseven\n"),
                                  ("empty.md", b"")):
                revision, _ = store.import_source_revision(ws, name, "text/markdown", content)
                revisions.append(revision)
            refs = [fixtures._source_identity_for_test(r) for r in revisions]
            attempt = store.create_mission_draft_attempt(ws, "Read the short synthetic materials")
            store.mark_mission_draft_running(ws, attempt.attempt_id)
            candidate = fixtures.agent.MissionDraftPayload(title="Short materials", goal="Read bounds",
                                                           completion_criteria=["Report gaps"], scope_notes=[])
            receipt = fixtures.agent._make_receipt(provider=fixtures.FakeProvider([]), workspace_id=ws,
                attempt_id=attempt.attempt_id, mission_id=None, run_id=None, turn_index=1,
                status="succeeded", p0_sha256=fixtures.agent.P0_DRAFT_SHA256, usage=fixtures._usage())
            ready = store.save_mission_draft_result(ws, attempt.attempt_id, candidate, receipt)
            mission = store.confirm_mission_draft_attempt(ws, attempt.attempt_id, 1, ready.candidate_sha256, refs)
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            snapshot = store.get_context_snapshot(ws, mission.mission_id, run.run_id)
            registry = RunReferences(snapshot, [store.get_source_artifact(ws, ref.revision_id) for ref in refs])
            self.assertEqual([item["text_line_count"] for item in registry.catalog], [7, 0])
