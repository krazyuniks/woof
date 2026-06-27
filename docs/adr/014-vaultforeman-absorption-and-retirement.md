---
type: adr
status: accepted
date: 2026-06-28
---

# ADR-014: VaultForeman Is Absorbed and Retired After Cutover

## Context

VaultForeman is currently the live runner for Freeflo and carries useful operational surfaces. Keeping it as a peer engine would preserve duplicate orchestration logic after Woof absorbs those surfaces.

## Decision

VaultForeman's standalone runner is absorbed into Woof and retired after the merged engine proves a guarded run and Freeflo is stable on it. A thin compatibility command may remain only as a wrapper over Woof if it proves useful.

## Consequences

- VaultForeman keeps running Freeflo during the transition.
- Remaining VaultForeman consolidation work redirects into the Woof merge backlog.
- The long-term system has one runner, one work-unit schema, one harness boundary, and one project policy model.
