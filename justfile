set dotenv-load := false

default:
    @just --list

# Install/synchronise the development environment.
setup:
    uv sync

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
    prek install

# Run the Woof CLI from this checkout.
woof *ARGS:
    ./bin/woof {{ARGS}}

