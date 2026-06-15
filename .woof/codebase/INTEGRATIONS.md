# Integrations

## Public CLI adapters

Woof dispatches subagents through two public CLI adapters, configured in `.woof/agents.toml`:

- **Claude (`claude` CLI, Anthropic).** The Stage-5 executor role. Launched by `woof dispatch --role execution.primary`. Has LSP access for type-aware code editing. Also the in-session runtime for the `/woof` operator skill. Model and effort set via `[model_profiles]` in `agents.toml`; default profile targets `claude-opus-4-7` at effort `max`.
- **Codex (OpenAI CLI).** The default primary role for planning, discovery, and breakdown stages, and the Stage-5 reviewer. Launched by `woof dispatch --role execution.reviewer` or `--role primary`. Default profile targets `gpt-5.5` at effort `xhigh`.

Both adapters are supervised in-process by `woof dispatch` via `src/woof/lib/supervise.py` with phase-scoped clocks. The dispatch adapter boundary is `src/woof/cli/dispatcher.py`.

## Issue tracker

Configured in `.woof/prerequisites.toml [tracker]`. Two adapters:

- **GitHub (`kind = "github"`, `src/woof/trackers/github.py`).** Creates and updates GitHub Issues to host epic contracts. Uses the `gh` CLI for all GitHub API calls. Requires `gh auth login` and `repo` set to `owner/name`.
- **Local (`kind = "local"`, `src/woof/trackers/local.py`).** Stores the epic contract body as a plain markdown file under `.woof/epics/E<N>/`. No external dependencies.

The tracker abstraction is `src/woof/trackers/base.py` (`Tracker` protocol). `woof wf new` creates the issue/local file; `woof render-epic --sync` pushes a rendered plan summary.

## Schema validation

- **ajv-cli + ajv-formats.** Node.js tools invoked as subprocesses by `woof validate`. Validates JSON, TOML (converted to JSON), JSONL, and front-matter artefacts against schemas in `schemas/`. Required at preflight time; checked by `woof preflight`.
- **Python `jsonschema`.** Used inline in the graph engine for gate authoring (`gate/write.py`) and node-output validation.

## Git

All graph state writes are mediated through Git:

- `git ls-files` populates `files.txt` in the mechanical cartography layer.
- `git rev-parse HEAD` is stamped into `freshness.json`.
- `git diff --staged` is the diff source for Stage-5 critique.
- `git commit` is the terminal action of the `commit` graph node, guarded by a transaction manifest.
- The `post-commit` hook (installed by `woof hooks install`) regenerates the mechanical cartography layer on every commit.

## LSP (pyright)

Configured in `.woof/prerequisites.toml [lsp]`. Pyright is the LSP binary for Python. It provides in-editor / Claude-Code-session type checking for the Stage-5 executor agent. Preflight verifies `pyright` is on PATH when `languages = ["python"]` is declared. Not invoked by `woof` Python code directly.

## Cartography

- **ctags.** Invoked by `scripts/refresh-cartography` to generate `.woof/codebase/tags` from `files.txt`. Language scoped to `Python` via the composed fragment in the refresh script.
- **`scripts/refresh-cartography`.** A consumer-owned shell script composed by `woof init --language python`. Called directly and via the post-commit hook.
