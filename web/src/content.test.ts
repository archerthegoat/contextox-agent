import { describe, expect, it } from "vitest";

import { AREA_CONTENT } from "./App";

describe("Workbench content boundaries", () => {
  it("keeps the four approved areas explicit", () => {
    expect(Object.keys(AREA_CONTENT).sort()).toEqual([
      "clarifications",
      "contract",
      "mission",
      "sources",
    ]);
  });

  it("does not describe N1 as a completed product", () => {
    expect(Object.values(AREA_CONTENT).every((area) => area.emptyBody.length > 20)).toBe(true);
    expect(Object.values(AREA_CONTENT).some((area) => area.emptyBody.includes("尚未实现"))).toBe(true);
  });
});
