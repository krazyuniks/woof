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
    merge_after_ready_pr: true
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
    summary: Move the canonical executable unit schema into Woof, retire legacy runtime contracts, and preserve graph dependency checks.
    acceptance:
      - Canonical Woof schema validates required work-unit fields and optional contract-trace fields.
      - Backlog front matter accepts the document-level executor block needed by transitional VaultForeman drains without adding custom per-unit wave fields.
      - Durable readers and writers use work_units without transitional unit-shape mirrors.
      - Duplicate ids, dangling deps, self-deps, and cycles fail validation.
  - id: policy-model
    title: Move project policy into repo-local Woof config
    kind: build
    state: done
    priority: high
    summary: Add delivery profile, producer/reviewer run profile, gate command, check floor, and cartography floor to `.woof/` policy.
    deps: [schema-unification]
    acceptance:
      - A repo can be onboarded without editing engine Python.
      - Policy validates profile A and profile B settings.
      - Policy declares producer and reviewer harness/model/effort slots in the consuming repo; the engine owns harness adapters, execution, parsing, and validation.
      - Missing required policy or cartography floor data fails preflight.
  - id: intake-predecomposed
    title: Pre-decomposed work-unit intake
    kind: build
    state: done
    priority: high
    summary: Validate pre-decomposed work_units and skip decomposition; establish the work-unit-set aggregate context, persist a stable set_id, and record run metadata without fabricating an epic. Delivered in repo 9d6a768.
    deps: [schema-unification, policy-model]
    acceptance:
      - Pre-decomposed work_units validate and skip decomposition.
      - Intake establishes the work-unit-set aggregate context and derives qualified references from it without fabricating an epic; when the source has no natural identity, intake assigns and persists a stable set_id once.
      - Intake records run metadata without reverse-generating a missing epic.
      - Pre-decomposed intake accepts work_units in topological dependency order so the runtime aggregate validates without reordering.
  - id: intake-epic-enrichment
    title: Epic sources, enrichment, and auto-decompose
    kind: build
    state: todo
    priority: medium
    summary: Epic-backed intake from greenfield, GitHub, and local-docs sources through sparse epic, optional brainstorm enrichment, epic, then work_units via the existing breakdown playbook. 9d6a768 delivered the epic context scaffolding but not the auto-decompose/enrichment node.
    deps: [intake-predecomposed]
    acceptance:
      - Epic-backed intake follows sparse epic to optional enrichment to epic to work_units, using project_ref plus epic_id.
      - Decomposition produces work_units through the existing breakdown playbook and brainstorm enrichment, not a second decomposer; the auto-decompose step replaces the manual decompose earlier waves relied on.
      - Decomposition emits work_units in topological dependency order so the runtime aggregate validates without reordering.
  - id: dispatch-swap
    title: Replace headless dispatch with the tmux harness
    kind: build
    state: done
    priority: high
    summary: Remove headless worker dispatch and consume structured results from the shared interactive tmux harness.
    deps: [schema-unification, policy-model]
    acceptance:
      - Producer and reviewer dispatches launch through tmux harness profiles.
      - Prompt-file delivery and structured result capture are covered by tests.
      - Engine code consumes verdict, evidence, usage, and session metadata without parsing raw terminal scrollback.
  - id: execution-shape-unification
    title: Converge the execution kernel on the one work_units schema
    kind: build
    priority: high
    state: done
    summary: Collapse the runtime plan/work-unit shape onto the canonical work_units shape (one lifecycle field, work-unit ids, named checks and gates), remove legacy id mirrors, and retire legacy-named playbooks and self-cartography. Realises the ADR-011 convergence that schema-unification left at the intake boundary.
    deps: [schema-unification]
    acceptance:
      - One canonical work-unit schema validates id, title, kind, state, and the optional contract-trace fields; the plan/runtime artefact and the backlog artefact share it, with no status-versus-state dual lifecycle.
      - The execution kernel exposes a work-unit entity and a work-unit aggregate boundary; aggregate validation owns unique local IDs, dependency closure, acyclicity, and topological order.
      - Cross-aggregate references use structured context plus the local work-unit id, rather than a second globally encoded id field; UUIDs are reserved for technical run, attempt, review, and audit records.
      - The aggregate context is a discriminated union -- an epic context (project_ref, epic_id) or a work-unit-set context (project_ref, set_id, optional source_ref), not an optional epic_id; set_id is a stable persisted identity, never a run UUID (architecture section 4).
      - "Review-cache, instability, and lineage joins carry the qualified work-unit reference as the unit-identity component alongside the content/version facts (diff_hash, prompt version, role): the qualified ref answers which unit, the content facts answer whether a cached review is reusable."
      - Runtime gates, checks, dispositions, and events key on work-unit id; no event carries both a legacy id and work_unit_id.
      - Deterministic checks and gate types are named around work units, and the gate writer can emit the work-unit gate.
      - Producer and reviewer playbooks and Woof's self-cartography use work-unit terminology; no legacy-named playbook remains.
      - An invariant guard test covers the single inbound legacy-shape normaliser (accept legacy input, reject dual shape), and a duplicate work-unit id case is tested.
      - Dead back-compat aliases are deleted in this change.
  - id: config-routing-ssot
    title: Make policy.toml the single routing and run-profile authority
    kind: build
    state: done
    priority: high
    summary: Consolidate routing, run profiles, and harness/model/effort vocabulary to one home each; retire the agents.toml routing duplication and the dead headless dispatch builders.
    deps: [policy-model, dispatch-swap]
    acceptance:
      - Producer and reviewer routing and run profiles are declared only in policy.toml; agents.toml no longer carries route or model-profile fields, and any non-routing scope it keeps lives in its own bounded file.
      - Harness, model, and effort vocabulary has one source of truth in the dispatch registry; effort, adapter, and alias maps are not re-declared across policy.py, dispatcher.py, and harness_registry.py.
      - Every harness the registry declares is reachable through a policy run profile, or is explicitly removed.
      - The headless claude -p and codex exec argv builders and their parsers are deleted; route probes use a registry-based check.
  - id: warm-session-seam
    title: Implement warm producer and fresh reviewer fix rounds
    kind: build
    state: done
    priority: high
    summary: Keep the producer attached across bounded fix rounds, use a fresh independent reviewer each round, and make resume producer-capable.
    deps: [dispatch-swap]
    acceptance:
      - Reviewer blocker evidence is pasted back to the same producer session within budget.
      - Each review round receives a fresh reviewer context and the full current diff.
      - Resume can reattach or respawn the producer from disk authority.
      - The fix-round budget is bounded and configurable, defaulting to two rounds per blocker before a gate opens.
  - id: runner-loop-absorption
    title: Absorb VaultForeman runner loop and profiles
    kind: build
    state: todo
    priority: high
    summary: Bring dependency draining, profile A/B delivery, usage telemetry, review cache, and serial merge coordination into Woof. Deploy-aware merge pacing is carved out to `deploy-aware-merge-coordinator`.
    deps: [schema-unification, policy-model, warm-session-seam]
    acceptance:
      - Work units run in dependency order with blocked/downstream reporting, consuming the aggregate's validated topological order rather than re-deriving it; cross-aggregate sequencing is out of the aggregate's scope.
      - Profile A publishes ready pull requests and serially merges the ready queue as deploy-aware transactions.
      - Partial-merge reconciliation records already-merged units before halting on any later terminal failure.
      - "Shared-file sibling conflicts fail closed: the merge queue halts to a durable human gate with already-merged siblings reconciled per PR, the conflicting PR left ready with its branch unmodified (rebase aborted cleanly, no force-push of half-rebased state), the queue resumable, and a rerun producing no duplicate work. No automatic semantic reapplication. Resolution is an explicit audited engine action -- a human reconciles in the worktree and re-pushes with a full gate and fresh-review rerun on the final diff, or the unit returns to production against moved main, or it is withdrawn; no path merges without gate and review rerun. Detection triggers: a coordinator rebase of a ready PR fails to apply cleanly; mergeability settles CONFLICTING after bounded settle-retry; required checks or the gate fail after a clean rebase on a PR whose paths intersect a sibling merged since that PR's base. Queued-sibling overlap never pre-empts; transient UNKNOWN/UNSTABLE gets bounded settle-retry, not a halt."
      - Runner absorption preserves project-owned producer/reviewer slot selection and engine-owned harness adapters, execution, parsing, and validation.
      - "Harness selection and any runner-level harness override resolve through the dispatch registry: changing harness resets omitted model/effort to the target harness defaults and validates effort against that harness, so one profile cannot leak an incompatible model into another harness."
      - "Worktree lifecycle -- provisioning, dirty-lease recovery, and teardown -- is owned by the project's worktree engine and task runner, not Woof (ADR-015). Woof fails closed on an anomalous worktree and never provisions, mutates, recovers, or invokes the engine."
      - "Review-size guards, if enabled, count non-generated changed lines only: `linguist-generated` files, known generated artefacts, and generated-header files do not silently skip review, and the threshold is policy-visible."
      - Profile B commits and pushes through graph-owned transactions.
  - id: deploy-aware-merge-coordinator
    title: Deploy-aware Profile A merge coordinator
    kind: build
    state: todo
    priority: high
    summary: Native Woof merge coordinator for deploy-triggering Profile A merges, carrying VaultForeman issue #1's behaviour. No VaultForeman source asset exists for this; hand-build with operator review, not an unattended vf-drain.
    deps: [runner-loop-absorption]
    acceptance:
      - Profile A waits for mergeability and check recompute after main moves, retrying transient UNKNOWN/UNSTABLE with bounded settle-retry.
      - Deploy-triggering merges are spaced until the configured deploy checks reach a terminal state between every consecutive merge pair.
      - The mergeability-settle timeout, deploy-wait timeout, and terminal deploy-check set are read from repo policy; preflight fails closed when deploy-aware merging is active and the deploy-check set is undeclared.
      - Proved Terraform state-lock contention halts safely for first flight; bounded-retry of proved lock contention is deferred to post-flight behind policy.
      - Partial-merge reconciliation records already-merged units before halting on any later terminal failure.
      - A four-defect regression suite covers per-PR mark-done, terminal-CI wait before merge, no self-stale after a coordinator force-push, and Closes-issue linkage with artefact lineage.
      - Anything unclassified fails safe to a terminal halt with reconciled artefacts and a resumable ready queue.
  - id: profile-a-worktree-contract
    title: Profile A worktree discovery and fail-closed validation
    kind: build
    state: todo
    priority: high
    summary: Policy-declared worktree root and engine; deterministic unit-to-path derivation; fail-closed preflight validation of provisioned worktrees. Woof discovers and validates, never provisions.
    deps: [policy-model]
    acceptance:
      - Policy declares the worktree root and the engine identity that provisions worktrees.
      - Unit-to-path derivation is deterministic (root plus work_unit_id, or an explicit per-unit map in the run manifest) and recorded in run metadata.
      - Preflight validates every ready unit's worktree -- it exists, is a linked worktree of the target repo, is on the expected base or unit branch, is clean, and no two units share a path.
      - "Any anomaly fails closed: no auto-create, no silent fallback to a single tree, no engine invocation to repair (ADR-015)."
  - id: engine-neutral-consumer-policy
    title: Engine-neutral consumer delivery policy
    kind: build
    state: todo
    priority: high
    summary: A consumer repo declares its delivery policy once in a form both VaultForeman and Woof honour; engine selection is a per-run choice and no consumer is coupled to a specific engine. This removes the migration framing between the two runners and resolves the `lane_plan.py` / `lane_launcher.py` design call.
    deps: [config-routing-ssot]
    acceptance:
      - A consumer's delivery policy (profile, run-profile slots, gate, check floor, cartography floor) is declared once in `.woof/policy.toml`; the transitional VaultForeman drain validates against and reads the same declaration.
      - Selecting the engine for a run is a per-run operator choice, not a property baked into the consumer repo.
      - No consumer repo carries engine-specific delivery configuration beyond the single shared declaration.
      - The transitional VaultForeman `executor.drain` policy fields (merge-after-ready-pr, rerun-after-merge, mark-done-after-publish, commit-backlog-state, stop-when-no-eligible) are expressed as a Woof-native drain contract in the shared declaration, so retirement removes the VaultForeman executor block without losing drain semantics.
  - id: vaultforeman-fix-parity
    title: Inherit VaultForeman behavioural and operator-UX fixes since the merge baseline
    kind: build
    state: todo
    priority: high
    summary: Re-baseline the runner absorption against VaultForeman HEAD. The runner-asset source map was cut 2026-06-28; VaultForeman has landed drain, review-parsing, and operator-UX fixes since that Woof will not otherwise inherit. Carry the four fix families below and refresh the source map to VF HEAD.
    deps: [runner-loop-absorption, deploy-aware-merge-coordinator]
    acceptance:
      - "Merge coordinator: serial one-unit-per-cycle drain, publish-rebase survival with no residue, detached coordinator worktree, index-free ready-PR listing, merge-phase transient safety (no drain crash or re-produce), rebase-and-re-gate onto the base tip before a Profile A PR, partial-merge reconciliation, and skip-re-produce when a unit already has an open PR."
      - "Review-verdict parsing is harness-aware: glyphless, Unicode, and settled-chrome Claude Code done-markers reap a PASS; GLM and codex readiness and done glyphs are matched; multi-line and bare file:line findings are captured whole; TUI pane markers are normalised before verdict parsing."
      - "Operator UX: slice-phase transitions narrate to stdout as the progress signal; a describe command resolves policy plus harness registry for a project or backlog; a committed branch can be adopted without a producer (resume); the producer done-signal window is configurable."
      - "Harness and profile: producer and reviewer harness are overridable profile fields resolved through the single dispatch registry; effort tiers are correct per profile."
      - The runner-asset source map is refreshed to VaultForeman HEAD and marked historical once parity lands.
  - id: run-lineage-immutable-attempts
    title: Add run lineage and immutable attempt artefacts
    kind: build
    state: done
    priority: high
    summary: Thread run identity through events and preserve every attempt for replay, review-cache reuse, and instability detection.
    deps: [schema-unification]
    acceptance:
      - Events and dispatch artefacts are joinable by run id and work-unit id.
      - Repeated review over the same diff hash and prompt version reuses the prior verdict.
      - Conflicting verdicts over the same inputs are recorded as review instability.
  - id: cartography-continuity
    title: Retain cartography as a policy-enforced capability
    kind: build
    state: done
    priority: medium
    summary: Move cartography-floor selection into policy.toml cartography.floor (adding a no-cartography level) and reconcile the existing ADR-004/ADR-009 cartography artefacts and refresh hook with the merged engine. Existing cartography is reused, not re-derived. Structural cartography is the deferred structural scope of this unit.
    deps: [policy-model]
    acceptance:
      - Repo policy can require no cartography, lexical/design cartography, or structural cartography.
      - Required cartography is enforced before execution.
      - Producer, reviewer, and deterministic checks consume declared cartography on the same engine path.
  - id: conformance-audit
    title: Implement policy-driven conformance audit
    kind: build
    state: todo
    priority: medium
    summary: Add deterministic diff checks over work-unit trace fields, epic contracts, repo policy, and cartography evidence.
    deps: [schema-unification, cartography-continuity]
    acceptance:
      - Audit findings are machine-readable and cite resolvable evidence.
      - Findings reference work units by qualified reference (aggregate context plus local id) so evidence resolves across aggregates.
      - Contract-trace checks no-op when trace fields are absent.
      - Cartography-dependent checks run only when policy requires the cartography floor.
  - id: eval-instrumentation
    title: Measure the merged execution shape
    kind: build
    state: todo
    priority: medium
    summary: Capture per-node and per-attempt evidence for prompt cost, loaded artefacts, usage, retries, checks, and review instability.
    deps: [dispatch-swap, run-lineage-immutable-attempts]
    acceptance:
      - Eval manifests attribute cost and loaded artefacts by node and qualified work-unit reference, with UUID-keyed run/attempt records, consistent with the lineage identity model.
      - Prompt/output bodies are retained according to audit policy.
      - The first optimisation target is chosen from measured data.
  - id: flight-1
    title: Prove the merged engine on a disposable repo
    kind: action
    state: todo
    priority: high
    summary: Full kernel plus deploy-decoupled Profile A on a disposable/Woof-repo pre-decomposed backlog, with failure proofs and a mock-Deploy rehearsal. Not the cutover gate.
    deps: [intake-predecomposed, runner-loop-absorption, deploy-aware-merge-coordinator, profile-a-worktree-contract, run-lineage-immutable-attempts, cartography-continuity]
    acceptance:
      - A disposable pre-decomposed backlog of at least three units with at least one dependency edge runs end to end -- produce, deterministic gate, fresh review, at least one real blocker fed back to a warm producer within budget, publish.
      - Profile A mechanics run without deploy coupling -- worktree handshake, branch push, PR publish with issue linkage, ready labelling, serial merge of at least two ready PRs with per-PR mark-done, and a coordinator self-rebase that leaves the remaining PR ready.
      - Resume of a killed producer from disk is exercised, and a human gate is opened and resolved with audited effect.
      - Lineage and artefacts hold -- immutable attempts, run/unit/attempt joins, review-cache reuse on an identical diff hash, and instability on a conflicting verdict.
      - Fail-closed behaviour is proved -- missing policy or cartography floor fails preflight; an induced sibling conflict halts to a gate; a mock Deploy workflow's terminal non-lock failure triggers a safe halt with reconciled artefacts; state-lock contention is classified and halts.
  - id: flight-2
    title: Prove Woof on a guarded real-deploy slice
    kind: action
    state: todo
    priority: high
    summary: Prove the merged engine end to end on a guarded, real prod-deploying consumer slice. Passing means Woof is trusted for prod-deploying consumers; it is not a consumer migration.
    deps: [flight-1, engine-neutral-consumer-policy]
    acceptance:
      - A guarded slice of three to five real low-risk code-only units sharing the deploy path (no schema, infra, or Terraform), outside launch-critical correctness lanes, runs with an operator-confirmation gate before every deploy-triggering merge and the prior engine held as fallback with per-run comparison evidence.
      - Every ready PR merges serially with per-PR mark-done; mergeability and check recompute settle after each main move with at least one transient UNKNOWN/UNSTABLE retried; the deploy-check set reaches a terminal state between every consecutive merge pair.
      - One induced or natural mid-queue failure after at least one merged PR triggers a safe halt -- already-merged units reconciled and marked done, a resumable queue, and a duplicate-free rerun.
      - A coordinator self-rebase never drops a queued PR; any sibling conflict gates rather than merges, with no automatic reapplication.
      - Zero hand-recovery is needed beyond operator confirmations, and lineage matches or beats the prior engine's path.
  - id: vaultforeman-retirement
    title: Retire the standalone VaultForeman runner
    kind: action
    state: todo
    priority: medium
    summary: Retire the standalone VaultForeman runner once Woof is at parity, proven (flight-2), carries the engine-neutral consumer policy, and no live run requires the standalone VF path. Any wrapper is proved after retirement, not before.
    deps: [flight-2, engine-neutral-consumer-policy]
    acceptance:
      - Active project records point to Woof as the orchestration engine, and no live consumer run requires the standalone VaultForeman path (per-run engine selection has moved live consumers onto Woof).
      - Woof-side hidden-engine sweep -- the executor document block is removed or re-pointed in the canonical schema, the vf-drain wave instructions and `vf orchestrate` operating-order references are deleted, and the `VAULT_FOREMAN.md` reference is dropped.
      - Schema-authority freeze -- the canonical work_units[] schema lives in Woof, VaultForeman schema files take no independent evolution, and transitional VaultForeman drains validate against Woof's schema.
      - A post-cutover stability window is met -- at least three real Woof drains including at least one without per-merge confirmation, zero hand-recovery, VaultForeman fallback retained through the window, and the window length is operator-set.
      - Standalone runner entry points are removed or wrap Woof without duplicate logic; VaultForeman records state the retired boundary.
  - id: safety-defect-sweep
    title: Fold remaining safety defects into the merge line
    kind: build
    state: done
    priority: medium
    summary: Carry forward verified small safety defects that still matter under the merged architecture.
    deps: [schema-unification]
    acceptance:
      - Raw durable artefact reads are routed through loaders.
      - Commit and publish boundaries pin the verified tree and expected paths.
      - Dead state-mutation surfaces are removed rather than mirrored.
---

# Woof Backlog

This is the forward work queue for the VaultForeman/Woof merge. It contains work to do. Historical stage epics are not retained here as a second plan; git history carries the old backlog.

The architecture target is `docs/architecture.md`. Decision records are in `docs/adr/`. The glossary is `docs/CONTEXT.md`.

## Commission reconciliation - 2026-07-06

The wave-5-onward tail below was reshaped from six deep-reasoning commissions (Fable-judged, adversarially reviewed by GLM, reconciled by Opus), plus an engine-agnostic correction. Provenance is in `~/Work/vault/records/personal/projects/vault-decomposition/commissions/`; each folder holds `ingestion-plan.md`, `plan-review.md`, and `plan-reconciliation.md`:

- `woof-vaultforeman-merge` -- split intake, rewrite the wave table, move conformance/eval off the pre-flight path.
- `vaultforeman-woof-absorption-boundary` -- carve deploy-aware behaviour out of `runner-loop-absorption`; executor sweep, schema freeze, and stability window on retirement.
- `woof-first-flight-cutover-gate` -- split `first-flight` into `flight-1` (disposable) and `flight-2` (guarded real-deploy).
- `woof-profile-a-worktree-merge-contract` -- Woof discovers and validates worktrees fail-closed; it never provisions.
- `woof-semantic-conflict-policy` -- shared-file sibling conflicts fail closed to a human gate, not semantic reapplication.
- `woof-vf-issue-1-fate` -- supersede VaultForeman issue #1 (deploy-aware merge) into Woof; VaultForeman held as interim fallback.

Engine-agnostic framing: a prod-deploying consumer is one either engine can run, so there is no consumer-specific migration. The residual is the engine-side `engine-neutral-consumer-policy` unit plus the general proving gate (`flight-1` and `flight-2`). Retirement triggers on Woof parity, proof, and no live VF-dependent run.

Live-state note: `intake-predecomposed` and `execution-shape-unification` are already delivered. The decided doc tranche is now applied: ADR-015 (Profile A worktree contract), ADR-016 (sibling-conflict fail-closed), the declarative ADR-014 rewrite (absorption, three bounded transition surfaces, schema-authority freeze, operator-set stability window), the architecture section 0/section 8 updates, the CONTEXT glossary additions, and the policy-schema worktree/deploy-timeout additions. `vaultforeman-fix-parity` is the sweep that re-baselines the absorption against VaultForeman HEAD.

## Operating Order

1. Hand-build schema unification and the safety-defect sweep.
2. Build the policy spine by hand, then drain policy-adjacent runner work in dependency order.
3. Hand-build the execution-shape and config-routing convergence so the kernel runs one `work_units[]` shape and one routing authority before any further runner logic is absorbed.
4. Drain the warm-session, cartography-continuity, and pre-decomposed intake units.
5. Absorb the runner loop, the deploy-aware merge coordinator, the Profile A worktree contract, the engine-neutral consumer policy, and the VaultForeman fix-parity sweep.
6. Run flight 1 (disposable repo), then flight 2 (guarded real-deploy slice), manually.
7. Drain the post-flight richness units: conformance audit, eval instrumentation, and epic-enrichment intake.
8. Retire the standalone VaultForeman runner once Woof is proven and no live run requires it.

## Wave Instructions

The `How` value controls execution mechanics:

- `hand-build` means the operator decomposes and implements the unit directly in the Woof checkout, using normal repo checks and commits. It is for contract/spine work that is too foundational to hand to the transitional runner.
- `vf-drain` means the unit is decomposed into a schema-valid `work_units[]` sub-backlog and run through `vf orchestrate`. The Woof VaultForeman run profile lives in `VAULT_FOREMAN.md`; backlog front matter owns only executor, timeout, drain, and state-update policy. Do not run `vf orchestrate docs/backlog.md` while hand-build or manual wave units are still `todo`; drain from a wave sub-backlog or after the earlier units are marked done.
- `manual` means an operational proof, cutover, or retirement step where the operator owns sequencing and judgement. It may run tools, but it is not an unattended producer drain.

| Wave | Units | How | Instructions |
|---|---|---|---|
| 0 | Runner-asset source map | done | Source map is in `~/Work/vault/records/radianit/projects/woof/planning/runner-asset-source-map.md`. |
| 1 | `schema-unification`, `safety-defect-sweep` | hand-build | Start here. Preserve one canonical `work_units[]` schema, keep the VaultForeman `executor` document block valid for transitional drains, retire legacy runtime mirrors, and keep graph dependency validation fail-closed. |
| 2 | `policy-model`, `dispatch-swap`, `run-lineage-immutable-attempts` | hand-build + vf-drain | Hand-build the repo-local policy schema/spine first. In `dispatch-swap`, consolidate VaultForeman's harness/model/effort registry into Woof's dispatcher before any produce/review logic is absorbed. |
| 3 | `execution-shape-unification`, `config-routing-ssot` | hand-build | Foundational convergence. Collapse the runtime to one `work_units[]` shape (retire the `status`/`state` dual lifecycle and legacy id mirrors; rename checks, gates, and playbooks onto work-unit language) and make `policy.toml` the single routing/run-profile authority (retire `agents.toml` routing, single-source the registry vocab, delete dead headless builders). Hand-build because the vf-drain waves fold runner logic into this kernel; draining them first deepens the mirror. |
| 4 | `warm-session-seam`, `cartography-continuity`, `intake-predecomposed` | vf-drain | Drain after the kernel runs one shape and the dispatch registry is single-sourced, so warm producer and fresh reviewer sessions use one adapter contract and one unit shape. `intake-predecomposed` is already delivered. |
| 5 | `runner-loop-absorption`, `deploy-aware-merge-coordinator`, `profile-a-worktree-contract`, `engine-neutral-consumer-policy`, `vaultforeman-fix-parity` | hand-build + vf-drain | Absorb Profile A/B drain, review cache, and usage/run telemetry. Drain sub-backlogs in order: `docs/backlogs/wave-5-shakedown.md` (`profile-a-worktree-contract` shakedown first), then `docs/backlogs/wave-5.md` (the decomposed absorption). `deploy-aware-merge-coordinator` is a native new-build (no VaultForeman source asset) and is hand-build with operator review, not an unattended vf-drain. `vaultforeman-fix-parity` re-baselines the absorption against VaultForeman HEAD; decompose it after its deps land. Shared-file sibling conflicts fail closed to a human gate. Producer reads the runner-asset source map. |
| 6 | `flight-1`, `flight-2` | manual | Flight 1 proves the kernel on a disposable repo with deploy decoupled. Flight 2 proves a guarded real-deploy slice and is the go/no-go for trusting Woof with prod-deploying consumers. Operator run-sheets: `docs/flight/flight-1-induction.md`, `docs/flight/flight-2-audit-evidence.md`. |
| 7 | `conformance-audit`, `eval-instrumentation`, `intake-epic-enrichment` | vf-drain | Post-flight richness. Structural cartography is the deferred structural scope of `cartography-continuity`, not a separate unit. |
| 8 | `vaultforeman-retirement` | manual | Retire standalone VaultForeman once Woof is proven, carries the engine-neutral consumer policy, and no live run requires the VaultForeman path. |

Same-day requirements are placed as follows: project-owned producer/reviewer run profiles are in `policy-model` and preserved by `runner-loop-absorption`; deploy-aware Profile A merge and partial-merge reconciliation are in `deploy-aware-merge-coordinator`, exercised by `flight-1`; shared-file sibling conflicts fail closed to a human gate in `runner-loop-absorption`; the dispatch registry mismatch is an explicit `dispatch-swap` prerequisite before the warm-session and runner-loop waves.

## Notes

Cartography is retained as a first-class capability. The policy floor decides what is required for a repo and run; it does not create a second engine path.

Pre-authored `work_units[]` are already decomposed input. They skip epic decomposition but run through the same execution kernel.

A prod-deploying consumer is engine-agnostic: it declares its delivery policy once and either engine can drain it, so the engine is a per-run selection, not a migration the consumer undergoes. Two operational rules follow: never point an unproven engine at a prod-deploying repo (that is what flight 2 clears), and never run two engines against the same repo at once.

Single source of truth is a principal rule (architecture section 1). Each concept has one authoritative home and one bounded scope: routing and run profiles in `policy.toml`, one `work_units[]` schema for the executable unit, harness/model/effort vocabulary in the dispatch registry. `execution-shape-unification` and `config-routing-ssot` bring the runtime up to this rule; everything downstream must hold it.

Work-unit identity is local to its aggregate. Cross-aggregate references are structured (aggregate context plus local id), never an encoded string; UUIDs identify technical run, attempt, review, and audit records. The work-unit aggregate owns identity, dependency closure, acyclicity, and topological order, and deps are intra-aggregate. Every consumer of the canonical schema -- pm-structure, vault overlays, and vf-drain sub-backlog generators -- must hold these invariants and emit topologically-ordered units.
