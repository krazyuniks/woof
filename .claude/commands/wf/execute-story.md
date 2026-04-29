---
description: Pure story executor node. Produces code/test changes and executor_result.json only.
allowed-tools: Bash(just:*), Bash(git:*), Bash(test:*), Bash(ls:*), Bash(cat:*), Bash(grep:*), Bash(jq:*), Bash(rg:*), Read, Edit, Write, Glob, Grep
argument-hint: "<E<N>> <S<k>>"
---

# /wf:execute-story

You are an LLM producer node inside Woof's deterministic Python graph. The graph owns orchestration, critique dispatch, verification, gates, and commits.

`$ARGUMENTS` resolves to `<E<N>> <S<k>>`.

## Read

1. `.woof/.current-epic`
2. `.woof/epics/E<N>/plan.json`
3. `.woof/epics/E<N>/EPIC.md`
4. `CLAUDE.md` / `AGENTS.md` if present

## Produce

Implement only the selected story's declared `paths[]` scope. Add or update tests for the story's `satisfies[]` outcomes. Run the project's normal quality command if one is declared in `.woof/quality-gates.toml`.

Then write `.woof/epics/E<N>/executor_result.json` atomically:

```json
{
  "epic_id": 1,
  "story_id": "S1",
  "outcome": "staged_for_verification",
  "commit_body": "One paragraph summary.",
  "position": null
}
```

Use `outcome: "aborted_with_position"` when the story cannot be completed inside scope. Use `outcome: "empty_diff"` when no diff is needed because prior work already realised the outcome. In both cases set `commit_body` to null and write a concrete `position`.

## Do Not

- Do not dispatch Codex or any other subprocess.
- Do not run `woof check`.
- Do not write or edit `gate.md`.
- Do not commit.
- Do not select the next step.

Exit 0 only after `executor_result.json` exists.
