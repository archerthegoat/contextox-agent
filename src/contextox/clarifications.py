"""Task-local immutable business answers; no Provider or external actions."""
from __future__ import annotations

import json
import sqlite3
from uuid import uuid4
from pydantic import ValidationError
from contextox.models import (
    AnswerImpact, AnswerImpactChange, AnswerQuestionRef, ApprovedAnswerRef,
    ApprovedAnswerSnapshot, ClarificationAnswerApproval, ClarificationAnswerRead,
    ClarificationAnswerVersion, ClarificationCase, ClarificationCasePage,
    ClarificationSubmissionReceipt, DraftIdentity, RemainingAnswerBlocker,
    ContextManifestInput, ContextPacketManifest, canonical_sha256,
)

TABLES = (
("clarification_answer_versions", """CREATE TABLE clarification_answer_versions (
 workspace_id TEXT NOT NULL, mission_id TEXT NOT NULL, origin_run_id TEXT NOT NULL,
 clarification_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0),
 sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
 PRIMARY KEY(workspace_id, mission_id, origin_run_id, clarification_id, version),
 UNIQUE(workspace_id, mission_id, origin_run_id, clarification_id, version, sha256),
 FOREIGN KEY(workspace_id, mission_id, origin_run_id, clarification_id)
 REFERENCES clarification_requests(workspace_id, mission_id, run_id, clarification_id)
)"""),
("clarification_answer_approvals", """CREATE TABLE clarification_answer_approvals (
 workspace_id TEXT NOT NULL, mission_id TEXT NOT NULL, origin_run_id TEXT NOT NULL,
 clarification_id TEXT NOT NULL, answer_version INTEGER NOT NULL,
 answer_sha256 TEXT NOT NULL, approval_id TEXT NOT NULL,
 approved_by TEXT NOT NULL CHECK(approved_by = 'local-owner'), approved_at TEXT NOT NULL,
 PRIMARY KEY(workspace_id, mission_id, origin_run_id, clarification_id, answer_version),
 UNIQUE(workspace_id, mission_id, approval_id),
 FOREIGN KEY(workspace_id, mission_id, origin_run_id, clarification_id, answer_version, answer_sha256)
 REFERENCES clarification_answer_versions(workspace_id, mission_id, origin_run_id, clarification_id, version, sha256)
)"""),
("clarification_submissions", """CREATE TABLE clarification_submissions (
 workspace_id TEXT NOT NULL, mission_id TEXT NOT NULL, client_request_id TEXT NOT NULL,
 operation TEXT NOT NULL CHECK(operation IN ('save', 'approve')), request_sha256 TEXT NOT NULL,
 origin_run_id TEXT NOT NULL, clarification_id TEXT NOT NULL, answer_version INTEGER NOT NULL,
 PRIMARY KEY(workspace_id, mission_id, client_request_id),
 FOREIGN KEY(workspace_id, mission_id, origin_run_id, clarification_id, answer_version)
 REFERENCES clarification_answer_versions(workspace_id, mission_id, origin_run_id, clarification_id, version)
)"""),
("run_approved_answers", """CREATE TABLE run_approved_answers (
 workspace_id TEXT NOT NULL, mission_id TEXT NOT NULL, run_id TEXT NOT NULL,
 origin_run_id TEXT NOT NULL, clarification_id TEXT NOT NULL, answer_version INTEGER NOT NULL,
 answer_sha256 TEXT NOT NULL, approval_id TEXT NOT NULL,
 PRIMARY KEY(workspace_id, mission_id, run_id, origin_run_id, clarification_id),
 FOREIGN KEY(workspace_id, mission_id, run_id) REFERENCES runs(workspace_id, mission_id, run_id),
 FOREIGN KEY(workspace_id, mission_id, origin_run_id, clarification_id, answer_version, answer_sha256)
 REFERENCES clarification_answer_versions(workspace_id, mission_id, origin_run_id, clarification_id, version, sha256),
 FOREIGN KEY(workspace_id, mission_id, approval_id)
 REFERENCES clarification_answer_approvals(workspace_id, mission_id, approval_id)
)"""),
)
MANIFEST_ALTER = "ALTER TABLE context_manifests ADD COLUMN approved_answer_refs_json TEXT NOT NULL DEFAULT '[]'"


def enabled(connection):
    return connection.execute("PRAGMA user_version").fetchone()[0] == 5


def draft_ref(draft):
    return None if draft is None else DraftIdentity(draft_id=draft.draft_id, version=draft.version, sha256=draft.sha256)


def request_in(connection, ws, mid, origin, cid):
    from contextox import store as db
    found = next((q for q in db._load_clarifications(connection, ws, mid, origin) if q.clarification_id == cid), None)
    if found is None:
        raise db.Path2StateError("clarification_not_found")
    return found


def load_version(connection, ws, mid, origin, cid, version):
    from contextox import store as db
    row = connection.execute("SELECT sha256,payload_json FROM clarification_answer_versions "
        "WHERE workspace_id=? AND mission_id=? AND origin_run_id=? AND clarification_id=? AND version=?",
        (ws, mid, origin, cid, version)).fetchone()
    if row is None:
        raise db.Path2StateError("clarification_answer_not_found")
    try:
        a = ClarificationAnswerVersion.model_validate_json(row[1])
        if (a.workspace_id,a.mission_id,a.origin_run_id,a.clarification_id,a.version,a.sha256) != (ws,mid,origin,cid,version,row[0]) or db._canonical_json(a) != row[1]:
            raise ValueError("answer row mismatch")
        q = request_in(connection, ws, mid, origin, cid)
        if a.request_sha256 != canonical_sha256(q) or len(a.items) != len(q.questions):
            raise ValueError("question binding mismatch")
        return a
    except (ValueError, TypeError) as exc:
        raise db.WorkspaceStoreUnavailableError() from exc


def load_approval(connection, answer):
    from contextox import store as db
    row = connection.execute("SELECT approval_id,approved_by,approved_at,answer_sha256 FROM clarification_answer_approvals "
        "WHERE workspace_id=? AND mission_id=? AND origin_run_id=? AND clarification_id=? AND answer_version=?",
        (answer.workspace_id,answer.mission_id,answer.origin_run_id,answer.clarification_id,answer.version)).fetchone()
    if row is None:
        return None
    if row[3] != answer.sha256:
        raise db.WorkspaceStoreUnavailableError()
    return ClarificationAnswerApproval(workspace_id=answer.workspace_id,mission_id=answer.mission_id,
        origin_run_id=answer.origin_run_id,clarification_id=answer.clarification_id,
        answer_version=answer.version,answer_sha256=answer.sha256,approval_id=row[0],approved_by=row[1],approved_at=row[2])


def latest_version(connection, ws, mid, origin, cid):
    return connection.execute("SELECT COALESCE(MAX(version),0) FROM clarification_answer_versions "
        "WHERE workspace_id=? AND mission_id=? AND origin_run_id=? AND clarification_id=?", (ws,mid,origin,cid)).fetchone()[0]


def validate_current(store, connection, answer, *, review=False):
    from contextox import store as db
    mission = db._load_mission(connection, answer.workspace_id, answer.mission_id)
    if any(ref not in mission.source_refs for ref in answer.source_refs):
        raise db.Path2StateError("source_refs_invalid")
    store._validate_source_identities(connection, answer.workspace_id, answer.source_refs)
    current = db._load_latest_draft(connection, answer.workspace_id, answer.mission_id)
    if review and draft_ref(current) != answer.review_draft:
        raise db.Path2StateError("clarification_draft_stale")
    for item in answer.items:
        for target in item.targets:
            values = [] if current is None else (current.fields if target.kind == "field" else current.relationships)
            key = "field_key" if target.kind == "field" else "relationship_key"
            if not any(getattr(v, key) == target.key for v in values):
                raise db.Path2StateError("clarification_target_stale")
        for ref in item.evidence_refs:
            if not any(all(getattr(ref,k) == getattr(source,k) for k in ("workspace_id","source_id","revision_id","sha256")) for source in answer.source_refs):
                raise db.Path2StateError("source_refs_invalid")
            revision, _ = db._load_source_in_connection(connection, answer.workspace_id, ref.revision_id)
            body = db._read_validated_source_file(db._source_path(store.data_dir,revision), revision)
            from contextox.sources import read_source_fragment
            read_source_fragment(revision, body, ref.locator)


def cases(store, connection, ws, mid):
    from contextox import store as db
    mission = db._load_mission(connection, ws, mid)
    requests = db._load_clarifications(connection, ws, mid)
    if len(requests) > 50:
        raise db.Path2StateError("clarification_scope_too_large")
    items = []
    for q in requests:
        version = latest_version(connection, ws, mid, q.run_id, q.clarification_id) if enabled(connection) else 0
        answer = load_version(connection,ws,mid,q.run_id,q.clarification_id,version) if version else None
        approval = load_approval(connection, answer) if answer else None
        state = "approved" if approval else "awaiting_approval" if answer else "awaiting_answer"
        if answer:
            try:
                validate_current(store, connection, answer, review=approval is None)
            except db.Path2StateError:
                state = "stale"
        items.append(ClarificationCase(request=q,request_sha256=canonical_sha256(q),
            latest_answer=answer,latest_approval=approval,review_state=state))
    return ClarificationCasePage(items=items,mission_state_version=mission.state_version)


def submission(store, connection, ws, mid, request_id, *, expected_hash=None):
    from contextox import store as db
    mission = db._load_mission(connection, ws, mid)
    if not enabled(connection):
        return None
    row = connection.execute("SELECT operation,request_sha256,origin_run_id,clarification_id,answer_version "
        "FROM clarification_submissions WHERE workspace_id=? AND mission_id=? AND client_request_id=?", (ws,mid,request_id)).fetchone()
    if row is None:
        return None
    if expected_hash is not None and expected_hash != row[1]:
        raise db.Path2StateError("idempotency_conflict")
    answer = load_version(connection,ws,mid,row[2],row[3],row[4])
    # Historical readback checks access, not current draft/target freshness.
    store._validate_source_identities(connection,ws,answer.source_refs)
    return ClarificationSubmissionReceipt(operation=row[0],client_request_id=request_id,answer=answer,
        approval=load_approval(connection,answer),mission_state_version=mission.state_version)


def mutate(store, ws, mid, origin, cid, payload, *, version=None):
    from contextox import store as db
    store._require_path2_workspace(ws)
    from contextox.models import ClarificationAnswerSaveRequest, ClarificationAnswerApproveRequest
    model = ClarificationAnswerSaveRequest if version is None else ClarificationAnswerApproveRequest
    try:
        payload = model.model_validate(payload.model_dump(mode="json"))
    except (ValueError, AttributeError) as exc:
        raise db.Path2StateError("clarification_answer_invalid") from exc
    operation = "save" if version is None else "approve"
    digest = canonical_sha256({"operation":operation,"origin_run_id":origin,"clarification_id":cid,
        "answer_version":version,"request":payload.model_dump(mode="json")})
    with store._write_transaction() as connection:
        mission = db._load_mission(connection,ws,mid)
        replay = submission(store,connection,ws,mid,payload.client_request_id,expected_hash=digest)
        if replay:
            return replay,False
        if not enabled(connection):
            raise db.Path2StateError("clarification_answers_not_implemented")
        if mission.status in {"completed","cancelled"} or mission.state_version != payload.expected_state_version:
            raise db.Path2StateError("state_conflict")
        if connection.execute("SELECT 1 FROM runs WHERE workspace_id=? AND mission_id=? AND status IN ('queued','running')", (ws,mid)).fetchone():
            raise db.Path2StateError("run_already_active")
        q = request_in(connection,ws,mid,origin,cid)
        latest = latest_version(connection,ws,mid,origin,cid)
        created = True
        if operation == "save":
            if latest != payload.expected_latest_version or canonical_sha256(q) != payload.request_sha256:
                raise db.Path2StateError("state_conflict")
            values = payload.model_dump(mode="json",exclude={"client_request_id","expected_latest_version","expected_state_version"})
            values.update(workspace_id=ws,mission_id=mid,origin_run_id=origin,clarification_id=cid,
                version=latest+1,saved_by="local-owner",created_at=db._utc_now().isoformat())
            values["sha256"] = canonical_sha256({k:v for k,v in values.items() if k != "created_at"})
            try:
                answer = ClarificationAnswerVersion.model_validate(values)
            except ValidationError as exc:
                raise db.Path2StateError("clarification_answer_invalid") from exc
            if len(answer.items) != len(q.questions):
                raise db.Path2StateError("clarification_answer_incomplete")
            if {canonical_sha256(ref) for ref in answer.source_refs} != {canonical_sha256(ref) for ref in mission.source_refs}:
                raise db.Path2StateError("source_refs_invalid")
            validate_current(store,connection,answer,review=True)
            connection.execute("INSERT INTO clarification_answer_versions VALUES (?,?,?,?,?,?,?)",
                (ws,mid,origin,cid,answer.version,answer.sha256,db._canonical_json(answer)))
        else:
            if version != latest:
                raise db.Path2StateError("clarification_answer_stale")
            answer = load_version(connection,ws,mid,origin,cid,version)
            if answer.sha256 != payload.expected_answer_sha256:
                raise db.Path2StateError("state_conflict")
            existing = load_approval(connection,answer)
            validate_current(store,connection,answer,review=existing is None)
            created = existing is None
            if existing is None:
                connection.execute("INSERT INTO clarification_answer_approvals VALUES (?,?,?,?,?,?,?,?,?)",
                    (ws,mid,origin,cid,version,answer.sha256,str(uuid4()),"local-owner",db._utc_now().isoformat()))
        connection.execute("INSERT INTO clarification_submissions VALUES (?,?,?,?,?,?,?,?)",
            (ws,mid,payload.client_request_id,operation,digest,origin,cid,answer.version))
        if created:
            connection.execute("UPDATE missions SET state_version=state_version+1 WHERE workspace_id=? AND mission_id=?", (ws,mid))
        return submission(store,connection,ws,mid,payload.client_request_id),created


def load_run_answers(connection, ws, mid, run_id):
    from contextox import store as db
    if not enabled(connection):
        return []
    rows = connection.execute("SELECT origin_run_id,clarification_id,answer_version,answer_sha256,approval_id "
        "FROM run_approved_answers WHERE workspace_id=? AND mission_id=? AND run_id=? ORDER BY origin_run_id,clarification_id", (ws,mid,run_id)).fetchall()
    if len(rows)>50:
        raise db.WorkspaceStoreUnavailableError()
    result=[]
    for origin,cid,version,sha,aid in rows:
        answer=load_version(connection,ws,mid,origin,cid,version)
        approval=load_approval(connection,answer)
        if approval is None or approval.approval_id != aid or answer.sha256 != sha:
            raise db.WorkspaceStoreUnavailableError()
        for source in answer.source_refs:
            revision, _ = db._load_source_in_connection(connection, ws, source.revision_id)
            if revision.permission_status != "read_allowed" or db._source_identity(revision) != source:
                raise db.Path2StateError("source_permission_denied")
        result.append(ApprovedAnswerSnapshot(request=request_in(connection,ws,mid,origin,cid),answer=answer,approval=approval))
    return result


def refs(answers):
    return [ApprovedAnswerRef(**{k:getattr(a.approval,k) for k in ApprovedAnswerRef.model_fields}) for a in answers]


def validate_send(store, connection, mission, payload):
    from contextox import store as db
    if not payload.approved_answers:
        return
    if not enabled(connection):
        raise db.Path2StateError("clarification_answers_not_implemented")
    page=cases(store,connection,mission.workspace_id,mission.mission_id)
    if not page.items or any(c.review_state != "approved" for c in page.items):
        raise db.Path2StateError("task_waiting_for_review")
    approved = [ApprovedAnswerSnapshot(request=c.request,answer=c.latest_answer,approval=c.latest_approval) for c in page.items]
    approved.sort(key=lambda a:(a.answer.origin_run_id,a.answer.clarification_id))
    if refs(approved) != payload.approved_answers:
        raise db.Path2StateError("clarification_answer_stale")
    if draft_ref(db._load_latest_draft(connection,mission.workspace_id,mission.mission_id)) != payload.expected_draft:
        raise db.Path2StateError("clarification_draft_stale")
    chosen={canonical_sha256(s) for s in payload.source_refs}
    if any({canonical_sha256(s) for s in a.answer.source_refs} != chosen for a in approved):
        raise db.Path2StateError("source_refs_invalid")
    # Budget includes full answer context; reject before inserting a Run or sending.
    if len(db._canonical_json(approved).encode("utf-8")) > 262144:
        raise db.Path2StateError("approved_answer_context_too_large")


def bind_run(connection, ws, mid, run_id, payload):
    if payload.approved_answers:
        connection.executemany("INSERT INTO run_approved_answers VALUES (?,?,?,?,?,?,?,?)",
            [(ws,mid,run_id,r.origin_run_id,r.clarification_id,r.answer_version,r.answer_sha256,r.approval_id) for r in payload.approved_answers])


def protect_unknowns(run, call):
    from contextox import store as db
    if call.name != "update_definition_draft":
        return
    for approved in run.approved_answers:
        for item in approved.answer.items:
            if item.disposition != "unknown":
                continue
            for target in item.targets:
                attr = "fields" if target.kind == "field" else "relationships"
                key="field_key" if target.kind == "field" else "relationship_key"
                merged = {getattr(v,key):v for v in getattr(run.draft,attr,[])}
                merged.update({getattr(v,key):v for v in getattr(call.arguments,attr)})
                for value in merged.values():
                    if getattr(value,key) == target.key and (getattr(value,target.property) is not None or not any(u.property_path == target.property for u in value.unknowns)):
                        raise db.Path2StateError("approved_unknown_must_remain_unresolved")


def answer_impact(store, connection, ws, mid, run_id):
    from contextox import store as db
    run=db._load_run(connection,ws,mid,run_id)
    for a in run.approved_answers:
        store._validate_source_identities(connection,ws,a.answer.source_refs)
    before=None
    row=connection.execute("SELECT draft_id,draft_version,draft_sha256 FROM context_manifests WHERE workspace_id=? AND mission_id=? AND run_id=? AND turn_index=1",(ws,mid,run_id)).fetchone()
    if row and row[0]:
        before=load_draft_version(connection,ws,mid,row[1],row[2])
    own=connection.execute("SELECT public_payload_json FROM run_events WHERE workspace_id=? AND mission_id=? AND run_id=? AND event_type='draft_updated' ORDER BY sequence DESC LIMIT 1",(ws,mid,run_id)).fetchone()
    update = DraftIdentity.model_validate_json(own[0]) if own else None
    after=load_draft_version(connection,ws,mid,update.version,update.sha256) if update else None
    changes=[]
    for kind,attr,key in (("field","fields","field_key"),("relationship","relationships","relationship_key")):
        old={getattr(v,key):v for v in getattr(before,attr,[])}
        new={getattr(v,key):v for v in getattr(after,attr,[])} if after else old
        for k in sorted(old.keys()|new.keys()):
            if old.get(k)==new.get(k):
                continue
            questions=[AnswerQuestionRef(origin_run_id=a.answer.origin_run_id,clarification_id=a.answer.clarification_id,question_index=i.question_index)
                for a in run.approved_answers for i in a.answer.items if any(t.kind==kind and t.key==k and (k not in old or k not in new or getattr(old[k],t.property) != getattr(new[k],t.property)) for t in i.targets)]
            changes.append(AnswerImpactChange(kind=kind,key=k,change="added" if k not in old else "removed" if k not in new else "changed",before=old.get(k),after=new.get(k),question_refs=questions))
    blockers=[RemainingAnswerBlocker(origin_run_id=a.answer.origin_run_id,clarification_id=a.answer.clarification_id,
        question_index=i.question_index,question=a.request.questions[i.question_index].question,blocker=i.blocker)
        for a in run.approved_answers for i in a.answer.items if i.disposition=="unknown"]
    return AnswerImpact(approved_answers=run.approved_answers,before_draft=before,after_draft=after,
        changes=changes,remaining_blockers=blockers,result_state="no_result" if after is None else "available" if run.status in {"partial","waiting_for_human"} else "partial")


def manifest_refs(connection, ws, mid, run_id, manifest_id):
    from contextox import store as db
    from pydantic import TypeAdapter
    if not enabled(connection):
        return []
    row=connection.execute("SELECT approved_answer_refs_json FROM context_manifests WHERE workspace_id=? AND mission_id=? AND run_id=? AND manifest_id=?",(ws,mid,run_id,manifest_id)).fetchone()
    if row is None:
        raise db.WorkspaceStoreUnavailableError()
    values=TypeAdapter(list[ApprovedAnswerRef]).validate_json(row[0])
    if db._canonical_json(values)!=row[0] or values!=refs(load_run_answers(connection,ws,mid,run_id)):
        raise db.WorkspaceStoreUnavailableError()
    return values


def run_before_ref(connection,ws,mid,run_id):
    from contextox import store as db
    row=connection.execute("SELECT draft_id,draft_version,draft_sha256 FROM context_manifests WHERE workspace_id=? AND mission_id=? AND run_id=? AND turn_index=1",(ws,mid,run_id)).fetchone()
    if row is None or row[0] is None:
        raise db.WorkspaceStoreUnavailableError()
    return DraftIdentity(draft_id=row[0],version=row[1],sha256=row[2])


def seed_manifest(connection,ws,mid,run_id,payload,mission):
    from contextox import store as db
    from contextox.models import RunBudget
    d=payload.expected_draft
    data=ContextManifestInput(mission_state_version=mission.state_version+(1 if mission.status in {"blocked","waiting_for_human"} else 0),
        turn_index=1,draft_id=d.draft_id,draft_version=d.version,draft_sha256=d.sha256,
        source_refs=payload.source_refs,clarification_ids=[],tool_receipt_ids=[],budget=RunBudget(max_output_tokens=16384),
        excluded_reasons=["cross_mission_chat_not_loaded","unapproved_memory_not_loaded","unselected_sources_not_loaded"],approved_answer_refs=payload.approved_answers).model_dump(mode="json")
    data.update(workspace_id=ws,mission_id=mid,run_id=run_id,manifest_id=str(uuid4()))
    data["sha256"]=canonical_sha256(data)
    p=ContextPacketManifest.model_validate(data)
    connection.execute("INSERT INTO context_manifests (workspace_id,mission_id,run_id,manifest_id,mission_state_version,turn_index,draft_id,draft_version,draft_sha256,source_refs_json,clarification_ids_json,tool_receipt_ids_json,budget_json,excluded_reasons_json,sha256,approved_answer_refs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ws,mid,run_id,p.manifest_id,p.mission_state_version,1,d.draft_id,d.version,d.sha256,
         db._canonical_json(p.source_refs),"[]","[]",db._canonical_json(p.budget),db._canonical_json(p.excluded_reasons),p.sha256,db._canonical_json(p.approved_answer_refs)))


def load_draft_version(connection,ws,mid,version,sha):
    from contextox import store as db
    row=connection.execute("SELECT workspace_id,mission_id,draft_id,version,sha256,status,semantic_approval,fields_json,relationships_json,unresolved_items_json FROM definition_drafts WHERE workspace_id=? AND mission_id=? AND version=? AND sha256=?",(ws,mid,version,sha)).fetchone()
    if row is None:
        raise db.WorkspaceStoreUnavailableError()
    return db._draft_from_row(row)
