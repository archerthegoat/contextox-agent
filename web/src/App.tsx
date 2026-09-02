import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  ApiRequestError,
  createWorkspace,
  fetchWorkbench,
  fetchWorkspaces,
  type WorkbenchSnapshot,
  type Workspace,
} from "./api/client";
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
    emptyBody: "N2a 只提供 Workspace 入口位置。文件准入、解析、版本和 profiling 将在后续来源处理 checkpoint 实现。",
  },
  mission: {
    eyebrow: "Mission / not implemented",
    title: "让每一步都能回到证据",
    description: "任务、阶段、工具收据和终态会在同一条公开事件线上留下位置。",
    emptyTitle: "Mission loop 尚未启用",
    emptyBody: "N2a 不调用模型、不执行领域工具，也不会把静态页面伪装成一次成功运行。",
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
    emptyBody: "N2a 只展示目标边界。字段映射、规则、例外、版本 Diff 和批准 Context 尚未实现。",
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

export const WORKSPACE_STORAGE_KEY = "contextox.selected_workspace_id";

type WorkspaceListState = "loading" | "ready" | "error";

type CreateState =
  | { kind: "idle" }
  | { kind: "submitting"; proposedName: string }
  | { kind: "success"; workspace: Workspace }
  | { kind: "error"; message: string }
  | { kind: "reconciling"; proposedName: string }
  | {
      kind: "unknown";
      proposedName: string;
      snapshotIds: string[];
      candidates: Workspace[];
      acknowledged: boolean;
      refreshError?: string;
    };

function readSelectedWorkspaceId(): string | null {
  try {
    const value = window.sessionStorage.getItem(WORKSPACE_STORAGE_KEY);
    return value && value.length > 0 ? value : null;
  } catch {
    return null;
  }
}

function writeSelectedWorkspaceId(workspaceId: string | null): void {
  try {
    if (workspaceId) {
      window.sessionStorage.setItem(WORKSPACE_STORAGE_KEY, workspaceId);
    } else {
      window.sessionStorage.removeItem(WORKSPACE_STORAGE_KEY);
    }
  } catch {
    // sessionStorage can be unavailable in a privacy-restricted browser. The
    // in-memory selection still works for this tab.
  }
}

function shortWorkspaceId(workspaceId: string): string {
  return workspaceId.slice(0, 8);
}

function workspaceTime(workspace: Workspace): string {
  const date = new Date(workspace.created_at);
  if (Number.isNaN(date.getTime())) {
    return workspace.created_at;
  }
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  if (error instanceof Error && error.message.length > 0) {
    return error.message;
  }
  return "本地 API 暂时无法读取 Workspace。";
}

function needsCreateReconciliation(error: unknown): boolean {
  return !(error instanceof ApiRequestError) || error.code === null || error.code === "workspace_create_outcome_unknown";
}

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
  const readinessStatus = snapshot?.readiness.status ?? "not_run";
  return (
    <section className="readiness-panel" aria-labelledby="readiness-title">
      <div className="section-heading">
        <div>
          <p className="panel-label">SYSTEM READINESS</p>
          <h2 id="readiness-title">N2a foundation</h2>
        </div>
        <StatusPill tone={readinessStatus === "partial" ? "accent" : "muted"}>
          {readinessStatus.replaceAll("_", " ")}
        </StatusPill>
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

function WorkspaceIdentity({ workspace }: { workspace: Workspace | null }) {
  const initial = workspace?.display_name.trim().charAt(0).toUpperCase() ?? "?";
  return (
    <>
      <span className="workspace-avatar" aria-hidden="true">{initial}</span>
      <span className="workspace-name">{workspace?.display_name ?? "选择 Workspace"}</span>
      <span className="workspace-short-id">
        {workspace ? `#${shortWorkspaceId(workspace.workspace_id)}` : "未选择"}
      </span>
      <span className="workspace-chevron" aria-hidden="true">⌄</span>
    </>
  );
}

function WorkspaceMenuItem({
  workspace,
  selected,
  onSelect,
}: {
  workspace: Workspace;
  selected: boolean;
  onSelect: (workspaceId: string) => void;
}) {
  return (
    <button
      type="button"
      aria-current={selected ? "true" : undefined}
      className={`workspace-menu-item ${selected ? "workspace-menu-item-active" : ""}`}
      onClick={() => onSelect(workspace.workspace_id)}
    >
      <span className="workspace-menu-item-main">
        <strong>{workspace.display_name}</strong>
        <code title={workspace.workspace_id}>{workspace.workspace_id}</code>
      </span>
      <time dateTime={workspace.created_at}>{workspaceTime(workspace)}</time>
    </button>
  );
}

function App() {
  const [activeArea, setActiveArea] = useState<AreaId>("sources");
  const [snapshot, setSnapshot] = useState<WorkbenchSnapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceListState, setWorkspaceListState] = useState<WorkspaceListState>("loading");
  const [workspaceListError, setWorkspaceListError] = useState<string | null>(null);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [selectionIssue, setSelectionIssue] = useState<"invalid" | null>(null);
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createState, setCreateState] = useState<CreateState>({ kind: "idle" });
  const workspaceTriggerRef = useRef<HTMLButtonElement>(null);
  const createNameInputRef = useRef<HTMLInputElement>(null);
  const restoreTriggerFocusRef = useRef(false);

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
    let cancelled = false;
    void fetchWorkspaces()
      .then((data) => {
        if (cancelled) {
          return;
        }
        setWorkspaces(data);
        setWorkspaceListState("ready");
        setWorkspaceListError(null);
        const savedId = readSelectedWorkspaceId();
        if (savedId && data.some((workspace) => workspace.workspace_id === savedId)) {
          setSelectedWorkspaceId(savedId);
          setSelectionIssue(null);
        } else if (savedId) {
          writeSelectedWorkspaceId(null);
          setSelectedWorkspaceId(null);
          setSelectionIssue("invalid");
        } else if (data.length === 1) {
          const onlyWorkspace = data[0];
          setSelectedWorkspaceId(onlyWorkspace.workspace_id);
          writeSelectedWorkspaceId(onlyWorkspace.workspace_id);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setWorkspaceListState("error");
          setWorkspaceListError(errorMessage(error));
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

  useEffect(() => {
    if (!workspaceMenuOpen) {
      return;
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        restoreTriggerFocusRef.current = true;
        setWorkspaceMenuOpen(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [workspaceMenuOpen]);

  useEffect(() => {
    if (createOpen && workspaceMenuOpen) {
      createNameInputRef.current?.focus();
    }
  }, [createOpen, workspaceMenuOpen]);

  useEffect(() => {
    if (!workspaceMenuOpen && restoreTriggerFocusRef.current) {
      restoreTriggerFocusRef.current = false;
      workspaceTriggerRef.current?.focus();
    }
  }, [workspaceMenuOpen]);

  const selectedWorkspace = useMemo(
    () => workspaces.find((workspace) => workspace.workspace_id === selectedWorkspaceId) ?? null,
    [selectedWorkspaceId, workspaces],
  );

  async function refreshWorkspaces(): Promise<Workspace[] | null> {
    setWorkspaceListState("loading");
    try {
      const data = await fetchWorkspaces();
      setWorkspaces(data);
      setWorkspaceListState("ready");
      setWorkspaceListError(null);
      return data;
    } catch (error: unknown) {
      setWorkspaceListState("error");
      setWorkspaceListError(errorMessage(error));
      return null;
    }
  }

  async function reconcileCreate(snapshotIds: string[], proposedName: string): Promise<void> {
    setCreateState({ kind: "reconciling", proposedName });
    try {
      const latest = await fetchWorkspaces();
      const snapshot = new Set(snapshotIds);
      const candidates = latest.filter((workspace) => !snapshot.has(workspace.workspace_id));
      setWorkspaces(latest);
      setWorkspaceListState("ready");
      setWorkspaceListError(null);
      setCreateState({
        kind: "unknown",
        proposedName,
        snapshotIds,
        candidates,
        acknowledged: false,
      });
    } catch (error: unknown) {
      setCreateState({
        kind: "unknown",
        proposedName,
        snapshotIds,
        candidates: [],
        acknowledged: false,
        refreshError: errorMessage(error),
      });
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (createState.kind === "submitting" || createState.kind === "reconciling") {
      return;
    }
    if (createState.kind === "unknown" && !createState.acknowledged) {
      return;
    }
    const proposedName = createName;
    const snapshotIds = workspaces.map((workspace) => workspace.workspace_id);
    setCreateState({ kind: "submitting", proposedName });
    try {
      const created = await createWorkspace(proposedName);
      setWorkspaces((current) =>
        [...current.filter((workspace) => workspace.workspace_id !== created.workspace_id), created].sort(
          (left, right) =>
            left.created_at.localeCompare(right.created_at) ||
            left.workspace_id.localeCompare(right.workspace_id),
        ),
      );
      setWorkspaceListState("ready");
      setWorkspaceListError(null);
      setSelectedWorkspaceId(created.workspace_id);
      writeSelectedWorkspaceId(created.workspace_id);
      setSelectionIssue(null);
      setCreateName("");
      setCreateOpen(false);
      restoreTriggerFocusRef.current = true;
      setWorkspaceMenuOpen(false);
      setCreateState({ kind: "success", workspace: created });
    } catch (error: unknown) {
      if (needsCreateReconciliation(error)) {
        setCreateOpen(true);
        setWorkspaceMenuOpen(true);
        await reconcileCreate(snapshotIds, proposedName);
        return;
      }
      setCreateOpen(true);
      setWorkspaceMenuOpen(true);
      setCreateState({ kind: "error", message: errorMessage(error) });
    }
  }

  function selectWorkspace(workspaceId: string): void {
    setSelectedWorkspaceId(workspaceId);
    writeSelectedWorkspaceId(workspaceId);
    setSelectionIssue(null);
    restoreTriggerFocusRef.current = true;
    setWorkspaceMenuOpen(false);
  }

  function toggleWorkspaceMenu(): void {
    if (workspaceMenuOpen) {
      restoreTriggerFocusRef.current = true;
      setWorkspaceMenuOpen(false);
      return;
    }
    setWorkspaceMenuOpen(true);
  }

  function openCreate(): void {
    setCreateOpen(true);
    setWorkspaceMenuOpen(true);
    if (createState.kind === "success" || createState.kind === "error") {
      setCreateState({ kind: "idle" });
    }
  }

  const active = useMemo(() => AREA_CONTENT[activeArea], [activeArea]);
  const areas = snapshot?.areas ?? DEFAULT_AREAS;
  const createSubmitBlocked =
    createState.kind === "submitting" ||
    createState.kind === "reconciling" ||
    (createState.kind === "unknown" && !createState.acknowledged);

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
            <button
              ref={workspaceTriggerRef}
              id="workspace-switcher-button"
              type="button"
              className="workspace-button"
              aria-label={selectedWorkspace ? `Current workspace: ${selectedWorkspace.display_name}` : "Choose a workspace"}
              aria-expanded={workspaceMenuOpen}
              aria-controls="workspace-switcher-panel"
              onClick={toggleWorkspaceMenu}
            >
              <WorkspaceIdentity workspace={selectedWorkspace} />
            </button>
            <p className="sidebar-note">Per-tab selection · local owner · no customer data</p>
            {workspaceMenuOpen ? (
              <section
                id="workspace-switcher-panel"
                className="workspace-menu"
                aria-labelledby="workspace-switcher-panel-title"
              >
                <div className="workspace-menu-heading">
                  <span id="workspace-switcher-panel-title" className="panel-label">
                    AVAILABLE WORKSPACES
                  </span>
                  <span className="workspace-menu-count">{workspaces.length}</span>
                </div>
                {workspaceListState === "loading" ? (
                  <p className="workspace-inline-state" role="status">正在读取 Workspace…</p>
                ) : workspaceListState === "error" ? (
                  <div className="workspace-inline-error" role="alert">
                    <strong>Workspace 列表不可用</strong>
                    <p>{workspaceListError ?? "本地 API 暂时无法读取 Workspace。"}</p>
                    <button
                      type="button"
                      className="workspace-secondary-button"
                      onClick={() => void refreshWorkspaces()}
                    >
                      重新读取
                    </button>
                  </div>
                ) : workspaces.length === 0 ? (
                  <div className="workspace-empty-state">
                    <strong>还没有 Workspace</strong>
                    <p>先创建一个本地 Workspace，选择会只保存在当前浏览器标签页。</p>
                  </div>
                ) : (
                  <div className="workspace-menu-list" role="group" aria-label="Workspace list">
                    {workspaces.map((workspace) => (
                      <WorkspaceMenuItem
                        key={workspace.workspace_id}
                        workspace={workspace}
                        selected={workspace.workspace_id === selectedWorkspaceId}
                        onSelect={selectWorkspace}
                      />
                    ))}
                  </div>
                )}
                {selectionIssue === "invalid" ? (
                  <div className="workspace-inline-error" role="alert">
                    <strong>当前标签页的 Workspace 选择已失效</strong>
                    <p>已清除无效 ID，请从列表中明确选择一个 Workspace。</p>
                  </div>
                ) : null}
                <button type="button" className="workspace-create-link" onClick={openCreate}>
                  <span aria-hidden="true">＋</span>
                  新建 Workspace
                </button>
                {createOpen ? (
                  <form className="workspace-create-form" onSubmit={(event) => void handleCreate(event)}>
                    <label htmlFor="workspace-name">Workspace 名称</label>
                    <input
                      id="workspace-name"
                      name="display_name"
                      type="text"
                      value={createName}
                      maxLength={80}
                      autoComplete="off"
                      onChange={(event) => setCreateName(event.target.value)}
                      disabled={createState.kind === "submitting" || createState.kind === "reconciling"}
                    />
                    <p className="workspace-form-help">首尾空白会裁剪；控制字符会被拒绝。</p>
                    {createState.kind === "error" ? (
                      <p className="workspace-form-error" role="alert">{createState.message}</p>
                    ) : null}
                    {createState.kind === "reconciling" ? (
                      <div className="workspace-unknown-state" role="status">
                        <strong>正在核对本次创建结果…</strong>
                        <p>服务没有给出可确认的响应，正在重新读取 Workspace 列表。</p>
                      </div>
                    ) : null}
                    {createState.kind === "unknown" ? (
                      <div className="workspace-unknown-state" role="alert">
                        <strong>创建结果未知，当前操作已阻塞</strong>
                        <p>不要依据名称或单一候选自动判断是否创建成功。请核对下面相对请求前快照新增的 ID 与创建时间。</p>
                        {createState.candidates.length > 0 ? (
                          <ul className="workspace-candidate-list">
                            {createState.candidates.map((workspace) => (
                              <li key={workspace.workspace_id}>
                                <code>{workspace.workspace_id}</code>
                                <time dateTime={workspace.created_at}>{workspace.created_at}</time>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p>本次核对没有发现新增 Workspace；这仍不能证明创建失败。</p>
                        )}
                        {createState.refreshError ? (
                          <p className="workspace-form-error">列表核对也未完成：{createState.refreshError}</p>
                        ) : null}
                        <div className="workspace-unknown-actions">
                          <button
                            type="button"
                            className="workspace-secondary-button"
                            onClick={() => void reconcileCreate(createState.snapshotIds, createState.proposedName)}
                          >
                            再次核对列表
                          </button>
                          {!createState.acknowledged ? (
                            <button
                              type="button"
                              className="workspace-ack-button"
                              onClick={() => setCreateState({ ...createState, acknowledged: true })}
                            >
                              我已手动核对新增列表
                            </button>
                          ) : (
                            <span className="workspace-acknowledged">已核对；下一次创建需由你重新提交</span>
                          )}
                        </div>
                      </div>
                    ) : null}
                    <button
                      type="submit"
                      className="workspace-submit-button"
                      disabled={createSubmitBlocked}
                    >
                      {createState.kind === "submitting" ? "创建中…" : "创建 Workspace"}
                    </button>
                  </form>
                ) : null}
              </section>
            ) : null}
            {createState.kind === "success" ? (
              <p className="workspace-success" role="status">
                已创建并选中 #{shortWorkspaceId(createState.workspace.workspace_id)}
              </p>
            ) : null}
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
                <p>No provider configured in N2a</p>
              </div>
            </div>
            <span className="version-label">v0.1.0 · N2a FOUNDATION</span>
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
              <StatusPill tone="accent">PARTIAL · N2a</StatusPill>
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
              <p className="panel-footnote">N2a 只铺设 Workspace identity 的可读边界；每个后续阶段会单独验收。</p>
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
            <span className="next-marker">N2b · NOT STARTED</span>
          </section>
        </aside>
      </div>
    </div>
  );
}

export default App;
