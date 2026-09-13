# 数契 45 秒产品片 QA

状态：`candidate_render`；人工验收 `PENDING`，公开发布 `NOT RUN`。

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
Build ID: PENDING_COMMIT
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
