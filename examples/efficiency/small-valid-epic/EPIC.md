---
epic_id: 1
title: Small valid efficiency benchmark
intent: Measure Woof graph efficiency from a valid epic contract without exercising brainstorm.
observable_outcomes:
  - id: O1
    statement: A tiny benchmark note helper in the consumer source reports measured status as a JSON-serialisable object.
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes: [O1]
    title: Benchmark note result schema
    json_schema_ref: schemas/bench-note.schema.json
acceptance_criteria:
  - The run starts from this schema-valid EPIC.md, not spark-only input.
  - The planned story creates a helper in the consumer source package, a behaviour test marker, and the schema contract.
  - The automated test calls the helper and asserts the measured status result; it must not inspect Woof runtime files under .woof/.
  - The benchmark harness records graph state, dispatch telemetry, diff stats, and quality outcome outside the consumer story.
open_questions: []
resolved_open_questions: []
---

# Small valid efficiency benchmark

This fixture is deliberately small. It exists to measure the repeatable Woof
workflow path for a valid epic contract before changing prompts, model policy,
or graph behaviour.
