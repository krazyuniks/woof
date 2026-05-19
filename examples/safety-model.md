# Safety Model Examples

These examples show the core safety behaviours Woof enforces. They are concise
on purpose: use them as an index into the retained dogfood artefacts and current
schemas, not as a full replay log.

The dogfood artefacts pre-date ADR-002, so some preserved events use legacy
provider-shaped role names. Current Woof behaviour is role-led: `primary`
produces, `reviewer` critiques, and the Python graph selects every successor
state.

## Behaviour Map

| Safety behaviour | Example | What to inspect |
|---|---|---|
| Graph-owned orchestration | E182 moved Stage 5 from prompt-owned sequencing to deterministic graph boundaries. | [dogfood/E182/EPIC.md](dogfood/E182/EPIC.md), [dogfood/E182/plan.json](dogfood/E182/plan.json), [../docs/adr/001-orchestration-topology.md](../docs/adr/001-orchestration-topology.md) |
| Second-LLM critique enforcement | E181 recorded a missed reviewer blocker; E182 turned it into Check 6. | [dogfood/E181/epic.jsonl](dogfood/E181/epic.jsonl), [dogfood/E182/critique/story-S1.md](dogfood/E182/critique/story-S1.md) |
| Manifest-verified commits | Current graph commits only the manifest-computed file set. | [../src/woof/graph/manifest.py](../src/woof/graph/manifest.py), [../schemas/transaction-manifest.schema.json](../schemas/transaction-manifest.schema.json) |
| Gate resolution | Gates halt the graph; resolution is recorded as a structured event. | [dogfood/E181/epic.jsonl](dogfood/E181/epic.jsonl), [../schemas/jsonl-events.schema.json](../schemas/jsonl-events.schema.json) |
| E146 contract fidelity | Definition records user-facing contracts as native artefact references; Check 4 verifies the declared references through the shared `check-cd` helper. | [../docs/research.md](../docs/research.md), [../schemas/epic.schema.json](../schemas/epic.schema.json), [../src/woof/checks/runners/check_4_contract_refs.py](../src/woof/checks/runners/check_4_contract_refs.py) |

## 1. Graph-Owned Orchestration

E182 is the concrete topology example. The retained trace separates generation,
critique, gate opening, gate resolution, and story completion:

```jsonl
{"event":"plan_generated","epic_id":182,"story_count":4}
{"event":"plan_critiqued","epic_id":182,"findings_count":0}
{"event":"plan_gate_opened","epic_id":182,"gate_type":"plan_gate"}
{"event":"plan_gate_resolved","epic_id":182,"decision":"approve"}
{"event":"story_completed","epic_id":182,"story_id":"S1"}
```

The important property is not the historical event names. It is the separation
of responsibilities: producer prompts write declared artefacts; reviewer prompts
write critiques; the graph owns successor selection, gate writing, and commits.

## 2. Second-LLM Critique Enforcement

E181 shows the failure mode that Woof now prevents. A reviewer blocker was
identified after the story had already landed, so the event stream had to record
a manual recovery gate:

```jsonl
{"event":"story_gate_opened","epic_id":181,"story_id":"S2","gate_type":"story_gate","triggered_by":["check_6_critique_blocker","manual"]}
{"event":"story_gate_resolved","epic_id":181,"story_id":"S2","decision":"revise_story_scope"}
```

Current Stage 5 makes this deterministic. The reviewer writes
`critique/story-S<k>.md`; Check 6 validates the critique front-matter and blocks
on `severity: blocker`. Non-blocking `info` and `minor` critiques require a
primary disposition instead of a model-to-model debate loop.

E182's `critique/story-S1.md` is the retained blocker example:

```yaml
target: story
target_id: S1
severity: blocker
findings:
  - id: F1
    severity: blocker
    category: outcome_coverage
```

## 3. Manifest-Verified Commits

The graph computes a story transaction manifest before committing. The staged
index must equal `expected_paths` exactly; missing or extra paths open a gate.

Minimal shape:

```json
{
  "epic_id": 182,
  "story_id": "S1",
  "expected_paths": [
    ".woof/epics/E182/plan.json",
    ".woof/epics/E182/epic.jsonl",
    ".woof/epics/E182/dispatch.jsonl",
    ".woof/epics/E182/critique/story-S1.md",
    ".woof/epics/E182/dispositions/story-S1.md",
    "src/example.py",
    "tests/test_example.py"
  ],
  "story_paths": ["src/example.py", "tests/test_example.py"],
  "required_paths": [
    ".woof/epics/E182/plan.json",
    ".woof/epics/E182/epic.jsonl",
    ".woof/epics/E182/dispatch.jsonl",
    ".woof/epics/E182/critique/story-S1.md",
    ".woof/epics/E182/dispositions/story-S1.md"
  ],
  "audit_paths": []
}
```

E182's story critique includes the historical missing-audit-artefact finding.
Current Woof closes that class by deriving `audit_paths` from committed
`.woof/epics/E<N>/audit/` files and verifying the staged index before commit.

## 4. Gate Resolution

`gate.md` is an open runtime halt and is intentionally not committed. The durable
evidence is the structured resolution event in `epic.jsonl`.

Current plan gate shape:

```jsonl
{"event":"plan_gate_opened","epic_id":182,"gate_type":"plan_gate","triggered_by":["plan_review"]}
{"event":"plan_gate_resolved","epic_id":182,"gate_type":"plan_gate","decision":"approve"}
```

Story gate example from E181:

```jsonl
{"event":"story_gate_opened","epic_id":181,"story_id":"S2","gate_type":"story_gate","triggered_by":["check_6_critique_blocker","manual"]}
{"event":"story_gate_resolved","epic_id":181,"story_id":"S2","gate_type":"story_gate","decision":"revise_story_scope"}
```

The graph reads the structured decision enum and resumes from that state. The
models do not negotiate or reinterpret the gate outcome.

## 5. E146 Contract Fidelity

E146's failure was contract substitution: repo convention replaced the epic's
declared user-facing route. Woof prevents that by making Definition record
contract decisions as artefact references and by making Stage 5 Check 4 validate
the declared references for the story-owned contract decisions. Outcome markers
and reviewer critique then inspect whether the implementation actually satisfies
the declared outcome.

Minimal `EPIC.md` front-matter pattern:

```yaml
observable_outcomes:
  - id: O1
    statement: "Authenticated user can update a comment through PATCH /api/v1/comments/{id}"
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes: [O1]
    title: "Comment update route"
    openapi_ref: "spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch"
    notes: "Legacy repo routes may be bridged, but the OpenAPI ref is the contract."
acceptance_criteria:
  - "Tests assert O1 against the route declared in CD1."
```

If `CD1` points at a missing or malformed OpenAPI path, Check 4 fails loud. If
the code targets only a legacy route, the story still has to satisfy `O1` and the
reviewer has a concrete contract reference to critique against. The fix is to
update the implementation or explicitly add a bridge contract, not to silently
replace the epic surface.
