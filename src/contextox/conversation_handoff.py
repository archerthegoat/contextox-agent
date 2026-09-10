"""Atomic local answer review followed by a durable, single-owner execution handoff.

Saving and approving use R2 on the *same* transaction. A crash therefore leaves
all local subrequests plus their receipts, or none; no compensating approvals and
no reconstruction from the latest unseen answers. Provider work happens outside
this module and is never retried by a read or an unclaimed operation.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from uuid import uuid4

from contextox import clarifications as r2
from contextox.handoff_models import (
    ConversationHandoffReceipt, ConversationHandoffRequest, HandoffAnswerStep,
)
from contextox.models import (
    ApprovedAnswerRef, ClarificationAnswerApproveRequest, ClarificationAnswerSaveRequest,
    TaskMessageSendRequest, canonical_sha256,
)

TABLES = (("conversation_handoffs", """CREATE TABLE conversation_handoffs (
 workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
 client_request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
 request_json TEXT NOT NULL, receipt_json TEXT NOT NULL,
 PRIMARY KEY(workspace_id, conversation_id, client_request_id),
 FOREIGN KEY(workspace_id, conversation_id)
 REFERENCES workspace_conversations(workspace_id, conversation_id)
)"""),)


class _LocalTransaction:
    """Reuse R2 validation and mutations without opening a second transaction."""
    def __init__(self, store, connection, workspace_id):
        self.store, self.connection, self.workspace_id = store, connection, workspace_id

    def __getattr__(self, name):
        return getattr(self.store, name)

    def _require_path2_workspace(self, workspace_id):
        from contextox import store as db
        if workspace_id != self.workspace_id:
            raise db.WorkspaceNotFoundError()

    @contextmanager
    def _write_transaction(self):
        yield self.connection


def _parent(store, connection, ws, cid):
    from contextox import store as db
    row = connection.execute(
        "SELECT mission_id,state_version,source_refs_json FROM workspace_conversations "
        "WHERE workspace_id=? AND conversation_id=?", (ws, cid),
    ).fetchone()
    if row is None:
        raise db.Path2StateError("conversation_not_found")
    if row[0] is None:
        raise db.Path2StateError("conversation_mission_required")
    mission = db._load_mission(connection, ws, row[0])
    return row, mission


def _validate_answer_steps(store, connection, payload, receipt):
    """Reconcile immutable R2 evidence, never substitute a newer answer version."""
    from contextox import store as db
    ws, mid = receipt.workspace_id, receipt.mission_id
    reviewed = {(a.origin_run_id, a.clarification_id): a for a in payload.reviewed_answers}
    if [(s.origin_run_id, s.clarification_id) for s in receipt.answer_steps] != sorted(reviewed):
        raise db.WorkspaceStoreUnavailableError()
    state_version = payload.expected_state_version
    request_ids = {payload.client_request_id, receipt.send_request.client_request_id}
    if len(request_ids) != 2:
        raise db.WorkspaceStoreUnavailableError()
    for step in receipt.answer_steps:
        key = (step.origin_run_id, step.clarification_id)
        original, ref = reviewed[key], step.approved_answer
        if (ref.origin_run_id, ref.clarification_id) != key:
            raise db.WorkspaceStoreUnavailableError()
        answer = r2.load_version(connection, ws, mid, *key, ref.answer_version)
        approval = r2.load_approval(connection, answer)
        if (approval is None or answer.sha256 != ref.answer_sha256
                or approval.approval_id != ref.approval_id
                or answer.request_sha256 != original.request_sha256
                or {canonical_sha256(s) for s in answer.source_refs}
                   != {canonical_sha256(s) for s in payload.source_refs}):
            raise db.WorkspaceStoreUnavailableError()
        if original.items is not None:
            save, saved = step.save_request, step.save_receipt
            if save is None or saved is None:
                raise db.WorkspaceStoreUnavailableError()
            expected = ClarificationAnswerSaveRequest(
                client_request_id=save.client_request_id, expected_latest_version=original.expected_latest_version,
                expected_state_version=state_version, request_sha256=original.request_sha256,
                review_draft=payload.expected_draft, source_refs=payload.source_refs, items=original.items)
            if (save != expected or answer.version != original.expected_latest_version + 1
                    or answer.items != original.items or answer.review_draft != payload.expected_draft
                    or saved.answer != answer or saved.approval is not None
                    or saved.operation != "save" or saved.client_request_id != save.client_request_id
                    or saved.mission_state_version != state_version + 1):
                raise db.WorkspaceStoreUnavailableError()
            state_version += 1
            _validate_subrequest(store, connection, ws, mid, key, "save", save, None, answer)
            requests = [save]
        else:
            if (step.save_request is not None or step.save_receipt is not None
                    or answer.version != original.saved_answer.version
                    or answer.sha256 != original.saved_answer.sha256):
                raise db.WorkspaceStoreUnavailableError()
            requests = []
        if original.saved_answer and original.saved_answer.approval_id is not None:
            if (approval.approval_id != original.saved_answer.approval_id
                    or step.approve_request is not None or step.approve_receipt is not None):
                raise db.WorkspaceStoreUnavailableError()
        else:
            approve, approved = step.approve_request, step.approve_receipt
            if approve is None or approved is None:
                raise db.WorkspaceStoreUnavailableError()
            if (approve.expected_state_version != state_version
                    or approve.expected_answer_sha256 != answer.sha256
                    or approved.operation != "approve" or approved.client_request_id != approve.client_request_id
                    or approved.answer != answer or approved.approval != approval
                    or approved.mission_state_version != state_version + 1):
                raise db.WorkspaceStoreUnavailableError()
            state_version += 1
            _validate_subrequest(store, connection, ws, mid, key, "approve", approve, answer.version, answer)
            requests.append(approve)
        for request in requests:
            if request.client_request_id in request_ids:
                raise db.WorkspaceStoreUnavailableError()
            request_ids.add(request.client_request_id)
    if receipt.send_request.expected_state_version != state_version:
        raise db.WorkspaceStoreUnavailableError()


def _validate_subrequest(store, connection, ws, mid, key, operation, request, version, answer):
    from contextox import store as db
    digest = canonical_sha256({"operation": operation, "origin_run_id": key[0],
        "clarification_id": key[1], "answer_version": version, "request": request.model_dump(mode="json")})
    submitted = r2.submission(store, connection, ws, mid, request.client_request_id, expected_hash=digest)
    # R2 replay reflects the *current* Mission state and approval, so only the
    # immutable request operation/identity and answer are compared here.
    if submitted is None or submitted.operation != operation or submitted.answer != answer:
        raise db.WorkspaceStoreUnavailableError()


def _read(store, connection, ws, cid, request_id, expected_hash=None):
    from contextox import store as db
    _, mission = _parent(store, connection, ws, cid)
    row = connection.execute(
        "SELECT request_sha256,request_json,receipt_json FROM conversation_handoffs "
        "WHERE workspace_id=? AND conversation_id=? AND client_request_id=?", (ws, cid, request_id),
    ).fetchone()
    if row is None:
        return None
    if expected_hash is not None and row[0] != expected_hash:
        raise db.Path2StateError("idempotency_conflict")
    try:
        payload = ConversationHandoffRequest.model_validate_json(row[1])
        receipt = ConversationHandoffReceipt.model_validate_json(row[2])
        if (row[0] != canonical_sha256(payload) or db._canonical_json(payload) != row[1]
                or db._canonical_json(receipt) != row[2]
                or (receipt.workspace_id, receipt.conversation_id, receipt.mission_id,
                    receipt.client_request_id, receipt.request_sha256) != (ws, cid, mission.mission_id, request_id, row[0])):
            raise ValueError("handoff identity mismatch")
    except ValueError as exc:
        raise db.WorkspaceStoreUnavailableError() from exc
    if (receipt.send_request.content != payload.content
            or receipt.send_request.references != payload.references
            or receipt.send_request.history_messages != payload.history_messages
            or receipt.send_request.source_refs != payload.source_refs
            or receipt.send_request.expected_draft != payload.expected_draft
            or receipt.send_request.approved_answers != [s.approved_answer for s in receipt.answer_steps]
            or {(s.origin_run_id, s.clarification_id) for s in receipt.answer_steps}
               != {(a.origin_run_id, a.clarification_id) for a in payload.reviewed_answers}):
        raise db.WorkspaceStoreUnavailableError()
    _validate_answer_steps(store, connection, payload, receipt)
    if receipt.run_id is not None:
        submitted = store._message_submission(connection, ws, mission.mission_id,
            receipt.send_request.client_request_id, receipt.send_request)
        if submitted is None or submitted.run.run_id != receipt.run_id:
            raise db.WorkspaceStoreUnavailableError()
    store._validate_source_identities(connection, ws, payload.source_refs)
    return receipt


def read(store, workspace_id, conversation_id, request_id):
    store._require_path2_workspace(workspace_id)
    with store._connection() as connection:
        connection.execute("BEGIN")
        return _read(store, connection, workspace_id, conversation_id, request_id)


def _persist(connection, receipt):
    from contextox import store as db
    connection.execute(
        "UPDATE conversation_handoffs SET receipt_json=? WHERE workspace_id=? "
        "AND conversation_id=? AND client_request_id=?",
        (db._canonical_json(receipt), receipt.workspace_id, receipt.conversation_id, receipt.client_request_id),
    )


def handoff(store, workspace_id, conversation_id, payload):
    from contextox import store as db
    ws, cid = workspace_id, conversation_id
    store._require_path2_workspace(ws)
    payload = ConversationHandoffRequest.model_validate(payload.model_dump(mode="json"))
    digest = canonical_sha256(payload)
    with store._write_transaction() as connection:
        row, mission = _parent(store, connection, ws, cid)
        replay = _read(store, connection, ws, cid, payload.client_request_id, digest)
        if replay is not None:
            return replay, False
        if row[1] != payload.expected_conversation_version or mission.state_version != payload.expected_state_version:
            raise db.Path2StateError("state_conflict")
        if r2.draft_ref(db._load_latest_draft(connection, ws, mission.mission_id)) != payload.expected_draft:
            raise db.Path2StateError("clarification_draft_stale")
        selected = {canonical_sha256(s) for s in payload.source_refs}
        if selected != {canonical_sha256(s) for s in mission.source_refs} or selected != {canonical_sha256(s) for s in json.loads(row[2])}:
            raise db.Path2StateError("source_refs_invalid")
        store._validate_source_identities(connection, ws, payload.source_refs)
        requests = db._load_clarifications(connection, ws, mission.mission_id)
        actual = {(q.run_id, q.clarification_id): q for q in requests}
        reviewed = {(a.origin_run_id, a.clarification_id): a for a in payload.reviewed_answers}
        if actual.keys() != reviewed.keys():
            raise db.Path2StateError("clarification_review_set_conflict")
        # Validate the *whole* reviewed set before writing any answer or approval.
        for key, item in reviewed.items():
            if canonical_sha256(actual[key]) != item.request_sha256 or r2.latest_version(connection, ws, mission.mission_id, *key) != item.expected_latest_version:
                raise db.Path2StateError("clarification_answer_stale")
            if item.items is not None and len(item.items) != len(actual[key].questions):
                raise db.Path2StateError("clarification_answer_incomplete")
            if item.saved_answer:
                answer = r2.load_version(connection, ws, mission.mission_id, *key, item.saved_answer.version)
                approval = r2.load_approval(connection, answer)
                if answer.sha256 != item.saved_answer.sha256 or (approval.approval_id if approval else None) != item.saved_answer.approval_id:
                    raise db.Path2StateError("clarification_answer_stale")
                r2.validate_current(store, connection, answer, review=approval is None)
        local = _LocalTransaction(store, connection, ws)
        steps = []
        state_version = mission.state_version
        for key, item in sorted(reviewed.items()):
            save_request = save_receipt = approve_request = approve_receipt = None
            if item.items is not None:
                save_request = ClarificationAnswerSaveRequest(
                    client_request_id=str(uuid4()), expected_latest_version=item.expected_latest_version,
                    expected_state_version=state_version, request_sha256=item.request_sha256,
                    review_draft=payload.expected_draft, source_refs=payload.source_refs, items=item.items,
                )
                save_receipt, _ = r2.mutate(local, ws, mission.mission_id, *key, save_request)
                answer, state_version = save_receipt.answer, save_receipt.mission_state_version
                approval = None
            else:
                answer = r2.load_version(connection, ws, mission.mission_id, *key, item.saved_answer.version)
                approval = r2.load_approval(connection, answer)
            if approval is None:
                approve_request = ClarificationAnswerApproveRequest(
                    client_request_id=str(uuid4()), expected_state_version=state_version,
                    expected_answer_sha256=answer.sha256,
                )
                approve_receipt, _ = r2.mutate(local, ws, mission.mission_id, *key, approve_request, version=answer.version)
                approval, state_version = approve_receipt.approval, approve_receipt.mission_state_version
            reference = ApprovedAnswerRef(**{name: getattr(approval, name) for name in ApprovedAnswerRef.model_fields})
            steps.append(HandoffAnswerStep(origin_run_id=key[0], clarification_id=key[1],
                save_request=save_request, save_receipt=save_receipt, approve_request=approve_request,
                approve_receipt=approve_receipt, approved_answer=reference))
        send = TaskMessageSendRequest(kind="message", client_request_id=str(uuid4()),
            expected_state_version=state_version, content=payload.content, references=payload.references,
            history_messages=payload.history_messages, source_refs=payload.source_refs,
            provider_send_confirmed=True, approved_answers=[s.approved_answer for s in steps],
            expected_draft=payload.expected_draft)
        r2.validate_send(store, connection, db._load_mission(connection, ws, mission.mission_id), send)
        receipt = ConversationHandoffReceipt(workspace_id=ws, conversation_id=cid, mission_id=mission.mission_id,
            client_request_id=payload.client_request_id, request_sha256=digest, created_at=db._utc_now(),
            answers_saved=True, answers_approved=True, analysis_started=False, analysis_state="ready",
            send_request=send, answer_steps=steps)
        connection.execute("INSERT INTO conversation_handoffs VALUES (?,?,?,?,?,?)",
            (ws, cid, payload.client_request_id, digest, db._canonical_json(payload), db._canonical_json(receipt)))
        return receipt, True


def claim_analysis(store, workspace_id, conversation_id, request_id):
    """Only call for an explicit POST, after acquiring the Runtime activity slot.

    A failed operation is retryable only when the runtime proved no Run exists.
    Unknown/claimed outcomes require reconciliation, never automatic re-execution.
    """
    from contextox import store as db
    store._require_path2_workspace(workspace_id)
    with store._write_transaction() as connection:
        receipt = _read(store, connection, workspace_id, conversation_id, request_id)
        if receipt is None:
            raise db.Path2StateError("conversation_handoff_not_found")
        if receipt.analysis_state not in {"ready", "failed"} or receipt.run_id is not None:
            return receipt, False
        # An old approved operation cannot silently adopt a later conversation
        # scope/version. Runtime will independently revalidate Mission and R2 CAS.
        parent, _ = _parent(store, connection, workspace_id, conversation_id)
        original = connection.execute(
            "SELECT request_json FROM conversation_handoffs WHERE workspace_id=? AND conversation_id=? AND client_request_id=?",
            (workspace_id, conversation_id, request_id),
        ).fetchone()
        payload = ConversationHandoffRequest.model_validate_json(original[0])
        if parent[1] != payload.expected_conversation_version or (
                {canonical_sha256(s) for s in json.loads(parent[2])}
                != {canonical_sha256(s) for s in payload.source_refs}):
            raise db.Path2StateError("state_conflict")
        receipt = receipt.model_copy(update={"analysis_state": "claimed", "error_code": None})
        _persist(connection, receipt)
        return receipt, True


def record_analysis(store, workspace_id, conversation_id, request_id, *, state, expected_analysis_state, run_id=None, error_code=None):
    """Persist the reconciled Runtime outcome; never dispatch model work here."""
    from contextox import store as db
    if state not in {"started", "failed", "unknown"}:
        raise ValueError("unsupported handoff outcome")
    store._require_path2_workspace(workspace_id)
    with store._write_transaction() as connection:
        receipt = _read(store, connection, workspace_id, conversation_id, request_id)
        if receipt is None:
            raise db.Path2StateError("conversation_handoff_not_found")
        if receipt.analysis_state != expected_analysis_state:
            raise db.Path2StateError("state_conflict")
        if (receipt.analysis_state == state and receipt.run_id == run_id and receipt.error_code == error_code):
            return receipt
        if receipt.analysis_state in {"ready", "failed"}:
            raise db.Path2StateError("handoff_analysis_not_claimed")
        if receipt.analysis_state == "unknown" and run_id is None:
            if state == "unknown":
                return receipt
            raise db.Path2StateError("previous_outcome_unresolved")
        if receipt.run_id is not None and receipt.run_id != run_id:
            raise db.Path2StateError("state_conflict")
        if receipt.analysis_state == "started" and state != "started":
            # A recorded Run remains bound even if starting its worker failed.
            if run_id is None:
                raise db.Path2StateError("state_conflict")
        if run_id is not None:
            submission = store._message_submission(connection, workspace_id, receipt.mission_id,
                receipt.send_request.client_request_id, receipt.send_request)
            if submission is None or submission.run.run_id != run_id:
                raise db.Path2StateError("handoff_run_identity_mismatch")
            if receipt.analysis_state == "started" and state != "started" and submission.run.status not in {"failed", "blocked", "cancelled"}:
                raise db.Path2StateError("state_conflict")
        receipt = ConversationHandoffReceipt.model_validate(receipt.model_dump(mode="json") | {
            "analysis_state": state, "analysis_started": state == "started", "run_id": run_id, "error_code": error_code,
        })
        _persist(connection, receipt)
        return receipt


def recovered_analysis_state(run):
    """Startup evidence survives a later analysis failure or cancellation."""
    if run.started_at is not None:
        return "started"
    if run.status in {"failed", "cancelled", "blocked"}:
        return "failed"
    return "unknown" if run.status == "queued" else "started"
