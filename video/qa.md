# 数契无声产品短片 QA

状态：`candidate_render`；自动验证通过，人工验收 `PENDING`，公开发布 `NOT RUN`。

## 构建标识

- 分支：`codex/contextox-brand-contracts`
- Composition：`ContextOxSilentLaunch`
- Remotion Skill／包版本：`4.0.506`
- Remotion／`@remotion/cli`：`4.0.506`／`4.0.506`
- React／React DOM：`19.2.4`／`19.2.4`
- TypeScript：`5.9.3`
- 输出：`output/video/contextox-demo-1.0.0-silent-launch.mp4`
- SHA-256：`8980f2f8d1fdb73c23000606e46e34ad09de190793ea08c1e040f9d252660694`

## 自动证据

- `npm ci --ignore-scripts`：PASS；249 个包安装，0 个已知漏洞。
- `npm run typecheck`：PASS。
- 关键帧 60／255／650／930／1290：PASS；覆盖开场、最大信息密度、产品画面、定义变化和结尾。
- 完整 CLI 渲染：PASS；1350／1350 帧。
- 媒体回读：H.264，1920 × 1080，30 fps，45.000 秒，单一视频流，无音轨，6,487,264 字节。
- 资产：仅仓库自有 Logo 和公开合成 Demo 截图；无真人、客户资料、私有 URL、远程媒体、音乐或旁白。

## 人工验收清单

1. 从头到尾播放，确认 7 个场景顺序与停留时间适合分享会开场。
2. 在 00:00、00:08、00:21、00:31、00:38、00:43 暂停，确认主标题、三项歧义、产品截图、前后变化、边界和 CTA 可读。
3. 确认产品截图始终作为公开合成实例理解，没有被误认为客户画面。
4. 确认所有内容位于 1920 × 1080 安全区，没有裁切、闪烁、空白帧或文字溢出。
5. 确认播放时无声音且文件无音轨；无需调整系统音量。
6. 对照 `script.md`、`style-spec.yaml` 与 `assets.yaml`，确认文案、顺序、品牌色和资产未漂移。
7. 确认结尾 GitHub 地址完整可读，且没有暗示已经完成正式 Contract 或公开部署。

## 验收记录

```text
Build ID: beceb4d
Composition: ContextOxSilentLaunch
Viewport: 1920x1080 / 100%
Test data: 公开合成订单与退款案例

Specification: PENDING
Visual and timing: PENDING
Captions and audio: PENDING（无字幕、无音轨）
Keyboard/focus/scroll: NOT APPLICABLE（成片）
Aspects/readability: PENDING
States: NOT APPLICABLE（线性短片）
Console/network: PENDING
Full render: PASS

Human acceptance: PENDING
Notes:
```
