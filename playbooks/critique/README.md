# Critique playbooks

Reviewer critique prompt templates are dispatched by semantic role route. The graph reads the appropriate template, substitutes the epic / story context where needed, and invokes:

```
./woof/bin/woof dispatch --role reviewer \
  --epic <N> [--story <Sk>] \
  --prompt-file <path-to-template>
```

Two templates ship today:

- `plan.md` — Stage 3 critique of `plan.json` (outcome coverage, decomposition, scope hygiene, dependency correctness, contract-decision implementation completeness, missed Class-2 concerns).
- `story.md` — Stage 5 critique of a story's staged commit (outcome fidelity, CD implementation, scope hygiene, test quality, Class-2 architectural concerns, hidden coupling).

Both templates output a critique document conforming to `woof/schemas/critique.schema.json` with structured findings (`severity: blocker | minor | info`).

The graph never auto-revises in response to a critique. Findings surface to the human via the inline `plan_gate` (Stage 4) or `story_gate` (Stage 6).
