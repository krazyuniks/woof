# Woof Implementation Plan

This document describes how the open work in `docs/backlog.md` gets sequenced. The backlog defines what to do; this file defines the order and the operating rules for running the work.

Per-epic implementation plans live under `docs/plans/<epic>.md` and are written only when that epic starts.

## Execution Principle

Work moves in small, reviewable coding-agent prompts. Each prompt has clear acceptance criteria, a focused diff, and a verification step. The operator reviews the result before the next prompt runs.

Do not use this document to preserve speculative architecture. If a direction is withdrawn, delete its plan and remove it from the sequence.

## Sequence

```text
E7 Dispatch process supervision   (active; refines E2/E4 telemetry, blocks nothing in the chain)
        |
E1 Cartography prerequisite       (prompts 4-5 deferred, not dropped)
        |
E2 Contract readiness and run resilience
        |
E3 Specwright bootstrap
        |
E4 Eval instrumentation
        |
E5 Baseline eval run
        |
E6 Contract conformance audit

Follow-on (sequenced after E7, off the critical chain):
E8 Run lineage  ->  E9 Producer-output recovery
E10 Plan-graph algorithms   (independent; gated only by a real consumer need, not by E8/E9)
```

E7 corrects per-dispatch outcome classification (the hanging-but-done -> timeout bug) so that E2's run-resilience counters and E4's eval summaries consume trustworthy `exit_type` signal; it depends on nothing and was chosen as the active epic ahead of E1's remaining prompts. E1 gives dispatched work stable codebase context. E2 hardens the current `woof wf` runner with readiness, gate, telemetry, and drift controls. E3 proves a real consumer can be prepared without extra operator ceremony. E4 and E5 measure the production shape before new optimisation work. E6 is post-baseline, so the first eval can show whether the conformance audit is the right next lever. E8 threads run lineage; E9 builds producer-output recovery on top of lineage and E7; E10 swaps the hand-rolled plan-DAG check for graph-library algorithms and is independent of E8/E9.

The old `woof graph` API plan and split-skill suite are withdrawn. New work builds against `woof wf`, `/woof`, and `/woof:brainstorm`.

## Historical: E1 first prompt

E1's first prompt (the smallest useful cartography contract: schema fields, preflight missing/stub reporting, `/woof` reference updates, focused tests) has landed, along with prompts 2-3. See Active Per-Epic Plan Pointers for the current active epic.

## Per-Epic Plan Template

```markdown
# E<n>. <Epic title>

## Goal
One paragraph stating what done looks like.

## Stories
| ID | Story | Acceptance criteria |
|---|---|---|

## Prompt sequence
| # | Prompt summary | Files touched | Review checkpoint |
|---|---|---|---|

## Risk register
- <Risk>: <Mitigation>

## Decisions resolved during the epic
| ID | Decision | Resolution |
|---|---|---|

## Out of scope
- <Item>

## Done definition
- All stories' acceptance criteria met.
- All review checkpoints passed.
- All decisions in the table resolved.
```

The prompt sequence is the contract between the operator and the coding agent. Keep each row small enough to review.

## Decision Points

Current decisions are already recorded in the backlog's Settled Choices. The sequence pauses only for concrete product calls:

| Where | Decision |
|---|---|
| Before E3 starts | Confirm specwright is still the first production-shape consumer. |
| After E5 baseline | Decide whether E6 conformance audit is the first optimisation target or whether eval data points elsewhere. |

## Risk Register

| Risk | Mitigation |
|---|---|
| Cartography becomes ceremony instead of useful context | Keep required docs few and practical; fail on missing/stub state, warn on stale mechanical state. |
| Readiness gate blocks on subjective quality | Keep checks deterministic: machinability, concrete refs, path/symbol resolution, and contract sufficiency only. |
| Baseline mode overpromises per-failure subtraction | E2 implements command-level baselines first; structured parsers are required before fine-grained subtraction. |
| Dispatched worker changes branch or HEAD unexpectedly | E2 records HEAD/branch before and after dispatch and opens a drift gate on unexplained movement. |
| tmux grows into a second workflow | tmux may supervise panes, logs, and child processes only. State still changes through Woof commands. |
| Eval result shows a different bottleneck than expected | Use the E5 manifest to choose the next optimisation; do not pre-commit to context-scoping changes. |

## Active Per-Epic Plan Pointers

**E7 (Dispatch Process Supervision) is active.** Its plan is `docs/plans/e7-process-supervision.md` and its decision is ADR-008. It was chosen as the next epic ahead of E1's remaining prompts because it corrects a per-dispatch outcome-classification bug (hanging-but-done worker reported as a timeout and gated as a crash) that all downstream telemetry consumers depend on.

E1 (Cartography Prerequisite) is **parked: deferred, not dropped**. Its plan is `docs/plans/e1-cartography.md`; prompts 1-3 have landed (the `[cartography]` contract plus missing/stub preflight enforcement, the non-blocking stale-`freshness.json` warning, and the per-language `refresh-cartography` composition with the `ts`-authoritative reader). E1 prompts 4-5 (fail-loud post-commit hook; blanket enforcement for cartography-less consumers) resume after E7 completes.

## What Happens Next

Confirmed E7-first ordering (operator-approved):

1. Sequencing patch (this doc): E7 active, E1 prompts 4-5 parked. **Landed.**
2. E7 prompt 0: ADR-008 + architecture cross-reference. **Landed.**
3. E7 prompt 1: the `supervise()` primitive - phase-scoped clocks (idle and wall-clock pre-terminal; completion-grace and tail cap post-terminal), process-group reaping, bounded streamed capture - with fault-injection unit tests against real fake-agent scripts.
4. E7 prompts 2-4: wire `cmd_dispatch` (and update `observe`/bench), add the shared node-layer dispatch-result classifier, then the end-to-end fault-injection integration matrix.
5. Return to E1 prompts 4-5 (fail-loud post-commit hook; cartography-less onboarding error).

Review the diff and targeted tests before each next prompt runs. This document is updated only when sequencing or active plan pointers change.
