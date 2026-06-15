# Stack

## Runtime

- **Python 3.11+.** Required minimum; the type system and `tomllib` (stdlib TOML parser) are used throughout. Managed with `uv`.
- **pydantic >= 2.7.** Used for structured validation in graph state, node inputs/outputs, and readiness results.
- **pyyaml >= 6.0.** Used to parse YAML front-matter from epic, disposition, and plan markdown documents.

## Build and dev tooling

- **uv.** Dependency management and virtual-environment control. `uv.lock` is committed; `uv sync --locked` is the canonical setup step.
- **just.** Task runner (`justfile` at repo root). Canonical recipes: `setup`, `bootstrap`, `test`, `lint`, `check`. Not an orchestration authority — dev convenience only.
- **ruff >= 0.15.1.** Lint (`E`, `F`, `I`, `UP`, `B`, `SIM`, `RUF`) and format check. Line length 100, target Python 3.11.
- **pytest >= 8.3.** Unit and integration test runner. `pytest-ini` config in `pyproject.toml`; `--strict-markers`.
- **prek >= 0.3.11.** Pre-commit hook framework. Hooks installed via `just install-hooks`. Git hooks live in `.git/hooks/`; the Woof post-commit cartography hook is managed separately by `woof hooks install`.

## Schema and validation

- **ajv-cli + ajv-formats.** JSON Schema validation for `.woof/` artefacts (`prerequisites.toml`, `agents.toml`, `plan.json`, `gate.md`, etc.). Called by `woof validate` and `woof check stage-5`.
- **jsonschema.** Used internally for preflight and in-process schema validation within the Python engine.

## External CLI integrations

- **claude (Anthropic CLI).** Stage-5 executor role and in-session operator skill runtime. Requires `claude /login`.
- **codex (OpenAI CLI).** Default primary role for planning/discovery stages; Stage-5 reviewer. Requires `codex login`.
- **gh (GitHub CLI).** Used by the GitHub tracker adapter to create/update/close issues. Required when `[tracker].kind = "github"`.
- **git.** Canonical VCS; used by `git ls-files`, `git rev-parse`, staged-diff checks, manifest builds, and the graph runner's commit node.

## Cartography tooling

- **ctags.** Generates the `tags` mechanical-layer file; scoped to declared cartography languages. Woof degrades gracefully to an empty index if ctags is absent.
- **pyright.** LSP binary for the `[lsp]` Python configuration. Used in Stage-5 executor context for type-aware editing. Not required at cartography-generation time.

## Distribution

The wheel (`hatchling` build backend) bundles `schemas/`, `playbooks/`, `languages/`, and `skills/` alongside the `src/woof/` Python package via `force-include`. The tool is designed for `uv tool install` on developer workstations, not PyPI library consumption.
