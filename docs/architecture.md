# Woof Architecture

This document is the system-design source of truth. It describes the target system. ADRs under `docs/adr/` carry the decisions and trade-offs; `docs/backlog.md` carries remaining work.

## 0. Scope

Woof is the orchestration engine for AI-assisted software delivery. It turns an epic or pre-decomposed `work_units[]` backlog into verified code changes through deterministic graph control, interactive LLM workers, project-owned verification, independent review, and auditable publish/merge steps.

The durable boundary is the consumer repository. Woof assumes a Git worktree, `.woof/` state, repo-local policy, repo-local verification commands, and the operator's subscription CLI harnesses. The engine owns harness adapters, launch, execution, readiness, output parsing, and validation. Repo policy owns only which harness/model/effort fills each worker slot.

## 1. Principles

- **One engine.** Intake may vary, but execution does not. Once intake has produced executable `work_units[]`, every run follows the same produce/gate/review/fix/publish path.
- **Disk authority.** Files under `.woof/` are the source of truth. Live sessions are attached execution resources, not state authority.
- **Work units execute.** `work_units[]` are the executable units. An epic normally decomposes into work units; a supplied `work_units[]` backlog is already decomposed input.
- **Deterministic control.** Python owns graph progression, validation, gates, checks, audit, and publish/merge decisions. LLM workers produce or review bounded artefacts; they do not choose graph successors.
- **Policy over modes.** Repo policy declares delivery profile, producer/reviewer run profile, gate command, deterministic check floor, and cartography floor. These capabilities activate checks and context loading without creating separate engine paths.
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

For each ready work unit, in dependency order:

1. Producer session starts or attaches.
2. Producer writes the implementation and expected artefacts.
3. Deterministic gate command and Woof checks run.
4. Independent reviewer inspects the diff and evidence.
5. Blocking findings are pasted back to the warm producer within the fix-round budget.
6. The unit is published through the configured profile.

The graph re-derives the next action from disk before each node. A run can resume from disk after process loss, operator handover, or machine restart.

### Producer Discipline and Playbooks

Decomposition prompt rules live in `playbooks/planning/breakdown.md`; architecture owns the contract, not prompt-level sizing prose.

Work-unit producer discipline remains tracer-bullet red-green-refactor: for each declared outcome, write one assertion-bearing RED test before implementation, make the smallest vertical GREEN slice pass, then refactor with tests as the harness. The horizontal-slicing anti-pattern is rejected because it tends to create the imagined-behaviour fingerprint: tests that mirror guessed structures or setup plumbing rather than proving the declared behaviour. The existing deterministic verification floor remains Checks 1-9 until the schema-unification work renames those checks around `work_units[]`.

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

Specialised `.woof/` files remain subordinate policy inputs: `agents.toml` supplies compatibility defaults for legacy route fields, `prerequisites.toml` supplies host/tool/cartography prerequisite details, and `quality-gates.toml` supplies named gate commands. `policy.toml` is the spine that selects the delivery, run-profile, check, and cartography floors, including producer/reviewer harness, model, and effort.

The cartography floor determines what preflight enforces and what context the engine loads. Cartography remains a capability of the same engine path:

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

Profile A merge is a deploy-aware transaction queue. After main moves, Woof waits for GitHub mergeability and
required-check recomputation to settle before attempting the next PR. After each merge, Woof waits for the
configured deploy-triggering checks to reach a terminal state before merging the next ready PR. Terraform
state-lock failures are retryable only when the check log proves lock contention; other deploy failures remain
terminal. Already-merged units are reconciled before any later terminal halt is reported.

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
