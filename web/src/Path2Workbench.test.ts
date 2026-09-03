import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApiRequestError } from "./api/client";
import {
  Path2AgentContent,
  Path2Workbench,
  acceptRunEvent,
  buildConfirmRequest,
  buildRunStartRequest,
  buildSourceUploadRequest,
  createRunEventState,
  executeExplicitRequest,
  isCurrentWorkspaceResponse,
  issueFromError,
  mergeRunSnapshot,
  parseRunEvent,
  runSnapshotMatchesIdentity,
  sourceIdentityFromRevision,
  type Mission,
  type MissionDraftAttempt,
  type Path2WorkbenchState,
  type RunEventEnvelope,
  type RunSnapshot,
  type SourceRevision,
} from "./Path2Workbench";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const missionId = "22222222-2222-4222-8222-222222222222";
const runId = "33333333-3333-4333-8333-333333333333";
const sourceId = "44444444-4444-4444-8444-444444444444";
const revisionId = "55555555-5555-4555-8555-555555555555";

const source: SourceRevision = {
  workspace_id: workspaceId,
  source_id: sourceId,
  revision_id: revisionId,
  original_name: "facts.csv",
  media_type: "text/csv",
  byte_size: 32,
  sha256: "a".repeat(64),
  observed_at: "2026-09-03T10:00:00Z",
  effective_time: null,
  permission_status: "read_allowed",
  parse_status: "ready",
  parser_version: "csv-v1",
};

const mission: Mission = {
  workspace_id: workspaceId,
  mission_id: missionId,
  created_at: "2026-09-03T10:00:00Z",
  state_version: 4,
  status: "active",
  title: "定义客户字段",
  goal: "建立可回看的字段定义草案。",
  completion_criteria: ["保留来源引用"],
  scope_notes: [],
  original_attempt_id: "66666666-6666-4666-8666-666666666666",
  source_refs: [sourceIdentityFromRevision(source)],
};

const attempt: MissionDraftAttempt = {
  workspace_id: workspaceId,
  attempt_id: "77777777-7777-4777-8777-777777777777",
  created_at: "2026-09-03T10:00:00Z",
  original_input: "定义客户字段",
  status: "ready",
  candidate: {
    title: "定义客户字段",
    goal: "建立可回看的字段定义草案。",
    completion_criteria: ["保留来源引用"],
    scope_notes: [],
  },
  candidate_version: 7,
  candidate_sha256: "b".repeat(64),
  provider_receipt_id: "88888888-8888-4888-8888-888888888888",
  mission_id: null,
  error_code: null,
};

const runBudget = {
  max_model_turns: 8,
  max_tool_calls: 24,
  max_elapsed_ms: 300000,
  max_output_tokens: 4096,
  max_retries: 0,
  connect_timeout_ms: 10000,
  first_event_timeout_ms: 60000,
  idle_timeout_ms: 30000,
  total_timeout_ms: 120000,
  max_context_bytes: 262144,
} as const;

const run: RunSnapshot = {
  workspace_id: workspaceId,
  mission_id: missionId,
  run_id: runId,
  status: "running",
  created_at: "2026-09-03T10:00:00Z",
  started_at: "2026-09-03T10:00:01Z",
  finished_at: null,
  budget: runBudget,
  source_refs: [sourceIdentityFromRevision(source)],
  draft: null,
  clarifications: [],
  last_sequence: 2,
  terminal_receipt: null,
  final_output: null,
  error_code: null,
};

const emptyState: Path2WorkbenchState = {
  workspaceId: null,
  sourceState: { status: "idle", items: [], issue: null },
  sourceArtifacts: {},
  selectedSourceIds: [],
  selectedSourceRefs: [],
  selectSource: () => undefined,
  toggleSource: () => undefined,
  loadSourceArtifact: async () => undefined,
  readSourceExcerpt: async () => undefined,
  uploadState: { status: "idle", issue: null, result: null },
  uploadSourceBatch: async () => null,
  missionState: { status: "empty", items: [], issue: null },
  selectedMission: null,
  missionSnapshot: null,
  missionSnapshotState: { status: "empty", items: [], issue: null },
  attempt: null,
  attemptAction: { status: "idle", issue: null },
  submitAttempt: async () => null,
  confirmAction: { status: "idle", issue: null },
  confirmAttempt: async () => null,
  runSnapshot: null,
  runAction: { status: "idle", issue: null },
  startRun: async () => null,
  cancelAction: { status: "idle", issue: null },
  cancelActiveRun: async () => null,
  runConnectionState: "idle",
  runReadbackIssue: null,
  runEventIssue: null,
  runEventState: createRunEventState(),
  latestDraft: null,
  clarifications: [],
};

function event(sequence: number, eventId: string, workspace = workspaceId): RunEventEnvelope {
  return {
    event_id: eventId,
    event_type: "model_started",
    occurred_at: "2026-09-03T10:00:02Z",
    workspace_id: workspace,
    mission_id: missionId,
    run_id: runId,
    sequence,
    public_payload: { turn_index: sequence },
  };
}

describe("Path 2 Workbench state boundaries", () => {
  it("rejects late responses once the Workspace epoch or identity changes", () => {
    expect(isCurrentWorkspaceResponse(3, 4, workspaceId, workspaceId)).toBe(false);
    expect(isCurrentWorkspaceResponse(3, 3, workspaceId, "99999999-9999-4999-8999-999999999999")).toBe(false);
    expect(isCurrentWorkspaceResponse(3, 3, workspaceId, workspaceId)).toBe(true);
    expect(runSnapshotMatchesIdentity(run, { workspaceId, missionId, runId })).toBe(true);
    expect(
      runSnapshotMatchesIdentity(
        {
          ...run,
          terminal_receipt: {
            workspace_id: "99999999-9999-4999-8999-999999999999",
            mission_id: missionId,
            run_id: runId,
            receipt_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            created_at: "2026-09-03T10:00:03Z",
            terminal_tool: "finish_run",
            outcome: "partial",
            draft_id: null,
            draft_version: null,
            draft_sha256: null,
            clarification_ids: [],
            provider_receipt_ids: [],
            tool_receipt_ids: [],
            source_refs: [],
          },
        },
        { workspaceId, missionId, runId },
      ),
    ).toBe(false);
  });

  it("keeps local-read consent separate from the generated upload contract", () => {
    const request = buildSourceUploadRequest(
      [{ original_name: "facts.csv", media_type: "text/csv", content_base64: "ZmFjdHM=" }],
      true,
    );
    expect(request).toEqual({
      files: [{ original_name: "facts.csv", media_type: "text/csv", content_base64: "ZmFjdHM=" }],
      local_read_confirmed: true,
    });
    expect(JSON.stringify(request)).not.toContain("provider");
  });

  it("builds confirmation and Start as separate versioned actions", () => {
    expect(buildConfirmRequest(attempt, [sourceIdentityFromRevision(source)])).toEqual({
      candidate_version: 7,
      candidate_sha256: "b".repeat(64),
      source_refs: [sourceIdentityFromRevision(source)],
    });
    expect(buildConfirmRequest({ ...attempt, candidate: null, candidate_version: null, candidate_sha256: null }, [])).toBeNull();

    const start = buildRunStartRequest(
      mission,
      [sourceIdentityFromRevision(source)],
      true,
      "99999999-9999-4999-8999-999999999999",
    );
    expect(start).toEqual({
      expected_state_version: 4,
      source_refs: [sourceIdentityFromRevision(source)],
      provider_send_confirmed: true,
      client_request_id: "99999999-9999-4999-8999-999999999999",
    });
  });

  it("parses only identity-bound public SSE events and deduplicates id/sequence", () => {
    const first = event(1, "evt-1");
    expect(parseRunEvent(JSON.stringify(first))).toEqual(first);
    expect(parseRunEvent("not-json")).toBeNull();
    expect(parseRunEvent(JSON.stringify({ ...first, public_payload: { turn_index: 0 } }))).toBeNull();

    let state = createRunEventState();
    state = acceptRunEvent(state, first, { workspaceId, missionId, runId });
    expect(acceptRunEvent(state, first, { workspaceId, missionId, runId })).toBe(state);
    expect(acceptRunEvent(state, event(1, "evt-other"), { workspaceId, missionId, runId })).toBe(state);
    state = acceptRunEvent(state, event(3, "evt-3"), { workspaceId, missionId, runId });
    expect(state.events.map((item) => item.sequence)).toEqual([1, 3]);
    expect(state.hasSequenceGap).toBe(true);
  });

  it("uses snapshot readback to mark recovery gaps without rebuilding missing delta text", () => {
    let state = acceptRunEvent(createRunEventState(), event(1, "evt-1"), { workspaceId, missionId, runId });
    state = mergeRunSnapshot(state, { ...run, last_sequence: 3 });
    expect(state.lastSequence).toBe(3);
    expect(state.hasSequenceGap).toBe(true);
    expect(state.events).toHaveLength(1);
  });

  it("does not retry a one-shot POST after an unknown result", async () => {
    let calls = 0;
    const request = executeExplicitRequest(async () => {
      calls += 1;
      throw new Error("transport disappeared");
    });
    await expect(request).rejects.toThrow("transport disappeared");
    expect(calls).toBe(1);
    expect(issueFromError(new Error("transport disappeared"))).toMatchObject({ kind: "unknown" });
  });

  it("keeps W0.2's real 503 as blocked rather than empty success", () => {
    const issue = issueFromError(new ApiRequestError(503, {
      code: "path2_not_implemented",
      message: "not implemented",
      request_id: "req-1",
    }));
    expect(issue).toEqual({
      kind: "blocked",
      code: "path2_not_implemented",
      message: "Path 2 当前不可用：W0.2 只提供共享契约接缝，尚未接入来源、Mission 或 Run。",
    });
  });

  it("renders empty and blocked states without built-in business data", () => {
    const emptyMarkup = renderToStaticMarkup(
      createElement(Path2Workbench, { state: emptyState, activeArea: "mission" }),
    );
    expect(emptyMarkup).toContain("请先选择 Workspace");
    expect(emptyMarkup).not.toContain("客户主数据.csv");

    const blockedIssue = issueFromError(new ApiRequestError(503, {
      code: "path2_not_implemented",
      message: "not implemented",
      request_id: "req-2",
    }));
    const blockedState: Path2WorkbenchState = {
      ...emptyState,
      workspaceId,
      sourceState: { status: "blocked", items: [], issue: blockedIssue },
      missionState: { status: "blocked", items: [], issue: blockedIssue },
      missionSnapshotState: { status: "blocked", items: [], issue: blockedIssue },
    };
    const blockedMarkup = renderToStaticMarkup(
      createElement(Path2Workbench, { state: blockedState, activeArea: "sources" }),
    );
    expect(blockedMarkup).toContain("W0.2 接缝不可用");
    expect(blockedMarkup).toContain("path2_not_implemented");
  });

  it("renders the exact candidate version/hash before a distinct Start action", () => {
    const state: Path2WorkbenchState = {
      ...emptyState,
      workspaceId,
      sourceState: { status: "ready", items: [source], issue: null },
      selectedSourceIds: [revisionId],
      selectedSourceRefs: [sourceIdentityFromRevision(source)],
      missionState: { status: "ready", items: [mission], issue: null },
      selectedMission: mission,
      missionSnapshotState: { status: "ready", items: [mission], issue: null },
      attempt,
    };
    const markup = renderToStaticMarkup(
      createElement(Path2Workbench, { state, activeArea: "mission" }),
    );
    expect(markup).toContain("version");
    expect(markup).toContain("bbbbbbbbbbbbbb");
    expect(markup).toContain("确认不会创建 Run");
    expect(markup).toContain("明确开始 Run");
    expect(markup).not.toContain("持久化公开摘要");
  });

  it("keeps a cancelled Run visible as cancelled in the Agent panel", () => {
    const cancelledState: Path2WorkbenchState = {
      ...emptyState,
      workspaceId,
      runSnapshot: { ...run, status: "cancelled", finished_at: "2026-09-03T10:00:03Z" },
    };
    const markup = renderToStaticMarkup(createElement(Path2AgentContent, { state: cancelledState }));
    expect(markup).toContain("已取消");
    expect(markup).not.toContain("已完成");
  });
});
