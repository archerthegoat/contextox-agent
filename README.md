<p align="center"><img src="web/src/assets/contextox-mark.png" width="76" alt="数契 Logo"></p>
<h1 align="center">数契 ContextOx</h1>
<p align="center"><strong>让每一张表，都说清自己代表什么。</strong></p>
<p align="center">把表格、说明和人的判断，整理成有证据、可确认、能持续演进的业务定义。</p>
<p align="center"><code>Demo 1.0.0</code> · 本地优先 · Agent 对话驱动 · MIT</p>
<p align="center">
  <a href="https://archerthegoat.github.io/contextox-agent/">观看动态产品介绍</a> ·
  <a href="https://archerthegoat.github.io/contextox-agent/downloads/contextox-demo-1.0.0-product-film.mp4">看 45 秒产品演示</a> ·
  <a href="https://github.com/archerthegoat/contextox-agent/releases/tag/v1.0.0">获取 Demo 1.0.0</a>
</p>

**同一份订单数据，为什么会得到 689、440 和 390 三个答案？**

不是大家不会算，而是还没有先说清：哪些订单算进去、退款怎么处理、按哪个时间。业务团队真正卡住的，常常就是这些会改变结果、却散落在表格、说明和不同人脑中的规则。

数契是一个本地优先的业务定义 Agent。你选中资料，像和同事一样说出目标；Agent 负责读字段、找关系、追问缺口，再把业务回答整理成卡片交给你确认。对话只是入口，最终留下的是可回读的字段、关系、规则、出处和未知事项。

![数契：从选择资料到核对候选成果](docs/assets/demo-flow.webp)

*这不是概念动画：鼠标移动、资料选择和每次点击，都来自同一条公开合成流程的实际浏览器操作。合成结果不代表真实业务结论或人的批准。*

## 现在就试这一句

打开 Workbench，选中订单表和退款表，然后直接发送：

> 我想按地区统计订单金额，并把退款和缺失金额的处理规则写清楚。

接下来只需要做三件事：

1. 点击 **了解本轮资料**，先看 Agent 对两张表的理解。
2. 用自然语言回答退款和缺失金额怎样处理，核对整理好的回答卡片。
3. 点击 **确认并继续**，在中间查看更新后的候选字段、资料关系和仍未知的事项。

没有 Mission、Provider 或 Run 的前置学习。资料与目标足够明确时，发送消息就会推进；遇到会改变结果的口径，Agent 会停下来请你决定。

没有 Key 也可以点击 **体验公开示例**，先看一份已经完成的候选成果；载入示例后还能亲自走完整流程。

<details>
<summary>查看完成后的静态大图</summary>

![回答确认后的候选字段与关系](docs/assets/demo-real.jpg)

</details>

## 数契和 Codex、数据平台、问数工具有什么区别

它们解决的是不同阶段的问题，数契不替代这些工具。数契聚焦的是：**当一句业务需求还有多种理解时，先把哪些算进去、怎么处理、按什么时间说清楚。**

| 现在要解决的问题 | 更适合的工具 | 数契与它怎样配合 |
| --- | --- | --- |
| 完成代码、研究或制作任务 | 通用 Agent，如 Codex | 业务定义明确以后，继续交给它执行 |
| 建设目录、血缘、权限和企业上下文 | Atlan、DataHub 等数据平台 | 未来可以对接，不重复建设企业数据底座 |
| 在已有语义模型上直接问数 | BI 与问数工具 | 数契处理规则还没有确定的前一步 |
| 一句业务需求存在多种理解 | **数契当前聚焦** | 找出缺口，请人确认，留下可复核的候选定义 |

通用 Agent 擅长把已经明确的目标做出来；数契先处理“目标看起来明确，里面的业务规则其实还没有定”的时刻。Agent 负责阅读、整理和追问，业务事实仍由人决定。留下的不只是一段回复，而是资料范围、人的回答、规则变化、出处和仍未知的事项。

这是数契当前的产品选择，不是已经被真实业务证明的绝对优势。它仍需要通过真实案例，以及和配置合理的通用 Agent 做同题比较来验证。完整说明和公开定位参考见[动态产品介绍](https://archerthegoat.github.io/contextox-agent/#10)。

## 快速启动

**Demo 1.0.0 已经发布。** 当前 Release 提供源码和公开合成示例，可以按下面的命令启动：

~~~sh
git clone https://github.com/archerthegoat/contextox-agent.git
cd contextox-agent
uv sync --locked
npm --prefix web ci --ignore-scripts
npm --prefix web run build
uv run --locked contextox start --agent-profile demo-fast --open-browser
~~~

开发环境需要 Python `3.14.7`、UV 和 Node.js `22.19.0` 以上版本。服务默认打开 <http://127.0.0.1:8787>；保持终端运行，按 `Ctrl+C` 停止。

当前 [Demo 1.0.0 Release](https://github.com/archerthegoat/contextox-agent/releases/tag/v1.0.0) 还没有预构建安装包；首次体验请按上面的源码命令启动。后续安装包计划支持 macOS Apple 芯片，并自带 Python、依赖和网页。

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

## 做过数据工作？一起来把它做成

数契现在最需要的不只是更多代码，还需要那些真实发生在指标会、数据仓库、BI、数据治理和交付现场里的问题：**同一个词为什么有三种算法？这列到底该听谁的？改完之后谁会受影响？**

如果你做过数据分析、数据产品、数据工程、数据治理、BI、FDE 或企业数据项目，或者正想把 Agent 用进真实的数据工作，欢迎加入：

- 带一个可以脱敏或改写成合成数据的真实问题，和我们一起打磨产品。
- 贡献业务定义方法、演示案例、交互建议、代码、测试或文档。
- 在 [GitHub Issues](https://github.com/archerthegoat/contextox-agent/issues) 留下问题，或直接联系维护者 [@archerthegoat](https://github.com/archerthegoat)。中文和英文都可以。

提交案例时请只使用合成或已脱敏材料，不要上传 API Key、客户资料、私人数据或原始 Provider 内容。

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

- [Demo 1.0.0 Release 说明](docs/releases/v1.0.0.md)
- [开发路径图](开发路径图.md)
- [架构与迁移报告](docs/架构与迁移报告.md)
- [Agent 主导 Workbench 本地验收](docs/Agent主导Workbench本地验收.md)

</details>

## License

[MIT](LICENSE) · 公开示例全部为人工合成材料。
