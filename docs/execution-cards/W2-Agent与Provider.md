# W2 执行卡：Agent 与 Provider

## 1. 身份、原意与当前授权

- card_id：PATH2-W2；card_version：1.0；日期：2026-09-03。
- 用户原意（摘要）：每个 worker 有独立执行说明，明确去哪里看、做哪些事、怎样验收，避免开发偏离。
- 原意判断：先跑通读表、关系与字段定义草案的 Agent；不扩展成企业通用平台。
- 目标 AI 类型：coding-ai；执行者必须显式选择 gpt-5.6-luna / max，在独立 worktree 工作，不再委派。
- 本次授权：只补执行卡文档；不是启动本卡产品实现，不批准新的共享契约或真实数据/模型调用。
- 文档取材基线（不可当作未来开工基线）：9324210e11141a5925a5d5d3922b629587c78f93；该 main 已与远端一致。
- 未来实施基线：PENDING_SHARED_CONTRACT_COMMIT；本卡文档 hash、正式任务 ID/worktree、批准记录和共享契约版本必须在派发时绑定。
- 实施开工状态：PENDING_SHARED_CONTRACT；不得用“有卡”或旧方案 human_confirmed 代替开工批准。
- 基线总方案 SHA-256：e03b76369d5fdf831d27ab6d75eec26ac8300934b3678fea52c2de91627826e8。这是历史取材标识，后续版本变化需重新核对，不自动追随 HEAD。

## 2. 背景与目标

实现单 Provider、串行领域工具的薄 Agent Loop，产生真实受限分析过程、收据和候选草案；不实现通用 Agent 框架，也不以 fake 对话替代真实模型验收。模型只建议，领域状态与权限由 ContextOx Store 再校验。

当前已有 Workspace API/SQLite/切换组件；W0.1 source 077822d5056fa53523e5f22d13d9748bff765716 已经合入上述 main。Source/Mission/正式 AgentRun 尚未实现；不能把演示图、聊天动画、历史测试或 Git 推送当作这些能力已完成。

## 3. 先读哪些资料

按顺序阅读，冲突报主协调者，不选择较宽授权：

1. [仓库规则](../../AGENTS.md)。
2. [开发路径图](../../开发路径图.md) 第五节路径二，以及 [架构与迁移报告](../架构与迁移报告.md) 的相关章节。
3. [路径二开发方案](../路径二开发方案.md) §§4–10：材料、限额、状态、分工和验收。其 W0.0/W0.1 历史权限与回执不是后续阶段的自动授权。
4. 主协调者与人共同确认后的共享契约及本任务派发消息；当前这些精确接口仍 PENDING。
5. 架构 §§7、9.1、10.2–10.3 及 §18 中 DeepSeek 官方协议来源。开始协议实现前核对官方文档；如与批准配置实质冲突，报告而不自行换模型。
6. W0 交付的 models.py、Store 接缝与生成 schema（只读）；当前它们尚未具备正式 Run。

涉及未来安装/验证前，只读核对 .python-version、pyproject.toml、uv.lock，以及 web/package.json、web/package-lock.json 的既有精确版本；不升级依赖，不运行 npm 生命周期脚本。

两份人控文件在取材基线的 SHA-256：

- 开发路径图.md：e3a3c5850d1956e2e77b0d38844caf7aff7078a3e296093b2d88c0962a6a42c4。
- docs/架构与迁移报告.md：8cd561bc0927a08d0693028d8ed1d3f48faa4d1bf858b25d7a3b94580bf2873b。

## 4. 文件所有权与权限

未来写入上限：src/contextox/agent.py、src/contextox/provider.py、tests/test_agent.py、tests/test_provider.py（计划路径，尚未创建）。
不改 Store/API/模型/client、来源解析或页面；不拿任意文件路径/原始 SQLite 连接执行领域写入。W0 负责 HTTP、生命周期接线与持久状态。

上面的列表是将来派发的写入上限，当前不能据此编辑代码。其他文件一律只读；不得改 AGENTS.md、路线图、架构报告、依赖或锁文件、publication manifest、其他 worker 的文件。新增路径或改变共享接口先报主协调者。

只用自己构造的最小合成测试输入。总方案 §4 的高潜客户候选材料仅在精确准入后使用；路径和 hash 不等于正文读取、复制、分发或 Provider 发送许可。禁止读取 .env、私有评测/evaluation、旧同名 fixture、整个 alphaox 材料目录或用户实际数据库。docs/migration-publication-manifest.json 当前不能当作该候选案例已 allow 的证明。

## 5. 给 AI 的任务说明：开工门通过后才执行

1. 将 MissionDraftAttempt 与正式 Run 分开：草案一次 non-streaming JSON，仅 P0+当前不可变原始输入，无 Source/tools/跨 Mission 聊天；不自动 retry，不创建 Mission。人确认精确版本/hash、显式 Start 由 W0 领域层处理。
2. 正式 Run 按 preflight→一致 Snapshot/ContextPacket→streaming 模型响应→完整工具批次校验→串行执行→显式终止。P0 与 data/evidence-only 内容分开；不制造未批准的知识或 MEMORY。
3. 沿用架构冻结的单 DeepSeek Chat Completions 配置与允许的人工选择；不新增 SDK、自定义 base_url、自动路由或 fallback。密钥只从规定环境入口使用，不读 .env、不记录值/长度/hash。
4. 收完整 tool-call chunks 后，先整批 JSON/Pydantic/allowlist/权限/状态校验；静态错误整批不执行。运行中失败保留已完成 receipt、跳过后续；业务拒绝、协议失败与权限 blocked 分开。
5. create_clarification/submit_for_review/finish_run 必须单独成批；普通 stop 无 terminal_result 时失败。路径二只到等待确认，不能完成 Mission。
6. 执行方案预算：8 轮模型、24 次工具、300 秒、单次输出 4096 tokens、自动 retry 0；connect/first-event/idle/total 为 10/60/30/120 秒并受 Run 剩余时间限制。缺 usage 立即 blocked，不再请求；取消/关闭及时停止。
7. reasoning_content 只在当前 Run 内存 transcript 中；不进入事件、SQLite、MissionMessage、日志、审计或跨 Run。对外只给有界公开内容/metadata/usage/收据，终态与故障丢弃隐藏 transcript。
8. 使用隔离 fake transport 测协议与循环，经 W0 接到实际运行；本阶段不发任何真实 Provider 请求。

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

未来命令（实施 worktree 根）：

    uv sync --locked
    uv run --locked python -m compileall -q src
    uv run --locked python -m unittest discover -s tests -p 'test_agent.py' -v
    uv run --locked python -m unittest discover -s tests -p 'test_provider.py' -v

至少覆盖请求组装/只发获准上下文、chunk 切分/keepalive/tool 参数拼接/usage、半流和错误响应、非法或混合 terminal 批次、自然 stop、缺 usage、业务拒绝续跑、权限/旧 hash、取消/预算/重启不续 hidden transcript、receipt 与终态一致。
验证草案请求无 Source/tools、失败不创建 Mission；任何跨 Workspace 内容或 reasoning 泄漏均不得通过。fake 测试不算真实模型 PASS；首次真实试跑须先另行完成精确材料准入并取得 Provider 调用许可。

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
- PENDING：ContextSnapshot/Packet/Manifest、Provider/Tool/Terminal receipt、严格工具参数及 Store 调用/Run 驱动的精确接缝；不能采纳尚未批准的候选实现。
- PENDING：共享基线 commit/卡 hash；真实材料和 Provider 调用许可。首次真实验证仍是路径二后续门，不因当前禁止调用而删掉。
- 默认维持架构指定 DeepSeek 配置；不选择新模型/SDK/transport 体系或更大预算。

默认只沿用已核对的仓库和既有依赖；不臆造客户、期限、费用、字段定义、API/Store 签名或 schema SQL。普通实现细节在批准边界内由执行者完成，产品、架构和跨 worker 契约由主协调者与人决定。

## 10. 保守翻译版、增强执行版与可直接复制版本

保守翻译版：只做本卡目标和 owned files 内的一个检查点，有证据地报告，不补不存在的业务事实。
增强执行版：先核验授权与基线，再按 §5 顺序实现，用 §7 公共行为反例验证，并按 §8 交付；任何新范围先报告。
可直接复制版本：请完整阅读本执行卡和 §3 权威材料，逐项核验正式派发消息的 card hash、共享契约批准、base/worktree 与权限。当前 PENDING 未解除就不要写产品代码；解除后只按 §4–7 做一个检查点，不改其他 worker 文件、不再委派，最后按 §8 回报并等待主协调者复核。
