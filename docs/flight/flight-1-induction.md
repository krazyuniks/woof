# Flight 1 - Induction Harness Checklist

Flight 1 proves the full kernel plus deploy-decoupled Profile A on a disposable repo. It is not the
cutover gate. Its job is to leave exactly one unproven surface for Flight 2 (real deploy coupling), so
every failure branch that cannot be safely induced on a live consumer is induced here against mocks.

Backlog unit: `flight-1`. Profile: A, deploy-decoupled. Target: a disposable or Woof-repo
pre-decomposed backlog of at least three units with at least one dependency edge.

## Preconditions

- [ ] `runner-loop-absorption`, `deploy-aware-merge-coordinator`, `profile-a-worktree-contract`,
      `run-lineage-immutable-attempts`, and `cartography-continuity` are done.
- [ ] The disposable repo has a valid project config at `~/.woof/config/projects/<project-key>.toml` (Profile A, worktree block, deploy
      timeouts, deploy-check set) and passes preflight.
- [ ] The worktree engine has provisioned one worktree per ready unit under the declared root; Woof
      discovers and validates them (it never provisions).
- [ ] A mock Deploy workflow exists whose outcome can be toggled to a terminal non-lock failure and
      to a state-lock-contention signature.

## Happy path to prove

- [ ] Produce -> deterministic gate -> fresh review -> at least one real blocker fed back to a warm
      producer within budget -> publish, across all units in dependency order.
- [ ] Profile A mechanics without deploy coupling: worktree handshake, branch push, PR publish with
      issue linkage, ready labelling, serial merge of at least two ready PRs with per-PR mark-done,
      and a coordinator self-rebase that leaves the remaining PR ready.
- [ ] Lineage holds: immutable attempts, run/unit/attempt joins, review-cache reuse on an identical
      diff hash, instability on a conflicting verdict.

## Induction toggles (fail-closed proofs)

- [ ] **F-missing-floor** - remove required policy or cartography floor -> preflight fails.
- [ ] **F-sibling-conflict** - stage a shared-file sibling pair so a ready PR cannot rebase cleanly
      against a merged sibling -> halt to a human gate, conflicting PR left ready and branch
      unmodified, queue resumable, rerun produces no duplicate (ADR-016).
- [ ] **F-mock-deploy-terminal** - toggle the mock Deploy workflow to a terminal non-lock failure ->
      safe halt with already-merged units reconciled and a resumable ready queue.
- [ ] **F-state-lock** - toggle the state-lock-contention signature -> classified as retryable, halts
      safely for first flight (no bounded-retry yet).
- [ ] **F-kill-producer** - kill a producer mid-run -> resume from disk reattaches or respawns and
      completes with audited effect.
- [ ] **F-transient-mergeability** - inject a transient UNKNOWN/UNSTABLE -> bounded settle-retry, not
      a halt.

## Evidence to capture

- [ ] Per-unit run/attempt/review artefacts and the node/transition JSONL.
- [ ] The gate records for each induced halt, with detection trigger recorded.
- [ ] The sibling-conflict corpus entry (detection trigger + resolution outcome).
- [ ] A short pass/fail note per checklist line, retained with the run.

Flight 1 passes only when the happy path and every induction toggle above behave as specified.
