import { useEffect, useState, type CSSProperties } from "react";

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
import { useTaskDialogue, ReferenceInspector, TaskConversation, TaskExecutionHistory, type DialogueState, type MessageReference } from "./TaskDialogue";
import WorkspaceSwitcher from "./WorkspaceSwitcher";
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

function Topbar({
  selectedWorkspace,
  onWorkspaceChange,
}: {
  selectedWorkspace: Workspace | null;
  onWorkspaceChange: (workspace: Workspace | null) => void;
}) {
  return (
    <header className="topbar">
      <div className="topbar-brand">
        <Brand />
      </div>
      <div className="topbar-workspace">
        <WorkspaceSwitcher
          selectedWorkspace={selectedWorkspace}
          onWorkspaceChange={onWorkspaceChange}
        />
      </div>
      <div className="topbar-actions" aria-label="工作区工具">
        <button type="button" className="utility-button">
          帮助
        </button>
        <button type="button" className="utility-button">
          文档
        </button>
        <button type="button" className="utility-button">
          通知
        </button>
        <button type="button" className="utility-button utility-button-muted">
          演示
        </button>
      </div>
    </header>
  );
}

function PrimaryRail({
  areas,
  activeArea,
  onAreaChange,
}: {
  areas: AreaNavigationItem[];
  activeArea: AreaId;
  onAreaChange: (area: AreaId) => void;
}) {
  return (
    <aside className="primary-rail" aria-label="工作区模块">
      <nav className="primary-nav" aria-label="主要模块">
        {areas.map((area) => {
          const isActive = activeArea === area.id;
          return (
            <button
              key={area.id}
              type="button"
              className={`primary-nav-item${isActive ? " primary-nav-item-active" : ""}`}
              aria-current={isActive ? "page" : undefined}
              title={area.description}
              onClick={() => onAreaChange(area.id)}
            >
              <span className="primary-nav-icon">
                <Icon name={AREA_NAV_ICONS[area.id]} />
              </span>
              <span className="primary-nav-label">{area.label}</span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

type MissionObjectId = string;

function ObjectPane({
  selectedObject,
  onObjectSelect,
  sources,
  missionTitle, path2,
}: {
  selectedObject: MissionObjectId;
  onObjectSelect: (objectId: MissionObjectId) => void;
  sources: SourceRevision[];
  missionTitle: string; path2: Path2WorkbenchState;
}) {
  return (
    <aside className="object-pane" aria-label="任务对象">
      <div className="object-pane-header">
        <h2>任务</h2>
      </div>
      <label className="task-selector">当前任务<select aria-label="切换任务" value={path2.selectedMission?.mission_id ?? ""} onChange={e => void path2.selectMission(e.target.value)}><option value="" disabled>请选择任务</option>{path2.missionState.items.map(m => <option value={m.mission_id} key={m.mission_id}>{m.title}</option>)}</select></label>

      <div className="object-tree" role="tree" aria-label="任务对象树">
        <button
          type="button"
          className={`tree-row tree-root${selectedObject === "mission" ? " tree-row-selected" : ""}`}
          role="treeitem"
          aria-selected={selectedObject === "mission"}
          onClick={() => onObjectSelect("mission")}
        >
          <span className="tree-disclosure" aria-hidden="true">
            <Icon name="chevron-down" />
          </span>
          <span className="tree-row-icon">
            <Icon name="target" />
          </span>
          <span className="tree-row-label">{missionTitle}</span>
        </button>

        <div className="tree-children" role="group">
          <button type="button" className="tree-row tree-folder" role="treeitem" aria-expanded="true">
            <span className="tree-disclosure" aria-hidden="true">
              <Icon name="chevron-down" />
            </span>
            <span className="tree-row-icon">
              <Icon name="archive" />
            </span>
            <span className="tree-row-label">数据与文档</span>
          </button>

          <div className="tree-file-list" role="group">
            {sources.slice(0, 8).map((source) => {
              const objectId = `source:${source.revision_id}`;
              const isMarkdown = source.media_type === "text/markdown" || source.media_type === "text/plain";
              return (
                <button
                  type="button"
                  className={`tree-row tree-file${selectedObject === objectId ? " tree-row-selected" : ""}`}
                  role="treeitem"
                  aria-selected={selectedObject === objectId}
                  key={source.revision_id}
                  onClick={() => onObjectSelect(objectId)}
                >
                  <span className={`file-badge ${isMarkdown ? "file-badge-md" : "file-badge-csv"}`} aria-hidden="true">
                    <Icon name={isMarkdown ? "reader" : "file-text"} />
                  </span>
                  <span className="tree-row-label">{source.original_name}</span>
                </button>
              );
            })}
            {sources.length === 0 ? <p className="tree-empty">当前 Workspace 尚无已回读来源</p> : null}
          </div>
        </div>

        <button
          type="button"
          className={`tree-row tree-relationship${selectedObject === "relationship" ? " tree-row-selected" : ""}`}
          role="treeitem"
          aria-selected={selectedObject === "relationship"}
          onClick={() => onObjectSelect("relationship")}
        >
          <span className="tree-disclosure" aria-hidden="true">
            <Icon name="chevron-down" />
          </span>
          <span className="object-type-tag" aria-hidden="true">
            <Icon name="mix" />
          </span>
          <span className="tree-row-label">关系与字段</span>
        </button>
      </div>
    </aside>
  );
}

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
  path2,
}: {
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
        <Path2Workbench state={path2} activeArea={activeArea} />
      )}
    </main>
  );
}

function AgentPanel({ path2, dialogue, onReference, onHistory, onResults, onClarifications }: { path2: Path2WorkbenchState; dialogue: DialogueState; onReference: (ref: MessageReference) => void; onHistory: () => void; onResults: () => void; onClarifications: () => void }) {
  const [isOpen, setIsOpen] = useState(true);
  const contentId = "agent-panel-content";
  const toggleLabel = isOpen ? "折叠任务对话" : "展开任务对话";

  return (
    <aside className={`agent-panel${isOpen ? "" : " agent-panel-collapsed"}`} aria-label="任务 Agent 对话">
      <div className="agent-panel-header">
        <div className="agent-panel-title-group">
          <h2>{AGENT_COPY.title}</h2>
          <span>{AGENT_COPY.mode}</span>
        </div>
        <button
          type="button"
          className="agent-panel-toggle"
          aria-label={toggleLabel}
          aria-expanded={isOpen}
          aria-controls={contentId}
          title={toggleLabel}
          onClick={() => setIsOpen((value) => !value)}
        >
          <Icon name={isOpen ? "double-arrow-right" : "double-arrow-left"} />
          <span className="sr-only">{toggleLabel}</span>
        </button>
      </div>
      <div id={contentId} className="agent-panel-content" hidden={!isOpen}>
        <TaskConversation state={path2} dialogue={dialogue} onReference={onReference} onHistory={onHistory} onResults={onResults} onClarifications={onClarifications} />
      </div>
    </aside>
  );
}

function App() {
  const [activeArea, setActiveArea] = useState<AreaId>("mission");
  const [activeTab, setActiveTab] = useState<ObjectTabId>("mission");
  const [selectedObject, setSelectedObject] = useState<MissionObjectId>("relationship");
  const [apiState, setApiState] = useState<ApiState>("loading");
  const [snapshot, setSnapshot] = useState<WorkbenchSnapshot | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [selectedWorkspace, setSelectedWorkspace] = useState<Workspace | null>(null);
  const path2 = usePath2Workbench(selectedWorkspace);
  const dialogue = useTaskDialogue(path2);
  const [focusedReference, setFocusedReference] = useState<MessageReference | null>(null);
  useEffect(() => {setFocusedReference(null);}, [path2.workspaceId, path2.selectedMission?.mission_id]);
  const [mobileView, setMobileView] = useState<"result" | "agent">("result");
  const [navOpen, setNavOpen] = useState(false);
  const addReference = (ref: MessageReference) => { dialogue.addReference(ref); setMobileView("agent"); };
  const inspectReference = (ref: MessageReference) => {
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
      className="app-shell"
      data-api-state={apiState}
      data-connection-state={connectionState}
      data-path2-state="workbench"
    >
      <Topbar
        selectedWorkspace={selectedWorkspace}
        onWorkspaceChange={setSelectedWorkspace}
      />
      <div className="compact-controls"><button aria-expanded={navOpen} onClick={() => setNavOpen(!navOpen)}>任务与资料</button><button aria-pressed={mobileView === "result"} onClick={() => setMobileView("result")}>结果</button><button aria-pressed={mobileView === "agent"} onClick={() => setMobileView("agent")}>Agent 对话</button></div>
      <div className={`workspace-layout mobile-${mobileView}${navOpen ? " nav-open" : ""}`}>
        <PrimaryRail areas={areas} activeArea={activeArea} onAreaChange={area => {setActiveArea(area); setActiveTab("mission"); setFocusedReference(null); setNavOpen(false);}} />
        <ObjectPane
          selectedObject={selectedObject}
          onObjectSelect={handleObjectSelect}
          sources={path2.sourceState.items}
          missionTitle={path2.selectedMission?.title ?? "当前任务"}
          path2={path2}
        />
        <CenterPanel
          activeArea={activeArea}
          activeTab={activeTab}
          onTabChange={tab => {setActiveArea("mission"); setActiveTab(tab); setFocusedReference(null);}}
          focusedReference={focusedReference}
          clearReference={() => setFocusedReference(null)}
          dialogue={dialogue}
          onReference={addReference}
          path2={path2}
        />
        <AgentPanel onClarifications={() => {setFocusedReference(null); setActiveArea("clarifications"); setActiveTab("mission"); setMobileView("result");}} path2={path2} dialogue={dialogue} onReference={inspectReference} onHistory={() => {setFocusedReference(null); setActiveTab("history"); setMobileView("result");}} onResults={() => {setFocusedReference(null); setActiveArea("mission"); setActiveTab("relationship"); setMobileView("result");}} />
      </div>
    </div>
  );
}

export default App;
