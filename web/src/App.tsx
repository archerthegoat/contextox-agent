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
    title: "添加第一份授权资料",
    description: "数契只会处理你明确选择的本地资料。",
    emptyTitle: "暂无资料",
    emptyBody: "来源导入将在下一阶段开放。",
  },
  mission: {
    label: "Missions",
    title: "Mission 尚未创建",
    description: "从明确的目标和资料开始，让每个任务都有清晰的边界。",
    emptyTitle: "暂无 Mission",
    emptyBody: "任务入口将在下一阶段开放。",
  },
  clarifications: {
    label: "Clarifications",
    title: "把未知问成能回答的问题",
    description: "把需要确认的问题交给合适的人，再回到清晰的定义。",
    emptyTitle: "暂无澄清请求",
    emptyBody: "澄清入口将在下一阶段开放。",
  },
  contract: {
    label: "Contracts",
    title: "让批准的定义可以复用",
    description: "让已确认的定义保留来源、版本和责任边界。",
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
  sources: { label: "Sources", description: "授权资料与结构化输入" },
  mission: { label: "Missions", description: "任务阶段与公开事件" },
  clarifications: { label: "Clarifications", description: "未决问题与冲突" },
  contract: { label: "Contracts", description: "协议记录与版本历史" },
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
  title: "Agent",
  body: "创建 Mission 后，数契会在这里持续协作。",
  availability: "即将开放",
  placeholder: "等待 Mission",
} as const;

const BRAND_MARK_URL = new URL("./assets/contextox-mark.png", import.meta.url).href;
const SOURCE_EMPTY_URL = new URL("./assets/source-empty.png", import.meta.url).href;
const AGENT_IDLE_URL = new URL("./assets/agent-idle.png", import.meta.url).href;

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

function WorkspaceSummary() {
  return (
    <div className="workspace-summary" aria-label="当前工作区">
      <span className="workspace-summary-title">本地工作区</span>
      <span className="workspace-summary-detail">个人空间</span>
    </div>
  );
}

function Sidebar({
  areas,
  activeArea,
  onAreaChange,
}: {
  areas: AreaNavigationItem[];
  activeArea: AreaId;
  onAreaChange: (area: AreaId) => void;
}) {
  return (
    <aside className="sidebar" aria-label="Workspace">
      <div className="sidebar-heading">Workspace</div>
      <WorkspaceSummary />
      <nav className="area-nav" aria-label="Workbench areas">
        <p className="nav-heading">领域</p>
        <div className="area-nav-list">
          {areas.map((area) => {
            const isActive = activeArea === area.id;
            return (
              <button
                key={area.id}
                type="button"
                className={`area-link${isActive ? " area-link-active" : ""}`}
                aria-current={isActive ? "page" : undefined}
                onClick={() => onAreaChange(area.id)}
              >
                <span className="area-link-label">{area.label}</span>
                <span className="area-link-description">{area.description}</span>
              </button>
            );
          })}
        </div>
      </nav>
    </aside>
  );
}

function LoadingState() {
  return (
    <section className="state-panel loading-state" aria-label="正在读取工作区" aria-live="polite">
      <div className="skeleton skeleton-heading" />
      <div className="skeleton skeleton-copy" />
      <div className="skeleton skeleton-copy skeleton-copy-short" />
    </section>
  );
}

function ErrorState() {
  return (
    <section className="state-panel error-state" role="alert">
      <p className="state-kicker">暂时无法读取</p>
      <h2>本地工作区暂时不可用</h2>
      <p>请稍后再试。</p>
    </section>
  );
}

function EmptyState({ area, content }: { area: AreaId; content: AreaContent }) {
  return (
    <section className="empty-state" aria-labelledby="empty-state-title">
      {area === "sources" ? <img className="empty-state-image" src={SOURCE_EMPTY_URL} alt="" aria-hidden="true" /> : null}
      <div className="empty-state-copy">
        <h2 id="empty-state-title">{content.emptyTitle}</h2>
        <p>{content.emptyBody}</p>
      </div>
    </section>
  );
}

function AgentPanel() {
  return (
    <aside className="agent-panel" aria-label="Agent">
      <div className="agent-panel-heading">
        <h2>{AGENT_COPY.title}</h2>
      </div>
      <div className="agent-empty-state">
        <img className="agent-idle-image" src={AGENT_IDLE_URL} alt="" aria-hidden="true" />
        <p>{AGENT_COPY.body}</p>
        <span className="agent-availability">{AGENT_COPY.availability}</span>
      </div>
      <div className="agent-composer">
        <input type="text" disabled placeholder={AGENT_COPY.placeholder} aria-label={AGENT_COPY.placeholder} />
      </div>
    </aside>
  );
}

function App() {
  const [activeArea, setActiveArea] = useState<AreaId>("sources");
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

  const content = AREA_CONTENT[activeArea];
  const areas = snapshot ? navigationForAreas(snapshot.areas) : AREA_NAV;

  return (
    <div className="app-shell" data-api-state={apiState} data-connection-state={connectionState}>
      <header className="topbar">
        <div className="topbar-brand">
          <Brand />
        </div>
        <div className="topbar-context">Workspace</div>
        <div className="topbar-meta">
          <span className="local-cue">本地</span>
        </div>
      </header>

      <div className="workspace-layout">
        <Sidebar areas={areas} activeArea={activeArea} onAreaChange={setActiveArea} />

        <main className="main-content" aria-labelledby="area-title">
          <div className="content-intro">
            <p className="breadcrumb">{content.label}</p>
            <h1 id="area-title">{content.title}</h1>
            <p className="intro-copy">{content.description}</p>
          </div>

          {apiState === "loading" ? <LoadingState /> : null}
          {apiState === "error" ? <ErrorState /> : null}
          {apiState === "ready" ? <EmptyState area={activeArea} content={content} /> : null}
        </main>

        <AgentPanel />
      </div>
    </div>
  );
}

export default App;
