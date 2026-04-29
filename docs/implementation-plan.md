# Woof Implementation Plan

> **Purpose:** Single authoritative implementation plan, sequencing guide, and progress ledger for Woof.
> **Authority:** This file supersedes `docs/backlog.md` for implementation work. Architecture remains governed by `docs/architecture.md`; graph topology remains governed by `docs/adr/001-orchestration-topology.md`; code is the source of truth for implemented behaviour.
> **Operating rule:** Do not keep a second live backlog. New work must be added here as a scoped work item before implementation starts.

## Current Baseline

Woof has an implemented ADR-001 Stage-5 execution path. `woof wf --epic <N>` runs the deterministic Python graph for story selection, executor dispatch, critique dispatch, verification, gate opening, structured gate resolution, and commit transaction verification.

Implemented surfaces:

- CLI wrapper and commands: `wf`, `validate`, `dispatch`, `render-epic`, `check-cd`, `check stage-5`, and `gate write`.
- Python graph runtime: typed node contracts, transition table, transaction manifest generation, and manifest/index verification.
- Schemas for plans, gates, critiques, JSONL events, node I/O, executor results, check results, transaction manifests, language registry, quality gates, docs paths, agents, prerequisites, and test markers.
- Language registry files for Python, TypeScript, Rust, and Go.
- Stage-5 Check 6 real runner: `check_6_critique_blocker`.
- Dogfood evidence under `examples/dogfood/`.

Remaining implementation work is sequenced below. Each item is intentionally narrow enough to land as a conventional commit or a short series of commits when the item explicitly says so.

## Operating Loop

Every implementation turn must use this loop.

1. Read `AGENTS.md`, `README.md`, this file, and any architecture or schema file directly touched by the selected item.
2. Run `git status --short --branch` before editing and preserve unrelated local changes.
3. Run `just --list` when command usage for the turn is not already established.
4. Select the first `Ready` item by sequence unless another item is explicitly marked `In progress` or a blocker requires resequencing.
5. Restate the selected item, its observable outcomes, and the files or subsystems likely to change before editing.
6. Update this ledger at the start of work: mark the item `In progress` and record the planned validation.
7. Implement code, schemas, tests, and docs together when behaviour or contracts move.
8. Run targeted validation while developing when it gives faster feedback than the full gate.
9. Run `just check` before handoff unless the item is docs-only or the ledger records an external blocker.
10. Update this ledger before committing: mark the item `Completed`, `Blocked`, or `Split`; record validation evidence; record the conventional commit message to be used.
11. Commit through normal hooks. Do not bypass pre-commit or pre-push hooks.
12. Push normally. If hooks fail, fix the underlying issue or record the blocker in this ledger.
13. Monitor GitHub CI for the pushed commit until it reaches a terminal state. If CI fails, inspect the failing job, fix the underlying issue, commit and push the fix, then monitor the new run. If CI cannot be made green in the session, record the blocker in this ledger.
14. Final handoff must include the pushed commit hash, local validation result, GitHub CI result, and the full copy-pasteable `Next Continuation Prompt` block from this file. Do not summarise the prompt or provide only the next work-item ID.

## Ledger Semantics

Statuses:

- `Ready`: scoped and available to pick up.
- `In progress`: selected in the current working tree.
- `Completed`: landed and pushed, with validation recorded.
- `Blocked`: cannot proceed without a named external prerequisite or design decision.
- `Split`: replaced by narrower child items in this file.

The `Commit` field records the intended or completed conventional commit message. The immutable commit hash is reported in the final handoff after push; a commit cannot contain its own hash.

## Standard Validation

Default validation for code, schema, or command changes:

- Run the most focused relevant test or validation command during implementation.
- Run `just check` before committing.
- Let pre-commit run Ruff, format checks, and Woof config validation.
- Let pre-push run the configured unit suite.
- After push, monitor GitHub CI for the pushed commit and treat a failed remote run as unfinished work.

Docs-only changes still run `just check` unless the item states a narrower validation is acceptable. Any skipped command must be recorded with the blocker and reason.

## Observable Outcomes For The Plan

The plan is working when these outcomes remain true:

- A future agent can choose the next work item from this file without reading a separate backlog.
- Every active item has observable outcomes and validation expectations before implementation starts.
- Completed items record validation evidence and the commit message used.
- Architecture, README, schemas, and code move together when a contract changes.
- Any blocked item records the exact prerequisite required to unblock it.

## Sequenced Work Items

### Phase 0: Plan Consolidation

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| IPL-001 | Completed | Create the initial implementation-plan ledger. | `docs/implementation-plan.md` exists with a basic loop and ledger. | `just check` passed: Ruff lint, Ruff format check, and 98 tests. | `docs(workflow): add implementation plan loop` |
| IPL-002 | Completed | Consolidate the backlog into this implementation plan and retire duplicate roadmap authority. | This file contains the full sequenced roadmap; `README.md` and `docs/architecture.md` point to this file; `docs/backlog.md` has been removed as a live roadmap. | `just check` passed: Ruff lint, Ruff format check, and 98 tests. | `docs(workflow): consolidate implementation roadmap` |

### Phase 1: Stage-5 Core Hardening

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| WF-001 | Completed | Tighten `woof wf` transition idempotence and crash recovery. | Re-running from an interrupted commit transaction resumes the commit node without duplicating durable JSONL events; stale successful-run transient files are cleaned before reporting epic completion. | Targeted graph tests passed; `just check` passed: Ruff lint, Ruff format check, and 100 tests. | `fix(graph): harden wf crash recovery` |
| WF-002 | Completed | Represent incomplete Stage-5 states as explicit graph states or gates. | Missing required artefacts produce structured failures or `gate.md`; permissive skips are removed from graph-owned execution paths. | Targeted graph tests passed; targeted gate/check validation tests passed; `just check` passed: Ruff lint, Ruff format check, and 105 tests. | `fix(graph): gate incomplete stage states` |
| WF-003 | Completed | Stabilise `woof wf --format json` output. | JSON output has schema-backed fields for node status, next state, gate path, and validation summary; tests assert stable keys for success and gate cases. | Targeted graph tests passed: 18 tests; `just check` passed: Ruff lint, Ruff format check, and 111 tests. | `feat(graph): stabilise wf json output` |
| WF-004 | Completed | Expand graph transition and transaction-manifest coverage. | Unit tests cover successor selection, gate re-entry, manifest/index verification, and empty-diff handling. | Targeted graph tests passed: 18 tests; `just check` passed: Ruff lint, Ruff format check, and 111 tests. | `feat(graph): stabilise wf json output` |

## Post-WF-002 Execution Workstreams

After `WF-002` is complete, implementation no longer needs to proceed as one prompt per row. The remaining work should run as larger execution workstreams, with each workstream allowed to contain multiple conventional commits when that keeps review and rollback clean.

Workstream rules:

- Pick a workstream, not an isolated row, unless the workstream says a specific item must lead.
- Keep file ownership explicit before parallel sessions start.
- Parallel workers may update disjoint runner, test, schema, or docs files. One integrator session owns shared wiring files.
- Shared wiring files include `src/woof/checks/registry.py`, `src/woof/cli/main.py`, graph transition tables, and this implementation plan.
- A workstream is complete only when all included rows are marked `Completed`, validation is recorded, changes are pushed, and GitHub CI is terminal green.

| Workstream | Items | Concurrency model | Integrator ownership | Notes |
|---|---|---|---|---|
| A: Graph output and coverage | `WF-003`, `WF-004` | Sequential or one session; these touch shared graph contracts. | `src/woof/graph/`, CLI JSON output tests, schema-backed output assertions. | Do this before broad graph consumers depend on unstable output. |
| B: Core cheap checks | `CHK-001`, `CHK-002`, `CHK-003`, `CHK-005`, `CHK-007` | Parallel by runner/test pair after an integrator reserves registry wiring. | `src/woof/checks/registry.py`, common `CheckContext` helpers, shared fixtures. | Prefer one commit per check unless helper extraction spans checks. |
| C: Policy-heavy checks | `CHK-004`, `CHK-008`, `CHK-009` | Parallel only if file ownership is explicit; these have more design surface. | Contract-ref helper boundaries, docs-drift config semantics, review-valve state semantics. | Start after Workstream B exposes enough runner patterns. |
| D: Preflight and local tooling | `ENV-001`, `ENV-002`, `ENV-003`, `ENV-004` | `ENV-001` then `ENV-002`; `ENV-003` and `ENV-004` can run independently. | CLI command wiring, cache file policy, hook installation semantics. | Reconcile `ENV-003` with the existing bootstrap/hook foundation before coding. |
| E: GitHub sync | `GH-001`, `GH-002`, `GH-003`, `GH-004`, `GH-005` | Split into state initialisation (`GH-001`, `GH-002`) and rendering/sync/conflict (`GH-003`..`GH-005`). | GitHub adapter boundary, `.last-sync` format, gate opening on conflict. | No offline fallback; all auth/network failures fail loud. |
| F: Stage 1-4 graph migration | `STG-001`, `STG-002`, `STG-003`, `STG-004`, `STG-005` | `STG-001` must lead; after schemas land, producer-node groups can split. | Stage graph transition ownership and producer prompt/orchestration boundary. | Keep prompts pure producers; orchestration stays in Python. |
| G: Consumer and evidence polish | `DOG-001`, `GTS-001`, `GTS-002`, `DOC-001`, `DOC-002`, `DOC-003` | Parallel docs/examples work after relevant behaviour exists. | README/architecture cross-links and curated example policy. | Avoid documenting speculative behaviour ahead of code. |

### Phase 2: Stage-5 Check Runners

Checks should land one runner at a time. Each runner must replace placeholder or permissive behaviour with structured findings and tests.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| CHK-001 | Completed | Implement Check 1: `check_1_quality_gates`. | Reads `.woof/quality-gates.toml`, runs configured commands with timeouts, captures output, and returns structured pass/fail findings. Registry wiring intentionally unchanged per Workstream B integrator ownership. | Targeted runner tests passed: 4 tests for passing, failing, timeout, and missing-command cases. `just check` passed: Ruff lint, Ruff format check, and 115 tests. | `feat(checks): run configured quality gates` |
| CHK-002 | Completed | Implement Check 2: `check_2_outcome_markers`. | Resolves story `satisfies[]`, inspects staged test diff using `.woof/test-markers.toml`, and requires each outcome marker. | Targeted runner tests passed: 5 tests; simulated hook-env regression passed: 23 tests; `just check` passed: Ruff lint, Ruff format check, and 116 tests. | `feat(checks): verify outcome markers` |
| CHK-003 | Completed | Implement Check 3: `check_3_scope`. | Compares staged paths with `story.paths[]` plus allowed durable `.woof/` paths using git pathspec semantics. | Focused runner tests passed: allowed paths, forbidden paths, deleted files, pathspec edge cases, and missing-story failure; `just check` passed: Ruff lint, Ruff format check, and 116 tests. Registry wiring intentionally unchanged for integrator ownership. | `feat(checks): enforce story path scope` |
| CHK-004 | Ready | Implement Check 4: `check_4_contract_refs`. | Verifies owned contract refs through native tooling for OpenAPI/Schemathesis, Pydantic import and resolution, and JSON Schema/ajv; preserves the E146 invariant. | E146 regression fixture plus runner tests for each supported contract type; `just check`. | `feat(checks): validate contract references` |
| CHK-005 | Ready | Implement Check 5: `check_5_plan_crossrefs`. | Validates plan schema and cross-artefact invariants: outcome refs, contract-decision refs, CD ownership, dependency closure, acyclicity, and status coherence. | Plan fixture tests covering each invariant; `just check`. | `feat(checks): validate plan cross references` |
| CHK-007 | Ready | Implement Check 7: `check_7_commit_transaction`. | Asserts commit readiness: staged diff exists unless gated as empty, required durable `.woof` files are staged, and no unstaged or foreign paths remain. | Runner tests for clean, empty-gated, missing-durable, unstaged, and foreign-path cases; `just check`. | `feat(checks): verify commit transactions` |
| CHK-008 | Ready | Implement Check 8: `check_8_docs_drift`. | Honours optional `.woof/docs-paths.toml`; mapped code-path changes require mapped docs-path changes in the same transaction. | Runner tests for configured mappings, unmapped paths, docs-only changes, and missing config; `just check`. | `feat(checks): detect mapped docs drift` |
| CHK-009 | Ready | Implement Check 9: `check_9_review_valve`. | Opens periodic or end-of-epic review gates summarising accumulated minor critique findings. | Runner tests for threshold, end-of-epic, no-finding, and already-gated cases; `just check`. | `feat(checks): open review valve gates` |

### Phase 3: Stage 1-4 Graph Migration

This phase promotes Discovery, Definition, Breakdown, and Plan Gate into the same deterministic graph topology as Stage 5.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| STG-001 | Ready | Define Stage 1-4 node input and output schemas. | Schemas exist for discovery synthesis, epic definition, breakdown planning, plan critique, plan gate open, and plan gate resolution. | Schema fixture validation plus `just check`. | `feat(schemas): add planning graph contracts` |
| STG-002 | Ready | Add graph nodes for discovery synthesis and epic definition. | Graph can produce or validate Discovery synthesis and `EPIC.md` artefacts through typed producer nodes without successor selection in prompts. | Node tests plus `just check`. | `feat(graph): add discovery definition nodes` |
| STG-003 | Ready | Add graph nodes for breakdown planning and plan critique. | Stage 3 produces `plan.json`, `PLAN.md`, and `critique/plan.md` through graph-owned transitions. | Node tests and plan fixture validation plus `just check`. | `feat(graph): add breakdown plan nodes` |
| STG-004 | Ready | Make Stage 4 plan gate mandatory after valid plan and critique. | No valid filesystem state can contain a new plan and critique without an open `gate.md` or recorded `gate_resolved` event. | Gate reconstitution tests plus `just check`. | `feat(graph): enforce mandatory plan gate` |
| STG-005 | Ready | Move Stage 3 plan generation from design prose into producer-node prompts. | Prompt files are pure producer prompts; executable orchestration remains in Python. | Prompt registry tests or static assertions plus `just check`. | `refactor(playbooks): isolate planning prompts` |

### Phase 4: GitHub Issue Sync

GitHub sync must fail loud on auth, network, repo access, and rate-limit failures. No offline fallback is allowed.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| GH-001 | Ready | Implement cold-start pull from GitHub issue to local epic state. | `woof wf --epic <N>` with no local directory fetches the issue, initialises `.woof/epics/E<N>/`, seeds `spark.md`, and seeds `EPIC.md` when structured sections exist. | CLI tests with mocked `gh` responses plus `just check`. | `feat(github): initialise epic from issue` |
| GH-002 | Ready | Implement new-epic creation through GitHub. | `woof wf new "<spark>"` creates the issue, captures issue number, creates local state, and sets `.woof/.current-epic`. | CLI tests with mocked `gh`; schema validation; `just check`. | `feat(github): create epic issues` |
| GH-003 | Ready | Implement Definition close push and deterministic issue rendering. | Schema-valid `EPIC.md` renders managed issue sections deterministically while preserving free-form prose above the first managed heading. | Renderer golden tests plus `just check`. | `feat(github): render epic issue body` |
| GH-004 | Ready | Implement plan summary and epic completion sync. | Plan approval updates issue body with story summary; epic completion appends closing summary and closes the issue. | CLI tests with mocked `gh` plus `just check`. | `feat(github): sync plan and completion` |
| GH-005 | Ready | Implement `.last-sync` conflict detection and gate opening. | Divergent remote `updatedAt` or body hash opens a gate with a three-way diff; no silent overwrite occurs. | Conflict fixture tests plus `just check`. | `feat(github): gate sync conflicts` |

### Phase 5: Preflight, Environment, Hooks, And Tooling

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| CI-001 | Completed | Pin CI action versions to resolvable tags and require CI monitoring in the session finish loop. | GitHub CI can resolve all configured actions; this operating loop requires monitoring the pushed commit until CI passes or a blocker is recorded. | `just check` passed: Ruff lint, Ruff format check, and 98 tests. First GitHub run reached tests and exposed CI-only test isolation failure handled by CI-002. | `ci(workflow): pin uv action for ci` |
| CI-002 | Completed | Make missing-`ajv` validation test independent of host install layout. | The test constructs a controlled `PATH` containing `uv` and excluding `ajv`, so it fails loud on both developer machines and GitHub runners. | Targeted test passed; `just check` passed: Ruff lint, Ruff format check, and 98 tests. | `test(validate): isolate missing ajv path` |
| ENV-001 | Ready | Implement preflight as a first-class CLI path. | Woof validates wrappers, GitHub access, language tools, LSP plugins, Tree-sitter parsing, quality-gate commands, and consumer config schemas through a single CLI entry point. | CLI tests with mocked prerequisites plus `just check`. | `feat(cli): add preflight command` |
| ENV-002 | Ready | Cache preflight by prerequisite hash. | Stable prerequisites reuse cached results while network/auth checks remain short-lived runtime checks. | Cache unit tests plus `just check`. | `feat(preflight): cache prerequisite checks` |
| ENV-003 | Ready | Install hooks idempotently through project tooling. | Hook installation preserves user-managed content and can be rerun without duplicate blocks. | Hook fixture tests plus `just check`. | `feat(hooks): install woof hooks idempotently` |
| ENV-004 | Ready | Enforce audit redaction and size caps before commit. | Commit-bound audit files are redacted; oversized raw output stays gitignored with capped committed summaries. | Redaction and truncation tests plus `just check`. | `feat(audit): redact and cap committed output` |

### Phase 6: Consumer Integration And Dogfood Evidence

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| DOG-001 | Ready | Keep dogfood artefacts curated as evidence. | `examples/dogfood/` records only reusable evidence: contracts, plans, critiques, audit summaries, gates, and lessons that demonstrate Woof behaviour or failure modes. | Docs/example review plus `just check`. | `docs(dogfood): curate evidence examples` |
| GTS-001 | Ready | Document GTS as an external consumer checkout. | Woof docs describe GTS responsibilities for `.woof/` config without vendor-copying Woof into GTS. | Docs review plus `just check`. | `docs(consumers): define gts integration boundary` |
| GTS-002 | Ready | Generalise consumer policies into configurable checks only when reusable. | Consumer-specific policy remains outside Woof unless represented by documented configuration and checker behaviour. | Relevant checker tests plus `just check`. | `docs(consumers): constrain policy generalisation` |

### Phase 7: Documentation And Evidence Polish

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| DOC-001 | Ready | Keep README as the entry map. | README links to architecture, research, ADR-001, this implementation plan, and examples without duplicating architecture detail. | Docs review plus `just check`. | `docs(readme): align entry map` |
| DOC-002 | Ready | Keep architecture focused on design contract and current implementation boundary. | `docs/architecture.md` avoids live backlog content and points implementation sequencing here. | Docs review plus `just check`. | `docs(architecture): point roadmap to implementation plan` |
| DOC-003 | Ready | Add concise examples for core safety behaviours. | Examples demonstrate graph-owned orchestration, second-LLM critique enforcement, manifest-verified commits, gate resolution, and E146 contract fidelity. | Example validation where applicable plus `just check`. | `docs(examples): demonstrate woof safety model` |

## Next Continuation Prompt

```text
We are working in /home/ryan/Work/woof.

Read first:
1. AGENTS.md
2. README.md
3. docs/implementation-plan.md
4. Any architecture, schema, or source file directly touched by the selected work item

Goal:
Continue the Woof implementation plan from the Post-WF-002 Execution Workstreams section. If an item is already In progress, finish it; otherwise pick the first incomplete workstream by order and select an appropriately sized slice from that workstream. Before editing, run git status --short --branch and use just --list to confirm project commands. Update the ledger when starting and completing work. Keep code, schemas, tests, and docs aligned.

Workflow:
- Use just for project commands.
- Run targeted validation during implementation when useful.
- Run just check before handoff unless the ledger records an external blocker.
- Commit through normal hooks with the commit message recorded on the selected item.
- Push normally.
- Monitor GitHub CI for the pushed commit until it passes. If it fails, inspect the failing job, fix the underlying issue, and repeat the commit/push/monitor loop.
- In the final response, paste this complete continuation prompt block so it can be copied into a new session.

Start with:
Workstream B: Core cheap checks (`CHK-001`, `CHK-002`, `CHK-003`, `CHK-005`, `CHK-007`). Start with `CHK-002` unless an item is already `In progress`.
```
