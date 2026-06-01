# Woof Backlog

This is the single source of truth for what is open. It is prescriptive: the items below describe work to do, not work that has been done. There is no history kept here.

Authority: this file plus the ADRs and `docs/architecture.md` define the project. Anything not described here is not part of the plan.

## Shape

Woof is an inner-loop SDLC tool for AI-assisted development.

- **Operator surface.** One umbrella `/woof` skill over the `woof` CLI (run epics with `woof wf`, resolve gates, reset, observe, onboard), plus the interactive `/woof:brainstorm` design specialist (ADR-007). The setup, map-codebase, run, and gate flows live in the umbrella's `references/`.
- **Deterministic engine.** A Python library at `src/woof/` exposes graph transitions, schema validation, JSONL audit, and tracker abstractions. The skills invoke it via Bash.
- **Setup CLI.** `woof init`, `woof preflight`, and `woof hooks install` are non-interactive Python commands used during project setup. They are not parallel surfaces for running epics.
- **State on disk.** `EPIC.md`, `plan.json`, JSONL audit, dispositions, gate files, and cartography artefacts are the authoritative state. The engine (`woof wf`) runs the graph in-process and reconstructs position from disk on every invocation; on crash or operator switch, re-running `woof wf --epic N` resumes.
- **Cartography prerequisite.** Every consumer repo has `.woof/codebase/` containing human-authored design docs, mapper-authored AS-IS docs, and a mechanical index. The workflow fails preflight if the prerequisite docs are missing.
- **Contract readiness.** After Stage 2 writes `EPIC.md`, a deterministic readiness boundary checks that the epic is machine-checkable before any model plans stories.
- **Evidence over confidence.** Reviewer blockers must carry resolvable evidence. Confidence scores, if ever added, are advisory eval metadata and never gate-affecting.
- **Producer/reviewer per stage.** Stage 5 story execution: Claude produces, Codex reviews. All other stages: Codex produces, Claude reviews. Mapper subagents: Claude (LSP helps with accuracy).
- **Epic intent in the spec.** Each epic declares whether it refactors toward the target architecture or accepts the current shape. The system does not need brownfield/greenfield modes; the epic carries the intent.
- **Expert workstation posture.** Woof may assume explicit local prerequisites, including tmux for long-run supervision. tmux can present and supervise; it must not become a second graph or state authority.

## Implementation approach

The backlog below is not a single coding-agent prompt. Each epic is broken into a focused implementation plan when it is about to start. An implementation plan splits the epic into a sequence of small, reviewable coding-agent prompts, each with clear acceptance criteria. The operator reviews each prompt's output before the next runs.

The plan for execution itself (sequencing the epics, deciding overlap, choosing the first prompt) lives in `docs/implementation-plan.md`.

## Epics (in order)

> **Superseded direction (2026-06-01).** E1's `woof graph` re-architecture (rename `woof wf` -> `woof graph`, remove the in-process dispatch loop, `state_token` compare-and-set, typed `record-*`/`run-deterministic-node` verbs) and E3's three-skill suite were overtaken by the shipped `woof wf` in-process runner and the single `/woof` umbrella skill (ADR-007). They are kept below as planning context; whether to formally withdraw or re-scope them is an open decision. New work builds against `woof wf`, not `woof graph`.

### E1. Foundation: graph library API

Make `src/woof/graph/` cleanly callable from outside Python so the skill orchestrator can drive it via Bash without depending on internal helpers or preserving the old Python-owned dispatch loop.

Open work:
- Public API surface for:
  - `create_epic` — allocate tracker/local ID, seed `spark.md`, set `.woof/.current-epic`.
  - `resume_epic` — fetch or initialise tracker-backed local state when needed.
  - `next_node` / `next_node_state` — pure state query returning the next node contract.
  - `run_deterministic_node` — graph-owned nodes only: `plan_gate_open`, `review_disposition`, `verification`, `commit`, `gate_open`.
  - typed model-output record verbs: `record_discovery_bucket`, `record_discovery_synthesis`, `record_epic_definition`, `record_plan`, `record_plan_critique`, `record_executor_result`, `record_story_critique`.
  - typed dispatch telemetry verbs: `record_dispatch_started`, `record_dispatch_returned`.
  - `resolve_gate`.
  - `mark_cartography_refreshed`.
- Single CLI entry point: `woof graph <command>` exposes the library to the skill via Bash. Replaces direct `woof wf` invocation in new docs and future skills.
- Explicit JSON contract on every CLI sub-command: input arguments, stdout payload, stderr shape, exit code.
- `next-node` dispatch contract includes: `kind`, `node_type`, optional `story_id`, `route_key`, transport hints (`task-subagent-claude` or `bash-codex-exec`), optional `subagent_type`, model/effort after route resolution, `prompt_template_path`, `expected_output_paths[]`, `expected_output_schemas[]`, `loaded_docs[]`, and `state_token`.
- `record-dispatch-returned` captures outcome/progress telemetry as part of the strict graph contract: `exit_type`, `exit_code`, normalised `error_signature`, `head_before`, `head_after`, `branch_before`, `branch_after`, expected artefact presence/schema status, rate-limit metadata, duration, byte counts, token counts where available, and command count where available.
- `loaded_docs[]` is a list of repo-relative cartography references for the skill to load and cache, not pre-loaded content.
- Every mutating graph command requires the observed `state_token` and fails compare-and-set when canonical state changed.
- Short filesystem locks around graph mutations only. No lock is held across LLM dispatch.
- No raw `append_epic_event` command. JSONL events are written only as a consequence of typed graph verbs.
- Route-table schema rewrite: `.woof/agents.toml` uses canonical `producer`, `reviewer`, `mapper`, and `gate-resolver` semantics with node-group or `route_key` overrides. `primary` / `critiquer` remain read-tolerated migration aliases only.
- Remove direct LLM dispatch from graph node handlers. The graph returns `kind=dispatch`; the skill performs dispatch and calls a typed record verb.
- Split or retire the old `run_graph` loop so it cannot hold a workflow lock across dispatched work.
- Any legacy run command delegates to the graph API during migration and has a removal deadline; no new docs or skills depend on it.
- Update live operator strings and tests that still point at `woof wf`, especially `src/woof/cli/init.py` next steps and `tests/unit/test_init.py`.
- Update playbook/readme prose that forbids or advertises `woof wf` so producer/reviewer prompts do not teach the old surface.
- Normalise gate schema and prose so story gates are Stage-5 halts; legacy `stage=6` remains read-tolerated only for existing artefacts if migration requires it.
- Tests covering library-call and CLI-call patterns for dispatch, deterministic, gate, complete, stale state-token failure, and typed record validation.

Depends on: nothing. Blocks: E3 (skill suite).

### E2. Cartography prerequisite

Make the cartography artefact group mandatory infrastructure with preflight enforcement and per-language refresh templates.

Open work:
- Schema additions to `prerequisites.toml`: `[cartography]` block with `staleness_floor_hours`, `summary_min_chars`, declared languages, and stub-marker policy.
- Preflight upgrade to fail closed on:
  - Missing `scripts/refresh-cartography`.
  - Non-executable `scripts/refresh-cartography` (already present at `preflight.py:914-932`; reuse).
  - Missing or stub `.woof/codebase/TARGET-ARCHITECTURE.md`.
  - Missing or stub `.woof/codebase/PRINCIPLES.md`.
  - Missing mechanical layer (`tags`, `files.txt`, `freshness.json`).
  - Stale `freshness.json` beyond declared floor (warning, with refresh prompt).
- Define stub detection exactly: setup templates carry a removable stub marker; documents below `summary_min_chars` fail unless explicitly marked complete by front matter.
- Per-language `refresh-cartography` templates shipped under a dedicated template path referenced from `languages/<lang>.toml`. Initial set: Python, Go, TypeScript, Rust.
- `woof init` composes the per-language fragments into the consumer's `scripts/refresh-cartography` (idempotent; never clobbers an existing script).
- Rename `tree.txt` to `files.txt` everywhere (gitignore, init, preflight, architecture doc, tests).
- Post-commit hook regenerates the mechanical layer and fails loud if the mandatory refresh script exits non-zero. The current no-op-if-missing hook body must be revised or explicitly justified.
- `freshness.json` schema: `{ ts, git_ref, age_s, generator_version }`.
- Existing consumers without cartography get a clear preflight error pointing at `/woof:setup` or `/woof:map-codebase`.

Depends on: nothing. Blocks: E3.2 (map-codebase skill), E3.3 (run skill).

### E3. Claude Code skill suite

Three operator-facing skills plus the deferred target-architecture authoring skill.

#### E3.1. `/woof:setup` skill

Onboarding skill that walks an operator through setting up a new consumer repo.

Open work:
- Skill bundle at `skills/woof-setup/` (or `~/.claude/skills/woof-setup/`).
- Q&A flow via `AskUserQuestion`:
  - Primary language(s).
  - Greenfield or against existing code.
  - Tracker choice (local vs hosted).
  - Whether to use the default DDD + hexagonal target-architecture template.
- Skill invokes `woof init` for file scaffolding.
- Skill writes `.woof/codebase/TARGET-ARCHITECTURE.md` and `.woof/codebase/PRINCIPLES.md` from templates pre-seeded with the operator's answers. (Detail authoring deferred to E3.4.)
- Skill optionally invokes `/woof:map-codebase` for the first map.
- Tests covering Q&A flow and file output.

Depends on: E1, E2, E3.4 (target-architecture authoring).

#### E3.2. `/woof:map-codebase` skill

Mapper orchestrator following GSD's pattern: parallel subagents, each writing to disk directly.

Open work:
- Skill bundle at `skills/woof-map-codebase/`.
- Seven subagent definitions, each focused on one or two outputs:
  - tech focus → STACK.md, INTEGRATIONS.md
  - arch focus → CURRENT-ARCHITECTURE.md, STRUCTURE.md
  - quality focus → CONVENTIONS.md, TESTING.md
  - concerns focus → CONCERNS.md
- Subagents are dispatched in parallel via Task tool; each writes its output(s) under `.woof/codebase/`.
- Subagents use ctags + `files.txt` + tree-sitter on demand during exploration.
- Skill writes `freshness.json` with timestamp + git ref after all subagents complete.
- Mechanical refresh (ctags + git ls-files + freshness) runs first to seed the subagents.
- Tests covering parallel dispatch + output file presence.

Depends on: E2.

#### E3.3. `/woof:run` skill

Epic creation/resume/execution orchestrator.

Open work:
- Skill bundle at `skills/woof-run/`.
- Skill can start a new epic from a spark, resume `.woof/.current-epic`, or resume an explicit `E<N>`.
- Skill calls `woof graph create-epic` or `woof graph resume-epic` before entering the node loop when needed.
- Skill calls `woof graph next-node` to determine the next dispatch.
- Per node, the skill:
  - Loads the relevant cartography slice into its session context.
  - Dispatches the producer subagent using the `route_key` and transport hints returned by `next-node`.
  - Persists model-produced artefacts via the typed `woof graph record-*` verb for that node.
  - Dispatches the reviewer subagent with isolated context (no producer working memory).
  - Records the reviewer's critique via `woof graph record-plan-critique` or `woof graph record-story-critique`.
  - Runs graph-owned deterministic nodes via `woof graph run-deterministic-node`.
  - Calls `woof graph next-node` again.
- On gate event: skill surfaces gate body in the session conversation and waits for operator resolution.
- Operator resolves gates via natural language ("approve", "revise plan", "split story"); skill calls `woof graph resolve-gate <verdict>`.
- Progress reporting: skill summarises completed steps after each node.
- Long-run supervision may use tmux for panes, logs, and operator visibility. The skill still drives the graph through typed commands; tmux is presentation and process supervision only.
- Tests covering the dispatch loop, gate surfacing, and operator resolution.

Depends on: E1, E2, E3.2.

#### E3.4. Target-architecture authoring skill

Complex skill for guiding the operator through TARGET-ARCHITECTURE.md authoring with language-idiom awareness.

Open work (high-level only; detailed design deferred):
- Skill at `skills/woof-target-architecture/`.
- Multi-stage Q&A: overview → domain model → layer boundaries → tech stack → integrations.
- Language-aware templates: Python (protocols + dataclasses + workspace), Rust (traits + crates + cargo workspace), TypeScript (interfaces + monorepo packages). Shared DDD + hexagonal backbone.
- Optional "from scratch" mode (greenfield) and "audit existing code" mode (brownfield).
- Output: a fully populated `.woof/codebase/TARGET-ARCHITECTURE.md` + `.woof/codebase/PRINCIPLES.md`.
- Reference shapes: extract commonality from Ryan's GTS architecture doc and the Rust project equivalent. (Ryan to share or point to these when ready.)

Depends on: E1, E2. Blocks: E3.1.

### E4. Contract readiness and run resilience

Add the operational guardrails captured in ADR-006 without changing the core graph philosophy.

Open work:
- Add a Stage-2.5 deterministic readiness node that runs after `EPIC.md` exists and before `breakdown_planning`.
- Add `readiness-result.schema.json` and gate support for `readiness_gate`.
- Readiness checks:
  - every observable outcome has a machine-checkable acceptance signal;
  - acceptance criteria avoid pure subjective prose unless paired with concrete assertions or commands;
  - contract decisions include exact paths, schema refs, API refs, or explicit forward-created markers;
  - referenced existing paths resolve against `git ls-files`;
  - cited symbols resolve where Woof can cheaply prove them, otherwise require explicit operator-marked forward creation;
  - Stage 3 receives enough information to decompose without inventing interfaces.
- Define the forward-created annotation grammar exactly: `` `path/or/symbol` (forward-created) `` or `` `path/or/symbol` (created by ticket <id>) ``. The annotation sits outside backticks and uses exactly one ASCII space before the parenthesis.
- Readiness checker timeouts produce non-blocking performance findings. A readiness gate must not block solely because its own checker ran out of time.
- Add readiness recycle escalation: after a configured number of failed readiness cycles, open an escalation-flavoured readiness gate rather than looping revise/fail indefinitely.
- Add audited readiness-gate resolutions: `revise_epic_contract`, `approve_with_reason`, `abandon_epic`.
- Add blocker-evidence discipline to `critique.schema.json`, prompts, and checks:
  - blocker findings require `evidence`;
  - evidence must resolve to a known artefact reference: file:line, story id, observable outcome id, contract-decision id, schema ref, or quality-gate id;
  - extend `check_6_critique_blocker` so unresolvable blocker evidence is itself a blocker;
  - do not add confidence-based gating in E4.
- Add `quality-gates.toml` mode support:
  - `strict`: any failure blocks.
  - `baseline`: command-level baseline for arbitrary gates; a command already red at capture time is reported but does not block unless its configured command identity changes or the baseline expires.
- Do not claim fine-grained per-failure subtraction in E4. Per-failure baselines require declared structured parsers or machine-readable output and are deferred until a later iteration proves the need.
- Defer known-flake allowlists until gates have structured failure identities and expiry metadata. Do not add a command-level flake bypass in E4.
- Add baseline freshness metadata with both wall-clock age and graph/run-iteration age, plus explicit recapture semantics.
- Add `/woof:run` circuit-breaker policy over dispatch telemetry:
  - stage-aware progress: Stage 1-3 progress is expected `.woof/` artefact creation/validation or graph-state advance; Stage 5 progress is expected story artefact/check/critique output or graph-owned commit advance;
  - separate counters for consecutive no-progress and consecutive same-error signatures;
  - same-error is checked before no-progress;
  - error-signature normalisation strips volatile paths, line/column spans, ISO timestamps, UUIDs, and excess whitespace, preserves standalone numbers, and truncates to a bounded length;
  - repeated constraint/invariant/contract-discovery signatures open a course-correction gate instead of a generic stuck-worker gate;
  - repeated timeout without expected artefact progress opens a run-resilience gate;
  - rate-limit wait/resume is classified separately from failure.
- Add HEAD/branch drift detection:
  - compare `head_before`/`branch_before` and `head_after`/`branch_after` across dispatch;
  - commit/gate paths halt if HEAD or branch moved unexpectedly and not through a graph-owned commit;
  - OD-1 remains detect-first, not a preventive tool sandbox.
- Add optional tmux-backed long-run supervision for `/woof:run`: panes/logs/status only; no direct state mutation outside typed graph commands.
- Tests covering readiness pass/fail, readiness timeout non-blocking behaviour, readiness escalation, readiness gate resolution, blocker evidence resolution, command-level baseline behaviour, baseline freshness/recapture, HEAD/branch drift, circuit-breaker decision logic, and tmux-mode command construction without graph bypass.

Depends on: E1, E2, E3.3. Blocks: E5, E6, E7.

### E5. Specwright bootstrap

Make specwright ready as the consumer for the production-shape eval.

Open work:
- Author `specwright/.woof/codebase/TARGET-ARCHITECTURE.md` (use E3.4 skill once ready, or hand-write a minimal target reflecting specwright's actual shape).
- Author `specwright/.woof/codebase/PRINCIPLES.md`.
- Run `/woof:map-codebase` against specwright to produce the seven AS-IS docs.
- Add `specwright/scripts/refresh-cartography` (composed from Python language template).
- Install post-commit hook (`woof hooks install`).
- Verify preflight and Stage-2.5 readiness pass against specwright.

Depends on: E4, E3.2, E3.4.

### E6. Eval instrumentation under new shape

The eval baseline must measure the production shape: skill orchestrator, cartography on disk, dispatched producer/reviewer, typed graph commands, readiness boundary, run-resilience telemetry, drift detection, blocker-evidence checks, and JSONL audit.

Open work:
- `node_type` field added to every dispatch event so per-node attribution is possible.
- Dispatch-return outcome fields included in the manifest: `exit_type`, `exit_code`, `error_signature`, expected artefact presence/schema status, rate-limit classification, and HEAD/branch before/after.
- Run-resilience rows include circuit-breaker decisions, no-progress/same-error counters, readiness outcomes, baseline-gate mode/outcome, blocker-evidence resolution outcomes, and drift events.
- `artefacts_loaded[]` retained per event in any compacted manifest produced by the eval harness.
- Prompt + output bodies persisted into the eval output directory at run teardown.
- Per-node rows in the comparison markdown (calls, tokens, prompt_bytes, artefact_bytes, duration_ms).
- "Artefact reload" section in manifest: `{artefact_path: [(call_index, node_type, bytes)]}` exposing duplicate context-loading.
- Harness reads dispatch events the skill writes so the new orchestrator surface is measured.
- Dispatch schema writes `adapter` only; legacy `harness` is read-tolerated for migration input.
- The existing Python bench is either rewritten around the production shape or replaced by `/woof:eval`, depending on OD-5.

Depends on: E4 (skill orchestrator emitting the events and guardrail outcomes to measure).

### E7. Baseline eval run

Run the eval against specwright in the production shape and capture the first manifest as a stable baseline for future iteration.

Open work:
- Single-variant run against specwright @ HEAD.
- Inspect per-node manifest; confirm `node_type` correlation works and artefact-reload section surfaces duplication.
- Decide on first iteration target based on the data (likely a context-scoping tweak in `/woof:run` skill).

Depends on: E4, E5, E6.

### E8. Deterministic contract conformance audit

Add a post-baseline conformance audit inspired by Pickle Rick's Citadel shape without copying its project-specific analyzers.

Open work:
- Add a diff-scoped conformance audit node or Stage-5 check that reads `EPIC.md`, `plan.json`, the story diff, and cartography.
- Add `conformance-result.schema.json`.
- Audit questions:
  - every satisfied observable outcome has production evidence in changed files, not only test markers;
  - every implemented contract decision's declared surface exists in the diff or current tree;
  - declared guards, permissions, state transitions, or lifecycle invariants from cartography are not bypassed;
  - new allowlist/config entries have production callers or explicit rationale;
  - domain-specific checks are consumer-supplied rules, not hardcoded Woof assumptions.
- Start with generic deterministic checks and a consumer-rule hook. Add language/framework analyzers only when a real consumer proves the rule has reusable value.
- The audit can open a story gate on deterministic high-severity violations. It must not become an LLM debate loop.

Depends on: E2, E4, E7. Blocks: none in the initial production-shape baseline.

## Outstanding Decisions

Decisions still needed before specific epics can proceed.

| ID | Decision | Blocks | Default if undecided |
|---|---|---|---|
| OD-1 | Runtime action-safety policy posture: trusted-local-only / soft-policy-with-audit / hard-policy-with-enforcement | E3.3 and E4 | Trusted-local plus HEAD/branch drift detection; no preventive tool sandbox unless real usage proves detection is insufficient |
| OD-3 | Codex producer dispatch from skill: Bash-wrapping Task subagent vs direct Bash | E3.3 | Direct Bash + JSON output capture |
| OD-4 | Reviewer subagent type: dedicated `woof-reviewer` definition vs `general-purpose` | E3.3 | Dedicated `woof-reviewer` with strict isolation prompt |
| OD-5 | Eval harness home: Python bench retained or replaced by `/woof:eval` skill | E6 | Python bench retained; skill drives it via Bash |
| OD-6 | tmux posture for `/woof:run`: always required for long-run mode vs opt-in flag | E4 | Supported and recommended for long epics; non-tmux mode remains available until real usage proves it is unnecessary |

## Glossary

- **Cartography** — the artefact group at `.woof/codebase/`. Two authoring layers (human + mapper) and one mechanical layer.
- **Mapper subagent** — a Claude subagent dispatched by `/woof:map-codebase` that explores the consumer codebase and writes one or two themed markdown documents directly to disk.
- **Producer** — the LLM-dispatched node that creates an artefact for a graph stage.
- **Reviewer** — the LLM-dispatched node that critiques the producer's output. Always a different model family from the producer for diversity-of-opinion.
- **Skill** — a Claude Code skill: a bundle of instructions and supporting files invoked by `/<name>` in a Claude Code session.
- **Mechanical layer** — `tags`, `files.txt`, `freshness.json`. Cheap, post-commit refreshed, used as fallback fact source by graph nodes and as exploration input by mapper subagents.
- **Readiness gate** — deterministic halt after Stage 2 when `EPIC.md` is not concrete enough for planning.
- **Strict quality gate** — quality gate mode where any failure blocks.
- **Baseline quality gate** — quality gate mode where a pre-existing red command or structured failure set is recorded and only deterioration blocks.
- **Command-level baseline** — baseline mode for arbitrary shell gates where the command's red/green status is the unit of comparison.
- **Structured gate parser** — declared parser or machine-readable output that lets Woof fingerprint individual failures for fine-grained baseline subtraction.
- **Blocker evidence** — machine-resolvable evidence attached to a blocker finding, such as file:line, story id, outcome id, contract-decision id, schema ref, or gate id.
- **HEAD/branch drift** — unexpected git position movement between dispatch, verification, and commit that is not explained by a graph-owned commit.
- **Conformance audit** — deterministic diff-scoped audit that checks implemented production changes against `EPIC.md`, plan contracts, and consumer invariants.
