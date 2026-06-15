# Testing

## Test layout

```
tests/
├── unit/           # Fast, isolated tests; no host tools by default
├── integration/    # Acceptance tests requiring git, ajv, and CLI binaries
└── fixtures/       # Shared TOML, JSON, and markdown test artefacts
```

`pytest` is the runner. `pyproject.toml` sets `pythonpath = ["src"]` and `testpaths = ["tests"]`. The `--strict-markers` flag means every marker must be declared before use.

## Markers

- `@pytest.mark.host_only` — marks tests that require host tools (`ajv`, `git`, `uv`, etc.). These tests are skipped in environments where those tools are absent. The marker is declared in `pyproject.toml [tool.pytest.ini_options].markers`.

## Unit tests (`tests/unit/`)

Scope: one module or one boundary behaviour per test file. Key files:

- `test_graph.py` — `next_node` routing, gate short-circuit, epic completion.
- `test_nodes.py` — node handler contracts, dispatch inputs/outputs, artefact loading.
- `test_preflight.py` — preflight finding generation for each check category.
- `test_hooks.py` — hook block composition and idempotency.
- `test_refresh_cartography.py` — refresh-cartography script composition and re-compose.
- `test_dispatch.py` — adapter routing, MCP config rendering, timeout configuration.
- `test_trackers.py` — GitHub and local tracker adapter behaviour.
- `test_validate.py` — `woof validate` schema validation against fixtures.
- `test_check_*.py` (9 files) — Stage-5 check matrix, one file per check.
- `test_audit*.py` — audit JSONL and audit-config behaviour.
- `test_supervise.py` — subprocess supervision lifecycle.

## Integration tests (`tests/integration/`)

Scope: end-to-end acceptance runs against a real git repository with real CLI binaries.

- `test_wf_acceptance.py` — full graph progression from spark to commit for a minimal epic.
- `test_wf_gate_recovery_acceptance.py` — gate-open and gate-resolve flows.
- `test_operator_state_surfaces.py` — `woof observe` and `woof wf` state surface consistency.
- `test_dispatch_supervision.py` — dispatch supervision lifecycle integration.
- `test_release_smoke.py` — smoke tests against the installed wheel.
- `test_wf_github_sync.py` — GitHub tracker sync.
- `test_wf_reset.py` — `woof wf reset` behaviour.

## Fixture strategy

Test fixtures live in `tests/fixtures/`. Artefacts are kept minimal: just enough front-matter and fields to satisfy schema validation. `conftest.py` in each test directory provides shared setup (temp repo init, CLI path resolution, environment variable stripping).

## Quality gate

`just check` runs `just lint && just test`. Lint first; a failing lint blocks tests. The `just check` target is the canonical local quality gate and is expected to pass before any commit.

## Stage-5 check conformance

`test_stage5_check_conformance_matrix.py` verifies that the check runner registry matches the declared check IDs in the schema, that each check runner returns a `check-result`-schema-valid dict, and that the check number sequence is contiguous.

## Outcome markers

Stage-5 Check 2 verifies that story tests carry `O<n>` outcome markers. Test files use the marker pattern declared in `.woof/test-markers.toml` (regex `(?<![A-Za-z0-9])O\d+(?![A-Za-z0-9])`). Tests in this repo carry `O<n>` markers where they cover outcomes declared in a Woof epic.
