# Woof

Woof is an inner-loop SDLC tool for AI-assisted software delivery. It owns discovery, definition, breakdown, review, gate, execution, verification, manifest-checked commit, and audit through a deterministic Python graph. The operator drives Woof from a Claude Code session.

Producer and reviewer subagents create artefacts and critiques; humans resolve explicit gates. Woof owns state transitions, schemas, checks, transaction manifests, and commit decisions.

## Install

Woof ships the Python engine and the Claude Code operator skills under `skills/` (`woof`, the umbrella, and `woof-brainstorm`, the design specialist).

```bash
uv tool install git+https://github.com/krazyuniks/woof@main
```

Confirm the engine is available:

```bash
woof --help
```

## Consumer setup

Run Woof against the repository you want it to manage. The `/woof` skill walks you through onboarding (see `skills/woof/references/setup.md`):

```
woof init
```

`woof init` writes the project's config to `~/.woof/config/projects/<project-key>.toml`, declaring the delivery profile, verification command, producer/reviewer run-profile slots, deterministic check floor, and cartography floor. It writes nothing into the driven repo. You then author the target architecture and design principles, install the cartography hook (`woof hooks install`), and start your first epic with `woof wf new "<spark>"` or ingest a pre-decomposed `work_units[]` source with `woof wf intake --source PATH`.

## Operator workflow

One umbrella skill is the operator's surface, plus one interactive design specialist. See `docs/adr/007-operator-skill-umbrella.md`.

| Skill | When to use |
|---|---|
| `/woof` | Drive the `woof` CLI: create and run epics, ingest pre-decomposed work units, resolve gates, reset, observe, and onboard a repo. The operator's command-map. |
| `/woof:brainstorm` | Lead the design conversation for an epic (the two brainstorm loops), then hand off to `woof wf`. |

The umbrella maps a request to the right `woof` shell command. Running an epic is `woof wf --epic N`: it reads the epic's on-disk state under `~/.woof/state/projects/<project-key>/` and runs the next graph node, dispatching producer and reviewer subagents for dispatch-shaped nodes, running deterministic nodes in-process, and surfacing gates for an operator decision (`woof wf --epic N --resolve <decision>`). The on-disk state is authoritative; the skill's in-session context is reconstructed from disk on a new session. Redo a design with `woof wf reset --epic N`.

The target graph checks contract readiness after definition and before planning. That gate runs early, but not immediately after epic creation: at creation time Woof only has a spark. Once `EPIC.md` exists, Woof can deterministically check whether acceptance criteria are machine-checkable, contract decisions are concrete, and referenced existing paths resolve before any model decomposes the work.

## Cartography

Every project carries a mandatory cartography artefact group at `~/.woof/state/projects/<project-key>/codebase/`:

- Human-authored design layer (`TARGET-ARCHITECTURE.md`, `PRINCIPLES.md`).
- Mapper-authored AS-IS layer (`CURRENT-ARCHITECTURE.md`, `STACK.md`, `INTEGRATIONS.md`, `STRUCTURE.md`, `CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md`).
- Mechanical layer (`tags`, `files.txt`, `freshness.json`) refreshed on every commit.

Woof loads the relevant subset per node, so producer and reviewer subagents do not pay tokens to rediscover the repo. See `docs/adr/004-cartography-prerequisite.md`.

## Runtime model

Dispatched agents run in trusted-local mode. Woof does not sandbox them, restrict commands, restrict writable paths, block network access, or add an MCP restriction layer. The safety boundary is before changes land: deterministic checks, reviewer critique, human gates, transaction manifests, and graph-owned commit decisions.

The runtime model is reported by `woof preflight` and by the skill at epic start.

Woof is allowed to be opinionated about expert-local tooling. tmux may be used for long-running supervision, logs, and dashboards when it improves operability. It is not a workflow authority; graph state, typed commands, gates, and commits remain owned by the operator home and the Python engine.

Quality gates support two postures in the target architecture. Strict mode blocks on any failure. Baseline mode starts as a command-level brownfield posture: pre-existing red commands can be recorded and reported without blocking, while fine-grained per-failure subtraction requires a structured parser or machine-readable gate output.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — system architecture: layers, stages, cartography, role routing, schemas, gates, transaction manifests, prerequisites.
- [`docs/backlog.md`](docs/backlog.md) — open work and the wave operating order, prescriptive.
- [`docs/adr/`](docs/adr/) — decision records. ADR-010 through ADR-017 define the merged engine (topology, work-units contract, interactive harness dispatch, policy-driven rigour and cartography, VaultForeman absorption and retirement, Profile A worktree contract, sibling-conflict fail-closed, operator-home config and state); earlier ADRs carry their supersession status in front matter.

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
- `skills/` — Claude Code skill bundles: `woof` (the umbrella operator surface) and `woof-brainstorm` (the generated design specialist).
- `bin/woof` — development-only source checkout wrapper.

JSON Schema is the durable contract authority. Pydantic is used at schema and serialisation boundaries; dataclasses are used for trusted in-process records.

## License

MIT. See [`LICENSE`](LICENSE).
