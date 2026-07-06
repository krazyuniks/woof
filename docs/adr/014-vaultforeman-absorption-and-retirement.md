---
type: adr
status: accepted
date: 2026-06-28
---

# ADR-014: VaultForeman Is Absorbed and Retired After Proof

## Context

VaultForeman is the transitional live runner for the estate's current prod-deploying consumer and carries useful operational surfaces. Keeping it as a peer engine would preserve duplicate orchestration logic after Woof absorbs those surfaces.

## Decision

VaultForeman's standalone runner is absorbed into Woof and retired once the merged engine is at parity, has proved a guarded real-deploy run, carries the engine-neutral consumer policy, and no live run requires the standalone path.

Exactly three bounded transition surfaces exist, each with a named end trigger, and no fourth:

- **Live delivery.** VaultForeman keeps draining current consumer delivery until per-run engine selection has moved live consumers onto Woof.
- **Build executor.** The transitional `executor: name: vault_foreman` block in Woof's canonical backlog schema, the `vf-drain` wave instructions, and `VAULT_FOREMAN.md` exist only so VaultForeman can drain Woof's own merge backlog. They are swept when retirement lands.
- **Fallback and comparison.** VaultForeman is held as warm fallback with per-run comparison evidence through a post-cutover stability window.

No thin `vf` wrapper is built by default. If one is later proved useful it is a pure argv-translation shim over Woof with zero orchestration or state, proved after retirement, not before.

Schema authority is frozen in Woof: the canonical `work_units[]` schema lives in Woof, VaultForeman schema files take no independent evolution, and transitional VaultForeman drains validate against Woof's schema.

## Consequences

- Remaining VaultForeman consolidation work redirects into the Woof merge backlog.
- The `executor` block's drain-policy fields migrate into a Woof-native drain contract in `policy.toml` rather than dying undocumented; the retirement sweep is a migration, not a hole.
- Retirement requires a post-cutover stability window: at least three real Woof drains including at least one without per-merge confirmation, zero hand-recovery, VaultForeman fallback retained throughout, and the window length operator-set against delivery cadence.
- The long-term system has one runner, one work-unit schema, one harness boundary, and one project policy model.
