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
    summary: Dependency anchor completed in the root backlog before this Wave 2 drain.
  - id: policy-model
    title: Move project policy into repo-local Woof config
    kind: build
    state: done
    priority: high
    summary: Dependency anchor completed by the manual Wave 2 policy-spine build.
    deps: [schema-unification]
  - id: dispatch-swap
    title: Replace headless dispatch with the tmux harness
    kind: build
    state: done
    priority: high
    summary: Remove headless worker dispatch and consume structured results from the shared interactive tmux harness. Consolidate VaultForeman's harness/model/effort registry into Woof's dispatcher before absorbing produce/review logic.
    deps: [schema-unification, policy-model]
    acceptance:
      - Producer and reviewer dispatches launch through tmux harness profiles.
      - Prompt-file delivery and structured result capture are covered by tests.
      - Engine code consumes verdict, evidence, usage, and session metadata without parsing raw terminal scrollback.
      - VaultForeman's harness/model/effort registry is consolidated into Woof's dispatcher before produce/review loop absorption.
  - id: run-lineage-immutable-attempts
    title: Add run lineage and immutable attempt artefacts
    kind: build
    state: todo
    priority: high
    summary: Thread run identity through events and preserve every attempt for replay, review-cache reuse, and instability detection.
    deps: [schema-unification, policy-model]
    acceptance:
      - Events and dispatch artefacts are joinable by run id and work-unit id.
      - Repeated review over the same diff hash and prompt version reuses the prior verdict.
      - Conflicting verdicts over the same inputs are recorded as review instability.
---

# Wave 2 Sub-Backlog

This executable backlog drains the remaining Wave 2 units after the manual `policy-model` build. Do not run VaultForeman against `docs/backlog.md` while this sub-backlog is active.
