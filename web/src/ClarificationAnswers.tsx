import { ApiRequestError, fetchAnswerImpact, fetchClarificationCases, saveClarificationAnswer, approveClarificationAnswer, fetchClarificationAnswer, fetchClarificationSubmission, sendTaskMessage, fetchMessageSubmission } from "./api/client";
import type { Path2WorkbenchState } from "./Path2Workbench";
import { EvidenceRefs, sourceIdentityEquals } from "./Path2Workbench";
import { useEffect, useRef, useState } from "react";
import type { components } from "./generated/api";
import type { DefinitionDraft, ClarificationRequest } from "./Path2Workbench";

type AnswerItem = components["schemas"]["AnswerItem"];
type AnswerVersion = components["schemas"]["ClarificationAnswerVersion"];
type Target = AnswerItem["targets"][number];

const dimensions = { meaning: "业务含义", value_type: "值类型", grain: "业务粒度", rule: "业务规则", time_basis: "时间口径", null_handling: "空值规则", join_rule: "关联规则", grain_notes: "关联粒度" };
export function targetKey(target: Target): string { return `${target.kind}/${target.key}/${target.property}`; }
export function draftTargets(draft: DefinitionDraft | null): Target[] {
  return draft ? [
    ...draft.fields.flatMap(field => (["meaning", "value_type", "grain", "rule", "time_basis", "null_handling"] as const).map(property => ({kind:"field" as const, key:field.field_key, property}))),
    ...draft.relationships.flatMap(relation => (["join_rule", "grain_notes"] as const).map(property => ({kind:"relationship" as const, key:relation.relationship_key, property}))),
  ] : [];
}
export function blankAnswers(request: ClarificationRequest): AnswerItem[] {
  return request.questions.map((_, question_index) => ({question_index, disposition:"answered", answer:"", respondent:"", basis:"", evidence_refs:[], targets:[], blocker:null}));
}
export function answerOmissions(items: AnswerItem[], count: number): string[] {
  const issues: string[] = [];
  if (items.length !== count || items.some((item, index) => item.question_index !== index)) issues.push("请完整回答原请求的每一道问题。");
  items.forEach((item, i) => {
    const missing: string[] = [];
    if (!item.respondent.trim()) missing.push("回答来源人或角色");
    if (!item.basis.trim()) missing.push("依据或不能确认的原因");
    if (item.disposition === "answered" && !item.answer?.trim()) missing.push("回答");
    if (item.disposition === "unknown") {
      if (!item.blocker?.resolver.trim()) missing.push("解决方");
      if (!item.blocker?.evidence_needed.trim()) missing.push("所需证据");
      if (!item.blocker?.next_action.trim()) missing.push("下一动作");
    }
    if (missing.length) issues.push(`第 ${i + 1} 题：补充${missing.join("、")}。`);
  });
  return issues;
}
export function AnswerReadback({answer, request}: {answer: AnswerVersion; request?: ClarificationRequest}) {
  return <div className="clarification-readback"><p>整份回答 v{answer.version} · {answer.created_at}</p>
    <p>这是回答人的声明；批准回答不代表批准最终草案。</p>
    {answer.items.map(item => <section className="path2-question" key={item.question_index}>
      <h4>{request?.questions[item.question_index]?.question ?? `第 ${item.question_index + 1} 题`} · {item.disposition === "unknown" ? "尚不能确认 · 未解决" : "已回答"}</h4>
      {item.answer && <p className="preserve-lines">{item.answer}</p>}
      <dl><dt>回答来源人或角色</dt><dd>{item.respondent}</dd><dt>依据 / 原因</dt><dd>{item.basis}</dd>
      {item.blocker && <><dt>解决方</dt><dd>{item.blocker.resolver}</dd><dt>所需证据</dt><dd>{item.blocker.evidence_needed}</dd><dt>下一动作</dt><dd>{item.blocker.next_action}</dd></>}</dl>
      {!item.targets.length ? <p>尚未绑定具体定义维度；问题身份和卡点仍保留。</p> : <ul>{item.targets.map(target => <li key={targetKey(target)}>{target.key} · {dimensions[target.property]}</li>)}</ul>}
      <details><summary>资料证据与精确身份</summary><pre>{JSON.stringify({evidence_refs:item.evidence_refs, question_index:item.question_index}, null, 2)}</pre></details>
    </section>)}
    <details><summary>回答版本身份</summary><pre>{JSON.stringify({origin_run_id:answer.origin_run_id, clarification_id:answer.clarification_id, version:answer.version, sha256:answer.sha256, review_draft:answer.review_draft, source_refs:answer.source_refs}, null, 2)}</pre></details>
  </div>;
}

export function AnswerForm({request, latest, draft, disabled, onSave, onDirty}: {
  request: ClarificationRequest; latest: AnswerVersion | null; draft: DefinitionDraft | null; disabled: boolean;
  onSave: (items: AnswerItem[]) => Promise<void>; onDirty: (dirty: boolean) => void;
}) {
  const [items, setItems] = useState<AnswerItem[]>(() => latest?.items ?? blankAnswers(request));
  const [dirty, setDirty] = useState(false);
  const [issues, setIssues] = useState<string[]>([]);
  const initial = useRef(latest?.sha256 ?? "");
  useEffect(() => {
    if (!dirty && initial.current !== (latest?.sha256 ?? "")) {setItems(latest?.items ?? blankAnswers(request)); initial.current = latest?.sha256 ?? "";}
  }, [latest, request, dirty]);
  useEffect(() => {
    onDirty(dirty);
    const beforeUnload = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = "";}};
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty, onDirty]);
  const update = (index: number, value: Partial<AnswerItem>) => {setDirty(true); setItems(old => old.map((item, i) => i === index ? {...item, ...value} : item));};
  const available = draftTargets(draft);
  return <form className="clarification-answer-form" onSubmit={event => {event.preventDefault(); const missing = answerOmissions(items, request.questions.length); setIssues(missing); if (!missing.length) void onSave(items).then(() => {setDirty(false);}).catch(() => { /* Parent retains input and the original request identifier. */ });}}>
    {latest && <p role="status">修改后保存为整份新版本，需要重新批准。旧版本保留供历史核对。</p>}
    {request.questions.map((question, index) => {
      const item = items[index];
      return <fieldset disabled={disabled} key={index}><legend>{index + 1}. {question.question}</legend>
        <p>{question.why_needed}</p><p>{question.blocking_impact === "blocking" ? "阻塞性问题" : "非阻塞问题"} · {question.expected_answer_type}</p>
        {question.suggested_owner_role && <p>建议回答角色：{question.suggested_owner_role}</p>}
        {question.related_definition_paths.length > 0 && <p>原关联路径：{question.related_definition_paths.join("、")}</p>}
        {question.evidence_requested.length > 0 && <p>问题所需证据：{question.evidence_requested.join("；")}</p>}
        {question.examples_or_options.length > 0 && <p>示例或选项：{question.examples_or_options.join("；")}</p>}
        <details><summary>原问题资料证据 / 可选回答引用</summary><pre>{JSON.stringify(question.source_refs, null, 2)}</pre>{question.source_refs.map((ref, refIndex) => <label className="history-choice" key={refIndex}><input type="checkbox" checked={item.evidence_refs.some(old => JSON.stringify(old) === JSON.stringify(ref))} disabled={item.evidence_refs.length >= 8 && !item.evidence_refs.some(old => JSON.stringify(old) === JSON.stringify(ref))} onChange={event => update(index, {evidence_refs:event.target.checked ? [...item.evidence_refs, ref] : item.evidence_refs.filter(old => JSON.stringify(old) !== JSON.stringify(ref))})}/><span>将原证据 {refIndex + 1} 作为本回答依据</span></label>)}</details>
        <label>回答状态<select value={item.disposition} onChange={event => update(index, event.target.value === "unknown" ? {disposition:"unknown", answer:null, blocker:{resolver:"", evidence_needed:"", next_action:""}} : {disposition:"answered", answer:"", blocker:null})}><option value="answered">可以回答</option><option value="unknown">尚不能确认</option></select></label>
        {item.disposition === "answered" ? <label>回答<textarea required maxLength={2048} value={item.answer ?? ""} onChange={event => update(index, {answer:event.target.value})}/></label> : <p>批准后仍是未解决卡点。填写解决安排，不代表已向对方分派。</p>}
        <label>回答来源人或角色<input required maxLength={128} value={item.respondent} onChange={event => update(index, {respondent:event.target.value})}/></label>
        <label>{item.disposition === "unknown" ? "尚不能确认的原因" : "回答依据"}<textarea required maxLength={2048} value={item.basis} onChange={event => update(index, {basis:event.target.value})}/></label>
        {item.blocker && ([['resolver','解决方',128],['evidence_needed','所需证据',2048],['next_action','下一动作',2048]] as const).map(([key,label,maxLength]) => <label key={key}>{label}<textarea required maxLength={maxLength} value={item.blocker![key]} onChange={event => update(index, {blocker:{...item.blocker!, [key]:event.target.value}})}/></label>)}
        <details><summary>可选：绑定影响的定义维度（{item.targets.length} / 20）</summary><p>没有合适的字段或关系时可以不选；原问题仍保留。程序只保护显式绑定的未知维度。</p>
          {[...available, ...item.targets.filter(target => !available.some(option => targetKey(option) === targetKey(target)))].map(target => {
            const selected = item.targets.some(old => targetKey(old) === targetKey(target));
            const stale = !available.some(option => targetKey(option) === targetKey(target));
            return <label className="history-choice" key={targetKey(target)}><input type="checkbox" checked={selected} disabled={!selected && item.targets.length >= 20} onChange={event => update(index, {targets:event.target.checked ? [...item.targets, target] : item.targets.filter(old => targetKey(old) !== targetKey(target))})}/><span>{target.key} · {dimensions[target.property]}{stale && "（当前草案已不存在，请移除或重新绑定）"}</span></label>;
          })}
        </details>
      </fieldset>;
    })}
    {issues.length > 0 && <div role="alert"><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></div>}
    <button className="path2-primary-button" disabled={disabled || !draft} type="submit">保存整份回答</button><p>保存不会调用模型。所有问题完整后才能保存。</p>
  </form>;
}

type DefinitionValue = components["schemas"]["AnswerImpactChange"]["before"];
export function DefinitionBusinessSummary({value}: {value: DefinitionValue}) {
  if (!value) return <p>无此字段或关系</p>;
  const rows: [string, string | null][] = "field_key" in value
    ? [["字段名称", value.name], ...(["meaning", "value_type", "grain", "rule", "time_basis", "null_handling"] as const).map(property => [dimensions[property], value[property]] as [string, string | null])]
    : [["左侧表及关联列", `${value.left.table_id || "根表"} · ${value.left.columns.join("、") || "尚未选择关联列"}`],
       ["右侧表及关联列", `${value.right.table_id || "根表"} · ${value.right.columns.join("、") || "尚未选择关联列"}`],
       ["观察到的对应关系", ({one_to_one:"一对一", one_to_many:"一对多", many_to_one:"多对一", many_to_many:"多对多", unknown:"尚未确认"})[value.observed_cardinality]],
       [dimensions.join_rule, value.join_rule], [dimensions.grain_notes, value.grain_notes]];
  return <div><dl>{rows.map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text ?? "尚未确认"}</dd></div>)}</dl>
    <p>证据状态：{({observed:"资料观察", candidate:"候选，尚未批准", conflict:"存在冲突", unknown:"尚未确认"})[value.evidence_status]}</p>
    {value.unknowns.length > 0 && <section><h6>未解决问题</h6><ul>{value.unknowns.map((item,index) => <li key={index}>{item.reason}</li>)}</ul></section>}
    {"risks" in value && value.risks.length > 0 && <section><h6>关系风险</h6><ul>{value.risks.map((risk,index) => <li key={index}>{risk}</li>)}</ul></section>}
  </div>;
}

export function AnswerImpactView({workspaceId, missionId, runId}: {workspaceId:string; missionId:string; runId:string}) {
  const [impact, setImpact] = useState<components["schemas"]["AnswerImpact"] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {let disposed = false; setImpact(null); setError("");
    void fetchAnswerImpact(workspaceId, missionId, runId).then(value => {
      if (value.approved_answers.some(snapshot => !clarificationCaseMatches({request:snapshot.request,request_sha256:snapshot.answer.request_sha256,latest_answer:snapshot.answer,latest_approval:snapshot.approval,review_state:"approved"},workspaceId,missionId)) || [value.before_draft,value.after_draft].some(draft => draft && (draft.workspace_id!==workspaceId || draft.mission_id!==missionId))) throw new Error("scope mismatch");
      if (!disposed) setImpact(value);
    }).catch(e => {if (!disposed) setError(clarificationError(e));});
    return () => {disposed = true;};
  }, [workspaceId, missionId, runId]);
  if (error) return <p role="alert">{error}</p>;
  if (!impact) return <p role="status">正在读取本轮回答及草案变化…</p>;
  if (!impact.approved_answers.length) return <p>本轮未采用批准回答。</p>;
  return <section className="answer-impact"><h3>本轮采用的回答与草案变化</h3>
    <p>{({no_result:"本轮尚无新草案结果", partial:"本轮仅产生部分结果", available:"本轮已保存草案变化"})[impact.result_state]}。回答版本与本轮固定绑定，后来的修改不会替换此处。</p>
    {impact.approved_answers.map(snapshot => <details key={`${snapshot.answer.origin_run_id}/${snapshot.answer.clarification_id}`}><summary>已批回答 v{snapshot.answer.version} · {snapshot.request.questions.length} 题</summary><AnswerReadback answer={snapshot.answer} request={snapshot.request}/></details>)}
    {[true, false].map(related => <section key={String(related)}><h4>{related ? "与本次回答相关" : "其他分析变化"}</h4>
      {impact.changes.filter(change => Boolean(change.question_refs.length) === related).map(change => <details key={`${change.kind}/${change.key}`}><summary>{change.key} · {({added:"新增",changed:"变化",removed:"移除"})[change.change]}</summary>
        {change.question_refs.length > 0 && <p>相关已批回答：{change.question_refs.map(ref => impact.approved_answers.find(snapshot => snapshot.request.run_id===ref.origin_run_id && snapshot.request.clarification_id===ref.clarification_id)?.request.questions[ref.question_index]?.question ?? `第 ${ref.question_index + 1} 题（原问题待核对）`).join("；")}。关联不代表该候选已获批准。</p>}
        <div className="answer-diff"><div><h5>分析前</h5><DefinitionBusinessSummary value={change.before}/></div><div><h5>本轮结果</h5><DefinitionBusinessSummary value={change.after}/></div></div>
        <details><summary>诊断：完整对象与证据身份</summary><pre>{JSON.stringify(change,null,2)}</pre></details>
      </details>)}
    </section>)}
    <h4>剩余卡点 · {impact.remaining_blockers.length}</h4>
    {impact.remaining_blockers.map(item => <article key={`${item.origin_run_id}/${item.clarification_id}/${item.question_index}`}><strong>{item.question} · 未解决</strong><dl><dt>解决方</dt><dd>{item.blocker.resolver}</dd><dt>所需证据</dt><dd>{item.blocker.evidence_needed}</dd><dt>下一动作</dt><dd>{item.blocker.next_action}</dd></dl></article>)}
  </section>;
}

type Case = components["schemas"]["ClarificationCase"];
type Receipt = components["schemas"]["ClarificationSubmissionReceipt"];
const reviewLabels = {awaiting_answer:"等待整份回答", awaiting_approval:"等待整份批准", approved:"整份回答已批准", stale:"需重新核对"};
export function clarificationError(error: unknown): string {
  if (!(error instanceof ApiRequestError)) return "连接中断或回读不完整。输入已保留，请核对原请求结果。";
  return ({state_conflict:"任务状态已变化。请刷新比较，本地输入仍保留。", clarification_target_stale:"选择的定义维度已不存在。请回读草案，更新整份回答后重新批准。", clarification_answer_stale:"回答或审阅草案已变化。请核对当前版本。", run_already_active:"分析正在进行，回答暂时只读。", clarification_submission_not_found:"尚未找到原请求回执；这不能证明先前未提交，请继续核对。", idempotency_conflict:"原请求标识对应不同内容，已阻止重复写入。", task_waiting_for_review:"仍有待批准回答或待审草案，请先完成相应核对。", previous_outcome_unresolved:"前次模型结果尚未核清，暂时不能继续。"} as Record<string,string>)[error.code ?? ""] ?? `操作未完成（${error.code ?? error.status}），请核对后再操作。`;
}
export function clarificationCaseMatches(item: Case, workspaceId: string, missionId: string): boolean {
  const q=item.request,a=item.latest_answer,p=item.latest_approval;
  return q.workspace_id===workspaceId && q.mission_id===missionId &&
    (!a || (a.workspace_id===workspaceId && a.mission_id===missionId && a.origin_run_id===q.run_id && a.clarification_id===q.clarification_id && a.request_sha256===item.request_sha256 && a.items.length===q.questions.length)) &&
    (!p || Boolean(a && p.workspace_id===workspaceId && p.mission_id===missionId && p.origin_run_id===q.run_id && p.clarification_id===q.clarification_id && p.answer_version===a.version && p.answer_sha256===a.sha256));
}
export function approvedRefs(cases: Case[]): components["schemas"]["ApprovedAnswerRef"][] {
  return cases.filter(item => item.review_state === "approved" && item.latest_approval && item.latest_answer && item.latest_approval.answer_version === item.latest_answer.version && item.latest_approval.answer_sha256 === item.latest_answer.sha256 && item.latest_approval.origin_run_id === item.request.run_id && item.latest_approval.clarification_id === item.request.clarification_id).map(item => ({origin_run_id:item.request.run_id, clarification_id:item.request.clarification_id, answer_version:item.latest_answer!.version, answer_sha256:item.latest_answer!.sha256, approval_id:item.latest_approval!.approval_id})).sort((a,b) => a.origin_run_id.localeCompare(b.origin_run_id) || a.clarification_id.localeCompare(b.clarification_id));
}

type SubmissionAction =
  | {operation:"save"; originRunId:string; clarificationId:string; body:components["schemas"]["ClarificationAnswerSaveRequest"]}
  | {operation:"approve"; originRunId:string; clarificationId:string; version:number; body:components["schemas"]["ClarificationAnswerApproveRequest"]}
  | {operation:"continue"; body:components["schemas"]["TaskMessageSendRequest"]};
export type PendingClarificationSubmission = {kind:"answer"|"continue"; id:string; workspaceId:string; missionId:string; action?:SubmissionAction};
export function decodePendingSubmission(serialized:string, workspaceId:string, missionId:string): PendingClarificationSubmission {
  const value = JSON.parse(serialized) as PendingClarificationSubmission;
  if (!value || typeof value!=="object" || !["answer","continue"].includes(value.kind) || typeof value.id!=="string" || !value.id ||
      (value.workspaceId!==undefined && value.workspaceId!==workspaceId) || (value.missionId!==undefined && value.missionId!==missionId)) throw new Error("pending scope mismatch");
  // Older ID-only records remain queryable, but cannot manufacture a replay payload.
  if (value.action) {
    const a=value.action;
    if (!a.body || a.body.client_request_id!==value.id || !Number.isInteger(a.body.expected_state_version) || a.body.expected_state_version<1) throw new Error("invalid pending body");
    if (a.operation==="continue") {
      if (value.kind!=="continue" || a.body.kind!=="message" || a.body.provider_send_confirmed!==true || !Array.isArray(a.body.approved_answers) || !a.body.expected_draft) throw new Error("invalid continuation");
    } else if ((a.operation!=="save" && a.operation!=="approve") || value.kind!=="answer" || typeof a.originRunId!=="string" || typeof a.clarificationId!=="string" || (a.operation==="approve" && (!Number.isInteger(a.version)||a.version<1))) throw new Error("invalid answer route");
  }
  return {...value,workspaceId,missionId};
}
const submissionApi={saveClarificationAnswer,approveClarificationAnswer,sendTaskMessage,fetchClarificationSubmission,fetchMessageSubmission};
type SubmissionResult = {kind:"answer"; receipt:Receipt} | {kind:"continue"; receipt:components["schemas"]["TaskMessageSendReceipt"]};
export async function performPendingSubmission(pending:PendingClarificationSubmission, mode:"query"|"replay", api=submissionApi): Promise<SubmissionResult> {
  const {workspaceId:ws,missionId:mid,id,action}=pending;
  if (mode==="query") return pending.kind==="answer" ? {kind:"answer",receipt:await api.fetchClarificationSubmission(ws,mid,id)} : {kind:"continue",receipt:await api.fetchMessageSubmission(ws,mid,id)};
  if (!action) throw new Error("original payload unavailable");
  switch(action.operation) {
    case "save": return {kind:"answer",receipt:await api.saveClarificationAnswer(ws,mid,action.originRunId,action.clarificationId,action.body)};
    case "approve": return {kind:"answer",receipt:await api.approveClarificationAnswer(ws,mid,action.originRunId,action.clarificationId,action.version,action.body)};
    case "continue": return {kind:"continue",receipt:await api.sendTaskMessage(ws,mid,action.body)};
  }
}

export function ClarificationAnswers({state}: {state: Path2WorkbenchState}) {
  const ws = state.workspaceId, mid = state.selectedMission?.mission_id;
  const scope = `${ws}/${mid}`;
  const current = useRef(scope); current.current = scope;
  const [page, setPage] = useState<components["schemas"]["ClarificationCasePage"] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const loadGeneration = useRef(0);
  const historyGeneration = useRef(0);
  const [readReady, setReadReady] = useState(false);
  const [storageReady, setStorageReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const mutex = useRef(false);
  const [pending, setPending] = useState<PendingClarificationSubmission | null>(null);
  const [checkedMissing, setCheckedMissing] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [history, setHistory] = useState<components["schemas"]["ClarificationAnswerRead"] | null>(null);
  const storageKey = `contextox-clarification-submission:${scope}`;
  const load = async () => {
    if (!ws || !mid) return;
    const generation = ++loadGeneration.current;
    setLoading(true); setReadReady(false);
    try {
      const next = await fetchClarificationCases(ws, mid);
      if (current.current !== scope || generation !== loadGeneration.current) return;
      if (next.items.some(item => !clarificationCaseMatches(item, ws, mid))) throw new Error("scope mismatch");
      setPage(next); setError(""); setReadReady(true);
    } catch (e) {if (current.current === scope && generation === loadGeneration.current) {setError(clarificationError(e)); if (e instanceof ApiRequestError && [403,404].includes(e.status)) {setPage(null);setHistory(null);}}}
    finally {if (current.current === scope && generation === loadGeneration.current) setLoading(false);}
  };
  useEffect(() => {
    setPage(null); setEditing(null); setDirty(false); setHistory(null); setConfirmed(false); setPending(null); setCheckedMissing(false); setBusy(false); mutex.current = false;
    try {const stored = sessionStorage.getItem(storageKey); if (stored) {setPending(decodePendingSubmission(stored,ws!,mid!));}setStorageReady(true);}
    catch {setStorageReady(false);setError("浏览器无法回读请求标识，请先核对待提交结果。");}
    void load();
  }, [scope]);
  const terminal = state.runSnapshot?.status;
  useEffect(() => {if (!dirty) void load();}, [state.missionSnapshot?.mission.state_version, terminal]);
  const finish = async (receipt: Receipt) => {
    if (current.current !== scope) return;
    if (receipt.answer.workspace_id !== ws || receipt.answer.mission_id !== mid) throw new Error("scope mismatch");
    sessionStorage.removeItem(storageKey); setPending(null); setEditing(null); setDirty(false); setConfirmed(false);
    await state.refreshTask(); await load();
  };
  const remember = (action:SubmissionAction) => {
    const serialized=JSON.stringify({kind:action.operation==="continue"?"continue":"answer",id:action.body.client_request_id,workspaceId:ws,missionId:mid,action});
    const saved=decodePendingSubmission(serialized,ws!,mid!);
    sessionStorage.setItem(storageKey,serialized);setPending(saved);setCheckedMissing(false);return saved;
  };
  const failed = (e:unknown) => {
    if (current.current !== scope) return;
    setError(clarificationError(e));
    if (e instanceof ApiRequestError && [403,404,409,422].includes(e.status) && !e.code?.includes("outcome_unknown")) {sessionStorage.removeItem(storageKey); setPending(null);}
  };
  const save = async (item:Case, items:AnswerItem[]) => {
    if (!ws || !mid || !page || !readReady || !storageReady || !state.latestDraft || mutex.current || pending) throw new Error("not ready");
    mutex.current = true; setBusy(true); setError("");
    const draft = state.latestDraft;
    try {const id = crypto.randomUUID(); const saved=remember({operation:"save",originRunId:item.request.run_id,clarificationId:item.request.clarification_id,body:{client_request_id:id,expected_latest_version:item.latest_answer?.version ?? 0,expected_state_version:page.mission_state_version,request_sha256:item.request_sha256,review_draft:{draft_id:draft.draft_id,version:draft.version,sha256:draft.sha256}, source_refs:state.missionSnapshot?.mission.source_refs ?? state.selectedMission!.source_refs,items}});
      const result=await performPendingSubmission(saved,"replay");if(result.kind==="answer")await finish(result.receipt);
    } catch(e) {failed(e); throw e;} finally {if(current.current === scope){mutex.current=false;setBusy(false);}}
  };
  const approve = async (item:Case) => {
    if (!ws || !mid || !page || !readReady || !storageReady || !item.latest_answer || mutex.current || pending) return;
    mutex.current=true;setBusy(true);setError("");
    try {const id=crypto.randomUUID();const saved=remember({operation:"approve",originRunId:item.request.run_id,clarificationId:item.request.clarification_id,version:item.latest_answer.version,body:{client_request_id:id,expected_state_version:page.mission_state_version,expected_answer_sha256:item.latest_answer.sha256}});const result=await performPendingSubmission(saved,"replay");if(result.kind==="answer")await finish(result.receipt);}
    catch(e){failed(e);}finally{if(current.current===scope){mutex.current=false;setBusy(false);}}
  };
  const adopt = async (receipt: components["schemas"]["TaskMessageSendReceipt"]) => {
    if(current.current!==scope)return;
    if(receipt.run.workspace_id!==ws||receipt.run.mission_id!==mid)throw new Error("scope mismatch");
    sessionStorage.removeItem(storageKey);setPending(null);setConfirmed(false);state.adoptDialogueRun(receipt.run);await state.refreshTask();await load();
  };
  const continueAnalysis = async () => {
    if(!ws||!mid||!page||!state.latestDraft||mutex.current||pending||!confirmed||!canContinue)return;
    mutex.current=true;setBusy(true);setError("");
    try {const id=crypto.randomUUID();const draft=state.latestDraft;
      const saved=remember({operation:"continue",body:{kind:"message",client_request_id:id,expected_state_version:page.mission_state_version,content:"请依据本次批准的整份回答继续分析，保留尚未解决的卡点，并说明草案变化。",references:[],history_messages:[],source_refs:state.selectedSourceRefs,provider_send_confirmed:true,approved_answers:refs,expected_draft:{draft_id:draft.draft_id,version:draft.version,sha256:draft.sha256}}});const result=await performPendingSubmission(saved,"replay");if(result.kind==="continue")await adopt(result.receipt);
    }catch(e){failed(e);}finally{if(current.current===scope){mutex.current=false;setBusy(false);}}
  };
  const reconcile = async (replay=false) => {
    if(!ws||!mid||!pending||mutex.current||(replay&&(!checkedMissing||!pending.action)))return;
    mutex.current=true;setBusy(true);setError("");
    try{const result=await performPendingSubmission(pending,replay?"replay":"query");if(result.kind==="answer")await finish(result.receipt);else await adopt(result.receipt);}
    catch(e){if(current.current===scope){if(replay)failed(e);else {setError(clarificationError(e));setCheckedMissing(e instanceof ApiRequestError && e.status===404 && ["clarification_submission_not_found","message_submission_not_found"].includes(e.code??""));}}}finally{if(current.current===scope){mutex.current=false;setBusy(false);}}
  };
  const latestStatus = state.missionSnapshot?.latest_run?.status;
  const active=terminal==="running"||terminal==="queued"||latestStatus==="running"||latestStatus==="queued";
  const cases=page?.items ?? [];
  const refs=approvedRefs(cases);
  const required=cases.flatMap(item=>item.latest_answer?.source_refs ?? []).filter((ref,i,all)=>all.findIndex(other=>sourceIdentityEquals(ref,other))===i);
  const sourceMatch=required.length===state.selectedSourceRefs.length&&required.every(ref=>state.selectedSourceRefs.some(other=>sourceIdentityEquals(ref,other)));
  const canContinue=Boolean(storageReady&&readReady&&page&&cases.length&&refs.length===cases.length&&!active&&!editing&&!dirty&&!loading&&!pending&&state.latestDraft&&state.latestDraft.status!=="in_review"&&sourceMatch);
  const unknownTargets = new Set(cases.flatMap(item => item.latest_answer?.items.filter(answer => answer.disposition === "unknown").flatMap(answer => answer.targets.map(targetKey)) ?? []));
  const conflicts = [...new Set(cases.flatMap(item => item.latest_answer?.items.filter(answer => answer.disposition === "answered").flatMap(answer => answer.targets.map(targetKey).filter(key => unknownTargets.has(key))) ?? []))];
  const blockers=cases.reduce((sum,item)=>sum+(item.latest_answer?.items.filter(answer=>answer.disposition==="unknown").length??0),0);
  if(!ws||!mid)return <p>选择任务后查看需要澄清的问题。</p>;
  return <section className="path2-panel-stack"><header className="path2-panel-intro"><div><h2>回答与批准</h2><p>整份保存、整份批准，再明确继续分析。尚不能确认的问题始终保留为卡点。提交内容仅在本标签会话保留用于失败核对，成功或明确拒绝后清除；未提交输入不会自动保存。</p></div><button disabled={loading||busy} onClick={()=>void load()}>刷新并核对</button></header>
    {error&&<p role="alert">{error}</p>}{loading&&<p role="status">正在核对当前请求及批准…</p>}
    {pending&&<div role="status"><p>操作结果待核对。本标签会话保留原请求及已提交内容，供核对或按原内容重提；不会自动重发。</p><button disabled={busy} onClick={()=>void reconcile()}>核对原请求结果</button>{checkedMissing&&pending.action&&<button disabled={busy} onClick={()=>void reconcile(true)}>按原请求重提</button>}{checkedMissing&&!pending.action&&<p>此旧记录仅保留请求标识，原负载不可用；不能自动重建请求，请保留标识继续核对。</p>}</div>}
    {!loading&&page&&!cases.length&&<p>当前任务没有澄清请求。</p>}
    {!page && !error && state.clarifications.map(request => <article className="path2-card" key={`${request.run_id}/${request.clarification_id}`}><h3>原澄清请求 · 批准状态待回读</h3>{request.questions.map((question,index) => <section key={index}><h4>{index+1}. {question.question}</h4><p>{question.why_needed}</p><p>{question.suggested_owner_role}</p><p>{question.related_definition_paths.join("、")}</p><p>{question.evidence_requested.join("；")}</p><p>{question.examples_or_options.join("；")}</p><EvidenceRefs refs={question.source_refs}/></section>)}</article>)}
    {conflicts.length > 0 && <p role="alert">已回答与未知映射到同一维度：{conflicts.join("、")}。未知保护优先，需更新整份回答并重新批准才能解除。</p>}
    {active&&<p role="status">分析期间回答只读。新事实请在本轮结束后保存为新版本。</p>}
    {cases.map(item=>{const key=`${item.request.run_id}/${item.request.clarification_id}`;return <article className="path2-card" key={key}><h3>澄清请求 · {item.request.questions.length} 题</h3><p>{editing===key ? "整份新版本编辑中 · 尚未批准" : reviewLabels[item.review_state]}</p>
      {editing===key?<AnswerForm key={key} request={item.request} latest={item.latest_answer} draft={state.latestDraft} disabled={!storageReady||!readReady||active||busy||Boolean(pending)||Boolean(editing&&editing!==key)} onDirty={setDirty} onSave={items=>save(item,items)}/>:<><ol>{item.request.questions.map((question,i)=><li key={i}>{question.question}</li>)}</ol>{item.latest_answer && <AnswerReadback answer={item.latest_answer} request={item.request}/>}
        {item.latest_answer && item.review_state==="awaiting_approval"&&<button disabled={!storageReady||!readReady||active||busy||Boolean(pending)||Boolean(editing)} onClick={()=>void approve(item)}>批准整份回答 v{item.latest_answer.version}</button>}
        <button disabled={!storageReady||!readReady||active||busy||Boolean(pending)||Boolean(editing)} onClick={()=>{setEditing(key);setHistory(null);}}>{item.latest_answer ? "修改整份回答" : "填写整份回答"}</button>
      </>}
      {editing===key && item.latest_answer && <details><summary>对照服务器当前整份回答 v{item.latest_answer.version}（本地输入保留）</summary><AnswerReadback answer={item.latest_answer} request={item.request}/><p>请先比较当前已保存版本，再保存本地整份新版本。</p></details>}
      {editing===key && <button disabled={busy||Boolean(pending)} onClick={()=>{if(!dirty||window.confirm("丢弃当前页面尚未保存的回答修改？")){setEditing(null);setDirty(false);}}}>取消编辑</button>}
      {item.latest_answer&&<details><summary>历史回答（只读）</summary>{Array.from({length:item.latest_answer.version},(_,i)=>i+1).map(version=><button key={version} disabled={busy} onClick={()=>{const generation=++historyGeneration.current;setHistory(null);void fetchClarificationAnswer(ws,mid,item.request.run_id,item.request.clarification_id,version).then(value=>{if(current.current===scope&&generation===historyGeneration.current&&value.answer.workspace_id===ws&&value.answer.mission_id===mid&&value.answer.origin_run_id===item.request.run_id&&value.answer.clarification_id===item.request.clarification_id&&value.answer.version===version)setHistory(value);}).catch(e=>{if(current.current===scope)setError(clarificationError(e));});}}>v{version}</button>)}</details>}
    </article>;})}
    {history&&<section className="path2-card"><h3>历史回答 · 只读</h3><p>{history.approval?"该版本有批准记录":"该版本未批准"}</p><AnswerReadback answer={history.answer} request={cases.find(item => item.request.run_id===history.answer.origin_run_id && item.request.clarification_id===history.answer.clarification_id)?.request}/><button onClick={()=>setHistory(null)}>关闭历史</button></section>}
    {cases.length>0&&<section className="path2-card"><h3>继续分析</h3><p>本次携带 {refs.length} 份已批回答；{blockers} 个问题仍未解决。</p>
      {!sourceMatch&&<p>请在资料页选择与这些回答完全一致的 {required.length} 份资料后继续。</p>}
      {refs.length!==cases.length&&<p>所有请求都必须保存并批准最新整份版本。</p>}
      {state.latestDraft?.status==="in_review"&&<p>当前草案待审，本入口不能代替草案批准。</p>}
      <label className="history-choice"><input type="checkbox" checked={confirmed} disabled={!canContinue||busy} onChange={event=>setConfirmed(event.target.checked)}/><span>将上述回答及所选资料交给模型，启动新一轮分析</span></label><button className="path2-primary-button" disabled={!canContinue||!confirmed||busy} onClick={()=>void continueAnalysis()}>继续分析</button><p>批准、查看和刷新不会调用模型。</p>
    </section>}
    {state.runSnapshot&&<AnswerImpactView key={`${state.runSnapshot.run_id}/${terminal}`} workspaceId={ws} missionId={mid} runId={state.runSnapshot.run_id}/>}
  </section>;
}
