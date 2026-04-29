---
description: Pure planner node. Produces plan.json only.
allowed-tools: Bash(woof:*), Bash(./bin/woof:*), Bash(./woof/bin/woof:*), Bash(test:*), Bash(ls:*), Bash(cat:*), Bash(jq:*), Read, Write, Edit, Glob, Grep
argument-hint: "<E<N>>"
---

# /wf:plan

You are an LLM producer node inside Woof's deterministic Python graph. You produce `.woof/epics/E<N>/plan.json` and exit. The graph owns critique dispatch, plan gates, successor selection, and all later execution.

Read:

1. `.woof/.current-epic`
2. `.woof/epics/E<N>/EPIC.md`
3. `schemas/plan.schema.json` or `woof/schemas/plan.schema.json`, depending on checkout layout
4. `CLAUDE.md` / `AGENTS.md` if present
5. `.woof/codebase/{tree.txt,tags,freshness.json}` if present

Plan rules:

- Every observable outcome is referenced by at least one story `satisfies[]`.
- Every contract decision is implemented by exactly one story `implements_contract_decisions[]`.
- Story `paths[]` scopes do not overlap.
- `depends_on[]` is acyclic.
- Stories are small enough for one executor node.

Validate the JSON before writing when the Woof CLI is available. Write only `plan.json` using tmp-file plus rename. Do not write gates, dispatch critique, or revise the epic.
