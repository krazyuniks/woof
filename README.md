# Woof

Woof is an inner-loop SDLC tool for AI-assisted development. It governs the developer's own AI-assisted work cycle — discovery → definition → breakdown → execution → gate — with schema-governed contracts and a per-epic JSONL audit trail.

## Position

Woof addresses the **inner loop**: the structured, auditable cycle of an individual developer or small team's AI-assisted work. Where outer-loop / programme-level systems (e.g. [Chorus](https://github.com/krazyuniks/chorus)) govern enterprise adoption across teams and providers, Woof governs the work cycle at the keyboard.

## Status

Active. `guitar-tone-shootout` is Woof's first external consumer.

The implementation is mid-flight. Discovery, definition, breakdown, and the start of Stage-5 execution are working; the deterministic checker registry, structured executor protocol, and per-story driver landed during dogfood epic E182 (commits `7bf2a12` and `b729860` in this repo's history). The first dogfood surfaced an architectural finding — see [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md) — that drives the next implementation cycle: shifting orchestration from the LLM to a deterministic Python graph with LLM and human review as typed nodes within it.

## Read first

- [`docs/architecture.md`](docs/architecture.md) — principles, architecture, stages, gates, schemas (current design — supersession in flight per ADR-001).
- [`docs/research.md`](docs/research.md) — framework evaluation, E146 contract-fidelity case study, lessons.
- [`docs/adr/001-orchestration-topology.md`](docs/adr/001-orchestration-topology.md) — the next architectural direction.

## Components

- `bin/woof` — PEP-723 Python CLI (single entry point for `validate`, `dispatch`, `render-epic`, `check-cd`, `check stage-5`, `gate write`).
- `schemas/` — 11 JSON schemas (epic, plan, gate, critique, jsonl-events, prerequisites, agents, test-markers, language-registry, quality-gates, docs-paths) plus the Stage-5 additions (check-result, executor-result).
- `cli/` — argparse-driven subcommand dispatcher and command implementations.
- `checks/` — Stage-5 checker registry; runners; `Check` Pydantic model.
- `gate/` — gate-authoring helpers (`woof gate write`).
- `lib/` — shared Python helpers.
- `playbooks/{discovery,critique}/` — prompt templates loaded into dispatched LLM contexts.
- `languages/{python,typescript,rust,go}.toml` — per-language install / lint / test registry.
- `.claude/commands/wf*.md` — orchestrator skills (current shape; superseded by ADR-001 once implemented).

## License

MIT.
