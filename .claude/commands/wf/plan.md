---
description: Pure primary planning producer node. Produces plan.json only.
allowed-tools: Bash(woof:*), Bash(./bin/woof:*), Bash(./woof/bin/woof:*), Bash(test:*), Bash(ls:*), Bash(cat:*), Bash(jq:*), Read, Write, Edit, Glob, Grep
argument-hint: "<E<N>>"
---

# /wf:plan

You are a producer node inside Woof's deterministic Python graph. You produce `.woof/epics/E<N>/plan.json` and exit. The graph owns critique dispatch, plan gates, successor selection, and all later execution.

Read:

1. `.woof/.current-epic`
2. `.woof/epics/E<N>/EPIC.md`
3. `playbooks/planning/breakdown.md` or `woof/playbooks/planning/breakdown.md`, depending on checkout layout
4. `schemas/plan.schema.json` or `woof/schemas/plan.schema.json`, depending on checkout layout
5. `CLAUDE.md` / `AGENTS.md` if present
6. `.woof/codebase/{tree.txt,tags,freshness.json}` if present

Follow the `breakdown.md` producer prompt. Validate the JSON before writing when the Woof CLI is available. Write only `plan.json` using tmp-file plus rename. Do not write gates, dispatch critique, or revise the epic.
