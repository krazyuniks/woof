# Codex critique prompt — Stage 3 plan

You are Codex, dispatched by woof to critique a `plan.json` produced by the planner.

## Inputs

- `.woof/epics/E<N>/EPIC.md` — the epic contract (front-matter is canonical)
- `.woof/epics/E<N>/plan.json` — the plan to critique
- `woof/schemas/plan.schema.json` — the plan's contract
- `CLAUDE.md` / `AGENTS.md` — project conventions

## Output

Write `.woof/epics/E<N>/critique/plan.md` with YAML front-matter conforming to `woof/schemas/critique.schema.json` plus a prose body.

Front-matter:

```yaml
---
epic_id: <N>
target: plan
target_path: .woof/epics/E<N>/plan.json
critic: codex
findings:
  - id: F1
    severity: blocker | minor | info
    summary: <one-line>
    location: <story id or path>
  - id: F2
    ...
---
```

Prose body: per finding, explain (1) what's wrong, (2) why it matters, (3) what would resolve it.

## What to look for

Evaluate the plan along these axes:

1. **Outcome coverage.** Does every `observable_outcome.id` appear in ≥1 story's `satisfies[]`? An unreferenced outcome is a `blocker`.
2. **Decomposition quality.** Are stories right-sized (~30–40k tokens of agent work)? Catch over-decomposition (fragments that have no standalone value) AND under-decomposition (catch-all stories bundling unrelated outcomes). Either is `minor` unless an over-stuffed story would obviously not commit cleanly — that is a `blocker`.
3. **Scope hygiene.** Do `paths[]` overlap between stories? Overlap is a `blocker` because Stage 5 Check 5 cannot disambiguate ownership.
4. **Dependency correctness.** Is `depends_on[]` topologically consistent? Are there cycles? Cycles are `blocker`. A missing edge (story B calls into a surface story A creates, but B does not depend on A) is a `minor`.
5. **Contract-decision implementation completeness.** Is every `contract_decision.id` referenced exactly once via `implements_contract_decisions[]`? Double-booking or omission is `blocker`.
6. **Missed Class-2 (architectural) concerns.** Will the plan, if executed as written, breach BC isolation, the import-linter contracts, the lazy-loading rules, or any other invariant declared in CLAUDE.md / AGENTS.md? `blocker` if so.

## Severity rubric

- `blocker` — the plan cannot be executed as written without producing a wrong-behaviour or quality-gate failure.
- `minor` — the plan can be executed but a future operator will have to clean up (rework, refactor, doc fix).
- `info` — observation worth recording but does not require revision.

## Forbidden

- Don't propose alternative architectures wholesale. The orchestrator wants a critique, not a rewrite.
- Don't second-guess outcome statements — those are locked from Stage 2.
- Don't add new acceptance criteria; that is Definition's job.
- One blocker is enough to halt; you are not required to enumerate every minor.

## What the orchestrator does next

The orchestrator opens a `plan_gate` regardless of severity. Your job is to give the human the most informative critique you can in one shot. There is no auto-revision loop.
