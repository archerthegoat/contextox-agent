import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

import {
  AGENT_COPY,
  AREA_CONTENT,
  AREA_NAV,
  AREA_NAV_PRESENTATION,
  DEMO_MESSAGES,
  ICON_URLS,
  OBJECT_TABS,
  navigationForAreas,
} from "./App";
import App from "./App";
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
      "资料来源",
      "任务",
      "待澄清",
      "业务契约",
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
      { id: "mission", label: "高潜客户定义" },
      { id: "relationship", label: "客户粒度关系" },
    ]);
    expect(DEMO_MESSAGES.map(({ role }) => role)).toEqual(["agent", "user", "agent"]);
    expect(DEMO_MESSAGES.some(({ body }) => body.includes("FDE"))).toBe(false);
  });

  it("keeps every primary module explicit and truthful", () => {
    expect(Object.values(AREA_CONTENT)).toHaveLength(4);
    expect(Object.values(AREA_CONTENT).every((area) => area.emptyTitle && area.emptyBody)).toBe(true);
    expect(Object.values(AREA_CONTENT).map((area) => area.label)).toEqual([
      "资料来源",
      "任务",
      "待澄清",
      "业务契约",
    ]);
  });

  it("keeps the approved static icon set local and package-free", () => {
    expect(Object.keys(ICON_URLS)).toEqual([
      "archive",
      "target",
      "question-mark-circled",
      "file-text",
      "reader",
      "cube",
      "mix",
      "double-arrow-left",
      "double-arrow-right",
      "chevron-down",
    ]);
    expect(Object.values(ICON_URLS).every((url) => url.includes("/assets/icons/"))).toBe(true);
  });

  it("renders the icon navigation and truthful control seams", () => {
    const markup = renderToStaticMarkup(createElement(App));

    expect(markup.match(/primary-nav-icon/g)).toHaveLength(4);
    expect(markup.match(/graph-node-icon/g)).toHaveLength(8);
    expect(markup).toContain("tabindex=\"0\"");
    expect(markup).toContain("aria-label=\"客户粒度关系图，可横向滚动查看\"");
    expect(markup).toContain("aria-expanded=\"true\"");
    expect(markup).toContain("aria-controls=\"agent-panel-content\"");
    expect(markup).not.toContain("object-tab-close");
    expect(markup).not.toContain("object-tab-add");
    expect(markup).not.toContain("aria-label=\"打开对象\"");
    expect(markup).not.toContain(">关闭<");
    expect(markup).not.toContain(">打开<");
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
