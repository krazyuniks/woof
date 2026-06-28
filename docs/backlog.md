---
schema_version: 1
type: backlog
project_ref: woof
status: active
work_units:
  - id: schema-unification
    title: Unify execution schema on work_units
    kind: build
    state: todo
    priority: high
    summary: Move the canonical executable unit schema into Woof, retire story-shaped runtime contracts, and preserve graph dependency checks.
    acceptance:
      - Canonical Woof schema validates required work-unit fields and optional contract-trace fields.
      - Durable readers and writers use work_units without transitional story mirrors.
      - Duplicate ids, dangling deps, self-deps, and cycles fail validation.
  - id: policy-model
    title: Move project policy into repo-local Woof config
    kind: build
    state: todo
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
  - id: dispatch-swap
    title: Replace headless dispatch with the tmux harness
    kind: build
    state: todo
    priority: high
    summary: Remove headless worker dispatch and consume structured results from the shared interactive tmux harness.
    acceptance:
      - Producer and reviewer dispatches launch through tmux harness profiles.
      - Prompt-file delivery and structured result capture are covered by tests.
      - Engine code consumes verdict, evidence, usage, and session metadata without parsing raw terminal scrollback.
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
    state: todo
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
    summary: Reconcile existing cartography, structural index work, and policy floors with the merged engine.
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
    state: todo
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

1. Land schema unification, repo-local policy, intake, dispatch swap, and the warm-session seam.
2. Absorb the VaultForeman runner loop and add immutable run lineage.
3. Preserve cartography through policy floors and wire conformance checks to data.
4. Prove the merged engine on a guarded run.
5. Cut Freeflo over.
6. Retire the standalone VaultForeman runner.

## Notes

Cartography is retained as a first-class capability. The policy floor decides what is required for a repo and run; it does not create a second engine path.

Pre-authored `work_units[]` are already decomposed input. They skip epic decomposition but run through the same execution kernel.
