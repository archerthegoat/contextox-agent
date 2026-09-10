import { describe, expect, it } from "vitest";
import { conversationBelongsTo, conversationUsesTaskHistory, conversationReviewMatches, conversationPreviewTarget, conversationSendGuidance, discussionStatusLabel, recentConversationHistory, mergeConversationPage, selectedConversationHistory } from "./ConversationDialogue";
import { reviewedAnswer, receiptMatchesReviewedAnswers, answersCanCollapse, suggestedAnswerItems, mergeReviewedSave } from "./ConversationAnswers";
import { answerOmissions } from "./ClarificationAnswers";
import type { MessageReference } from "./TaskDialogue";
import type { components } from "./generated/api";

const hash="a".repeat(64);
const question={question:"退款订单是否纳入？",why_needed:"统计金额会变化",expected_answer_type:"text" as const,blocking_impact:"blocking" as const,suggested_owner_role:null,related_definition_paths:[],evidence_requested:[],examples_or_options:[],source_refs:[]};
const item:components["schemas"]["ClarificationCase"]={request:{workspace_id:"ws",mission_id:"mission",run_id:"origin",clarification_id:"question",draft_version:1,draft_sha256:hash,status:"awaiting_answer",questions:[question]},request_sha256:hash,latest_answer:null,latest_approval:null,review_state:"awaiting_answer"};
const suggestion:components["schemas"]["DiscussionAnswerSuggestion"]={origin_run_id:"origin",clarification_id:"question",request_sha256:hash,question_index:0,disposition:"answered",answer:"退款不纳入"};

describe("continuous conversation boundaries",()=>{
  it("follows only the latest submitted source snapshot, preferring its explicit evidence",()=>{
    const source={workspace_id:"ws",source_id:"s",revision_id:"r",sha256:hash};
    const other={...source,source_id:"s2",revision_id:"r2"};
    const chat={workspace_id:"ws",conversation_id:"chat",title:"讨论",created_at:"2026-09-10T00:00:00Z",state_version:1,source_refs:[other]};
    const input:components["schemas"]["ConversationMessage"]={workspace_id:"ws",conversation_id:"chat",message_id:"m",role:"user",content:"先看看资料",created_at:chat.created_at,sha256:hash,source_refs:[source,other]};
    expect(conversationPreviewTarget(chat,[],"ws")).toEqual({reference:null,source:null});
    expect(conversationPreviewTarget(chat,[input],"ws")).toEqual({reference:null,source});
    const reference:MessageReference={kind:"source_column",source_ref:other,table_id:"orders",column_name:"amount"};
    expect(conversationPreviewTarget(chat,[input,{...input,message_id:"reply",role:"assistant",references:[reference]}],"ws")).toEqual({reference,source:null});
    expect(conversationPreviewTarget(chat,[input,{...input,message_id:"next",source_refs:[]}],"ws")).toEqual({reference:null,source:null});
    expect(conversationPreviewTarget(chat,[input],"other-workspace")).toEqual({reference:null,source:null});
    expect(conversationPreviewTarget({...chat,mission_id:"mission"},[input],"ws")).toEqual({reference:null,source:null});
  });
  it("labels every discussion state in user-facing Chinese",()=>{
    expect(discussionStatusLabel("succeeded")).toBe("答复已更新");
    expect(discussionStatusLabel("cancelled")).toBe("讨论已停止");
    for(const state of ["queued","running","blocked","failed"] as const)expect(discussionStatusLabel(state)).not.toMatch(/[a-z]/);
  });
  it("explains source selection and automatic task start without implying workspace-wide search",()=>{
    expect(conversationSendGuidance(false,0,false)).toContain("不会自动使用工作区全部资料");
    expect(conversationSendGuidance(false,2,false)).toContain("自动形成任务并开始分析");
    expect(conversationSendGuidance(true,2,false)).toContain("推进当前任务");
    expect(conversationSendGuidance(true,2,true)).toContain("不自动排队");
  });
  it("uses discussion history for a newly saved or stale answer even while mission remains blocked",()=>{
    const state={selectedMission:{mission_id:"mission",status:"blocked"},missionSnapshot:null,latestDraft:{status:"partial"}} as unknown as import("./Path2Workbench").Path2WorkbenchState;
    expect(conversationUsesTaskHistory(state,["awaiting_approval"])).toBe(false);
    expect(conversationUsesTaskHistory(state,["stale"])).toBe(false);
    expect(conversationUsesTaskHistory(state,["awaiting_answer"])).toBe(false);
    expect(conversationUsesTaskHistory(state,["approved"])).toBe(true);
    expect(conversationUsesTaskHistory(state,[])).toBe(true);
    expect(conversationUsesTaskHistory(state,null)).toBe(false);
    const chat={workspace_id:"ws",conversation_id:"chat",mission_id:"mission",title:"讨论",created_at:"2026-09-10T00:00:00Z",state_version:1,source_refs:[]};
    const review={workspaceId:"ws",conversationId:"chat",missionId:"mission",missionStateVersion:3,reviewStates:["awaiting_approval" as const]};
    expect(conversationReviewMatches(review,chat,"ws")).toBe(true);
    expect(conversationReviewMatches({...review,conversationId:"old-chat"},chat,"ws")).toBe(false);
    expect(conversationReviewMatches({...review,missionId:"old-mission"},chat,"ws")).toBe(false);
    expect(conversationReviewMatches(review,chat,"other-workspace")).toBe(false);
  });
  it("rejects cross-workspace conversation and source identities",()=>{
    const conversation={workspace_id:"ws",conversation_id:"chat",title:"讨论",created_at:"2026-09-10T00:00:00Z",state_version:1,source_refs:[]};
    expect(conversationBelongsTo(conversation,"ws","chat")).toBe(true);
    expect(conversationBelongsTo(conversation,"other","chat")).toBe(false);
    expect(conversationBelongsTo({...conversation,source_refs:[{workspace_id:"other",source_id:"s",revision_id:"r",sha256:hash}]},"ws")).toBe(false);
  });
  it("suggests at most the latest four messages without manufacturing history",()=>{
    const messages=Array.from({length:7},(_,i)=>({workspace_id:"ws",conversation_id:"chat",message_id:`m${i}`,role:"user" as const,content:"公开合成问题",created_at:"2026-09-10T00:00:00Z",sha256:hash}));
    expect(recentConversationHistory(messages)).toEqual(["m3","m4","m5","m6"]);
  });
  it("defaults ordinary analysis to task-backed history and explicitly rejects a manually selected discussion",()=>{
    const message=(id:string,task=false)=>({workspace_id:"ws",conversation_id:"chat",message_id:id,role:"user" as const,content:"公开合成问题",created_at:"2026-09-10T00:00:00Z",sha256:hash,task_message:task?{workspace_id:"ws",mission_id:"mission",message_id:`task-${id}`,role:"user" as const,content:"公开合成问题",created_at:"2026-09-10T00:00:00Z",sha256:hash,run_id:"run",original_attempt_id:null,references:[]}:null});
    const messages=[message("discussion-user"),message("discussion-reply"),message("first-input",true),message("first-reply",true)];
    expect(recentConversationHistory(messages,true)).toEqual(["first-input","first-reply"]);
    expect(recentConversationHistory(messages,false)).toEqual(messages.map(item=>item.message_id));
    expect(selectedConversationHistory(messages,recentConversationHistory(messages,true),true)).toHaveLength(2);
    expect(()=>selectedConversationHistory(messages,["discussion-reply"],true)).toThrow("不能携带");
    expect(selectedConversationHistory(messages,["discussion-reply"],false)).toHaveLength(1);
  });
  it("retains selected older history across latest-page refresh and refuses unavailable ids",()=>{
    const message=(id:string)=>({workspace_id:"ws",conversation_id:"chat",message_id:id,role:"user" as const,content:"公开合成问题",created_at:"2026-09-10T00:00:00Z",sha256:hash});
    const merged=mergeConversationPage([message("older"),message("current")],[message("current"),message("new")]);
    expect(merged.map(item=>item.message_id)).toEqual(["older","current","new"]);
    expect(selectedConversationHistory(merged,["older"])).toEqual([{message_id:"older",sha256:hash}]);
    expect(()=>selectedConversationHistory(merged,["missing"])).toThrow("未完整回读");
  });
  it("does not invent respondent, basis, or resolution responsibility from model suggestions",()=>{
    const answers=suggestedAnswerItems(item,[suggestion]);
    expect(answers[0]).toMatchObject({answer:"退款不纳入",respondent:"",basis:""});
    expect(answerOmissions(answers,1).length).toBeGreaterThan(0);
    const unknown=suggestedAnswerItems(item,[{...suggestion,disposition:"unknown",answer:null}]);
    expect(unknown[0].blocker).toEqual({resolver:"",evidence_needed:"",next_action:""});
    expect(answerOmissions(unknown,1).length).toBeGreaterThan(0);
    expect(suggestedAnswerItems(item,[{...suggestion,request_sha256:"b".repeat(64)}])[0].answer).toBe("");
  });
  it("pins the reviewed saved version and approval, while an edit requests a new version",()=>{
    const items=suggestedAnswerItems(item,[{...suggestion,respondent:"业务同事",basis:"本轮明确回答"}]);
    const saved:components["schemas"]["ClarificationCase"]={...item,review_state:"approved",latest_answer:{workspace_id:"ws",mission_id:"mission",origin_run_id:"origin",clarification_id:"question",version:2,request_sha256:hash,review_draft:{draft_id:"draft",version:1,sha256:hash},source_refs:[],items,saved_by:"local-owner",created_at:"2026-09-10T00:00:00Z",sha256:hash},latest_approval:{workspace_id:"ws",mission_id:"mission",origin_run_id:"origin",clarification_id:"question",answer_version:2,answer_sha256:hash,approval_id:"approval",approved_by:"local-owner",approved_at:"2026-09-10T00:00:00Z"}};
    const receipt={workspace_id:"ws",mission_id:"mission",answer_steps:[{origin_run_id:"origin",clarification_id:"question",approved_answer:{origin_run_id:"origin",clarification_id:"question",answer_version:2,answer_sha256:hash,approval_id:"approval"}}]} as components["schemas"]["ConversationHandoffReceipt"];
    expect(receiptMatchesReviewedAnswers(receipt,[saved])).toBe(true);
    expect(receiptMatchesReviewedAnswers(receipt,[{...saved,latest_answer:{...saved.latest_answer!,version:3},latest_approval:null,review_state:"awaiting_approval"}])).toBe(false);
    expect(receiptMatchesReviewedAnswers(receipt,[{...saved,review_state:"stale"}])).toBe(false);
    expect(receiptMatchesReviewedAnswers(receipt,[{...saved,latest_approval:{...saved.latest_approval!,approval_id:"new-approval"}}])).toBe(false);
    expect(receiptMatchesReviewedAnswers({...receipt,answer_steps:[...receipt.answer_steps,...receipt.answer_steps]},[saved])).toBe(false);
    expect(answersCanCollapse([saved],{"origin/question":items},false,false,null)).toBe(true);
    expect(answersCanCollapse([saved],{"origin/question":[{...items[0],answer:"修改"}]},false,false,null)).toBe(false);
    expect(answersCanCollapse([saved],{"origin/question":items},true,false,null)).toBe(false);
    expect(answersCanCollapse([saved],{"origin/question":items},false,true,null)).toBe(false);
    expect(answersCanCollapse([{...saved,review_state:"stale"}],{"origin/question":items},false,false,null)).toBe(false);
    const other={...item,request:{...item.request,clarification_id:"other-question"}};
    const merged=mergeReviewedSave({mission_state_version:5,items:[item,other]},{operation:"save",client_request_id:"save-id",answer:saved.latest_answer!,approval:null,mission_state_version:6});
    expect(merged.mission_state_version).toBe(6);
    expect(merged.items[0].latest_answer?.version).toBe(2);
    expect(merged.items[1]).toBe(other);
    expect(reviewedAnswer(saved,items)).toMatchObject({expected_latest_version:2,items:null,saved_answer:{version:2,sha256:hash,approval_id:"approval"}});
    expect(reviewedAnswer(saved,[{...items[0],answer:"退款单独统计"}])).toMatchObject({expected_latest_version:2,saved_answer:null});
  });
});
