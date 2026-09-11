import { useEffect, useRef, useState } from "react";
import type { components } from "./generated/api";
import { ApiRequestError, fetchClarificationCases, saveClarificationAnswer, fetchClarificationSubmission, sendConversationHandoff, fetchConversationHandoff, fetchRunSnapshot, type ConversationHandoffReceipt, type ConversationHandoffRequest, type WorkspaceConversation } from "./api/client";
import { AnswerForm, AnswerReadback, answerOmissions, blankAnswers, clarificationCaseMatches } from "./ClarificationAnswers";
import type { Path2WorkbenchState } from "./Path2Workbench";
import { sourceIdentityListEquals } from "./Path2Workbench";

type Case = components["schemas"]["ClarificationCase"];
type Answer = components["schemas"]["AnswerItem"];
type Suggestion = components["schemas"]["DiscussionAnswerSuggestion"];
type Page = components["schemas"]["ClarificationCasePage"];
const caseKey=(item:Case)=>`${item.request.run_id}/${item.request.clarification_id}`;
export function suggestedAnswerItems(item:Case,suggestions:Suggestion[]):Answer[] {
  const base=item.latest_answer?.items??blankAnswers(item.request);
  return base.map(answer=>{
    const proposed=suggestions.find(s=>s.origin_run_id===item.request.run_id&&s.clarification_id===item.request.clarification_id&&s.request_sha256===item.request_sha256&&s.question_index===answer.question_index);
    return proposed ? {...answer,disposition:proposed.disposition??answer.disposition,answer:proposed.answer??null,respondent:proposed.respondent??"",basis:proposed.basis??"",blocker:proposed.disposition==='unknown'?(proposed.blocker??{resolver:"",evidence_needed:"",next_action:""}):null,evidence_refs:proposed.evidence_refs??[],targets:proposed.targets??[]} : answer;
  });
}
export function reviewedAnswer(item:Case,items:Answer[]):components["schemas"]["HandoffReviewedAnswer"] {
  const same=item.latest_answer && JSON.stringify(items)===JSON.stringify(item.latest_answer.items);
  return {origin_run_id:item.request.run_id,clarification_id:item.request.clarification_id,request_sha256:item.request_sha256,expected_latest_version:item.latest_answer?.version??0,items:same?null:items,saved_answer:same?{version:item.latest_answer!.version,sha256:item.latest_answer!.sha256,approval_id:item.latest_approval?.approval_id??null}:null};
}
export function mergeReviewedSave(page:Page,value:components["schemas"]["ClarificationSubmissionReceipt"]):Page {
  const key=`${value.answer.origin_run_id}/${value.answer.clarification_id}`;
  const old=page.items.find(item=>caseKey(item)===key);
  if(!old||old.request_sha256!==value.answer.request_sha256)throw new Error('保存回执不属于当前审阅的问题集合。');
  return {mission_state_version:value.mission_state_version,items:page.items.map(item=>caseKey(item)===key?{...item,latest_answer:value.answer,latest_approval:value.approval,review_state:value.approval?'approved':'awaiting_approval'}:item)};
}
export type ConversationReviewState={workspaceId:string;conversationId:string;missionId:string;missionStateVersion:number;reviewStates:Case["review_state"][]};
export function receiptMatchesReviewedAnswers(receipt:ConversationHandoffReceipt|null,cases:Case[]) {
  if(!receipt||!cases.length||receipt.answer_steps.length!==cases.length)return false;
  return cases.every(item=>{
    const answer=item.latest_answer,approval=item.latest_approval;
    const steps=receipt.answer_steps.filter(step=>step.origin_run_id===item.request.run_id&&step.clarification_id===item.request.clarification_id);
    if(item.review_state!=="approved"||steps.length!==1||!answer||!approval)return false;
    const ref=steps[0].approved_answer;
    return receipt.workspace_id===item.request.workspace_id&&receipt.mission_id===item.request.mission_id&&ref.origin_run_id===item.request.run_id&&ref.clarification_id===item.request.clarification_id&&ref.answer_version===answer.version&&ref.answer_sha256===answer.sha256&&ref.approval_id===approval.approval_id&&approval.answer_version===answer.version&&approval.answer_sha256===answer.sha256;
  });
}
export function answersCanCollapse(cases:Case[],edits:Record<string,Answer[]>,pending:boolean,issue:boolean,receipt:ConversationHandoffReceipt|null) {
  return cases.length>0&&!pending&&!issue&&(!receipt||receipt.analysis_state==='started')&&cases.every(item=>{
    const answer=item.latest_answer,approval=item.latest_approval;
    return item.review_state==='approved'&&answer&&approval&&approval.answer_version===answer.version&&approval.answer_sha256===answer.sha256&&JSON.stringify(edits[caseKey(item)])===JSON.stringify(answer.items);
  });
}
export function ConversationAnswers({state,conversation,suggestions,onUpdated,onReviewState}: {state:Path2WorkbenchState;conversation:WorkspaceConversation;suggestions:Suggestion[];onUpdated:()=>Promise<void>;onReviewState?:(value:ConversationReviewState)=>void}) {
  const ws=conversation.workspace_id,mid=conversation.mission_id;
  const [page,setPage]=useState<Page|null>(null);
  const [initials,setInitials]=useState<Record<string,Answer[]>>({});
  const [edits,setEdits]=useState<Record<string,Answer[]>>({});
  const [revision,setRevision]=useState(0);
  const [busy,setBusy]=useState(false);const mutex=useRef(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [receipt,setReceipt]=useState<ConversationHandoffReceipt|null>(null);
  const [pending,setPending]=useState<{kind:'handoff'|'save';id:string}|null>(null);
  const dirty=useRef(false);const alive=useRef(true);const loadGeneration=useRef(0);const editGeneration=useRef(0);const receiptGeneration=useRef(0);
  const storageKey=`contextox.conversation-handoff:${ws}/${conversation.conversation_id}`;
  const observeReview=useRef(onReviewState);observeReview.current=onReviewState;
  useEffect(()=>{if(mid&&page&&page.items.every(item=>clarificationCaseMatches(item,ws,mid)))observeReview.current?.({workspaceId:ws,conversationId:conversation.conversation_id,missionId:mid,missionStateVersion:page.mission_state_version,reviewStates:page.items.map(item=>item.review_state)});},[page,ws,conversation.conversation_id,mid]);
  const load=async(useSuggestions=false)=>{
    if(!mid)return;
    const ticket=++loadGeneration.current,editTicket=editGeneration.current;
    const value=await fetchClarificationCases(ws,mid);
    if(!alive.current||ticket!==loadGeneration.current||editTicket!==editGeneration.current)return;
    if(value.items.some(item=>!clarificationCaseMatches(item,ws,mid)))throw new Error('回答请求归属不一致。');
    setPage(value);const next=Object.fromEntries(value.items.map(item=>[caseKey(item),useSuggestions?suggestedAnswerItems(item,suggestions):item.latest_answer?.items??blankAnswers(item.request)]));
    setInitials(next);setEdits(next);setRevision(old=>old+1);dirty.current=false;
  };
  useEffect(()=>{alive.current=true;try{const saved=sessionStorage.getItem(storageKey);if(saved)setPending(JSON.parse(saved));}catch{setError('无法回读交接标识，请先核对浏览器存储。');}void load().catch(e=>setError(e instanceof Error?e.message:'回答读取失败'));return()=>{alive.current=false;};},[storageKey,mid]);
  useEffect(()=>{if(!dirty.current&&!mutex.current&&!pending)void load().catch(e=>{if(alive.current)setError(e instanceof Error?e.message:'回答读取失败');});},[state.missionSnapshot?.mission.state_version,state.runSnapshot?.status]);
  const suggestionKey=JSON.stringify(suggestions);
  useEffect(()=>{
    if(suggestions.length&&!dirty.current&&!mutex.current&&!pending)void load(true).catch(e=>{if(alive.current)setError(e instanceof Error?e.message:'回答建议读取失败');});
  },[suggestionKey]);
  const failure=(e:unknown)=>{if(!alive.current)return;setError(e instanceof ApiRequestError?`操作未完成：${e.code ?? e.message}`:e instanceof Error?e.message:'结果待核对');if(e instanceof ApiRequestError&&[400,403,404,409,422].includes(e.status)&&!e.code?.includes('unknown')){sessionStorage.removeItem(storageKey);setPending(null);}};
  const remember=(value:{kind:'handoff'|'save';id:string})=>{sessionStorage.setItem(storageKey,JSON.stringify(value));setPending(value);};
  const applyHandoff=async(value:ConversationHandoffReceipt)=>{
    if(value.workspace_id!==ws||value.conversation_id!==conversation.conversation_id||value.mission_id!==mid)throw new Error('交接结果归属不一致。');
    setReceipt(value);
    setNotice(value.answers_approved?'整份回答已确认。':'回答交接尚待核对。');
    if(value.analysis_state==='started'&&value.run_id){const run=await fetchRunSnapshot(ws,mid!,value.run_id);if(alive.current&&(!state.runSnapshot||state.runSnapshot.created_at<=run.created_at))state.adoptDialogueRun(run);}
    if(['started','failed','ready'].includes(value.analysis_state)){sessionStorage.removeItem(storageKey);setPending(null);if(value.analysis_state==='started')sessionStorage.removeItem(`${storageKey}:request`);}
    await load();await onUpdated();
  };
  const save=async(item:Case,items:Answer[])=>{
    if(!mid||!page||!state.latestDraft||mutex.current||pending)throw new Error('当前回答尚不能保存。');
    mutex.current=true;setBusy(true);setError('');dirty.current=true;loadGeneration.current++;receiptGeneration.current++;const id=crypto.randomUUID();
    try{remember({kind:'save',id});const draft=state.latestDraft;const value=await saveClarificationAnswer(ws,mid,item.request.run_id,item.request.clarification_id,{client_request_id:id,expected_latest_version:item.latest_answer?.version??0,expected_state_version:page.mission_state_version,request_sha256:item.request_sha256,review_draft:{draft_id:draft.draft_id,version:draft.version,sha256:draft.sha256},source_refs:state.selectedSourceRefs,items});
      if(value.answer.workspace_id!==ws||value.answer.mission_id!==mid)throw new Error('保存结果归属不一致。');sessionStorage.removeItem(storageKey);setPending(null);setNotice('回答已保存 · 尚未批准；其他卡片的未保存输入保留。');
      if(alive.current){
        const merged=mergeReviewedSave(page,value);setPage(merged);
        setInitials(old=>({...old,[caseKey(item)]:value.answer.items}));
        // Preserve the reviewed snapshot of every other card until an explicit reload or handoff.
      }
      await state.refreshTask();
    }catch(e){failure(e);throw e;}finally{mutex.current=false;setBusy(false);}
  };
  const handoff=async()=>{
    if(!mid||!page||!state.latestDraft||mutex.current||pending)return;
    const omitted=page.items.flatMap((item,index)=>answerOmissions(edits[caseKey(item)]??[],item.request.questions.length).map(issue=>`问题组 ${index+1}：${issue}`));if(omitted.length){setError(omitted.join('；'));return;}
    mutex.current=true;setBusy(true);setError('');receiptGeneration.current++;
    try{

      const draft=state.latestDraft;
      const body:ConversationHandoffRequest={client_request_id:crypto.randomUUID(),expected_conversation_version:conversation.state_version,expected_state_version:page.mission_state_version,expected_draft:{draft_id:draft.draft_id,version:draft.version,sha256:draft.sha256},source_refs:state.selectedSourceRefs,reviewed_answers:page.items.map(item=>reviewedAnswer(item,edits[caseKey(item)]??[])),content:'请依据本次确认的整份业务回答继续分析，保留尚未解决的事项，并说明草案变化。',references:[],history_messages:[],provider_send_confirmed:true};
      sessionStorage.setItem(`${storageKey}:request`,JSON.stringify(body));remember({kind:'handoff',id:body.client_request_id});await applyHandoff(await sendConversationHandoff(ws,conversation.conversation_id,body));dirty.current=false;
    }catch(e){failure(e);}finally{mutex.current=false;setBusy(false);}
  };
  const reconcile=async(retry=false)=>{
    if(!mid||mutex.current||(!pending&&!receipt))return;mutex.current=true;setBusy(true);setError('');receiptGeneration.current++;
    try{
      const entry=pending??{kind:'handoff' as const,id:receipt!.client_request_id};
      if(entry.kind==='save'){const value=await fetchClarificationSubmission(ws,mid,entry.id);if(value.answer.workspace_id!==ws||value.answer.mission_id!==mid)throw new Error('回答回读归属不一致');sessionStorage.removeItem(storageKey);setPending(null);setNotice('回答保存已核对，尚未批准。');if(page)setPage(mergeReviewedSave(page,value));else await load();}
      else{const known=await fetchConversationHandoff(ws,conversation.conversation_id,entry.id);if(retry&&['ready','failed'].includes(known.analysis_state)&&!known.run_id){const raw=sessionStorage.getItem(`${storageKey}:request`);if(!raw)throw new Error('原交接负载不可用，只能继续核对，不能重建请求。');const body=JSON.parse(raw) as ConversationHandoffRequest;if(body.client_request_id!==entry.id)throw new Error('原交接标识不一致。');await applyHandoff(await sendConversationHandoff(ws,conversation.conversation_id,body));}else await applyHandoff(known);}
    }catch(e){setError(e instanceof Error?e.message:'仍无法确认结果');}finally{mutex.current=false;setBusy(false);}
  };
  useEffect(()=>{
    if(!conversation.last_handoff_id||!mid)return;
    const ticket=++receiptGeneration.current,editTicket=editGeneration.current;
    void fetchConversationHandoff(ws,conversation.conversation_id,conversation.last_handoff_id).then(value=>{
      if(!alive.current||ticket!==receiptGeneration.current||editTicket!==editGeneration.current||mutex.current)return;
      if(value.workspace_id!==ws||value.conversation_id!==conversation.conversation_id||value.mission_id!==mid)throw new Error('历史交接归属不一致。');
      setReceipt(value);
      if(['claimed','unknown'].includes(value.analysis_state))setPending({kind:'handoff',id:value.client_request_id});
    }).catch(e=>{if(alive.current)setError(e instanceof Error?e.message:'交接回读失败');});
  },[conversation.last_handoff_id,mid]);
  const cases=page?.items??[];if(!mid||(!cases.length&&!error&&!pending))return null;
  const active=['queued','running'].includes(state.runSnapshot?.status??'');
  const hasChanges=cases.some(item=>!item.latest_answer||JSON.stringify(edits[caseKey(item)])!==JSON.stringify(item.latest_answer.items));
  const selectedMatches=sourceIdentityListEquals(state.selectedSourceRefs,conversation.source_refs ?? []);
  const currentReceipt=receiptMatchesReviewedAnswers(receipt,cases)?receipt:null;
  const collapsed=answersCanCollapse(cases,edits,Boolean(pending)||busy,Boolean(error),currentReceipt);
  return <details className="conversation-answer-cards" aria-label="核对业务回答" open={!collapsed}><summary>{collapsed?"业务回答已确认 · 展开查看":"需要你确认的业务口径"}</summary><p>Agent 已把对话整理成卡片。请检查回答、来源和仍未知的事项；只有点击“确认并继续”才会采用。</p>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {pending&&<div role="status"><p>操作结果待核对，不自动重新发送。</p><button disabled={busy} onClick={()=>void reconcile()}>核对交接结果</button></div>}
    <div className="answer-card-tools"><button disabled={busy||active||Boolean(pending)} onClick={()=>{if(!dirty.current||window.confirm("重新读取会丢弃卡片中尚未保存的修改，继续？"))void load().catch(failure);}}>重新读取最新内容</button>{suggestions.length>0&&dirty.current&&<button disabled={busy||active||Boolean(pending)} onClick={()=>{if(!dirty.current||window.confirm('使用新的整理建议替换尚未保存的卡片输入？'))void load(true).catch(failure);}}>采用新的整理建议</button>}</div>
    {cases.map(item=><details className="conversation-answer-group" key={`${caseKey(item)}/${item.review_state}`} open={item.review_state!=="approved" ? true : undefined}><summary>{item.request.questions.length} 个问题 · {item.latest_answer?'回答已保存':'等待填写'}</summary><AnswerForm key={`${caseKey(item)}/${revision}`} request={item.request} latest={item.latest_answer} initialItems={initials[caseKey(item)]} draft={state.latestDraft} disabled={busy||active||Boolean(pending)} onDirty={value=>{if(value){dirty.current=true;receiptGeneration.current++;setReceipt(null);setNotice("回答内容已修改 · 保存后需要重新确认");}}} onItemsChange={items=>{editGeneration.current++;setEdits(old=>({...old,[caseKey(item)]:items}));}} onSave={items=>save(item,items)} saveLabel="仅保存，稍后继续"/>{item.latest_answer&&<details><summary>查看已经保存的回答</summary><AnswerReadback answer={item.latest_answer} request={item.request}/></details>}<details className="technical-details"><summary>问题组技术详情</summary><p>原始问题版本 {item.request.draft_version} · 回答版本 {item.latest_answer?.version ?? "尚未保存"}</p></details></details>)}
    {!selectedMatches&&<p role="alert">会话资料范围已修改。请先通过对话明确提交并核对新范围，再确认回答。</p>}
    {cases.length>0&&<button className="path2-primary-button" disabled={busy||active||Boolean(pending)||(Boolean(currentReceipt)&&!hasChanges)||!state.latestDraft||!selectedMatches} onClick={()=>void handoff()}>确认并继续</button>}
    {receipt&&!currentReceipt&&<p>此前回答已经交接；历史回执不代表当前修改已经确认。</p>}
    {currentReceipt&&<div className="handoff-stages" role="status"><p><strong>业务回答</strong>{currentReceipt.answers_approved?'已确认':'等待核对'}</p><p><strong>后续分析</strong>{currentReceipt.analysis_started?'已经开始':currentReceipt.analysis_state==='unknown'?'结果未知':currentReceipt.analysis_state==='claimed'?'正在核对启动结果':'尚未启动'}</p>{currentReceipt.error_code&&<details className="technical-details"><summary>技术详情</summary><p>{currentReceipt.error_code}</p></details>}{['ready','failed'].includes(currentReceipt.analysis_state)&&!currentReceipt.run_id&&<button disabled={busy} onClick={()=>void reconcile(true)}>核对并重试后续分析</button>}{['claimed','unknown'].includes(currentReceipt.analysis_state)&&<button disabled={busy} onClick={()=>void reconcile()}>核对启动结果</button>}</div>}
  </details>;
}
