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

type SourceUploadRequest = components["schemas"]["SourceUploadRequest"];
type SourceBatchResult = components["schemas"]["SourceBatchResult"];
type SourceRevision = components["schemas"]["SourceRevision"];
type SourceArtifact = components["schemas"]["SourceArtifact"];
type SourceExcerptRequest = components["schemas"]["SourceExcerptRequest"];
type SourceExcerpt = components["schemas"]["SourceExcerpt"];
type MissionDraftAttemptCreateRequest =
  components["schemas"]["MissionDraftAttemptCreateRequest"];
type MissionDraftAttempt = components["schemas"]["MissionDraftAttempt"];
type MissionDraftConfirmRequest = components["schemas"]["MissionDraftConfirmRequest"];
type Mission = components["schemas"]["Mission"];
type MissionSnapshot = components["schemas"]["MissionSnapshot"];
type RunStartRequest = components["schemas"]["RunStartRequest"];
type RunSnapshot = components["schemas"]["RunSnapshot"];
type CancelRunRequest = components["schemas"]["CancelRunRequest"];

function workspacePath(workspaceId: string) {
  return { path: { workspace_id: workspaceId } };
}

export async function uploadSources(
  workspaceId: string,
  request: SourceUploadRequest,
): Promise<SourceBatchResult> {
  const result = await client.POST("/api/workspaces/{workspace_id}/sources", {
    params: workspacePath(workspaceId),
    body: request,
  });
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function fetchSources(workspaceId: string): Promise<SourceRevision[]> {
  const result = await client.GET("/api/workspaces/{workspace_id}/sources", {
    params: workspacePath(workspaceId),
  });
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function fetchSourceArtifact(
  workspaceId: string,
  revisionId: string,
): Promise<SourceArtifact> {
  const result = await client.GET("/api/workspaces/{workspace_id}/sources/{revision_id}", {
    params: { path: { workspace_id: workspaceId, revision_id: revisionId } },
  });
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function readSourceExcerpt(
  workspaceId: string,
  revisionId: string,
  request: SourceExcerptRequest,
): Promise<SourceExcerpt> {
  const result = await client.POST(
    "/api/workspaces/{workspace_id}/sources/{revision_id}/read",
    {
      params: { path: { workspace_id: workspaceId, revision_id: revisionId } },
      body: request,
    },
  );
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function createMissionDraftAttempt(
  workspaceId: string,
  request: MissionDraftAttemptCreateRequest,
): Promise<MissionDraftAttempt> {
  const result = await client.POST(
    "/api/workspaces/{workspace_id}/mission-draft-attempts",
    {
      params: workspacePath(workspaceId),
      body: request,
    },
  );
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function fetchMissionDraftAttempt(
  workspaceId: string,
  attemptId: string,
): Promise<MissionDraftAttempt> {
  const result = await client.GET(
    "/api/workspaces/{workspace_id}/mission-draft-attempts/{attempt_id}",
    {
      params: { path: { workspace_id: workspaceId, attempt_id: attemptId } },
    },
  );
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function confirmMissionDraftAttempt(
  workspaceId: string,
  attemptId: string,
  request: MissionDraftConfirmRequest,
): Promise<Mission> {
  const result = await client.POST(
    "/api/workspaces/{workspace_id}/mission-draft-attempts/{attempt_id}/confirm",
    {
      params: { path: { workspace_id: workspaceId, attempt_id: attemptId } },
      body: request,
    },
  );
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function fetchMissions(workspaceId: string): Promise<Mission[]> {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions", {
    params: workspacePath(workspaceId),
  });
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function fetchMissionSnapshot(
  workspaceId: string,
  missionId: string,
): Promise<MissionSnapshot> {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}", {
    params: { path: { workspace_id: workspaceId, mission_id: missionId } },
  });
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function startRun(
  workspaceId: string,
  missionId: string,
  request: RunStartRequest,
): Promise<RunSnapshot> {
  const result = await client.POST("/api/workspaces/{workspace_id}/missions/{mission_id}/runs", {
    params: { path: { workspace_id: workspaceId, mission_id: missionId } },
    body: request,
  });
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function fetchRunSnapshot(
  workspaceId: string,
  missionId: string,
  runId: string,
): Promise<RunSnapshot> {
  const result = await client.GET(
    "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}",
    {
      params: { path: { workspace_id: workspaceId, mission_id: missionId, run_id: runId } },
    },
  );
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function cancelRun(
  workspaceId: string,
  missionId: string,
  runId: string,
): Promise<RunSnapshot> {
  const result = await client.POST(
    "/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}/cancel",
    {
      params: { path: { workspace_id: workspaceId, mission_id: missionId, run_id: runId } },
      body: {} satisfies CancelRunRequest,
    },
  );
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export function runEventsUrl(
  workspaceId: string,
  missionId: string,
  runId: string,
): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/missions/${encodeURIComponent(
    missionId,
  )}/runs/${encodeURIComponent(runId)}/events`;
}
