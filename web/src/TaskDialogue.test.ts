import { describe, expect, it } from "vitest";
import { dialogueSources } from "./TaskDialogue";

const source = {workspace_id:"workspace-a", source_id:"source-a", revision_id:"revision-a", sha256:"a".repeat(64)};

describe("dialogue source authorization preflight", () => {
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
