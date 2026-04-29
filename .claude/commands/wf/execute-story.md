---
description: Execute one woof story end-to-end (Stage 5 inner sequence). Invoked as a cld -p subprocess by 'just wf-run'. Produces executor_result.json and exits 0; the driver verifies and commits.
allowed-tools: Bash(just:*), Bash(./woof/bin/woof:*), Bash(git:*), Bash(test:*), Bash(ls:*), Bash(cat:*), Bash(grep:*), Bash(jq:*), Bash(rg:*), Read, Edit, Write, Glob, Grep
argument-hint: "<E<N>> <S<k>>"
---

# /wf:execute-story — Stage 5 inner sequence (executor role)

You are the story-executor. The `wf-run` driver spawned you to produce a staged diff. You do not verify, commit, or write gate YAML — those belong to the driver. Your contract ends when you write `executor_result.json` and exit 0.

`$ARGUMENTS` resolves to `<E<N>> <S<k>>`.

## Bootstrap

Read in order:

1. `.woof/.current-epic` — verify the epic id
2. `.woof/epics/E<N>/plan.json` — find your story by id
3. `.woof/epics/E<N>/EPIC.md` — front-matter for outcomes / contract decisions referenced by your story
4. `CLAUDE.md` / `AGENTS.md` — project conventions

## Inner sequence

1. **Code.** Edit only files matching `story.paths[]` (git-pathspec globs). Files outside that set are a scope violation — if you cannot complete the story within scope, go to step 5 (abort).
2. **Tests.** Add or modify tests asserting `story.satisfies[]` outcomes. Each `O<n>` in `satisfies[]` must be referenced (literal `O<n>` token, word-boundary anchored) by at least one test in the diff.
3. **Refactor.** Tighten only if it does not widen the diff beyond `paths[]`.
4. **Continuous quality gate.** Run the quality-gate command from `.woof/quality-gates.toml` until it exits 0. This is a precondition — do not proceed until the gate is green.
5. **Codex critique.** Dispatch:

   ```
   ./woof/bin/woof dispatch codex --role critiquer --epic <N> --story <Sk> \
       --prompt-file woof/playbooks/critique/story.md
   ```

   The critique writes `.woof/epics/E<N>/critique/story-S<k>.md`. Read the `severity` field. If `severity: blocker`, proceed to **abort path** (step 7b).

6. **Stage the commit transaction.** `git add` all paths matching `story.paths[]` PLUS:
   - `.woof/epics/E<N>/plan.json`
   - `.woof/epics/E<N>/critique/story-S<k>.md`
   - `.woof/epics/E<N>/epic.jsonl`
   - `.woof/epics/E<N>/dispatch.jsonl`
   - `.woof/epics/E<N>/audit/`

   Update `plan.json` story status to `done` and append `story_completed` to `epic.jsonl` before staging.

7. **Write `executor_result.json`** at `.woof/epics/E<N>/executor_result.json` and exit 0.

   **a. Normal path** (diff staged, critique not blocker):
   ```json
   {
     "epic_id": <N>,
     "story_id": "<Sk>",
     "outcome": "staged_for_verification",
     "commit_body": "<one-paragraph summary of what was implemented>",
     "position": null
   }
   ```

   **b. Abort path** (critique blocker, or scope violation, or cannot proceed):
   ```json
   {
     "epic_id": <N>,
     "story_id": "<Sk>",
     "outcome": "aborted_with_position",
     "commit_body": null,
     "position": "<prose: what the critique found, why you halted, recommended resolution>"
   }
   ```
   Do NOT stage any diff when aborting. The driver reads `position` and writes a story gate.

   **c. Empty diff path** (quality gate green but diff is empty):
   ```json
   {
     "epic_id": <N>,
     "story_id": "<Sk>",
     "outcome": "empty_diff",
     "commit_body": null,
     "position": "<prose: which earlier story already realised this outcome, or why the diff is legitimately empty>"
   }
   ```

## Subprocess discipline

- **No conversation.** You are running headless.
- **No interactive prompts.** All decisions derive from filesystem state and schemas.
- **No off-spec excursions.** If you cannot complete the story within scope, write `executor_result.json` with `outcome: aborted_with_position` and exit 0.
- **Write executor_result.json once.** Atomic write: write to a `.tmp` suffix, then rename.
- **Atomic writes for `plan.json` / `epic.jsonl`.** Use tmp-file + `mv` for `plan.json`; append-mode for `epic.jsonl`.

## Exit codes

- `0` — `executor_result.json` written; driver reads it and proceeds.
- `non-zero` — crash (no `executor_result.json`); driver writes a crash gate automatically.

Do not exit 0 without writing `executor_result.json`.
