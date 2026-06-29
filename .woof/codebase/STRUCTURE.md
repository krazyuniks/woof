# Structure

## Repository root

```
woof/
├── src/woof/          # Python library and CLI entry point
├── schemas/           # JSON Schema contracts for all .woof/ artefacts
├── playbooks/         # Producer and reviewer prompt templates
├── languages/         # Per-language registry TOMLs and refresh fragments
├── skills/            # Claude Code skill bundles
├── tests/             # Unit and integration test suite
├── docs/              # Architecture, ADRs, backlog, plans, research
├── scripts/           # Developer scripts (first-time setup, brainstorm gen)
│   └── refresh-cartography  # Composed by `woof init`; regenerates mechanical layer
├── .woof/             # Woof consumer config for this repo
├── justfile           # Task runner recipes
└── pyproject.toml     # Project metadata, dependencies, tool config
```

## Source tree (`src/woof/`)

```
src/woof/
├── graph/
│   ├── nodes.py           # Node registry and all node handler implementations
│   ├── transitions.py     # next_node, gate/event writes, state-query helpers
│   ├── runner.py          # run_graph: the in-process graph loop
│   ├── state.py           # NodeInput, NodeOutput, NodeStatus, Plan, WorkUnitSpec typedefs
│   ├── pathspec.py        # Work-unit-scoped path filtering (filter_paths_matching)
│   ├── manifest.py        # Transaction manifest build and verification
│   ├── readiness.py       # Stage-2.5 contract readiness matrix
│   ├── planning_contracts.py  # plan.json and EPIC.md contract validators
│   ├── dispositions.py    # Critique front-matter parsing and disposition writes
│   ├── decisions.py       # Contract-decision reference helpers
│   ├── git.py             # Thin git subprocess wrappers
│   └── lock.py            # Per-epic workflow lock
├── cli/
│   ├── main.py            # argparse entrypoint; wires all subcommands
│   ├── preflight.py       # woof preflight: full prerequisite and artefact validator
│   ├── init.py            # woof init: .woof/ scaffold + refresh-cartography composer
│   ├── hooks.py           # woof hooks install: post-commit hook management
│   ├── dispatcher.py      # woof dispatch: adapter routing and subprocess supervision
│   └── commands/
│       ├── wf.py          # woof wf: graph runner, new, resolve, reset
│       ├── observe.py     # woof observe: read-only status/timeline/gate views
│       ├── check.py       # woof check stage-5: check matrix CLI
│       └── gate.py        # woof gate write: mechanical gate authoring
├── checks/
│   └── runners/           # Stage-5 check implementations (check_1 through check_9)
├── gate/
│   └── write.py           # Gate YAML+markdown authoring helpers
├── trackers/
│   ├── base.py            # Tracker protocol
│   ├── github.py          # GitHub issue adapter (gh CLI)
│   └── local.py           # Local file adapter
├── bench/
│   └── efficiency.py      # Eval harness for gate/dispatch summary
├── lib/
│   ├── audit.py           # Audit JSONL write helpers
│   ├── audit_config.py    # Audit config loading
│   ├── audit_bundle.py    # woof audit-bundle: transcript copy
│   └── supervise.py       # Subprocess supervision with phase-scoped clocks
├── paths.py               # tool_root(), schema_dir() — wheel-aware path resolution
└── decisions.py           # Contract-decision surface conformance
```

## Schemas (`schemas/`)

One JSON Schema file per artefact type. Key schemas: `prerequisites.schema.json`, `plan.schema.json`, `gate.schema.json`, `epic.schema.json`, `agents.schema.json`, `node-input.schema.json`, `node-output.schema.json`, `readiness-result.schema.json`, `freshness.schema.json`.

## Playbooks (`playbooks/`)

```
playbooks/
├── discovery/      # research.md, thinking.md, ideate.md, synthesis.md
├── planning/       # breakdown.md
├── execution/      # work-unit.md
└── critique/       # plan.md, work-unit.md
```

## Languages (`languages/`)

Per-language registry TOMLs (e.g. `python.toml`) declare `[lsp]` binaries and `[cartography].refresh_fragment`. Fragments live in `languages/refresh-cartography/<lang>.sh` and are composed into `scripts/refresh-cartography` by `woof init --language <lang>`.

## Skills (`skills/`)

```
skills/
├── woof/           # Umbrella operator skill: run, gate, reset, observe, onboard flows
└── woof-brainstorm/  # Interactive design specialist (generated; see scripts/gen_woof_brainstorm.py)
```

## Tests (`tests/`)

```
tests/
├── unit/           # Fast unit tests; no host tools required except host-only markers
├── integration/    # Acceptance and gate-recovery tests that need real git and CLI binaries
└── fixtures/       # Shared test artefacts (schema fixtures, plan stubs)
```

## Consumer layout (`.woof/` in this repo)

```
.woof/
├── prerequisites.toml    # Declared dependencies and cartography config
├── agents.toml           # Role routes and model profiles
├── quality-gates.toml    # Stage-5 Check 1 gate commands
├── test-markers.toml     # Stage-5 Check 2 test marker rules
└── codebase/             # Cartography artefacts (AS-IS docs committed; mechanical layer gitignored)
```
