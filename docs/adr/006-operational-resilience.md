---
type: adr
status: superseded by ADR-012 and ADR-013
date: 2026-05-30
---

# ADR-006: Operational Resilience Guardrails

Superseded by ADR-012 and ADR-013. Resilience remains part of the target, but dispatch supervision and rigour activation now sit behind the tmux harness and repo policy.

## Context

Woof's pivot keeps workflow authority in a deterministic graph and leaves LLMs as typed
producer, reviewer, and mapper workers. The graph boundary is right, but long-running
agentic delivery still needs operational guardrails: early contract readiness, rich
dispatch telemetry, runaway protection, baseline-aware quality gates, reviewer
signal hygiene, and later deterministic conformance audits.

The project is for expert backend operators. Woof may assume an opinionated local
workstation, including tools such as tmux when they improve supervision. Those tools
must not become a second source of workflow truth.

## Decision

Woof adds operational resilience around the graph, not inside model reasoning loops.

1. A deterministic contract-readiness node runs after Stage 2 definition and before
   Stage 3 breakdown. This is still early in the lifecycle: it is the first point at
   which Woof has a structured `EPIC.md` contract to validate. Running the same check
   immediately after epic creation would only inspect the raw spark, which is too thin
   to prove acceptance criteria, contract decisions, path references, or machinability.
2. Dispatch telemetry is part of the strict graph contract. The engine records outcome
   and progress signals, not only token and byte counts. Minimum fields:
   exit type, exit code, normalised error signature when available, HEAD and branch
   before/after, expected artefact presence, expected artefact schema status,
   rate-limit metadata, duration, loaded artefacts, bytes, tokens, and command count
   when available.
3. Runaway protection belongs around the current `woof wf` runner, not in a separate
   `/woof:run` skill. The engine records the facts; Woof can pause the run and
   open a human gate when repeated no-progress or same-error patterns appear. Progress
   is stage-aware: planning stages make progress by writing expected `.woof/` artefacts;
   story stages make progress by writing expected outputs and eventually advancing the
   graph-owned commit. Same-error and no-progress counters are separate. Constraint
   discovery routes to a course-correction gate rather than being treated as a stuck
   worker. The graph still owns durable state mutation and gate records.
4. Quality gates support strict and baseline modes. Strict mode requires zero failures.
   Baseline mode starts with command-level baselines because Woof gates are arbitrary
   shell commands today. Fine-grained per-failure subtraction is only available for
   gates that declare a structured parser or machine-readable output. Baselines have
   both wall-clock and graph-iteration freshness limits and can be recaptured only by
   explicit operator action. Known-flake allowlists are deferred until failures are
   structured enough to expire and report them without becoming a quiet bypass.
5. Reviewer findings carry severity and evidence. A blocker must cite concrete evidence
   that Woof can resolve, such as a file:line reference, story id, observable outcome id,
   contract-decision id, schema ref, or quality-gate id. Confidence is not a gate input.
   If a confidence field is added later, it is advisory metadata for evals and triage
   only; it must never suppress a blocker with plausible severe impact.
6. tmux is allowed as an operator supervision and presentation layer for long-running
   Woof sessions. It may host panes, monitors, logs, and child processes. It does not
   own graph state, choose successors, or mutate `.woof/` outside Woof commands.
7. HEAD and branch drift are detected, not silently tolerated. Dispatch telemetry records
   the observed git position before and after a worker. The graph can open a gate when
   the branch or HEAD moves in a way not explained by a graph-owned commit.
8. Citadel-style deterministic conformance auditing is a later extension. The
   transferable idea is not Pickle Rick's domain-specific analyzers; it is the shape:
   a diff-scoped audit that checks implemented production changes against the epic's
   observable outcomes, contract decisions, and consumer-supplied invariants.

## Consequences

- The lifecycle gains a Stage 2.5 readiness boundary. This changes graph topology and
  therefore needs schemas, tests, and documentation when implemented.
- The run-resilience work must include enough dispatch-return contract shape for
  circuit-breaker, rate-limit handling, and HEAD/branch drift detection. This is not
  a future embellishment; it is part of the graph contract.
- `woof wf` can grow tmux-backed long-run supervision without creating a parallel
  orchestration authority.
- Baseline quality gates need durable baseline records and freshness rules. E4 should
  not claim per-failure subtraction unless the gate has a declared parser or structured
  output.
- Reviewer prompts and critique schemas need evidence discipline, not confidence
  gating.
- A later conformance-audit epic can use cartography and consumer rules to add
  deterministic contract-vs-diff checks.
- Woof still rejects model-to-model debate loops as the default remediation path.
  Blockers open human gates.
