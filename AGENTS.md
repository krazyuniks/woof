# Woof Agent Guide

## Project Shape

Woof is a Python CLI for deterministic inner-loop SDLC orchestration. It owns schemas, playbooks, graph execution, gate writing, and validation for `.woof/` consumer projects.

Use `README.md` for the public overview and `docs/architecture.md` / `docs/adr/001-orchestration-topology.md` for design authority.

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

## Code Boundaries

- CLI command wiring lives under `src/woof/cli/`.
- Deterministic graph behaviour lives under `src/woof/graph/`.
- Stage check definitions live under `src/woof/checks/`.
- Gate authoring lives under `src/woof/gate/`.
- JSON Schema contracts live under `schemas/`; keep tests and docs aligned when a contract changes.
- Playbook prompt content lives under `playbooks/`; avoid burying executable orchestration in prompts.

## Workflow Rules

- Read the relevant schema before changing artefact shape.
- Update docs in the same change as code when behaviour, commands, or contracts move.
- Prefer narrow changes that preserve the deterministic graph topology from ADR-001.
- Do not commit runtime Woof state: locks, current-epic markers, generated audit raw data, and codebase maps are intentionally gitignored.
- Use conventional commits, e.g. `feat(graph): add transaction guard`.

## Quality Bar

Before handing off code changes, run `just check` unless the task is docs-only or an external prerequisite is unavailable. If schema-facing behaviour changed, include targeted validation or tests that prove the contract.
