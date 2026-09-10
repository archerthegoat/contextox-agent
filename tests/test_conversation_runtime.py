"""Runtime activity ownership and handoff failure paths, without Provider I/O."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from contextox.runtime import Path2Runtime
from contextox.models import ConversationMessageSendRequest
from contextox.store import Path2StateError, WorkspaceStoreUnavailableError
from test_runtime import InlineThread, HoldingThread, FailingThread


class RuntimeStore:
    def __init__(self):
        self.ws, self.cid, self.tid, self.mid, self.rid = [str(uuid4()) for _ in range(5)]
        self.receipt = None
        self.failed = None
        self.output = SimpleNamespace(next_action="start_task")
        self.turn = SimpleNamespace(status="queued", mission_id=None, run_id=None,
                                    turn_id=self.tid, output=self.output)
        self.run = SimpleNamespace(run_id=self.rid, mission_id=self.mid, status="queued", started_at=None)
        self.handoffs = 0

    def set_event_sink(self, _):
        pass

    def conversation_submission(self, *_):
        return self.receipt

    def create_conversation_submission(self, *_, **kwargs):
        self.receipt = SimpleNamespace(run=None, discussion_turn=self.turn)
        return self.receipt, True

    def start_conversation_task(self, *_, **kwargs):
        self.handoffs += 1
        self.turn.mission_id, self.turn.run_id = self.mid, self.rid
        return SimpleNamespace(run=self.run)

    def fail_conversation_start(self, ws, cid, tid, code):
        self.failed = code

    def fail_discussion_turn(self, ws, cid, tid, status, code):
        self.turn.status, self.failed = status, code
        return self.turn

    def get_discussion_turn(self, *_):
        return self.turn

    def cancel_discussion_turn(self, *_):
        self.turn.status = "cancelled"
        return self.turn


class ConversationRuntimeTests(unittest.TestCase):
    def request(self):
        return ConversationMessageSendRequest(client_request_id=str(uuid4()),
            expected_state_version=1, content="开始整理订单金额", provider_send_confirmed=True)

    def test_stop_signals_owned_run_even_when_revocation_blocks_readback(self):
        from contextox.runtime import _ActiveTask
        from threading import Event
        store = RuntimeStore()
        runtime = Path2Runtime(store, thread_factory=HoldingThread)
        event = Event()
        runtime._active = _ActiveTask(token="owned", kind="run", cancel_event=event,
            workspace_id=store.ws, mission_id=store.mid, object_id=store.rid)
        store.cancel_run = lambda *_: (_ for _ in ()).throw(Path2StateError("source_permission_denied"))
        with self.assertRaises(Path2StateError):
            runtime.cancel_run(str(uuid4()), store.mid, store.rid)
        self.assertFalse(event.is_set())
        with self.assertRaises(Path2StateError):
            runtime.cancel_run(store.ws, store.mid, store.rid)
        self.assertTrue(event.is_set())

    def test_discussion_hands_over_same_slot_and_replay_never_starts_again(self):
        store = RuntimeStore()
        runtime = Path2Runtime(store, thread_factory=InlineThread)
        slot = []

        def discuss(*args, **kwargs):
            slot.append(runtime._active.token)
            self.assertTrue(runtime.busy)
            store.turn.status = "succeeded"
            return store.turn

        def run(*args, **kwargs):
            self.assertTrue(runtime.busy)
            self.assertEqual(runtime._active.token, slot[0])
            self.assertEqual(runtime._active.kind, "run")

        with patch("contextox.runtime.discussion.run_discussion", side_effect=discuss), \
             patch("contextox.runtime.agent.run_agent", side_effect=run) as execute:
            request = self.request()
            runtime.send_conversation_message(store.ws, store.cid, request)
            self.assertFalse(runtime.busy)
            self.assertEqual(store.handoffs, 1)
            _, created = runtime.send_conversation_message(store.ws, store.cid, request)
            self.assertFalse(created)
            self.assertEqual(execute.call_count, 1)

    def test_cancel_between_discussion_and_task_does_not_create_mission(self):
        store = RuntimeStore()
        runtime = Path2Runtime(store, thread_factory=InlineThread)

        def discuss(*args, **kwargs):
            args[4].set()
            store.turn.status = "succeeded"
            return store.turn

        with patch("contextox.runtime.discussion.run_discussion", side_effect=discuss):
            runtime.send_conversation_message(store.ws, store.cid, self.request())
        self.assertEqual(store.handoffs, 0)
        self.assertEqual(store.failed, "cancelled")
        self.assertFalse(runtime.busy)

    def test_thread_start_failure_persists_failure_and_releases_slot(self):
        store = RuntimeStore()
        runtime = Path2Runtime(store, thread_factory=FailingThread)
        with self.assertRaises(Exception):
            runtime.send_conversation_message(store.ws, store.cid, self.request())
        self.assertEqual(store.failed, "agent_start_failed")
        self.assertEqual(store.turn.status, "failed")
        self.assertFalse(runtime.busy)

    def test_shutdown_preserves_cancel_and_does_not_dispatch_queued_turn(self):
        store = RuntimeStore()
        runtime = Path2Runtime(store, thread_factory=HoldingThread)
        runtime.send_conversation_message(store.ws, store.cid, self.request())
        self.assertTrue(runtime.busy)
        runtime.shutdown(timeout=0)
        self.assertEqual(store.turn.status, "cancelled")
        self.assertEqual(store.handoffs, 0)

    def test_unknown_write_or_failed_reconciliation_never_becomes_retryable(self):
        for code in ("state_write_outcome_unknown", "state_conflict"):
            with self.subTest(code=code):
                store = RuntimeStore()
                runtime = Path2Runtime(store)
                request = self.request()
                receipt = SimpleNamespace(analysis_state="ready", run_id=None, mission_id=store.mid,
                                          send_request=request)
                store.send_task_message = lambda *a, **kw: (_ for _ in ()).throw(Path2StateError(code))
                store.message_submission = lambda *a: (_ for _ in ()).throw(WorkspaceStoreUnavailableError())
                with patch("contextox.runtime.conversation_handoff.handoff", return_value=(receipt, True)), \
                     patch("contextox.runtime.conversation_handoff.claim_analysis", return_value=(receipt, True)), \
                     patch("contextox.runtime.conversation_handoff.record_analysis", return_value=receipt) as record:
                    runtime.handoff_answers(store.ws, store.cid, request)
                    self.assertEqual(record.call_args.kwargs["state"], "unknown")
                    self.assertFalse(runtime.busy)

    def test_explicit_reconciliation_reads_existing_run_without_redispatch(self):
        store = RuntimeStore()
        runtime = Path2Runtime(store, thread_factory=FailingThread)
        request = self.request()
        receipt = SimpleNamespace(analysis_state="claimed", run_id=None, mission_id=store.mid,
                                  send_request=request)
        store.run.status = "failed"
        store.message_submission = lambda *a: SimpleNamespace(run=store.run)
        with patch("contextox.runtime.conversation_handoff.handoff", return_value=(receipt, False)), \
             patch("contextox.runtime.conversation_handoff.record_analysis", return_value=receipt) as record, \
             patch("contextox.runtime.agent.run_agent") as execute:
            runtime.handoff_answers(store.ws, store.cid, request)
            self.assertEqual(record.call_args.kwargs["run_id"], store.rid)
            execute.assert_not_called()
            self.assertFalse(runtime.busy)

    def test_replay_observer_cannot_fail_an_existing_run_on_record_error(self):
        store = RuntimeStore()
        runtime = Path2Runtime(store)
        request = self.request()
        receipt = SimpleNamespace(analysis_state="claimed", run_id=None, mission_id=store.mid,
                                  send_request=request)
        initial = SimpleNamespace(**{**vars(receipt), "analysis_state": "ready"})
        existing = SimpleNamespace(run=store.run)
        store.run.status = "running"
        store.send_task_message = lambda *a, **kw: (existing, False)
        store.message_submission = lambda *a: existing
        with patch("contextox.runtime.conversation_handoff.handoff", return_value=(initial, False)), \
             patch("contextox.runtime.conversation_handoff.claim_analysis", return_value=(receipt, True)), \
             patch("contextox.runtime.conversation_handoff.record_analysis",
                   side_effect=[WorkspaceStoreUnavailableError(), receipt]), \
             patch.object(runtime, "_fail_run") as fail:
            runtime.handoff_answers(store.ws, store.cid, request)
            fail.assert_not_called()
            self.assertFalse(runtime.busy)


if __name__ == "__main__":
    unittest.main()
