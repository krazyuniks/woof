# Woof

Woof is an inner-loop SDLC tool for AI-assisted development. It governs the developer's own AI-assisted work cycle - discovery, definition, breakdown, execution, and gate - with schema-governed contracts and a per-epic JSONL audit trail.

## Position

Woof addresses the inner loop: the structured, auditable cycle of an individual developer or small team's AI-assisted work. Where outer-loop / programme-level systems such as [Chorus](https://github.com/krazyuniks/chorus) govern enterprise adoption across teams and providers, Woof governs the work cycle at the keyboard.

## Status

Pre-release, under active course correction. The urgent target is reliable use in Ryan's own projects. Portfolio exemplar work comes next, and OSS/distribution polish is deferred until the core loop is trustworthy.

The current architecture is graph-led. `woof wf --epic <N>` runs the deterministic graph; LLM prompts are producer or reviewer nodes, not workflow orchestrators. ADR-002 defines the current role policy: GPT-5.5 is the preferred primary producer route, Claude Opus 4.7 at `max` effort is the preferred reviewer route, and reviewer blockers open human gates rather than model-to-model debate loops.

The previous release-closure work delivered useful foundations: portable Stage 1 Discovery prompts, a tracker abstraction with `github` and `local` adapters, `woof init` scaffolding, and package smoke coverage. Those foundations stay. They do not mean arbitrary-consumer portability is complete: the deep audit found that Stage 5 still instructs the producer to invoke a Claude-only slash command. That gap is tracked in [`docs/course-correction-2026-05-21.md`](docs/course-correction-2026-05-21.md) and [`docs/implementation-plan.md`](docs/implementation-plan.md).

Current implementation status, remaining work, and the session continuation prompt live in [`docs/implementation-plan.md`](docs/implementation-plan.md).

## Entry Map

- [`docs/architecture.md`](docs/architecture.md) - principles, architecture, stages, gates, schemas.
- [`docs/research.md`](docs/research.md) - framework evaluation, E146 contract-fidelity case study, lessons.
- [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md) - accepted graph topology for execution.
- [`docs/adr/002-graph-led-role-routing.md`](docs/adr/002-graph-led-role-routing.md) - accepted primary/reviewer role policy and model-routing pivot.
- [`docs/adr/003-issue-tracker-abstraction.md`](docs/adr/003-issue-tracker-abstraction.md) - accepted issue-tracker abstraction and the `[tracker]` config boundary.
- [`docs/consumers.md`](docs/consumers.md) - external consumer checkout boundary, including GTS.
- [`docs/course-correction-2026-05-21.md`](docs/course-correction-2026-05-21.md) - current self-use-first correction, guardrail taxonomy, and active backlog.
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

## Packaging Smoke

Woof keeps packaging smoke coverage so future distribution work does not rot while the project focuses on self-use. Graph subprocesses re-enter through `python -m woof`, so the package boundary must work without the source-checkout `bin/woof` wrapper. The packaging smoke test (`tests/unit/test_packaging_install.py`) builds a wheel into a temporary directory, installs it into an isolated virtual environment, and verifies that:

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

This is evidence for the future distribution path. It is not the active product priority during the current course correction.

`tests/integration/test_release_smoke.py` extends this with a consumer-facing check: it builds and installs the wheel, runs `woof init --tracker local` in a throwaway consumer worktree, and confirms the Stage 1 Discovery producer nodes build fully self-contained dispatch prompts from the installed package - no Woof-author-local agent skills, wrappers, or host paths. It runs as part of `just check`; run it alone with:

```bash
uv run pytest tests/integration/test_release_smoke.py
```

## Operator Usage

During the current self-use phase, run Woof from this checkout:

```bash
just woof --help
```

When operating from another checkout on Ryan's current machine, set the source wrapper once:

```bash
export WOOF=/home/ryan/Work/woof/bin/woof
```

Initialise a fresh consumer checkout's `.woof/` config:

```bash
$WOOF init
```

Run the graph from a consumer checkout that has `.woof/` state:

```bash
$WOOF wf --epic <N>
```

Start a new tracker-backed epic; the configured tracker assigns the epic id:

```bash
$WOOF wf new "<spark>"
```

Resolve an open gate with a structured decision:

```bash
$WOOF wf --epic <N> --resolve approve
```

Inspect workflow state without mutating the epic:

```bash
$WOOF observe --epic <N> --view status
$WOOF observe --epic <N> --view timeline
$WOOF observe --epic <N> --view gate
$WOOF observe --epic <N> --view audit
```

Check startup infrastructure before invoking the graph:

```bash
$WOOF preflight
```

Distribution-oriented install instructions are deferred; [`docs/consumers.md`](docs/consumers.md) is retained as future external-consumer guidance. See [`docs/architecture.md`](docs/architecture.md) for stage and gate semantics.

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
