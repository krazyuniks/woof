---
type: adr
status: accepted
date: 2026-06-28
---

# ADR-011: `work_units[]` Are the Executable Contract

## Context

Woof used stories as the execution unit. VaultForeman and pm-structure use `work_units[]`. Keeping both creates a schema mirror and forces adapters to translate between two names for the same executable thing.

## Decision

`work_units[]` are Woof's single executable unit shape. Epic-backed intake normally decomposes `epic.md` into `work_units[]`. A supplied `work_units[]` backlog is accepted as pre-decomposed intake and skips decomposition.

The engine never reverse-generates an epic from work units. Epic data enriches decomposition and trace checks when present; work units remain the executable end entity.

## Consequences

- The story object retires.
- Contract-trace fields are optional work-unit fields, not a separate story contract.
- Schema, durable readers, checks, gates, and audit all converge on one shape.
- pm-structure and vault overlays consume Woof's canonical schema through drift checks.
