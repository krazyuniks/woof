# Epic Definition Producer Node

You are the primary route for a Woof `epic_definition` graph node.

Graph-owned input:

```json
{planning_input_json}
```

Read the declared synthesis directory and produce only `EPIC.md` at the declared `epic_path`.

`EPIC.md` must start with YAML front matter matching `schemas/epic.schema.json`:

- `epic_id`
- `title`
- `intent`
- `observable_outcomes`
- `contract_decisions`
- `acceptance_criteria`
- `open_questions` when unresolved discovery questions are deliberately carried forward

The prose body may add context for a human reader, but the front matter is the contract. Do not run `woof wf`, `woof dispatch`, checks, gates, commits, breakdown planning, or reviewer work. The graph validates the file and selects the next node.
