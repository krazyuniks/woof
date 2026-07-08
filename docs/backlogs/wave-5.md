---
schema_version: 1
type: backlog
project_ref: woof
status: active
executor:
  name: vault_foreman
  contract_version: 1
  project: woof
  timeouts:
    produce_timeout_min: 180
  drain:
    merge_after_ready_pr: false
    rerun_after_merge: true
    mark_unit_done_after_publish: true
    commit_backlog_state: true
    stop_when_no_eligible_units: true
work_units:
- id: schema-unification
  title: Unify execution schema on work_units
  kind: build
  state: done
  priority: high
  summary: Dependency anchor completed before the Wave 5 drain.
- id: policy-model
  title: Move project policy into repo-local Woof config
  kind: build
  state: done
  priority: high
  summary: Dependency anchor completed before the Wave 5 drain.
  deps:
  - schema-unification
- id: dispatch-swap
  title: Replace headless dispatch with the tmux harness
  kind: build
  state: done
  priority: high
  summary: Dependency anchor completed before the Wave 5 drain.
  deps:
  - schema-unification
  - policy-model
- id: warm-session-seam
  title: Implement warm producer and fresh reviewer fix rounds
  kind: build
  state: done
  priority: high
  summary: Dependency anchor completed before the Wave 5 drain.
  deps:
  - dispatch-swap
- id: config-routing-ssot
  title: Make policy.toml the single routing and run-profile authority
  kind: build
  state: done
  priority: high
  summary: Dependency anchor completed before the Wave 5 drain.
  deps:
  - policy-model
  - dispatch-swap
- id: profile-a-worktree-contract
  title: Profile A worktree discovery and fail-closed validation
  kind: build
  state: done
  priority: high
  summary: Dependency anchor delivered by the wave-5 shakedown drain (docs/backlogs/wave-5-shakedown.md).
    Verify it is done in the master backlog before draining this file.
  deps:
  - policy-model
- id: drain-loop-core
  title: Absorb the dependency-order drain loop
  kind: build
  state: done
  priority: high
  summary: Absorb the VaultForeman drain shell (cli.py drain, driver.py loop) into
    the Woof kernel. Eligible-unit selection over the aggregate's validated topological
    order, strictly serial one-unit-per-cycle produce/gate/review, blocked and downstream
    reporting. Read the runner-asset source map at ~/Work/vault/records/radianit/projects/woof/planning/runner-asset-source-map.md
    before analysing VaultForeman source.
  deps:
  - schema-unification
  - policy-model
  - warm-session-seam
  - profile-a-worktree-contract
  acceptance:
  - Work units run in dependency order with blocked/downstream reporting, consuming
    the aggregate's validated topological order rather than re-deriving it; cross-aggregate
    sequencing is out of the aggregate's scope.
  - The drain is strictly serial one-unit-per-cycle; the next unit starts only after
    the previous unit's publish hand-off completes.
  - Project-owned producer/reviewer slot selection is preserved; the engine owns harness
    adapters, execution, parsing, and validation.
  - On Profile A, each ready unit's worktree resolves through the profile-a-worktree-contract
    preflight; any anomaly fails closed with no provisioning, mutation, recovery,
    or engine invocation (ADR-015).
- id: profile-b-graph-transactions
  title: Profile B commit and push through graph-owned transactions
  kind: build
  state: done
  priority: high
  summary: Absorb VaultForeman profile_b.py as thin delegation to the produce/review
    core. Read the runner-asset source map at ~/Work/vault/records/radianit/projects/woof/planning/runner-asset-source-map.md
    before analysing VaultForeman source.
  deps:
  - drain-loop-core
  acceptance:
  - Profile B commits and pushes through graph-owned transactions; no Profile B path
    bypasses the graph's commit boundary.
  - A unit is marked done only after its transaction lands (commit recorded, and push
    completed when policy declares push).
- id: profile-a-pr-publish
  title: Profile A branch publish, pull request, and ready labelling
  kind: build
  state: done
  priority: high
  summary: Absorb the VaultForeman PR lifecycle (git_pr.py) and ready gate (ready_gate.py).
    Branch push, PR creation with issue linkage, ready labelling on merge eligibility.
    Read the runner-asset source map at ~/Work/vault/records/radianit/projects/woof/planning/runner-asset-source-map.md
    before analysing VaultForeman source.
  deps:
  - drain-loop-core
  acceptance:
  - Profile A publishes a produced unit as a branch push plus pull request, with issue
    linkage recorded in run metadata.
  - The ready label is applied if and only if the unit is merge-eligible (deterministic
    gate green and review PASS).
  - Publish pins the verified tree; the published diff is the gated and reviewed diff.
- id: serial-merge-queue
  title: Serial ready-queue merge with partial-merge reconciliation
  kind: build
  state: done
  priority: high
  summary: Absorb the VaultForeman serial FIFO merge queue (merge_coordinator.py)
    minus deploy-aware pacing, which is carved out to deploy-aware-merge-coordinator
    (hand-build). Read the runner-asset source map at ~/Work/vault/records/radianit/projects/woof/planning/runner-asset-source-map.md
    before analysing VaultForeman source.
  deps:
  - profile-a-pr-publish
  acceptance:
  - The ready queue merges serially in order with per-PR mark-done, rebasing each
    PR onto the moved tip and re-running the gate before merge.
  - Partial-merge reconciliation records already-merged units before halting on any
    later terminal failure.
  - Transient UNKNOWN or UNSTABLE mergeability gets bounded settle-retry, not a halt.
  - Deploy-aware merge pacing is out of scope; the queue does not space merges on
    deploy checks (carved out to deploy-aware-merge-coordinator).
- id: sibling-conflict-fail-closed
  title: Shared-file sibling conflicts fail closed to a human gate
  kind: build
  state: done
  priority: high
  summary: Implement the ADR-016 sibling-conflict policy on the serial merge queue.
    No automatic semantic reapplication; conflicts halt to a durable, resumable human
    gate.
  deps:
  - serial-merge-queue
  acceptance:
  - 'Detection triggers: a coordinator rebase of a ready PR fails to apply cleanly;
    mergeability settles CONFLICTING after bounded settle-retry; required checks or
    the gate fail after a clean rebase on a PR whose paths intersect a sibling merged
    since that PR''s base. Queued-sibling overlap never pre-empts.'
  - On detection the merge queue halts to a durable human gate with already-merged
    siblings reconciled per PR, and the conflicting PR left ready with its branch
    unmodified (rebase aborted cleanly, no force-push of half-rebased state).
  - The queue is resumable and a rerun produces no duplicate work.
  - 'No automatic semantic reapplication. Resolution is an explicit audited engine
    action: a human reconciles in the worktree and re-pushes with a full gate and
    fresh-review rerun on the final diff, or the unit returns to production against
    moved main, or it is withdrawn; no path merges without gate and review rerun (ADR-016).'
- id: harness-override-registry-resolution
  title: Resolve runner-level harness overrides through the dispatch registry
  kind: build
  state: done
  priority: high
  summary: Harness selection and any runner-level harness override resolve through
    the single dispatch registry with reset-and-validate semantics.
  deps:
  - config-routing-ssot
  acceptance:
  - Harness selection and any runner-level harness override resolve through the dispatch
    registry; no second harness/model/effort vocabulary is introduced.
  - Changing harness resets omitted model and effort to the target harness defaults
    and validates effort against that harness, so one profile cannot leak an incompatible
    model into another harness.
- id: review-size-guards
  title: Review-size guards count non-generated changed lines only
  kind: build
  state: todo
  priority: high
  summary: Policy-visible review-size guard that counts only non-generated changed
    lines and never silently skips review for generated files.
  deps:
  - drain-loop-core
  acceptance:
  - Review-size guards, when enabled, count non-generated changed lines only.
  - linguist-generated files, known generated artefacts, and generated-header files
    do not silently skip review.
  - The guard threshold is policy-visible.
- id: engine-neutral-consumer-policy
  title: Engine-neutral consumer delivery policy
  kind: build
  state: todo
  priority: high
  summary: A consumer repo declares its delivery policy once in a form both VaultForeman
    and Woof honour; engine selection is a per-run choice and no consumer is coupled
    to a specific engine. Resolves the lane_plan.py / lane_launcher.py design call
    (residual lane logic routes to a consumer-intake adapter or retires).
  deps:
  - config-routing-ssot
  acceptance:
  - A consumer's delivery policy (profile, run-profile slots, gate, check floor, cartography
    floor) is declared once in `.woof/policy.toml`; the transitional VaultForeman
    drain validates against and reads the same declaration.
  - Selecting the engine for a run is a per-run operator choice, not a property baked
    into the consumer repo.
  - No consumer repo carries engine-specific delivery configuration beyond the single
    shared declaration.
  - The transitional VaultForeman `executor.drain` policy fields (merge-after-ready-pr,
    rerun-after-merge, mark-done-after-publish, commit-backlog-state, stop-when-no-eligible)
    are expressed as a Woof-native drain contract in the shared declaration, so retirement
    removes the VaultForeman executor block without losing drain semantics.
---

# Wave 5 Sub-Backlog

Decomposes the wave-5 vf-drain units from the master backlog: `runner-loop-absorption` (split into `drain-loop-core`, `profile-b-graph-transactions`, `profile-a-pr-publish`, `serial-merge-queue`, `sibling-conflict-fail-closed`, `harness-override-registry-resolution`, `review-size-guards`) and `engine-neutral-consumer-policy` (carried whole). Every master acceptance criterion of `runner-loop-absorption` maps to exactly one sub-unit.

Excluded by design:

- `deploy-aware-merge-coordinator` is a native new-build with no VaultForeman source asset; hand-build with operator review after `runner-loop-absorption` lands. Not an unattended drain.
- `vaultforeman-fix-parity` depends on `runner-loop-absorption` and `deploy-aware-merge-coordinator`; decompose it after both land.
- `profile-a-worktree-contract` ships first via `docs/backlogs/wave-5-shakedown.md` and appears here as a done anchor.

Drain prerequisites: the shakedown unit is done in the master backlog, and Woof self-hosts under Profile B (commit + push, no PR). Do not run VaultForeman against `docs/backlog.md` while this sub-backlog is active.

When all units here are done, mark `runner-loop-absorption` and `engine-neutral-consumer-policy` done in the master backlog.