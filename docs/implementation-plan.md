# Woof Implementation Plan

This document describes how the open work in `docs/backlog.md` gets sequenced. The backlog defines what to do; this file defines the order and the operating rules for running the work.

Per-epic implementation plans live under `docs/plans/<epic>.md` and are written only when that epic starts.

## Execution Principle

Work moves in small, reviewable coding-agent prompts. Each prompt has clear acceptance criteria, a focused diff, and a verification step. The operator reviews the result before the next prompt runs.

Do not use this document to preserve speculative architecture. If a direction is withdrawn, delete its plan and remove it from the sequence.

## Sequence

```text
E1 Cartography prerequisite
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
```

E1 gives dispatched work stable codebase context. E2 hardens the current `woof wf` runner with readiness, gate, telemetry, and drift controls. E3 proves a real consumer can be prepared without extra operator ceremony. E4 and E5 measure the production shape before new optimisation work. E6 is post-baseline, so the first eval can show whether the conformance audit is the right next lever.

The old `woof graph` API plan and split-skill suite are withdrawn. New work builds against `woof wf`, `/woof`, and `/woof:brainstorm`.

## First Prompt

The next coding prompt should start E1 with the smallest useful contract:

- add the `[cartography]` schema fields and fixtures;
- make `woof preflight` report missing/stub cartography clearly;
- update the `/woof` setup/map-codebase references to match the checked contract;
- add focused tests for the new preflight outcomes.

Do not build a new skill or a new graph command to start E1.

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

E1 is active. Its plan is `docs/plans/e1-cartography.md`. Prompts 1-3 have landed: the `[cartography]` contract plus missing/stub preflight enforcement (prompt 1), the non-blocking stale-`freshness.json` warning keyed on `staleness_floor_hours` (prompt 2), and the per-language `refresh-cartography` fragments with `woof init --language` composition, the `freshness.json` schema (`{ts, git_ref, age_s, generator_version}`), and the `ts`-authoritative freshness reader (prompt 3). Prompts 4-5 cover the fail-loud post-commit hook and blanket enforcement for cartography-less consumers.

## What Happens Next

1. Run E1 prompt 4: make the Woof post-commit hook regenerate the mechanical layer by running `./scripts/refresh-cartography` and fail loud (non-zero hook exit) when the refresh script exits non-zero, rather than the current best-effort `[ -x ... ] && ...` no-op.
2. Review the diff and targeted tests before continuing.

This document is updated only when sequencing or active plan pointers change.
