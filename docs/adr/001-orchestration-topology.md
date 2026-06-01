---
type: adr
status: accepted
date: 2026-05-27
---

# ADR-001: Orchestration Topology

2026-06-01 update: ADR-007 withdraws the `woof graph` command API and the `/woof:run` orchestrator skill described in the original decision. The retained decision is the layered ownership model: state on disk, Python engine, operator skill, and isolated dispatched workers. The current implementation exposes the engine through `woof wf` and the operator surface through the `/woof` umbrella.

## Context

Woof orchestrates an inner-loop SDLC pipeline. LLM inference must not own workflow control: a producer can skip a safety check while still producing plausible prose. The workflow controller must:

- Hold codebase context warm across an epic so dispatched nodes do not pay tokens to rediscover the repo.
- Surface gates conversationally to the operator.
- Preserve producer-reviewer context isolation.
- Use LSP where the dispatched model supports it.
- Provide one entry point per operator task.
- Support crash recovery from on-disk state.

## Decision

Woof is layered. Each layer has one responsibility.

| Layer | Responsibility | Implementation |
|---|---|---|
| State | Durable, schema-governed record of every epic, plan, gate, dispatch, and cartography artefact. | Files under `.woof/`. |
| Engine | Deterministic transitions, schema validation, typed audit/event writes, dispatch orchestration, and the resumable graph runner. No LLM picks successors. | Python at `src/woof/`, exposed via `woof wf`. |
| Operator skill | Maps operator requests to `woof` CLI calls, routes the design phase to `/woof:brainstorm`, and surfaces gates. It does not own graph state. | Claude Code umbrella skill at `skills/woof/`. |
| Dispatched | Stateless, isolated LLM invocations. Each subagent receives only its prompt and scoped artefacts. Output is written back by the engine. | `Task` subagents for Claude; `Bash + codex exec` for Codex. |

State on disk is the only authoritative record. Operator-skill context is opportunistic; a new session reconstructs from disk.

Project setup commands (`woof init`, `woof preflight`, `woof hooks install`) are non-interactive Python CLI utilities used once per project lifecycle. They are not parallel runners for epics.

Human gates remain graph states: the graph halts on `gate.md` until `woof wf --epic N --resolve <decision>` records a structured decision.

`woof wf --epic N` derives the next node from on-disk state and runs the graph until it halts at a gate, encounters malformed state, or completes the epic. `--once` steps one node. Mutating state by hand under `.woof/` is not an operator workflow.

## Consequences

- Running an epic requires Claude Code as the operator's runtime.
- The skill assumes the 1M Opus context tier.
- Single-threaded within a session: parallel-story execution is not supported.
- Producer-reviewer separation is preserved by construction: each subagent dispatch is a fresh context.
- Crash recovery is a state-layer property: any operator surface can resume from on-disk state.
- The Python engine remains the schema-and-validation authority.
- `woof wf` is the single run/resume surface; a separate `woof graph` command API is not planned.
- A non-Claude operator surface is a strict addition; state and engine behaviour do not change.
- Adding a stage or node type is a source, schema, test, and docs change.
- Malformed or unsafe state opens a gate or fails loud; it is never silently repaired by a prompt.
