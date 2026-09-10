"""Explicit per-send source changes; immutable origins and approvals stay scoped."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from contextox import agent, conversation_handoff as handoff, store as db
from contextox.models import (
    ConversationCreateRequest, ConversationMessageSendRequest, DiscussionOutput,
    DiscussionProviderReceipt, EvidenceRef, MessageHistoryRef, ReadSourceCall,
    SourceExcerptMessageReference, SourceIdentity, TaskMessageSendRequest,
)
from contextox.handoff_models import ConversationHandoffRequest
from test_agent import _persisted_mission
from test_conversation_store import CFG


class ConversationScopeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="contextox-conversation-scope-",dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.store=db.WorkspaceStore.open(Path(temporary.name))
        self.ws,self.mission,self.initial=_persisted_mission(self.store,with_sources=True)
        self.mid=self.mission.mission_id
        self.extra=self.source()
        self.expanded=[*self.initial,self.extra]

    def source(self,workspace_id=None):
        revision,_=self.store.import_source_revision(workspace_id or self.ws,"extra.csv","text/csv",b"region,amount\nwest,12\n")
        return SourceIdentity(**{key:getattr(revision,key) for key in SourceIdentity.model_fields})

    def attach(self):
        conv,_=self.store.create_conversation(self.ws,ConversationCreateRequest(client_request_id=str(uuid4()),mission_id=self.mid))
        self.cid=conv.conversation_id
        return conv

    def send(self,selected=None,**changes):
        conv=self.store.get_conversation(self.ws,self.cid)
        values=dict(client_request_id=str(uuid4()),expected_state_version=conv.state_version,
            content="请分析本次明确选择的资料",source_refs=selected if selected is not None else self.expanded,
            provider_send_confirmed=True)
        values.update(changes)
        request=ConversationMessageSendRequest(**values)
        return self.store.create_conversation_submission(self.ws,self.cid,request,CFG,"1"*64,"2"*64)[0]

    def finish_discussion(self,result,output=None):
        turn=self.store.claim_discussion_turn(self.ws,self.cid,result.discussion_turn.turn_id)
        receipt=DiscussionProviderReceipt(workspace_id=self.ws,conversation_id=self.cid,turn_id=turn.turn_id,
            receipt_id=str(uuid4()),created_at=datetime.now(timezone.utc),request_sha256=turn.request_sha256,
            context_sha256=turn.context_sha256,p0_sha256=turn.p0_sha256,output_schema_sha256=turn.output_schema_sha256,
            config=CFG,status="succeeded",request_started=True,input_tokens=1,output_tokens=1)
        return self.store.finish_discussion_turn(self.ws,self.cid,turn.turn_id,output or DiscussionOutput(public_reply="已更新本次资料范围；仍需核对回答。"),receipt)

    def clarification_fixture(self):
        from test_conversation_handoff import HandoffTests
        fixture=HandoffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.store,self.ws,self.mid,self.cid=fixture.store,fixture.ws,fixture.mid,fixture.cid
        self.initial=fixture.sources
        self.extra=self.source()
        self.expanded=[*self.initial,self.extra]
        return fixture

    def test_expanded_message_uses_run_snapshot_and_preserves_origin_and_old_hashes(self):
        self.attach()
        first=self.send(self.initial)
        self.store.mark_run_running(self.ws,self.mid,first.run.run_id)
        old_manifest=self.store.record_context_manifest(self.ws,self.mid,first.run.run_id,
            agent._context_manifest(self.store.get_context_snapshot(self.ws,self.mid,first.run.run_id),turn_index=1,tool_receipt_ids=[]))
        self.store.fail_run(self.ws,self.mid,first.run.run_id,"failed","agent_start_failed")
        old_message=self.store.list_conversation_messages(self.ws,self.cid).items[-1]
        mission=self.store.get_mission_snapshot(self.ws,self.mid).mission
        ref=SourceExcerptMessageReference(kind="source_excerpt",evidence_ref=EvidenceRef(**self.extra.model_dump(),
            locator={"kind":"csv_rows","row_start":1,"row_end":1,"column":None}))
        result=self.send(references=[ref],history_messages=[MessageHistoryRef(message_id=old_message.message_id,sha256=old_message.sha256)])
        self.assertIsNone(result.discussion_turn)
        self.assertEqual(result.run.source_refs,self.expanded)
        current=self.store.get_mission_snapshot(self.ws,self.mid).mission
        self.assertEqual(current.source_refs,mission.source_refs)
        self.assertEqual(current.original_attempt_id,mission.original_attempt_id)
        snapshot=self.store.get_context_snapshot(self.ws,self.mid,result.run.run_id)
        self.assertEqual([source.revision_id for source in snapshot.sources],[source.revision_id for source in self.expanded])
        from contextox.semantic_controller import build_context_plan
        plan,_=build_context_plan(snapshot,self.store)
        self.assertEqual(len(plan.sources),len(self.expanded))
        self.assertTrue(any(source["name"]=="extra.csv" for source in plan.sources))
        self.store.mark_run_running(self.ws,self.mid,result.run.run_id)
        read=self.store.execute_run_tool(self.ws,self.mid,result.run.run_id,ReadSourceCall(call_id="read_new",name="read_source",
            arguments={"revision_id":self.extra.revision_id,"locator":ref.evidence_ref.locator}))
        self.assertIn("west",read.output.text)
        with self.store._connection() as connection:
            row=connection.execute("SELECT sha256 FROM context_manifests WHERE manifest_id=?",(old_manifest.manifest_id,)).fetchone()
            self.assertEqual(row[0],old_manifest.sha256)
        self.assertEqual(self.store.list_conversation_messages(self.ws,self.cid).items[-2].sha256,old_message.sha256)
        self.assertEqual(self.store.get_run_snapshot(self.ws,self.mid,first.run.run_id).source_refs,self.initial)

    def test_old_entry_and_unrecorded_expansion_cannot_claim_conversation_authority(self):
        request=TaskMessageSendRequest(kind="message",client_request_id=str(uuid4()),expected_state_version=self.mission.state_version,
            content="请分析新增资料",references=[],history_messages=[],source_refs=self.expanded,provider_send_confirmed=True)
        with self.assertRaises(db.Path2StateError):self.store.send_task_message(self.ws,self.mid,request)
        from test_agent import _start_request
        with self.assertRaises(db.Path2StateError):self.store.start_run(self.ws,self.mid,_start_request(self.mission,self.expanded))
        self.attach()
        with self.assertRaises(db.Path2StateError):self.store.send_task_message(self.ws,self.mid,request)
        # A conversation association is still not proof of this request's CAS.
        with self.store._write_transaction() as connection:
            connection.execute("UPDATE workspace_conversations SET source_refs_json=? WHERE workspace_id=? AND conversation_id=?",
                (db._canonical_json(self.expanded),self.ws,self.cid))
        with self.assertRaises(db.Path2StateError):self.store.send_task_message(self.ws,self.mid,request)
        self.assertEqual(self.store.list_task_runs(self.ws,self.mid).items,[])

    def test_scope_cas_cross_workspace_revocation_and_unselected_reference(self):
        self.attach()
        other=self.store.create_workspace("Other synthetic").workspace_id
        foreign=self.source(other)
        for selected in ([*self.initial,foreign],):
            with self.assertRaises(db.Path2StateError):self.send(selected)
        ref=SourceExcerptMessageReference(kind="source_excerpt",evidence_ref=EvidenceRef(**self.extra.model_dump(),
            locator={"kind":"csv_rows","row_start":1,"row_end":1,"column":None}))
        with self.assertRaises(db.Path2StateError):self.send(self.initial,references=[ref])
        with self.assertRaises(db.Path2StateError):self.send(expected_state_version=2)
        with self.store._write_transaction() as connection:
            connection.execute("UPDATE source_revisions SET permission_status='denied' WHERE workspace_id=? AND revision_id=?",(self.ws,self.extra.revision_id))
        with self.assertRaises(db.Path2StateError):self.send()
        self.assertEqual(self.store.get_conversation(self.ws,self.cid).source_refs,self.initial)

    def test_conversation_origin_is_immutable_and_goal_sources_remain_required(self):
        from contextox.models import ConversationGoal
        conv,_=self.store.create_conversation(self.ws,ConversationCreateRequest(client_request_id=str(uuid4())))
        self.cid=conv.conversation_id
        first=self.send(self.initial)
        goal=ConversationGoal(text=first.input_message.content,message_refs=[MessageHistoryRef(
            message_id=first.input_message.message_id,sha256=first.input_message.sha256)])
        output=DiscussionOutput(public_reply="按当前目标开始。",next_action="start_task",goal=goal)
        turn=self.finish_discussion(first,output)
        started=self.store.start_conversation_task(self.ws,self.cid,turn.turn_id,output)
        self.mid=started.run.mission_id
        original=self.store.get_mission_snapshot(self.ws,self.mid).mission.conversation_origin
        self.store.fail_run(self.ws,self.mid,started.run.run_id,"failed","agent_start_failed")
        result=self.send()
        self.assertEqual(result.run.source_refs,self.expanded)
        self.assertEqual(self.store.get_mission_snapshot(self.ws,self.mid).mission.conversation_origin,original)
        self.store.fail_run(self.ws,self.mid,result.run.run_id,"failed","agent_start_failed")
        with self.assertRaises(db.Path2StateError):self.send([self.extra])

    def test_old_approval_remains_limited_to_original_sources_after_expansion(self):
        fixture=self.clarification_fixture()
        saved,_=fixture.save();approved,_=fixture.approve(saved.answer)
        result=self.send()
        self.assertIsNone(result.discussion_turn)
        answer=result.run.approved_answers[0]
        self.assertEqual(answer.answer.sha256,saved.answer.sha256)
        self.assertEqual(answer.answer.source_refs,self.initial)
        self.assertEqual(answer.approval,approved.approval)
        self.assertEqual(result.run.source_refs,self.expanded)
        with self.store._connection() as connection:
            row=connection.execute("SELECT source_refs_json FROM context_manifests WHERE run_id=?",(result.run.run_id,)).fetchone()
            self.assertEqual(json.loads(row[0]),[source.model_dump(mode="json") for source in self.expanded])

    def test_new_answer_and_handoff_bind_current_scope_and_replay_exactly(self):
        fixture=self.clarification_fixture()
        self.finish_discussion(self.send())
        conv=self.store.get_conversation(self.ws,self.cid)
        payload=fixture.payload(expected_conversation_version=conv.state_version,source_refs=self.expanded)
        receipt,created=handoff.handoff(self.store,self.ws,self.cid,payload)
        self.assertTrue(created)
        self.assertEqual(receipt.answer_steps[0].save_receipt.answer.source_refs,self.expanded)
        self.assertEqual(handoff.read(self.store,self.ws,self.cid,payload.client_request_id),receipt)
        handoff.claim_analysis(self.store,self.ws,self.cid,payload.client_request_id)
        result,created=self.store.send_task_message(self.ws,self.mid,receipt.send_request)
        self.assertTrue(created)
        self.assertEqual(result.run.source_refs,self.expanded)
        self.assertEqual(self.store.send_task_message(self.ws,self.mid,receipt.send_request)[0],result)

    def test_reused_approval_handoff_does_not_rewrite_answer_scope(self):
        fixture=self.clarification_fixture()
        saved,_=fixture.save()
        self.finish_discussion(self.send())
        approved,_=fixture.approve(saved.answer)
        conv=self.store.get_conversation(self.ws,self.cid)
        payload=fixture.payload(expected_conversation_version=conv.state_version,source_refs=self.expanded)
        values=payload.model_dump(mode="json")
        values["reviewed_answers"][0].update(items=None,saved_answer={"version":saved.answer.version,
            "sha256":saved.answer.sha256,"approval_id":approved.approval.approval_id})
        payload=ConversationHandoffRequest(**values)
        receipt,_=handoff.handoff(self.store,self.ws,self.cid,payload)
        self.assertIsNone(receipt.answer_steps[0].save_request)
        self.assertEqual(handoff.read(self.store,self.ws,self.cid,payload.client_request_id),receipt)
        self.assertEqual(self.store.get_clarification_answer(self.ws,self.mid,fixture.origin,fixture.q.clarification_id,1).answer.source_refs,self.initial)

    def test_removed_answer_dependency_and_old_handoff_scope_change_are_blocked(self):
        fixture=self.clarification_fixture()
        receipt,_=fixture.submit()
        with self.assertRaises(db.Path2StateError):self.send([self.extra])
        result=self.send()
        self.store.fail_run(self.ws,self.mid,result.run.run_id,"failed","agent_start_failed")
        with self.assertRaises(db.Path2StateError):handoff.claim_analysis(self.store,self.ws,self.cid,receipt.client_request_id)
        with self.assertRaises(db.Path2StateError):self.store.send_task_message(self.ws,self.mid,receipt.send_request)
        self.assertTrue(handoff.read(self.store,self.ws,self.cid,receipt.client_request_id).answers_approved)

    def test_unanswered_question_evidence_is_a_required_source(self):
        from contextox.models import CreateClarificationCall, UpdateDefinitionDraftCall
        from test_agent import _start_request
        from test_clarifications import field
        run=self.store.start_run(self.ws,self.mid,_start_request(self.mission,self.initial))
        self.store.mark_run_running(self.ws,self.mid,run.run_id)
        draft=self.store.execute_run_tool(self.ws,self.mid,run.run_id,UpdateDefinitionDraftCall(call_id="draft",name="update_definition_draft",
            arguments={"expected_version":0,"expected_sha256":None,"fields":[field()],"relationships":[],"unresolved_items":[]})).output
        evidence=EvidenceRef(**self.initial[0].model_dump(),locator={"kind":"csv_rows","row_start":1,"row_end":1,"column":None})
        question={"question":"What business window does this evidence represent?","why_needed":"Confirm the rule","expected_answer_type":"text",
            "suggested_owner_role":"business owner","related_definition_paths":["fields.window.rule"],"evidence_requested":[],
            "examples_or_options":[],"blocking_impact":"blocking","source_refs":[evidence.model_dump(mode="json")]}
        self.store.execute_run_tool(self.ws,self.mid,run.run_id,CreateClarificationCall(call_id="question",name="create_clarification",
            arguments={"draft_version":draft.version,"draft_sha256":draft.sha256,"questions":[question]}))
        self.attach()
        with self.assertRaises(db.Path2StateError):self.send([self.extra])
        self.assertEqual(self.store.get_conversation(self.ws,self.cid).source_refs,self.initial)

    def test_current_draft_evidence_cannot_be_removed_from_scope(self):
        from contextox.models import UpdateDefinitionDraftCall
        from test_agent import _start_request
        from test_clarifications import field
        run=self.store.start_run(self.ws,self.mid,_start_request(self.mission,self.initial))
        self.store.mark_run_running(self.ws,self.mid,run.run_id)
        evidence=EvidenceRef(**self.initial[0].model_dump(),locator={"kind":"csv_rows","row_start":1,"row_end":1,"column":None})
        self.store.execute_run_tool(self.ws,self.mid,run.run_id,UpdateDefinitionDraftCall(call_id="draft",name="update_definition_draft",
            arguments={"expected_version":0,"expected_sha256":None,"fields":[field(source_refs=[evidence.model_dump(mode="json")])],"relationships":[],"unresolved_items":[]}))
        self.store.fail_run(self.ws,self.mid,run.run_id,"failed","agent_start_failed")
        self.attach()
        with self.assertRaises(db.Path2StateError):self.send([self.extra])


if __name__=="__main__":unittest.main()
