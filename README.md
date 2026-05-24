# Woof

Woof is a Python CLI for agentic multi-step software delivery. It owns
discovery, definition, breakdown, review, gate, execution, verification,
manifest-checked commit, and audit/resume through a deterministic graph.

Agents produce artefacts and critiques. Humans resolve explicit gates. Woof owns
state transitions, schemas, checks, transaction manifests, and commit decisions.

## Status

The technical finish path is complete for the current public workflow:

- `woof init --tracker local` or `woof init --tracker github` scaffolds a
  consumer repository.
- `woof wf new "<spark>"` creates a tracker-backed epic.
- `woof wf --epic <N>` drives the graph from discovery through story execution.
- Plan gates, story gates, reviewer blockers, check failures, empty diffs,
  tracker conflicts, and interrupted commit transactions have CLI-level
  acceptance coverage.
- The package build and installed-package workflow path are covered by smoke and
  acceptance tests.

Distribution is currently from tagged releases in this GitHub repository. CI
runs lint, tests, and package build on pushes to `main` and on pull requests.

## Install

Install the current release as a standalone tool:

```bash
uv tool install git+https://github.com/krazyuniks/woof@v0.1.1
```

Or install it into an existing Python environment:

```bash
pip install git+https://github.com/krazyuniks/woof@v0.1.1
```

Confirm the command is available:

```bash
woof --help
```

## Consumer Setup

Run Woof from the root of the repository you want it to manage.

For a filesystem-only setup with no hosted issue tracker:

```bash
woof init --tracker local
```

For a GitHub-backed setup where each Woof epic maps to one GitHub issue:

```bash
woof init --tracker github
```

Then replace every `<replace>` placeholder in `.woof/*.toml`, authenticate the
model CLIs, and run preflight:

```bash
claude /login
codex login
woof preflight
```

The full first-run guide is in [`docs/consumers.md`](docs/consumers.md).

## Operator Workflow

Create an epic from a one-line spark:

```bash
woof wf new "<spark>"
```

The command prints the assigned `E<N>` and the next graph command. Run or resume
the graph with:

```bash
woof wf --epic <N>
```

When the graph opens a gate, inspect the current state and resolve it with a
structured decision:

```bash
woof observe --epic <N> --view status
woof observe --epic <N> --view gate
woof wf --epic <N> --resolve approve
woof wf --epic <N>
```

Other read-only operator views:

```bash
woof observe --epic <N> --view timeline
woof observe --epic <N> --view audit
```

Inspect the resolved dispatch route and runtime policy without spawning an
agent:

```bash
woof dispatch --role primary --epic <N> --dry-run
```

## Runtime Model

Dispatched agents run in trusted-local mode. Woof does not sandbox them, restrict
commands, restrict writable paths, block network access, or add an MCP
restriction layer. This is reported by `woof preflight`, `woof dispatch
--dry-run`, dispatch audit events, and `woof observe`.

The safety boundary is before changes land: deterministic checks, reviewer
critique, human gates, transaction manifests, and graph-owned commit decisions.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) - current architecture,
  contracts, stages, gates, runtime boundary, tracker abstraction, and operator
  surfaces.
- [`docs/consumers.md`](docs/consumers.md) - first-run guide for a consumer
  repository using either `local` or `github` tracking.
- [`docs/implementation-plan.md`](docs/implementation-plan.md) - completion
  ledger and release-readiness validation evidence.
- [`CHANGELOG.md`](CHANGELOG.md) - public release notes.
- [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md)
  - deterministic graph topology.
- [`docs/adr/002-graph-led-role-routing.md`](docs/adr/002-graph-led-role-routing.md)
  - semantic primary/reviewer role routing.
- [`docs/adr/003-issue-tracker-abstraction.md`](docs/adr/003-issue-tracker-abstraction.md)
  - tracker protocol and local/GitHub adapters.
- [`examples/safety-model.md`](examples/safety-model.md) - concise examples of
  Woof's safety behaviours.

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
uv run pytest tests/integration/test_release_smoke.py
```

`just woof ...` is a development convenience for running the checkout CLI. The
installed operator command is `woof`.

## Source Map

- `src/woof/cli/` - argparse command implementations.
- `src/woof/graph/` - deterministic graph, transition table, node contracts,
  and transaction manifest verification.
- `src/woof/checks/` - Stage-5 checker registry and runners.
- `src/woof/gate/` - gate authoring helpers.
- `src/woof/trackers/` - `Tracker` protocol and adapter implementations.
- `schemas/` - JSON Schema contracts for runtime artefacts, graph node I/O, and
  transaction manifests.
- `playbooks/` - producer and reviewer prompt templates loaded into dispatched
  LLM contexts.
- `languages/` - per-language install, lint, and test registry files.
- `bin/woof` - development-only source checkout wrapper.

JSON Schema is the durable contract authority. Pydantic is used at schema and
serialisation boundaries; dataclasses are used for trusted in-process records.

## License

MIT. See [`LICENSE`](LICENSE).
