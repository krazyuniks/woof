# Woof Implementation Plan

This document describes how the open work in `docs/backlog.md` gets sequenced. The backlog defines what to do; this file defines the order and the operating rules for running the work.

Per-epic implementation plans live under `docs/plans/<epic>.md` and are written only when that epic starts.

## Execution Principle

Work moves in small, reviewable coding-agent prompts. Each prompt has clear acceptance criteria, a focused diff, and a verification step. The operator reviews the result before the next prompt runs.

Do not use this document to preserve speculative architecture. If a direction is withdrawn, delete its plan and remove it from the sequence.

## Sequence

```text
E2 Contract readiness and run resilience
        |
E3 Specwright bootstrap
        |
E4 Eval instrumentation
        |
E5 Baseline eval run
        |
E12 Structural cartography index
        |
E13 Stage-5 impact context integration
        |
E6 Contract conformance audit

Completed prerequisite:
E7 Dispatch process supervision   (complete; ADR-008; per-epic plan removed)
E1 Cartography prerequisite       (complete; prompts 1-5 landed)

Follow-on (off the critical chain):
E8 Run lineage  ->  E9 Producer-output recovery
E10 Plan-graph algorithms   (independent; gated only by a real consumer need, not by E8/E9)
E11 MP engineering review imports (P0.1/P0.2 pull-forward only; P0.4 deferred review)
E14 Ranked and semantic cartography retrieval
E15 Structural onboarding and mapper grounding
```

E7 is complete: per-dispatch supervision now classifies hanging-but-done workers as `completed_lingering`, emits trustworthy `exit_type` telemetry, and leaves the detailed behaviour in ADR-008 plus architecture s11.5. E1 is complete: prompts 1-5 give dispatched work stable codebase context, including a hard onboarding error for legacy consumers without `[cartography]`. E2 is the next active line and hardens the current `woof wf` runner with readiness, gate, telemetry, and drift controls. E3 proves a real consumer can be prepared without extra operator ceremony. E4 and E5 measure the production shape before new optimisation work.

After E5, the next planned optimisation pivot is structural cartography. E12 builds the ADR-009 local files/symbols/edges index, starting with an audit of any concurrent tree-sitter/parser work so Woof does not grow two extraction paths. E13 uses that index first in the Stage-5 reviewer path as bounded impact context. E6 then runs with structural evidence available for conformance checks, rather than trying to infer callers and affected surfaces from prose alone.

E8 threads run lineage; E9 builds producer-output recovery on top of lineage and completed E7 process supervision; E10 swaps the hand-rolled plan-DAG check for graph-library algorithms and is independent of E8/E9. E11 captures the MP engineering review imports; its P0.1/P0.2 items may be batched into active prompts when they touch the same files, but P0.3 starts only after the readiness and structural-cartography paths are stable and P0.4 is a lower-priority review item. E14 and E15 carry the remaining code-mapping research recommendations - ranking/semantic retrieval and onboarding/mapper grounding - and should start only after E12/E13 produce measured value or E5 shows they are the stronger bottleneck.

The old `woof graph` API plan and split-skill suite are withdrawn. New work builds against `woof wf`, `/woof`, and `/woof:brainstorm`.

## Historical: E1 first prompt

E1's first prompt (the smallest useful cartography contract: schema fields, preflight missing/stub reporting, `/woof` reference updates, focused tests) landed, along with prompts 2-5. See Active Per-Epic Plan Pointers for the current active epic.

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
| After E5 baseline | Confirm structural cartography remains the first optimisation target; if eval data points elsewhere, reorder E12/E13 explicitly. |
| Before E12 starts | Audit the concurrent tree-sitter/parser work and decide whether E12 reuses it, finishes it, or starts with a Python `ast` extractor behind the same adapter boundary. |
| Before E14 starts | Decide whether semantic retrieval needs embeddings or whether BM25 plus structural ranking is enough for the first pass. |
| Before E15 starts | Confirm there is a real onboarding target large enough to justify mapper grounding and community/hub analysis. |
| Before E11 P0.3 starts | Decide whether prototype/diagnose should be a dedicated epic or folded into the next readiness/recovery pass. |
| Before E11 P0.4 starts | Review whether codebase-deepening and zoom-out flows are still worth building, based on cheap-heuristic results and eval data. |

## Risk Register

| Risk | Mitigation |
|---|---|
| Cartography becomes ceremony instead of useful context | Keep required docs few and practical; fail on missing/stub state, warn on stale mechanical state. |
| Readiness gate blocks on subjective quality | Keep checks deterministic: machinability, concrete refs, path/symbol resolution, and contract sufficiency only. |
| Baseline mode overpromises per-failure subtraction | E2 implements command-level baselines first; structured parsers are required before fine-grained subtraction. |
| Dispatched worker changes branch or HEAD unexpectedly | E2 records HEAD/branch before and after dispatch and opens a drift gate on unexplained movement. |
| tmux grows into a second workflow | tmux may supervise panes, logs, and child processes only. State still changes through Woof commands. |
| Eval result shows a different bottleneck than expected | Use the E5 manifest to choose the next optimisation; do not pre-commit to context-scoping changes. |
| Concurrent tree-sitter work conflicts with E12 | E12 starts with an audit and reuse decision; no second parser/indexer path lands without an ADR-009 update. |
| Structural index looks precise but is heuristic | Store provenance/confidence on every edge; prompts and gates treat heuristic/ambiguous edges as leads requiring source evidence. |
| Structural cartography becomes a parallel graph surface | Only `woof cartography` reads the index. It does not mutate workflow state, expose MCP, or create a `woof graph` API. |
| MP comparison imports expand into a second skill suite | Keep imports inside `/woof`, `/woof:brainstorm`, playbooks, schemas, and deterministic checks. Do not add parallel tracker or split-skill surfaces. |

## Active Per-Epic Plan Pointers

**E1 (Cartography Prerequisite) is complete.** Its plan is `docs/plans/e1-cartography.md`; prompts 1-5 have landed (the `[cartography]` contract plus missing/stub preflight enforcement, the non-blocking stale-`freshness.json` warning, the per-language `refresh-cartography` composition with the `ts`-authoritative reader, the fail-loud post-commit hook, and the onboarding preflight error for cartography-less consumers).

**E7 (Dispatch Process Supervision) is complete.** ADR-008 and architecture s11.5 describe the landed terminal-seen, inherited-stream, and `completed_lingering` semantics. Its per-epic plan was removed because `docs/plans/` only keeps active epic plans.

## What Happens Next

Current ordering:

1. E7 closeout: mark dispatch process supervision complete and remove its active plan. **Landed.**
2. E1 prompt 4: fail-loud post-commit hook for the mechanical cartography layer. **Landed.**
3. E1 prompt 5: clear preflight error for cartography-less consumers. **Landed.**
4. E2 starts now that E1 is complete.
5. E3/E4/E5 produce the first production-shape baseline.
6. E12 starts the structural cartography pivot, beginning with the concurrent tree-sitter work audit.
7. E13 wires bounded impact context into the Stage-5 reviewer path.
8. E6 conformance audit follows with structural evidence available.
9. E11 P0.1/P0.2 items may be batched only when they touch the same active files; otherwise they remain follow-on backlog work.

Review the diff and targeted tests before each next prompt runs. This document is updated only when sequencing or active plan pointers change.
