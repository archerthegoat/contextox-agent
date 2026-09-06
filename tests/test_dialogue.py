"""Task conversation behavior through Store, runtime and the fake Provider seam."""
import asyncio
import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from contextox import agent
from contextox.api import _run_event_stream
from contextox.models import TaskMessageSendRequest, MessageHistoryRef
from contextox.provider import ProviderCompletion, ProviderToolCall, ProviderUsage
from contextox.runtime import Path2Runtime
from contextox.store import WorkspaceStore, Path2StateError, _EXPECTED_V3_TABLES, _EXPECTED_V3_INDEXES
from test_runtime import _mission, HoldingThread, FailingThread


def request(version=1, **changes):
    return TaskMessageSendRequest(**dict(dict(kind="message", client_request_id=str(uuid4()),
        expected_state_version=version, content="Explain this definition", references=[],
        history_messages=[], source_refs=[], provider_send_confirmed=True), **changes))


class AnswerProvider:
    config = {"endpoint_id": "deepseek_chat_completions", "model": "deepseek-v4-flash",
              "thinking": "enabled", "reasoning_effort": "high"}
    calls = 0
    packets = []

    @staticmethod
    def opaque_user_id(workspace_id):
        return workspace_id

    def complete(self, messages, **kwargs):
        self.calls += 1
        self.packets.append(messages)
        return ProviderCompletion(completion_id="synthetic", content="Transient thinking is not the final answer", reasoning_content="",
            tool_calls=(ProviderToolCall(call_id="call_answer", name="finish_run", arguments=json.dumps(
                {"outcome": "partial", "reason": "A bounded public answer", "source_refs": []})),),
            finish_reason="tool_calls", usage=ProviderUsage(1, 1, 0, 1))


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="contextox-dialogue-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore.open(self.temp.name)
        self.ws, self.mission = _mission(self.store)
        self.mid = self.mission.mission_id

    def test_sse_waits_for_answer_after_terminal_receipt(self):
        runtime = Path2Runtime(self.store)
        entered, release, observed = Event(), Event(), Event()
        save = self.store.save_run_final_output
        snapshot = self.store.get_run_snapshot

        def delayed_save(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release final output")
            return save(*args)

        def inspect_snapshot(*args):
            result = snapshot(*args)
            if entered.is_set() and not result.final_output:
                observed.set()
            return result

        async def check():
            receipt, _ = runtime.send_task_message(self.ws, self.mid, request())
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            self.assertTrue(runtime.is_run_active(self.ws, self.mid, receipt.run.run_id))
            self.assertFalse(runtime.is_run_active(str(uuid4()), self.mid, receipt.run.run_id))

            async def consume():
                return [part async for part in _run_event_stream(
                    self.store, runtime, self.ws, self.mid, receipt.run.run_id, 0)]

            stream = asyncio.create_task(consume())
            try:
                self.assertTrue(await asyncio.to_thread(observed.wait, 3))
                await asyncio.sleep(0.05)
                self.assertFalse(stream.done(), "SSE closed before the answer was saved")
            finally:
                release.set()
            chunks = await asyncio.wait_for(stream, 3)
            self.assertIn("event: run_partial", "".join(chunks))
            self.assertEqual(snapshot(self.ws, self.mid, receipt.run.run_id).final_output,
                             "A bounded public answer")

        try:
            with patch.object(agent, "get_provider", return_value=AnswerProvider()), \
                 patch.object(self.store, "save_run_final_output", side_effect=delayed_save), \
                 patch.object(self.store, "get_run_snapshot", side_effect=inspect_snapshot):
                asyncio.run(check())
        finally:
            release.set()
            runtime.shutdown()

    def test_send_is_atomic_and_idempotent_including_runtime_busy(self):
        runtime = Path2Runtime(self.store, thread_factory=HoldingThread)
        self.addCleanup(lambda: runtime.shutdown(timeout=0))
        req = request()
        receipt, created = runtime.send_task_message(self.ws, self.mid, req)
        self.assertTrue(created)
        replay, created = runtime.send_task_message(self.ws, self.mid, req)
        self.assertFalse(created)
        self.assertEqual(receipt.input_message, replay.input_message)
        with self.assertRaises(Path2StateError):
            runtime.send_task_message(self.ws, self.mid, req.model_copy(update={"content": "changed"}))
        self.assertEqual(len(self.store.list_task_messages(self.ws, self.mid).items), 2)
        self.assertEqual(self.store.list_task_runs(self.ws, self.mid).items[0].input_message_id, receipt.input_message.message_id)

    def test_two_answer_rounds_use_persisted_context_and_reason(self):
        provider = AnswerProvider()
        first, _ = self.store.send_task_message(self.ws, self.mid, request())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(self.store, self.ws, self.mid, first.run.run_id, Event())
        run = self.store.get_run_snapshot(self.ws, self.mid, first.run.run_id)
        self.assertEqual(run.status, "partial", run.error_code)
        self.assertEqual(run.final_output, "A bounded public answer")
        page = self.store.list_task_messages(self.ws, self.mid)
        self.assertEqual(len(page.items), 3)
        version = self.store.get_mission_snapshot(self.ws, self.mid).mission.state_version
        second, _ = self.store.send_task_message(self.ws, self.mid, request(version, history_messages=[
            MessageHistoryRef(message_id=m.message_id, sha256=m.sha256) for m in page.items[-2:]
        ]))
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(self.store, self.ws, self.mid, second.run.run_id, Event())
        self.assertEqual(provider.calls, 2)
        self.assertEqual(self.store.get_run_snapshot(self.ws, self.mid, second.run.run_id).status, "partial")
        context = self.store.get_context_snapshot(self.ws, self.mid, second.run.run_id).message_context
        self.assertEqual(len(context.history), 2)
        self.assertEqual(len(self.store.list_task_messages(self.ws, self.mid).items), 5)

    def test_pages_scope_and_bad_history_roll_back(self):
        original = self.store.list_task_messages(self.ws, self.mid).items[0]
        with self.assertRaises(Path2StateError):
            self.store.send_task_message(self.ws, self.mid, request(history_messages=[
                MessageHistoryRef(message_id=original.message_id, sha256="0" * 64)]))
        self.assertEqual(len(self.store.list_task_runs(self.ws, self.mid).items), 0)
        for limit in (0, 51, True):
            with self.assertRaises(Path2StateError):
                self.store.list_task_messages(self.ws, self.mid, limit=limit)
        with self.assertRaises(Path2StateError):
            self.store.list_task_messages(self.ws, self.mid, before_message_id=str(uuid4()))
        other_ws, other = _mission(self.store)
        with self.assertRaises(Path2StateError):
            self.store.list_task_messages(other_ws, other.mission_id, before_message_id=original.message_id)
        with closing(sqlite3.connect(self.store.db_path)) as c, c:
            for i in range(6):
                c.execute("INSERT INTO mission_messages VALUES (?,?,?,?,?,?,NULL,NULL)",
                    (self.ws, self.mid, str(uuid4()), original.created_at.isoformat(), "user", "中" * 32768))
        all_ids, cursor = [], None
        while True:
            page = self.store.list_task_messages(self.ws, self.mid, cursor, 50)
            self.assertLessEqual(len(page.model_dump_json().encode()), 262144)
            all_ids.extend(m.message_id for m in page.items)
            cursor = page.next_before_message_id
            if not cursor:
                break
        self.assertEqual(len(all_ids), 7)
        self.assertEqual(len(set(all_ids)), 7)

    def test_start_failure_is_reconciled_without_duplicate(self):
        runtime = Path2Runtime(self.store, thread_factory=FailingThread)
        self.addCleanup(lambda: runtime.shutdown(timeout=0))
        req = request()
        with self.assertRaises(Exception):
            runtime.send_task_message(self.ws, self.mid, req)
        receipt = self.store.message_submission(self.ws, self.mid, req.client_request_id)
        self.assertEqual(receipt.run.error_code, "agent_start_failed")
        replay, created = runtime.send_task_message(self.ws, self.mid, req)
        self.assertFalse(created)
        self.assertEqual(replay.run.run_id, receipt.run.run_id)

    def test_v3_read_only_then_explicit_migration_keeps_history(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            db = Path(directory) / "contextox.sqlite3"
            with closing(sqlite3.connect(db)) as c, c:
                for _, ddl in _EXPECTED_V3_TABLES:
                    c.execute(ddl)
                for _, _, ddl in _EXPECTED_V3_INDEXES:
                    c.execute(ddl)
                c.execute("PRAGMA user_version=3")
            store = WorkspaceStore.open(directory)
            ws, mission = _mission(store)
            before = store.list_task_messages(ws, mission.mission_id)
            with self.assertRaises(Path2StateError) as error:
                store.send_task_message(ws, mission.mission_id, request())
            self.assertEqual(error.exception.code, "task_dialogue_not_implemented")
            backup = store.migrate_task_dialogue()
            self.assertTrue((backup / "manifest.json").exists())
            self.assertEqual(store.list_task_messages(ws, mission.mission_id), before)
            with closing(sqlite3.connect(backup / "contextox.sqlite3")) as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertTrue(store.send_task_message(ws, mission.mission_id, request())[1])

    def test_unknown_model_turn_blocks_both_entrypoints(self):
        from contextox.models import ModelStartedEventInput, ModelStartedPayload
        from test_runtime import _request
        receipt, _ = self.store.send_task_message(self.ws, self.mid, request())
        self.store.mark_run_running(self.ws, self.mid, receipt.run.run_id)
        self.store.append_run_event(self.ws, self.mid, receipt.run.run_id,
            ModelStartedEventInput(event_type="model_started", public_payload=ModelStartedPayload(turn_index=1)))
        self.store.fail_run(self.ws, self.mid, receipt.run.run_id, "failed", "interrupted_without_receipt")
        mission = self.store.get_mission_snapshot(self.ws, self.mid).mission
        for send in (
            lambda: self.store.send_task_message(self.ws, self.mid, request(mission.state_version)),
            lambda: self.store.start_run(self.ws, self.mid, _request(mission)),
        ):
            with self.assertRaises(Path2StateError) as error:
                send()
            self.assertEqual(error.exception.code, "previous_outcome_unresolved")
        self.assertEqual(len(self.store.list_task_runs(self.ws, self.mid).items), 1)

    def test_waiting_for_review_does_not_accept_generic_message(self):
        with closing(sqlite3.connect(self.store.db_path)) as c, c:
            c.execute("UPDATE missions SET status='waiting_for_human' WHERE workspace_id=? AND mission_id=?", (self.ws, self.mid))
        with self.assertRaises(Path2StateError) as error:
            self.store.send_task_message(self.ws, self.mid, request())
        self.assertEqual(error.exception.code, "task_waiting_for_review")
        self.assertEqual(len(self.store.list_task_messages(self.ws, self.mid).items), 1)

    def test_run_input_mutation_rejected_before_context_or_replay(self):
        req = request()
        receipt, _ = self.store.send_task_message(self.ws, self.mid, req)
        with closing(sqlite3.connect(self.store.db_path)) as c, c:
            c.execute("UPDATE mission_messages SET content='Changed input' WHERE workspace_id=? AND mission_id=? AND message_id=?",
                (self.ws, self.mid, receipt.input_message.message_id))
        for read in (lambda: self.store.get_context_snapshot(self.ws, self.mid, receipt.run.run_id),
                     lambda: self.store.message_submission(self.ws, self.mid, req.client_request_id)):
            with self.assertRaises(Path2StateError):
                read()

    def test_migration_validation_failure_rolls_back_and_backup_is_exact(self):
        import contextox.store as module
        with closing(sqlite3.connect(self.store.db_path)) as c, c:
            c.execute("DROP TABLE run_message_inputs")
            c.execute("PRAGMA user_version=3")
        before = self.store.list_task_messages(self.ws, self.mid)
        real_validate = module._validate_connection_schema
        def validate(c):
            real_validate(c)
            if c.execute("PRAGMA user_version").fetchone()[0] == 4:
                raise module.WorkspaceStoreUnavailableError()
        with patch.object(module, "_validate_connection_schema", side_effect=validate):
            with self.assertRaises(module.WorkspaceStoreUnavailableError):
                self.store.migrate_task_dialogue()
        with closing(sqlite3.connect(self.store.db_path)) as c:
            self.assertTrue(module._schema_matches(c, 3, _EXPECTED_V3_TABLES, _EXPECTED_V3_INDEXES))
            self.assertFalse(c.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(self.store.list_task_messages(self.ws, self.mid), before)
        self.assertEqual({d.actual for d in self.store.diagnose(self.store.data_dir) if d.key == "workspace_store_schema"}, {"user_version=3"})

    def test_http_accept_replay_reconcile_and_scope(self):
        import asyncio
        from contextox.api import create_app
        from test_api import _asgi_request
        app = create_app(data_dir=Path(self.temp.name))
        app.state.path2_runtime = Path2Runtime(app.state.workspace_store, thread_factory=HoldingThread)
        self.addCleanup(lambda: app.state.path2_runtime.shutdown(timeout=0))
        url = f"/api/workspaces/{self.ws}/missions/{self.mid}/messages"
        req = request()
        status, data = asyncio.run(_asgi_request(app, "POST", url, req.model_dump_json().encode()))
        self.assertEqual(status, 202, data)
        first = json.loads(data)
        status, data = asyncio.run(_asgi_request(app, "POST", url, req.model_dump_json().encode()))
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["run"]["run_id"], first["run"]["run_id"])
        reconcile = url.rsplit("/", 1)[0] + "/message-submissions/" + req.client_request_id
        self.assertEqual(asyncio.run(_asgi_request(app, "GET", reconcile))[0], 200)
        self.assertEqual(asyncio.run(_asgi_request(app, "GET", url))[0], 200)
        changed = req.model_copy(update={"content": "Different"})
        self.assertEqual(asyncio.run(_asgi_request(app, "POST", url, changed.model_dump_json().encode()))[0], 409)
        self.assertEqual(asyncio.run(_asgi_request(app, "GET", url.replace(self.ws, str(uuid4()))))[0], 404)

    def test_budget_stage_contains_only_bounded_safe_diagnostics(self):
        from contextox.provider import ProviderContextBudgetError, _bounded_text_append, DeepSeekProvider
        with self.assertLogs("contextox.provider", level="WARNING") as logs:
            with self.assertRaises(ProviderContextBudgetError) as error:
                _bounded_text_append([], "SYNTHETIC_SECRET", max_bytes=2, used_bytes=[0])
        self.assertEqual(error.exception.stage, "stream_content")
        self.assertEqual(error.exception.code, "context_budget_exceeded")
        self.assertNotIn("SYNTHETIC_SECRET", " ".join(logs.output))
        with self.assertRaises(ProviderContextBudgetError) as error:
            list(DeepSeekProvider._iter_sse_data([b"data: {}\n\n"], max_bytes=2))
        self.assertEqual(error.exception.stage, "sse_wire")
