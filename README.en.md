<p align="center"><img src="web/src/assets/contextox-mark.png" width="76" alt="ContextOx mark"></p>
<h1 align="center">ContextOx · 数契</h1>
<p align="center"><strong>Make every table explain what it means.</strong></p>
<p align="center">Turn scattered business rules in tables, notes and people's memory into evidence-backed, human-confirmed candidate definitions.</p>
<p align="center"><code>Demo 1.0.0</code> · local-first · conversation-led · MIT</p>
<p align="center">
  <a href="https://archerthegoat.github.io/contextox-agent/presentation-en.html#1">Dynamic introduction</a> ·
  <a href="https://archerthegoat.github.io/contextox-agent/downloads/contextox-demo-1.0.0-product-film-en.mp4">45-second product film</a> ·
  <a href="README.md">简体中文</a> ·
  <a href="https://github.com/archerthegoat/contextox-agent/releases/tag/v1.0.0">Demo 1.0.0 release</a>
</p>

## The same order data can have three answers

**For the same order data, is the East China total 689 CNY, 440 CNY, or 390 CNY?**

| 689 CNY | 440 CNY | 390 CNY |
| --- | --- | --- |
| Add every status | Count paid orders only | Count paid, then subtract refunds |

All three numbers are calculable. The missing part is the rule: **what counts, how refunds are handled, and which time is used.**

ContextOx is a local-first business-definition Agent. You select sources and state a goal; it finds evidence, identifies the questions that can change the result, asks a person to decide, and leaves a candidate definition that can be reviewed later.

> The model helps understand, organize and ask. People confirm business facts.

The 689 / 440 / 390 example is made from the repository's public synthetic CSV files. It illustrates alternate rules; it is not customer data and not a calculation executed by ContextOx.

## See the workflow

The public dynamic introduction follows one complete, understandable path:

1. **Select sources** — choose the exact tables and notes allowed in this round.
2. **State the goal** — describe the question in ordinary language.
3. **Confirm rules** — the Agent surfaces pending, refund and time choices; a person answers them.
4. **Review the candidate** — inspect the field, relationship, evidence, changes and open items.

The current Workbench is conversation-first. When the selected sources and goal are sufficient, sending the message advances the analysis. When the evidence cannot decide a business rule, the Agent pauses and explains why the answer matters.

## What makes ContextOx different

ContextOx is not trying to replace the tools that execute code, answer questions or catalogue data. It focuses on the hand-off before those tools can safely execute: **when a request sounds clear but still has multiple valid meanings.**

| Tool type | Best starting point | Typical output | ContextOx's relationship |
| --- | --- | --- | --- |
| **General Agent, such as Codex or Claude Code** | A task with a clear goal and usable context | Code, research, documents or delivery work | A natural next step after the business definition is clear; a like-for-like baseline we must test honestly |
| **BI and data-question tools** | An existing metric or semantic model | Queries, charts and analysis | Good at “what is the number?”; ContextOx handles “which number should we mean?” |
| **Data catalog and governance platforms, such as Atlan or DataHub** | Metadata, lineage, permissions and enterprise assets | Discoverable and governable data assets | Potential read-only context later; ContextOx does not rebuild the enterprise data foundation |
| **ContextOx today** | Authorized sources plus a concrete disagreement | Evidence-linked, human-confirmed candidate definition | Finds gaps, asks for decisions, preserves changes and unknowns for review and hand-off |

This is a product choice, not an absolute advantage validated in real customer work. The next meaningful comparison is the same material, task and time boundary against a reasonably configured general Agent, measuring missing rules, clarification quality, human edits and hand-off cost.

## What the Demo does—and does not—claim

**Available in Demo 1.0.0:**

- continuous conversation with an Agent;
- an explicit source boundary for each round;
- clarification cards with answers, sources and bases;
- human confirmation before answers are adopted;
- candidate fields, relationships, citations, changes and open items;
- local persistence and failure recovery.

**Not the meaning of this Demo:**

- a candidate is not a formally approved enterprise Contract;
- the Demo does not run arbitrary SQL, shell or code, and does not write production databases;
- the current service is local-only, with no cloud sync or multi-user collaboration;
- engineering checks and synthetic responses do not prove customer value.

The repository keeps engineering tests, synthetic model responses, real-model trials, browser inspection, human acceptance and user value as separate evidence lanes. Only import or send material you are authorized to use.

## Product direction

The near-term goal is to make one disputed definition understandable and reviewable. The longer-term direction is deliberately staged:

| Horizon | Direction | Evidence still needed |
| --- | --- | --- |
| **Now** | Select sources, clarify rules and form a reviewable candidate definition | Real cases and human acceptance |
| **Next** | Reuse approved definitions with scope, owner, version and acceptance conditions | Safe approval and reuse semantics |
| **Later** | Hand a confirmed definition to Codex, a data team or another engineering Agent, then bring implementation evidence back | A real end-to-end delivery case |
| **When justified** | Read enterprise metadata and documents through controlled, read-only integrations | Permission, freshness, audit and boundary proof |

The long-term idea is a **definition hand-off layer between business, data and Agents**: every field, metric and data change should answer what it means, where it came from, who decided, what changed, what remains unknown, and how correctness will be checked.

That vision does not turn the current Demo into a data catalog, permission platform, general coding Agent or production database operator.

## English material kit

- [Dynamic HTML](https://archerthegoat.github.io/contextox-agent/presentation-en.html#1) — 12 interactive slides with keyboard navigation, contents, fullscreen and reduced-motion support.
- [45-second product film](https://archerthegoat.github.io/contextox-agent/downloads/contextox-demo-1.0.0-product-film-en.mp4) — English captions over the same Workbench workflow and audio mix.
- [12-page presentation PDF](https://archerthegoat.github.io/contextox-agent/downloads/contextox-demo-1.0.0-presentation-en.pdf) · [one-page overview](https://archerthegoat.github.io/contextox-agent/downloads/contextox-demo-1.0.0-one-pager-en.pdf)
- [Chinese dynamic HTML](https://archerthegoat.github.io/contextox-agent/presentation.html#1) · [Chinese product film](https://archerthegoat.github.io/contextox-agent/downloads/contextox-demo-1.0.0-product-film.mp4)
- [Shared logo and brand kit](https://archerthegoat.github.io/contextox-agent/downloads/contextox-brand-kit.zip)

All public showcase links are served by GitHub Pages from the `site/` directory. The English route is a sibling of the original Chinese route, so existing links remain stable.

## Run the open Demo locally

You need Python `3.14.7`, [UV](https://docs.astral.sh/uv/) and Node.js `22.19.0` or newer:

~~~sh
git clone https://github.com/archerthegoat/contextox-agent.git
cd contextox-agent
uv sync --locked
npm --prefix web ci --ignore-scripts
npm --prefix web run build
uv run --locked contextox start --agent-profile demo-fast --open-browser
~~~

The service opens at <http://127.0.0.1:8787>. The public example does not need a model key. To analyze your own authorized material, open **Model settings** in the lower-left of the Workbench and provide a DeepSeek key. Saving settings does not call the model; sending a message may incur provider charges.

The current service is bound to `127.0.0.1`. It does not provide remote access, cloud sync, multi-user collaboration or a prebuilt installer.

## Contribute or bring a case

The project needs real questions from metric reviews, data warehouses, BI, governance and delivery work: **why does one term have three algorithms, which source should win, and who is affected when the definition changes?**

- Bring a synthetic or redacted problem and help test the clarification flow.
- Contribute definition methods, examples, interaction feedback, code, tests or documentation.
- Open an [Issue](https://github.com/archerthegoat/contextox-agent/issues) or contact [@archerthegoat](https://github.com/archerthegoat). Chinese and English are welcome.

Never upload keys, customer data, private model payloads or raw confidential material. Use synthetic or properly redacted examples only.

## Repository map and checks

~~~text
src/contextox/   Python service, state and Agent loop
web/             React + TypeScript Workbench
site/            dynamic introduction and GitHub Pages files
video/           Remotion product-film source
tests/           Python standard-library tests
scripts/         generation, validation and release helpers
docs/            architecture, acceptance and delivery records
~~~

~~~sh
uv run --locked contextox doctor
uv run --locked python -m compileall -q src tests
uv run --locked python -m unittest discover -s tests
npm --prefix web run check:api
npm --prefix web run typecheck
npm --prefix web test
npm --prefix web run build
python3 scripts/check_showcase.py
~~~

## License

[MIT](LICENSE) · all public showcase examples are synthetic.
