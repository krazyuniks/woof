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
    summary: Move the canonical executable unit schema into Woof, retire story-shaped runtime contracts, and preserve graph dependency checks.
    acceptance:
      - Canonical Woof schema validates required work-unit fields and optional contract-trace fields.
      - Backlog front matter accepts the document-level executor block needed by transitional VaultForeman drains without adding custom per-unit wave fields.
      - Durable readers and writers use work_units without transitional story mirrors.
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
  - id: intake-enrichment
    title: Implement epic sources, enrichment, and pre-decomposed intake
    kind: build
    state: todo
    priority: high
    summary: Support greenfield, GitHub, local-docs, and pre-decomposed work-unit sources through one intake boundary.
    deps: [schema-unification, policy-model]
    acceptance:
      - Epic-backed intake follows sparse epic to optional enrichment to epic to work_units.
      - Pre-decomposed work_units validate and skip decomposition.
      - Intake records run metadata without reverse-generating a missing epic.
      - Decomposition produces work_units through the existing breakdown playbook and brainstorm enrichment, not a second decomposer; the auto-decompose step this unit builds replaces the manual decompose earlier waves relied on.
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
    state: todo
    priority: high
    summary: Collapse the runtime plan/story shape onto the canonical work_units shape (one lifecycle field, work-unit ids, named checks and gates), remove the story_id/work_unit_id mirror, and retire story-named playbooks and self-cartography. Realises the ADR-011 convergence that schema-unification left at the intake boundary.
    deps: [schema-unification]
    acceptance:
      - One canonical work-unit schema validates id, title, kind, state, and the optional contract-trace fields; the plan/runtime artefact and the backlog artefact share it, with no status-versus-state dual lifecycle.
      - Runtime gates, checks, dispositions, and events key on work-unit id; no event carries both story_id and work_unit_id.
      - Deterministic checks and gate types are named around work units rather than numbered Stage-5 story checks, and the gate writer can emit the work-unit gate.
      - Producer and reviewer playbooks and Woof's self-cartography use work-unit terminology; no story.md playbook remains.
      - An invariant guard test covers the single inbound legacy-shape normaliser (accept legacy input, reject dual shape), and a duplicate work-unit id case is tested.
      - Dead back-compat aliases are deleted in this change.
  - id: config-routing-ssot
    title: Make policy.toml the single routing and run-profile authority
    kind: build
    state: todo
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
    state: todo
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
    summary: Bring dependency draining, profile A/B delivery, usage telemetry, review cache, and serial merge coordination into Woof.
    deps: [schema-unification, policy-model, warm-session-seam]
    acceptance:
      - Work units run in dependency order with blocked/downstream reporting.
      - Profile A publishes ready pull requests and serially merges the ready queue as deploy-aware transactions.
      - Profile A waits for mergeability/check recompute after main moves, spaces deploy-triggering merges until the configured terminal deploy checks settle, and treats proved Terraform state-lock contention as bounded-retryable rather than terminal.
      - Partial-merge reconciliation records already-merged units before halting on any later terminal failure.
      - "Shared-file sibling conflicts are reconciled semantically: the later slice's intended diff is reapplied onto current main, expected symbols/tests are verified after resolution, and the gate reruns before merge."
      - Runner absorption preserves project-owned producer/reviewer slot selection and engine-owned harness adapters, execution, parsing, and validation.
      - Profile B commits and pushes through graph-owned transactions.
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
    state: todo
    priority: medium
    summary: Move cartography-floor selection into policy.toml cartography.floor (adding a no-cartography level) and reconcile the existing ADR-004/ADR-009 cartography artefacts and refresh hook with the merged engine. Existing cartography is reused, not re-derived.
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
      - Eval manifests attribute cost and loaded artefacts by node and work unit.
      - Prompt/output bodies are retained according to audit policy.
      - The first optimisation target is chosen from measured data.
  - id: first-flight
    title: Prove the merged engine on a guarded run
    kind: action
    state: todo
    priority: high
    summary: Run a low-risk backlog or guarded Freeflo slice through the merged engine end to end before Freeflo cutover.
    deps: [intake-enrichment, runner-loop-absorption, run-lineage-immutable-attempts, cartography-continuity]
    acceptance:
      - A complete run produces, gates, reviews, fixes, publishes, and records audit artefacts.
      - Resume, gate handling, and Profile A merge-settle/deploy-spacing behaviour are exercised.
      - The result identifies any required fix before Freeflo migration.
  - id: freeflo-cutover
    title: Cut Freeflo onto the merged Woof engine
    kind: action
    state: todo
    priority: high
    summary: Move Freeflo delivery from VaultForeman onto Woof after the guarded first flight.
    deps: [first-flight]
    acceptance:
      - Freeflo policy and delivery backlog run through Woof profile A.
      - Freeflo pull-request readiness and serial merge behaviour match the target.
      - The legacy VaultForeman path is no longer needed for active Freeflo delivery.
  - id: vaultforeman-retirement
    title: Retire the standalone VaultForeman runner
    kind: action
    state: todo
    priority: medium
    summary: Remove or reduce VaultForeman to a thin compatibility wrapper once Freeflo is stable on Woof.
    deps: [freeflo-cutover]
    acceptance:
      - Active project records point to Woof as the orchestration engine.
      - Standalone runner entry points are removed or wrap Woof without duplicate logic.
      - VaultForeman records state the retired boundary.
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

This is the forward work queue for the VaultForeman/Woof merge. It contains work to do. Historical stage/story epics are not retained here as a second plan; git history carries the old backlog.

The architecture target is `docs/architecture.md`. Decision records are in `docs/adr/`. The glossary is `docs/CONTEXT.md`.

## Operating Order

1. Hand-build schema unification and the safety-defect sweep.
2. Build the policy spine by hand, then drain policy-adjacent runner work in dependency order.
3. Hand-build the execution-shape and config-routing convergence so the kernel runs one `work_units[]` shape and one routing authority before any further runner logic is absorbed.
4. Drain the warm-session, cartography-continuity, and intake-enrichment units.
5. Drain runner-loop absorption, conformance audit, and eval instrumentation.
6. Run the guarded first flight manually.
7. Cut Freeflo over manually, then retire or wrap the standalone VaultForeman runner.

## Wave Instructions

The `How` value controls execution mechanics:

- `hand-build` means the operator decomposes and implements the unit directly in the Woof checkout, using normal repo checks and commits. It is for contract/spine work that is too foundational to hand to the transitional runner.
- `vf-drain` means the unit is decomposed into a schema-valid `work_units[]` sub-backlog and run through `vf orchestrate`. The Woof VaultForeman run profile lives in `VAULT_FOREMAN.md`; backlog front matter owns only executor, timeout, drain, and state-update policy. Do not run `vf orchestrate docs/backlog.md` while hand-build or manual wave units are still `todo`; drain from a wave sub-backlog or after the earlier units are marked done.
- `manual` means an operational proof, cutover, or retirement step where the operator owns sequencing and judgement. It may run tools, but it is not an unattended producer drain.

| Wave | Units | How | Instructions |
|---|---|---|---|
| 0 | Runner-asset source map | done | Source map is in `~/Work/vault/records/radianit/projects/woof/planning/runner-asset-source-map.md`. |
| 1 | `schema-unification`, `safety-defect-sweep` | hand-build | Start here. Preserve one canonical `work_units[]` schema, keep the VaultForeman `executor` document block valid for transitional drains, retire story-shaped runtime mirrors, and keep graph dependency validation fail-closed. |
| 2 | `policy-model`, `dispatch-swap`, `run-lineage-immutable-attempts` | hand-build + vf-drain | Hand-build the repo-local policy schema/spine first. In `dispatch-swap`, consolidate VaultForeman's harness/model/effort registry into Woof's dispatcher before any produce/review logic is absorbed. |
| 3 | `execution-shape-unification`, `config-routing-ssot` | hand-build | Foundational convergence. Collapse the runtime to one `work_units[]` shape (retire the `status`/`state` dual lifecycle and the `story_id`/`work_unit_id` mirror; rename checks, gates, and playbooks off story) and make `policy.toml` the single routing/run-profile authority (retire `agents.toml` routing, single-source the registry vocab, delete dead headless builders). Hand-build because the vf-drain waves fold runner logic into this kernel; draining them first deepens the mirror. |
| 4 | `warm-session-seam`, `cartography-continuity`, `intake-enrichment` | vf-drain | Drain after the kernel runs one shape and the dispatch registry is single-sourced, so warm producer and fresh reviewer sessions use one adapter contract and one unit shape. |
| 5 | `runner-loop-absorption`, `conformance-audit`, `eval-instrumentation` | vf-drain | Absorb Profile A/B drain, deploy-aware merge pacing, partial-merge reconciliation, semantic sibling-conflict reconciliation, review cache, and usage/run telemetry. Producer reads the runner-asset source map. |
| 6 | `first-flight` | manual | Prove the merged engine on a throwaway or guarded low-risk backlog before Freeflo. Exercise resume, gate handling, Profile A merge-settle/deploy-spacing, and audit evidence. |
| 7 | `freeflo-cutover`, `vaultforeman-retirement` | manual | Cut Freeflo over only after first flight. Resolve the `lane_plan.py` / `lane_launcher.py` design call here; retire standalone VaultForeman once Freeflo is stable on Woof. |

Same-day requirements are placed as follows: project-owned producer/reviewer run profiles are in `policy-model` and preserved by `runner-loop-absorption`; deploy-aware Profile A merge and partial-merge reconciliation are in `runner-loop-absorption` and exercised by `first-flight`; semantic sibling-conflict reconciliation is in `runner-loop-absorption`; the dispatch registry mismatch is an explicit `dispatch-swap` prerequisite before the warm-session and runner-loop waves.

## Notes

Cartography is retained as a first-class capability. The policy floor decides what is required for a repo and run; it does not create a second engine path.

Pre-authored `work_units[]` are already decomposed input. They skip epic decomposition but run through the same execution kernel.

Single source of truth is a principal rule (architecture section 1). Each concept has one authoritative home and one bounded scope: routing and run profiles in `policy.toml`, one `work_units[]` schema for the executable unit, harness/model/effort vocabulary in the dispatch registry. `execution-shape-unification` and `config-routing-ssot` bring the runtime up to this rule; everything downstream must hold it.
