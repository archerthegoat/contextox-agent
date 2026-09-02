# ContextOx Workbench design QA

## Comparison target

- Source visual truth: `/private/tmp/contextox-design-option1-n1-truth.png`
- Source state: light desktop Sources empty state from the approved option-1 reference.
- Implementation URL: `http://127.0.0.1:8877/`
- Implementation state: current branch build, ready API state, connected event boundary, Sources selected, truthful empty state, Agent idle state.
- Implementation screenshot: `/private/tmp/contextox-desktop-1512x860.png`
- Mobile screenshot: `/private/tmp/contextox-mobile-390x844.png`
- Comparison input: `/private/tmp/contextox-design-qa-comparison.png`
- Focused implementation evidence: `/private/tmp/contextox-desktop-main-focus.png`, `/private/tmp/contextox-desktop-agent-focus.png`, `/private/tmp/contextox-desktop-focus.png`

## Viewport and density normalization

- Source pixels: 1440 x 1024.
- Implementation CSS viewport: 1512 x 860.
- Implementation screenshot pixels: 1512 x 860.
- Device pixel ratio: 1.
- Zoom: 100%.
- The source and implementation have different aspect ratios by explicit approval: the source is a 1440 x 1024 design reference, while the real target viewport is 1512 x 860. This is an intentional desktop proportion adaptation, not an unreported crop or density mismatch.
- The comparison input is one 1512 x 560 RGBA PNG. It places both artifacts on the same light canvas, preserves each aspect ratio, fits each into an equal 724 x 528 panel, and separates the panels with a divider. No browser chrome is included.

## Full-view comparison evidence

The combined comparison input was opened and judged after opening the source and capturing the rendered implementation. The implementation preserves the source's three-column hierarchy, cool white and silver-gray surfaces, carbon typography, mineral-blue active rail and mark direction, restrained dividers, and generous empty-state whitespace. The desktop screenshot is complete at 1512 x 860 with no clipping or horizontal overflow. The approved truthful-N1 constraints intentionally remove the source mock's unsupported profile avatar, status dot, settings affordance, enabled-looking empty-state pill, and agent send affordance.

## Focused region comparison evidence

- Main focus `/private/tmp/contextox-desktop-main-focus.png`: breadcrumb, title, supporting copy, divider, document raster, empty title, and next-stage note are readable and aligned. The image is a real transparent raster asset, not CSS art or inline SVG.
- Agent focus `/private/tmp/contextox-desktop-agent-focus.png`: Agent heading, idle raster, truthful availability copy, persistent divider, and disabled composer are readable. There are no fake messages or provider/model claims.
- Focused keyboard evidence `/private/tmp/contextox-desktop-focus.png`: keyboard focus is visibly outlined on the Sources navigation control.

## Required fidelity surfaces

- Fonts and typography: system UI fallback is legible at 14 to 16px body sizes, with a heavier carbon display hierarchy for the Chinese title and area labels. The title, supporting copy, empty-state copy, and Agent copy wrap without clipping at both tested viewports. No decorative uppercase eyebrows or version labels were introduced.
- Spacing and layout rhythm: desktop geometry is topbar 1512 x 72, sidebar 248 x 788, main 944 x 788, and Agent 320 x 788. The empty state is 817 x 470 inside the main column with dividers before emphasis. Mobile reflows to one column: topbar 390 x 64, sidebar 390 x 346, main 390 x 586, and Agent starts at y 995 below the main content. There is no horizontal overflow.
- Colors and visual tokens: the rendered page uses the approved light cool-white/silver-gray/carbon palette with one mineral-blue active accent. No warm paper/brick-orange/sage palette or AI-purple gradient is visible. Focus and disabled states remain distinct and readable.
- Image quality and asset fidelity: `contextox-mark.png` is a 256 x 256 RGBA PNG, `source-empty.png` is 512 x 512 RGBA, and `agent-idle.png` is 384 x 384 RGBA. All three loaded with their full native dimensions in Chrome at DPR 1, have transparent backgrounds, and remain sharp at their display sizes. The Sources illustration is rendered only for Sources; Mission, Clarifications, and Contracts are text-only.
- Copy and content: Sources shows `添加第一份授权资料`, `数契只会处理你明确选择的本地资料。`, `暂无资料`, and `来源导入将在下一阶段开放。`. Agent shows `创建 Mission 后，数契会在这里持续协作。`, `即将开放`, and disabled placeholder `等待 Mission`. Navigation presentation labels remain concise while area ordering is driven by the typed API snapshot. No SSE, IP address, readiness, provider, DeepSeek, API key, N1, checkpoint, fake evidence, files, conflicts, progress, metrics, or contracts are visible.

## Interaction, accessibility, and runtime evidence

- Clicked Sources, Missions, Clarifications, and Contracts in one reusable Chrome tab. Each click changed the breadcrumb/title/empty copy and active `aria-current="page"`; the Agent panel remained present. The Sources document illustration was present only on Sources.
- Keyboard traversal reached the four area buttons in order with `:focus-visible` and a solid outline. The disabled Agent input is intentionally skipped by Tab, has `disabled=true`, reports `isEnabled=false`, and retains placeholder `等待 Mission`.
- Desktop viewport check: `innerWidth=1512`, `innerHeight=860`, `devicePixelRatio=1`, `scrollWidth=1512`, `scrollHeight=860`.
- Mobile viewport check: `innerWidth=390`, `innerHeight=844`, `devicePixelRatio=1`, `scrollWidth=390`, full-page `scrollHeight=1381`. Agent is below the main empty state and does not cover core content.
- Chrome console: no warning or error entries were returned for desktop or mobile reloads.
- Runtime resource checks: root HTML, `/api/workbench`, and the three rendered PNG assets returned HTTP 200. Chrome reported all three images complete with native dimensions; API state was `ready` and connection state was `connected`.

## Findings

No actionable P0, P1, or P2 findings remain. Differences from the source mock are classified as approved truthful-N1 behavior or intentional viewport adaptation: the real viewport is wider and shorter, unsupported avatar/status/settings controls are removed, navigation icons are omitted because no icon dependency is approved, and unsupported upload/send affordances are disabled or absent.

## Open questions

- Human visual acceptance for the exact commit/build remains pending. Real model execution is not part of this checkpoint and remains NOT RUN.

## Comparison history

1. Initial current-build comparison: source and desktop Sources render were opened together in `/private/tmp/contextox-design-qa-comparison.png`. No P0/P1/P2 mismatch was identified. The source-to-1512 x 860 ratio difference, removed unsupported controls, and truthful empty-state affordance changes were classified as intentional constraints.
2. Responsive and interaction pass: captured `/private/tmp/contextox-mobile-390x844.png` and its full-page evidence. No P0/P1/P2 issue was found: the Agent panel moves below the main content, all four areas remain reachable, and no horizontal overflow occurs.
3. Focused evidence pass: captured the main, Agent, and keyboard-focus regions listed above. No P0/P1/P2 issue was found. No code correction was required during this QA loop.

## Implementation checklist

- [x] Source opened and visually inspected.
- [x] Current branch implementation opened in Chrome.
- [x] Same-state full-view comparison input created and inspected.
- [x] Desktop 1512 x 860 and mobile 390 x 844 screenshots captured.
- [x] Focused main, Agent, and keyboard-focus evidence captured.
- [x] Navigation, responsive reflow, disabled composer, asset loading, and console checks completed.
- [x] Five required fidelity surfaces reviewed.
- [x] No actionable P0/P1/P2 findings remain.

## Follow-up polish

No P3 follow-up is required for this checkpoint. Human acceptance is still PENDING.

final result: passed
