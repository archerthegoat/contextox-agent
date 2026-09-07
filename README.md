# ContextOx Workbench

数契（ContextOx）是帮助用户借助 Agent 理解和规范业务与数据知识的本地工作台，面向实际项目交付者、现有业务整理者，以及学习、转型和求职者。目前可在 Workspace 中导入授权资料、创建任务、对话分析并保存带证据的字段、关系与澄清候选。业务回答、批准、正式 Contract 和批准 Context 的闭环仍待实现。

当前实现与证据分层如下。任务对话详情见 [A–D 交付记录](docs/任务对话A-D交付与验收.md)。

规划阅读入口：[开发路径图](开发路径图.md)定义目标用户、差异化与路径通过门；[架构与迁移报告](docs/架构与迁移报告.md)定义领域状态和权限边界；[Agent 任务协作开发实施计划](docs/Agent任务协作开发实施计划.md)第 0 节说明现行合同、证据入口与开发路径对应关系。旧计划和执行卡中的“当前/下一步”须按其历史基线理解，不能据此重做已交付能力或跳过未完成验收。

| 能力或证据层 | 当前边界 |
| --- | --- |
| 本地 Python API、React/TypeScript Workbench、SQLite Workspace | 已实现 |
| 授权本地资料导入、来源版本、确定性解析与证据读取 | 已实现；只处理明确授权材料 |
| 任务草案确认、持久对话、有限历史与引用、执行历史 | 已实现 |
| 有预算的 Agent Loop、领域工具、取消、事件与收据 | 已实现；真实 Provider 可用性需要独立运行证据 |
| 字段与关系草案、生成澄清、提交候选审核 | 已实现；候选不代表业务事实获批 |
| 澄清回答与批准后续接、正式 Contract、批准 Context 复用 | 尚未实现 |
| 本批真实 Provider、私有资料迁移 | `NOT RUN` |
| 人工验收与用户价值 | `PENDING` / `NOT VERIFIED`；不能从测试、构建或 fake Provider 推导 |

## 任务对话增量

Workbench 支持在同一任务中明确发送问题、查看持久消息、选择有限历史、引用资料与草案字段/关系。每次发送对应一轮有预算的分析；执行历史与结构化结果在中间查看。普通消息不能代替业务裁决或批准，P5 回答与批准入口尚未实现。

新资料库使用 schema v4。已有 v3 资料库仍可读取，但对话写入需要显式迁移。先停止使用该资料目录的旧实例，再用已核验的新构建启动：

```bash
uv run contextox start --host 127.0.0.1 --port 8787 \
  --data-dir /absolute/path/to/authorized-data \
  --static-dir /absolute/path/to/matching-build \
  --migrate-task-dialogue
```

迁移会在授权资料目录内保留数据库、已知来源文件及校验清单的备份，拒绝存在活动执行或不符预期的 schema。不要向同一资料目录同时启动多个实例，也不要通过恢复旧备份丢弃迁移后产生的新消息。迁移失败时保留备份并核对状态，具体恢复边界见交付记录。

多个本地服务应使用各自版本匹配的静态目录。可用 `npm --prefix web run build -- --outDir /absolute/path/to/matching-build` 单独构建，避免覆盖旧服务正在使用的 `web/dist`。本轮自动预览只使用合成资料与 fake Provider，不能据此认定真实模型、已有私有资料迁移或人工验收通过。

ContextOx Workbench 的产品目标是把明确授权的文档、样例数据、数据库结构和人的经验，整理成有来源、可追溯、可验收、可复用的业务定义 Contract。实际使用者可以围绕自己的业务或交付任务梳理实体、字段、指标和规则；学习者可以通过明确标记的公开或合成案例练习业务理解、澄清和定义交付，学习入口的具体功能及效果仍待验证。

产品聚焦文档、样例数据、表结构和不同角色说法之间的定义缺口：粒度、身份、时间、口径、例外和责任人需要被明确。目标流程是从业务对象定位证据，把缺口转成可回答的问题，再将人的确认、版本和适用范围保存在可复用的 Workspace Context 中。

与 Codex、Claude Code 等通用 Agent 的差异化目标，是将业务对象、证据、澄清、确认、版本和交付物整合为可直接使用的流程，减少用户自行组织和维护这些工作的投入。通用 Agent 配合合适的 Skills、项目上下文、模板和工具是正式比较基线；资料读取、项目记忆和工具接入本身不作为独占优势。目前差异化收益仍为 `NOT VERIFIED`。定义包也可作为后续通用 Agent 的输入，当前不据此承诺新增自动集成。

近期“规范数据库”指梳理数据库背后的业务含义、关系与定义，产出整理建议和可确认的规范。练习材料及模拟裁决必须明确标记；真实业务批准、学习效果和求职成效分别判断。

N1 首阶段只交付了本地 Workbench 壳层；其历史能力与验证记录保留在下表，当前增量能力以上文为准。

## 产品闭环

```text
授权资料 → 确定性证据 → 结构化澄清 → 人的裁决 → Contract → 批准 Context
```

这条链路是产品目标。目前已覆盖资料、证据与澄清候选，人的回答与批准及其后续闭环仍待实现。

## N1 历史能力与验证记录

| 能力或证据层 | 状态 |
| --- | --- |
| 本地 Python API + React/TypeScript Workbench 壳层 | `IMPLEMENTED` |
| `contextox doctor` / `contextox start` | `IMPLEMENTED` |
| Pydantic → OpenAPI → TypeScript 类型与 typed client | `IMPLEMENTED` |
| SSE 连接骨架 | `IMPLEMENTED`；当前只有公开连接事件和心跳 |
| N1 自动验证、构建与外部临时目录运行烟测 | `PASS`；仅限 N1 壳层范围 |
| Workspace、来源读取、解析与 profiling | `NOT STARTED` |
| Agent Loop、领域工具与真实 provider | `NOT STARTED`；真实模型为 `NOT RUN` |
| Clarifications、审批、Contract、Context 持久化 | `NOT STARTED` |
| 浏览器人工 Workbench 验收 | `PENDING` |
| 用户价值或 FDE 对比证据 | `NOT VERIFIED` |

`PASS` 只表示对应自动化或运行证据通过，不代表产品完成、真实模型可用或人已验收。Human acceptance 仍由人针对精确 commit/build 记录。

## 本地快速开始

需要 Python `3.14.7`、UV 和 Node.js/npm。依赖版本已锁定；安装前核对项目中的依赖审查记录。

```bash
uv sync --locked

cd web
npm ci --ignore-scripts
npm run generate:api
npm run check:api
npm run typecheck
npm test
npm run build
cd ..

uv run contextox doctor
uv run contextox start
```

然后打开 <http://127.0.0.1:8787>。服务只绑定本机回环地址；默认数据目录为 `.contextox-agent/`。用于验收时请明确指定仓库外的临时数据目录；已有资料目录的迁移需要单独授权。

也可以导出当前 API 合同：

```bash
uv run contextox openapi --output /tmp/contextox-openapi.json
```

`doctor` 核对 Python、锁定依赖、公开接口与构建资源；传入 `--data-dir` 时还会检查现有资料库。它不读取凭据、不发起模型请求、不导入业务资料，因此 Provider 与 customer_data 检查保持 `not_run`，整体可以是 `partial`。输出的 `scope: n2a` 是兼容现有接口的诊断范围标识，不是当前产品版本或能力清单。

如果旧虚拟环境指向已经失效的临时 Python 目录，先保存 `.venv/pyvenv.cfg` 和解释器链接，再用 UV 在原位置修复，保留现有包：

```bash
uv venv --allow-existing --no-python-downloads --python /absolute/path/to/installed/python3.14 .venv
uv sync --locked
uv run python --version
```

选定的解释器必须是已批准的 `3.14.7`；不要修改系统 Python。

## Workbench 里的四个区域

- **资料来源**：导入授权材料，查看解析结果、来源版本与证据；导入不等于允许向模型发送。
- **任务**：选择实际任务，在右侧发送问题，中间查看结果与执行历史。
- **待澄清**：查看模型生成的问题及依据；回答、批准和续接入口尚未开放。
- **业务契约**：查看字段与关系草案、未知项和版本；正式 Contract 审批尚未实现。

运行范围限本机回环服务，不提供任意文件/SQL/shell 执行、远程同步、SSO、多用户协作、云端部署或发布。聊天与模型推断不会自动成为获批的公司事实。

## 技术边界

- Python 后端由 UV 管理；FastAPI、Pydantic 和 Uvicorn 是锁定的运行时依赖。
- Pydantic 模型是 HTTP JSON、SSE envelope 和错误边界的权威来源；OpenAPI 生成 TypeScript 类型，前端通过 `openapi-fetch` 使用 typed client。
- React + TypeScript 构建本地静态 SPA，由同一个绑定 `127.0.0.1` 的 Python 进程提供。
- N1 使用标准库测试，不引入 ORM、Redux、CSS/UI framework、pytest、Agent framework 或 provider SDK。

## 权威文档

- [`开发路径图.md`](开发路径图.md)：人工控制的产品方向与开发顺序；不会由代码或测试自动同步。
- [`docs/架构与迁移报告.md`](docs/架构与迁移报告.md)：批准的架构、状态、失败语义、迁移边界和验收清单。
- [`docs/migration-publication-manifest.json`](docs/migration-publication-manifest.json)：旧材料公开迁移的逐文件决策记录。

## 隐私与许可证

客户资料、公司私有资料、运行数据库、凭证、原始 provider payload、敏感日志和私有评测数据不得进入 Git、普通日志或测试。只处理明确授权的本地材料；发送给 Provider 前必须确认本次输入与资料范围。具体边界以 [`AGENTS.md`](AGENTS.md) 和上述架构报告为准。

本项目采用 [MIT License](LICENSE)。
