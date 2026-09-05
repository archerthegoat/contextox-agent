"""Single-slot local runtime for bounded Path 2 Agent work."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition, Event, RLock, Thread
from typing import Callable, Literal
from uuid import uuid4

from contextox import agent
from contextox.models import (
    MissionDraftAttempt,
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
)


@dataclass
class _ActiveTask:
    token: str
    kind: Literal["attempt", "run"]
    workspace_id: str | None
    mission_id: str | None
    object_id: str | None
    cancel_event: Event
    thread: Thread | None = None


class Path2Runtime:
    """Own one local Agent slot and a bounded live-event buffer."""

    def __init__(
        self,
        store: WorkspaceStore,
        *,
        thread_factory: Callable[..., Thread] = Thread,
        event_capacity: int = 512,
    ) -> None:
        self.store = store
        self._thread_factory = thread_factory
        self._slot_lock = RLock()
        self._active: _ActiveTask | None = None
        self._closed = False
        self._events: deque[RunEventEnvelope] = deque(maxlen=event_capacity)
        self._event_condition = Condition()
        self.store.set_event_sink(self.publish_event)

    def _reserve(self, kind: Literal["attempt", "run"]) -> _ActiveTask:
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
                    self.store, workspace_id, attempt.attempt_id, task.cancel_event
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
            run = self.store.start_run(workspace_id, mission_id, request)
            if run.status != "queued":
                self._release(task.token)
                return run
            task.workspace_id = workspace_id
            task.mission_id = mission_id
            task.object_id = run.run_id
            self._start_thread(
                task,
                lambda: agent.run_agent(
                    self.store, workspace_id, mission_id, run.run_id, task.cancel_event
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
        except BaseException:
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
        finally:
            self._release(task.token)

    def cancel_run(
        self, workspace_id: str, mission_id: str, run_id: str,
    ) -> RunSnapshot:
        stopped = self.store.cancel_run(workspace_id, mission_id, run_id)
        with self._slot_lock:
            task = self._active
            if (
                task is not None and task.kind == "run"
                and task.workspace_id == workspace_id
                and task.mission_id == mission_id
                and task.object_id == run_id
            ):
                task.cancel_event.set()
        return stopped

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
    ) -> None:
        with self._event_condition:
            if any(
                event.root.workspace_id == workspace_id
                and event.root.mission_id == mission_id
                and event.root.run_id == run_id
                and event.root.sequence > after_sequence
                for event in self._events
            ):
                return
            self._event_condition.wait(timeout=timeout)

    def shutdown(self, timeout: float = 5.0) -> bool:
        with self._slot_lock:
            self._closed = True
            task = self._active
            if task is not None:
                task.cancel_event.set()
            thread = task.thread if task is not None else None
        if task is not None:
            try:
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
