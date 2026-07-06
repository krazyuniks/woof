---
type: adr
status: accepted
date: 2026-07-06
---

# ADR-015: Profile A Worktree Contract - Woof Discovers and Validates, Never Provisions

## Context

Profile A delivers each work unit in its own Git worktree. Something must create, place, and tear down those worktrees. Woof is one of two tools a project composes; the other is a host-level worktree engine. Making Woof own worktree lifecycle would fold a second concern into the engine and couple it to host provisioning details.

## Decision

Woof never provisions, mutates, destroys, or invokes the worktree engine. Worktree lifecycle - creation, placement, dirty-lease recovery, and teardown - is owned by the project-declared host-level worktree engine, orchestrated by the project's task runner. The two tools never call each other; the project calls both.

Woof owns deterministic discovery and fail-closed validation only:

- Repo policy declares the worktree root, the unit-to-path derivation rule, and an informational identity string naming the engine that provisions the worktrees. The identity is recorded for audit; it is not an invokable command.
- Derivation is deterministic: either root plus `work_unit_id`, or - when the worktree engine owns path allocation (for example slot-based placement) - an explicit per-unit map the task runner records in the run manifest. Woof reads the resulting paths; it never predicts or chooses them.
- The resolved unit-to-path derivation is recorded in run metadata.
- Preflight validates every ready unit's worktree: it exists, is a linked worktree of the target repo, is on the expected base or unit branch, is clean, and no two units share a path.
- Any anomaly fails closed: no auto-create, no silent fallback to a single tree, and no engine invocation to repair.

## Consequences

- Woof stays a portable engine with no host-provisioning logic; a consumer swaps its worktree engine without touching Woof.
- The project's task runner sequences provisioning (worktree engine) and delivery (Woof) and owns cleanup, including safe dirty-lease recovery; Woof never recommends or performs destructive recovery.
- `runner-loop-absorption` absorbs VaultForeman's drain, merge, telemetry, and review-cache surfaces but not worktree driving; that concern stays outside Woof.
- The policy schema gains a Profile A `worktree` block; preflight fails closed when Profile A is active and the block is absent or invalid.
