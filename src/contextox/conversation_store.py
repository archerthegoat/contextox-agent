"""Workspace-scoped immutable conversation inputs and bounded discussion receipts.

Provider work belongs to Runtime. These functions only validate and persist local
state; reads never resume work or send requests.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from contextox.models import (
    ConversationCreateRequest, ConversationGoal, ConversationMessage,
    ConversationMessagePage, ConversationMessageSendRequest,
    ConversationMissionOrigin, ConversationSubmissionReceipt, DiscussionContext,
    DiscussionMissionContext,
    DiscussionOutput, DiscussionProviderReceipt, DiscussionTurn, MessageHistoryRef,
    SourceIdentity, TaskMessageSendRequest,
    WorkspaceConversation, canonical_sha256,
)

TABLES = (
    ("workspace_conversations", """CREATE TABLE workspace_conversations (
 workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL, created_at TEXT NOT NULL,
 title TEXT NOT NULL, state_version INTEGER NOT NULL CHECK(state_version > 0),
 mission_id TEXT, source_refs_json TEXT NOT NULL, goal_json TEXT,
 PRIMARY KEY(workspace_id, conversation_id), UNIQUE(workspace_id, mission_id),
 FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id),
 FOREIGN KEY(workspace_id, mission_id) REFERENCES missions(workspace_id, mission_id)
)"""),
    ("conversation_creations", """CREATE TABLE conversation_creations (
 workspace_id TEXT NOT NULL, client_request_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL, conversation_id TEXT NOT NULL,
 PRIMARY KEY(workspace_id, client_request_id),
 FOREIGN KEY(workspace_id, conversation_id)
 REFERENCES workspace_conversations(workspace_id, conversation_id)
)"""),
    ("conversation_messages", """CREATE TABLE conversation_messages (
 workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
 created_at TEXT NOT NULL, body_json TEXT, task_message_id TEXT,
 PRIMARY KEY(workspace_id, conversation_id, message_id),
 UNIQUE(workspace_id, conversation_id, task_message_id),
 CHECK(body_json IS NOT NULL OR task_message_id IS NOT NULL),
 FOREIGN KEY(workspace_id, conversation_id)
 REFERENCES workspace_conversations(workspace_id, conversation_id)
)"""),
    ("discussion_turns", """CREATE TABLE discussion_turns (
 workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL, turn_id TEXT NOT NULL,
 input_message_id TEXT NOT NULL, turn_json TEXT NOT NULL, context_json TEXT NOT NULL,
 PRIMARY KEY(workspace_id, conversation_id, turn_id),
 FOREIGN KEY(workspace_id, conversation_id, input_message_id)
 REFERENCES conversation_messages(workspace_id, conversation_id, message_id)
)"""),
    ("discussion_provider_receipts", """CREATE TABLE discussion_provider_receipts (
 workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL, turn_id TEXT NOT NULL,
 receipt_id TEXT NOT NULL, receipt_json TEXT NOT NULL,
 PRIMARY KEY(workspace_id, conversation_id, turn_id), UNIQUE(workspace_id, receipt_id),
 FOREIGN KEY(workspace_id, conversation_id, turn_id)
 REFERENCES discussion_turns(workspace_id, conversation_id, turn_id)
)"""),
    ("conversation_submissions", """CREATE TABLE conversation_submissions (
 workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL, client_request_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL, request_json TEXT NOT NULL, input_message_id TEXT NOT NULL,
 turn_id TEXT, mission_id TEXT, run_id TEXT,
 PRIMARY KEY(workspace_id, conversation_id, client_request_id),
 FOREIGN KEY(workspace_id, conversation_id, input_message_id)
 REFERENCES conversation_messages(workspace_id, conversation_id, message_id),
 FOREIGN KEY(workspace_id, conversation_id, turn_id)
 REFERENCES discussion_turns(workspace_id, conversation_id, turn_id),
 FOREIGN KEY(workspace_id, mission_id, run_id) REFERENCES runs(workspace_id, mission_id, run_id)
)"""),
)


def _db():
    from contextox import store
    return store


def enabled(connection):
    return connection.execute("PRAGMA user_version").fetchone()[0] == 7


def require(connection):
    if not enabled(connection):
        raise _db().Path2StateError("conversation_not_implemented")


def load_conversation(store, connection, ws, cid):
    require(connection)
    row = connection.execute(
        "SELECT created_at,title,state_version,mission_id,source_refs_json,goal_json "
        "FROM workspace_conversations WHERE workspace_id=? AND conversation_id=?", (ws, cid),
    ).fetchone()
    if row is None:
        raise _db().Path2StateError("conversation_not_found")
    try:
        active=None
        for tid, serialized in connection.execute("SELECT turn_id,turn_json FROM discussion_turns WHERE workspace_id=? AND conversation_id=? ORDER BY rowid DESC",(ws,cid)):
            if json.loads(serialized).get("status") in {"queued","running"}:
                active=tid
                break
        last=connection.execute("SELECT client_request_id FROM conversation_submissions WHERE workspace_id=? AND conversation_id=? ORDER BY rowid DESC LIMIT 1",(ws,cid)).fetchone()
        handoff=connection.execute("SELECT client_request_id FROM conversation_handoffs WHERE workspace_id=? AND conversation_id=? ORDER BY rowid DESC LIMIT 1",(ws,cid)).fetchone()
        return WorkspaceConversation(workspace_id=ws, conversation_id=cid,
            created_at=row[0], title=row[1], state_version=row[2], mission_id=row[3],
            source_refs=json.loads(row[4]), goal=json.loads(row[5]) if row[5] else None,
            active_turn_id=active,last_submission_id=last[0] if last else None,last_handoff_id=handoff[0] if handoff else None)
    except ValueError as exc:
        raise _db().WorkspaceStoreUnavailableError() from exc


def sync_task_messages(connection):
    """Link new TaskMessages during the owning write transaction, without copies."""
    if not enabled(connection):
        return
    connection.execute("""INSERT INTO conversation_messages
        (workspace_id,conversation_id,message_id,created_at,body_json,task_message_id)
        SELECT m.workspace_id,c.conversation_id,m.message_id,m.created_at,NULL,m.message_id
        FROM mission_messages m JOIN workspace_conversations c
        ON c.workspace_id=m.workspace_id AND c.mission_id=m.mission_id
        WHERE NOT EXISTS(SELECT 1 FROM conversation_messages cm WHERE
        cm.workspace_id=c.workspace_id AND cm.conversation_id=c.conversation_id
        AND cm.task_message_id=m.message_id) ORDER BY m.rowid""")


def mission_conversation(store, connection, workspace_id, mission_id):
    """A real v7 association, not a caller-selected permission mode."""
    if not enabled(connection):
        return None
    row=connection.execute("SELECT conversation_id FROM workspace_conversations WHERE workspace_id=? AND mission_id=?",
        (workspace_id,mission_id)).fetchone()
    return load_conversation(store,connection,workspace_id,row[0]) if row else None


def validate_message_scope(store, connection, mission, request):
    """Prove expanded Run inputs were explicitly submitted through a conversation."""
    db=_db()
    original_scope=all(ref in mission.source_refs for ref in request.source_refs)
    conv=mission_conversation(store,connection,mission.workspace_id,mission.mission_id)
    if conv is None:
        if original_scope:
            return
        raise db.Path2StateError("source_refs_invalid")
    ws,cid=conv.workspace_id,conv.conversation_id
    row=connection.execute("SELECT request_sha256,request_json,input_message_id,turn_id FROM conversation_submissions WHERE workspace_id=? AND conversation_id=? AND client_request_id=?",
        (ws,cid,request.client_request_id)).fetchone()
    if row:
        # The first Run is already authorized by start_task's terminal receipt,
        # origin and CAS gate. Its source snapshot exactly created this Mission.
        if (original_scope and row[3] is not None and mission.conversation_origin
            and mission.conversation_origin.client_request_id==request.client_request_id
            and mission.conversation_origin.message_id==row[2]):
            return
        saved=ConversationMessageSendRequest.model_validate_json(row[1])
        if canonical_sha256(saved)!=row[0] or saved.client_request_id!=request.client_request_id:
            raise db.WorkspaceStoreUnavailableError()
        history=_history(store,connection,conv,saved)
        if (conv.state_version!=saved.expected_state_version+1 or saved.content!=request.content
            or saved.source_refs!=request.source_refs or saved.references!=request.references
            or saved.regenerate_from_run_id!=request.regenerate_from_run_id
            or any(message.task_message is None for message in history)
            or [MessageHistoryRef(message_id=m.task_message.message_id,sha256=m.task_message.sha256) for m in history]!=request.history_messages):
            raise db.Path2StateError("state_conflict")
        message=load_message(store,connection,ws,cid,row[2])
        if message.role!="user" or message.content!=saved.content or message.source_refs!=saved.source_refs or message.references!=saved.references:
            raise db.WorkspaceStoreUnavailableError()
    else:
        from contextox.handoff_models import ConversationHandoffReceipt, ConversationHandoffRequest
        found=False
        for request_id,raw in connection.execute("SELECT client_request_id,receipt_json FROM conversation_handoffs WHERE workspace_id=? AND conversation_id=?",(ws,cid)):
            candidate=ConversationHandoffReceipt.model_validate_json(raw)
            if candidate.send_request.client_request_id!=request.client_request_id:
                continue
            receipt=db.conversation_handoff._read(store,connection,ws,cid,request_id)
            original=connection.execute("SELECT request_json FROM conversation_handoffs WHERE workspace_id=? AND conversation_id=? AND client_request_id=?",(ws,cid,request_id)).fetchone()
            payload=ConversationHandoffRequest.model_validate_json(original[0])
            if receipt.send_request!=request or conv.state_version!=payload.expected_conversation_version:
                raise db.Path2StateError("state_conflict")
            found=True
            break
        if not found:
            if original_scope:
                return
            raise db.Path2StateError("source_refs_invalid")
    if {canonical_sha256(ref) for ref in conv.source_refs}!={canonical_sha256(ref) for ref in request.source_refs}:
        raise db.Path2StateError("source_refs_invalid")
    store._validate_source_identities(connection,ws,request.source_refs)
    validate_goal(store,connection,conv,conv.goal,request.source_refs)


def create(store, ws, request):
    db = _db()
    request = ConversationCreateRequest.model_validate(request.model_dump())
    store._require_path2_workspace(ws)
    with store._write_transaction() as c:
        require(c)
        old = c.execute("SELECT request_sha256,conversation_id FROM conversation_creations "
            "WHERE workspace_id=? AND client_request_id=?", (ws, request.client_request_id)).fetchone()
        if old:
            if old[0] != canonical_sha256(request):
                raise db.Path2StateError("idempotency_conflict")
            result=load_conversation(store,c,ws,old[1])
            store._validate_source_identities(c,ws,result.source_refs)
            return result,False
        refs = db._validated_source_identities(ws, request.source_refs)
        if request.mission_id:
            mission = db._load_mission(c, ws, request.mission_id)
            linked = c.execute("SELECT conversation_id FROM workspace_conversations "
                "WHERE workspace_id=? AND mission_id=?", (ws, request.mission_id)).fetchone()
            if linked:
                cid = linked[0]
                c.execute("INSERT INTO conversation_creations VALUES(?,?,?,?)",
                    (ws, request.client_request_id, canonical_sha256(request), cid))
                result=load_conversation(store,c,ws,cid)
                store._validate_source_identities(c,ws,result.source_refs)
                return result,False
            refs = refs or mission.source_refs
            if any(ref not in mission.source_refs for ref in refs):
                raise db.Path2StateError("source_refs_invalid")
        store._validate_source_identities(c, ws, refs)
        cid = str(uuid4())
        c.execute("INSERT INTO workspace_conversations VALUES(?,?,?,?,1,?,?,NULL)",
            (ws, cid, db._utc_now().isoformat(), request.title, request.mission_id, db._canonical_json(refs)))
        c.execute("INSERT INTO conversation_creations VALUES(?,?,?,?)",
            (ws, request.client_request_id, canonical_sha256(request), cid))
        return load_conversation(store, c, ws, cid), True


def load_message(store, c, ws, cid, message_id):
    db = _db()
    conv = load_conversation(store, c, ws, cid)
    row = c.execute("SELECT body_json,task_message_id FROM conversation_messages "
        "WHERE workspace_id=? AND conversation_id=? AND message_id=?", (ws, cid, message_id)).fetchone()
    if row is None:
        raise db.Path2StateError("message_not_found")
    task_message = None
    refs = []
    if row[1]:
        if not conv.mission_id:
            raise db.WorkspaceStoreUnavailableError()
        task_message = store._task_message(c, ws, conv.mission_id, row[1])
        if task_message.run_id:
            refs = db._run_source_refs(c, ws, conv.mission_id, task_message.run_id)
    if row[0]:
        message = ConversationMessage.model_validate_json(row[0])
        base = message.model_dump(mode="json", exclude={"sha256", "task_message"})
        if (message.workspace_id, message.conversation_id, message.message_id) != (ws,cid,message_id) or canonical_sha256(base) != message.sha256:
            raise db.WorkspaceStoreUnavailableError()
        store._validate_source_identities(c, ws, message.source_refs)
        return message.model_copy(update={"task_message":task_message})
    return ConversationMessage(workspace_id=ws, conversation_id=cid,
        message_id=message_id, created_at=task_message.created_at, role=task_message.role,
        content=task_message.content, references=task_message.references, source_refs=refs,
        sha256=task_message.sha256, task_message=task_message)


def _insert_message(c, ws, cid, content, role, refs, sources, turn_id=None):
    db = _db()
    payload = dict(workspace_id=ws, conversation_id=cid, message_id=str(uuid4()),
        created_at=db._utc_now().isoformat(), role=role, content=content,
        references=[r.model_dump(mode="json") for r in refs],
        source_refs=[r.model_dump(mode="json") for r in sources], turn_id=turn_id)
    candidate = ConversationMessage(**payload, sha256="0"*64)
    message = candidate.model_copy(update={"sha256":canonical_sha256(candidate.model_dump(mode="json",exclude={"sha256","task_message"}))})
    c.execute("INSERT INTO conversation_messages VALUES(?,?,?,?,?,NULL)",
        (ws,cid,message.message_id,message.created_at.isoformat(),db._canonical_json(message)))
    return message


def _history(store, c, conv, request):
    db = _db()
    items = [load_message(store,c,conv.workspace_id,conv.conversation_id,r.message_id) for r in request.history_messages]
    if any(m.sha256 != r.sha256 for m,r in zip(items,request.history_messages)):
        raise db.Path2StateError("state_conflict")
    order = [c.execute("SELECT rowid FROM conversation_messages WHERE workspace_id=? AND conversation_id=? AND message_id=?",
        (conv.workspace_id,conv.conversation_id,m.message_id)).fetchone()[0] for m in items]
    if order != sorted(order):
        raise db.Path2StateError("message_history_invalid")
    for item in items:
        if any(ref not in request.source_refs for ref in item.source_refs):
            raise db.Path2StateError("message_context_scope_mismatch")
    return items


def validate_goal(store, c, conv, goal, source_refs=None):
    if goal is None:
        return
    messages = [load_message(store,c,conv.workspace_id,conv.conversation_id,r.message_id) for r in goal.message_refs]
    if source_refs is not None and any(ref not in source_refs for message in messages for ref in message.source_refs):
        raise _db().Path2StateError("message_context_scope_mismatch")
    if (len({r.message_id for r in goal.message_refs}) != len(goal.message_refs)
        or any(m.role != "user" or m.sha256 != r.sha256 for m,r in zip(messages,goal.message_refs))
        or not goal.text.strip()
        or not (any(goal.text in m.content for m in messages) or goal.text == "\n".join(m.content for m in messages))):
        raise _db().Path2StateError("conversation_goal_invalid")


def _scope(payload, refs):
    """All exact identities carried by mandatory context must remain selected."""
    if isinstance(payload, dict):
        if all(k in payload for k in ("workspace_id","source_id","revision_id","sha256")):
            identity = SourceIdentity(**{k:payload[k] for k in ("workspace_id","source_id","revision_id","sha256")})
            if identity not in refs:
                raise _db().Path2StateError("message_context_scope_mismatch")
        for value in payload.values():
            _scope(value, refs)
    elif isinstance(payload,list):
        for value in payload:
            _scope(value, refs)


def _references(store, c, conv, refs, selected):
    db = _db()
    excerpts=[]
    for ref in refs:
        if ref.kind in {"source_excerpt", "source_column"}:
            identity = ref.source_ref if ref.kind == "source_column" else SourceIdentity(**{
                k:getattr(ref.evidence_ref,k) for k in ("workspace_id","source_id","revision_id","sha256")})
            if identity not in selected:
                raise db.Path2StateError("message_context_scope_mismatch")
            revision, artifact = db._load_source_in_connection(c,conv.workspace_id,identity.revision_id)
            content = db._read_validated_source_file(db._source_path(store.data_dir,revision),revision)
            if ref.kind == "source_excerpt":
                excerpts.append(db.read_source_fragment(revision,content,ref.evidence_ref.locator))
            elif not any(t.table_id == ref.table_id and any(col.name == ref.column_name for col in t.columns) for t in artifact.tables):
                raise db.Path2StateError("source_refs_invalid")
        else:
            draft = db._load_latest_draft(c,conv.workspace_id,conv.mission_id) if conv.mission_id else None
            if draft is None or (draft.draft_id,draft.version,draft.sha256) != (ref.draft_id,ref.draft_version,ref.draft_sha256):
                raise db.Path2StateError("message_reference_stale")
            _scope(draft.model_dump(mode="json"),selected)
            if (ref.kind == "draft_field" and not any(f.field_key == ref.field_key for f in draft.fields)) or (
                ref.kind == "draft_relationship" and not any(r.relationship_key == ref.relationship_key for r in draft.relationships)):
                raise db.Path2StateError("message_reference_stale")
    return excerpts


def load_turn(store, c, ws, cid, tid):
    db = _db()
    load_conversation(store,c,ws,cid)
    row = c.execute("SELECT turn_json FROM discussion_turns WHERE workspace_id=? AND conversation_id=? AND turn_id=?",(ws,cid,tid)).fetchone()
    if not row:
        raise db.Path2StateError("discussion_turn_not_found")
    try:
        turn=DiscussionTurn.model_validate_json(row[0])
        if (turn.workspace_id,turn.conversation_id,turn.turn_id)!=(ws,cid,tid) or turn.request_sha256!=canonical_sha256(turn.request):
            raise ValueError("discussion identity mismatch")
        if db._canonical_json(turn)!=row[0]:
            raise ValueError("discussion serialization mismatch")
        saved=c.execute("SELECT receipt_id,receipt_json FROM discussion_provider_receipts WHERE workspace_id=? AND conversation_id=? AND turn_id=?",(ws,cid,tid)).fetchone()
        if turn.provider_receipt is not None:
            receipt=turn.provider_receipt
            if not saved or saved[0]!=receipt.receipt_id or saved[1]!=db._canonical_json(receipt):
                raise ValueError("discussion receipt missing or different")
            if (receipt.workspace_id,receipt.conversation_id,receipt.turn_id,receipt.request_sha256,receipt.context_sha256,receipt.p0_sha256,receipt.output_schema_sha256,receipt.config)!=(
                ws,cid,tid,turn.request_sha256,turn.context_sha256,turn.p0_sha256,turn.output_schema_sha256,turn.config):
                raise ValueError("discussion receipt identity mismatch")
        elif saved:
            raise ValueError("unbound discussion receipt")
        if turn.status=="succeeded" and (turn.output is None or turn.provider_receipt is None or turn.provider_receipt.status!="succeeded"):
            raise ValueError("successful discussion requires output and receipt")
        if (turn.status in {"queued","running"}) != (turn.finished_at is None):
            raise ValueError("discussion terminal time mismatch")
        if turn.status=="running" and turn.started_at is None:
            raise ValueError("discussion missing claim")
        return turn
    except ValueError as exc:
        raise db.WorkspaceStoreUnavailableError() from exc


def _save_turn(c, turn):
    c.execute("UPDATE discussion_turns SET turn_json=? WHERE workspace_id=? AND conversation_id=? AND turn_id=?",
        (_db()._canonical_json(turn),turn.workspace_id,turn.conversation_id,turn.turn_id))


def submission(store,c,ws,cid,request_id,request=None):
    db=_db()
    conv=load_conversation(store,c,ws,cid)
    row=c.execute("SELECT request_sha256,request_json,input_message_id,turn_id,mission_id,run_id FROM conversation_submissions "
        "WHERE workspace_id=? AND conversation_id=? AND client_request_id=?",(ws,cid,request_id)).fetchone()
    if row is None:
        return None
    saved=ConversationMessageSendRequest.model_validate_json(row[1])
    if canonical_sha256(saved)!=row[0] or saved.client_request_id!=request_id:
        raise db.WorkspaceStoreUnavailableError()
    if request is not None and canonical_sha256(request)!=row[0]:
        raise db.Path2StateError("idempotency_conflict")
    store._validate_source_identities(c,ws,saved.source_refs)
    store._validate_source_identities(c,ws,conv.source_refs)
    turn=load_turn(store,c,ws,cid,row[3]) if row[3] else None
    return ConversationSubmissionReceipt(conversation=conv,input_message=load_message(store,c,ws,cid,row[2]),
        discussion_turn=turn,run=db._load_run(c,ws,row[4],row[5]) if row[5] else None)


def create_submission(store,ws,cid,request,config,p0_hash,output_schema_hash,*,demo_fast=False):
    db=_db()
    store._require_path2_workspace(ws)
    request=ConversationMessageSendRequest.model_validate(request.model_dump())
    with store._write_transaction() as c:
        conv=load_conversation(store,c,ws,cid)
        old=submission(store,c,ws,cid,request.client_request_id,request)
        if old:
            return old,False
        if conv.state_version!=request.expected_state_version:
            raise db.Path2StateError("state_conflict")
        rows=c.execute("SELECT turn_id FROM discussion_turns WHERE workspace_id=? AND conversation_id=? ORDER BY rowid DESC LIMIT 1",(ws,cid)).fetchall()
        if rows:
            latest=load_turn(store,c,ws,cid,rows[0][0])
            if latest.status in {"queued","running"}:
                raise db.WorkspaceStoreBusyError()
            unsent={"provider_not_configured","provider_config_invalid","provider_config_changed","provider_key_missing","agent_start_failed","cancelled_before_send"}
            rejected={"provider_auth_failed","provider_balance_insufficient","provider_rate_limited","provider_request_invalid"}
            receipt=latest.provider_receipt
            if receipt is None and latest.error_code not in unsent:
                raise db.Path2StateError("previous_outcome_unresolved")
            if receipt and receipt.usage_status=="missing" and receipt.request_started is not False and receipt.error_code not in unsent|rejected:
                raise db.Path2StateError("previous_outcome_unresolved")
            if latest.error_code and ("interrupted" in latest.error_code or "unknown" in latest.error_code) and not (
                receipt and (receipt.usage_status=="known" or receipt.request_started is False or receipt.error_code in unsent|rejected)):
                raise db.Path2StateError("previous_outcome_unresolved")
        refs=db._validated_source_identities(ws,request.source_refs)
        store._validate_source_identities(c,ws,refs)
        validate_goal(store,c,conv,request.goal or conv.goal,refs)
        if request.goal is not None and conv.goal is not None and request.goal != conv.goal:
            raise db.Path2StateError("state_conflict")
        history=_history(store,c,conv,request)
        excerpts=_references(store,c,conv,request.references,refs)
        mission=db._load_mission(c,ws,conv.mission_id) if conv.mission_id else None
        draft=db._load_latest_draft(c,ws,conv.mission_id) if conv.mission_id else None
        questions=db._load_clarifications(c,ws,conv.mission_id) if conv.mission_id else []
        if mission and (mission.status in {"completed","cancelled"}):
            raise db.Path2StateError("state_conflict")
        if mission and c.execute("SELECT 1 FROM runs WHERE workspace_id=? AND mission_id=? AND status IN ('queued','running')",(ws,mission.mission_id)).fetchone():
            raise db.WorkspaceStoreBusyError()
        cases=db.r2.cases(store,c,ws,mission.mission_id).items if mission else []
        if mission:
            _scope({"draft":draft.model_dump(mode="json") if draft else None,
                "clarification_cases":[case.model_dump(mode="json") for case in cases]},refs)
        waiting_answers=any(case.review_state!="approved" for case in cases)
        discussion=mission is None or waiting_answers or bool(draft and draft.status=="in_review")
        tid=str(uuid4()) if discussion else None
        message=_insert_message(c,ws,cid,request.content,"user",request.references,refs,tid)
        c.execute("UPDATE workspace_conversations SET source_refs_json=?,state_version=state_version+1 WHERE workspace_id=? AND conversation_id=?",
            (db._canonical_json(refs),ws,cid))
        c.execute("INSERT INTO conversation_submissions VALUES(?,?,?,?,?,?,NULL,NULL,NULL)",
            (ws,cid,request.client_request_id,canonical_sha256(request),db._canonical_json(request),message.message_id))
        if discussion:
            context_conv=load_conversation(store,c,ws,cid)
            profiles=[]
            for ref in refs:
                revision,_=db._load_source_in_connection(c,ws,ref.revision_id)
                content=db._read_validated_source_file(db._source_path(store.data_dir,revision),revision)
                profiles.append(db.build_profile_pack(revision,content))
            ms=DiscussionMissionContext(mission_id=mission.mission_id,state_version=mission.state_version,goal=mission.goal,
                draft=draft,clarifications=questions,clarification_cases=cases) if mission else None
            context=DiscussionContext(conversation=context_conv,input=message,history=history,goal=request.goal or conv.goal,
                source_refs=refs,source_profiles=profiles,excerpts=excerpts,allowed_references=request.references,mission=ms)
            if len(context.model_dump_json().encode())>65536:
                raise db.Path2StateError("message_context_too_large")
            _scope(context.model_dump(mode="json"),refs)
            turn=DiscussionTurn(workspace_id=ws,conversation_id=cid,turn_id=tid,created_at=db._utc_now(),
                input_message_id=message.message_id,request=request,request_sha256=canonical_sha256(request),status="queued",
                context_sha256=canonical_sha256(context),
                config=config,p0_sha256=p0_hash,output_schema_sha256=output_schema_hash,
                mission_id=mission.mission_id if mission else None,mission_state_version=mission.state_version if mission else None)
            c.execute("INSERT INTO discussion_turns VALUES(?,?,?,?,?,?)",(ws,cid,tid,message.message_id,db._canonical_json(turn),db._canonical_json(context)))
            c.execute("UPDATE conversation_submissions SET turn_id=? WHERE workspace_id=? AND conversation_id=? AND client_request_id=?",
                (tid,ws,cid,request.client_request_id))
        else:
            # Only actual TaskMessage references enter the legacy task history.
            # Discussion material cannot silently vanish from an explicit send.
            if any(m.task_message is None for m in history):
                raise db.Path2StateError("message_context_scope_mismatch")
            approved=[]
            if cases:
                from contextox.models import ApprovedAnswerRef, DraftIdentity
                approved=[ApprovedAnswerRef(origin_run_id=item.request.run_id,clarification_id=item.request.clarification_id,
                    answer_version=item.latest_answer.version,answer_sha256=item.latest_answer.sha256,approval_id=item.latest_approval.approval_id)
                    for item in cases]
                approved.sort(key=lambda item:(item.origin_run_id,item.clarification_id))
            task_request=TaskMessageSendRequest(kind="message",client_request_id=request.client_request_id,
                expected_state_version=mission.state_version,content=request.content,references=request.references,
                history_messages=[MessageHistoryRef(message_id=m.task_message.message_id,sha256=m.task_message.sha256) for m in history],
                source_refs=refs,provider_send_confirmed=True,regenerate_from_run_id=request.regenerate_from_run_id,
                approved_answers=approved,expected_draft=DraftIdentity(draft_id=draft.draft_id,version=draft.version,sha256=draft.sha256) if approved and draft else None)
            result,_=store._send_task_message_in_connection(c,ws,mission.mission_id,task_request,demo_fast=demo_fast)
            bind_run(c,ws,cid,request.client_request_id,message.message_id,result)
        return submission(store,c,ws,cid,request.client_request_id),True


def bind_run(c,ws,cid,request_id,message_id,result):
    c.execute("UPDATE conversation_submissions SET mission_id=?,run_id=? WHERE workspace_id=? AND conversation_id=? AND client_request_id=?",
        (result.run.mission_id,result.run.run_id,ws,cid,request_id))
    c.execute("UPDATE conversation_messages SET task_message_id=? WHERE workspace_id=? AND conversation_id=? AND message_id=?",
        (result.input_message.message_id,ws,cid,message_id))


def context(store,c,ws,cid,tid):
    db=_db()
    turn=load_turn(store,c,ws,cid,tid)
    store._validate_source_identities(c,ws,turn.request.source_refs)
    row=c.execute("SELECT context_json FROM discussion_turns WHERE workspace_id=? AND conversation_id=? AND turn_id=?",(ws,cid,tid)).fetchone()
    result=DiscussionContext.model_validate_json(row[0])
    if (result.conversation.workspace_id,result.conversation.conversation_id,result.input.message_id)!=(ws,cid,turn.input_message_id):
        raise db.WorkspaceStoreUnavailableError()
    if result.source_refs!=turn.request.source_refs:
        raise db.WorkspaceStoreUnavailableError()
    if turn.context_sha256 is None or canonical_sha256(result)!=turn.context_sha256 or db._canonical_json(result)!=row[0]:
        raise db.WorkspaceStoreUnavailableError()
    original=load_message(store,c,ws,cid,turn.input_message_id)
    if original.model_copy(update={"task_message":None})!=result.input:
        raise db.WorkspaceStoreUnavailableError()
    if [MessageHistoryRef(message_id=m.message_id,sha256=m.sha256) for m in result.history]!=turn.request.history_messages:
        raise db.WorkspaceStoreUnavailableError()
    for message in result.history:
        current=load_message(store,c,ws,cid,message.message_id)
        if current.sha256!=message.sha256:
            raise db.WorkspaceStoreUnavailableError()
    validate_goal(store,c,result.conversation,result.goal,turn.request.source_refs)
    _scope(result.model_dump(mode="json"),turn.request.source_refs)
    return result


def claim(store,ws,cid,tid):
    with store._write_transaction() as c:
        turn=load_turn(store,c,ws,cid,tid)
        if turn.status!="queued":
            raise _db().Path2StateError("state_conflict")
        context(store,c,ws,cid,tid)
        turn=turn.model_copy(update={"status":"running","started_at":_db()._utc_now()})
        _save_turn(c,turn)
        return turn


def _receipt(c,turn,receipt):
    db=_db()
    receipt=DiscussionProviderReceipt.model_validate(receipt.model_dump(mode="json"))
    if (receipt.workspace_id,receipt.conversation_id,receipt.turn_id,receipt.request_sha256,receipt.context_sha256,receipt.p0_sha256,receipt.output_schema_sha256,receipt.config)!=(
        turn.workspace_id,turn.conversation_id,turn.turn_id,turn.request_sha256,turn.context_sha256,turn.p0_sha256,turn.output_schema_sha256,turn.config):
        raise db.Path2StateError("provider_receipt_invalid")
    row=c.execute("SELECT receipt_json FROM discussion_provider_receipts WHERE workspace_id=? AND conversation_id=? AND turn_id=?",
        (turn.workspace_id,turn.conversation_id,turn.turn_id)).fetchone()
    if row and row[0]!=db._canonical_json(receipt):
        raise db.Path2StateError("provider_receipt_conflict")
    if not row:
        c.execute("INSERT INTO discussion_provider_receipts VALUES(?,?,?,?,?)",(receipt.workspace_id,receipt.conversation_id,
            receipt.turn_id,receipt.receipt_id,db._canonical_json(receipt)))
    return receipt


def finish(store,ws,cid,tid,output,receipt):
    db=_db()
    output=DiscussionOutput.model_validate(output.model_dump())
    with store._write_transaction() as c:
        turn=load_turn(store,c,ws,cid,tid)
        if turn.status=="succeeded":
            if turn.output!=output or turn.provider_receipt!=receipt:
                raise db.Path2StateError("state_conflict")
            return turn
        if turn.status!="running" or receipt.status!="succeeded":
            raise db.Path2StateError("state_conflict")
        conv=load_conversation(store,c,ws,cid)
        frozen=context(store,c,ws,cid,tid)
        if conv.state_version!=turn.request.expected_state_version+1:
            raise db.Path2StateError("state_conflict")
        if turn.mission_id and db._load_mission(c,ws,turn.mission_id).state_version!=turn.mission_state_version:
            raise db.Path2StateError("state_conflict")
        if any(ref not in frozen.allowed_references for ref in output.references):
            raise db.Path2StateError("message_context_scope_mismatch")
        if turn.mission_id and output.next_action=="start_task":
            raise db.Path2StateError("task_waiting_for_review")
        validate_goal(store,c,conv,output.goal,turn.request.source_refs)
        if output.goal:
            allowed_ids={m.message_id for m in [frozen.input,*frozen.history]}
            if frozen.goal:
                allowed_ids.update(r.message_id for r in frozen.goal.message_refs)
            if any(r.message_id not in allowed_ids for r in output.goal.message_refs):
                raise db.Path2StateError("conversation_goal_invalid")
        if output.answer_suggestions:
            cases=db.r2.cases(store,c,ws,turn.mission_id).items if turn.mission_id else []
            for suggestion in output.answer_suggestions:
                case=next((item for item in cases if (item.request.run_id,item.request.clarification_id,item.request_sha256)==
                    (suggestion.origin_run_id,suggestion.clarification_id,suggestion.request_sha256)),None)
                if case is None or suggestion.question_index>=len(case.request.questions):
                    raise db.Path2StateError("clarification_answer_stale")
                _scope(suggestion.model_dump(mode="json"),turn.request.source_refs)
                permitted=[]
                def collect(value):
                    if isinstance(value,dict):
                        if all(key in value for key in ("workspace_id","source_id","revision_id","sha256","locator")):
                            permitted.append(value)
                        for child in value.values():collect(child)
                    elif isinstance(value,list):
                        for child in value:collect(child)
                collect(frozen.model_dump(mode="json"))
                if any(ref.model_dump(mode="json") not in permitted for ref in suggestion.evidence_refs):
                    raise db.Path2StateError("message_context_scope_mismatch")
                current_draft=frozen.mission.draft if frozen.mission else None
                for target in suggestion.targets:
                    objects=(current_draft.fields if target.kind=="field" else current_draft.relationships) if current_draft else []
                    if not any(getattr(item,"field_key" if target.kind=="field" else "relationship_key")==target.key for item in objects):
                        raise db.Path2StateError("clarification_target_stale")
        saved=_receipt(c,turn,receipt)
        updated=turn.model_copy(update={"status":"succeeded","finished_at":db._utc_now(),"output":output,"provider_receipt":saved})
        _save_turn(c,updated)
        _insert_message(c,ws,cid,output.public_reply,"assistant",output.references,turn.request.source_refs,tid)
        c.execute("UPDATE workspace_conversations SET state_version=state_version+1,goal_json=? WHERE workspace_id=? AND conversation_id=?",
            (db._canonical_json(output.goal or conv.goal) if output.goal or conv.goal else None,ws,cid))
        return updated


def fail(store,ws,cid,tid,status,error_code,receipt=None):
    db=_db()
    if status not in {"failed","blocked","cancelled"}:
        raise db.Path2StateError("state_conflict")
    with store._write_transaction() as c:
        turn=load_turn(store,c,ws,cid,tid)
        if turn.status not in {"queued","running"}:
            # A cancellation can precede the supervisor's terminal receipt.
            if receipt is not None and turn.provider_receipt is None and turn.status=="cancelled":
                turn=turn.model_copy(update={"provider_receipt":_receipt(c,turn,receipt)})
                _save_turn(c,turn)
            return turn
        updated=turn.model_copy(update={"status":status,"finished_at":db._utc_now(),"error_code":error_code,
            "provider_receipt":_receipt(c,turn,receipt) if receipt else None})
        _save_turn(c,updated)
        c.execute("UPDATE workspace_conversations SET state_version=state_version+1 WHERE workspace_id=? AND conversation_id=?",(ws,cid))
        return updated


def start_task(store,ws,cid,tid,output,*,demo_fast=False):
    db=_db()
    with store._write_transaction() as c:
        conv=load_conversation(store,c,ws,cid)
        turn=load_turn(store,c,ws,cid,tid)
        if turn.output!=output:
            raise db.Path2StateError("idempotency_conflict")
        if turn.run_id:
            saved=store._message_submission(c,ws,turn.mission_id,turn.request.client_request_id,None)
            if saved is None:
                raise db.WorkspaceStoreUnavailableError()
            return saved
        if (turn.status!="succeeded" or turn.output!=output or output.next_action!="start_task"
            or turn.provider_receipt is None or turn.provider_receipt.usage_status!="known"
            or turn.provider_receipt.status!="succeeded" or turn.handoff_error_code is not None
            or conv.mission_id is not None or conv.state_version!=turn.request.expected_state_version+2):
            raise db.Path2StateError("state_conflict")
        frozen=context(store,c,ws,cid,tid)
        if is_referential_start(turn.request.content):
            goal=frozen.goal
            if output.goal is not None and output.goal!=goal:
                raise db.Path2StateError("conversation_goal_invalid")
        else:
            goal=output.goal
            if goal is None or goal.text!=frozen.input.content or any(r.message_id!=frozen.input.message_id for r in goal.message_refs):
                raise db.Path2StateError("conversation_goal_invalid")
        if goal is None:
            raise db.Path2StateError("conversation_goal_invalid")
        if not is_explicit_work_instruction(turn.request.content):
            raise db.Path2StateError("conversation_execution_not_authorized")
        validate_goal(store,c,conv,goal,turn.request.source_refs)
        store._validate_source_identities(c,ws,turn.request.source_refs)
        if not turn.request.source_refs:
            raise db.Path2StateError("source_refs_invalid")
        input_message=load_message(store,c,ws,cid,turn.input_message_id)
        origin=ConversationMissionOrigin(conversation_id=cid,message_id=input_message.message_id,
            message_sha256=input_message.sha256,client_request_id=turn.request.client_request_id,
            goal=goal,source_refs=turn.request.source_refs)
        mid=str(uuid4())
        c.execute("INSERT INTO missions(workspace_id,mission_id,created_at,state_version,status,title,goal,completion_criteria_json,scope_notes_json,original_attempt_id,conversation_origin_json) "
            "VALUES(?,?,?,1,'active',?,?,?,?,NULL,?)",(ws,mid,db._utc_now().isoformat(),output.title or goal.text[:120],goal.text,
            db._canonical_json(["整理可核对的候选定义，保留证据与未决事项"]),db._canonical_json([]),db._canonical_json(origin)))
        c.executemany("INSERT INTO mission_sources VALUES(?,?,?,?,?,?)",[(ws,mid,i,ref.source_id,ref.revision_id,ref.sha256)
            for i,ref in enumerate(turn.request.source_refs)])
        c.execute("UPDATE workspace_conversations SET mission_id=?,goal_json=?,title=?,state_version=state_version+1 WHERE workspace_id=? AND conversation_id=?",
            (mid,db._canonical_json(goal),output.title or goal.text[:120],ws,cid))
        request=TaskMessageSendRequest(kind="message",client_request_id=turn.request.client_request_id,expected_state_version=1,
            content=turn.request.content,references=turn.request.references,history_messages=[],source_refs=turn.request.source_refs,provider_send_confirmed=True)
        result,_=store._send_task_message_in_connection(c,ws,mid,request,demo_fast=demo_fast)
        bind_run(c,ws,cid,turn.request.client_request_id,turn.input_message_id,result)
        _save_turn(c,turn.model_copy(update={"mission_id":mid,"run_id":result.run.run_id}))
        return result


def is_explicit_work_instruction(content):
    """Conservative supplementary gate over the current user message, never sources."""
    text=content.strip()
    if not text or text.startswith(('"',"'","“","‘",">","```","{")):
        return False
    if re.search(r"假设|如果|要不要|是否|先别|暂不|不想|不需要|不用|不要|别执行|仅讨论|先讨论|只是问|只想|仅查看|只看|建议|怎么|如何|想想|聊聊|what if|hypothetic|do not|don't|should (?:i|we)|whether",text,re.I):
        return False
    return bool(re.match(r"(?:我想|我要|我需要|请|帮我|麻烦|现在|开始|执行|继续|就按|按).*(?:分析|统计|汇总|计算|整理|核对|检查|梳理|建立|生成|开始|执行|继续)|(?:开始|执行|继续)(?:吧|工作|分析|整理)?[。！!. ]*$|(?:please |now |i (?:want|need) to )?(?:start|proceed|analyze|analyse|organize|compare|check|build|generate|summarize|calculate|go ahead)\b",text,re.I))


def is_referential_start(content):
    return bool(re.fullmatch(r"(?:请|现在)?(?:开始|执行|继续)(?:吧|工作|分析|整理)?[。！!. ]*|(?:就)?按(?:这个|上面|刚才)(?:的)?(?:目标|方案)?(?:开始|执行|继续)[。！!. ]*|(?:please )?(?:start|proceed|go ahead)[.! ]*",content.strip(),re.I))


def fail_start(store,ws,cid,tid,error_code):
    with store._write_transaction() as c:
        turn=load_turn(store,c,ws,cid,tid)
        turn=turn.model_copy(update={"handoff_error_code":error_code})
        _save_turn(c,turn)
        return turn


def recover(store):
    db=_db()
    with store._write_transaction() as c:
        if not enabled(c):
            return 0
        count=0
        for ws,cid,tid in c.execute("SELECT workspace_id,conversation_id,turn_id FROM discussion_turns").fetchall():
            turn=load_turn(store,c,ws,cid,tid)
            if turn.status in {"queued","running"}:
                _save_turn(c,turn.model_copy(update={"status":"failed","finished_at":db._utc_now(),
                    "error_code":"interrupted_without_receipt"}))
                c.execute("UPDATE workspace_conversations SET state_version=state_version+1 WHERE workspace_id=? AND conversation_id=?",(ws,cid))
                count+=1
        return count
