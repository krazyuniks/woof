---
layer: design
status: complete
authored_by: operator
refresh: human-driven (only when architectural strategy changes)
---

# Target Architecture

The shape Woof is designed to be. This is the design-layer cartography document
(ADR-004): human-authored intent, loaded into Stage 2, 2.5, 3, and 5 dispatch
payloads so producers and reviewers build toward the intended shape, not merely the
observed one. `CURRENT-ARCHITECTURE.md` records what the code *is*; this document
records what it is *for*. Where the two diverge, this document is the destination and
the divergence is tracked work, not the target.

## Brownfield stance

**Aspirational/mixed.** Woof is mid-build. The target is the fully-realised
architecture set out declaratively in `docs/architecture.md`; the codebase is
converging on it. `docs/architecture.md` is the authoritative long-form statement of
this target — its rollout notes mark exactly where current trails target, and
`docs/backlog.md` carries the remaining epics. A producer or reviewer should treat the
documented architecture as the contract and treat any shortfall in the current code as
debt to close in the direction stated here, never as a pattern to extend.

## Target topology (four layers, ADR-001)

Each layer holds a single responsibility and an invariant that must not erode:

- **State.** Files under `.woof/` are the only authoritative record. Invariant: all
  position is reconstructable from disk; no in-memory or skill-held state is load-bearing
  across a crash or session switch.
- **Engine.** Deterministic Python at `src/woof/`, run in-process by `woof wf`. Pure
  transitions, schema validation, typed audit/event writes, dispatch, verification, and
  commit. Invariant: **no LLM picks a successor** — `next_node` derives the next node from
  on-disk artefact presence alone. Inference never drives control flow.
- **Operator skill.** The single `/woof` umbrella over the `woof` CLI, plus the
  `/woof:brainstorm` design specialist (ADR-007). Invariant: the skill maps requests to
  CLI calls and surfaces gates; it is opportunistic and reconstructable, never a second
  source of graph authority. No node-by-node orchestrator skill, no decomposed `woof
  graph` command API (that ADR-005 direction is withdrawn).
- **Dispatched.** Stateless, isolated LLM invocations spawned by node handlers — `Task`
  subagents for Claude, `Bash + codex exec` for Codex. Invariant: each subagent receives
  only its scoped prompt and artefact references and cannot widen its own scope.

## Target stage pipeline

Five stages plus the Stage 2.5 readiness boundary, per-work-unit commits, and gate halts:
Discovery (locks direction) -> Definition (locks surface) -> Contract readiness
(deterministic pre-plan boundary) -> Breakdown/Plan -> Plan gate -> Work-unit execution.
Stage 5 is tracer-bullet red-green-refactor per declared outcome; verification is the
deterministic Stage-5 check matrix with no LLM in the loop. Role routing is per-stage
(ADR-002): Codex produces and Claude reviews in Stages 1-3; Stage 5 overrides so Claude
produces (it holds the LSP) and Codex reviews independently; mappers are Claude.

## Target cartography consumption

Cartography (`.woof/codebase/`) is a mandatory prerequisite (ADR-004), failing preflight
closed when absent or stub. The target end-state is per-node loading: every dispatch
receives exactly the cartography subset its stage needs (the loading map in
`docs/architecture.md` section 4), records `artefacts_loaded[]` telemetry, and the design
layer is consumed as intent by definition, planning, and execution nodes. The mechanical
layer regenerates every commit; mapper docs refresh on demand; this design layer is
human-driven.

## Boundaries — what Woof must not become

- No LLM selecting graph successors or mutating `.woof/` directly.
- No second graph authority: the structural index (ADR-009) is read-only query verbs, not
  an orchestration API or MCP graph.
- No silent repair: missing, malformed, or unsafe state opens a gate or fails preflight.
- No commit outside declared work-unit scope: the transaction manifest is the commit-safety
  boundary.
- tmux and other supervision surfaces never own workflow state or pick transitions.
