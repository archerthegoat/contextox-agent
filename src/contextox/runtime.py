"""Single-slot local runtime for bounded Path 2 Agent work."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition, Event, RLock, Thread
from typing import Callable, Literal
from uuid import uuid4

from contextox import agent
from contextox import profile_interpretation
from contextox import discussion, conversation_handoff
from contextox.handoff_models import ConversationHandoffRequest, ConversationHandoffReceipt
from contextox.models import (
    ConversationMessageSendRequest, ConversationSubmissionReceipt, DiscussionTurn,
    TaskMessageSendRequest, TaskMessageSendReceipt,
    MissionDraftAttempt,
    ProfileInterpretationAttempt,
    ProfileInterpretationCreateRequest,
    RunEventEnvelope,
    RunFailedEventInput,
    RunFailedPayload,
    RunSnapshot,
    RunStartRequest,
)
from contextox.store import (
    WorkspaceStore,
    WorkspaceStoreBusyError,
    WorkspaceStoreError,
    WorkspaceStoreUnavailableError,
    WorkspaceNotFoundError,
    Path2StateError,
)


@dataclass
class _ActiveTask:
    token: str
    kind: Literal["attempt", "run", "profile", "discussion"]
    workspace_id: str | None
    mission_id: str | None
    object_id: str | None
    cancel_event: Event
    thread: Thread | None = None
    conversation_id: str | None = None
    discussion_turn_id: str | None = None


class Path2Runtime:
    """Own one local Agent slot and a bounded live-event buffer."""

    def __init__(
        self,
        store: WorkspaceStore,
        *,
        thread_factory: Callable[..., Thread] = Thread,
        event_capacity: int = 512,
        agent_profile: agent.AgentProfile = "production",
    ) -> None:
        if agent_profile not in {"production", "demo-fast"}:
            raise ValueError("unsupported agent profile")
        self.agent_profile = agent_profile
        self.store = store
        self._thread_factory = thread_factory
        self._slot_lock = RLock()
        self._active: _ActiveTask | None = None
        self._closed = False
        self._events: deque[RunEventEnvelope] = deque(maxlen=event_capacity)
        self._event_condition = Condition()
        self.store.set_event_sink(self.publish_event)

    def _reserve(self, kind: Literal["attempt", "run", "profile", "discussion"]) -> _ActiveTask:
        with self._slot_lock:
            if self._closed or self._active is not None:
                raise WorkspaceStoreBusyError()
            task = _ActiveTask(
                token=str(uuid4()), kind=kind, workspace_id=None,
                mission_id=None, object_id=None, cancel_event=Event(),
            )
            self._active = task
            return task

    def _release(self, token: str) -> None:
        with self._slot_lock:
            if self._active is not None and self._active.token == token:
                self._active = None
        with self._event_condition:
            self._event_condition.notify_all()

    def is_run_active(self, workspace_id: str, mission_id: str, run_id: str) -> bool:
        with self._slot_lock:
            task = self._active
            return bool(task is not None and task.kind == "run"
                        and task.workspace_id == workspace_id
                        and task.mission_id == mission_id and task.object_id == run_id)

    def _start_thread(self, task: _ActiveTask, target: Callable[[], None]) -> None:
        thread = self._thread_factory(
            target=self._worker, args=(task, target),
            name=f"contextox-{task.kind}-{task.token[:8]}", daemon=True,
        )
        with self._slot_lock:
            if (
                self._closed or self._active is None
                or self._active.token != task.token
            ):
                raise WorkspaceStoreBusyError()
            task.thread = thread
            try:
                thread.start()
            except BaseException:
                self._active = None
                raise

    def start_mission_draft(
        self, workspace_id: str, original_input: str,
    ) -> MissionDraftAttempt:
        if self.store.get_workspace(workspace_id) is None:
            raise WorkspaceNotFoundError()
        task = self._reserve("attempt")
        attempt: MissionDraftAttempt | None = None
        try:
            attempt = self.store.create_mission_draft_attempt(
                workspace_id, original_input
            )
            task.workspace_id = workspace_id
            task.object_id = attempt.attempt_id
            self._start_thread(
                task,
                lambda: agent.generate_mission_draft(
                    self.store, workspace_id, attempt.attempt_id, task.cancel_event,
                    agent_profile=self.agent_profile,
                ),
            )
            return attempt
        except BaseException as exc:
            if attempt is not None:
                try:
                    self.store.fail_mission_draft_attempt(
                        workspace_id, attempt.attempt_id, "failed",
                        "agent_start_failed", None,
                    )
                except WorkspaceStoreError:
                    pass
            self._release(task.token)
            if isinstance(exc, WorkspaceStoreError):
                raise
            raise WorkspaceStoreUnavailableError() from exc

    def start_run(
        self, workspace_id: str, mission_id: str, request: RunStartRequest,
    ) -> RunSnapshot:
        with self._slot_lock:
            busy = self._closed or self._active is not None
        if busy:
            replay = self.store.find_run_start(workspace_id, mission_id, request)
            if replay is not None:
                return replay
            raise WorkspaceStoreBusyError()

        task = self._reserve("run")
        run: RunSnapshot | None = None
        try:
            run = self.store.start_run(workspace_id, mission_id, request, demo_fast=self.agent_profile == "demo-fast")
            if run.status != "queued":
                self._release(task.token)
                return run
            task.workspace_id = workspace_id
            task.mission_id = mission_id
            task.object_id = run.run_id
            self._start_thread(
                task,
                lambda: agent.run_agent(
                    self.store, workspace_id, mission_id, run.run_id, task.cancel_event,
                    agent_profile=self.agent_profile,
                ),
            )
            return run
        except BaseException as exc:
            if run is not None and run.status == "queued":
                self._fail_run(workspace_id, mission_id, run.run_id, "agent_start_failed")
            self._release(task.token)
            if isinstance(exc, WorkspaceStoreError):
                raise
            raise WorkspaceStoreUnavailableError() from exc

    def start_profile_interpretation(
        self,
        workspace_id: str,
        revision_id: str,
        request: ProfileInterpretationCreateRequest,
    ) -> tuple[ProfileInterpretationAttempt, bool]:
        with self._slot_lock:
            busy = self._closed or self._active is not None
        if busy:
            replay = self.store.find_profile_interpretation_request(
                workspace_id, revision_id, request.client_request_id
            )
            if replay is not None:
                return replay, False
            raise WorkspaceStoreBusyError()
        task = self._reserve("profile")
        attempt: ProfileInterpretationAttempt | None = None
        try:
            attempt, created = self.store.create_profile_interpretation_attempt(
                workspace_id,
                revision_id,
                request,
                profile_interpretation.profile_provider_config(),
                profile_interpretation.PROFILE_INTERPRETATION_P0_SHA256,
            )
            if not created or attempt.status != "queued":
                self._release(task.token)
                return attempt, created
            task.workspace_id = workspace_id
            task.mission_id = revision_id
            task.object_id = attempt.attempt_id
            self._start_thread(task, lambda: profile_interpretation.run_profile_interpretation(
                self.store, workspace_id, revision_id, attempt.attempt_id,
                task.cancel_event,
            ))
            return attempt, True
        except BaseException as exc:
            if attempt is not None and attempt.status == "queued":
                try:
                    self.store.fail_profile_interpretation_attempt(
                        workspace_id, revision_id, attempt.attempt_id,
                        "failed", "agent_start_failed",
                    )
                except WorkspaceStoreError:
                    pass
            self._release(task.token)
            if isinstance(exc, WorkspaceStoreError):
                raise
            raise WorkspaceStoreUnavailableError() from exc

    def send_task_message(
        self, workspace_id: str, mission_id: str, request: TaskMessageSendRequest,
    ) -> tuple[TaskMessageSendReceipt, bool]:
        replay = self.store.message_submission(workspace_id, mission_id, request.client_request_id, request)
        if replay is not None:
            return replay, False
        task = self._reserve("run")
        receipt = None
        try:
            receipt, created = self.store.send_task_message(workspace_id, mission_id, request, demo_fast=self.agent_profile == "demo-fast")
            if not created:
                self._release(task.token)
                return receipt, False
            run = receipt.run
            task.workspace_id, task.mission_id, task.object_id = workspace_id, mission_id, run.run_id
            self._start_thread(task, lambda: agent.run_agent(
                self.store, workspace_id, mission_id, run.run_id, task.cancel_event,
                agent_profile=self.agent_profile,
            ))
            return receipt, True
        except BaseException as exc:
            if receipt is not None:
                self._fail_run(workspace_id, mission_id, receipt.run.run_id, "agent_start_failed")
            self._release(task.token)
            if isinstance(exc, WorkspaceStoreError):
                raise
            raise WorkspaceStoreUnavailableError() from exc

    def send_conversation_message(self, workspace_id: str, conversation_id: str,
                                  request: ConversationMessageSendRequest
                                  ) -> tuple[ConversationSubmissionReceipt, bool]:
        replay = self.store.conversation_submission(workspace_id, conversation_id,
                                                    request.client_request_id, request)
        if replay is not None:
            return replay, False
        task = self._reserve("discussion")
        receipt = None
        try:
            provider = agent.get_provider(agent_profile=self.agent_profile)
            receipt, created = self.store.create_conversation_submission(
                workspace_id, conversation_id, request, provider.config,
                discussion.P0_SHA256, discussion.OUTPUT_SCHEMA_SHA256,
                demo_fast=self.agent_profile == "demo-fast",
            )
            if not created:
                self._release(task.token)
                return receipt, False
            task.workspace_id, task.conversation_id = workspace_id, conversation_id
            if receipt.run is not None:
                task.kind, task.mission_id, task.object_id = "run", receipt.run.mission_id, receipt.run.run_id
                self._start_thread(task, lambda: agent.run_agent(self.store, workspace_id,
                    task.mission_id, task.object_id, task.cancel_event, agent_profile=self.agent_profile))
            else:
                turn = receipt.discussion_turn
                if turn is None:
                    raise WorkspaceStoreUnavailableError()
                task.object_id = task.discussion_turn_id = turn.turn_id
                task.mission_id = turn.mission_id
                self._start_thread(task, lambda: self._discuss_and_continue(task))
            return receipt, True
        except BaseException as error:
            if receipt is not None:
                if receipt.run is not None:
                    self._fail_run(workspace_id, receipt.run.mission_id, receipt.run.run_id, "agent_start_failed")
                elif receipt.discussion_turn is not None:
                    try:
                        self.store.fail_discussion_turn(workspace_id, conversation_id,
                            receipt.discussion_turn.turn_id, "failed", "agent_start_failed")
                    except WorkspaceStoreError:
                        pass
            self._release(task.token)
            if isinstance(error, WorkspaceStoreError):
                raise
            raise WorkspaceStoreUnavailableError() from error

    def _discuss_and_continue(self, task: _ActiveTask) -> None:
        ws, cid, tid = task.workspace_id, task.conversation_id, task.discussion_turn_id
        turn = discussion.run_discussion(self.store, ws, cid, tid, task.cancel_event,
                                         agent_profile=self.agent_profile)
        if turn.status != "succeeded" or turn.output is None or turn.output.next_action != "start_task":
            return
        try:
            # Cancellation and the persisted task/Run handoff share the same slot.
            with self._slot_lock:
                if self._closed or task.cancel_event.is_set():
                    self.store.fail_conversation_start(ws, cid, tid, "cancelled")
                    return
                receipt = self.store.start_conversation_task(ws, cid, tid, turn.output,
                    demo_fast=self.agent_profile == "demo-fast")
                task.kind, task.mission_id, task.object_id = "run", receipt.run.mission_id, receipt.run.run_id
            agent.run_agent(self.store, ws, task.mission_id, task.object_id,
                            task.cancel_event, agent_profile=self.agent_profile)
        except WorkspaceStoreError as error:
            self.store.fail_conversation_start(ws, cid, tid,
                error.code if isinstance(error, Path2StateError) else "agent_start_failed")
            if task.kind == "run":
                self._fail_run(ws, task.mission_id, task.object_id, "agent_start_failed")

    def cancel_conversation_turn(self, workspace_id: str, conversation_id: str,
                                 turn_id: str) -> DiscussionTurn:
        with self._slot_lock:
            task = self._active
            if (task is not None and task.workspace_id == workspace_id
                and task.conversation_id == conversation_id and task.discussion_turn_id == turn_id):
                task.cancel_event.set()
            # Re-read inside the lock: the discussion may have just handed off.
            turn = self.store.get_discussion_turn(workspace_id, conversation_id, turn_id, validate_sources=False)
            if turn.run_id is not None and turn.mission_id is not None:
                self.store.cancel_run(workspace_id, turn.mission_id, turn.run_id)
            else:
                turn = self.store.cancel_discussion_turn(workspace_id, conversation_id, turn_id)
        # Revocation must not prevent stopping work, or disclose old source text
        # in the public cancellation response.
        return self.store.get_discussion_turn(workspace_id, conversation_id, turn_id)

    def handoff_answers(self, workspace_id: str, conversation_id: str,
                        request: ConversationHandoffRequest) -> tuple[ConversationHandoffReceipt, bool]:
        receipt, created = conversation_handoff.handoff(self.store, workspace_id, conversation_id, request)
        if receipt.analysis_state in {"claimed", "unknown"}:
            # An explicit POST may reconcile the original identity after a crash.
            # It never redispatches the associated queued or running work.
            try:
                existing = self.store.message_submission(workspace_id, receipt.mission_id,
                    receipt.send_request.client_request_id, receipt.send_request)
                if existing is not None:
                    state = conversation_handoff.recovered_analysis_state(existing.run)
                    receipt = conversation_handoff.record_analysis(self.store, workspace_id, conversation_id,
                        request.client_request_id, expected_analysis_state=receipt.analysis_state, state=state, run_id=existing.run.run_id)
            except WorkspaceStoreError:
                pass
            return receipt, False
        if receipt.analysis_state not in {"ready", "failed"} or receipt.run_id is not None:
            return receipt, False
        try:
            task = self._reserve("run")
        except WorkspaceStoreBusyError:
            # Another request may already have claimed this same handoff. A busy
            # observer must never overwrite its state with a retryable failure.
            return conversation_handoff.read(self.store, workspace_id, conversation_id,
                request.client_request_id), created
        run_receipt = None
        owned_run_created = False
        try:
            receipt, claimed = conversation_handoff.claim_analysis(self.store, workspace_id,
                conversation_id, request.client_request_id)
            if not claimed:
                self._release(task.token)
                return receipt, False
            run_receipt, run_created = self.store.send_task_message(workspace_id, receipt.mission_id,
                receipt.send_request, demo_fast=self.agent_profile == "demo-fast")
            owned_run_created = run_created
            if not run_created:
                receipt = conversation_handoff.record_analysis(self.store, workspace_id, conversation_id,
                    request.client_request_id, expected_analysis_state=receipt.analysis_state, state=conversation_handoff.recovered_analysis_state(run_receipt.run),
                    run_id=run_receipt.run.run_id)
                self._release(task.token)
                return receipt, False
            receipt = conversation_handoff.record_analysis(self.store, workspace_id, conversation_id,
                request.client_request_id, expected_analysis_state=receipt.analysis_state, state="started", run_id=run_receipt.run.run_id)
            task.workspace_id, task.mission_id, task.object_id = workspace_id, receipt.mission_id, receipt.run_id
            task.conversation_id = conversation_id
            self._start_thread(task, lambda: agent.run_agent(self.store, workspace_id,
                task.mission_id, task.object_id, task.cancel_event, agent_profile=self.agent_profile))
            return receipt, created
        except BaseException as error:
            # A failed response is not evidence that the Run was never created.
            reconciliation_failed = False
            if run_receipt is None:
                try:
                    run_receipt = self.store.message_submission(workspace_id, receipt.mission_id,
                        receipt.send_request.client_request_id, receipt.send_request)
                except WorkspaceStoreError:
                    reconciliation_failed = True
            if run_receipt is not None and owned_run_created:
                self._fail_run(workspace_id, receipt.mission_id, run_receipt.run.run_id, "agent_start_failed")
            code = error.code if isinstance(error, Path2StateError) else "agent_start_failed"
            known_rejection = not reconciliation_failed and code in {
                "state_conflict", "run_already_active", "source_permission_denied",
                "source_revision_mismatch", "source_refs_invalid", "message_reference_stale",
                "message_context_scope_mismatch", "clarification_answer_stale",
                "clarification_draft_stale", "clarification_target_stale", "idempotency_conflict",
            }
            try:
                result = conversation_handoff.record_analysis(self.store, workspace_id, conversation_id,
                    request.client_request_id, expected_analysis_state=receipt.analysis_state,
                    state="failed" if owned_run_created or (run_receipt is None and known_rejection) else "unknown",
                    run_id=None if run_receipt is None else run_receipt.run.run_id, error_code=code)
            finally:
                self._release(task.token)
            return result, created

    def _fail_run(
        self, workspace_id: str, mission_id: str, run_id: str, code: str,
    ) -> None:
        try:
            stopped = self.store.fail_run(
                workspace_id, mission_id, run_id, "failed", code
            )
            if stopped.status == "failed":
                self.store.append_run_event(
                    workspace_id, mission_id, run_id,
                    RunFailedEventInput(
                        event_type="run_failed",
                        public_payload=RunFailedPayload(
                            status="failed", terminal_receipt_id=None,
                            error_code=code,
                        ),
                    ),
                )
        except WorkspaceStoreError:
            pass

    def _worker(self, task: _ActiveTask, target: Callable[[], None]) -> None:
        try:
            target()
        except BaseException as error:
            if task.kind == "discussion" and task.workspace_id and task.conversation_id and task.object_id:
                try:
                    self.store.fail_discussion_turn(task.workspace_id, task.conversation_id,
                        task.object_id, "blocked" if isinstance(error, Path2StateError) else "failed",
                        error.code if isinstance(error, Path2StateError) else "agent_worker_failed")
                except WorkspaceStoreError:
                    pass
            if (
                task.kind == "run"
                and task.workspace_id is not None
                and task.mission_id is not None
                and task.object_id is not None
            ):
                self._fail_run(
                    task.workspace_id, task.mission_id, task.object_id,
                    "agent_worker_failed",
                )
            elif (
                task.kind == "attempt"
                and task.workspace_id is not None
                and task.object_id is not None
            ):
                try:
                    self.store.fail_mission_draft_attempt(
                        task.workspace_id, task.object_id, "failed",
                        "agent_worker_failed", None,
                    )
                except WorkspaceStoreError:
                    pass
            elif (
                task.kind == "profile"
                and task.workspace_id is not None
                and task.mission_id is not None
                and task.object_id is not None
            ):
                try:
                    self.store.fail_profile_interpretation_attempt(
                        task.workspace_id, task.mission_id, task.object_id,
                        "failed", "agent_worker_failed",
                    )
                except WorkspaceStoreError:
                    pass
        finally:
            self._release(task.token)

    def cancel_run(
        self, workspace_id: str, mission_id: str, run_id: str,
    ) -> RunSnapshot:
        # Stop the already-owned worker even if source revocation blocks public readback.
        with self._slot_lock:
            task = self._active
            if (
                task is not None and task.kind == "run"
                and task.workspace_id == workspace_id
                and task.mission_id == mission_id
                and task.object_id == run_id
            ):
                task.cancel_event.set()
        return self.store.cancel_run(workspace_id, mission_id, run_id)

    def publish_event(self, event: RunEventEnvelope) -> None:
        with self._event_condition:
            self._events.append(event)
            self._event_condition.notify_all()

    def buffered_events(
        self, workspace_id: str, mission_id: str, run_id: str,
        after_sequence: int,
    ) -> list[RunEventEnvelope]:
        with self._event_condition:
            return [
                event for event in self._events
                if event.root.workspace_id == workspace_id
                and event.root.mission_id == mission_id
                and event.root.run_id == run_id
                and event.root.sequence > after_sequence
            ]

    def wait_for_change(
        self, workspace_id: str, mission_id: str, run_id: str,
        after_sequence: int, timeout: float = 15.0,
        stop_event: Event | None = None,
    ) -> None:
        with self._event_condition:
            if stop_event is not None and stop_event.is_set():
                return
            if any(
                event.root.workspace_id == workspace_id
                and event.root.mission_id == mission_id
                and event.root.run_id == run_id
                and event.root.sequence > after_sequence
                for event in self._events
            ):
                return
            self._event_condition.wait(timeout=timeout)

    def wake_event_waiters(self) -> None:
        """Wake SSE readers after the server sets its stream-stop event."""
        with self._event_condition:
            self._event_condition.notify_all()

    def shutdown(self, timeout: float = 5.0) -> bool:
        with self._slot_lock:
            self._closed = True
            task = self._active
            if task is not None:
                task.cancel_event.set()
            thread = task.thread if task is not None else None
        if task is not None:
            try:
                if task.kind == "discussion" and task.workspace_id and task.conversation_id and task.object_id:
                    self.store.cancel_discussion_turn(task.workspace_id, task.conversation_id, task.object_id)
                if (
                    task.kind == "run" and task.workspace_id is not None
                    and task.mission_id is not None and task.object_id is not None
                ):
                    self.store.cancel_run(
                        task.workspace_id, task.mission_id, task.object_id
                    )
                elif (
                    task.kind == "attempt" and task.workspace_id is not None
                    and task.object_id is not None
                ):
                    self.store.fail_mission_draft_attempt(
                        task.workspace_id, task.object_id, "cancelled", "cancelled", None
                    )
                elif (
                    task.kind == "profile" and task.workspace_id is not None
                    and task.mission_id is not None and task.object_id is not None
                ):
                    task.cancel_event.set()
                    self.store.fail_profile_interpretation_attempt(
                        task.workspace_id, task.mission_id, task.object_id,
                        "cancelled", "cancelled",
                    )
            except WorkspaceStoreError:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self.store.set_event_sink(None)
        return thread is None or not thread.is_alive()

    @property
    def busy(self) -> bool:
        with self._slot_lock:
            return self._active is not None

    def change_credentials(self, operation: Callable[[], None]) -> None:
        """Keep the same credential throughout a reserved Provider task."""
        with self._slot_lock:
            if self._closed or self._active is not None:
                raise WorkspaceStoreBusyError()
            operation()
