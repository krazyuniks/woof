# Conventions

## Commits

Conventional commit format: `type(scope): description`. Common types: `feat`, `fix`, `docs`, `refactor`, `test`. Scope matches the affected subsystem (e.g. `graph`, `cli`, `checks`, `preflight`, `cartography`). No AI attribution in commit messages or PR bodies.

## Python style

- Target Python 3.11+. Use `from __future__ import annotations` at module top for deferred annotation evaluation.
- Ruff enforces `E`, `F`, `I`, `UP`, `B`, `SIM`, `RUF`. Line length 100 (ruff does not hard-wrap at 100; `E501` is ignored). Import order managed by ruff `I`.
- `uv run ruff check .` and `uv run ruff format --check .` are the gate commands.
- Dataclasses with `frozen=True` for value objects (`NodeInput`, `NodeOutput`, `FileAction`, `RoleRoute`). Pydantic models for artefacts that require schema validation.
- Type annotations on all public function signatures. `Any` permitted in CLI argument handling where argparse types are not statically knowable.
- Module-level constants in `SCREAMING_SNAKE_CASE`. Single-character loop variables acceptable; descriptive names for anything crossing a function boundary.

## Schema discipline

Every `.woof/` artefact that crosses a subsystem boundary has a JSON Schema in `schemas/`. Schema filenames match the artefact: `plan.schema.json` validates `plan.json`. `woof validate` (ajv-cli) is the reference validator; Python `jsonschema` is used inline for gate authoring. Schema version bumps require a matching migration note in the relevant ADR.

## Shell scripts

- Shebang: `#!/usr/bin/env sh`. POSIX sh; no bash-isms unless a file explicitly needs bash features.
- `set -eu` at the top of managed script blocks. Variable quoting: `"$var"` everywhere.
- Managed blocks delimited by `# >>> <block-name>` / `# <<< <block-name>` so idempotent re-composition can locate and replace them.

## TOML config files

`[infra]`, `[commands]`, `[validators]`, `[tracker]`, `[cartography]`, `[lsp]` are the conventional section order in `prerequisites.toml`. Comments explain every section. No inline `<replace>` placeholders survive to production; they are scaffolding markers for `woof init` output.

## Error handling

- Raise `StageStateError` (from `src/woof/graph/transitions.py`) for operator-recoverable state mismatches. The runner catches these and opens a gate.
- `InitError`, `HookInstallError`, `PathspecEvaluationError` are module-local exception types; they propagate to CLI entry points that convert them to stderr messages and non-zero exit codes.
- CLI subcommands return integer exit codes; `sys.exit()` is called only at the main entrypoint.

## Paths

`tool_root()` and `schema_dir()` in `src/woof/paths.py` resolve wheel-aware paths so the schemas, playbooks, languages, and skills directories are found whether Woof runs from source (`src/`) or an installed wheel. Do not hard-code relative paths to these directories.

## Task runner

`just` recipes are development conveniences; they are not orchestration authority. All recipes must be single-line or use shell continuations (`\`). `just --list` is the discovery surface. Do not introduce parallel Make, npm, tox, or ad-hoc shell entry points alongside `just`.

## Gitignore

Runtime and per-worktree state is managed in a `# >>> woof` / `# <<< woof` block maintained by `woof init`. The mechanical cartography layer (`tags`, `files.txt`, `freshness.json`) is gitignored; the design and AS-IS markdown docs are committed state.
