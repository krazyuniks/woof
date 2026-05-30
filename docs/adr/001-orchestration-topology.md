---
type: adr
status: accepted
date: 2026-05-27
---

# ADR-001: Orchestration Topology

## Context

Woof orchestrates an inner-loop SDLC pipeline. LLM inference must not own workflow control: a producer can skip a safety check while still producing plausible prose. The orchestrator must:

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
| Graph | Pure, deterministic transitions. Given on-disk state, returns the next node and its inputs. Validates artefacts against schemas. Writes typed JSONL audit events as a consequence of typed commands. No LLM picks successors. | Python library at `src/woof/`, exposed via `woof graph <command>`. |
| Orchestrator | Holds in-memory context warm across an epic. Loads cartography slices. Dispatches producer and reviewer subagents. Surfaces gates. Calls the graph library via Bash. | Claude Code skill at `skills/woof-run/`. |
| Dispatched | Stateless, isolated LLM invocations. Each subagent receives only its prompt and scoped artefacts. Output is written back to state via the graph library. | `Task` subagents for Claude; `Bash + codex exec` for Codex. |

State on disk is the only authoritative record. Orchestrator in-memory context is opportunistic; a new session reconstructs from disk.

Project setup commands (`woof init`, `woof preflight`, `woof hooks install`) are non-interactive Python CLI utilities used once per project lifecycle. They are not parallel orchestrators for running epics.

Human gates remain graph states: the graph halts on `gate.md` until `woof graph resolve-gate <decision>` records a structured decision.

`woof graph next-node` is the skill-facing state query. It returns a dispatch, deterministic, gate, or complete contract plus a `state_token`. Mutating commands require the observed `state_token` and fail if canonical state changed. The graph takes short locks during mutation only; it never holds a lock across an LLM dispatch.

Dispatched output is persisted through typed graph verbs such as `record-executor-result` and `record-story-critique`. There is no raw "append event" API for the skill.

## Consequences

- Running an epic requires Claude Code as the operator's runtime.
- The skill assumes the 1M Opus context tier.
- Single-threaded within a session: parallel-story execution is not supported.
- Producer-reviewer separation is preserved by construction: each subagent dispatch is a fresh context.
- Crash recovery is a state-layer property: any orchestrator can resume from on-disk state.
- Graph layer is callable from outside Python via `woof graph <command>`. It remains the schema-and-validation authority.
- Graph commands expose typed verbs rather than generic mutation primitives; invalid state should be hard to express from the skill.
- A non-Claude orchestrator surface is a strict addition; state and graph do not change.
- Adding a stage or node type is a source, schema, test, and docs change.
- Malformed or unsafe state opens a gate or fails loud; it is never silently repaired by a prompt.
