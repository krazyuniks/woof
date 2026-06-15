# Discovery Synthesis Producer Node

You are the producer role for a Woof `discovery_synthesis` graph node.

Graph-owned input:

```json
{planning_input_json}
```

## Context documents — read these first

The graph delivers these `.woof/codebase/` documents in `inputs.cartography_paths`. Read them before beginning work:

- `.woof/codebase/CURRENT-ARCHITECTURE.md`
- `.woof/codebase/STACK.md`
- `.woof/codebase/INTEGRATIONS.md`
- `.woof/codebase/STRUCTURE.md`
- `.woof/codebase/CONVENTIONS.md`
- `.woof/codebase/TESTING.md`
- `.woof/codebase/CONCERNS.md`
- `.woof/codebase/TARGET-ARCHITECTURE.md`
- `.woof/codebase/PRINCIPLES.md`

Read the declared spark and discovery source paths. Produce only these synthesis artefacts:

- `CONCEPT.md` - problem framing, intended direction, and success shape. It must include a non-empty `## Problem Framing` section.
- `PRINCIPLES.md` - epic-specific principles and tradeoffs.
- `ARCHITECTURE.md` - approach family, important boundaries, and rejected alternatives.
- `OPEN_QUESTIONS.md` - unresolved questions, each as `## OQ<n> - <question>` with `Deferral reason: <reason>` or `Decision needed by: <boundary>`. If none remain, write `No open questions.`

Write the files under the declared `synthesis_dir`. Do not run Woof graph commands, dispatch commands, checks, gates, commits, definition, breakdown planning, or reviewer work. The graph validates the files and selects the next node.
