import { useEffect, useRef, useState, type CSSProperties } from "react";

import { fetchWorkbench, type WorkbenchSnapshot, type Workspace } from "./api/client";
import {
  Path2Workbench,
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
import { CandidateExport } from "./CandidateExport";
import { AgentLayout } from "./AgentLayout";
import { ConversationDialogue, useConversationDialogue, type ConversationDialogueState } from "./ConversationDialogue";
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
  { id: "mission", label: "任务工作区" },
  { id: "relationship", label: "关系与字段" },
  { id: "history", label: "执行历史" },
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
  const sourceName = (table: RelationshipCandidate["left"]) => path2.sourceState.items.find(s =>
    sourceIdentityEquals(table.source_ref, sourceIdentityFromRevision(s)))?.original_name ?? "来源未匹配";
  return <section className="task-results" aria-label="关系与字段结果">
    <CandidateExport draft={draft} />
    <p>关系连接来自当前任务草案。基数是观测或候选结果，业务含义仍待确认。</p>
    {!draft?.relationships.length && <div className="conversation-empty"><h3>尚无关系候选</h3><p>导入资料并分析后，在这里查看实际表与表之间的关系。</p></div>}
    {draft?.relationships.map((relationship, index) => <article className="relationship-result" key={relationship.relationship_key}>
      <header><h3>{relationship.relationship_key}</h3><span>{statusLabel(relationship.evidence_status)} · v{draft.version}</span></header>
      <svg viewBox="0 0 620 145" role="img" aria-label={`${relationship.left.table_id || "根表"} 与 ${relationship.right.table_id || "根表"}，${relationship.observed_cardinality}`}>
        <title>{relationship.relationship_key}</title>
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
      {relationship.source_refs.map((ref, i) => <button className="reference-chip" key={i} onClick={() => onReference({kind:"source_excerpt", evidence_ref:ref})}>引用证据 {i + 1}</button>)}
    </article>)}
    {draft && <section aria-label="字段定义"><h2>字段定义</h2>{draft.fields.map(field => <article className="field-result" key={field.field_key}><h3>{field.name}</h3><p>{statusLabel(field.evidence_status)} · 业务语义待批准</p>
      <dl className="path2-detail-grid">{([
        ["meaning", "含义"], ["value_type", "值类型"], ["grain", "粒度"],
        ["rule", "规则"], ["time_basis", "时间基准"], ["null_handling", "缺失值处理"],
      ] as const).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{field[key] ?? "未知"}
        {field.unknowns.filter(item => item.property_path === key).map((item, i) => <p key={i}>未知原因：{item.reason}</p>)}
      </dd></div>)}</dl>
      {field.source_refs.map((ref, i) => <button className="reference-chip" key={i} onClick={() => onReference({kind:"source_excerpt", evidence_ref:ref})}>引用证据 {i + 1}</button>)}<button onClick={() => onReference({kind:"draft_field", draft_id:draft.draft_id, draft_version:draft.version, draft_sha256:draft.sha256, field_key:field.field_key})}>引用字段继续讨论</button></article>)}</section>}
  </section>;
}
function CenterPanel({
  activeArea,
  activeTab,
  onTabChange,
  dialogue, onReference, focusedReference, clearReference,
  path2, following, onFollow,
}: {
  following: boolean; onFollow: () => void;
  activeArea: AreaId;
  activeTab: ObjectTabId;
  onTabChange: (tab: ObjectTabId) => void;
  dialogue: DialogueState; onReference: (ref: MessageReference) => void;
  focusedReference: MessageReference | null; clearReference: () => void;
  path2: Path2WorkbenchState;
}) {
  const content = AREA_CONTENT[activeArea];
  const title = activeTab === "history" ? "执行历史"
    : activeArea === "mission" && activeTab === "relationship" ? "关系与字段"
    : activeArea === "mission" ? (path2.selectedMission?.title ?? content.title) : content.title;

  return (
    <main className="center-panel" aria-labelledby="center-title">
      <WorkbenchProgress path2={path2} />
      <div className="agent-follow-bar"><span>{following ? "跟随对话展示相关内容" : "手动查看中 · 新进展不会切换此视图"}</span>{!following && <button onClick={onFollow}>跟随当前进展</button>}</div>
      <OpenObjectTabs activeTab={activeTab} onTabChange={onTabChange} />
      <div className="center-toolbar">
        <h1 id="center-title">{title}</h1>
      </div>
      {focusedReference ? <ReferenceInspector state={path2} reference={focusedReference} onClose={clearReference} onQuote={onReference}/> : activeTab === "history" ? <TaskExecutionHistory state={path2} dialogue={dialogue} /> : activeArea === "mission" && activeTab === "relationship" ? (
        <RelationshipGraph
          onReference={onReference}
          path2={path2}
        />
      ) : (
        activeArea === "mission" ? <MissionOverview path2={path2} /> : activeArea === "clarifications" ? <section className="mission-overview"><h2>等待你确认的业务口径</h2><p>在右侧继续讨论，核对回答卡片后确认继续。</p>{path2.clarifications.map(item => <article key={`${item.run_id}/${item.clarification_id}`}>{item.questions.map((question,index) => <section key={index}><h3>{question.question}</h3><p>{question.why_needed}</p><p>{question.suggested_owner_role ?? "回答角色尚未确定"}</p></section>)}</article>)}</section> : <Path2Workbench state={path2} activeArea={activeArea} />
      )}
    </main>
  );
}

function WorkbenchProgress({path2}: {path2: Path2WorkbenchState}) {
  const mission = path2.missionSnapshot?.mission ?? path2.selectedMission;
  const waiting = mission?.status === "waiting_for_human";
  const active = path2.runSnapshot?.status === "queued" || path2.runSnapshot?.status === "running";
  const current = !mission ? 0 : waiting ? 2 : active ? (path2.runSnapshot?.phase === "apply" ? 3 : 1) : path2.latestDraft ? 3 : 1;
  return <header className="workbench-progress"><h2>{mission?.title ?? "从一个问题开始"}</h2><ol>{["明确目标", "理解资料", "澄清口径", "整理成果"].map((label,index) => <li key={label} className={current === index ? "current" : current > index ? "past" : ""} aria-current={current === index ? "step" : undefined}><span>{index + 1}</span>{label}</li>)}</ol><p>{!mission ? "先聊问题，目标与资料明确后再形成任务。" : waiting ? `等待业务回答 · ${path2.clarifications.reduce((sum,item) => sum + item.questions.length, 0)} 个已记录问题` : path2.runSnapshot ? `本轮：${statusLabel(path2.runSnapshot.status)}` : "已建立任务，尚未开始分析"}</p></header>;
}
function MissionOverview({path2}: {path2: Path2WorkbenchState}) {
  const mission = path2.missionSnapshot?.mission ?? path2.selectedMission;
  return <section className="mission-overview">{mission ? <><p className="path2-eyebrow">当前目标</p><h2>{mission.goal}</h2><p>资料范围与对话在右侧延续。你可以打开关系与字段核对草案，或查看执行历史。</p>{path2.latestDraft && <p>候选草案 v{path2.latestDraft.version} · {statusLabel(path2.latestDraft.status)}。候选结果不等于正式业务契约。</p>}</> : <><img src={BRAND_MARK_URL} alt="" /><p className="path2-eyebrow">从讨论到清晰的业务口径</p><h2>说出问题，<br/>一起找到有依据的答案。</h2><p>在右侧告诉 Agent 你想弄清什么。这里会随着对话，展示资料、待确认的问题和逐步形成的成果。</p><ol><li><strong>从你的问题开始</strong><p>不必预先定义任务，先聊业务背景。</p></li><li><strong>随时核对资料依据</strong><p>让字段、关系和未知事项有处可查。</p></li><li><strong>关键口径，由你确认</strong><p>自然语言补充，整理后再核对采用。</p></li></ol></>}</section>;
}
function AgentPanel({ path2, conversation, onReference, onHistory, onSources, expanded, onExpand }: { path2: Path2WorkbenchState; conversation: ConversationDialogueState; onReference: (ref: MessageReference) => void; onHistory: () => void; onSources: () => void; expanded: boolean; onExpand: () => void }) {
  return <aside className="agent-panel" aria-label="Agent 对话"><header className="agent-panel-header"><div className="agent-panel-title-group"><h2>{AGENT_COPY.title}</h2><span>一起把问题弄清楚</span></div><button className="agent-panel-toggle" onClick={onExpand} aria-pressed={expanded}>{expanded ? "恢复双栏" : "展开对话"}</button></header><div id="agent-panel-content" className="agent-panel-content"><ConversationDialogue state={path2} d={conversation} onReference={onReference} onHistory={onHistory} onSources={onSources} /></div></aside>;
}

function App() {
  const [activeArea, setActiveArea] = useState<AreaId>("mission");
  const [activeTab, setActiveTab] = useState<ObjectTabId>("mission");
  const [selectedObject, setSelectedObject] = useState<MissionObjectId>("relationship");
  const [apiState, setApiState] = useState<ApiState>("loading");
  const [snapshot, setSnapshot] = useState<WorkbenchSnapshot | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [selectedWorkspace, setSelectedWorkspace] = useState<Workspace | null>(null);
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
  const followCurrent = () => {setFocusedReference(null);setActiveArea(path2.selectedMission?.status === "waiting_for_human" ? "clarifications" : "mission");setActiveTab(path2.latestDraft && path2.selectedMission?.status !== "waiting_for_human" ? "relationship" : "mission");};
  useEffect(() => {if(following) followCurrent();}, [following, path2.selectedMission?.mission_id, path2.selectedMission?.status, path2.latestDraft?.draft_id, path2.latestDraft?.version]);
  const addReference = (ref: MessageReference) => { conversation.addReference(ref); setMobileView("agent"); };
  const inspectReference = (ref: MessageReference) => {
    setFollowing(false);
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
    setFollowing(false);setMobileView("result");
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

  return (
    <div
      className="app-shell agent-led-shell"
      data-api-state={apiState}
      data-connection-state={connectionState}
      data-path2-state="workbench"
    >
      <AgentLayout expanded={expanded} mobileView={mobileView} onMobileView={setMobileView} navOpen={navOpen} onNavOpen={setNavOpen}
        sidebar={<aside className="agent-led-navigation" aria-label="工作区导航"><Brand/><WorkspaceSwitcher selectedWorkspace={selectedWorkspace} onWorkspaceChange={setSelectedWorkspace}/><button className="new-conversation-button" disabled={conversation.sending} onClick={() => {conversation.newConversation();setFollowing(true);setMobileView("agent");setNavOpen(false);}}>＋ 新对话</button><p className="nav-section-label">最近对话</p>{conversation.list.map(item => <button className={conversation.conversation?.conversation_id===item.conversation_id ? "nav-conversation selected" : "nav-conversation"} key={item.conversation_id} onClick={() => {void conversation.select(item);setFollowing(true);setNavOpen(false);}}><Icon name="reader"/><span>{item.title}</span></button>)}{path2.missionState.items.filter(mission=>!conversation.list.some(item=>item.mission_id===mission.mission_id)).map(mission=><button className="nav-conversation" key={mission.mission_id} onClick={()=>{void conversation.attachMission(mission.mission_id);setFollowing(true);setNavOpen(false);}}><Icon name="reader"/><span>{mission.title}</span></button>)}{!conversation.list.length&&!path2.missionState.items.length&&<p className="nav-empty">从右侧开始新对话</p>}<div className="nav-section-label"><span>资料库</span><button onClick={() => {setFollowing(false);setActiveArea("sources");setActiveTab("mission");setMobileView("result");setNavOpen(false);}}>添加资料</button></div>{path2.sourceState.items.map(source => <button className={selectedObject === `source:${source.revision_id}` ? "nav-conversation selected" : "nav-conversation"} key={source.revision_id} onClick={() => handleObjectSelect(`source:${source.revision_id}`)}><Icon name="file-text"/><span>{source.original_name}</span></button>)}<div className="navigation-bottom"><details><summary>工作区视图</summary>{areas.map(area => <button key={area.id} onClick={() => {setFollowing(false);setActiveArea(area.id);setActiveTab("mission");setFocusedReference(null);setNavOpen(false);setMobileView("result");}}>{area.label}</button>)}</details><ModelSettings/><DemoEntry onLoaded={(workspace, task, revisions) => {setSelectedWorkspace(workspace);setDemoSetup({workspaceId:workspace.workspace_id,task,revisions});setFollowing(true);setMobileView("agent");}}/><small>仅在本机运行</small></div></aside>}
        center={<CenterPanel activeArea={activeArea} activeTab={activeTab} onTabChange={tab => {setFollowing(false);setActiveArea("mission");setActiveTab(tab);setFocusedReference(null);}} focusedReference={focusedReference} clearReference={() => setFocusedReference(null)} dialogue={dialogue} onReference={addReference} path2={path2} following={following} onFollow={() => {setFollowing(true);followCurrent();}}/>}
        agent={<AgentPanel expanded={expanded} onExpand={() => setExpanded(value => !value)} conversation={conversation} path2={path2} onReference={inspectReference} onSources={() => {setFollowing(false);setFocusedReference(null);setActiveArea("sources");setActiveTab("mission");setMobileView("result");}} onHistory={() => {setFollowing(false);setFocusedReference(null);setActiveTab("history");setMobileView("result");}}/>}/>
    </div>
  );
}

export default App;
