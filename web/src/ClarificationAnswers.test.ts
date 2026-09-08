import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerForm, AnswerReadback, answerOmissions, blankAnswers, clarificationCaseMatches, approvedRefs, draftTargets } from "./ClarificationAnswers";
import type { components } from "./generated/api";

type Answer = components["schemas"]["ClarificationAnswerVersion"];
const hash = "a".repeat(64);
const question = {question:"谁确认空值口径？", why_needed:"样本不能代表业务规则", expected_answer_type:"text" as const, blocking_impact:"blocking" as const, suggested_owner_role:"业务负责人", related_definition_paths:["fields.customer_id.null_handling"], evidence_requested:["业务制度"], examples_or_options:[], source_refs:[]};
const request: components["schemas"]["ClarificationRequest"] = {workspace_id:"ws",mission_id:"mission",run_id:"old-run",clarification_id:"clarification",draft_version:1,draft_sha256:hash,status:"awaiting_answer",questions:[question, {...question,question:"时间窗口是多久？"}]};
const items: Answer["items"] = [
  {question_index:0,disposition:"unknown",answer:null,respondent:"业务同事",basis:"尚无规则文件",evidence_refs:[],targets:[],blocker:{resolver:"业务负责人",evidence_needed:"空值制度",next_action:"查找制度并确认处理规则"}},
  {question_index:1,disposition:"answered",answer:"30天",respondent:"业务负责人",basis:"本任务确认",evidence_refs:[],targets:[],blocker:null},
];
const answer: Answer = {workspace_id:"ws",mission_id:"mission",origin_run_id:"old-run",clarification_id:"clarification",version:1,request_sha256:hash,review_draft:{draft_id:"draft",version:1,sha256:hash},source_refs:[],items,saved_by:"local-owner",created_at:"2026-09-08T00:00:00Z",sha256:hash};

describe("whole clarification answer", () => {
  it("accepts an answered item and an unbound unknown with a complete resolution arrangement", () => {
    expect(answerOmissions(items,2)).toEqual([]);
    expect(draftTargets(null)).toEqual([]);
  });
  it("rejects missing question coverage and each missing blocker responsibility", () => {
    expect(answerOmissions(items.slice(1),2).join(" ")).toContain("完整回答");
    expect(answerOmissions([{...items[0],blocker:{resolver:" ",evidence_needed:"",next_action:""}}],1).join(" ")).toContain("解决方、所需证据、下一动作");
    expect(answerOmissions(blankAnswers(request),2)).toHaveLength(2);
  });
  it("renders original questions, required provenance and optional targets without saving on render", () => {
    let saves=0;
    const html=renderToStaticMarkup(createElement(AnswerForm,{request,latest:answer,draft:null,disabled:false,onDirty:()=>{},onSave:async()=>{saves++;}}));
    expect(html).toContain("谁确认空值口径");expect(html).toContain("fields.customer_id.null_handling");
    expect(html).toContain("解决方");expect(html).toContain("所需证据");expect(html).toContain("下一动作");
    expect(html).toContain("保存不会调用模型");expect(html).toContain("可以不选");expect(saves).toBe(0);
  });
  it("historical readback preserves the unknown and has no mutation buttons", () => {
    const html=renderToStaticMarkup(createElement(AnswerReadback,{answer}));
    expect(html).toContain("尚不能确认 · 未解决");expect(html).toContain("业务负责人");expect(html).toContain("30天");expect(html).not.toContain("<button");
  });
  it("never assembles an unapproved latest answer as a continuation reference", () => {
    const approval={workspace_id:"ws",mission_id:"mission",origin_run_id:"old-run",clarification_id:"clarification",answer_version:1,answer_sha256:hash,approval_id:"approval",approved_by:"local-owner" as const,approved_at:"2026-09-08T00:00:00Z"};
    const item={request,request_sha256:hash,latest_answer:answer,latest_approval:approval,review_state:"approved" as const};
    expect(clarificationCaseMatches(item,"ws","mission")).toBe(true);
    expect(clarificationCaseMatches({...item,latest_answer:{...answer,workspace_id:"other"}},"ws","mission")).toBe(false);
    expect(clarificationCaseMatches({...item,latest_approval:{...approval,answer_version:2}},"ws","mission")).toBe(false);
    expect(approvedRefs([item])).toEqual([{origin_run_id:"old-run",clarification_id:"clarification",answer_version:1,answer_sha256:hash,approval_id:"approval"}]);
    expect(approvedRefs([{...item,latest_answer:{...answer,version:2},review_state:"awaiting_approval"}])).toEqual([]);
  });
});
