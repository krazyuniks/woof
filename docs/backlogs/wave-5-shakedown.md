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
work_units:
- id: policy-model
  title: Move project policy into repo-local Woof config
  kind: build
  state: done
  priority: high
  summary: Dependency anchor completed before this shakedown drain.
- id: profile-a-worktree-contract
  title: Profile A worktree discovery and fail-closed validation
  kind: build
  state: done
  priority: high
  summary: Policy-declared worktree root; deterministic unit-to-path derivation;
    fail-closed preflight validation of provisioned worktrees. Woof discovers and
    validates, never provisions. Fully specified by ADR-015 plus the policy.schema.json
    worktree block.
  deps:
  - policy-model
  acceptance:
  - Policy declares the worktree root and unit-to-path derivation without naming the provider that provisions worktrees.
  - Unit-to-path derivation is deterministic (root plus work_unit_id, or an explicit
    per-unit map in the run manifest) and recorded in run metadata.
  - Preflight validates every ready unit's worktree -- it exists, is a linked worktree
    of the target repo, is on the expected base or unit branch, is clean, and no two
    units share a path.
  - 'Any anomaly fails closed: no auto-create, no silent fallback to a single tree,
    no engine invocation to repair (ADR-015).'
---

# Wave 5 Shakedown Sub-Backlog

One small, fully-specified unit to shake down the Woof-repo drain path before the large wave-5 absorption. Drain this file first, confirm the unit lands on main, then move to `docs/backlogs/wave-5.md`.

The unit is specified by ADR-015 and the `worktree` block in `schemas/policy.schema.json`. Woof self-hosts under Profile B (commit + push, no PR).

Do not run VaultForeman against `docs/backlog.md` while this sub-backlog is active. After the unit lands, mark `profile-a-worktree-contract` done in the master backlog; `docs/backlogs/wave-5.md` carries it as a done dependency anchor and must not be drained before that is true.
