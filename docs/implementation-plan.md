# Woof Implementation Plan

> **Purpose:** Single authoritative implementation plan, sequencing guide, and progress ledger for Woof.
> **Authority:** This file supersedes `docs/backlog.md` for implementation work. Architecture remains governed by `docs/architecture.md`; graph topology remains governed by `docs/adr/001-orchestration-topology.md`; role routing remains governed by `docs/adr/002-graph-led-role-routing.md`; code is the source of truth for implemented behaviour.
> **Operating rule:** Do not keep a second live backlog. New work must be added here as a scoped work item before implementation starts.

## Current Baseline

Woof has an implemented ADR-001 Stage-5 execution path. `woof wf --epic <N>` runs the deterministic Python graph for story selection, executor dispatch, critique dispatch, verification, gate opening, structured gate resolution, and commit transaction verification.

ADR-002 is now accepted and implemented. Woof is graph-led, GPT-5.5 is the preferred primary producer route, Claude Opus 4.7 at `max` effort is the preferred reviewer route, and reviewer blockers open human gates rather than model-to-model debate loops. Stage-5 dispatch uses semantic `primary` / `reviewer` roles and public raw `claude` / `codex` adapters owned by Woof. Preflight is the startup infrastructure check. This role-routing baseline remains the active route policy.

Implemented surfaces:

- CLI wrapper and commands: `wf`, `preflight`, `hooks install`, `validate`, `dispatch`, `render-epic`, `check-cd`, `check stage-5`, and `gate write`.
- Python graph runtime: typed node contracts, transition table, transaction manifest generation, and manifest/index verification.
- Schemas for plans, gates, critiques, JSONL events, node I/O, executor results, check results, transaction manifests, language registry, quality gates, docs paths, agents, prerequisites, and test markers.
- Language registry files for Python, TypeScript, Rust, and Go.
- All nine Stage-5 check runners are implemented and wired into the registry.
- Dogfood evidence under `examples/dogfood/`.

The historical sequenced rows below remain the implementation evidence ledger. The active backlog is now the Course Correction section. Future continuation turns select the first `Ready` or `In progress` course-correction workstream and complete a meaningful slice that closes multiple related gaps; they do not invent one tiny row at a time.

## Operating Loop

Every implementation turn must use this loop.

1. Read `AGENTS.md`, `README.md`, this file, and any architecture or schema file directly touched by the selected item.
2. Run `git status --short --branch` before editing and preserve unrelated local changes.
3. Run `just --list` when command usage for the turn is not already established.
4. Select the first `Ready` course-correction workstream by order unless another workstream is explicitly marked `In progress` or a recorded blocker requires resequencing. Choose a workstream slice large enough to close multiple related child gaps before committing.
5. Restate the selected workstream slice, its observable outcomes, and the files or subsystems likely to change before editing.
6. Update this ledger at the start of work: mark the workstream `In progress`, name the child gaps in scope, and record the planned validation.
7. Implement code, schemas, tests, and docs together when behaviour or contracts move.
8. Run targeted validation while developing when it gives faster feedback than the full gate.
9. Run `just check` before handoff unless the item is docs-only or the ledger records an external blocker.
10. Update this ledger before committing: mark the workstream or child gaps `Completed`, `Blocked`, or `Split`; record validation evidence; record the conventional commit message or short commit series to be used.
11. Commit through normal hooks. Do not bypass pre-commit or pre-push hooks. A workstream may use multiple conventional commits when that makes review or rollback clearer.
12. Push normally. If hooks fail, fix the underlying issue or record the blocker in this ledger.
13. Monitor GitHub CI for the pushed commit until it reaches a terminal state. If CI fails, inspect the failing job, fix the underlying issue, commit and push the fix, then monitor the new run. If CI cannot be made green in the session, record the blocker in this ledger.
14. Final handoff must include the pushed commit hash, local validation result, GitHub CI result, and the full copy-pasteable `Next Continuation Prompt` block from this file. Do not summarise the prompt or provide only the next work-item ID.

## Ledger Semantics

Statuses:

- `Ready`: scoped and available to pick up.
- `In progress`: selected in the current working tree.
- `Completed`: landed and pushed, with validation recorded.
- `Completed (uncommitted)`: validation-complete local work that has not yet landed or pushed; must be committed before it is durable.
- `Blocked`: cannot proceed without a named external prerequisite or design decision.
- `Split`: replaced by narrower child items in this file.

Gap audit statuses:

- `implemented`: architecture claim is implemented and has direct code/test evidence.
- `partial`: a meaningful implementation exists, but the architecture claim is not fully closed.
- `missing`: no current implementation surface was found for the documented claim.
- `docs-drift`: implementation may be acceptable, but docs, schemas, or help text overclaim or disagree.
- `intentionally deferred`: the architecture explicitly allows later relaxation or the current behaviour is a deliberate holding pattern.
- `rejected`: the audited claim or option must not be implemented because it conflicts with accepted architecture.

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
| R: Role routing and startup preflight | `ROLE-001`, `ROLE-002`, `ROLE-003`, `ROLE-004`, `ROLE-005`, `ROLE-006` | Sequential through `ROLE-004`; prompt/docs cleanup can follow after code stabilises. | Dispatch adapter, agents schema, preflight, Stage-5 graph call sites, prompt contracts, and this implementation plan. | Must complete before `STG-002` so Stage 1-4 nodes do not encode obsolete Claude/Codex assumptions. |
| F: Stage 1-4 graph migration | `STG-001`, `STG-002`, `STG-003`, `STG-004`, `STG-005` | `STG-001` must lead; after schemas land, producer-node groups can split. | Stage graph transition ownership and producer prompt/orchestration boundary. | Keep prompts pure producers; orchestration stays in Python. |
| G: Consumer and evidence polish | `DOG-001`, `GTS-001`, `GTS-002`, `DOC-001`, `DOC-002`, `DOC-003` | Parallel docs/examples work after relevant behaviour exists. | README/architecture cross-links and curated example policy. | Avoid documenting speculative behaviour ahead of code. |

### Phase 2: Stage-5 Check Runners

Checks should land one runner at a time. Each runner must replace placeholder or permissive behaviour with structured findings and tests.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| CHK-001 | Completed | Implement Check 1: `check_1_quality_gates`. | Reads `.woof/quality-gates.toml`, runs configured commands with timeouts, captures output, returns structured pass/fail findings, and is wired into the Stage-5 registry. | Targeted runner tests passed: 4 tests for passing, failing, timeout, and missing-command cases. Workstream B integration validation passed: Ruff lint, Ruff format check, and 139 tests. | `feat(checks): run configured quality gates` |
| CHK-002 | Completed | Implement Check 2: `check_2_outcome_markers`. | Resolves story `satisfies[]`, inspects staged test diff using `.woof/test-markers.toml`, requires each outcome marker, and is wired into the Stage-5 registry. | Targeted runner tests passed: 5 tests; simulated hook-env regression passed: 23 tests. Workstream B integration validation passed: Ruff lint, Ruff format check, and 139 tests. | `feat(checks): verify outcome markers` |
| CHK-003 | Completed | Implement Check 3: `check_3_scope`. | Compares staged paths with `story.paths[]` plus allowed durable `.woof/` paths using git pathspec semantics; wired into the Stage-5 registry. | Focused runner tests passed: allowed paths, forbidden paths, deleted files, pathspec edge cases, and missing-story failure. Workstream B integration validation passed: Ruff lint, Ruff format check, and 139 tests. | `feat(checks): enforce story path scope` |
| CHK-004 | Completed | Implement Check 4: `check_4_contract_refs`. | Verifies owned contract refs through native tooling for OpenAPI/Schemathesis, Pydantic import and resolution, and JSON Schema/ajv; preserves the E146 invariant. | Targeted Check 4/E146/Stage-5 command tests passed: 21 tests. `just check` passed: Ruff lint, Ruff format check, and 145 tests. | `feat(checks): validate contract references` |
| CHK-005 | Completed | Implement Check 5: `check_5_plan_crossrefs`. | Validates plan schema and cross-artefact invariants: outcome refs, contract-decision refs, CD ownership, dependency closure, acyclicity, and status coherence; wired into the Stage-5 registry. | Targeted Check 5 runner tests passed: 9 tests. Workstream B integration validation passed: Ruff lint, Ruff format check, and 139 tests. | `feat(checks): validate plan cross references` |
| CHK-007 | Completed | Implement Check 7: `check_7_commit_transaction`. | Asserts commit readiness: staged diff exists unless gated as empty, required durable `.woof` files are staged, no unstaged or foreign paths remain, and the runner is wired into the Stage-5 registry. | Focused runner tests passed: 5 tests. Workstream B integration validation passed: Ruff lint, Ruff format check, and 139 tests. | `feat(checks): verify commit transactions` |
| CHK-008 | Completed | Implement Check 8: `check_8_docs_drift`. | Honours optional `.woof/docs-paths.toml`; mapped code-path changes require mapped docs-path changes in the same transaction. | Targeted Check 8/Stage-5 command tests passed: 11 tests. `just check` passed: Ruff lint, Ruff format check, and 151 tests. | `feat(checks): detect mapped docs drift` |
| CHK-009 | Completed | Implement Check 9: `check_9_review_valve`. | Opens periodic or end-of-epic review gates summarising accumulated minor critique findings. | Targeted Check 9/Stage-5 command tests passed: 10 tests. `just check` passed: Ruff lint, Ruff format check, and 156 tests. | `feat(checks): open review valve gates` |

### Phase 2.5: Role Routing, Model Policy, And Startup Preflight

This phase implements ADR-002 before Stage 1-4 graph migration continues.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| ROLE-001 | Completed | Record the graph-led primary/reviewer role-routing decision. | ADR-002 exists; README and architecture point to GPT-5.5 as the preferred primary producer route, Claude Opus 4.7 at `max` effort as the preferred reviewer route, and `woof preflight` as the startup infrastructure check. | `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 199 tests. | `docs(architecture): define graph-led role routing` |
| ROLE-002 | Completed | Replace personal wrappers with public dispatch adapters and semantic role routes. | Graph call sites invoke `woof dispatch --role <role>`; dispatch resolves adapter from `.woof/agents.toml`; Woof constructs raw `claude` and `codex` commands itself; no runtime path calls `cld`, `cod`, `agent-sync`, or Ryan-local dotfiles; legacy `planner`, `story-executor`, and `critiquer` names remain supported through adapter migration. | Focused dispatch, preflight, graph, validate, and Stage-5 related tests passed. `just check` passed: Ruff lint, Ruff format check, and 203 tests. | `refactor(dispatch): route through public cli adapters` |
| ROLE-003 | Completed | Add effort-aware role configuration and MCP JSON construction. | `agents.schema.json` supports public adapters and role `effort`; Claude routes map effort to `claude --effort <level>`; reviewer dry-run shows `--effort max`; Codex routes set or verify reasoning effort through the supported CLI/config path; Woof generates `--strict-mcp-config --mcp-config` JSON for Claude roles; dispatch events record resolved command, model, effort, and MCP set. | `just woof validate --schema agents .woof/agents.toml` passed. Targeted dispatch tests passed: 21 tests. `just check` passed: Ruff lint, Ruff format check, and 204 tests. | `feat(config): add public role routes` |
| ROLE-004 | Completed | Expand preflight into the startup infrastructure check. | `woof preflight` verifies Woof checkout/install, consumer `.woof/` schemas, public CLI availability (`claude`, `codex`), route model/effort settings, generated MCP config, GitHub access, quality-gate command resolution, language tooling, and project-specific host/server prerequisites. | Focused preflight/dispatch tests passed: 32 tests. Self-preflight passed: 18 checks. `just woof validate --schema prerequisites .woof/prerequisites.toml` and `just woof validate --schema agents .woof/agents.toml` passed. `just check` passed: Ruff lint, Ruff format check, and 209 tests. | `feat(preflight): verify startup infrastructure` |
| ROLE-005 | Completed | Add non-blocking reviewer disposition handling. | Reviewer `info` and `minor` findings require a primary disposition record; accepted feedback may update artefacts; reviewer `blocker` opens a human gate with primary and reviewer positions; no model debate loop exists. | Focused graph/disposition/schema tests passed: 87 tests. `just check` passed: Ruff lint, Ruff format check, and 214 tests. | `feat(graph): record reviewer dispositions` |
| ROLE-006 | Completed | Update producer/reviewer prompts and docs after routing lands. | Prompt files, examples, architecture, and README use primary/reviewer terminology; provider names appear only in route examples and compatibility notes. | Focused prompt terminology assertion passed: `uv run pytest tests/unit/test_prompt_role_terminology.py`. `just check` passed: Ruff lint, Ruff format check, and 215 tests. | `docs(workflow): align prompts with role routing` |

### Phase 3: Stage 1-4 Graph Migration

This phase promotes Discovery, Definition, Breakdown, and Plan Gate into the same deterministic graph topology as Stage 5.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| STG-001 | Completed | Define Stage 1-4 node input and output schemas. | Schemas exist for discovery synthesis, epic definition, breakdown planning, plan critique, plan gate open, and plan gate resolution. | Targeted schema tests passed: 46 tests. `just lint` passed. `just check` passed: Ruff lint, Ruff format check, and 199 tests. | `feat(schemas): add planning graph contracts` |
| STG-002 | Completed | Add graph nodes for discovery synthesis and epic definition. | Graph can produce or validate Discovery synthesis and `EPIC.md` artefacts through typed producer nodes without successor selection in prompts. | Focused graph/schema/prompt tests passed: 78 tests. `just check` passed: Ruff lint, Ruff format check, and 221 tests. | `feat(graph): add discovery definition nodes` |
| STG-003 | Completed | Add graph nodes for breakdown planning and plan critique. | Stage 3 produces `plan.json`, `PLAN.md`, and `critique/plan.md` through graph-owned transitions. | Focused graph and schema tests passed: 79 tests. Prompt terminology guard passed. `just lint` passed. `just check` passed: Ruff lint, Ruff format check, and 223 tests. | `feat(graph): add breakdown plan nodes` |
| STG-004 | Completed | Make Stage 4 plan gate mandatory after valid plan and critique. | No valid filesystem state can contain a new plan and critique without an open `gate.md` or recorded `gate_resolved` event with `gate_type=plan_gate`. | Focused graph gate tests passed: 4 tests. Focused validation tests passed: 20 tests. `just check` passed: Ruff lint, Ruff format check, and 225 tests. | `feat(graph): enforce mandatory plan gate` |
| STG-005 | Completed | Move Stage 3 plan generation from design prose into producer-node prompts. | Prompt files are pure producer prompts; executable orchestration remains in Python. | Prompt/static assertions and focused graph tests passed: 36 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 227 tests. | `refactor(playbooks): isolate planning prompts` |

### Phase 4: GitHub Issue Sync

GitHub sync must fail loud on auth, network, repo access, and rate-limit failures. No offline fallback is allowed.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| GH-001 | Completed | Implement cold-start pull from GitHub issue to local epic state. | `woof wf --epic <N>` with no local directory fetches the issue, initialises `.woof/epics/E<N>/`, seeds `spark.md`, and seeds `EPIC.md` when structured sections exist. | Targeted GitHub sync/render/graph tests passed: 29 tests. `just check` passed: Ruff lint, Ruff format check, and 174 tests. | `feat(github): initialise epic from issue` |
| GH-002 | Completed | Implement new-epic creation through GitHub. | `woof wf new "<spark>"` creates the issue, captures issue number, creates local state, and sets `.woof/.current-epic`. | Targeted GitHub sync/render tests passed: 13 tests. `.woof/prerequisites.toml` schema validation passed. `just check` passed: Ruff lint, Ruff format check, and 177 tests. | `feat(github): create epic issues` |
| GH-003 | Completed | Implement Definition close push and deterministic issue rendering. | Schema-valid `EPIC.md` renders managed issue sections deterministically while preserving free-form prose above the first managed heading. | Targeted GitHub sync/render tests passed: 15 tests. `just check` passed: Ruff lint, Ruff format check, and 179 tests. | `feat(github): render epic issue body` |
| GH-004 | Completed | Implement plan summary and epic completion sync. | Plan approval updates issue body with story summary; epic completion appends closing summary and closes the issue. | Targeted GitHub sync/render/graph tests passed: 36 tests. `just check` passed: Ruff lint, Ruff format check, and 181 tests. | `feat(github): sync plan and completion` |
| GH-005 | Completed | Implement `.last-sync` conflict detection and gate opening. | Divergent remote `updatedAt` or body hash opens a gate with a three-way diff; no silent overwrite occurs. | Targeted GitHub sync/gate/graph tests passed: 36 tests. `just check` passed: Ruff lint, Ruff format check, and 184 tests. | `feat(github): gate sync conflicts` |

### Phase 5: Preflight, Environment, Hooks, And Tooling

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| CI-001 | Completed | Pin CI action versions to resolvable tags and require CI monitoring in the session finish loop. | GitHub CI can resolve all configured actions; this operating loop requires monitoring the pushed commit until CI passes or a blocker is recorded. | `just check` passed: Ruff lint, Ruff format check, and 98 tests. First GitHub run reached tests and exposed CI-only test isolation failure handled by CI-002. | `ci(workflow): pin uv action for ci` |
| CI-002 | Completed | Make missing-`ajv` validation test independent of host install layout. | The test constructs a controlled `PATH` containing `uv` and excluding `ajv`, so it fails loud on both developer machines and GitHub runners. | Targeted test passed; `just check` passed: Ruff lint, Ruff format check, and 98 tests. | `test(validate): isolate missing ajv path` |
| ENV-001 | Completed | Implement preflight as a first-class CLI path. | `woof preflight` validates declared public commands, GitHub access, language tools, optional LSP plugins, Tree-sitter parsing, quality-gate command resolution, and consumer config schemas through a single CLI entry point with text and JSON output. | Targeted preflight tests passed: 4 tests. Language registry schema validation passed. Self-preflight passed: 15 checks. `just check` passed: Ruff lint, Ruff format check, and 160 tests. | `feat(cli): add preflight command`; `test(cli): make preflight stubs portable` |
| ENV-002 | Completed | Cache preflight by prerequisite hash. | Stable prerequisites reuse cached results while network/auth checks remain short-lived runtime checks. | Focused preflight cache tests passed: 6 tests. `just check` passed: Ruff lint, Ruff format check, and 162 tests. | `feat(preflight): cache prerequisite checks` |
| ENV-003 | Completed | Install hooks idempotently through project tooling. | `woof hooks install` appends or refreshes the managed post-commit cartography block while preserving user-managed hook content; `just install-hooks` runs the Woof installer after `prek`; reruns do not duplicate the block. | Focused hook fixture tests passed: 5 tests. `just install-hooks` passed. `just check` passed: Ruff lint, Ruff format check, and 167 tests. | `feat(hooks): install woof hooks idempotently` |
| ENV-004 | Completed | Enforce audit redaction and size caps before commit. | Commit-bound audit files are redacted; oversized raw output stays gitignored with capped committed summaries. | Targeted audit/config/graph tests passed: 26 tests. `just check` passed: Ruff lint, Ruff format check, and 171 tests. | `feat(audit): redact and cap committed output` |

### Phase 6: Consumer Integration And Dogfood Evidence

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| DOG-001 | Completed | Keep dogfood artefacts curated as evidence. | `examples/dogfood/` records only reusable evidence: contracts, plans, critiques, audit summaries, gates, and lessons that demonstrate Woof behaviour or failure modes. | Example schema validation passed for retained epic, plan, critique, and JSONL event artefacts. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 227 tests. | `docs(dogfood): curate evidence examples` |
| GTS-001 | Completed | Document GTS as an external consumer checkout. | Woof docs describe GTS responsibilities for `.woof/` config without vendor-copying Woof into GTS. | Docs review passed. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 227 tests. | `docs(consumers): define gts integration boundary`; `docs(workflow): advance continuation prompt` |
| GTS-002 | Completed | Generalise consumer policies into configurable checks only when reusable. | Consumer-specific policy remains outside Woof unless represented by documented configuration and checker behaviour. | Docs review passed. Focused configurable-policy tests passed: quality gates, outcome markers, docs drift, and preflight, 25 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 227 tests. | `docs(consumers): constrain policy generalisation` |

### Phase 7: Documentation And Evidence Polish

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| DOC-001 | Completed | Keep README as the entry map. | README links to architecture, research, ADR-001, this implementation plan, and examples without duplicating architecture detail. | Docs review passed. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 227 tests. | `docs(readme): align entry map` |
| DOC-002 | Completed | Keep architecture focused on design contract and current implementation boundary. | `docs/architecture.md` avoids live backlog content and points implementation sequencing here. | Docs review passed. `git diff --check` passed for touched docs. `just check` passed: Ruff lint, Ruff format check, and 227 tests. | `docs(architecture): point roadmap to implementation plan` |
| DOC-003 | Completed | Add concise examples for core safety behaviours. | Examples demonstrate graph-owned orchestration, second-LLM critique enforcement, manifest-verified commits, gate resolution, and E146 contract fidelity. | Dogfood `EPIC.md`, `plan.json`, `critique/*.md`, `epic.jsonl`, and `dispatch.jsonl` examples validated; `git diff --check` passed; `just check` passed: Ruff lint, Ruff format check, and 227 tests. | `docs(examples): demonstrate woof safety model` |

### Phase 8: Producer Execution Discipline

This phase codifies a tracer-bullet red-green-refactor rhythm inside the Stage-5 primary producer turn. The graph topology is unchanged: the producer remains a single subprocess that returns `executor_result.json`, the Stage-5 deterministic checks remain Check 1-9, and the commit transaction remains one-commit-per-story. The discipline lives in the producer prompt; a sibling reviewer finding catches the failure mode the discipline is designed to prevent (tests that assert data shape rather than the declared outcome). May land as a short commit series.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| PRD-001 | Completed | Codify tracer-bullet RGR rhythm in the primary producer prompt and add an assertion-first reviewer fidelity check. | `.claude/commands/wf/execute-story.md` instructs the producer to enumerate `story.satisfies[]` outcomes up front, write one assertion-bearing test per outcome before its implementation, run the configured quality command after each cycle, and run a refactor pass with tests as harness once all outcomes are GREEN; the prompt names the horizontal-slicing anti-pattern (all tests then all impl) and the imagined-behaviour fingerprint it produces. `playbooks/critique/story.md` documents a test-fingerprint finding category that separates behaviour-anchored assertions from data-structure-anchored ones with severity `minor`, accumulating into the Check 9 periodic-review valve. `docs/architecture.md` references the rhythm as the recommended producer-internal discipline at Stage 5. Graph topology, Check 1-9 behaviour, and commit transaction semantics are unchanged. | Focused prompt terminology test passed: 4 tests. `just check` passed: Ruff lint, Ruff format check, and 228 tests. | `docs(workflow): codify tracer-bullet producer rhythm` |

### Phase 9: Audit Reconstruction And Portability

This phase fills implementation gaps discovered after the Stage 1-5 graph and role-routing workstreams completed. Items stay narrow and must preserve ADR-002 portability constraints.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| AUD-001 | Completed | Implement the Claude transcript audit bundle helper promised by the architecture. | `just wf-audit-bundle <E<N>>` copies portable `~/.claude/projects/<project-slug>/<session>.jsonl` references from `.woof/epics/E<N>/dispatch.jsonl` into `.woof/epics/E<N>/audit/claude-code/`, reports copied and missing transcripts, and rejects non-portable transcript paths without depending on host-specific absolute paths. | Targeted audit bundle tests passed: 7 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 232 tests. | `feat(audit): bundle claude transcripts` |

### Phase 10: Contract Implementation Model

This phase records implementation-boundary clarifications discovered after the Stage 1-5 graph, role-routing, and audit-portability workstreams completed. Items stay narrow and must keep JSON Schema as the portable contract authority while reflecting the Python runtime model actually used by Woof.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| CIM-001 | Completed | Clarify the JSON Schema, Pydantic, and dataclass boundary in architecture docs. | README, ADR-001, and architecture docs state that Woof-owned durable artefact contracts are JSON Schema-governed; Pydantic is the Python runtime representation at schema and serialisation boundaries; dataclasses remain acceptable for trusted in-process records such as check outcomes, preflight findings, GitHub sync results, and audit summaries. No graph topology, schema, or runtime behaviour changes. | Representative Pydantic and dataclass source uses inspected. Docs review passed. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 232 tests. | `docs(architecture): clarify contract model boundaries` |

### Phase 11: Stage-5 Check Strictness

This phase removes the final bootstrap-era tolerance path now that every Stage-5 check runner exists. Items stay narrow and preserve the graph-owned Stage-5 verification contract: missing checker implementation is a blocker finding, not a soft pass.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| CHK-010 | Completed | Fail closed when a Stage-5 check runner is not implemented. | `woof check stage-5 --format json` emits a schema-valid blocker check entry and exits 1 when any registered runner raises `NotImplementedError`; the runner is included in `triggered_by`; the old bootstrap placeholder `ok=true` path is removed; docs state that unimplemented registry slots are blocker failures. | Focused Stage-5 check subcommand tests passed: 6 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 233 tests. | `fix(checks): fail closed on unimplemented runners` |

### Phase 12: Dispatch Audit Completeness

This phase tightens dispatch audit reconstruction after transcript bundling by making the durable event stream record the graph-owned artefacts loaded into each prompt.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| AUD-002 | Completed | Record prompt artefact references in dispatch events. | `woof dispatch` accepts explicit repo-relative artefact references, records them as `artefacts_loaded[]` on spawned and returned dispatch events plus adapter meta, rejects absolute or parent-traversal references, and graph dispatch call sites pass the stage/story artefacts they embed into prompts. | Focused dispatch and graph tests passed: 60 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 238 tests. | `feat(audit): record dispatch artefacts` |

### Phase 13: Dispatch Adapter Modularisation

This phase removes a structural follow-up left by the role-routing work: public adapter command construction is implemented, but the reusable dispatch adapter core still lives inside the monolithic CLI module. Items must preserve ADR-002 portability constraints and remain behaviour-preserving unless the row explicitly states a contract change.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| DPA-001 | Completed | Move dispatch adapter core into the dedicated dispatcher module. | `src/woof/cli/dispatcher.py` owns role-route resolution, public `claude` / `codex` argv construction, Claude MCP JSON rendering, token-output parsing, artefact reference normalisation, and dispatch execution helpers; `src/woof/cli/main.py` only wires the CLI command and imports the adapter boundary; preflight imports route helpers from the dispatcher module rather than the monolithic CLI. Runtime command output and audit JSONL shape remain unchanged. | Focused dispatch and preflight tests passed: 37 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 238 tests. | `refactor(dispatch): extract adapter core` |

### Phase 14: Workflow Runtime Prerequisites

This phase tightens the always-online GitHub boundary after the graph, role routing, and dispatch-audit workstreams completed. Items must preserve the existing GitHub sync contract: no offline fallback, auth or network failure fails loud, and graph-owned workflow state is not mutated after a failed startup guard.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| WFR-001 | Completed | Enforce GitHub runtime reachability before `woof wf` graph or gate work. | Every `woof wf` invocation loads `.woof/prerequisites.toml`, verifies `gh api /rate_limit` succeeds before local graph or gate mutation, fails loud on missing auth/unreachable API or exhausted core quota, and keeps cold-start/new/sync behaviour unchanged after the guard passes. | Focused `wf` GitHub sync, graph CLI, render-epic, and preflight tests passed: 64 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 240 tests. | `fix(wf): enforce github runtime reachability` |

### Phase 15: Event Schema Contract Tightening

This phase aligns the durable JSONL event schema with events already emitted by the graph and GitHub sync code. Items must preserve existing audit logs while ensuring newly emitted workflow events validate against `jsonl-events.schema.json`.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| JEV-001 | Completed | Align JSONL event schema with graph-emitted workflow events. | `jsonl-events.schema.json` accepts `current_epic_selected`, `breakdown_planned`, and `transaction_manifest_verified`; focused validation fixtures cover the emitted event vocabulary used by `woof wf new`, Stage 3 breakdown planning, and commit transaction verification. | Focused JSONL validation tests passed: 3 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 240 tests. | `fix(schema): cover emitted jsonl events` |

### Phase 16: Preflight Bootstrap Contract

This phase tightens the first-run consumer configuration path after runtime preflight checks landed. Items must preserve the fail-loud startup boundary and avoid host-specific assumptions.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| PFT-001 | Completed | Make the missing prerequisites template match the documented bootstrap contract. | `woof preflight` with `.woof/` present but no `.woof/prerequisites.toml` exits non-zero and prints a starter template containing explicit `<replace>` placeholders for project-specific values. | Focused preflight CLI test passed: 1 test. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 240 tests. | `fix(preflight): show replacement placeholders in template` |

### Phase 17: Dispatch Audit Hardening

This phase tightens dispatch audit file safety after prompt-artefact recording and transcript bundling landed. Items must preserve ADR-002 portability constraints and keep durable audit references repo-relative or portable home-relative.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| AUD-003 | Completed | Make dispatch audit file stems collision-resistant and path-safe. | Dispatch-created `.prompt`, `.output`, `.stderr`, and `.meta` files use a stem that cannot collide across concurrent same-epic same-role dispatch invocations and cannot derive path separators or unsafe filename characters from role text; Codex audit references remain repo-relative and JSONL-valid. | Focused dispatch tests passed: 29 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 242 tests. | `fix(dispatch): harden audit file stems` |

### Phase 18: Workflow Locking

This phase implements the per-epic workflow lock promised by the architecture after graph execution, GitHub runtime checks, and audit-event schema tightening landed. Items must preserve the graph-owned mutation boundary: only one `woof wf --epic <N>` graph execution may mutate a given epic at a time, live locks fail loud, and recognised stale locks are removed with durable audit evidence.

| ID | Status | Work item | Observable outcomes | Validation | Commit |
|---|---|---|---|---|---|
| WFL-001 | Completed | Enforce the graph workflow lockfile. | `run_graph` acquires `.woof/epics/E<N>/.wf.lock` for the duration of graph execution, refuses to run when a same-host live lock exists, removes same-host stale locks whose recorded process is gone, writes a JSONL-valid `wf_lock_stale_removed` audit event, and releases only the lock it owns. | Focused graph lock tests passed: 36 tests. Focused JSONL schema validation tests passed: 48 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 245 tests. | `fix(graph): enforce workflow lockfile` |

## Release-Closure Audit

Audit date: 2026-05-19.

Purpose: close the gap between documented architecture promises and current implementation without weakening ADR-001 or ADR-002. The accepted architecture remains graph-led. GPT-5.5 remains the preferred primary producer route. Claude Opus 4.7 at `max` effort remains the preferred reviewer route. Reviewer blockers open human gates; there are no model-to-model debate loops.

This audit read the public docs, ADRs, schemas, CLI help, graph runtime, dispatch adapter, preflight, check runners, gate writer, GitHub sync helpers, audit helpers, tests, packaging metadata, and an isolated wheel install probe. Historical phase rows above remain useful evidence, but the active continuation backlog is the workstream table in this section.

### Gap Table

| ID | Architecture / documented claim | Implementation evidence | Test / schema evidence | Status | Risk | Closure item or workstream |
|---|---|---|---|---|---|---|
| GAP-001 | Woof is graph-led; model invocations are producer/reviewer nodes, not workflow orchestrators. | `src/woof/graph/runner.py` drives `next_node`; `src/woof/graph/nodes.py` owns dispatch, verification, gates, and commit. `src/woof/cli/dispatcher.py` builds public `claude` / `codex` commands from semantic roles. | `tests/unit/test_graph.py` covers graph sequencing and role dispatch; `tests/unit/test_dispatch.py` covers raw Claude/Codex argv and role routing. | implemented | Low. Keep as invariant while closing other gaps. | No closure item. Preserve in all workstreams. |
| GAP-002 | Reviewer blockers open human gates, non-blocking findings require primary dispositions, and there are no model-to-model debate loops. | `review_disposition_node` opens a gate on `severity=blocker`; dispatches `primary` only for `info` / `minor` dispositions. | `tests/unit/test_graph.py::test_reviewer_blocker_opens_gate_without_primary_debate`; `tests/unit/test_check_6_critique_blocker.py`; `schemas/disposition.schema.json`. | implemented | Low. Regression would undermine ADR-002 safety. | No closure item. Preserve in Workstream RC-1. |
| GAP-003 | Structured gate decisions drive graph state after human review. | `_resolve_gate()` applies deterministic effects before deleting `gate.md`: plan revisions remove downstream plan artefacts for re-entry, story approvals clear stale failed `check-result.json`, split/scope revisions clear stale verification state, abandoned stories advance the plan, and `plan_gate_resolved()` only treats approved non-conflict plan gates as Stage-5-unblocking. | Focused graph tests cover `revise_plan`, stale failed-check approval, `split_story`, and `abandon_story` re-entry behaviour. `just check` passed: Ruff lint, Ruff format check, and 253 tests. | implemented | Low. Continue preserving event-order-sensitive plan gate semantics. | RC-1 completed. |
| GAP-004 | Human gates surface the Context block, findings, primary position, and reviewer position through the operator surface. | `write_gate()`, `write_gate_from_check_result()`, and `write_gate_for_trigger()` ensure every generated gate body has `## Context`, `## Findings`, `## Primary position`, and `## Reviewer position`. `human_review_node` now surfaces the gate body in operator output. | Focused gate writer and graph tests passed. `just check` passed: Ruff lint, Ruff format check, and 253 tests. | implemented | Low. Future gate types must preserve the four-section body. | RC-1 completed. |
| GAP-005 | GitHub is the epic authority; no local-only epics are valid. | Existing local `.woof/epics/E<N>/` directories now fetch the GitHub issue and require `.last-sync` with the same issue number before any graph or gate mutation. Missing issues, pull requests, missing `.last-sync`, or mismatched issue numbers fail loud. | Focused GitHub sync tests cover missing `.last-sync` and missing issue authority. `just check` passed: Ruff lint, Ruff format check, and 253 tests. | implemented | Low. Local-only epic mutation is now rejected at `woof wf` startup. | RC-1 completed. |
| GAP-006 | GitHub sync conflict gates offer keep-local, accept-remote, or hand-merge resolution. | `woof wf --resolve` accepts `keep_local`, `accept_remote`, and `hand_merge` for `github_sync_conflict` gates. Conflict resolution updates `.last-sync` to the current remote baseline; `accept_remote` also rewrites local `EPIC.md` from the managed GitHub body. Non-conflict gates reject conflict-only decisions. | Focused render/GitHub sync tests cover `keep_local` and `accept_remote`; schema enums are aligned. `just check` passed: Ruff lint, Ruff format check, and 253 tests. | implemented | Low. `hand_merge` remains an operator-authored local merge followed by baseline update and retry. | RC-1 completed. |
| GAP-007 | Stage 1 Discovery synthesis has mechanically checked boundary invariants, including non-empty problem framing and ID + deferral reason for every open question. | `src/woof/graph/planning_contracts.py` requires `CONCEPT.md` to include non-empty `## Problem Framing`; parses `OPEN_QUESTIONS.md`; accepts `No open questions.` or active `## OQ<n> - ...` entries with `Deferral reason:` / `Decision needed by:`. `discovery_synthesis_node` and `epic_definition_node` fail loud before Stage 2 when the contract is malformed. | Focused graph tests cover missing problem framing, missing open-question deferral, and reconstitution with malformed existing synthesis. `just check` passed: Ruff lint, Ruff format check, and 262 tests. | implemented | Low. Keep prompt examples and parser syntax aligned. | RC-2 completed. |
| GAP-008 | Stage 2 Definition resolves or explicitly carries forward every discovery open question and enforces Definition surface invariants. | `epic.schema.json` now models unresolved `open_questions[]` as `{id, question, deferral_reason}` and resolved questions as `resolved_open_questions[]`; `epic_definition_node` compares both sets with active Discovery `OQ<n>` IDs before closing Definition. GitHub cold-start migrates legacy open-question bullets to structured `OQ<n>` entries. | Focused graph and GitHub sync tests cover missing resolution, accepted resolution, and legacy issue migration. `just check` passed: Ruff lint, Ruff format check, and 262 tests. | implemented | Low. Unknown or uncovered discovery questions now fail before Breakdown. | RC-2 completed. |
| GAP-009 | Stage 3 Breakdown invariants are enforced before the mandatory plan gate: outcome coverage, CD coverage, dependency closure, no invalid status, and story-scope discipline. | `stage3_plan_contract_failures()` reuses Check 5 cross-reference logic before plan critique and plan gate; it adds pre-gate status, topological-order, and duplicate pathspec checks. `breakdown_planning_node`, `plan_critique_node`, and `plan_gate_open_node` all fail loud on invalid plan contracts. | Focused graph and Check 5 tests cover unknown outcomes before critique, dependency order, duplicate pathspecs, and pre-gate pending statuses. `just check` passed: Ruff lint, Ruff format check, and 262 tests. | implemented | Low. Stage-5 Check 5 remains the repeated commit-time guard. | RC-2 completed. |
| GAP-010 | Stage 1-4 planning node output contracts are schema-governed by `planning-node-output.schema.json`. | `planning-node-output.schema.json` is now the planning-node-restricted view of the runtime `node-output` shape emitted by `woof wf --format json`; the non-emitted `produced` field and divergent validation-summary shape were retired. Architecture docs describe the merged contract. | `tests/unit/test_validate.py` fixture-tests the merged planning-node-output contract; graph tests continue validating runtime outputs against `node-output.schema.json`. `just check` passed: Ruff lint, Ruff format check, and 262 tests. | implemented | Low. RC-7 can still polish release docs, but the contract drift is closed. | RC-2 completed. |
| GAP-011 | Stage 5 Check 4 verifies native contract conformance: Schemathesis for OpenAPI, Pydantic model resolution, JSON Schema self-validation and fixtures where present. | RC-3 narrowed Check 4 to native reference resolution. CC-006 adds bounded native conformance without a broad framework: OpenAPI refs under `#/paths` must point at operation-shaped objects with `responses`, and JSON Schema top-level `examples[]` validate under `ajv-cli` when present. Pydantic remains import + `BaseModel` resolution. Broader generated HTTP traffic, external fixture suites, and Pydantic fixture validation remain deferred. | Focused Check 4/check-cd tests cover broken-openapi artefact-path surfacing, ajv-missing preflight pointer, OpenAPI path-item rejection, and JSON Schema invalid-example rejection. `just check` passed: Ruff lint, Ruff format check, and 338 tests. | partial | Low. The bounded native checks improve contract evidence while quality-gate commands remain the active behavioural coverage. | CC-006 completed; broader conformance deferred. |
| GAP-012 | Stage 5 scope and transaction checks agree on durable `.woof` paths and story pathspec semantics. | `src/woof/graph/pathspec.py` is the shared git-pathspec engine; Check 3, Check 7, and the transaction manifest all evaluate `story.paths[]` through it. Check 3's allowed `.woof` set now includes `dispositions/story-S<k>.md`. fnmatch is no longer used for pathspec matching. | Focused regression tests cover staged disposition acceptance and recursive `:(glob)src/**/*.py` semantics for Check 3, Check 7, and `build_story_manifest`. | implemented | Low. Pathspec evaluation is uniform; fnmatch divergence is closed. | RC-3 completed. |
| GAP-013 | Check 1 quality gates all exit 0 to pass. | `schemas/quality-gates.schema.json` top-level and per-command descriptions now describe blocking vs advisory (`blocking = false`) behaviour, matching the runner. Architecture Check 1 row records the same distinction. | Existing Check 1 runner tests continue to cover pass/fail/timeout/missing-command cases; schema validation against `.woof/quality-gates.toml` passes after the description edit. | implemented | Low. Schema, runner, and architecture all describe the same blocking semantics. | RC-3 completed. |
| GAP-014 | Structured artefacts are atomically written and JSONL logs are durable under concurrent driver/subprocess activity. | `write_plan()` uses tmp + replace. The per-epic `.wf.lock` (`src/woof/graph/lock.py`) serialises every `run_graph` invocation that mutates `.woof/epics/E<N>/`. Only the lock-holding parent appends to `epic.jsonl` (`transitions.append_epic_event`, `gate/write._append_jsonl`, `cli/github._append_jsonl`); only the synchronous in-process `woof dispatch` Python adapter appends to `dispatch.jsonl` (`cli/dispatcher.append_jsonl`); the dispatched LLM subprocess does not write either log. JSONL appends use Python text-mode `open("a", ...)` plus a single short `write(json + "\n")`, which maps to one `O_APPEND` syscall (POSIX-atomic at line size). The architecture line at `docs/architecture.md` ("Atomic writes" paragraph) has been narrowed to describe these actual guarantees rather than an unimplemented "advisory file lock". | Focused validation: `tests/unit/test_graph.py::test_run_graph_refuses_live_workflow_lock`, `tests/unit/test_graph.py::test_run_graph_removes_stale_workflow_lock_and_records_event`, `tests/unit/test_graph.py::test_wf_reports_live_workflow_lock`, `tests/unit/test_validate.py` `wf_lock_stale_removed` event fixture. | implemented | Low. The race the original line described (driver vs story subprocess race on JSONL appends) is not reachable in the implemented topology. A narrow theoretical write surface outside `run_graph` exists for `--resolve` and post-run `epic_completed` writes; it is gated by `gate.md` presence and `append_epic_event_once` deduplication and is recorded separately in the RC-4 narrowing notes. | RC-4 completed. |
| GAP-015 | Token usage is logged for subprocesses and in-session stage transitions. | Dispatch records token fields when the adapter parser surfaces them; the Python graph itself does not spend tokens, so there is no stage-transition emitter. The architecture "Token usage logging" paragraph in `docs/architecture.md` has been narrowed to state that subprocess dispatch records token usage, and that the `token_usage` enum in `schemas/jsonl-events.schema.json` is reserved for a future driver mode that runs an LLM in the same process. | `schemas/jsonl-events.schema.json` retains the reserved `token_usage` enum; dispatch token parser tests continue to cover subprocess parsing. | docs-drift (narrowed) | Low. Architecture and schema now describe the same emission surface. | RC-7 completed. |
| GAP-016 | Preflight is the startup infrastructure check for public CLIs, auth, generated MCP config, GitHub, model/effort routes, language tooling, host/server checks, and runtime cache. | `preflight.py` validates binaries, config schemas, route model/effort presence, Claude MCP JSON construction, GitHub rate/repo, quality-gate command resolution, language tooling, host and server checks. Adapter credential markers are now checked in the runtime cache tier: each configured `primary` / `reviewer` route is required to have either its API-key env var (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) set or its CLI-managed credential file present (Claude `~/.claude/.credentials.json` honouring `CLAUDE_CONFIG_DIR`; Codex `~/.codex/auth.json` honouring `CODEX_HOME`). Live API auth state and model availability are documented as validated at first dispatch because probing them would burn tokens and couple preflight to model availability windows. | `tests/unit/test_preflight.py` covers binary/config/route/GitHub/language/host/server cases and the new env-var-pass, credential-file-pass, and missing-marker-fail paths for Claude and Codex routes. | implemented | Low. Token-revocation or model-retirement still surfaces only at dispatch, by design and documented. | RC-5 completed. |
| GAP-017 | Cartography artefacts under `.woof/codebase/` are regenerated by the Woof-managed post-commit hook. | Architecture, README, and `docs/consumers.md` now state that cartography substance is consumer-owned: the Woof-managed post-commit hook block invokes `./scripts/refresh-cartography` when present, and the block is a no-op when absent. `_check_cartography_script` in `preflight.py` flags a present-but-non-regular or non-executable script so a broken script fails loud at preflight rather than silently no-opping in the hook. No Woof-side cartography generator is added; the architectural promise has been narrowed instead. | `tests/unit/test_preflight.py` covers non-executable detection, post-fix recovery, and absent-script no-op cases. | implemented (docs narrowed) | Low. Consumers without a script lose cartography artefacts by design; the post-commit hook is unchanged. | RC-5 completed. |
| GAP-018 | Consumer checkouts can bootstrap the required `.woof/` config and gitignore policy without vendoring Woof or relying on Ryan-local assumptions. | New `woof init` command (`src/woof/cli/init.py`, wired in `src/woof/cli/main.py`) creates `.woof/prerequisites.toml`, `.woof/agents.toml`, `.woof/quality-gates.toml`, and `.woof/test-markers.toml` with explicit `<replace>` placeholders, and inserts a fenced `# >>> woof` block into the repo `.gitignore` covering `.current-epic`, per-epic locks/last-sync, audit raw overflow, cartography artefacts, and `.preflight-*` caches. `--force` re-writes existing TOMLs, `--with-docs-paths` adds `.woof/docs-paths.toml`, the gitignore block is updated in place rather than duplicated, and the command prints concrete next-steps including `claude /login`, `codex login`, `woof preflight`, and `woof hooks install`. | `tests/unit/test_init.py` covers creation, idempotence, `--force`, optional docs-paths scaffold, existing-gitignore preservation, managed-block replacement, schema validation of scaffolded TOMLs, help-text exposure, next-steps output, and no-argument current-dir behaviour. | implemented | Low. Re-runs preserve consumer edits unless `--force` is passed; placeholder `<replace>` strings make an unedited bootstrap fail loud at preflight or first command resolution. | RC-5 completed. |
| GAP-019 | Woof can run as an installed package, not only from a source checkout. | `pyproject.toml` builds a wheel with `schemas/`, `playbooks/`, `languages/`, and `bin/woof`. `src/woof/__main__.py` makes `python -m woof` the portable entry. `src/woof/graph/nodes.py` `_woof_subprocess_argv()` returns `[sys.executable, "-m", "woof"]` and `_woof_subprocess_env()` prepends `PYTHONPATH` covering source-checkout `src/` or wheel-install layout so the child Python imports the `woof` package regardless of whether the parent was launched by the source-checkout `uv run --script` wrapper. The five graph subprocess callsites (`_validate_epic`, `_validate_plan`, `_validate_plan_critique`, `_run_dispatch`, `verification_node`) all use this helper pair. | `tests/unit/test_packaging_install.py` asserts the active-interpreter argv and PYTHONPATH/WOOF_TOOL_ROOT env, runs `python -m woof --help` against the test interpreter, and exercises an isolated wheel build/install smoke that resolves `tool_root()` schemas, playbooks, and language registries from the wheel. `tests/unit/test_graph.py::test_dispatch_helper_uses_role_route_without_provider_target` asserts the new argv shape and env propagation through `_run_dispatch`. | implemented | Low. Installed-wheel graph subprocesses now use the active Python module entry; the source-checkout wrapper is no longer on the graph path. | RC-6 completed. |
| GAP-020 | The docs and development surface match released commands and recipes. | The architecture "Version policy" paragraph no longer references the non-existent `just upgrade-prereqs` recipe; operators upgrade prerequisites manually using the install commands `woof preflight` prints. README has a new "Installed Package Smoke" section that documents the wheel build/install path exercised by `tests/unit/test_packaging_install.py` and the `python -m woof --help` entry point. README "Status" reflects RC-6 closed and RC-7 as the final release-closure workstream, and the README source map names `src/woof/__main__.py` as the install-safe re-entry boundary. | `tests/unit/test_packaging_install.py` continues to build and install a wheel into an isolated venv and run `python -m woof --help`; ledger and architecture references match `just --list` output. | implemented (docs aligned) | Low. Released docs now describe commands that exist. | RC-7 completed. |
| GAP-021 | Empty-diff stories open a human review gate during dogfood; later auto-completion can be considered only after evidence. | `gate_open_node` opens `empty_diff_review`; `plan.schema.json` documents the current operator policy. | `tests/unit/test_graph.py::test_empty_diff_executor_result_opens_review_gate`. | intentionally deferred | Low. Current conservative gate is architecture-compatible. | No current closure. Revisit only with empirical release evidence. |
| GAP-022 | Model-to-model debate loops should not be added for reviewer blockers. | ADR-002 rejects automatic model debate. `review_disposition_node` gates blockers instead of dispatching another model. | `tests/unit/test_graph.py::test_reviewer_blocker_opens_gate_without_primary_debate`. | rejected | Critical if reintroduced. It would violate ADR-002. | No closure item. Preserve rejection in all workstreams. |

### Release-Closure Workstreams

Future work uses these workstreams instead of adding one micro-item per gap. A session may complete several child gaps and use multiple conventional commits when that improves review. Mark a workstream `Completed` only when its child gaps are closed or explicitly split/deferred with rationale, targeted validation and `just check` have passed or a blocker is recorded, changes are pushed, and GitHub CI is green.

| Workstream | Status | Child gaps | Closure outcomes | Validation expectations | Commit |
|---|---|---|---|---|---|
| RC-1: Gate Resolution And GitHub State Safety | Completed | GAP-003, GAP-004, GAP-005, GAP-006 | Gate decisions now have deterministic state effects; stale failed check results cannot reopen approved gates; conflict gates support `keep_local`, `accept_remote`, and `hand_merge`; existing local epics verify GitHub issue authority and `.last-sync`; operator output surfaces complete gate context and role positions. | Focused validation passed: `uv run pytest tests/unit/test_graph.py tests/unit/test_render_epic.py tests/unit/test_wf_github_sync.py tests/unit/test_gate_write.py tests/unit/test_validate.py` (117 tests). `just test` passed: 253 tests. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 253 tests. | `fix(graph): close gate resolution semantics` |
| RC-2: Planning Contract Enforcement | Completed | GAP-007, GAP-008, GAP-009, GAP-010 | Discovery `CONCEPT.md` problem-framing and `OPEN_QUESTIONS.md` ID/deferral structure are enforced; Definition must resolve or explicitly carry forward every active Discovery open question; Stage 3 runs cross-artefact plan invariants before plan critique and plan gate; `planning-node-output` is merged with the runtime `node-output` contract instead of exposing a non-emitted `produced` shape. | Focused validation passed: `uv run pytest tests/unit/test_wf_github_sync.py tests/unit/test_graph.py tests/unit/test_check_5_plan_crossrefs.py tests/unit/test_validate.py tests/unit/test_render_epic.py` (131 tests). `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 262 tests. | `fix(graph): enforce planning contracts before gate` |
| RC-3: Stage-5 Verification And Contract Fidelity | Completed | GAP-011, GAP-012, GAP-013 | Docs/schema scope of Check 4 is narrowed to reference resolution (OpenAPI parse + JSON pointer, Pydantic BaseModel import, ajv compile) with conformance testing recorded as a deferred enhancement; Check 4 failure paths now include the resolved artefact source path; missing ajv is surfaced as a preflight pointer rather than an in-band finding. `src/woof/graph/pathspec.py` is the single git-pathspec engine used by Check 3, Check 7, and the transaction manifest; fnmatch is gone, and Check 3's durable `.woof` allow-list includes `dispositions/story-S<k>.md`. `schemas/quality-gates.schema.json` and the architecture Check 1 row describe blocking vs advisory (`blocking = false`) gate semantics, matching the runner. | Focused validation passed: `uv run pytest tests/unit/test_check_3_scope.py tests/unit/test_check_7_commit_transaction.py tests/unit/test_check_4_contract_refs.py tests/unit/test_check_cd.py tests/unit/test_check_stage_5_subcommand.py tests/unit/test_graph.py` covers staged disposition acceptance, recursive `:(glob)src/**/*.py` semantics on Check 3/Check 7/manifest, broken-openapi artefact-path surfacing, and ajv-missing preflight pointer. `just woof validate --schema prerequisites .woof/prerequisites.toml` and `just woof validate --schema quality-gates .woof/quality-gates.toml` passed. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 268 tests. | `fix(checks): align verification contracts` |
| RC-4: Audit/Event Durability | Completed | GAP-014 | The 2026-05-19 RC-4 verification confirmed the race the architecture line described ("driver and story subprocess race" on JSONL appends) is unreachable: WFL-001's per-epic `.wf.lock` serialises `run_graph` invocations; only the lock-holding parent writes `epic.jsonl`; only the synchronous in-process `woof dispatch` Python adapter writes `dispatch.jsonl`; the dispatched LLM subprocess writes neither. Closure was a docs-narrowing, not a new helper: `docs/architecture.md` "Atomic writes" paragraph now describes the actual lock-and-syscall guarantees. No `_locked_append_jsonl` helper was added; if a future driver mode reintroduces a real concurrent-writer surface, GAP-014 reopens. | Focused validation passed: `uv run pytest tests/unit/test_graph.py::test_run_graph_refuses_live_workflow_lock tests/unit/test_graph.py::test_run_graph_removes_stale_workflow_lock_and_records_event tests/unit/test_graph.py::test_wf_reports_live_workflow_lock tests/unit/test_validate.py` (lock + JSONL event fixtures). `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 268 tests. | `docs(architecture): narrow jsonl append durability claim` |
| RC-5: Preflight, Bootstrap, And Cartography | Completed | GAP-016, GAP-017, GAP-018 | Preflight now probes Claude/Codex credential markers (env-key or CLI-managed credential file, both override paths honoured) and surfaces a fail-loud finding when neither is present; the documented caveat is that live API auth state and model availability are validated only at first dispatch because probing them would burn tokens. Cartography is demoted to consumer-owned: the architecture, README, and `docs/consumers.md` now describe `./scripts/refresh-cartography` as a consumer responsibility, and preflight flags a present-but-non-executable script. `woof init` scaffolds `.woof/{prerequisites,agents,quality-gates,test-markers}.toml` with `<replace>` placeholders plus an idempotent `# >>> woof` block in the repository `.gitignore`; `--force` re-writes existing TOMLs and `--with-docs-paths` adds the optional Stage 5 Check 8 mappings file. The first-run walkthrough now lives in `docs/consumers.md`. | Focused validation passed: `uv run pytest tests/unit/test_init.py tests/unit/test_preflight.py tests/unit/test_hooks.py tests/unit/test_validate.py` (79 tests). `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 279 tests. | `feat(preflight): close consumer bootstrap checks` |
| RC-6: Packaging And Install Portability | Completed | GAP-019 | Installed-wheel graph execution works without the source-checkout `bin/woof` wrapper assumption. `src/woof/__main__.py` exposes `python -m woof` as the portable module entry. `src/woof/graph/nodes.py` `_woof_subprocess_argv()` returns `[sys.executable, "-m", "woof"]` and `_woof_subprocess_env()` injects `PYTHONPATH` plus `WOOF_TOOL_ROOT` so the child Python imports `woof` from either the source `src/` directory or the wheel-install layout. The five graph subprocess callsites in `_validate_epic`, `_validate_plan`, `_validate_plan_critique`, `_run_dispatch`, and `verification_node` use the new helper pair. `tests/unit/test_packaging_install.py` ships an isolated wheel build/install smoke that runs `python -m woof --help` and resolves `tool_root()` schemas, playbooks, and language registries from the bundled wheel artefact. `docs/architecture.md` (Graph execution lifecycle) describes the install-safe re-entry rule. | Focused validation passed: `uv run pytest tests/unit/test_packaging_install.py tests/unit/test_graph.py tests/unit/test_dispatch.py` (80 tests). `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 288 tests. | `fix(packaging): make graph subprocesses install-safe` |
| RC-7: Release Evidence And Docs/Schema Drift Cleanup | Completed | GAP-010 (residue), GAP-013 (residue), GAP-015 (architecture line narrowing), GAP-020 (`just upgrade-prereqs` removal, installed-package smoke documented in README) | The `docs/architecture.md` "Token usage logging" paragraph now states that subprocess dispatch records token usage and that the Python graph itself does not spend tokens; the `token_usage` enum in `schemas/jsonl-events.schema.json` is described as reserved for a future driver mode. The "Version policy" paragraph no longer references the non-existent `just upgrade-prereqs` recipe; operators upgrade prerequisites manually using the install commands `woof preflight` prints. README has a new "Installed Package Smoke" section documenting the wheel build/install path exercised by `tests/unit/test_packaging_install.py` and the `python -m woof --help` entry; README "Status" now reflects RC-6 closed and RC-7 as the final release-closure workstream; the source map names `src/woof/__main__.py` as the install-safe re-entry boundary. GAP-010 and GAP-013 closure residue was spot-checked: `schemas/planning-node-output.schema.json` remains wired through `src/woof/cli/main.py`; `schemas/quality-gates.schema.json` and the architecture Check 1 row continue to describe blocking vs advisory (`blocking = false`) gate semantics. | `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 288 tests. | `docs(release): align closure evidence` |

### Release-Closure Cadence

- Future sessions pick the first `Ready` release-closure workstream by order. Do not add a tiny standalone row unless a workstream must be split at a real risky boundary.
- A session chooses a coherent workstream slice and may close multiple child gaps before committing.
- Multiple conventional commits are allowed inside one workstream when they make review clearer, for example one code/test commit and one docs/schema commit.
- Stop only at a real external blocker, a risky design boundary that needs Ryan's decision, or after completing the chosen workstream slice.
- Keep code, schemas, tests, README/architecture/ADR references, and this ledger aligned.
- Run targeted validation while developing, then `git diff --check`, `just check`, normal hooks, push, and GitHub CI monitoring.
- Preserve ADR-001 and ADR-002 invariants unless a new audit proves an accepted decision is obsolete and a new ADR is written.

Audit turn commit: `docs(workflow): add release closure audit workstreams`.

## Phase B: Portability For Arbitrary Consumers

Audit date: 2026-05-19.

Purpose: close the gap between "release the current architecture as implemented" (Phases 0-18 plus RC-1..RC-7) and "any project, anyone's, anywhere can install Woof and use it against their own repo." Phase A closes the gap between architecture promises and current implementation. Phase B closes the gap between current architecture and a substrate usable by a stranger without Ryan-local agent skills or GitHub-only assumptions.

Phase A (RC-1..RC-7) and Phase B (RC-B1..RC-B5) are both historically validation-complete as of 2026-05-20. Phase B work preserved ADR-001 and ADR-002 invariants and introduced ADR-003 (issue-tracker abstraction). That closure statement is no longer the active planning state; the 2026-05-21 course correction below opened a new backlog. RC-B5, the deep audit report, and CC-001 were landed together in the course-correction commit because their documentation surfaces overlap.

### Phase B Hidden Gaps

| ID | Documented or implied claim | Implementation evidence | Status | Risk |
|---|---|---|---|---|
| BHID-001 | Stage 1 Discovery produces upstream artefacts in `research/`, `thinking/`, `brainstorm/`, `inputs/` before synthesis (`docs/architecture.md:54-69`). | Only `discovery_synthesis_node` and `epic_definition_node` exist; no graph nodes feed `research/`, `thinking/`, or `brainstorm/`. 21 building-block playbooks live under `playbooks/discovery/research/` and `playbooks/discovery/consider/` but are not referenced by any graph code. They are not portable as written: they use Claude Code's interactive `AskUserQuestion` tool (`claude -p` non-interactive dispatch cannot use it), write to `artifacts/research/` instead of `.woof/epics/E<N>/discovery/<bucket>/`, and carry Claude-Code slash-command frontmatter unused by Woof dispatch. | implemented (RC-B1, 2026-05-20) | High for portability. A stranger running `woof wf new "<spark>"` against their own repo without Ryan's `~/.claude/plugins/marketplaces/taches-cc-resources/` ecosystem gets thin one-shot synthesis from the spark alone. |
| BHID-002 | Woof is project-agnostic and usable against arbitrary consumer repos. | GitHub coupling is at architecture-principle level (`docs/architecture.md:184-187`: "Epic IDs. Always the gh issue number. `E<N>` ≡ gh issue `#<N>`. No local-only epics; every epic has a gh issue."). `src/woof/cli/github.py` (1192 lines) is imported directly throughout the codebase; there is no `Tracker` interface, no `trackers.toml`, no GitHubTrackerAdapter. `schemas/prerequisites.schema.json` requires `[github]`. `schemas/jsonl-events.schema.json` has `github_synced`/`github_sync_conflict` as first-class enum values. `schemas/gate.schema.json` has `github_sync_conflict` in `triggered_by`. Five schemas describe `epic_id` as a GitHub issue number. | implemented (RC-B2, 2026-05-20) | High for portability. Linear / Jira / Plane / Forgejo / local-file consumers cannot use Woof at all. |
| BHID-003 | `woof init` or equivalent produces a complete consumer bootstrap (`docs/architecture.md:714`). | Preflight emits `prerequisites.toml` template when missing. `agents.toml` template is suggestion text only (`preflight.py:567`); no command writes the file. No `woof init` command exists. Other configs (`quality-gates.toml`, `test-markers.toml`, `docs-paths.toml`), the required `.gitignore` entries, and the absent default `scripts/refresh-cartography` script are all manual. | implemented (RC-B3, 2026-05-20) | High for portability. First-consumer setup currently requires reading the architecture doc and hand-assembling at least four config files. |
| BHID-004 | `bin/woof` is portable across consumer environments. | Shebang is `#!/usr/bin/env -S uv run --script` with inline metadata that does not declare `woof` itself. A consumer using pip/poetry/conda without `uv` on PATH cannot run the source-checkout wrapper. `prerequisites.toml` does not declare `uv` as `[infra]`. This overlaps with GAP-019 (RC-6) which fixes the same root cause: graph subprocesses should invoke `sys.executable -m woof` instead of `tool_root()/bin/woof`. | partial | Medium for portability. Addressed by RC-6 if the graph subprocess fix lands; orphan otherwise. |

### Phase B Workstreams

| Workstream | Status | Child gaps | Closure outcomes | Validation expectations | Commit |
|---|---|---|---|---|---|
| RC-B1: Stage 1 Producer Skill Bundling | Completed | BHID-001 | Stage 1 Discovery now runs four graph producer nodes in order: `discovery_research`, `discovery_thinking`, `discovery_brainstorm`, then `discovery_synthesis`. The three bucket nodes dispatch the primary producer to populate `.woof/epics/E<N>/discovery/{research,thinking,brainstorm}/`; `next_node` walks the buckets in order before synthesis, file-presence drives transitions, and each bucket emits a `discovery_bucket_explored` epic event. `playbooks/discovery/{research,thinking,brainstorm}.md` are the graph-owned bucket producer prompts; the research and thinking nodes embed their building-block playbook text directly in the prompt so a consumer without Woof-author-local agent skills runs Stage 1 end-to-end. The 20 building-block playbooks under `playbooks/discovery/research/` (8) and `playbooks/discovery/consider/` (12) - the audit's "21" was a miscount - are rewritten for non-interactive dispatch: `type: discovery-playbook` frontmatter, no `AskUserQuestion`/intake gates/`$ARGUMENTS`, output directed at `.woof/epics/E<N>/discovery/<bucket>/`. `ask-me-questions.md` is documented as a human-operator intake aid, not a dispatched playbook. Schemas `node-input`, `node-output`, `planning-node-input` (with `discovery_bucket_input`), `planning-node-output`, and `jsonl-events` (with the `discovery_bucket_explored` event and `bucket` field) carry the new node types. | Focused validation passed: `uv run pytest tests/unit/test_graph.py tests/unit/test_validate.py tests/unit/test_prompt_role_terminology.py` (110 tests, including new bucket-node, transition, and playbook-portability tests). `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 299 tests. | `feat(discovery): bundle stage 1 producer skills` |
| RC-B2: Issue-Tracker Abstraction | Completed | BHID-002 | ADR-003 records the issue-tracker boundary. `src/woof/cli/github.py` (1192 lines) was removed; the abstraction lives in a new `src/woof/trackers/` package: `base.py` (the `Tracker` protocol, `TrackerError`, frozen result records, the conflict trigger/decision constants, and shared filesystem/hash helpers), `epic_body.py` (tracker-agnostic `EPIC.md`<->managed-body rendering and parsing), `github.py` (`GitHubTracker`), `local.py` (`LocalTracker`), and `__init__.py` (the `resolve_tracker` factory). The `Tracker` protocol covers `assert_runtime_reachable`, `create_epic`, `fetch_epic`, `assert_epic_authority`, `has_sync_state`, `push_epic_definition`, `push_plan_summary`, `complete_epic`, and `resolve_conflict`; conflict detection is intrinsic to the push operations rather than a standalone `detect_conflict` method (a standalone form would double the per-push tracker fetch - the deviation from the original sketch is recorded in ADR-003, which also adds `create_epic`/`assert_epic_authority`). `wf.py`, `main.py`, `preflight.py`, `gate/write.py`, and `transitions.py` depend on the protocol. The `local` filesystem-only adapter ships as the second tracker: `.woof/epics/E<N>/` is self-authoritative, integer epic IDs are allocated as max-existing + 1, push operations are no-ops, and a sync conflict cannot arise. `prerequisites.toml` `[github]` became `[tracker]` with `kind = "github" \| "local"` (clean rename, no config alias - config is live, audit logs are not). `github_synced`/`github_sync_conflict` became `tracker_synced`/`tracker_sync_conflict` in code and as canonical schema enum values, with the legacy spellings retained as enum aliases and accepted by gate-resolution/transition code so pre-RC-B2 `epic.jsonl`/`gate.md` files still validate and resolve. `epic_id` descriptions in `epic`, `plan`, `planning-node-input`, and `jsonl-events`, plus `gate.schema.json` `triggered_by`, are tracker-neutral; integer IDs are retained until a string-ID adapter lands. Architecture, README, and `docs/consumers.md` replace the "Epic IDs. Always the gh issue number" principle with the pluggable-tracker model. | Focused validation passed: `uv run pytest tests/unit/test_trackers.py tests/unit/test_wf_github_sync.py tests/unit/test_render_epic.py tests/unit/test_gate_write.py tests/unit/test_graph.py tests/unit/test_preflight.py tests/unit/test_init.py tests/unit/test_validate.py` covers the factory, protocol conformance, the `local` adapter end-to-end (CLI `woof wf new` with a failing `gh` stub on PATH), GitHub adapter parity regression, and legacy enum-value validation. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 319 tests. | `feat(trackers): introduce issue-tracker abstraction`; `docs(architecture): adopt issue-tracker abstraction` |
| RC-B3: First-Run Consumer Onboarding | Completed | BHID-003 | `woof init` (delivered in RC-5) already scaffolds `.woof/{prerequisites,agents,quality-gates,test-markers}.toml` and the required `.gitignore` block; RC-B3 closes the remaining first-run gaps. `woof init` gains `--tracker {github,local}` (default `github`): the `github` scaffold keeps the GitHub-backed `[tracker]` table and declares `gh` as required `[infra]`, while `--tracker local` scaffolds the no-remote `local` tracker, omits `gh`, and drops the `repo` line so the `local` scaffold validates against `prerequisites.schema.json` as-is (the `github` scaffold keeps the intended `<replace>` repo placeholder that fails loud at preflight). The cartography decision stays as taken in RC-5/GAP-017: no default `scripts/refresh-cartography` ships, cartography is consumer-owned, and the post-commit hook block is a no-op when the script is absent. `woof init` next-steps output now reaches the first epic (`woof wf new`) and points at the walkthrough, and the result header records the scaffolded tracker. `docs/consumers.md` replaces the `woof init`-anchored bootstrap note with an end-to-end eight-step first-run walkthrough (install via `uv tool install woof` / `pip install woof`, scaffold, fill placeholders, authenticate, preflight, install hook, `woof wf new`, run the graph) so a stranger reaches a running epic without reading the architecture document. README and `docs/architecture.md` describe `--tracker` and link the walkthrough. | Focused validation passed: `uv run pytest tests/unit/test_init.py` (13 tests, including new `--tracker local` scaffold/schema-validation, default-`github` tracker, and next-steps-loop coverage). `woof validate --schema prerequisites` passes for the `local` scaffold and fails loud on the `github` `<replace>` placeholder by design. `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 321 tests. | `feat(init): bundle consumer onboarding` |
| RC-B4: Phase B Release Evidence | Completed | — | `tests/integration/test_release_smoke.py` is the Phase B release-readiness evidence: it builds a wheel, installs it into an isolated virtual environment, runs `woof init --tracker local` in a throwaway consumer worktree, asserts the scaffold is shaped for a no-remote tracker (`kind = "local"`, no `gh` `[infra]`, no `[tracker]` `repo`) and validates against the wheel-bundled `prerequisites.schema.json`, then renders the Stage 1 `discovery_research`, `discovery_thinking`, `discovery_brainstorm`, and `discovery_synthesis` producer prompts from the installed package and asserts each embeds its full building-block playbook set (8 research, 12 consider) with no Woof-author-local skill, wrapper, or host-path token (`taches-cc-resources`, `~/.claude/plugins`, `agent-sync`, `AskUserQuestion`, `$ARGUMENTS`, host home paths). `pyproject.toml` gained PyPI release metadata: `keywords`, `classifiers`, `license-files`, and `[project.urls]` (Homepage/Repository/Issues). README adds a `Publishing` section documenting the `uv build` / `uv publish` path, the TestPyPI dry-run, and the release smoke test, and the Status line records Phase A and Phase B both complete. BHID-004 (`bin/woof` portability) was already closed under RC-6. No graph topology, schema contract, or ADR invariant changed. | Focused validation passed: `uv run pytest tests/integration/test_release_smoke.py` (1 test: wheel build, isolated venv install, `woof init --tracker local`, schema validation, Stage 1 prompt portability). `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 322 tests. | `feat(packaging): add pypi metadata and release smoke test`; `docs(release): record phase b portability evidence` |
| RC-B5: PyPI Surface Correction | Completed | — | RC-B4 framed Woof's release surface around publishing to PyPI; that premise was wrong and is corrected here. Woof ships from its GitHub repository (`github.com/krazyuniks/woof`) as an AI-assisted development tool installed with `uv tool install git+https://github.com/krazyuniks/woof`; it is not a PyPI-published Python library and cannot be one - it requires the `claude` and `codex` CLIs, `just`, and a consumer project layout to operate, and is never imported as a library. The wheel-build and installed-package architecture from RC-6 (`python -m woof` re-entry, `tool_root()` asset resolution, hatchling force-include of `schemas`/`playbooks`/`languages`/`bin`) is correct and unchanged; only the PyPI publishing layer above it was removed. Corrections: the README `Publishing` section (`uv publish`, `UV_PUBLISH_TOKEN`, TestPyPI) was deleted and `test_release_smoke.py` folded into the `Installed Package Smoke` section; README and `docs/consumers.md` install commands changed from `uv tool install woof` / `pip install woof` to the `git+https` source; `pyproject.toml` `keywords` and `classifiers` (PyPI catalogue metadata) were removed, with `[build-system]`, the `[tool.hatch.build.targets.wheel]` force-include, `[project.urls]`, `[project.scripts]`, and `license`/`license-files` retained because they are needed for any wheel build including a `git+https` install; the `test_release_smoke.py` and `test_packaging_install.py` docstrings were de-PyPI'd. No graph topology, schema contract, or ADR invariant changed. | `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 322 tests. Landed with the course-correction commit. | `chore(course-correction): align woof around self-use backlog` |

### Phase B Sequencing And Estimates

- RC-B1 (skill bundling): 2-4 sessions. Depends on whether all 21 playbooks land or only a starter set (`research/landscape`, `research/technical`, `consider/first-principles`, `consider/inversion`).
- RC-B2 (tracker abstraction): 3-5 sessions for scaffold + local adapter + renames + docs without string-ID support. Add 5-7 more sessions if a third-party adapter (Linear/Jira) and string epic IDs are included.
- RC-B3 (init + onboarding): 1-2 sessions after RC-5 / RC-B1 / RC-B2 land.
- RC-B4 (release evidence): 1 session.

Phase B subtotal: 6-11 sessions without third-party tracker support; 12-18 sessions with full tracker portability. Combined with Phase A (6-9 sessions), full any-project-ship target is **12-20 sessions** of focused work, not the 5-7 the Phase-A-only audit implied.

## Audit Reconciliation - 2026-05-19 Second Pass

After the original 2026-05-19 audit, Ryan stopped the prompt loop to assess velocity, scope, and engineering correctness. A second-pass audit confirmed the original gap table, descoped one item, and surfaced new gaps the first pass missed. Findings landed in this plan and in `README.md` the same day; this section preserves the reasoning so the next worker session does not re-discover it.

**Velocity baseline at second pass:** 24 calendar days since first commit, 94 commits, roughly 60 work-bearing sessions plus ~11 pure prompt-bump commits. Active commit days: 8 of 24. The per-row continuation cadence used for Phases 0-18 (and especially Phases 9-18, each a retrofitted single-row phase after an audit) is responsible for most of the babysitting overhead. Today's workstream chunking (commit `f7adbfe`) closed RC-1 plus RC-2 (8 child gaps) in two slice commits, demonstrating ~4x leverage over the per-row pace.

**Descope outcome:**

- **GAP-015 only.** Reclassified `missing` -> `docs-drift`, moved RC-4 -> RC-7. The Python graph does not itself spend tokens; subprocess dispatch already records actual token usage in `dispatch.jsonl`. The architecture line at `docs/architecture.md:152` describes a model-in-driver scenario that is not implemented and is not part of the accepted topology. Narrow the line, do not implement a stage-transition emitter.
- All other gaps (GAP-011..014, GAP-016..020) stay. The 2026-05-19 note that the "GTS-only, source checkout, defer the rest" path was eliminated is superseded by the 2026-05-21 course correction: Ryan's own-project use is urgent, portfolio exemplar work comes next, and OSS/distribution polish is deferred until the core loop is reliable.

**RC-3 hidden gaps surfaced by second-pass code reading:**

- `src/woof/checks/runners/check_3_scope.py:88` Check 3 uses real git pathspec; `src/woof/checks/runners/check_7_commit_transaction.py:62` and `src/woof/graph/manifest.py:17` use `fnmatch.fnmatch`. Pathspec syntax mismatches were already in GAP-012. The additional finding: Check 3's `_is_allowed_woof_path` allow-list (`check_3_scope.py:110-115`) omits the disposition file that Check 7's `_required_paths` (`check_7_commit_transaction.py:65-73`) and `manifest.py:36-42` require. A correctly executed Stage 5 (disposition staged) is rejected by Check 3 as "foreign staged paths."
- `src/woof/checks/contract_refs.py:115` and `:263` treat ajv-cli missing as an in-band Check 4 finding ("ajv-cli not found on PATH"). It should be a preflight failure with an install command; preflight already checks ajv under `[validators]`. Folded into RC-3 closure outcomes.
- `src/woof/checks/runners/check_4_contract_refs.py:77` Check 4's failure `paths` field always returns `[EPIC.md]`. When the broken artefact is `spec/openapi.yaml`, the gate.md surface points at the wrong file. Folded into RC-3 closure outcomes.

**RC-4 narrowing (closed 2026-05-19):**

- The WFL-001 per-epic lock at `src/woof/graph/lock.py` plus single-threaded graph execution already prevents the concurrent-driver race the architecture line at `docs/architecture.md` ("Atomic writes" paragraph) claimed to guard against. Verification: only the lock-holding parent appends to `epic.jsonl` (via `transitions.append_epic_event`, `gate/write._append_jsonl`, `cli/github._append_jsonl`); only the synchronous in-process `woof dispatch` Python adapter appends to `dispatch.jsonl` (via `cli/dispatcher.append_jsonl`); the dispatched LLM subprocess (`claude -p ...` or `codex`) writes neither file. The graph parent invokes `_run_dispatch` through `subprocess.run(..., capture_output=True)`, so only one dispatch is active at a time. POSIX `O_APPEND` is atomic for the line-sized payloads these writers emit. RC-4 closure was therefore a docs narrowing: the architecture line now describes the actual lock-and-syscall guarantees rather than the unimplemented advisory-file-lock helper. A narrow theoretical write surface for `--resolve` and post-run `epic_completed` events outside `run_graph` remains, gated by `gate.md` presence and `append_epic_event_once`; it is not the race the original line described and is recorded here for visibility. If a future driver mode reintroduces a real concurrent-writer surface, GAP-014 reopens.

**Phase B inventory carried out today:**

- `src/woof/graph/nodes.py:309` wires `playbooks/discovery/synthesis.md` as the only Stage 1 producer. Discovery `research/`, `thinking/`, `brainstorm/` artefacts have no graph-dispatched producer nodes. The 21 building-block playbooks under `playbooks/discovery/research/` and `playbooks/discovery/consider/` are on disk but unreferenced. They are also non-portable as written (rely on Claude Code's interactive `AskUserQuestion`, write to `artifacts/research/` instead of `.woof/epics/E<N>/discovery/<bucket>/`, carry Claude-Code slash-command frontmatter).
- `src/woof/cli/github.py` (1192 lines) is imported directly across `src/woof/cli/commands/wf.py`, `src/woof/cli/main.py`, `src/woof/cli/preflight.py`, `src/woof/gate/write.py`, `src/woof/graph/transitions.py`. Five schemas reference GitHub: `prerequisites.schema.json` requires `[github]`; `epic.schema.json`, `plan.schema.json`, `planning-node-input.schema.json` describe `epic_id` as a GitHub issue number; `jsonl-events.schema.json` has `github_synced`/`github_sync_conflict` as first-class enum values; `gate.schema.json` has `github_sync_conflict` in `triggered_by`.
- `src/woof/cli/main.py` has no `init` subcommand; `src/woof/cli/preflight.py:567` `_agents_template()` returns suggestion text only without writing the file.
- `bin/woof:1` shebang requires `uv` on PATH; `prerequisites.toml` declares `gh`, `git`, `just`, `claude`, `codex`, `ajv` but not `uv`. Same root cause as GAP-019.

**Files NOT yet read end-to-end in second-pass audit:**

- `src/woof/graph/nodes.py` (graph node definitions; spot-read only)
- `src/woof/cli/dispatcher.py` (raw `claude`/`codex` argv construction)
- `src/woof/graph/dispositions.py` (reviewer disposition flow)
- `src/woof/gate/write.py` (gate body composition)
- `src/woof/cli/github.py` (GitHub sync; only the public surface was inventoried)
- `src/woof/graph/transitions.py` (transition table)
- `src/woof/graph/runner.py` (graph orchestration loop)
- `src/woof/graph/state.py` (typed state shapes)

These are the priority targets for the Deep Code Review session (see prompt block below).

**Deep Code Review completed - 2026-05-20:** the read-only end-to-end audit of the eight files above is recorded at `docs/audit-2026-05-19-deep-code-review.md` (4371 LOC; 12 gaps - 1 high, 4 medium, 7 low; 10 refactors; 3 cross-cutting analyses; no finding overturns an ADR invariant).

## Course Correction - 2026-05-21

Ryan redirected the project around actual use value rather than release polish. The durable course-correction record is `docs/course-correction-2026-05-21.md`.

### Decisions

- Priority order is self-use in Ryan's own projects first, portfolio exemplar second, OSS/stranger-consumer distribution last.
- Do not ask more PyPI, GitHub install, tagging, or packaging-distribution questions during this correction. Distribution work is deferred, although packaging smoke tests remain useful regression evidence.
- Commit-safety guardrails and runtime action-safety guardrails are both first-class systems. Commit safety protects what gets committed; runtime action safety protects the host and working project while dispatched agents are running.
- Stage 5 producer guidance must be portable Woof-owned prompt/playbook content. The graph must not depend on a Claude-only `/wf:execute-story` slash command.
- Commit messages should describe the actual story/result/work. A hard-coded `feat(woof)` scope is a defect in a consumer-project tool, not a policy question.
- The current gate surface remains the file-and-command interface for self-use, but gate resolution needs transaction hardening and better operator reporting.
- Audit redaction/capping exists; audit retention/archive is not implemented and should not be described as current behaviour.
- Runtime permission policy decision, 2026-05-23: for Ryan self-use, dispatched agents run as trusted local automation. Woof should not constrain read, write, execute, network, or MCP access at runtime during this correction; it should document and surface the broad mode while relying on commit-safety checks, reviewer critique, gates, and transaction manifests before changes land.

### Active Workstreams

| Workstream | Status | Child gaps / sources | Closure outcomes | Validation expectations | Commit |
|---|---|---|---|---|---|
| CC-001: Documentation And Backlog Realignment | Completed | Ryan direction, RC-B5, deep audit, README/architecture/plan drift | Preserved both audit trails; added `docs/course-correction-2026-05-21.md`; aligned README, architecture, consumers guide, implementation plan, and continuation prompt around self-use-first priority and deferred distribution; recorded the guardrail taxonomy and Stage 5 portability direction. | `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 322 tests. | `chore(course-correction): align woof around self-use backlog` |
| CC-002: Self-Use Stage 5 Portability | Completed | DRH-001, DRH-003, DRH-004, DRH-006, DRH-010 | Stage 5 producer guidance now lives in `playbooks/execution/story.md`; `_story_prompt` loads that portable playbook and no longer invokes `/wf:execute-story`; `woof dispatch` sends prompt payloads to Claude/Codex on stdin and records `<prompt:stdin>` plus `prompt_transport`; `executor_result.json` accepts optional `commit_subject`, and commit construction uses it with a generic story-title fallback instead of hard-coded `feat(woof)`; a real-subprocess graph smoke uses a stub public CLI on `PATH` to emit `executor_result.json`-shaped output. | Focused validation passed: `uv run pytest tests/unit/test_dispatch.py tests/unit/test_graph.py tests/unit/test_prompt_role_terminology.py tests/unit/test_executor_result_schema.py` (96 tests). `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 325 tests. | `fix(graph): make stage 5 producer prompt portable` |
| CC-003: Graph Failure And Gate Transaction Hardening | Completed | Lead: DRH-002; DRH-005, DRH-008, DRH-009, DRH-012; additional gate-resolution atomicity hypothesis | Commit-resume git inspection failures now preserve `executor_result.json` and `check-result.json` and fail loud instead of silently discarding the resume path. Operator-recoverable malformed governance state such as missing/malformed `plan.json` and dependency dead ends now opens a plan-level `incomplete_stage_state` gate. Deterministic `write_gate` schema failures remain loud Woof bugs and roll back `gate.md` without appending a gate-opened event. The `woof wf --resolve` path was inspected and left behaviour-preserving: tracker/effect errors return before gate deletion, and destructive local effects are idempotent on retry. All GitHub `gh` subprocesses now have timeouts, and a just-created GitHub issue is closed if local epic initialisation fails, with manual cleanup details if closing also fails. | Focused validation passed: `uv run pytest tests/unit/test_graph.py tests/unit/test_gate_write.py tests/unit/test_trackers.py tests/unit/test_wf_github_sync.py tests/unit/test_render_epic.py` (110 tests). `just check` passed: Ruff lint, Ruff format check, and 331 tests. | `fix(graph): harden failure recovery and gates` |
| CC-004: Runtime Action-Safety Model | Ready | Ryan runtime-permission policy decision supplied 2026-05-23, dispatch permission audit, architecture gap | Document and test the trusted-local runtime model: dispatched agents are not constrained by Woof at runtime, and broad public-CLI permission modes are intentional for Ryan self-use. Do not add sandboxing, command allow-lists, writable-path restrictions, or network policy in this correction. | Architecture/update docs, dispatcher/preflight dry-run tests that surface the trusted-local mode, and `just check`. | `docs(dispatch): record trusted runtime policy` |
| CC-005: Observability And Audit UX | Completed | Deep audit cross-cutting findings, audit retention drift | Added `woof observe --epic <N> --view status\|timeline\|gate\|audit` as a read-only local reporting command. Status reports the next graph step without invoking mutating graph reconstitution. Timeline merges `epic.jsonl` and `dispatch.jsonl`. Gate view inspects the current `gate.md` without resolving it. Audit view reports commit-bound and raw-overflow audit files, redaction/truncation markers, dispatch audit references, token/cost fields only when dispatch events provide them, and explicitly reports retention/archive as not implemented. README and architecture now document the surface and the implemented audit-retention boundary. | Focused validation passed: `uv run pytest tests/unit/test_observe.py tests/unit/test_dispatch.py tests/unit/test_graph.py tests/unit/test_audit.py` (97 tests). `git diff --check` passed. `just check` passed: Ruff lint, Ruff format check, and 334 tests. | `feat(cli): add workflow observability views` |
| CC-006: Governance Depth | Completed | Release audit, Check 4 conformance note, gate UX note, DRH-007 deferred, REF-1..10 deferred | Check 4 now strengthens native contract-reference conformance without adding a broad framework: OpenAPI refs under `#/paths` must point at operation-shaped objects with `responses`, and JSON Schema top-level `examples[]` validate under `ajv-cli` when present. Story reviewer dispatch now prepends graph-owned story context to the prompt, and the story critique playbook requires concrete evidence by path, diff hunk, story field, or CD id/ref. Check 6 blocker evidence now preserves finding ID, category, evidence, and suggestion text. Gate-writing and gate-resolution surfaces were inspected; no command or gate-surface change was needed. DRH-007 and REF-1..10 remain deferred low/refactor material. | Focused validation passed: `uv run pytest tests/unit/test_check_cd.py tests/unit/test_check_4_contract_refs.py tests/unit/test_check_6_critique_blocker.py tests/unit/test_graph.py::test_critique_dispatch_failure_opens_reviewer_gate tests/unit/test_prompt_role_terminology.py` (34 tests). `just lint` passed. `just check` passed: Ruff lint, Ruff format check, and 338 tests. | `feat(checks): deepen contract governance` |
| CC-007: Distribution And Release Polish | Blocked | Deferred by Ryan | Revisit install, packaging, GitHub/PyPI/tagging decisions, external consumer docs, and OSS onboarding only after self-use and portfolio readiness are established. | To be defined when unblocked. | Not selected |

### Sequencing

Ryan supplied the CC-004 runtime-permission policy decision on 2026-05-23: trusted local automation, no Woof runtime constraints during this correction. CC-006 is complete; the next ready workstream by order is CC-004.

## Next Continuation Prompt

```text
We are working in /home/ryan/Work/woof.

Read first:
1. AGENTS.md
2. README.md
3. docs/implementation-plan.md
4. docs/architecture.md
5. docs/course-correction-2026-05-21.md
6. docs/audit-2026-05-19-deep-code-review.md
7. docs/adr/001-orchestration-topology.md
8. docs/adr/002-graph-led-role-routing.md
9. docs/adr/003-issue-tracker-abstraction.md

Status:
The project is under the 2026-05-21 course correction. Ryan's own development use is the urgent priority. Portfolio exemplar value comes after the core loop works. OSS/distribution polish is deferred. CC-001, CC-002, CC-003, CC-005, and CC-006 are complete. Ryan supplied the CC-004 runtime-permission policy decision on 2026-05-23: trusted local automation, no Woof runtime constraints during this correction. CC-004 is the next ready workstream. CC-007 remains blocked until Ryan explicitly reopens distribution.

Goal:
Start CC-004 from the course-correction backlog in docs/implementation-plan.md:

- Document and test the trusted-local runtime model: dispatched agents are not constrained by Woof at runtime, and broad public-CLI permission modes are intentional for Ryan self-use.
- Do not add sandboxing, command allow-lists, writable-path restrictions, network policy, or MCP restriction logic in this correction.
- Keep the implemented safety boundary focused on commit-safety checks, reviewer critique, human gates, transaction manifests, and commit decisions before changes land.
- Surface the trusted-local mode honestly in the operator/config/docs path where it is most useful, likely dispatch dry-run/preflight/architecture docs rather than a new runtime policy engine.
- Do not ask PyPI, GitHub install, tagging, or packaging-distribution questions. CC-007 is blocked until Ryan explicitly reopens distribution.

Preserve the accepted architecture in any future work: Woof stays graph-led (ADR-001); GPT-5.5 is the preferred primary producer route and Claude Opus 4.7 at `max` effort is the preferred reviewer route (ADR-002); reviewer blockers open human gates with no model-to-model debate loop; the issue tracker stays behind the `Tracker` protocol (ADR-003); and Woof must not depend on Woof-author-local wrappers (`cld`, `cod`), `agent-sync`, `~/.dotfiles`, or host-specific absolute paths.

Start with:
Run `git status --short --branch`, preserve unrelated local changes, and select the first active course-correction workstream by order. For CC-004, inspect `src/woof/cli/dispatcher.py`, `.woof/agents.toml` schema/config surfaces, preflight role-route checks, dispatch dry-run output, and related dispatch/preflight/docs tests before editing. Update docs and tests with any trusted-runtime behaviour or reporting change.
```

## Deep Code Review Continuation Prompt

Retained for provenance only. This audit has already been run, and the report exists at `docs/audit-2026-05-19-deep-code-review.md`. Do not use this block as the next-session prompt.

This prompt is for a one-off audit session that does NOT continue Phase A execution. It runs in a clean context window so the reviewer has full room to read load-bearing source files end-to-end and write findings without context pressure from prior session work. The intended invoker is Ryan in a fresh Claude Code session.

```text
We are working in /home/ryan/Work/woof. This is a code-audit session, NOT an implementation session. Do not modify source files. Do not start any RC-3..RC-7 or Phase B workstream. Produce a written audit report only.

Context for the audit:

1. A second-pass audit was performed on 2026-05-19 and is recorded in `docs/implementation-plan.md` under the "Audit Reconciliation - 2026-05-19 Second Pass" section. Read it first; it explains what was checked, what was descoped, and what was deferred.
2. The second pass did NOT read these source files end-to-end:
   - `src/woof/graph/nodes.py` (graph node implementations; spot-read only)
   - `src/woof/cli/dispatcher.py` (raw `claude`/`codex` argv construction, MCP JSON generation, dispatch event emission)
   - `src/woof/graph/dispositions.py` (reviewer disposition flow)
   - `src/woof/gate/write.py` (gate body composition: Context / Findings / Primary position / Reviewer position sections)
   - `src/woof/cli/github.py` (GitHub sync: ~1192 lines; only the public surface was inventoried)
   - `src/woof/graph/transitions.py` (transition table)
   - `src/woof/graph/runner.py` (graph orchestration loop, transaction commit, lockfile acquisition)
   - `src/woof/graph/state.py` (typed state shapes)
3. Architectural authority order: `docs/architecture.md` (design contract), `docs/adr/001-orchestration-topology.md` (graph topology), `docs/adr/002-graph-led-role-routing.md` (role routing), then code. When code and docs disagree, code is the source of truth for implemented behaviour.
4. Accepted invariants:
   - Graph-led. Producers/reviewers are nodes, never workflow orchestrators.
   - GPT-5.5 is the preferred primary route; Claude Opus 4.7 at `max` effort is the preferred reviewer route.
   - Reviewer blockers open human gates; there is NO model-to-model debate loop.
   - No Ryan-local wrappers (`cld`, `cod`), no `agent-sync`, no `~/.dotfiles`, no host-specific absolute paths.
   - `.woof/` is runtime state only; design contract work does not live in `.woof/`.

Audit scope:

Read each of the 8 files listed above end-to-end. For each file, produce a section in your report covering:

a) **What this file actually does** - a precise behaviour description, not a paraphrase of the docstrings.
b) **Architecture conformance** - does the implementation match what `docs/architecture.md` and the ADRs claim? File line citations both ways.
c) **Internal consistency** - do helper functions, error paths, schema references, and event names match each other? Look for orphan code, dead branches, and copy-paste errors.
d) **Hidden gaps** - bugs, race conditions, missing tests, incorrect failure modes, places where the code is more conservative or more permissive than the architecture. Each one gets a proposed gap ID (e.g., `DRH-001`, `DRH-002`) plus risk classification (`low` / `medium` / `high` / `critical`) and pointer to which workstream (RC-3..RC-7, RC-B1..B4, or new) should close it.
e) **Refactor opportunities that are NOT bugs** - structural smells, duplicated logic, missing abstractions. Marked as `refactor`, not gaps.

In addition to the per-file sections, produce three cross-cutting analyses:

- **Cross-cutting analysis 1: Dispatch lifecycle.** Trace one complete Stage 5 story dispatch from `runner.py` through `nodes.py` -> `dispatcher.py` -> subprocess -> `dispatch.jsonl` append -> `dispositions.py` -> `gate/write.py` if blocker -> commit transaction in `runner.py`. Identify every JSONL append, every filesystem write, every subprocess boundary, every lock acquire/release. Flag anywhere the audit-trail can become inconsistent.
- **Cross-cutting analysis 2: Gate semantics.** Trace every code path that writes `gate.md` and every code path that deletes `gate.md` (gate resolution). Confirm there is no state where `gate.md` and a `gate_resolved` event coexist or where `gate.md` is deleted without a `gate_resolved` event. Confirm Plan Gate is mandatory (cannot be bypassed). Confirm structured decisions (`approve`, `revise_plan`, `keep_local`, `accept_remote`, `hand_merge`, `split_story`, `abandon_story`) drive deterministic state effects.
- **Cross-cutting analysis 3: GitHub coupling depth.** This audit's purpose is partly to feed the Phase B RC-B2 (tracker abstraction) workstream. Inventory every concrete piece of GitHub-specific behaviour in the 8 audited files: what would need to become an interface method, what shapes are tracker-shape-dependent vs tracker-shape-agnostic, what `.last-sync` semantics actually exist in code (vs documented), and which gate-trigger / event / schema names embed `github` in their identifier.

Output format:

- Write the audit report to `docs/audit-2026-05-19-deep-code-review.md` as a new file. Do not embed it in `implementation-plan.md`.
- Use ASCII printable characters only; this is an internal vault doc but follow Ryan's typography rule for consistency (no em dashes, no curly quotes, no ellipsis character).
- File-line citations everywhere: `src/woof/graph/runner.py:42-67`. No paraphrase without a citation.
- Quantitative summary at the top: file count audited, total LOC, gap count by severity, refactor count, cross-cutting findings count.
- Do not modify any source file.
- Do not modify any other documentation file except creating the new audit report.
- Do not run tests, just check, or any CI commands. This is a read-only review.

Finish by appending a one-line summary entry to `docs/implementation-plan.md`'s "Audit Reconciliation - 2026-05-19 Second Pass" section noting the deep-review report exists at `docs/audit-2026-05-19-deep-code-review.md`. That is the only edit allowed to implementation-plan.md.

After the report is written, do not start implementation work. Hand back to Ryan with the path to the report.
```
