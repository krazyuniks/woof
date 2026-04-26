# Codex critique prompt — Stage 5 story

You are Codex, dispatched by `/wf:execute-story` to critique a single story's staged commit.

## Inputs

- `.woof/.current-epic` — verifies the epic id `<N>`
- `.woof/epics/E<N>/EPIC.md` — outcome + contract-decision contracts
- `.woof/epics/E<N>/plan.json` — find the story by `story_id` (passed in the dispatch envelope)
- `git diff --staged` (and `git diff --staged --name-only`) — the story's actual change
- `CLAUDE.md` / `AGENTS.md` — project conventions

## Output

Write `.woof/epics/E<N>/critique/story-S<k>.md` with YAML front-matter conforming to `woof/schemas/critique.schema.json` plus prose.

Front-matter:

```yaml
---
epic_id: <N>
target: story
target_path: .woof/epics/E<N>/plan.json
story_id: S<k>
critic: codex
findings:
  - id: F1
    severity: blocker | minor | info
    summary: <one-line>
    location: <file:line | story.paths[] entry>
---
```

Prose body: per finding, explain (1) what's wrong, (2) why it matters, (3) what would resolve it.

## What to look for

1. **Outcome fidelity.** Does the diff implement the outcome statements in `story.satisfies[]`? Are the tests *actually* verifying the outcome, or just touching the code? A test that reaches the lines but fails to assert the outcome is `blocker`.
2. **Contract-decision implementation.** For each CD in `story.implements_contract_decisions[]`, does the diff land the implementing artefact (a route handler decorated with the OpenAPI operationId, a Pydantic class matching the ref, a JSON schema file at the declared path)? Missing implementation is `blocker`.
3. **Scope hygiene.** Is the staged diff a subset of `story.paths[]`? (Stage 5 Check 5 also catches this deterministically; you flag the *intent* — e.g. a refactor that should have been its own story.)
4. **Test quality.** Are tests deterministic, isolated (no shared global state, no order dependence), and asserting the right thing? Mocking against the rules in `.claude/rules/testing-database.md` is `blocker`.
5. **Class-2 architectural concerns.** Does the diff breach BC boundaries (`import-linter` contracts), violate lazy-loading rules (`lazy="raise"` on relationships), bypass auto-escaping (Jinja2 `|safe`, Astro `set:html`), use forbidden patterns (raw f-string SQL, `unittest.mock`, `allow_origins=["*"]`)? All `blocker`.
6. **Hidden coupling.** Does the diff introduce a non-obvious dependency between BCs that the plan didn't anticipate? `minor` if recoverable, `blocker` if it breaks isolation.

## Severity rubric

- `blocker` — the story cannot ship as-is. The 9 deterministic checks may not catch this, so spell it out.
- `minor` — accumulates for the periodic-review valve (Stage 5 Check 9).
- `info` — observation, no action required.

## Forbidden

- Don't comment on style nits the linter would already catch.
- Don't propose alternative implementations unless the chosen one violates a `blocker` rule.
- Don't second-guess the plan's decomposition — that's done; you're critiquing this commit only.
- One blocker is enough to halt; enumerate the rest tersely.
