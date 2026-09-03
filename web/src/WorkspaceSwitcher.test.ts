import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ApiRequestError, type Workspace } from "./api/client";
import WorkspaceSwitcher, {
  WORKSPACE_STORAGE_KEY,
  needsCreateReconciliation,
  readSelectedWorkspaceId,
  sortWorkspaces,
  shortWorkspaceId,
  writeSelectedWorkspaceId,
  workspaceTime,
} from "./WorkspaceSwitcher";

const firstWorkspace: Workspace = {
  workspace_id: "11111111-1111-4111-8111-111111111111",
  display_name: "客户定义",
  created_at: "2026-09-03T07:00:00Z",
};

const secondWorkspace: Workspace = {
  workspace_id: "22222222-2222-4222-8222-222222222222",
  display_name: "客户定义",
  created_at: "2026-09-03T07:00:00Z",
};

describe("WorkspaceSwitcher", () => {
  it("keeps selection in the current browser tab", () => {
    const values = new Map<string, string>();
    const sessionStorage = {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => {
        values.set(key, value);
      }),
      removeItem: vi.fn((key: string) => {
        values.delete(key);
      }),
    };
    vi.stubGlobal("window", { sessionStorage });

    expect(WORKSPACE_STORAGE_KEY).toContain("selected_workspace_id");
    expect(WORKSPACE_STORAGE_KEY).not.toContain("localStorage");
    expect(readSelectedWorkspaceId()).toBeNull();
    writeSelectedWorkspaceId(firstWorkspace.workspace_id);
    expect(sessionStorage.setItem).toHaveBeenCalledWith(
      WORKSPACE_STORAGE_KEY,
      firstWorkspace.workspace_id,
    );
    expect(readSelectedWorkspaceId()).toBe(firstWorkspace.workspace_id);
    writeSelectedWorkspaceId(null);
    expect(sessionStorage.removeItem).toHaveBeenCalledWith(WORKSPACE_STORAGE_KEY);
    expect(readSelectedWorkspaceId()).toBeNull();

    const throwingSessionStorage = {
      getItem: vi.fn(() => {
        throw new Error("storage unavailable");
      }),
      setItem: vi.fn(() => {
        throw new Error("storage unavailable");
      }),
      removeItem: vi.fn(() => {
        throw new Error("storage unavailable");
      }),
    };
    vi.stubGlobal("window", { sessionStorage: throwingSessionStorage });
    expect(readSelectedWorkspaceId()).toBeNull();
    expect(() => writeSelectedWorkspaceId(firstWorkspace.workspace_id)).not.toThrow();
    expect(() => writeSelectedWorkspaceId(null)).not.toThrow();
    vi.unstubAllGlobals();
  });

  it("sorts equal timestamps by server-generated workspace ID", () => {
    expect(sortWorkspaces([secondWorkspace, firstWorkspace])).toEqual([
      firstWorkspace,
      secondWorkspace,
    ]);
    expect(shortWorkspaceId(firstWorkspace.workspace_id)).toBe("11111111");
    expect(workspaceTime(firstWorkspace)).not.toBe("");
  });

  it("reconciles unknown transport outcomes but keeps validation errors local", () => {
    expect(needsCreateReconciliation(new Error("network unavailable"))).toBe(true);
    expect(
      needsCreateReconciliation(
        new ApiRequestError(503, {
          code: "workspace_create_outcome_unknown",
          message: "unknown",
          request_id: "req-1",
        }),
      ),
    ).toBe(true);
    expect(
      needsCreateReconciliation(
        new ApiRequestError(422, {
          code: "invalid_workspace_name",
          message: "invalid",
          request_id: "req-2",
        }),
      ),
    ).toBe(false);
  });

  it("renders the unselected trigger without opening the menu during SSR", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceSwitcher, {
        selectedWorkspace: null,
        onWorkspaceChange: () => undefined,
      }),
    );

    expect(markup).toContain('aria-label="选择一个 Workspace"');
    expect(markup).toContain("选择 Workspace");
    expect(markup).toContain("未选择");
    expect(markup).not.toContain('<section id="workspace-switcher-menu"');
  });
});
