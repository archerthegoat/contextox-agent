import { describe, expect, it } from "vitest";

import {
  AGENT_COPY,
  AREA_CONTENT,
  AREA_NAV,
  AREA_NAV_PRESENTATION,
  DEMO_MESSAGES,
  OBJECT_TABS,
  navigationForAreas,
} from "./App";
import type { WorkbenchSnapshot } from "./api/client";

describe("ContextOx Workbench v3 content boundaries", () => {
  it("keeps the four primary modules in the approved rail order", () => {
    expect(AREA_NAV.map((area) => area.id)).toEqual([
      "sources",
      "mission",
      "clarifications",
      "contract",
    ]);
    expect(AREA_NAV.map((area) => area.label)).toEqual([
      "Sources",
      "Mission",
      "Clarifications",
      "Contract",
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

  it("keeps the Mission relationship demo grounded in synthetic objects", () => {
    expect(OBJECT_TABS).toEqual([
      { id: "mission", label: "统一-高潜客户定义" },
      { id: "relationship", label: "客户粒度关系" },
    ]);
    expect(DEMO_MESSAGES.map(({ role }) => role)).toEqual(["agent", "user", "agent"]);
    expect(DEMO_MESSAGES.some(({ body }) => body.includes("FDE"))).toBe(false);
  });

  it("keeps every primary module explicit and truthful", () => {
    expect(Object.values(AREA_CONTENT)).toHaveLength(4);
    expect(Object.values(AREA_CONTENT).every((area) => area.emptyTitle && area.emptyBody)).toBe(true);
    expect(Object.values(AREA_CONTENT).map((area) => area.label)).toEqual([
      "Sources",
      "Mission",
      "Clarifications",
      "Contract",
    ]);
  });

  it("labels the Agent composer as disabled demo mode and the human speaker as 用户", () => {
    expect(AGENT_COPY).toEqual({
      title: "演示对话",
      mode: "演示模式",
      composerPlaceholder: "演示模式，暂不可发送",
    });
    expect(JSON.stringify(AGENT_COPY)).not.toContain("FDE");
    expect(DEMO_MESSAGES.find(({ role }) => role === "user")?.role).toBe("user");
  });
});
