---
type: adr
status: accepted, amended by ADR-010 and ADR-011
date: 2026-06-01
---

# ADR-007: Single-Umbrella Operator Skill

Amended by ADR-010 and ADR-011. The single `/woof` operator surface remains; old discovery bucket and story wording yields to intake and `work_units[]`.

## Context

ADR-005 split the operator surface into three peer Claude Code skills (`/woof:setup`,
`/woof:map-codebase`, `/woof:run`) plus a nested target-architecture skill. It also asserted "there
is no `woof wf` user-facing command".

Both premises have since changed:

- A `woof wf` shell command now exists and is the deterministic graph entry point (`woof wf new`,
  `woof wf --epic N`, `woof wf --epic N --resolve`, and `woof wf reset`). The Python engine is the
  user-facing CLI; running an epic is a shell command, not a skill-only action.
- The wider tooling has pivoted from MCP servers towards plain CLIs that a skill makes discoverable.
  The exemplar is the `playwright-cli` skill: one skill is the whole command-map for one CLI, with
  `allowed-tools` as the guardrail and `references/` for depth. Three peer skills for one CLI is more
  surface than the model needs and splits one contained operator flow across entry points.

## Decision

One umbrella operator skill plus one interactive specialist.

| Skill | Role |
|---|---|
| `/woof` (`skills/woof/`) | The umbrella. The operator's map of the `woof` shell CLI - `wf` (new / run / `--resolve` / `reset`), `observe`, `check`, `validate`, `init`, `preflight`, `hooks`, `render-epic`, `audit-bundle` - grouped with usage, plus the gate-decision sets. It routes the design phase to `/woof:brainstorm`. Setup, map-codebase, run, and gate guidance lives in `references/`. |
| `/woof:brainstorm` (`skills/woof-brainstorm/`) | The one interactive specialist. It hosts the multi-turn design conversation (the two brainstorm loops), writes the resolved bundle into the epic's `discovery/brainstorm/` bucket, and hands off to `woof wf`. It is generated from the canonical agent-toolkit brainstorm skill plus a woof wrapper, pinned and drift-checked (`scripts/gen_woof_brainstorm.py`), so Woof stays standalone. |

### Mechanics

- The umbrella's guardrail is `allowed-tools: Bash(woof:*), Bash(git:*), Task` plus prose rules:
  never hand-edit `.woof/` state, surface gates rather than auto-approving, and confirm destructive
  verbs (`woof wf reset`).
- Skills shell out to the engine. `woof wf` is a shell command; the umbrella maps a request to the
  right `woof ...` call rather than re-implementing graph logic.
- Skill bundles ship under `skills/woof-<name>/` (and `skills/woof/` for the umbrella) and install
  into the operator's Claude Code skill directory under the `woof:` namespace.
- Gate resolution surfaces the open gate (`woof observe --view gate`) and resolves it only with an
  explicit operator decision (`woof wf --epic N --resolve <decision>`).
- Trackers are `github` (epics are GitHub issues; Woof's only external integration) and `local`
  (epics on disk; a Kanban board is `local` and lives a layer out, driving `woof wf new`).

This refines ADR-005: the same operator concerns are covered, but as one entry point over the CLI
rather than three peer skills, and `woof wf` is an acknowledged shell surface.

## Consequences

- One entry point. The operator reaches everything through `/woof`; depth lives in `references/`.
  Adding an operator concern is usually a new `woof` subcommand and a line in the command-map, not a
  new skill.
- The interactive design conversation stays a separate skill because it has its own vendored
  lifecycle and a multi-turn shape the umbrella does not.
- The destructive `woof wf reset` verb (reset an epic to its spark) backs the Start-fresh redo path
  in `/woof:brainstorm`.
- ADR-005's "no `woof wf` command" statement is superseded; its three-skill split is replaced by
  this umbrella. ADR-005 stays as the record of the prior model.
