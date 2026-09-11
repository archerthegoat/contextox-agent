<p align="center"><img src="web/src/assets/contextox-mark.png" width="76" alt="数契 Logo"></p>
<h1 align="center">数契 ContextOx</h1>
<p align="center"><strong>把表格和说明里的业务口径，聊清楚、写清楚、留出处。</strong></p>
<p align="center"><code>Demo 1.0.0</code> · 本地优先 · Agent 对话驱动 · MIT</p>

数契是一个本地优先的业务定义 Agent。你选择本轮要使用的表格和说明，直接说出目标；Agent 会理解字段与关系、找到依据，并把真正会改变结果的问题交给你确认。

它适合这样的工作：指标口径散落在列名、说明文件和人的经验里，大家都“知道大概怎么算”，却很难说清版本、来源和仍未确定的地方。

![数契：从选择资料到核对候选成果](docs/assets/demo-flow.gif)

*动图来自正式 Workbench 的公开合成页面。它展示产品流程，不代表真实业务结论或人的批准。*

## 用这个例子开始

假设你有订单表和退款表，想按地区统计净订单金额。打开工作台后：

1. 点击 **体验公开示例**，无需 Key 先看完整候选；也可以载入示例亲自运行。
2. 在右侧选择本轮资料，点击 **了解本轮资料**。
3. 直接发送：

   > 我想按地区统计订单金额，并把退款和缺失金额的处理规则写清楚。

4. Agent 会指出退款、空值或时间范围等会改变结果的问题。你可以继续追问，也可以自然语言回答。
5. 回答会先整理成卡片。核对后点击 **确认并继续**，规则才会被采用。
6. 在中间查看候选字段、资料关系、本轮变化和仍未知的事项。

你不需要先创建 Mission、Provider 或 Run。资料与目标足够明确时，发送消息就会推进；缺少关键口径时，Agent 会停下来问一个具体问题。

<details>
<summary>查看完成后的静态大图</summary>

![回答确认后的候选字段与关系](docs/assets/demo-real.jpg)

</details>

## 为什么不只是把表格丢给普通 Agent

通用 Agent 也能解释表格。数契进一步把一次对话组织成可回读、可确认、可继续完善的业务定义过程。

| 直接在通用 Agent 中上传表格 | 数契 |
| --- | --- |
| 本轮究竟用了哪些资料，常依赖聊天上下文 | 明确绑定本轮资料及其精确版本 |
| 观察、推断和业务规则可能写在同一段回答里 | 分开记录资料证据、候选结论、人的回答和未知事项 |
| 退款、空值等模糊点可能被临时假设 | 把会改变结果的问题交给人确认 |
| 一句“好的”可能继续改变后续结论 | 自然语言先整理成卡片，明确确认后才采用 |
| 修改前后的差异需要人工对照聊天 | Draft、回答版本和本轮变化可以回读 |
| 失败后往往重新提问 | 保留运行状态、交接结果和恢复入口 |
| 主要得到一次性答案 | 得到带字段、关系、口径、来源与未知项的候选定义 |

数契不会把 Agent 输出自动当成业务事实。它让模型负责整理和追问，让人保留关键结论的决定权。

## 快速启动

1.0.0 目前仍是发布前 Demo 候选。现在可以从源码启动：

~~~sh
git clone https://github.com/archerthegoat/contextox-agent.git
cd contextox-agent
uv sync --locked
npm --prefix web ci --ignore-scripts
npm --prefix web run build
uv run --locked contextox start --agent-profile demo-fast --open-browser
~~~

开发环境需要 Python `3.14.7`、UV 和 Node.js `22.19.0` 以上版本。服务默认打开 <http://127.0.0.1:8787>；保持终端运行，按 `Ctrl+C` 停止。

首个安装包计划支持 macOS Apple 芯片，并自带 Python、依赖和网页。`v1.0.0` Release 发布前，固定版本安装命令暂不可用。

## 连接模型

公开示例预览不需要 Key。亲自运行分析时，在左下角打开 **模型设置**，填入自己的 DeepSeek API Key：

- 默认保存到 **macOS Keychain**，不会写入工作区数据库或浏览器存储。
- 保存设置不会调用模型；只有发送消息才可能产生费用。
- 如果一条消息正在等待连接，可以选择 **仅保存设置** 或 **保存并发送这条消息**。
- 页面不会回显已经保存的 Key。

<details>
<summary>在启动环境或本地配置文件中提供 Key</summary>

读取顺序为：`DEEPSEEK_API_KEY` 环境变量 → `--env-file` 指定文件 → macOS Keychain。

~~~dotenv
DEEPSEEK_API_KEY=your-deepseek-api-key
~~~

~~~sh
chmod 600 contextox.env
uv run --locked contextox start --agent-profile demo-fast --env-file ./contextox.env --open-browser
~~~

程序不会自动寻找 `.env`，网页也不会创建或改写配置文件。

</details>

## 数据留在哪里

工作区、资料、对话、草案、回答和执行记录保存在本机：

~~~text
~/Library/Application Support/ContextOx/
~~~

导入和预览在本机完成。发送消息后，只有本轮明确选择的资料范围和必要上下文会交给 DeepSeek。表格先在本地生成有界画像，模型只收到受预算限制的样例、正文和引用。

服务只绑定 `127.0.0.1`。当前不提供远程访问、多用户协作、任意文件枚举、SQL 或 Shell 执行。请只导入和发送你有权使用的资料。

## 1.0.0 能做到哪里

**已经包含：** 连续对话、精确资料范围、发送即推进、澄清卡片、确认续接、字段与关系候选、引用、导出和失败恢复。

**暂未包含：** 正式 Contract 发布、跨任务知识复用、云同步、多用户协作、其他模型供应商，以及 Windows / Linux 运行包。

当前成果始终是可核对的候选草案。工程测试、合成 Provider、真实 Provider、浏览器检查和人工验收会分别记录，不会互相替代。

## 欢迎 Contribution

如果你也在处理“表格看得懂，但业务口径说不清”的问题，欢迎一起完善数契：

- 在 [GitHub Issues](https://github.com/archerthegoat/contextox-agent/issues) 反馈卡住的步骤、可复现问题或产品建议。
- 提交只含合成数据的演示案例，帮助覆盖更多业务口径和失败场景。
- 改进第一次使用说明、交互提示、可访问性、测试或文档。
- 通过 GitHub 联系维护者 [@archerthegoat](https://github.com/archerthegoat)。中文和英文都可以。

提交问题时请附版本和脱敏后的错误信息，不要上传 API Key、客户资料、私人数据或原始 Provider 内容。

<details>
<summary>开发检查与项目文档</summary>

~~~sh
uv run --locked contextox doctor
uv run --locked python -m compileall -q src tests
uv run --locked python -m unittest discover -s tests
npm --prefix web run check:api
npm --prefix web run typecheck
npm --prefix web test
npm --prefix web run build
~~~

- [1.0.0 Release 草案](docs/releases/v1.0.0.md)
- [开发路径图](开发路径图.md)
- [架构与迁移报告](docs/架构与迁移报告.md)
- [Agent 主导 Workbench 本地验收](docs/Agent主导Workbench本地验收.md)

</details>

## License

[MIT](LICENSE) · 公开示例全部为人工合成材料。
