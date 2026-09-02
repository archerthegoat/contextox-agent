import { describe, expect, it } from "vitest";

import {
  AGENT_COPY,
  AREA_CONTENT,
  AREA_NAV,
  AREA_NAV_PRESENTATION,
  navigationForAreas,
} from "./App";
import type { WorkbenchSnapshot } from "./api/client";

describe("数契 Workbench content boundaries", () => {
  it("keeps the four user-facing areas in their navigation order", () => {
    expect(AREA_NAV.map((area) => area.id)).toEqual([
      "sources",
      "mission",
      "clarifications",
      "contract",
    ]);
    expect(AREA_NAV.map((area) => area.label)).toEqual([
      "Sources",
      "Missions",
      "Clarifications",
      "Contracts",
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

  it("keeps the Sources empty state copy exact", () => {
    expect(AREA_CONTENT.sources).toEqual({
      label: "Sources",
      title: "添加第一份授权资料",
      description: "数契只会处理你明确选择的本地资料。",
      emptyTitle: "暂无资料",
      emptyBody: "来源导入将在下一阶段开放。",
    });
  });

  it("keeps every area explicit and truthful", () => {
    expect(Object.values(AREA_CONTENT)).toHaveLength(4);
    expect(Object.values(AREA_CONTENT).every((area) => area.emptyTitle && area.emptyBody)).toBe(true);
  });

  it("keeps the Agent idle state copy and composer placeholder exact", () => {
    expect(AGENT_COPY).toEqual({
      title: "Agent",
      body: "创建 Mission 后，数契会在这里持续协作。",
      availability: "即将开放",
      placeholder: "等待 Mission",
    });
  });
});
