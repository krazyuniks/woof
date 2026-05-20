# Woof

Woof is an inner-loop SDLC tool for AI-assisted development. It governs the developer's own AI-assisted work cycle - discovery, definition, breakdown, execution, and gate - with schema-governed contracts and a per-epic JSONL audit trail.

## Position

Woof addresses the inner loop: the structured, auditable cycle of an individual developer or small team's AI-assisted work. Where outer-loop / programme-level systems such as [Chorus](https://github.com/krazyuniks/chorus) govern enterprise adoption across teams and providers, Woof governs the work cycle at the keyboard.

## Status

Pre-release. The internal architecture is implemented and dogfooded against `guitar-tone-shootout`. Phase A of the release-closure audit is complete; Phase B (portability for arbitrary consumers) is in progress.

The current architecture is graph-led. `woof wf --epic <N>` runs the deterministic graph; LLM prompts are producer or reviewer nodes, not workflow orchestrators. ADR-002 defines the current role policy: GPT-5.5 is the preferred primary producer route, Claude Opus 4.7 at `max` effort is the preferred reviewer route, and reviewer blockers open human gates rather than model-to-model debate loops.

Portability for arbitrary consumers (Phase B):
- Stage 1 Discovery (Phase B RC-B1) is portable. Graph producer nodes populate the `research/`, `thinking/`, and `brainstorm/` buckets before synthesis, and each bucket node embeds its building-block playbooks directly in the producer prompt. A consumer without Woof-author-local agent skills gets full Stage 1 output.
- The issue tracker is pluggable behind a `Tracker` protocol (Phase B RC-B2, [ADR-003](docs/adr/003-issue-tracker-abstraction.md)). `.woof/prerequisites.toml` declares `[tracker]` with `kind = "github"` or `kind = "local"`: the `github` adapter keeps each epic in a GitHub issue, the `local` adapter keeps every epic under `.woof/` with no remote so any repository can run Woof without a hosted tracker. A Linear, Jira, Plane, or Forgejo adapter is a new file implementing the protocol.
- `woof init` scaffolds the `.woof/` starter config (`prerequisites.toml`, `agents.toml`, `quality-gates.toml`, `test-markers.toml`) and patches the repo `.gitignore`; `woof init --tracker local` scaffolds a setup for a repository with no hosted issue tracker. The cartography script (`./scripts/refresh-cartography`) remains consumer-owned; the Woof post-commit hook block is a no-op when the script is absent. The end-to-end first-run walkthrough in [`docs/consumers.md`](docs/consumers.md) takes a new consumer from `pip install woof` to a running epic (Phase B RC-B3).

Current implementation status, remaining work, and the session continuation prompt live in [`docs/implementation-plan.md`](docs/implementation-plan.md).

## Entry Map

- [`docs/architecture.md`](docs/architecture.md) - principles, architecture, stages, gates, schemas.
- [`docs/research.md`](docs/research.md) - framework evaluation, E146 contract-fidelity case study, lessons.
- [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md) - accepted graph topology for execution.
- [`docs/adr/002-graph-led-role-routing.md`](docs/adr/002-graph-led-role-routing.md) - accepted primary/reviewer role policy and model-routing pivot.
- [`docs/adr/003-issue-tracker-abstraction.md`](docs/adr/003-issue-tracker-abstraction.md) - accepted issue-tracker abstraction and the `[tracker]` config boundary.
- [`docs/consumers.md`](docs/consumers.md) - external consumer checkout boundary, including GTS.
- [`docs/implementation-plan.md`](docs/implementation-plan.md) - implementation plan, roadmap, progress ledger, and continuation prompt.
- [`examples/safety-model.md`](examples/safety-model.md) - concise examples of Woof's core safety behaviours.
- [`examples/dogfood/`](examples/dogfood/) - selected artefacts from the first Woof dogfood epics.

## Development

```bash
just bootstrap
```

`just bootstrap` runs the first-time setup script, verifies host prerequisites, synchronises the locked `uv` environment, installs `prek` git hooks, and runs the local quality gate. After bootstrap, the regular inner loop is:

```bash
just check
just woof preflight
just woof --help
```

`uv.lock` is committed. Git hooks are installed with `just install-hooks`; pre-commit runs Ruff and Woof config schema validation, pre-push runs the unit suite, and `woof hooks install` adds the idempotent Woof-managed post-commit cartography block.

## Installed Package Smoke

Woof is also distributed as a wheel; graph subprocesses re-enter through `python -m woof`, so the installed package must work without the source-checkout `bin/woof` wrapper or `uv`. The packaging smoke test (`tests/unit/test_packaging_install.py`) builds a wheel into a temporary directory, installs it into an isolated virtual environment, and verifies that:

- `python -m woof --help` exits 0 from the installed interpreter.
- `tool_root()` resolves `schemas/`, `playbooks/`, and `languages/` from inside the installed package.
- `_woof_subprocess_argv()` / `_woof_subprocess_env()` produce `[sys.executable, "-m", "woof", ...]` plus a `PYTHONPATH` that loads the installed `woof` package.

The test runs as part of `just check` and `just test`. To reproduce manually:

```bash
uv build --wheel
uv venv /tmp/woof-smoke
/tmp/woof-smoke/bin/python -m pip install dist/woof-*.whl
/tmp/woof-smoke/bin/python -m woof --help
```

This is the release smoke path; consumer projects should `uv tool install woof` (or `pip install woof`) and invoke `woof` directly rather than calling `bin/woof` from the source checkout.

## Operator Usage

Install Woof as a standalone tool:

```bash
uv tool install woof
```

Initialise a fresh consumer checkout's `.woof/` config:

```bash
woof init
```

Run the graph from a consumer checkout that has `.woof/` state:

```bash
woof wf --epic <N>
```

Start a new tracker-backed epic; the configured tracker assigns the epic id:

```bash
woof wf new "<spark>"
```

Resolve an open gate with a structured decision:

```bash
woof wf --epic <N> --resolve approve
```

Check startup infrastructure before invoking the graph:

```bash
woof preflight
```

New consumers should follow the first-run walkthrough in [`docs/consumers.md`](docs/consumers.md); see [`docs/architecture.md`](docs/architecture.md) for stage and gate semantics.

## Consumer Checkouts

Consumer repositories keep project-specific declarations under `.woof/*.toml` and run the external `woof` command from the consumer root. Woof owns the graph, schemas, dispatch adapters, check runners, playbooks, and gate-writing logic; those assets stay in the Woof checkout or package.

`guitar-tone-shootout` is the first external consumer. Its Woof integration should define only consumer config such as `.woof/agents.toml`, `.woof/prerequisites.toml`, and `.woof/quality-gates.toml`; it must not vendor-copy Woof source, schemas, playbooks, tests, dogfood examples, or Ryan-local wrapper assumptions into the GTS repository. See [`docs/consumers.md`](docs/consumers.md).

## Source Map

- `bin/woof` - source-checkout convenience wrapper (`uv run --script`); not used by graph subprocesses.
- `src/woof/__main__.py` - module entry (`python -m woof`); the install-safe boundary for graph re-entry.
- `src/woof/cli/` - argparse-driven command implementations.
- `src/woof/graph/` - deterministic graph, JSON Schema-backed Pydantic boundary models, transition table, and transaction manifest verification.
- `src/woof/checks/` - Stage-5 checker registry, runners, and internal check context/outcome records.
- `src/woof/gate/` - gate-authoring helpers.
- `src/woof/trackers/` - issue-tracker abstraction: the `Tracker` protocol, GitHub and local adapters, the resolver factory, and the `EPIC.md`-to-issue-body renderer.
- `src/woof/lib/` - shared Python helpers.
- `schemas/` - JSON schemas for runtime artefacts, graph node I/O, and transaction manifests.
- `playbooks/` - prompt templates loaded into dispatched LLM contexts.
- `languages/` - per-language install, lint, and test registry files.
- `.claude/commands/wf*.md` - thin wrappers and producer-node prompts; orchestration lives in Python.

Data-modelling rule: Pydantic is used at schema and serialisation boundaries; dataclasses are acceptable for trusted in-process records that do not define external artefact shape. The detailed rule lives in [`docs/architecture.md`](docs/architecture.md#contract-implementation-model).

## License

MIT. See [`LICENSE`](LICENSE).
