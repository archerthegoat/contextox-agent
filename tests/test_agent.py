import json
import concurrent.futures
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from contextox import agent
from contextox.models import (
    ClarificationRequest,
    ContextPacketManifest,
    ContextSnapshot,
    CreateClarificationCall,
    DomainRejection,
    InspectDatasetCall,
    ListSourcesCall,
    Mission,
    MissionDraftAttempt,
    ProviderConfigSnapshot,
    ProviderReceipt,
    ReadSourceCall,
    RunCompletedEventInput,
    RunCompletedPayload,
    RunStartRequest,
    RunSnapshot,
    RunToolResult,
    SourceIdentity,
    SubmitForReviewCall,
    TerminalReceipt,
    ToolReceipt,
    UpdateDefinitionDraftCall,
    canonical_sha256,
)
from contextox.provider import (
    ProviderCancelledError,
    ProviderCompletion,
    ProviderToolCall,
    ProviderUsage,
)
from contextox.store import (
    MissionDraftAttemptNotFoundError, Path2StateError, WorkspaceStore,
    WorkspaceStoreError, WorkspaceStoreUnavailableError,
)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _usage() -> ProviderUsage:
    return ProviderUsage(input_tokens=9, output_tokens=5, cache_hit_tokens=1, cache_miss_tokens=8)


class ControlledClock:
    def __init__(self, values: list[float], default: float | None = None) -> None:
        self.values = list(values)
        self.default = values[-1] if default is None else default

    def monotonic(self) -> float:
        if self.values:
            return self.values.pop(0)
        return self.default


def _attempt() -> MissionDraftAttempt:
    return MissionDraftAttempt(
        workspace_id=_id(1),
        attempt_id=_id(2),
        created_at="2026-09-03T00:00:00+00:00",
        original_input="Map the order and customer fields.",
        status="queued",
        candidate=None,
        candidate_version=None,
        candidate_sha256=None,
        provider_receipt_id=None,
        mission_id=None,
        error_code=None,
    )


def _context_snapshot(status: str = "queued") -> ContextSnapshot:
    mission = Mission(
        workspace_id=_id(1),
        mission_id=_id(3),
        created_at="2026-09-03T00:00:00+00:00",
        state_version=1,
        status="active",
        title="Definition mapping",
        goal="Map authorized tables.",
        completion_criteria=["Produce a candidate draft"],
        scope_notes=[],
        original_attempt_id=_id(2),
        source_refs=[],
    )
    run = RunSnapshot(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        status=status,
        created_at="2026-09-03T00:00:00+00:00",
        started_at=None,
        finished_at=None,
        budget={
            "max_model_turns": 8,
            "max_tool_calls": 24,
            "max_elapsed_ms": 300000,
            "max_output_tokens": 4096,
            "max_retries": 0,
            "connect_timeout_ms": 10000,
            "first_event_timeout_ms": 60000,
            "idle_timeout_ms": 30000,
            "total_timeout_ms": 120000,
            "max_context_bytes": 262144,
        },
        source_refs=[],
        draft=None,
        clarifications=[],
        last_sequence=0,
        terminal_receipt=None,
        final_output=None,
        error_code=None,
    )
    return ContextSnapshot(mission=mission, run=run, sources=[], draft=None, clarifications=[])


class FakeProvider:
    def __init__(self, completions: list[ProviderCompletion], cancel_event: Event | None = None) -> None:
        self.completions = list(completions)
        self.calls: list[dict] = []
        self.cancel_event = cancel_event
        self.config = ProviderConfigSnapshot(
            endpoint_id="deepseek_chat_completions",
            model="deepseek-v4-flash",
            thinking="enabled",
            reasoning_effort="high",
        )

    @staticmethod
    def opaque_user_id(workspace_id: str) -> str:
        return "ws-test"

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "kwargs": kwargs})
        if self.cancel_event is not None:
            self.cancel_event.set()
            raise ProviderCancelledError()
        completion = self.completions.pop(0)
        callback = kwargs.get("on_content")
        if callback is not None and completion.content:
            callback(completion.content)
        return completion


class PersistedAttemptTests(unittest.TestCase):
    @contextmanager
    def store_case(self):
        with tempfile.TemporaryDirectory(prefix="contextox-attempt-", dir="/private/tmp") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Synthetic definition case").workspace_id
            attempt = store.create_mission_draft_attempt(workspace_id, "Define the synthetic customer ID.")
            yield store, workspace_id, attempt

    def generate(self, store, workspace_id, attempt, *, content=None, usage=True):
        payload = {
            "title": "Customer ID", "goal": "Define customer ID",
            "completion_criteria": ["Return a candidate definition"], "scope_notes": [],
        }
        provider = FakeProvider([ProviderCompletion(
            completion_id="synthetic-completion",
            content=json.dumps(payload) if content is None else content,
            reasoning_content=None, tool_calls=[], finish_reason="stop",
            usage=_usage() if usage else None,
        )])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.generate_mission_draft(store, workspace_id, attempt.attempt_id, Event())
            agent.generate_mission_draft(store, workspace_id, attempt.attempt_id, Event())
        self.assertEqual(len(provider.calls), 1)
        return store.get_mission_draft_attempt(workspace_id, attempt.attempt_id)

    def test_real_store_draft_confirm_replay_restart_preserves_two_step_flow(self):
        with self.store_case() as (store, workspace_id, attempt):
            ready = self.generate(store, workspace_id, attempt)
            self.assertEqual((ready.status, ready.candidate_version), ("ready", 1))
            revision, _ = store.import_source_revision(workspace_id, "input.csv", "text/csv", b"id\n01\n")
            identity = SourceIdentity.model_validate(
                revision.model_dump(include=set(SourceIdentity.model_fields))
            )
            args = (workspace_id, attempt.attempt_id, 1, ready.candidate_sha256, [identity])
            mission = store.confirm_mission_draft_attempt(*args)
            self.assertEqual(mission.status, "active")
            self.assertEqual(store.confirm_mission_draft_attempt(*args), mission)
            import contextox.store as store_module
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("DROP INDEX runs_one_active_per_mission")
                connection.execute("DROP TABLE definition_drafts")
                connection.execute("DROP TABLE runs")
                connection.execute(store_module._EXPECTED_V2_RUNS_SQL)
                connection.execute(store_module._EXPECTED_V2_DEFINITION_DRAFTS_SQL)
                for _, _, sql in store_module._EXPECTED_V2_INDEXES:
                    connection.execute(sql)
                connection.execute("PRAGMA user_version=2")
                self.assertTrue(store_module._schema_is_exact_v2(connection))
            restarted = WorkspaceStore.open(store.data_dir)
            self.assertEqual(restarted.list_missions(workspace_id), [mission])
            self.assertEqual(restarted.confirm_mission_draft_attempt(*args), mission)
            self.assertEqual(restarted.get_mission_draft_attempt(workspace_id, attempt.attempt_id).status, "confirmed")
            with closing(sqlite3.connect(store.db_path)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM runs").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM provider_receipts").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT role, content FROM mission_messages").fetchall(),
                                 [("user", attempt.original_input)])
            for version, digest, refs in ((2, ready.candidate_sha256, [identity]),
                                          (1, "0" * 64, [identity]),
                                          (1, ready.candidate_sha256, [])):
                with self.assertRaises(Path2StateError):
                    restarted.confirm_mission_draft_attempt(workspace_id, attempt.attempt_id, version, digest, refs)

    def test_failure_and_cancellation_are_persisted_without_mission(self):
        for content, usage, expected in (("invalid-json", True, "failed"), (None, False, "blocked")):
            with self.subTest(status=expected), self.store_case() as (store, workspace_id, attempt):
                result = self.generate(store, workspace_id, attempt, content=content, usage=usage)
                self.assertEqual(result.status, expected)
                self.assertIsNone(result.candidate)
                self.assertEqual(WorkspaceStore.open(store.data_dir).get_mission_draft_attempt(
                    workspace_id, attempt.attempt_id), result)
                with self.assertRaises(Path2StateError):
                    store.confirm_mission_draft_attempt(workspace_id, attempt.attempt_id, 1, "0" * 64, [])
                self.assertEqual(store.list_missions(workspace_id), [])
        with self.store_case() as (store, workspace_id, attempt):
            cancelled = Event()
            cancelled.set()
            provider = FakeProvider([])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.generate_mission_draft(store, workspace_id, attempt.attempt_id, cancelled)
            self.assertEqual(provider.calls, [])
            self.assertEqual(store.get_mission_draft_attempt(workspace_id, attempt.attempt_id).status, "cancelled")

    def test_claim_is_exclusive_and_confirmation_checks_source_permissions_and_bytes(self):
        with self.store_case() as (store, workspace_id, attempt):
            store.mark_mission_draft_running(workspace_id, attempt.attempt_id)
            with self.assertRaises(Path2StateError):
                store.mark_mission_draft_running(workspace_id, attempt.attempt_id)
            other = store.create_workspace("Other").workspace_id
            with self.assertRaises(MissionDraftAttemptNotFoundError):
                store.get_mission_draft_attempt(other, attempt.attempt_id)
        for mutation in ("workspace", "hash", "denied", "bytes", "duplicate"):
            with self.subTest(mutation=mutation), self.store_case() as (store, workspace_id, attempt):
                ready = self.generate(store, workspace_id, attempt)
                revision, _ = store.import_source_revision(workspace_id, "source.txt", "text/plain", b"hello")
                ref = SourceIdentity.model_validate(revision.model_dump(include=set(SourceIdentity.model_fields)))
                if mutation == "workspace":
                    ref = ref.model_copy(update={"workspace_id": str(uuid4())})
                elif mutation == "hash":
                    ref = ref.model_copy(update={"sha256": "0" * 64})
                elif mutation == "denied":
                    with closing(sqlite3.connect(store.db_path)) as connection, connection:
                        connection.execute("UPDATE source_revisions SET permission_status='denied'")
                elif mutation == "bytes":
                    path = store.data_dir / "sources" / workspace_id / revision.source_id / f"{revision.revision_id}.bin"
                    path.write_bytes(b"other")
                refs = [ref, ref] if mutation == "duplicate" else [ref]
                with self.assertRaises(WorkspaceStoreError):
                    store.confirm_mission_draft_attempt(
                        workspace_id, attempt.attempt_id, 1, ready.candidate_sha256, refs)
                self.assertEqual(store.list_missions(workspace_id), [])
                self.assertEqual(store.get_mission_draft_attempt(workspace_id, attempt.attempt_id).status, "ready")

    def test_confirm_rolls_back_all_rows_when_readback_fails(self):
        import contextox.store as store_module
        with self.store_case() as (store, workspace_id, attempt):
            ready = self.generate(store, workspace_id, attempt)
            with patch.object(store_module, "_load_mission", side_effect=WorkspaceStoreUnavailableError()):
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    store.confirm_mission_draft_attempt(workspace_id, attempt.attempt_id, 1, ready.candidate_sha256, [])
            self.assertEqual(store.get_mission_draft_attempt(workspace_id, attempt.attempt_id).status, "ready")
            with closing(sqlite3.connect(store.db_path)) as connection:
                for table in ("missions", "mission_sources", "mission_messages", "runs"):
                    self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_ready_readback_rejects_tampered_receipt_and_state_metadata(self):
        mutations = (
            ("UPDATE provider_receipts SET p0_sha256=?", ("0" * 64,)),
            ("UPDATE mission_draft_attempts SET status='running'", ()),
            ("UPDATE mission_draft_attempts SET candidate_json=?", ('{"title":"wrong"}',)),
            ("UPDATE mission_draft_attempts SET error_code='unexpected'", ()),
        )
        for sql, values in mutations:
            with self.subTest(sql=sql), self.store_case() as (store, workspace_id, attempt):
                self.generate(store, workspace_id, attempt)
                with closing(sqlite3.connect(store.db_path)) as connection, connection:
                    connection.execute(sql, values)
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    store.get_mission_draft_attempt(workspace_id, attempt.attempt_id)

    def test_concurrent_claim_has_one_owner_and_never_reopens_a_terminal_attempt(self):
        with self.store_case() as (store, workspace_id, attempt):
            def claim():
                try:
                    return store.mark_mission_draft_running(workspace_id, attempt.attempt_id).status
                except Path2StateError as error:
                    return error.code
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: claim(), range(2)))
            self.assertCountEqual(results, ["running", "state_conflict"])
            for status in ("blocked", "failed", "cancelled"):
                current = store.create_mission_draft_attempt(workspace_id, "Synthetic input")
                terminal = store.fail_mission_draft_attempt(
                    workspace_id, current.attempt_id, status, "interrupted_without_receipt", None)
                self.assertEqual(store.fail_mission_draft_attempt(
                    workspace_id, current.attempt_id, status, "interrupted_without_receipt", None), terminal)
                with self.assertRaises(Path2StateError):
                    store.mark_mission_draft_running(workspace_id, current.attempt_id)
                with self.assertRaises(Path2StateError):
                    store.fail_mission_draft_attempt(
                        workspace_id, current.attempt_id, status, "different_error", None)

    def test_receipt_and_ready_result_commit_atomically_and_replay_exactly(self):
        import contextox.store as store_module
        with self.store_case() as (store, workspace_id, attempt):
            store.mark_mission_draft_running(workspace_id, attempt.attempt_id)
            candidate = agent.MissionDraftPayload(
                title="Synthetic draft", goal="Define synthetic fields",
                completion_criteria=["Return a candidate"], scope_notes=[])
            receipt = agent._make_receipt(
                provider=FakeProvider([]), workspace_id=workspace_id,
                attempt_id=attempt.attempt_id, mission_id=None, run_id=None,
                turn_index=1, status="succeeded", p0_sha256=agent.P0_DRAFT_SHA256,
                usage=_usage())
            original = store.get_mission_draft_attempt(workspace_id, attempt.attempt_id)
            with patch.object(store_module, "_load_attempt", side_effect=[
                original, WorkspaceStoreUnavailableError()
            ]):
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    store.save_mission_draft_result(workspace_id, attempt.attempt_id, candidate, receipt)
            self.assertEqual(store.get_mission_draft_attempt(workspace_id, attempt.attempt_id), original)
            with closing(sqlite3.connect(store.db_path)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM provider_receipts").fetchone()[0], 0)
            ready = store.save_mission_draft_result(workspace_id, attempt.attempt_id, candidate, receipt)
            self.assertEqual(store.save_mission_draft_result(
                workspace_id, attempt.attempt_id, candidate, receipt), ready)
            for invalid in (
                receipt.model_copy(update={"receipt_id": str(uuid4())}),
                receipt.model_copy(update={"output_tokens": 999}),
                receipt.model_copy(update={"p0_sha256": "0" * 64}),
                receipt.model_copy(update={"workspace_id": str(uuid4())}),
            ):
                with self.assertRaises(Path2StateError):
                    store.save_mission_draft_result(workspace_id, attempt.attempt_id, candidate, invalid)

    def test_unknown_commit_is_reconciled_from_store_without_another_provider_send(self):
        import contextox.store as store_module

        class CommitOutcomeUnknown(sqlite3.Connection):
            def commit(self):
                super().commit()
                raise sqlite3.OperationalError("synthetic commit acknowledgement lost")

        with self.store_case() as (store, workspace_id, attempt):
            store.mark_mission_draft_running(workspace_id, attempt.attempt_id)
            candidate = agent.MissionDraftPayload(
                title="Synthetic draft", goal="Define fields",
                completion_criteria=["Return a candidate"], scope_notes=[])
            receipt = agent._make_receipt(
                provider=FakeProvider([]), workspace_id=workspace_id,
                attempt_id=attempt.attempt_id, mission_id=None, run_id=None,
                turn_index=1, status="succeeded", p0_sha256=agent.P0_DRAFT_SHA256,
                usage=_usage())

            def connect(path, *, mode="rw"):
                return sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True,
                                       factory=CommitOutcomeUnknown)

            with patch.object(store_module, "_connect_existing_database", side_effect=connect):
                with self.assertRaises(Path2StateError) as raised:
                    store.save_mission_draft_result(workspace_id, attempt.attempt_id, candidate, receipt)
            self.assertEqual(raised.exception.code, "state_write_outcome_unknown")
            restarted = WorkspaceStore.open(store.data_dir)
            reconciled = restarted.get_mission_draft_attempt(workspace_id, attempt.attempt_id)
            self.assertEqual(reconciled.status, "ready")
            self.assertEqual(reconciled.provider_receipt_id, receipt.receipt_id)
            provider = FakeProvider([])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.generate_mission_draft(restarted, workspace_id, attempt.attempt_id, Event())
            self.assertEqual(provider.calls, [])

    def test_inflight_cancel_and_private_reasoning_do_not_become_mission_content(self):
        with self.store_case() as (store, workspace_id, attempt):
            event = Event()
            provider = FakeProvider([], cancel_event=event)
            with patch.object(agent, "get_provider", return_value=provider):
                agent.generate_mission_draft(store, workspace_id, attempt.attempt_id, event)
            result = store.get_mission_draft_attempt(workspace_id, attempt.attempt_id)
            self.assertEqual((len(provider.calls), result.status), (1, "cancelled"))
            self.assertEqual(store.list_missions(workspace_id), [])
        with self.store_case() as (store, workspace_id, attempt):
            provider = FakeProvider([ProviderCompletion(
                completion_id="synthetic-provider-only-id",
                content='{"title":"Draft","goal":"Define fields","completion_criteria":["Candidate"],"scope_notes":[]}',
                reasoning_content="synthetic-private-reasoning-marker",
                tool_calls=[], finish_reason="stop", usage=_usage())])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.generate_mission_draft(store, workspace_id, attempt.attempt_id, Event())
            ready = store.get_mission_draft_attempt(workspace_id, attempt.attempt_id)
            store.confirm_mission_draft_attempt(
                workspace_id, attempt.attempt_id, 1, ready.candidate_sha256, [])
            with closing(sqlite3.connect(store.db_path)) as connection:
                dump = "\n".join(connection.iterdump())
                self.assertNotIn("synthetic-private-reasoning-marker", dump)
                self.assertNotIn("synthetic-provider-only-id", dump)
                self.assertEqual(connection.execute("SELECT content FROM mission_messages").fetchall(),
                                 [(attempt.original_input,)])

    def test_confirm_preserves_order_and_rejects_a_mismatched_parent_on_readback(self):
        with self.store_case() as (store, workspace_id, attempt):
            ready = self.generate(store, workspace_id, attempt)
            refs = []
            for name in ("left.csv", "right.csv"):
                revision, _ = store.import_source_revision(workspace_id, name, "text/csv", b"id\n1\n")
                refs.append(SourceIdentity.model_validate(
                    revision.model_dump(include=set(SourceIdentity.model_fields))))
            mission = store.confirm_mission_draft_attempt(
                workspace_id, attempt.attempt_id, 1, ready.candidate_sha256, refs)
            with self.assertRaises(Path2StateError):
                store.confirm_mission_draft_attempt(
                    workspace_id, attempt.attempt_id, 1, ready.candidate_sha256, list(reversed(refs)))
            other = store.create_mission_draft_attempt(workspace_id, "Unrelated synthetic attempt")
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute("UPDATE missions SET original_attempt_id=? WHERE mission_id=?",
                                   (other.attempt_id, mission.mission_id))
            with self.assertRaises(WorkspaceStoreUnavailableError):
                store.list_missions(workspace_id)
            with self.assertRaises(WorkspaceStoreUnavailableError):
                store.get_mission_draft_attempt(workspace_id, attempt.attempt_id)


def _persisted_mission(store: WorkspaceStore, *, with_sources: bool = False):
    workspace_id = store.create_workspace("Synthetic Path 2 lifecycle").workspace_id
    attempt = store.create_mission_draft_attempt(workspace_id, "Define the authorized data.")
    store.mark_mission_draft_running(workspace_id, attempt.attempt_id)
    candidate = agent.MissionDraftPayload(
        title="Authorized definition", goal="Build a candidate definition",
        completion_criteria=["Return a reviewable candidate"], scope_notes=[],
    )
    receipt = agent._make_receipt(
        provider=FakeProvider([]), workspace_id=workspace_id,
        attempt_id=attempt.attempt_id, mission_id=None, run_id=None,
        turn_index=1, status="succeeded", p0_sha256=agent.P0_DRAFT_SHA256,
        usage=_usage(),
    )
    ready = store.save_mission_draft_result(
        workspace_id, attempt.attempt_id, candidate, receipt
    )
    refs = []
    if with_sources:
        for name, content in (
            ("left.csv", b"id,value\n1,a\n2,b\n"),
            ("right.csv", b"id,label\n1,x\n3,y\n"),
        ):
            revision, _ = store.import_source_revision(
                workspace_id, name, "text/csv", content
            )
            refs.append(_source_identity_for_test(revision))
    mission = store.confirm_mission_draft_attempt(
        workspace_id, attempt.attempt_id, 1, ready.candidate_sha256, refs
    )
    return workspace_id, mission, refs


def _source_identity_for_test(revision):
    return SourceIdentity.model_validate(
        revision.model_dump(include=set(SourceIdentity.model_fields))
    )


def _start_request(mission: Mission, refs: list[SourceIdentity], client: int = 900):
    return RunStartRequest(
        expected_state_version=mission.state_version,
        source_refs=refs,
        provider_send_confirmed=True,
        client_request_id=_id(client),
    )


class PersistedRunTests(unittest.TestCase):
    @contextmanager
    def store_case(self, *, with_sources: bool = False):
        with tempfile.TemporaryDirectory(prefix="contextox-run-", dir="/private/tmp") as directory:
            store = WorkspaceStore.open(directory)
            workspace_id, mission, refs = _persisted_mission(
                store, with_sources=with_sources
            )
            yield store, workspace_id, mission, refs

    def test_start_is_exactly_idempotent_and_rejects_conflicts_before_creation(self):
        with self.store_case(with_sources=True) as (store, workspace_id, mission, refs):
            request = _start_request(mission, refs)
            run = store.start_run(workspace_id, mission.mission_id, request)
            self.assertEqual(run.status, "queued")
            self.assertEqual(store.start_run(workspace_id, mission.mission_id, request), run)
            with self.assertRaises(Path2StateError):
                store.start_run(
                    workspace_id, mission.mission_id,
                    request.model_copy(update={"source_refs": list(reversed(refs))}),
                )
            with self.assertRaises(Path2StateError):
                store.start_run(
                    workspace_id, mission.mission_id,
                    request.model_copy(update={"client_request_id": _id(901)}),
                )
            other = store.create_workspace("Other Workspace").workspace_id
            with self.assertRaises(WorkspaceStoreError):
                store.start_run(
                    other, mission.mission_id,
                    request.model_copy(update={
                        "client_request_id": _id(902),
                        "source_refs": [ref.model_copy(update={"workspace_id": other}) for ref in refs],
                    }),
                )
            with closing(sqlite3.connect(store.db_path)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM runs").fetchone()[0], 1)

    def test_real_store_fake_provider_persists_partial_run_without_delta_or_reasoning(self):
        with self.store_case() as (store, workspace_id, mission, refs):
            run = store.start_run(
                workspace_id, mission.mission_id, _start_request(mission, refs)
            )
            update = ProviderToolCall(
                "call-update", "update_definition_draft",
                '{"expected_version":0,"expected_sha256":null,"fields":[],"relationships":[],"unresolved_items":["Need a business owner"]}',
            )
            finish = ProviderToolCall(
                "call-finish", "finish_run",
                '{"outcome":"partial","reason":"Business meaning is unresolved.","source_refs":[]}',
            )
            provider = FakeProvider([
                _completion("Candidate work.", (update,)),
                _completion("Public partial summary.", (finish,)),
            ])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, workspace_id, mission.mission_id, run.run_id, Event())

            snapshot = store.get_run_snapshot(workspace_id, mission.mission_id, run.run_id)
            self.assertEqual(snapshot.status, "partial")
            self.assertEqual(snapshot.final_output, "Candidate work.Public partial summary.")
            self.assertEqual(
                store.save_run_final_output(
                    workspace_id, mission.mission_id, run.run_id, snapshot.final_output
                ),
                snapshot,
            )
            with self.assertRaises(Path2StateError):
                store.save_run_final_output(
                    workspace_id, mission.mission_id, run.run_id, "Different summary"
                )
            self.assertEqual(snapshot.draft.version, 1)
            self.assertEqual(snapshot.terminal_receipt.terminal_tool, "finish_run")
            self.assertEqual(len(snapshot.terminal_receipt.provider_receipt_ids), 2)
            self.assertEqual(len(snapshot.terminal_receipt.tool_receipt_ids), 2)
            events = store.list_run_events(workspace_id, mission.mission_id, run.run_id)
            self.assertNotIn("model_delta", [event.event_type for event in events])
            self.assertTrue(any(event.event_type == "run_partial" for event in events))
            self.assertGreater(snapshot.last_sequence, len(events))
            with closing(sqlite3.connect(store.db_path)) as connection:
                dump = "\n".join(connection.iterdump())
                self.assertNotIn("hidden reasoning", dump)
                self.assertNotIn("completion-1", dump)
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM mission_messages WHERE role='assistant'"
                    ).fetchone()[0], 1,
                )
            restarted = WorkspaceStore.open(store.data_dir)
            self.assertEqual(
                restarted.get_run_snapshot(workspace_id, mission.mission_id, run.run_id),
                snapshot,
            )
            self.assertEqual(
                restarted.get_mission_snapshot(workspace_id, mission.mission_id).mission.status,
                "blocked",
            )
            with closing(sqlite3.connect(store.db_path)) as connection, connection:
                connection.execute(
                    """
                    UPDATE provider_receipts SET p0_sha256=?
                    WHERE workspace_id=? AND mission_id=? AND run_id=? AND turn_index=1
                    """,
                    ("0" * 64, workspace_id, mission.mission_id, run.run_id),
                )
            with self.assertRaises(WorkspaceStoreUnavailableError):
                restarted.get_run_snapshot(workspace_id, mission.mission_id, run.run_id)

    def test_all_seven_tools_use_selected_sources_and_terminal_receipts(self):
        with self.store_case(with_sources=True) as (store, workspace_id, mission, refs):
            run = store.start_run(
                workspace_id, mission.mission_id, _start_request(mission, refs)
            )
            store.mark_run_running(workspace_id, mission.mission_id, run.run_id)
            artifacts = [store.get_source_artifact(workspace_id, ref.revision_id) for ref in refs]
            calls = [
                ListSourcesCall(call_id="list", name="list_sources", arguments={}),
                ReadSourceCall(call_id="read", name="read_source", arguments={
                    "revision_id": refs[0].revision_id,
                    "locator": {"kind": "csv_rows", "row_start": 1, "row_end": 1,
                                "column": "id"},
                }),
                InspectDatasetCall(call_id="table", name="inspect_dataset", arguments={
                    "kind": "table", "revision_id": refs[0].revision_id,
                    "table_id": artifacts[0].tables[0].table_id,
                }),
                InspectDatasetCall(call_id="relationship", name="inspect_dataset", arguments={
                    "kind": "relationship",
                    "left": {"source_ref": refs[0], "table_id": artifacts[0].tables[0].table_id,
                             "columns": ["id"]},
                    "right": {"source_ref": refs[1], "table_id": artifacts[1].tables[0].table_id,
                              "columns": ["id"]},
                }),
                UpdateDefinitionDraftCall(
                    call_id="update", name="update_definition_draft", arguments={
                        "expected_version": 0, "expected_sha256": None,
                        "fields": [], "relationships": [], "unresolved_items": ["Confirm grain"],
                    },
                ),
            ]
            store.validate_run_tool_batch(workspace_id, mission.mission_id, run.run_id, calls)
            results = [
                store.execute_run_tool(workspace_id, mission.mission_id, run.run_id, call)
                for call in calls
            ]
            self.assertTrue(all(result.status == "succeeded" for result in results))
            draft = results[-1].output
            for call_id, unresolved in (("update-b", ["Different candidate"]),
                                        ("update-a", ["Confirm grain"])):
                update = UpdateDefinitionDraftCall(
                    call_id=call_id, name="update_definition_draft", arguments={
                        "expected_version": draft.version,
                        "expected_sha256": draft.sha256,
                        "fields": [], "relationships": [],
                        "unresolved_items": unresolved,
                    },
                )
                result = store.execute_run_tool(
                    workspace_id, mission.mission_id, run.run_id, update
                )
                results.append(result)
                draft = result.output
            self.assertEqual(
                (results[-3].output.version, results[-2].output.version,
                 results[-1].output.version),
                (1, 2, 3),
            )
            self.assertEqual(results[-3].output.sha256, results[-1].output.sha256)
            clarification_call = CreateClarificationCall(
                call_id="clarify", name="create_clarification", arguments={
                    "draft_version": draft.version, "draft_sha256": draft.sha256,
                    "questions": [{
                        "question": "What grain should this use?",
                        "why_needed": "The sources do not define business grain.",
                        "expected_answer_type": "text", "suggested_owner_role": None,
                        "related_definition_paths": [], "evidence_requested": [],
                        "examples_or_options": [], "blocking_impact": "blocking",
                        "source_refs": [],
                    }],
                },
            )
            store.validate_run_tool_batch(
                workspace_id, mission.mission_id, run.run_id, [clarification_call]
            )
            clarification = store.execute_run_tool(
                workspace_id, mission.mission_id, run.run_id, clarification_call
            )
            self.assertIsInstance(clarification.output, ClarificationRequest)
            self.assertEqual(clarification.terminal_snapshot.status, "waiting_for_human")
            self.assertEqual(
                clarification.terminal_snapshot.terminal_receipt.tool_receipt_ids,
                [result.tool_receipt.receipt_id for result in results]
                + [clarification.tool_receipt.receipt_id],
            )

        with self.store_case() as (store, workspace_id, mission, refs):
            run = store.start_run(
                workspace_id, mission.mission_id, _start_request(mission, refs, 903)
            )
            store.mark_run_running(workspace_id, mission.mission_id, run.run_id)
            update = UpdateDefinitionDraftCall(
                call_id="update", name="update_definition_draft", arguments={
                    "expected_version": 0, "expected_sha256": None,
                    "fields": [], "relationships": [], "unresolved_items": [],
                },
            )
            draft = store.execute_run_tool(
                workspace_id, mission.mission_id, run.run_id, update
            ).output
            submit = SubmitForReviewCall(
                call_id="submit", name="submit_for_review",
                arguments={"draft_version": draft.version, "draft_sha256": draft.sha256},
            )
            result = store.execute_run_tool(
                workspace_id, mission.mission_id, run.run_id, submit
            )
            self.assertEqual((result.output.status, result.terminal_snapshot.status),
                             ("in_review", "waiting_for_human"))

    def test_cancel_and_restart_recovery_are_terminal_and_do_not_call_provider(self):
        with self.store_case() as (store, workspace_id, mission, refs):
            run = store.start_run(
                workspace_id, mission.mission_id, _start_request(mission, refs, 904)
            )
            store.mark_run_running(workspace_id, mission.mission_id, run.run_id)
            with self.assertRaises(Path2StateError):
                store.append_run_event(
                    workspace_id, mission.mission_id, run.run_id,
                    RunCompletedEventInput(
                        event_type="run_completed",
                        public_payload=RunCompletedPayload(
                            status="completed", terminal_receipt_id=None, error_code=None
                        ),
                    ),
                )
            cancelled = store.cancel_run(workspace_id, mission.mission_id, run.run_id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(store.cancel_run(workspace_id, mission.mission_id, run.run_id), cancelled)
            self.assertEqual(store.list_run_events(
                workspace_id, mission.mission_id, run.run_id
            )[-1].event_type, "run_cancelled")

        with self.store_case() as (store, workspace_id, mission, refs):
            run = store.start_run(
                workspace_id, mission.mission_id, _start_request(mission, refs, 905)
            )
            provider = FakeProvider([])
            with patch.object(agent, "get_provider", return_value=provider):
                restarted = WorkspaceStore.open(store.data_dir)
            recovered = restarted.get_run_snapshot(
                workspace_id, mission.mission_id, run.run_id
            )
            self.assertEqual((recovered.status, recovered.error_code),
                             ("failed", "interrupted_without_receipt"))
            self.assertEqual(provider.calls, [])
            self.assertEqual(restarted.get_mission_snapshot(
                workspace_id, mission.mission_id
            ).mission.status, "blocked")
            self.assertEqual(restarted.list_run_events(
                workspace_id, mission.mission_id, run.run_id
            )[-1].event_type, "run_failed")

        with self.store_case() as (store, workspace_id, mission, refs):
            run = store.start_run(
                workspace_id, mission.mission_id, _start_request(mission, refs, 906)
            )
            event = Event()
            provider = FakeProvider([], cancel_event=event)
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, workspace_id, mission.mission_id, run.run_id, event)
            cancelled = store.get_run_snapshot(
                workspace_id, mission.mission_id, run.run_id
            )
            self.assertEqual(cancelled.status, "cancelled")
            with closing(sqlite3.connect(store.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM provider_receipts WHERE run_id=?",
                        (run.run_id,),
                    ).fetchall(),
                    [("cancelled",)],
                )

    def test_batch_validation_and_receipt_failure_leave_no_partial_draft(self):
        import contextox.store as store_module

        with self.store_case() as (store, workspace_id, mission, refs):
            run = store.start_run(
                workspace_id, mission.mission_id, _start_request(mission, refs, 907)
            )
            store.mark_run_running(workspace_id, mission.mission_id, run.run_id)
            update = UpdateDefinitionDraftCall(
                call_id="update", name="update_definition_draft", arguments={
                    "expected_version": 0, "expected_sha256": None,
                    "fields": [], "relationships": [], "unresolved_items": [],
                },
            )
            invalid_read = ReadSourceCall(
                call_id="read", name="read_source", arguments={
                    "revision_id": _id(999),
                    "locator": {"kind": "csv_rows", "row_start": 1,
                                "row_end": 1, "column": None},
                },
            )
            with self.assertRaises(Path2StateError):
                store.validate_run_tool_batch(
                    workspace_id, mission.mission_id, run.run_id,
                    [update, invalid_read],
                )
            self.assertIsNone(store.get_run_snapshot(
                workspace_id, mission.mission_id, run.run_id
            ).draft)

            with patch.object(
                store_module, "_insert_tool_receipt",
                side_effect=sqlite3.OperationalError("synthetic receipt failure"),
            ):
                with self.assertRaises(WorkspaceStoreUnavailableError):
                    store.execute_run_tool(
                        workspace_id, mission.mission_id, run.run_id, update
                    )
            snapshot = store.get_run_snapshot(
                workspace_id, mission.mission_id, run.run_id
            )
            self.assertIsNone(snapshot.draft)
            with closing(sqlite3.connect(store.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM tool_receipts WHERE run_id=?",
                        (run.run_id,),
                    ).fetchone()[0],
                    0,
                )


class FakeStore:
    def __init__(
        self,
        *,
        context: ContextSnapshot | None = None,
        attempt: MissionDraftAttempt | None = None,
        validation_error: WorkspaceStoreError | None = None,
    ) -> None:
        self.context = context
        self.attempt = attempt
        self.validation_error = validation_error
        self.context_calls = 0
        self.mark_calls = 0
        self.manifests = []
        self.receipts: list[ProviderReceipt] = []
        self.events = []
        self.validated_batches = []
        self.executed_calls = []
        self.failures = []
        self.cancel_calls = 0
        self.saved_outputs = []
        self.saved_attempts = []
        self.tool_results: dict[str, RunToolResult] = {}

    def get_mission_draft_attempt(self, workspace_id: str, attempt_id: str):
        return self.attempt

    def mark_mission_draft_running(self, workspace_id, attempt_id):
        self.attempt = self.attempt.model_copy(update={"status": "running"})
        return self.attempt

    def save_mission_draft_result(self, workspace_id, attempt_id, candidate, receipt):
        self.saved_attempts.append((workspace_id, attempt_id, candidate, receipt))
        return self.attempt

    def fail_mission_draft_attempt(self, workspace_id, attempt_id, status, code, receipt):
        self.failures.append(("attempt", status, code, receipt))
        return self.attempt

    def get_context_snapshot(self, workspace_id: str, mission_id: str, run_id: str):
        self.context_calls += 1
        return self.context

    def mark_run_running(self, workspace_id: str, mission_id: str, run_id: str):
        self.mark_calls += 1
        run_data = self.context.run.model_dump(mode="python")
        run_data.update({"status": "running"})
        running = RunSnapshot(**run_data)
        self.context = self.context.model_copy(update={"run": running})
        return running

    def record_context_manifest(self, workspace_id, mission_id, run_id, manifest):
        self.manifests.append(manifest)
        payload = manifest.model_dump(mode="json")
        payload.update(
            {
                "workspace_id": workspace_id,
                "mission_id": mission_id,
                "run_id": run_id,
                "manifest_id": _id(30 + len(self.manifests)),
            }
        )
        payload["sha256"] = canonical_sha256(payload)
        return ContextPacketManifest.model_validate(payload)

    def record_provider_receipt(self, workspace_id, mission_id, run_id, receipt):
        self.receipts.append(receipt)
        return receipt

    def append_run_event(self, workspace_id, mission_id, run_id, event):
        self.events.append(event)
        return event

    def validate_run_tool_batch(self, workspace_id, mission_id, run_id, calls):
        self.validated_batches.append(list(calls))
        if self.validation_error is not None:
            raise self.validation_error

    def execute_run_tool(self, workspace_id, mission_id, run_id, call):
        self.executed_calls.append(call)
        return self.tool_results[call.call_id]

    def fail_run(self, workspace_id, mission_id, run_id, status, code):
        self.failures.append(("run", status, code))
        run_data = self.context.run.model_dump(mode="python")
        run_data.update({"status": status, "error_code": code})
        stopped = RunSnapshot(**run_data)
        self.context = self.context.model_copy(update={"run": stopped})
        return stopped

    def cancel_run(self, workspace_id, mission_id, run_id):
        self.cancel_calls += 1
        run_data = self.context.run.model_dump(mode="python")
        run_data.update({"status": "cancelled", "error_code": "cancelled"})
        stopped = RunSnapshot(**run_data)
        self.context = self.context.model_copy(update={"run": stopped})
        return stopped

    def save_run_final_output(self, workspace_id, mission_id, run_id, content):
        self.saved_outputs.append(content)
        return self.context.run


def _completion(content: str, calls: tuple[ProviderToolCall, ...] = ()) -> ProviderCompletion:
    return ProviderCompletion(
        completion_id="completion-1",
        content=content,
        reasoning_content="hidden reasoning",
        tool_calls=calls,
        finish_reason="tool_calls" if calls else "stop",
        usage=_usage(),
    )


def _tool_result(call, *, terminal: bool = False, rejected: bool = False, ordinal: int = 1) -> RunToolResult:
    status = "rejected" if rejected else "succeeded"
    output = DomainRejection(code="not_ready", reason="The domain precondition is not met.") if rejected else []
    terminal_snapshot = None
    if terminal:
        terminal_receipt = TerminalReceipt(
            workspace_id=_id(1),
            mission_id=_id(3),
            run_id=_id(4),
            receipt_id=_id(80),
            created_at="2026-09-03T00:00:00+00:00",
            terminal_tool="finish_run",
            outcome="partial",
            draft_id=None,
            draft_version=None,
            draft_sha256=None,
            clarification_ids=[],
            provider_receipt_ids=[],
            tool_receipt_ids=[],
            source_refs=[],
        )
        output = terminal_receipt
        run_data = _context_snapshot().run.model_dump(mode="python")
        run_data.update({"status": "partial", "terminal_receipt": terminal_receipt})
        terminal_snapshot = RunSnapshot(**run_data)
    receipt = ToolReceipt(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        receipt_id=_id(60 + (1 if terminal else 0)),
        ordinal=ordinal,
        call_id=call.call_id,
        name=call.name,
        arguments_sha256=canonical_sha256(call.arguments),
        status=status,
        created_at="2026-09-03T00:00:00+00:00",
        source_refs=[],
        error_code="not_ready" if rejected else None,
    )
    return RunToolResult(
        call_id=call.call_id,
        status=status,
        output=output,
        tool_receipt=receipt,
        terminal_snapshot=terminal_snapshot,
    )


def _clarification_result(call) -> RunToolResult:
    clarification = ClarificationRequest(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        clarification_id=_id(81),
        draft_version=1,
        draft_sha256="0" * 64,
        status="awaiting_answer",
        questions=[
            {
                "question": "Which grain should be used?",
                "why_needed": "The authorized evidence does not settle the grain.",
                "expected_answer_type": "text",
                "suggested_owner_role": None,
                "related_definition_paths": [],
                "evidence_requested": [],
                "examples_or_options": [],
                "blocking_impact": "blocking",
                "source_refs": [],
            }
        ],
    )
    terminal_receipt = TerminalReceipt(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        receipt_id=_id(82),
        created_at="2026-09-03T00:00:00+00:00",
        terminal_tool="create_clarification",
        outcome="waiting_for_human",
        draft_id=None,
        draft_version=None,
        draft_sha256=None,
        clarification_ids=[clarification.clarification_id],
        provider_receipt_ids=[],
        tool_receipt_ids=[],
        source_refs=[],
    )
    run_data = _context_snapshot().run.model_dump(mode="python")
    run_data.update({"status": "waiting_for_human", "terminal_receipt": terminal_receipt})
    receipt = ToolReceipt(
        workspace_id=_id(1),
        mission_id=_id(3),
        run_id=_id(4),
        receipt_id=_id(83),
        ordinal=1,
        call_id=call.call_id,
        name=call.name,
        arguments_sha256=canonical_sha256(call.arguments),
        status="succeeded",
        created_at="2026-09-03T00:00:00+00:00",
        source_refs=[],
        error_code=None,
    )
    return RunToolResult(
        call_id=call.call_id,
        status="succeeded",
        output=clarification,
        tool_receipt=receipt,
        terminal_snapshot=RunSnapshot(**run_data),
    )


class AgentTests(unittest.TestCase):
    def test_draft_sends_only_p0_and_original_input_and_saves_candidate(self) -> None:
        store = FakeStore(attempt=_attempt())
        provider = FakeProvider(
            [
                _completion(
                    '{"title":"Order mapping","goal":"Map fields","completion_criteria":["Draft"],"scope_notes":[]}'
                )
            ]
        )
        with patch.object(agent, "get_provider", return_value=provider):
            agent.generate_mission_draft(store, _id(1), _id(2), Event())

        self.assertEqual(len(provider.calls), 1)
        call = provider.calls[0]
        self.assertFalse(call["kwargs"]["stream"])
        self.assertIsNone(call["kwargs"]["tools"])
        self.assertEqual([message["role"] for message in call["messages"]], ["system", "user"])
        self.assertEqual(call["messages"][1]["content"], store.attempt.original_input)
        self.assertEqual(len(store.saved_attempts), 1)
        self.assertEqual(store.saved_attempts[0][2].title, "Order mapping")
        self.assertEqual(store.saved_attempts[0][3].status, "succeeded")
        self.assertIsNone(store.saved_attempts[0][3].mission_id)

    def test_draft_missing_usage_blocks_without_candidate_and_invalid_json_fails(self) -> None:
        for completion, expected_status, expected_code in (
            (_completion("{}"), "blocked", "provider_usage_unknown"),
            (
                ProviderCompletion(
                    completion_id="completion-2",
                    content="not-json",
                    reasoning_content="hidden",
                    tool_calls=(),
                    finish_reason="stop",
                    usage=_usage(),
                ),
                "failed",
                "provider_protocol_error",
            ),
        ):
            store = FakeStore(attempt=_attempt())
            if expected_status == "blocked":
                completion = ProviderCompletion(
                    completion_id="completion-3",
                    content="{}",
                    reasoning_content="hidden",
                    tool_calls=(),
                    finish_reason="stop",
                    usage=None,
                )
            provider = FakeProvider([completion])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.generate_mission_draft(store, _id(1), _id(2), Event())
            self.assertEqual(len(store.saved_attempts), 0)
            self.assertEqual(store.failures[0][1:3], (expected_status, expected_code))

    def test_run_serializes_tools_and_stops_on_partial_terminal_without_reasoning_leak(self) -> None:
        list_call = ProviderToolCall("call-list", "list_sources", "{}")
        finish_call = ProviderToolCall(
            "call-finish",
            "finish_run",
            '{"outcome":"partial","reason":"Evidence is incomplete.","source_refs":[]}',
        )
        provider = FakeProvider([_completion("Inspecting.", (list_call,)), _completion("Partial.", (finish_call,))])
        store = FakeStore(context=_context_snapshot())
        list_domain_call = None
        finish_domain_call = None
        # The fake result keys are the normalized provider call ids.
        with patch.object(agent, "get_provider", return_value=provider):
            # Build result objects through the same public model seam that the Store returns.
            from contextox.models import ListSourcesCall, FinishRunCall

            list_domain_call = ListSourcesCall(call_id="call-list", name="list_sources", arguments={})
            finish_domain_call = FinishRunCall(
                call_id="call-finish",
                name="finish_run",
                arguments={
                    "outcome": "partial",
                    "reason": "Evidence is incomplete.",
                    "source_refs": [],
                },
            )
            store.tool_results = {
                "call-list": _tool_result(list_domain_call),
                "call-finish": _tool_result(finish_domain_call, terminal=True, ordinal=2),
            }
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(provider.calls[0]["kwargs"]["tools"]), 7)
        self.assertEqual({item["function"]["name"] for item in provider.calls[0]["kwargs"]["tools"]}, agent._TOOL_NAMES)
        self.assertEqual(len(store.validated_batches), 2)
        self.assertEqual([call.call_id for call in store.executed_calls], ["call-list", "call-finish"])
        self.assertEqual(len(store.manifests), 2)
        self.assertEqual(store.manifests[1].tool_receipt_ids, [_id(60)])
        self.assertEqual(store.saved_outputs, ["Inspecting.Partial."])
        self.assertFalse(any("hidden reasoning" in repr(event) for event in store.events))
        self.assertFalse(any("hidden reasoning" in repr(receipt) for receipt in store.receipts))
        self.assertFalse(store.failures)
        self.assertTrue(any(event.event_type == "run_partial" for event in store.events))

    def test_run_shortens_provider_timeouts_to_remaining_elapsed_budget(self) -> None:
        provider = FakeProvider([_completion("No terminal.")])
        store = FakeStore(context=_context_snapshot())
        clock = ControlledClock([0.0, 299.0, 299.0, 299.0, 299.0])
        with patch.object(agent, "get_provider", return_value=provider), patch.object(
            agent.time, "monotonic", side_effect=clock.monotonic
        ):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())

        timeouts = provider.calls[0]["kwargs"]["timeouts"]
        self.assertEqual(
            (timeouts.connect_ms, timeouts.first_event_ms, timeouts.idle_ms, timeouts.total_ms),
            (1000, 1000, 1000, 1000),
        )
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_result_missing"))

    def test_run_stops_before_a_later_tool_when_elapsed_budget_is_consumed(self) -> None:
        first = ProviderToolCall("call-1", "list_sources", "{}")
        second = ProviderToolCall("call-2", "list_sources", "{}")
        from contextox.models import ListSourcesCall

        store = FakeStore(context=_context_snapshot())
        store.tool_results = {
            "call-1": _tool_result(
                ListSourcesCall(call_id="call-1", name="list_sources", arguments={}), ordinal=1
            ),
            "call-2": _tool_result(
                ListSourcesCall(call_id="call-2", name="list_sources", arguments={}), ordinal=2
            ),
        }
        original_execute = store.execute_run_tool
        clock = ControlledClock([0.0, 0.0, 0.0, 299.0, 299.0], default=299.0)

        def execute(workspace_id, mission_id, run_id, call):
            result = original_execute(workspace_id, mission_id, run_id, call)
            if call.call_id == "call-1":
                clock.default = 300.001
            return result

        store.execute_run_tool = execute
        provider = FakeProvider([_completion("", (first, second))])
        with patch.object(agent, "get_provider", return_value=provider), patch.object(
            agent.time, "monotonic", side_effect=clock.monotonic
        ):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())

        self.assertEqual([call.call_id for call in store.executed_calls], ["call-1"])
        self.assertEqual(store.failures[0][1:], ("blocked", "elapsed_budget_exceeded"))

    def test_run_enforces_fixed_eight_turn_and_twenty_four_tool_budgets(self) -> None:
        eight_store = FakeStore(context=_context_snapshot())
        eight_calls = []
        eight_completions = []
        from contextox.models import ListSourcesCall

        for index in range(1, 9):
            call_id = f"turn-call-{index}"
            raw_call = ProviderToolCall(call_id, "list_sources", "{}")
            domain_call = ListSourcesCall(call_id=call_id, name="list_sources", arguments={})
            eight_calls.append(raw_call)
            eight_completions.append(_completion("", (raw_call,)))
            eight_store.tool_results[call_id] = _tool_result(domain_call, ordinal=index)
        eight_provider = FakeProvider(eight_completions)
        with patch.object(agent, "get_provider", return_value=eight_provider):
            agent.run_agent(eight_store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(eight_provider.calls), 8)
        self.assertEqual(len(eight_store.executed_calls), 8)
        self.assertEqual(eight_store.failures[0][1:], ("blocked", "model_turn_budget_exceeded"))

        tool_store = FakeStore(context=_context_snapshot())
        tool_calls = []
        for index in range(1, 25):
            call_id = f"tool-call-{index}"
            raw_call = ProviderToolCall(call_id, "list_sources", "{}")
            tool_calls.append(raw_call)
            tool_store.tool_results[call_id] = _tool_result(
                ListSourcesCall(call_id=call_id, name="list_sources", arguments={}),
                ordinal=index,
            )
        tool_provider = FakeProvider([_completion("", tuple(tool_calls))])
        with patch.object(agent, "get_provider", return_value=tool_provider):
            agent.run_agent(tool_store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(tool_provider.calls), 1)
        self.assertEqual(len(tool_store.executed_calls), 24)
        self.assertEqual(tool_store.failures[0][1:], ("blocked", "tool_call_budget_exceeded"))

    def test_invalid_mixed_terminal_batch_is_not_executed(self) -> None:
        calls = (
            ProviderToolCall("call-list", "list_sources", "{}"),
            ProviderToolCall(
                "call-finish",
                "finish_run",
                '{"outcome":"partial","reason":"incomplete","source_refs":[]}',
            ),
        )
        provider = FakeProvider([_completion("", calls)])
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.executed_calls, [])
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_tool_mixed_batch"))

    def test_tool_result_arguments_hash_must_match_the_normalized_call(self) -> None:
        from contextox.models import ListSourcesCall

        raw_call = ProviderToolCall("call-list", "list_sources", "{}")
        domain_call = ListSourcesCall(call_id="call-list", name="list_sources", arguments={})
        result = _tool_result(domain_call)
        bad_receipt = result.tool_receipt.model_copy(update={"arguments_sha256": "0" * 64})
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {raw_call.call_id: result.model_copy(update={"tool_receipt": bad_receipt})}
        provider = FakeProvider([_completion("", (raw_call,))])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual([call.call_id for call in store.executed_calls], ["call-list"])
        self.assertEqual(store.failures[0][1:], ("failed", "tool_result_invalid"))

    def test_successful_terminal_without_snapshot_cannot_continue_the_run(self) -> None:
        from contextox.models import FinishRunCall

        raw_call = ProviderToolCall(
            "call-finish",
            "finish_run",
            '{"outcome":"partial","reason":"incomplete","source_refs":[]}',
        )
        domain_call = FinishRunCall(
            call_id="call-finish",
            name="finish_run",
            arguments={"outcome": "partial", "reason": "incomplete", "source_refs": []},
        )
        result = _tool_result(domain_call, terminal=True).model_copy(update={"terminal_snapshot": None})
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {raw_call.call_id: result}
        provider = FakeProvider([_completion("", (raw_call,))])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(store.saved_outputs, [])
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_result_invalid"))

    def test_terminal_clarification_draft_binding_must_match_request(self) -> None:
        from contextox.models import CreateClarificationCall

        arguments = {
            "draft_version": 2,
            "draft_sha256": "1" * 64,
            "questions": [
                {
                    "question": "Which grain should be used?",
                    "why_needed": "The authorized evidence does not settle the grain.",
                    "expected_answer_type": "text",
                    "suggested_owner_role": None,
                    "related_definition_paths": [],
                    "evidence_requested": [],
                    "examples_or_options": [],
                    "blocking_impact": "blocking",
                    "source_refs": [],
                }
            ],
        }
        raw_call = ProviderToolCall(
            "call-clarify",
            "create_clarification",
            json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        domain_call = CreateClarificationCall(
            call_id=raw_call.call_id,
            name=raw_call.name,
            arguments=arguments,
        )
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {raw_call.call_id: _clarification_result(domain_call)}
        provider = FakeProvider([_completion("Need a decision.", (raw_call,))])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.saved_outputs, [])
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_result_invalid"))

    def test_rejected_terminal_tool_can_continue_without_a_terminal_snapshot(self) -> None:
        from contextox.models import FinishRunCall

        rejected_raw = ProviderToolCall(
            "call-rejected-finish",
            "finish_run",
            '{"outcome":"partial","reason":"not yet","source_refs":[]}',
        )
        rejected_domain = FinishRunCall(
            call_id=rejected_raw.call_id,
            name=rejected_raw.name,
            arguments={"outcome": "partial", "reason": "not yet", "source_refs": []},
        )
        success_raw = ProviderToolCall(
            "call-finish",
            "finish_run",
            '{"outcome":"partial","reason":"now complete enough","source_refs":[]}',
        )
        success_domain = FinishRunCall(
            call_id=success_raw.call_id,
            name=success_raw.name,
            arguments={"outcome": "partial", "reason": "now complete enough", "source_refs": []},
        )
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {
            rejected_raw.call_id: _tool_result(rejected_domain, rejected=True, ordinal=1),
            success_raw.call_id: _tool_result(success_domain, terminal=True, ordinal=2),
        }
        provider = FakeProvider(
            [
                _completion("The terminal precondition is not met.", (rejected_raw,)),
                _completion("Partial.", (success_raw,)),
            ]
        )
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(store.saved_outputs, ["The terminal precondition is not met.Partial."])
        self.assertFalse(store.failures)

    def test_business_rejection_continues_to_a_single_terminal_batch(self) -> None:
        list_call = ProviderToolCall("call-list", "list_sources", "{}")
        finish_call = ProviderToolCall(
            "call-finish",
            "finish_run",
            '{"outcome":"partial","reason":"still incomplete","source_refs":[]}',
        )
        from contextox.models import FinishRunCall, ListSourcesCall

        list_domain_call = ListSourcesCall(call_id="call-list", name="list_sources", arguments={})
        finish_domain_call = FinishRunCall(
            call_id="call-finish",
            name="finish_run",
            arguments={"outcome": "partial", "reason": "still incomplete", "source_refs": []},
        )
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {
            "call-list": _tool_result(list_domain_call, rejected=True, ordinal=1),
            "call-finish": _tool_result(finish_domain_call, terminal=True, ordinal=2),
        }
        provider = FakeProvider(
            [_completion("The source precondition is not met.", (list_call,)), _completion("Partial.", (finish_call,))]
        )
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())

        self.assertEqual([call.call_id for call in store.executed_calls], ["call-list", "call-finish"])
        completed = [event for event in store.events if event.event_type == "tool_completed"]
        self.assertEqual([event.public_payload.status for event in completed], ["rejected", "succeeded"])
        self.assertEqual(store.saved_outputs, ["The source precondition is not met.Partial."])
        self.assertFalse(store.failures)

    def test_store_permission_and_old_hash_failures_are_not_downgraded(self) -> None:
        for code, expected_status in (("permission_unknown", "blocked"), ("state_conflict", "failed")):
            error = WorkspaceStoreError(code)
            error.code = code
            store = FakeStore(context=_context_snapshot(), validation_error=error)
            call = ProviderToolCall("call-list", "list_sources", "{}")
            provider = FakeProvider([_completion("", (call,))])
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, _id(1), _id(3), _id(4), Event())
            self.assertEqual(store.executed_calls, [])
            self.assertEqual(store.failures[0][1:], (expected_status, code))

    def test_clarification_terminal_uses_snapshot_receipt_and_waits_for_human(self) -> None:
        call = ProviderToolCall(
            "call-clarify",
            "create_clarification",
            '{"draft_version":1,"draft_sha256":"' + "0" * 64 + '","questions":[{"question":"Which grain should be used?","why_needed":"The authorized evidence does not settle the grain.","expected_answer_type":"text","suggested_owner_role":null,"related_definition_paths":[],"evidence_requested":[],"examples_or_options":[],"blocking_impact":"blocking","source_refs":[]}]}',
        )
        from contextox.models import CreateClarificationCall

        normalized = CreateClarificationCall(
            call_id=call.call_id,
            name=call.name,
            arguments={
                "draft_version": 1,
                "draft_sha256": "0" * 64,
                "questions": [
                    {
                        "question": "Which grain should be used?",
                        "why_needed": "The authorized evidence does not settle the grain.",
                        "expected_answer_type": "text",
                        "suggested_owner_role": None,
                        "related_definition_paths": [],
                        "evidence_requested": [],
                        "examples_or_options": [],
                        "blocking_impact": "blocking",
                        "source_refs": [],
                    }
                ],
            },
        )
        provider = FakeProvider([_completion("Need an owner decision.", (call,))])
        store = FakeStore(context=_context_snapshot())
        store.tool_results = {call.call_id: _clarification_result(normalized)}
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.saved_outputs, ["Need an owner decision."])
        self.assertFalse(store.failures)
        self.assertFalse(any(event.event_type == "run_partial" for event in store.events))

    def test_run_missing_usage_is_blocked_without_tool_execution(self) -> None:
        completion = ProviderCompletion(
            completion_id="completion-4",
            content="",
            reasoning_content="hidden reasoning",
            tool_calls=(ProviderToolCall("call-list", "list_sources", "{}"),),
            finish_reason="tool_calls",
            usage=None,
        )
        provider = FakeProvider([completion])
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.executed_calls, [])
        self.assertEqual(store.failures[0][1:], ("blocked", "provider_usage_unknown"))
        self.assertEqual(store.receipts[0].status, "blocked")

    def test_public_model_delta_total_bytes_are_bounded(self) -> None:
        provider = FakeProvider([_completion("x" * (262144 + 1))])
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.failures[0][1:], ("blocked", "context_budget_exceeded"))
        self.assertEqual(store.receipts[0].status, "blocked")
        deltas = [event for event in store.events if event.event_type == "model_delta"]
        self.assertTrue(deltas)
        self.assertTrue(all(len(event.public_payload.content.encode("utf-8")) <= 4096 for event in deltas))
        self.assertEqual(store.saved_outputs, [])

    def test_natural_stop_fails_without_persisting_final_output(self) -> None:
        provider = FakeProvider([_completion("No terminal.")])
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(store.saved_outputs, [])
        self.assertEqual(store.failures[0][1:], ("failed", "terminal_result_missing"))

    def test_cancelled_snapshot_and_external_cancel_do_not_call_provider_or_tools(self) -> None:
        cancelled_store = FakeStore(context=_context_snapshot(status="cancelled"))
        cancelled_provider = FakeProvider([])
        with patch.object(agent, "get_provider", return_value=cancelled_provider):
            agent.run_agent(cancelled_store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(cancelled_provider.calls, [])
        self.assertEqual(cancelled_store.mark_calls, 0)

        event = Event()
        event.set()
        queued_store = FakeStore(context=_context_snapshot())
        queued_provider = FakeProvider([])
        with patch.object(agent, "get_provider", return_value=queued_provider):
            agent.run_agent(queued_store, _id(1), _id(3), _id(4), event)
        self.assertEqual(queued_provider.calls, [])
        self.assertEqual(queued_store.mark_calls, 0)
        self.assertEqual(queued_store.cancel_calls, 1)

    def test_provider_cancel_during_stream_records_cancelled_receipt_and_stops(self) -> None:
        event = Event()
        provider = FakeProvider([], cancel_event=event)
        store = FakeStore(context=_context_snapshot())
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), event)
        self.assertEqual(len(store.receipts), 1)
        self.assertEqual(store.receipts[0].status, "cancelled")
        self.assertEqual(store.receipts[0].error_code, "cancelled")
        self.assertEqual(store.failures, [])
        self.assertEqual(store.cancel_calls, 1)
        self.assertEqual(store.executed_calls, [])

    def test_nonrunning_mark_readback_prevents_starting_a_provider(self) -> None:
        class WaitingMarkStore(FakeStore):
            def mark_run_running(self, workspace_id, mission_id, run_id):
                self.mark_calls += 1
                return _context_snapshot(status="waiting_for_human").run

        store = WaitingMarkStore(context=_context_snapshot())
        provider = FakeProvider([])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(provider.calls, [])
        self.assertEqual(store.events, [])

    def test_failure_event_respects_cancelled_or_terminal_store_readback(self) -> None:
        class StateRaceStore(FakeStore):
            def __init__(self, returned_status, **kwargs):
                super().__init__(**kwargs)
                self.returned_status = returned_status

            def fail_run(self, workspace_id, mission_id, run_id, status, code):
                self.failures.append(("run", status, code))
                return _context_snapshot(status=self.returned_status).run

        for returned_status in ("cancelled", "waiting_for_human", "partial"):
            store = StateRaceStore(returned_status, context=_context_snapshot())
            agent._stop_run(store, _id(1), _id(3), _id(4), "failed", "provider_protocol_error")
            self.assertFalse(any(event.event_type == "run_failed" for event in store.events))

    def test_nonrunning_context_snapshot_stops_before_the_next_provider_turn(self) -> None:
        from contextox.models import ListSourcesCall

        class WaitingAfterFirstStore(FakeStore):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.waiting = _context_snapshot(status="waiting_for_human")

            def get_context_snapshot(self, workspace_id, mission_id, run_id):
                self.context_calls += 1
                return self.context if self.context_calls == 1 else self.waiting

        raw_call = ProviderToolCall("call-list", "list_sources", "{}")
        domain_call = ListSourcesCall(call_id="call-list", name="list_sources", arguments={})
        store = WaitingAfterFirstStore(context=_context_snapshot())
        store.tool_results = {raw_call.call_id: _tool_result(domain_call, ordinal=1)}
        provider = FakeProvider([_completion("Evidence.", (raw_call,))])
        with patch.object(agent, "get_provider", return_value=provider):
            agent.run_agent(store, _id(1), _id(3), _id(4), Event())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(store.failures, [])


if __name__ == "__main__":
    unittest.main()
