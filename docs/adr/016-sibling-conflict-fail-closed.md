---
type: adr
status: accepted
date: 2026-07-06
---

# ADR-016: Shared-File Sibling Conflicts Fail Closed

## Context

Profile A merges a queue of ready pull requests serially. When two sibling units touch the same files, a later merge can conflict with a sibling already merged since the later PR's base. Automatic semantic reapplication of a conflicted change is attractive for throughput but can silently produce a wrong merge.

## Decision

Every detected shared-file sibling conflict halts to a durable human gate with reconciled state and a resumable queue. No automatic semantic reapplication ships in the pre-flight build or either flight, and the fail-closed policy holds through both flights, cutover, and the whole post-cutover stability window.

Detection is conflict, not overlap. A halt triggers when:

- a coordinator rebase of a ready PR fails to apply cleanly;
- GitHub mergeability settles CONFLICTING after the bounded UNKNOWN/UNSTABLE settle-retry;
- required checks or the gate fail after a clean rebase on a PR whose paths intersect a sibling merged since that PR's base.

Path overlap between still-queued siblings never pre-emptively halts. Transient UNKNOWN/UNSTABLE gets bounded settle-retry, not a halt.

On halt: already-merged siblings are reconciled per PR, the conflicting PR is left ready with its branch unmodified (rebase aborted cleanly, no force-push of half-rebased state), and the queue is resumable with no duplicate work on rerun. Resolution is an explicit audited engine action - a human reconciles in the worktree and re-pushes with a full gate and fresh-review rerun on the final diff, the unit returns to production against moved main, or it is withdrawn. No path merges without a gate and review rerun.

## Consequences

- Correctness is preferred over merge throughput for the entire proving period.
- Gated conflicts are captured to a sibling-conflict corpus (JSONL under `.woof/`, independent of `eval-instrumentation`) recording detection trigger and resolution outcome, so a future automation decision has evidence.
- Automatic reapplication is a separate future ADR, taken only when the corpus and observed toil clear a pre-registered bar; the first automation rung and its thresholds are set then, not now.
