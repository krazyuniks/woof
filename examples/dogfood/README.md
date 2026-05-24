# Dogfood Evidence

> **Archive status:** These artefacts are retained as historical workflow
> evidence. They are not current operator guidance. Use `README.md`,
> `docs/architecture.md`, and `docs/consumers.md` for the current public
> workflow.

These are selected public artefacts from Woof's first two internal acceptance epics.

- `E181` demonstrates the audit redaction / size-cap epic that exposed the critique-blocker handling failure.
- `E182` demonstrates the Stage-5 driver/checker epic that exposed the larger orchestration-topology flaw captured in ADR-001.
- [../safety-model.md](../safety-model.md) maps those retained artefacts to the current safety behaviours: graph-owned orchestration, reviewer enforcement, manifest-verified commits, gate resolution, and E146 contract fidelity.

## Curation policy

This folder keeps only reusable evidence:

- contracts: `EPIC.md`
- plans: `plan.json`
- critiques: `critique/*.md`
- audit summaries: `epic.jsonl` and `dispatch.jsonl`
- gates: preserved as `epic.jsonl` gate events when no standalone `gate.md` was retained
- lessons: each epic README summarises the failure mode and why the artefacts matter

Raw `audit/` transcripts are intentionally omitted. They contain bulky harness command/output captures from the consumer repository and are not needed to understand the workflow evidence. Raw intake prompts, handoff prompts, and one-off operator instructions are also omitted because they are not stable examples of Woof behaviour.

These examples pre-date ADR-002, so some preserved artefacts contain legacy provider-shaped role names, wrapper names, and harness fields. Treat those entries as compatibility evidence only; current prompts and docs use `primary` / `reviewer` role terminology.

## Retained examples

| Epic | Evidence value | Key files |
|---|---|---|
| `E181` | Audit redaction and size-cap contract, plus the missed reviewer blocker that forced the deterministic gate/check work. | `EPIC.md`, `plan.json`, `critique/`, `epic.jsonl`, `dispatch.jsonl`, `README.md` |
| `E182` | Stage-5 checker and driver contract, including the bootstrap deadlock found by review and the planning graph evidence that led to ADR-001. | `EPIC.md`, `plan.json`, `discovery/synthesis/`, `critique/`, `epic.jsonl`, `dispatch.jsonl`, `README.md` |
