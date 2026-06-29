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

The work unit is the execution entity. The work-unit aggregate owns the ordered collection and enforces identity and dependency invariants. A work-unit `id` is unique inside that aggregate. Cross-aggregate references carry structured context plus the local ID, for example `project_ref`, `epic_id`, and `work_unit_id`; Woof does not encode that context into a single canonical ID string. UUIDs are reserved for technical execution records such as runs, attempts, review records, and audit events. The aggregate context is a discriminated union of an epic context and a work-unit-set context (see architecture section 4); a stable `set_id`, assigned once at intake, carries pre-decomposed identity rather than a synthetic epic.

## Consequences

- The story object retires.
- Contract-trace fields are optional work-unit fields, not a separate story contract.
- Schema, durable readers, aggregate validation, checks, gates, and audit all converge on one shape.
- pm-structure and vault overlays consume Woof's canonical schema through drift checks.
