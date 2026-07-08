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
  summary: Dependency anchor completed before the Wave 4 drain.
- id: cartography-continuity
  title: Retain cartography as a policy-enforced capability
  kind: build
  state: done
  priority: medium
  summary: Move cartography-floor selection into policy.toml cartography.floor (adding
    a no-cartography level) and reconcile the existing ADR-004/ADR-009 cartography
    artefacts and refresh hook with the merged engine. Existing cartography is reused,
    not re-derived. Structural cartography is the deferred structural scope of this
    unit.
  deps:
  - policy-model
  acceptance:
  - Repo policy can require no cartography, lexical/design cartography, or structural
    cartography.
  - Required cartography is enforced before execution.
  - Producer, reviewer, and deterministic checks consume declared cartography on the
    same engine path.
---

# Wave 4 Sub-Backlog

Drains `cartography-continuity`, the remaining Wave 4 unit. The other Wave 4 units are delivered: `warm-session-seam` landed in the earlier Wave 4 drain, and the master backlog split the pre-split `intake-enrichment` unit into `intake-predecomposed` (delivered, repo 9d6a768) and `intake-epic-enrichment` (moved to Wave 7). Woof self-hosts under Profile B (commit + push, no PR).

Do not run VaultForeman against `docs/backlog.md` while this sub-backlog is active. When the unit lands, mark `cartography-continuity` done in the master backlog.
