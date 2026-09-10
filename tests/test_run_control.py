"""Run control survives revoked content without weakening public reads or hashes."""
import json
import unittest
from unittest.mock import patch

from contextox import agent, store as db
from contextox.runtime import Path2Runtime


class RunControlTests(unittest.TestCase):
    def running_case(self):
        from test_agent import FakeProvider, _usage
        from test_clarifications import ClarificationTests

        case = ClarificationTests()
        case.with_sources = True
        case.setUp()
        self.addCleanup(case.doCleanups)
        saved, _ = case.save()
        case.approve(saved.answer)
        (submission, _), request = case.send()
        run_id = submission.run.run_id
        case.store.mark_run_running(case.ws, case.mid, run_id)
        snapshot = case.store.get_context_snapshot(case.ws, case.mid, run_id)
        manifest = case.store.record_context_manifest(
            case.ws, case.mid, run_id,
            agent._context_manifest(snapshot, turn_index=1, tool_receipt_ids=[]),
        )
        receipt = agent._make_receipt(
            provider=FakeProvider([]), workspace_id=case.ws,
            attempt_id=None, mission_id=case.mid, run_id=run_id,
            turn_index=1, status="succeeded", p0_sha256=agent.P0_RUN_SHA256,
            usage=_usage(), context_manifest=manifest,
        )
        case.store.record_provider_receipt(case.ws, case.mid, run_id, receipt)
        return case, run_id, request

    def revoke(self, case):
        with case.store._write_transaction() as connection:
            connection.execute(
                "UPDATE source_revisions SET permission_status='denied' "
                "WHERE workspace_id=? AND revision_id=?",
                (case.ws, case.sources[0].revision_id),
            )

    def raw_state(self, case, run_id):
        with case.store._connection() as connection:
            return connection.execute(
                "SELECT status,phase,error_code,last_sequence,finished_at FROM runs "
                "WHERE workspace_id=? AND mission_id=? AND run_id=?",
                (case.ws, case.mid, run_id),
            ).fetchone()

    def assert_public_reads_denied(self, case, run_id, request):
        for read in (
            lambda: case.store.get_run_snapshot(case.ws, case.mid, run_id),
            lambda: case.store.get_mission_snapshot(case.ws, case.mid),
            lambda: case.store.message_submission(case.ws, case.mid, request.client_request_id),
            lambda: case.store.list_run_events(case.ws, case.mid, run_id),
            lambda: case.store.list_clarification_cases(case.ws, case.mid),
        ):
            with self.assertRaises(db.Path2StateError) as raised:
                read()
            self.assertEqual(raised.exception.code, "source_permission_denied")

    def assert_one_failure_event(self, case, run_id, status, code, published):
        expected_payload = {"status": status, "terminal_receipt_id": None, "error_code": code}
        with case.store._connection() as connection:
            rows = connection.execute(
                "SELECT event_type,public_payload_json FROM run_events "
                "WHERE workspace_id=? AND mission_id=? AND run_id=? "
                "AND event_type IN ('run_blocked','run_failed') ORDER BY sequence",
                (case.ws, case.mid, run_id),
            ).fetchall()
        self.assertEqual([(kind, json.loads(payload)) for kind, payload in rows],
                         [(f"run_{status}", expected_payload)])
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].event_type, f"run_{status}")
        self.assertEqual(published[0].public_payload.model_dump(), expected_payload)

    def test_cancel_revoked_approved_run_commits_before_public_rejection(self):
        case, run_id, request = self.running_case()
        self.revoke(case)
        with patch.object(db, "_read_validated_source_file", side_effect=AssertionError("control read source")) as reader:
            with self.assertRaises(db.Path2StateError) as raised:
                case.store.cancel_run(case.ws, case.mid, run_id)
            self.assertEqual(raised.exception.code, "source_permission_denied")
            stopped = self.raw_state(case, run_id)
            self.assertEqual(stopped[:3], ("cancelled", "terminal", "cancelled"))
            self.assertIsNotNone(stopped[4])
            # Repeating the same stop cannot create another terminal event.
            with self.assertRaises(db.Path2StateError):
                case.store.cancel_run(case.ws, case.mid, run_id)
            self.assertEqual(self.raw_state(case, run_id), stopped)
            case.store = db.WorkspaceStore.open(case.store.data_dir)
            self.assert_public_reads_denied(case, run_id, request)
            reader.assert_not_called()
        with case.store._connection() as connection:
            event = connection.execute(
                "SELECT event_type,public_payload_json FROM run_events "
                "WHERE workspace_id=? AND mission_id=? AND run_id=? ORDER BY sequence DESC LIMIT 1",
                (case.ws, case.mid, run_id),
            ).fetchone()
        self.assertEqual(event[0], "run_cancelled")
        self.assertEqual(json.loads(event[1]), {
            "status": "cancelled", "terminal_receipt_id": None, "error_code": "cancelled",
        })

    def test_fail_revoked_approved_run_commits_before_public_rejection(self):
        for status in ("blocked", "failed"):
            with self.subTest(status=status):
                case, run_id, request = self.running_case()
                self.revoke(case)
                published = []
                case.store.set_event_sink(published.append)
                with patch.object(db, "_read_validated_source_file", side_effect=AssertionError("control read source")) as reader:
                    with self.assertRaises(db.Path2StateError) as raised:
                        case.store.fail_run(case.ws, case.mid, run_id, status, "synthetic_failure")
                    self.assertEqual(raised.exception.code, "source_permission_denied")
                    stopped = self.raw_state(case, run_id)
                    self.assertEqual(stopped[:3], (status, "terminal", "synthetic_failure"))
                    with self.assertRaises(db.Path2StateError):
                        case.store.fail_run(case.ws, case.mid, run_id, status, "synthetic_failure")
                    self.assertEqual(self.raw_state(case, run_id), stopped)
                    case.store = db.WorkspaceStore.open(case.store.data_dir)
                    self.assert_public_reads_denied(case, run_id, request)
                    reader.assert_not_called()
                self.assert_one_failure_event(case, run_id, status, "synthetic_failure", published)

    def test_runtime_failure_after_revocation_emits_one_content_free_event(self):
        case, run_id, request = self.running_case()
        self.revoke(case)
        runtime = Path2Runtime(case.store)
        with patch.object(db, "_read_validated_source_file", side_effect=AssertionError("failure read source")) as reader:
            runtime._fail_run(case.ws, case.mid, run_id, "agent_worker_failed")
            stopped = self.raw_state(case, run_id)
            runtime._fail_run(case.ws, case.mid, run_id, "agent_worker_failed")
            self.assertEqual(self.raw_state(case, run_id), stopped)
            self.assertEqual(stopped[:3], ("failed", "terminal", "agent_worker_failed"))
            self.assert_public_reads_denied(case, run_id, request)
            reader.assert_not_called()
        self.assert_one_failure_event(case, run_id, "failed", "agent_worker_failed",
                                      runtime.buffered_events(case.ws, case.mid, run_id, 0))

    def test_readable_runtime_and_agent_stop_paths_do_not_duplicate_events(self):
        for caller, status in (("runtime", "failed"), ("agent", "failed"), ("agent", "blocked")):
            with self.subTest(caller=caller, status=status):
                case, run_id, _ = self.running_case()
                runtime = Path2Runtime(case.store)
                for _ in range(2):
                    if caller == "runtime":
                        runtime._fail_run(case.ws, case.mid, run_id, "synthetic_failure")
                    else:
                        agent._stop_run(case.store, case.ws, case.mid, run_id, status, "synthetic_failure")
                self.assertEqual(case.store.get_run_snapshot(case.ws, case.mid, run_id).status, status)
                self.assert_one_failure_event(case, run_id, status, "synthetic_failure",
                                              runtime.buffered_events(case.ws, case.mid, run_id, 0))

    def test_failed_terminal_event_write_rolls_back_run_transition(self):
        case, run_id, _ = self.running_case()
        before = self.raw_state(case, run_id)
        published = []
        case.store.set_event_sink(published.append)
        append = db._append_event_in_transaction

        def reject_terminal(connection, run, event_type, payload):
            if event_type == "run_failed":
                raise db.WorkspaceStoreUnavailableError()
            return append(connection, run, event_type, payload)

        with patch.object(db, "_append_event_in_transaction", side_effect=reject_terminal):
            with self.assertRaises(db.WorkspaceStoreUnavailableError):
                case.store.fail_run(case.ws, case.mid, run_id, "failed", "synthetic_failure")
        self.assertEqual(self.raw_state(case, run_id), before)
        self.assertEqual(published, [])
        self.assertFalse(any(event.event_type == "run_failed"
                             for event in case.store.list_run_events(case.ws, case.mid, run_id)))

    def test_open_recovers_revoked_approved_run_without_reading_source(self):
        case, run_id, request = self.running_case()
        self.revoke(case)
        with patch.object(db, "_read_validated_source_file", side_effect=AssertionError("recovery read source")) as reader:
            case.store = db.WorkspaceStore.open(case.store.data_dir)
            stopped = self.raw_state(case, run_id)
            self.assertEqual(stopped[:3], ("failed", "terminal", "interrupted_without_receipt"))
            self.assertEqual(case.store.recover_interrupted_runs(), 0)
            self.assertEqual(self.raw_state(case, run_id), stopped)
            self.assert_public_reads_denied(case, run_id, request)
            reader.assert_not_called()
        with case.store._connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT event_type FROM run_events WHERE workspace_id=? AND mission_id=? "
                "AND run_id=? ORDER BY sequence DESC LIMIT 1", (case.ws, case.mid, run_id),
            ).fetchone(), ("run_failed",))

    def test_control_does_not_read_missing_or_changed_approved_source(self):
        for failure in ("missing", "changed"):
            with self.subTest(failure=failure):
                case, run_id, _ = self.running_case()
                revision = next(r for r in case.store.list_source_revisions(case.ws)
                                if r.revision_id == case.sources[0].revision_id)
                source_path = db._source_path(case.store.data_dir, revision)
                if failure == "missing":
                    source_path.unlink()
                else:
                    source_path.write_bytes(b"changed synthetic content")
                with patch.object(db, "_read_validated_source_file", side_effect=AssertionError("recovery read source")) as reader:
                    case.store = db.WorkspaceStore.open(case.store.data_dir)
                    reader.assert_not_called()
                self.assertEqual(self.raw_state(case, run_id)[:3],
                                 ("failed", "terminal", "interrupted_without_receipt"))
                with self.assertRaises(db.WorkspaceStoreError):
                    case.store.get_run_snapshot(case.ws, case.mid, run_id)

    def test_cross_workspace_control_cannot_change_run(self):
        case, run_id, _ = self.running_case()
        other_ws = case.store.create_workspace("Other synthetic workspace").workspace_id
        self.revoke(case)
        before = self.raw_state(case, run_id)
        for control in (
            lambda: case.store.cancel_run(other_ws, case.mid, run_id),
            lambda: case.store.fail_run(other_ws, case.mid, run_id, "failed", "synthetic_failure"),
        ):
            with self.assertRaises(db.WorkspaceStoreError):
                control()
            self.assertEqual(self.raw_state(case, run_id), before)

    def test_control_still_rejects_tampered_answer_hash(self):
        case, run_id, _ = self.running_case()
        self.revoke(case)
        before = self.raw_state(case, run_id)
        with case.store._write_transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM clarification_answer_versions WHERE workspace_id=? AND mission_id=?",
                (case.ws, case.mid),
            ).fetchone()
            payload = json.loads(row[0])
            payload["items"][0]["answer"] = "Never approved synthetic answer"
            connection.execute(
                "UPDATE clarification_answer_versions SET payload_json=? WHERE workspace_id=? AND mission_id=?",
                (db._canonical_json(payload), case.ws, case.mid),
            )
        for control in (
            lambda: case.store.cancel_run(case.ws, case.mid, run_id),
            lambda: case.store.fail_run(case.ws, case.mid, run_id, "failed", "synthetic_failure"),
            lambda: case.store.recover_interrupted_runs(),
        ):
            with self.assertRaises(db.WorkspaceStoreUnavailableError):
                control()
            self.assertEqual(self.raw_state(case, run_id), before)
