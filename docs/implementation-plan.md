# Woof Implementation Plan

> **Purpose:** Single authoritative implementation plan, sequencing guide, and progress ledger for Woof.
> **Authority:** This file supersedes `docs/backlog.md` for implementation work. Architecture remains governed by `docs/architecture.md`; graph topology remains governed by `docs/adr/001-orchestration-topology.md`; role routing remains governed by `docs/adr/002-graph-led-role-routing.md`; code is the source of truth for implemented behaviour.
> **Operating rule:** Do not keep a second live backlog. New work must be added here as a scoped work item before implementation starts.

## Current Baseline

Woof has an implemented ADR-001 Stage-5 execution path. `woof wf --epic <N>` runs the deterministic Python graph for story selection, executor dispatch, critique dispatch, verification, gate opening, structured gate resolution, and commit transaction verification.

ADR-002 is now accepted and implemented. Woof is graph-led, GPT-5.5 is the preferred primary producer route, Claude Opus 4.7 at `max` effort is the preferred reviewer route, and reviewer blockers open human gates rather than model-to-model debate loops. Stage-5 dispatch uses semantic `primary` / `reviewer` roles and public raw `claude` / `codex` adapters owned by Woof. Preflight is the startup infrastructure check. Workstream R is complete, so Stage 1-4 graph migration can continue.

Implemented surfaces:

- CLI wrapper and commands: `wf`, `preflight`, `hooks install`, `validate`, `dispatch`, `render-epic`, `check-cd`, `check stage-5`, and `gate write`.
- Python graph runtime: typed node contracts, transition table, transaction manifest generation, and manifest/index verification.
- Schemas for plans, gates, critiques, JSONL events, node I/O, executor results, check results, transaction manifests, language registry, quality gates, docs paths, agents, prerequisites, and test markers.
- Language registry files for Python, TypeScript, Rust, and Go.
- All nine Stage-5 check runners are implemented and wired into the registry.
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

## Next Continuation Prompt

```text
We are working in /home/ryan/Work/woof.

Read first:
1. AGENTS.md
2. README.md
3. docs/implementation-plan.md
4. docs/adr/002-graph-led-role-routing.md
5. Any architecture, schema, or source file directly touched by the selected work item

Goal:
Continue the Woof implementation plan from the Post-WF-002 Execution Workstreams section. The current architectural decision is graph-led role routing: GPT-5.5 is the preferred primary producer route, Claude Opus 4.7 at `max` effort is the preferred reviewer route, and reviewer blockers open human gates rather than model-to-model debate loops. Woof must not depend on Ryan-local wrappers (`cld`, `cod`), `agent-sync`, `~/.dotfiles`, or host-specific absolute paths; it must construct public raw `claude` / `codex` invocations itself, including Claude MCP JSON and portable `~/.claude/projects/<project-slug>/...` transcript references. If an item is already In progress, finish it; otherwise pick the first incomplete workstream by order and select an appropriately sized slice from that workstream. Before editing, run git status --short --branch and use just --list to confirm project commands. Update the ledger when starting and completing work. Keep code, schemas, tests, and docs aligned.

Workflow:
- Use just for project commands.
- Run targeted validation during implementation when useful.
- Run just check before handoff unless the ledger records an external blocker.
- Commit through normal hooks with the commit message recorded on the selected item.
- Push normally.
- Monitor GitHub CI for the pushed commit until it passes. If it fails, inspect the failing job, fix the underlying issue, and repeat the commit/push/monitor loop.
- In the final response, paste this complete continuation prompt block so it can be copied into a new session.

Start with:
Workstreams R, F, G, Phase 8 `PRD-001`, Phase 9 `AUD-001`, Phase 10 `CIM-001`, Phase 11 `CHK-010`, Phase 12 `AUD-002`, Phase 13 `DPA-001`, and Phase 14 `WFR-001` are complete. No `Ready` items remain in this plan. If continuing implementation, add the next scoped work item to `docs/implementation-plan.md` before editing, then start that item.
```
