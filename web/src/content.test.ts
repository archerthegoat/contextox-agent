import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

import {
  RelationshipGraph,
  AGENT_COPY,
  AREA_CONTENT,
  AREA_NAV,
  AREA_NAV_PRESENTATION,
  ICON_URLS,
  OBJECT_TABS,
  navigationForAreas,
  relationshipGraphResolution,
  resolveWorkbenchProgress,
  resolveWorkbenchSummary,
  resolveFollowTarget,
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
      { id: "mission", label: "当前进展" },
      { id: "relationship", label: "关系与字段" },
      { id: "history", label: "过程记录" },
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

    expect(markup).toContain('aria-label="工作区导航"');
    expect(markup).toContain('role="separator"');
    expect(markup).not.toContain('class="primary-rail"');
    expect(markup).not.toContain("graph-node-icon");
    expect(markup).toContain("过程记录");
    expect(markup).toContain("aria-label=\"Agent 对话\"");
    expect(markup).toContain("展开对话");
    expect(markup).toContain("aria-controls=\"agent-panel-content\"");
    expect(markup).toContain("data-path2-state=\"workbench\"");
    expect(markup).toContain("说说你想弄清什么，也可以先添加资料");
    expect(markup).toContain('id="conversation-input"');
    expect(markup).not.toContain("生成任务草案");
    expect(markup).not.toContain("尚无第三方关系来源");
    expect(markup).not.toContain("客户主数据.csv");
    expect(markup).not.toContain("演示模式");
    expect(markup).not.toContain("object-tab-close");
    expect(markup).not.toContain("object-tab-add");
    expect(markup).not.toContain("aria-label=\"打开对象\"");
    expect(markup).toContain('aria-label="关闭模型设置"');
    expect(markup).toContain('aria-labelledby="model-settings-title"');
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

  it("moves a review-ready draft out of clarification even while the mission awaits human review", () => {
    const waiting = resolveWorkbenchProgress({
      missionSnapshot: null,
      selectedMission: {mission_id:"mission", title:"候选任务", status:"waiting_for_human"} as never,
      runSnapshot: {status:"waiting_for_human"} as never,
      latestDraft: {version:2, status:"draft"} as never,
      clarifications: [{questions:[{question:"时间范围？"}]}] as never,
    });
    expect(waiting).toMatchObject({current:2, waitingForAnswers:true, description:"等待业务回答 · 1 个已记录问题"});

    const review = resolveWorkbenchProgress({
      missionSnapshot: null,
      selectedMission: {mission_id:"mission", title:"候选任务", status:"waiting_for_human"} as never,
      runSnapshot: {status:"waiting_for_human"} as never,
      latestDraft: {version:3, status:"in_review"} as never,
      clarifications: [],
    });
    expect(review).toMatchObject({current:3, waitingForAnswers:false, description:"候选成果等待核对"});
  });

  it("summarizes the current work in business language without leaking versions", () => {
    const empty = resolveWorkbenchSummary({missionSnapshot:null,selectedMission:null,runSnapshot:null,latestDraft:null,clarifications:[]}, 0);
    expect(empty).toEqual({now:"还没有开始分析",next:"在右侧说目标，或先添加资料",result:"本次对话还没有选择资料"});

    const waiting = resolveWorkbenchSummary({missionSnapshot:null,selectedMission:{mission_id:"mission"} as never,runSnapshot:{status:"waiting_for_human"} as never,latestDraft:{fields:[],relationships:[]} as never,clarifications:[{questions:[{},{}]}] as never}, 2);
    expect(waiting).toMatchObject({now:"已找到需要业务判断的事项",next:"回答 2 个会改变结果的问题"});

    const result = resolveWorkbenchSummary({missionSnapshot:null,selectedMission:{mission_id:"mission"} as never,runSnapshot:{status:"completed"} as never,latestDraft:{version:9,fields:[{unknowns:[{}]}],relationships:[{unknowns:[]}]} as never,clarifications:[]}, 2);
    expect(result).toEqual({now:"候选成果已经更新",next:"核对变化，继续补充仍未知的口径",result:"1 个字段 · 1 条关系 · 1 项未知"});
    expect(JSON.stringify(result)).not.toContain("9");
  });

  it("shows a newly updated result while keeping its next clarification visible", () => {
    const target=resolveFollowTarget({runSnapshot:{status:"partial",approved_answers:[{}]} as never,latestDraft:{version:2} as never,clarifications:[{questions:[{question:"时间范围？"}]}] as never});
    expect(target).toEqual({area:"mission",tab:"relationship"});
    expect(resolveFollowTarget({runSnapshot:{status:"waiting_for_human"} as never,latestDraft:{version:1} as never,clarifications:[{questions:[{question:"退款规则？"}]}] as never})).toEqual({area:"clarifications",tab:"mission"});
  });
});


describe("field semantic review", () => {
  it("opens the recorded answer impact only for the current scoped terminal analysis",()=>{
    const path2={workspaceId:"ws",selectedMission:{mission_id:"mission"},latestDraft:null,sourceState:{items:[]},runSnapshot:{workspace_id:"ws",mission_id:"mission",run_id:"run",status:"completed",approved_answers:[{}]}} as unknown as import("./Path2Workbench").Path2WorkbenchState;
    const render=()=>renderToStaticMarkup(createElement(RelationshipGraph,{path2,onReference:()=>{}}));
    expect(render()).toContain("正在读取本轮回答及草案变化");
    path2.runSnapshot!.status="running";expect(render()).not.toContain("正在读取本轮回答及草案变化");
    path2.runSnapshot!.status="partial";expect(render()).toContain("正在读取本轮回答及草案变化");
    path2.runSnapshot!.workspace_id="other";expect(render()).not.toContain("正在读取本轮回答及草案变化");
    path2.runSnapshot!.workspace_id="ws";path2.runSnapshot!.approved_answers=[];expect(render()).not.toContain("正在读取本轮回答及草案变化");
  });
  it("shows a supplied missing-value policy and a separate unknown dimension without claiming approval", () => {
    const field: DefinitionDraft["fields"][number] = {field_key:"amount", name:"金额", meaning:"金额（元）", value_type:"decimal", grain:"每订单",
      rule:"源金额", time_basis:null, null_handling:"缺失金额行排除",
      unknowns:[{property_path:"time_basis", reason:"尚未提供统计窗口"}], evidence_status:"candidate",
      source_columns:[], source_refs:[]};
    const draft = {draft_id:"draft",version:1,sha256:"a".repeat(64),fields:[field],relationships:[]};
    const path2 = {latestDraft:draft,sourceState:{items:[]}} as unknown as import("./Path2Workbench").Path2WorkbenchState;
    const html = renderToStaticMarkup(createElement(RelationshipGraph, {path2,onReference:()=>{}}));
    for (const text of ["含义","值类型","粒度","规则","时间基准","缺失值处理","金额（元）","缺失金额行排除","未知原因：","尚未提供统计窗口","业务含义等待核对"]) expect(html).toContain(text);
    field.null_handling = null;
    field.unknowns.push({property_path:"null_handling",reason:"仅观察到样本无缺失，业务规则未提供"});
    const unknownHtml = renderToStaticMarkup(createElement(RelationshipGraph, {path2,onReference:()=>{}}));
    expect(unknownHtml).toContain("仅观察到样本无缺失，业务规则未提供");
    expect(unknownHtml).not.toContain("缺失金额行排除");
  });
});
