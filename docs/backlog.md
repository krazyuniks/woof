# Woof Backlog

This is the single source of truth for open Woof work. It is prescriptive: the items below describe work to do, not work that has already been done.

Authority: this file plus the ADRs and `docs/architecture.md` define the project. Anything not described here is not part of the plan.

## Shape

Woof is an inner-loop SDLC tool for AI-assisted development.

- **Operator surface.** One umbrella `/woof` skill over the `woof` CLI, plus the interactive `/woof:brainstorm` design specialist (ADR-007). Setup, map-codebase, run, and gate flows live as references inside the umbrella.
- **No split-skill suite.** `/woof:setup`, `/woof:map-codebase`, `/woof:run`, and a separate target-architecture skill are withdrawn. Useful workflow guidance belongs in the `/woof` umbrella and its references.
- **No `woof graph` command API.** The shipped runner is `woof wf`. It owns graph progression, dispatch, deterministic nodes, gate writes, and resume behaviour in-process.
- **Setup CLI.** `woof init`, `woof preflight`, and `woof hooks install` are non-interactive Python commands used during project setup. They are not parallel surfaces for running epics.
- **State on disk.** `spark.md`, `EPIC.md`, `plan.json`, JSONL audit, dispositions, gate files, and cartography artefacts are the authoritative state. Re-running `woof wf --epic N` resumes from disk.
- **Interactive design.** `/woof:brainstorm` hydrates an existing spark by writing an accepted bundle into `.woof/epics/E<N>/discovery/brainstorm/`. Woof ingests that bundle as discovery source material; it does not mechanically map `work_units[]` to stories.
- **Cartography prerequisite.** Every consumer repo has `.woof/codebase/` containing human-authored design docs, mapper-authored AS-IS docs, and a mechanical index.
- **Evidence over confidence.** Reviewer blockers must carry resolvable evidence. Confidence scores, if ever added, are advisory eval metadata and never gate-affecting.
- **Expert workstation posture.** Woof assumes explicit local prerequisites. tmux may supervise long runs, but it must not become a second graph or state authority.

## Implementation Approach

Each epic is broken into small, reviewable coding-agent prompts when it starts. The operator reviews each prompt's output before the next prompt runs.

Per-epic plans live under `docs/plans/` only when an epic is active. Do not keep speculative plans for withdrawn directions.

## Epics

### E1. Cartography Prerequisite

Make the cartography artefact group mandatory infrastructure with preflight enforcement and per-language refresh templates.

The contract and missing/stub enforcement landed in prompt 1 (`docs/plans/e1-cartography.md`): the `[cartography]` schema shape (`staleness_floor_hours`, `summary_min_chars`, `languages`, `stub_marker`); enforcement keyed on the block's presence and scaffolded by `woof init`; `woof preflight` failing closed on a missing/non-executable `scripts/refresh-cartography`, a missing or stub `TARGET-ARCHITECTURE.md`/`PRINCIPLES.md`, or a missing mechanical-layer file (`tags`, `files.txt`, `freshness.json`); exact stub detection (removable stub marker, or body below `summary_min_chars` unless front matter marks it complete); and the `tree.txt` -> `files.txt` rename.

Open work:
- Treat stale `freshness.json` beyond the declared floor as a warning with a refresh prompt, not a blocker.
- Ship per-language `refresh-cartography` templates referenced from `languages/<lang>.toml`. Initial set: Python, Go, TypeScript, Rust.
- Make `woof init` compose the per-language fragments into the consumer's `scripts/refresh-cartography` idempotently.
- Make the post-commit hook regenerate the mechanical layer and fail loud if the refresh script exits non-zero.
- Define `freshness.json`: `{ ts, git_ref, age_s, generator_version }`.
- Move from opt-in (`[cartography]` present) to a clear preflight error for an existing consumer with no cartography at all, pointing at the `/woof` setup/map-codebase references.

Depends on: nothing. Blocks: E2, E3.

### E2. Contract Readiness and Run Resilience

Add operational guardrails around the current `woof wf` runner without splitting the operator surface.

Open work:
- Add a deterministic Stage-2.5 readiness node after `EPIC.md` exists and before `breakdown_planning`.
- Add `readiness-result.schema.json` and gate support for `readiness_gate`.
- Readiness checks:
  - every observable outcome has a machine-checkable acceptance signal;
  - acceptance criteria avoid pure subjective prose unless paired with concrete assertions or commands;
  - contract decisions include exact paths, schema refs, API refs, or explicit forward-created markers;
  - referenced existing paths resolve against `git ls-files`;
  - cited symbols resolve where Woof can cheaply prove them, otherwise require explicit operator-marked forward creation;
  - Stage 3 receives enough information to decompose without inventing interfaces.
- Define the forward-created annotation grammar exactly: `` `path/or/symbol` (forward-created) `` or `` `path/or/symbol` (created by ticket <id>) ``.
- Readiness checker timeouts produce non-blocking performance findings. A readiness gate must not block solely because its own checker timed out.
- Add readiness recycle escalation: after a configured number of failed readiness cycles, open an escalation-flavoured readiness gate rather than looping revise/fail indefinitely.
- Add audited readiness-gate resolutions: `revise_epic_contract`, `approve_with_reason`, `abandon_epic`.
- Add blocker-evidence discipline to `critique.schema.json`, prompts, and checks:
  - blocker findings require `evidence`;
  - evidence must resolve to a known artefact reference: file:line, story id, observable outcome id, contract-decision id, schema ref, or quality-gate id;
  - extend `check_6_critique_blocker` so unresolvable blocker evidence is itself a blocker;
  - do not add confidence-based gating.
- Add `quality-gates.toml` mode support:
  - `strict`: any failure blocks;
  - `baseline`: command-level baseline for arbitrary gates; a command already red at capture time is reported but does not block unless its configured command identity changes or the baseline expires.
- Add baseline freshness metadata with wall-clock age and graph/run-iteration age, plus explicit recapture semantics.
- Add run-resilience policy over `woof wf` dispatch telemetry:
  - stage-aware progress detection;
  - separate counters for consecutive no-progress and consecutive same-error signatures;
  - same-error checked before no-progress;
  - error-signature normalisation that strips volatile paths, line/column spans, ISO timestamps, UUIDs, and excess whitespace, preserves standalone numbers, and truncates to a bounded length;
  - course-correction gates for repeated constraint/invariant/contract-discovery signatures;
  - run-resilience gates for repeated timeout without expected artefact progress;
  - separate classification for rate-limit wait/resume.
- Add HEAD/branch drift detection:
  - record `head_before`, `branch_before`, `head_after`, and `branch_after` for dispatched work;
  - halt commit/gate paths if HEAD or branch moved unexpectedly and not through a graph-owned commit.
- Add optional tmux-backed long-run supervision for `woof wf`: panes/logs/status only; no direct state mutation outside Woof commands.
- Tests covering readiness pass/fail, readiness timeout non-blocking behaviour, readiness escalation, readiness gate resolution, blocker evidence resolution, command-level baseline behaviour, baseline freshness/recapture, HEAD/branch drift, circuit-breaker decision logic, and tmux command construction without graph bypass.

Depends on: E1. Blocks: E3, E4, E5.

### E3. Specwright Bootstrap

Make specwright ready as the first real consumer for the production-shape eval.

Open work:
- Author `specwright/.woof/codebase/TARGET-ARCHITECTURE.md` by hand or through the `/woof` setup reference.
- Author `specwright/.woof/codebase/PRINCIPLES.md`.
- Run the `/woof` map-codebase flow against specwright to produce the AS-IS docs.
- Add `specwright/scripts/refresh-cartography` from the Python language template.
- Install the Woof post-commit hook.
- Verify preflight and Stage-2.5 readiness pass against specwright.

Depends on: E1, E2.

### E4. Eval Instrumentation

Measure the production shape: `/woof` as the operator surface, `woof wf` as the runner, cartography on disk, dispatched producer/reviewer work, readiness, run-resilience telemetry, drift detection, blocker-evidence checks, and JSONL audit.

Open work:
- Add `node_type` to every dispatch event so per-node attribution is possible.
- Include dispatch-return outcome fields in the manifest: `exit_type`, `exit_code`, `error_signature`, expected artefact presence/schema status, rate-limit classification, and HEAD/branch before/after.
- Include run-resilience rows for circuit-breaker decisions, no-progress/same-error counters, readiness outcomes, baseline-gate mode/outcome, blocker-evidence resolution outcomes, and drift events.
- Retain `artefacts_loaded[]` per event in any compacted manifest produced by the eval harness.
- Persist prompt and output bodies into the eval output directory at run teardown.
- Add per-node rows in the comparison markdown: calls, tokens, prompt bytes, artefact bytes, and duration.
- Add an artefact-reload section to the manifest: `{artefact_path: [(call_index, node_type, bytes)]}`.
- Keep the Python bench as the eval harness; `/woof` may run it through Bash, but evals do not require a separate skill.

Depends on: E2.

### E5. Baseline Eval Run

Run the eval against specwright in the production shape and capture the first manifest as a stable baseline for future iteration.

Open work:
- Single-variant run against specwright at HEAD.
- Inspect the per-node manifest; confirm `node_type` correlation works and artefact reloads surface duplication.
- Decide the first optimisation target from the data.

Depends on: E3, E4.

### E6. Deterministic Contract Conformance Audit

Add a post-baseline conformance audit inspired by Pickle Rick's Citadel shape without copying its project-specific analysers.

Open work:
- Add a diff-scoped conformance audit node or Stage-5 check that reads `EPIC.md`, `plan.json`, the story diff, and cartography.
- Add `conformance-result.schema.json`.
- Audit questions:
  - every satisfied observable outcome has production evidence in changed files, not only test markers;
  - every implemented contract decision's declared surface exists in the diff or current tree;
  - declared guards, permissions, state transitions, or lifecycle invariants from cartography are not bypassed;
  - new allowlist/config entries have production callers or explicit rationale;
  - domain-specific checks are consumer-supplied rules, not hardcoded Woof assumptions.
- Start with generic deterministic checks and a consumer-rule hook. Add language/framework analysers only when a real consumer proves the rule has reusable value.
- The audit can open a story gate on deterministic high-severity violations. It must not become an LLM debate loop.

Depends on: E1, E2, E5.

## Settled Choices

- Runtime action safety is trusted-local plus commit-safety, audit, and drift detection. Woof does not add a preventive sandbox unless real usage proves detection is insufficient.
- The eval harness stays in Python. `/woof` can launch it, but there is no `/woof:eval` skill.
- tmux is optional for long runs and only supervises `woof wf`; it does not own workflow state.
- There is no active plan to build `woof graph` or split `/woof` into peer skills.

## Glossary

- **Cartography** - the artefact group at `.woof/codebase/`: durable design docs, mapper-authored AS-IS docs, and mechanical files.
- **Mechanical layer** - `tags`, `files.txt`, and `freshness.json`; cheap, post-commit refreshed, and gitignored.
- **Producer** - the LLM-dispatched node that creates an artefact for a graph stage.
- **Reviewer** - the LLM-dispatched node that critiques the producer's output in an isolated context.
- **Mapper subagent** - a Claude subagent launched by the `/woof` map-codebase flow to author one or two cartography docs.
- **Readiness gate** - deterministic halt after Stage 2 when `EPIC.md` is not concrete enough for planning.
- **Strict quality gate** - quality gate mode where any failure blocks.
- **Baseline quality gate** - quality gate mode where a pre-existing red command is recorded and only deterioration blocks.
- **Blocker evidence** - machine-resolvable evidence attached to a blocker finding, such as file:line, story id, outcome id, contract-decision id, schema ref, or gate id.
- **HEAD/branch drift** - unexpected git position movement during dispatch or commit that is not explained by a graph-owned commit.
- **Conformance audit** - deterministic diff-scoped audit that checks implemented production changes against `EPIC.md`, plan contracts, and consumer invariants.
