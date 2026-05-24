# Woof Implementation Plan

> **Purpose:** Prescriptive technical roadmap for finishing Woof.
> **Authority:** Architecture remains governed by `docs/architecture.md`; graph topology
> remains governed by `docs/adr/001-orchestration-topology.md`; role routing remains
> governed by `docs/adr/002-graph-led-role-routing.md`; tracker boundaries remain
> governed by `docs/adr/003-issue-tracker-abstraction.md`.
> **Operating rule:** This is the single live backlog. Select the first `In progress`
> or `Ready` Technical Finish item by order unless the user explicitly redirects.

## Product Goal

Woof delivers software through an agentic multi-step process:

1. capture an epic spark;
2. run graph-owned discovery, definition, breakdown, review, and gate steps;
3. dispatch producer and reviewer agents through declared roles;
4. verify generated work with deterministic checks;
5. commit only through manifest-verified graph transactions;
6. leave an auditable epic trail that can be inspected and resumed.

The product is not a wrapper around a specific consumer repository. It is a
portable orchestration tool that runs against any repository with `.woof/`
configuration.

## Strategy

Build the smallest complete vertical product first, then harden its failure
paths. The roadmap is ordered around executable software delivery, not around
documentation provenance or distribution polish.

Architecture invariants:

- Woof is graph-led. The deterministic Python graph owns transitions, state,
  gates, verification, and commits.
- LLMs are producers or reviewers. They do not orchestrate the workflow.
- The primary producer route is `primary`; the reviewer route is `reviewer`.
- Reviewer blockers open human gates. There is no model-to-model debate loop.
- Issue tracking stays behind the `Tracker` protocol.
- Runtime agent execution is trusted-local: Woof does not add sandboxing,
  command allow-lists, writable-path restrictions, network policy, or MCP
  restriction logic. Safety is enforced before changes land through checks,
  reviewer critique, gates, transaction manifests, and commit decisions.
- Woof must not depend on private shell wrappers, external sync tools, dotfiles,
  or host-specific absolute paths.

## Operating Loop

Every implementation turn follows this loop:

1. Read `AGENTS.md`, `README.md`, this file, and any architecture, schema, or
   source file directly touched by the selected item.
2. Run `git status --short --branch` before editing and preserve unrelated local
   changes.
3. Select the first `In progress` or `Ready` Technical Finish item by order.
4. Implement a broad, coherent slice that advances the selected item end to end.
5. Update code, schemas, tests, and docs together when behaviour changes.
6. Run focused validation while developing.
7. Run `just check` before handoff unless a real external prerequisite blocks it.
8. Update this file with status and validation evidence.
9. Commit using a conventional commit message.
10. Push and monitor CI to a terminal state when the session is operating in the
    normal repository workflow.

Stop only for a real blocker, a risky architecture change that needs a decision,
or completion of the selected item.

## Definition Of Done

Woof is technically finished enough for regular use when these are true:

- `woof init --tracker local` creates a complete consumer configuration.
- `woof wf new "<spark>"` creates a local epic.
- `woof wf --epic <N>` can drive Stage 1 through Stage 4 to a plan gate.
- `woof wf --epic <N> --resolve approve` resumes into Stage 5.
- Stage 5 can dispatch a producer, dispatch a reviewer, record a disposition,
  run checks, verify the transaction manifest, and commit the story.
- Gates open predictably for malformed state, subprocess crashes, reviewer
  blockers, check failures, empty diffs, tracker conflicts, and manifest
  mismatches.
- `woof observe`, `woof preflight`, and JSONL audit streams expose enough state
  for an operator to resume without reading source code.
- The full path is covered by CLI-level acceptance tests using throwaway
  consumer repositories and public CLI-shaped test doubles.
- Installation/runtime boundaries are covered by smoke tests for both the source
  checkout and installed package paths.

## Technical Finish Backlog

| ID | Status | Work item | Observable outcome | Validation |
|---|---|---|---|---|
| TF-001 | Completed | End-to-end CLI workflow acceptance | A CLI-level integration test creates a throwaway consumer repository, runs `woof init --tracker local`, starts an epic, drives Stage 1-5 with public CLI-shaped `codex` and `claude` stubs, approves the plan gate, verifies the story commit, and asserts audit events. The implementation also stages graph-owned durable `.woof` files before commit-readiness checks, includes durable planning artefacts in story manifests, and ignores transient Stage-5 result files in `woof init` scaffolds. | Passed: `uv run pytest tests/integration/test_wf_acceptance.py -q`; `uv run pytest tests/unit/test_init.py tests/unit/test_graph.py tests/unit/test_check_3_scope.py tests/unit/test_check_7_commit_transaction.py tests/integration/test_wf_acceptance.py -q`; `just check` (339 tests) |
| TF-002 | Completed | Gate and recovery acceptance | CLI tests cover subprocess crash gates, reviewer blocker gates, failed check gates, empty-diff gates, malformed-state gates, and interrupted commit resume. The final story transaction now records `epic_completed` before the manifest-checked commit when that story completes the plan, so interrupted commit resume does not leave the durable audit log dirty after commit. | Passed: `uv run pytest tests/integration/test_wf_gate_recovery_acceptance.py -q`; `uv run pytest tests/integration/test_wf_acceptance.py tests/integration/test_wf_gate_recovery_acceptance.py tests/unit/test_graph.py -q`; `just check` (345 tests) |
| TF-003 | Completed | Operator state surfaces | `woof observe` and `woof preflight` expose current epic state, next action, gate cause, dispatch route, runtime policy, audit pointers, and check summaries without requiring source inspection. The status/audit views now report `.woof/.current-epic`, next operator command, gate cause, Stage-5 check summaries, resolved primary/reviewer routes, trusted-local runtime policy, and audit log pointers; `preflight` includes the same current-epic operator-state summary in text and JSON output. | Passed: `uv run pytest tests/integration/test_operator_state_surfaces.py tests/unit/test_observe.py tests/unit/test_preflight.py -q`; `just lint`; `just check` (349 tests) |
| TF-004 | Completed | Stage-5 check conformance matrix | Each Stage-5 check now has explicit success and failure conformance fixtures that call the real registry runner and prove its contract: quality gates, outcome markers, scope, contract refs, plan crossrefs, critique blockers, transaction manifests, docs drift, and review valve behaviour. The matrix also asserts every Stage-5 check has both fixture kinds. | Passed: `uv run pytest tests/unit/test_stage5_check_conformance_matrix.py -q`; `uv run pytest tests/unit/test_check_1_quality_gates.py tests/unit/test_check_2_outcome_markers.py tests/unit/test_check_3_scope.py tests/unit/test_check_4_contract_refs.py tests/unit/test_check_5_plan_crossrefs.py tests/unit/test_check_6_critique_blocker.py tests/unit/test_check_7_commit_transaction.py tests/unit/test_check_8_docs_drift.py tests/unit/test_check_9_review_valve.py tests/unit/test_check_stage_5_subcommand.py tests/unit/test_stage5_check_conformance_matrix.py -q`; `just lint`; `just check` (368 tests) |
| TF-005 | Ready | Tracker contract matrix | The `local` and `github` adapters satisfy the same `Tracker` protocol behaviours for create, fetch, authority checks, conflict resolution, plan summary push, and epic completion. | Adapter contract tests with deterministic command stubs; `just check` |
| TF-006 | Ready | Installed-package workflow acceptance | The installed package path can run the same local-tracker workflow acceptance without relying on source-checkout wrappers or private host state. | Installed-package integration test; `just check` |
| TF-007 | Ready | Final operator documentation | README, architecture, schemas, help text, and consumer docs describe only implemented behaviour and the next required operator commands. | Docs review, schema validation, `just check` |

## Current Item

TF-005 is the next ready item. It should build the tracker contract matrix so
the `local` and `github` adapters satisfy the same `Tracker` protocol behaviours
for create, fetch, authority checks, conflict resolution, plan summary push, and
epic completion.

Planned files:

- `src/woof/trackers/base.py`
- `src/woof/trackers/epic_body.py`
- `src/woof/trackers/github.py`
- `src/woof/trackers/local.py`
- `tests/unit/test_trackers.py`
- `docs/implementation-plan.md`

## Next Continuation Prompt

```text
We are working in /home/ryan/Work/woof.

Product goal:
Woof delivers software through an agentic multi-step process: discovery,
definition, breakdown, review, gate, execution, verification, manifest-checked
commit, and audit/resume.

Next item is TF-005: Tracker contract matrix.

Read first:
1. AGENTS.md
2. README.md
3. docs/implementation-plan.md
4. docs/architecture.md
5. docs/adr/001-orchestration-topology.md
6. docs/adr/002-graph-led-role-routing.md
7. docs/adr/003-issue-tracker-abstraction.md

Start with:
Run `git status --short --branch`, preserve unrelated local changes, and select
the first `In progress` or `Ready` item from the Technical Finish Backlog in
docs/implementation-plan.md.

Execution rule:
Implement a broad source-code slice for the selected item. Do not stop at a
proposal. Update tests and docs with any behaviour change. Run focused
validation and `just check`. Use conventional commits. Push and monitor CI when
the repository workflow requires it.

For TF-005:
Add a tracker contract matrix so the `local` and `github` adapters satisfy the
same `Tracker` protocol behaviours for create, fetch, authority checks, conflict
resolution, plan summary push, and epic completion.

Do not:
- add project-specific consumer assumptions;
- depend on private shell wrappers, external sync tools, dotfiles, or
  host-specific absolute paths;
- add runtime sandboxing or permission policy logic unless a new architecture
  decision explicitly requires it;
- ask packaging, tagging, or distribution questions before the technical finish
  backlog is complete.
```
