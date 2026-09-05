import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from contextox import agent
from contextox.models import MissionDraftPayload, RunStartRequest
from contextox.provider import ProviderUsage
from contextox.runtime import Path2Runtime
from contextox.store import WorkspaceStore, WorkspaceStoreBusyError, WorkspaceStoreUnavailableError


class HoldingThread:
    instances = []

    def __init__(self, *, target, args, name, daemon):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False
        self.joined = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started and not self.joined

    def join(self, timeout=None):
        del timeout
        self.joined = True


class InlineThread(HoldingThread):
    def start(self):
        self.started = True
        self.target(*self.args)
        self.joined = True


class FailingThread(HoldingThread):
    def start(self):
        raise RuntimeError("synthetic thread start failure")


def _mission(store: WorkspaceStore):
    workspace_id = store.create_workspace("Runtime test").workspace_id
    attempt = store.create_mission_draft_attempt(workspace_id, "Define the data")
    store.mark_mission_draft_running(workspace_id, attempt.attempt_id)
    candidate = MissionDraftPayload(
        title="Runtime mission", goal="Test one slot",
        completion_criteria=["Return a candidate"], scope_notes=[],
    )

    class Provider:
        config = {
            "endpoint_id": "deepseek_chat_completions",
            "model": "deepseek-v4-flash",
            "thinking": "enabled",
            "reasoning_effort": "high",
        }

        @staticmethod
        def opaque_user_id(workspace_id):
            return workspace_id

    receipt = agent._make_receipt(
        provider=Provider(), workspace_id=workspace_id,
        attempt_id=attempt.attempt_id, mission_id=None, run_id=None,
        turn_index=1, status="succeeded", p0_sha256=agent.P0_DRAFT_SHA256,
        usage=ProviderUsage(input_tokens=1, output_tokens=1,
                            cache_hit_tokens=0, cache_miss_tokens=1),
    )
    ready = store.save_mission_draft_result(
        workspace_id, attempt.attempt_id, candidate, receipt
    )
    mission = store.confirm_mission_draft_attempt(
        workspace_id, attempt.attempt_id, 1, ready.candidate_sha256, []
    )
    return workspace_id, mission


def _request(mission, number=1):
    return RunStartRequest(
        expected_state_version=mission.state_version,
        source_refs=[], provider_send_confirmed=True,
        client_request_id=f"00000000-0000-4000-8000-{number:012d}",
    )


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        HoldingThread.instances.clear()

    def test_slot_is_reserved_before_attempt_creation_and_shutdown_closes_it(self):
        with tempfile.TemporaryDirectory(prefix="contextox-runtime-", dir="/private/tmp") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Runtime").workspace_id
            runtime = Path2Runtime(store, thread_factory=HoldingThread)
            attempt = runtime.start_mission_draft(workspace_id, "Define the source")
            self.assertTrue(runtime.busy)
            with self.assertRaises(WorkspaceStoreBusyError):
                runtime.start_mission_draft(workspace_id, "Second attempt")
            self.assertEqual(
                len(store.list_missions(workspace_id)), 0
            )
            self.assertTrue(runtime.shutdown(timeout=0))
            self.assertEqual(
                store.get_mission_draft_attempt(workspace_id, attempt.attempt_id).status,
                "cancelled",
            )
            with self.assertRaises(WorkspaceStoreBusyError):
                runtime.start_mission_draft(workspace_id, "After shutdown")

    def test_inline_worker_releases_slot_and_thread_start_failure_is_persisted(self):
        with tempfile.TemporaryDirectory(prefix="contextox-runtime-", dir="/private/tmp") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Runtime").workspace_id
            runtime = Path2Runtime(store, thread_factory=InlineThread)

            def finish_attempt(store, workspace_id, attempt_id, cancel_event):
                del cancel_event
                store.fail_mission_draft_attempt(
                    workspace_id, attempt_id, "blocked", "synthetic_block", None
                )

            with patch.object(agent, "generate_mission_draft", side_effect=finish_attempt):
                attempt = runtime.start_mission_draft(workspace_id, "Define the source")
            self.assertFalse(runtime.busy)
            self.assertEqual(
                store.get_mission_draft_attempt(workspace_id, attempt.attempt_id).status,
                "blocked",
            )

        with tempfile.TemporaryDirectory(prefix="contextox-runtime-", dir="/private/tmp") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Runtime").workspace_id
            runtime = Path2Runtime(store, thread_factory=FailingThread)
            with self.assertRaises(WorkspaceStoreUnavailableError):
                runtime.start_mission_draft(workspace_id, "Define the source")
            with closing(sqlite3.connect(store.db_path)) as connection:
                attempts = connection.execute(
                    "SELECT attempt_id, status, error_code FROM mission_draft_attempts"
                ).fetchall()
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0][1:], ("failed", "agent_start_failed"))
            self.assertFalse(runtime.busy)

        with tempfile.TemporaryDirectory(prefix="contextox-runtime-", dir="/private/tmp") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id, mission = _mission(store)
            runtime = Path2Runtime(store, thread_factory=FailingThread)
            with self.assertRaises(WorkspaceStoreUnavailableError):
                runtime.start_run(workspace_id, mission.mission_id, _request(mission, 8))
            with closing(sqlite3.connect(store.db_path)) as connection:
                run_id = connection.execute("SELECT run_id FROM runs").fetchone()[0]
            failed = store.get_run_snapshot(workspace_id, mission.mission_id, run_id)
            self.assertEqual(
                (failed.status, failed.error_code),
                ("failed", "agent_start_failed"),
            )
            self.assertEqual(
                store.list_run_events(workspace_id, mission.mission_id, run_id)[-1].event_type,
                "run_failed",
            )

    def test_run_replay_busy_cancel_and_shutdown_are_bounded(self):
        with tempfile.TemporaryDirectory(prefix="contextox-runtime-", dir="/private/tmp") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id, mission = _mission(store)
            runtime = Path2Runtime(store, thread_factory=HoldingThread)
            request = _request(mission)
            run = runtime.start_run(workspace_id, mission.mission_id, request)
            self.assertEqual(
                runtime.start_run(workspace_id, mission.mission_id, request).run_id,
                run.run_id,
            )
            other_workspace = store.create_workspace("Other runtime").workspace_id
            with self.assertRaises(WorkspaceStoreBusyError):
                runtime.start_mission_draft(other_workspace, "Other work")
            cancelled = runtime.cancel_run(workspace_id, mission.mission_id, run.run_id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertTrue(HoldingThread.instances[-1].args[0].cancel_event.is_set())
            self.assertTrue(runtime.shutdown(timeout=0))

    def test_event_buffer_is_bounded_and_scoped(self):
        with tempfile.TemporaryDirectory(prefix="contextox-runtime-", dir="/private/tmp") as directory:
            store = WorkspaceStore.open(directory)
            runtime = Path2Runtime(store, event_capacity=2)
            from contextox.models import RunEventEnvelope

            for sequence in range(1, 4):
                event = RunEventEnvelope.model_validate({
                    "event_id": str(sequence), "event_type": "model_started",
                    "occurred_at": "2026-09-05T00:00:00+00:00",
                    "workspace_id": "00000000-0000-4000-8000-000000000001",
                    "mission_id": "00000000-0000-4000-8000-000000000002",
                    "run_id": "00000000-0000-4000-8000-000000000003",
                    "sequence": sequence,
                    "public_payload": {"turn_index": 1},
                })
                runtime.publish_event(event)
            buffered = runtime.buffered_events(
                "00000000-0000-4000-8000-000000000001",
                "00000000-0000-4000-8000-000000000002",
                "00000000-0000-4000-8000-000000000003", 0,
            )
            self.assertEqual([event.root.sequence for event in buffered], [2, 3])


if __name__ == "__main__":
    unittest.main()
