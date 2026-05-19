# Woof

Woof is an inner-loop SDLC tool for AI-assisted development. It governs the developer's own AI-assisted work cycle - discovery, definition, breakdown, execution, and gate - with schema-governed contracts and a per-epic JSONL audit trail.

## Position

Woof addresses the inner loop: the structured, auditable cycle of an individual developer or small team's AI-assisted work. Where outer-loop / programme-level systems such as [Chorus](https://github.com/krazyuniks/chorus) govern enterprise adoption across teams and providers, Woof governs the work cycle at the keyboard.

## Status

Active. `guitar-tone-shootout` is Woof's first external consumer.

The current architecture is graph-led. `woof wf --epic <N>` runs the deterministic graph; LLM prompts are producer or reviewer nodes, not workflow orchestrators. ADR-002 defines the current role policy: GPT-5.5 is the preferred primary producer route, Claude Opus 4.7 at `max` effort is the preferred reviewer route, and reviewer blockers open human gates rather than model-to-model debate loops.

Current implementation status, remaining work, and the session continuation prompt live in [`docs/implementation-plan.md`](docs/implementation-plan.md).

## Entry Map

- [`docs/architecture.md`](docs/architecture.md) - principles, architecture, stages, gates, schemas.
- [`docs/research.md`](docs/research.md) - framework evaluation, E146 contract-fidelity case study, lessons.
- [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md) - accepted graph topology for execution.
- [`docs/adr/002-graph-led-role-routing.md`](docs/adr/002-graph-led-role-routing.md) - accepted primary/reviewer role policy and model-routing pivot.
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

## Operator Usage

Run the graph from a consumer checkout that has `.woof/` state:

```bash
woof wf --epic <N>
```

Start a new GitHub-backed epic by letting GitHub assign the issue number:

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

See [`docs/architecture.md`](docs/architecture.md) for stage and gate semantics and [`docs/consumers.md`](docs/consumers.md) for consumer checkout configuration.

## Consumer Checkouts

Consumer repositories keep project-specific declarations under `.woof/*.toml` and run the external `woof` command from the consumer root. Woof owns the graph, schemas, dispatch adapters, check runners, playbooks, and gate-writing logic; those assets stay in the Woof checkout or package.

`guitar-tone-shootout` is the first external consumer. Its Woof integration should define only consumer config such as `.woof/agents.toml`, `.woof/prerequisites.toml`, and `.woof/quality-gates.toml`; it must not vendor-copy Woof source, schemas, playbooks, tests, dogfood examples, or Ryan-local wrapper assumptions into the GTS repository. See [`docs/consumers.md`](docs/consumers.md).

## Source Map

- `bin/woof` - source-checkout executable wrapper.
- `src/woof/cli/` - argparse-driven command implementations.
- `src/woof/graph/` - deterministic graph, JSON Schema-backed Pydantic boundary models, transition table, and transaction manifest verification.
- `src/woof/checks/` - Stage-5 checker registry, runners, and internal check context/outcome records.
- `src/woof/gate/` - gate-authoring helpers.
- `src/woof/lib/` - shared Python helpers.
- `schemas/` - JSON schemas for runtime artefacts, graph node I/O, and transaction manifests.
- `playbooks/` - prompt templates loaded into dispatched LLM contexts.
- `languages/` - per-language install, lint, and test registry files.
- `.claude/commands/wf*.md` - thin wrappers and producer-node prompts; orchestration lives in Python.

Data-modelling rule: Pydantic is used at schema and serialisation boundaries; dataclasses are acceptable for trusted in-process records that do not define external artefact shape. The detailed rule lives in [`docs/architecture.md`](docs/architecture.md#contract-implementation-model).

## License

MIT. See [`LICENSE`](LICENSE).
