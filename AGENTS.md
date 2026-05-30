# Woof Agent Guide

## Project shape

Woof is an inner-loop SDLC tool for AI-assisted development. It has four layers: state on disk (`.woof/`), a Python graph library (`src/woof/`), a Claude Code skill suite (`skills/`) as the operator orchestrator, and dispatched producer/reviewer/mapper subagents.

Authority for design decisions: ADRs under `docs/adr/`. Authority for system architecture: `docs/architecture.md`. Authority for open work: `docs/backlog.md`. Authority for execution sequencing: `docs/implementation-plan.md`.

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

- `src/woof/graph/` — deterministic graph transitions, typed graph commands, validation, JSONL audit.
- `src/woof/cli/` — CLI command surface (`woof init`, `woof preflight`, `woof hooks install`, `woof graph`, `woof validate`, etc.).
- `src/woof/checks/` — Stage-5 check runners.
- `src/woof/gate/` — gate authoring helpers.
- `src/woof/trackers/` — `Tracker` protocol and adapters.
- `src/woof/bench/` — eval harness.
- `schemas/` — JSON Schema contracts.
- `playbooks/` — producer and reviewer prompt templates.
- `languages/` — per-language registry: install instructions, LSP binaries, tree-sitter grammars, refresh-cartography templates.
- `skills/` — Claude Code skill bundles: `woof-setup`, `woof-map-codebase`, `woof-run`, `woof-target-architecture`.

## Workflow rules

- Read the relevant schema before changing artefact shape.
- Update docs in the same change as code when behaviour, commands, or contracts move.
- Preserve the layered topology (ADR-001). The skill is the orchestrator; Python is the engine library; state is on disk.
- Preserve the role-routing policy (ADR-002). Stage 5 producer is Claude (LSP); reviewer is Codex. Other stages are the reverse.
- Preserve the cartography prerequisite (ADR-004). Do not introduce nodes that bypass `.woof/codebase/` content loading.
- Do not introduce a parallel operator surface for running epics. The only operator entry point for running an epic is `/woof:run`.
- Do not add generic graph mutation commands. Skill-facing state changes use typed `woof graph` verbs and `state_token` guards.
- Do not commit runtime state: locks, current-epic markers, generated audit raw data, and the mechanical cartography layer are gitignored.
- Use conventional commits, e.g. `feat(graph): add transaction guard`.

## Quality bar

Before handing off code changes, run `just check` unless the task is docs-only or an external prerequisite is unavailable. If schema-facing behaviour changed, include targeted validation or tests that prove the contract.
