import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  ApiRequestError,
  createWorkspace,
  fetchWorkspaces,
  type Workspace,
} from "./api/client";

export const WORKSPACE_STORAGE_KEY = "contextox.selected_workspace_id";

export type WorkspaceSwitcherProps = {
  selectedWorkspace: Workspace | null;
  onWorkspaceChange: (workspace: Workspace | null) => void;
};

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

export function readSelectedWorkspaceId(): string | null {
  try {
    const value = window.sessionStorage.getItem(WORKSPACE_STORAGE_KEY);
    return value && value.length > 0 ? value : null;
  } catch {
    return null;
  }
}

export function writeSelectedWorkspaceId(workspaceId: string | null): void {
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

export function sortWorkspaces(workspaces: Workspace[]): Workspace[] {
  return [...workspaces].sort(
    (left, right) =>
      left.created_at.localeCompare(right.created_at) ||
      left.workspace_id.localeCompare(right.workspace_id),
  );
}

export function shortWorkspaceId(workspaceId: string): string {
  return workspaceId.slice(0, 8);
}

export function workspaceTime(workspace: Workspace): string {
  const date = new Date(workspace.created_at);
  if (Number.isNaN(date.getTime())) {
    return workspace.created_at;
  }
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  if (error instanceof Error && error.message.length > 0) {
    return error.message;
  }
  return "本地 API 暂时无法读取 Workspace。";
}

export function needsCreateReconciliation(error: unknown): boolean {
  return !(
    error instanceof ApiRequestError &&
    error.code !== null &&
    error.code !== "workspace_create_outcome_unknown"
  );
}

function WorkspaceIdentity({ workspace }: { workspace: Workspace | null }) {
  const initial = workspace?.display_name.trim().charAt(0).toUpperCase() ?? "?";
  return (
    <>
      <span className="workspace-switcher-avatar" aria-hidden="true">
        {initial}
      </span>
      <span className="workspace-switcher-name">
        {workspace?.display_name ?? "选择 Workspace"}
      </span>
      <span className="workspace-switcher-short-id">
        {workspace ? `#${shortWorkspaceId(workspace.workspace_id)}` : "未选择"}
      </span>
      <span className="workspace-switcher-chevron" aria-hidden="true">
        ⌄
      </span>
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
      className={`workspace-switcher-item${selected ? " workspace-switcher-item-active" : ""}`}
      onClick={() => onSelect(workspace.workspace_id)}
    >
      <span className="workspace-switcher-item-main">
        <strong>{workspace.display_name}</strong>
        <code title={workspace.workspace_id}>{workspace.workspace_id}</code>
      </span>
      <time dateTime={workspace.created_at}>{workspaceTime(workspace)}</time>
    </button>
  );
}

export default function WorkspaceSwitcher({
  selectedWorkspace,
  onWorkspaceChange,
}: WorkspaceSwitcherProps) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceListState, setWorkspaceListState] =
    useState<WorkspaceListState>("loading");
  const [workspaceListError, setWorkspaceListError] = useState<string | null>(null);
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
    void fetchWorkspaces()
      .then((data) => {
        if (cancelled) {
          return;
        }
        const ordered = sortWorkspaces(data);
        setWorkspaces(ordered);
        setWorkspaceListState("ready");
        setWorkspaceListError(null);
        const savedId = readSelectedWorkspaceId();
        const savedWorkspace = savedId
          ? ordered.find((workspace) => workspace.workspace_id === savedId) ?? null
          : null;
        if (savedId && savedWorkspace) {
          onWorkspaceChange(savedWorkspace);
          setSelectionIssue(null);
        } else if (savedId) {
          writeSelectedWorkspaceId(null);
          onWorkspaceChange(null);
          setSelectionIssue("invalid");
        } else if (ordered.length === 1) {
          const onlyWorkspace = ordered[0];
          onWorkspaceChange(onlyWorkspace);
          writeSelectedWorkspaceId(onlyWorkspace.workspace_id);
          setSelectionIssue(null);
        } else {
          onWorkspaceChange(null);
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
  }, [onWorkspaceChange]);

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

  const createSubmitBlocked =
    createState.kind === "submitting" ||
    createState.kind === "reconciling" ||
    (createState.kind === "unknown" && !createState.acknowledged);

  async function refreshWorkspaces(): Promise<Workspace[] | null> {
    setWorkspaceListState("loading");
    try {
      const data = sortWorkspaces(await fetchWorkspaces());
      setWorkspaces(data);
      setWorkspaceListState("ready");
      setWorkspaceListError(null);
      if (
        selectedWorkspace &&
        !data.some((workspace) => workspace.workspace_id === selectedWorkspace.workspace_id)
      ) {
        writeSelectedWorkspaceId(null);
        onWorkspaceChange(null);
        setSelectionIssue("invalid");
      }
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
      const latest = sortWorkspaces(await fetchWorkspaces());
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
        sortWorkspaces([
          ...current.filter((workspace) => workspace.workspace_id !== created.workspace_id),
          created,
        ]),
      );
      setWorkspaceListState("ready");
      setWorkspaceListError(null);
      onWorkspaceChange(created);
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
    const workspace = workspaces.find((candidate) => candidate.workspace_id === workspaceId);
    if (!workspace) {
      return;
    }
    onWorkspaceChange(workspace);
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

  const menuTitleId = "workspace-switcher-menu-title";
  const selectedLabel = useMemo(
    () =>
      selectedWorkspace
        ? `当前 Workspace：${selectedWorkspace.display_name}`
        : "选择一个 Workspace",
    [selectedWorkspace],
  );

  return (
    <div className="workspace-switcher-control">
      <button
        ref={workspaceTriggerRef}
        id="workspace-switcher-trigger"
        type="button"
        className="workspace-switcher-trigger"
        aria-label={selectedLabel}
        aria-haspopup="dialog"
        aria-expanded={workspaceMenuOpen}
        aria-controls="workspace-switcher-menu"
        onClick={toggleWorkspaceMenu}
      >
        <WorkspaceIdentity workspace={selectedWorkspace} />
      </button>
      {workspaceMenuOpen ? (
        <section
          id="workspace-switcher-menu"
          className="workspace-switcher-menu"
          aria-labelledby={menuTitleId}
          role="dialog"
        >
          <div className="workspace-switcher-heading">
            <span id={menuTitleId} className="workspace-switcher-label">
              WORKSPACES
            </span>
            <span className="workspace-switcher-count">{workspaces.length}</span>
          </div>
          {workspaceListState === "loading" ? (
            <p className="workspace-switcher-state" role="status">
              正在读取 Workspace…
            </p>
          ) : workspaceListState === "error" ? (
            <div className="workspace-switcher-error" role="alert">
              <strong>Workspace 列表不可用</strong>
              <p>{workspaceListError ?? "本地 API 暂时无法读取 Workspace。"}</p>
              <button
                type="button"
                className="workspace-switcher-secondary"
                onClick={() => void refreshWorkspaces()}
              >
                重新读取
              </button>
            </div>
          ) : workspaces.length === 0 ? (
            <div className="workspace-switcher-state">
              <strong>还没有 Workspace</strong>
              <p>先创建一个本地 Workspace，选择会只保存在当前浏览器标签页。</p>
            </div>
          ) : (
            <div className="workspace-switcher-list" role="group" aria-label="Workspace 列表">
              {workspaces.map((workspace) => (
                <WorkspaceMenuItem
                  key={workspace.workspace_id}
                  workspace={workspace}
                  selected={workspace.workspace_id === selectedWorkspace?.workspace_id}
                  onSelect={selectWorkspace}
                />
              ))}
            </div>
          )}
          {selectionIssue === "invalid" ? (
            <div className="workspace-switcher-error" role="alert">
              <strong>当前标签页的 Workspace 选择已失效</strong>
              <p>已清除无效 ID，请从列表中明确选择一个 Workspace。</p>
            </div>
          ) : null}
          <button type="button" className="workspace-switcher-create" onClick={openCreate}>
            <span aria-hidden="true">＋</span>
            新建 Workspace
          </button>
          {createOpen ? (
            <form className="workspace-switcher-form" onSubmit={(event) => void handleCreate(event)}>
              <label htmlFor="workspace-switcher-name">Workspace 名称</label>
              <input
                ref={createNameInputRef}
                id="workspace-switcher-name"
                name="display_name"
                type="text"
                value={createName}
                maxLength={80}
                autoComplete="off"
                onChange={(event) => setCreateName(event.target.value)}
                disabled={createState.kind === "submitting" || createState.kind === "reconciling"}
              />
              <p className="workspace-switcher-help">首尾空白会裁剪；控制字符会被拒绝。</p>
              {createState.kind === "error" ? (
                <p className="workspace-switcher-form-error" role="alert">
                  {createState.message}
                </p>
              ) : null}
              {createState.kind === "reconciling" ? (
                <div className="workspace-switcher-unknown" role="status">
                  <strong>正在核对本次创建结果…</strong>
                  <p>服务没有给出可确认的响应，正在重新读取 Workspace 列表。</p>
                </div>
              ) : null}
              {createState.kind === "unknown" ? (
                <div className="workspace-switcher-unknown" role="alert">
                  <strong>创建结果未知，当前操作已阻塞</strong>
                  <p>
                    不要依据名称或单一候选自动判断是否创建成功。请核对下面相对请求前快照新增的 ID 与创建时间。
                  </p>
                  {createState.candidates.length > 0 ? (
                    <ul className="workspace-switcher-candidates">
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
                    <p className="workspace-switcher-form-error">
                      列表核对也未完成：{createState.refreshError}
                    </p>
                  ) : null}
                  <div className="workspace-switcher-actions">
                    <button
                      type="button"
                      className="workspace-switcher-secondary"
                      onClick={() =>
                        void reconcileCreate(createState.snapshotIds, createState.proposedName)
                      }
                    >
                      再次核对列表
                    </button>
                    {!createState.acknowledged ? (
                      <button
                        type="button"
                        className="workspace-switcher-ack"
                        onClick={() => setCreateState({ ...createState, acknowledged: true })}
                      >
                        我已手动核对新增列表
                      </button>
                    ) : (
                      <span className="workspace-switcher-acknowledged">
                        已核对；下一次创建需由你重新提交
                      </span>
                    )}
                  </div>
                </div>
              ) : null}
              <button
                type="submit"
                className="workspace-switcher-submit"
                disabled={createSubmitBlocked}
              >
                {createState.kind === "submitting" ? "创建中…" : "创建 Workspace"}
              </button>
            </form>
          ) : null}
        </section>
      ) : null}
      {createState.kind === "success" ? (
        <p className="workspace-switcher-success" role="status">
          已创建并选中 #{shortWorkspaceId(createState.workspace.workspace_id)}
        </p>
      ) : null}
    </div>
  );
}
