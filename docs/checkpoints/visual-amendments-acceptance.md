# ContextOx Workbench 视觉补漏验收说明

## 交付边界

- 工作树分支：`codex/contextox-workbench-redesign-v1`
- 本轮基线：`dfa8c19f984efb71e4d6d1cbdc0c3d31ea76f23f`
- 验收记录建立时状态：仅有工作树改动，尚未 stage、commit、push、merge；用户已于 2026-09-03 明确批准本地 commit，并在协调者复核后合入 main。本轮仅执行本地 commit，尚未发生 merge；没有远端推送权限，远端状态未改变。
- 变更文件：`web/src/App.tsx`、`web/src/styles.css`、`web/src/content.test.ts`、`web/src/assets/icons/*.svg`、`web/src/assets/icons/LICENSE`、`docs/checkpoints/visual-amendments-acceptance.md`。
- 工作树源码集合 SHA-256：`7af514e7adf291e7c005f8e2b316b54c39a2d0a07a2ed1ee53b15f0091f41ad1`。
- 构建产物（本地 `web/dist`，未纳入本轮 Git 变更）：`index-BkMpaNcZ.js` SHA-256 `f6fc84b23e28297d40ef44f22f7c79313603bfe12fc02999ee748bf9460b25c6`；`index-CPrGiJB9.css` SHA-256 `258a2ec91214901fb49644aee832800cf270664a46d57cefb9a9260d033a372c`；`index.html` SHA-256 `e986f1c8c9b5ec6c9f747e00f5365986c6dbf102f080ae16f1366ad5c7e3a366`。
- 任务名称与模块展示改为：`资料来源`、`任务`、`待澄清`、`业务契约`；API area id 与后端顺序映射保持不变。
- 对象标签保留 `高潜客户定义`、`客户粒度关系` 两个原生按钮，删除伪关闭文字与无 handler 的打开按钮。
- Agent 折叠保留同一个可聚焦按钮，以 `aria-expanded`、`aria-controls`、中文 label/tooltip 和方向图标表达状态。
- 图节点保留三来源、两实体、口径冲突、用户确认、业务契约草案及原有点击选中语义；节点主体为 52px 圆形图标，名称在圆下方，连线落在圆边。

## 静态图标来源

使用官方 Radix Icons 仓库的纯静态 SVG，未安装 npm 包：

- 来源：<https://github.com/radix-ui/icons>
- 版本：`@radix-ui/react-icons@1.3.2`
- 固定 tag peeled commit：`bde33b13aa5848555f5512ac12155930fb4beb7d`
- 资源目录：`packages/radix-icons/icons/<name>.svg`；本地原样保存在 `web/src/assets/icons/`。
- 本地许可：`web/src/assets/icons/LICENSE`，MIT License，Copyright (c) 2022 WorkOS。
- 使用映射：导航 `archive`、`target`、`question-mark-circled`、`file-text`；对象/图节点 `file-text`、`reader`、`cube`、`mix`；折叠/恢复 `double-arrow-right`、`double-arrow-left`；树展开 `chevron-down`。

## 自动检查

- `cd web && npm run typecheck`：PASS（退出码 0）。
- `cd web && npm test -- --run`：PASS（1 test file，7 tests）。包含静态渲染 seam：4 个导航图标、8 个图节点图标、可聚焦画布、对象标签无伪开关、Agent aria 控件。
- `cd web && npm run build`：PASS（Vite，退出码 0）。
- `git diff --check`：PASS。
- 变更文件敏感 pattern 扫描：无命中。
- 精确 scope 检查：PASS；未修改后端、生成 API、`开发路径图.md`、`docs/架构与迁移报告.md`、依赖清单或 lockfile；未读取 `.env`。

## 浏览器验收记录

- URL：`http://127.0.0.1:8788/`；复用同一 Workbench 标签；zoom 100%。
- 起始状态：`任务` 模块、`客户粒度关系` 对象、`口径冲突` 选中、Agent 展开；使用现有合成演示消息与本地数据。
- 1512×860：四栏与右侧用户/Agent 对话保持；8 个 52px 圆形图标可见；三来源→两实体→冲突→确认→业务契约全图可读；连线端点通过 DOM 几何读回落在圆边。
- 1280×800：中心画布 `clientWidth=572`、内部 `scrollWidth=720`；横向滚动只发生在画布内部，整页 `scrollWidth=1280`；横滚到右端后业务契约节点与下游连线完整可见。
- 模块切换：点击 `资料来源` 后显示对应空面板；返回 `任务` 并点击 `高潜客户定义` 对象标签后显示对应空面板；点击 `客户粒度关系` 恢复关系图。
- 节点交互：点击实体和冲突节点后 `aria-pressed` 与选中样式同步更新。
- Agent 交互：鼠标点击折叠后内容隐藏、按钮焦点保留、label 为 `展开演示对话`、`aria-expanded=false`；同一按钮按 Enter 恢复消息与输入区，焦点仍在按钮，`aria-expanded=true`、`aria-controls=agent-panel-content`。
- 读取浏览器告警：error/warn 为空。
- 已检查滚动、鼠标点击、键盘 Enter 和焦点；未扩展手机适配。
- 截图： [1512 首屏](/private/tmp/contextox-visual-amendments-1512.png)、[1280 横滚右端](/private/tmp/contextox-visual-amendments-1280-right.png)、[Agent 折叠](/private/tmp/contextox-visual-amendments-agent-collapsed.png)、[Agent 恢复](/private/tmp/contextox-visual-amendments-agent-restored.png)。
- 截图由浏览器输出，窗口展示可能存在裁切；视口尺寸与布局结论以浏览器 DOM 的 `window.innerWidth/innerHeight`、滚动尺寸和节点几何读回为准，不宣称所有截图文件像素尺寸严格等于设定视口。
- HTTP 只读探测：`GET /`、`GET /assets/index-BkMpaNcZ.js`、`GET /assets/index-CPrGiJB9.css` 均为 `200`；未运行真实模型。

## 人工复核清单（总体已验收，逐项记录未提供）

用户已明确通过本版整体 Workbench 视觉验收。以下 7 项保留为精确工作树构建上的复核记录模板；协调者未收到逐项 PASS/FAIL 记录，因此不伪填单项结果，也不由自动检查代记人类验收：

1. 打开 `http://127.0.0.1:8788/`，确认分支为 `codex/contextox-workbench-redesign-v1`、使用 zoom 100%，起始为 `任务` → `客户粒度关系`、冲突节点选中、Agent 展开。预期四栏、白蓝 DataWorks 风格、右侧用户/Agent 对话与上述截图一致。`PASS / FAIL：____`
2. 依次点击 `资料来源`、`任务`、`待澄清`、`业务契约`，确认模块标题和空面板文案分别中文化，回到 `任务`。`PASS / FAIL：____`
3. 用鼠标或键盘 Tab/Enter 切换 `高潜客户定义`、`客户粒度关系` 两个对象按钮；确认仅有两套原生按钮，不出现伪关闭或无 handler 的打开控件。`PASS / FAIL：____`
4. 在 1512×860 查看关系图，确认三来源→两实体→口径冲突→待用户确认→业务契约草案的结构、圆形图标、标签换行和连线端点可读。点击来源、实体、冲突、确认、契约节点，确认每次只有当前节点呈选中状态。`PASS / FAIL：____`
5. 将视口设为 1280×800，在画布内部横向滚动到右端，确认整页不横溢且业务契约节点/下游连线完整可达；将焦点置于画布，用左右方向键确认键盘滚动入口可用。`PASS / FAIL：____`
6. 点击 Agent 折叠按钮，确认内容隐藏、按钮仍聚焦、`aria-expanded=false`、label/tooltip 为展开；按 Enter 恢复，确认同一按钮焦点保留、消息和输入区恢复、`aria-expanded=true`、`aria-controls=agent-panel-content`。`PASS / FAIL：____`
7. 观察空面板、滚动边界、文本对比度和 console error/warn；记录未覆盖的 loading/API error/SSE 状态，不把未运行流程记为 PASS。最后复位为 1512×860、`任务` → `客户粒度关系`、冲突选中、Agent 展开。`PASS / FAIL：____`

复位步骤：恢复 1512×860、zoom 100%，点击 `任务`，点击 `客户粒度关系`，点击 `口径冲突`，确认 Agent 展开；关闭或保留截图标签由验收人决定，不改动 Git 工作树。

本轮明确保留但未实现的旧占位控件：画布 `适应画布`、`缩放`、`布局`、`更多`，以及对象侧栏 `搜索`、`新增`、`筛选`。它们不属于本轮已实现功能，本说明不宣称这些控件可用。

## 保留项与限制

- loading、API error、SSE 重连等既有状态逻辑未改；本轮未人为制造 API error 或 loading 长驻，故该流程为 `NOT RUN`，不能由本说明推导错误态视觉 PASS。
- 未找到历史圆形版原稿；本轮圆形表达依据本次明确的圆形节点要求调整，不宣称历史稿逐像素还原。
- 本地自动检查与浏览器检查不等于人类 Workbench 验收；人类 Workbench 视觉验收状态：`PASS`，来源为用户于 2026-09-03 的明确验收，不是自动测试推断。该 `PASS` 绑定本文记录的构建产物哈希：`index-BkMpaNcZ.js`（JS，`f6fc84b23e28297d40ef44f22f7c79313603bfe12fc02999ee748bf9460b25c6`）、`index-CPrGiJB9.css`（CSS，`258a2ec91214901fb49644aee832800cf270664a46d57cefb9a9260d033a372c`）和 `index.html`（`e986f1c8c9b5ec6c9f747e00f5365986c6dbf102f080ae16f1366ad5c7e3a366`）。本验收说明路径为 `docs/checkpoints/visual-amendments-acceptance.md`。
