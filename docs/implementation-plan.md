# Woof Implementation Plan

This document describes how the work in `docs/backlog.md` gets executed: epic sequencing, decision points, risk callouts, and the per-epic implementation plan template.

Authority: `docs/backlog.md` defines what to do; this file defines how to sequence it. Per-epic implementation plans live under `docs/plans/<epic>.md`, each written at the start of that epic.

## Execution principle

Each epic is broken into a sequence of small coding-agent prompts. A prompt is a focused, reviewable unit of work with explicit acceptance criteria. After each prompt, the operator inspects the diff and the test result before approving the next prompt. No epic runs end-to-end as a single dispatch.

The operator is the conductor; coding agents are instrument sections. The operator chooses pace, branching, and revision.

## Sequencing

```
                                E1 Foundation (graph library API)
                                       │
                          E2 Cartography prerequisite
                                       │
                              E3 Claude Code skill suite
                              (3.4 → 3.2 → 3.1 → 3.3)
                                       │
                    E4 Contract readiness and run resilience
                                       │
                              E5 Specwright bootstrap
                                       │
                             E6 Eval instrumentation
                                       │
                              E7 Baseline eval run
                                       │
                    E8 Contract conformance audit (post-baseline)
```

E1 unblocks everything else because the skill suite needs a stable graph API before it can safely own orchestration. E2 (cartography) follows E1 so preflight and setup can depend on the new graph boundary. E3 (skills) requires E1 and E2 in place. Inside E3, E3.4 (target-architecture authoring) must complete before E3.1 (setup) because setup invokes it; E3.2 (map-codebase) is independent of E3.1 but feeds E3.3 (run); E3.3 closes the suite. E4 then hardens the production run path with readiness, command-level baseline gates, blocker evidence checks, circuit-breaker policy, HEAD/branch drift detection, and tmux supervision.

E4 is the guardrail hardening pass: it adds the readiness boundary and the run-resilience controls learned from external project review. E5, E6, E7 are end-of-cycle; they validate the production shape rather than broaden it. E8 is deliberately post-baseline: add deterministic contract-vs-diff conformance only after the first measured run shows the production loop is stable.

## First Prompt Decision

The documentation checkpoint has settled the design baseline. The first coding-agent prompt should now target E1 and be the smallest, most reversible step that exposes the graph API boundary.

First prompt: add schemas and Python types for the `next-node` contract and state-token hashing, with focused tests and no CLI wiring yet. This is prompt 1 in `docs/plans/e1-foundation.md`.

Do not begin with a broad `woof graph` command port. The first prompt must define the contract and token boundary before moving existing behaviour across it.

## Development restart checkpoint

The Pickle Rick review changes later guardrails, not the first coding move. Reconvene development at E1 prompt 1.

Settled strategy:

- Keep graph authority in Python and keep `/woof:run` as the skill orchestrator.
- Keep Stage 2.5 readiness after `EPIC.md`, before breakdown.
- Put dispatch telemetry and HEAD/branch drift facts into E1 so E4 can use them without changing event contracts.
- Use blocker evidence, not confidence floors.
- Implement command-level baseline gates first; require structured parsers before claiming per-failure subtraction.
- Add deterministic conformance auditing only after the first production-shape baseline.

## Per-epic implementation plan template

Each epic gets a plan at `docs/plans/<epic-id>.md` written when the epic is about to start. The template:

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

The "Prompt sequence" is the most important section: it is the contract between the operator and the coding agent. Each row is one coding-agent invocation.

## Decision points

The operator makes a call at these points; the implementation plan stops and waits.

| Where | Decision |
|---|---|
| Before E1 starts | Approve E1's API-first prompt sequence in `docs/plans/e1-foundation.md` |
| Before E2 starts | Confirm cartography prerequisite is mandatory (no opt-out for "small" projects) |
| Before E3.4 starts | Confirm target-architecture skill scope; share GTS + Rust reference docs |
| Before E3.3 starts | Resolve OD-3 (codex dispatch mechanism) and OD-4 (reviewer subagent type) |
| Before E4 starts | Confirm tmux posture for long-run `/woof:run` supervision (OD-6) |
| Before E5 starts | Resolve specwright's TARGET-ARCHITECTURE.md (hand-author or skill-author) |
| Before E6 starts | Resolve OD-5 (eval harness home: Python bench or skill) |
| After E7 baseline | Decide whether E8 conformance audit is the first iteration target or whether per-node manifest data points elsewhere |

## Risk register

Top-level risks at the meta-execution layer. Per-epic plans have their own.

| Risk | Mitigation |
|---|---|
| E3.4 target-architecture skill is harder than estimated | High-level design only in initial pass; ship a stub template; iterate after specwright bootstrap |
| Skill orchestrator runs out of context window on a large epic | 1M Opus tier is the operating assumption; flag if any single epic approaches the limit; design carryover artefacts (handoff doc) before it bites |
| `claude -p` mode does not actually load LSP plugins as assumed | Verify before E3.3 starts; if it does not, change Stage-5 producer dispatch to use the Task tool with a Claude subagent rather than `claude -p` |
| Codex dispatch from inside the skill leaks operator context into the producer | OD-3 default (Bash + `codex exec` with explicit prompt-only invocation) is the safe option; document the isolation expectation |
| E1 accidentally preserves the old Python-owned dispatch loop | Split dispatch-shaped nodes from deterministic graph-owned nodes first; disallow LLM dispatch from `run-deterministic-node`; tests must prove dispatch nodes only return contracts |
| State changes while a dispatched model is running | `next-node` returns `state_token`; all mutation commands require it and fail compare-and-set on stale state |
| Eval result shows the new shape does not materially help with codex sprawl | Plan iteration: per-node manifest reveals next lever; not a blocker for shipping the design |
| Readiness gate becomes ceremony instead of signal | Keep checks deterministic and few: machinability, concrete refs, path/symbol resolution, and contract sufficiency only |
| Confidence becomes a soft gate that models can game | No confidence floor. Blockers require resolvable evidence; confidence, if added later, is advisory eval metadata only |
| Baseline mode overpromises per-failure subtraction | E4 implements command-level baseline first; structured parsers are required before fine-grained baseline subtraction |
| Dispatched worker changes branch or HEAD unexpectedly | E1 telemetry records HEAD/branch before/after; E4 opens a drift gate on unexplained movement |
| tmux mode grows into a second workflow surface | tmux may supervise processes and logs only; all state mutation still goes through typed `woof graph` commands |

## Active per-epic plan pointers

When an epic starts, link its plan here.

| Epic | Plan location | Status |
|---|---|---|
| E1 | `docs/plans/e1-foundation.md` | ready for review |
| E2 | `docs/plans/e2-cartography.md` | not started |
| E3.1 | `docs/plans/e3-1-setup-skill.md` | not started |
| E3.2 | `docs/plans/e3-2-map-codebase-skill.md` | not started |
| E3.3 | `docs/plans/e3-3-run-skill.md` | not started |
| E3.4 | `docs/plans/e3-4-target-arch-skill.md` | not started |
| E4 | `docs/plans/e4-contract-readiness-run-resilience.md` | not started |
| E5 | `docs/plans/e5-specwright-bootstrap.md` | not started |
| E6 | `docs/plans/e6-eval-instrumentation.md` | not started |
| E7 | `docs/plans/e7-baseline-eval.md` | not started |
| E8 | `docs/plans/e8-contract-conformance-audit.md` | post-baseline candidate |

## What happens next

1. Run E1 prompt 1: contract types and state-token hashing, no CLI wiring.
2. Review the diff and targeted validation.
3. Continue through the E1 prompt sequence in `docs/plans/e1-foundation.md`.
4. E2 (cartography) starts after E1 is complete; pattern repeats.

This document is updated as decisions resolve and epics complete.
