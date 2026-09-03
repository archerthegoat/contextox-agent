import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

import {
  AGENT_COPY,
  AREA_CONTENT,
  AREA_NAV,
  AREA_NAV_PRESENTATION,
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

  it("keeps the Mission relationship shell free of synthetic result data", () => {
    expect(OBJECT_TABS).toEqual([
      { id: "mission", label: "高潜客户定义" },
      { id: "relationship", label: "客户粒度关系" },
    ]);
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
    expect(markup).toContain("data-path2-state=\"workbench\"");
    expect(markup).toContain("来源待导入");
    expect(markup).not.toContain("客户主数据.csv");
    expect(markup).not.toContain("演示模式");
    expect(markup).not.toContain("object-tab-close");
    expect(markup).not.toContain("object-tab-add");
    expect(markup).not.toContain("aria-label=\"打开对象\"");
    expect(markup).not.toContain(">关闭<");
    expect(markup).not.toContain(">打开<");
  });

  it("labels the Agent panel as public Run state without a free composer", () => {
    expect(AGENT_COPY).toEqual({
      title: "Agent Run",
      mode: "公开状态",
      composerPlaceholder: "当前版本不提供自由对话输入",
    });
  });
});
