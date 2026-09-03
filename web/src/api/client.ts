import createClient from "openapi-fetch";
import type { components, paths } from "../generated/api";

const client = createClient<paths>({ baseUrl: "" });

export type Workspace = components["schemas"]["Workspace"];
export type WorkspaceError = components["schemas"]["WorkspaceError"];

export type WorkbenchSnapshot = NonNullable<
  paths["/api/workbench"]["get"]["responses"][200]["content"]["application/json"]
>;

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(status: number, error: WorkspaceError | null) {
    super(error?.message ?? `Local API request failed (${status}).`);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = error?.code ?? null;
    this.requestId = error?.request_id ?? null;
  }
}

function asWorkspaceError(value: unknown): WorkspaceError | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const candidate = value as Partial<Record<keyof WorkspaceError, unknown>>;
  if (
    typeof candidate.code === "string" &&
    typeof candidate.message === "string" &&
    typeof candidate.request_id === "string"
  ) {
    return {
      code: candidate.code,
      message: candidate.message,
      request_id: candidate.request_id,
    };
  }
  return null;
}

function throwForResult(result: { response: Response; error?: unknown }): never {
  throw new ApiRequestError(result.response.status, asWorkspaceError(result.error));
}

export async function fetchWorkbench(): Promise<WorkbenchSnapshot> {
  const result = await client.GET("/api/workbench");
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function fetchWorkspaces(): Promise<Workspace[]> {
  const result = await client.GET("/api/workspaces");
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function createWorkspace(displayName: string): Promise<Workspace> {
  const result = await client.POST("/api/workspaces", {
    body: { display_name: displayName },
  });
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function fetchWorkspace(workspaceId: string): Promise<Workspace> {
  const result = await client.GET("/api/workspaces/{workspace_id}", {
    params: { path: { workspace_id: workspaceId } },
  });
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}
