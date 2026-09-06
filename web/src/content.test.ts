import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

import {
  AGENT_COPY,
  AREA_CONTENT,
  AREA_NAV,
  AREA_NAV_PRESENTATION,
  ICON_URLS,
  OBJECT_TABS,
  navigationForAreas,
  relationshipGraphResolution,
} from "./App";
import App from "./App";
import { sourceIdentityFromRevision, type DefinitionDraft, type SourceRevision } from "./Path2Workbench";
import type { WorkbenchSnapshot } from "./api/client";

const graphWorkspaceId = "11111111-1111-4111-8111-111111111111";
const graphSource = (sourceId: string, revisionId: string, hash: string, name: string): SourceRevision => ({
  workspace_id: graphWorkspaceId,
  source_id: sourceId,
  revision_id: revisionId,
  original_name: name,
  media_type: "text/csv",
  byte_size: 10,
  sha256: hash,
  observed_at: "2026-09-03T10:00:00Z",
  effective_time: null,
  permission_status: "read_allowed",
  parse_status: "ready",
  parser_version: "csv-v1",
});

const graphLeft = graphSource(
  "44444444-4444-4444-8444-444444444444",
  "55555555-5555-4555-8555-555555555555",
  "a".repeat(64),
  "customers.csv",
);
const graphRight = graphSource(
  "66666666-6666-4666-8666-666666666666",
  "77777777-7777-4777-8777-777777777777",
  "b".repeat(64),
  "orders.csv",
);
const graphCandidate = (rightHash = graphRight.sha256): DefinitionDraft["relationships"][number] => ({
  relationship_key: "customers_to_orders",
  left: { source_ref: sourceIdentityFromRevision(graphLeft), table_id: "customers", columns: ["customer_id"] },
  right: { source_ref: { ...sourceIdentityFromRevision(graphRight), sha256: rightHash }, table_id: "orders", columns: ["customer_id"] },
  observed_cardinality: "one_to_many",
  join_rule: "customers.customer_id = orders.customer_id",
  grain_notes: "左侧一行代表一个客户。",
  evidence_status: "candidate",
  source_refs: [],
  risks: [],
  unknowns: [],
});

describe("ContextOx Workbench v3 content boundaries", () => {
  it("keeps the four primary modules in the approved rail order", () => {
    expect(AREA_NAV.map((area) => area.id)).toEqual([
      "sources",
      "mission",
      "clarifications",
      "contract",
    ]);
    expect(AREA_NAV.map((area) => area.label)).toEqual([
      "资料来源",
      "任务",
      "待澄清",
      "业务契约",
    ]);
  });

  it("follows API area order while using the presentation copy map", () => {
    const apiAreas: WorkbenchSnapshot["areas"] = [
      { id: "contract", label: "backend contract", description: "backend description", status: "not_implemented" },
      { id: "sources", label: "backend sources", description: "backend description", status: "not_implemented" },
    ];

    expect(navigationForAreas(apiAreas)).toEqual([
      { id: "contract", ...AREA_NAV_PRESENTATION.contract },
      { id: "sources", ...AREA_NAV_PRESENTATION.sources },
    ]);
  });

  it("keeps the Mission relationship shell free of synthetic result data", () => {
    expect(OBJECT_TABS).toEqual([
      { id: "mission", label: "任务工作区" },
      { id: "relationship", label: "关系与字段" },
      { id: "history", label: "执行历史" },
    ]);
  });

  it("keeps every primary module explicit and truthful", () => {
    expect(Object.values(AREA_CONTENT)).toHaveLength(4);
    expect(Object.values(AREA_CONTENT).every((area) => area.emptyTitle && area.emptyBody)).toBe(true);
    expect(Object.values(AREA_CONTENT).map((area) => area.label)).toEqual([
      "资料来源",
      "任务",
      "待澄清",
      "业务契约",
    ]);
  });

  it("keeps the approved static icon set local and package-free", () => {
    expect(Object.keys(ICON_URLS)).toEqual([
      "archive",
      "target",
      "question-mark-circled",
      "file-text",
      "reader",
      "cube",
      "mix",
      "double-arrow-left",
      "double-arrow-right",
      "chevron-down",
    ]);
    expect(Object.values(ICON_URLS).every((url) => url.includes("/assets/icons/"))).toBe(true);
  });

  it("renders the icon navigation and truthful control seams", () => {
    const markup = renderToStaticMarkup(createElement(App));

    expect(markup.match(/primary-nav-icon/g)).toHaveLength(4);
    expect(markup).not.toContain("graph-node-icon");
    expect(markup).toContain("执行历史");
    expect(markup).toContain("aria-label=\"任务 Agent 对话\"");
    expect(markup).toContain("aria-expanded=\"true\"");
    expect(markup).toContain("aria-controls=\"agent-panel-content\"");
    expect(markup).toContain("data-path2-state=\"workbench\"");
    expect(markup).toContain("围绕一个任务展开分析");
    expect(markup).not.toContain("尚无第三方关系来源");
    expect(markup).not.toContain("客户主数据.csv");
    expect(markup).not.toContain("演示模式");
    expect(markup).not.toContain("object-tab-close");
    expect(markup).not.toContain("object-tab-add");
    expect(markup).not.toContain("aria-label=\"打开对象\"");
    expect(markup).not.toContain(">关闭<");
    expect(markup).not.toContain(">打开<");
  });

  it("draws relationship edges only for candidate identities backed by current sources", () => {
    const candidate = graphCandidate();
    expect(relationshipGraphResolution([candidate, { ...candidate, relationship_key: "second_candidate" }], [graphRight, graphLeft])).toMatchObject({
      candidateCount: 2,
      leftSourceMatched: true,
      rightSourceMatched: true,
      completeRelationship: true,
    });
    expect(relationshipGraphResolution([candidate], [graphLeft])).toMatchObject({
      candidateCount: 1,
      leftSourceMatched: true,
      rightSourceMatched: false,
      completeRelationship: false,
    });
    expect(relationshipGraphResolution([graphCandidate("c".repeat(64))], [graphLeft, graphRight])).toMatchObject({
      leftSourceMatched: true,
      rightSourceMatched: false,
      completeRelationship: false,
    });
    expect(relationshipGraphResolution([], [graphLeft, graphRight])).toMatchObject({
      candidateCount: 0,
      relationship: null,
      completeRelationship: false,
    });
  });

  it("labels the Agent panel as task dialogue", () => {
    expect(AGENT_COPY).toEqual({
      title: "数契 Agent",
      mode: "任务对话",
      composerPlaceholder: "围绕当前任务继续提问",
    });
  });
});
