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

## Efficiency Backlog

These items track the post-0.1.6 efficiency pass prompted by the specwright
GitHub dogfood run. The governing invariant is: a valid graph artefact already
present on disk or reconstructed from a tracker means deterministic validation
and graph advancement, not model rediscovery.

| ID | Status | Work item | Observable outcome | Validation |
|---|---|---|---|---|
| EF-001 | open | Treat valid Woof epic contracts as sufficient planning input | If a GitHub issue cold-start parses into schema-valid `EPIC.md`, or a local `EPIC.md` already exists and validates, Woof must skip Stage-1 Discovery and only run deterministic Definition validation/closure before Breakdown. The graph must not require provenance events proving Discovery happened; the filesystem artefact is the authority and `epic.jsonl` is audit. Plain GitHub issues that only seed `spark.md` still run Discovery. | Add transition/cold-start tests for structured GitHub issue, local `EPIC.md` without `definition_closed`, invalid `EPIC.md`, and plain spark-only issue. Prove no primary discovery dispatch occurs when `EPIC.md` is valid. |
| EF-002 | done | Graph-wide token-efficiency design audit | Review graph nodes, prompts, cold starts, and dogfood audit telemetry to identify unnecessary model dispatches, repeated broad repo exploration, heavyweight default routes, and consolidation candidates. Produce a ranked implementation backlog covering prompt/context packs, per-node model/effort policy, graph modes for small epics, dispatch budgets, and deterministic bookkeeping. | Design note recorded below on 2026-05-26. No reduction implementation was included in this pass, so no reduction benchmark applies yet. |
| EF-003 | open | Bounded story execution context | Replace the current broad story prompt posture with a graph-built context pack: selected story, relevant `EPIC.md` outcomes/contract decisions, allowed paths, quality gates, and narrow repo facts. Subsequent story stages should use targeted exploration rather than repeating cold-start repo discovery. | Add tests for context-pack content and an audit comparison showing reduced command count/read volume on a representative dogfood story. |
| EF-004 | open | Per-node route policy and budgets | Allow model, effort, timeout, command-count, and token soft-limit defaults by graph node rather than only global primary/reviewer roles. Small deterministic or low-risk stages should not inherit high-effort defaults intended for hard implementation work. | Schema/docs/tests for node route policy plus observe output showing resolved per-node policy and budget outcomes. |
| EF-005 | open | Bounded planning context and repo map | Give Breakdown and plan critique a small deterministic repo map plus contract-specific path candidates instead of relying on broad model exploration to infer `paths[]`, ownership, and quality commands. | Add context-pack tests for Stage 3 planning inputs and compare plan-dispatch command count/read volume on a small GitHub-backed epic. |
| EF-006 | open | Small-epic planning mode | For valid `EPIC.md` contracts below explicit complexity thresholds, reduce planning overhead by skipping non-essential discovery work, using lower-effort route defaults, and optionally consolidating plan critique inputs around deterministic checks plus a single reviewer pass. | Add graph-mode schema/docs/tests and dogfood a small epic before making it default. |
| EF-007 | done | Small-valid-epic efficiency benchmark harness | `woof.bench.efficiency` plus the `just efficiency-bench` recipe run the same schema-valid `EPIC.md` fixture against isolated fresh consumer worktrees from the same base commit, one branch/worktree per Woof variant. The harness writes redacted JSON manifests and optional Markdown comparisons covering output quality, gate/success state, model profile, route selection, per-dispatch token usage, wall time, dispatch count, command count, prompt/artefact bytes, and diff stats without testing the brainstorm process. | Passed: `uv run pytest tests/unit/test_efficiency_bench.py -q`. Passed: focused route/profile/schema tests. Passed: dry two-variant smoke with `--stub-models --compare`, producing two `passed` manifests and a comparison table with no live model spend. Passed: `just check` (406 tests) and `git diff --check`. Operator workflow recorded in `docs/efficiency-evals.md`. |

### EF-002 Design Audit, 2026-05-26

**Measured dogfood evidence.** The specwright E1 dogfood run shows the cost is
not prompt payload size. It ran 10 dispatches with 57,096 prompt bytes, about
2,093s of subprocess time, 4,736,894 input tokens, 6,119,300 cache-read tokens,
123,899 output tokens, and about 200 completed Codex command executions. The
first story implementation had a 2,904-byte prompt but still consumed 503.8s,
2,866,343 input tokens, 2,727,552 cache-read tokens, 29,695 output tokens, and
57 completed Codex commands. That story had only four declared pathspecs, five
outcomes, one contract decision, and an estimate of seven tests, so the waste is
mostly model-side repository rediscovery and high-effort default routing.

**Validation for this design pass.**

- Passed: `git diff --check -- docs/implementation-plan.md docs/continuation-prompt.md`.
- Passed: targeted pytest selection covering pre-plan Discovery transitions,
  invalid existing `EPIC.md`, and GitHub structured/plain cold-start behaviour
  (5 tests).

**Node classification.**

| Node | Class | Current behaviour | Efficiency decision |
|---|---|---|---|
| `discovery_research`, `discovery_thinking`, `discovery_brainstorm` | Producer dispatch | Dispatches `primary` when the bucket is empty; otherwise accepts any non-empty bucket artefact and records audit. | Keep for spark-only issues. Never run when a valid `EPIC.md` is already present. Later consider a small-epic mode that consolidates or bypasses buckets. |
| `discovery_synthesis` | Producer dispatch with deterministic validation | Dispatches `primary` if synthesis files are missing, then validates required files and open-question shape. | Keep for spark-only discovery. Existing synthesis can be validated without dispatch. |
| `epic_definition` | Producer dispatch or deterministic Definition validation | Dispatches `primary` only when `EPIC.md` is missing and synthesis exists. If `EPIC.md` exists, validates schema/open-question closure and appends `definition_closed`. | This is the correct EF-001 closure point for GitHub/local valid `EPIC.md`: no Discovery, no Definition producer turn. Add regression tests. |
| `breakdown_planning` | Producer dispatch with deterministic validation/render | Dispatches `primary` only when `plan.json` is missing; validates `EPIC.md`, validates `plan.json`, validates crossrefs, and renders `PLAN.md`. | Needs bounded planning context and lower/default route policy for small epics. Existing valid `plan.json` should remain validation-only. |
| `plan_critique` | Reviewer dispatch with deterministic validation | Dispatches `reviewer` only when `critique/plan.md` is absent; otherwise validates existing critique. | Keep one reviewer pass for architectural commitments, but route/budget separately from story implementation. |
| `plan_gate_open`, `gate_open`, `human_review` | Human gate | Deterministic gate write or halt on `gate.md`. | No model tokens. Preserve mandatory plan gate. |
| `executor_dispatch` | Producer dispatch | Dispatches `primary` for the selected story with `plan.json`, `EPIC.md`, `.current-epic`, and agent instruction files as declared artefacts. | Highest priority for context packs and budgets; E1 S1 shows small prompt, huge exploration. |
| `critique_dispatch` | Reviewer dispatch | Dispatches `reviewer` with selected story, staged diff commands, `EPIC.md`, and `plan.json`. | Already narrower than executor, but still needs budgets and explicit staged-diff-first policy. |
| `review_disposition` | Deterministic, or human gate for blocker critique | Writes non-blocking dispositions without a model turn; blocker findings open a gate. | Already fixed by e99845e; keep deterministic. |
| `verification`, `commit` | Deterministic subprocess/check/git work | Runs declared checks, stages graph-owned artefacts, verifies manifests, and commits. | No model tokens. Add wall-time/file-change budget reporting, not model routing. |

**Cold-start and artefact authority findings.**

- GitHub cold-start (`woof wf --epic <N>` with no local directory) fetches the
  issue, writes `spark.md` for every issue, reconstructs `EPIC.md` only when
  managed Woof sections parse, writes `.last-sync`, appends `spark_created` and
  `tracker_synced`, then returns without running the graph.
- Local tracker creation writes only `spark.md`; local tracker fetch fails
  loudly because there is no remote. A hand-authored local `EPIC.md` in an
  existing epic directory is filesystem-authoritative.
- `plan.json` is never reconstructed from GitHub. A local `plan.json` is
  filesystem-authoritative and drives Breakdown/plan-review/gate/story state.
- With no `plan.json` and a present `EPIC.md`, the graph routes through
  `epic_definition` for deterministic validation/`definition_closed`, then to
  Breakdown. This matches the invariant, but the coverage is implicit and the
  observe mirror has the same provenance-shaped logic, so EF-001 should make it
  explicit and regression-proof.
- Plain GitHub issues without managed Woof sections seed only `spark.md`, so
  the next graph invocation still runs Stage-1 Discovery.

**Playbook exploration findings.**

- Discovery playbooks intentionally explore broadly and bundle many
  building-block prompts. That is acceptable for spark-only issues and wasteful
  once `EPIC.md` is already valid.
- Breakdown reads only the declared `EPIC.md`, but it still asks a model to
  infer story pathspecs and ownership. A deterministic repo map plus candidate
  path facts should reduce exploratory shell commands.
- Story execution already says to read only `.current-epic`, `plan.json`,
  `EPIC.md`, and agent instruction files, but it gives no precomputed target
  context. The E1 S1 command count shows the executor compensates with broad
  repository discovery.
- Story critique is naturally bounded by staged diff commands. It should remain
  staged-diff-first and should not repeat whole-repository discovery unless the
  staged diff or contract refs require it.

**Skip, consolidate, or make deterministic.**

1. EF-001: make valid `EPIC.md` cold-start/local paths explicit with tests.
   This skips all Stage-1 producer dispatches and permits only deterministic
   Definition validation/closure before Breakdown.
2. EF-003: build a story context pack before `executor_dispatch` containing the
   selected story, outcome statements, contract decision refs, allowed paths,
   declared quality gates, relevant agent-instruction excerpts, and narrow repo
   facts. Treat it as graph-owned prompt input and audited artefact context.
3. EF-004: introduce node route policy and soft budgets. Planning, story
   implementation, review, and deterministic nodes should have separate model,
   effort, timeout, command-count, token, read-volume, file-change, and wall-time
   defaults.
4. EF-005: add a deterministic Stage-3 planning context/repo map so Breakdown
   does less source-tree exploration when producing `paths[]`.
5. EF-006: only after telemetry proves the shape, add a small-epic mode that
   consolidates or bypasses low-value planning dispatches without weakening the
   mandatory plan gate.

**Telemetry and budget design.**

- Extend dispatch events with `node_type`, `budget_policy_id`, resolved
  per-node model/effort/timeout, and budget outcome fields such as
  `budget_status`, `budget_warnings[]`, and `budget_exceeded[]`.
- Keep existing counters: `prompt_bytes`, `artefact_bytes`, `duration_ms`,
  `tokens_in`, `tokens_out`, cache token fields, and Codex `command_count`.
- Add deterministic graph-side counters where Woof can measure them without
  provider support: changed file count, staged file count, staged diff bytes,
  declared context-pack bytes, declared context-pack file count, and check
  wall-time. Treat true model read volume as adapter-reported only; do not infer
  it from shell logs unless a provider exposes a stable signal.
- Budgets should be soft at first: record warnings in `dispatch.jsonl`, expose
  them in `observe`, and reserve hard gates for explicit policy settings. This
  avoids turning measurement into a new source of flaky workflow halts.

**Bounded story context pack shape.**

The first implementation should generate a small graph-owned JSON/Markdown pack
before `executor_dispatch` and pass that pack as the primary context. Minimum
content:

- `epic_id`, `story_id`, story object, dependency state, and allowed `paths[]`;
- only the `EPIC.md` outcomes named by `story.satisfies[]`;
- only the contract decisions named by `implements_contract_decisions[]` and
  `uses_contract_decisions[]`, plus their native refs;
- declared quality-gate commands and test-marker/doc-path policy relevant to
  the story;
- selected `AGENTS.md`/`CLAUDE.md` excerpts, bounded by path relevance;
- deterministic repo facts: existing files matching story pathspecs, nearby
  tests, native contract files referenced by CDs, and a compact tree summary.

**Efficiency benchmark protocol.**

Use a small valid epic as the primary benchmark fixture because the current
efficiency pass is not measuring brainstorm quality. The fixture starts at
`EPIC.md` with schema-valid outcomes, contract decisions, and acceptance
criteria; it deliberately bypasses Stage-1 Discovery and exercises deterministic
Definition validation, Breakdown, plan critique, the plan gate, and at least one
small story path.

The benchmark harness should use fresh worktrees rather than hand-reverting a
single branch:

- Each run starts from the same consumer repository base commit, recorded as
  `consumer_base_sha`.
- Each Woof implementation or prompt/model-policy variant is run in its own
  branch/worktree, for example `bench/<scenario>/<variant>/<run-id>`, created
  from `consumer_base_sha`.
- The run records the Woof checkout commit or tree under test as `woof_sha`.
- If the graph commits story output, the resulting consumer commit is recorded
  as `consumer_result_sha`.
- The harness seeds identical `.woof/epics/E<N>/EPIC.md` and tracker/runtime
  config for every run. It must not reuse `.woof` runtime state, audit files,
  staged files, or working tree changes between variants.
- Raw `.woof/epics/E<N>/audit/` output remains untracked unless explicitly
  redacted by Woof's audit pipeline. The durable benchmark artefact is a small
  redacted manifest, such as `docs/efficiency-runs/<date>-<scenario>-<variant>.json`,
  plus an optional Markdown comparison table.
- The comparison treats correctness as a first-class metric: fewer tokens is
  only a win when the graph reaches the expected state, quality gates pass or
  open the expected gate, the plan/story output remains acceptable, and the
  result diff stays inside the declared story scope.

Minimum run-manifest fields:

- scenario id, variant id, run id, timestamp, `woof_sha`,
  `consumer_base_sha`, and optional `consumer_result_sha`;
- seeded epic id/path and whether the scenario started from `EPIC.md`,
  `spark.md`, or `plan.json`;
- resolved route policy: selected model profile, adapter, model, effort,
  timeout, flags, and any per-node overrides;
- node sequence, final graph state, gates opened, gate triggers, check outcomes,
  and story statuses;
- dispatch totals and compact per-route/per-event rows: spawned/returned/killed,
  subprocess wall time, token fields, cache token fields, prompt bytes, artefact
  bytes, output/stderr bytes, model profile, and Codex command count;
- result quality summary: changed file count, staged/committed diff stats,
  declared pathscope match, quality command result, reviewer severity, and
  operator notes.

### EF-007 Benchmark Harness, 2026-05-26

**Implemented harness.** The first measurement path lives in
`src/woof/bench/efficiency.py` and is exposed through
`just efficiency-bench`. It supports:

- `run`: creates one throwaway consumer worktree and branch per variant from
  the same resolved `consumer_base_sha`, seeds a fixed valid `EPIC.md`, runs
  `woof wf`, auto-approves the known plan gate by default, and writes redacted
  run manifests;
- `compare`: renders a deterministic Markdown table from one or more manifest
  JSON files;
- `--stub-models`: creates local public-CLI-shaped `codex` and `claude` stubs
  so the collection/comparison path can be proven without live model spend;
- `--model-profile` and `--variant-model-profile`: select named
  `.woof/agents.toml` model profiles globally or per variant without editing
  prompts or graph code;
- `--config-dir`: copies deterministic runtime config into every worktree when
  the consumer base does not already carry the benchmark policy.

**Fixture.** `examples/efficiency/small-valid-epic/EPIC.md` starts from
schema-valid Definition output, not a spark. It has one outcome (`O1`), one
contract decision (`CD1`), and a tiny story shape used by the stub planner and
executor. This keeps the benchmark focused on valid-epic graph efficiency
rather than brainstorm quality.

**Manifest fields.** Each run manifest records:

- scenario id, variant id, run id, timestamp, `woof_sha`, `woof_dirty`,
  selected model profile, `consumer_base_sha`, branch, dirty flag, and optional
  `consumer_result_sha`;
- seeded epic id/path/start state;
- resolved route policy and trusted-local runtime policy from `observe`;
- node sequence, final state, gate events/current gate, check summaries, and
  story statuses;
- dispatch spawned/returned/killed counts, subprocess duration, token/cache
  totals, prompt/artefact/output/stderr bytes, command-count totals, compact
  returned-event rows, and per-route aggregate rows;
- committed/staged/unstaged diff stats, pathscope summary, reviewer severity,
  quality command result, and operator notes after conservative redaction.

**Isolation behaviour.** Worktrees are created with `git worktree add -B` from
the same base SHA. The harness deletes and reseeds only the benchmark epic
directory in each worktree, writes `.woof/.current-epic`, and uses each linked
worktree's git exclude file for benchmark-only runtime/config files when stub
or copied config is used. The tests prove variant-local `.woof` audit/runtime
state in one worktree is absent from the next variant.

**Validation for this implementation.**

- Passed: `uv run pytest tests/unit/test_efficiency_bench.py -q` (manifest
  aggregation/comparison, redaction, model-profile capture, and isolated
  throwaway worktrees).
- Passed: focused route/profile/schema tests covering dispatch, observe,
  preflight, validate, init, and efficiency bench behaviour.
- Passed: dry two-variant smoke using
  `just efficiency-bench run --stub-models --compare`, which produced two
  `passed` manifests with `epic_complete` final state, selected `stub` model
  profile, no pathscope failures, sane token/command totals, and non-null
  `consumer_result_sha` values.
- Added `docs/efficiency-evals.md` and `docs/efficiency-runs/README.md` as the
  live-eval operator workflow, manifest location, and execution prompt.

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
