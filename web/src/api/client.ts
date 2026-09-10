import createClient from "openapi-fetch";
import type { components, paths } from "../generated/api";

const client = createClient<paths>({ baseUrl: "" });

export type DeepSeekSettings = components["schemas"]["DeepSeekSettings"];
export type DemoCase = components["schemas"]["DemoCaseV1"];
export type CandidateExportDocument = components["schemas"]["CandidateExportDocument"];

export async function fetchDemo(): Promise<DemoCase> {
  const result = await client.GET("/api/demo");
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

export async function exportCandidate(workspaceId: string, missionId: string, version: number, sha256: string): Promise<CandidateExportDocument> {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}/draft-export", {
    params: { path: { workspace_id: workspaceId, mission_id: missionId }, query: { expected_version: version, expected_sha256: sha256 } }, cache: "no-store",
  });
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

export async function fetchDeepSeekSettings(): Promise<DeepSeekSettings> {
  const result = await client.GET("/api/local-settings/deepseek", { cache: "no-store" });
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

export async function saveDeepSeekKey(apiKey: string, sessionToken: string): Promise<DeepSeekSettings> {
  const result = await client.PUT("/api/local-settings/deepseek", {
    body: { api_key: apiKey }, params: { header: { "X-ContextOx-Session": sessionToken } }, cache: "no-store",
  });
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

export async function removeDeepSeekKey(sessionToken: string): Promise<DeepSeekSettings> {
  const result = await client.DELETE("/api/local-settings/deepseek", {
    params: { header: { "X-ContextOx-Session": sessionToken } }, cache: "no-store",
  });
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

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
type ProfilePackV1 = components["schemas"]["ProfilePackV1"];
type ProfileInterpretationCreateRequest =
  components["schemas"]["ProfileInterpretationCreateRequest"];
type ProfileInterpretationAttempt = components["schemas"]["ProfileInterpretationAttempt"];
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

export async function fetchSourceProfile(
  workspaceId: string,
  revisionId: string,
): Promise<ProfilePackV1> {
  const result = await client.GET(
    "/api/workspaces/{workspace_id}/sources/{revision_id}/profile",
    { params: { path: { workspace_id: workspaceId, revision_id: revisionId } } },
  );
  if (!result.response.ok || !result.data) {
    throwForResult(result);
  }
  return result.data;
}

export async function createProfileInterpretation(
  workspaceId: string,
  revisionId: string,
  request: ProfileInterpretationCreateRequest,
): Promise<ProfileInterpretationAttempt> {
  const result = await client.POST(
    "/api/workspaces/{workspace_id}/sources/{revision_id}/profile-interpretations",
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

export async function fetchProfileInterpretation(
  workspaceId: string,
  revisionId: string,
  attemptId: string,
): Promise<ProfileInterpretationAttempt> {
  const result = await client.GET(
    "/api/workspaces/{workspace_id}/sources/{revision_id}/profile-interpretations/{attempt_id}",
    {
      params: {
        path: {
          workspace_id: workspaceId,
          revision_id: revisionId,
          attempt_id: attemptId,
        },
      },
    },
  );
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

export async function fetchTaskMessages(workspaceId: string, missionId: string, before?: string) {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}/messages", {
    params: { path: { workspace_id: workspaceId, mission_id: missionId }, query: { before_message_id: before } },
  });
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

export async function fetchTaskRuns(workspaceId: string, missionId: string, before?: string) {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}/runs", {
    params: { path: { workspace_id: workspaceId, mission_id: missionId }, query: { before_run_id: before } },
  });
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

export async function sendTaskMessage(workspaceId: string, missionId: string, body: components["schemas"]["TaskMessageSendRequest"]) {
  const result = await client.POST("/api/workspaces/{workspace_id}/missions/{mission_id}/messages", {
    params: { path: { workspace_id: workspaceId, mission_id: missionId } }, body,
  });
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

export async function fetchMessageSubmission(workspaceId: string, missionId: string, requestId: string) {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}/message-submissions/{client_request_id}", {
    params: { path: { workspace_id: workspaceId, mission_id: missionId, client_request_id: requestId } },
  });
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}

export async function fetchClarificationCases(workspaceId: string, missionId: string) {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}/clarification-cases", {params:{path:{workspace_id:workspaceId, mission_id:missionId}}});
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}
export async function saveClarificationAnswer(workspaceId: string, missionId: string, originRunId: string, clarificationId: string, body: components["schemas"]["ClarificationAnswerSaveRequest"]) {
  const result = await client.POST("/api/workspaces/{workspace_id}/missions/{mission_id}/clarifications/{origin_run_id}/{clarification_id}/answers", {params:{path:{workspace_id:workspaceId, mission_id:missionId, origin_run_id:originRunId, clarification_id:clarificationId}}, body});
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}
export async function approveClarificationAnswer(workspaceId: string, missionId: string, originRunId: string, clarificationId: string, version: number, body: components["schemas"]["ClarificationAnswerApproveRequest"]) {
  const result = await client.POST("/api/workspaces/{workspace_id}/missions/{mission_id}/clarifications/{origin_run_id}/{clarification_id}/answers/{version}/approve", {params:{path:{workspace_id:workspaceId, mission_id:missionId, origin_run_id:originRunId, clarification_id:clarificationId, version}}, body});
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}
export async function fetchClarificationAnswer(workspaceId: string, missionId: string, originRunId: string, clarificationId: string, version: number) {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}/clarifications/{origin_run_id}/{clarification_id}/answers/{version}", {params:{path:{workspace_id:workspaceId, mission_id:missionId, origin_run_id:originRunId, clarification_id:clarificationId, version}}});
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}
export async function fetchClarificationSubmission(workspaceId: string, missionId: string, requestId: string) {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}/clarification-submissions/{client_request_id}", {params:{path:{workspace_id:workspaceId, mission_id:missionId, client_request_id:requestId}}});
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}
export async function fetchAnswerImpact(workspaceId: string, missionId: string, runId: string) {
  const result = await client.GET("/api/workspaces/{workspace_id}/missions/{mission_id}/runs/{run_id}/answer-impact", {params:{path:{workspace_id:workspaceId, mission_id:missionId, run_id:runId}}});
  if (!result.response.ok || !result.data) throwForResult(result);
  return result.data;
}
