"""R2 public Store/Agent seams with synthetic business answers, no real model."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from contextox import agent, store as db
from contextox.clarifications import draft_ref, refs
from contextox.models import (AnswerItem, ClarificationAnswerSaveRequest, ClarificationAnswerApproveRequest,
    UpdateDefinitionDraftCall, CreateClarificationCall, TaskMessageSendRequest, canonical_sha256)
from test_agent import _persisted_mission, _start_request
from test_dialogue import AnswerProvider

DIMS=("meaning","value_type","grain","rule","time_basis","null_handling")


def field(key="window", **changes):
    data={"field_key":key,"name":key,**{d:None for d in DIMS},"source_columns":[],
        "evidence_status":"unknown","source_refs":[],"unknowns":[{"property_path":d,"reason":"Requires business answer"} for d in DIMS]}
    data.update(changes)
    data["unknowns"]=[{"property_path":d,"reason":"Requires business answer"} for d in DIMS if data[d] is None]
    return data


def strip_r2_fixture(connection):
    """Only synthetic test databases: reconstruct exact historical schema."""
    if connection.execute("PRAGMA user_version").fetchone()[0] == 5:
        for name,_ in reversed(db.r2.TABLES):
            connection.execute("DROP TABLE " + name)
        connection.execute("ALTER TABLE context_manifests DROP COLUMN approved_answer_refs_json")
        connection.execute("PRAGMA user_version=4")


class ClarificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="contextox-r2-",dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.store=db.WorkspaceStore.open(self.temp.name)
        self.ws,self.mission,self.sources=_persisted_mission(self.store,with_sources=getattr(self,"with_sources",False))
        self.mid=self.mission.mission_id
        run=self.store.start_run(self.ws,self.mid,_start_request(self.mission,self.sources))
        self.origin=run.run_id
        self.store.mark_run_running(self.ws,self.mid,run.run_id)
        result=self.update(run.run_id,[field()])
        q={"question":"What is the business window?","why_needed":"Define the rule", "expected_answer_type":"text",
           "suggested_owner_role":"business owner","related_definition_paths":["fields.window.rule"],
           "evidence_requested":["approved policy"],"examples_or_options":[],"blocking_impact":"blocking","source_refs":[]}
        call=CreateClarificationCall(call_id="clarify",name="create_clarification",arguments={
            "draft_version":result.output.version,"draft_sha256":result.output.sha256,"questions":[q,{**q,"question":"How are missing values handled?"}]})
        self.q=self.store.execute_run_tool(self.ws,self.mid,run.run_id,call).output

    def update(self,run_id,fields):
        d=self.store.get_run_snapshot(self.ws,self.mid,run_id).draft
        call=UpdateDefinitionDraftCall(call_id=str(uuid4()),name="update_definition_draft",arguments={
            "expected_version":d.version if d else 0,"expected_sha256":d.sha256 if d else None,
            "fields":fields,"relationships":[],"unresolved_items":[]})
        return self.store.execute_run_tool(self.ws,self.mid,run_id,call)

    def save_payload(self,**changes):
        snap=self.store.get_mission_snapshot(self.ws,self.mid)
        page=self.store.list_clarification_cases(self.ws,self.mid)
        latest=page.items[0].latest_answer
        data=dict(client_request_id=str(uuid4()),expected_latest_version=latest.version if latest else 0,
            expected_state_version=snap.mission.state_version,request_sha256=canonical_sha256(self.q),review_draft=draft_ref(snap.draft),source_refs=self.sources,
            items=[dict(question_index=0,disposition="answered",answer="30 days",respondent="Business owner",basis="Current policy",evidence_refs=[],targets=[],blocker=None),
                   dict(question_index=1,disposition="unknown",answer=None,respondent="Business owner",basis="Policy not yet confirmed",evidence_refs=[],
                        targets=[dict(kind="field",key="window",property="null_handling")],blocker=dict(resolver="Data owner",evidence_needed="Missing value policy",next_action="Request signed policy"))])
        data.update(changes)
        return ClarificationAnswerSaveRequest(**data)

    def save(self,payload=None):
        return self.store.save_clarification_answer(self.ws,self.mid,self.origin,self.q.clarification_id,payload or self.save_payload())

    def approve(self,answer):
        version=self.store.get_mission_snapshot(self.ws,self.mid).mission.state_version
        return self.store.approve_clarification_answer(self.ws,self.mid,self.origin,self.q.clarification_id,answer.version,
            ClarificationAnswerApproveRequest(client_request_id=str(uuid4()),expected_state_version=version,expected_answer_sha256=answer.sha256))

    def send(self):
        page=self.store.list_clarification_cases(self.ws,self.mid)
        approved=[db.r2.ApprovedAnswerSnapshot(request=c.request,answer=c.latest_answer,approval=c.latest_approval) for c in page.items]
        snap=self.store.get_mission_snapshot(self.ws,self.mid)
        req=TaskMessageSendRequest(kind="message",client_request_id=str(uuid4()),expected_state_version=snap.mission.state_version,
            content="Apply the approved answer; preserve unknowns",references=[],history_messages=[],source_refs=self.sources,provider_send_confirmed=True,
            approved_answers=refs(approved),expected_draft=draft_ref(snap.draft))
        return self.store.send_task_message(self.ws,self.mid,req),req

    def test_whole_approval_fixed_snapshot_and_followup(self):
        payload=self.save_payload()
        saved,created=self.save(payload)
        self.assertTrue(created)
        replay,created=self.save(payload)
        self.assertFalse(created)
        self.assertEqual(saved.answer,replay.answer)
        self.assertEqual(saved.mission_state_version,replay.mission_state_version)
        self.approve(saved.answer)
        (receipt,_),req=self.send()
        self.assertEqual(receipt.run.approved_answers[0].answer.version,1)
        self.assertEqual(self.store.message_submission(self.ws,self.mid,req.client_request_id).run.run_id,receipt.run.run_id)
        provider=AnswerProvider()
        with patch.object(agent,"get_provider",return_value=provider):
            agent.run_agent(self.store,self.ws,self.mid,receipt.run.run_id,Event())
        finished=self.store.get_run_snapshot(self.ws,self.mid,receipt.run.run_id)
        self.assertEqual(finished.status,"partial",finished.error_code)
        packet=json.loads(provider.packets[-1][1]["content"])
        self.assertEqual(packet["approved_answers"][0]["items"][1]["disposition"],"unknown")
        impact=self.store.get_answer_impact(self.ws,self.mid,receipt.run.run_id)
        self.assertEqual(impact.result_state,"no_result")
        self.assertEqual(len(impact.remaining_blockers),1)
        # New answer invalidates current approval, but never changes old Run input.
        second,_=self.save()
        self.assertEqual(second.answer.version,2)
        self.assertEqual(self.store.list_clarification_cases(self.ws,self.mid).items[0].review_state,"awaiting_approval")
        self.assertEqual(self.store.get_run_snapshot(self.ws,self.mid,receipt.run.run_id).approved_answers[0].answer.version,1)
        with self.assertRaises(db.Path2StateError):
            self.store.send_task_message(self.ws,self.mid,req.model_copy(update={"client_request_id":str(uuid4()),"expected_state_version":second.mission_state_version}))

    def test_incomplete_conflict_and_unknown_target_guard(self):
        payload=self.save_payload()
        with self.assertRaises(db.Path2StateError):
            self.save(payload.model_copy(update={"items":payload.items[:1]}))
        self.assertIsNone(self.store.list_clarification_cases(self.ws,self.mid).items[0].latest_answer)
        saved,_=self.save(payload)
        with self.assertRaises(db.Path2StateError) as error:
            self.save(payload.model_copy(update={"request_sha256":"f"*64}))
        self.assertEqual(error.exception.code,"idempotency_conflict")
        self.approve(saved.answer)
        (receipt,_),_=self.send()
        self.store.mark_run_running(self.ws,self.mid,receipt.run.run_id)
        with self.assertRaises(db.Path2StateError) as error:
            self.update(receipt.run.run_id,[field(null_handling="drop rows")])
        self.assertEqual(error.exception.code,"approved_unknown_must_remain_unresolved")
        self.update(receipt.run.run_id,[field(rule="30 days",evidence_status="candidate"),field("unrelated")])
        impact=self.store.get_answer_impact(self.ws,self.mid,receipt.run.run_id)
        self.assertEqual(impact.result_state,"partial")
        self.assertEqual(len(impact.changes),2)
        # Bound null-handling did not change when only the rule changed.
        self.assertTrue(all(not change.question_refs for change in impact.changes))
        self.store.fail_run(self.ws,self.mid,receipt.run.run_id,"failed","synthetic_stop")
        self.assertEqual(self.store.list_clarification_cases(self.ws,self.mid).items[0].review_state,"approved")
        # An unrelated candidate change does not invalidate approved business facts.
        (followup,_),_=self.send()
        self.assertEqual(followup.run.approved_answers[0].answer.version,1)
        self.assertEqual(self.store.get_answer_impact(self.ws,self.mid,receipt.run.run_id).after_draft.fields[-1].field_key,"unrelated")

    def test_optional_targets_and_workspace_boundary(self):
        payload=self.save_payload()
        payload.items[1].targets=[]
        saved,_=self.save(payload)
        self.approve(saved.answer)
        self.assertEqual(self.store.list_clarification_cases(self.ws,self.mid).items[0].review_state,"approved")
        other=self.store.create_workspace("other")
        for read in (lambda:self.store.list_clarification_cases(other.workspace_id,self.mid),
            lambda:self.store.get_clarification_answer(other.workspace_id,self.mid,self.origin,self.q.clarification_id,1)):
            with self.assertRaises(db.WorkspaceStoreError):read()

    def test_v4_migration_backup_rollback_and_history(self):
        with closing(sqlite3.connect(self.store.db_path)) as c,c:
            strip_r2_fixture(c)
        before=self.store.db_path.read_bytes()
        original=db._validate_connection_schema
        def reject(c):
            if db.r2.enabled(c):raise db.WorkspaceSchemaUnsupportedError()
            original(c)
        with patch.object(db,"_validate_connection_schema",side_effect=reject), self.assertRaises(db.WorkspaceSchemaUnsupportedError):
            self.store.migrate_clarification_answers()
        self.assertEqual(self.store.db_path.read_bytes(),before)
        backup=self.store.migrate_clarification_answers()
        with closing(sqlite3.connect(backup/db.DB_FILENAME)) as c, closing(sqlite3.connect(self.store.db_path)) as current:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],4)
            self.assertTrue(db._schema_matches(c,4,db._EXPECTED_V4_TABLES,db._EXPECTED_V3_INDEXES))
            self.assertEqual(c.execute("SELECT * FROM clarification_requests").fetchall(),current.execute("SELECT * FROM clarification_requests").fetchall())
        self.assertIsNone(self.store.migrate_clarification_answers())
        self.assertEqual(self.store.get_run_snapshot(self.ws,self.mid,self.origin).clarifications,[self.q])
        self.assertTrue(self.store.list_clarification_cases(self.ws,self.mid).items)

    def test_api_save_approve_reconcile_without_provider(self):
        import asyncio
        from contextox.api import create_app
        from test_api import _asgi_request
        app=create_app(data_dir=Path(self.temp.name))
        base=f"/api/workspaces/{self.ws}/missions/{self.mid}"
        url=f"{base}/clarifications/{self.origin}/{self.q.clarification_id}/answers"
        def call(method,url,payload=None):
            code,body=asyncio.run(_asgi_request(app,method,url,payload.model_dump_json().encode() if payload else b""))
            return code,json.loads(body)
        with patch.object(agent,"get_provider",side_effect=AssertionError("answer actions must not call a model")):
            payload=self.save_payload()
            code,result=call("POST",url,payload)
            self.assertEqual(code,201,result)
            self.assertEqual(call("POST",url,payload)[0],200)
            approved=ClarificationAnswerApproveRequest(client_request_id=str(uuid4()),expected_state_version=result["mission_state_version"],expected_answer_sha256=result["answer"]["sha256"])
            code,receipt=call("POST",url+"/1/approve",approved)
            self.assertEqual(code,201,receipt)
            self.assertEqual(call("POST",url+"/1/approve",approved)[0],200)
            self.assertEqual(call("GET",base+"/clarification-submissions/"+approved.client_request_id)[1]["approval"],receipt["approval"])
            self.assertEqual(call("GET",url+"/1")[1]["answer"],result["answer"])
            self.assertEqual(call("GET",base+"/clarification-submissions/"+str(uuid4()))[0],404)
            self.assertEqual(call("POST",url,payload.model_copy(update={"expected_latest_version":10}))[0],409)
            # Another explicit duplicate approval reads the same approval without bumping state.
            again=approved.model_copy(update={"client_request_id":str(uuid4()),"expected_state_version":receipt["mission_state_version"]})
            code,replay=call("POST",url+"/1/approve",again)
            self.assertEqual(code,200,replay)
            self.assertEqual(replay["mission_state_version"],receipt["mission_state_version"])
            self.assertEqual(replay["approval"],receipt["approval"])

    def test_active_run_and_corrupt_binding_cannot_change_answers(self):
        saved,_=self.save()
        self.approve(saved.answer)
        (receipt,_),req=self.send()
        with self.assertRaises(db.Path2StateError) as error:self.save()
        self.assertEqual(error.exception.code,"run_already_active")
        with closing(sqlite3.connect(self.store.db_path)) as c,c:
            c.execute("UPDATE run_approved_answers SET approval_id=? WHERE workspace_id=? AND mission_id=? AND run_id=?",(str(uuid4()),self.ws,self.mid,receipt.run.run_id))
        with self.assertRaises(db.WorkspaceStoreError):self.store.get_run_snapshot(self.ws,self.mid,receipt.run.run_id)

    def test_unknown_rejection_uses_bounded_no_effect_feedback(self):
        saved,_=self.save()
        self.approve(saved.answer)
        (receipt,_),_=self.send()
        self.store.mark_run_running(self.ws,self.mid,receipt.run.run_id)
        d=receipt.run.draft
        call=UpdateDefinitionDraftCall(call_id="guess",name="update_definition_draft",arguments={
            "expected_version":d.version,"expected_sha256":d.sha256,"fields":[field(null_handling="invented")],"relationships":[],"unresolved_items":[]})
        from contextox.model_tools import CandidateRejected
        with self.assertRaises(CandidateRejected) as error:
            agent._validate_model_batch(self.store,self.ws,self.mid,receipt.run.run_id,[call])
        self.assertEqual(error.exception.code,"approved_unknown_must_remain_unresolved")
        self.assertEqual(self.store.get_run_snapshot(self.ws,self.mid,receipt.run.run_id).draft,d)

    def test_unknown_protects_merged_candidate_not_only_incoming_fields(self):
        # Synthetic prior draft has an unapproved known-looking value before the human answers unknown.
        with closing(sqlite3.connect(self.store.db_path)) as c,c:
            c.execute("UPDATE missions SET status='active' WHERE workspace_id=? AND mission_id=?",(self.ws,self.mid))
        # Use an ordinary Store write on a synthetic running Run to establish the prior candidate.
        with closing(sqlite3.connect(self.store.db_path)) as c,c:
            c.execute("UPDATE runs SET status='running',finished_at=NULL WHERE workspace_id=? AND mission_id=? AND run_id=?",(self.ws,self.mid,self.origin))
            c.execute("DELETE FROM terminal_receipts WHERE workspace_id=? AND mission_id=? AND run_id=?",(self.ws,self.mid,self.origin))
        self.update(self.origin,[field(null_handling="unconfirmed old guess",evidence_status="candidate")])
        self.store.fail_run(self.ws,self.mid,self.origin,"failed","synthetic_prior_candidate")
        saved,_=self.save()
        self.approve(saved.answer)
        (receipt,_),_=self.send()
        self.store.mark_run_running(self.ws,self.mid,receipt.run.run_id)
        before=self.store.get_run_snapshot(self.ws,self.mid,receipt.run.run_id).draft
        with self.assertRaises(db.Path2StateError) as error:
            self.update(receipt.run.run_id,[field("unrelated")])
        self.assertEqual(error.exception.code,"approved_unknown_must_remain_unresolved")
        self.assertEqual(self.store.get_run_snapshot(self.ws,self.mid,receipt.run.run_id).draft,before)
        self.update(receipt.run.run_id,[field(),field("unrelated")])
        self.assertIsNone(self.store.get_run_snapshot(self.ws,self.mid,receipt.run.run_id).draft.fields[0].null_handling)

    def test_all_answer_reads_reject_missing_changed_or_revoked_sources(self):
        for failure in ("missing","changed","revoked"):
            with self.subTest(failure=failure):
                case=ClarificationTests();case.with_sources=True;case.setUp()
                self.addCleanup(case.doCleanups)
                saved,_=case.save();case.approve(saved.answer)
                (receipt,_),req=case.send()
                # Cancel before dispatch, preserving the immutable historical input.
                case.store.cancel_run(case.ws,case.mid,receipt.run.run_id)
                revision=next(r for r in case.store.list_source_revisions(case.ws) if r.revision_id==case.sources[0].revision_id)
                source_path=db._source_path(case.store.data_dir,revision)
                if failure=="missing":source_path.unlink()
                elif failure=="changed":source_path.write_bytes(b"changed synthetic bytes")
                else:
                    with closing(sqlite3.connect(case.store.db_path)) as c,c:
                        c.execute("UPDATE source_revisions SET permission_status='denied' WHERE workspace_id=? AND revision_id=?",(case.ws,revision.revision_id))
                for read in (
                    lambda:case.store.get_run_snapshot(case.ws,case.mid,receipt.run.run_id),
                    lambda:case.store.get_mission_snapshot(case.ws,case.mid),
                    lambda:case.store.get_clarification_answer(case.ws,case.mid,case.origin,case.q.clarification_id,1),
                    lambda:case.store.clarification_submission(case.ws,case.mid,saved.client_request_id),
                    lambda:case.store.message_submission(case.ws,case.mid,req.client_request_id),
                    lambda:case.store.get_answer_impact(case.ws,case.mid,receipt.run.run_id),
                    lambda:case.store.list_clarification_cases(case.ws,case.mid)):
                    with self.assertRaises(db.WorkspaceStoreError):read()
