# Flight 2 - Audit Evidence Checklist

Flight 2 proves the merged engine on a guarded, real prod-deploying consumer slice. Passing it means
Woof is trusted for prod-deploying consumers. It authorises cutover only; VaultForeman retirement needs
the separate post-cutover stability window (ADR-014).

Backlog unit: `flight-2`. Deps: `flight-1`, `engine-neutral-consumer-policy`. This checklist stays
engine- and consumer-neutral; the concrete slice, deploy-check names, and launch-calendar timing live
in the consumer's own records, never here.

## Preconditions

- [ ] Flight 1 passed in full.
- [ ] The consumer declares its delivery policy once (`engine-neutral-consumer-policy`): Profile A,
      run-profile slots, gate, check floor, cartography floor, worktree block, deploy-check set, and
      settle/wait timeouts. Preflight passes.
- [ ] The consumer's terminal deploy-check set is enumerable and stable enough to declare by name.
- [ ] The worktree engine provisions the per-unit worktrees; Woof discovers and validates them.
- [ ] The prior engine is retained as a warm fallback with per-run comparison capture.
- [ ] The slice is three to five real low-risk code-only units sharing the deploy path (no schema,
      infra, or Terraform) and outside launch-critical correctness lanes.
- [ ] An operator-confirmation gate is armed before every deploy-triggering merge.

## Proofs to capture (F1-F6)

- [ ] **F1 mid-queue failure** - one induced or natural failure after at least one merged PR triggers
      a safe halt: already-merged units reconciled and marked done, resumable queue, duplicate-free
      rerun.
- [ ] **F2 sibling fail-closed** - any sibling conflict gates rather than merges, no automatic
      reapplication.
- [ ] **F3 coordinator self-rebase** - a self-rebase never drops a queued PR; the remaining PR stays
      ready.
- [ ] **F4 deploy spacing** - the deploy-check set reaches a terminal state between every consecutive
      merge pair; at least one transient UNKNOWN/UNSTABLE is retried and settles.
- [ ] **F5 killed-producer resume** - a killed producer resumes from disk (if exercised in the slice).
- [ ] **F6 per-PR mark-done** - every ready PR merges serially with per-PR mark-done and Closes-issue
      linkage.

## Exit evidence

- [ ] Zero hand-recovery beyond operator confirmations.
- [ ] Lineage matches or beats the prior engine's path (comparison capture attached).
- [ ] Every operator confirmation recorded with its merge.
- [ ] A go/no-go note: Flight 2 passing is necessary but not sufficient for cutover; cutover timing is
      a human call against the consumer's launch calendar.

Flight 2 passes only when F1-F6 and the exit evidence hold with no unplanned hand-recovery.
