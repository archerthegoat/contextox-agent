# ContextOx Workbench

ContextOx Workbench 是一个面向 FDE 的本地业务定义助手：把客户明确授权的资料和人的裁决，整理成有来源、可追溯、可验收、可复用的业务定义 Contract。

它要解决的不是“再做一个会写代码的 Agent”。FDE 真正容易卡住的地方，是文档、样例数据、表结构和不同角色的说法无法稳定对齐：粒度、身份、时间、口径、例外和责任人常常藏在资料之间，也常常没有被明确回答。ContextOx 的任务是把这些缺口显出来，让问题能交给合适的人回答，再把获批定义留在 Workspace Context 中。

N1 是这个方向的本地 Workbench 壳层。它提供可运行的 Python API、React/TypeScript 界面、OpenAPI 类型链路和 SSE 连接骨架；它还没有读取客户资料、调用模型或完成业务定义闭环。

## 闭环

```text
授权资料 → 确定性证据 → 结构化澄清 → 人的裁决 → Contract → 批准 Context
```

这条链路是产品目标，不是 N1 已完成的能力声明。N1 只把入口、状态边界和后续可验证的界面放在一起。

## 当前状态

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

## N1 快速开始

需要 Python `3.14.7`、UV 和 Node.js/npm。依赖版本已锁定；安装前后的依赖审查记录属于本次 N1 交付证据。

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

然后打开 <http://127.0.0.1:8787>。服务只绑定本机回环地址；默认运行目录 `.contextox-agent/` 仅用于本地启动准备，N1 不写入客户资料。

也可以导出当前 API 合同：

```bash
uv run contextox openapi --output /tmp/contextox-openapi.json
```

`doctor` 显示 `partial` 是预期的：它会明确列出尚未实现的 Workspace、来源和 provider 能力，而不是把它们伪装成 ready。

## Workbench 里的四个区域

- **Sources**：未来的授权资料、来源版本和证据入口。N1 尚未读取或枚举文件。
- **Mission**：未来的任务阶段、领域工具收据和结构化事件。N1 只有公开的 SSE 连接骨架。
- **Clarifications**：未来把未知变成可回答、可路由的问题。N1 尚未生成或提交澄清表单。
- **Contract**：未来保存有来源、版本、哈希和审批边界的业务定义。N1 尚未提供审批或持久化。

N1 不提供 provider SDK、真实模型调用、任意文件/SQL/shell 执行、通用 `read/write/edit` 工具、客户数据导入、远程同步、SSO、多用户协作、云端部署或发布能力。它也不把聊天、模型推断或未批准的表单回答提升为公司事实。

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

客户资料、公司私有资料、运行数据库、凭证、原始 provider payload、敏感日志和私有评测数据不得进入 Git、普通日志或测试。只处理明确授权的本地材料；N1 当前不接收客户文件，也不调用 provider。具体边界以 [`AGENTS.md`](AGENTS.md) 和上述架构报告为准。

本项目采用 [MIT License](LICENSE)。
