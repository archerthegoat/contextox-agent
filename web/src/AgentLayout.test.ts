import { describe, expect, it } from "vitest";
import { agentWidthBounds, clampAgentWidth } from "./AgentLayout";

describe("Agent panel width", () => {
  it("uses 44 percent initially and keeps both desktop panels usable", () => {
    expect(agentWidthBounds(1200).initial).toBe(528);
    expect(clampAgentWidth(2000, 1200)).toBe(840);
    expect(clampAgentWidth(200, 1200)).toBe(360);
    expect(clampAgentWidth(600, 730)).toBe(370);
  });
  it("limits the initial preference and rejects corrupt numeric widths", () => {
    expect(agentWidthBounds(2000).initial).toBe(640);
    expect(agentWidthBounds(720).initial).toBe(360);
    expect(clampAgentWidth(NaN, 1200)).toBe(528);
  });
});
