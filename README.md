# Woof

Woof is an inner-loop SDLC tool for AI-assisted development. It governs the developer's own AI-assisted work cycle — discovery → definition → breakdown → execution → gate — with schema-governed contracts and a per-epic JSONL audit trail.

## Position

Woof addresses the **inner loop**: the structured, auditable cycle of an individual developer or small team's AI-assisted work. Where outer-loop / programme-level systems (e.g. [Chorus](https://github.com/krazyuniks/chorus)) govern enterprise adoption across teams and providers, Woof governs the work cycle at the keyboard.

## Status

Active. `guitar-tone-shootout` is Woof's first external consumer.

ADR-001 is implemented for the Stage-5 execution path: `woof wf --epic <N>` runs a deterministic Python graph whose nodes dispatch the executor, dispatch the critique, run verification, open gates, and commit through a transaction manifest. LLM prompts are producer nodes only; they no longer own successor selection, critique dispatch, gate writing, or commits.

Discovery, definition, and breakdown remain documented in [`docs/architecture.md`](docs/architecture.md). The old skill-driven Stage-5 topology is superseded by [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md).

## Read first

- [`docs/architecture.md`](docs/architecture.md) — principles, architecture, stages, gates, schemas.
- [`docs/research.md`](docs/research.md) — framework evaluation, E146 contract-fidelity case study, lessons.
- [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md) — accepted graph topology for execution.
- [`docs/backlog.md`](docs/backlog.md) — current implementation backlog and roadmap.
- [`examples/dogfood/`](examples/dogfood/) — selected artefacts from the first Woof dogfood epics.

## Development

```bash
just bootstrap
```

`just bootstrap` runs the first-time setup script, verifies host prerequisites, synchronises the locked `uv` environment, installs `prek` git hooks, and runs the local quality gate. After bootstrap, the regular inner loop is:

```bash
just check
just woof --help
```

`uv.lock` is committed. Git hooks are installed with `prek`; pre-commit runs Ruff and Woof config schema validation, and pre-push runs the unit suite.

## Operator usage

Run the deterministic Stage-5 graph from a consumer checkout that has `.woof/` state:

```bash
woof wf --epic <N>
```

Use `--once` to run a single graph node, `--format json` for machine-readable node output, and `--resolve <decision>` to close an open `gate.md` with a structured gate decision.

## Components

- `bin/woof` — source-checkout executable wrapper.
- `src/woof/cli/` — argparse-driven command implementations (`wf`, `validate`, `dispatch`, `render-epic`, `check-cd`, `check stage-5`, `gate write`).
- `src/woof/graph/` — ADR-001 deterministic graph, typed node contracts, transition table, and transaction manifest verification.
- `src/woof/checks/` — Stage-5 checker registry; runners; `Check` Pydantic model.
- `src/woof/gate/` — gate-authoring helpers (`woof gate write`).
- `src/woof/lib/` — shared Python helpers.
- `schemas/` — JSON schemas for runtime artefacts, graph node I/O, and transaction manifests.
- `playbooks/{discovery,critique}/` — prompt templates loaded into dispatched LLM contexts.
- `languages/{python,typescript,rust,go}.toml` — per-language install / lint / test registry.
- `.claude/commands/wf*.md` — thin wrappers / producer-node prompts; orchestration lives in Python.

## License

MIT. See [`LICENSE`](LICENSE).
