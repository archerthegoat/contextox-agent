"""Handoff storage seams with existing synthetic R2 fixtures; no Provider calls."""
import sqlite3
import unittest
from unittest.mock import patch
from contextlib import closing, contextmanager
from uuid import uuid4

from contextox import store as db
from contextox import conversation_handoff as handoff
from contextox.handoff_models import ConversationHandoffRequest
from contextox.models import canonical_sha256
import test_clarifications as r2_tests


class HandoffTests(unittest.TestCase):
    with_sources = True
    update = r2_tests.ClarificationTests.update
    save_payload = r2_tests.ClarificationTests.save_payload
    save = r2_tests.ClarificationTests.save
    approve = r2_tests.ClarificationTests.approve

    def setUp(self):
        r2_tests.ClarificationTests.setUp(self)
        self.cid = str(uuid4())
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "workspace_conversations" not in names:
                # Baseline compatibility while the independent v7 migration slice
                # is integrated. Exact schema validation still includes our DDL.
                sql = "CREATE TABLE workspace_conversations (workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL, mission_id TEXT, state_version INTEGER NOT NULL, source_refs_json TEXT NOT NULL, PRIMARY KEY(workspace_id,conversation_id))"
                connection.execute(sql)
                for _, ddl in handoff.TABLES:
                    connection.execute(ddl)
                schema_patch = patch.object(db, "_EXPECTED_V6_TABLES", (*db._EXPECTED_V6_TABLES, ("workspace_conversations", sql), *handoff.TABLES))
                schema_patch.start()
                self.addCleanup(schema_patch.stop)
                connection.execute("INSERT INTO workspace_conversations VALUES (?,?,?,?,?)", (self.ws, self.cid, self.mid, 1, db._canonical_json(self.sources)))
            else:
                connection.execute("INSERT INTO workspace_conversations (workspace_id,conversation_id,created_at,title,state_version,mission_id,source_refs_json,goal_json) VALUES (?,?,?,?,?,?,?,?)", (self.ws, self.cid, db._utc_now().isoformat(), "Synthetic handoff", 1, self.mid, db._canonical_json(self.sources), "null"))

            connection.commit()

    @contextmanager
    def assertCode(self, code):
        with self.assertRaises(db.Path2StateError) as caught:
            yield
        self.assertEqual(caught.exception.code, code)

    def payload(self, **changes):
        save = self.save_payload()
        data = dict(client_request_id=str(uuid4()), expected_conversation_version=1,
            expected_state_version=save.expected_state_version, expected_draft=save.review_draft,
            source_refs=save.source_refs, content="Apply the reviewed answers; preserve unknowns",
            references=[], history_messages=[], provider_send_confirmed=True,
            reviewed_answers=[dict(origin_run_id=self.origin, clarification_id=self.q.clarification_id,
                request_sha256=save.request_sha256, expected_latest_version=save.expected_latest_version,
                items=[a.model_dump(mode="json") for a in save.items])])
        data.update(changes)
        return ConversationHandoffRequest(**data)

    def submit(self, payload=None):
        return handoff.handoff(self.store, self.ws, self.cid, payload or self.payload())

    def test_fixed_payload_replay_and_single_claim(self):
        payload = self.payload()
        receipt, created = self.submit(payload)
        self.assertTrue(created)
        self.assertEqual(receipt.analysis_state, "ready")
        replay, created = self.submit(payload)
        self.assertFalse(created)
        self.assertEqual(replay, receipt)
        self.assertEqual(len(self.store.list_clarification_cases(self.ws, self.mid).items), 1)
        self.assertEqual(receipt.answer_steps[0].save_receipt.answer.version, 1)
        claimed, claimed_once = handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)
        self.assertTrue(claimed_once)
        self.assertFalse(handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)[1])
        self.assertEqual(handoff.read(self.store, self.ws, self.cid, payload.client_request_id), claimed)
        with self.assertCode("idempotency_conflict"):
            self.submit(payload.model_copy(update={"content": "Different"}))

    def test_approval_survives_known_failure_and_explicit_retry(self):
        payload = self.payload()
        receipt, _ = self.submit(payload)
        handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)
        failed = handoff.record_analysis(self.store, self.ws, self.cid, payload.client_request_id,
            state="failed", expected_analysis_state="claimed", error_code="runtime_busy")
        self.assertTrue(failed.answers_approved)
        self.assertEqual(handoff.read(self.store, self.ws, self.cid, payload.client_request_id).analysis_state, "failed")
        retry, yes = handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)
        self.assertTrue(yes)
        self.assertEqual(retry.send_request, receipt.send_request)
        submission, created = self.store.send_task_message(self.ws, self.mid, retry.send_request)
        self.assertTrue(created)
        started = handoff.record_analysis(self.store, self.ws, self.cid, payload.client_request_id,
            state="started", expected_analysis_state="claimed", run_id=submission.run.run_id)
        self.assertTrue(started.analysis_started)
        self.assertFalse(handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)[1])
        self.assertEqual(self.store.send_task_message(self.ws, self.mid, retry.send_request)[0].run.run_id, started.run_id)

    def test_unknown_result_never_reclaims(self):
        payload = self.payload()
        self.submit(payload)
        handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)
        handoff.record_analysis(self.store, self.ws, self.cid, payload.client_request_id,
            state="unknown", expected_analysis_state="claimed", error_code="state_write_outcome_unknown")
        replay, created = self.submit(payload)
        self.assertFalse(created)
        self.assertEqual(replay.analysis_state, "unknown")
        self.assertFalse(handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)[1])

    def test_stale_review_does_not_save_or_approve(self):
        payload = self.payload()
        self.save()
        with self.assertCode("state_conflict"):
            self.submit(payload)
        page = self.store.list_clarification_cases(self.ws, self.mid)
        self.assertIsNone(page.items[0].latest_approval)
        self.assertIsNone(handoff.read(self.store, self.ws, self.cid, payload.client_request_id))

    def test_incomplete_whole_answer_and_other_workspace_fail(self):
        payload = self.payload()
        values = payload.model_dump(mode="json")
        values["reviewed_answers"][0]["items"].pop()
        with self.assertCode("clarification_answer_incomplete"):
            self.submit(ConversationHandoffRequest(**values))
        other = self.store.create_workspace("Other synthetic workspace")
        with self.assertCode("conversation_not_found"):
            handoff.handoff(self.store, other.workspace_id, self.cid, payload)
        self.assertIsNone(self.store.list_clarification_cases(self.ws, self.mid).items[0].latest_answer)

    def test_atomic_local_failure_has_no_saved_or_approved_partial(self):
        payload = self.payload()
        original = handoff.r2.mutate
        def fail_approval(*args, **kwargs):
            if kwargs.get("version") is not None:
                raise db.Path2StateError("synthetic_approval_failure")
            return original(*args, **kwargs)
        with patch.object(handoff.r2, "mutate", side_effect=fail_approval):
            with self.assertCode("synthetic_approval_failure"):
                self.submit(payload)
        self.assertIsNone(self.store.list_clarification_cases(self.ws, self.mid).items[0].latest_answer)
        self.assertIsNone(handoff.read(self.store, self.ws, self.cid, payload.client_request_id))
        self.assertEqual(self.submit(payload)[0].answer_steps[0].save_receipt.answer.version, 1)

    def test_exact_existing_approved_answer_is_reused(self):
        saved, _ = self.save()
        approved, _ = self.approve(saved.answer)
        payload = self.payload()
        values = payload.model_dump(mode="json")
        values["reviewed_answers"][0].update(items=None, saved_answer=dict(
            version=saved.answer.version, sha256=saved.answer.sha256, approval_id=approved.approval.approval_id))
        receipt, _ = self.submit(ConversationHandoffRequest(**values))
        step = receipt.answer_steps[0]
        self.assertIsNone(step.save_request)
        self.assertIsNone(step.approve_request)
        self.assertEqual(step.approved_answer.approval_id, approved.approval.approval_id)

    def test_actual_missing_approval_and_subrequest_are_not_reported_approved(self):
        payload = self.payload()
        receipt, _ = self.submit(payload)
        with closing(sqlite3.connect(self.store.db_path)) as connection, connection:
            connection.execute("DELETE FROM clarification_answer_approvals WHERE workspace_id=? AND mission_id=?", (self.ws, self.mid))
        with self.assertRaises(db.WorkspaceStoreError):
            handoff.read(self.store, self.ws, self.cid, payload.client_request_id)
        # Restoring the exact approved R2 row makes the receipt valid again.
        approval = receipt.answer_steps[0].approve_receipt.approval
        with closing(sqlite3.connect(self.store.db_path)) as connection, connection:
            connection.execute("INSERT INTO clarification_answer_approvals VALUES (?,?,?,?,?,?,?,?,?)", (self.ws, self.mid,
                approval.origin_run_id, approval.clarification_id, approval.answer_version,
                approval.answer_sha256, approval.approval_id, approval.approved_by, approval.approved_at.isoformat()))
        self.assertTrue(handoff.read(self.store, self.ws, self.cid, payload.client_request_id).answers_approved)
        with closing(sqlite3.connect(self.store.db_path)) as connection, connection:
            connection.execute("DELETE FROM clarification_submissions WHERE workspace_id=? AND mission_id=? AND client_request_id=?",
                (self.ws, self.mid, receipt.answer_steps[0].save_request.client_request_id))
        with self.assertRaises(db.WorkspaceStoreError):
            handoff.read(self.store, self.ws, self.cid, payload.client_request_id)

    def test_unknown_cannot_downgrade_and_stale_observer_cannot_update(self):
        payload = self.payload()
        receipt, _ = self.submit(payload)
        handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)
        handoff.record_analysis(self.store, self.ws, self.cid, payload.client_request_id,
            state="unknown", expected_analysis_state="claimed", error_code="state_write_outcome_unknown")
        with self.assertCode("state_conflict"):
            handoff.record_analysis(self.store, self.ws, self.cid, payload.client_request_id,
                state="failed", expected_analysis_state="claimed", error_code="agent_start_failed")
        with self.assertCode("previous_outcome_unresolved"):
            handoff.record_analysis(self.store, self.ws, self.cid, payload.client_request_id,
                state="failed", expected_analysis_state="unknown", error_code="agent_start_failed")
        self.assertFalse(handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)[1])
        submission, _ = self.store.send_task_message(self.ws, self.mid, receipt.send_request)
        started = handoff.record_analysis(self.store, self.ws, self.cid, payload.client_request_id,
            state="started", expected_analysis_state="unknown", run_id=submission.run.run_id)
        self.assertTrue(started.analysis_started)
        with self.assertCode("state_conflict"):
            handoff.record_analysis(self.store, self.ws, self.cid, payload.client_request_id,
                state="failed", expected_analysis_state="started", run_id=submission.run.run_id,
                error_code="late_worker_error")

    def test_exact_old_handoff_remains_readable_after_new_answer_version(self):
        payload = self.payload()
        receipt, _ = self.submit(payload)
        saved, _ = self.save()
        self.assertEqual(saved.answer.version, 2)
        read = handoff.read(self.store, self.ws, self.cid, payload.client_request_id)
        self.assertEqual(read, receipt)
        self.assertEqual(read.answer_steps[0].approved_answer.answer_version, 1)

    def test_changed_conversation_scope_cannot_claim_old_handoff(self):
        payload = self.payload()
        self.submit(payload)
        with closing(sqlite3.connect(self.store.db_path)) as connection, connection:
            connection.execute("UPDATE workspace_conversations SET state_version=state_version+1 WHERE workspace_id=? AND conversation_id=?", (self.ws, self.cid))
        with self.assertCode("state_conflict"):
            handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)
        self.assertTrue(handoff.read(self.store, self.ws, self.cid, payload.client_request_id).answers_approved)

    def test_revoked_source_blocks_handoff_read_and_claim(self):
        payload = self.payload()
        self.submit(payload)
        with closing(sqlite3.connect(self.store.db_path)) as connection, connection:
            connection.execute("UPDATE source_revisions SET permission_status='denied' WHERE workspace_id=? AND revision_id=?", (self.ws, self.sources[0].revision_id))
        for action in (lambda: self.submit(payload),
                       lambda: handoff.read(self.store, self.ws, self.cid, payload.client_request_id),
                       lambda: handoff.claim_analysis(self.store, self.ws, self.cid, payload.client_request_id)):
            with self.assertRaises(db.WorkspaceStoreError):
                action()

    def test_unreviewed_question_set_is_not_filled_from_latest(self):
        payload = self.payload()
        values = payload.model_dump(mode="json")
        values["reviewed_answers"][0]["clarification_id"] = str(uuid4())
        with self.assertCode("clarification_review_set_conflict"):
            self.submit(ConversationHandoffRequest(**values))


if __name__ == "__main__":
    unittest.main()
