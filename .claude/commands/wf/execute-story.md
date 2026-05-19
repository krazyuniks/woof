---
description: Pure primary story producer node. Produces code/test changes and executor_result.json only.
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

### Tracer-bullet red-green-refactor discipline

Before editing, enumerate the selected story's `story.satisfies[]` outcomes and match each outcome ID to its statement in `EPIC.md`.

Work one outcome at a time:

1. RED: write one assertion-bearing test for the next outcome before implementation. The test must fail when the declared behaviour is absent, and it must assert the observable outcome rather than only internal data shape, helper calls, or fixture wiring.
2. GREEN: implement the smallest vertical slice that makes that outcome pass while preserving earlier GREEN outcomes.
3. Run the configured quality command after each cycle when `.woof/quality-gates.toml` declares one.

After all outcomes are GREEN, run a refactor pass with the tests as the harness, then run the configured quality command again.

Avoid the horizontal-slicing anti-pattern: all tests first then all implementation. That pattern produces the imagined-behaviour fingerprint: tests mirror assumed data structures or setup plumbing instead of proving the declared outcome through behaviour.

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

- Do not dispatch the reviewer or any other subprocess.
- Do not run `woof check`.
- Do not write or edit `gate.md`.
- Do not commit.
- Do not select the next step.

Exit 0 only after `executor_result.json` exists.
