# ContextOx Workbench

ContextOx Workbench 是一个面向 FDE 的本地私人业务定义助手。它接收客户明确授权的文档、数据样例与仓库资料，发现业务定义中的缺口和冲突，向合适的人提出结构化澄清，并把获批回答内化为有来源、版本和责任边界的业务定义 Contract。

它不是通用 Coding Agent，也不以替代 Codex 或 Claude Code 写代码为目标。它要证明的是：在同样的资料和时间下，能否更稳定地发现语义缺口、减少对齐轮次、形成可复用的批准定义，并把结果交给后续工程实现。

> 当前状态：产品与技术方向已于 2026-09-02 重新对齐；Python 后端、React/TypeScript Workbench 与 ContextOx 自有薄 Agent Loop 尚未实现。此前 C1 TypeScript CLI 骨架在提交 `8b234eaa6830e66c4217067e1d52997811730a13` 上完成的自动验证和人工验收仍是有效历史证据，但不证明新架构或 V0 产品闭环。真实模型验证为 `NOT RUN`，Workbench 人工验收为 `PENDING`，用户价值为 `NOT VERIFIED`。

## V0 目标

- 一个由 FDE 在个人电脑上部署和使用的本地 Web Workbench。
- 支持多个相互隔离的客户 Workspace。
- 支持批量上传授权文件，并区分文档、结构化数据、SQL/DDL 与代码仓库资料。
- Agent 只能通过 ContextOx 的领域工具读取获准来源、检查数据、提出澄清和更新结构化草案；不提供任意 `bash`、文件 `write/edit` 或代码执行能力。
- 需要人裁决时生成专业但低沟通成本的澄清表单，说明为什么要问、需要谁回答、期望什么证据以及答案会影响哪些定义。
- 人的回答和批准绑定精确版本与哈希；只有获批内容才能进入可复用的 Workspace Context。
- Workbench 实时展示模型阶段、工具调用、证据、缺口、表单、草案变化和终止状态。
- 使用同一批资料与 Codex/Claude Code 做基线对比，分别记录完整性、错误、澄清质量、人工修改量和总耗时。

## 技术基线

- 后端：Python，由 UV 管理 Python 版本、虚拟环境、依赖和 lockfile。
- API Contract：Pydantic 模型是唯一权威，经 OpenAPI/JSON Schema 生成前端 TypeScript 类型与客户端；生成文件不手工编辑。
- 前端：React + TypeScript，本地静态 SPA；第一阶段不引入 Next.js、SSR、Redux 或云端前端服务。
- 通信：本地 HTTP JSON + Server-Sent Events（SSE）。
- 持久化：SQLite 保存产品状态、来源版本、批准记录、运行收据和审计；是否使用 DuckDB 处理较大表格由真实样例决定。
- Agent Runtime：ContextOx 自有的薄 Python Loop，第一阶段只支持串行领域工具、明确预算、取消和结构化事件。
- 部署：一个绑定 `127.0.0.1` 的本地 Python 进程提供 API、SSE 和构建后的 Workbench 静态文件。

所有依赖的精确版本、许可证、生命周期脚本、完整性与 lockfile diff，仍需在安装或变更前单独审查和批准。

## V0 不做

- 通用 Coding Agent，或任意 `read/write/edit/bash` 工具集。
- Agent 自动执行代码、任意 SQL、生产数据库写入或外部副作用。
- SSO、RBAC、多用户协作、云同步、远程托管或分布式工作流。
- 飞书集成；它是首个本地闭环获得证据后的第二阶段候选。
- 向量数据库、知识图谱、独立 Memory 服务或 MCP Server。
- 把聊天、未批准表单回答或 Agent 推断自动晋升为公司事实。

## 状态边界

| 层级 | 当前状态 |
| --- | --- |
| 2026-09-01 C0/C1 历史基线 | `PASS`，仅证明旧 TypeScript CLI 骨架 |
| 2026-09-02 新架构文档 | `APPROVED`，不代表实现 |
| Python 后端与 Workbench 实现 | `NOT STARTED` |
| 自动验证 | 新架构 `NOT RUN` |
| 真实模型验证 | `NOT RUN` |
| Workbench 人工产品验收 | `PENDING` |
| 用户价值证据 | `NOT VERIFIED` |

静态文档、旧 C1 测试或构建通过，不能替代新架构的运行验证、浏览器验收、真实模型结果或用户价值证据。

## 数据与公开边界

客户资料、公司私有资料、运行数据库、凭证、原始 provider payload、敏感日志和私有评测数据不得进入 Git。旧仓库材料只有在 [`docs/migration-publication-manifest.json`](docs/migration-publication-manifest.json) 中完成逐文件权利、许可证、第三方内容、secret、PII、私有数据和 evaluator-only 审查，并由人明确标为 `allow` 后才能复制。

## 权威文档

- [`开发路径图.md`](开发路径图.md)：人工控制的产品方向与开发顺序。
- [`docs/架构与迁移报告.md`](docs/架构与迁移报告.md)：V0 架构、状态、失败语义、迁移边界与验收清单。
- [`docs/migration-publication-manifest.json`](docs/migration-publication-manifest.json)：旧材料公开迁移的逐文件决策记录。

## License

本项目采用 [MIT License](LICENSE)。
