import { describe, expect, it } from "vitest";
import { conversationBelongsTo, recentConversationHistory } from "./ConversationDialogue";
import { reviewedAnswer, suggestedAnswerItems } from "./ConversationAnswers";
import { answerOmissions } from "./ClarificationAnswers";
import type { components } from "./generated/api";

const hash="a".repeat(64);
const question={question:"退款订单是否纳入？",why_needed:"统计金额会变化",expected_answer_type:"text" as const,blocking_impact:"blocking" as const,suggested_owner_role:null,related_definition_paths:[],evidence_requested:[],examples_or_options:[],source_refs:[]};
const item:components["schemas"]["ClarificationCase"]={request:{workspace_id:"ws",mission_id:"mission",run_id:"origin",clarification_id:"question",draft_version:1,draft_sha256:hash,status:"awaiting_answer",questions:[question]},request_sha256:hash,latest_answer:null,latest_approval:null,review_state:"awaiting_answer"};
const suggestion:components["schemas"]["DiscussionAnswerSuggestion"]={origin_run_id:"origin",clarification_id:"question",request_sha256:hash,question_index:0,disposition:"answered",answer:"退款不纳入"};

describe("continuous conversation boundaries",()=>{
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
    expect(reviewedAnswer(saved,items)).toMatchObject({expected_latest_version:2,items:null,saved_answer:{version:2,sha256:hash,approval_id:"approval"}});
    expect(reviewedAnswer(saved,[{...items[0],answer:"退款单独统计"}])).toMatchObject({expected_latest_version:2,saved_answer:null});
  });
});
