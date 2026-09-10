import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import Request, Response
import test_agent as fixtures
import test_semantic_controller as controller
import test_clarifications as clarification_fixtures
from test_api import _endpoint
from contextox.api import create_app
from contextox.demo_content import demo_case
from contextox.models import SourceUploadRequest
from contextox.model_tools import SemanticProposalV1
from contextox.provider import DeepSeekProvider
from contextox.semantic_controller import build_context_plan, normalize_semantic_proposal
from contextox.store import WorkspaceStore


class DemoExportTests(unittest.TestCase):
    def test_export_keeps_exact_approved_answer_version_and_remaining_unknown(self):
        case = clarification_fixtures.ClarificationTests()
        case.setUp()
        try:
            saved, _ = case.save()
            case.approve(saved.answer)
            draft = case.store.get_mission_snapshot(case.ws, case.mid).draft
            result = case.store.export_candidate(case.ws, case.mid, draft.version, draft.sha256)
            exported = result.clarifications[0]
            self.assertEqual(exported.latest_answer, saved.answer)
            self.assertEqual(exported.latest_approval.answer_sha256, saved.answer.sha256)
            self.assertEqual(exported.latest_approval.answer_version, saved.answer.version)
            self.assertEqual(exported.latest_answer.items[1].disposition, "unknown")
            self.assertEqual(result.draft.semantic_approval, "pending")
        finally:
            case.doCleanups()

    def test_public_demo_imports_into_independent_workspaces_without_provider(self):
        example = _endpoint(create_app(), "/api/demo")()
        self.assertEqual(example.kind, "prepared_synthetic_preview")
        self.assertEqual(len(example.files), 3)
        upload = SourceUploadRequest(files=[file.model_dump(exclude={"sha256"}) for file in example.files], local_read_confirmed=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(DeepSeekProvider, "complete") as provider:
            store = WorkspaceStore.open(Path(directory))
            identities = []
            for index in range(2):
                ws = store.create_workspace(f"Synthetic Demo {index}")
                revisions = []
                for file in upload.files:
                    content = base64.b64decode(file.content_base64)
                    self.assertEqual(hashlib.sha256(content).hexdigest(), next(item.sha256 for item in example.files if item.original_name == file.original_name))
                    revision, artifact = store.import_source_revision(ws.workspace_id, file.original_name, file.media_type, content)
                    self.assertEqual(revision.parse_status, "ready")
                    revisions.append(revision.revision_id)
                identities.append(set(revisions))
                self.assertEqual(store.list_missions(ws.workspace_id), [])
            self.assertTrue(identities[0].isdisjoint(identities[1]))
            provider.assert_not_called()
        notes = next(file for file in example.files if file.original_name == "notes.md")
        self.assertIn("单位为元", base64.b64decode(notes.content_base64).decode())

    def test_export_preserves_partial_draft_evidence_and_questions_without_mutation(self):
        with fixtures.PersistedRunTests().store_case(with_sources=True, controller=True) as (store, ws, mission, refs):
            run, snapshot = controller.SemanticProposalBoundaryTests()._running_semantic_case(store, ws, mission, refs)
            plan, adapter = build_context_plan(snapshot, store)
            source = plan.sources[0]
            evidence = source["profile_pack"]["tables"][0]["evidence_handles"]
            proposal = SemanticProposalV1.model_validate({"version":"v1", "action":"draft_and_clarify", "public_answer":"Synthetic candidate",
                "fields":[{"field_key":"candidate_id", "name":"候选标识", "source_column_handles":[source["tables"][0]["columns"][0]["column_handle"]],
                    "evidence_status":"candidate", "evidence_handles":evidence}],
                "questions":[{"question":"是否保留前导零？", "why_needed":"明确标识规则", "expected_answer_type":"text",
                    "targets":[{"kind":"field", "key":"candidate_id", "property":"rule"}], "evidence_handles":evidence}]})
            result = store.apply_semantic_proposal(ws, mission.mission_id, run.run_id, normalize_semantic_proposal(adapter, proposal))
            before = store.get_mission_snapshot(ws, mission.mission_id)
            app = create_app(data_dir=store.data_dir)
            endpoint = _endpoint(app, "/api/workspaces/{workspace_id}/missions/{mission_id}/draft-export")
            request = Request({"type":"http", "headers":[], "path":"/export"})
            response = Response()
            document = endpoint(ws, mission.mission_id, request, response, result.draft.version, result.draft.sha256)
            exported = document.candidate
            self.assertEqual(exported.draft, result.draft)
            self.assertEqual(exported.clarifications[0].request, result.clarifications[0])
            self.assertEqual(exported.draft.fields[0].source_refs, result.draft.fields[0].source_refs)
            self.assertEqual(exported.product_status, "candidate_not_contract")
            self.assertEqual(exported.draft.semantic_approval, "pending")
            self.assertEqual(len(exported.draft.fields[0].unknowns), 6)
            self.assertIn(result.draft.sha256, document.markdown)
            self.assertIn("是否保留前导零", document.markdown)
            self.assertNotIn("reasoning_content", document.model_dump_json())
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(store.get_mission_snapshot(ws, mission.mission_id), before)
            for bad_ws, version, sha in ((ws, 99, result.draft.sha256), (ws, 1, "0"*64), (fixtures._id(987), 1, result.draft.sha256)):
                with self.subTest(workspace=bad_ws, version=version):
                    rejected = endpoint(bad_ws, mission.mission_id, request, Response(), version, sha)
                    self.assertIn(rejected.status_code, (404, 409))
            self.assertEqual(store.get_mission_snapshot(ws, mission.mission_id), before)


if __name__ == "__main__":
    unittest.main()
