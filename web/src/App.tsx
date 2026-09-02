import { useEffect, useMemo, useState } from "react";

import { fetchWorkbench, type WorkbenchSnapshot } from "./api/client";
import "./styles.css";

export type AreaId = "sources" | "mission" | "clarifications" | "contract";

export const AREA_CONTENT: Record<
  AreaId,
  { eyebrow: string; title: string; description: string; emptyTitle: string; emptyBody: string }
> = {
  sources: {
    eyebrow: "Sources / empty",
    title: "先把证据面摆出来",
    description: "把明确授权的资料放进同一个可追溯的 Workspace，再谈业务定义。",
    emptyTitle: "还没有获准来源",
    emptyBody: "N1 只提供入口位置。文件准入、解析、版本和 profiling 将在后续来源处理 checkpoint 实现。",
  },
  mission: {
    eyebrow: "Mission / not implemented",
    title: "让每一步都能回到证据",
    description: "任务、阶段、工具收据和终态会在同一条公开事件线上留下位置。",
    emptyTitle: "Mission loop 尚未启用",
    emptyBody: "N1 不调用模型、不执行领域工具，也不会把静态页面伪装成一次成功运行。",
  },
  clarifications: {
    eyebrow: "Clarifications / not implemented",
    title: "把未知问成能回答的问题",
    description: "澄清应说明为什么要问、谁适合回答，以及答案会改变哪一条定义。",
    emptyTitle: "暂无澄清请求",
    emptyBody: "澄清表单、回答者路由与冲突状态将在 Contract 闭环前实现。",
  },
  contract: {
    eyebrow: "Contract / not implemented",
    title: "让批准的定义可以复用",
    description: "Contract 不是聊天摘要，而是带来源、版本、责任与验收边界的业务定义。",
    emptyTitle: "还没有可批准 Contract",
    emptyBody: "N1 只展示目标边界。字段映射、规则、例外、版本 Diff 和批准 Context 尚未实现。",
  },
};

const STAGES = [
  { number: "01", label: "Material", detail: "授权资料" },
  { number: "02", label: "Evidence", detail: "确定性证据" },
  { number: "03", label: "Decision", detail: "人的裁决" },
  { number: "04", label: "Contract", detail: "可复用定义" },
] as const;

const DEFAULT_AREAS: Array<{ id: AreaId; label: string; description: string; status: string }> = [
  { id: "sources", label: "Sources", description: "授权资料、结构与证据的入口。", status: "not_implemented" },
  { id: "mission", label: "Mission", description: "任务阶段、工具收据与公开事件。", status: "not_implemented" },
  { id: "clarifications", label: "Clarifications", description: "把未知变成可回答、可路由的问题。", status: "not_implemented" },
  { id: "contract", label: "Contract", description: "有来源、版本与审批边界的业务定义。", status: "not_implemented" },
];

type ConnectionState = "connecting" | "connected" | "reconnecting";

function StatusPill({ children, tone = "muted" }: { children: string; tone?: "muted" | "accent" }) {
  return <span className={`status-pill status-pill-${tone}`}>{children}</span>;
}

function LoadingPanel() {
  return (
    <div className="loading-panel" aria-live="polite">
      <div className="skeleton skeleton-kicker" />
      <div className="skeleton skeleton-title" />
      <div className="skeleton skeleton-copy" />
      <div className="skeleton skeleton-copy skeleton-copy-short" />
    </div>
  );
}

function EmptyPanel({ area }: { area: AreaId }) {
  const content = AREA_CONTENT[area];
  return (
    <section className="empty-panel" aria-labelledby="empty-panel-title">
      <div className="empty-mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div>
        <p className="panel-label">CURRENT STATE</p>
        <h3 id="empty-panel-title">{content.emptyTitle}</h3>
        <p>{content.emptyBody}</p>
      </div>
    </section>
  );
}

function StageRail() {
  return (
    <ol className="stage-rail" aria-label="ContextOx definition loop">
      {STAGES.map((stage, index) => (
        <li key={stage.number} className="stage-item">
          <span className="stage-index">{stage.number}</span>
          <span className="stage-copy">
            <strong>{stage.label}</strong>
            <small>{stage.detail}</small>
          </span>
          {index < STAGES.length - 1 ? <span className="stage-line" aria-hidden="true" /> : null}
        </li>
      ))}
    </ol>
  );
}

function ReadinessPanel({ snapshot }: { snapshot: WorkbenchSnapshot | null }) {
  const checks = snapshot?.readiness.checks ?? [];
  return (
    <section className="readiness-panel" aria-labelledby="readiness-title">
      <div className="section-heading">
        <div>
          <p className="panel-label">SYSTEM READINESS</p>
          <h2 id="readiness-title">N1 shell</h2>
        </div>
        <StatusPill tone="accent">PARTIAL</StatusPill>
      </div>
      <p className="readiness-copy">{snapshot?.readiness.label ?? "Connecting to the local API…"}</p>
      <ul className="check-list">
        {checks.length > 0 ? (
          checks.map((check) => (
            <li key={check.key}>
              <span className={`check-indicator check-${check.status}`} aria-hidden="true" />
              <span>{check.key.replaceAll("_", " ")}</span>
              <span className="check-status">{check.status.replaceAll("_", " ")}</span>
            </li>
          ))
        ) : (
          <li>
            <span className="check-indicator check-not-run" aria-hidden="true" />
            <span>api</span>
            <span className="check-status">connecting</span>
          </li>
        )}
      </ul>
    </section>
  );
}

function App() {
  const [activeArea, setActiveArea] = useState<AreaId>("sources");
  const [snapshot, setSnapshot] = useState<WorkbenchSnapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  useEffect(() => {
    let cancelled = false;
    void fetchWorkbench()
      .then((data) => {
        if (!cancelled) {
          setSnapshot(data);
          setLoadError(null);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : "The local API could not be read.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/events");
    const handleConnected = () => setConnection("connected");
    source.addEventListener("connected", handleConnected);
    source.onerror = () => setConnection("reconnecting");
    return () => {
      source.removeEventListener("connected", handleConnected);
      source.close();
    };
  }, []);

  const active = useMemo(() => AREA_CONTENT[activeArea], [activeArea]);
  const areas = snapshot?.areas ?? DEFAULT_AREAS;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="wordmark" href="/" aria-label="ContextOx Workbench home">
          <span className="wordmark-mark" aria-hidden="true">CX</span>
          <span>ContextOx</span>
          <span className="wordmark-product">WORKBENCH</span>
        </a>
        <div className="topbar-meta">
          <span className="connection-state">
            <span className={`connection-dot connection-${connection}`} aria-hidden="true" />
            SSE {connection}
          </span>
          <span className="topbar-divider" aria-hidden="true" />
          <span className="local-badge">127.0.0.1 / LOCAL ONLY</span>
        </div>
      </header>

      <div className="workspace-layout">
        <aside className="sidebar" aria-label="Workbench navigation">
          <div className="workspace-switcher">
            <span className="panel-label">WORKSPACE</span>
            <button type="button" className="workspace-button" aria-label="Current workspace: local shell">
              <span className="workspace-avatar">L</span>
              <span className="workspace-name">local shell</span>
              <span className="workspace-chevron" aria-hidden="true">⌄</span>
            </button>
            <p className="sidebar-note">Single local owner · no customer data</p>
          </div>

          <nav className="area-nav" aria-label="Workbench areas">
            <p className="panel-label">AREAS</p>
            {areas.map((area) => (
              <button
                key={area.id}
                type="button"
                className={`area-link ${activeArea === area.id ? "area-link-active" : ""}`}
                aria-current={activeArea === area.id ? "page" : undefined}
                onClick={() => setActiveArea(area.id)}
              >
                <span className="area-link-text">
                  <strong>{area.label}</strong>
                  <small>{area.description}</small>
                </span>
                <span className="area-status">{area.status === "ready" ? "READY" : "N/I"}</span>
              </button>
            ))}
          </nav>

          <div className="sidebar-footer">
            <div className="agent-boundary">
              <span className="boundary-icon" aria-hidden="true">—</span>
              <div>
                <strong>Agent boundary</strong>
                <p>No provider configured in N1</p>
              </div>
            </div>
            <span className="version-label">v0.1.0 · N1 SHELL</span>
          </div>
        </aside>

        <main className="main-content">
          <div className="content-intro">
            <div>
              <p className="eyebrow">{active.eyebrow}</p>
              <h1>{active.title}</h1>
              <p className="intro-copy">{active.description}</p>
            </div>
            <div className="intro-status">
              <span className="intro-status-label">PRODUCT STATUS</span>
              <StatusPill tone="accent">PARTIAL · N1</StatusPill>
            </div>
          </div>

          {loadError ? (
            <section className="error-panel" role="alert">
              <p className="panel-label">LOCAL API ERROR</p>
              <h2>页面没有拿到当前快照</h2>
              <p>{loadError}</p>
              <p className="error-help">请确认服务由 `contextox start` 在 127.0.0.1 上启动。</p>
            </section>
          ) : snapshot ? (
            <EmptyPanel area={activeArea} />
          ) : (
            <LoadingPanel />
          )}

          <div className="lower-grid">
            <section className="loop-panel" aria-labelledby="loop-title">
              <div className="section-heading">
                <div>
                  <p className="panel-label">THE LOOP</p>
                  <h2 id="loop-title">从资料到 Contract</h2>
                </div>
                <span className="section-index">0 / 4 active</span>
              </div>
              <StageRail />
              <p className="panel-footnote">N1 只铺设可读的流程骨架；每个阶段的真实行为会在对应 checkpoint 单独验收。</p>
            </section>

            <section className="boundary-panel" aria-labelledby="boundary-title">
              <p className="panel-label">TRUST BOUNDARY</p>
              <h2 id="boundary-title">先把“不做什么”写清楚</h2>
              <ul className="boundary-list">
                <li><span>01</span><span>不读取任意本地文件</span></li>
                <li><span>02</span><span>不执行 SQL、Shell 或代码</span></li>
                <li><span>03</span><span>不调用真实模型或外部服务</span></li>
              </ul>
            </section>
          </div>
        </main>

        <aside className="right-rail" aria-label="Run and evidence status">
          <ReadinessPanel snapshot={snapshot} />
          <section className="evidence-panel" aria-labelledby="evidence-title">
            <div className="section-heading">
              <div>
                <p className="panel-label">EVIDENCE LANES</p>
                <h2 id="evidence-title">状态分开记</h2>
              </div>
            </div>
            <ul className="evidence-list">
              {(snapshot?.evidence ?? []).map((lane) => (
                <li key={lane.key}>
                  <span>{lane.label}</span>
                  <StatusPill>{lane.status.replaceAll("_", " ")}</StatusPill>
                </li>
              ))}
            </ul>
            {!snapshot ? <p className="rail-muted">等待本地快照…</p> : null}
          </section>
          <section className="next-panel" aria-labelledby="next-title">
            <p className="panel-label">NEXT CHECKPOINT</p>
            <h2 id="next-title">Source admission</h2>
            <p>为授权资料建立 Workspace 隔离、版本与确定性解析边界。</p>
            <span className="next-marker">N2 · NOT STARTED</span>
          </section>
        </aside>
      </div>
    </div>
  );
}

export default App;
