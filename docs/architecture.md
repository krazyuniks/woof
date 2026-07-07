# Woof Architecture

This document is the system-design source of truth. It describes the target system. ADRs under `docs/adr/` carry the decisions and trade-offs; `docs/backlog.md` carries remaining work.

## 0. Scope

Woof is the orchestration engine for AI-assisted software delivery. It turns an epic or pre-decomposed `work_units[]` backlog into verified code changes through deterministic graph control, interactive LLM workers, project-owned verification, independent review, and auditable publish/merge steps.

The durable boundary is the consumer repository. Woof assumes a Git worktree, `.woof/` state, repo-local policy, repo-local verification commands, and the operator's subscription CLI harnesses. The engine owns harness adapters, launch, execution, readiness, output parsing, and validation. Repo policy owns only which harness/model/effort fills each worker slot.

Woof is one of the tools a project composes, and it owns neither the other tools nor the choice of them. A project composes a host-level worktree engine and an SDLC delivery tool; Woof is one such delivery tool, selected per run. It runs against whatever checkout it is pointed at and neither creates nor manages worktrees - the worktree engine provisions those and runs the project's registered lifecycle commands, orchestrated by the project's task runner; in Profile A the worktree is that engine's, never Woof's (ADR-015). External issue ingestion is upstream of Woof's intake: Woof decomposes an already-local epic or `work_units[]` backlog, it does not pull issues. The tools never call each other; the project composes them.

## 1. Principles

- **One engine.** Intake may vary, but execution does not. Once intake has produced executable `work_units[]`, every run follows the same produce/gate/review/fix/publish path.
- **Disk authority.** Files under `.woof/` are the source of truth. Live sessions are attached execution resources, not state authority.
- **Work units execute.** `work_units[]` are the executable units. An epic normally decomposes into work units; a supplied `work_units[]` backlog is already decomposed input.
- **Deterministic control.** Python owns graph progression, validation, gates, checks, audit, and publish/merge decisions. LLM workers produce or review bounded artefacts; they do not choose graph successors.
- **Policy over modes.** Repo policy declares delivery profile, producer/reviewer run profile, gate command, deterministic check floor, and cartography floor. These capabilities activate checks and context loading without creating separate engine paths.
- **Single source of truth.** Every concept has exactly one authoritative home and one bounded scope. Routing and run profiles are declared only in `.woof/policy.toml`; the executable unit has one schema; the dispatch registry owns harness, model, and effort vocabulary. A concept is never declared in two places, and a back-compat alias never outlives the change that introduces it.
- **Cartography remains first-class.** Cartography is a policy-enforced capability for context, impact analysis, and conformance checks. Lean runs may require less cartography; richer runs may require full structural cartography.
- **Warm producer, fresh reviewer.** The producer stays warm across bounded fix rounds. The reviewer is independent and fresh each round.
- **Evidence before confidence.** Reviewer blockers and deterministic findings cite resolvable evidence. Confidence-like metadata is advisory and never gate-affecting on its own.
- **Fail loud.** Missing, malformed, stale, or unsafe state fails preflight, opens a gate, or halts structurally.

## 2. Topology

| Layer | Responsibility | Implementation |
|---|---|---|
| State | Durable run, epic, work-unit, gate, audit, and cartography records. | `.woof/` in the consumer repo. |
| Engine | Intake, decomposition, graph progression, checks, gates, run lineage, publish/merge, and replay. | Python under `src/woof/`. |
| Dispatch substrate | Interactive TUI worker launch, prompt-file delivery, completion detection, output capture, and usage/session telemetry. | Shared `tmux_harness` package. |
| Operator surface | Human-facing command and skill entry points over the engine. | `woof` CLI and `/woof` skill. |
| Workers | Producer, reviewer, mapper, and enrichment agents. | Subscription CLI harnesses launched through tmux. |

The engine consumes structured dispatch results. It never parses raw terminal scrollback.

## 3. Intake

Intake produces executable `work_units[]` and run metadata. Source variation ends at intake.

### Epic-backed intake

Epic-backed intake accepts:

- a greenfield idea;
- a GitHub epic copied to local working state;
- a local docs-area epic.

The canonical flow is:

```text
epic-sparse.md -> optional brainstorm enrichment -> epic.md -> decompose -> work_units[]
```

`epic.md` may carry observable outcomes, contract decisions, acceptance criteria, cartography requirements, and policy hints. Brainstorm is an enrichment tool before decomposition; it is not an engine mode.

### Pre-decomposed intake

A supplied `work_units[]` backlog is legal input when the work has already been decomposed. Intake validates the schema and dependency graph, records run metadata, skips decomposition, and hands the units to the execution graph.

Pre-decomposed intake does not infer a missing epic, observable outcomes, contract decisions, or cartography requirements from the units.

## 4. Work Units

`work_units[]` are the single executable shape.

The work unit is the execution entity. Its `id` is stable and unique inside the
work-unit aggregate, not globally unique by itself. A work-unit aggregate owns
the ordered collection and enforces executable invariants: no duplicate work-unit
IDs, dependencies refer to units in the same aggregate, no self-dependencies, no
dependency cycles, and dependency order is topological.

Cross-aggregate identity is a structured reference, not an encoded string. The
aggregate context is a discriminated union, not an optional epic field:

```text
EpicWorkUnitContext  = {kind: "epic",          project_ref, epic_id}
SetWorkUnitContext   = {kind: "work_unit_set", project_ref, set_id, source_ref?}
QualifiedWorkUnitRef = {context: EpicWorkUnitContext | SetWorkUnitContext, work_unit_id}
```

Epic-backed plans use the epic context; pre-decomposed intake uses the work-unit-set
context until an upstream tracker epic exists. `set_id` is a stable domain or input
identity, assigned and persisted once at intake when a pre-decomposed source has no
natural identity; it is never a run UUID. Display strings may be derived from these
fields, but the fields remain the authority. UUIDs are for technical records such as
runs, attempts, reviews, and audit events, not for authored work-unit IDs.

Required fields:

- `id`
- `title`
- `kind`
- `state`
- `priority`

Common optional fields:

- `summary`
- `body`
- `bounded_context`
- `acceptance[]`
- `deps[]`
- `issue`
- `links{}`

Contract-trace fields are optional:

- `satisfies[]`
- `implements_contract_decisions[]`
- `uses_contract_decisions[]`
- `paths[]`
- `tests{count,types}`

When an epic carries observable outcomes or contract decisions, decomposition fills the trace fields and the relevant checks enforce them. When those inputs are absent, the same checks no-op.

The canonical schema lives in Woof. Vault overlays, pm-structure, and downstream tooling are drift-checked consumers.

## 5. Execution Kernel

For each ready work unit, in the aggregate's validated topological order:

1. Producer session starts or attaches.
2. Producer writes the implementation and expected artefacts.
3. Deterministic gate command and Woof checks run.
4. Independent reviewer inspects the diff and evidence.
5. Blocking findings are pasted back to the warm producer within the fix-round budget.
6. The unit is published through the configured profile.

The drain cycle is strictly serial. One invocation may advance many graph nodes for the active unit,
but after the unit's publish hand-off completes the cycle returns before any dependent or sibling
unit starts. If no pending unit is eligible, the kernel reports the directly blocked units and the
downstream pending units derived from the same validated order; it does not re-sort the aggregate or
sequence across aggregates.

The graph re-derives the next action from disk before each node. A run can resume from disk after process loss, operator handover, or machine restart.

### Producer Discipline and Playbooks

Decomposition prompt rules live in `playbooks/planning/breakdown.md`; architecture owns the contract, not prompt-level sizing prose.

Work-unit producer discipline remains tracer-bullet red-green-refactor: for each declared outcome, write one assertion-bearing RED test before implementation, make the smallest vertical GREEN slice pass, then refactor with tests as the harness. The horizontal-slicing anti-pattern is rejected because it tends to create the imagined-behaviour fingerprint: tests that mirror guessed structures or setup plumbing rather than proving the declared behaviour. The deterministic verification floor is the named Stage-5 work-unit check matrix (Checks 1-9). Runtime gates, checks, dispositions, events, and producer/reviewer playbooks key on the canonical `work_units[]` shape and `work_unit_id`.

## 6. Dispatch and Sessions

All LLM workers run as interactive TUIs under tmux. Headless `claude -p`, `codex exec`, or equivalent one-shot reasoning paths are not part of the build path.

The dispatch substrate owns:

- harness launch and readiness;
- prompt-file delivery with a short kickoff;
- completion detection by sentinel, idle, or harness-specific done marker;
- output capture and presentation-chrome stripping;
- structured verdict/evidence parsing;
- usage and session telemetry;
- process cleanup.

The structured result contract includes verdict, evidence, usage, session identity, artefact references, and completion classification.

### Warm Producer Seam

The producer session is an attached execution resource for the active work unit. It persists across bounded fix rounds so reviewer findings can be returned to the same context. If it dies, resume reconstructs from disk and reattaches or respawns.

The fix-round budget lives in `.woof/agents.toml` as `[fix_rounds].max_rounds_per_blocker`, defaulting to two rounds for the same blocker signature before the graph opens a human gate.

The reviewer is fresh each round. A reviewer session may stay warm only as a launch optimisation when its context is cleared and the full current diff is supplied again.

## 7. Policy and Cartography

Repo policy is stored in `.woof/policy.toml` and declares:

- profile (`A` worktree+PR, or `B` single-tree);
- repo root and toolchain root;
- project verification command;
- default base branch and GitHub repo;
- ready label and merge path groups;
- producer/reviewer run profile: slot -> harness/model/effort;
- deterministic check floor;
- cartography floor.

`policy.toml` is the single authority for delivery profile, producer/reviewer run profile (harness, model, effort), check floor, and cartography floor. Routing and run profiles are declared here and nowhere else. Other `.woof/` files own only their own bounded scope and never re-declare routing: `prerequisites.toml` owns host/tool/cartography prerequisite details, and `quality-gates.toml` owns named gate commands.

The cartography floor determines what preflight enforces and what context the engine loads. `none` loads no cartography; `design` loads only the design layer; `lexical` loads the design/AS-IS prose and lexical mechanical layer; `structural` currently reuses the lexical baseline until the structural index implementation lands. Cartography remains a capability of the same engine path:

- design docs and principles inform decomposition and checks;
- mapper-authored AS-IS docs inform producer and reviewer context;
- lexical files provide cheap path and identifier context;
- structural cartography provides impact context and conformance evidence when required.

Cartography is loaded through bounded, declared artefact references. Missing required cartography fails preflight or opens a structural halt.

## 8. Profiles

Profiles define publish and merge shape only. They do not change the engine path.

| Profile | Shape | Publish |
|---|---|---|
| A | Worktree per work unit, pull request per unit, serial merge coordinator. | Push branch, mark ready, rebase and merge ready queue in order. |
| B | Single checked-out tree. | Graph-owned commit and push. |

Both profiles run producer, deterministic checks, reviewer, fix rounds, and audit in the same order.

Before dispatching a ready Profile A unit, Woof runs the Profile A worktree preflight for that unit's
aggregate. Any worktree anomaly fails closed to a work-unit gate before provisioning, mutation,
recovery, or engine invocation.

Profile A merge is a deploy-aware transaction queue. After main moves, Woof waits for GitHub mergeability and
required-check recomputation to settle before attempting the next PR. After each merge, Woof waits for the
configured deploy-triggering checks to reach a terminal state before merging the next ready PR. Terraform
state-lock failures are retryable only when the check log proves lock contention; other deploy failures remain
terminal. Already-merged units are reconciled before any later terminal halt is reported.

The mergeability-settle and deploy-wait timeouts and the terminal deploy-check set are declared in repo policy;
preflight fails closed when Profile A deploy-aware merging is active and the deploy-check set is undeclared.
Shared-file sibling conflicts fail closed to a human gate with no automatic reapplication (ADR-016).

## 9. State and Audit

Woof records:

- source epic or pre-decomposed backlog reference;
- generated or supplied `work_units[]`;
- work-unit runtime state;
- gates and gate decisions;
- deterministic check results;
- review attempts and verdicts;
- producer/reviewer session references;
- publish/merge artefacts;
- node and transition JSONL;
- run lineage.

Per-attempt artefacts are immutable. A repeated review over the same diff hash and prompt version reuses the prior verdict. A conflicting verdict over the same inputs is recorded as review instability.

Dispatch events and attempt artefacts carry `run_id`, `work_unit_id`, and `attempt_id`. Review attempts are keyed by `work_unit_id`, staged `diff_hash`, and `prompt_version`; cache entries and instability records live under the epic's `.woof/epics/E<N>/reviews/` directory.

## 10. Gates and Checks

The deterministic gate floor runs before LLM review. Policy and epic content decide which checks are active:

- schema validation;
- dependency graph validation;
- repo verification command;
- path/scope checks;
- contract-trace checks when trace fields exist;
- cartography checks when policy requires cartography;
- conformance audit when policy requires it;
- publish/merge safety checks.

A gate is a durable state recorded on disk. Resolution is an explicit engine action with audited effect.

## 11. Operator Surface

`woof` is the CLI entry point for init, intake, run, gate resolution, observation, validation, baseline capture, and replay.

`/woof` is the operator skill over the CLI. It does not mutate `.woof/` directly and does not implement a second runner.

`/woof:brainstorm` remains the interactive enrichment path for sparse epics. It writes enrichment artefacts consumed by intake and decomposition.

## 12. Change Control

Planning artefacts for a specific epic are temporary. Decisions promote to ADRs, target-state changes promote to this architecture, new terms promote to `docs/CONTEXT.md`, and residual work promotes to `docs/backlog.md`. The per-epic plan is deleted after promotion.
