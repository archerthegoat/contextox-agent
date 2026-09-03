import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import {
  ApiRequestError,
  cancelRun,
  confirmMissionDraftAttempt,
  createMissionDraftAttempt,
  fetchMissionDraftAttempt,
  fetchMissionSnapshot,
  fetchMissions,
  fetchRunSnapshot,
  fetchSourceArtifact,
  fetchSources,
  readSourceExcerpt,
  runEventsUrl,
  startRun,
  uploadSources,
  type Workspace,
} from "./api/client";
import type { components } from "./generated/api";

export type SourceUploadRequest = components["schemas"]["SourceUploadRequest"];
export type SourceUploadFile = components["schemas"]["SourceUploadFile"];
export type SourceBatchResult = components["schemas"]["SourceBatchResult"];
export type SourceRevision = components["schemas"]["SourceRevision"];
export type SourceArtifact = components["schemas"]["SourceArtifact"];
export type SourceExcerpt = components["schemas"]["SourceExcerpt"];
export type SourceExcerptRequest = components["schemas"]["SourceExcerptRequest"];
export type MissionDraftAttempt = components["schemas"]["MissionDraftAttempt"];
export type MissionDraftAttemptCreateRequest =
  components["schemas"]["MissionDraftAttemptCreateRequest"];
export type MissionDraftConfirmRequest = components["schemas"]["MissionDraftConfirmRequest"];
export type MissionDraftPayload = components["schemas"]["MissionDraftPayload"];
export type Mission = components["schemas"]["Mission"];
export type MissionSnapshot = components["schemas"]["MissionSnapshot"];
export type RunStartRequest = components["schemas"]["RunStartRequest"];
export type RunSnapshot = components["schemas"]["RunSnapshot"];
export type DefinitionDraft = components["schemas"]["DefinitionDraft"];
export type ClarificationRequest = components["schemas"]["ClarificationRequest"];
export type RunEventEnvelope = components["schemas"]["RunEventEnvelope"];
export type SourceIdentity = components["schemas"]["SourceIdentity"];
export type EvidenceRef = components["schemas"]["EvidenceRef"];
export type EvidenceLocator = components["schemas"]["CsvRowsLocator"] |
  components["schemas"]["JsonPointerLocator"] |
  components["schemas"]["TextLinesLocator"];

export function isCurrentWorkspaceResponse(
  expectedEpoch: number,
  currentEpoch: number,
  expectedWorkspaceId: string,
  responseWorkspaceId: string,
): boolean {
  return expectedEpoch === currentEpoch && expectedWorkspaceId === responseWorkspaceId;
}

export function sourceRevisionMatchesWorkspace(
  revision: SourceRevision,
  workspaceId: string,
): boolean {
  return revision.workspace_id === workspaceId;
}

export function sourceIdentityEquals(left: SourceIdentity, right: SourceIdentity): boolean {
  return (
    left.workspace_id === right.workspace_id &&
    left.source_id === right.source_id &&
    left.revision_id === right.revision_id &&
    left.sha256 === right.sha256
  );
}

export function sourceIdentityListEquals(left: SourceIdentity[], right: SourceIdentity[]): boolean {
  return (
    left.length === right.length &&
    left.every((reference, index) => sourceIdentityEquals(reference, right[index]))
  );
}

export function missionMatchesWorkspace(mission: Mission, workspaceId: string): boolean {
  return mission.workspace_id === workspaceId;
}

export function missionSnapshotMatchesIdentity(
  snapshot: MissionSnapshot,
  workspaceId: string,
  missionId: string,
): boolean {
  return (
    snapshot.mission.workspace_id === workspaceId &&
    snapshot.mission.mission_id === missionId &&
    (!snapshot.draft ||
      (snapshot.draft.workspace_id === workspaceId && snapshot.draft.mission_id === missionId)) &&
    snapshot.clarifications.every(
      (clarification) =>
        clarification.workspace_id === workspaceId && clarification.mission_id === missionId,
    ) &&
    (!snapshot.latest_run || runSnapshotMatchesIdentity(snapshot.latest_run, {
      workspaceId,
      missionId,
      runId: snapshot.latest_run.run_id,
    }))
  );
}

export function runSnapshotMatchesIdentity(snapshot: RunSnapshot, identity: RunIdentity): boolean {
  return (
    snapshot.workspace_id === identity.workspaceId &&
    snapshot.mission_id === identity.missionId &&
    snapshot.run_id === identity.runId &&
    snapshot.source_refs.every((reference) => reference.workspace_id === identity.workspaceId) &&
    (!snapshot.draft ||
      (snapshot.draft.workspace_id === identity.workspaceId && snapshot.draft.mission_id === identity.missionId)) &&
    snapshot.clarifications.every(
      (clarification) =>
        clarification.workspace_id === identity.workspaceId &&
        clarification.mission_id === identity.missionId &&
        clarification.run_id === identity.runId,
    ) &&
    (!snapshot.terminal_receipt ||
      (snapshot.terminal_receipt.workspace_id === identity.workspaceId &&
        snapshot.terminal_receipt.mission_id === identity.missionId &&
        snapshot.terminal_receipt.run_id === identity.runId))
  );
}

const TERMINAL_RUN_STATUSES: RunSnapshot["status"][] = [
  "waiting_for_human",
  "partial",
  "completed",
  "blocked",
  "failed",
  "cancelled",
];

function isTerminalRunStatus(status: RunSnapshot["status"]): boolean {
  return TERMINAL_RUN_STATUSES.includes(status);
}

const RUN_STATUS_ORDER: Record<RunSnapshot["status"], number> = {
  queued: 0,
  running: 1,
  waiting_for_human: 2,
  partial: 2,
  completed: 2,
  blocked: 2,
  failed: 2,
  cancelled: 2,
};

export function runSnapshotIsMonotonic(
  current: RunSnapshot,
  next: RunSnapshot,
): boolean {
  if (
    current.workspace_id !== next.workspace_id ||
    current.mission_id !== next.mission_id ||
    current.run_id !== next.run_id ||
    next.last_sequence < current.last_sequence ||
    (current.finished_at !== null && next.finished_at === null) ||
    (current.draft && (!next.draft || next.draft.version < current.draft.version)) ||
    (current.final_output !== null && next.final_output === null) ||
    (current.terminal_receipt !== null && next.terminal_receipt === null) ||
    (current.terminal_receipt && next.terminal_receipt && current.terminal_receipt.receipt_id !== next.terminal_receipt.receipt_id) ||
    RUN_STATUS_ORDER[next.status] < RUN_STATUS_ORDER[current.status]
  ) {
    return false;
  }
  if (current.clarifications.some((currentClarification) =>
    !next.clarifications.some((nextClarification) => nextClarification.clarification_id === currentClarification.clarification_id))) {
    return false;
  }
  if (isTerminalRunStatus(current.status)) {
    return next.status === current.status;
  }
  return true;
}

export function missionSnapshotIsMonotonic(
  current: MissionSnapshot,
  next: MissionSnapshot,
): boolean {
  if (
    current.mission.workspace_id !== next.mission.workspace_id ||
    current.mission.mission_id !== next.mission.mission_id ||
    next.mission.state_version < current.mission.state_version
  ) {
    return false;
  }
  if (current.draft && (!next.draft || next.draft.version < current.draft.version)) {
    return false;
  }
  if (current.latest_run && !next.latest_run) {
    return false;
  }
  if (current.latest_run && next.latest_run && !runSnapshotIsMonotonic(current.latest_run, next.latest_run)) {
    return false;
  }
  if (current.clarifications.some((currentClarification) =>
    !next.clarifications.some((nextClarification) => nextClarification.clarification_id === currentClarification.clarification_id))) {
    return false;
  }
  return true;
}

export function missionDraftAttemptMatchesIdentity(
  attempt: MissionDraftAttempt,
  workspaceId: string,
  attemptId: string,
  expectedOriginalInput?: string,
): boolean {
  return (
    attempt.workspace_id === workspaceId &&
    attempt.attempt_id === attemptId &&
    (expectedOriginalInput === undefined || attempt.original_input === expectedOriginalInput)
  );
}

type OperationKind =
  | "sources"
  | "artifact"
  | "excerpt"
  | "missions"
  | "mission_snapshot"
  | "attempt"
  | "confirm"
  | "upload"
  | "run_snapshot"
  | "run_start"
  | "cancel";

type OperationScope = {
  workspaceId: string;
  missionId?: string | null;
  attemptId?: string | null;
  runId?: string | null;
};

export type OperationToken = OperationScope & {
  kind: OperationKind;
  generation: number;
  epoch: number;
};

type OperationGenerations = Partial<Record<OperationKind, number>>;

export function isCurrentOperation(
  token: OperationToken,
  currentEpoch: number,
  generations: OperationGenerations,
  currentScope: OperationScope,
): boolean {
  if (
    !isCurrentWorkspaceResponse(
      token.epoch,
      currentEpoch,
      token.workspaceId,
      currentScope.workspaceId,
    ) ||
    token.generation !== generations[token.kind]
  ) {
    return false;
  }
  if (token.missionId !== undefined && token.missionId !== currentScope.missionId) {
    return false;
  }
  if (token.attemptId !== undefined && token.attemptId !== currentScope.attemptId) {
    return false;
  }
  if (token.runId !== undefined && token.runId !== currentScope.runId) {
    return false;
  }
  return true;
}

export type Path2ObjectPointers = {
  attemptId: string | null;
  missionId: string | null;
  runId: string | null;
  runMissionId: string | null;
  pendingActions: Path2PendingActions;
};

export type PendingActionKind = "upload" | "attempt" | "confirm" | "run_start" | "cancel";

export type Path2PendingAction = {
  kind: PendingActionKind;
  requestId: string;
  workspaceId: string;
  missionId: string | null;
  attemptId: string | null;
  runId: string | null;
  clientRequestId: string | null;
  intentKey: string | null;
  stateVersion: number | null;
  candidateVersion: number | null;
  candidateSha256: string | null;
  sourceRefs: SourceIdentity[];
};

export type Path2PendingActions = Partial<Record<PendingActionKind, Path2PendingAction>>;

const PATH2_OBJECT_POINTERS_PREFIX = "contextox.path2.object-pointers.";

const PENDING_ACTION_KINDS: PendingActionKind[] = ["upload", "attempt", "confirm", "run_start", "cancel"];

function emptyPath2ObjectPointers(): Path2ObjectPointers {
  return {
    attemptId: null,
    missionId: null,
    runId: null,
    runMissionId: null,
    pendingActions: {},
  };
}

function storedString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function storedPositiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

function storedNonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function isStoredSourceIdentity(value: unknown, workspaceId: string): value is SourceIdentity {
  return (
    isRecord(value) &&
    value.workspace_id === workspaceId &&
    storedString(value.source_id) !== null &&
    storedString(value.revision_id) !== null &&
    typeof value.sha256 === "string" &&
    /^[a-f0-9]{64}$/.test(value.sha256)
  );
}

function parsePendingAction(value: unknown, workspaceId: string): Path2PendingAction | null {
  if (!isRecord(value) || value.workspaceId !== workspaceId || typeof value.kind !== "string" || !PENDING_ACTION_KINDS.includes(value.kind as PendingActionKind)) {
    return null;
  }
  const requestId = storedString(value.requestId);
  const sourceRefs = Array.isArray(value.sourceRefs) && value.sourceRefs.every((item) => isStoredSourceIdentity(item, workspaceId))
    ? value.sourceRefs as SourceIdentity[]
    : null;
  const stateVersion = value.stateVersion === null ? null : storedNonNegativeInteger(value.stateVersion);
  const candidateVersion = value.candidateVersion === null ? null : storedPositiveInteger(value.candidateVersion);
  if (
    !requestId ||
    !sourceRefs ||
    !Object.prototype.hasOwnProperty.call(value, "stateVersion") ||
    !Object.prototype.hasOwnProperty.call(value, "candidateVersion") ||
    (value.stateVersion !== null && stateVersion === null) ||
    (value.candidateVersion !== null && candidateVersion === null) ||
    !Object.prototype.hasOwnProperty.call(value, "missionId") ||
    !Object.prototype.hasOwnProperty.call(value, "attemptId") ||
    !Object.prototype.hasOwnProperty.call(value, "runId") ||
    !Object.prototype.hasOwnProperty.call(value, "clientRequestId") ||
    !Object.prototype.hasOwnProperty.call(value, "intentKey") ||
    !Object.prototype.hasOwnProperty.call(value, "candidateSha256")
  ) {
    return null;
  }
  const missionId = value.missionId === null ? null : storedString(value.missionId);
  const attemptId = value.attemptId === null ? null : storedString(value.attemptId);
  const runId = value.runId === null ? null : storedString(value.runId);
  const clientRequestId = value.clientRequestId === null ? null : storedString(value.clientRequestId);
  const intentKey = value.intentKey === null ? null : storedString(value.intentKey);
  const candidateSha256 = value.candidateSha256 === null ? null : storedString(value.candidateSha256);
  if (
    (value.missionId !== null && !missionId) ||
    (value.attemptId !== null && !attemptId) ||
    (value.runId !== null && !runId) ||
    (value.clientRequestId !== null && !clientRequestId) ||
    (value.intentKey !== null && !intentKey) ||
    (value.candidateSha256 !== null && (!candidateSha256 || !/^[a-f0-9]{64}$/.test(candidateSha256)))
  ) {
    return null;
  }
  return {
    kind: value.kind as PendingActionKind,
    requestId,
    workspaceId,
    missionId,
    attemptId,
    runId,
    clientRequestId,
    intentKey,
    stateVersion,
    candidateVersion,
    candidateSha256,
    sourceRefs,
  };
}

export function readPath2ObjectPointers(workspaceId: string): Path2ObjectPointers {
  if (typeof window === "undefined") {
    return emptyPath2ObjectPointers();
  }
  try {
    const raw = window.sessionStorage.getItem(`${PATH2_OBJECT_POINTERS_PREFIX}${workspaceId}`);
    if (!raw) {
      return emptyPath2ObjectPointers();
    }
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value)) {
      return emptyPath2ObjectPointers();
    }
    const pendingActions: Path2PendingActions = {};
    const storedPendingActions = value.pendingActions;
    if (isRecord(storedPendingActions)) {
      PENDING_ACTION_KINDS.forEach((kind) => {
        const pending = parsePendingAction(storedPendingActions[kind], workspaceId);
        if (pending?.kind === kind) {
          pendingActions[kind] = pending;
        }
      });
    }
    return {
      attemptId: storedString(value.attemptId),
      missionId: storedString(value.missionId),
      runId: storedString(value.runId),
      runMissionId: storedString(value.runMissionId),
      pendingActions,
    };
  } catch {
    return emptyPath2ObjectPointers();
  }
}

export function writePath2ObjectPointers(
  workspaceId: string,
  patch: Partial<Path2ObjectPointers>,
): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    const next = { ...readPath2ObjectPointers(workspaceId), ...patch };
    const serialized = JSON.stringify(next);
    const storageKey = `${PATH2_OBJECT_POINTERS_PREFIX}${workspaceId}`;
    window.sessionStorage.setItem(
      storageKey,
      serialized,
    );
    return window.sessionStorage.getItem(storageKey) === serialized;
  } catch {
    return false;
  }
}

function createPendingAction(
  kind: PendingActionKind,
  workspaceId: string,
  patch: Partial<Omit<Path2PendingAction, "kind" | "requestId" | "workspaceId">> = {},
): Path2PendingAction {
  return {
    kind,
    requestId: createClientRequestId(),
    workspaceId,
    missionId: null,
    attemptId: null,
    runId: null,
    clientRequestId: null,
    intentKey: null,
    stateVersion: null,
    candidateVersion: null,
    candidateSha256: null,
    sourceRefs: [],
    ...patch,
  };
}

function writePendingAction(
  workspaceId: string,
  pending: Path2PendingAction,
  pointerPatch: Partial<Path2ObjectPointers> = {},
): boolean {
  const pointers = readPath2ObjectPointers(workspaceId);
  return writePath2ObjectPointers(workspaceId, {
    pendingActions: { ...pointers.pendingActions, [pending.kind]: pending },
    ...pointerPatch,
  });
}

function clearPendingAction(workspaceId: string, kind: PendingActionKind): boolean {
  const pointers = readPath2ObjectPointers(workspaceId);
  const pendingActions = { ...pointers.pendingActions };
  delete pendingActions[kind];
  return writePath2ObjectPointers(workspaceId, { pendingActions });
}

export function pendingConfirmMatchesIdentity(
  pending: Path2PendingAction | undefined,
  attempt: MissionDraftAttempt | null,
  mission: Mission | null,
): boolean {
  return Boolean(
    pending &&
    pending.kind === "confirm" &&
    attempt &&
    mission &&
    pending.workspaceId === mission.workspace_id &&
    pending.attemptId === attempt.attempt_id &&
    attempt.status === "confirmed" &&
    attempt.mission_id === mission.mission_id &&
    pending.candidateVersion === attempt.candidate_version &&
    pending.candidateSha256 === attempt.candidate_sha256 &&
    sourceIdentityListEquals(pending.sourceRefs, mission.source_refs),
  );
}

function scopeIssue(): ApiIssue {
  return {
    kind: "failed",
    code: "workspace_scope_mismatch",
    message: "服务端响应的 Workspace/Mission/Run 身份不匹配，已拒绝显示。",
  };
}

export type Path2Api = {
  uploadSources: typeof uploadSources;
  fetchSources: typeof fetchSources;
  fetchSourceArtifact: typeof fetchSourceArtifact;
  readSourceExcerpt: typeof readSourceExcerpt;
  createMissionDraftAttempt: typeof createMissionDraftAttempt;
  fetchMissionDraftAttempt: typeof import("./api/client").fetchMissionDraftAttempt;
  confirmMissionDraftAttempt: typeof confirmMissionDraftAttempt;
  fetchMissions: typeof fetchMissions;
  fetchMissionSnapshot: typeof fetchMissionSnapshot;
  startRun: typeof startRun;
  fetchRunSnapshot: typeof fetchRunSnapshot;
  cancelRun: typeof cancelRun;
};

export const productionPath2Api: Path2Api = {
  uploadSources,
  fetchSources,
  fetchSourceArtifact,
  readSourceExcerpt,
  createMissionDraftAttempt,
  fetchMissionDraftAttempt,
  confirmMissionDraftAttempt,
  fetchMissions,
  fetchMissionSnapshot,
  startRun,
  fetchRunSnapshot,
  cancelRun,
};

export type RunEventSource = {
  addEventListener: (type: string, listener: (event: Event) => void) => void;
  removeEventListener: (type: string, listener: (event: Event) => void) => void;
  close: () => void;
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
};

export type RunEventSourceFactory = (url: string) => RunEventSource;

export const browserRunEventSourceFactory: RunEventSourceFactory = (url) => {
  const source = new EventSource(url);
  const listeners = new Map<string, Map<(event: Event) => void, (event: Event) => void>>();
  const wrapped: RunEventSource = {
    addEventListener: (type, listener) => {
      const wrappedListener = (event: Event) => listener(event);
      const typeListeners = listeners.get(type) ?? new Map();
      typeListeners.set(listener, wrappedListener);
      listeners.set(type, typeListeners);
      source.addEventListener(type, wrappedListener);
    },
    removeEventListener: (type, listener) => {
      const typeListeners = listeners.get(type);
      const wrappedListener = typeListeners?.get(listener);
      if (!wrappedListener) {
        return;
      }
      source.removeEventListener(type, wrappedListener);
      typeListeners?.delete(listener);
    },
    close: () => source.close(),
    onopen: null,
    onerror: null,
  };
  source.onopen = (event) => wrapped.onopen?.(event);
  source.onerror = (event) => wrapped.onerror?.(event);
  return wrapped;
};

const RUN_EVENT_TYPES: RunEventEnvelope["event_type"][] = [
  "run_started",
  "message_created",
  "model_started",
  "model_delta",
  "model_completed",
  "tool_requested",
  "tool_started",
  "tool_completed",
  "tool_failed",
  "draft_updated",
  "clarification_requested",
  "run_completed",
  "run_partial",
  "run_blocked",
  "run_failed",
  "run_cancelled",
];

const RUN_EVENT_TYPE_SET = new Set<string>(RUN_EVENT_TYPES);
const DOMAIN_TOOL_NAMES = new Set([
  "list_sources",
  "read_source",
  "inspect_dataset",
  "update_definition_draft",
  "create_clarification",
  "submit_for_review",
  "finish_run",
]);
const TERMINAL_EVENT_STATUS: Record<string, RunSnapshot["status"]> = {
  run_completed: "completed",
  run_partial: "partial",
  run_blocked: "blocked",
  run_failed: "failed",
  run_cancelled: "cancelled",
};

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isOneOf(value: unknown, values: readonly string[]): value is string {
  return typeof value === "string" && values.includes(value);
}

function hasString(record: UnknownRecord, key: string): boolean {
  return typeof record[key] === "string";
}

function hasInteger(record: UnknownRecord, key: string): boolean {
  return isPositiveInteger(record[key]);
}

function isRunEventPayload(eventType: string, payload: unknown): payload is UnknownRecord {
  if (!isRecord(payload)) {
    return false;
  }
  switch (eventType) {
    case "run_started":
      return payload.status === "running";
    case "message_created":
      return hasString(payload, "message_id") && isOneOf(payload.role, ["user", "assistant"]);
    case "model_started":
      return hasInteger(payload, "turn_index");
    case "model_delta":
      return hasInteger(payload, "turn_index") && hasString(payload, "content");
    case "model_completed":
      return hasInteger(payload, "turn_index") && hasString(payload, "provider_receipt_id");
    case "tool_requested":
    case "tool_started":
      return (
        hasString(payload, "call_id") &&
        typeof payload.name === "string" &&
        DOMAIN_TOOL_NAMES.has(payload.name) &&
        hasInteger(payload, "ordinal")
      );
    case "tool_completed":
      return (
        hasString(payload, "call_id") &&
        hasString(payload, "tool_receipt_id") &&
        isOneOf(payload.status, ["succeeded", "rejected"])
      );
    case "tool_failed":
      return hasString(payload, "call_id") && hasString(payload, "error_code");
    case "draft_updated":
      return hasString(payload, "draft_id") && hasInteger(payload, "version") && hasString(payload, "sha256");
    case "clarification_requested":
      return (
        hasString(payload, "clarification_id") &&
        hasInteger(payload, "draft_version") &&
        hasString(payload, "draft_sha256")
      );
    case "run_completed":
    case "run_partial":
    case "run_blocked":
    case "run_failed":
    case "run_cancelled":
      return (
        payload.status === TERMINAL_EVENT_STATUS[eventType] &&
        isNullableString(payload.terminal_receipt_id) &&
        isNullableString(payload.error_code)
      );
    default:
      return false;
  }
}

export function isRunEventEnvelope(value: unknown): value is RunEventEnvelope {
  if (!isRecord(value)) {
    return false;
  }
  const eventType = value.event_type;
  if (
    !hasString(value, "event_id") ||
    typeof eventType !== "string" ||
    !RUN_EVENT_TYPE_SET.has(eventType) ||
    !hasString(value, "occurred_at") ||
    !hasString(value, "workspace_id") ||
    !hasString(value, "mission_id") ||
    !hasString(value, "run_id") ||
    !hasInteger(value, "sequence")
  ) {
    return false;
  }
  return isRunEventPayload(eventType, value.public_payload);
}

export function parseRunEvent(data: string): RunEventEnvelope | null {
  try {
    const value: unknown = JSON.parse(data);
    return isRunEventEnvelope(value) ? value : null;
  } catch {
    return null;
  }
}

export type RunIdentity = {
  workspaceId: string;
  missionId: string;
  runId: string;
};

export type RunEventState = {
  events: RunEventEnvelope[];
  lastSequence: number;
  hasSequenceGap: boolean;
};

export function createRunEventState(lastSequence = 0): RunEventState {
  return { events: [], lastSequence, hasSequenceGap: false };
}

export function acceptRunEvent(
  state: RunEventState,
  event: RunEventEnvelope,
  identity: RunIdentity,
): RunEventState {
  if (
    event.workspace_id !== identity.workspaceId ||
    event.mission_id !== identity.missionId ||
    event.run_id !== identity.runId ||
    event.sequence <= state.lastSequence ||
    state.events.some((current) => current.event_id === event.event_id)
  ) {
    return state;
  }
  const hasGap = event.sequence > state.lastSequence + 1;
  return {
    events: [...state.events, event].sort((left, right) => left.sequence - right.sequence),
    lastSequence: event.sequence,
    hasSequenceGap: state.hasSequenceGap || hasGap,
  };
}

export function mergeRunSnapshot(state: RunEventState, snapshot: RunSnapshot): RunEventState {
  return {
    ...state,
    lastSequence: Math.max(state.lastSequence, snapshot.last_sequence),
    hasSequenceGap:
      state.hasSequenceGap ||
      (state.events.length > 0 && snapshot.last_sequence > state.lastSequence + 1),
  };
}

export type ApiIssue = {
  kind: "blocked" | "failed" | "unknown";
  code: string | null;
  message: string;
};

export function pendingActionIssue(kind: PendingActionKind): ApiIssue {
  const labels: Record<PendingActionKind, string> = {
    upload: "来源导入",
    attempt: "Attempt 创建",
    confirm: "草案确认",
    run_start: "Run Start",
    cancel: "Run 取消",
  };
  return {
    kind: "unknown",
    code: "pending_action_unknown",
    message: `${labels[kind]} 上一次请求的结果未知；当前仅保留阻塞状态，必须用精确对象回读或人工核对，不能恢复确认或自动重试。`,
  };
}

function pendingActionStorageIssue(): ApiIssue {
  return {
    kind: "blocked",
    code: "pending_action_persistence_unavailable",
    message: "无法在当前 tab 保存待处理动作标记，未发送请求；请恢复本地存储后再试。",
  };
}

export function issueFromError(error: unknown): ApiIssue {
  if (error instanceof ApiRequestError) {
    if (error.code === "path2_not_implemented") {
      return {
        kind: "blocked",
        code: error.code,
        message: "Path 2 当前不可用：W0.2 只提供共享契约接缝，尚未接入来源、Mission 或 Run。",
      };
    }
    if (error.code) {
      return { kind: "failed", code: error.code, message: error.message };
    }
  }
  return {
    kind: "unknown",
    code: null,
    message: "请求结果无法确认，操作已阻塞；未自动重发。",
  };
}

export async function executeExplicitRequest<T>(operation: () => Promise<T>): Promise<T> {
  return operation();
}

export type CollectionStatus = "idle" | "loading" | "ready" | "empty" | "blocked" | "failed" | "unknown";

export type CollectionState<T> = {
  status: CollectionStatus;
  items: T[];
  issue: ApiIssue | null;
};

export type ActionStatus = "idle" | "submitting" | "success" | "blocked" | "failed" | "unknown";

export type ActionState = {
  status: ActionStatus;
  issue: ApiIssue | null;
};

export type SourceArtifactState = {
  status: CollectionStatus;
  artifact: SourceArtifact | null;
  excerpt: SourceExcerpt | null;
  issue: ApiIssue | null;
};

function emptyCollection<T>(): CollectionState<T> {
  return { status: "idle", items: [], issue: null };
}

function loadingCollection<T>(): CollectionState<T> {
  return { status: "loading", items: [], issue: null };
}

function emptyAction(): ActionState {
  return { status: "idle", issue: null };
}

function actionForIssue(issue: ApiIssue): ActionState {
  return { status: issue.kind, issue };
}

function collectionForError<T>(error: unknown): CollectionState<T> {
  const issue = issueFromError(error);
  return { status: issue.kind, items: [], issue };
}

export function sourceIdentityFromRevision(revision: SourceRevision): components["schemas"]["SourceIdentity"] {
  return {
    workspace_id: revision.workspace_id,
    source_id: revision.source_id,
    revision_id: revision.revision_id,
    sha256: revision.sha256,
  };
}

export function buildSourceUploadRequest(
  files: SourceUploadFile[],
  localReadConfirmed: boolean,
): SourceUploadRequest {
  return { files, local_read_confirmed: localReadConfirmed };
}

export function buildConfirmRequest(
  attempt: MissionDraftAttempt,
  sourceRefs: components["schemas"]["SourceIdentity"][],
): MissionDraftConfirmRequest | null {
  if (
    attempt.candidate_version === null ||
    attempt.candidate_sha256 === null ||
    attempt.candidate === null
  ) {
    return null;
  }
  return {
    candidate_version: attempt.candidate_version,
    candidate_sha256: attempt.candidate_sha256,
    source_refs: sourceRefs,
  };
}

export function buildRunStartRequest(
  mission: Mission,
  sourceRefs: components["schemas"]["SourceIdentity"][],
  providerSendConfirmed: boolean,
  clientRequestId: string,
): RunStartRequest {
  return {
    expected_state_version: mission.state_version,
    source_refs: sourceRefs,
    provider_send_confirmed: providerSendConfirmed,
    client_request_id: clientRequestId,
  };
}

export function createClientRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const random = () => Math.floor(Math.random() * 16).toString(16);
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
    const value = Number.parseInt(random(), 16);
    const next = character === "x" ? value : (value & 0x3) | 0x8;
    return next.toString(16);
  });
}

export function defaultExcerptRequest(revision: SourceRevision): SourceExcerptRequest {
  if (revision.media_type === "text/csv") {
    return { locator: { kind: "csv_rows", row_start: 1, row_end: 5, column: null } };
  }
  if (revision.media_type === "application/json") {
    return { locator: { kind: "json_pointer", pointer: "" } };
  }
  return { locator: { kind: "text_lines", line_start: 1, line_end: 10 } };
}

export type Path2WorkbenchState = {
  workspaceId: string | null;
  sourceState: CollectionState<SourceRevision>;
  sourceArtifacts: Record<string, SourceArtifactState>;
  selectedSourceIds: string[];
  selectedSourceRefs: components["schemas"]["SourceIdentity"][];
  selectSource: (revisionId: string) => void;
  toggleSource: (revisionId: string) => void;
  loadSourceArtifact: (revisionId: string) => Promise<void>;
  readSourceExcerpt: (revisionId: string) => Promise<void>;
  refreshSources: () => Promise<void>;
  uploadState: ActionState & { result: SourceBatchResult | null };
  uploadSourceBatch: (request: SourceUploadRequest) => Promise<SourceBatchResult | null>;
  acknowledgeUploadUnknown: () => void;
  missionState: CollectionState<Mission>;
  selectedMission: Mission | null;
  missionSnapshot: MissionSnapshot | null;
  missionSnapshotState: CollectionState<Mission>;
  refreshMission: () => Promise<void>;
  attempt: MissionDraftAttempt | null;
  attemptAction: ActionState;
  submitAttempt: (request: MissionDraftAttemptCreateRequest) => Promise<MissionDraftAttempt | null>;
  reconcileAttempt: () => Promise<void>;
  acknowledgeAttemptUnknown: () => void;
  confirmAction: ActionState;
  confirmAttempt: () => Promise<Mission | null>;
  acknowledgeConfirmUnknown: () => void;
  runSnapshot: RunSnapshot | null;
  runAction: ActionState;
  startRun: (providerSendConfirmed: boolean) => Promise<RunSnapshot | null>;
  acknowledgeRunUnknown: () => void;
  cancelAction: ActionState;
  cancelActiveRun: () => Promise<RunSnapshot | null>;
  reconcileRun: () => Promise<void>;
  acknowledgeCancelUnknown: () => void;
  runConnectionState: "idle" | "connecting" | "connected" | "reconnecting" | "blocked" | "closed";
  runReadbackIssue: ApiIssue | null;
  runEventIssue: string | null;
  runEventState: RunEventState;
  latestDraft: DefinitionDraft | null;
  clarifications: ClarificationRequest[];
};

export type WorkbenchSurfaceSummary = {
  status: CollectionStatus | "partial";
  label: string;
};

export function workbenchSurfaceSummary(state: Pick<
  Path2WorkbenchState,
  "workspaceId" | "sourceState" | "missionState" | "missionSnapshotState"
>): WorkbenchSurfaceSummary {
  if (!state.workspaceId) {
    return { status: "empty", label: "未选择 Workspace" };
  }
  const statuses = [state.sourceState.status, state.missionState.status, state.missionSnapshotState.status];
  if (statuses.includes("unknown")) {
    return { status: "unknown", label: "结果未知，已阻塞" };
  }
  if (statuses.includes("blocked")) {
    return { status: "blocked", label: "当前能力已阻塞" };
  }
  if (statuses.includes("failed")) {
    return { status: "failed", label: "读取失败" };
  }
  if (statuses.includes("loading")) {
    return { status: "loading", label: "正在回读" };
  }
  if (statuses.every((status) => status === "empty" || status === "idle")) {
    return { status: "empty", label: "已回读，暂无对象" };
  }
  if (statuses.includes("empty")) {
    return { status: "partial", label: "部分对象已回读" };
  }
  return { status: "ready", label: "来源与 Mission 已回读" };
}

const ATTEMPT_POLL_LIMIT = 5;
const ATTEMPT_POLL_DELAY_MS = 180;

const ATTEMPT_STATUS_ORDER: Record<MissionDraftAttempt["status"], number> = {
  queued: 0,
  running: 1,
  ready: 2,
  confirmed: 3,
  blocked: 3,
  failed: 3,
  cancelled: 3,
};

export function missionDraftAttemptIsMonotonic(
  current: MissionDraftAttempt,
  next: MissionDraftAttempt,
): boolean {
  if (
    current.workspace_id !== next.workspace_id ||
    current.attempt_id !== next.attempt_id ||
    current.original_input !== next.original_input ||
    ATTEMPT_STATUS_ORDER[next.status] < ATTEMPT_STATUS_ORDER[current.status] ||
    (current.candidate !== null && next.candidate === null) ||
    (current.candidate_version !== null && next.candidate_version === null) ||
    (current.candidate_sha256 !== null && next.candidate_sha256 === null) ||
    (current.provider_receipt_id !== null && next.provider_receipt_id === null) ||
    (current.mission_id !== null && next.mission_id === null)
  ) {
    return false;
  }
  if (current.status === "confirmed" && next.status !== "confirmed") {
    return false;
  }
  if (["blocked", "failed", "cancelled"].includes(current.status) && next.status !== current.status) {
    return false;
  }
  if (current.status === "ready" && next.status !== "ready" && next.status !== "confirmed") {
    return false;
  }
  if (
    current.candidate_version !== null &&
    next.candidate_version !== null &&
    current.candidate_version !== next.candidate_version
  ) {
    return false;
  }
  if (
    current.candidate_sha256 !== null &&
    next.candidate_sha256 !== null &&
    current.candidate_sha256 !== next.candidate_sha256
  ) {
    return false;
  }
  return true;
}

function attemptActionForResult(result: MissionDraftAttempt): ActionState {
  if (result.status === "blocked") {
    return {
      status: "blocked",
      issue: result.error_code
        ? { kind: "blocked", code: result.error_code, message: result.error_code }
        : null,
    };
  }
  if (result.status === "failed" || result.status === "cancelled") {
    return {
      status: "failed",
      issue: result.error_code
        ? { kind: "failed", code: result.error_code, message: result.error_code }
        : null,
    };
  }
  return { status: "success", issue: null };
}

function staleRunSnapshotIssue(): ApiIssue {
  return {
    kind: "failed",
    code: "stale_run_snapshot",
    message: "收到较旧或已回退的 Run 快照，已保留当前较新状态。",
  };
}

function staleAttemptIssue(): ApiIssue {
  return {
    kind: "failed",
    code: "stale_attempt",
    message: "收到较旧或已回退的 Attempt 快照，已保留当前较新状态。",
  };
}

function selectionChangedIssue(objectName: string): ApiIssue {
  return {
    kind: "failed",
    code: "selection_changed",
    message: `当前 ${objectName} 或来源选择已变化，旧请求结果已拒绝显示。`,
  };
}

export function usePath2Workbench(
  workspace: Workspace | null,
  api: Path2Api = productionPath2Api,
  eventSourceFactory: RunEventSourceFactory = browserRunEventSourceFactory,
): Path2WorkbenchState {
  const workspaceId = workspace?.workspace_id ?? null;
  const epochRef = useRef(0);
  const operationGenerationsRef = useRef<OperationGenerations>({});
  const workspaceIdRef = useRef<string | null>(workspaceId);
  const selectedMissionIdRef = useRef<string | null>(null);
  const selectedSourceRefsRef = useRef<SourceIdentity[]>([]);
  const attemptRef = useRef<MissionDraftAttempt | null>(null);
  const pendingAttemptIdRef = useRef<string | null>(null);
  const missionSnapshotRef = useRef<MissionSnapshot | null>(null);
  const clientRequestIdRef = useRef<string | null>(null);
  const startRequestKeyRef = useRef<string | null>(null);
  const preferredRunIdRef = useRef<string | null>(null);
  const preferredRunMissionIdRef = useRef<string | null>(null);
  const runRef = useRef<RunSnapshot | null>(null);
  const pendingRunIdRef = useRef<string | null>(null);
  const runEventStateRef = useRef<RunEventState>(createRunEventState());
  const [loadedWorkspaceId, setLoadedWorkspaceId] = useState<string | null>(workspaceId);
  const [sourceState, setSourceState] = useState<CollectionState<SourceRevision>>(emptyCollection);
  const [sourceArtifacts, setSourceArtifacts] = useState<Record<string, SourceArtifactState>>({});
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [uploadState, setUploadState] = useState<ActionState & { result: SourceBatchResult | null }>({
    ...emptyAction(),
    result: null,
  });
  const [missionState, setMissionState] = useState<CollectionState<Mission>>(emptyCollection);
  const [selectedMissionId, setSelectedMissionId] = useState<string | null>(null);
  const [missionSnapshot, setMissionSnapshot] = useState<MissionSnapshot | null>(null);
  const [missionSnapshotState, setMissionSnapshotState] =
    useState<CollectionState<Mission>>(emptyCollection);
  const [attempt, setAttempt] = useState<MissionDraftAttempt | null>(null);
  const [attemptAction, setAttemptAction] = useState<ActionState>(emptyAction);
  const [confirmAction, setConfirmAction] = useState<ActionState>(emptyAction);
  const [runSnapshot, setRunSnapshot] = useState<RunSnapshot | null>(null);
  const [runAction, setRunAction] = useState<ActionState>(emptyAction);
  const [cancelAction, setCancelAction] = useState<ActionState>(emptyAction);
  const [runConnectionState, setRunConnectionState] = useState<
    "idle" | "connecting" | "connected" | "reconnecting" | "blocked" | "closed"
  >("idle");
  const [runReadbackIssue, setRunReadbackIssue] = useState<ApiIssue | null>(null);
  const [runEventIssue, setRunEventIssue] = useState<string | null>(null);
  const [runEventState, setRunEventState] = useState<RunEventState>(createRunEventState);

  const uploadStateRef = useRef(uploadState);
  const attemptActionRef = useRef(attemptAction);
  const confirmActionRef = useRef(confirmAction);
  const runActionRef = useRef(runAction);
  const cancelActionRef = useRef(cancelAction);
  const setUploadStateNow = useCallback((next: typeof uploadState): void => {
    uploadStateRef.current = next;
    setUploadState(next);
  }, []);
  const setAttemptActionNow = useCallback((next: ActionState): void => {
    attemptActionRef.current = next;
    setAttemptAction(next);
  }, []);
  const setConfirmActionNow = useCallback((next: ActionState): void => {
    confirmActionRef.current = next;
    setConfirmAction(next);
  }, []);
  const setRunActionNow = useCallback((next: ActionState): void => {
    runActionRef.current = next;
    setRunAction(next);
  }, []);
  const setCancelActionNow = useCallback((next: ActionState): void => {
    cancelActionRef.current = next;
    setCancelAction(next);
  }, []);
  workspaceIdRef.current = workspaceId;
  selectedMissionIdRef.current = selectedMissionId;
  uploadStateRef.current = uploadState;
  attemptActionRef.current = attemptAction;
  confirmActionRef.current = confirmAction;
  runActionRef.current = runAction;
  cancelActionRef.current = cancelAction;

  const beginOperation = useCallback((kind: OperationKind, scope: OperationScope): OperationToken => {
    const generation = (operationGenerationsRef.current[kind] ?? 0) + 1;
    operationGenerationsRef.current[kind] = generation;
    return { ...scope, kind, generation, epoch: epochRef.current };
  }, []);

  const invalidateOperation = useCallback((kind: OperationKind): void => {
    operationGenerationsRef.current[kind] = (operationGenerationsRef.current[kind] ?? 0) + 1;
  }, []);

  const isOperationCurrent = useCallback((token: OperationToken): boolean => {
    return isCurrentOperation(
      token,
      epochRef.current,
      operationGenerationsRef.current,
      {
        workspaceId: workspaceIdRef.current ?? "",
        missionId: selectedMissionIdRef.current,
        attemptId: attemptRef.current?.attempt_id ?? pendingAttemptIdRef.current,
        runId: runRef.current?.run_id ?? pendingRunIdRef.current,
      },
    );
  }, []);

  const replaceRunEventState = useCallback((next: RunEventState): void => {
    runEventStateRef.current = next;
    setRunEventState(next);
  }, []);

  const updateRunEventState = useCallback((updater: (current: RunEventState) => RunEventState): RunEventState => {
    const next = updater(runEventStateRef.current);
    runEventStateRef.current = next;
    setRunEventState(next);
    return next;
  }, []);

  const storeAttempt = useCallback((next: MissionDraftAttempt | null): void => {
    attemptRef.current = next;
    setAttempt(next);
  }, []);

  const storeMissionSnapshot = useCallback((next: MissionSnapshot | null): void => {
    missionSnapshotRef.current = next;
    setMissionSnapshot(next);
  }, []);

  const storeRunSnapshot = useCallback((next: RunSnapshot | null): void => {
    runRef.current = next;
    setRunSnapshot(next);
  }, []);

  const applyRunSnapshot = useCallback((next: RunSnapshot, identity: RunIdentity): boolean => {
    if (!runSnapshotMatchesIdentity(next, identity)) {
      setRunReadbackIssue(scopeIssue());
      return false;
    }
    const current = runRef.current;
    if (current && current.run_id !== identity.runId) {
      return false;
    }
    if (current && !runSnapshotIsMonotonic(current, next)) {
      setRunReadbackIssue(staleRunSnapshotIssue());
      return false;
    }
    pendingRunIdRef.current = null;
    storeRunSnapshot(next);
    updateRunEventState((currentState) => mergeRunSnapshot(currentState, next));
    setRunReadbackIssue(null);
    return true;
  }, [storeRunSnapshot, updateRunEventState]);

  const loadMissionSnapshot = useCallback(
    async (currentWorkspaceId: string, missionId: string): Promise<MissionSnapshot | null> => {
      const token = beginOperation("mission_snapshot", {
        workspaceId: currentWorkspaceId,
        missionId,
      });
      try {
        const next = await executeExplicitRequest(() => api.fetchMissionSnapshot(currentWorkspaceId, missionId));
        if (!isOperationCurrent(token)) {
          return null;
        }
        if (!missionSnapshotMatchesIdentity(next, currentWorkspaceId, missionId)) {
          setMissionSnapshotState({ status: "failed", items: [], issue: scopeIssue() });
          return null;
        }
        const pendingRunStart = readPath2ObjectPointers(currentWorkspaceId).pendingActions.run_start;
        const runStartOutcomeUnknown = pendingRunStart?.missionId === missionId;
        const nextForDisplay = runStartOutcomeUnknown
          ? { ...next, draft: null, clarifications: [], latest_run: null }
          : next;
        const current = missionSnapshotRef.current;
        if (
          current &&
          current.mission.mission_id === missionId &&
          !missionSnapshotIsMonotonic(current, nextForDisplay)
        ) {
          setMissionSnapshotState({ status: "ready", items: [current.mission], issue: null });
          return current;
        }
        storeMissionSnapshot(nextForDisplay);
        setMissionSnapshotState({ status: "ready", items: [nextForDisplay.mission], issue: null });
        const preferredRunId =
          preferredRunMissionIdRef.current === missionId ? preferredRunIdRef.current : null;
        if (
          next.latest_run &&
          !runStartOutcomeUnknown &&
          (!preferredRunId || next.latest_run.run_id === preferredRunId)
        ) {
          const applied = applyRunSnapshot(next.latest_run, {
            workspaceId: currentWorkspaceId,
            missionId,
            runId: next.latest_run.run_id,
          });
          if (applied) {
            writePath2ObjectPointers(currentWorkspaceId, {
              missionId,
              runId: next.latest_run.run_id,
              runMissionId: missionId,
            });
            preferredRunIdRef.current = next.latest_run.run_id;
            preferredRunMissionIdRef.current = missionId;
          }
        }
        return nextForDisplay;
      } catch (error: unknown) {
        if (isOperationCurrent(token)) {
          setMissionSnapshotState(collectionForError(error));
        }
        return null;
      }
    }, [api, applyRunSnapshot, beginOperation, isOperationCurrent, storeMissionSnapshot]);

  const refreshSources = useCallback(async (): Promise<void> => {
    if (!workspaceId) {
      return;
    }
    const currentWorkspaceId = workspaceId;
    const token = beginOperation("sources", { workspaceId: currentWorkspaceId });
    invalidateOperation("artifact");
    invalidateOperation("excerpt");
    setSourceState(loadingCollection());
    setSourceArtifacts({});
    try {
      const items = await executeExplicitRequest(() => api.fetchSources(currentWorkspaceId));
      if (!isOperationCurrent(token)) {
        return;
      }
      if (!items.every((item) => sourceRevisionMatchesWorkspace(item, currentWorkspaceId))) {
        setSourceState({ status: "failed", items: [], issue: scopeIssue() });
        return;
      }
      setSourceState({ status: items.length > 0 ? "ready" : "empty", items, issue: null });
    } catch (error: unknown) {
      if (isOperationCurrent(token)) {
        setSourceState(collectionForError(error));
      }
    }
  }, [api, beginOperation, invalidateOperation, isOperationCurrent, workspaceId]);

  const readRunSnapshot = useCallback(async (identity: RunIdentity): Promise<RunSnapshot | null> => {
    pendingRunIdRef.current = identity.runId;
    const token = beginOperation("run_snapshot", identity);
    try {
      const next = await executeExplicitRequest(() =>
        api.fetchRunSnapshot(identity.workspaceId, identity.missionId, identity.runId),
      );
      if (!isOperationCurrent(token)) {
        return null;
      }
      if (runRef.current && runRef.current.run_id !== identity.runId) {
        return null;
      }
      if (!applyRunSnapshot(next, identity)) {
        return null;
      }
      return next;
    } catch (error: unknown) {
      if (isOperationCurrent(token)) {
        setRunReadbackIssue(issueFromError(error));
      }
      return null;
    }
  }, [api, applyRunSnapshot, beginOperation, isOperationCurrent]);

  const readAttempt = useCallback(
    async (
      currentWorkspaceId: string,
      attemptId: string,
      expectedOriginalInput: string | undefined,
      token: OperationToken,
    ): Promise<MissionDraftAttempt | null> => {
      let latest: MissionDraftAttempt | null = null;
      for (let poll = 0; poll < ATTEMPT_POLL_LIMIT; poll += 1) {
        if (!isOperationCurrent(token)) {
          return null;
        }
        try {
          latest = await executeExplicitRequest(() =>
            api.fetchMissionDraftAttempt(currentWorkspaceId, attemptId),
          );
        } catch (error: unknown) {
          if (isOperationCurrent(token)) {
            setAttemptActionNow(actionForIssue(issueFromError(error)));
          }
          return null;
        }
        if (!isOperationCurrent(token)) {
          return null;
        }
        if (!missionDraftAttemptMatchesIdentity(latest, currentWorkspaceId, attemptId, expectedOriginalInput)) {
          setAttemptActionNow(actionForIssue(scopeIssue()));
          return null;
        }
        if (
          attemptRef.current &&
          attemptRef.current.attempt_id === attemptId &&
          !missionDraftAttemptIsMonotonic(attemptRef.current, latest)
        ) {
          setAttemptActionNow(actionForIssue(staleAttemptIssue()));
          return attemptRef.current;
        }
        storeAttempt(latest);
        pendingAttemptIdRef.current = null;
        writePath2ObjectPointers(currentWorkspaceId, { attemptId });
        setAttemptActionNow(attemptActionForResult(latest));
        if (latest.status !== "queued" && latest.status !== "running") {
          return latest;
        }
        if (poll + 1 < ATTEMPT_POLL_LIMIT) {
          await new Promise<void>((resolve) => setTimeout(resolve, ATTEMPT_POLL_DELAY_MS));
        }
      }
      return latest;
    }, [api, isOperationCurrent, storeAttempt]);

  const refreshMissions = useCallback(async (): Promise<void> => {
    if (!workspaceId) {
      return;
    }
    const currentWorkspaceId = workspaceId;
    const token = beginOperation("missions", { workspaceId: currentWorkspaceId });
    setMissionState(loadingCollection());
    try {
      const items = await executeExplicitRequest(() => api.fetchMissions(currentWorkspaceId));
      if (!isOperationCurrent(token)) {
        return;
      }
      if (!items.every((item) => missionMatchesWorkspace(item, currentWorkspaceId))) {
        const failed = { status: "failed" as const, items: [], issue: scopeIssue() };
        setMissionState(failed);
        setMissionSnapshotState(failed);
        return;
      }
      setMissionState({ status: items.length > 0 ? "ready" : "empty", items, issue: null });
      if (items.length === 0) {
        selectedMissionIdRef.current = null;
        setSelectedMissionId(null);
        storeMissionSnapshot(null);
        setMissionSnapshotState({ status: "empty", items: [], issue: null });
        return;
      }

      const pointers = readPath2ObjectPointers(currentWorkspaceId);
      const currentAttemptId = attemptRef.current?.attempt_id ?? pointers.attemptId;
      const confirmedMission = currentAttemptId
        ? items.find((item) => item.original_attempt_id === currentAttemptId) ?? null
        : null;
      const pointerMission = pointers.missionId
        ? items.find((item) => item.mission_id === pointers.missionId) ?? null
        : null;
      if (pointers.missionId && !pointerMission && !confirmedMission) {
        setSelectedMissionId(null);
        selectedMissionIdRef.current = null;
        storeMissionSnapshot(null);
        setMissionSnapshotState({
          status: "failed",
          items: [],
          issue: {
            kind: "failed",
            code: "persisted_mission_not_found",
            message: "当前 tab 保存的 Mission ID 不在该 Workspace 的回读列表中，未静默切换到其它 Mission。",
          },
        });
        return;
      }
      const selected = confirmedMission ?? pointerMission ?? items[0];
      const previousMissionId = selectedMissionIdRef.current;
      selectedMissionIdRef.current = selected.mission_id;
      setSelectedMissionId(selected.mission_id);
      setMissionSnapshotState({ status: "loading", items: [selected], issue: null });
      const missionChanged = previousMissionId !== null && previousMissionId !== selected.mission_id;
      if (missionChanged) {
        storeMissionSnapshot(null);
        storeRunSnapshot(null);
        replaceRunEventState(createRunEventState());
        setRunReadbackIssue(null);
      }
      const persistedRunIsForMission =
        pointers.runId && pointers.runMissionId === selected.mission_id;
      preferredRunIdRef.current = persistedRunIsForMission ? pointers.runId : null;
      preferredRunMissionIdRef.current = persistedRunIsForMission ? selected.mission_id : null;
      if (pointers.runId && !pointers.runMissionId) {
        setRunReadbackIssue({
          kind: "failed",
          code: "persisted_run_scope_missing",
          message: "当前 tab 保存的 Run ID 缺少 Mission 绑定，未读取该 ID。",
        });
      } else if (pointers.runId && !persistedRunIsForMission) {
        setRunReadbackIssue({
          kind: "failed",
          code: "persisted_run_scope_mismatch",
          message: "当前 tab 保存的 Run 不属于选中的 Mission，未显示该 Run。",
        });
      }
      await loadMissionSnapshot(currentWorkspaceId, selected.mission_id);
      if (!isOperationCurrent(token)) {
        return;
      }
      if (persistedRunIsForMission) {
        await readRunSnapshot({
          workspaceId: currentWorkspaceId,
          missionId: selected.mission_id,
          runId: pointers.runId as string,
        });
        if (!isOperationCurrent(token)) {
          return;
        }
      }
      const pendingConfirm = pointers.pendingActions.confirm;
      if (
        pendingConfirm &&
        currentAttemptId &&
        attemptRef.current?.attempt_id !== currentAttemptId
      ) {
        pendingAttemptIdRef.current = currentAttemptId;
        const attemptToken = beginOperation("attempt", {
          workspaceId: currentWorkspaceId,
          attemptId: currentAttemptId,
        });
        await readAttempt(currentWorkspaceId, currentAttemptId, undefined, attemptToken);
        if (!isOperationCurrent(token)) {
          return;
        }
      }
      if (
        confirmedMission &&
        pendingConfirmMatchesIdentity(pendingConfirm, attemptRef.current, confirmedMission) &&
        confirmActionRef.current.status === "unknown"
      ) {
        clearPendingAction(currentWorkspaceId, "confirm");
        const nextAction: ActionState = { status: "success", issue: null };
        confirmActionRef.current = nextAction;
        setConfirmActionNow(nextAction);
        storeAttempt({ ...attemptRef.current!, status: "confirmed", mission_id: confirmedMission.mission_id });
      }
    } catch (error: unknown) {
      if (isOperationCurrent(token)) {
        const failed = collectionForError<Mission>(error);
        setMissionState(failed);
        setMissionSnapshotState(failed);
      }
    }
  }, [
    api,
    beginOperation,
    isOperationCurrent,
    loadMissionSnapshot,
    readAttempt,
    readRunSnapshot,
    replaceRunEventState,
    storeMissionSnapshot,
    storeRunSnapshot,
    workspaceId,
  ]);

  useEffect(() => {
    const epoch = epochRef.current + 1;
    epochRef.current = epoch;
    operationGenerationsRef.current = {};
    const pointers = workspaceId ? readPath2ObjectPointers(workspaceId) : emptyPath2ObjectPointers();
    const pendingActions = pointers.pendingActions;
    const pendingRunStart = pendingActions.run_start;
    preferredRunIdRef.current = pointers.runId;
    preferredRunMissionIdRef.current = pointers.runMissionId;
    setLoadedWorkspaceId(workspaceId);
    clientRequestIdRef.current = pendingRunStart?.clientRequestId ?? null;
    startRequestKeyRef.current = pendingRunStart?.intentKey ?? null;
    attemptRef.current = null;
    missionSnapshotRef.current = null;
    runRef.current = null;
    pendingRunIdRef.current = null;
    pendingAttemptIdRef.current = null;
    selectedMissionIdRef.current = null;
    selectedSourceRefsRef.current = [];
    runEventStateRef.current = createRunEventState();
    setSourceState(workspaceId ? loadingCollection() : emptyCollection());
    setSourceArtifacts({});
    setSelectedSourceIds([]);
    setUploadStateNow(
      pendingActions.upload
        ? { ...actionForIssue(pendingActionIssue("upload")), result: null }
        : { ...emptyAction(), result: null },
    );
    setMissionState(workspaceId ? loadingCollection() : emptyCollection());
    setSelectedMissionId(null);
    setMissionSnapshot(null);
    setMissionSnapshotState(workspaceId ? loadingCollection() : emptyCollection());
    setAttempt(null);
    setAttemptActionNow(pendingActions.attempt ? actionForIssue(pendingActionIssue("attempt")) : emptyAction());
    setConfirmActionNow(pendingActions.confirm ? actionForIssue(pendingActionIssue("confirm")) : emptyAction());
    setRunSnapshot(null);
    setRunActionNow(pendingRunStart ? actionForIssue(pendingActionIssue("run_start")) : emptyAction());
    setCancelActionNow(pendingActions.cancel ? actionForIssue(pendingActionIssue("cancel")) : emptyAction());
    setRunReadbackIssue(null);
    setRunEventIssue(null);
    setRunEventState(createRunEventState());
    setRunConnectionState(workspaceId ? "connecting" : "idle");

    if (!workspaceId) {
      return () => {
        epochRef.current += 1;
      };
    }

    void refreshSources();
    void refreshMissions();
    if (pointers.attemptId && !pendingActions.attempt) {
      pendingAttemptIdRef.current = pointers.attemptId;
      const token = beginOperation("attempt", {
        workspaceId,
        attemptId: pointers.attemptId,
      });
      void readAttempt(workspaceId, pointers.attemptId, undefined, token);
    }

    return () => {
      epochRef.current += 1;
    };
  }, [beginOperation, readAttempt, refreshMissions, refreshSources, workspaceId]);

  useEffect(() => {
    const pendingRunStart = workspaceId
      ? readPath2ObjectPointers(workspaceId).pendingActions.run_start
      : null;
    if (pendingRunStart && (!selectedMissionId || pendingRunStart.missionId === selectedMissionId)) {
      clientRequestIdRef.current = pendingRunStart.clientRequestId;
      startRequestKeyRef.current = pendingRunStart.intentKey;
      return;
    }
    clientRequestIdRef.current = null;
    startRequestKeyRef.current = null;
  }, [selectedMissionId, workspaceId]);

  const selectedMission = useMemo(
    () =>
      missionSnapshot?.mission ??
      (selectedMissionId
        ? missionState.items.find((candidate) => candidate.mission_id === selectedMissionId) ?? null
        : null),
    [missionSnapshot, missionState.items, selectedMissionId],
  );

  const selectedSourceRefs = useMemo(
    () =>
      sourceState.items
        .filter((revision) => selectedSourceIds.includes(revision.revision_id))
        .map(sourceIdentityFromRevision),
    [selectedSourceIds, sourceState.items],
  );
  selectedSourceRefsRef.current = selectedSourceRefs;

  const selectSource = useCallback((revisionId: string) => {
    setSelectedSourceIds((current) => (current.includes(revisionId) ? current : [...current, revisionId]));
  }, []);

  const toggleSource = useCallback((revisionId: string) => {
    setSelectedSourceIds((current) =>
      current.includes(revisionId)
        ? current.filter((currentId) => currentId !== revisionId)
        : [...current, revisionId],
    );
  }, []);

  const loadSourceArtifact = useCallback(
    async (revisionId: string): Promise<void> => {
      if (!workspaceId) {
        return;
      }
      const currentWorkspaceId = workspaceId;
      const revision = sourceState.items.find((candidate) => candidate.revision_id === revisionId);
      if (!revision) {
        return;
      }
      const token = beginOperation("artifact", { workspaceId: currentWorkspaceId });
      setSourceArtifacts((current) => ({
        ...current,
        [revisionId]: { status: "loading", artifact: null, excerpt: null, issue: null },
      }));
      try {
        const artifact = await executeExplicitRequest(() =>
          api.fetchSourceArtifact(currentWorkspaceId, revisionId),
        );
        if (!isOperationCurrent(token)) {
          return;
        }
        if (
          artifact.source_ref.workspace_id !== currentWorkspaceId ||
          artifact.source_ref.revision_id !== revisionId ||
          artifact.source_ref.source_id !== revision.source_id ||
          artifact.source_ref.sha256 !== revision.sha256
        ) {
          setSourceArtifacts((current) => ({
            ...current,
            [revisionId]: { status: "failed", artifact: null, excerpt: null, issue: scopeIssue() },
          }));
          return;
        }
        setSourceArtifacts((current) => ({
          ...current,
          [revisionId]: { status: "ready", artifact, excerpt: null, issue: null },
        }));
      } catch (error: unknown) {
        if (isOperationCurrent(token)) {
          const issue = issueFromError(error);
          setSourceArtifacts((current) => ({
            ...current,
            [revisionId]: { status: issue.kind, artifact: null, excerpt: null, issue },
          }));
        }
      }
    }, [api, beginOperation, isOperationCurrent, sourceState.items, workspaceId],
  );

  const readExcerpt = useCallback(
    async (revisionId: string): Promise<void> => {
      if (!workspaceId) {
        return;
      }
      const currentWorkspaceId = workspaceId;
      const revision = sourceState.items.find((candidate) => candidate.revision_id === revisionId);
      if (!revision) {
        return;
      }
      const currentArtifact = sourceArtifacts[revisionId];
      if (!currentArtifact?.artifact) {
        return;
      }
      const token = beginOperation("excerpt", { workspaceId: currentWorkspaceId });
      try {
        const excerpt = await executeExplicitRequest(() =>
          api.readSourceExcerpt(currentWorkspaceId, revisionId, defaultExcerptRequest(revision)),
        );
        if (!isOperationCurrent(token)) {
          return;
        }
        if (
          excerpt.source_ref.workspace_id !== currentWorkspaceId ||
          excerpt.source_ref.revision_id !== revisionId ||
          excerpt.source_ref.source_id !== revision.source_id ||
          excerpt.source_ref.sha256 !== revision.sha256
        ) {
          setSourceArtifacts((current) => ({
            ...current,
            [revisionId]: { ...current[revisionId], status: "failed", issue: scopeIssue() },
          }));
          return;
        }
        setSourceArtifacts((current) => ({
          ...current,
          [revisionId]: { ...current[revisionId], excerpt },
        }));
      } catch (error: unknown) {
        if (isOperationCurrent(token)) {
          const issue = issueFromError(error);
          setSourceArtifacts((current) => ({
            ...current,
            [revisionId]: { ...current[revisionId], status: issue.kind, issue },
          }));
        }
      }
    }, [api, beginOperation, isOperationCurrent, sourceArtifacts, sourceState.items, workspaceId],
  );

  const uploadSourceBatch = useCallback(
    async (request: SourceUploadRequest): Promise<SourceBatchResult | null> => {
      if (
        !workspaceId ||
        uploadStateRef.current.status === "submitting" ||
        uploadStateRef.current.status === "unknown"
      ) {
        return null;
      }
      if (!request.local_read_confirmed) {
        const issue: ApiIssue = {
          kind: "failed",
          code: "local_read_confirmation_required",
          message: "必须先明确确认只读取选定的本地文件；该确认不等于允许 Provider 外发。",
        };
        setUploadStateNow({ ...actionForIssue(issue), result: null });
        return null;
      }
      const currentWorkspaceId = workspaceId;
      const pending = createPendingAction("upload", currentWorkspaceId);
      if (!writePendingAction(currentWorkspaceId, pending)) {
        setUploadStateNow({ ...actionForIssue(pendingActionStorageIssue()), result: null });
        return null;
      }
      const token = beginOperation("upload", { workspaceId: currentWorkspaceId });
      setUploadStateNow({ status: "submitting", issue: null, result: null });
      try {
        const result = await executeExplicitRequest(() => api.uploadSources(currentWorkspaceId, request));
        if (!isOperationCurrent(token)) {
          return null;
        }
        const accepted = result.items.flatMap((item) => (item.revision ? [item.revision] : []));
        if (!accepted.every((item) => sourceRevisionMatchesWorkspace(item, currentWorkspaceId))) {
          const issue = scopeIssue();
          setUploadStateNow({ status: issue.kind, issue, result: null });
          return null;
        }
        clearPendingAction(currentWorkspaceId, "upload");
        setUploadStateNow({ status: "success", issue: null, result });
        if (accepted.length > 0) {
          setSourceState((current) => {
            const revisions = new Map(current.items.map((item) => [item.revision_id, item]));
            accepted.forEach((item) => revisions.set(item.revision_id, item));
            const items = [...revisions.values()];
            return { status: items.length > 0 ? "ready" : "empty", items, issue: null };
          });
        }
        return result;
      } catch (error: unknown) {
        if (isOperationCurrent(token)) {
          const issue = issueFromError(error);
          if (issue.kind !== "unknown") {
            clearPendingAction(currentWorkspaceId, "upload");
          }
          setUploadStateNow({ ...actionForIssue(issue), result: null });
        }
        return null;
      }
    }, [api, beginOperation, isOperationCurrent, workspaceId],
  );

  const acknowledgeUploadUnknown = useCallback(() => {
    if (uploadStateRef.current.status === "unknown") {
      if (workspaceId && !clearPendingAction(workspaceId, "upload")) {
        setUploadStateNow({ ...actionForIssue(pendingActionStorageIssue()), result: null });
        return;
      }
      setUploadStateNow({ ...emptyAction(), result: null });
    }
  }, [workspaceId]);

  const submitAttempt = useCallback(
    async (request: MissionDraftAttemptCreateRequest): Promise<MissionDraftAttempt | null> => {
      if (
        !workspaceId ||
        attemptActionRef.current.status === "submitting" ||
        attemptActionRef.current.status === "unknown"
      ) {
        return null;
      }
      if (!request.provider_send_confirmed) {
        const issue: ApiIssue = {
          kind: "failed",
          code: "provider_confirmation_required",
          message: "必须明确确认只发送原始任务输入；不会自动附带来源文件。",
        };
        setAttemptActionNow(actionForIssue(issue));
        return null;
      }
      const currentWorkspaceId = workspaceId;
      const pending = createPendingAction("attempt", currentWorkspaceId, {
        intentKey: "attempt-create",
      });
      if (!writePendingAction(currentWorkspaceId, pending, { attemptId: null })) {
        setAttemptActionNow(actionForIssue(pendingActionStorageIssue()));
        return null;
      }
      const token = beginOperation("attempt", { workspaceId: currentWorkspaceId });
      invalidateOperation("confirm");
      invalidateOperation("run_start");
      storeAttempt(null);
      pendingAttemptIdRef.current = null;
      setConfirmActionNow(emptyAction());
      setAttemptActionNow({ status: "submitting", issue: null });
      try {
        const result = await executeExplicitRequest(() =>
          api.createMissionDraftAttempt(currentWorkspaceId, request),
        );
        if (!isOperationCurrent(token)) {
          return null;
        }
        if (
          !missionDraftAttemptMatchesIdentity(
            result,
            currentWorkspaceId,
            result.attempt_id,
            request.original_input,
          )
        ) {
          setAttemptActionNow(actionForIssue(scopeIssue()));
          return null;
        }
        clearPendingAction(currentWorkspaceId, "attempt");
        storeAttempt(result);
        pendingAttemptIdRef.current = null;
        writePath2ObjectPointers(currentWorkspaceId, { attemptId: result.attempt_id });
        setAttemptActionNow(attemptActionForResult(result));
        if (result.status === "queued" || result.status === "running") {
          const finalResult = await readAttempt(
            currentWorkspaceId,
            result.attempt_id,
            request.original_input,
            token,
          );
          return finalResult ?? result;
        }
        return result;
      } catch (error: unknown) {
        if (isOperationCurrent(token)) {
          const issue = issueFromError(error);
          if (issue.kind !== "unknown") {
            clearPendingAction(currentWorkspaceId, "attempt");
          }
          setAttemptActionNow(actionForIssue(issue));
        }
        return null;
      }
    }, [
      api,
      beginOperation,
      invalidateOperation,
      isOperationCurrent,
      readAttempt,
      storeAttempt,
      workspaceId,
    ],
  );

  const reconcileAttempt = useCallback(async (): Promise<void> => {
    if (!workspaceId || !attemptRef.current || attemptActionRef.current.status === "submitting") {
      return;
    }
    const currentWorkspaceId = workspaceId;
    const attemptId = attemptRef.current.attempt_id;
    const token = beginOperation("attempt", { workspaceId: currentWorkspaceId, attemptId });
    setAttemptActionNow({ status: "submitting", issue: null });
    await readAttempt(currentWorkspaceId, attemptId, undefined, token);
  }, [beginOperation, readAttempt, workspaceId]);

  const acknowledgeAttemptUnknown = useCallback(() => {
    if (attemptActionRef.current.status === "unknown") {
      if (workspaceId && !clearPendingAction(workspaceId, "attempt")) {
        setAttemptActionNow(actionForIssue(pendingActionStorageIssue()));
        return;
      }
      setAttemptActionNow(emptyAction());
    }
  }, [workspaceId]);

  const confirmAttempt = useCallback(async (): Promise<Mission | null> => {
    const currentAttempt = attemptRef.current;
    const currentSourceRefs = [...selectedSourceRefsRef.current];
    if (
      !workspaceId ||
      !currentAttempt ||
      !currentSourceRefs.length ||
      confirmActionRef.current.status === "submitting" ||
      confirmActionRef.current.status === "unknown"
    ) {
      return null;
    }
    const request = buildConfirmRequest(currentAttempt, currentSourceRefs);
    if (!request) {
      setConfirmActionNow({
        status: "failed",
        issue: { kind: "failed", code: "draft_not_ready", message: "当前草案没有可确认的 version/hash。" },
      });
      return null;
    }
    const currentWorkspaceId = workspaceId;
    const attemptId = currentAttempt.attempt_id;
    const pending = createPendingAction("confirm", currentWorkspaceId, {
      attemptId,
      candidateVersion: currentAttempt.candidate_version,
      candidateSha256: currentAttempt.candidate_sha256,
      sourceRefs: currentSourceRefs,
      intentKey: JSON.stringify({
        attemptId,
        candidateVersion: currentAttempt.candidate_version,
        candidateSha256: currentAttempt.candidate_sha256,
        sourceRefs: currentSourceRefs,
      }),
    });
    if (!writePendingAction(currentWorkspaceId, pending)) {
      setConfirmActionNow(actionForIssue(pendingActionStorageIssue()));
      return null;
    }
    invalidateOperation("missions");
    invalidateOperation("mission_snapshot");
    const token = beginOperation("confirm", {
      workspaceId: currentWorkspaceId,
      attemptId,
    });
    setConfirmActionNow({ status: "submitting", issue: null });
    try {
      const result = await executeExplicitRequest(() =>
        api.confirmMissionDraftAttempt(currentWorkspaceId, attemptId, request),
      );
      if (!isOperationCurrent(token)) {
        return null;
      }
      if (
        attemptRef.current?.attempt_id !== attemptId ||
        !sourceIdentityListEquals(selectedSourceRefsRef.current, currentSourceRefs)
      ) {
        setConfirmActionNow(actionForIssue(selectionChangedIssue("Attempt")));
        return null;
      }
      if (
        !missionMatchesWorkspace(result, currentWorkspaceId) ||
        result.original_attempt_id !== attemptId ||
        !sourceIdentityListEquals(result.source_refs, currentSourceRefs)
      ) {
        setConfirmActionNow(actionForIssue(scopeIssue()));
        return null;
      }
      clearPendingAction(currentWorkspaceId, "confirm");
      storeAttempt({ ...currentAttempt, status: "confirmed", mission_id: result.mission_id });
      setMissionState((current) => ({
        status: "ready",
        items: [
          ...current.items.filter((item) => item.mission_id !== result.mission_id),
          result,
        ],
        issue: null,
      }));
      setConfirmActionNow({ status: "success", issue: null });
      writePath2ObjectPointers(currentWorkspaceId, {
        missionId: result.mission_id,
        runId: null,
        runMissionId: null,
      });
      preferredRunIdRef.current = null;
      preferredRunMissionIdRef.current = null;
      selectedMissionIdRef.current = result.mission_id;
      setSelectedMissionId(result.mission_id);
      storeRunSnapshot(null);
      replaceRunEventState(createRunEventState());
      setRunReadbackIssue(null);
      storeMissionSnapshot(null);
      setMissionSnapshotState({ status: "loading", items: [result], issue: null });
      await loadMissionSnapshot(currentWorkspaceId, result.mission_id);
      return result;
    } catch (error: unknown) {
      if (isOperationCurrent(token)) {
        const issue = issueFromError(error);
        if (issue.kind !== "unknown") {
          clearPendingAction(currentWorkspaceId, "confirm");
        }
        setConfirmActionNow(actionForIssue(issue));
      }
      return null;
    }
  }, [
    api,
    beginOperation,
    invalidateOperation,
    isOperationCurrent,
    loadMissionSnapshot,
    replaceRunEventState,
    storeAttempt,
    storeMissionSnapshot,
    storeRunSnapshot,
    workspaceId,
  ]);

  const acknowledgeConfirmUnknown = useCallback(() => {
    if (confirmActionRef.current.status === "unknown") {
      if (workspaceId && !clearPendingAction(workspaceId, "confirm")) {
        setConfirmActionNow(actionForIssue(pendingActionStorageIssue()));
        return;
      }
      setConfirmActionNow(emptyAction());
    }
  }, [workspaceId]);

  const startRunAction = useCallback(
    async (providerSendConfirmed: boolean): Promise<RunSnapshot | null> => {
      const currentMission = selectedMission;
      const currentSourceRefs = [...selectedSourceRefsRef.current];
      if (
        !workspaceId ||
        !currentMission ||
        missionSnapshotState.status !== "ready" ||
        !currentSourceRefs.length ||
        runActionRef.current.status === "submitting" ||
        runActionRef.current.status === "unknown"
      ) {
        return null;
      }
      if (!providerSendConfirmed) {
        setRunActionNow({
          status: "failed",
          issue: { kind: "failed", code: "provider_confirmation_required", message: "必须明确确认本次 Run 的 Provider 外发范围。" },
        });
        return null;
      }
      const requestKey = JSON.stringify({
        workspaceId,
        missionId: currentMission.mission_id,
        stateVersion: currentMission.state_version,
        sourceRefs: currentSourceRefs,
      });
      const clientRequestId =
        startRequestKeyRef.current === requestKey && clientRequestIdRef.current
          ? clientRequestIdRef.current
          : createClientRequestId();
      clientRequestIdRef.current = clientRequestId;
      startRequestKeyRef.current = requestKey;
      const request = buildRunStartRequest(
        currentMission,
        currentSourceRefs,
        providerSendConfirmed,
        clientRequestId,
      );
      const currentWorkspaceId = workspaceId;
      const missionId = currentMission.mission_id;
      invalidateOperation("run_snapshot");
      invalidateOperation("missions");
      invalidateOperation("mission_snapshot");
      const pending = createPendingAction("run_start", currentWorkspaceId, {
        missionId,
        clientRequestId,
        intentKey: requestKey,
        stateVersion: currentMission.state_version,
        sourceRefs: currentSourceRefs,
      });
      if (!writePendingAction(currentWorkspaceId, pending, { runId: null, runMissionId: null })) {
        clientRequestIdRef.current = null;
        startRequestKeyRef.current = null;
        setRunActionNow(actionForIssue(pendingActionStorageIssue()));
        return null;
      }
      const token = beginOperation("run_start", {
        workspaceId: currentWorkspaceId,
        missionId,
      });
      storeMissionSnapshot(null);
      storeRunSnapshot(null);
      replaceRunEventState(createRunEventState());
      setMissionSnapshotState({ status: "loading", items: [currentMission], issue: null });
      setRunReadbackIssue(null);
      preferredRunIdRef.current = null;
      preferredRunMissionIdRef.current = null;
      pendingRunIdRef.current = null;
      setRunActionNow({ status: "submitting", issue: null });
      setCancelActionNow(emptyAction());
      const recoverMissionSnapshot = async (): Promise<void> => {
        if (isOperationCurrent(token)) {
          await loadMissionSnapshot(currentWorkspaceId, missionId);
        }
      };
      try {
        const result = await executeExplicitRequest(() =>
          api.startRun(currentWorkspaceId, missionId, request),
        );
        if (!isOperationCurrent(token)) {
          return null;
        }
        if (
          selectedMissionIdRef.current !== missionId ||
          !sourceIdentityListEquals(selectedSourceRefsRef.current, currentSourceRefs)
        ) {
          setRunActionNow(actionForIssue(selectionChangedIssue("Mission")));
          await recoverMissionSnapshot();
          return null;
        }
        if (
          !runSnapshotMatchesIdentity(result, {
            workspaceId: currentWorkspaceId,
            missionId,
            runId: result.run_id,
          }) ||
          !sourceIdentityListEquals(result.source_refs, currentSourceRefs)
        ) {
          const issue = scopeIssue();
          setRunActionNow(actionForIssue(issue));
          await recoverMissionSnapshot();
          return null;
        }
        clearPendingAction(currentWorkspaceId, "run_start");
        storeRunSnapshot(result);
        replaceRunEventState(createRunEventState(result.last_sequence));
        setRunReadbackIssue(null);
        setRunEventIssue(null);
        setRunActionNow({ status: "success", issue: null });
        setCancelActionNow(emptyAction());
        writePath2ObjectPointers(currentWorkspaceId, {
          missionId,
          runId: result.run_id,
          runMissionId: missionId,
        });
        preferredRunIdRef.current = result.run_id;
        preferredRunMissionIdRef.current = missionId;
        clientRequestIdRef.current = null;
        startRequestKeyRef.current = null;
        await recoverMissionSnapshot();
        return result;
      } catch (error: unknown) {
        if (isOperationCurrent(token)) {
          const issue = issueFromError(error);
          setRunActionNow(actionForIssue(issue));
          if (issue.kind !== "unknown") {
            clearPendingAction(currentWorkspaceId, "run_start");
            clientRequestIdRef.current = null;
            startRequestKeyRef.current = null;
          }
          await recoverMissionSnapshot();
        }
        return null;
      }
    }, [
      api,
      beginOperation,
      invalidateOperation,
      isOperationCurrent,
      loadMissionSnapshot,
      missionSnapshotState.status,
      replaceRunEventState,
      selectedMission,
      storeMissionSnapshot,
      storeRunSnapshot,
      workspaceId,
    ],
  );

  const acknowledgeRunUnknown = useCallback(() => {
    if (runActionRef.current.status === "unknown") {
      if (workspaceId && !clearPendingAction(workspaceId, "run_start")) {
        setRunActionNow(actionForIssue(pendingActionStorageIssue()));
        return;
      }
      setRunActionNow(emptyAction());
      clientRequestIdRef.current = null;
      startRequestKeyRef.current = null;
    }
  }, [workspaceId]);

  const cancelActiveRun = useCallback(async (): Promise<RunSnapshot | null> => {
    const currentRun = runRef.current;
    if (
      !workspaceId ||
      !currentRun ||
      cancelActionRef.current.status === "submitting" ||
      cancelActionRef.current.status === "unknown"
    ) {
      return null;
    }
    if (currentRun.status !== "queued" && currentRun.status !== "running") {
      return null;
    }
    const identity: RunIdentity = {
      workspaceId,
      missionId: currentRun.mission_id,
      runId: currentRun.run_id,
    };
    invalidateOperation("run_snapshot");
    invalidateOperation("mission_snapshot");
    const pending = createPendingAction("cancel", identity.workspaceId, {
      missionId: identity.missionId,
      runId: identity.runId,
    });
    if (!writePendingAction(identity.workspaceId, pending)) {
      setCancelActionNow(actionForIssue(pendingActionStorageIssue()));
      return null;
    }
    const token = beginOperation("cancel", identity);
    setCancelActionNow({ status: "submitting", issue: null });
    try {
      const result = await executeExplicitRequest(() =>
        api.cancelRun(identity.workspaceId, identity.missionId, identity.runId),
      );
      if (!isOperationCurrent(token) || runRef.current?.run_id !== identity.runId) {
        return null;
      }
      if (!runSnapshotMatchesIdentity(result, identity)) {
        const issue = scopeIssue();
        setCancelActionNow(actionForIssue(issue));
        return null;
      }
      if (!applyRunSnapshot(result, identity)) {
        return null;
      }
      clearPendingAction(identity.workspaceId, "cancel");
      setCancelActionNow({ status: "success", issue: null });
      return result;
    } catch (error: unknown) {
      if (isOperationCurrent(token)) {
        const issue = issueFromError(error);
        if (issue.kind !== "unknown") {
          clearPendingAction(identity.workspaceId, "cancel");
        }
        setCancelActionNow(actionForIssue(issue));
      }
      return null;
    }
  }, [api, applyRunSnapshot, beginOperation, invalidateOperation, isOperationCurrent, workspaceId]);

  const reconcileRun = useCallback(async (): Promise<void> => {
    const currentRun = runRef.current;
    if (!currentRun || !workspaceId || cancelActionRef.current.status === "submitting") {
      return;
    }
    const next = await readRunSnapshot({
      workspaceId,
      missionId: currentRun.mission_id,
      runId: currentRun.run_id,
    });
    if (next?.status === "cancelled" && cancelActionRef.current.status === "unknown") {
      clearPendingAction(workspaceId, "cancel");
      setCancelActionNow({ status: "success", issue: null });
    }
  }, [readRunSnapshot, workspaceId]);

  const acknowledgeCancelUnknown = useCallback(() => {
    if (cancelActionRef.current.status === "unknown") {
      if (workspaceId && !clearPendingAction(workspaceId, "cancel")) {
        setCancelActionNow(actionForIssue(pendingActionStorageIssue()));
        return;
      }
      setCancelActionNow(emptyAction());
    }
  }, [workspaceId]);

  const refreshRunSnapshot = useCallback(
    async (identity: RunIdentity): Promise<void> => {
      await readRunSnapshot(identity);
    },
    [readRunSnapshot],
  );

  useEffect(() => {
    if (!workspaceId || !runSnapshot) {
      setRunConnectionState(workspaceId ? "closed" : "idle");
      return;
    }
    if (runSnapshot.status !== "queued" && runSnapshot.status !== "running") {
      setRunConnectionState("closed");
      return;
    }

    const identity: RunIdentity = {
      workspaceId,
      missionId: runSnapshot.mission_id,
      runId: runSnapshot.run_id,
    };
    let cancelled = false;
    let opened = false;
    const source = eventSourceFactory(
      runEventsUrl(identity.workspaceId, identity.missionId, identity.runId),
    );
    const handlers = new Map<string, (event: Event) => void>();
    const handleEvent = (event: Event) => {
      if (cancelled || !("data" in event) || typeof event.data !== "string") {
        return;
      }
      const parsed = parseRunEvent(event.data);
      if (!parsed) {
        setRunEventIssue("收到无法解析的公开事件；已忽略，等待真实快照。");
        return;
      }
      if (
        parsed.workspace_id !== identity.workspaceId ||
        parsed.mission_id !== identity.missionId ||
        parsed.run_id !== identity.runId
      ) {
        return;
      }
      const current = runEventStateRef.current;
      const next = acceptRunEvent(current, parsed, identity);
      if (next === current) {
        return;
      }
      updateRunEventState(() => next);
      const requiresReadback =
        parsed.event_type === "draft_updated" ||
        parsed.event_type === "clarification_requested" ||
        parsed.event_type in TERMINAL_EVENT_STATUS ||
        (!current.hasSequenceGap && next.hasSequenceGap);
      if (requiresReadback) {
        const readback = refreshRunSnapshot(identity);
        if (parsed.event_type in TERMINAL_EVENT_STATUS) {
          void readback.finally(() => {
            if (!cancelled) {
              source.close();
            }
          });
        }
      }
    };
    const handleOpen = () => {
      if (!cancelled) {
        opened = true;
        setRunConnectionState("connected");
        void refreshRunSnapshot(identity);
      }
    };
    const handleError = () => {
      if (cancelled) {
        return;
      }
      setRunConnectionState(opened ? "reconnecting" : "blocked");
      void refreshRunSnapshot(identity);
    };
    source.onopen = handleOpen;
    source.onerror = handleError;
    RUN_EVENT_TYPES.forEach((eventType) => {
      handlers.set(eventType, handleEvent);
      source.addEventListener(eventType, handleEvent);
    });
    setRunConnectionState("connecting");

    return () => {
      cancelled = true;
      source.onopen = null;
      source.onerror = null;
      handlers.forEach((handler, eventType) => source.removeEventListener(eventType, handler));
      source.close();
    };
  }, [
    eventSourceFactory,
    refreshRunSnapshot,
    runSnapshot?.mission_id,
    runSnapshot?.run_id,
    runSnapshot?.status,
    updateRunEventState,
    workspaceId,
  ]);

  const dataBelongsToWorkspace = loadedWorkspaceId === workspaceId;
  const visibleSourceState = dataBelongsToWorkspace
    ? sourceState
    : workspaceId
      ? loadingCollection<SourceRevision>()
      : emptyCollection<SourceRevision>();
  const visibleSourceArtifacts = dataBelongsToWorkspace ? sourceArtifacts : {};
  const visibleSelectedSourceIds = dataBelongsToWorkspace ? selectedSourceIds : [];
  const visibleSelectedSourceRefs = dataBelongsToWorkspace ? selectedSourceRefs : [];
  const visibleUploadState = dataBelongsToWorkspace
    ? uploadState
    : { ...emptyAction(), result: null };
  const visibleMissionState = dataBelongsToWorkspace
    ? missionState
    : workspaceId
      ? loadingCollection<Mission>()
      : emptyCollection<Mission>();
  const visibleSelectedMission = dataBelongsToWorkspace ? selectedMission : null;
  const visibleMissionSnapshot = dataBelongsToWorkspace ? missionSnapshot : null;
  const visibleMissionSnapshotState = dataBelongsToWorkspace
    ? missionSnapshotState
    : workspaceId
      ? loadingCollection<Mission>()
      : emptyCollection<Mission>();
  const visibleAttempt = dataBelongsToWorkspace ? attempt : null;
  const visibleAttemptAction = dataBelongsToWorkspace ? attemptAction : emptyAction();
  const visibleConfirmAction = dataBelongsToWorkspace ? confirmAction : emptyAction();
  const visibleRunSnapshot = dataBelongsToWorkspace ? runSnapshot : null;
  const visibleRunAction = dataBelongsToWorkspace ? runAction : emptyAction();
  const visibleCancelAction = dataBelongsToWorkspace ? cancelAction : emptyAction();
  const visibleRunEventState = dataBelongsToWorkspace ? runEventState : createRunEventState();
  const visibleRunReadbackIssue = dataBelongsToWorkspace ? runReadbackIssue : null;
  const visibleRunEventIssue = dataBelongsToWorkspace ? runEventIssue : null;
  const visibleRunConnectionState = dataBelongsToWorkspace
    ? runConnectionState
    : workspaceId
      ? "connecting"
      : "idle";
  const latestDraft = visibleRunSnapshot?.draft ?? visibleMissionSnapshot?.draft ?? null;
  const clarifications = visibleRunSnapshot?.clarifications ?? visibleMissionSnapshot?.clarifications ?? [];

  return {
    workspaceId,
    sourceState: visibleSourceState,
    sourceArtifacts: visibleSourceArtifacts,
    selectedSourceIds: visibleSelectedSourceIds,
    selectedSourceRefs: visibleSelectedSourceRefs,
    selectSource,
    toggleSource,
    loadSourceArtifact,
    readSourceExcerpt: readExcerpt,
    refreshSources,
    uploadState: visibleUploadState,
    uploadSourceBatch,
    acknowledgeUploadUnknown,
    missionState: visibleMissionState,
    selectedMission: visibleSelectedMission,
    missionSnapshot: visibleMissionSnapshot,
    missionSnapshotState: visibleMissionSnapshotState,
    refreshMission: refreshMissions,
    attempt: visibleAttempt,
    attemptAction: visibleAttemptAction,
    submitAttempt,
    reconcileAttempt,
    acknowledgeAttemptUnknown,
    confirmAction: visibleConfirmAction,
    confirmAttempt,
    acknowledgeConfirmUnknown,
    runSnapshot: visibleRunSnapshot,
    runAction: visibleRunAction,
    startRun: startRunAction,
    acknowledgeRunUnknown,
    cancelAction: visibleCancelAction,
    cancelActiveRun,
    reconcileRun,
    acknowledgeCancelUnknown,
    runConnectionState: visibleRunConnectionState,
    runReadbackIssue: visibleRunReadbackIssue,
    runEventIssue: visibleRunEventIssue,
    runEventState: visibleRunEventState,
    latestDraft,
    clarifications,
  };
}

export type Path2AreaId = "sources" | "mission" | "clarifications" | "contract";

const STATUS_LABELS: Record<string, string> = {
  idle: "未开始",
  loading: "读取中",
  empty: "暂无",
  submitting: "提交中",
  success: "已完成",
  queued: "排队中",
  running: "运行中",
  ready: "可确认",
  confirmed: "已确认",
  waiting_for_human: "等待人工",
  partial: "部分完成",
  completed: "已完成",
  blocked: "已阻塞",
  failed: "失败",
  cancelled: "已取消",
  pending: "待处理",
  denied: "已拒绝",
  unknown: "未知",
  connected: "已连接",
  reconnecting: "重连中",
  closed: "已关闭",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function StatusPill({ status, children }: { status: string; children?: string }) {
  return <span className={`path2-status path2-status-${status}`}>{children ?? statusLabel(status)}</span>;
}

function IssueCallout({ issue, title }: { issue: ApiIssue; title?: string }) {
  return (
    <div className={`path2-issue path2-issue-${issue.kind}`} role={issue.kind === "unknown" ? "alert" : "status"}>
      <strong>{title ?? (issue.kind === "blocked" ? "当前能力不可用" : issue.kind === "unknown" ? "结果未知，已阻塞" : "请求未完成")}</strong>
      <p>{issue.message}</p>
      {issue.code ? <code>code: {issue.code}</code> : null}
    </div>
  );
}

function WorkspaceRequired({ copy }: { copy: string }) {
  return (
    <div className="path2-empty-state" role="status">
      <span className="path2-empty-icon" aria-hidden="true">○</span>
      <strong>请先选择 Workspace</strong>
      <p>{copy}</p>
    </div>
  );
}

function WorkspaceContext({ state }: { state: Path2WorkbenchState }) {
  const summary = workbenchSurfaceSummary(state);
  return (
    <div className="path2-context-strip">
      <div>
        <span className="path2-eyebrow">PATH 2 WORKBENCH</span>
        <strong>{state.workspaceId ? "当前 Workspace 工作区" : "等待 Workspace"}</strong>
      </div>
      <div className="path2-context-meta">
        <StatusPill status={summary.status}>{summary.label}</StatusPill>
        {state.workspaceId ? <code title={state.workspaceId}>{state.workspaceId}</code> : null}
      </div>
    </div>
  );
}

type UploadCandidate = {
  id: string;
  file: File;
  mediaType: SourceUploadFile["media_type"] | null;
  status: "pending" | "reading" | "ready" | "blocked" | "failed";
  message: string | null;
};

function mediaTypeForFile(file: File): SourceUploadFile["media_type"] | null {
  const suffix = file.name.toLowerCase().split(".").pop() ?? "";
  if (suffix === "csv") {
    return "text/csv";
  }
  if (suffix === "json") {
    return "application/json";
  }
  if (suffix === "md" || suffix === "markdown") {
    return "text/markdown";
  }
  if (suffix === "txt") {
    return "text/plain";
  }
  return null;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let start = 0; start < bytes.length; start += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(start, start + chunkSize));
  }
  return btoa(binary);
}

async function encodeFile(file: File): Promise<string> {
  return bytesToBase64(new Uint8Array(await file.arrayBuffer()));
}

function SourceSelection({
  state,
  idPrefix,
  title,
  description,
}: {
  state: Path2WorkbenchState;
  idPrefix: string;
  title: string;
  description: string;
}) {
  const collection = state.sourceState;
  return (
    <fieldset className="path2-source-selection">
      <legend>{title}</legend>
      <p>{description}</p>
      {collection.status === "loading" ? <span className="path2-inline-status">正在读取来源…</span> : null}
      {collection.issue ? <IssueCallout issue={collection.issue} /> : null}
      {collection.status === "empty" ? <span className="path2-inline-status">当前 Workspace 没有已读来源；请先导入本地资料。</span> : null}
      {collection.items.length > 0 ? (
        <div className="path2-source-options">
          {collection.items.map((revision) => {
            const inputId = `${idPrefix}-${revision.revision_id}`;
            return (
              <label className="path2-source-option" key={revision.revision_id} htmlFor={inputId}>
                <input
                  id={inputId}
                  type="checkbox"
                  checked={state.selectedSourceIds.includes(revision.revision_id)}
                  onChange={() => state.toggleSource(revision.revision_id)}
                  disabled={revision.permission_status !== "read_allowed" || revision.parse_status === "blocked"}
                />
                <span className="path2-source-option-copy">
                  <strong>{revision.original_name}</strong>
                  <small>{statusLabel(revision.parse_status)} · {revision.byte_size.toLocaleString()} bytes</small>
                </span>
                <code title={revision.sha256}>{revision.sha256.slice(0, 10)}…</code>
              </label>
            );
          })}
        </div>
      ) : null}
    </fieldset>
  );
}

function SourceUploadPanel({ state }: { state: Path2WorkbenchState }) {
  const [candidates, setCandidates] = useState<UploadCandidate[]>([]);
  const [localReadConfirmed, setLocalReadConfirmed] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [isReading, setIsReading] = useState(false);
  const uploadOperationRef = useRef(0);
  const currentWorkspaceIdRef = useRef(state.workspaceId);
  currentWorkspaceIdRef.current = state.workspaceId;

  useEffect(() => {
    uploadOperationRef.current += 1;
    setIsReading(false);
    return () => {
      uploadOperationRef.current += 1;
    };
  }, [state.workspaceId]);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    uploadOperationRef.current += 1;
    setIsReading(false);
    const files = Array.from(event.currentTarget.files ?? []);
    const next = files.map((file, index) => {
      const mediaType = mediaTypeForFile(file);
      let message: string | null = null;
      if (!mediaType) {
        message = "仅支持 UTF-8 CSV、JSON、Markdown、TXT。";
      } else if (file.size > 2 * 1024 * 1024) {
        message = "单文件超过 2 MiB，已阻止提交。";
      }
      return {
        id: `${file.name}-${file.lastModified}-${file.size}-${index}`,
        file,
        mediaType,
        status: message ? "blocked" : "pending",
        message,
      } satisfies UploadCandidate;
    });
    setCandidates(next);
    setLocalError(files.length > 8 ? "单批最多 8 个文件，已阻止提交。" : null);
    setLocalReadConfirmed(false);
    event.currentTarget.value = "";
  };

  const handleUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLocalError(null);
    if (candidates.length === 0) {
      setLocalError("请先选择要读取的本地文件。");
      return;
    }
    if (candidates.length > 8 || candidates.some((candidate) => candidate.status === "blocked")) {
      setLocalError("本批包含不支持或超限文件；请修正后再提交，未发送请求。");
      return;
    }
    const totalSize = candidates.reduce((total, candidate) => total + candidate.file.size, 0);
    if (totalSize > 8 * 1024 * 1024) {
      setLocalError("本批合计超过 8 MiB，已阻止提交。");
      return;
    }
    if (!localReadConfirmed) {
      setLocalError("请明确确认只读取这些本地文件；该确认不等于允许 Provider 外发。");
      return;
    }
    if (state.uploadState.status === "unknown") {
      setLocalError("上一次导入结果未知；请先回读来源列表或明确人工核对，未发送新请求。");
      return;
    }
    const operation = ++uploadOperationRef.current;
    const capturedWorkspaceId = state.workspaceId;
    setIsReading(true);
    const encoded: SourceUploadFile[] = [];
    try {
      for (const candidate of candidates) {
        if (operation !== uploadOperationRef.current || currentWorkspaceIdRef.current !== capturedWorkspaceId) {
          return;
        }
        if (!candidate.mediaType) {
          setLocalError("至少一个文件没有受支持的媒体类型；未提交本批请求。");
          return;
        }
        setCandidates((current) =>
          current.map((item) => item.id === candidate.id ? { ...item, status: "reading", message: null } : item),
        );
        try {
          const contentBase64 = await encodeFile(candidate.file);
          if (operation !== uploadOperationRef.current || currentWorkspaceIdRef.current !== capturedWorkspaceId) {
            return;
          }
          encoded.push({
            original_name: candidate.file.name,
            media_type: candidate.mediaType,
            content_base64: contentBase64,
          });
          setCandidates((current) =>
            current.map((item) => item.id === candidate.id ? { ...item, status: "ready" } : item),
          );
        } catch {
          if (operation !== uploadOperationRef.current || currentWorkspaceIdRef.current !== capturedWorkspaceId) {
            return;
          }
          setCandidates((current) =>
            current.map((item) => item.id === candidate.id ? { ...item, status: "failed", message: "本地文件读取失败。" } : item),
          );
          setLocalError("至少一个文件读取失败；未提交本批请求。");
          return;
        }
      }
      if (operation !== uploadOperationRef.current || currentWorkspaceIdRef.current !== capturedWorkspaceId) {
        return;
      }
      await state.uploadSourceBatch(buildSourceUploadRequest(encoded, localReadConfirmed));
    } finally {
      if (operation === uploadOperationRef.current && currentWorkspaceIdRef.current === capturedWorkspaceId) {
        setIsReading(false);
      }
    }
  };

  return (
    <div className="path2-panel-stack">
      <div className="path2-panel-intro">
        <div>
          <span className="path2-eyebrow">SOURCE ADMISSION</span>
          <h2>来源与证据</h2>
          <p>本地读取、来源版本和 Provider 外发分别确认。当前正式 API 仍返回真实不可用状态，不显示预置资料。</p>
        </div>
        <StatusPill status={state.sourceState.status}>{statusLabel(state.sourceState.status)}</StatusPill>
      </div>
      <form className="path2-card path2-upload-card" onSubmit={(event) => void handleUpload(event)}>
        <div className="path2-card-heading">
          <div>
            <h3>读取本地资料</h3>
            <p>支持 UTF-8 CSV / JSON / Markdown / TXT；单文件 2 MiB，单批 8 文件 / 8 MiB。</p>
          </div>
          <label className="path2-file-picker">
            <span>选择文件</span>
            <input type="file" multiple accept=".csv,.json,.md,.markdown,.txt" onChange={handleFileChange} disabled={isReading} />
          </label>
        </div>
        {candidates.length > 0 ? (
          <div className="path2-upload-files" aria-label="待读取文件">
            {candidates.map((candidate) => (
              <div className="path2-upload-file" key={candidate.id}>
                <span className="path2-upload-file-name">{candidate.file.name}</span>
                <small>{candidate.file.size.toLocaleString()} bytes</small>
                <StatusPill status={candidate.status}>{candidate.message ?? statusLabel(candidate.status)}</StatusPill>
              </div>
            ))}
          </div>
        ) : (
          <div className="path2-inline-empty">尚未选择文件。导入不会自动允许 Provider 发送。</div>
        )}
        <label className="path2-confirmation-row">
          <input
            type="checkbox"
            checked={localReadConfirmed}
            onChange={(event) => setLocalReadConfirmed(event.target.checked)}
          />
          <span>我确认只读取上面列出的本地文件；不授权将文件发送给 Provider。</span>
        </label>
        {localError ? <p className="path2-form-error" role="alert">{localError}</p> : null}
        {state.uploadState.issue ? <IssueCallout issue={state.uploadState.issue} /> : null}
        {state.uploadState.status === "unknown" ? (
          <div className="path2-reconcile-actions">
            <button type="button" className="path2-secondary-button" onClick={() => void state.refreshSources()}>重新读取来源列表（仍需核对导入结果）</button>
            <button type="button" className="path2-secondary-button" onClick={state.acknowledgeUploadUnknown}>我已人工核对，解除阻塞</button>
          </div>
        ) : null}
        {state.uploadState.result ? (
          <div className="path2-result-list" role="status">
            <strong>本批服务器结果</strong>
            {state.uploadState.result.items.map((item) => (
              <div className="path2-result-row" key={`${item.file_index}-${item.original_name}`}>
                <span>{item.original_name}</span>
                <StatusPill status={item.status}>{statusLabel(item.status)}</StatusPill>
                {item.error ? <small>{item.error.message}</small> : null}
              </div>
            ))}
          </div>
        ) : null}
        <button
          type="submit"
          className="path2-primary-button"
          disabled={
            isReading ||
            state.uploadState.status === "submitting" ||
            state.uploadState.status === "unknown" ||
            !state.workspaceId ||
            candidates.length === 0 ||
            !localReadConfirmed ||
            candidates.some((candidate) => candidate.status === "blocked")
          }
        >
          {isReading || state.uploadState.status === "submitting" ? "读取中…" : "导入本地资料"}
        </button>
      </form>
      <div className="path2-card">
        <div className="path2-card-heading">
          <div>
            <h3>当前 Workspace 来源</h3>
            <p>仅展示服务端回读的 SourceRevision；版本与原始字节 hash 不可变。</p>
          </div>
          <span className="path2-count">{state.sourceState.items.length}</span>
        </div>
        {state.sourceState.issue ? <IssueCallout issue={state.sourceState.issue} /> : null}
        {state.sourceState.status === "loading" ? <div className="path2-inline-status">正在读取来源列表…</div> : null}
        {state.sourceState.status === "empty" ? <div className="path2-inline-empty">当前 Workspace 尚无来源。</div> : null}
        {state.sourceState.items.length > 0 ? (
          <div className="path2-source-list">
            {state.sourceState.items.map((revision) => {
              const artifactState = state.sourceArtifacts[revision.revision_id];
              const isSelected = state.selectedSourceIds.includes(revision.revision_id);
              return (
                <article className={`path2-source-row${isSelected ? " path2-source-row-selected" : ""}`} key={revision.revision_id}>
                  <div className="path2-source-row-main">
                    <button type="button" className="path2-source-name" onClick={() => state.selectSource(revision.revision_id)}>
                      {revision.original_name}
                    </button>
                    <small>{statusLabel(revision.parse_status)} · {revision.permission_status === "read_allowed" ? "本地读取已允许" : statusLabel(revision.permission_status)}</small>
                  </div>
                  <code title={revision.sha256}>{revision.sha256.slice(0, 12)}…</code>
                  <button type="button" className="path2-secondary-button" onClick={() => void state.loadSourceArtifact(revision.revision_id)}>
                    {artifactState?.status === "loading" ? "读取中…" : "查看证据"}
                  </button>
                  {artifactState?.issue ? <IssueCallout issue={artifactState.issue} /> : null}
                  {artifactState?.artifact ? (
                    <div className="path2-artifact-preview">
                      <div className="path2-artifact-meta">
                        <span>parser: {artifactState.artifact.parser_version}</span>
                        <StatusPill status={artifactState.artifact.parse_status}>{statusLabel(artifactState.artifact.parse_status)}</StatusPill>
                      </div>
                      {artifactState.artifact.tables.map((table) => (
                        <div className="path2-table-preview" key={table.table_id}>
                          <strong>表 {table.table_id || "根表"}</strong>
                          <small>{table.row_count.toLocaleString()} 行 · {table.columns.length} 列 · 重复行 {table.duplicate_row_count.toLocaleString()}</small>
                          {table.sample_rows.length > 0 ? (
                            <div className="path2-sample-table" role="table" aria-label={`表 ${table.table_id || "根表"} 样例`}>
                              {table.sample_rows.slice(0, 5).map((row) => (
                                <div className="path2-sample-row" role="row" key={row.row_number}>
                                  <span role="cell">#{row.row_number}</span>
                                  {row.cells.slice(0, 6).map((cell) => (
                                    <span role="cell" key={`${row.row_number}-${cell.column_name}`} title={cell.column_name}>
                                      {cell.text ?? cell.value_kind}{cell.truncated ? "…" : ""}
                                    </span>
                                  ))}
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ))}
                      <button type="button" className="path2-secondary-button" onClick={() => void state.readSourceExcerpt(revision.revision_id)}>
                        {artifactState.excerpt ? "重新读取片段" : "读取证据片段"}
                      </button>
                      {artifactState.excerpt ? (
                        <pre className="path2-excerpt"><code>{artifactState.excerpt.text}</code>{artifactState.excerpt.truncated ? "\n…展示已截断" : ""}</pre>
                      ) : null}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function DraftCandidate({ candidate }: { candidate: MissionDraftPayload }) {
  return (
    <div className="path2-candidate">
      <div className="path2-candidate-title">
        <span className="path2-eyebrow">CANDIDATE</span>
        <h3>{candidate.title}</h3>
      </div>
      <dl>
        <div><dt>目标</dt><dd>{candidate.goal}</dd></div>
        <div><dt>完成标准</dt><dd><ul>{candidate.completion_criteria.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></dd></div>
        {candidate.scope_notes.length > 0 ? <div><dt>范围说明</dt><dd><ul>{candidate.scope_notes.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></dd></div> : null}
      </dl>
    </div>
  );
}

function RunStatusCard({ state }: { state: Path2WorkbenchState }) {
  const run = state.runSnapshot;
  if (!run) {
    return (
      <div className="path2-run-card">
        {state.runReadbackIssue ? <IssueCallout issue={state.runReadbackIssue} title="Run 指针回读未完成" /> : null}
        {state.runAction.status === "unknown" ? (
          <div className="path2-reconcile-actions">
            <button type="button" className="path2-secondary-button" onClick={() => void state.refreshMission()}>重新读取 Mission</button>
            <button type="button" className="path2-secondary-button" onClick={state.acknowledgeRunUnknown}>我已人工核对，解除阻塞</button>
          </div>
        ) : null}
        <div className="path2-inline-empty">
          {state.runAction.status === "unknown"
            ? "Start 结果未知；当前不能确认是否创建了本次 Run，Mission/Run 回读也不会替代原 Start 的成功收据。"
            : "尚未创建 Run。确认 Mission 后，仍需单独点击“明确开始 Run”。"}
        </div>
      </div>
    );
  }
  const isLive = run.status === "queued" || run.status === "running";
  return (
    <div className="path2-run-card">
      <div className="path2-card-heading">
        <div>
          <span className="path2-eyebrow">AGENT RUN</span>
          <h3>Run 状态与公开事件</h3>
        </div>
        <StatusPill status={run.status}>{statusLabel(run.status)}</StatusPill>
      </div>
      <div className="path2-run-meta">
        <span>run <code>{run.run_id}</code></span>
        <span>sequence {run.last_sequence}</span>
        <span>SSE {state.runConnectionState}</span>
      </div>
      {state.runAction.issue ? <IssueCallout issue={state.runAction.issue} /> : null}
      {state.cancelAction.issue ? <IssueCallout issue={state.cancelAction.issue} title="取消未完成" /> : null}
      {state.runReadbackIssue ? <IssueCallout issue={state.runReadbackIssue} title="快照回读未完成" /> : null}
      {state.runEventIssue ? <div className="path2-event-warning" role="status">{state.runEventIssue}</div> : null}
      {state.runEventState.hasSequenceGap ? <div className="path2-event-warning" role="status">公开事件存在序号缺口；未用 delta 拼造完整文字，最终摘要只取持久化快照。</div> : null}
      {run.final_output ? (
        <div className="path2-final-output">
          <strong>持久化公开摘要</strong>
          <p>{run.final_output}</p>
        </div>
      ) : run.status === "partial" || run.status === "waiting_for_human" ? (
        <div className="path2-inline-status">结构化结果已回读，但持久化公开摘要为空；不伪造完整文字。</div>
      ) : null}
      {isLive ? (
        <>
          {state.cancelAction.status === "unknown" ? (
            <div className="path2-reconcile-actions">
              <button type="button" className="path2-secondary-button" onClick={() => void state.reconcileRun()}>重新读取 Run 快照</button>
              <button type="button" className="path2-secondary-button" onClick={state.acknowledgeCancelUnknown}>我已人工核对，解除阻塞</button>
            </div>
          ) : null}
          <button
            type="button"
            className="path2-danger-button"
            onClick={() => void state.cancelActiveRun()}
            disabled={state.cancelAction.status === "submitting" || state.cancelAction.status === "unknown"}
          >
            {state.cancelAction.status === "submitting" ? "取消中…" : "取消 Run"}
          </button>
        </>
      ) : null}
    </div>
  );
}

function MissionPanel({ state }: { state: Path2WorkbenchState }) {
  const [originalInput, setOriginalInput] = useState("");
  const [attemptSendConfirmed, setAttemptSendConfirmed] = useState(false);
  const [candidateAcknowledged, setCandidateAcknowledged] = useState(false);
  const [runSendConfirmed, setRunSendConfirmed] = useState(false);
  const attempt = state.attempt;
  const candidate = attempt?.candidate;
  const mission = state.selectedMission;
  const sourceSelectionKey = state.selectedSourceRefs
    .map((reference) => `${reference.workspace_id}:${reference.source_id}:${reference.revision_id}:${reference.sha256}`)
    .join("|");

  useEffect(() => {
    setCandidateAcknowledged(false);
  }, [attempt?.attempt_id, attempt?.candidate_version, attempt?.candidate_sha256, sourceSelectionKey]);

  useEffect(() => {
    setRunSendConfirmed(false);
  }, [mission?.mission_id, mission?.state_version, sourceSelectionKey]);

  const canConfirm = Boolean(
    attempt?.status === "ready" && candidate && candidateAcknowledged && state.selectedSourceRefs.length > 0,
  );
  const canStart = Boolean(
    mission &&
    state.missionSnapshotState.status === "ready" &&
    state.selectedSourceRefs.length > 0 &&
    runSendConfirmed &&
    (state.runSnapshot?.status !== "queued" && state.runSnapshot?.status !== "running"),
  );

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void state.submitAttempt({
      original_input: originalInput,
      provider_send_confirmed: attemptSendConfirmed,
    });
  };

  if (!state.workspaceId) {
    return <WorkspaceRequired copy="任务草案和 Run 都必须绑定明确 Workspace；当前没有发送任何请求。" />;
  }

  return (
    <div className="path2-panel-stack">
      <div className="path2-panel-intro">
        <div>
          <span className="path2-eyebrow">MISSION FLOW</span>
          <h2>任务草案与 Agent Run</h2>
          <p>草案生成、精确确认和显式 Start 是三个可回读的领域边界；当前服务接缝不可用时只显示真实阻塞。</p>
        </div>
        <StatusPill status={state.missionState.status}>{statusLabel(state.missionState.status)}</StatusPill>
      </div>
      <form className="path2-card" onSubmit={handleSubmit}>
        <div className="path2-card-heading">
          <div>
            <h3>生成任务草案</h3>
            <p>该次 attempt 只会发送下面的原始输入，不自动附带来源或工具上下文。</p>
          </div>
          <span className="path2-step-badge">1</span>
        </div>
        <label className="path2-field-label" htmlFor="mission-original-input">原始任务输入</label>
        <textarea
          id="mission-original-input"
          value={originalInput}
          maxLength={16384}
          rows={4}
          placeholder="描述你希望定义的目标、范围和判断标准…"
          onChange={(event) => {
            setOriginalInput(event.target.value);
            setAttemptSendConfirmed(false);
          }}
        />
        <label className="path2-confirmation-row">
          <input
            type="checkbox"
            checked={attemptSendConfirmed}
            onChange={(event) => setAttemptSendConfirmed(event.target.checked)}
          />
          <span>我明确确认将这段原始输入发送给已配置的 Provider；不包含来源文件。</span>
        </label>
        {state.attemptAction.issue ? <IssueCallout issue={state.attemptAction.issue} /> : null}
        {state.attemptAction.status === "unknown" ? (
          <div className="path2-reconcile-actions">
            {attempt ? <button type="button" className="path2-secondary-button" onClick={() => void state.reconcileAttempt()}>重新读取 Attempt</button> : null}
            <button type="button" className="path2-secondary-button" onClick={state.acknowledgeAttemptUnknown}>我已人工核对，解除阻塞</button>
          </div>
        ) : null}
        <button type="submit" className="path2-primary-button" disabled={!originalInput.trim() || !attemptSendConfirmed || state.attemptAction.status === "submitting" || state.attemptAction.status === "unknown"}>
          {state.attemptAction.status === "submitting" ? "生成中…" : "生成任务草案"}
        </button>
      </form>
      {attempt ? (
        <div className="path2-card">
          <div className="path2-card-heading">
            <div>
              <span className="path2-eyebrow">ATTEMPT</span>
              <h3>当前任务草案 Attempt</h3>
            </div>
            <StatusPill status={attempt.status}>{statusLabel(attempt.status)}</StatusPill>
          </div>
          <div className="path2-attempt-meta">
            <span>attempt <code>{attempt.attempt_id}</code></span>
            {attempt.candidate_version !== null ? <span>version <code>{attempt.candidate_version}</code></span> : null}
            {attempt.candidate_sha256 ? <span>sha256 <code title={attempt.candidate_sha256}>{attempt.candidate_sha256.slice(0, 14)}…</code></span> : null}
          </div>
          {candidate ? <DraftCandidate candidate={candidate} /> : null}
          {attempt.status === "queued" || attempt.status === "running" ? (
            <div className="path2-reconcile-actions">
              <span className="path2-inline-status">Attempt 尚未产生候选内容；页面只按服务端状态等待，不会重复创建。</span>
              <button type="button" className="path2-secondary-button" onClick={() => void state.reconcileAttempt()}>重新读取 Attempt</button>
            </div>
          ) : null}
          {candidate ? (
            <>
              <SourceSelection state={state} idPrefix="confirm-source" title="确认时绑定来源" description="请逐项选择本次确认使用的 SourceRevision；这一步不会启动 Run。" />
              <label className="path2-confirmation-row">
                <input type="checkbox" checked={candidateAcknowledged} onChange={(event) => setCandidateAcknowledged(event.target.checked)} />
                <span>我已核对上面的候选内容、version 和 sha256，并确认这些来源身份。</span>
              </label>
              {state.confirmAction.issue ? <IssueCallout issue={state.confirmAction.issue} title="草案确认未完成" /> : null}
              {state.confirmAction.status === "unknown" ? (
                <div className="path2-reconcile-actions">
                  <button type="button" className="path2-secondary-button" onClick={() => void state.refreshMission()}>重新读取 Mission</button>
                  <button type="button" className="path2-secondary-button" onClick={state.acknowledgeConfirmUnknown}>我已人工核对，解除阻塞</button>
                </div>
              ) : null}
              <button type="button" className="path2-primary-button" disabled={!canConfirm || state.confirmAction.status === "submitting" || state.confirmAction.status === "unknown"} onClick={() => void state.confirmAttempt()}>
                {state.confirmAction.status === "submitting" ? "确认中…" : "确认任务草案"}
              </button>
              <p className="path2-boundary-note">确认不会创建 Run；下一步必须单独明确 Start。</p>
            </>
          ) : null}
        </div>
      ) : null}
      {mission ? (
        <div className="path2-card">
          <div className="path2-card-heading">
            <div>
              <span className="path2-eyebrow">MISSION</span>
              <h3>{mission.title}</h3>
            </div>
            <StatusPill status={mission.status}>{statusLabel(mission.status)}</StatusPill>
          </div>
          <p className="path2-body-copy">{mission.goal}</p>
          <div className="path2-attempt-meta"><span>state version <code>{mission.state_version}</code></span><span>mission <code>{mission.mission_id}</code></span></div>
          {state.missionSnapshotState.issue ? <IssueCallout issue={state.missionSnapshotState.issue} title="Mission 快照不可用" /> : null}
          <SourceSelection state={state} idPrefix="run-source" title="本次 Run 使用的来源" description="Run 外发确认只覆盖下面显式选择的 SourceRevision 和该任务所需上下文。" />
          <label className="path2-confirmation-row">
            <input type="checkbox" checked={runSendConfirmed} onChange={(event) => setRunSendConfirmed(event.target.checked)} />
            <span>我明确确认本次 Run 可以向 Provider 发送所选来源及任务上下文。</span>
          </label>
          {state.runAction.issue ? <IssueCallout issue={state.runAction.issue} title={state.runAction.status === "unknown" ? "Start 结果未知" : "Run 未开始"} /> : null}
          {state.runAction.status === "unknown" ? (
            <div className="path2-reconcile-actions">
              <button type="button" className="path2-secondary-button" onClick={() => void state.refreshMission()}>重新读取 Mission/Run</button>
              <button type="button" className="path2-secondary-button" onClick={state.acknowledgeRunUnknown}>我已人工核对，解除阻塞</button>
            </div>
          ) : null}
          <button type="button" className="path2-primary-button" disabled={!canStart || state.runAction.status === "submitting" || state.runAction.status === "unknown"} onClick={() => void state.startRun(runSendConfirmed)}>
            {state.runAction.status === "submitting" ? "Start 中…" : "明确开始 Run"}
          </button>
          <p className="path2-boundary-note">Start 是独立领域动作；未知结果不会自动重发。Agent 自然停止也不表示 Mission completed。</p>
        </div>
      ) : state.missionState.issue ? (
        <IssueCallout issue={state.missionState.issue} title="Mission 列表不可用" />
      ) : state.missionState.status === "empty" ? (
        <div className="path2-inline-empty">当前 Workspace 尚无 Mission。请先生成并确认任务草案。</div>
      ) : null}
      <RunStatusCard state={state} />
    </div>
  );
}

function evidenceLocatorLabel(locator: EvidenceLocator): string {
  switch (locator.kind) {
    case "csv_rows":
      return `CSV 行 ${locator.row_start}-${locator.row_end}${locator.column ? ` · 列 ${locator.column}` : ""}`;
    case "json_pointer":
      return `JSON Pointer ${locator.pointer || "/"}`;
    case "text_lines":
      return `文本行 ${locator.line_start}-${locator.line_end}`;
  }
}

function EvidenceRefs({ refs, label = "证据引用" }: { refs: EvidenceRef[]; label?: string }) {
  if (refs.length === 0) {
    return <small className="path2-detail-muted">{label}：暂无；证据身份未提供。</small>;
  }
  return (
    <details className="path2-evidence-details">
      <summary>{label} {refs.length} 条</summary>
      <ul className="path2-plain-list path2-evidence-list">
        {refs.map((reference, index) => (
          <li key={`${reference.revision_id}-${reference.sha256}-${index}`}>
            <code title={`${reference.workspace_id}:${reference.source_id}:${reference.revision_id}:${reference.sha256}`}>
              {reference.source_id.slice(0, 8)}… / {reference.revision_id.slice(0, 8)}… / {reference.sha256.slice(0, 12)}…
            </code>
            <span>{evidenceLocatorLabel(reference.locator)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function UnknownItems({ items }: { items: components["schemas"]["UnknownItem"][] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="path2-detail-block">
      <strong>未知项</strong>
      <ul className="path2-plain-list">
        {items.map((item, index) => <li key={`${item.property_path}-${index}`}><code>{item.property_path}</code>：{item.reason}</li>)}
      </ul>
    </div>
  );
}

function StringList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="path2-detail-block">
      <strong>{label}</strong>
      <ul className="path2-plain-list">
        {items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
      </ul>
    </div>
  );
}

function DefinitionPanel({ state, mode }: { state: Path2WorkbenchState; mode: "clarifications" | "contract" }) {
  const draft = state.latestDraft;
  const snapshotIssue = state.missionSnapshotState.issue;
  if (!state.workspaceId) {
    return <WorkspaceRequired copy="定义草案和待确认问题只从当前 Workspace 的真实 Mission/Run 快照读取。" />;
  }
  if (mode === "clarifications") {
    return (
      <div className="path2-panel-stack">
        <div className="path2-panel-intro">
          <div>
            <span className="path2-eyebrow">HUMAN INPUT</span>
            <h2>待澄清问题</h2>
            <p>这里只查看 Agent 提出的公开问题；本卡不实现回答、审批或正式契约发布。</p>
          </div>
          <span className="path2-count">{state.clarifications.length}</span>
        </div>
        {snapshotIssue ? <IssueCallout issue={snapshotIssue} title="澄清快照不可用" /> : null}
        {!snapshotIssue && state.missionSnapshotState.status === "loading" ? (
          <div className="path2-inline-status">正在回读澄清请求…</div>
        ) : null}
        {!snapshotIssue && state.missionSnapshotState.status !== "loading" && state.clarifications.length === 0 ? (
          <div className="path2-inline-empty">当前快照没有待确认问题。</div>
        ) : null}
        {state.clarifications.map((request) => (
          <article className="path2-card path2-clarification-card" key={request.clarification_id}>
            <div className="path2-card-heading">
              <div>
                <h3>澄清请求</h3>
                <p>draft version {request.draft_version} · <code>{request.draft_sha256.slice(0, 14)}…</code></p>
              </div>
              <StatusPill status={request.status}>等待回答</StatusPill>
            </div>
            {request.questions.map((question, index) => (
              <div className="path2-question" key={`${request.clarification_id}-${index}`}>
                <strong>{index + 1}. {question.question}</strong>
                <p>{question.why_needed}</p>
                <small>{question.blocking_impact === "blocking" ? "阻塞性问题" : "非阻塞问题"} · {question.expected_answer_type}</small>
                <dl className="path2-detail-grid">
                  {question.suggested_owner_role ? <div><dt>建议负责人</dt><dd>{question.suggested_owner_role}</dd></div> : null}
                  {question.related_definition_paths.length > 0 ? <div><dt>关联定义路径</dt><dd><ul className="path2-plain-list">{question.related_definition_paths.map((item, itemIndex) => <li key={`${itemIndex}-${item}`}><code>{item}</code></li>)}</ul></dd></div> : null}
                </dl>
                <StringList label="需要的证据" items={question.evidence_requested} />
                <StringList label="示例或选项" items={question.examples_or_options} />
                <EvidenceRefs refs={question.source_refs} />
              </div>
            ))}
          </article>
        ))}
      </div>
    );
  }
  return (
    <div className="path2-panel-stack">
      <div className="path2-panel-intro">
        <div>
          <span className="path2-eyebrow">DEFINITION DRAFT</span>
          <h2>业务契约草案</h2>
          <p>展示带来源的字段与关系候选；语义批准保持 pending，当前不提供发布动作。</p>
        </div>
        {draft ? <StatusPill status={draft.status}>{statusLabel(draft.status)}</StatusPill> : null}
      </div>
      {!draft ? (
        snapshotIssue ? <IssueCallout issue={snapshotIssue} title="定义草案快照不可用" /> :
        state.missionSnapshotState.status === "loading" ? <div className="path2-inline-status">正在回读 DefinitionDraft…</div> :
        <div className="path2-inline-empty">当前快照没有 DefinitionDraft。Run 结束前不显示预置业务定义。</div>
      ) : (
        <>
          <div className="path2-card path2-draft-identity">
            <div><span>draft</span><code>{draft.draft_id}</code></div>
            <div><span>version</span><code>{draft.version}</code></div>
            <div><span>sha256</span><code title={draft.sha256}>{draft.sha256}</code></div>
            <StatusPill status="pending">语义批准待处理</StatusPill>
          </div>
          {draft.unresolved_items.length > 0 ? (
            <div className="path2-card">
              <h3>未决项</h3>
              <ul className="path2-plain-list">{draft.unresolved_items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
            </div>
          ) : null}
          <div className="path2-card">
            <h3>字段候选 <span className="path2-count">{draft.fields.length}</span></h3>
            {draft.fields.length === 0 ? <div className="path2-inline-empty">暂无字段候选。</div> : (
              <div className="path2-definition-list">
                {draft.fields.map((field) => (
                  <article key={field.field_key}>
                    <div className="path2-definition-heading">
                      <strong>{field.name}</strong>
                      <StatusPill status={field.evidence_status}>{statusLabel(field.evidence_status)}</StatusPill>
                    </div>
                    <p>{field.meaning ?? "含义未知"}</p>
                    <dl className="path2-detail-grid">
                      <div><dt>值类型</dt><dd>{field.value_type ?? "未知"}</dd></div>
                      <div><dt>粒度</dt><dd>{field.grain ?? "未知"}</dd></div>
                      <div><dt>规则</dt><dd>{field.rule ?? "未知"}</dd></div>
                      <div><dt>时间基准</dt><dd>{field.time_basis ?? "未知"}</dd></div>
                      <div><dt>空值处理</dt><dd>{field.null_handling ?? "未知"}</dd></div>
                      <div>
                        <dt>来源列</dt>
                        <dd>
                          {field.source_columns.length > 0 ? (
                            <ul className="path2-plain-list">
                              {field.source_columns.map((column, index) => (
                                <li key={`${column.table_id}-${column.column}-${index}`}>
                                  <code title={`${column.source_ref.workspace_id}:${column.source_ref.source_id}:${column.source_ref.revision_id}:${column.source_ref.sha256}`}>
                                    {column.table_id || "根表"}.{column.column}
                                  </code>
                                </li>
                              ))}
                            </ul>
                          ) : "未提供"}
                        </dd>
                      </div>
                    </dl>
                    <UnknownItems items={field.unknowns} />
                    <EvidenceRefs refs={field.source_refs} />
                  </article>
                ))}
              </div>
            )}
          </div>
          <div className="path2-card">
            <h3>关系候选 <span className="path2-count">{draft.relationships.length}</span></h3>
            {draft.relationships.length === 0 ? <div className="path2-inline-empty">暂无关系候选。</div> : (
              <div className="path2-definition-list">
                {draft.relationships.map((relationship) => (
                  <article key={relationship.relationship_key}>
                    <div className="path2-definition-heading">
                      <strong>{relationship.relationship_key}</strong>
                      <StatusPill status={relationship.evidence_status}>{statusLabel(relationship.evidence_status)}</StatusPill>
                    </div>
                    <dl className="path2-detail-grid">
                      <div><dt>左表</dt><dd><code>{relationship.left.table_id || "根表"}</code> · {relationship.left.columns.join(", ") || "列未知"}</dd></div>
                      <div><dt>右表</dt><dd><code>{relationship.right.table_id || "根表"}</code> · {relationship.right.columns.join(", ") || "列未知"}</dd></div>
                      <div><dt>基数</dt><dd>{relationship.observed_cardinality}</dd></div>
                      <div><dt>连接规则</dt><dd>{relationship.join_rule ?? "未知"}</dd></div>
                      <div><dt>粒度说明</dt><dd>{relationship.grain_notes ?? "未知"}</dd></div>
                    </dl>
                    <StringList label="风险" items={relationship.risks} />
                    <UnknownItems items={relationship.unknowns} />
                    <EvidenceRefs refs={relationship.source_refs} />
                  </article>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
export function Path2Workbench({ state, activeArea }: { state: Path2WorkbenchState; activeArea: Path2AreaId }) {
  return (
    <section className="path2-surface" aria-label="Path 2 Workbench">
      <WorkspaceContext state={state} />
      <div className="path2-content" key={state.workspaceId ?? "no-workspace"}>
        {activeArea === "sources" ? <SourceUploadPanel state={state} /> : null}
        {activeArea === "mission" ? <MissionPanel state={state} /> : null}
        {activeArea === "clarifications" ? <DefinitionPanel state={state} mode="clarifications" /> : null}
        {activeArea === "contract" ? <DefinitionPanel state={state} mode="contract" /> : null}
      </div>
    </section>
  );
}

function eventSummary(event: RunEventEnvelope): string {
  switch (event.event_type) {
    case "run_started": return "Run 已进入运行阶段";
    case "message_created": return `公开消息已创建（${event.public_payload.role}）`;
    case "model_started": return `模型轮次 ${event.public_payload.turn_index} 开始`;
    case "model_delta": return `收到公开输出增量：${event.public_payload.content}`;
    case "model_completed": return `模型轮次 ${event.public_payload.turn_index} 完成`;
    case "tool_requested": return `请求工具 ${event.public_payload.name}`;
    case "tool_started": return `开始工具 ${event.public_payload.name}`;
    case "tool_completed": return `工具 ${event.public_payload.call_id} ${event.public_payload.status}`;
    case "tool_failed": return `工具 ${event.public_payload.call_id} 失败：${event.public_payload.error_code}`;
    case "draft_updated": return `定义草案更新至 version ${event.public_payload.version}`;
    case "clarification_requested": return `产生澄清请求，draft version ${event.public_payload.draft_version}`;
    case "run_completed":
    case "run_partial":
    case "run_blocked":
    case "run_failed":
    case "run_cancelled": return `Run 终态：${statusLabel(event.public_payload.status)}`;
  }
}

export function Path2AgentContent({ state }: { state: Path2WorkbenchState }) {
  const run = state.runSnapshot;
  return (
    <div className="path2-agent-state">
      {!state.workspaceId ? <WorkspaceRequired copy="Agent 面板只显示当前 Workspace 的公开状态，不保留跨 Workspace 内容。" /> : null}
      {state.workspaceId && !run && state.runReadbackIssue ? <IssueCallout issue={state.runReadbackIssue} title="Run 指针回读未完成" /> : null}
      {state.workspaceId && !run ? (
        <div className="path2-agent-empty">
          <strong>{state.runAction.status === "unknown" ? "Start 结果未知" : "尚未开始 Run"}</strong>
          <p>
            {state.runAction.status === "unknown"
              ? "当前未能确认是否创建了本次 Run；请先精确回读或人工核对，页面不会自动重发。"
              : "生成并确认 Mission 后，使用中心区域的“明确开始 Run”。"}
          </p>
        </div>
      ) : null}
      {run ? (
        <>
          <div className="path2-agent-run-heading"><div><span className="path2-eyebrow">PUBLIC RUN STATE</span><strong>{run.run_id}</strong></div><StatusPill status={run.status}>{statusLabel(run.status)}</StatusPill></div>
          {state.runEventState.hasSequenceGap ? <div className="path2-event-warning">事件中间有缺口；实时文字可能不完整，摘要以快照为准。</div> : null}
          <div className="path2-agent-events" aria-label="公开 Run 事件">
            {state.runEventState.events.length === 0 ? <span className="path2-inline-status">等待公开事件…</span> : null}
            {state.runEventState.events.map((event) => <article className="path2-agent-event" key={event.event_id}><div><span>#{event.sequence}</span><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleTimeString()}</time></div><p>{eventSummary(event)}</p></article>)}
          </div>
          {run.final_output ? <div className="path2-agent-output"><strong>持久化公开摘要</strong><p>{run.final_output}</p></div> : null}
        </>
      ) : null}
      <div className="path2-agent-composer-note">当前版本不提供自由对话输入；不会把问题或隐藏推理写入 Run。</div>
    </div>
  );
}
