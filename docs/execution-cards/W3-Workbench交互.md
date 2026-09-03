# W3 执行卡：Workbench 交互

## 1. 身份、原意与当前授权

- card_id：PATH2-W3；card_version：1.0；日期：2026-09-03。
- 用户原意（摘要）：每个 worker 有独立执行说明，明确去哪里看、做哪些事、怎样验收，避免开发偏离。
- 原意判断：先跑通读表、关系与字段定义草案的 Agent；不扩展成企业通用平台。
- 目标 AI 类型：coding-ai；执行者必须显式选择 gpt-5.6-luna / max，在独立 worktree 工作，不再委派。
- 本次授权：只补执行卡文档；不是启动本卡产品实现，不批准新的共享契约或真实数据/模型调用。
- 文档取材基线（不可当作未来开工基线）：9324210e11141a5925a5d5d3922b629587c78f93；该 main 已与远端一致。
- 未来实施基线：PENDING_SHARED_CONTRACT_COMMIT；本卡文档 hash、正式任务 ID/worktree、批准记录和共享契约版本必须在派发时绑定。
- 实施开工状态：PENDING_SHARED_CONTRACT；不得用“有卡”或旧方案 human_confirmed 代替开工批准。
- 基线总方案 SHA-256：e03b76369d5fdf831d27ab6d75eec26ac8300934b3678fea52c2de91627826e8。这是历史取材标识，后续版本变化需重新核对，不自动追随 HEAD。

## 2. 背景与目标

在现有 Workbench 中让用户完成资料导入、任务草案确认、显式开始 Run、查看工具证据和关系/定义草案及待确认问题。保留现有视觉/布局/滚动和 Workspace 行为，以真实 API 状态驱动，不把演示内容当当前 Workspace 结果。

当前已有 Workspace API/SQLite/切换组件；W0.1 source 077822d5056fa53523e5f22d13d9748bff765716 已经合入上述 main。Source/Mission/正式 AgentRun 尚未实现；不能把演示图、聊天动画、历史测试或 Git 推送当作这些能力已完成。

## 3. 先读哪些资料

按顺序阅读，冲突报主协调者，不选择较宽授权：

1. [仓库规则](../../AGENTS.md)。
2. [开发路径图](../../开发路径图.md) 第五节路径二，以及 [架构与迁移报告](../架构与迁移报告.md) 的相关章节。
3. [路径二开发方案](../路径二开发方案.md) §§4–10：材料、限额、状态、分工和验收。其 W0.0/W0.1 历史权限与回执不是后续阶段的自动授权。
4. 主协调者与人共同确认后的共享契约及本任务派发消息；当前这些精确接口仍 PENDING。
5. 现有 web/src/App.tsx、styles.css、content.test.ts、WorkspaceSwitcher.tsx、WorkspaceSwitcher.test.ts、api/client.ts、generated/api.ts；后两项只读。
6. [现有视觉记录](../checkpoints/visual-amendments-acceptance.md) 只约束其原构建的视觉；不能将其中 PASS 转用于新交互或新构建。架构 §11 与总方案 §9 是后续验收要求。

涉及未来安装/验证前，只读核对 .python-version、pyproject.toml、uv.lock，以及 web/package.json、web/package-lock.json 的既有精确版本；不升级依赖，不运行 npm 生命周期脚本。

两份人控文件在取材基线的 SHA-256：

- 开发路径图.md：e3a3c5850d1956e2e77b0d38844caf7aff7078a3e296093b2d88c0962a6a42c4。
- docs/架构与迁移报告.md：8cd561bc0927a08d0693028d8ed1d3f48faa4d1bf858b25d7a3b94580bf2873b。

## 4. 文件所有权与权限

未来写入上限：web/src/App.tsx、web/src/styles.css、web/src/content.test.ts；确有当前需求时可新增 web/src/Path2Workbench.tsx、web/src/Path2Workbench.test.ts。
WorkspaceSwitcher.tsx/测试、生成类型/client、后端、图片/图标资产和锁文件只读。缺 client 接缝交 W0，不手工补 generated 类型，不借新交互重做整页或改品牌视觉。

上面的列表是将来派发的写入上限，当前不能据此编辑代码。其他文件一律只读；不得改 AGENTS.md、路线图、架构报告、依赖或锁文件、publication manifest、其他 worker 的文件。新增路径或改变共享接口先报主协调者。

只用自己构造的最小合成测试输入。总方案 §4 的高潜客户候选材料仅在精确准入后使用；路径和 hash 不等于正文读取、复制、分发或 Provider 发送许可。禁止读取 .env、私有评测/evaluation、旧同名 fixture、整个 alphaox 材料目录或用户实际数据库。docs/migration-publication-manifest.json 当前不能当作该候选案例已 allow 的证明。

## 5. 给 AI 的任务说明：开工门通过后才执行

1. 保留当前主导航、对象/图区域、右侧 Agent、焦点/滚动/折叠行为。先列出现有 demo 展示与真实状态边界；无 API/Key/数据时显示明确 empty/blocked/not_implemented。
2. 用未来获准 typed client 完成选择 Workspace、批量上传与只读授权、逐文件状态/revision、profile/证据预览。上传本地与发送 Provider 是不同许可，不能捆绑或默许外发。
3. 展示当前输入的任务草案，用户确认精确 version/hash，再明确开始 Run；即便用“确认并开始”按钮，领域调用仍为两步。失败或未知结果不自动重复 POST。
4. 所有请求绑定 workspace/mission/run；切换 Workspace 时隔离旧异步响应，不让晚到结果污染新工作区；保留每 tab 的 view selection。
5. 展示实际 Run 阶段/工具收据、草案与待确认问题；SSE 按 id/sequence 去重并回读 snapshot，不拼造丢失 delta。取消、失败和等待确认不伪装成功。
6. 刷新从持久快照恢复显示。保留旧视觉回归并增加关键状态/调用边界测试，不删除旧测试凑 PASS；不实现澄清回答、审批或 Contract 发布。
7. 用合成临时数据验证完整交互；真实模型调用及最终人工验收留到各自授权门。

## 6. 约束、依赖与停止条件

- 保持本地 Python + React/TypeScript 结构；127.0.0.1；不加 SDK、Agent 框架、pytest、ORM、队列、连接器、MCP、知识/记忆服务或 UI 框架。
- 对象身份、workspace_id、权限、来源版本/hash、终止收据未知时关闭失败。数据证据不是业务批准；Agent 自然停止不是 Mission 完成。
- 实现只能交付到关系/定义草案与待确认问题；不实现回答审批、正式 Contract 发布、跨任务知识复用、对比评测或 Mission completed。
- 输入范围沿用总方案：UTF-8 CSV/JSON/Markdown/TXT；每批最多 8 文件/合计 8 MiB、单文件 2 MiB、每表 5000 行/100 列；超限拒绝，不静默截断。
- 当前文档阶段不装依赖、不跑产品测试/构建/服务，不创建 W1/W2/W3 任务，不做真实模型调用。
- 未来实施中的安装、测试、运行和 Git 动作必须以正式派发合同为准。上一批 main 合并/push 授权已执行完，不能延用；不得自动 push、merge main、部署、删分支/worktree、reset/clean/force-push。
- 若需要新接口/依赖/材料/业务判断、发现共享文件重叠、Git 冲突、未知外部结果、缺少权限或验收前提，停止受影响部分，报告位置、事实、影响与最小提议；不自行扩大白名单或降级验收。
- 已确认范围内的小修与检查不用反复重谈产品方向；越界需求由主协调者与人重新确认。

## 7. 验收与验证命令

未来命令（cwd 为实施 worktree 的 web/）：

    npm ci --ignore-scripts
    npm run check:api
    npm run typecheck
    npm test
    npm run build

现有 Vitest/SSR/纯状态测试至少覆盖 Workspace 切换晚到响应、确认与 Start 分离、旧 version/hash、POST 未知不重发、SSE 去重/快照恢复、取消/失败/空状态。SSR 与字符串断言不算鼠标键盘通过，不新增 DOM/测试依赖绕过 dependency gate。

人工/浏览器 checklist（实施后先填精确 branch/commit/build hash、临时 data_dir、素材版本、实际 URL；未填为 PENDING）：

1. 复用一个 tab，127.0.0.1（优先 8787，已占用则记录获准空闲端口，不关用户服务），1440×900、100% zoom；新建合成 Workspace，记录起点。
2. 上传自造的获准 CSV/JSON/MD/TXT；预期逐文件状态和可回读引用；另试损坏/超限，预期明确失败/partial，无假成功。
3. 生成任务草案→核对版本/hash→确认→明确 Start→观察工具/证据→查看草案/问题。该真实模型集成验证只能在已另行取得相应材料与模型调用许可后实际执行；此前最多用显式标记的测试替身，不能记真实流程通过。
4. 鼠标/Tab/Enter/Esc 检查可达性、焦点恢复、Agent 折叠、图与列表内部滚动、文本可读性；1280×800 检查窄窗口溢出，不新增移动端产品范围。
5. 检查 loading/empty/partial/blocked/failed/cancelled/stale/conflict、断开 SSE 后恢复、刷新、快速切 Workspace；确认未重发有副作用动作，console/network 不含敏感正文。
6. 记录每项 PASS/FAIL/NOT RUN 与证据；最后恢复同一测试 Workspace、默认区域、Agent 展开、1440×900/100%，不删用户数据。只有人能把精确构建的人工验收记 PASS。

这些是未来实施的验收方法，不是本次已经执行。必须报告非零测试数和每条命令的 exit code，不能用后一个成功掩盖前一个失败。文档检查、静态/单元测试、构建、运行、真实模型、浏览器、人验收、用户价值各自记录；NOT RUN/PENDING/partial/blocked/failed/cancelled/PASS 不互换。

## 8. 输出格式与交付回执

先给一个独立可复核检查点，不横跨下一路径。交付回执至少列：

- card_id/version/hash、共享契约版本与批准记录、base/branch/commit/worktree、exact owned files；
- 完成行为及未完成项，代码 diff 与验收项的对应关系；
- 实际命令/测试数/exit、运行数据和 build 身份、证据位置与限制；
- 来源权限、未知/失败、未解决风险；可回滚的最小本地检查点；
- commit/push/merge/远端 readback 分别是否做了；没有授权的不做；
- 真实模型 NOT RUN、人工 PENDING，除非已分别获得该项真实证据或人的精确构建验收。

## 9. 缺失信息或默认假设

- 当前任务 ID/worktree：NOT_CREATED。
- PENDING：W0 的精确 HTTP/client/状态契约、共享代码基线、卡 hash 与模型/材料许可；不得按候选字段先写页面锁死接口。
- PENDING：最终 branch/build/URL/测试数据与人工逐项记录；历史视觉 PASS 不继承。
- 默认只接当前 Workbench，不重做整体视觉、不引入 CSS/UI/状态框架，不硬编码案例答案。

默认只沿用已核对的仓库和既有依赖；不臆造客户、期限、费用、字段定义、API/Store 签名或 schema SQL。普通实现细节在批准边界内由执行者完成，产品、架构和跨 worker 契约由主协调者与人决定。

## 10. 保守翻译版、增强执行版与可直接复制版本

保守翻译版：只做本卡目标和 owned files 内的一个检查点，有证据地报告，不补不存在的业务事实。
增强执行版：先核验授权与基线，再按 §5 顺序实现，用 §7 公共行为反例验证，并按 §8 交付；任何新范围先报告。
可直接复制版本：请完整阅读本执行卡和 §3 权威材料，逐项核验正式派发消息的 card hash、共享契约批准、base/worktree 与权限。当前 PENDING 未解除就不要写产品代码；解除后只按 §4–7 做一个检查点，不改其他 worker 文件、不再委派，最后按 §8 回报并等待主协调者复核。

## 11. 实施绑定记录（2026-09-03）

本节只追加本次调度事实，解除的是本卡模块实现的开工等待，不改变前文目标、文件所有权、验证要求与停止条件。真实材料、真实模型、完整集成和人工验收门未解除。各代码 worktree 继续使用共同基线内的 v1.0 启动卡；下面的启动卡 hash 指该不可变版本，不包含本节新增记录。

- 共享契约：path2-shared-contract-v0.1；SHA-256：883aa4d4e7752ae8445f65f6d592effa52524ce156957a14c5b8c79c24ef017b。人已在主协调会话确认，批准记录见本地提交 bfdb97fd0321455a1d12d6412cf7ad589046e212 的 commit body 与本次正式派发。
- 共同代码基线：73ff723ca1859e569351fb7d1047ce706c560f99（codex/path2-shared-contract），已由主协调者复核。
- 启动卡：PATH2-W3 v1.0；派发 hash：0aa114e9828da5f50b23b852f4d6ea5c49bfc10669c246bd874c3ccbb34be5a9。
- 执行会话：路径二 W3 Workbench交互；thread_id：01a066d9-216f-74f3-91a6-6f03a4fac27f；host：local。
- 执行模型：gpt-5.6-luna；reasoning：max；唯一执行者，不再委派。
- worktree：/Users/archer/.codex/worktrees/544c/contextox-agent。
- 执行分支：codex/path2-w3-workbench；已读回 HEAD 从共同代码基线起步。
- 本次派发状态：RUNNING，任务已接单并完成基线核对，不是仅创建文档。模块交付与其最终检查结果仍待该任务提交后复核；本节不自动更新后续进度。
- Git 权限：仅本地限定文件与小步 commit；不 push、不 merge main、不发布/部署、不删除分支或 worktree。
- 真实案例：NOT RUN；真实模型：NOT RUN；人工验收：PENDING。共享基线检查通过不替代本模块或端到端验证。
