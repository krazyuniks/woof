---
type: adr
status: accepted
date: 2026-05-27
---

# ADR-002: Role Routing

## Context

ADR-001 places orchestration in a Claude Code skill backed by a Python graph library. The skill dispatches LLM work as typed roles. Roles are semantic, not provider-named: the dispatch policy chooses the best model per stage without leaking provider names into prompt prose or graph state.

Stage 5 (story execution) is qualitatively different from other stages: it is the only stage that writes code. The producer for Stage 5 benefits most from LSP; the reviewer for Stage 5 benefits from independence from the producer's narrative. The role policy reflects this.

## Decision

Roles are semantic. The skill orchestrator dispatches by role name; the route picks the adapter.

| Role | Responsibility |
|---|---|
| `producer` | Produces an artefact for a graph stage: discovery document, epic definition, plan, story diff, mapper document. |
| `reviewer` | Critiques a producer artefact. Classifies findings as `info`, `minor`, or `blocker`. Operates in a fresh context: never sees the producer's working memory. |
| `mapper` | Authors one or two themed cartography documents from codebase exploration. Same context-isolation invariant as a reviewer. |
| `gate-resolver` | Surfaces an open gate in the operator's session and records the structured decision. |

### Route policy per stage

| Stage | Producer | Reviewer |
|---|---|---|
| 1. Discovery (research, thinking, ideate, synthesis) | Codex | Claude |
| 2. Definition | Codex | Claude |
| 3. Breakdown / planning | Codex | Claude |
| 4. Plan gate | (deterministic) | n/a |
| 5. Story execution | Claude (LSP) | Codex (independent verifier) |
| 5. Verification | (deterministic checks) | n/a |

Mapper subagents are Claude. LSP improves accuracy when writing `CURRENT-ARCHITECTURE.md`, `STRUCTURE.md`, and `CONVENTIONS.md`.

The graph continues after reviewer `info` or `minor` findings. For non-blocking story findings, the graph records a deterministic covering disposition rather than dispatching another producer turn. Reviewer `blocker` findings open a human gate. There is no model-to-model debate loop.

### Dispatch mechanism

| Target | Transport |
|---|---|
| Claude subagent | `Task` tool with a Woof-defined subagent type (`woof-producer`, `woof-reviewer`, `woof-mapper`). Each subagent receives only the scoped prompt and explicit artefact references. |
| Codex CLI | `Bash` invocation of `codex exec` with the prompt on stdin and the JSON output captured. No MCP exposure of the operator session. |

Producer-reviewer separation is by construction: each subagent dispatch is a fresh context with isolated working memory.

Default scaffolded routes are declared in `.woof/agents.toml` per consumer repo. The schema exposes a route table keyed by `route_key` or node group. Defaults match the table above; Stage 5 declares an explicit override. `woof graph next-node` returns the resolved `route_key` in the dispatch contract so the skill can select the correct transport without hardcoding provider policy.

## Consequences

- Per-stage route policy is data in `.woof/agents.toml`, not code in the graph.
- `primary` and `critiquer` are migration aliases only. Canonical prompt prose, schemas, and docs use `producer`, `reviewer`, `mapper`, and `gate-resolver`.
- Stage 5 is the only stage where the producer is Claude. The default policy makes this explicit.
- Adding a stage or role is a schema change plus a routing-policy update, not a prompt change.
- Producer-reviewer separation does not depend on a process boundary. It depends on the orchestrator giving each subagent a fresh context.
- Codex has no LSP integration. Structural facts a Codex node needs are injected by the orchestrator from cartography or tree-sitter queries.
- Prompts, schemas, and CLI output use the semantic role names. Provider names appear only in route configuration and dispatch audit records.
