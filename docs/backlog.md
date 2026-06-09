# Woof Backlog

This is the single source of truth for open Woof work. It is prescriptive: the items below describe work to do, not work that has already been done.

Authority: this file plus the ADRs and `docs/architecture.md` define the project. Anything not described here is not part of the plan.

## Shape

Woof is an inner-loop SDLC tool for AI-assisted development.

- **Operator surface.** One umbrella `/woof` skill over the `woof` CLI, plus the interactive `/woof:brainstorm` design specialist (ADR-007). Setup, map-codebase, run, and gate flows live as references inside the umbrella.
- **No split-skill suite.** `/woof:setup`, `/woof:map-codebase`, `/woof:run`, and a separate target-architecture skill are withdrawn. Useful workflow guidance belongs in the `/woof` umbrella and its references.
- **No `woof graph` command API.** The shipped runner is `woof wf`. It owns graph progression, dispatch, deterministic nodes, gate writes, and resume behaviour in-process.
- **Setup CLI.** `woof init`, `woof preflight`, and `woof hooks install` are non-interactive Python commands used during project setup. They are not parallel surfaces for running epics.
- **State on disk.** `spark.md`, `EPIC.md`, `plan.json`, JSONL audit, dispositions, gate files, and cartography artefacts are the authoritative state. Re-running `woof wf --epic N` resumes from disk.
- **Dispatch supervision.** `woof dispatch` supervises workers in process groups on phase-scoped clocks: idle and wall-clock before the terminal marker, completion-grace and tail cap after it. Dispatch telemetry carries `exit_type`; `completed_lingering` is a successful outcome, not a timeout. See ADR-008.
- **Interactive design.** `/woof:brainstorm` hydrates an existing spark by writing an accepted bundle into `.woof/epics/E<N>/discovery/brainstorm/`. Woof ingests that bundle as discovery source material; it does not mechanically map `work_units[]` to stories.
- **Cartography prerequisite.** Every consumer repo has `.woof/codebase/` containing human-authored design docs, mapper-authored AS-IS docs, and a mechanical index.
- **Structural cartography.** ADR-009 accepts a queryable structural index under `.woof/codebase/structural/` as the next cartography pivot: files, symbols, and typed edges regenerated locally and consumed through `woof cartography`, not through a graph DB, daemon, MCP server, or `woof graph` API.
- **Evidence over confidence.** Reviewer blockers must carry resolvable evidence. Confidence scores, if ever added, are advisory eval metadata and never gate-affecting.
- **Expert workstation posture.** Woof assumes explicit local prerequisites. tmux may supervise long runs, but it must not become a second graph or state authority.

## Implementation Approach

Each epic is broken into small, reviewable coding-agent prompts when it starts. The operator reviews each prompt's output before the next prompt runs.

Per-epic plans live under `docs/plans/` only when an epic is active. Do not keep speculative plans for withdrawn directions.

## Epics

### E1. Cartography Prerequisite

Make the cartography artefact group mandatory infrastructure with preflight enforcement and per-language refresh templates.

The contract and missing/stub enforcement landed in prompt 1 (`docs/plans/e1-cartography.md`): the `[cartography]` schema shape (`staleness_floor_hours`, `summary_min_chars`, `languages`, `stub_marker`); enforcement keyed on the block's presence and scaffolded by `woof init`; `woof preflight` failing closed on a missing/non-executable `scripts/refresh-cartography`, a missing or stub `TARGET-ARCHITECTURE.md`/`PRINCIPLES.md`, or a missing mechanical-layer file (`tags`, `files.txt`, `freshness.json`); exact stub detection (removable stub marker, or body below `summary_min_chars` unless front matter marks it complete); and the `tree.txt` -> `files.txt` rename.

Prompt 2 landed the staleness warning: a `cartography.freshness` floor check reads the mechanical `freshness.json`, derives its age, and warns (non-blocking, refresh-prompt carrying) past `staleness_floor_hours`. `PreflightFinding` gained a `warn` severity (an `ok=True` finding printed `WARN`, kept out of `failed`/exit code). A missing stamp stays the mechanical check's blocking concern; a malformed stamp warns non-blockingly.

Prompt 3 landed the per-language templates and `woof init` composition: `refresh-cartography` fragments for Python, Go, TypeScript, Rust under `languages/refresh-cartography/`, referenced from each `languages/<lang>.toml` via `[cartography].refresh_fragment`. `woof init --language <lang>` (repeatable) records `[cartography].languages` and composes `scripts/refresh-cartography` (mode 0o755) from a shared scaffold (git ls-files -> files.txt; one ctags pass -> tags; freshness.json) plus the fragments, idempotently via a managed block, falling back to an existing `prerequisites.toml` on re-run. `freshness.json` is defined as `{ ts, git_ref, age_s, generator_version }` with `schemas/freshness.schema.json`; `ts` is the authoritative staleness signal and `age_s` the deterministic test fallback (the prompt-2 reader was inverted accordingly so a frozen `age_s` cannot mask production staleness).

Prompt 4 landed the fail-loud post-commit hook path: the Woof-managed hook runs `./scripts/refresh-cartography` on every commit, emits a `woof post-commit:` diagnostic when the script is missing, non-executable, or exits non-zero, and preserves a failing refresh script's exit status from the hook.

Prompt 5 landed the legacy-consumer onboarding error: `woof preflight` now fails a project whose `.woof/prerequisites.toml` has no `[cartography]` block with a `cartography.contract` finding that points at `/woof` setup, the map-codebase flow, `skills/woof/references/setup.md`, and `skills/woof/references/map-codebase.md`. The finding explains the expected path: re-run `woof init --language <lang>`, author the design docs, run map-codebase for the AS-IS layer, refresh the mechanical files, and install the post-commit hook. The preflight cache version was bumped so a stale green floor cache cannot mask the migration error.

Status: complete. E2 and E3 are no longer blocked on E1.

Depends on: nothing. Completed prerequisite for: E2, E3.

### E2. Contract Readiness and Run Resilience

Add operational guardrails around the current `woof wf` runner without splitting the operator surface.

Open work:
- Add a deterministic Stage-2.5 readiness node after `EPIC.md` exists and before `breakdown_planning`.
- Add `readiness-result.schema.json` and gate support for `readiness_gate`.
- Readiness checks:
  - every observable outcome has a machine-checkable acceptance signal;
  - acceptance criteria avoid pure subjective prose unless paired with concrete assertions or commands;
  - contract decisions include exact paths, schema refs, API refs, or explicit forward-created markers;
  - referenced existing paths resolve against `git ls-files`;
  - cited symbols resolve where Woof can cheaply prove them, otherwise require explicit operator-marked forward creation;
  - Stage 3 receives enough information to decompose without inventing interfaces.
- Define the forward-created annotation grammar exactly: `` `path/or/symbol` (forward-created) `` or `` `path/or/symbol` (created by ticket <id>) ``.
- Readiness checker timeouts produce non-blocking performance findings. A readiness gate must not block solely because its own checker timed out.
- Add readiness recycle escalation: after a configured number of failed readiness cycles, open an escalation-flavoured readiness gate rather than looping revise/fail indefinitely.
- Add audited readiness-gate resolutions: `revise_epic_contract`, `approve_with_reason`, `abandon_epic`.
- Add blocker-evidence discipline to `critique.schema.json`, prompts, and checks:
  - blocker findings require `evidence`;
  - evidence must resolve to a known artefact reference: file:line, story id, observable outcome id, contract-decision id, schema ref, or quality-gate id;
  - extend `check_6_critique_blocker` so unresolvable blocker evidence is itself a blocker;
  - do not add confidence-based gating.
- Add `quality-gates.toml` mode support:
  - `strict`: any failure blocks;
  - `baseline`: command-level baseline for arbitrary gates; a command already red at capture time is reported but does not block unless its configured command identity changes or the baseline expires.
- Add baseline freshness metadata with wall-clock age and graph/run-iteration age, plus explicit recapture semantics.
- Add run-resilience policy over `woof wf` dispatch telemetry:
  - stage-aware progress detection;
  - separate counters for consecutive no-progress and consecutive same-error signatures;
  - same-error checked before no-progress;
  - error-signature normalisation that strips volatile paths, line/column spans, ISO timestamps, UUIDs, and excess whitespace, preserves standalone numbers, and truncates to a bounded length;
  - course-correction gates for repeated constraint/invariant/contract-discovery signatures;
  - run-resilience gates for repeated timeout without expected artefact progress;
  - separate classification for rate-limit wait/resume.
- Add HEAD/branch drift detection:
  - record `head_before`, `branch_before`, `head_after`, and `branch_after` for dispatched work;
  - halt commit/gate paths if HEAD or branch moved unexpectedly and not through a graph-owned commit.
- Add optional tmux-backed long-run supervision for `woof wf`: panes/logs/status only; no direct state mutation outside Woof commands.
- Tests covering readiness pass/fail, readiness timeout non-blocking behaviour, readiness escalation, readiness gate resolution, blocker evidence resolution, command-level baseline behaviour, baseline freshness/recapture, HEAD/branch drift, circuit-breaker decision logic, and tmux command construction without graph bypass.

Depends on: E1. Blocks: E3, E4, E5.

### E3. Specwright Bootstrap

Make specwright ready as the first real consumer for the production-shape eval.

Open work:
- Author `specwright/.woof/codebase/TARGET-ARCHITECTURE.md` by hand or through the `/woof` setup reference.
- Author `specwright/.woof/codebase/PRINCIPLES.md`.
- Run the `/woof` map-codebase flow against specwright to produce the AS-IS docs.
- Add `specwright/scripts/refresh-cartography` from the Python language template.
- Install the Woof post-commit hook.
- Verify preflight and Stage-2.5 readiness pass against specwright.

Depends on: E1, E2.

### E4. Eval Instrumentation

Measure the production shape: `/woof` as the operator surface, `woof wf` as the runner, cartography on disk, dispatched producer/reviewer work, readiness, run-resilience telemetry, drift detection, blocker-evidence checks, and JSONL audit.

Open work:
- Add `node_type` to every dispatch event so per-node attribution is possible.
- Include dispatch-return outcome fields in the manifest: `exit_type`, `exit_code`, `error_signature`, expected artefact presence/schema status, rate-limit classification, and HEAD/branch before/after.
- Include run-resilience rows for circuit-breaker decisions, no-progress/same-error counters, readiness outcomes, baseline-gate mode/outcome, blocker-evidence resolution outcomes, and drift events.
- Retain `artefacts_loaded[]` per event in any compacted manifest produced by the eval harness.
- Persist prompt and output bodies into the eval output directory at run teardown.
- Add per-node rows in the comparison markdown: calls, tokens, prompt bytes, artefact bytes, and duration.
- Add an artefact-reload section to the manifest: `{artefact_path: [(call_index, node_type, bytes)]}`.
- Keep the Python bench as the eval harness; `/woof` may run it through Bash, but evals do not require a separate skill.

Depends on: E2.

### E5. Baseline Eval Run

Run the eval against specwright in the production shape and capture the first manifest as a stable baseline for future iteration.

Open work:
- Single-variant run against specwright at HEAD.
- Inspect the per-node manifest; confirm `node_type` correlation works and artefact reloads surface duplication.
- Decide the first optimisation target from the data.

Depends on: E3, E4.

### E6. Deterministic Contract Conformance Audit

Add a post-baseline conformance audit inspired by Pickle Rick's Citadel shape without copying its project-specific analysers.

Open work:
- Add a diff-scoped conformance audit node or Stage-5 check that reads `EPIC.md`, `plan.json`, the story diff, cartography, and the structural index when available.
- Add `conformance-result.schema.json`.
- Audit questions:
  - every satisfied observable outcome has production evidence in changed files, not only test markers;
  - every implemented contract decision's declared surface exists in the diff or current tree;
  - declared guards, permissions, state transitions, or lifecycle invariants from cartography are not bypassed;
  - new allowlist/config entries have production callers, structural-index evidence, or explicit rationale;
  - domain-specific checks are consumer-supplied rules, not hardcoded Woof assumptions.
- Fold in the TrueCourse-style spec-to-contract-to-verify idea where it is deterministic: extract checkable obligations from `TARGET-ARCHITECTURE.md`, `PRINCIPLES.md`, and `EPIC.md`, then verify the diff against those obligations.
- Emit stable machine-readable findings suitable for later SARIF-like export; do not make SARIF itself a V1 requirement.
- Start with generic deterministic checks and a consumer-rule hook. Add language/framework analysers only when a real consumer proves the rule has reusable value.
- The audit can open a story gate on deterministic high-severity violations. It must not become an LLM debate loop.

Depends on: E1, E2, E5, E13.

### E8. Run Lineage

Thread one run identity across an epic so it is reconstructable and replayable.

Open work:
- Add a `run_id` to every `epic.jsonl` and `dispatch.jsonl` event, threading epic -> story -> dispatch -> model session -> gate -> check -> commit. Confirm an epic is reconstructable as a single trace from disk, joinable from a check failure back to the producer session.
- Define file-first run replay from on-disk state plus the JSONL lineage: re-enter the graph at a recorded node for debugging without re-running prior work.
- Tests: lineage id present across all event types and joinable from a check failure to the producer session; replay re-enters at a recorded node without redoing prior work.

Depends on: completed E7 process supervision. Refines: E4 (telemetry lineage).

### E9. Producer-Output Recovery

Add a bounded recovery ladder so producer-output failures do not jump straight to a human gate.

Open work:
- Add resume-to-correct: when a producer artefact fails schema validation or a deterministic check, resume the producer's captured session (the `cc_session_id` / transcript reference in dispatch telemetry) with the deterministic failure evidence as feedback, instead of a cold re-dispatch. Bounded retry budget.
- Add a graded recovery ladder ahead of gate-open: narrow deterministic salvage of a recoverable-but-malformed payload (trim unfinished trailing value, drop dangling comma, close open containers; never invent values); normalisation with safe defaults (missing optional -> default, missing required -> hard fail); bounded retry (compacted payload or resume-to-correct); gate only when exhausted. Salvage and normalisation fail loud; they are not a tolerant parser.
- Tests: resume-to-correct happy path and retry-budget exhaustion; salvage of truncated payloads and hard-fail on unrecoverable ones.

Depends on: completed E7 process supervision, E8 (session-join via run lineage).

### E10. Plan-Graph Algorithms

Replace the hand-rolled plan dependency-graph checks with a graph-library implementation.

Open work:
- Replace the hand-rolled `depends_on[]` acyclicity check (check 5) with NetworkX: cycle detection, topological generations (legal execution waves), and descendant impact analysis (which stories a failed story blocks). NetworkX is a data-structure library, not an orchestrator; it does not own graph authority.
- Tests: cycle detection and topological-generation output on representative plans; impact analysis identifies the blocked descendants of a failed story.

Depends on: nothing (refines an existing check). Independent of E8/E9.

### E11. MP Engineering Review Imports

Convert the MP engineering-skill comparison into Woof improvements without interrupting the active E1/E2 implementation line. This epic is a holding area for review-derived work: small prompt/doc fixes can be pulled into earlier epics when they touch the same file, while larger capability work starts only when the current chain has room.

Open work:
- **P0.3 new inner-loop capability:**
  - Add a human-supervised "build a throwaway to learn" escape hatch to `woof-brainstorm`, preferably upstream in agent-toolkit. The probe must be named throwaway, answer a stated design question, feed the answer into the brainstorm bundle, and be deleted or absorbed.
  - Design a scoped bug-diagnosis lifecycle: a `kind: bug` spark path, a diagnosis playbook for reproduce/minimise/hypothesise/instrument, and a handoff into the existing Stage-5 red-green-refactor fix flow.
- **P0.4 review later, lower on the backlog:**
  - Review whether to build a standalone codebase-deepening review flow off the `CURRENT-ARCHITECTURE.md` / `TARGET-ARCHITECTURE.md` delta. Treat it as a small subsystem, not a prompt tweak; defer until the cheap heuristics or baseline eval data justify it.
  - Review an on-demand "zoom out this neighbourhood" operator gesture and the lifecycle for binding repo-durable cartography to per-epic `CONTEXT.md` glossary terms.

Sequencing: preserve the current E1/E2 chain. The P0.1 hygiene items (onboarding alignment, vendored-README fix) and the P0.2 prompt-doctrine imports are complete; P0.3 starts after the readiness and structural-cartography paths are stable, and P0.4 is explicitly deferred for later review.

### E12. Structural Cartography Index

Build the ADR-009 mechanical structural index as the foundation for impact queries and later context optimisation.

Open work:
- Start with a tree-sitter work audit: inspect any concurrent tree-sitter/parser branch or landed code, decide whether it is the extraction substrate, and avoid creating a second parser path. If no reusable substrate exists, implement the V1 Python extractor with a clear adapter boundary so tree-sitter can replace or extend it later.
- Add `.woof/codebase/structural/index.sqlite` as a gitignored mechanical artefact regenerated by `scripts/refresh-cartography` once the generator exists.
- Define the SQLite schema and migration/versioning strategy for `files`, `symbols`, `edges`, and `meta`. Keep line numbers as metadata, not primary symbol identity.
- Define stable symbol IDs for Python v1: module path + qualified name + kind, with overload/disambiguation rules where needed. Do not use line-sensitive IDs for durable audit references.
- Build the Python-first extractor:
  - symbol outlines for modules, classes, functions, methods, signatures, docstrings where cheap, and line spans;
  - `contains` and `defines` edges;
  - import/dependency edges;
  - high-confidence direct call edges where resolution is safe;
  - labelled `EXTRACTED`, `HEURISTIC`, or `AMBIGUOUS` provenance/confidence for every edge.
- Prefer precision over apparent completeness. Dynamic dispatch, member calls, import alias ambiguity, and framework magic should be omitted or explicitly labelled rather than silently guessed.
- Add `woof cartography` read-only CLI verbs: `symbols`, `callers`, `callees`, `impact`, `context`, and `stats`, each with JSON and token-bounded text output.
- Add `cartography-structural-query-result.schema.json` for JSON query output and focused fixtures for a small Python repo.
- Add an eval harness and a Woof-on-Woof gold set (the 2026-06-07 spike under vault `research/code-mapping/spikes/` seeds both): hand-labelled true callers/callees for a sample of symbols, plus one larger external Python repo for scale. Measure extraction coverage, per-tier edge precision (`EXTRACTED` target >= 0.95, observed ~1.0 on the seed sample; `HEURISTIC` measured and thresholded, with common-name method edges suppressed), caller/callee precision and recall, changed-file -> affected-symbol impact recall, and structural-context token cost. These metrics gate E13 producer wiring and any completeness claim.
- Update `woof preflight`, `woof init`, `skills/woof/references/map-codebase.md`, and architecture docs so structural indexing is opt-in until the generator is present, then enforced by the declared cartography capability.
- Tests: extractor fixtures for outlines/imports/calls; stable-ID resilience to line shifts; ambiguous-edge labelling; query CLI output; refresh idempotency; gitignore coverage; preflight behaviour when structural indexing is declared but stale/missing.

Depends on: E1. Can start before E5 only for isolated parser/indexer foundation work; prompt dispatch integration waits for E5 baseline measurement.

### E13. Stage-5 Impact Context Integration

Thread structural impact context into the story loop, starting with the independent reviewer.

Open work:
- Map staged diff paths and hunks to changed files/symbols using the structural index, falling back cleanly to file-level impact when symbol resolution is unavailable.
- Feed Stage-5 `critique_dispatch` a bounded `woof cartography impact` context: direct callers/importers first, then likely transitive neighbours, with provenance/confidence labels preserved.
- Record structural-context bytes and token estimates in dispatch telemetry and E4/E5 eval manifests.
- Add reviewer prompt wording: structural impact is evidence to inspect, not proof of correctness; ambiguous or heuristic edges require source verification before becoming a blocker.
- Keep producer integration off by default until the E12 eval metrics plus a reviewer-delta A/B (Stage-5 critique with vs without impact context, over real Woof diffs) show a net real-finding improvement with no noise blow-up. If enabled later, give the producer neighbours and tests before editing, not a broad graph dump.
- Tests: diff-to-symbol mapping, prompt assembly under token caps, fallback when index is missing/stale, telemetry attribution, and blocker evidence resolving to structural query output plus file lines.

Depends on: E12, E4, E5.

### E14. Ranked And Semantic Cartography Retrieval

Add the non-structural retrieval recommendations as separate, measurable artefacts rather than bundling them into the call graph.

Open work:
- Add Aider-style ranking over the structural index: centrality/PageRank, task personalisation from epic/story paths and mentioned identifiers, and a token-budgeted signature skeleton.
- Add token-savings instrumentation for cartography context: raw candidate bytes/tokens vs injected bytes/tokens, per node.
- Add a Semble-style semantic retrieval artefact under `.woof/codebase/retrieval/`: BM25 first, then static local embeddings only if dependency and model size stay compatible with Woof's expert-workstation posture.
- Add `woof cartography search` returning bounded snippets with file path, line span, score, and why it matched. This is retrieval, not structural truth.
- Add `cartography-search-result.schema.json`.
- Tests and evals: ranking determinism, personalisation effect, retrieval fallback without embeddings, token-saving accounting, and no API-key/network dependency in the default path.

Depends on: E12. Sequenced after E13 unless E5 shows retrieval is a larger bottleneck than structural impact.

### E15. Structural Onboarding And Mapper Grounding

Use the structural index to improve large-repo onboarding and AS-IS cartography without replacing mapper-authored prose.

Open work:
- Add an onboarding-only structural pass that computes hubs, bridge files, shortest paths between entry points, and coarse communities from the structural index.
- Use structural summaries to seed mapper subagent prompts so `CURRENT-ARCHITECTURE.md` and `STRUCTURE.md` are grounded in observed edges rather than mapper rediscovery alone.
- Add a bottom-up mapper option: summarise leaf modules first, then feed those summaries upward into architecture/structure docs.
- Adopt SCIP-style stable symbol strings in AS-IS prose where useful, so docs can cite exact symbols rather than free-text names.
- Add DESCRIBES-style lightweight links from AS-IS sections to files/symbols they cover. Use them later for precise stale-doc prompts when the structural index changes.
- Keep visualisation and human `observe` views optional. Do not build an HTML/graph UI until a real onboarding run proves it is worth the surface area.
- Tests: generated prompt seed shape, symbol-link validation, stale-section detection over changed symbols, and mapper output hygiene preserving the forbidden-files/secret-scan rules.

Depends on: E12. Best started when a real large inherited repo or specwright-scale onboarding run exists.

## Settled Choices

- Runtime action safety is trusted-local plus commit-safety, audit, and drift detection. Woof does not add a preventive sandbox unless real usage proves detection is insufficient. External sandbox-orchestration tooling (e.g. sandcastle) treats the sandbox as the product; Woof deliberately places the safety boundary at commit time instead. Git worktrees, if adopted for parallel dispatch, give collision avoidance but not the isolation boundary.
- Woof owns the graph authority in deterministic Python. LangGraph and Temporal are not adopted; their transferable concepts (explicit conditional edges, reducer-based state merge, checkpoint/interrupt/replay vocabulary) are mined into Woof's own engine rather than ceding control flow to a framework. NetworkX is adopted only as a plan-graph algorithm library (cycle and topological analysis), because it is a data structure, not an orchestrator.
- Structural cartography is an embedded mechanical index, not an orchestration graph. Default storage is SQLite under `.woof/codebase/structural/`; the only public surface is `woof cartography`. Woof does not adopt Neo4j, LadybugDB, always-on code-intel daemons, or MCP graph tools.
- Structural-index confidence is advisory. Reviewer blockers still need resolvable evidence; an `AMBIGUOUS` or `HEURISTIC` edge can point a reviewer to source, but cannot block on its own.
- Parallel story dispatch via git worktrees is deferred, not rejected. It is sequenced after the completed E7 process-supervision work plus E8/E9 (lineage and recovery), because running multiple trusted-local full-access workers concurrently turns the commit-safety boundary into a concurrency-safety boundary: concurrent transaction manifests, shared non-worktree state, shared MCP and credentials. It is not an active epic.
- The eval harness stays in Python. `/woof` can launch it, but there is no `/woof:eval` skill.
- tmux is optional for long runs and only supervises `woof wf`; it does not own workflow state.
- There is no active plan to build `woof graph` or split `/woof` into peer skills.

## Glossary

- **Cartography** - the artefact group at `.woof/codebase/`: durable design docs, mapper-authored AS-IS docs, and mechanical files.
- **Mechanical layer** - `tags`, `files.txt`, `freshness.json`, and planned generated indexes such as `.woof/codebase/structural/`; cheap, post-commit refreshed, and gitignored.
- **Structural cartography index** - ADR-009's generated files/symbols/edges SQLite artefact under `.woof/codebase/structural/`.
- **Structural impact context** - token-bounded callers/callees/dependencies output from `woof cartography impact`, used first by the Stage-5 reviewer.
- **Producer** - the LLM-dispatched node that creates an artefact for a graph stage.
- **Reviewer** - the LLM-dispatched node that critiques the producer's output in an isolated context.
- **Mapper subagent** - a Claude subagent launched by the `/woof` map-codebase flow to author one or two cartography docs.
- **Readiness gate** - deterministic halt after Stage 2 when `EPIC.md` is not concrete enough for planning.
- **Strict quality gate** - quality gate mode where any failure blocks.
- **Baseline quality gate** - quality gate mode where a pre-existing red command is recorded and only deterioration blocks.
- **Blocker evidence** - machine-resolvable evidence attached to a blocker finding, such as file:line, story id, outcome id, contract-decision id, schema ref, or gate id.
- **HEAD/branch drift** - unexpected git position movement during dispatch or commit that is not explained by a graph-owned commit.
- **Conformance audit** - deterministic diff-scoped audit that checks implemented production changes against `EPIC.md`, plan contracts, and consumer invariants.
- **Run lineage** - a single `run_id` carried by every epic/dispatch event so one epic execution is reconstructable as a single end-to-end trace and replayable from disk.
- **Completed-but-lingering** - a dispatched worker that emitted its terminal result but whose process has not exited because a spawned child holds the stdout pipe open. Classified as completed, not as a timeout.
- **Resume-to-correct** - on a recoverable producer-output failure, resuming the producer's captured model session with the deterministic failure evidence as feedback, instead of a cold re-dispatch.
- **Graded recovery ladder** - bounded sequence applied before a gate-open: deterministic salvage, normalisation with safe defaults, bounded retry (compacted payload or resume-to-correct), then gate.
