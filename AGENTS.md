# ContextOx Agent Instructions

## Authority and scope

- `开发路径图.md` is the human-controlled product and development authority.
- `docs/架构与迁移报告.md` is the approved architecture, migration, failure-state, and acceptance authority.
- Before non-trivial work, restate the objective, scope, planned changes, deliverables, assumptions, risks, and external effects, then wait for explicit human confirmation.
- Never create, edit, move, replace, or delete `开发路径图.md` without first showing the exact content or diff and receiving approval for that exact change.
- Architecture discovery, trade-off selection, interfaces, data contracts, and hard-to-reverse decisions remain interactive with the human. Do not freeze them from implementation inference.
- Approval covers only the aligned scope. Stop and reconfirm when the scope, assumptions, risks, or deliverables materially change.

## Product boundaries

- V0 is one local TypeScript package and CLI, with multiple isolated Workspaces and one persistent Mission closed loop.
- Pi Agent Core owns only the model-tool loop, events, cancellation, and in-run state. ContextOx owns Workspace Context, Mission state, approvals, permissions, evidence, persistence, audit, and completion semantics.
- Use released Pi packages as exact npm dependencies. Do not fork, vendor, copy, patch, or use Git/local workspace dependencies for Pi.
- Do not add Web, SSO, multi-user collaboration, cloud sync, MCP, vector search, a knowledge graph, a memory service, a queue, or a workflow engine without a new human decision backed by a concrete need.
- Do not implement cross-task personal/chat memory. Company knowledge must be source-backed, versioned, permission-scoped, and human-approved Workspace Context.
- Every persisted object and object-level CLI operation must enforce `workspace_id`; only account-level `doctor`, `workspace create`, and `workspace list` may omit `--workspace`.

## TypeScript and dependencies

- Use Node `>=22.19.0`, npm, ESM, strict TypeScript, and erasable TypeScript syntax only.
- Use top-level imports. Do not use dynamic imports, `any`, parameter properties, `enum`, `namespace`, `import =`, or `export =` unless the human approves a demonstrated necessity.
- Keep domain, store, and context modules independent of Pi. Only the runtime adapter may instantiate Pi.
- Prefer Node standard library. Do not add a dependency, install packages, or change a lockfile without explicit approval and review of exact versions, license, release notes, lifecycle scripts, tarball integrity, and lockfile diff.
- Use `npm install --ignore-scripts` for an approved install. Do not run lifecycle scripts unless separately approved.
- Hide volatile provider and Pi details behind small explicit interfaces; do not create speculative extension points.

## State, safety, and privacy

- Fail closed on unknown workspace, permission, identity, source freshness, proposal version/hash, terminal receipt, or side-effect outcome.
- `agent_end` and Approval are not Mission completion. Only the approved Mission state transition with required receipt and evidence may complete a Mission.
- Keep `partial`, `blocked`, `failed`, `cancelled`, `NOT RUN`, `PENDING`, and `PASS` distinct. Never infer a higher-level PASS from a lower-level check.
- Never write secrets, customer/company private data, raw provider payloads, private evaluator data, or sensitive log bodies to Git, prompts, normal logs, test output, or audit exports.
- Do not copy legacy or fixture files unless their manifest entry is human-approved as `allow` and source/destination hashes are verified.
- Do not retry an external side effect when its outcome is unknown; reconcile first.

## Verification

- Use the smallest review surface that can falsify the current checkpoint. Review scope must be proportional to the changed behavior and irreversible risk.
- For a narrow change, inspect the exact diff and directly affected contracts. Do not reread or re-review unchanged documents, files, or the whole repository unless a concrete dependency or cross-cutting risk requires it.
- Reserve full-scope review for a new or materially revised architecture/roadmap, a cross-cutting security or data migration, or an explicit human request. A status-only documentation diff receives a status-only review.
- Prefer deterministic checks for hashes, schemas, links, status invariants, and file identity. When independent review is required, keep its brief bounded to the changed version and unresolved risks; review does not recursively trigger another review.
- Work backward from the public behavior and failure claim. Test state transitions, boundaries, time, concurrency, retries, crashes, partial failure, and downstream effects.
- Use `node:test`, `node:assert`, temporary directories, and temporary/in-memory SQLite. Default tests must not call a real provider or paid API.
- After code changes, run the approved typecheck, explicit standard-library test runner, build, and risk-proportionate focused checks. The test runner must fail on zero matched files or zero actual tests.
- Minimum Node 22.19 compatibility, automated checks, real-model runs, and human acceptance are separate evidence lanes. Human acceptance remains `PENDING` until the human records `PASS` for an exact commit/build.
- Never claim runtime behavior from static documents, fixture checks, or exit code alone.

## Git and delivery

- Preserve unrelated user changes. Read files in full before broad edits and edit only owned paths.
- Keep checkpoints small, coherent, independently verifiable, and reversible.
- Do not create commits, remotes, pushes, pull requests, releases, packages, deployments, or destructive migrations unless explicitly authorized for that action.
- Stage explicit owned paths only. Never use destructive reset/clean commands or overwrite unrelated work.
- At each checkpoint, report exact files, checks run, unresolved risks, rollback boundary, and human acceptance status.
- Review `开发路径图.md` for drift at the end of a development path, but propose any update as an exact diff; never sync it automatically.
