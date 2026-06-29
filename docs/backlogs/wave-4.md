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
    summary: Dependency anchor completed before Wave 4 drain.
  - id: policy-model
    title: Move project policy into repo-local Woof config
    kind: build
    state: done
    priority: high
    summary: Dependency anchor completed before Wave 4 drain.
    deps: [schema-unification]
  - id: dispatch-swap
    title: Replace headless dispatch with the tmux harness
    kind: build
    state: done
    priority: high
    summary: Dependency anchor completed before Wave 4 drain.
    deps: [schema-unification, policy-model]
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
      - Pre-decomposed intake establishes the work-unit-set aggregate context and derives qualified references from it without fabricating an epic; when the source has no natural identity, intake assigns and persists a stable set_id once; epic-backed intake uses project_ref plus epic_id.
      - Decomposition emits work_units in topological dependency order so the runtime aggregate validates without reordering.
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
---

# Wave 4 Sub-Backlog

Drains `warm-session-seam`, `intake-enrichment`, and `cartography-continuity` after the Wave 3 kernel convergence. All three units have satisfied deps. Woof self-hosts under Profile B (commit + push, no PR).

Do not run VaultForeman against `docs/backlog.md` while this sub-backlog is active.
