# Critique playbooks

Reviewer critique prompt templates are dispatched by semantic role route. The graph returns the appropriate template and expected output contract through `woof graph next-node`; the skill dispatches the reviewer and records the critique through a typed graph verb.

Two templates ship today:

- `plan.md` - Stage 3 critique of `plan.json` (outcome coverage, decomposition, scope hygiene, dependency correctness, contract-decision implementation completeness, missed Class-2 concerns).
- `work-unit.md` - Stage 5 critique of a work unit's staged commit (outcome fidelity, CD implementation, scope hygiene, test quality, Class-2 architectural concerns, hidden coupling).

Both templates output a critique document conforming to `woof/schemas/critique.schema.json` with structured findings (`severity: blocker | minor | info`).

The graph never auto-revises in response to a critique. Findings surface to the human via the plan gate or a Stage-5 work-unit gate.
