# Safety Model Examples

These examples show the core safety behaviours Woof enforces in the target
architecture. They point to live schemas and source files where the behaviour
already exists, and to the graph boundary where E1 moves orchestration behind
typed `woof graph` commands.

## Behaviour Map

| Safety behaviour | Current rule | What to inspect |
|---|---|---|
| Graph-owned orchestration | Python selects every successor node; producer and reviewer prompts do not choose workflow state. E1 exposes this through typed `woof graph` commands. | [`src/woof/graph/transitions.py`](../src/woof/graph/transitions.py), [`src/woof/graph/nodes.py`](../src/woof/graph/nodes.py) |
| Reviewer enforcement | Reviewer `blocker` critiques open a human gate; `info` and `minor` critiques receive a deterministic graph-owned disposition. | [`schemas/critique.schema.json`](../schemas/critique.schema.json), [`schemas/disposition.schema.json`](../schemas/disposition.schema.json) |
| Manifest-verified commits | Story commits stage and verify the exact manifest-computed file set. | [`src/woof/graph/manifest.py`](../src/woof/graph/manifest.py), [`schemas/transaction-manifest.schema.json`](../schemas/transaction-manifest.schema.json) |
| Gate resolution | Gates halt the graph; resolution is recorded as a structured event. | [`schemas/gate.schema.json`](../schemas/gate.schema.json), [`schemas/jsonl-events.schema.json`](../schemas/jsonl-events.schema.json) |
| Contract fidelity | Definition records user-facing contracts as native artefact references; Check 4 verifies the declared references. | [`schemas/epic.schema.json`](../schemas/epic.schema.json), [`src/woof/checks/runners/check_4_contract_refs.py`](../src/woof/checks/runners/check_4_contract_refs.py) |

## Graph-Owned Orchestration

The graph owns successor selection:

```text
spark.md
  -> discovery_research
  -> discovery_thinking
  -> discovery_brainstorm
  -> discovery_synthesis
  -> epic_definition
  -> breakdown_planning
  -> plan_critique
  -> plan_gate_open
  -> executor_dispatch
  -> critique_dispatch
  -> review_disposition
  -> verification
  -> commit
```

The important property is separation of responsibilities: producer prompts write
declared artefacts, reviewer prompts write critiques, and the graph owns
successor selection, typed state mutation, gate writing, verification, and
commits.

## Reviewer Enforcement

Stage 5 expects a reviewer critique at
`.woof/epics/E<N>/critique/story-S<k>.md`:

```yaml
target: story
target_id: S1
severity: blocker
findings:
  - id: F1
    severity: blocker
    category: outcome_coverage
    summary: The story does not satisfy O1.
```

`severity: blocker` opens a human gate. `severity: info` or `severity: minor`
continues only after the graph writes a matching deterministic disposition at
`.woof/epics/E<N>/dispositions/story-S<k>.md`.

## Manifest-Verified Commits

The graph computes a story transaction manifest before committing. The staged
index must equal `expected_paths` exactly; missing or extra paths open a gate.

Minimal shape:

```json
{
  "epic_id": 1,
  "story_id": "S1",
  "expected_paths": [
    ".woof/epics/E1/plan.json",
    ".woof/epics/E1/epic.jsonl",
    ".woof/epics/E1/dispatch.jsonl",
    ".woof/epics/E1/critique/story-S1.md",
    ".woof/epics/E1/dispositions/story-S1.md",
    "src/example.py",
    "tests/test_example.py"
  ],
  "story_paths": ["src/example.py", "tests/test_example.py"],
  "required_paths": [
    ".woof/epics/E1/plan.json",
    ".woof/epics/E1/epic.jsonl",
    ".woof/epics/E1/dispatch.jsonl",
    ".woof/epics/E1/critique/story-S1.md",
    ".woof/epics/E1/dispositions/story-S1.md"
  ],
  "audit_paths": []
}
```

## Gate Resolution

`gate.md` is an open runtime halt and is intentionally not committed. The
durable evidence is the structured resolution event in `epic.jsonl`:

```jsonl
{"event":"plan_gate_resolved","epic_id":1,"gate_type":"plan_gate","decision":"approve"}
{"event":"story_gate_resolved","epic_id":1,"story_id":"S1","gate_type":"story_gate","decision":"revise_story_scope"}
```
