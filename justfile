set dotenv-load := false

default:
    @just --list

# Install/synchronise the development environment.
setup:
    uv sync --locked

# First-time host bootstrap: prerequisites, hooks, and quality gate.
bootstrap *ARGS:
    ./scripts/first-time-setup.sh {{ARGS}}

# Run the unit suite.
test:
    uv run pytest

# Run lint and formatting checks.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Apply mechanical Python formatting/lint fixes.
format:
    uv run ruff check . --fix
    uv run ruff format .

# Full local quality gate.
check: lint test

# Install git hooks via prek.
install-hooks:
    uv run prek install
    uv run prek install --hook-type pre-push
    uv run woof hooks install --project-root .

# Run the Woof CLI from this checkout.
woof *ARGS:
    ./bin/woof {{ARGS}}

# Run the small-valid-epic efficiency benchmark harness.
efficiency-bench *ARGS:
    uv run python -m woof.bench.efficiency {{ARGS}}

# Bundle Claude Code transcripts referenced by an epic dispatch log.
wf-audit-bundle EPIC:
    ./bin/woof audit-bundle {{EPIC}}

# Re-vendor the brainstorm skill playbook from agent-toolkit (one-way copy).
vendor-brainstorm *ARGS:
    uv run python scripts/vendor_brainstorm.py {{ARGS}}

# Verify the vendored brainstorm playbook matches its recorded pin (no source needed).
vendor-brainstorm-check:
    uv run python scripts/vendor_brainstorm.py --check
