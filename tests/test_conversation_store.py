"""Real local Store seams; all material and Provider receipts are synthetic."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from contextox import store as db
from contextox.models import (
    ConversationCreateRequest, ConversationGoal, ConversationMessageSendRequest,
    DiscussionOutput, DiscussionProviderReceipt, MessageHistoryRef,
    ProviderConfigSnapshot, SourceIdentity, canonical_sha256,
)
from contextox.conversation_store import is_explicit_work_instruction
from test_runtime import _mission


CFG=ProviderConfigSnapshot(endpoint_id="deepseek_chat_completions",model="deepseek-v4-flash",thinking="enabled",reasoning_effort="high")


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="contextox-conversations-",dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.store=db.WorkspaceStore.open(self.temp.name)
        self.ws=self.store.create_workspace("Synthetic conversation test").workspace_id
        self.conv,_=self.store.create_conversation(self.ws,ConversationCreateRequest(client_request_id=str(uuid4())))
        self.cid=self.conv.conversation_id

    def send(self,text="先聊聊字段含义",**kw):
        cv=self.store.get_conversation(self.ws,self.cid)
        request=ConversationMessageSendRequest(client_request_id=str(uuid4()),expected_state_version=cv.state_version,
            content=text,provider_send_confirmed=True,**kw)
        return self.store.create_conversation_submission(self.ws,self.cid,request,CFG,"1"*64,"2"*64)[0]

    def done(self,result,output=None,usage=True):
        turn=self.store.claim_discussion_turn(self.ws,self.cid,result.discussion_turn.turn_id)
        receipt=DiscussionProviderReceipt(workspace_id=self.ws,conversation_id=self.cid,turn_id=turn.turn_id,
            receipt_id=str(uuid4()),created_at=datetime.now(timezone.utc),request_sha256=turn.request_sha256,
            context_sha256=turn.context_sha256,p0_sha256=turn.p0_sha256,output_schema_sha256=turn.output_schema_sha256,
            config=CFG,status="succeeded",input_tokens=1 if usage else None,output_tokens=1 if usage else None,
            error_code=None if usage else "provider_usage_missing")
        return self.store.finish_discussion_turn(self.ws,self.cid,turn.turn_id,output or DiscussionOutput(public_reply="请说明任务目标。"),receipt)

    def source(self):
        source,_=self.store.import_source_revision(self.ws,"synthetic.csv","text/csv",b"id,amount\n1,2\n")
        return SourceIdentity(**{k:getattr(source,k) for k in SourceIdentity.model_fields})

    def goal(self,result):
        return ConversationGoal(text=result.input_message.content,message_refs=[
            MessageHistoryRef(message_id=result.input_message.message_id,sha256=result.input_message.sha256)])

    def failed(self,result,code,request_started=None):
        turn=self.store.claim_discussion_turn(self.ws,self.cid,result.discussion_turn.turn_id)
        receipt=DiscussionProviderReceipt(workspace_id=self.ws,conversation_id=self.cid,turn_id=turn.turn_id,
            receipt_id=str(uuid4()),created_at=datetime.now(timezone.utc),request_sha256=turn.request_sha256,
            context_sha256=turn.context_sha256,p0_sha256=turn.p0_sha256,output_schema_sha256=turn.output_schema_sha256,
            config=CFG,status="blocked",request_started=request_started,input_tokens=None,output_tokens=None,error_code=code)
        return self.store.fail_discussion_turn(self.ws,self.cid,turn.turn_id,"blocked",code,receipt)

    def test_continuous_dialogue_replay_and_active_readback(self):
        result=self.send()
        cv=self.store.get_conversation(self.ws,self.cid)
        self.assertEqual(cv.active_turn_id,result.discussion_turn.turn_id)
        self.assertEqual(cv.last_submission_id,result.discussion_turn.request.client_request_id)
        replay,created=self.store.create_conversation_submission(self.ws,self.cid,result.discussion_turn.request,CFG,"1"*64,"2"*64)
        self.assertFalse(created)
        self.assertEqual(replay.input_message,result.input_message)
        self.done(result)
        messages=self.store.list_conversation_messages(self.ws,self.cid).items
        self.assertEqual([m.role for m in messages],["user","assistant"])
        follow=self.send("继续讨论",history_messages=[MessageHistoryRef(message_id=m.message_id,sha256=m.sha256) for m in messages])
        self.assertEqual(len(self.store.discussion_context(self.ws,self.cid,follow.discussion_turn.turn_id).history),2)
        self.assertEqual(len(self.store.list_missions(self.ws)),0)

    def test_cross_workspace_and_cursor_cas_rejected(self):
        result=self.send()
        other=self.store.create_workspace("Other synthetic").workspace_id
        with self.assertRaises(db.Path2StateError):self.store.get_conversation(other,self.cid)
        with self.assertRaises(db.Path2StateError):self.store.list_conversation_messages(self.ws,self.cid,str(uuid4()))
        self.done(result)
        stale=result.discussion_turn.request.model_copy(update={"client_request_id":str(uuid4())})
        with self.assertRaises(db.Path2StateError):self.store.create_conversation_submission(self.ws,self.cid,stale,CFG,"1"*64,"2"*64)

    def test_new_mission_and_first_run_are_atomic_and_idempotent(self):
        source=self.source()
        result=self.send("请分析订单金额字段",source_refs=[source])
        goal=ConversationGoal(text=result.input_message.content,message_refs=[MessageHistoryRef(message_id=result.input_message.message_id,sha256=result.input_message.sha256)])
        output=DiscussionOutput(public_reply="开始核对已选资料。",next_action="start_task",goal=goal,title="订单金额")
        turn=self.done(result,output)
        first=self.store.start_conversation_task(self.ws,self.cid,turn.turn_id,output)
        second=self.store.start_conversation_task(self.ws,self.cid,turn.turn_id,output)
        self.assertEqual(first.run.run_id,second.run.run_id)
        with self.assertRaises(db.Path2StateError):
            self.store.start_conversation_task(self.ws,self.cid,turn.turn_id,output.model_copy(update={"title":"Different"}))
        mission=self.store.get_mission_snapshot(self.ws,first.run.mission_id).mission
        self.assertIsNone(mission.original_attempt_id)
        self.assertEqual(mission.goal,result.input_message.content)
        self.assertEqual(mission.conversation_origin.message_id,result.input_message.message_id)
        self.assertEqual(len(self.store.list_task_runs(self.ws,mission.mission_id).items),1)
        texts=[m.content for m in self.store.list_conversation_messages(self.ws,self.cid).items]
        self.assertEqual(texts.count(result.input_message.content),1)
        reopened=db.WorkspaceStore.open(self.temp.name)
        self.assertEqual(reopened.get_mission_snapshot(self.ws,mission.mission_id).mission.conversation_origin,mission.conversation_origin)

    def test_plain_natural_instruction_and_explicit_goal_revision(self):
        first=self.send("先聊聊按产品汇总销售额")
        self.done(first,DiscussionOutput(public_reply="目标暂定按产品汇总销售额。",goal=self.goal(first)))
        source=self.source()
        revised=self.send("我想按地区统计订单金额。",source_refs=[source])
        output=DiscussionOutput(public_reply="使用当前资料开始统计。",next_action="start_task",goal=self.goal(revised))
        turn=self.done(revised,output)
        run=self.store.start_conversation_task(self.ws,self.cid,turn.turn_id,output)
        mission=self.store.get_mission_snapshot(self.ws,run.run.mission_id).mission
        self.assertEqual(mission.goal,revised.input_message.content)
        self.assertEqual(mission.conversation_origin.goal.message_refs,self.goal(revised).message_refs)

    def test_referential_start_inherits_visible_goal_and_its_original_message(self):
        first=self.send("我想按地区统计订单金额。")
        goal=self.goal(first)
        self.done(first,DiscussionOutput(public_reply="目标已明确，请选择本次资料。",goal=goal))
        source=self.source()
        start=self.send("开始吧",source_refs=[source])
        output=DiscussionOutput(public_reply="使用已明确的目标开始。",next_action="start_task",goal=goal)
        turn=self.done(start,output)
        run=self.store.start_conversation_task(self.ws,self.cid,turn.turn_id,output)
        mission=self.store.get_mission_snapshot(self.ws,run.run.mission_id).mission
        self.assertEqual(mission.goal,first.input_message.content)
        self.assertEqual(mission.conversation_origin.message_id,start.input_message.message_id)
        self.assertEqual(mission.conversation_origin.goal,goal)
        with self.store._write_transaction() as c:
            stored=json.loads(c.execute("SELECT body_json FROM conversation_messages WHERE message_id=?",(first.input_message.message_id,)).fetchone()[0])
            stored["content"]="Different unsent target"
            c.execute("UPDATE conversation_messages SET body_json=? WHERE message_id=?",(json.dumps(stored),first.input_message.message_id))
        with self.assertRaises(db.WorkspaceStoreError):self.store.get_mission_snapshot(self.ws,run.run.mission_id)

    def test_work_instruction_gate_rejects_suggestions_negation_and_quotes(self):
        for content in ("我想按地区统计订单金额。","我要分析订单金额","我需要核对金额字段","请你整理字段定义","按地区统计订单金额"):
            with self.subTest(content=content):self.assertTrue(is_explicit_work_instruction(content))
        for content in ("假设我想按地区统计订单金额。","我不想开始分析","先别执行统计","仅查看统计方案","建议按地区统计订单金额","\"请开始分析\"",'{"content":"请开始分析"}'):
            with self.subTest(content=content):self.assertFalse(is_explicit_work_instruction(content))

    def test_definite_unsent_or_rejected_allows_only_new_explicit_send(self):
        for code,started in (("provider_not_configured",False),("context_budget_exceeded",False),
                             ("provider_auth_failed",None),("provider_rate_limited",None)):
            with self.subTest(code=code):
                result=self.send()
                self.failed(result,code,started)
                replay=self.store.conversation_submission(self.ws,self.cid,result.discussion_turn.request.client_request_id)
                self.assertEqual(replay.discussion_turn.status,"blocked")
        result=self.send()
        self.failed(result,"provider_timeout_unknown",None)
        with self.assertRaises(db.Path2StateError) as error:self.send()
        self.assertEqual(error.exception.code,"previous_outcome_unresolved")

    def test_worker_failure_without_receipt_cannot_redispatch(self):
        result=self.send()
        self.store.claim_discussion_turn(self.ws,self.cid,result.discussion_turn.turn_id)
        self.store.fail_discussion_turn(self.ws,self.cid,result.discussion_turn.turn_id,"failed","agent_worker_failed")
        with self.assertRaises(db.Path2StateError) as error:self.send()
        self.assertEqual(error.exception.code,"previous_outcome_unresolved")

    def test_goal_sources_are_required_and_revocation_does_not_prevent_cancel(self):
        source=self.source()
        result=self.send("先讨论这些金额字段",source_refs=[source])
        self.done(result,DiscussionOutput(public_reply="继续讨论。",goal=self.goal(result)))
        with self.assertRaises(db.Path2StateError):self.send("继续讨论",source_refs=[])
        follow=self.send("继续讨论",source_refs=[source])
        with self.store._write_transaction() as c:
            c.execute("UPDATE source_revisions SET permission_status='denied' WHERE workspace_id=? AND revision_id=?",(self.ws,source.revision_id))
        for read in (lambda:self.store.discussion_context(self.ws,self.cid,follow.discussion_turn.turn_id),
                     lambda:self.store.get_discussion_turn(self.ws,self.cid,result.discussion_turn.turn_id),
                     lambda:self.store.list_conversation_messages(self.ws,self.cid)):
            with self.assertRaises(db.Path2StateError):read()
        cancelled=self.store.cancel_discussion_turn(self.ws,self.cid,follow.discussion_turn.turn_id)
        self.assertEqual(cancelled.status,"cancelled")

    def test_first_run_failure_rolls_back_mission_and_association(self):
        source=self.source();result=self.send("请整理字段",source_refs=[source])
        goal=ConversationGoal(text=result.input_message.content,message_refs=[MessageHistoryRef(message_id=result.input_message.message_id,sha256=result.input_message.sha256)])
        output=DiscussionOutput(public_reply="已明确范围。",next_action="start_task",goal=goal)
        turn=self.done(result,output)
        with patch.object(self.store,"_send_task_message_in_connection",side_effect=db.Path2StateError("synthetic_failure")):
            with self.assertRaises(db.Path2StateError):self.store.start_conversation_task(self.ws,self.cid,turn.turn_id,output)
        self.assertEqual(self.store.list_missions(self.ws),[])
        self.assertIsNone(self.store.get_conversation(self.ws,self.cid).mission_id)

    def test_hypothetical_input_and_missing_usage_cannot_start(self):
        source=self.source();result=self.send("假设请分析这些字段",source_refs=[source])
        goal=ConversationGoal(text=result.input_message.content,message_refs=[MessageHistoryRef(message_id=result.input_message.message_id,sha256=result.input_message.sha256)])
        output=DiscussionOutput(public_reply="候选讨论",next_action="start_task",goal=goal)
        turn=self.done(result,output)
        with self.assertRaises(db.Path2StateError):self.store.start_conversation_task(self.ws,self.cid,turn.turn_id,output)
        follow=self.send("请分析字段",source_refs=[source])
        missing=self.done(follow,usage=False)
        with self.assertRaises(db.Path2StateError):self.send("请继续",source_refs=[source])
        self.assertEqual(missing.provider_receipt.usage_status,"missing")

    def test_context_tampering_and_missing_terminal_receipt_fail_closed(self):
        result=self.send()
        with self.store._write_transaction() as c:
            row=c.execute("SELECT context_json FROM discussion_turns WHERE turn_id=?",(result.discussion_turn.turn_id,)).fetchone()
            payload=json.loads(row[0]);payload["input"]["content"]="Different unsent input"
            c.execute("UPDATE discussion_turns SET context_json=? WHERE turn_id=?",(json.dumps(payload),result.discussion_turn.turn_id))
        with self.assertRaises(db.WorkspaceStoreError):self.store.discussion_context(self.ws,self.cid,result.discussion_turn.turn_id)

    def test_missing_receipt_row_cannot_authorize_execution(self):
        result=self.send();turn=self.done(result)
        with self.store._write_transaction() as c:
            c.execute("DELETE FROM discussion_provider_receipts WHERE turn_id=?",(turn.turn_id,))
        with self.assertRaises(db.WorkspaceStoreError):self.store.get_discussion_turn(self.ws,self.cid,turn.turn_id)

    def test_old_mission_messages_and_hashes_remain_unchanged(self):
        ws,mission=_mission(self.store)
        old_json=mission.model_dump_json(); old=self.store.list_task_messages(ws,mission.mission_id).items
        cv,_=self.store.create_conversation(ws,ConversationCreateRequest(client_request_id=str(uuid4()),mission_id=mission.mission_id))
        linked=self.store.list_conversation_messages(ws,cv.conversation_id).items
        self.assertEqual([m.sha256 for m in linked],[m.sha256 for m in old])
        self.assertEqual(self.store.get_mission_snapshot(ws,mission.mission_id).mission.model_dump_json(),old_json)
        self.assertNotIn("conversation_origin",json.loads(old_json))

    def test_legacy_mission_normal_message_creates_run_without_discussion(self):
        ws,mission=_mission(self.store)
        cv,_=self.store.create_conversation(ws,ConversationCreateRequest(client_request_id=str(uuid4()),mission_id=mission.mission_id))
        q=ConversationMessageSendRequest(client_request_id=str(uuid4()),expected_state_version=1,content="请解释字段",provider_send_confirmed=True)
        result,_=self.store.create_conversation_submission(ws,cv.conversation_id,q,CFG,"1"*64,"2"*64,demo_fast=True)
        self.assertIsNone(result.discussion_turn);self.assertIsNotNone(result.run)
        self.assertEqual(result.run.budget.max_model_turns,2)

    def test_clarification_discussion_freezes_saved_answers_and_rejects_invented_evidence(self):
        from test_clarifications import ClarificationTests
        from contextox.models import DiscussionAnswerSuggestion, EvidenceRef
        fixture=ClarificationTests()
        fixture.with_sources=True
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        saved,_=fixture.save()
        self.store=fixture.store; self.ws=fixture.ws
        cv,_=self.store.create_conversation(self.ws,ConversationCreateRequest(client_request_id=str(uuid4()),mission_id=fixture.mid))
        self.cid=cv.conversation_id
        result=self.send("请整理仍缺失的回答",source_refs=fixture.sources)
        context=self.store.discussion_context(self.ws,self.cid,result.discussion_turn.turn_id)
        self.assertEqual(context.mission.clarification_cases[0].latest_answer,saved.answer)
        suggestion=DiscussionAnswerSuggestion(origin_run_id=fixture.origin,clarification_id=fixture.q.clarification_id,
            request_sha256=canonical_sha256(fixture.q),question_index=0,
            evidence_refs=[EvidenceRef(**fixture.sources[0].model_dump(),locator={"kind":"csv_rows","row_start":999,"row_end":999,"column":None})])
        with self.assertRaises(db.Path2StateError):
            self.done(result,DiscussionOutput(public_reply="候选回答",answer_suggestions=[suggestion]))

    def test_approved_historical_clarifications_allow_normal_followup_run(self):
        from test_clarifications import ClarificationTests
        fixture=ClarificationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        saved,_=fixture.save();fixture.approve(saved.answer)
        self.store=fixture.store;self.ws=fixture.ws
        cv,_=self.store.create_conversation(self.ws,ConversationCreateRequest(client_request_id=str(uuid4()),mission_id=fixture.mid))
        self.cid=cv.conversation_id
        result=self.send("继续应用已批准的回答",source_refs=fixture.sources)
        self.assertIsNone(result.discussion_turn)
        self.assertEqual(result.run.approved_answers[0].answer.sha256,saved.answer.sha256)


class ConversationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="contextox-v6-migration-",dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/db.DB_FILENAME
        with closing(sqlite3.connect(self.path)) as c:
            for _,sql in db._EXPECTED_V6_TABLES:c.execute(sql)
            for _,_,sql in db._EXPECTED_V3_INDEXES:c.execute(sql)
            c.execute("PRAGMA user_version=6");c.commit()
        self.store=db.WorkspaceStore.open(self.temp.name)
        self.ws,self.mission=_mission(self.store)
        self.source,_=self.store.import_source_revision(self.ws,"migration.csv","text/csv",b"field\nsynthetic\n")

    def test_explicit_migration_preserves_old_rows_and_backup(self):
        old=self.store.get_mission_snapshot(self.ws,self.mission.mission_id).mission.model_dump_json()
        messages=self.store.list_task_messages(self.ws,self.mission.mission_id).model_dump_json()
        with self.store._connection() as c:
            rows={name:c.execute('SELECT * FROM '+name+' ORDER BY rowid').fetchall() for name,_ in db._EXPECTED_V6_TABLES}
        with self.assertRaises(db.Path2StateError):self.store.create_conversation(self.ws,ConversationCreateRequest(client_request_id=str(uuid4())))
        backup=self.store.migrate_conversations()
        self.assertTrue((backup/'manifest.json').exists())
        self.assertIsNone(self.store.migrate_conversations())
        with self.store._connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],7)
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0],1)
            for name,_ in db._EXPECTED_V6_TABLES:
                current=c.execute('SELECT * FROM '+name+' ORDER BY rowid').fetchall()
                self.assertEqual([row[:-1] for row in current] if name=='missions' else current,rows[name],name)
        self.assertEqual(self.store.get_mission_snapshot(self.ws,self.mission.mission_id).mission.model_dump_json(),old)
        self.assertEqual(self.store.list_task_messages(self.ws,self.mission.mission_id).model_dump_json(),messages)
        manifest=json.loads((backup/'manifest.json').read_text())
        import hashlib
        for relative,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((backup/relative).read_bytes()).hexdigest(),digest)
        self.assertEqual(len(manifest['files']),2)

    def test_migration_failure_rolls_back_and_future_schema_rejected(self):
        with patch.object(db,'_validate_connection_schema',side_effect=[None,db.WorkspaceSchemaUnsupportedError()]):
            with self.assertRaises(db.WorkspaceSchemaUnsupportedError):self.store.migrate_conversations()
        with self.store._connection() as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],6)
        with closing(sqlite3.connect(self.path)) as c:c.execute('PRAGMA user_version=8');c.commit()
        with self.assertRaises(db.WorkspaceSchemaUnsupportedError):db.WorkspaceStore.open(self.temp.name)


if __name__=='__main__':unittest.main()
