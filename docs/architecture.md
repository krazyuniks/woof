# Woof Architecture

This document is the system-design source of truth. It describes the target system. ADRs under `docs/adr/` carry the decisions and trade-offs; `docs/backlog.md` carries remaining work.

## 0. Scope

Woof is the orchestration engine for AI-assisted software delivery. It turns an epic or pre-decomposed `work_units[]` backlog into verified code changes through deterministic graph control, interactive LLM workers, project-owned verification, independent review, and auditable publish/merge steps.

The durable boundary is the operator home plus the target checkout. Woof assumes a Git worktree, per-project config and state under `~/.woof` (ADR-017), repo-local verification commands, and the operator's subscription CLI harnesses. A driven repository carries no trace of the engine: no config, planning, or tracking files. The engine owns harness adapters, launch, execution, readiness, output parsing, and validation. Repo policy owns which harness fills each worker slot plus optional model and effort overrides; omitted model and effort values resolve through the dispatch registry defaults for the selected harness.

Woof is one of the tools a project composes, and it owns neither the other tools nor the choice of them. A project composes a host-level worktree engine and an SDLC delivery tool; Woof is one such delivery tool, selected per run. It runs against whatever checkout it is pointed at and neither creates nor manages worktrees - the worktree engine provisions those and runs the project's registered lifecycle commands, orchestrated by the project's task runner; in Profile A the worktree is that engine's, never Woof's (ADR-015). External issue ingestion is upstream of Woof's intake: Woof decomposes an already-local epic or `work_units[]` backlog, it does not pull issues. The tools never call each other; the project composes them.

## 1. Principles

- **One engine.** Intake may vary, but execution does not. Once intake has produced executable `work_units[]`, every run follows the same produce/gate/review/fix/publish path.
- **Disk authority.** Files under `~/.woof/state/projects/<project-key>/` are the source of truth. Live sessions are attached execution resources, not state authority. Engine files never live in the driven repo (ADR-017).
- **Work units execute.** `work_units[]` are the executable units. An epic normally decomposes into work units; a supplied `work_units[]` backlog is already decomposed input.
- **Deterministic control.** Python owns graph progression, validation, gates, checks, audit, and publish/merge decisions. LLM workers produce or review bounded artefacts; they do not choose graph successors.
- **Policy over modes.** Repo policy declares delivery profile, producer/reviewer run profile, gate command, deterministic check floor, and cartography floor. These capabilities activate checks and context loading without creating separate engine paths.
- **Single source of truth.** Every concept has exactly one authoritative home and one bounded scope. Routing and run profiles are declared only in the project's `~/.woof/config/projects/<project-key>.toml`; the executable unit has one schema; the dispatch registry owns harness, model, and effort vocabulary. A concept is never declared in two places, and a back-compat alias never outlives the change that introduces it.
- **Cartography remains first-class.** Cartography is a policy-enforced capability for context, impact analysis, and conformance checks. Lean runs may require less cartography; richer runs may require full structural cartography.
- **Warm producer, fresh reviewer.** The producer stays warm across bounded fix rounds. The reviewer is independent and fresh each round.
- **Evidence before confidence.** Reviewer blockers and deterministic findings cite resolvable evidence. Confidence-like metadata is advisory and never gate-affecting on its own.
- **Fail loud.** Missing, malformed, stale, or unsafe state fails preflight, opens a gate, or halts structurally.

## 2. Topology

| Layer | Responsibility | Implementation |
|---|---|---|
| State | Durable run, epic, work-unit, gate, audit, and cartography records. | `~/.woof/state/projects/<project-key>/` in the operator home (ADR-017). |
| Engine | Intake, decomposition, graph progression, checks, gates, run lineage, publish/merge, and replay. | Python under `src/woof/`. |
| Dispatch substrate | Interactive TUI worker launch, prompt-file delivery, lifecycle observation, output capture, and usage/session telemetry. | Shared tmux and herdr transport packages behind the dispatch registry. |
| Operator surface | Human-facing command and skill entry points over the engine. | `woof` CLI and `/woof` skill. |
| Workers | Producer, reviewer, mapper, and enrichment agents. | Subscription CLI harnesses launched through the backend declared by their registry profile. |

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

All LLM workers run as interactive TUIs. Each harness profile declares tmux or herdr explicitly; project policy selects the harness, not its transport. Headless `claude -p`, `codex exec`, or equivalent one-shot reasoning paths are not part of the build path.

The dispatch substrate owns:

- harness launch and readiness;
- prompt-file delivery with a short kickoff;
- backend-specific lifecycle observation: tmux uses its declared prompt/files/markers, while herdr uses semantic status events plus the payload;
- output capture and presentation-chrome stripping;
- structured verdict/evidence parsing;
- usage and session telemetry;
- process cleanup.

The structured result contract includes verdict, evidence, usage, session identity, artefact references, and completion classification.

The producer/reviewer session contract is backend-neutral. A retained producer keeps the same worker identity across bounded fix rounds; every reviewer round receives a fresh independent worker. Herdr-backed turns arm lifecycle observation before prompt submission, complete on `working -> idle` or `done` with the payload present, and surface blocked and timeout as distinct graph outcomes. Tmux remains available for profiles whose TUI has no validated herdr lifecycle integration.

Herdr compatibility is established against the running named-session server reached through its explicit socket. Preflight records and validates server version and protocol. Development against a new protocol uses a disposable named session and never mutates an operator's active server.

### Warm Producer Seam

The producer session is an attached execution resource for the active work unit. It persists across bounded fix rounds so reviewer findings can be returned to the same context. If it dies, resume reconstructs from disk and reattaches or respawns.

The fix-round budget lives in the project config as `[fix_rounds].max_rounds_per_blocker`, defaulting to two rounds for the same blocker signature before the graph opens a human gate.

The reviewer is fresh each round. A reviewer session may stay warm only as a launch optimisation when its context is cleared and the full current diff is supplied again.

## 7. Policy and Cartography

Project policy is stored in `~/.woof/config/projects/<project-key>.toml` and declares:

- profile (`A` worktree+PR, or `B` single-tree);
- repo root and toolchain root;
- project verification command;
- default base branch and GitHub repo;
- Profile A worktree root and derivation when Profile A is selected;
- ready label and merge path groups;
- producer/reviewer run profile: slot -> harness, optional model, optional effort;
- deterministic check floor;
- cartography floor;
- native drain semantics shared by Woof and transitional drain consumers.

The project config is the single authority for delivery profile, producer/reviewer run profile (harness plus optional model and effort overrides), check floor, cartography floor, and drain semantics. Routing and run profiles are declared here and nowhere else. Harness, model, and effort vocabulary and defaults resolve through the dispatch registry. Prerequisite, gate, and fix-round scopes live as bounded sections of the same per-project config and never re-declare routing; transitional backlog executor metadata never carries drain policy. A missing project config is a hard preflight error — there is no in-repo fallback (ADR-017).

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
For Profile B, the work-unit completion marker is staged inside the graph-owned commit, and the
graph publishes that commit with `git push` when policy enables push. If commit or push fails, the
graph restores the pre-completion state so the unit is not durably marked done before its publish
transaction lands.

For Profile A, the graph commits the verified work-unit transaction, pushes the unit branch, creates
or reuses the pull request against `delivery.base_branch`, records the PR and issue linkage in
`epic.jsonl`, amends that graph metadata into the branch, and force-pushes with a lease pinned to the
initial published head. The ready label is applied only after the final push and only when the
deterministic gate is green and the reviewer critique is non-blocking.

Before dispatching a ready Profile A unit, Woof runs the Profile A worktree preflight for that unit's
aggregate. Any worktree anomaly fails closed to a work-unit gate before provisioning, mutation,
recovery, or engine invocation.

Profile A merge is a serial transaction queue. After the base branch moves, Woof rebases each ready PR on the
new tip, reruns the gate before merging, and waits for GitHub mergeability to settle before attempting the merge.
Transient `UNKNOWN` or `UNSTABLE` mergeability consumes a bounded retry budget and leaves the PR waiting if it
does not settle. The squash merge itself is also bounded-retried for GitHub `--match-head-commit` head-view lag
after the coordinator force-pushes the rebased head, using the Profile A `merge_attempts` and
`merge_interval_s` policy knobs. Terminal mergeability, gate, rebase, or exhausted merge failures halt the queue.
Already-merged units are reconciled before any later terminal halt is reported.

Deploy-aware merge pacing is part of the Profile A merge transaction when policy declares a terminal deploy-check set.
After the coordinator force-pushes a rebased ready PR, it waits for mergeability and the configured checks to recompute
to a successful terminal state before merging. After each successful deploy-triggering merge, it waits for those checks
on the base branch to reach a terminal state before considering the next ready PR. Proved Terraform state-lock contention
halts safely for operator handling; bounded retry is deferred behind explicit policy. Any unclassified terminal failure
halts with already-merged units reconciled and the remaining queue resumable.

Shared-file sibling conflicts fail closed to a human gate with no automatic reapplication (ADR-016). Ready PR metadata
carries the changed paths needed to classify a gate failure after clean rebase as a sibling conflict. Detected sibling
conflicts open a work-unit gate and append an idempotent corpus record to `sibling-conflicts.jsonl` in the project's
state root.

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

Dispatch events and attempt artefacts carry `run_id`, `work_unit_id`, and `attempt_id`. Review attempts are keyed by `work_unit_id`, staged `diff_hash`, and `prompt_version`; cache entries and instability records live under the epic's `epics/E<N>/reviews/` directory in the project's state root.

## 10. Gates and Checks

The deterministic gate floor runs before LLM review. Policy and epic content decide which checks are active:

- schema validation;
- dependency graph validation;
- repo verification command;
- path/scope checks;
- contract-trace checks when trace fields exist;
- cartography checks when policy requires cartography;
- review-size checks when the project config declares `[checks.review_size]`, counting only non-generated staged changed lines against the policy threshold while reporting excluded generated paths;
- conformance audit when policy requires it;
- publish/merge safety checks.

A gate is a durable state recorded on disk. Resolution is an explicit engine action with audited effect.

## 11. Operator Surface

`woof` is the CLI entry point. Its verbs are `init`, `wf` (graph run, intake, and reset), `observe`, `validate`, `check`, `baseline`, `dispatch`, `preflight`, `hooks`, `render-epic`, `audit-bundle`, and `check-cd`.

`/woof` is the operator skill over the CLI. It does not mutate engine config or state directly and does not implement a second runner.

`/woof:brainstorm` remains the interactive enrichment path for sparse epics. It writes enrichment artefacts consumed by intake and decomposition.

## 12. Change Control

Planning artefacts for a specific epic are temporary. Decisions promote to ADRs, target-state changes promote to this architecture, new terms promote to `docs/CONTEXT.md`, and residual work promotes to `docs/backlog.md`. The per-epic plan is deleted after promotion.
