# ContextOx Agent Instructions

## Authority and scope

- `开发路径图.md` is the human-controlled product and development authority.
- `docs/架构与迁移报告.md` is the approved architecture, migration,
  failure-state, and acceptance authority.
- Before non-trivial work, restate the objective, scope, planned changes,
  deliverables, assumptions, risks, and external effects, then wait for explicit
  human confirmation.
- Never create, edit, move, replace, or delete `开发路径图.md` without first
  showing the exact content or diff and receiving approval for that exact
  change. Do not edit `docs/架构与迁移报告.md` unless its exact change is also
  explicitly approved.
- Architecture discovery, trade-off selection, interfaces, data contracts, and
  hard-to-reverse decisions remain interactive with the human. Do not freeze
  them from implementation inference.
- Approval covers only the aligned scope. Stop and reconfirm when scope,
  assumptions, risks, deliverables, architecture, or external effects change
  materially.

## Product and runtime boundaries

- V0 is a local Python service with a React/TypeScript Workbench. CLI commands
  are limited to `doctor`, `start`, and necessary local automation entrypoints;
  the browser Workbench is the primary product surface.
- Bind the local server to `127.0.0.1` only. Do not add cloud hosting, SSO,
  multi-user collaboration, remote sync, deployment, or release behavior.
- ContextOx owns Workspace Context, Mission state, approvals, permissions,
  evidence, persistence, audit, and completion semantics. The first Agent Loop
  is a thin Python implementation with one configured provider boundary,
  serial domain tools, explicit budgets, cancellation, and structured events.
- Do not add a provider SDK, real model call, customer data, arbitrary file or
  SQL execution, shell/code execution, general `read/write/edit` tools, MCP,
  vector search, knowledge graph, memory service, queue, workflow engine, ORM,
  Redux, CSS/UI framework, or speculative extension points without a new human
  decision backed by a concrete need.
- Every persisted object and object-level operation must enforce
  `workspace_id`. Account-level `doctor`, workspace creation, and workspace
  listing may omit it only where the approved contract says so.
- V0 source handling is limited to explicitly authorized local material. Keep
  parsing, profiling, source revisions, evidence references, and failure states
  deterministic and bounded. Do not enumerate arbitrary paths or cross
  Workspace boundaries.

## Python, API, and frontend

- Use the approved Python version and UV for the virtual environment,
  dependencies, and lockfile. Use FastAPI/Pydantic/Uvicorn only at the exact
  reviewed versions in the active checkpoint manifest.
- Pydantic models are the single source of truth for HTTP JSON, SSE envelopes,
  forms, events, errors, and Contract objects. Generate OpenAPI/JSON Schema,
  TypeScript types, and the typed frontend client from that source; generated
  files are not hand-edited. A drift check must fail when regeneration differs.
- Use React + TypeScript for a local static SPA served by the Python process in
  delivery builds. Keep UI states explicit and accessible: loading, empty,
  partial, blocked, failed, cancelled, stale, conflict, and not implemented.
- Keep frontend dependencies to the approved exact set. Before changing a
  lockfile or installing anything, review exact versions, transitive packages,
  licenses, release notes, lifecycle scripts, integrity, and lockfile diff.
  Install npm packages with `--ignore-scripts`; never run a lifecycle script
  unless separately approved.
- Prefer Python standard library and small explicit modules. Use standard
  library tests; do not introduce pytest or an Agent framework for N1.

## State, safety, and privacy

- Fail closed on unknown Workspace, permission, identity, source freshness,
  proposal version/hash, terminal receipt, or side-effect outcome.
- `agent_end` and approval are not Mission completion. Only the approved Mission
  transition with the required receipt and evidence can complete a Mission.
- Keep `partial`, `blocked`, `failed`, `cancelled`, `NOT RUN`, `PENDING`, and
  `PASS` distinct. Never infer a higher-level PASS from a lower-level check.
- Never write secrets, customer/company private data, raw provider payloads,
  private evaluator data, or sensitive log bodies to Git, prompts, normal logs,
  tests, or audit exports. Do not read `.env` contents.
- Do not copy legacy or fixture files unless the publication manifest marks the
  exact file `allow` and source/destination hashes are verified.
- Do not retry an external side effect when its outcome is unknown; reconcile
  it first. No external side effects are part of N1 except the approved Git
  branch/commit/push/merge lifecycle.

## Verification and acceptance

- Work backward from user-visible behavior and failure claims. Use the smallest
  public-seam checks that can falsify the current checkpoint, including
  boundaries, state transitions, crashes, partial failure, and downstream
  effects where relevant.
- Keep static checks, automated tests, builds, runtime readback, external
  effects, real-model runs, browser inspection, human acceptance, and user
  value evidence as separate lanes. Tests and builds do not prove product
  behavior; browser inspection does not prove real-model behavior.
- N1 must verify, at minimum: locked UV sync; Python compile and nonzero
  standard-library tests; `contextox doctor`; OpenAPI-to-TypeScript generation
  and drift; npm clean install with `--ignore-scripts`; TypeScript typecheck;
  nonzero frontend tests; Vite build; an outside-repository temporary-data
  runtime smoke for health, OpenAPI, SSE, and root assets; diff checks; a
  sensitive-pattern scan; and an explicit scope check.
- Human Workbench acceptance remains `PENDING` until a human records `PASS` for
  the exact commit/build. Automated checks may report evidence and limits but
  must not record human acceptance.

## Human-controlled roadmap

- `开发路径图.md` is not an automatically maintained status document. Do not
  synchronize it from code, tests, task results, or priorities.
- If the roadmap is missing or stale, report the condition and propose an exact
  diff; do not write it. At the end of a development path, review for drift and
  keep any proposed update pending human approval.

## Git and delivery

- Verify repository root, working tree, current/default branches, remotes,
  upstream tracking, and local/remote divergence before repository work.
- Preserve unrelated changes. Never broadly stage, reset, clean, force-push,
  rewrite shared history, or delete branches. Stage explicit owned paths only.
- Code and higher-risk changes use a short-lived `codex/<short-purpose>` branch.
  Keep checkpoints small, coherent, independently verifiable, and reversible.
- The approved N1 lifecycle is: start from verified `main`; create the change
  branch; commit rules, scaffold, legacy replacement, and README as separate
  checkpoints; push the branch; reverify latest remote `main`; merge safely to
  local `main`; push `main`; and read back that delivered commits are ancestors
  of local and remote `main`.
- Commit, branch push, merge, and main push require explicit scope approval.
  They do not authorize release, deployment, tagging, destructive migration,
  GitHub metadata edits, or human acceptance unless those actions are separately
  included in the approved plan.
- Before merging, verify source and target commits, required checks, review
  findings, migrations, rollback, conflicts, latest remote state, and unrelated
  changes. Stop and reconfirm on unexpected divergence, conflicts, or failed
  checks.
- At every checkpoint report exact files, checks, runtime evidence, unresolved
  risks, rollback boundary, branch/commit/remote state, and acceptance status.
