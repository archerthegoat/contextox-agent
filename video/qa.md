# 数契 45 秒产品片 QA

状态：`candidate_render`；人工验收 `PENDING`，公开发布 `NOT RUN`。

## README 14 秒产品动图 V2

- 分支：`codex/readme-vision-demo`
- Composition：`ContextOxReadmeDemo`
- 时间轴：420 帧、30 fps、14.000 秒；无旁白、音乐或界面音效。
- 母版回读：H.264、1280 × 720、30 fps、420 帧、14.000 秒；母版仅用于本地转码，不进入当前分发包。
- README 主图：`docs/assets/demo-flow.webp`，1280 × 720、163 个动画帧块、14.000 秒、循环播放、3,328,856 字节；SHA-256 `b882bc4e22f5349bc7584ae039ca585e3c71874667ee38618c4736f5a206f523`。
- 兼容 GIF：`docs/assets/demo-flow.gif`，960 × 540、142 帧、14.200 秒、循环播放、5,857,294 字节；SHA-256 `bb5f69dd2555ffb092d2b5ed80c3ad187d9578b73112fc26ce7f16e6e5edcb0b`。
- 开场：第一秒内直接进入资料选择，随后输入并发送目标；不重复 689／440／390 问题页。
- 连续动作：选资料并发送目标 → Agent 提出三类澄清 → 人核对并确认规则 → 候选定义出现 → 展开本轮变化。
- 镜头：按当前动作在资料范围、问题卡、回答确认和候选成果之间平滑移动；底部只保留一个当前动作说明。
- 产品边界：重用 45 秒产品片中与当前 Workbench 一一对应的组件，不新增按钮、自动批准或数据库能力；持续标注界面演示、公开合成资料和候选结果。
- 关键帧回读：已检查 1.5／4.8／8.1／10.9／12.8 秒，以及最终 GIF 的变化面板；文字、鼠标、状态、披露和压缩颜色可辨认。
- 自动验证：TypeScript 类型检查 PASS；完整 420 帧渲染 PASS；WebP／GIF 尺寸、时长、循环、字节数和哈希回读 PASS。
- 人工观感验收：`PENDING`；本地渲染和自动检查不能替代用户对节奏、可读性和产品忠实度的最终确认。

## 构建标识

- 分支：`codex/contextox-brand-contracts`
- Composition：`ContextOxProductFilm`
- Remotion／`@remotion/cli`：`4.0.506`／`4.0.506`
- React／React DOM：`19.2.4`／`19.2.4`
- TypeScript：`5.9.3`
- 输出：`output/video/contextox-demo-1.0.0-product-film.mp4`
- SHA-256：`4de5d8e04a49a0ec58cfaac791db4007f100a2ee0926e762b54707f4b320bcdd`
- 审片板：`output/video/contextox-demo-1.0.0-product-film-review-board.jpg`
- 审片板 SHA-256：`0677d1b7dc83bf483fbd4258f3aa7c6f3b78a7fffc9f3ef166dda873b9bdb809`

## 自动证据

- `npm run typecheck`：PASS。
- 音轨生成：PASS；项目内确定性合成，无第三方采样、下载素材或人声。
- 音轨生成可复现：PASS；连续两次生成的 SHA-256 均为 `941a0d01750bbc698ede4bf026a917ddb97c17ec522c28f604141d82bbd353cd`。
- 源音轨回读：45.000 秒、48 kHz、立体声 PCM。
- 成片响度回读：`-16.09 LUFS`、`-0.97 dBTP`，符合约 `-16 LUFS`、True Peak 不高于 `-1 dBTP` 的目标容差。
- 关键动作静帧：PASS；已核对选择资料、发送目标、确认回答、查看本轮变化四个鼠标落点。
- 0／4／9／15／22／29／36／41／44 秒审片板：PASS；切点无空白，开场、连续 App 操作、候选定义与结尾均可辨认。
- 完整 CLI 渲染：PASS；1350／1350 帧。
- 媒体回读：H.264、1920 × 1080、30 fps、1350 帧、视频流 45.000 秒；AAC、48 kHz、立体声。MP4 容器为 45.056 秒，额外 0.056 秒来自 AAC 末包编码填充，画面帧数与时长未增加。
- 资产：仅仓库自有 Logo、公开合成文字数据与项目内生成音频；无真人、客户资料、私有 URL 或远程媒体。
- 独立产品审阅：NOT RUN；本任务按单一写入者执行，人工验收不由自动检查替代。

## 人工验收清单

1. 前 10 秒是否能理解“同一份数据为什么会有三个答案”。
2. 前 15 秒内是否已经看到选资料、输入目标和鼠标操作。
3. 主体是否明显是一轮 App 工作过程，而不是把演示页做成转场。
4. 重绘界面是否忠于当前 Workbench，没有虚构按钮、自动操作或最终能力。
5. 689／440／390 是否只作为规则差异案例理解，而非产品输出。
6. 音乐和界面音效是否增强节奏但不喧宾夺主。
7. 公开合成、候选状态和产品边界是否足够清楚。
8. 全片是否没有裁切、闪烁、空白帧、文字溢出或鼠标错位。

## 验收记录

```text
Build ID: 16f2db7
Composition: ContextOxProductFilm
Viewport: 1920x1080 / 100%
Test data: 公开合成订单、客户与说明资料

Specification: PENDING HUMAN REVIEW
Visual and timing: PENDING HUMAN REVIEW
Captions: PENDING HUMAN REVIEW
Audio: PENDING HUMAN REVIEW
Aspects/readability: PENDING HUMAN REVIEW
Product fidelity: PENDING HUMAN REVIEW
Full render: PASS

Human acceptance: PENDING
Notes:
```
