# Woof Technical Finish And Release Readiness

> **Purpose:** Completion ledger and release-readiness evidence for the current public Woof workflow.
> **Authority:** Architecture is governed by `docs/architecture.md`; graph topology by `docs/adr/001-orchestration-topology.md`; role routing by `docs/adr/002-graph-led-role-routing.md`; tracker boundaries by `docs/adr/003-issue-tracker-abstraction.md`.

## Product Goal

Woof delivers software through an agentic multi-step process:

1. capture an epic spark;
2. run graph-owned discovery, definition, breakdown, review, and gate steps;
3. dispatch producer and reviewer agents through declared roles;
4. verify generated work with deterministic checks;
5. commit only through manifest-verified graph transactions;
6. leave an auditable epic trail that can be inspected and resumed.

Woof is portable across consumer repositories. Consumer policy is declared under
`.woof/`; Woof owns graph transitions, schemas, dispatch adapters, checks, gate
writing, and transaction manifests.

## Release-Readiness Status

The Technical Finish items are complete. TF-001 through TF-007 are implemented,
covered by tests, and validated through `just check`.

REL-001 covers public repository readiness: GitHub-facing documentation,
architecture alignment, consumer setup guidance, ADR cleanup, package metadata,
and validation evidence. This file records the release-readiness validation
performed for REL-001.

REL-002 covers release cut and distribution readiness: tagged GitHub-sourced
install paths, package artefact contents, installed entry points, release notes,
and tag-driven GitHub release automation.

## Definition Of Done

The current public workflow is complete when these are true:

- `woof init --tracker local` creates a complete consumer configuration.
- `woof init --tracker github` creates a GitHub-backed consumer configuration.
- `woof wf new "<spark>"` creates a tracker-backed epic and prints the next graph command.
- `woof wf --epic <N>` drives Stage 1 through Stage 4 to the mandatory plan gate.
- `woof wf --epic <N> --resolve approve` resumes into Stage 5.
- Stage 5 dispatches a producer, dispatches a reviewer, records a disposition,
  runs deterministic checks, verifies the transaction manifest, and commits the
  story.
- Gates open predictably for malformed state, subprocess crashes, reviewer
  blockers, check failures, empty diffs, tracker conflicts, and manifest
  mismatches.
- `woof observe`, `woof preflight`, and JSONL audit streams expose enough state
  for an operator to resume without reading source code.
- The full path is covered by CLI-level acceptance tests using throwaway
  consumer repositories and public CLI-shaped test doubles.
- Installation/runtime boundaries are covered by smoke tests for both
  development checkout and installed package paths.

## Technical Finish Ledger

| ID | Status | Work item | Observable outcome | Validation |
|---|---|---|---|---|
| TF-001 | Completed | End-to-end CLI workflow acceptance | A CLI-level integration test creates a throwaway consumer repository, runs `woof init --tracker local`, starts an epic, drives Stage 1-5 with public CLI-shaped `codex` and `claude` stubs, approves the plan gate, verifies the story commit, and asserts audit events. The implementation also stages graph-owned durable `.woof` files before commit-readiness checks, includes durable planning artefacts in story manifests, and ignores transient Stage-5 result files in `woof init` scaffolds. | Passed: `uv run pytest tests/integration/test_wf_acceptance.py -q`; `uv run pytest tests/unit/test_init.py tests/unit/test_graph.py tests/unit/test_check_3_scope.py tests/unit/test_check_7_commit_transaction.py tests/integration/test_wf_acceptance.py -q`; `just check` (339 tests) |
| TF-002 | Completed | Gate and recovery acceptance | CLI tests cover subprocess crash gates, reviewer blocker gates, failed check gates, empty-diff gates, malformed-state gates, and interrupted commit resume. The final story transaction records `epic_completed` before the manifest-checked commit when that story completes the plan, so interrupted commit resume does not leave the durable audit log dirty after commit. | Passed: `uv run pytest tests/integration/test_wf_gate_recovery_acceptance.py -q`; `uv run pytest tests/integration/test_wf_acceptance.py tests/integration/test_wf_gate_recovery_acceptance.py tests/unit/test_graph.py -q`; `just check` (345 tests) |
| TF-003 | Completed | Operator state surfaces | `woof observe` and `woof preflight` expose current epic state, next action, gate cause, dispatch route, runtime policy, audit pointers, and check summaries without requiring source inspection. The status/audit views report `.woof/.current-epic`, next operator command, gate cause, Stage-5 check summaries, resolved primary/reviewer routes, trusted-local runtime policy, and audit log pointers; `preflight` includes the same current-epic operator-state summary in text and JSON output. | Passed: `uv run pytest tests/integration/test_operator_state_surfaces.py tests/unit/test_observe.py tests/unit/test_preflight.py -q`; `just lint`; `just check` (349 tests) |
| TF-004 | Completed | Stage-5 check conformance matrix | Each Stage-5 check has explicit success and failure conformance fixtures that call the real registry runner and prove its contract: quality gates, outcome markers, scope, contract refs, plan crossrefs, critique blockers, transaction manifests, docs drift, and review valve behaviour. The matrix also asserts every Stage-5 check has both fixture kinds. | Passed: `uv run pytest tests/unit/test_stage5_check_conformance_matrix.py -q`; `uv run pytest tests/unit/test_check_1_quality_gates.py tests/unit/test_check_2_outcome_markers.py tests/unit/test_check_3_scope.py tests/unit/test_check_4_contract_refs.py tests/unit/test_check_5_plan_crossrefs.py tests/unit/test_check_6_critique_blocker.py tests/unit/test_check_7_commit_transaction.py tests/unit/test_check_8_docs_drift.py tests/unit/test_check_9_review_valve.py tests/unit/test_check_stage_5_subcommand.py tests/unit/test_stage5_check_conformance_matrix.py -q`; `just lint`; `just check` (368 tests) |
| TF-005 | Completed | Tracker contract matrix | The `local` and `github` adapters share a parametrised `Tracker` protocol contract matrix for create, fetch, authority checks, sync-conflict resolution decisions, plan summary push, and epic completion. The matrix uses a deterministic `gh` command stub for the GitHub adapter. The `local` adapter remains no-remote and no-`.last-sync`, but its lifecycle methods load local `EPIC.md`/`plan.json`, render the shared managed body shape, and reject epic completion until all stories are `done`. | Passed: `uv run pytest tests/unit/test_trackers.py -q`; `uv run pytest tests/unit/test_trackers.py tests/unit/test_render_epic.py tests/unit/test_wf_github_sync.py -q`; `uv run pytest tests/integration/test_operator_state_surfaces.py -q`; `just lint`; `just check` (388 tests) |
| TF-006 | Completed | Installed-package workflow acceptance | The installed package path runs the same local-tracker workflow acceptance as the development checkout: build wheel, install into an isolated virtual environment, scaffold a consumer with `woof init --tracker local`, drive Stage 1-5 through `python -m woof`, approve the plan gate, verify the story commit, and assert audit events without checkout wrappers or private host state. | Passed: `uv run pytest tests/integration/test_wf_acceptance.py::test_installed_package_wf_cli_drives_local_tracker_epic_to_story_commit -q`; `uv run pytest tests/integration/test_wf_acceptance.py -q`; `just lint`; `just check` (389 tests) |
| TF-007 | Completed | Final operator documentation | README, architecture, schemas, help text, and consumer docs describe the implemented operator workflow with tracker-neutral commands, installed-CLI consumer usage, trusted-local runtime disclosure, schema-aligned Stage-5 reference checks, and explicit next operator commands from init/new epic through graph resume. `woof wf new` reports the next `woof wf --epic <N>` command in text and JSON output. | Passed: `uv run pytest tests/unit/test_operator_help_docs.py tests/unit/test_init.py::test_init_outputs_next_steps tests/unit/test_trackers.py::test_woof_wf_new_local_tracker_never_calls_gh tests/unit/test_validate.py::test_shipped_schema_compiles -q`; `just lint`; `just check` (392 tests) |

## REL-001 Validation Evidence

REL-001 documentation readiness validation, 2026-05-24:

- Public documentation audit covered `README.md`, `docs/**/*.md`,
  `examples/**/*.md`, playbook READMEs, schema descriptions, CLI help text,
  package metadata, and GitHub Actions workflow.
- Stale roadmap, session-prompt, checkout-only, private-wrapper,
  host-dependent, provider-locked, and historical work-log language was removed
  from the main reader path or marked archival.
- Current public docs scanned clean for the release-readiness stale-language
  patterns used during the audit.
- Current public docs scanned clean for non-ASCII typography in the files edited
  for REL-001.
- Passed: `git diff --check`.
- Passed: `uv run pytest tests/unit/test_operator_help_docs.py -q` (3 tests).
- Passed: `just lint`.
- Passed: `just check` (392 tests).
- Remaining release blockers: none known.

## REL-002 Validation Evidence

REL-002 release-cut validation, 2026-05-24:

- Repository state before edits: `main` tracking `origin/main`, clean worktree,
  no existing local tags, no remote tags, and no GitHub releases.
- Version decision: kept `version = "0.1.0"` because this is the first public
  release, and `pyproject.toml` and `uv.lock` already agree on `0.1.0`.
- Install path: README and consumer guide now use the tagged GitHub source
  install path `git+https://github.com/krazyuniks/woof@v0.1.0` for both `uv
  tool install` and `pip install`.
- Release record: `CHANGELOG.md` records the public `0.1.0` release.
- Release automation: `.github/workflows/release.yml` builds the tagged package,
  verifies both `woof --help` and `python -m woof --help` from the built wheel,
  and publishes a GitHub release using only the repository `GITHUB_TOKEN`.
- Package artefacts: `uv build` built `dist/woof-0.1.0.tar.gz` and
  `dist/woof-0.1.0-py3-none-any.whl`.
- Bundled assets: the wheel contains `schemas/`, `playbooks/`, and
  `languages/`, excludes the source-checkout `bin/woof` wrapper, and contains
  no `__pycache__` or `.pyc` files. The sdist contains the runtime assets and
  keeps `bin/woof` as source checkout tooling, with no `__pycache__` or `.pyc`
  files.
- Passed: `uv build`.
- Passed: `uv run pytest tests/unit/test_packaging_install.py -q` (5 tests).
- Passed: `uv run pytest tests/integration/test_release_smoke.py -q` (1 test).
- Passed: `just lint`.
- Passed: `just check` (393 tests).
- Remaining release blockers: none known locally. Tag creation and GitHub
  release publication are intentionally after the commit CI for this change is
  green.
