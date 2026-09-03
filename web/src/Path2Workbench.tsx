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
  uploadState: ActionState & { result: SourceBatchResult | null };
  uploadSourceBatch: (request: SourceUploadRequest) => Promise<SourceBatchResult | null>;
  missionState: CollectionState<Mission>;
  selectedMission: Mission | null;
  missionSnapshot: MissionSnapshot | null;
  missionSnapshotState: CollectionState<Mission>;
  attempt: MissionDraftAttempt | null;
  attemptAction: ActionState;
  submitAttempt: (request: MissionDraftAttemptCreateRequest) => Promise<MissionDraftAttempt | null>;
  confirmAction: ActionState;
  confirmAttempt: () => Promise<Mission | null>;
  runSnapshot: RunSnapshot | null;
  runAction: ActionState;
  startRun: (providerSendConfirmed: boolean) => Promise<RunSnapshot | null>;
  cancelAction: ActionState;
  cancelActiveRun: () => Promise<RunSnapshot | null>;
  runConnectionState: "idle" | "connecting" | "connected" | "reconnecting" | "blocked" | "closed";
  runReadbackIssue: ApiIssue | null;
  runEventIssue: string | null;
  runEventState: RunEventState;
  latestDraft: DefinitionDraft | null;
  clarifications: ClarificationRequest[];
};

export function usePath2Workbench(
  workspace: Workspace | null,
  api: Path2Api = productionPath2Api,
  eventSourceFactory: RunEventSourceFactory = browserRunEventSourceFactory,
): Path2WorkbenchState {
  const workspaceId = workspace?.workspace_id ?? null;
  const epochRef = useRef(0);
  const clientRequestIdRef = useRef<string | null>(null);
  const runRef = useRef<RunSnapshot | null>(null);
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

  useEffect(() => {
    runRef.current = runSnapshot;
  }, [runSnapshot]);

  const loadMissionSnapshot = useCallback(
    async (currentWorkspaceId: string, missionId: string, epoch: number): Promise<void> => {
      try {
        const next = await api.fetchMissionSnapshot(currentWorkspaceId, missionId);
        if (epoch !== epochRef.current) {
          return;
        }
        if (!missionSnapshotMatchesIdentity(next, currentWorkspaceId, missionId)) {
          setMissionSnapshotState({ status: "failed", items: [], issue: scopeIssue() });
          return;
        }
        setMissionSnapshot(next);
        setMissionSnapshotState({
          status: "ready",
          items: [next.mission],
          issue: null,
        });
        if (next.latest_run) {
          runRef.current = next.latest_run;
          setRunSnapshot(next.latest_run);
          setRunEventState(createRunEventState(next.latest_run.last_sequence));
        }
      } catch (error: unknown) {
        if (epoch === epochRef.current) {
          setMissionSnapshotState(collectionForError(error));
        }
      }
    },
    [api],
  );

  useEffect(() => {
    const epoch = epochRef.current + 1;
    epochRef.current = epoch;
    let cancelled = false;
    setLoadedWorkspaceId(workspaceId);
    clientRequestIdRef.current = null;
    runRef.current = null;
    setSourceState(workspaceId ? loadingCollection() : emptyCollection());
    setSourceArtifacts({});
    setSelectedSourceIds([]);
    setUploadState({ ...emptyAction(), result: null });
    setMissionState(workspaceId ? loadingCollection() : emptyCollection());
    setSelectedMissionId(null);
    setMissionSnapshot(null);
    setMissionSnapshotState(workspaceId ? loadingCollection() : emptyCollection());
    setAttempt(null);
    setAttemptAction(emptyAction());
    setConfirmAction(emptyAction());
    setRunSnapshot(null);
    setRunAction(emptyAction());
    setCancelAction(emptyAction());
    setRunReadbackIssue(null);
    setRunEventIssue(null);
    setRunEventState(createRunEventState());
    setRunConnectionState(workspaceId ? "connecting" : "idle");

    if (!workspaceId) {
      return () => {
        cancelled = true;
      };
    }

    void api.fetchSources(workspaceId)
      .then((items) => {
        if (cancelled || epoch !== epochRef.current) {
          return;
        }
        if (!items.every((item) => sourceRevisionMatchesWorkspace(item, workspaceId))) {
          setSourceState({ status: "failed", items: [], issue: scopeIssue() });
          return;
        }
        setSourceState({ status: items.length > 0 ? "ready" : "empty", items, issue: null });
      })
      .catch((error: unknown) => {
        if (!cancelled && epoch === epochRef.current) {
          setSourceState(collectionForError(error));
        }
      });

    void api.fetchMissions(workspaceId)
      .then((items) => {
        if (cancelled || epoch !== epochRef.current) {
          return;
        }
        if (!items.every((item) => missionMatchesWorkspace(item, workspaceId))) {
          const failed = { status: "failed" as const, items: [], issue: scopeIssue() };
          setMissionState(failed);
          setMissionSnapshotState(failed);
          return;
        }
        setMissionState({ status: items.length > 0 ? "ready" : "empty", items, issue: null });
        if (items.length === 0) {
          setMissionSnapshotState({ status: "empty", items: [], issue: null });
          return;
        }
        const selected = items[0];
        setSelectedMissionId(selected.mission_id);
        setMissionSnapshotState({ status: "loading", items: [selected], issue: null });
        void loadMissionSnapshot(workspaceId, selected.mission_id, epoch);
      })
      .catch((error: unknown) => {
        if (!cancelled && epoch === epochRef.current) {
          const failed = collectionForError<Mission>(error);
          setMissionState(failed);
          setMissionSnapshotState(failed);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [api, loadMissionSnapshot, workspaceId]);

  useEffect(() => {
    clientRequestIdRef.current = null;
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
      const epoch = epochRef.current;
      const revision = sourceState.items.find((candidate) => candidate.revision_id === revisionId);
      if (!revision) {
        return;
      }
      setSourceArtifacts((current) => ({
        ...current,
        [revisionId]: { status: "loading", artifact: null, excerpt: null, issue: null },
      }));
      try {
        const artifact = await executeExplicitRequest(() => api.fetchSourceArtifact(workspaceId, revisionId));
        if (epoch !== epochRef.current) {
          return;
        }
        if (
          artifact.source_ref.workspace_id !== workspaceId ||
          artifact.source_ref.revision_id !== revisionId ||
          artifact.source_ref.source_id !== revision.source_id
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
        if (epoch === epochRef.current) {
          const issue = issueFromError(error);
          setSourceArtifacts((current) => ({
            ...current,
            [revisionId]: { status: issue.kind, artifact: null, excerpt: null, issue },
          }));
        }
      }
    },
    [api, sourceState.items, workspaceId],
  );

  const readExcerpt = useCallback(
    async (revisionId: string): Promise<void> => {
      if (!workspaceId) {
        return;
      }
      const epoch = epochRef.current;
      const revision = sourceState.items.find((candidate) => candidate.revision_id === revisionId);
      if (!revision) {
        return;
      }
      const currentArtifact = sourceArtifacts[revisionId];
      if (!currentArtifact?.artifact) {
        return;
      }
      try {
        const excerpt = await executeExplicitRequest(() =>
          api.readSourceExcerpt(workspaceId, revisionId, defaultExcerptRequest(revision)),
        );
        if (epoch !== epochRef.current) {
          return;
        }
        if (
          excerpt.source_ref.workspace_id !== workspaceId ||
          excerpt.source_ref.revision_id !== revisionId ||
          excerpt.source_ref.source_id !== revision.source_id
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
        if (epoch === epochRef.current) {
          const issue = issueFromError(error);
          setSourceArtifacts((current) => ({
            ...current,
            [revisionId]: { ...current[revisionId], status: issue.kind, issue },
          }));
        }
      }
    },
    [api, sourceArtifacts, sourceState.items, workspaceId],
  );

  const uploadSourceBatch = useCallback(
    async (request: SourceUploadRequest): Promise<SourceBatchResult | null> => {
      if (!workspaceId || uploadState.status === "submitting") {
        return null;
      }
      if (!request.local_read_confirmed) {
        const issue: ApiIssue = {
          kind: "failed",
          code: "local_read_confirmation_required",
          message: "必须先明确确认只读取选定的本地文件；该确认不等于允许 Provider 外发。",
        };
        setUploadState({ ...actionForIssue(issue), result: null });
        return null;
      }
      const epoch = epochRef.current;
      setUploadState({ status: "submitting", issue: null, result: null });
      try {
        const result = await executeExplicitRequest(() => api.uploadSources(workspaceId, request));
        if (epoch !== epochRef.current) {
          return null;
        }
        setUploadState({ status: "success", issue: null, result });
        const accepted = result.items.flatMap((item) => (item.revision ? [item.revision] : []));
        if (!accepted.every((item) => sourceRevisionMatchesWorkspace(item, workspaceId))) {
          const issue = scopeIssue();
          setUploadState({ status: issue.kind, issue, result: null });
          return null;
        }
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
        if (epoch === epochRef.current) {
          setUploadState({ ...actionForIssue(issueFromError(error)), result: null });
        }
        return null;
      }
    },
    [api, uploadState.status, workspaceId],
  );

  const submitAttempt = useCallback(
    async (request: MissionDraftAttemptCreateRequest): Promise<MissionDraftAttempt | null> => {
      if (!workspaceId || attemptAction.status === "submitting") {
        return null;
      }
      if (!request.provider_send_confirmed) {
        const issue: ApiIssue = {
          kind: "failed",
          code: "provider_confirmation_required",
          message: "必须明确确认只发送原始任务输入；不会自动附带来源文件。",
        };
        setAttemptAction(actionForIssue(issue));
        return null;
      }
      const epoch = epochRef.current;
      setAttemptAction({ status: "submitting", issue: null });
      try {
        const result = await executeExplicitRequest(() =>
          api.createMissionDraftAttempt(workspaceId, request),
        );
        if (epoch !== epochRef.current) {
          return null;
        }
        setAttempt(result);
        setAttemptAction({
          status: result.status === "ready" ? "success" : result.status === "blocked" ? "blocked" : "failed",
          issue: result.error_code
            ? { kind: result.status === "blocked" ? "blocked" : "failed", code: result.error_code, message: result.error_code }
            : null,
        });
        return result;
      } catch (error: unknown) {
        if (epoch === epochRef.current) {
          setAttemptAction(actionForIssue(issueFromError(error)));
        }
        return null;
      }
    },
    [api, attemptAction.status, workspaceId],
  );

  const confirmAttempt = useCallback(async (): Promise<Mission | null> => {
    if (!workspaceId || !attempt || !selectedSourceRefs.length || confirmAction.status === "submitting") {
      return null;
    }
    const request = buildConfirmRequest(attempt, selectedSourceRefs);
    if (!request) {
      setConfirmAction({
        status: "failed",
        issue: { kind: "failed", code: "draft_not_ready", message: "当前草案没有可确认的 version/hash。" },
      });
      return null;
    }
    const epoch = epochRef.current;
    setConfirmAction({ status: "submitting", issue: null });
    try {
      const result = await executeExplicitRequest(() =>
        api.confirmMissionDraftAttempt(workspaceId, attempt.attempt_id, request),
      );
      if (epoch !== epochRef.current) {
        return null;
      }
      if (!missionMatchesWorkspace(result, workspaceId)) {
        const issue = scopeIssue();
        setConfirmAction(actionForIssue(issue));
        return null;
      }
      setAttempt((current) =>
        current
          ? { ...current, status: "confirmed", mission_id: result.mission_id }
          : current,
      );
      setConfirmAction({ status: "success", issue: null });
      setSelectedMissionId(result.mission_id);
      setMissionSnapshot(null);
      setMissionSnapshotState({ status: "loading", items: [result], issue: null });
      void loadMissionSnapshot(workspaceId, result.mission_id, epoch);
      return result;
    } catch (error: unknown) {
      if (epoch === epochRef.current) {
        setConfirmAction(actionForIssue(issueFromError(error)));
      }
      return null;
    }
  }, [api, attempt, confirmAction.status, loadMissionSnapshot, selectedSourceRefs, workspaceId]);

  const refreshRunSnapshot = useCallback(
    async (identity: RunIdentity): Promise<void> => {
      const epoch = epochRef.current;
      try {
        const next = await executeExplicitRequest(() =>
          api.fetchRunSnapshot(identity.workspaceId, identity.missionId, identity.runId),
        );
        if (epoch !== epochRef.current || runRef.current?.run_id !== identity.runId) {
          return;
        }
        if (!runSnapshotMatchesIdentity(next, identity)) {
          setRunReadbackIssue(scopeIssue());
          return;
        }
        runRef.current = next;
        setRunSnapshot(next);
        setRunEventState((current) => mergeRunSnapshot(current, next));
        setRunReadbackIssue(null);
      } catch (error: unknown) {
        if (epoch === epochRef.current && runRef.current?.run_id === identity.runId) {
          setRunReadbackIssue(issueFromError(error));
        }
      }
    },
    [api],
  );

  const startRunAction = useCallback(
    async (providerSendConfirmed: boolean): Promise<RunSnapshot | null> => {
      if (
        !workspaceId ||
        !selectedMission ||
        missionSnapshotState.status !== "ready" ||
        !selectedSourceRefs.length ||
        runAction.status === "submitting"
      ) {
        return null;
      }
      if (!providerSendConfirmed) {
        setRunAction({
          status: "failed",
          issue: { kind: "failed", code: "provider_confirmation_required", message: "必须明确确认本次 Run 的 Provider 外发范围。" },
        });
        return null;
      }
      const clientRequestId = clientRequestIdRef.current ?? createClientRequestId();
      clientRequestIdRef.current = clientRequestId;
      const request = buildRunStartRequest(
        selectedMission,
        selectedSourceRefs,
        providerSendConfirmed,
        clientRequestId,
      );
      const epoch = epochRef.current;
      setRunAction({ status: "submitting", issue: null });
      try {
        const result = await executeExplicitRequest(() =>
          api.startRun(workspaceId, selectedMission.mission_id, request),
        );
        if (epoch !== epochRef.current) {
          return null;
        }
        if (!runSnapshotMatchesIdentity(result, {
          workspaceId,
          missionId: selectedMission.mission_id,
          runId: result.run_id,
        })) {
          const issue = scopeIssue();
          setRunAction(actionForIssue(issue));
          return null;
        }
        runRef.current = result;
        setRunSnapshot(result);
        setRunEventState(createRunEventState(result.last_sequence));
        setRunReadbackIssue(null);
        setRunEventIssue(null);
        setRunAction({ status: "success", issue: null });
        setCancelAction(emptyAction());
        return result;
      } catch (error: unknown) {
        if (epoch === epochRef.current) {
          setRunAction(actionForIssue(issueFromError(error)));
        }
        return null;
      }
    },
    [api, missionSnapshotState.status, runAction.status, selectedMission, selectedSourceRefs, workspaceId],
  );

  const cancelActiveRun = useCallback(async (): Promise<RunSnapshot | null> => {
    if (!workspaceId || !runSnapshot || cancelAction.status === "submitting") {
      return null;
    }
    if (runSnapshot.status !== "queued" && runSnapshot.status !== "running") {
      return null;
    }
    const identity: RunIdentity = {
      workspaceId,
      missionId: runSnapshot.mission_id,
      runId: runSnapshot.run_id,
    };
    const epoch = epochRef.current;
    setCancelAction({ status: "submitting", issue: null });
    try {
      const result = await executeExplicitRequest(() =>
        api.cancelRun(identity.workspaceId, identity.missionId, identity.runId),
      );
      if (epoch !== epochRef.current || runRef.current?.run_id !== identity.runId) {
        return null;
      }
      if (!runSnapshotMatchesIdentity(result, identity)) {
        const issue = scopeIssue();
        setCancelAction(actionForIssue(issue));
        return null;
      }
      runRef.current = result;
      setRunSnapshot(result);
      setRunEventState((current) => mergeRunSnapshot(current, result));
      setCancelAction({ status: "success", issue: null });
      return result;
    } catch (error: unknown) {
      if (epoch === epochRef.current) {
        setCancelAction(actionForIssue(issueFromError(error)));
      }
      return null;
    }
  }, [api, cancelAction.status, runSnapshot, workspaceId]);

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
      setRunEventState((current) => acceptRunEvent(current, parsed, identity));
      if (parsed.event_type in TERMINAL_EVENT_STATUS) {
        void refreshRunSnapshot(identity);
        source.close();
      }
    };
    const handleOpen = () => {
      if (!cancelled) {
        opened = true;
        setRunConnectionState("connected");
      }
    };
    const handleError = () => {
      if (cancelled) {
        return;
      }
      setRunConnectionState(opened ? "reconnecting" : "blocked");
      void refreshRunSnapshot(identity);
      if (!opened) {
        source.close();
      }
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
  }, [eventSourceFactory, refreshRunSnapshot, runSnapshot, workspaceId]);

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
    uploadState: visibleUploadState,
    uploadSourceBatch,
    missionState: visibleMissionState,
    selectedMission: visibleSelectedMission,
    missionSnapshot: visibleMissionSnapshot,
    missionSnapshotState: visibleMissionSnapshotState,
    attempt: visibleAttempt,
    attemptAction: visibleAttemptAction,
    submitAttempt,
    confirmAction: visibleConfirmAction,
    confirmAttempt,
    runSnapshot: visibleRunSnapshot,
    runAction: visibleRunAction,
    startRun: startRunAction,
    cancelAction: visibleCancelAction,
    cancelActiveRun,
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
  return (
    <div className="path2-context-strip">
      <div>
        <span className="path2-eyebrow">PATH 2 WORKBENCH</span>
        <strong>{state.workspaceId ? "当前 Workspace 工作区" : "等待 Workspace"}</strong>
      </div>
      <div className="path2-context-meta">
        <StatusPill status={state.workspaceId ? "blocked" : "empty"}>
          {state.workspaceId ? "W0.2 接缝不可用" : "未选择"}
        </StatusPill>
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

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
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
    const encoded: SourceUploadFile[] = [];
    for (const candidate of candidates) {
      if (!candidate.mediaType) {
        setLocalError("至少一个文件没有受支持的媒体类型；未提交本批请求。");
        return;
      }
      setCandidates((current) =>
        current.map((item) => item.id === candidate.id ? { ...item, status: "reading", message: null } : item),
      );
      try {
        const contentBase64 = await encodeFile(candidate.file);
        encoded.push({
          original_name: candidate.file.name,
          media_type: candidate.mediaType,
          content_base64: contentBase64,
        });
        setCandidates((current) =>
          current.map((item) => item.id === candidate.id ? { ...item, status: "ready" } : item),
        );
      } catch {
        setCandidates((current) =>
          current.map((item) => item.id === candidate.id ? { ...item, status: "failed", message: "本地文件读取失败。" } : item),
        );
        setLocalError("至少一个文件读取失败；未提交本批请求。");
        return;
      }
    }
    await state.uploadSourceBatch(buildSourceUploadRequest(encoded, localReadConfirmed));
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
            <input type="file" multiple accept=".csv,.json,.md,.markdown,.txt" onChange={handleFileChange} />
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
            state.uploadState.status === "submitting" ||
            !state.workspaceId ||
            candidates.length === 0 ||
            !localReadConfirmed ||
            candidates.some((candidate) => candidate.status === "blocked")
          }
        >
          {state.uploadState.status === "submitting" ? "读取中…" : "导入本地资料"}
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
    return <div className="path2-inline-empty">尚未创建 Run。确认 Mission 后，仍需单独点击“明确开始 Run”。</div>;
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
        <button type="button" className="path2-danger-button" onClick={() => void state.cancelActiveRun()} disabled={state.cancelAction.status === "submitting"}>
          {state.cancelAction.status === "submitting" ? "取消中…" : "取消 Run"}
        </button>
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
        <button type="submit" className="path2-primary-button" disabled={!originalInput.trim() || !attemptSendConfirmed || state.attemptAction.status === "submitting"}>
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
          {candidate ? (
            <>
              <SourceSelection state={state} idPrefix="confirm-source" title="确认时绑定来源" description="请逐项选择本次确认使用的 SourceRevision；这一步不会启动 Run。" />
              <label className="path2-confirmation-row">
                <input type="checkbox" checked={candidateAcknowledged} onChange={(event) => setCandidateAcknowledged(event.target.checked)} />
                <span>我已核对上面的候选内容、version 和 sha256，并确认这些来源身份。</span>
              </label>
              {state.confirmAction.issue ? <IssueCallout issue={state.confirmAction.issue} title="草案确认未完成" /> : null}
              <button type="button" className="path2-primary-button" disabled={!canConfirm || state.confirmAction.status === "submitting"} onClick={() => void state.confirmAttempt()}>
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
          {state.runAction.issue ? <IssueCallout issue={state.runAction.issue} title="Run 未开始" /> : null}
          <button type="button" className="path2-primary-button" disabled={!canStart || state.runAction.status === "submitting"} onClick={() => void state.startRun(runSendConfirmed)}>
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

function DefinitionPanel({ state, mode }: { state: Path2WorkbenchState; mode: "clarifications" | "contract" }) {
  const draft = state.latestDraft;
  if (!state.workspaceId) {
    return <WorkspaceRequired copy="定义草案和待确认问题只从当前 Workspace 的真实 Mission/Run 快照读取。" />;
  }
  if (mode === "clarifications") {
    return (
      <div className="path2-panel-stack">
        <div className="path2-panel-intro"><div><span className="path2-eyebrow">HUMAN INPUT</span><h2>待澄清问题</h2><p>这里只查看 Agent 提出的公开问题；本卡不实现回答、审批或正式契约发布。</p></div><span className="path2-count">{state.clarifications.length}</span></div>
        {state.missionSnapshotState.issue ? <IssueCallout issue={state.missionSnapshotState.issue} /> : null}
        {state.clarifications.length === 0 ? <div className="path2-inline-empty">当前快照没有待确认问题。</div> : null}
        {state.clarifications.map((request) => (
          <article className="path2-card path2-clarification-card" key={request.clarification_id}>
            <div className="path2-card-heading"><div><h3>澄清请求</h3><p>draft version {request.draft_version} · <code>{request.draft_sha256.slice(0, 14)}…</code></p></div><StatusPill status={request.status}>等待回答</StatusPill></div>
            {request.questions.map((question, index) => <div className="path2-question" key={`${request.clarification_id}-${index}`}><strong>{index + 1}. {question.question}</strong><p>{question.why_needed}</p><small>{question.blocking_impact === "blocking" ? "阻塞性问题" : "非阻塞问题"} · {question.expected_answer_type}</small></div>)}
          </article>
        ))}
      </div>
    );
  }
  return (
    <div className="path2-panel-stack">
      <div className="path2-panel-intro"><div><span className="path2-eyebrow">DEFINITION DRAFT</span><h2>业务契约草案</h2><p>展示带来源的字段与关系候选；语义批准保持 pending，当前不提供发布动作。</p></div>{draft ? <StatusPill status={draft.status}>{statusLabel(draft.status)}</StatusPill> : null}</div>
      {!draft ? <div className="path2-inline-empty">当前快照没有 DefinitionDraft。Run 结束前不显示预置业务定义。</div> : <>
        <div className="path2-card path2-draft-identity"><div><span>draft</span><code>{draft.draft_id}</code></div><div><span>version</span><code>{draft.version}</code></div><div><span>sha256</span><code title={draft.sha256}>{draft.sha256}</code></div><StatusPill status="pending">语义批准待处理</StatusPill></div>
        {draft.unresolved_items.length > 0 ? <div className="path2-card"><h3>未决项</h3><ul className="path2-plain-list">{draft.unresolved_items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div> : null}
        <div className="path2-card"><h3>字段候选 <span className="path2-count">{draft.fields.length}</span></h3>{draft.fields.length === 0 ? <div className="path2-inline-empty">暂无字段候选。</div> : <div className="path2-definition-list">{draft.fields.map((field) => <article key={field.field_key}><div className="path2-definition-heading"><strong>{field.name}</strong><StatusPill status={field.evidence_status}>{statusLabel(field.evidence_status)}</StatusPill></div><p>{field.meaning ?? "含义未知"}</p><small>{field.value_type ?? "类型未知"} · {field.grain ?? "粒度未知"} · 来源 {field.source_refs.length} 条</small></article>)}</div>}</div>
        <div className="path2-card"><h3>关系候选 <span className="path2-count">{draft.relationships.length}</span></h3>{draft.relationships.length === 0 ? <div className="path2-inline-empty">暂无关系候选。</div> : <div className="path2-definition-list">{draft.relationships.map((relationship) => <article key={relationship.relationship_key}><div className="path2-definition-heading"><strong>{relationship.relationship_key}</strong><StatusPill status={relationship.evidence_status}>{statusLabel(relationship.evidence_status)}</StatusPill></div><p>{relationship.left.table_id} ↔ {relationship.right.table_id} · {relationship.observed_cardinality}</p><small>{relationship.join_rule ?? "连接规则未知"} · 来源 {relationship.source_refs.length} 条</small></article>)}</div>}</div>
      </>}
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
      {state.workspaceId && !run ? <div className="path2-agent-empty"><strong>尚未开始 Run</strong><p>生成并确认 Mission 后，使用中心区域的“明确开始 Run”。</p></div> : null}
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
