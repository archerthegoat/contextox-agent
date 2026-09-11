import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api/client";
import { type PendingClarificationSubmission, decodePendingSubmission, performPendingSubmission, AnswerForm, AnswerReadback, DefinitionBusinessSummary, answerOmissions, blankAnswers, clarificationCaseMatches, approvedRefs, draftTargets, impactChangeTitle } from "./ClarificationAnswers";
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


describe("business-facing answer impact", () => {
  it("names changes with business objects instead of raw definition paths", () => {
    const source={workspace_id:"ws",source_id:"source",revision_id:"revision",sha256:hash};
    const field:components["schemas"]["DefinitionField"]={field_key:"amount",name:"净订单金额",meaning:null,value_type:null,grain:null,rule:null,time_basis:null,null_handling:null,source_columns:[],source_refs:[],evidence_status:"candidate",unknowns:[]};
    const relationship:components["schemas"]["RelationshipCandidate"]={relationship_key:"orders_refunds",left:{source_ref:source,table_id:"orders",columns:["region"]},right:{source_ref:source,table_id:"refunds",columns:["region"]},observed_cardinality:"unknown",join_rule:null,grain_notes:null,evidence_status:"candidate",source_refs:[],unknowns:[],risks:[]};
    expect(impactChangeTitle({kind:"field",key:"amount",change:"added",before:null,after:field,question_refs:[]})).toBe("净订单金额");
    expect(impactChangeTitle({kind:"relationship",key:"orders_refunds",change:"added",before:null,after:relationship,question_refs:[]})).toBe("orders 与 refunds");
  });
  it("shows six field dimensions and unknown reasons without engineering identities", () => {
    const value: components["schemas"]["DefinitionField"] = {field_key:"window",name:"统计窗口",meaning:"订单统计时间范围",value_type:"整数",grain:"每笔订单",rule:"30天",time_basis:"下单时间",null_handling:null,source_columns:[],source_refs:[],evidence_status:"candidate",unknowns:[{property_path:"fields.window.null_handling",reason:"业务负责人尚未确认空值规则"}]};
    const html=renderToStaticMarkup(createElement(DefinitionBusinessSummary,{value}));
    for(const label of ["业务含义","值类型","业务粒度","业务规则","时间口径","空值规则"]) expect(html).toContain(label);
    expect(html).toContain("30天");expect(html).toContain("业务负责人尚未确认空值规则");expect(html).toContain("尚未确认");expect(html).not.toContain("property_path");expect(html).not.toContain("source_refs");
  });
  it("shows actual relation tables and columns while keeping source identity out of the business summary", () => {
    const source={workspace_id:"private-workspace",source_id:"private-source",revision_id:"private-revision",sha256:hash};
    const value: components["schemas"]["RelationshipCandidate"] = {relationship_key:"customer_orders",left:{source_ref:source,table_id:"customers",columns:["customer_id"]},right:{source_ref:source,table_id:"orders",columns:["customer_id"]},observed_cardinality:"one_to_many",join_rule:"按客户编号关联",grain_notes:"客户到订单",evidence_status:"candidate",source_refs:[],unknowns:[],risks:["客户编号重复会扩大结果"]};
    const html=renderToStaticMarkup(createElement(DefinitionBusinessSummary,{value}));
    for(const text of ["customers","orders","customer_id","一对多","按客户编号关联","客户到订单","客户编号重复会扩大结果"]) expect(html).toContain(text);
    expect(html).not.toContain("private-workspace");expect(html).not.toContain(hash);
    expect(renderToStaticMarkup(createElement(DefinitionBusinessSummary,{value:null}))).toContain("无此字段或关系");
  });
});

describe("explicit replay of the submitted request", () => {
  const submission: PendingClarificationSubmission = {kind:"answer",id:"request-1",workspaceId:"ws",missionId:"mission",action:{operation:"save",originRunId:"old-run",clarificationId:"clarification",body:{client_request_id:"request-1",expected_state_version:7,expected_latest_version:0,request_sha256:hash,review_draft:answer.review_draft,source_refs:[],items}}};
  const receipt: components["schemas"]["ClarificationSubmissionReceipt"]={operation:"save",client_request_id:"request-1",answer,approval:null,mission_state_version:8};
  it("a not-arrived request and a 404 lookup do not write until explicit same-payload replay", async () => {
    const saved=decodePendingSubmission(JSON.stringify(submission),"ws","mission");
    const save=vi.fn<typeof api.saveClarificationAnswer>(async()=>receipt);
    const lookup=vi.fn<typeof api.fetchClarificationSubmission>(async()=>{throw new api.ApiRequestError(404,{code:"clarification_submission_not_found",message:"missing",request_id:"lookup"});});
    const transport={...api,saveClarificationAnswer:save,fetchClarificationSubmission:lookup};
    await expect(performPendingSubmission(saved,"query",transport)).rejects.toMatchObject({status:404});
    expect(save).not.toHaveBeenCalled();
    await expect(performPendingSubmission(saved,"replay",transport)).resolves.toEqual({kind:"answer",receipt});
    expect(save).toHaveBeenCalledExactlyOnceWith("ws","mission","old-run","clarification",submission.action!.body);
    expect(saved.action!.body).toEqual(submission.action!.body);
    expect(saved.action!.body).not.toBe(submission.action!.body);
  });
  it("an accepted request whose response was lost is only read back", async () => {
    const save=vi.fn<typeof api.saveClarificationAnswer>(async()=>receipt);
    const lookup=vi.fn<typeof api.fetchClarificationSubmission>(async()=>receipt);
    const result=await performPendingSubmission(submission,"query",{...api,saveClarificationAnswer:save,fetchClarificationSubmission:lookup});
    expect(result).toEqual({kind:"answer",receipt});expect(save).not.toHaveBeenCalled();
    expect(lookup).toHaveBeenCalledExactlyOnceWith("ws","mission","request-1");
  });
  it("rejects another scope or changed request identity and cannot replay an old ID-only record", async () => {
    expect(()=>decodePendingSubmission(JSON.stringify(submission),"other","mission")).toThrow("scope mismatch");
    expect(()=>decodePendingSubmission(JSON.stringify({...submission,id:"different"}),"ws","mission")).toThrow("invalid pending body");
    const old=decodePendingSubmission(JSON.stringify({kind:"answer",id:"old-request"}),"ws","mission");
    await expect(performPendingSubmission(old,"replay",api)).rejects.toThrow("original payload unavailable");
  });
});
