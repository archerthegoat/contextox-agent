import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./generated/api";
import { ApiRequestError, createConversation, createWorkspace, fetchConversation, fetchConversationMessages, fetchConversations, fetchConversationSubmission, fetchDeepSeekSettings, fetchWorkspaces, sendConversationMessage, cancelDiscussionTurn, type Workspace, type WorkspaceConversation, type ConversationMessage, type ConversationMessageSendRequest, type ConversationSubmissionReceipt } from "./api/client";
import { sourceIdentityEquals, sourceIdentityFromRevision, statusLabel, type Path2WorkbenchState, type SourceIdentity } from "./Path2Workbench";
import { referenceLabel, type MessageReference } from "./TaskDialogue";
import { ModelSettings } from "./ModelSettings";
import { ConversationAnswers, type ConversationReviewState } from "./ConversationAnswers";
import { writeSelectedWorkspaceId } from "./WorkspaceSwitcher";

type CreateRequest = components["schemas"]["ConversationCreateRequest"];
type Turn = components["schemas"]["DiscussionTurn"];
const conversationKey = (ws: string) => `contextox.conversation:${ws}`;
const submissionKey = (ws: string, id: string) => `contextox.conversation-submission:${ws}/${id}`;
const createKey = (ws: string) => `contextox.conversation-create:${ws}`;

export function discussionStatusLabel(status: Turn["status"]) {
  return {queued:"准备答复…",running:"正在理解你的问题…",succeeded:"答复已更新",blocked:"讨论暂时受阻",failed:"本次讨论未完成",cancelled:"讨论已停止"}[status];
}
export function conversationSendGuidance(hasMission:boolean,sourceCount:number,active:boolean) {
  if(active)return "可以编辑下一条草稿；本轮结束后再发送，不自动排队。";
  if(sourceCount===0)return "当前未选择资料。可以先聊天；如需基于资料工作，请先选择本轮资料。系统不会自动使用工作区全部资料。";
  if(!hasMission)return `已选择 ${sourceCount} 份资料。发送明确的工作指令（例如“请分析订单表中的地区和金额字段”）后，会自动形成任务并开始分析；普通询问仍保持讨论。`;
  return `本轮将使用 ${sourceCount} 份资料。发送后会根据你的话继续讨论或推进当前任务。`;
}
export function conversationPreviewTarget(conversation: WorkspaceConversation | null, messages: ConversationMessage[], workspaceId: string | null): {reference: MessageReference | null; source: SourceIdentity | null} {
  const empty={reference:null,source:null};
  if(!conversation||conversation.workspace_id!==workspaceId||conversation.mission_id)return empty;
  const current=messages.filter(message=>message.workspace_id===workspaceId&&message.conversation_id===conversation.conversation_id);
  let lastInput=current.length-1;
  while(lastInput>=0&&current[lastInput].role!=='user')lastInput--;
  if(lastInput<0)return empty;
  const scope=current[lastInput].source_refs??[];
  // Only persisted messages from the latest submitted exchange drive the preview.
  // Composer selections and imported files are not a submitted scope.
  for(const message of current.slice(lastInput).reverse())for(const reference of message.references??[]) {
    const source=reference.kind==='source_column'?reference.source_ref:reference.kind==='source_excerpt'?reference.evidence_ref:null;
    if(source&&source.workspace_id===workspaceId&&scope.some(item=>sourceIdentityEquals(item,source)))return {reference,source:null};
  }
  return {reference:null,source:scope.find(source=>source.workspace_id===workspaceId)??null};
}

export function conversationError(error: unknown) {
  const code = error instanceof ApiRequestError ? error.code : null;
  return ({source_permission_denied:"所选资料的读取许可已失效。请核对资料授权和本轮范围后继续。",source_refs_invalid:"所选资料与这次任务或回答的范围不一致，请重新核对资料范围。",discussion_outcome_unknown:"上次讨论结果仍待核对，不能自动重发。请先核对原发送结果。",previous_outcome_unresolved:"前一轮结果尚不确定，请先核对执行详情，避免重复分析。",state_conflict:"任务或回答版本已变化，请刷新并核对当前内容后继续。",clarification_answer_stale:"回答版本已变化，请重新读取并核对，旧批准不会用于新版本。",conversation_not_implemented:"此资料库需要显式迁移后才能使用连续对话，现有历史可继续查看。",conversation_stale:"会话版本已变化。请刷新核对当前目标与资料后重新发送。",message_context_scope_mismatch:"历史、草案或回答引用了本轮未选资料。请调整范围，系统不会自动补回。",workspace_store_busy:"已有模型工作正在执行，请稍后发送。草稿已保留。",conversation_submission_not_found:"尚未查到本次发送。这不能证明请求未执行，请保留原标识继续核对。",source_identity_stale:"资料版本已变化，请重新选择当前版本。"} as Record<string,string>)[code ?? ""] ?? (error instanceof ApiRequestError ? `操作未完成：${code ?? error.message}` : error instanceof Error ? error.message : "连接中断，结果待核对。请勿重复发送。");
}
export function conversationBelongsTo(value: WorkspaceConversation, workspaceId: string, conversationId?: string) {
  return value.workspace_id === workspaceId && (!conversationId || value.conversation_id === conversationId) && (value.source_refs ?? []).every(ref => ref.workspace_id === workspaceId);
}
export function conversationUsesTaskHistory(state:Pick<Path2WorkbenchState,"selectedMission"|"missionSnapshot"|"latestDraft">,reviewStates:ConversationReviewState["reviewStates"]|null=null) {
  const mission=state.missionSnapshot?.mission??state.selectedMission;
  return Boolean(mission&&reviewStates!==null&&reviewStates.every(status=>status==='approved')&&state.latestDraft?.status!=="in_review");
}
export function conversationReviewMatches(value:ConversationReviewState|null,conversation:WorkspaceConversation|null,workspaceId:string|null) {
  return Boolean(value&&conversation&&value.workspaceId===workspaceId&&value.workspaceId===conversation.workspace_id&&value.conversationId===conversation.conversation_id&&value.missionId===conversation.mission_id);
}
export function recentConversationHistory(messages: ConversationMessage[],taskOnly=false) {return messages.filter(message=>!taskOnly||message.task_message).slice(-4).map(message => message.message_id);}

export function mergeConversationPage(previous:ConversationMessage[],page:ConversationMessage[],older=false) {
  const ids=new Set(page.map(message=>message.message_id));const retained=previous.filter(message=>!ids.has(message.message_id));
  return older?[...page,...retained]:[...retained,...page];
}
export function selectedConversationHistory(messages:ConversationMessage[],ids:string[],taskOnly=false) {
  if(ids.length>4||new Set(ids).size!==ids.length)throw new Error('历史选择超限或重复，请重新选择。');
  return ids.map(id=>{const message=messages.find(item=>item.message_id===id);if(!message)throw new Error('所选历史尚未完整回读，请重新核对；本轮未发送。');if(taskOnly&&!message.task_message)throw new Error('已选历史含任务前或澄清讨论消息，本轮任务分析不能携带；请在历史选择中取消该项。');return {message_id:message.message_id,sha256:message.sha256};});
}

export function useConversationDialogue(state: Path2WorkbenchState, onWorkspace: (workspace: Workspace | null) => void) {
  const ws = state.workspaceId;
  const stateRef = useRef(state); stateRef.current = state;
  const workspaceRef = useRef(ws); workspaceRef.current = ws;
  const workspaceInitialization = useRef<{workspaceId:string;resolve:()=>void;reject:(error:Error)=>void}|null>(null);
  const generation = useRef(0);
  const readSequence = useRef(0);
  const [loadedWorkspace, setLoadedWorkspace] = useState(ws);
  const [list, setList] = useState<WorkspaceConversation[]>([]);
  const [conversation, setConversation] = useState<WorkspaceConversation | null>(null);
  const conversationRef = useRef(conversation); conversationRef.current = conversation;
  const [reviewState,setReviewState]=useState<ConversationReviewState|null>(null);
  const reviewRef=useRef(reviewState);reviewRef.current=reviewState;
  const updateReviewState=useCallback((value:ConversationReviewState)=>{
    if(!conversationReviewMatches(value,conversationRef.current,workspaceRef.current))return;
    setReviewState(previous=>conversationReviewMatches(previous,conversationRef.current,workspaceRef.current)&&previous!.missionStateVersion>value.missionStateVersion?previous:value);
  },[]);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [references, setReferences] = useState<MessageReference[]>([]);
  const [historyIds, setHistoryIds] = useState<string[]>([]);
  const historyTouched = useRef(false);
  const [turn, setTurn] = useState<Turn | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const mutex = useRef(false);
  const [needsConnection, setNeedsConnection] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [pendingCreate, setPendingCreate] = useState<CreateRequest | null>(null);
  const [scopeRefs, setScopeRefs] = useState<SourceIdentity[]>([]);
  const scopeHydrated = useRef("");
  const [readyScope, setReadyScope] = useState("");
  const [storageReady, setStorageReady] = useState(true);
  const scope = `${ws}/${conversation?.conversation_id ?? "new"}`;
  const current = useRef(scope); current.current = scope;

  const setKnownConversation = (value: WorkspaceConversation) => {
    setConversation(value); conversationRef.current=value;
    setList(old => [value,...old.filter(item => item.conversation_id !== value.conversation_id)]);
    sessionStorage.setItem(conversationKey(value.workspace_id),value.conversation_id);
  };
  const applyReceipt = async (receipt: ConversationSubmissionReceipt, workspaceId: string, id: string) => {
    if (!conversationBelongsTo(receipt.conversation,workspaceId,id) || receipt.input_message.workspace_id!==workspaceId || receipt.input_message.conversation_id!==id) throw new Error("会话结果归属不一致，已停止回读。");
    if (workspaceRef.current !== workspaceId || conversationRef.current?.conversation_id !== id) return;
    if(receipt.conversation.state_version < (conversationRef.current?.state_version ?? 0))return;
    setKnownConversation(receipt.conversation);
    setTurn(old=>old?.turn_id===receipt.discussion_turn?.turn_id&&!['queued','running'].includes(old?.status??'')&&['queued','running'].includes(receipt.discussion_turn?.status??'')?old:receipt.discussion_turn??null);
    if(receipt.conversation.mission_id && stateRef.current.selectedMission?.mission_id !== receipt.conversation.mission_id) {
      await stateRef.current.selectMission(receipt.conversation.mission_id);
      if(workspaceRef.current!==workspaceId||conversationRef.current?.conversation_id!==id)return;
      stateRef.current.replaceSourceSelection?.(receipt.conversation.source_refs ?? []);
    }
    if(workspaceRef.current !== workspaceId || conversationRef.current?.conversation_id !== id) return;
    if(receipt.run && (!stateRef.current.runSnapshot || stateRef.current.runSnapshot.mission_id!==receipt.run.mission_id || stateRef.current.runSnapshot.created_at<=receipt.run.created_at)) stateRef.current.adoptDialogueRun(receipt.run);
    sessionStorage.removeItem(submissionKey(workspaceId,id));setPendingId(null);
  };
  const readCurrent = useCallback(async (workspaceId: string, id: string, paginate?: string) => {
    const ticket = generation.current, sequence=++readSequence.current;
    const [value,page] = await Promise.all([fetchConversation(workspaceId,id),fetchConversationMessages(workspaceId,id,paginate)]);
    if(ticket!==generation.current || sequence!==readSequence.current || workspaceRef.current!==workspaceId || conversationRef.current?.conversation_id!==id) return;
    if(!conversationBelongsTo(value,workspaceId,id) || page.items.some(item=>item.workspace_id!==workspaceId||item.conversation_id!==id)) throw new Error("返回内容不属于当前会话，已停止回读。");
    if(value.state_version < (conversationRef.current?.state_version ?? 0))return;
    setKnownConversation(value);setMessages(old=>mergeConversationPage(old,page.items,Boolean(paginate)));setCursor(page.next_before_message_id ?? null);

    const pending=sessionStorage.getItem(submissionKey(workspaceId,id));
    const requestId=pending ?? value.last_submission_id;
    if(requestId) {
      const receipt=await fetchConversationSubmission(workspaceId,id,requestId);
      if(ticket!==generation.current||sequence!==readSequence.current)return;
      await applyReceipt(receipt,workspaceId,id);
      if(!paginate&&receipt.discussion_turn?.output&&!page.items.some(message=>message.role==='assistant'&&message.turn_id===receipt.discussion_turn?.turn_id)) {
        const completed=await fetchConversationMessages(workspaceId,id);
        if(ticket===generation.current&&workspaceRef.current===workspaceId&&conversationRef.current?.conversation_id===id&&completed.items.every(message=>message.workspace_id===workspaceId&&message.conversation_id===id)) {
          setMessages(old=>mergeConversationPage(old,completed.items));setCursor(completed.next_before_message_id??null);
        }
      }
    }
    if(ticket===generation.current)setReadyScope(`${workspaceId}/${id}`);
  }, []);
  const openConversation = async (value: WorkspaceConversation) => {
    if(value.workspace_id!==workspaceRef.current)return;
    if(conversationRef.current?.conversation_id===value.conversation_id) {await readCurrent(value.workspace_id,value.conversation_id);return;}
    generation.current++;historyTouched.current=false;setError("");setReadyScope("");setLoading(true);setText("");setReferences([]);setMessages([]);setTurn(null);setScopeRefs(value.source_refs ?? []);scopeHydrated.current="";
    try {
      setKnownConversation(value);setPendingId(sessionStorage.getItem(submissionKey(value.workspace_id,value.conversation_id)));
      if(value.mission_id)await stateRef.current.selectMission(value.mission_id);else stateRef.current.clearMission?.();
      await readCurrent(value.workspace_id,value.conversation_id);
    }catch(e){if(workspaceRef.current===value.workspace_id)setError(conversationError(e));}finally{if(workspaceRef.current===value.workspace_id)setLoading(false);}
  };
  const refresh = async () => {
    const selected=conversationRef.current;if(!selected||loading)return;setLoading(true);
    try{await readCurrent(selected.workspace_id,selected.conversation_id);setError("");}catch(e){setError(conversationError(e));}finally{setLoading(false);}
  };
  useEffect(() => {
    setLoadedWorkspace(ws);generation.current++;setConversation(null);conversationRef.current=null;setList([]);setMessages([]);setTurn(null);setReferences([]);setHistoryIds([]);setText("");setScopeRefs([]);setReadyScope("");setPendingId(null);setPendingCreate(null);setError("");scopeHydrated.current="";historyTouched.current=false;
    if(workspaceInitialization.current&&workspaceInitialization.current.workspaceId!==ws){workspaceInitialization.current.reject(new Error("工作区选择已变化，本次消息尚未发送。"));workspaceInitialization.current=null;}
    if(!ws)return;
    const ticket=generation.current;setLoading(true);
    try{const saved=sessionStorage.getItem(createKey(ws));if(saved)setPendingCreate(JSON.parse(saved) as CreateRequest);}catch{setStorageReady(false);setError("浏览器无法核对会话创建标识，请先恢复存储访问。");}
    void fetchConversations(ws).then(async values=>{
      if(ticket!==generation.current||workspaceRef.current!==ws)return;
      if(values.some(value=>!conversationBelongsTo(value,ws)))throw new Error("会话列表归属不一致。");setList(values);
      const saved=sessionStorage.getItem(conversationKey(ws));const selected=values.find(value=>value.conversation_id===saved);
      if(selected) await openConversation(selected);else {stateRef.current.clearMission?.();if(saved)setError("此前选择的对话已无法读取，请从列表明确选择。");}
    }).catch(e=>{if(workspaceRef.current===ws)setError(conversationError(e));if(workspaceInitialization.current?.workspaceId===ws){workspaceInitialization.current.reject(new Error("工作区会话尚未完成回读，消息未发送。"));workspaceInitialization.current=null;}}).finally(()=>{if(workspaceRef.current===ws)setLoading(false);if(workspaceInitialization.current?.workspaceId===ws){if(workspaceRef.current===ws)workspaceInitialization.current.resolve();else workspaceInitialization.current.reject(new Error("工作区选择已变化。"));workspaceInitialization.current=null;}});
  },[ws]);
  const sourceSignature=state.sourceState.items.map(source=>`${source.revision_id}/${source.sha256}`).join("|");
  useEffect(()=>{
    if(!conversation||conversation.workspace_id!==ws||!['ready','empty'].includes(state.sourceState.status)||scopeHydrated.current===scope)return;
    state.replaceSourceSelection?.(scopeRefs);scopeHydrated.current=scope;
  },[scope,sourceSignature,state.sourceState.status,conversation]);
  const sourcesReady=["ready","empty"].includes(state.sourceState.status);
  const currentReview=conversationReviewMatches(reviewState,conversation,ws)?reviewState:null;
  const currentMission=state.missionSnapshot?.mission??state.selectedMission;
  const reviewReady=!conversation?.mission_id||Boolean(currentReview&&currentMission?.mission_id===conversation.mission_id&&currentReview.missionStateVersion>=currentMission.state_version);
  const taskHistoryOnly=conversationUsesTaskHistory(state,reviewReady?currentReview?.reviewStates??null:null);
  useEffect(()=>{if(!historyTouched.current)setHistoryIds(recentConversationHistory(messages,taskHistoryOnly));},[messages,taskHistoryOnly]);
  const unsupportedHistory=taskHistoryOnly?messages.filter(message=>historyIds.includes(message.message_id)&&!message.task_message):[];
  const unavailableSources=(sourcesReady?scopeRefs:[]).filter(ref=>!state.sourceState.items.some(source=>sourceIdentityEquals(sourceIdentityFromRevision(source),ref)));
  const sourceRefs=[...state.selectedSourceRefs,...unavailableSources];
  const active=turn?.status==='queued'||turn?.status==='running'||state.runSnapshot?.status==='queued'||state.runSnapshot?.status==='running';
  useEffect(()=>{
    if(!active||!conversation||pendingId)return;
    const timer=setInterval(()=>{void readCurrent(conversation.workspace_id,conversation.conversation_id).catch(e=>setError(conversationError(e)));},1600);
    return()=>clearInterval(timer);
  },[active,conversation?.conversation_id,pendingId,readCurrent]);

  useEffect(()=>{
    if(conversation&&state.runSnapshot?.mission_id===conversation.mission_id&&!pendingId)void readCurrent(conversation.workspace_id,conversation.conversation_id).catch(e=>setError(conversationError(e)));
  },[state.runSnapshot?.run_id,state.runSnapshot?.status,Boolean(state.runSnapshot?.final_output),conversation?.conversation_id,readCurrent]);

  const newConversation=()=>{
    if(mutex.current)return;
    generation.current++;setConversation(null);conversationRef.current=null;setMessages([]);setTurn(null);setText("");setReferences([]);setScopeRefs([]);setHistoryIds([]);setError("");setPendingId(null);setReadyScope("");scopeHydrated.current="";historyTouched.current=false;
    stateRef.current.clearMission?.();stateRef.current.replaceSourceSelection?.([]);if(ws)sessionStorage.removeItem(conversationKey(ws));
  };
  const ensureWorkspace=async()=>{
    if(workspaceRef.current)return workspaceRef.current;
    const all=await fetchWorkspaces();if(workspaceRef.current!==null)throw new Error("工作区选择已变化，本次消息尚未发送。");if(all.length>1)throw new Error("请先从左侧选择工作区，消息草稿已保留。");
    let value=all[0];
    if(!value){
      if(sessionStorage.getItem('contextox.default-workspace-create-unknown'))throw new Error("上次创建本地工作区结果未知。请从左侧核对工作区列表并明确选择，不能盲目重建。");
      sessionStorage.setItem('contextox.default-workspace-create-unknown','true');
      try{value=await createWorkspace('本地工作区');sessionStorage.removeItem('contextox.default-workspace-create-unknown');}
      catch(e){if(e instanceof ApiRequestError && e.code && e.code!=='workspace_create_outcome_unknown')sessionStorage.removeItem('contextox.default-workspace-create-unknown');throw e;}
    }
    if(workspaceRef.current!==null)throw new Error("工作区选择已变化，创建结果请在工作区列表核对。");
    const initialized=new Promise<void>((resolve,reject)=>{workspaceInitialization.current={workspaceId:value.workspace_id,resolve,reject};});
    onWorkspace(value);writeSelectedWorkspaceId(value.workspace_id);
    await initialized;
    return value.workspace_id;
  };
  const create = async (workspaceId: string, missionId?: string) => {
    const ticket=generation.current;
    const request:CreateRequest=pendingCreate ?? {client_request_id:crypto.randomUUID(),title:'新对话',mission_id:missionId ?? null,source_refs:missionId ? stateRef.current.missionState.items.find(item=>item.mission_id===missionId)?.source_refs ?? [] : stateRef.current.selectedSourceRefs};
    sessionStorage.setItem(createKey(workspaceId),JSON.stringify(request));setPendingCreate(request);
    let value:WorkspaceConversation;
    try {value=await createConversation(workspaceId,request);} catch(e) {
      if(e instanceof ApiRequestError&&[400,403,422].includes(e.status)&&!e.code?.includes('unknown')){sessionStorage.removeItem(createKey(workspaceId));setPendingCreate(null);}
      throw e;
    }
    if(!conversationBelongsTo(value,workspaceId))throw new Error('新对话归属不一致。');
    if(workspaceRef.current!==workspaceId||ticket!==generation.current)throw new Error('会话选择已变化，旧创建结果请在原工作区核对。');
    sessionStorage.removeItem(createKey(workspaceId));setPendingCreate(null);setKnownConversation(value);setScopeRefs(value.source_refs ?? []);stateRef.current.replaceSourceSelection?.(value.source_refs ?? []);scopeHydrated.current=`${workspaceId}/${value.conversation_id}`;setReadyScope(`${workspaceId}/${value.conversation_id}`);return value;
  };
  const attachMission=async(missionId:string)=>{
    if(!ws||mutex.current)return;mutex.current=true;setLoading(true);
    try{const existing=list.find(value=>value.mission_id===missionId);await stateRef.current.selectMission(missionId);await openConversation(existing??await create(ws,missionId));}catch(e){if(workspaceRef.current===ws)setError(conversationError(e));}finally{mutex.current=false;setLoading(false);}
  };
  const submit=async()=>{
    if(mutex.current||active||pendingId||!storageReady||!reviewReady||!text.trim())return;
    const body=text;const selectedReferences=references;mutex.current=true;setSending(true);setError("");
    const originalWs=workspaceRef.current;let operationWorkspace=originalWs;let operationGeneration=generation.current;let submittedTo:{workspaceId:string;conversationId:string}|null=null;
    try{
      const settings=await fetchDeepSeekSettings();if(workspaceRef.current!==originalWs||operationGeneration!==generation.current)return;if(!settings.configured){setNeedsConnection(true);return;}setNeedsConnection(false);
      const workspaceId=await ensureWorkspace();operationWorkspace=workspaceId;
      if(originalWs===null)operationGeneration=generation.current;
      if(workspaceRef.current!==workspaceId||operationGeneration!==generation.current)return;
      const value=conversationRef.current??await create(workspaceId);
      if(value.workspace_id!==workspaceId)throw new Error('当前对话归属不一致。');
      const latestReview=conversationReviewMatches(reviewRef.current,value,workspaceId)?reviewRef.current:null;
      const latestMission=stateRef.current.missionSnapshot?.mission??stateRef.current.selectedMission;
      if(value.mission_id&&(!latestReview||latestMission?.mission_id!==value.mission_id||latestReview.missionStateVersion<latestMission.state_version))throw new Error('回答审阅状态尚未完整核对，本轮未发送。请重新读取问题与回答。');
      const request:ConversationMessageSendRequest={kind:'message',client_request_id:crypto.randomUUID(),expected_state_version:value.state_version,content:body,references:selectedReferences,history_messages:selectedConversationHistory(messages,historyIds,conversationUsesTaskHistory(stateRef.current,latestReview?.reviewStates??null)),source_refs:sourceRefs,provider_send_confirmed:true,goal:value.goal ?? null};
      if(workspaceRef.current!==workspaceId||operationGeneration!==generation.current)return;
      submittedTo={workspaceId,conversationId:value.conversation_id};
      sessionStorage.setItem(submissionKey(workspaceId,value.conversation_id),request.client_request_id);setPendingId(request.client_request_id);
      const receipt=await sendConversationMessage(workspaceId,value.conversation_id,request);
      await applyReceipt(receipt,workspaceId,value.conversation_id);
      if(workspaceRef.current===workspaceId&&conversationRef.current?.conversation_id===value.conversation_id){setText(currentText=>currentText===body?'':currentText);setReferences([]);historyTouched.current=false;await readCurrent(workspaceId,value.conversation_id);}
    }catch(e){
      if(operationWorkspace!==workspaceRef.current||operationGeneration!==generation.current)return;
      setError(conversationError(e));
      if(e instanceof ApiRequestError&&[400,403,404,409,422].includes(e.status)&&!e.code?.includes('unknown')){if(submittedTo)sessionStorage.removeItem(submissionKey(submittedTo.workspaceId,submittedTo.conversationId));if(submittedTo?.conversationId===conversationRef.current?.conversation_id)setPendingId(null);}
      if(originalWs===null)setText(body);
    }finally{mutex.current=false;setSending(false);}
  };
  const reconcile=async()=>{
    const value=conversationRef.current;if(!value||!pendingId||mutex.current)return;mutex.current=true;setSending(true);
    try{await applyReceipt(await fetchConversationSubmission(value.workspace_id,value.conversation_id,pendingId),value.workspace_id,value.conversation_id);await readCurrent(value.workspace_id,value.conversation_id);setError("");}catch(e){setError(conversationError(e));}finally{mutex.current=false;setSending(false);}
  };
  const reconcileCreate=async()=>{if(!ws||!pendingCreate||mutex.current)return;mutex.current=true;setLoading(true);try{const value=await create(ws);await openConversation(value);}catch(e){if(workspaceRef.current===ws)setError(conversationError(e));}finally{mutex.current=false;setLoading(false);}};
  const cancel=async()=>{if(mutex.current||!conversation)return;mutex.current=true;setSending(true);try{if(turn&&['queued','running'].includes(turn.status))setTurn(await cancelDiscussionTurn(conversation.workspace_id,conversation.conversation_id,turn.turn_id));else await stateRef.current.cancelActiveRun();await readCurrent(conversation.workspace_id,conversation.conversation_id);}catch(e){setError(conversationError(e));}finally{mutex.current=false;setSending(false);}};
  const more=async()=>{if(!conversation||!cursor||loading)return;setLoading(true);try{await readCurrent(conversation.workspace_id,conversation.conversation_id,cursor);}catch(e){setError(conversationError(e));}finally{setLoading(false);}};
  return {list:list.filter(value=>value.workspace_id===ws),conversation:conversation?.workspace_id===ws?conversation:null,messages:conversation?.workspace_id===ws?messages:[],cursor,text:loadedWorkspace===ws?text:"",setText,references:loadedWorkspace===ws?references:[],setReferences,addReference:(ref:MessageReference)=>setReferences(old=>old.length<8&&!old.some(item=>JSON.stringify(item)===JSON.stringify(ref))?[...old,ref]:old),historyIds,setHistoryIds:(ids:string[])=>{historyTouched.current=true;setHistoryIds(ids);},taskHistoryOnly,unsupportedHistory,updateReviewState,reviewReady,turn,error,loading,sending,active,needsConnection,pendingId,pendingCreate,unavailableSources,sourceRefs,ready:(!conversation||readyScope===scope)&&reviewReady,select:(value:WorkspaceConversation)=>mutex.current?Promise.resolve():openConversation(value),newConversation,attachMission,submit,reconcile,reconcileCreate,refresh,cancel,more,removeUnavailable:(ref:SourceIdentity)=>setScopeRefs(old=>old.filter(item=>!sourceIdentityEquals(item,ref)))};
}
export type ConversationDialogueState=ReturnType<typeof useConversationDialogue>;

export function taskAnalysisLabel(state: Pick<Path2WorkbenchState, "runSnapshot" | "latestDraft" | "clarifications">): string {
  if (!state.runSnapshot) return "";
  if (state.latestDraft?.status === "in_review" && state.clarifications.length === 0) return "候选草案待审核";
  return statusLabel(state.runSnapshot.status);
}

export function ConversationDialogue({state,d,onReference,onSources,onHistory}: {state:Path2WorkbenchState;d:ConversationDialogueState;onReference:(ref:MessageReference)=>void;onSources:()=>void;onHistory:()=>void}) {
  const scroll=useRef<HTMLDivElement>(null);const nearEnd=useRef(true);const scopePanel=useRef<HTMLDetailsElement>(null);
  const [scopeOpen,setScopeOpen]=useState(false);
  useEffect(()=>{nearEnd.current=true;},[d.conversation?.conversation_id]);
  useEffect(()=>{setScopeOpen(false);},[d.conversation?.conversation_id,state.workspaceId]);
  useEffect(()=>{if(scopeOpen)scopePanel.current?.scrollIntoView({block:"nearest"});},[scopeOpen]);
  useEffect(()=>{if(scroll.current&&nearEnd.current)scroll.current.scrollTop=scroll.current.scrollHeight;},[d.messages.at(-1)?.message_id,d.turn?.status]);
  const blocked=Boolean(state.workspaceId)&&!["ready","empty"].includes(state.sourceState.status)||d.loading||d.sending||d.active||Boolean(d.pendingId)||Boolean(d.pendingCreate)||!d.ready||d.unavailableSources.length>0||d.unsupportedHistory.length>0;
  return <div className="task-conversation continuous-conversation">
    {d.conversation?.goal && <div className="conversation-goal"><span>当前已明确目标</span><p>{d.conversation.goal.text}</p></div>}
    <div className="conversation-messages" ref={scroll} onScroll={event=>{const el=event.currentTarget;nearEnd.current=el.scrollHeight-el.scrollTop-el.clientHeight<80;}} aria-label="连续对话" aria-busy={d.loading}>
      {d.cursor&&<button disabled={d.loading} onClick={()=>void d.more()}>加载更早消息</button>}
      {!d.messages.length&&<div className="conversation-welcome"><p>你好，有什么想一起弄清楚的？</p><p>可以先聊业务问题，也可以从手头的资料开始。我会把相关资料和整理过程放在工作区，方便你随时核对。</p></div>}
      {d.messages.map(message=><article className={`conversation-message message-${message.role}`} key={message.message_id}><header><strong>{message.role==='user'?'你':'数契 Agent'}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</time></header><p>{message.content}</p>{(message.references ?? []).map((ref,i)=><button className="reference-chip" key={i} onClick={()=>onReference(ref)}>{referenceLabel(ref,state.sourceState.items)}</button>)}</article>)}
      {d.turn&&<div className="conversation-activity" role="status"><strong>{discussionStatusLabel(d.turn.status)}</strong>{d.turn.error_code&&<p>讨论未完成：{d.turn.error_code}</p>}{d.turn.handoff_error_code&&<p>讨论结束，但任务交接未完成：{d.turn.handoff_error_code}。请先核对当前状态。</p>}</div>}
      {state.runSnapshot&&<div className="conversation-activity"><strong>任务分析：{taskAnalysisLabel(state)}</strong>{state.runSnapshot.error_code&&<p>{state.runSnapshot.error_code}</p>}<button onClick={onHistory}>查看执行过程</button></div>}
      {d.conversation?.mission_id&&<ConversationAnswers key={`${d.conversation.workspace_id}/${d.conversation.conversation_id}`} state={state} conversation={d.conversation} suggestions={d.turn?.output?.answer_suggestions ?? []} onUpdated={d.refresh} onReviewState={d.updateReviewState}/>}
    </div>
    <form className="conversation-composer" onSubmit={event=>{event.preventDefault();nearEnd.current=true;void d.submit();}}>
      {d.error&&<div className="conversation-error" role="alert">{d.error}<button type="button" onClick={()=>void d.refresh()}>刷新并核对</button></div>}
      {d.pendingId&&<div role="status"><p>发送结果待核对，原请求标识已保留。</p><button type="button" disabled={d.sending} onClick={()=>void d.reconcile()}>核对发送结果</button></div>}
      {d.pendingCreate&&<div role="status"><p>新对话创建结果待核对，未自动发送模型请求。</p><button type="button" disabled={d.loading} onClick={()=>void d.reconcileCreate()}>核对新对话创建</button></div>}
      {d.needsConnection&&<div className="conversation-connect"><p>先连接模型，消息草稿已保留。</p><ModelSettings onSend={()=>void d.submit()} canSend={Boolean(d.text.trim())}/></div>}
      {!d.reviewReady&&<p role="status">正在核对回答审阅状态。若卡片保留了修改，请先在卡片中重新读取并核对；本轮尚未发送。</p>}
      {d.unsupportedHistory.length>0&&<p role="alert">已选的 {d.unsupportedHistory.length} 条历史属于任务前或澄清讨论，本轮任务分析不能携带。请展开历史选择取消这些项；系统未改动你的选择。</p>}
      <details ref={scopePanel} open={scopeOpen} onToggle={event=>setScopeOpen(event.currentTarget.open)} className="conversation-scope-picker"><summary>本轮资料 {d.sourceRefs.length} 份 · 历史 {d.historyIds.length} 条</summary><p>这里只使用你明确勾选的资料，系统不会自动带上工作区全部资料。</p>{state.sourceState.items.length===0?<p className="conversation-scope-empty">当前工作区还没有资料，请先从本机导入。</p>:<div role="group" aria-label="选择本轮资料">{state.sourceState.items.map(source=><label className="history-choice" key={source.revision_id}><input type="checkbox" checked={state.selectedSourceIds.includes(source.revision_id)} disabled={d.active||Boolean(d.pendingId)||(!state.selectedSourceIds.includes(source.revision_id)&&d.sourceRefs.length>=8)} onChange={()=>state.toggleSource(source.revision_id)}/><span>{source.original_name} · {state.selectedSourceIds.includes(source.revision_id)?"本轮已选":"未选择"}</span></label>)}</div>}{d.unavailableSources.map(ref=><p key={ref.revision_id}>来源版本已不可用：{ref.revision_id.slice(0,8)}<button type="button" onClick={()=>d.removeUnavailable(ref)}>从本轮范围移除</button></p>)}<button type="button" className="conversation-import-source" onClick={onSources}>从本机导入新资料</button><p className="conversation-history-heading">本轮携带的对话历史（最多 4 条）</p>{d.messages.map(message=><label className="history-choice" key={message.message_id}><input type="checkbox" checked={d.historyIds.includes(message.message_id)} disabled={Boolean(d.pendingId)||(!d.historyIds.includes(message.message_id)&&d.historyIds.length>=4)} onChange={event=>d.setHistoryIds(event.target.checked?[...d.historyIds,message.message_id]:d.historyIds.filter(id=>id!==message.message_id))}/><span>{message.role==='user'?'你':'Agent'}：{message.content.slice(0,90)}{d.taskHistoryOnly&&!message.task_message?"（本轮任务分析不可携带）":""}</span></label>)}</details>
      {d.references.map((ref,index)=><button type="button" className="reference-chip" key={index} disabled={Boolean(d.pendingId)} onClick={()=>d.setReferences(d.references.filter((_,i)=>i!==index))}>{referenceLabel(ref,state.sourceState.items)} ×</button>)}
      <label className="sr-only" htmlFor="conversation-input">与 Agent 对话</label><textarea id="conversation-input" rows={3} maxLength={4096} value={d.text} placeholder="说说你想弄清什么，也可以先添加资料" onChange={event=>d.setText(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();if(!blocked&&d.text.trim()){nearEnd.current=true;void d.submit();}}}}/>
      <div className="conversation-send-row"><button type="button" aria-expanded={scopeOpen} onClick={()=>setScopeOpen(value=>!value)}>＋ 资料 · {d.sourceRefs.length}</button>{d.active?<button type="button" disabled={d.sending} onClick={()=>void d.cancel()}>停止分析</button>:<button className="path2-primary-button" disabled={blocked||!d.text.trim()} type="submit">{d.sending?'正在发送…':'发送 ↑'}</button>}</div><p className="composer-scope">{conversationSendGuidance(Boolean(d.conversation?.mission_id),d.sourceRefs.length,d.active)}</p>
    </form>
  </div>;
}
