# Woof Agent Guide

## Project shape

Woof is the orchestration engine for AI-assisted software delivery. It has five layers: state on disk (`.woof/`), a Python graph library (`src/woof/`), the shared interactive harness transport boundary, a Claude Code operator skill layer (`skills/`: the `woof` umbrella over the `woof` CLI plus the `woof-brainstorm` design specialist), and dispatched producer/reviewer/mapper workers.

Authority for design decisions: ADRs under `docs/adr/`. Authority for system architecture: `docs/architecture.md`. Authority for glossary terms: `docs/CONTEXT.md`. Authority for open work: `docs/backlog.md`.

## Commands

Use `just` for repo commands. Discover with `just --list`.

- `just bootstrap` — first-time setup: host prerequisites, `uv sync`, git hooks, and quality gate.
- `just setup` — synchronise the locked `uv` environment.
- `just lint` — Ruff lint and format check.
- `just test` — unit test suite.
- `just check` — local quality gate.
- `just woof --help` — run the checkout CLI wrapper.

Do not introduce parallel Make, npm, tox, or ad-hoc shell entry points while a `just` recipe can express the workflow.

## Tooling

- Python uses `uv`; keep `uv.lock` committed when dependencies change.
- Git hooks use `prek` and are installed with `just install-hooks`.
- Schema validation uses `ajv-cli` plus `ajv-formats`; keep `.woof/*.toml`, `languages/*.toml`, and schema fixtures valid with `woof validate`.
- Shell scripts are zsh unless a file has a stronger local reason to use another shell.

## Code boundaries

- `src/woof/graph/` — deterministic graph transitions, the runner, validation, gates, profile publish/merge decisions, and JSONL audit.
- `src/woof/cli/` — CLI command surface (`woof wf`, `woof init`, `woof preflight`, `woof hooks install`, `woof observe`, `woof validate`, etc.).
- `src/woof/checks/` — deterministic check runners.
- `src/woof/gate/` — gate authoring helpers.
- `src/woof/trackers/` — `Tracker` protocol and adapters.
- `src/woof/bench/` — eval harness.
- `schemas/` — JSON Schema contracts, including the canonical `work_units[]` execution shape.
- `playbooks/` — producer and reviewer prompt templates.
- `languages/` — per-language registry: install instructions, LSP binaries, tree-sitter grammars, refresh-cartography templates.
- `skills/` — Claude Code skill bundles: `woof` (the umbrella operator surface) and `woof-brainstorm` (the generated design specialist; regenerate with `just gen-brainstorm`).

## Workflow rules

- Read the relevant schema before changing artefact shape.
- Update docs in the same change as code when behaviour, commands, or contracts move.
- Preserve the merged topology (ADR-010). Intake varies; execution runs one engine path over `work_units[]`.
- Preserve `work_units[]` as the executable contract (ADR-011). Do not reintroduce story/work-unit mirrors. Work-unit ids are local to the aggregate; cross-aggregate references are structured (aggregate context plus local id), never an encoded string, and UUIDs are reserved for technical run/attempt/review/audit records.
- Preserve the interactive harness transport boundary (ADR-012). Profiles select tmux or herder explicitly; do not add headless `claude -p`, `codex exec`, or equivalent one-shot reasoning paths.
- Preserve policy-driven rigour and cartography (ADR-013). Cartography remains first-class, with the required floor declared by repo policy.
- Single source of truth. Every concept has one authoritative home and one bounded scope. Routing and run profiles live only in `.woof/policy.toml`; the executable unit has one schema; the dispatch registry owns harness/model/effort vocabulary. Never declare a concept in two files, and never ship a back-compat alias without its deletion in the same change.
- Do not introduce a parallel operator surface for running epics. The operator entry point is the `/woof` umbrella, which runs `woof wf` (ADR-007).
- Do not add a parallel state-mutation path. Skill-facing state changes go through typed `woof wf` verbs (`new`, `--resolve`, `reset`); never hand-edit `.woof/` state.
- Do not commit runtime state: locks, current-epic markers, generated audit raw data, and the mechanical cartography layer are gitignored.
- Use conventional commits, e.g. `feat(graph): add transaction guard`.

## Quality bar

Before handing off code changes, run `just check` unless the task is docs-only or an external prerequisite is unavailable. If schema-facing behaviour changed, include targeted validation or tests that prove the contract.
