# ContextOx Agent

ContextOx Agent 是一个面向 FDE 和工程师的、受业务上下文约束的任务 Agent。它把授权的公司资料、任务证据和人的决策组织成可追溯、可恢复、可验收的单任务闭环。

> 当前状态：C0 文档基线。产品代码、依赖安装、自动验证、真实模型运行与人工产品验收均未开始或未运行；本仓库目前不可安装、不可执行。

## V0 目标

- 本地 TypeScript CLI，面向个人开发者和一线工程师。
- 支持多个相互隔离的公司 Workspace。
- 公司知识作为受治理、版本化、可追溯的 Workspace Context 持久存在。
- 每个 Mission 绑定不可变的 Context Snapshot，并可跨进程恢复。
- 每次 Agent Run 都是新的临时运行；不保存跨任务个人记忆或聊天人格。
- 提案必须带来源、未知项、例外与反例；人的批准绑定精确版本和哈希。
- 只有恢复后的 Run 产生规定的成功结果与证据，Mission 才能完成。

Agent Runtime 将使用发布版 `@earendil-works/pi-agent-core` 与 `@earendil-works/pi-ai`。ContextOx 不 fork、vendor 或复制 Pi 源码，也不依赖 Pi Coding Agent/TUI。实际依赖版本、完整性、许可证、安装脚本和 lockfile 需要在安装前另行审查。

## V0 不做

- Web UI、SSO、多用户协作、云同步或分布式工作流。
- 跨任务个人记忆、自动偏好学习或把聊天内容自动晋升为公司事实。
- 向量数据库、知识图谱、独立 Memory 服务或 MCP Server。
- 未经授权的外部写操作。
- 直接迁移旧 Pi monorepo、旧 runtime state 或旧 ContextOx 实现。

## 状态边界

| 层级 | 当前状态 |
| --- | --- |
| C0 文档基线 | 进行中；legacy/fixture 迁移仍按 publication manifest 逐文件保持 `pending` |
| 产品实现 | `NOT STARTED` |
| 自动验证 | `NOT RUN` |
| 真实模型验证 | `NOT RUN` |
| 人工产品验收 | `PENDING` |
| 用户价值证据 | `NOT VERIFIED` |

静态文件或测试通过不能替代真实模型验证、人工验收或用户价值证据。

## 数据与公开边界

客户资料、公司私有资料、运行数据库、凭证、原始 provider payload、敏感日志和私有评测数据不得进入 Git。旧仓库材料只有在 [`docs/migration-publication-manifest.json`](docs/migration-publication-manifest.json) 中完成逐文件权利、许可证、第三方内容、secret、PII、私有数据和 evaluator-only 审查，并由人明确标为 `allow` 后才能复制。

## 权威文档

- [`开发路径图.md`](开发路径图.md)：人工控制的产品方向与开发顺序。
- [`docs/架构与迁移报告.md`](docs/架构与迁移报告.md)：V0 架构、状态、失败语义、迁移边界与验收清单。
- [`docs/migration-publication-manifest.json`](docs/migration-publication-manifest.json)：旧材料公开迁移的逐文件决策记录。

## License

本项目采用 [MIT License](LICENSE)。
