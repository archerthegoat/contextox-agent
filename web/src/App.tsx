import { useEffect, useState, type CSSProperties } from "react";

import { fetchWorkbench, type WorkbenchSnapshot, type Workspace } from "./api/client";
import {
  Path2AgentContent,
  Path2Workbench,
  statusLabel,
  sourceIdentityEquals,
  sourceIdentityFromRevision,
  usePath2Workbench,
  type DefinitionDraft,
  type Path2WorkbenchState,
  type SourceRevision,
} from "./Path2Workbench";
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
    emptyBody: "来源导入将在下一阶段开放。",
  },
  mission: {
    label: "任务",
    title: "高潜客户定义",
    description: "围绕一个清晰目标，把来源、实体、冲突和确认串成可回看的工作链。",
    emptyTitle: "暂无任务",
    emptyBody: "任务入口将在下一阶段开放。",
  },
  clarifications: {
    label: "待澄清",
    title: "待澄清问题",
    description: "把定义中的未知交给合适的人确认，再回到同一个工作对象。",
    emptyTitle: "暂无澄清请求",
    emptyBody: "澄清入口将在下一阶段开放。",
  },
  contract: {
    label: "业务契约",
    title: "业务契约草案",
    description: "让已经确认的定义保留来源、版本和责任边界。",
    emptyTitle: "暂无业务契约",
    emptyBody: "业务契约入口将在下一阶段开放。",
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
  title: "Agent Run",
  mode: "公开状态",
  composerPlaceholder: "当前版本不提供自由对话输入",
} as const;

export type ObjectTabId = "mission" | "relationship";

export const OBJECT_TABS: Array<{ id: ObjectTabId; label: string }> = [
  { id: "mission", label: "高潜客户定义" },
  { id: "relationship", label: "客户粒度关系" },
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
  missionTitle,
}: {
  selectedObject: MissionObjectId;
  onObjectSelect: (objectId: MissionObjectId) => void;
  sources: SourceRevision[];
  missionTitle: string;
}) {
  return (
    <aside className="object-pane" aria-label="任务对象">
      <div className="object-pane-header">
        <h2>任务</h2>
        <div className="object-pane-actions">
          <button type="button" aria-label="新增对象">
            新增
          </button>
          <button type="button" aria-label="筛选对象">
            筛选
          </button>
        </div>
      </div>

      <label className="object-search">
        <span className="sr-only">搜索任务内容</span>
        <input type="search" placeholder="搜索任务内容" />
      </label>

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
          <span className="tree-row-label">客户粒度关系</span>
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

type GraphNodeId =
  | "customers-source"
  | "orders-source"
  | "customers-entity"
  | "orders-entity"
  | "conflict"
  | "confirmation"
  | "contract";

type GraphNodeProps = {
  id: GraphNodeId;
  icon: IconName;
  kind: string;
  title: string;
  subtitle: string;
  className: string;
  selected: boolean;
  onSelect: (id: GraphNodeProps["id"]) => void;
};

function GraphNode({ id, icon, kind, title, subtitle, className, selected, onSelect }: GraphNodeProps) {
  return (
    <button
      type="button"
      className={`graph-node ${className}${selected ? " graph-node-selected" : ""}`}
      aria-pressed={selected}
      aria-label={`${title}，${kind}，${subtitle}`}
      onClick={() => onSelect(id)}
    >
      <span className="graph-node-icon">
        <Icon name={icon} />
      </span>
      <span className="graph-node-title">{title}</span>
      <span className="graph-node-subtitle">{subtitle}</span>
    </button>
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

function GraphWires({
  relationshipPresent,
  leftSourceMatched,
  rightSourceMatched,
  completeRelationship,
}: {
  relationshipPresent: boolean;
  leftSourceMatched: boolean;
  rightSourceMatched: boolean;
  completeRelationship: boolean;
}) {
  return (
    <div className="graph-wires" aria-hidden="true">
      {relationshipPresent && leftSourceMatched && rightSourceMatched ? <span className="wire wire-source-branch" /> : null}
      {relationshipPresent && leftSourceMatched && rightSourceMatched ? <span className="wire wire-source-top" /> : null}
      {relationshipPresent && leftSourceMatched && rightSourceMatched ? <span className="wire wire-source-middle" /> : null}
      {relationshipPresent && leftSourceMatched ? (
        <span className={`wire ${rightSourceMatched ? "wire-source-to-entity-top" : "wire-source-direct-top"}`} />
      ) : null}
      {relationshipPresent && rightSourceMatched ? (
        <span className={`wire ${leftSourceMatched ? "wire-source-to-entity-bottom" : "wire-source-direct-bottom"}`} />
      ) : null}
      {completeRelationship ? <span className="wire wire-entity-join" /> : null}
      {completeRelationship ? <span className="wire wire-entity-top" /> : null}
      {completeRelationship ? <span className="wire wire-entity-bottom" /> : null}
      {completeRelationship ? <span className="wire wire-conflict" /> : null}
      {completeRelationship ? <span className="wire wire-confirmation" /> : null}
      {completeRelationship ? <span className="wire wire-contract" /> : null}
    </div>
  );
}

function RelationshipGraph({
  selectedNode,
  onNodeSelect,
  path2,
}: {
  selectedNode: GraphNodeProps["id"];
  onNodeSelect: (id: GraphNodeProps["id"]) => void;
  path2: Path2WorkbenchState;
}) {
  const relationshipCandidates = path2.latestDraft?.relationships ?? [];
  const sources = path2.sourceState.items;
  const hasWorkspace = Boolean(path2.workspaceId);
  const graphResolution = relationshipGraphResolution(relationshipCandidates, sources);
  const relationship = graphResolution.relationship;

  const sourceForTable = (table: RelationshipCandidate["left"]) => {
    const match = sources.find((source) => sourceIdentityEquals(
      table.source_ref,
      sourceIdentityFromRevision(source),
    ));
    return {
      title: match
        ? `${match.original_name}${table.table_id ? ` · ${table.table_id}` : ""}`
        : hasWorkspace
          ? "关系来源待核验"
          : "来源待导入",
      subtitle: match ? "候选绑定的 SourceRevision" : "来源身份未匹配",
    };
  };
  const leftSource = relationship ? sourceForTable(relationship.left) : null;
  const rightSource = relationship ? sourceForTable(relationship.right) : null;
  const sourceTitles = [
    leftSource?.title ?? (hasWorkspace ? "尚无关系候选" : "来源待导入"),
    rightSource?.title ?? (hasWorkspace ? "尚无关系候选" : "来源待导入"),
  ];
  const sourceSubtitles = [
    leftSource?.subtitle ?? (hasWorkspace ? "等待 DefinitionDraft" : "请选择 Workspace"),
    rightSource?.subtitle ?? (hasWorkspace ? "等待 DefinitionDraft" : "请选择 Workspace"),
  ];
  const leftEntity = relationship?.left.table_id || (hasWorkspace ? "实体待识别" : "实体待加载");
  const rightEntity = relationship?.right.table_id || (hasWorkspace ? "实体待识别" : "实体待加载");
  const relationshipStatus = relationship ? statusLabel(relationship.evidence_status) : "尚无关系草案";
  const hasSourceMismatch = Boolean(relationship && !graphResolution.completeRelationship);
  const canvasNote = !hasWorkspace
    ? "请先选择 Workspace；下方仅保留关系区域的空状态框架。"
    : path2.missionSnapshotState.issue
      ? "当前 Mission/Run 快照不可用；关系图不显示预置业务结果。"
      : path2.missionSnapshotState.status === "loading"
        ? "正在回读当前 Workspace 的 DefinitionDraft…"
        : relationship
          ? `仅展示当前 Workspace 快照中的关系候选；共 ${relationshipCandidates.length} 条，语义仍待确认。`
          : "当前 Workspace 尚无 DefinitionDraft；不显示预置业务结果。";
  return (
    <section className="relationship-canvas" aria-label="客户粒度关系图，可横向滚动查看" tabIndex={0}>
      <div className="relationship-canvas-inner">
        <div className="relationship-canvas-note" role="status">{canvasNote}</div>
        <GraphWires
          relationshipPresent={Boolean(relationship)}
          leftSourceMatched={graphResolution.leftSourceMatched}
          rightSourceMatched={graphResolution.rightSourceMatched}
          completeRelationship={graphResolution.completeRelationship}
        />
        <GraphNode
          id="customers-source"
          icon="file-text"
          kind="来源"
          title={sourceTitles[0]}
          subtitle={sourceSubtitles[0]}
          className="graph-source graph-source-top"
          selected={selectedNode === "customers-source"}
          onSelect={onNodeSelect}
        />
        <GraphNode
          id="orders-source"
          icon="file-text"
          kind="来源"
          title={sourceTitles[1]}
          subtitle={sourceSubtitles[1]}
          className="graph-source graph-source-middle"
          selected={selectedNode === "orders-source"}
          onSelect={onNodeSelect}
        />
        <GraphNode
          id="customers-entity"
          icon="cube"
          kind="实体"
          title={leftEntity}
          subtitle={relationship ? "左侧表" : "尚未识别"}
          className="graph-entity graph-entity-top"
          selected={selectedNode === "customers-entity"}
          onSelect={onNodeSelect}
        />
        <GraphNode
          id="orders-entity"
          icon="cube"
          kind="实体"
          title={rightEntity}
          subtitle={relationship ? "右侧表" : "尚未识别"}
          className="graph-entity graph-entity-bottom"
          selected={selectedNode === "orders-entity"}
          onSelect={onNodeSelect}
        />
        <GraphNode
          id="conflict"
          icon="mix"
          kind="定义冲突"
          title={relationship?.evidence_status === "conflict" ? "关系冲突" : "定义检查"}
          subtitle={hasSourceMismatch ? "来源身份未匹配" : relationshipStatus}
          className="graph-conflict"
          selected={selectedNode === "conflict"}
          onSelect={onNodeSelect}
        />
        <GraphNode
          id="confirmation"
          icon="question-mark-circled"
          kind="用户确认"
          title="待用户确认"
          subtitle={path2.latestDraft?.unresolved_items.length ? `${path2.latestDraft.unresolved_items.length} 个未决项` : "尚无问题"}
          className="graph-confirmation"
          selected={selectedNode === "confirmation"}
          onSelect={onNodeSelect}
        />
        <GraphNode
          id="contract"
          icon="file-text"
          kind="业务契约"
          title="定义草案"
          subtitle={path2.latestDraft ? `version ${path2.latestDraft.version}` : "尚未生成"}
          className="graph-contract"
          selected={selectedNode === "contract"}
          onSelect={onNodeSelect}
        />
        {relationshipCandidates.length > 0 ? (
          <div className="relationship-candidate-strip" aria-label="关系候选列表">
            <strong>关系候选</strong>
            {relationshipCandidates.map((candidate) => (
              <div className="relationship-candidate-item" key={candidate.relationship_key}>
                <code>{candidate.relationship_key}</code>
                <span>{candidate.left.table_id || "根表"} ↔ {candidate.right.table_id || "根表"}</span>
                <span className="relationship-candidate-status">{statusLabel(candidate.evidence_status)}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
function CenterPanel({
  activeArea,
  activeTab,
  onTabChange,
  selectedNode,
  onNodeSelect,
  path2,
}: {
  activeArea: AreaId;
  activeTab: ObjectTabId;
  onTabChange: (tab: ObjectTabId) => void;
  selectedNode: GraphNodeId;
  onNodeSelect: (id: GraphNodeId) => void;
  path2: Path2WorkbenchState;
}) {
  const content = AREA_CONTENT[activeArea];
  const title = activeArea === "mission" && activeTab === "relationship" ? "客户粒度关系" : content.title;

  return (
    <main className="center-panel" aria-labelledby="center-title">
      <OpenObjectTabs activeTab={activeTab} onTabChange={onTabChange} />
      <div className="center-toolbar">
        <h1 id="center-title">{title}</h1>
        <div className="canvas-tools" aria-label="画布工具">
          <button type="button">适应画布</button>
          <button type="button">缩放</button>
          <button type="button">布局</button>
          <button type="button">更多</button>
        </div>
      </div>
      {activeArea === "mission" && activeTab === "relationship" ? (
        <RelationshipGraph
          selectedNode={selectedNode}
          onNodeSelect={onNodeSelect}
          path2={path2}
        />
      ) : (
        <Path2Workbench state={path2} activeArea={activeArea} />
      )}
    </main>
  );
}

function AgentPanel({ path2 }: { path2: Path2WorkbenchState }) {
  const [isOpen, setIsOpen] = useState(true);
  const contentId = "agent-panel-content";
  const toggleLabel = isOpen ? "折叠 Agent Run" : "展开 Agent Run";

  return (
    <aside className={`agent-panel${isOpen ? "" : " agent-panel-collapsed"}`} aria-label="Agent Run 公开状态">
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
        <Path2AgentContent state={path2} />
      </div>
    </aside>
  );
}

function App() {
  const [activeArea, setActiveArea] = useState<AreaId>("mission");
  const [activeTab, setActiveTab] = useState<ObjectTabId>("relationship");
  const [selectedObject, setSelectedObject] = useState<MissionObjectId>("relationship");
  const [selectedNode, setSelectedNode] = useState<GraphNodeId>("conflict");
  const [apiState, setApiState] = useState<ApiState>("loading");
  const [snapshot, setSnapshot] = useState<WorkbenchSnapshot | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [selectedWorkspace, setSelectedWorkspace] = useState<Workspace | null>(null);
  const path2 = usePath2Workbench(selectedWorkspace);

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
    if (objectId.startsWith("source:")) {
      setActiveArea("sources");
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
      <div className="workspace-layout">
        <PrimaryRail areas={areas} activeArea={activeArea} onAreaChange={setActiveArea} />
        <ObjectPane
          selectedObject={selectedObject}
          onObjectSelect={handleObjectSelect}
          sources={path2.sourceState.items}
          missionTitle={path2.selectedMission?.title ?? "当前 Mission"}
        />
        <CenterPanel
          activeArea={activeArea}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          selectedNode={selectedNode}
          onNodeSelect={setSelectedNode}
          path2={path2}
        />
        <AgentPanel path2={path2} />
      </div>
    </div>
  );
}

export default App;
