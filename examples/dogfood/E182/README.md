# E182 Dogfood Evidence

> **Archive status:** This is retained as historical workflow evidence. It is
> not current operator guidance.

E182 converted the E181 failure into a Stage-5 safety epic: deterministic check runners, graph-owned gate writing, and driver-owned commit decisions.

## Retained artefacts

| File | Why it is retained |
|---|---|
| `discovery/synthesis/*.md` | Planning evidence for the architectural move from prompt-owned checks to deterministic graph boundaries. |
| `EPIC.md` | Contract for checker registry coverage, check-result output, gate authoring, executor results, and prompt drift tests. |
| `plan.json` | Bootstrap order showing why `S1` had to land the load-bearing safety boundary first. |
| `critique/plan.md` | Reviewer evidence that the plan covered outcomes and contract decisions. |
| `critique/story-S1.md` | Reviewer blocker showing the bootstrap deadlock risk and missing audit artefact concern. |
| `epic.jsonl` | Audit summary for discovery, definition, plan generation, mandatory plan gate, approval, and first story completion. |
| `dispatch.jsonl` | Compact subprocess summary with legacy role and harness names from the pre-ADR-002 implementation. |

## Lesson

E182 shows why Woof now treats prompts as producer instructions only. The reviewer found that a partially implemented checker registry could deadlock later stories if placeholder runners were treated as hard blockers. That feedback belongs in a human gate or deterministic graph state, not in a model-to-model debate loop.

No raw intake prompt is retained. One synthesis file still mentions `spark.md` as a historical source, but the reusable material is the resulting contract, synthesis, plan, critique, and event trail. The retained event trace uses the current `discovery_synthesised` event name for schema compatibility.
