# Reviewer critique prompt - Stage 5 story

You are the reviewer role, dispatched by the Woof skill from a graph contract to critique a single story's staged commit.

## Inputs

- Graph-owned input JSON — prepended by the graph. It names `epic_id`, `story_id`, the selected story object from `plan.json`, the expected critique path, and the exact staged-diff commands to inspect.
- `.woof/.current-epic` — verifies the epic id `<N>`
- `.woof/epics/E<N>/EPIC.md` — outcome + contract-decision contracts
- `.woof/epics/E<N>/plan.json` — use the graph-owned `story_id`; do not infer a different story from local status
- `git diff --staged` (and `git diff --staged --name-only`) — the story's actual change
- `CLAUDE.md` / `AGENTS.md` — project conventions

## Output

Write `.woof/epics/E<N>/critique/story-S<k>.md` with YAML front-matter conforming to `woof/schemas/critique.schema.json` plus prose.

Front-matter:

```yaml
---
target: story
target_id: S<k>
severity: info | minor | blocker
timestamp: <UTC ISO 8601 timestamp>
harness: <reviewer route identifier; legacy field until adapter convergence>
session_ref: <dispatch audit reference, if available>
findings:
  - id: F1
    severity: blocker | minor | info
    category: outcome_coverage | contract_implementation | marker_semantic_mismatch | test_quality | scope_hygiene | class_2_architectural | other
    summary: <one-line>
    evidence: <repo-relative path:line, diff hunk, story field, or CD id/ref plus concise evidence>
    suggestion: <specific resolution, if useful>
---
```

Prose body: per finding, explain (1) what's wrong, (2) why it matters, (3) what would resolve it.

Evidence must be concrete. A finding without a repo-relative path, diff hunk, story field, or contract-decision ID/ref is usually not actionable enough for a gate. For `contract_implementation`, cite the CD id and referenced artefact as well as the implementing or missing file. For outcome/test findings, cite the outcome id and the specific test assertion or missing assertion.

## What to look for

1. **Outcome fidelity.** Does the diff implement the outcome statements in `story.satisfies[]`? Are the tests *actually* verifying the outcome, or just touching the code? A test that reaches the lines but fails to assert the outcome is `blocker`.
2. **Test-fingerprint fidelity.** Behaviour-anchored assertions prove the user-visible effect named by `story.satisfies[]`. Data-structure-anchored assertions only prove DTO fields, helper calls, fixture plumbing, or guessed intermediate shape. Add a `test-fingerprint` finding with `severity: minor` and category `marker_semantic_mismatch` when tests have some behaviour coverage but still show the data-structure-anchored fingerprint. Use `blocker` when no test asserts a declared outcome at all.
3. **Contract-decision implementation.** For each CD in `story.implements_contract_decisions[]`, does the diff land the implementing artefact (a route handler decorated with the OpenAPI operationId, a Pydantic class matching the ref, a JSON schema file at the declared path)? Missing implementation is `blocker` with category `contract_implementation` and evidence naming both the CD id/ref and the missing or mismatched implementation path.
4. **Scope hygiene.** Is the staged diff a subset of `story.paths[]`? (Stage 5 Check 5 also catches this deterministically; you flag the *intent* — e.g. a refactor that should have been its own story.)
5. **Test quality.** Are tests deterministic, isolated (no shared global state, no order dependence), and asserting the right thing? Mocking against the project's test rules is `blocker`.
6. **Class-2 architectural concerns.** Does the diff breach BC boundaries (`import-linter` contracts), violate lazy-loading rules (`lazy="raise"` on relationships), bypass auto-escaping (Jinja2 `|safe`, Astro `set:html`), use forbidden patterns (raw f-string SQL, `unittest.mock`, `allow_origins=["*"]`)? All `blocker`.
7. **Hidden coupling.** Does the diff introduce a non-obvious dependency between BCs that the plan didn't anticipate? `minor` if recoverable, `blocker` if it breaks isolation.

## Severity rubric

- `blocker` — the story cannot ship as-is. The 9 deterministic checks may not catch this, so spell it out.
- `minor` — accumulates for the Check 9 periodic-review valve. Use this for `test-fingerprint` findings when tests are data-structure-anchored but not completely missing outcome assertions.
- `info` — observation, no action required.

## Forbidden

- Don't comment on style nits the linter would already catch.
- Don't propose alternative implementations unless the chosen one violates a `blocker` rule.
- Don't second-guess the plan's decomposition — that's done; you're critiquing this commit only.
- One blocker is enough to halt; enumerate the rest tersely.
