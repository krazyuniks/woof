# Woof

Woof is a Python CLI for agentic multi-step software delivery. It runs a
deterministic workflow graph over a repository: discovery, definition,
breakdown, review, gate, execution, verification, manifest-checked commit, and
audit/resume.

## Product Goal

Woof is built to move software work from a spark to a checked commit while
keeping the workflow deterministic. Agents produce and review artefacts; Woof
owns state transitions, gates, checks, transaction manifests, and commits.

## Status

Pre-release. The active target is a complete, tested vertical workflow through
the CLI. The technical finish backlog lives in
[`docs/implementation-plan.md`](docs/implementation-plan.md).

The architecture is graph-led. `woof wf --epic <N>` runs the Python graph; LLM
prompts are producer or reviewer nodes, not workflow orchestrators. ADR-002
defines semantic role routing: `primary` produces, `reviewer` critiques, and
reviewer blockers open human gates.

Dispatched agents run in trusted-local mode. Woof does not sandbox them or
restrict commands, writable paths, network access, or MCP access at runtime.
`woof preflight` and `woof dispatch --dry-run` report this mode. The safety
boundary is before changes land: deterministic checks, reviewer critique, human
gates, transaction manifests, and graph-owned commit decisions.

## Entry Map

- [`docs/implementation-plan.md`](docs/implementation-plan.md) - live technical
  finish backlog and continuation prompt.
- [`docs/architecture.md`](docs/architecture.md) - design contract, stages,
  gates, schemas, and runtime boundaries.
- [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md)
  - graph-led orchestration decision.
- [`docs/adr/002-graph-led-role-routing.md`](docs/adr/002-graph-led-role-routing.md)
  - primary/reviewer route policy.
- [`docs/adr/003-issue-tracker-abstraction.md`](docs/adr/003-issue-tracker-abstraction.md)
  - tracker protocol and configuration boundary.
- [`docs/consumers.md`](docs/consumers.md) - consumer repository configuration
  boundary.
- [`examples/safety-model.md`](examples/safety-model.md) - concise examples of
  Woof's core safety behaviours.

## Development

```bash
just bootstrap
```

`just bootstrap` verifies host prerequisites, synchronises the locked `uv`
environment, installs git hooks, and runs the local quality gate. After
bootstrap, the regular inner loop is:

```bash
just check
just woof preflight
just woof --help
```

`uv.lock` is committed. Git hooks are installed with `just install-hooks`;
pre-commit runs Ruff and Woof config schema validation, pre-push runs the unit
suite, and `woof hooks install` adds the idempotent Woof-managed post-commit
cartography block.

## Operator Usage

Run Woof from this checkout:

```bash
just woof --help
```

Initialise a consumer repository's `.woof/` config:

```bash
/path/to/woof/bin/woof init --tracker local
```

Start a new epic:

```bash
/path/to/woof/bin/woof wf new "<spark>"
```

Run or resume the graph:

```bash
/path/to/woof/bin/woof wf --epic <N>
```

Approve an open plan gate:

```bash
/path/to/woof/bin/woof wf --epic <N> --resolve approve
```

Inspect workflow state without mutating the epic:

```bash
/path/to/woof/bin/woof observe --epic <N> --view status
/path/to/woof/bin/woof observe --epic <N> --view timeline
/path/to/woof/bin/woof observe --epic <N> --view gate
/path/to/woof/bin/woof observe --epic <N> --view audit
```

`observe --view status` reports the selected `.woof/.current-epic` marker,
current graph node, next operator command, gate cause, Stage-5 check summary,
resolved primary/reviewer routes, trusted-local runtime policy, and audit log
pointers. `--format json` exposes the same fields for automation.

Check startup infrastructure before invoking the graph:

```bash
/path/to/woof/bin/woof preflight
```

`preflight` prints prerequisite findings plus an operator-state section for the
current epic when `.woof/.current-epic` is set. The JSON output includes the same
`operator_state` object, including dispatch routes, runtime policy, next action,
gate cause, check summary, and audit pointers.

Inspect the resolved dispatch route and trusted-local runtime policy without
spawning an agent:

```bash
/path/to/woof/bin/woof dispatch --role primary --epic <N> --dry-run
```

## Consumer Repositories

Consumer repositories keep project-specific declarations under `.woof/*.toml`
and run the external `woof` command from the consumer root. Woof owns the graph,
schemas, dispatch adapters, check runners, playbooks, and gate-writing logic;
those assets stay in the Woof checkout or package.

Consumer-specific policy should be expressed as:

- quality-gate commands in `.woof/quality-gates.toml`;
- startup checks in `.woof/prerequisites.toml`;
- outcome marker rules in `.woof/test-markers.toml`;
- role routes in `.woof/agents.toml`.

Do not copy Woof source, schemas, playbooks, tests, generated epic state, or
private host assumptions into a consumer repository.

## Packaging Smoke

The packaging smoke tests prove graph subprocesses can re-enter through
`python -m woof` without the source-checkout `bin/woof` wrapper. They build a
wheel, install it into an isolated virtual environment, and verify bundled
assets such as `schemas/`, `playbooks/`, and `languages/`.

Run the smoke checks through the normal gate:

```bash
just check
```

Run the installed-package smoke directly:

```bash
uv run pytest tests/integration/test_release_smoke.py
```

## Source Map

- `bin/woof` - source-checkout convenience wrapper.
- `src/woof/__main__.py` - module entry point for `python -m woof`.
- `src/woof/cli/` - argparse-driven command implementations.
- `src/woof/graph/` - deterministic graph, transition table, node contracts,
  and transaction manifest verification.
- `src/woof/checks/` - Stage-5 checker registry and runners.
- `src/woof/gate/` - gate-authoring helpers.
- `src/woof/trackers/` - `Tracker` protocol and adapter implementations.
- `src/woof/lib/` - shared Python helpers.
- `schemas/` - JSON schemas for runtime artefacts, graph node I/O, and
  transaction manifests.
- `playbooks/` - prompt templates loaded into dispatched LLM contexts.
- `languages/` - per-language install, lint, and test registry files.

Data-modelling rule: JSON Schema is the durable contract authority. Pydantic is
used at schema and serialisation boundaries; dataclasses are acceptable for
trusted in-process records.

## License

MIT. See [`LICENSE`](LICENSE).
