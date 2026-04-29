# Woof Implementation Plan

> **Purpose:** Single authoritative implementation plan and progress ledger for Woof.
> **Authority:** This file controls implementation sequencing, work-item status, validation evidence, and continuation prompts.
> **Inputs:** `README.md`, `docs/architecture.md`, `docs/adr/001-orchestration-topology.md`, and `docs/backlog.md`.

## Operating Loop

Each implementation turn must run the same loop.

1. Read the current project instructions, public overview, backlog, and this plan before editing.
2. Check `git status --short --branch` and preserve unrelated local changes.
3. Discover available project commands with `just --list` when command usage is not already established for the turn.
4. Select exactly one narrow work item from the ledger unless the current item explicitly requires a split.
5. Make code, schema, tests, and documentation changes together when behaviour or contracts move.
6. Run the smallest useful targeted validation during implementation, then run `just check` before handoff unless the change is docs-only or an external prerequisite blocks it.
7. Commit through the normal git hooks with a conventional commit message and do not bypass pre-commit or pre-push hooks.
8. Update this ledger with the work item status, validation result, blocker if any, and next continuation prompt before committing.
9. Push normally after hooks pass and report the pushed commit hash in the handoff.

## Observable Outcomes

The implementation loop is healthy only when every completed turn leaves these observable outcomes:

- The selected work item is either completed, explicitly blocked, or split into narrower follow-up items.
- The repository has a narrow conventional commit that passed the configured hooks.
- `just check` has a recorded pass result, or the ledger records the exact external blocker.
- Behavioural, schema, or command changes have matching documentation updates in the same commit.
- This ledger contains the next continuation prompt and the handoff contains the pushed commit hash, so another agent can resume without reconstructing state from chat history.

## Progress Ledger

| ID | Status | Work item | Validation | Commit | Continuation |
|---|---|---|---|---|---|
| IPL-001 | Completed | Add the operating loop, observable outcomes, and first work item for the implementation-plan ledger workflow. | `just check` passed: Ruff lint, Ruff format check, and 98 tests. | This commit. | Continue by promoting the highest-priority roadmap item from `docs/backlog.md` into this ledger before implementation. |
