import { useEffect, useState } from "react";

import { fetchWorkbench, type WorkbenchSnapshot } from "./api/client";
import "./styles.css";

export type AreaId = "sources" | "mission" | "clarifications" | "contract";

export type AreaContent = {
  label: string;
  title: string;
  description: string;
  emptyTitle: string;
  emptyBody: string;
};

export const AREA_CONTENT: Record<AreaId, AreaContent> = {
  sources: {
    label: "Sources",
    title: "授权来源",
    description: "查看已授权的资料与它们在定义工作中的位置。",
    emptyTitle: "暂无来源对象",
    emptyBody: "来源导入将在下一阶段开放。",
  },
  mission: {
    label: "Mission",
    title: "统一-高潜客户定义",
    description: "围绕一个清晰目标，把来源、实体、冲突和确认串成可回看的工作链。",
    emptyTitle: "暂无 Mission",
    emptyBody: "任务入口将在下一阶段开放。",
  },
  clarifications: {
    label: "Clarifications",
    title: "待确认问题",
    description: "把定义中的未知交给合适的人确认，再回到同一个工作对象。",
    emptyTitle: "暂无澄清请求",
    emptyBody: "澄清入口将在下一阶段开放。",
  },
  contract: {
    label: "Contract",
    title: "Contract 草案",
    description: "让已经确认的定义保留来源、版本和责任边界。",
    emptyTitle: "暂无 Contract",
    emptyBody: "Contract 入口将在下一阶段开放。",
  },
};

export type AreaNavigationItem = {
  id: AreaId;
  label: string;
  description: string;
};

export const AREA_NAV_PRESENTATION: Record<AreaId, Omit<AreaNavigationItem, "id">> = {
  sources: { label: "Sources", description: "授权资料" },
  mission: { label: "Mission", description: "任务对象" },
  clarifications: { label: "Clarifications", description: "待确认问题" },
  contract: { label: "Contract", description: "定义版本" },
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
  title: "演示对话",
  mode: "演示模式",
  composerPlaceholder: "演示模式，暂不可发送",
} as const;

export type AgentMessage = {
  id: string;
  role: "agent" | "user";
  time: string;
  body: string;
};

export const DEMO_MESSAGES: AgentMessage[] = [
  {
    id: "agent-1",
    role: "agent",
    time: "13:22",
    body: "我发现订单表和客户表对“客户”的粒度不同。",
  },
  {
    id: "user-1",
    role: "user",
    time: "13:23",
    body: "按企业客户统计，门店属于企业。",
  },
  {
    id: "agent-2",
    role: "agent",
    time: "13:23",
    body: "收到。我会把企业作为主实体，并把门店映射列为待确认规则。",
  },
];

export type ObjectTabId = "mission" | "relationship";

export const OBJECT_TABS: Array<{ id: ObjectTabId; label: string }> = [
  { id: "mission", label: "统一-高潜客户定义" },
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

function Topbar() {
  return (
    <header className="topbar">
      <div className="topbar-brand">
        <Brand />
      </div>
      <div className="topbar-workspace">
        <button className="workspace-switcher" type="button" aria-label="切换工作区">
          演示工作区
        </button>
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
    <aside className="primary-rail" aria-label="Workspace modules">
      <nav className="primary-nav" aria-label="主要模块">
        {areas.map((area) => {
          const isActive = activeArea === area.id;
          return (
            <button
              key={area.id}
              type="button"
              className={`primary-nav-item${isActive ? " primary-nav-item-active" : ""}`}
              aria-current={isActive ? "page" : undefined}
              onClick={() => onAreaChange(area.id)}
            >
              <span className="primary-nav-label">{area.label}</span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

type MissionObjectId = "mission" | "customers" | "orders" | "notes" | "relationship";

function ObjectPane({
  selectedObject,
  onObjectSelect,
}: {
  selectedObject: MissionObjectId;
  onObjectSelect: (objectId: MissionObjectId) => void;
}) {
  return (
    <aside className="object-pane" aria-label="Mission 对象">
      <div className="object-pane-header">
        <h2>Mission</h2>
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
        <span className="sr-only">搜索 Mission 内容</span>
        <input type="search" placeholder="搜索 Mission 内容" />
      </label>

      <div className="object-tree" role="tree" aria-label="Mission 对象树">
        <button
          type="button"
          className={`tree-row tree-root${selectedObject === "mission" ? " tree-row-selected" : ""}`}
          role="treeitem"
          aria-selected={selectedObject === "mission"}
          onClick={() => onObjectSelect("mission")}
        >
          <span className="tree-disclosure" aria-hidden="true">
            展开
          </span>
          <span className="tree-row-label">统一-高潜客户定义</span>
        </button>

        <div className="tree-children" role="group">
          <button type="button" className="tree-row tree-folder" role="treeitem" aria-expanded="true">
            <span className="tree-disclosure" aria-hidden="true">
              展开
            </span>
            <span className="tree-row-label">数据与文档</span>
          </button>

          <div className="tree-file-list" role="group">
            <button
              type="button"
              className={`tree-row tree-file${selectedObject === "customers" ? " tree-row-selected" : ""}`}
              role="treeitem"
              aria-selected={selectedObject === "customers"}
              onClick={() => onObjectSelect("customers")}
            >
              <span className="file-badge file-badge-csv" aria-hidden="true">
                CSV
              </span>
              <span className="tree-row-label">客户主数据.csv</span>
            </button>
            <button
              type="button"
              className={`tree-row tree-file${selectedObject === "orders" ? " tree-row-selected" : ""}`}
              role="treeitem"
              aria-selected={selectedObject === "orders"}
              onClick={() => onObjectSelect("orders")}
            >
              <span className="file-badge file-badge-csv" aria-hidden="true">
                CSV
              </span>
              <span className="tree-row-label">订单明细.csv</span>
            </button>
            <button
              type="button"
              className={`tree-row tree-file${selectedObject === "notes" ? " tree-row-selected" : ""}`}
              role="treeitem"
              aria-selected={selectedObject === "notes"}
              onClick={() => onObjectSelect("notes")}
            >
              <span className="file-badge file-badge-md" aria-hidden="true">
                MD
              </span>
              <span className="tree-row-label">业务口径说明.md</span>
            </button>
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
            展开
          </span>
          <span className="object-type-tag" aria-hidden="true">
            图
          </span>
          <span className="tree-row-label">客户粒度关系</span>
        </button>
      </div>
    </aside>
  );
}

function OpenObjectTabs({ activeTab, onTabChange }: { activeTab: ObjectTabId; onTabChange: (tab: ObjectTabId) => void }) {
  return (
    <div className="center-tabs" role="tablist" aria-label="已打开对象">
      {OBJECT_TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={activeTab === tab.id}
          className={`object-tab${activeTab === tab.id ? " object-tab-active" : ""}`}
          onClick={() => onTabChange(tab.id)}
        >
          <span>{tab.label}</span>
          <span className="object-tab-close" aria-hidden="true">
            关闭
          </span>
        </button>
      ))}
      <button type="button" className="object-tab-add" aria-label="打开对象">
        打开
      </button>
    </div>
  );
}

type GraphNodeId =
  | "customers-source"
  | "orders-source"
  | "notes-source"
  | "customers-entity"
  | "orders-entity"
  | "conflict"
  | "confirmation"
  | "contract";

type GraphNodeProps = {
  id: GraphNodeId;
  kind: string;
  title: string;
  subtitle: string;
  className: string;
  selected: boolean;
  onSelect: (id: GraphNodeProps["id"]) => void;
};

function GraphNode({ id, kind, title, subtitle, className, selected, onSelect }: GraphNodeProps) {
  return (
    <button
      type="button"
      className={`graph-node ${className}${selected ? " graph-node-selected" : ""}`}
      aria-pressed={selected}
      aria-label={`${title}，${subtitle}`}
      onClick={() => onSelect(id)}
    >
      <span className="graph-node-kind">{kind}</span>
      <span className="graph-node-title">{title}</span>
      <span className="graph-node-subtitle">{subtitle}</span>
    </button>
  );
}

function GraphWires() {
  return (
    <div className="graph-wires" aria-hidden="true">
      <span className="wire wire-source-branch" />
      <span className="wire wire-source-top" />
      <span className="wire wire-source-middle" />
      <span className="wire wire-source-bottom" />
      <span className="wire wire-entity-join" />
      <span className="wire wire-entity-top" />
      <span className="wire wire-entity-bottom" />
      <span className="wire wire-conflict" />
      <span className="wire wire-confirmation" />
      <span className="wire wire-contract" />
    </div>
  );
}

function RelationshipGraph({
  selectedNode,
  onNodeSelect,
}: {
  selectedNode: GraphNodeProps["id"];
  onNodeSelect: (id: GraphNodeProps["id"]) => void;
}) {
  return (
    <section className="relationship-canvas" aria-label="客户粒度关系图">
      <GraphWires />
      <GraphNode
        id="customers-source"
        kind="来源 · CSV"
        title="客户主数据.csv"
        subtitle="数据文件"
        className="graph-source graph-source-top"
        selected={selectedNode === "customers-source"}
        onSelect={onNodeSelect}
      />
      <GraphNode
        id="orders-source"
        kind="来源 · CSV"
        title="订单明细.csv"
        subtitle="数据文件"
        className="graph-source graph-source-middle"
        selected={selectedNode === "orders-source"}
        onSelect={onNodeSelect}
      />
      <GraphNode
        id="notes-source"
        kind="来源 · MD"
        title="业务口径说明.md"
        subtitle="文档文件"
        className="graph-source graph-source-bottom"
        selected={selectedNode === "notes-source"}
        onSelect={onNodeSelect}
      />
      <GraphNode
        id="customers-entity"
        kind="实体"
        title="客户实体"
        subtitle="核心实体"
        className="graph-entity graph-entity-top"
        selected={selectedNode === "customers-entity"}
        onSelect={onNodeSelect}
      />
      <GraphNode
        id="orders-entity"
        kind="实体"
        title="订单实体"
        subtitle="核心实体"
        className="graph-entity graph-entity-bottom"
        selected={selectedNode === "orders-entity"}
        onSelect={onNodeSelect}
      />
      <GraphNode
        id="conflict"
        kind="定义冲突"
        title="口径冲突"
        subtitle="客户粒度不同"
        className="graph-conflict"
        selected={selectedNode === "conflict"}
        onSelect={onNodeSelect}
      />
      <GraphNode
        id="confirmation"
        kind="用户确认"
        title="待用户确认"
        subtitle="退款订单是否计入净收入"
        className="graph-confirmation"
        selected={selectedNode === "confirmation"}
        onSelect={onNodeSelect}
      />
      <GraphNode
        id="contract"
        kind="Contract draft"
        title="Contract 草案"
        subtitle="定义与规则草案"
        className="graph-contract"
        selected={selectedNode === "contract"}
        onSelect={onNodeSelect}
      />
    </section>
  );
}

function ModuleSurface({ content }: { content: AreaContent }) {
  return (
    <section className="module-surface" aria-labelledby="module-surface-title">
      <p className="module-surface-kicker">{content.label}</p>
      <h2 id="module-surface-title">{content.title}</h2>
      <p>{content.description}</p>
      <span>
        {content.emptyTitle} · {content.emptyBody}
      </span>
    </section>
  );
}

function CenterPanel({
  activeArea,
  activeTab,
  onTabChange,
  selectedNode,
  onNodeSelect,
}: {
  activeArea: AreaId;
  activeTab: ObjectTabId;
  onTabChange: (tab: ObjectTabId) => void;
  selectedNode: GraphNodeId;
  onNodeSelect: (id: GraphNodeId) => void;
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
        <RelationshipGraph selectedNode={selectedNode} onNodeSelect={onNodeSelect} />
      ) : (
        <ModuleSurface content={content} />
      )}
    </main>
  );
}

function AgentMessage({ message }: { message: AgentMessage }) {
  const isUser = message.role === "user";
  return (
    <article className={`agent-message agent-message-${message.role}`}>
      <div className="agent-message-meta">
        {isUser ? <time dateTime={`2026-09-02T${message.time}:00+08:00`}>{message.time}</time> : null}
        <span>{isUser ? "用户" : "Agent"}</span>
        {!isUser ? <time dateTime={`2026-09-02T${message.time}:00+08:00`}>{message.time}</time> : null}
      </div>
      <p>{message.body}</p>
    </article>
  );
}

function AgentPanel() {
  const [isOpen, setIsOpen] = useState(true);

  return (
    <aside className={`agent-panel${isOpen ? "" : " agent-panel-collapsed"}`} aria-label="演示对话">
      <div className="agent-panel-header">
        <div className="agent-panel-title-group">
          <h2>{AGENT_COPY.title}</h2>
          <span>{AGENT_COPY.mode}</span>
        </div>
        <button
          type="button"
          className="agent-panel-toggle"
          aria-label={isOpen ? "关闭演示对话" : "打开演示对话"}
          onClick={() => setIsOpen((value) => !value)}
        >
          {isOpen ? "关闭" : "打开"}
        </button>
      </div>
      {isOpen ? (
        <>
          <div className="agent-conversation" aria-label="演示消息">
            {DEMO_MESSAGES.map((message) => (
              <AgentMessage key={message.id} message={message} />
            ))}
          </div>
          <div className="agent-composer">
            <textarea
              disabled
              rows={3}
              placeholder={AGENT_COPY.composerPlaceholder}
              aria-label={AGENT_COPY.composerPlaceholder}
            />
          </div>
        </>
      ) : null}
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
    if (objectId === "relationship") {
      setActiveTab("relationship");
    } else if (objectId === "mission") {
      setActiveTab("mission");
    }
  };

  return (
    <div
      className="app-shell"
      data-api-state={apiState}
      data-connection-state={connectionState}
      data-demo-state="workbench-v3"
    >
      <Topbar />
      <div className="workspace-layout">
        <PrimaryRail areas={areas} activeArea={activeArea} onAreaChange={setActiveArea} />
        <ObjectPane selectedObject={selectedObject} onObjectSelect={handleObjectSelect} />
        <CenterPanel
          activeArea={activeArea}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          selectedNode={selectedNode}
          onNodeSelect={setSelectedNode}
        />
        <AgentPanel />
      </div>
    </div>
  );
}

export default App;
