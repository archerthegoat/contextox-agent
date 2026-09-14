# 数契 45 秒产品片 V3 QA

状态：`rejected_candidate`；人工验收 `FAIL`，公开发布 `NOT RUN`。

## V3 人工验收结论（2026-09-14）

用户观看 V3 后明确否决该候选。失败集中在三个可观察问题：画线与目标元素没有精确贴合；若干特效没有服务对应的产品动作，造成风格和内容脱节；音乐虽然按固定节拍编排，但缺少有吸引力的起伏、停顿与记忆点。自动渲染、媒体规格和静帧检查仍然保留为工程证据，但不能抵消本次人工观感 `FAIL`。

V3 不进入公开分发。后续 V4 使用独立 Composition 和候选文件：先完成声音结构与 10–12 秒钩子样片，再扩展完整 45 秒；界面标注必须由真实页面坐标生成，视觉效果必须逐项绑定产品动作。V3 源码与工件仅作为 Git 历史中的失败基线保留。

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

- 分支：`codex/gawx-product-film-v3`
- 基线提交：`c5527b9341e6b943dbdf0d06316e7fadb1508df7`
- 方向检查点：`d66d337`（内容与风格）／`29bf200`（Shotcraft 动效实现）
- Composition：`ContextOxProductFilm`
- Remotion／`@remotion/cli`：`4.0.506`／`4.0.506`
- React／React DOM：`19.2.4`／`19.2.4`
- TypeScript：`5.9.3`
- 输出：`output/video/contextox-demo-1.0.0-product-film.mp4`
- SHA-256：`976622b0b20c9ed8dfd0797bd9e4d24086ebdad38e42994a83357bd5266b0eb8`
- 审片板：`output/video/contextox-demo-1.0.0-product-film-review-board.jpg`
- 审片板 SHA-256：`aa8f685361aab2b8b5ca642498bfccad57e1faa0ad97a5ca24c14dca3387ae6f`
- 原创音轨：`video/public/audio/contextox-v3-jazz-mix.wav`
- 音轨 SHA-256：`8f017ea63b03ccaa97340203f90d6e6a7556e5e11fdc31bd92aa0337c90983a1`
- Workbench 纹理：本地 `127.0.0.1`、公开合成 Demo、无 Provider；开始／目标状态为本轮实机截图，候选状态复用仓库已有公开 Demo 图。

## 自动证据

- `npm run typecheck`：PASS。
- Brief、脚本规范、风格规范、分镜与资产清单 YAML 解析：PASS。
- 100 BPM 节拍网格：PASS；每拍 18 帧，9 个场景首尾连续且全部切点落在节拍上，总计 1350 帧。
- 音轨生成：PASS；项目内确定性合成，无第三方采样、下载素材或人声；鼓、刷镲、短鼓花、闷音贝斯和界面点击按画面动作编排。
- 音轨生成可复现：PASS；重复生成 SHA-256 均为 `8f017ea63b03ccaa97340203f90d6e6a7556e5e11fdc31bd92aa0337c90983a1`。
- 源音轨回读：45.000 秒、48 kHz、立体声 PCM。
- 成片响度回读：`-16.07 LUFS`、`-1.19 dBTP`、`2.60 LU` LRA，符合约 `-16 LUFS`、True Peak 不高于 `-1 dBTP` 的目标。
- 动效实现：三次数字冲击、冻结批注、工具定位对照、真实 Workbench 鼠标操作、问题展开、确认冲击、候选定义横移、本轮变化点击和界面到 Logo 变形均已进入最终渲染。
- 关键动作静帧：PASS；已核对选择资料、发送目标、确认回答、查看本轮变化四类鼠标落点。
- 0.8／4.2／8.4／11.1／16.8／25.2／29.4／35.4／39.6／44.2 秒审片板：PASS；10 张画面均从最终 MP4 重新抽取，时间标记位于画面外；切点无空白，三个问题、连续 App 操作、候选定义与结尾可辨认。
- 完整 CLI 渲染：PASS；1350／1350 帧。
- 媒体回读：H.264、1920 × 1080、30 fps、1350 帧、视频流 45.000 秒；AAC、48 kHz、立体声。MP4 容器为 45.056 秒，额外 0.056 秒来自 AAC 末包编码填充，画面帧数与时长未增加。
- 资产：仅仓库自有 Logo、公开合成文字数据与项目内生成音频；无真人、客户资料、私有 URL 或远程媒体。
- 展示与分发检查：PASS；公开合成 CSV 重新计算为 689／440／390，下载副本、海报、链接、manifest 字节数与 SHA-256 一致。
- 确定性与敏感信息扫描：PASS；视频源码未使用 `Math.random`、`Date.now` 或运行时日期，已改文字文件未发现常见密钥或口令模式。
- 独立产品审阅：PASS；首轮发现叙事字幕和必要披露低于小窗可读性门槛，整改后复测主字幕为 60–64px、辅助披露为 32–35px；开场最终定格 67 帧、规则页低运动定格 48 帧、确认后稳定状态 48 帧，未留下材料性 MUST-FIX。
- 独立审阅剩余建议：16.8／25.2 秒的顶部标签会覆盖少量 Workbench 顶栏，但不遮挡核心任务内容；保留为人工观感验收项，不提升为自动失败。
- 公开发布：`NOT RUN`；远端 `main` 与 GitHub Pages 仍是已发布的 V2.1，不把本地 V3 候选表述为已上线。

## 人工验收清单

1. 前 10 秒是否能理解“同一份数据为什么会有三个答案”。
2. 前 15 秒内是否已经看到选资料、输入目标和鼠标操作。
3. 主体是否明显是一轮 App 工作过程，而不是把演示页做成转场。
4. 重绘界面是否忠于当前 Workbench，没有虚构按钮、自动操作或最终能力。
5. 689／440／390 是否只作为规则差异案例理解，而非产品输出。
6. 音乐和界面音效是否增强节奏但不喧宾夺主。
7. 公开合成、候选状态和产品边界是否足够清楚。
8. 全片是否没有裁切、闪烁、空白帧、文字溢出或鼠标错位。
9. “Codex／通用 Agent 偏任务执行，数契聚焦业务定义”是否表达为产品分工，而不是排他能力或贬低竞品。
10. 快切、手写批注、景别变化与鼓点驱动是否带来手作编辑感，同时仍给关键产品信息留下阅读停顿。

## 验收记录

```text
Build ID: MP4 SHA-256 976622b0b20c9ed8dfd0797bd9e4d24086ebdad38e42994a83357bd5266b0eb8
Source branch: codex/gawx-product-film-v3
Composition: ContextOxProductFilm
Viewport: 1920x1080 / 100%
Test data: 公开合成订单、客户与说明资料

Specification: NOT REASSESSED
Visual and timing: FAIL
Captions: NOT REASSESSED
Audio: FAIL
Aspects/readability: NOT REASSESSED
Product fidelity: FAIL — annotation targets were not precise
Full render: PASS

Human acceptance: FAIL
Notes: V3 rejected on 2026-09-14 for imprecise line animation, effects that did not fit the demonstrated actions, and an unengaging music rhythm.
```
