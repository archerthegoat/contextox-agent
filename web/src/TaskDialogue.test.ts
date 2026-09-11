import { describe, expect, it } from "vitest";
import { dialogueError, dialogueSources, referenceLabel, runNarrative } from "./TaskDialogue";

const source = {workspace_id:"workspace-a", source_id:"source-a", revision_id:"revision-a", sha256:"a".repeat(64)};

describe("dialogue source authorization preflight", () => {
  it("describes run outcomes without exposing execution status codes", () => {
    expect(runNarrative({status:"waiting_for_human",has_final_output:false})).toBe("发现需要业务确认的事项");
    expect(runNarrative({status:"completed",has_final_output:true})).toBe("完成分析并保存了一轮答复");
    expect(runNarrative({status:"partial",has_final_output:false})).toBe("只完成部分分析");
  });
  it("labels an exact source excerpt compactly without exposing an opaque handle", () => {
    expect(referenceLabel({kind:"source_excerpt", evidence_ref:{...source,
      locator:{kind:"text_lines", line_start:1, line_end:2}}}, [{revision_id:"revision-a", original_name:"口径说明.md"}]))
      .toBe("@口径说明.md/行1–2");
    expect(referenceLabel({kind:"source_column", source_ref:source, table_id:"/orders", column_name:"amount"},
      [{revision_id:"revision-a", original_name:"data.json"}])).toBe("@data.json/orders/amount");
  });
  it("does not label an incomplete read as an unknown send outcome", () => {
    const error = new Error("task readback failed");
    expect(dialogueError(error, "read")).toContain("对话读取尚未核对完成");
    expect(dialogueError(error, "read")).not.toContain("不要重复发送");
    expect(dialogueError(error, "send")).toContain("不要重复发送");
  });
  it("includes sources in the existing packet even when no new citation is selected", () => {
    const draft = {fields:[{source_columns:[{source_ref:source}]}], relationships:[]};
    const historyRunSources = [{...source, revision_id:"revision-b"}];
    expect(dialogueSources(draft, [], historyRunSources)).toEqual([source, historyRunSources[0]]);
  });

  it("deduplicates exact identities without treating another Workspace or hash as authorized", () => {
    const otherWorkspace = {...source, workspace_id:"workspace-b"};
    const otherHash = {...source, sha256:"b".repeat(64)};
    expect(dialogueSources({kind:"source_excerpt", evidence_ref:{...source, locator:{kind:"json_pointer", pointer:"/0"}}},
      {source_ref:source}, otherWorkspace, otherHash)).toEqual([source, otherWorkspace, otherHash]);
  });
});
