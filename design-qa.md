# ContextOx Workbench v3 design QA

## Target and build identity

- Selected visual truth: `/private/tmp/contextox-dataworks-desktop-v3.png`.
- External visual reference: `/private/tmp/dataworks-copilot-reference.png`.
- Branch under test: `codex/contextox-workbench-redesign-v1`.
- Base commit before this checkpoint: `665d9294a9c9bdcac6c4f906c90ab26a30dba2bd`.
- This report was captured against the pre-commit worktree. Frontend fingerprint at capture:
  - `web/src/App.tsx`: `05269be39b7f76b7b5cb7150e0e55b47a8b5d6e957c9bbe20352daddf59553cb`.
  - `web/src/styles.css`: `73f6bf5c113f0e89c9db32daa2082b19e11c8c0a426ef9bdf50cc6478bffaf3a`.
  - `web/src/content.test.ts`: `435ac3af865a93002390e53eb5444772218fa6b2000278df73399c10bc81898b`.
- Human acceptance: `PENDING`; this report records automated and agent visual evidence only.

## Run commands and runtime

- Frontend typecheck: `cd web && npm run typecheck` — PASS.
- Frontend tests: `cd web && npm test` — PASS, 1 test file / 5 tests.
- Vite production build: `cd web && npm run build` — PASS, 18 modules transformed.
- API contract drift check: `cd web && npm run check:api` — PASS, generated OpenAPI types are up to date.
- Local runtime command: `uv run contextox start` — RUNNING on `http://127.0.0.1:8787`.
- Root URL under test: `http://127.0.0.1:8787/`.
- Runtime readback: `/` returned 200; `/api/health` returned `status: ready`, `product_status: partial`, and `bind_address: 127.0.0.1`; `/api/workbench` returned 200; `/api/events` emitted the public `connected` event before the bounded curl timeout; the built brand asset returned 200.
- The local service remains strictly loopback-bound. No deployment, release, provider call, or external write was used.

## Viewport, density, and state

- CSS viewport: `1512 x 860`.
- Implementation screenshot pixels: `1512 x 860`, captured from the approved Chrome extension at `devicePixelRatio: 1`, 100% zoom / explicit viewport override.
- Selected source pixels: `1512 x 860`.
- External reference pixels: `1369 x 832`.
- Combined visual comparison input: `/private/tmp/contextox-dataworks-v3-comparison.png`, `1512 x 540`, made by a local macOS native compositor. It places the selected source, external reference, and implementation in one equal-width comparison board while preserving each image's aspect ratio.
- Final implementation screenshot: `/private/tmp/contextox-dataworks-v3-implementation-final.png`.
- Focused implementation evidence:
  - `/private/tmp/contextox-dataworks-v3-implementation-main-focus.png` (center tabs, toolbar, graph).
  - `/private/tmp/contextox-dataworks-v3-implementation-agent-focus.png` (persistent Agent panel and composer).
- Initial state: `Mission` active in the only primary rail; Mission object tree visible; `客户粒度关系` object tab selected; eight synthetic relationship nodes visible; conflict node selected; Agent panel expanded; three synthetic messages visible; composer disabled with `演示模式，暂不可发送`.
- Reset state: reload `http://127.0.0.1:8787/`, click `Mission`, click `客户粒度关系`, then click `口径冲突` if another node is selected.

## Full-view comparison

The combined input was opened and compared after the final Chrome capture. The implementation preserves the selected v3 composition: a compact white DataWorks-like workbench with a 108px primary rail, 332px Mission object column, 750px center object workspace, and 322px persistent Agent panel. The implementation keeps the cool-white/silver divider system, carbon text, mineral-blue active state, denser typography, compact object tree, opened-object tabs, and left-to-right source-to-Contract graph.

The implementation corrects the reference crop issue by omitting fake account/profile information and keeps all visible content synthetic/demo. The primary module controls appear once in the far-left rail. The second column contains Mission objects and authorized-looking demo file rows rather than a second feature navigation. The center tabs represent opened objects, and the center canvas represents sources → entities → definition conflict → user confirmation → Contract draft.

No actionable P0, P1, or P2 visual mismatch remains after the focused pass. The missing decorative nav glyphs and text-only utility controls are intentional constraints: no icon library was present in the approved dependency set, and this checkpoint prohibits new dependencies, inline SVG, and CSS-art icons. Labels remain semantic and keyboard reachable.

## Focused comparison evidence

- Center focus: source rows align left and feed two entity rows; conflict, confirmation, and Contract draft nodes sit in the target right-side sequence. Node widths and positions were tuned against the source: source x≈455, entity x≈667, conflict x≈823, confirmation x≈940, Contract x≈1044 at the 1512px viewport.
- Agent focus: Agent messages are left aligned; the user message is right aligned with the exact speaker label `用户`; the Agent panel stays persistent and the disabled composer stays at the bottom. Message bubbles, compact type scale, borders, and blue user state are readable without clipping.
- No separate focused source crop was required because the full selected source and the focused implementation crops make the graph and Agent density directly comparable. The external DataWorks reference was used for the three-column workbench rhythm and Copilot panel treatment.

## Required fidelity surfaces

- Fonts and typography: system UI / PingFang-compatible fallback is used with compact 10–17px workbench labels, 13–14px messages, and clear 600–730 weights. Text wraps inside bubbles and graph nodes without overflow. No large marketing hierarchy or unsupported status copy is present.
- Spacing and layout rhythm: measured layout is rail `x=0,width=108`, objects `x=108,width=332`, center `x=440,width=750`, Agent `x=1190,width=322`, each beginning at `y=72` and filling the `788px` workspace. Document rows and Agent spacing were tuned in the second visual pass. The document has `scrollWidth=1512` and `scrollHeight=860`; no viewport overflow hides persistent controls.
- Colors and visual tokens: cool white surfaces, pale silver dividers, carbon text, mineral blue active/focus states, restrained conflict red, confirmation amber, and Contract violet follow the selected v3 art direction. Disabled composer remains visibly distinct and readable.
- Image quality and asset fidelity: the existing `contextox-mark.png` is reused as the only non-standard raster asset in the target. No new image asset, inline SVG, handcrafted SVG, emoji, placeholder illustration, or fake account artwork was introduced.
- Copy/content: visible primary labels are exactly `Sources`, `Mission`, `Clarifications`, and `Contract`. Agent messages use `Agent` and `用户`; the composer says `演示模式，暂不可发送`. No cloud, account, provider, model, or readiness claim is shown in the product UI.

## Interaction, keyboard, and runtime evidence

- Primary rail: four buttons were clicked; clicking `Sources` changed the active `aria-current="page"` and center heading to `授权来源`; clicking `Mission` restored the Mission canvas. No new route was created.
- Object tabs: clicking `统一-高潜客户定义` selected that `role="tab"` and showed its module surface; clicking `客户粒度关系` restored the graph.
- Relationship nodes: clicking `Contract 草案` set exactly one graph button to `aria-pressed="true"`; clicking `口径冲突` restored the initial selection. All eight nodes are semantic buttons with inspectable accessible names.
- Agent composer: the textarea is `disabled=true`, is not a send control, and exposes the exact placeholder `演示模式，暂不可发送`.
- Keyboard: after focusing `Sources` and pressing Tab, focus advanced to `Mission`; the next traversal after `Contract` reached the Mission object action `新增`. Visible focus outlines use the mineral-blue focus ring.
- Chrome console: no `error` or `warning` entries were returned after reload and interaction pass.
- Network/runtime: root HTML, workbench snapshot, connected SSE event, and brand asset all responded through the loopback service. No external network destination was used.

## Comparison history

1. Initial v3 implementation comparison: four-column shell, Mission object tree, opened object tabs, graph, and persistent Agent panel were present. The first pass identified right-side graph nodes as too far right and Agent/object rows as too compressed.
2. Layout correction: source/graph node geometry was tuned to the target sequence and object file rows were made denser but readable. Agent message padding and rhythm were adjusted. The implementation was rebuilt and recaptured at 1512×860.
3. Focused interaction pass: all primary rail buttons, both object tabs, graph node selection, composer disabled state, keyboard traversal, overflow, and Chrome console were checked. No P0/P1/P2 issue remained.
4. Combined comparison pass: `/private/tmp/contextox-dataworks-v3-comparison.png` was opened with the selected source, DataWorks reference, and final implementation together. Chrome blocked a `data:` URL comparison page under its URL policy; no bypass was attempted. The same comparison input was instead created and inspected locally with the macOS native compositor, and the browser-rendered implementation screenshot remained independently verified in Chrome.

## Open questions and limitations

- Human Workbench acceptance for the exact eventual commit/build remains `PENDING` and must be recorded by a human.
- This checkpoint is desktop-only; no mobile acceptance is claimed.
- The visible graph and conversation are synthetic demo content. Real source ingestion, provider calls, Mission execution, persistence, and user value evidence are not part of this checkpoint and remain `NOT RUN` / `NOT VERIFIED`.
- Decorative rail glyphs are omitted because the current approved dependency set has no icon library and adding one or recreating icons with SVG/CSS would expand the approved scope.
- The browser's URL policy blocked opening a `data:` URL comparison board; this is recorded as a limitation, not bypassed. Local native composition and `view_image` inspection supplied the required unified comparison artifact.

## Implementation checklist

- [x] Selected v3 source and DataWorks reference opened and inspected.
- [x] Final implementation captured in approved Chrome at 1512×860, DPR 1.
- [x] Source, external reference, and implementation placed in one comparison input and inspected locally.
- [x] Full view, center graph focus, and Agent focus checked.
- [x] Typography, spacing, colors, image handling, and copy reviewed.
- [x] Primary navigation appears only once in the far-left rail.
- [x] Mission object tree contains Mission objects only.
- [x] Opened-object tabs select content.
- [x] Source → entity → conflict → confirmation → Contract graph is selectable.
- [x] Agent and 用户 message alignment verified.
- [x] Disabled demo composer, keyboard focus, no-overflow, and empty/demo states checked.
- [x] Console and loopback runtime readback checked.
- [x] Human acceptance remains PENDING.

## Final result

No actionable P0/P1/P2 findings remain. P3 follow-up is limited to optional icon-library review in a separately approved checkpoint.

final result: passed
