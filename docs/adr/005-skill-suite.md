---
type: adr
status: superseded by ADR-007
date: 2026-05-27
---

# ADR-005: Skill Suite

Superseded 2026-06-01 by ADR-007. The three peer skills and `woof graph` command API described here are withdrawn. The current operator surface is one `/woof` umbrella over the `woof` CLI plus the `/woof:brainstorm` design specialist.

## Context

The operator runs Woof from a Claude Code session. The operator surface must be:

- Clear: one entry point per task. No parallel surfaces for the same job.
- Interactive: questions surface to the operator conversationally.
- Declarative about role boundaries: setup, mapping, and epic execution are distinct skills.

## Decision

Three operator-facing skills.

| Skill | Purpose | Calls |
|---|---|---|
| `/woof:setup` | Onboard a new consumer repo. | `woof init`; the target-architecture authoring skill; optionally `/woof:map-codebase`. |
| `/woof:map-codebase` | Regenerate cartography mapper documents. | Parallel mapper subagents (one per theme bucket); mechanical-layer refresh; freshness stamp. |
| `/woof:run` | Create or resume one epic and drive it forward. | `woof graph create-epic`; `woof graph resume-epic`; `woof graph next-node`; producer and reviewer subagents; typed `record-*` verbs; deterministic graph-node execution; gate surfacing; operator gate resolution. |

A fourth, nested skill — target-architecture authoring — is invoked only by `/woof:setup`. It is not a standalone operator surface.

### Skill mechanics

- Skill bundles ship under `skills/woof-<name>/` in the Woof repo and install to the operator's Claude Code skill directory.
- Each bundle contains: `SKILL.md` (instructions for Claude), `templates/` (markdown fragments the skill writes), helper scripts the skill invokes.
- Skills invoke the deterministic engine via Bash: `woof graph <command>`, `woof init`, `woof preflight`.
- Skills dispatch LLM work via the Task tool (Claude subagents) or Bash (`codex exec`).
- Skills use `AskUserQuestion` for multi-choice operator questions and free-text reply for prose answers.
- Skills never call a raw JSONL append command. All state mutation uses typed graph verbs guarded by `state_token`.

### Python boundary

The Python `src/woof/` package is the deterministic engine library and the setup CLI:

- Engine library: graph transitions, schema validation, JSONL audit, tracker abstractions.
- Setup CLI: `woof init`, `woof preflight`, `woof hooks install`.
- Graph CLI surface: `woof graph <command>` exposes library functions for skill invocation.

There is no `woof wf` user-facing command. Running an epic is `/woof:run`. There is no parallel headless orchestration surface.

## Consequences

- Running an epic requires Claude Code as the operator's runtime.
- One entry point per task: setup, map, run.
- Skill bundles are first-class repo artefacts maintained alongside the engine.
- The four skills together (three operator-facing + one nested) are the only operator interface for the inner loop.
- Operator-facing UI is conversational: structured choices via `AskUserQuestion`, prose via reply, progress via skill output.
- Adding a new operator concern is a new skill, not a new CLI command.
