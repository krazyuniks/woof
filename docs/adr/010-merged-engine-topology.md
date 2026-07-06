---
type: adr
status: accepted
date: 2026-06-28
---

# ADR-010: Woof Is the Merged Engine

## Context

Woof and VaultForeman both implement the same delivery loop: decompose work, produce changes, run deterministic checks, review independently, fix, publish, and merge. VaultForeman has proven live runner surfaces under production consumer load. Woof has the stronger durable graph, state, schema, gate, and audit model.

## Decision

Woof is the surviving engine. VaultForeman's runner assets fold into Woof: profile A/B publish shapes, queue draining, serial merge coordination, warm producer/fresh reviewer fix rounds, usage telemetry, review caching, and hardened tmux-based review capture.

There is one engine path after intake has produced `work_units[]`.

## Consequences

- VaultForeman's standalone runner is migration source code, not the long-term engine.
- Woof absorbs the operational surfaces needed for registered consumer repos.
- A registered prod-deploying consumer stays on VaultForeman until the merged Woof engine has proved a guarded run.
- The product boundary remains shareable Woof plus repo-local policy, not operator-local orchestration scripts.
