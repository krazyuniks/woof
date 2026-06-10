# Woof Implementation Plan

This document describes how the open work in `docs/backlog.md` gets sequenced. The backlog defines what to do; this file defines the order and the operating rules for running the work.

Per-epic implementation plans live under `docs/plans/<epic>.md` and are written only when that epic starts.

## Execution Principle

Work moves in small, reviewable coding-agent prompts. Each prompt has clear acceptance criteria, a focused diff, and a verification step. The operator reviews the result before the next prompt runs.

Do not use this document to preserve speculative architecture. If a direction is withdrawn, delete its plan and remove it from the sequence.

## Sequence

```text
E2 Contract readiness and run resilience   (active)
  + E16 Defect sweep              (immediate; batch with E2 where files overlap)

Hard ordering constraints (a DAG, not one chain - E17, E20, E19, and E21 S1-S3 are
mutually independent):
  E17 readiness-resolution slice   -> before E3
  E20, E19, E21 S1-S3              -> before E5
  E17 + E18 + E22 complete         -> before the first unattended overnight run

Eval line:
E3 Specwright bootstrap -> E4 Eval instrumentation -> E5 Baseline eval run

Recommended single-operator order: E17 -> E20 -> E19 -> E21 S1-S3 -> E3 -> E4 -> E5.
E19's Woof self-onboarding rehearses E3's mapper flow, and a post-E20/E19 bootstrap
smoke run exercises the production shape. Preference, not dependency.

Unattended-safety set (sequence within the set is free):
E17 Gate decision semantics, E18 Artefact integrity and commit boundary,
E22 Runner seam hardening

Post-baseline optimisation chain:
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
E21 S4-S6 Solo-operator affordances
E23 Architecture doc truth pass   (after E17 settles the verb table)
E14 Ranked and semantic cartography retrieval
E15 Structural onboarding and mapper grounding
```

E7 is complete: per-dispatch supervision now classifies hanging-but-done workers as `completed_lingering`, emits trustworthy `exit_type` telemetry, and leaves the detailed behaviour in ADR-008 plus architecture s11.5. E1 is complete on the supply side: prompts 1-5 enforce the cartography prerequisite and onboard legacy consumers that lack `[cartography]`. E19 is still required before dispatched work actually consumes the mapped cartography documents.

E2 remains the active hardening line for readiness, blocker evidence, quality-gate modes, run resilience, and drift detection. E16 runs immediately beside it for small silent-wrong-result defects. The ordering constraints form a DAG, not one chain. E17's readiness-resolution slice precedes E3 because a readiness failure during consumer bootstrap must be legally resolvable rather than a reset-or-stuck loop. E20, E19, and E21 S1-S3 precede E5 so the first baseline measures the intended production shape: Stage-5 roles routable and correctly configured, cartography consumed by dispatches, and the discovery prompt free of the known removable playbook bulk. Those four work items do not depend on each other; the recommended single-operator order runs E17 first, then E20, E19, and E21 S1-S3, then the eval line.

E17, E18, and E22 are the unattended-safety gate. They may overlap the eval chain if the baseline is needed sooner, but no overnight unattended run should happen until gate semantics, artefact integrity, and runner seam hardening are complete.

After E5, the next planned optimisation pivot is structural cartography. E12 builds the ADR-009 local files/symbols/edges index using tree-sitter as the V1 extraction substrate, with Python `ast` only as a fallback behind the same adapter boundary. E13 uses that index first in the Stage-5 reviewer path as bounded impact context. E6 then runs with structural evidence available for conformance checks, rather than trying to infer callers and affected surfaces from prose alone.

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
| Before E3 starts | Confirm specwright is still the first production-shape consumer and E17's readiness-resolution slice has landed. |
| Before E5 starts | Confirm E19, E20, and E21 S1-S3 have landed. The baseline is captured only over the intended production shape; there is no transitional-baseline option. |
| After E5 baseline | Confirm structural cartography remains the first optimisation target; if eval data points elsewhere, reorder E12/E13 explicitly. |
| Before the first unattended overnight run | Confirm E17, E18, and E22 are complete. |
| Before E14 starts | Decide whether semantic retrieval needs embeddings or whether BM25 plus structural ranking is enough for the first pass. |
| Before E15 starts | Confirm there is a real onboarding target large enough to justify mapper grounding and community/hub analysis. |
| Before E11 P0.3 starts | Decide whether prototype/diagnose should be a dedicated epic or folded into the next readiness/recovery pass. |
| Before E11 P0.4 starts | Review whether codebase-deepening and zoom-out flows are still worth building, based on cheap-heuristic results and eval data. |

## Risk Register

| Risk | Mitigation |
|---|---|
| Cartography becomes ceremony instead of useful context | Keep required docs few and practical; fail on missing/stub state, warn on stale mechanical state. |
| Cartography remains mandatory but unconsumed | E19 is before E3/E5 and wires the architecture loading map into dispatch payloads and telemetry. |
| The baseline measures a transitional product shape | E20, E19, and E21 S1-S3 are hard E5 dependencies; no transitional baseline is captured. |
| Readiness gate blocks on subjective quality | Keep checks deterministic: machinability, concrete refs, path/symbol resolution, and contract sufficiency only. |
| Gate verbs look available but do not move the graph | E17 centralises the decision table and adds conformance tests for advertised-vs-implemented verbs. |
| Verification checks different content from the commit | E18 pins the approved plan and staged tree, validates durable reads, and moves story-complete marking after commit. |
| Baseline mode overpromises per-failure subtraction | E2 implements command-level baselines first; structured parsers are required before fine-grained subtraction. |
| Dispatched worker changes branch or HEAD unexpectedly | E2 records HEAD/branch before and after dispatch and opens a drift gate on unexplained movement. |
| Runner state mutates outside the workflow lock | E22 makes reset/resolve share the runner lock and derives observe from the same next-node logic. |
| tmux grows into a second workflow | tmux may supervise panes, logs, and child processes only. State still changes through Woof commands. |
| Eval result shows a different bottleneck than expected | Use the E5 manifest to choose the next optimisation; do not pre-commit to context-scoping changes. |
| Tree-sitter and `ast` grow into two parser paths | E12 uses tree-sitter as V1 and keeps `ast` only as an adapter-compatible fallback. |
| Structural index looks precise but is heuristic | Store provenance/confidence on every edge; prompts and gates treat heuristic/ambiguous edges as leads requiring source evidence. |
| Structural cartography becomes a parallel graph surface | Only `woof cartography` reads the index. It does not mutate workflow state, expose MCP, or create a `woof graph` API. |
| MP comparison imports expand into a second skill suite | Keep imports inside `/woof`, `/woof:brainstorm`, playbooks, schemas, and deterministic checks. Do not add parallel tracker or split-skill surfaces. |

## Active Per-Epic Plan Pointers

**E1 (Cartography Prerequisite) is complete.** Its plan is `docs/plans/e1-cartography.md`; prompts 1-5 have landed (the `[cartography]` contract plus missing/stub preflight enforcement, the non-blocking stale-`freshness.json` warning, the per-language `refresh-cartography` composition with the `ts`-authoritative reader, the fail-loud post-commit hook, and the onboarding preflight error for cartography-less consumers).

E1 did not wire cartography into dispatch payloads. E19 owns that demand-side consumption work.

**E7 (Dispatch Process Supervision) is complete.** ADR-008 and architecture s11.5 describe the landed terminal-seen, inherited-stream, and `completed_lingering` semantics. Its per-epic plan was removed because `docs/plans/` only keeps active epic plans.

## What Happens Next

Current ordering:

1. E7 closeout: mark dispatch process supervision complete and remove its active plan. **Landed.**
2. E1 prompt 4: fail-loud post-commit hook for the mechanical cartography layer. **Landed.**
3. E1 prompt 5: clear preflight error for cartography-less consumers. **Landed.**
4. E2 continues; E16 defect-sweep items batch with it where they touch the same files.
5. E17 lands its decision table and readiness-resolution verbs first; that slice unblocks E3.
6. E20, E19, and E21 S1-S3 land before E5 (recommended: after E17, before E3).
7. E3/E4/E5 produce the first production-shape baseline.
8. E17/E18/E22 must complete before the first unattended overnight run.
9. E12 starts the structural cartography pivot using the recorded tree-sitter-first decision.
10. E13 wires bounded impact context into the Stage-5 reviewer path.
11. E6 conformance audit follows with structural evidence available.
12. E11 P0.1/P0.2 items may be batched only when they touch the same active files; otherwise they remain follow-on backlog work.

Review the diff and targeted tests before each next prompt runs. This document is updated only when sequencing or active plan pointers change.
