import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ApiRequestError } from "./api/client";
import {
  Path2RunDetails,
  RunUsageNotice,
  Path2Workbench,
  acceptRunEvent,
  buildConfirmRequest,
  buildRunStartRequest,
  buildSourceUploadRequest,
  createRunEventState,
  executeExplicitRequest,
  isCurrentOperation,
  isCurrentWorkspaceResponse,
  issueFromError,
  missionDraftAttemptMatchesIdentity,
  missionDraftAttemptIsMonotonic,
  missionSnapshotIsMonotonic,
  missionStatusLabel,
  sourceParseLabel,
  mergeRunSnapshot,
  parseRunEvent,
  replayTerminalRunEvents,
  pendingActionIssue,
  pendingConfirmMatchesIdentity,
  runSnapshotIsMonotonic,
  runSnapshotMatchesIdentity,
  sourceIdentityFromRevision,
  workbenchSurfaceSummary,
  type ClarificationRequest,
  type DefinitionDraft,
  type EvidenceRef,
  type Mission,
  type MissionDraftAttempt,
  type Path2WorkbenchState,
  type Path2PendingAction,
  type RunEventEnvelope,
  type RunSnapshot,
  type RunEventSource,
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
  regeneration_allowed: false,
  workspace_id: workspaceId,
  mission_id: missionId,
  run_id: runId,
  status: "running",
  phase: "legacy_loop",
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

const evidence: EvidenceRef = {
  workspace_id: workspaceId,
  source_id: sourceId,
  revision_id: revisionId,
  sha256: source.sha256,
  locator: { kind: "csv_rows", row_start: 2, row_end: 12, column: "customer_id" },
};

const draft: DefinitionDraft = {
  workspace_id: workspaceId,
  mission_id: missionId,
  draft_id: "99999999-9999-4999-8999-999999999999",
  version: 8,
  sha256: "c".repeat(64),
  status: "in_review",
  semantic_approval: "pending",
  fields: [{
    field_key: "customer_id",
    name: "客户 ID",
    meaning: "客户的稳定标识。",
    value_type: "string",
    grain: "客户",
    source_columns: [{ source_ref: sourceIdentityFromRevision(source), table_id: "customers", column: "customer_id" }],
    rule: "去除空白后保持原值",
    time_basis: "as_of_date",
    null_handling: "未知值保留为 null",
    evidence_status: "candidate",
    source_refs: [evidence],
    unknowns: [{ property_path: "fields.customer_id.rule", reason: "尚未确认历史修订规则。" }],
  }],
  relationships: [{
    relationship_key: "customers_to_orders",
    left: { source_ref: sourceIdentityFromRevision(source), table_id: "customers", columns: ["customer_id"] },
    right: { source_ref: sourceIdentityFromRevision(source), table_id: "orders", columns: ["customer_id"] },
    observed_cardinality: "one_to_many",
    join_rule: "customers.customer_id = orders.customer_id",
    grain_notes: "左侧一行代表一个客户。",
    evidence_status: "candidate",
    source_refs: [evidence],
    risks: ["同一客户可能存在合并记录。"],
    unknowns: [],
  }],
  unresolved_items: ["确认历史客户合并规则。"],
};

const clarification: ClarificationRequest = {
  workspace_id: workspaceId,
  mission_id: missionId,
  run_id: runId,
  clarification_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  draft_version: draft.version,
  draft_sha256: draft.sha256,
  status: "awaiting_answer",
  questions: [{
    question: "客户合并后的 ID 如何保留？",
    why_needed: "否则历史粒度无法稳定对齐。",
    expected_answer_type: "text",
    suggested_owner_role: "客户数据负责人",
    related_definition_paths: ["fields.customer_id.rule"],
    evidence_requested: ["客户合并规则文档"],
    examples_or_options: ["保留旧 ID", "映射到新 ID"],
    blocking_impact: "blocking",
    source_refs: [evidence],
  }],
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
  refreshSources: async () => undefined,
  uploadState: { status: "idle", issue: null, result: null },
  uploadSourceBatch: async () => null,
  acknowledgeUploadUnknown: () => undefined,
  missionState: { status: "empty", items: [], issue: null },
  selectedMission: null,
  missionSnapshot: null,
  missionSnapshotState: { status: "empty", items: [], issue: null },
  selectMission: async () => undefined,
  adoptDialogueRun: () => undefined,
  refreshMission: async () => undefined,
  refreshTask: async () => null,
  attempt: null,
  attemptAction: { status: "idle", issue: null },
  submitAttempt: async () => null,
  reconcileAttempt: async () => undefined,
  acknowledgeAttemptUnknown: () => undefined,
  confirmAction: { status: "idle", issue: null },
  confirmAttempt: async () => null,
  acknowledgeConfirmUnknown: () => undefined,
  runSnapshot: null,
  runAction: { status: "idle", issue: null },
  startRun: async () => null,
  acknowledgeRunUnknown: () => undefined,
  cancelAction: { status: "idle", issue: null },
  cancelActiveRun: async () => null,
  reconcileRun: async () => undefined,
  acknowledgeCancelUnknown: () => undefined,
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
    public_payload: { turn_index: sequence, transport: "stream", fallback_of_turn_index: null },
  };
}

describe("Path 2 Workbench state boundaries", () => {
  it("keeps resumable, waiting, unknown and failed task status distinct", () => {
    const terminal: RunSnapshot = {
      ...run, status: "partial", final_output: "Synthetic saved answer",
      terminal_receipt: {
        workspace_id: workspaceId, mission_id: missionId, run_id: runId,
        receipt_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        created_at: "2026-09-03T10:00:03Z", terminal_tool: "finish_run", outcome: "partial",
        draft_id: null, draft_version: null, draft_sha256: null,
        clarification_ids: [], provider_receipt_ids: [], tool_receipt_ids: [], source_refs: [],
      },
    };
    const state: Path2WorkbenchState = {
      ...emptyState, workspaceId, selectedMission: { ...mission, status: "blocked" },
      missionSnapshotState: { status: "ready", items: [mission], issue: null },
      runSnapshot: terminal,
    };
    expect(missionStatusLabel(state)).toBe("待继续分析");
    expect(missionStatusLabel({ ...state, runSnapshot: { ...terminal, final_output: null } })).not.toBe("待继续分析");
    expect(missionStatusLabel({ ...state, runSnapshot: { ...terminal, terminal_receipt: null } })).not.toBe("待继续分析");
    expect(missionStatusLabel({ ...state, clarifications: [clarification] })).toBe("待回答澄清");
    expect(missionStatusLabel({ ...state, latestDraft: draft })).toBe("待审核定义");
    expect(missionStatusLabel({ ...state, runSnapshot: { ...terminal, status: "failed", error_code: "table_not_found" } })).toBe("分析失败");
    for (const error_code of ["provider_cancelled_outcome_unknown", "interrupted_without_receipt"]) {
      expect(missionStatusLabel({ ...state, runSnapshot: { ...terminal, status: "blocked", error_code } })).toBe("需核对上次结果");
    }
    expect(missionStatusLabel({ ...state, runAction: { status: "unknown", issue: null } })).toBe("需核对上次结果");
    const markup = renderToStaticMarkup(createElement(Path2Workbench, { state, activeArea: "mission" }));
    expect(markup).toContain("待继续分析");
    expect(markup).toContain("任务版本与身份");
  });

  it("labels source readiness as parsing evidence rather than business approval", () => {
    expect(sourceParseLabel("ready")).toBe("解析完成");
    expect(sourceParseLabel("failed")).toBe("失败");
    expect(sourceParseLabel("blocked")).toBe("已阻塞");
  });

  it("replays terminal events once and closes without rerunning the task", () => {
    const listeners = new Map<string, (event: Event) => void>();
    const source: RunEventSource = {
      addEventListener: (type, listener) => { listeners.set(type, listener); },
      removeEventListener: (type) => { listeners.delete(type); },
      close: vi.fn(), onopen: null, onerror: null,
    };
    const factory = vi.fn(() => source);
    const state = vi.fn();
    const issue = vi.fn();
    let events = mergeRunSnapshot(createRunEventState(), { ...run, last_sequence: 3 });
    const cleanup = replayTerminalRunEvents({ ...run, status: "blocked", last_sequence: 3 }, factory,
      (history) => { events = history; },
      state, issue);
    const handler = listeners.get("model_started")!;
    const first = event(1, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    handler(new MessageEvent("model_started", { data: JSON.stringify(first) }));
    handler(new MessageEvent("model_started", { data: JSON.stringify(first) }));
    listeners.get("model_delta")!(new MessageEvent("model_delta", { data: JSON.stringify({
      ...event(2, "cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
      event_type: "model_delta", public_payload: { turn_index: 1, content: "transient text" },
    }) }));
    handler(new MessageEvent("model_started", { data: JSON.stringify(event(3, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")) }));
    expect(events.events).toHaveLength(2);
    expect(factory).toHaveBeenCalledTimes(1);
    expect(state).toHaveBeenLastCalledWith("closed");
    expect(source.close).toHaveBeenCalledTimes(1);
    expect(source.onerror).toBeNull();
    cleanup();
    handler(new MessageEvent("model_started", { data: JSON.stringify(first) }));
    expect(events.events).toHaveLength(2);
  });

  it("fails closed on wrong-scope replay, malformed events, incomplete EOF and timeout", () => {
    vi.useFakeTimers();
    try {
      for (const failure of ["scope", "malformed", "eof", "timeout"]) {
        const listeners = new Map<string, (event: Event) => void>();
        const source: RunEventSource = {
          addEventListener: (type, handler) => { listeners.set(type, handler); },
          removeEventListener: (type) => { listeners.delete(type); },
          close: vi.fn(), onopen: null, onerror: null,
        };
        const accepted = vi.fn();
        const state = vi.fn();
        const issue = vi.fn();
        replayTerminalRunEvents({ ...run, status: "failed" }, () => source, accepted, state, issue);
        if (failure === "timeout") vi.advanceTimersByTime(15000);
        else if (failure === "eof") source.onerror!(new Event("error"));
        else listeners.get("model_started")!(new MessageEvent("model_started", { data:
          failure === "malformed" ? "invalid" : JSON.stringify(event(1, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "another-workspace")),
        }));
        expect(accepted).not.toHaveBeenCalled();
        expect(source.close).toHaveBeenCalledTimes(1);
        expect(state).toHaveBeenLastCalledWith("blocked");
        expect(issue.mock.lastCall?.[0]).toContain("历史事件未完整加载");
      }
    } finally { vi.useRealTimers(); }
  });

  it("does not open replay for zero events and cleans up on Workspace change", () => {
    const factory = vi.fn();
    const state = vi.fn();
    replayTerminalRunEvents({ ...run, status: "failed", last_sequence: 0 }, factory, vi.fn(), state, vi.fn());
    expect(factory).not.toHaveBeenCalled();
    expect(state).toHaveBeenLastCalledWith("closed");
    const listeners = new Map<string, (event: Event) => void>();
    const source: RunEventSource = {
      addEventListener: (type, handler) => { listeners.set(type, handler); },
      removeEventListener: (type) => { listeners.delete(type); },
      close: vi.fn(), onopen: null, onerror: null,
    };
    const accepted = vi.fn();
    const cleanup = replayTerminalRunEvents({ ...run, status: "blocked" }, () => source, accepted, state, vi.fn());
    const late = listeners.get("model_started")!;
    cleanup();
    late(new MessageEvent("model_started", { data: JSON.stringify(event(1, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")) }));
    expect(accepted).not.toHaveBeenCalled();
    expect(source.close).toHaveBeenCalledTimes(1);
  });

  it("shows persisted failure reasons and truthful replay loading, empty and failed states", () => {
    const stopped: Path2WorkbenchState = {
      ...emptyState, workspaceId, selectedMission: mission,
      runSnapshot: { ...run, status: "blocked", error_code: "table_not_found" },
      runConnectionState: "connecting",
    };
    const render = (state: Path2WorkbenchState) => renderToStaticMarkup(createElement(Path2RunDetails, { state }));
    expect(render(stopped)).toContain("正在加载历史事件");
    expect(render(stopped)).toContain("table_not_found");
    expect(render(stopped)).toContain("核对资料与定位方式");
    expect(render({ ...stopped, runConnectionState: "closed" })).toContain("本次运行已结束");
    expect(render({ ...stopped, runEventIssue: "回放连接失败" })).toContain("历史事件加载失败");
    expect(render({ ...stopped, runConnectionState: "closed" })).not.toContain("等待公开事件");
    const missionMarkup = renderToStaticMarkup(createElement(Path2Workbench, { state: stopped, activeArea: "mission" }));
    expect(missionMarkup).toContain("table_not_found");
    expect(render({ ...stopped, runSnapshot: { ...run, status: "failed", error_code: "interrupted_without_receipt" } })).toContain("不要直接重试");
  });

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

  it("rejects a delayed continuation when its operation generation or object identity is stale", () => {
    const token = {
      kind: "run_snapshot" as const,
      generation: 2,
      epoch: 7,
      workspaceId,
      missionId,
      runId,
    };
    const currentScope = { workspaceId, missionId, attemptId: null, runId };
    expect(isCurrentOperation(token, 7, { run_snapshot: 2 }, currentScope)).toBe(true);
    expect(isCurrentOperation(token, 7, { run_snapshot: 1 }, currentScope)).toBe(false);
    expect(isCurrentOperation(token, 7, { run_snapshot: 2 }, { ...currentScope, runId: "stale-run" })).toBe(false);
    expect(isCurrentOperation(token, 8, { run_snapshot: 2 }, currentScope)).toBe(false);
  });

  it("keeps Attempt polling states and validates the original input binding", () => {
    expect(missionDraftAttemptMatchesIdentity(attempt, workspaceId, attempt.attempt_id, attempt.original_input)).toBe(true);
    expect(missionDraftAttemptMatchesIdentity(attempt, workspaceId, attempt.attempt_id, "different input")).toBe(false);
    expect(missionDraftAttemptMatchesIdentity({ ...attempt, workspace_id: "99999999-9999-4999-8999-999999999999" }, workspaceId, attempt.attempt_id)).toBe(false);
    expect(missionDraftAttemptIsMonotonic({ ...attempt, status: "running", candidate: null, candidate_version: null, candidate_sha256: null }, attempt)).toBe(true);
    expect(missionDraftAttemptIsMonotonic(attempt, { ...attempt, status: "running", candidate: null, candidate_version: null, candidate_sha256: null })).toBe(false);
    expect(missionDraftAttemptIsMonotonic({ ...attempt, status: "confirmed", mission_id: missionId }, attempt)).toBe(false);
  });

  it("never lets a Run readback regress sequence, draft, output, or terminal state", () => {
    expect(runSnapshotIsMonotonic(run, { ...run, last_sequence: 1 })).toBe(false);
    expect(runSnapshotIsMonotonic({ ...run, status: "completed", finished_at: "2026-09-03T10:00:03Z" }, run)).toBe(false);
    expect(runSnapshotIsMonotonic(run, { ...run, status: "completed", finished_at: "2026-09-03T10:00:03Z", last_sequence: 3 })).toBe(true);
    expect(runSnapshotIsMonotonic({ ...run, draft }, { ...run, last_sequence: 3, draft: null })).toBe(false);
  });

  it("keeps Mission snapshots monotonic when their Run or draft disappears", () => {
    const current = {
      mission,
      draft,
      clarifications: [clarification],
      latest_run: run,
    };
    expect(missionSnapshotIsMonotonic(current, { ...current, latest_run: null })).toBe(false);
    expect(missionSnapshotIsMonotonic(current, { ...current, draft: null })).toBe(false);
    expect(missionSnapshotIsMonotonic(current, { ...current, mission: { ...mission, state_version: 3 } })).toBe(false);
  });

  it("keeps pending action recovery scoped without persisting consent or raw input", () => {
    const pendingConfirm: Path2PendingAction = {
      kind: "confirm",
      requestId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      workspaceId,
      missionId: null,
      attemptId: attempt.attempt_id,
      runId: null,
      clientRequestId: null,
      intentKey: "confirm-intent",
      stateVersion: null,
      candidateVersion: attempt.candidate_version,
      candidateSha256: attempt.candidate_sha256,
      sourceRefs: [sourceIdentityFromRevision(source)],
    };
    expect(pendingActionIssue("run_start")).toMatchObject({
      kind: "unknown",
      code: "pending_action_unknown",
    });
    expect(pendingActionIssue("run_start").message).not.toContain("原始任务输入");
    expect(pendingConfirmMatchesIdentity(pendingConfirm, { ...attempt, status: "confirmed", mission_id: missionId }, mission)).toBe(true);
    expect(
      pendingConfirmMatchesIdentity(
        { ...pendingConfirm, candidateVersion: (attempt.candidate_version ?? 0) + 1 },
        { ...attempt, status: "confirmed", mission_id: missionId },
        mission,
      ),
    ).toBe(false);
    expect(pendingConfirmMatchesIdentity(pendingConfirm, attempt, mission)).toBe(false);
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
    expect(blockedMarkup).toContain("当前能力已阻塞");
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

  it("renders the contract fields, evidence locators, and clarification responsibility details", () => {
    const richState: Path2WorkbenchState = {
      ...emptyState,
      workspaceId,
      sourceState: { status: "ready", items: [source], issue: null },
      missionState: { status: "ready", items: [mission], issue: null },
      selectedMission: mission,
      missionSnapshotState: { status: "ready", items: [mission], issue: null },
      runSnapshot: { ...run, status: "waiting_for_human", finished_at: "2026-09-03T10:00:03Z", draft, clarifications: [clarification] },
      latestDraft: draft,
      clarifications: [clarification],
    };
    const contractMarkup = renderToStaticMarkup(createElement(Path2Workbench, { state: richState, activeArea: "contract" }));
    expect(contractMarkup).toContain("时间基准");
    expect(contractMarkup).toContain("去除空白后保持原值");
    expect(contractMarkup).toContain("as_of_date");
    expect(contractMarkup).toContain("CSV 行 2-12");
    expect(contractMarkup).toContain("历史客户合并规则");

    const clarificationMarkup = renderToStaticMarkup(createElement(Path2Workbench, { state: richState, activeArea: "clarifications" }));
    expect(clarificationMarkup).toContain("客户数据负责人");
    expect(clarificationMarkup).toContain("fields.customer_id.rule");
    expect(clarificationMarkup).toContain("保留旧 ID");
    expect(clarificationMarkup).toContain("CSV 行 2-12");
  });

  it("derives the Workbench context status from actual collection state", () => {
    expect(workbenchSurfaceSummary(emptyState)).toEqual({ status: "empty", label: "未选择 Workspace" });
    expect(workbenchSurfaceSummary({
      ...emptyState,
      workspaceId,
      sourceState: { status: "ready", items: [source], issue: null },
      missionState: { status: "ready", items: [mission], issue: null },
      missionSnapshotState: { status: "ready", items: [mission], issue: null },
    })).toEqual({ status: "ready", label: "来源与任务已读取" });
  });

  it("keeps a cancelled Run visible as cancelled in the Agent panel", () => {
    const cancelledState: Path2WorkbenchState = {
      ...emptyState,
      workspaceId,
      runSnapshot: { ...run, status: "cancelled", finished_at: "2026-09-03T10:00:03Z" },
    };
    const markup = renderToStaticMarkup(createElement(Path2RunDetails, { state: cancelledState }));
    expect(markup).toContain("已取消");
    expect(markup).not.toContain("已完成");
  });
});


describe("Run usage readback", () => {
  const receipt = {
    workspace_id: workspaceId, mission_id: missionId, run_id: runId,
    receipt_id: "00000000-0000-4000-8000-000000000081", attempt_id: null,
    turn_index: 1, created_at: "2026-09-03T10:00:00Z", status: "succeeded" as const,
    config: { endpoint_id: "deepseek_chat_completions" as const, model: "deepseek-v4-flash" as const,
      thinking: "enabled" as const, reasoning_effort: "high" as const },
    p0_sha256: "0".repeat(64), input_tokens: null, output_tokens: null,
    cache_hit_tokens: null, cache_miss_tokens: null, context_manifest_id: runId,
    context_manifest_sha256: "0".repeat(64), tool_schema_sha256: "0".repeat(64),
    error_code: "provider_usage_missing", usage_status: "missing" as const,
  };
  it("shows missing usage without presenting a zero total", () => {
    const markup = renderToStaticMarkup(createElement(RunUsageNotice, { run: { ...run, provider_receipts: [receipt] } }));
    expect(markup).toContain("1 次请求用量未返回");
    expect(markup).not.toContain("输入 0");
    expect(markup).not.toContain("已知用量小计");
  });
  it("separates a known subtotal from missing attempts and checks receipt scope", () => {
    const snapshot = { ...run, provider_receipts: [receipt, { ...receipt,
      receipt_id: "00000000-0000-4000-8000-000000000082", turn_index: 2,
      input_tokens: 9, output_tokens: 5, usage_status: "known" as const, error_code: null,
    }] };
    const markup = renderToStaticMarkup(createElement(RunUsageNotice, { run: snapshot }));
    expect(markup).toContain("已知用量小计：输入 9，输出 5 tokens");
    expect(markup).toContain("总用量尚不完整");
    expect(runSnapshotMatchesIdentity({ ...snapshot, provider_receipts: [{ ...receipt, workspace_id: "wrong" }] },
      { workspaceId, missionId, runId })).toBe(false);
  });
});

describe("non-stream fallback history", () => {
  const first = event(1, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  const fallback: RunEventEnvelope = { ...first, sequence: 4,
    event_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_type: "model_started",
    public_payload: { turn_index: 2, transport: "non_stream", fallback_of_turn_index: 1 },
  };
  it("retains abandoned stream fragments as history distinct from the persisted answer", () => {
    const events: RunEventEnvelope[] = [first, { ...first, sequence: 2,
      event_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", event_type: "model_delta",
      public_payload: { turn_index: 1, content: "abandoned fragment" },
    }, { ...first, sequence: 3, event_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      event_type: "model_completed", public_payload: { turn_index: 1, provider_receipt_id: runId },
    }, fallback];
    const markup = renderToStaticMarkup(createElement(Path2RunDetails, { state: {
      ...emptyState, workspaceId, runSnapshot: { ...run, final_output: "persisted replacement" },
      runEventState: { events, lastSequence: 4, hasSequenceGap: false },
    } }));
    expect(markup).toContain("请求 2 开始非流式降级，替代流式请求 1");
    expect(markup).toContain("已停止并降级，不作为结果");
    expect(markup).toContain("此事件不表示原请求成功");
    expect(markup).toContain("abandoned fragment");
    expect(markup).toContain("persisted replacement");
  });
  it("accepts legacy stream events and validates fallback linkage", () => {
    expect(parseRunEvent(JSON.stringify(first))).not.toBeNull();
    expect(parseRunEvent(JSON.stringify(fallback))).not.toBeNull();
    for (const payload of [
      { turn_index: 2, transport: "non_stream" },
      { turn_index: 2, transport: "non_stream", fallback_of_turn_index: 2 },
      { turn_index: 3, transport: "non_stream", fallback_of_turn_index: 1 },
      { turn_index: 2, transport: "stream", fallback_of_turn_index: 1 },
    ]) expect(parseRunEvent(JSON.stringify({ ...fallback, public_payload: payload }))).toBeNull();
  });
});
