# Woof

Woof is an inner-loop SDLC tool for AI-assisted software delivery. It owns discovery, definition, breakdown, review, gate, execution, verification, manifest-checked commit, and audit through a deterministic Python graph. The operator drives Woof from a Claude Code session.

Producer and reviewer subagents create artefacts and critiques; humans resolve explicit gates. Woof owns state transitions, schemas, checks, transaction manifests, and commit decisions.

## Install

Woof currently ships the Python engine. The Claude Code skill suite is part of the redesign backlog and is documented as the target operator surface; its installer is added when E3 ships.

```bash
uv tool install git+https://github.com/krazyuniks/woof@main
```

Confirm the engine is available:

```bash
woof --help
```

## Consumer setup

Run Woof against the repository you want it to manage. The target skill suite walks you through onboarding:

```
/woof:setup
```

This invokes `woof init` for file scaffolding, prompts you to author the target architecture and design principles for the project, and optionally runs `/woof:map-codebase` to produce the current-state codebase documentation.

## Operator workflow

Three operator-facing skills cover the inner loop. One entry point per task.

| Skill | When to use |
|---|---|
| `/woof:setup` | Onboard a new consumer repository. |
| `/woof:map-codebase` | Regenerate the codebase mapper documents when the codebase has changed materially. |
| `/woof:run` | Execute an epic. |

Inside `/woof:run`, the skill drives or resumes one epic. It may start from a new spark, resume `.woof/.current-epic`, or resume an explicit `E<N>`. The skill calls `woof graph next-node`, dispatches producer and reviewer subagents for dispatch-shaped nodes, calls typed `woof graph record-*` commands for model-produced artefacts, runs graph-owned deterministic nodes through `woof graph run-deterministic-node`, surfaces gates conversationally, and records gate resolutions. The on-disk state under `.woof/` is authoritative; the skill's in-session context is opportunistic and reconstructed from disk on a new session.

The target graph checks contract readiness after definition and before planning. That gate runs early, but not immediately after epic creation: at creation time Woof only has a spark. Once `EPIC.md` exists, Woof can deterministically check whether acceptance criteria are machine-checkable, contract decisions are concrete, and referenced existing paths resolve before any model decomposes the work.

## Cartography

Every consumer repository carries a mandatory cartography artefact group at `.woof/codebase/`:

- Human-authored design layer (`TARGET-ARCHITECTURE.md`, `PRINCIPLES.md`).
- Mapper-authored AS-IS layer (`CURRENT-ARCHITECTURE.md`, `STACK.md`, `INTEGRATIONS.md`, `STRUCTURE.md`, `CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md`).
- Mechanical layer (`tags`, `files.txt`, `freshness.json`) refreshed on every commit.

The skill orchestrator loads the relevant subset per node, so producer and reviewer subagents do not pay tokens to rediscover the repo. See `docs/adr/004-cartography-prerequisite.md`.

## Runtime model

Dispatched agents run in trusted-local mode. Woof does not sandbox them, restrict commands, restrict writable paths, block network access, or add an MCP restriction layer. The safety boundary is before changes land: deterministic checks, reviewer critique, human gates, transaction manifests, and graph-owned commit decisions.

The runtime model is reported by `woof preflight` and by the skill at epic start.

Woof is allowed to be opinionated about expert-local tooling. tmux may be used for long-running supervision, logs, and dashboards when it improves operability. It is not a workflow authority; graph state, typed commands, gates, and commits remain owned by `.woof/` and the Python engine.

Quality gates support two postures in the target architecture. Strict mode blocks on any failure. Baseline mode starts as a command-level brownfield posture: pre-existing red commands can be recorded and reported without blocking, while fine-grained per-failure subtraction requires a structured parser or machine-readable gate output.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — system architecture: layers, stages, cartography, role routing, schemas, gates, transaction manifests, prerequisites.
- [`docs/backlog.md`](docs/backlog.md) — open work, prescriptive.
- [`docs/implementation-plan.md`](docs/implementation-plan.md) — how the backlog gets executed.
- [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md) — layered topology.
- [`docs/adr/002-graph-led-role-routing.md`](docs/adr/002-graph-led-role-routing.md) — semantic role routing.
- [`docs/adr/003-issue-tracker-abstraction.md`](docs/adr/003-issue-tracker-abstraction.md) — tracker protocol.
- [`docs/adr/004-cartography-prerequisite.md`](docs/adr/004-cartography-prerequisite.md) — cartography artefact group.
- [`docs/adr/005-skill-suite.md`](docs/adr/005-skill-suite.md) — operator skill suite.
- [`docs/adr/006-operational-resilience.md`](docs/adr/006-operational-resilience.md) — readiness, dispatch telemetry, circuit breaker, baseline gates, reviewer evidence, drift detection, tmux supervision, and later conformance auditing.

## Development

This repository uses `uv`, `just`, Ruff, pytest, and GitHub Actions.

```bash
just bootstrap
just check
```

Useful development commands:

```bash
just lint
just test
just woof --help
```

`just woof ...` is a development convenience for running the checkout CLI. The installed operator command is `woof`.

## Source map

- `src/woof/cli/` — command implementations.
- `src/woof/graph/` — deterministic graph, transition contracts, typed record verbs, state-token guarded mutation, transaction manifest verification.
- `src/woof/checks/` — Stage-5 checker registry and runners.
- `src/woof/gate/` — gate authoring helpers.
- `src/woof/trackers/` — `Tracker` protocol and adapter implementations.
- `src/woof/bench/` — eval harness.
- `schemas/` — JSON Schema contracts.
- `playbooks/` — producer and reviewer prompt templates.
- `languages/` — per-language install, lint, test, and refresh-cartography registry files.
- `skills/` — Claude Code skill bundles (`woof-setup`, `woof-map-codebase`, `woof-run`, `woof-target-architecture`).
- `bin/woof` — development-only source checkout wrapper.

JSON Schema is the durable contract authority. Pydantic is used at schema and serialisation boundaries; dataclasses are used for trusted in-process records.

## License

MIT. See [`LICENSE`](LICENSE).
