import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./generated/api";
import { ApiRequestError, fetchTaskMessages, fetchTaskRuns, fetchRunSnapshot, sendTaskMessage, fetchMessageSubmission, readSourceExcerpt, fetchSourceArtifact } from "./api/client";
import { Path2RunDetails, replayTerminalRunEvents, browserRunEventSourceFactory, createRunEventState, statusLabel, missionStatusLabel, sourceIdentityEquals, type Path2WorkbenchState, type RunSnapshot } from "./Path2Workbench";

type Message = components["schemas"]["TaskMessage"];
export type MessageReference = Message["references"][number];
type SendRequest = components["schemas"]["TaskMessageSendRequest"];
type RunSummary = components["schemas"]["TaskRunSummary"];
type SourceIdentity = components["schemas"]["SourceIdentity"];

// Collect exact identities carried by the approved context packet and references.
export function dialogueSources(...payloads: unknown[]): SourceIdentity[] {
  const sources: SourceIdentity[] = [];
  const visit = (value: unknown): void => {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) { value.forEach(visit); return; }
    const item = value as Record<string, unknown>;
    if (["workspace_id", "source_id", "revision_id", "sha256"].every(key => typeof item[key] === "string")) {
      const ref = {workspace_id:item.workspace_id, source_id:item.source_id, revision_id:item.revision_id, sha256:item.sha256} as SourceIdentity;
      if (!sources.some(old => sourceIdentityEquals(old, ref))) sources.push(ref);
    } else Object.values(item).forEach(visit);
  };
  payloads.forEach(visit);
  return sources;
}

export function referenceLabel(ref: MessageReference): string {
  switch (ref.kind) {
    case "draft_field": return `字段 ${ref.field_key} · v${ref.draft_version}`;
    case "draft_relationship": return `关系 ${ref.relationship_key} · v${ref.draft_version}`;
    case "source_column": return `${ref.table_id || "根表"}.${ref.column_name}`;
    case "source_excerpt": {
      const locator = ref.evidence_ref.locator;
      return locator.kind === "json_pointer" ? `资料 ${locator.pointer || "/"}` :
        locator.kind === "text_lines" ? `资料行 ${locator.line_start}–${locator.line_end}` : `资料行 ${locator.row_start}–${locator.row_end}`;
    }
  }
}

export function dialogueError(error: unknown, operation: "read" | "send" = "send"): string {
  const code = error instanceof ApiRequestError ? error.code : null;
  return ({
    task_dialogue_not_implemented: "当前资料库尚未启用任务对话写入。已有历史仍可查看。",
    task_waiting_for_review: "任务正在等待业务裁决，请先查看待回应事项。",
    previous_outcome_unresolved: "上次分析的结果或用量仍未核清，暂时不能继续发送。",
    workspace_store_busy: "已有分析正在运行，请稍后再发送；草稿已保留。",
    state_conflict: "任务状态或历史内容已变化，请刷新并重新确认此次发送。",
    message_reference_stale: "引用的草案版本已变化，请重新选择当前字段或关系。",
    message_context_scope_mismatch: "所选历史或草案包含本轮未选资料，请调整范围。",
    message_context_too_large: "携带的历史过长，请减少所选消息。",
    message_submission_not_found: "尚未查到本次发送记录；这不能证明先前请求未被接受。请继续核对，或用保留的原请求重提。",
  } as Record<string, string>)[code ?? ""] ?? (code ? `读取或发送未完成（${code}），草稿已保留。` : operation === "read" ? "对话读取尚未核对完成，请刷新。已有执行结果可在任务与执行历史中核对。" : "连接中断，结果尚未确认。请核对原请求，不要重复发送。");
}

export function useTaskDialogue(state: Path2WorkbenchState) {
  const ws = state.workspaceId;
  const mid = state.selectedMission?.mission_id ?? null;
  const scope = `${ws}/${mid}`;
  const current = useRef(scope); current.current = scope;
  const stateRef = useRef(state); stateRef.current = state;
  const [loaded, setLoaded] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  const [runCursor, setRunCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState("");
  const [references, setReferences] = useState<MessageReference[]>([]);
  const [historyIds, setHistoryIds] = useState<string[]>([]);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const pendingRequest = useRef<SendRequest | null>(null);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const generation = useRef(0);
  const storageKey = `contextox-message-submission:${scope}`;
  const refresh = useCallback(async () => {
    if (!ws || !mid) return;
    const gen = ++generation.current;
    setLoading(true);
    try {
      const task = await stateRef.current.refreshTask();
      if (current.current !== scope || gen !== generation.current) return;
      if (!task || task.mission.workspace_id !== ws || task.mission.mission_id !== mid) throw new Error("task readback failed");
      const [mp, rp] = await Promise.all([fetchTaskMessages(ws, mid), fetchTaskRuns(ws, mid)]);
      if (current.current !== scope || gen !== generation.current) return;
      if ([...mp.items, ...rp.items].some(item => item.workspace_id !== ws || item.mission_id !== mid)) throw new Error("scope mismatch");
      setMessages(mp.items); setRuns(rp.items);
      setMessageCursor(mp.next_before_message_id); setRunCursor(rp.next_before_run_id);
      setLoaded(scope); setError("");
    } catch (e) { if (current.current === scope && gen === generation.current) setError(dialogueError(e, "read")); }
    finally { if (current.current === scope && gen === generation.current) setLoading(false); }
  }, [ws, mid, scope]);
  useEffect(() => {
    setError(""); setLoaded(""); setMessages([]); setRuns([]); setText(""); setReferences([]); setHistoryIds([]);
    setPendingId(null); pendingRequest.current = null; sendingRef.current = false; setSending(false);
    try { setPendingId(sessionStorage.getItem(storageKey)); } catch { setError("浏览器无法保留请求标识，发送已暂停。"); }
  }, [scope, storageKey]);
  const run = state.runSnapshot;
  // One effect covers both scope changes and Run transitions.
  useEffect(() => { void refresh(); }, [refresh, run?.run_id, run?.status, run?.final_output]);
  const [historyTouched, setHistoryTouched] = useState(false);
  useEffect(() => { setHistoryTouched(false); }, [scope]);
  useEffect(() => {
    if (historyTouched || loaded !== scope) return;
    const pair = messages.slice(-2);
    if (pair.length === 2 && pair[0].role === "user" && pair[1].role === "assistant") setHistoryIds(pair.map(m => m.message_id));
  }, [messages, historyTouched, loaded, scope]);

  const historyRunIds = [...new Set(messages.filter(m => historyIds.includes(m.message_id)).flatMap(m => m.run_id ? [m.run_id] : []))];
  const historyKey = JSON.stringify([scope, ...historyRunIds]);
  const [historyScope, setHistoryScope] = useState<{key: string; sources: SourceIdentity[]; issue: string}>({key:"", sources:[], issue:""});
  useEffect(() => {
    let disposed = false;
    if (!ws || !mid || loaded !== scope) return;
    void Promise.all(historyRunIds.map(id => fetchRunSnapshot(ws, mid, id))).then(snapshots => {
      if (disposed) return;
      if (snapshots.some((snapshot, i) => snapshot.workspace_id !== ws || snapshot.mission_id !== mid || snapshot.run_id !== historyRunIds[i])) throw new Error("scope mismatch");
      setHistoryScope({key:historyKey, sources:dialogueSources(snapshots.map(snapshot => snapshot.source_refs)), issue:""});
    }).catch(e => {if (!disposed) setHistoryScope({key:historyKey, sources:[], issue:dialogueError(e, "read")});});
    return () => {disposed = true;};
  }, [historyKey, loaded, runs]);
  const scopeReady = loaded === scope && historyScope.key === historyKey && !historyScope.issue;
  const requiredSources = dialogueSources(state.latestDraft, state.clarifications, references,
    messages.filter(m => historyIds.includes(m.message_id)).map(m => m.references), historyScope.key === historyKey ? historyScope.sources : []);
  const missingSources = requiredSources.filter(ref => !state.selectedSourceRefs.some(selected => sourceIdentityEquals(ref, selected)));

  const adopt = async (receipt: components["schemas"]["TaskMessageSendReceipt"]) => {
    if (current.current !== scope || receipt.run.workspace_id !== ws || receipt.run.mission_id !== mid || receipt.input_message.run_id !== receipt.run.run_id) return;
    sessionStorage.removeItem(storageKey);
    pendingRequest.current = null; setPendingId(null); setText(""); setReferences([]); setHistoryTouched(false);
    stateRef.current.adoptDialogueRun(receipt.run);
    await refresh();
  };
  const submit = async (repeat = false) => {
    if (!ws || !mid || sendingRef.current || (!repeat && (!scopeReady || missingSources.length > 0))) return;
    const mission = stateRef.current.missionSnapshot?.mission ?? stateRef.current.selectedMission;
    if (!mission) return;
    const request: SendRequest | null = repeat ? pendingRequest.current : {
      kind: "message", client_request_id: crypto.randomUUID(), expected_state_version: mission.state_version,
      content: text, references, history_messages: messages.filter(m => historyIds.includes(m.message_id)).map(m => ({message_id: m.message_id, sha256: m.sha256})),
      source_refs: stateRef.current.selectedSourceRefs, provider_send_confirmed: true,
    };
    if (!request || (!repeat && pendingId)) return;
    sendingRef.current = true; setSending(true);
    try {
      sessionStorage.setItem(storageKey, request.client_request_id);
      pendingRequest.current = request; setPendingId(request.client_request_id);
      const receipt = await sendTaskMessage(ws, mid, request);
      await adopt(receipt);
    } catch (e) {
      if (current.current !== scope) return;
      setError(dialogueError(e));
      if (e instanceof ApiRequestError && [404, 409, 422].includes(e.status) && !e.code?.includes("outcome_unknown")) {
        sessionStorage.removeItem(storageKey); pendingRequest.current = null; setPendingId(null);
      }
      if (e instanceof ApiRequestError && e.code === "task_dialogue_not_implemented") {
        sessionStorage.removeItem(storageKey); pendingRequest.current = null; setPendingId(null);
      }
    } finally { if (current.current === scope) { sendingRef.current = false; setSending(false); } }
  };
  const reconcile = async () => {
    if (!ws || !mid || !pendingId || sendingRef.current) return;
    sendingRef.current = true; setSending(true);
    try { await adopt(await fetchMessageSubmission(ws, mid, pendingId)); }
    catch (e) { if (current.current === scope) setError(dialogueError(e)); }
    finally { if (current.current === scope) { sendingRef.current = false; setSending(false); } }
  };
  const more = async (kind: "messages" | "runs") => {
    if (!ws || !mid || loading) return;
    const gen = generation.current; setLoading(true);
    try {
      if (kind === "messages" && messageCursor) {
        const page = await fetchTaskMessages(ws, mid, messageCursor);
        if (current.current !== scope || gen !== generation.current) return;
        if (page.items.some(item => item.workspace_id !== ws || item.mission_id !== mid)) throw new Error("scope");
        setMessages(old => [...page.items, ...old]); setMessageCursor(page.next_before_message_id);
      } else if (kind === "runs" && runCursor) {
        const page = await fetchTaskRuns(ws, mid, runCursor);
        if (current.current !== scope || gen !== generation.current) return;
        if (page.items.some(item => item.workspace_id !== ws || item.mission_id !== mid)) throw new Error("scope");
        setRuns(old => [...page.items, ...old]); setRunCursor(page.next_before_run_id);
      }
    } catch (e) { if (current.current === scope && gen === generation.current) setError(dialogueError(e, "read")); }
    finally { if (current.current === scope && gen === generation.current) setLoading(false); }
  };
  const addReference = (ref: MessageReference) => {
    if (references.length >= 8 || references.some(item => JSON.stringify(item) === JSON.stringify(ref))) return;
    setReferences(old => [...old, ref]);
  };
  return { messages: loaded === scope ? messages : [], runs: loaded === scope ? runs : [],
    loading, ready: loaded === scope, error, text: loaded === scope ? text : "", setText, references: loaded === scope ? references : [], setReferences, addReference,
    historyIds, setHistoryIds: (ids: string[]) => {setHistoryTouched(true); setHistoryIds(ids);},
    scopeReady, scopeIssue: historyScope.key === historyKey ? historyScope.issue : "", missingSources,
    pendingId, canRepeat: pendingRequest.current !== null, sending, submit, reconcile, refresh, more, messageCursor, runCursor };
}
export type DialogueState = ReturnType<typeof useTaskDialogue>;

export function TaskConversation({ state, dialogue: d, onReference, onHistory, onResults }:
  {state: Path2WorkbenchState; dialogue: DialogueState; onReference: (ref: MessageReference) => void; onHistory: () => void; onResults: () => void}) {
  const messageList = useRef<HTMLDivElement>(null);
  const nearEnd = useRef(true);
  useEffect(() => {
    const element = messageList.current;
    if (element && nearEnd.current) element.scrollTop = element.scrollHeight;
  }, [d.messages.at(-1)?.message_id]);
  const run = state.runSnapshot;
  const mission = state.missionSnapshot?.mission ?? state.selectedMission;
  const active = run?.status === "queued" || run?.status === "running";
  const finalizing = run?.status === "partial" && !run.final_output && state.runConnectionState !== "closed";
  const waiting = mission?.status === "waiting_for_human" || state.clarifications.length > 0 || state.latestDraft?.status === "in_review";
  const sourceAllowed = state.selectedSourceRefs.every(ref => mission?.source_refs.some(source => sourceIdentityEquals(source, ref)));
  const blocked = !mission || !d.ready || d.loading || !d.scopeReady || d.missingSources.length > 0 || active || finalizing || waiting || mission.status === "cancelled" || mission.status === "completed" || !sourceAllowed;
  if (!mission) return <div className="conversation-empty"><h3>围绕一个任务展开分析</h3><p>先在任务工作区描述目标并确认任务，再在这里提问、查看答复与引用。</p></div>;
  return <div className="task-conversation">
    <div className="conversation-context"><strong>{mission.title}</strong><span>任务：{d.pendingId ? "需核对发送结果" : missionStatusLabel(state)}</span><button onClick={onHistory}>执行历史</button></div>
    <div className="conversation-messages" ref={messageList} onScroll={e => {const el = e.currentTarget; nearEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;}} aria-label="当前任务对话" aria-busy={d.loading}>
      {d.messageCursor && <button disabled={d.loading} onClick={() => void d.more("messages")}>加载更早消息</button>}
      {!d.ready && d.loading && <p role="status">正在读取任务对话…</p>}
      {d.ready && d.messages.length === 0 && <p>尚无已保存的对话。发送一个具体问题开始分析。</p>}
      {d.messages.map(message => <article className={`conversation-message message-${message.role}`} key={message.message_id}>
        <header><strong>{message.role === "user" ? "你" : "数契 Agent"}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}</time></header>
        <p>{message.content}</p>
        {message.references.map((ref, index) => <button className="reference-chip" key={index} onClick={() => onReference(ref)}>{referenceLabel(ref)}</button>)}
      </article>)}
      {run && <div className="conversation-activity" role="status">
        <strong>{active ? "正在分析本轮问题…" : `本轮：${statusLabel(run.status)}`}</strong>
        {active ? <p>可以继续编辑草稿，当前分析结束后再发送。</p> : !run.final_output && <p>{finalizing ? "正在核对本轮答复与任务状态…" : waiting ? "本轮已产生待回应事项（系统状态）。" : "本轮未形成可读取的公开答复，可查看结构化结果。"}</p>}
        {run.error_code && <p>{run.error_code === "context_budget_exceeded" ? "本轮触及上下文预算，已停止；已有结果保留。" : `分析停止：${run.error_code}`}</p>}
        {state.latestDraft && <button onClick={onResults}>查看字段与关系草案 · v{state.latestDraft.version}</button>}
        {waiting && <p>需要业务裁决。当前版本尚未提供回答与批准入口，普通消息不能代替批准。</p>}
        {active && <button disabled={state.cancelAction.status === "submitting"} onClick={() => void state.cancelActiveRun()}>停止本轮分析</button>}
      </div>}
    </div>
    <form className="conversation-composer" onSubmit={event => {event.preventDefault(); void d.submit();}}>
      {(d.error || d.scopeIssue) && <div role="alert" className="conversation-error">{d.error || d.scopeIssue}<button type="button" disabled={d.loading} onClick={() => void d.refresh()}>刷新对话</button></div>}
      {finalizing && state.runConnectionState === "blocked" && <button type="button" onClick={() => void d.refresh()}>核对本轮结果</button>}
      {d.pendingId && !d.sending && <div role="status"><p>本次发送等待核对，原请求标识已保留。</p><button type="button" disabled={d.sending} onClick={() => void d.reconcile()}>核对发送结果</button>{d.canRepeat && <button type="button" disabled={d.sending} onClick={() => void d.submit(true)}>按原请求重提</button>}</div>}
      {d.references.map((ref, index) => <button type="button" className="reference-chip" key={index} disabled={Boolean(d.pendingId)} onClick={() => d.setReferences(d.references.filter((_, i) => i !== index))}>{referenceLabel(ref)} ×</button>)}
      {d.missingSources.length > 0 && <fieldset className="dialogue-missing-sources"><legend>本轮还需明确选择资料</legend><p>历史、引用或当前草案使用了以下资料。勾选后才纳入本轮模型范围。</p>{d.missingSources.map(ref => {
        const source = state.sourceState.items.find(item => sourceIdentityEquals(item, ref));
        const allowed = Boolean(source && mission.source_refs.some(item => sourceIdentityEquals(item, ref)));
        return <label className="history-choice" key={`${ref.revision_id}/${ref.sha256}`}><input type="checkbox" checked={false} disabled={!allowed || Boolean(d.pendingId)} onChange={() => state.toggleSource(ref.revision_id)}/><span>{source?.original_name ?? `来源 ${ref.revision_id.slice(0, 8)}`}{!allowed && "（当前任务不可选，请调整引用或历史并核对任务范围）"}</span></label>;
      })}</fieldset>}
      {!d.scopeReady && !d.scopeIssue && d.ready && <p role="status">正在核对历史使用的资料范围…</p>}
      <details><summary>携带 {d.historyIds.length} 条历史 · {state.selectedSourceRefs.length} 份资料</summary>
        <p>仅携带勾选的消息与资料；最多 4 条历史。可在资料页调整来源。</p>
        {state.selectedSourceRefs.map(ref => <p key={ref.revision_id}>{state.sourceState.items.find(s => s.revision_id === ref.revision_id)?.original_name ?? "已选来源"}</p>)}
        {d.messages.map(m => <label className="history-choice" key={m.message_id}><input type="checkbox" checked={d.historyIds.includes(m.message_id)} disabled={Boolean(d.pendingId) || (!d.historyIds.includes(m.message_id) && d.historyIds.length >= 4)} onChange={e => d.setHistoryIds(e.target.checked ? [...d.historyIds, m.message_id] : d.historyIds.filter(id => id !== m.message_id))}/><span>{m.role === "user" ? "你" : "Agent"}：{m.content.slice(0, 90)}</span></label>)}
      </details>
      <label className="sr-only" htmlFor="task-message-input">给当前任务发消息</label>
      <textarea id="task-message-input" rows={3} maxLength={4096} value={d.text} disabled={!d.ready || Boolean(d.pendingId)} placeholder="追问定义、解释字段，或引用关系继续分析…" onChange={e => d.setText(e.target.value)}/>
      <p className="composer-scope">发送会将消息、所选历史和资料范围交给模型，发起一轮有预算的分析。</p>
      {!sourceAllowed && <p role="status">所选资料超出当前任务范围，请在资料页调整。</p>}
      <button className="path2-primary-button" disabled={blocked || d.sending || Boolean(d.pendingId) || !d.text.trim()} type="submit">{d.sending ? "正在发送…" : active ? "分析结束后可发送" : waiting ? "等待业务裁决" : "发送并分析"}</button>
    </form>
  </div>;
}

export function TaskExecutionHistory({ state, dialogue: d }: {state: Path2WorkbenchState; dialogue: DialogueState}) {
  const [detail, setDetail] = useState<RunSnapshot | null>(null);
  const [error, setError] = useState("");
  const scope = `${state.workspaceId}/${state.selectedMission?.mission_id}`;
  const current = useRef(scope); current.current = scope;
  const selection = useRef(0);
  useEffect(() => {setDetail(null); setError(""); selection.current++;}, [scope]);
  const show = async (id: string) => {
    if (!state.workspaceId || !state.selectedMission) return;
    const generation = ++selection.current; setDetail(null); setError("");
    try {
      const next = await fetchRunSnapshot(state.workspaceId, state.selectedMission.mission_id, id);
      if (current.current === scope && generation === selection.current && next.workspace_id === state.workspaceId && next.mission_id === state.selectedMission.mission_id && next.run_id === id) setDetail(next);
    } catch (e) {if (current.current === scope && generation === selection.current) setError(dialogueError(e, "read"));}
  };
  return <section className="task-history"><h2>执行历史</h2><p>每次明确发送对应一轮分析。任务对话保留在右侧。</p>
    {d.error && <p role="alert">{d.error}</p>}{d.loading && <p role="status">正在读取…</p>}
    {d.runCursor && <button onClick={() => void d.more("runs")}>加载更早执行</button>}
    {d.ready && !d.runs.length && <p>这个任务还没有执行记录。</p>}
    {[...d.runs].reverse().map((run, index) => <button className="history-run" key={run.run_id} onClick={() => void show(run.run_id)}><strong>{new Date(run.created_at).toLocaleString()} · {statusLabel(run.status)}</strong><span>{run.has_final_output ? "已保存公开答复" : run.status === "waiting_for_human" ? "已产生待回应事项" : "未保存公开答复"}{run.error_code ? ` · ${run.error_code}` : ""}</span><small>执行 {d.runs.length - index} · {run.run_id.slice(0, 8)}</small></button>)}
    {error && <p role="alert">{error}</p>}
    {detail && <article className="history-detail"><h3>本轮执行详情</h3><p>{statusLabel(detail.status)} · {detail.run_id}</p><p>{detail.final_output ?? (detail.status === "waiting_for_human" ? "本轮已产生待回应事项，可展开结构化结果核对。" : "本轮没有已保存的公开答复。")}</p><details><summary>终止收据与结构化结果</summary><pre>{JSON.stringify({receipt:detail.terminal_receipt, draft:detail.draft, clarifications:detail.clarifications}, null, 2)}</pre></details>
      {<details><summary>公开执行事件</summary><RunHistoryDetails key={detail.run_id} state={state} run={detail}/></details>}
    </article>}
  </section>;
}

export function ReferenceInspector({ state, reference, onClose, onQuote }: {
  state: Path2WorkbenchState; reference: MessageReference; onClose: () => void; onQuote: (ref: MessageReference) => void;
}) {
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const key = JSON.stringify(reference);
  useEffect(() => {
    let disposed = false;
    setPreview(""); setError(""); setLoading(true);
    const read = async () => {
      try {
        if (reference.kind === "source_excerpt") {
          const expected = reference.evidence_ref;
          if (expected.workspace_id !== state.workspaceId) throw new Error("scope");
          const result = await readSourceExcerpt(expected.workspace_id, expected.revision_id, {locator: expected.locator});
          if (!sourceIdentityEquals(result.source_ref, expected) || JSON.stringify(result.source_ref.locator) !== JSON.stringify(expected.locator)) throw new Error("scope");
          if (!disposed) setPreview(result.text + (result.truncated ? "\n（片段已达到显示上限）" : ""));
        } else if (reference.kind === "source_column") {
          const result = await fetchSourceArtifact(reference.source_ref.workspace_id, reference.source_ref.revision_id);
          if (!sourceIdentityEquals(result.source_ref, reference.source_ref) || result.source_ref.workspace_id !== state.workspaceId) throw new Error("scope");
          const table = result.tables.find(t => t.table_id === reference.table_id);
          const column = table?.columns.find(c => c.name === reference.column_name);
          if (!column) throw new Error("missing");
          if (!disposed) setPreview(JSON.stringify(column, null, 2));
        } else {
          const draft = state.latestDraft;
          if (!draft || draft.draft_id !== reference.draft_id || draft.version !== reference.draft_version || draft.sha256 !== reference.draft_sha256) {
            if (!disposed) setError(`引用的是 v${reference.draft_version}。当前版本已变化或不可用，未自动替换为新版本；可在执行历史中核对该版本收据。`);
          } else {
            const item = reference.kind === "draft_field" ? draft.fields.find(f => f.field_key === reference.field_key) : draft.relationships.find(r => r.relationship_key === reference.relationship_key);
            if (!item) throw new Error("missing");
            if (!disposed) setPreview(JSON.stringify(item, null, 2));
          }
        }
      } catch (e) { if (!disposed) setError(dialogueError(e, "read")); }
      finally {if (!disposed) setLoading(false);}
    };
    void read(); return () => {disposed = true;};
  }, [key, state.workspaceId, state.latestDraft]);
  return <section className="reference-inspector" aria-label="引用定位"><header><h2>{referenceLabel(reference)}</h2><button onClick={onClose}>关闭引用</button></header>
    {loading && <p role="status">正在核对引用…</p>}{error && <p role="alert">{error}</p>}{preview && <pre>{preview}</pre>}
    {!loading && !error && <button onClick={() => onQuote(reference)}>将此引用加入下一条消息</button>}
  </section>;
}

function RunHistoryDetails({state, run}: {state: Path2WorkbenchState; run: RunSnapshot}) {
  const [events, setEvents] = useState(createRunEventState);
  const [connection, setConnection] = useState<Path2WorkbenchState["runConnectionState"]>("connecting");
  const [issue, setIssue] = useState<string | null>(null);
  useEffect(() => {
    if (run.status === "queued" || run.status === "running") return;
    return replayTerminalRunEvents(run, browserRunEventSourceFactory, setEvents, setConnection, setIssue);
  }, [run]);
  if ((run.status === "queued" || run.status === "running") && run.run_id === state.runSnapshot?.run_id) return <Path2RunDetails state={state}/>;
  return <Path2RunDetails state={{...state, runSnapshot:run, runEventState:events, runConnectionState:connection, runEventIssue:issue}}/>;
}
