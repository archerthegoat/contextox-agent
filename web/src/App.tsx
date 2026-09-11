import { useEffect, useRef, useState, type CSSProperties } from "react";

import { fetchWorkbench, type WorkbenchSnapshot, type Workspace } from "./api/client";
import {
  Path2Workbench,
  ConversationSourcePreview,
  type SourceIdentity,
  statusLabel,
  sourceIdentityEquals,
  sourceIdentityFromRevision,
  usePath2Workbench,
  type DefinitionDraft,
  type Path2WorkbenchState,
  type SourceRevision,
} from "./Path2Workbench";
import { useTaskDialogue, ReferenceInspector, TaskExecutionHistory, type DialogueState, type MessageReference } from "./TaskDialogue";
import WorkspaceSwitcher from "./WorkspaceSwitcher";
import { ModelSettings } from "./ModelSettings";
import { DemoEntry } from "./DemoEntry";
import { AnswerImpactView } from "./ClarificationAnswers";
import { CandidateExport } from "./CandidateExport";
import { AgentLayout } from "./AgentLayout";
import { ConversationDialogue, useConversationDialogue, conversationPreviewTarget, type ConversationDialogueState } from "./ConversationDialogue";
import "./styles.css";

export { WORKSPACE_STORAGE_KEY } from "./WorkspaceSwitcher";

export type AreaId = "sources" | "mission" | "clarifications" | "contract";

export type AreaContent = {
  label: string;
  title: string;
  description: string;
  emptyTitle: string;
  emptyBody: string;
};

export type IconName =
  | "archive"
  | "target"
  | "question-mark-circled"
  | "file-text"
  | "reader"
  | "cube"
  | "mix"
  | "double-arrow-left"
  | "double-arrow-right"
  | "chevron-down";

export const ICON_URLS: Record<IconName, string> = {
  archive: new URL("./assets/icons/archive.svg", import.meta.url).href,
  target: new URL("./assets/icons/target.svg", import.meta.url).href,
  "question-mark-circled": new URL("./assets/icons/question-mark-circled.svg", import.meta.url).href,
  "file-text": new URL("./assets/icons/file-text.svg", import.meta.url).href,
  reader: new URL("./assets/icons/reader.svg", import.meta.url).href,
  cube: new URL("./assets/icons/cube.svg", import.meta.url).href,
  mix: new URL("./assets/icons/mix.svg", import.meta.url).href,
  "double-arrow-left": new URL("./assets/icons/double-arrow-left.svg", import.meta.url).href,
  "double-arrow-right": new URL("./assets/icons/double-arrow-right.svg", import.meta.url).href,
  "chevron-down": new URL("./assets/icons/chevron-down.svg", import.meta.url).href,
};

function Icon({ name, className = "" }: { name: IconName; className?: string }) {
  const style = { "--icon-url": `url("${ICON_URLS[name]}")` } as CSSProperties;
  return <span className={`icon ${className}`.trim()} aria-hidden="true" style={style} />;
}

export const AREA_CONTENT: Record<AreaId, AreaContent> = {
  sources: {
    label: "资料来源",
    title: "资料来源",
    description: "查看已授权的资料与它们在定义工作中的位置。",
    emptyTitle: "暂无资料来源",
    emptyBody: "选择工作区后，可导入明确授权的本地资料。",
  },
  mission: {
    label: "任务",
    title: "任务工作区",
    description: "围绕一个清晰目标，把来源、实体、冲突和确认串成可回看的工作链。",
    emptyTitle: "暂无任务",
    emptyBody: "描述目标并确认任务，即可开始分析。",
  },
  clarifications: {
    label: "待澄清",
    title: "待澄清问题",
    description: "把定义中的未知交给合适的人确认，再回到同一个工作对象。",
    emptyTitle: "暂无澄清请求",
    emptyBody: "分析产生的待澄清问题会显示在这里；回答与批准入口尚未开放。",
  },
  contract: {
    label: "业务契约",
    title: "业务契约草案",
    description: "让已经确认的定义保留来源、版本和责任边界。",
    emptyTitle: "暂无业务契约",
    emptyBody: "分析产生的定义草案会显示在这里；正式契约审批尚未开放。",
  },
};

export type AreaNavigationItem = {
  id: AreaId;
  label: string;
  description: string;
};

export const AREA_NAV_PRESENTATION: Record<AreaId, Omit<AreaNavigationItem, "id">> = {
  sources: { label: "资料来源", description: "授权资料" },
  mission: { label: "任务", description: "当前任务" },
  clarifications: { label: "待澄清", description: "待澄清问题" },
  contract: { label: "业务契约", description: "定义版本" },
};

export const AREA_NAV_ICONS: Record<AreaId, IconName> = {
  sources: "archive",
  mission: "target",
  clarifications: "question-mark-circled",
  contract: "file-text",
};

export const AREA_NAV: AreaNavigationItem[] = [
  { id: "sources", ...AREA_NAV_PRESENTATION.sources },
  { id: "mission", ...AREA_NAV_PRESENTATION.mission },
  { id: "clarifications", ...AREA_NAV_PRESENTATION.clarifications },
  { id: "contract", ...AREA_NAV_PRESENTATION.contract },
];

export function navigationForAreas(areas: WorkbenchSnapshot["areas"]): AreaNavigationItem[] {
  return areas.map(({ id }) => ({ id, ...AREA_NAV_PRESENTATION[id] }));
}

export const AGENT_COPY = {
  title: "数契 Agent",
  mode: "任务对话",
  composerPlaceholder: "围绕当前任务继续提问",
} as const;

export type ObjectTabId = "mission" | "relationship" | "history";

export const OBJECT_TABS: Array<{ id: ObjectTabId; label: string }> = [
  { id: "mission", label: "当前进展" },
  { id: "relationship", label: "关系与字段" },
  { id: "history", label: "过程记录" },
];

const BRAND_MARK_URL = new URL("./assets/contextox-mark.png", import.meta.url).href;

type ApiState = "loading" | "ready" | "error";
type ConnectionState = "connecting" | "connected" | "reconnecting";

function Brand() {
  return (
    <a className="wordmark" href="/" aria-label="数契 ContextOx">
      <img className="wordmark-mark" src={BRAND_MARK_URL} alt="" aria-hidden="true" />
      <span className="wordmark-cn">数契</span>
      <span className="wordmark-en">ContextOx</span>
    </a>
  );
}

type MissionObjectId = string;

function OpenObjectTabs({ activeTab, onTabChange }: { activeTab: ObjectTabId; onTabChange: (tab: ObjectTabId) => void }) {
  return (
    <div className="center-tabs" aria-label="已打开对象">
      {OBJECT_TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          aria-pressed={activeTab === tab.id}
          className={`object-tab${activeTab === tab.id ? " object-tab-active" : ""}`}
          onClick={() => onTabChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

type RelationshipCandidate = DefinitionDraft["relationships"][number];

export type RelationshipGraphResolution = {
  candidateCount: number;
  relationship: RelationshipCandidate | null;
  leftSourceMatched: boolean;
  rightSourceMatched: boolean;
  completeRelationship: boolean;
};

export function relationshipGraphResolution(
  relationships: RelationshipCandidate[],
  sources: SourceRevision[],
): RelationshipGraphResolution {
  const relationship = relationships[0] ?? null;
  if (!relationship) {
    return {
      candidateCount: 0,
      relationship: null,
      leftSourceMatched: false,
      rightSourceMatched: false,
      completeRelationship: false,
    };
  }
  const sourceMatches = (table: RelationshipCandidate["left"]): boolean =>
    sources.some((source) => sourceIdentityEquals(table.source_ref, sourceIdentityFromRevision(source)));
  const leftSourceMatched = sourceMatches(relationship.left);
  const rightSourceMatched = sourceMatches(relationship.right);
  return {
    candidateCount: relationships.length,
    relationship,
    leftSourceMatched,
    rightSourceMatched,
    completeRelationship: leftSourceMatched && rightSourceMatched,
  };
}

export function RelationshipGraph({ path2, onReference }: {
  path2: Path2WorkbenchState; onReference: (ref: MessageReference) => void;
}) {
  const draft = path2.latestDraft;
  const run = path2.runSnapshot;
  const showAnswerImpact = run && run.workspace_id===path2.workspaceId && run.mission_id===path2.selectedMission?.mission_id && !["queued","running"].includes(run.status) && Boolean(run.approved_answers?.length);
  const sourceName = (table: RelationshipCandidate["left"]) => path2.sourceState.items.find(s =>
    sourceIdentityEquals(table.source_ref, sourceIdentityFromRevision(s)))?.original_name ?? "来源未匹配";
  return <section className="task-results" aria-label="关系与字段结果">
    <p className="result-intro">这里先展示业务含义、资料关系和仍未知的事项。版本、对象标识和运行信息可在技术详情中查看。</p>
    {showAnswerImpact && <AnswerImpactView key={run.run_id} workspaceId={run.workspace_id} missionId={run.mission_id} runId={run.run_id}/>}
    {!draft?.relationships.length && <div className="conversation-empty"><h3>还没有找到可展示的资料关系</h3><p>在右侧添加资料并说明目标，Agent 会把可核对的关系放在这里。</p></div>}
    {draft?.relationships.map((relationship, index) => <article className="relationship-result" key={relationship.relationship_key}>
      <header><div><span className="result-kicker">候选关系</span><h3>{sourceName(relationship.left)} 与 {sourceName(relationship.right)}</h3></div><span>{relationship.evidence_status === "observed" ? "资料中已观察" : relationship.evidence_status === "conflict" ? "存在冲突" : "等待核对"}</span></header>
      <svg viewBox="0 0 620 145" role="img" aria-label={`${relationship.left.table_id || "根表"} 与 ${relationship.right.table_id || "根表"}，${relationship.observed_cardinality}`}>
        <title>{sourceName(relationship.left)} 与 {sourceName(relationship.right)} 的候选关系</title>
        <defs><marker id={`relation-arrow-${index}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="none" stroke="currentColor"/></marker></defs>
        {relationshipGraphResolution([relationship], path2.sourceState.items).completeRelationship && <path d="M225 70 H395" fill="none" stroke="currentColor" strokeWidth="2" markerEnd={`url(#relation-arrow-${index})`} strokeDasharray={relationship.evidence_status === "observed" ? undefined : "6 5"}/>}
        <rect x="5" y="30" width="220" height="85" rx="10"/><rect x="395" y="30" width="220" height="85" rx="10"/>
        <text x="20" y="58">{sourceName(relationship.left).slice(0, 25)}</text><text x="20" y="86">{(relationship.left.table_id || "根表").slice(0, 25)}</text>
        <text x="410" y="58">{sourceName(relationship.right).slice(0, 25)}</text><text x="410" y="86">{(relationship.right.table_id || "根表").slice(0, 25)}</text>
        <text x="310" y="52" textAnchor="middle">{({one_to_one:"1 : 1",one_to_many:"1 : N",many_to_one:"N : 1",many_to_many:"N : N",unknown:"待核验"} as Record<string,string>)[relationship.observed_cardinality] ?? relationship.observed_cardinality}</text>
      </svg>
      <p><strong>连接规则：</strong>{relationship.join_rule ?? "待定义"}</p><p><strong>数据粒度：</strong>{relationship.grain_notes ?? "待确认"}</p>
      {relationship.risks.map((risk, i) => <p className="relationship-risk" key={i}>{risk}</p>)}
      <button onClick={() => onReference({kind:"draft_relationship", draft_id:draft.draft_id, draft_version:draft.version, draft_sha256:draft.sha256, relationship_key:relationship.relationship_key})}>引用关系继续讨论</button>
      {relationship.source_refs.map((ref, i) => <button className="reference-chip" key={i} onClick={() => onReference({kind:"source_excerpt", evidence_ref:ref})}>查看证据 {i + 1}</button>)}
      <details className="technical-details"><summary>技术详情</summary><dl><dt>关系标识</dt><dd>{relationship.relationship_key}</dd><dt>候选版本</dt><dd>{draft.version}</dd><dt>证据状态</dt><dd>{relationship.evidence_status}</dd></dl></details>
    </article>)}
    {draft && <section aria-label="字段定义"><h2>字段与业务口径</h2>{draft.fields.map(field => <article className="field-result" key={field.field_key}><span className="result-kicker">候选字段</span><h3>{field.name}</h3><p>{field.evidence_status === "observed" ? "资料中已观察" : field.evidence_status === "conflict" ? "存在冲突" : "业务含义等待核对"}</p>
      <dl className="path2-detail-grid">{([
        ["meaning", "含义"], ["value_type", "值类型"], ["grain", "粒度"],
        ["rule", "规则"], ["time_basis", "时间基准"], ["null_handling", "缺失值处理"],
      ] as const).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{field[key] ?? "未知"}
        {field.unknowns.filter(item => item.property_path === key).map((item, i) => <p key={i}>未知原因：{item.reason}</p>)}
      </dd></div>)}</dl>
      {field.source_refs.map((ref, i) => <button className="reference-chip" key={i} onClick={() => onReference({kind:"source_excerpt", evidence_ref:ref})}>查看证据 {i + 1}</button>)}<button onClick={() => onReference({kind:"draft_field", draft_id:draft.draft_id, draft_version:draft.version, draft_sha256:draft.sha256, field_key:field.field_key})}>引用字段继续讨论</button><details className="technical-details"><summary>技术详情</summary><dl><dt>字段标识</dt><dd>{field.field_key}</dd><dt>候选版本</dt><dd>{draft.version}</dd><dt>证据状态</dt><dd>{field.evidence_status}</dd></dl></details></article>)}</section>}
    <CandidateExport draft={draft} />
  </section>;
}
function CenterPanel({
  activeArea,
  activeTab,
  onTabChange,
  dialogue, onReference, focusedReference, clearReference,
  path2, following, onFollow, followedSource, newProgress, sourceImportRequest,
}: {
  following: boolean; onFollow: () => void; followedSource: SourceIdentity | null; newProgress: boolean;
  sourceImportRequest: number;
  activeArea: AreaId;
  activeTab: ObjectTabId;
  onTabChange: (tab: ObjectTabId) => void;
  dialogue: DialogueState; onReference: (ref: MessageReference) => void;
  focusedReference: MessageReference | null; clearReference: () => void;
  path2: Path2WorkbenchState;
}) {
  const content = AREA_CONTENT[activeArea];
  const title = activeTab === "history" ? "过程记录"
    : activeArea === "mission" && activeTab === "relationship" ? "关系与字段"
    : activeArea === "mission" ? (path2.selectedMission?.title ?? content.title) : content.title;

  return (
    <main className="center-panel" aria-labelledby="center-title">
      <WorkbenchProgress path2={path2} />
      <div className="agent-follow-bar"><span>{following ? "跟随对话展示相关内容" : newProgress ? "有新进展 · 当前阅读保持不变" : "手动查看中 · 新进展不会切换此视图"}</span>{!following && <button onClick={onFollow}>跟随当前进展</button>}</div>
      <OpenObjectTabs activeTab={activeTab} onTabChange={onTabChange} />
      <div className="center-toolbar">
        <h1 id="center-title">{title}</h1>
      </div>
      {focusedReference ? <ReferenceInspector state={path2} reference={focusedReference} onClose={clearReference} onQuote={onReference}/> : activeArea === "sources" && followedSource ? <ConversationSourcePreview state={path2} source={followedSource}/> : activeTab === "history" ? <TaskExecutionHistory state={path2} dialogue={dialogue} /> : activeArea === "mission" && activeTab === "relationship" ? (
        <RelationshipGraph
          onReference={onReference}
          path2={path2}
        />
      ) : (
        activeArea === "mission" ? <MissionOverview path2={path2} /> : activeArea === "clarifications" ? <section className="mission-overview"><h2>等待你确认的业务口径</h2><p>在右侧继续讨论，核对回答卡片后确认继续。</p>{path2.clarifications.map(item => <article key={`${item.run_id}/${item.clarification_id}`}>{item.questions.map((question,index) => <section key={index}><h3>{question.question}</h3><p>{question.why_needed}</p><p>{question.suggested_owner_role ?? "回答角色尚未确定"}</p></section>)}</article>)}</section> : <Path2Workbench state={path2} activeArea={activeArea} focusUploadRequest={sourceImportRequest} />
      )}
    </main>
  );
}

export function resolveWorkbenchProgress(path2: Pick<Path2WorkbenchState, "missionSnapshot" | "selectedMission" | "runSnapshot" | "latestDraft" | "clarifications">) {
  const mission = path2.missionSnapshot?.mission ?? path2.selectedMission;
  const questionCount = path2.clarifications.reduce((sum,item) => sum + item.questions.length, 0);
  const waitingForAnswers = questionCount > 0;
  const active = path2.runSnapshot?.status === "queued" || path2.runSnapshot?.status === "running";
  const current = !mission ? 0 : active ? (path2.runSnapshot?.phase === "apply" ? 3 : 1) : waitingForAnswers ? 2 : path2.latestDraft ? 3 : 1;
  const description = !mission
    ? "直接从右侧说出问题，目标和资料明确后会自动开始。"
    : active
      ? "正在分析；可以继续编辑下一条草稿，或随时停止。"
      : waitingForAnswers
      ? `等待业务回答 · ${questionCount} 个已记录问题`
    : path2.latestDraft?.status === "in_review"
        ? "候选成果等待核对"
        : path2.runSnapshot
          ? `本轮：${statusLabel(path2.runSnapshot.status)}`
          : "目标已经明确，可以继续说明你想得到的结果";
  return {mission, current, description, waitingForAnswers};
}

export function resolveWorkbenchSummary(path2: Pick<Path2WorkbenchState, "missionSnapshot" | "selectedMission" | "runSnapshot" | "latestDraft" | "clarifications">, sourceCount:number) {
  const mission=path2.missionSnapshot?.mission??path2.selectedMission;
  const questions=path2.clarifications.reduce((sum,item)=>sum+item.questions.length,0);
  const active=path2.runSnapshot?.status==="queued"||path2.runSnapshot?.status==="running";
  const draft=path2.latestDraft;
  if(!mission)return {now:"还没有开始分析",next:sourceCount?"在右侧说目标，或先让 Agent 了解资料":"在右侧说目标，或先添加资料",result:sourceCount?`${sourceCount} 份资料已选入本次对话`:"本次对话还没有选择资料"};
  if(active)return {now:path2.runSnapshot?.phase==="apply"?"正在把已确认规则整理进成果":"正在核对资料、字段和业务问题",next:"可以等待，也可以随时停止",result:draft?"已有候选内容会继续保留":"本轮还没有形成业务结论"};
  if(questions)return {now:"已找到需要业务判断的事项",next:`回答 ${questions} 个会改变结果的问题`,result:draft?"候选字段与关系已经保留，业务规则尚待确认":"问题已经记录，尚未采用业务结论"};
  if(draft){
    const unknowns=draft.fields.reduce((sum,field)=>sum+field.unknowns.length,0)+draft.relationships.reduce((sum,relationship)=>sum+relationship.unknowns.length,0);
    return {now:"候选成果已经更新",next:unknowns?"核对变化，继续补充仍未知的口径":"核对本轮成果",result:`${draft.fields.length} 个字段 · ${draft.relationships.length} 条关系${unknowns?` · ${unknowns} 项未知`:""}`};
  }
  return {now:"目标已经明确",next:"在右侧继续说明，发送后开始分析",result:`本轮使用 ${sourceCount} 份资料`};
}

function WorkbenchProgress({path2}: {path2: Path2WorkbenchState}) {
  const {mission, current, description} = resolveWorkbenchProgress(path2);
  const summary=resolveWorkbenchSummary(path2,path2.selectedSourceRefs.length);
  return <><header className="workbench-progress"><p className="workbench-eyebrow">当前进展</p><h2>{mission?.title ?? "从一个问题开始"}</h2><ol>{["明确目标", "理解资料", "澄清口径", "整理成果"].map((label,index) => <li key={label} className={current === index ? "current" : current > index ? "past" : ""} aria-current={current === index ? "step" : undefined}><span>{current>index?"✓":index + 1}</span>{label}</li>)}</ol><p>{description}</p></header><section className="workbench-summary" aria-label="本轮工作摘要">{([['正在做什么',summary.now],['需要你做什么',summary.next],['已经得到什么',summary.result]] as const).map(([label,value],index)=><div className={index===1?"summary-item needs-action":"summary-item"} key={label}><span>{label}</span><strong>{value}</strong></div>)}</section></>;
}
function MissionOverview({path2}: {path2: Path2WorkbenchState}) {
  const mission = path2.missionSnapshot?.mission ?? path2.selectedMission;
  return <section className="mission-overview">{mission ? <><p className="path2-eyebrow">当前目标</p><h2>{mission.goal}</h2><p>继续在右侧讨论和推进。资料依据、需要确认的问题和候选成果会在这里跟随显示。</p>{path2.latestDraft && <><div className="result-ready-note"><strong>已经形成候选成果</strong><p>可以打开“关系与字段”核对业务含义、资料依据和仍未知的事项。</p></div><details className="technical-details"><summary>技术详情</summary><p>候选版本 {path2.latestDraft.version} · {statusLabel(path2.latestDraft.status)} · 候选结果尚未发布为正式业务契约。</p></details></>}</> : <><img src={BRAND_MARK_URL} alt="" /><p className="path2-eyebrow">从对话开始</p><h2>说出问题，<br/>一起找到有依据的答案。</h2><p>右侧负责讨论和推进；这里解释当前过程、展示资料依据和核对成果。</p><ol><li><strong>先说你想解决什么</strong><p>不必学习任务、Run 或模型配置。</p></li><li><strong>Agent 找资料、说明关系</strong><p>遇到会改变结论的地方再问你。</p></li><li><strong>关键规则由你确认</strong><p>自然语言回答先整理成卡片，确认后才采用。</p></li></ol></>}</section>;
}
function AgentPanel({ path2, conversation, onReference, onHistory, onSources, onResults, onClarifications, onDemoLoaded, expanded, onExpand }: {
  path2: Path2WorkbenchState; conversation: ConversationDialogueState; onReference: (ref: MessageReference) => void; onHistory: () => void;
  onSources: () => void; onResults: () => void; onClarifications: () => void; onDemoLoaded:(workspace:Workspace,task:string,revisions:string[])=>void;
  expanded: boolean; onExpand: () => void;
}) {
  return <aside className="agent-panel" aria-label="Agent 对话"><header className="agent-panel-header"><div className="agent-panel-title-group"><h2>{AGENT_COPY.title}</h2><span>一起把问题弄清楚</span></div><button className="agent-panel-toggle" onClick={onExpand} aria-pressed={expanded}>{expanded ? "恢复双栏" : "展开对话"}</button></header><div id="agent-panel-content" className="agent-panel-content"><ConversationDialogue state={path2} d={conversation} onReference={onReference} onHistory={onHistory} onSources={onSources} onResults={onResults} onClarifications={onClarifications} onDemoLoaded={onDemoLoaded}/></div></aside>;
}

export function resolveFollowTarget(path2: Pick<Path2WorkbenchState, "runSnapshot" | "latestDraft" | "clarifications">): {area: AreaId; tab: ObjectTabId} {
  const waitingForAnswers = path2.clarifications.some(item => item.questions.length > 0);
  const hasUpdatedResult = Boolean(path2.latestDraft && path2.runSnapshot?.approved_answers?.length && !["queued", "running"].includes(path2.runSnapshot.status));
  if (hasUpdatedResult || (path2.latestDraft && !waitingForAnswers)) return {area:"mission", tab:"relationship"};
  if (waitingForAnswers) return {area:"clarifications", tab:"mission"};
  return {area:"mission", tab:"mission"};
}

function App() {
  const [activeArea, setActiveArea] = useState<AreaId>("mission");
  const [activeTab, setActiveTab] = useState<ObjectTabId>("mission");
  const [selectedObject, setSelectedObject] = useState<MissionObjectId>("relationship");
  const [apiState, setApiState] = useState<ApiState>("loading");
  const [snapshot, setSnapshot] = useState<WorkbenchSnapshot | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [selectedWorkspace, setSelectedWorkspace] = useState<Workspace | null>(null);
  const [sourceImportRequest, setSourceImportRequest] = useState(0);
  const [demoSetup, setDemoSetup] = useState<{workspaceId: string; task: string; revisions: string[]} | null>(null);
  const basePath2 = usePath2Workbench(selectedWorkspace);
  const path2 = { ...basePath2, suggestedTask: demoSetup?.workspaceId === basePath2.workspaceId ? demoSetup.task : undefined };
  const selectedDemo = useRef<string | null>(null);
  useEffect(() => {
    if (!demoSetup || basePath2.workspaceId !== demoSetup.workspaceId || selectedDemo.current === demoSetup.workspaceId ||
        !demoSetup.revisions.length || !demoSetup.revisions.every(id => basePath2.sourceState.items.some(source => source.revision_id === id))) return;
    selectedDemo.current = demoSetup.workspaceId;
    demoSetup.revisions.forEach(id => { if (!basePath2.selectedSourceIds.includes(id)) basePath2.toggleSource(id); });
  }, [demoSetup, basePath2]);
  const dialogue = useTaskDialogue(path2);
  const conversation = useConversationDialogue(path2, setSelectedWorkspace);
  const [focusedReference, setFocusedReference] = useState<MessageReference | null>(null);
  useEffect(() => {setFocusedReference(null);}, [path2.workspaceId, path2.selectedMission?.mission_id]);
  const [mobileView, setMobileView] = useState<"result" | "agent">("agent");
  const [navOpen, setNavOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [following, setFollowing] = useState(true);
  const [followedSource,setFollowedSource]=useState<SourceIdentity|null>(null);
  const preview=conversationPreviewTarget(conversation.conversation,conversation.messages,path2.workspaceId);
  const progressKey=JSON.stringify([path2.workspaceId,conversation.conversation?.conversation_id,preview,path2.selectedMission?.mission_id,path2.selectedMission?.status,path2.latestDraft?.draft_id,path2.latestDraft?.version]);
  const lastFollowed=useRef(progressKey);
  const followCurrent = () => {
    lastFollowed.current=progressKey;
    if(!path2.selectedMission&&(preview.reference||preview.source)) {
      setFocusedReference(preview.reference);setFollowedSource(preview.source);setActiveArea("sources");setActiveTab("mission");return;
    }
    const target=resolveFollowTarget(path2);
    setFollowedSource(null);setFocusedReference(null);setActiveArea(target.area);setActiveTab(target.tab);
  };
  useEffect(() => {if(following) followCurrent();}, [following,progressKey]);
  const addReference = (ref: MessageReference) => { conversation.addReference(ref); setMobileView("agent"); };
  const inspectReference = (ref: MessageReference) => {
    setFollowing(false);setFollowedSource(null);
    setFocusedReference(ref);
    setMobileView("result");
    if (ref.kind === "draft_field" || ref.kind === "draft_relationship") { setActiveArea("mission"); setActiveTab("relationship"); }
    else { setActiveArea("sources"); setActiveTab("mission"); }
  };

  useEffect(() => {
    let cancelled = false;
    void fetchWorkbench()
      .then((data) => {
        if (!cancelled) {
          setSnapshot(data);
          setApiState("ready");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setApiState("error");
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/events");
    const handleConnected = () => setConnectionState("connected");
    const handleReconnect = () => setConnectionState("reconnecting");

    source.addEventListener("connected", handleConnected);
    source.onerror = handleReconnect;

    return () => {
      source.removeEventListener("connected", handleConnected);
      source.close();
    };
  }, []);

  const areas = snapshot ? navigationForAreas(snapshot.areas) : AREA_NAV;
  const handleObjectSelect = (objectId: MissionObjectId) => {
    setFollowing(false);setFollowedSource(null);setMobileView("result");
    setSelectedObject(objectId);
    setFocusedReference(null);
    setNavOpen(false);
    if (objectId.startsWith("source:")) {
      setActiveArea("sources");
      setActiveTab("mission");
      path2.selectSource(objectId.slice("source:".length));
    } else if (objectId === "relationship") {
      setActiveArea("mission");
      setActiveTab("relationship");
    } else if (objectId === "mission") {
      setActiveArea("mission");
      setActiveTab("mission");
    }
  };
  const handleDemoLoaded=(workspace:Workspace,task:string,revisions:string[])=>{setSelectedWorkspace(workspace);setDemoSetup({workspaceId:workspace.workspace_id,task,revisions});setFollowing(true);setMobileView("agent");};
  const openSources=()=>{setFollowing(false);setFollowedSource(null);setFocusedReference(null);setActiveArea("sources");setActiveTab("mission");setMobileView("result");setNavOpen(false);setSourceImportRequest(value=>value+1);};
  const openResults=()=>{setFollowing(false);setFollowedSource(null);setFocusedReference(null);setActiveArea("mission");setActiveTab("relationship");setMobileView("result");setNavOpen(false);};
  const openClarifications=()=>{setFollowing(false);setFollowedSource(null);setFocusedReference(null);setActiveArea("clarifications");setActiveTab("mission");setMobileView("result");setNavOpen(false);};
  const openHistory=()=>{setFollowing(false);setFollowedSource(null);setFocusedReference(null);setActiveArea("mission");setActiveTab("history");setMobileView("result");setNavOpen(false);};

  return (
    <div
      className="app-shell agent-led-shell"
      data-api-state={apiState}
      data-connection-state={connectionState}
      data-path2-state="workbench"
    >
      <AgentLayout expanded={expanded} mobileView={mobileView} onMobileView={setMobileView} navOpen={navOpen} onNavOpen={setNavOpen}
        sidebar={<aside className="agent-led-navigation" aria-label="工作区导航"><Brand/><WorkspaceSwitcher selectedWorkspace={selectedWorkspace} onWorkspaceChange={setSelectedWorkspace}/><button className="new-conversation-button" disabled={conversation.sending} onClick={() => {conversation.newConversation();setFollowing(true);setMobileView("agent");setNavOpen(false);}}>新对话</button><p className="nav-section-label">最近工作</p>{conversation.list.map(item => <button className={conversation.conversation?.conversation_id===item.conversation_id ? "nav-conversation selected" : "nav-conversation"} key={item.conversation_id} onClick={() => {void conversation.select(item);setFollowing(true);setNavOpen(false);}}><Icon name="reader"/><span>{item.title}</span></button>)}{path2.missionState.items.filter(mission=>!conversation.list.some(item=>item.mission_id===mission.mission_id)).map(mission=><button className="nav-conversation" key={mission.mission_id} onClick={()=>{void conversation.attachMission(mission.mission_id);setFollowing(true);setNavOpen(false);}}><Icon name="reader"/><span>{mission.title}</span></button>)}{!conversation.list.length&&!path2.missionState.items.length&&<p className="nav-empty">从右侧开始新对话</p>}<div className="nav-section-label"><span>资料库</span><button onClick={() => {openSources();setNavOpen(false);}}>添加资料</button></div>{path2.sourceState.items.map(source => <button className={selectedObject === `source:${source.revision_id}` ? "nav-conversation selected" : "nav-conversation"} key={source.revision_id} onClick={() => handleObjectSelect(`source:${source.revision_id}`)}><Icon name="file-text"/><span>{source.original_name}</span></button>)}<div className="navigation-bottom"><details><summary>更多视图</summary>{areas.map(area => <button key={area.id} onClick={() => {setFollowing(false);setFollowedSource(null);setActiveArea(area.id);setActiveTab("mission");setFocusedReference(null);setNavOpen(false);setMobileView("result");}}>{area.label}</button>)}</details><ModelSettings/><DemoEntry idPrefix="sidebar-demo" onLoaded={handleDemoLoaded}/><small>资料与设置只留在本机</small></div></aside>}
        center={<CenterPanel activeArea={activeArea} activeTab={activeTab} onTabChange={tab => {setFollowing(false);setFollowedSource(null);setActiveArea("mission");setActiveTab(tab);setFocusedReference(null);}} focusedReference={focusedReference} clearReference={() => setFocusedReference(null)} dialogue={dialogue} onReference={addReference} path2={path2} following={following} followedSource={followedSource} newProgress={!following&&lastFollowed.current!==progressKey} onFollow={() => {setFollowing(true);followCurrent();}} sourceImportRequest={sourceImportRequest}/>}
        agent={<AgentPanel expanded={expanded} onExpand={() => setExpanded(value => !value)} conversation={conversation} path2={path2} onReference={inspectReference} onSources={openSources} onResults={openResults} onClarifications={openClarifications} onHistory={openHistory} onDemoLoaded={handleDemoLoaded}/>}/>
    </div>
  );
}

export default App;
