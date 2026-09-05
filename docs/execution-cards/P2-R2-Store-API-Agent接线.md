# PATH2-P2-R2 执行卡：Store / API / Agent 接线

## 1. 身份、版本与已批准执行范围

- card_id：`PATH2-P2-R2`；card_version：`1.0`；日期：`2026-09-04`。
- 目标：在已经进入 `main` 的路径二共享契约之上，完成 SQLite v2、来源持久化、任务与 Run 生命周期、七个领域工具、单进程 Agent slot、真实 Store/API/Agent 接线和持久化 SSE；交付到带证据的关系/字段定义草案和待确认问题，不能完成 Mission。
- 执行者：当前任务唯一实际执行者；使用保存项目的本地 checkout；不创建 Worktree，不再委派。
- 起点：`/Users/archer/LocalProjects/contextox-agent`，干净 `main@6bb22c3def6234ddab6bf73adc4e20266c483e50`；执行前必须复核 `origin/main` 同 SHA、`0/0`、目标分支不存在。
- 执行分支：从该起点创建 `codex/path2-p2r2`。不要在 `main` 上直接写代码。
- 已确认的共享契约：`path2-shared-contract-v0.1`，已批准并进入 main；本卡不改 `开发路径图.md`、`docs/架构与迁移报告.md`、`docs/路径二开发方案.md`，也不重新设计共享接口。
- 本卡授权包含：本卡白名单内的本地代码、测试、锁定依赖安装、仓库外临时运行、标准库/fake Provider 验证、每个 checkpoint 的本地 commit，以及本卡末尾明确列出的 feature branch push、无漂移时的 local main 合并和 main push。不存在的外部业务副作用不在授权内。

## 2. 先读的权威材料与验收目标

按以下顺序完整阅读并以较窄边界为准：

1. 仓库根 `AGENTS.md`；
2. `开发路径图.md`，尤其路径二和人控路线边界；
3. `docs/架构与迁移报告.md` §§6–10；
4. `docs/路径二开发方案.md`；
5. `docs/路径二共享契约.md`；
6. 本执行卡；
7. 现有 `models.py`、`provider.py`、`sources.py`、`store.py`、`api.py`、`agent.py`、相关测试、生成脚本和 `web/` 依赖清单。

用户可接受的结果是：在明确授权的本地文件输入上，通过真实 Store/API/Agent 接线完成“上传来源 → 创建任务草案 → 核对版本/hash → 显式确认 → 显式 Start → 持久化 Run/SSE/工具收据 → 关系/字段草案和澄清问题”的受限闭环；所有 Workspace 隔离、状态、版本、hash、收据、失败和重启恢复可回读。自动测试、fake Provider、服务启动和浏览器观察均不能单独替代真实模型、人验收或用户价值证据。

## 3. 写入白名单与明确禁止项

只能修改下列路径：

- `docs/路径二共享契约.md`；
- `docs/execution-cards/P2-R2-Store-API-Agent接线.md`；
- `src/contextox/store.py`；
- `src/contextox/api.py`；
- `src/contextox/agent.py`；
- `src/contextox/runtime.py`，仅在确有单进程 runtime slot 需要时；
- `tests/test_store.py`；
- `tests/test_api.py`；
- `tests/test_agent.py`；
- `tests/test_runtime.py`，仅与新增 runtime 一一对应时；
- `web/src/generated/api.ts`，只能由现有生成命令更新。

禁止修改：`开发路径图.md`、`docs/架构与迁移报告.md`、`docs/路径二开发方案.md`、`src/contextox/models.py`、`src/contextox/provider.py`、`src/contextox/sources.py`、`src/contextox/cli.py`、`web/src/Path2Workbench.tsx`、`web/src/api/client.ts`、依赖清单、lockfile、生成脚本以及白名单外任何文件。不得读取 `.env`、真实 Provider、客户/候选材料、真实数据库或私有评测正文。Agent 集成只使用自造输入、fake Provider 和仓库外临时 `data_dir`。

不引入新依赖、ORM、队列、worker service、MCP、任意文件/SQL/代码执行、真实网络 Provider、云托管、部署、tag、GitHub metadata 或破坏性 Git 操作。

## 4. 冻结架构与不变契约

- 使用专用 SQLite v2 表和真实关键列；复杂 JSON 必须使用 canonical JSON 持久化，并经过 Pydantic readback；不把一个大 JSON blob 当作关系约束的替代品。
- 已知 v1 只有 `workspaces` 精确 schema；v1→v2 必须在事务内增量迁移，不清空、不重建、不静默修复。未知、损坏、symlink、non-regular、hash/size 不匹配和 schema 不精确时 fail closed；迁移失败保护原 v1 可读性，迁移前后均能 rollback/readback。
- Source 原始字节只写 Git 外的 Workspace 私有 opaque UUID 路径，原始文件名只用于展示；SourceRevision identity immutable。文件写入和数据库结果未知时先 reconcile，不自动重试或伪造成功。
- 所有对象及对象级操作强制 `workspace_id`；parent、复合外键和查询均同时约束 Workspace/Mission/Run identity，跨 Workspace 不返回数据。
- Persist Attempt、Mission、Run、Manifest、DefinitionDraft、Clarification、ProviderReceipt、ToolReceipt、TerminalReceipt 和公开 Events 的生命周期、CAS、幂等、状态校验、取消和重启恢复；`agent_end`、普通文本、确认和 `submit_for_review` 都不能完成 Mission。
- 单进程最多一个活动 Agent/Provider slot，无队列、无持久 worker service；busy 必须在创建业务对象或发送 Provider 前拒绝，并响应取消、shutdown、启动失败和回收。
- `event_id` 是当前 Run sequence 的十进制字符串；阶段/收据/终态事件持久化，`model_delta` 仅有界内存实时发送，不逐条入库。重连缺口靠 Snapshot 和不完整提示，不伪造文本或隐藏推理。
- P2-R2 不提供合法 Mission `completed` 通道；Run 只能准确表示 `waiting_for_human`、`partial`、`blocked`、`failed`、`cancelled` 等状态，保留 completed schema 但不得成功生成。
- Provider 仍位于既有 Provider 边界。生产真实 Provider 不调用；fake Provider 只用于隔离回归。不得写入 raw request/response、密钥、prompt、源正文、reasoning 或异常 body。

## 5. P2-R2A：SQLite v2、迁移、Source 与 Artifact

实现并独立提交：`feat(store): add v2 source persistence`。

范围：

1. 精确 v2 DDL、schema/version 识别、新库初始化和 v1→v2 事务迁移；保留现有 Workspace 数据和精确 v1 约束。
2. Source/SourceRevision/SourceIssue/Artifact 的关系持久化，原始字节写入 opaque UUID 私有目录；按白名单限额拒绝超限，不静默截断。
3. Store 的 Workspace-scoped Source 创建、列表、Artifact 读取、Excerpt 读取；所有结果由模型构造并 readback，错误只暴露有界 code/message/request_id。
4. 覆盖 migration rollback、未知/损坏 schema、symlink/non-regular entry、hash/size mismatch、跨 Workspace identity、重复/限制、`partial`/`blocked`/`failed` 和 restart readback；不调用 Provider。

P2-R2A 不实现 Mission/Run 的假成功接口，不把 bytes、原始正文、内部路径或 SQLite 错误回显给 HTTP。Source upload 的未知结果必须先通过 list/reconcile 再决定人工动作。

## 6. P2-R2B：完整生命周期、工具与 fake Provider Agent

实现并独立提交：`feat(agent): persist path2 lifecycle and tools`。

范围：

1. 持久化 `MissionDraftAttempt`、`Mission`、`MissionMessage`、`Run`、`ContextPacketManifest`、`DefinitionDraft`、`ClarificationRequest`、`ProviderReceipt`、`ToolReceipt`、`TerminalReceipt` 和 Run events。
2. 实现草案单次请求、candidate version/hash 确认、Mission 创建、显式 Start 两步语义；实现 `client_request_id` 幂等和不同 payload 冲突，旧 version/hash 不能写入。
3. 实现 Run 状态 CAS、同 Mission 最多一个 queued/running Run、固定 RunBudget、取消、terminal 独占批次、事务原子性和 restart recovery。发现无 terminal receipt 的旧 active Run 时封存 `failed:interrupted_without_receipt`、Mission blocked，不自动调用 Provider。
4. 实现七个领域工具：`list_sources`、`read_source`、`inspect_dataset`、`update_definition_draft`、`create_clarification`、`submit_for_review`、`finish_run`。完成整批参数/allowlist/Workspace/权限/版本校验后才串行执行；业务拒绝可返回 rejected；协议、权限和状态错误分别保持 failed/blocked，不执行后续调用。
5. 实现 ContextSnapshot→Manifest→ProviderReceipt 的可校验关联；不保存临时 transcript，不把模型文字或自然停止当作 terminal，不允许 Mission completed。
6. 使用真实 SQLite Store 和 fake Provider 做集成测试，覆盖事件顺序、收据、CAS/幂等/取消/失败/重启和跨 Workspace；不得把 fake 结果写成真实模型证据。

## 7. P2-R2C：单进程 Runtime、HTTP/SSE 与生成类型

实现并独立提交：`feat(api): wire bounded path2 runtime`。

范围：

1. 实现单 slot、后台启动、busy、启动失败、shutdown、cancel 和 bounded join/recovery；HTTP handler 不同步阻塞事件循环。不得引入队列或 worker service。
2. 将现有路径二 API 接入真实 Store/Agent：合法 Workspace 上按契约返回成功/202；未知 Workspace 保持 404；无授权、非法版本/hash、冲突、预算/slot/状态错误保持有界失败。
3. 实现 Source upload/list/Artifact/excerpt、Mission draft attempt、confirm、Mission list/snapshot、Run start/snapshot/cancel 的真实生命周期。
4. 实现 Run SSE 持久回放、实时 delta、`Last-Event-ID` 去重、terminal event、Snapshot recovery 和缺口提示；event identity/sequence 只由 Store 产生。
5. 更新 OpenAPI 并用现有生成命令更新 `web/src/generated/api.ts`；如果生成会要求修改白名单之外文件，立即停止，不改生成器、不手改 generated 文件。

## 8. 状态、CAS、幂等、取消与重启证据

必须分别验证并报告：

- queued/running/waiting_for_human/partial/blocked/failed/cancelled 的合法转移和非法转移；P2-R2 completed 成功路径不可达；
- attempt candidate/version/hash 的精确读回、确认重放、不同 payload conflict、旧 hash/version conflict；
- Start 的 `client_request_id` 同 payload 只读回原 Run，不重复创建或发送；不同 payload conflict；同 Mission 第二个活动 Run 在对象创建前拒绝；
- 工具收据 ordinal/call_id/arguments hash/source refs 与 Run/Workspace 对齐；terminal 独占批次；相同结果重放；
- cancel 在 queued/running/terminal 前后的结果，Provider 可能已发出的字节保持 unknown，不自动重试；shutdown 不留下可继续调用的 slot；
- 进程重启后读回来源、Artifact、Mission、Run、Draft、Clarification、Receipt、terminal 和 events；缺 terminal 的 active Run 转为 failed/blocked，不自动触发 Provider；
- SSE 的十进制 event_id、Run 单调 sequence、持久事件重放、Last-Event-ID、实时 delta 不入库、缺口 Snapshot 和最终摘要只从 final_output 恢复。

## 9. 验收清单与证据分栏

每个 checkpoint 和总交付都必须分开记录：

- implementation：代码和白名单内行为是否完成；
- automated：标准库测试、schema/drift、typecheck、前端测试和 build；
- runtime：仓库外临时 `data_dir`、`127.0.0.1`、精确 branch/commit/build 上的 HTTP/SSE/readback；
- fake Provider：仅证明隔离的 Loop/状态/工具接缝；
- real Provider：本卡 `NOT RUN`，不得读取 `.env` 或调用真实网络；
- browser/human acceptance：本卡 `PENDING`，只能由人针对精确 build 记录 `PASS`；
- user value：`NOT VERIFIED`，不能由测试、DeepSeek smoke 或 Agent fake run 代替。

## 10. 技术检查命令

在对应工作目录执行并逐条保留 exit code；npm 命令在 `web/`：

```text
uv sync --locked
uv run --locked python -m compileall -q src
uv run --locked python -m unittest discover -s tests -p 'test_*.py' -v
uv run --locked contextox doctor --json
npm ci --ignore-scripts
npm run generate:api
npm run check:api
npm run typecheck
npm test
npm run build
git diff --check
```

所有测试、build、runtime smoke 使用 task-specific 的仓库外 `/private/tmp` 路径；不要污染仓库，不要输出秘密。敏感扫描只报告命中数量/路径和结论，不输出秘密、正文或原始 payload。运行 smoke 必须新建并在结束后清理临时 `data_dir`，再 readback 确认已清理。

运行 smoke 至少覆盖：新库 v2、v1 migration、migration failure/rollback、health、OpenAPI、旧 `/api/events`、root assets、Workspace create/list/get、Source upload/list/Artifact/excerpt、restart readback，以及非 Provider 路径的 Run/SSE；只绑定 `127.0.0.1`。

## 11. 受控浏览器验收（仅供人执行）

使用一个可复用 tab、`http://127.0.0.1:<port>/`、1440×900、100% zoom；记录精确 branch、commit、build、runtime data_dir、测试输入和 reset 方法。按以下顺序操作：导入自造/已获准材料，生成任务草案，核对版本/hash，确认，明确 Start，观察工具/证据/状态，查看关系/字段草案与待确认问题。

人的 checklist 必须覆盖鼠标、键盘、Tab/Enter/Esc、focus、scroll、loading、empty、partial、blocked、failed、cancelled、stale、conflict、刷新恢复、断线 SSE、长 ID/表格/错误可读性、console/network 敏感信息和重复副作用。路径二本轮只查看澄清，不办理回答批准或正式 Contract 发布。浏览器观察不等于真实 Provider 或用户价值 PASS；未执行保持 `PENDING`/`NOT RUN`。

## 12. Git checkpoint 与外部效果

- 每个 checkpoint 只 stage 白名单内 owned paths，逐项检查 staged diff、`git diff --check`、敏感模式和 scope，再独立 commit。不得广泛 `git add`、reset、clean、force-push 或重写历史。
- docs checkpoint 只包含共享契约批准状态文字和本执行卡；随后依次为 R2A、R2B、R2C。每一步先定向验证，形成可回滚父链，再进入下一步。
- 完整验证后、push 前重读实时 `origin/main`；必须仍为 `6bb22c3def6234ddab6bf73adc4e20266c483e50`。若漂移、冲突、失败或远端结果未知，停止并报告；未知 push 先 `ls-remote`/fetch reconcile，绝不盲目重试。
- 无漂移且所有检查证据满足时，push `codex/path2-p2r2`；再无 `main` 冲突地 `--no-ff` 合并到 local `main` 并 push `main`。验证所有 feature commits 是 local/remote `main` 祖先、tracking `0/0`、working tree clean。
- 不删除任何分支或 worktree，不部署、不发布、不 tag、不改 GitHub metadata。commit/push/merge 仍不等于 release、deployment、真实模型、人验收或用户价值。

## 13. Stop conditions、风险与回滚

发现需要修改白名单之外的文件、依赖、生成脚本、模型/API/Pydantic 字段、表集合、状态语义、权限、外部作用或任何架构边界时，立即停止受影响部分并报告位置、事实、影响和最小提议；不得自行扩 scope。真实 Git 漂移/冲突、未知副作用、未知迁移结果、Provider/材料权限缺失、预算不足或无法安全隔离用户改动同样停止。

最小回滚边界是最后一个已验证 checkpoint commit：docs、R2A、R2B、R2C 各自可独立回退；禁止使用 destructive reset 恢复。SQLite 迁移必须保留 v1 失败保护，源码文件和 DB 结果未知先 reconcile；不删除审计/收据/用户数据。

## 14. 最终交付回执格式

回报 card/version、共享契约版本/批准状态、起点、branch、worktree、所有 commit 及 parents、精确文件范围和禁止项检查；说明 DDL/schema、v1→v2 migration/rollback、Source opaque storage、Pydantic/canonical JSON readback、Workspace 隔离；逐项说明状态/CAS/幂等/工具收据/取消/restart recovery/SSE evidence；列出所有命令、exit code、测试数量、runtime data_dir/port/cleanup、scope/sensitive/diff 结果。

分别回报 implementation、automated、runtime、fake Provider、real Provider、browser/human acceptance、user value；保留 `NOT RUN`、`PENDING`、`partial`、`blocked`、`failed`、`cancelled` 与 `PASS` 的原义。报告 feature push、main merge/push、远端祖先/tracking/clean readback、未解决风险和回滚边界。真实 Provider `NOT RUN`，真实 Agent E2E `NOT RUN`，人工 `PENDING`，用户价值 `NOT VERIFIED`，除非各自有独立且可回读的证据。
