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
- **Cartography prerequisite.** Every consumer repo has `.woof/codebase/` containing human-authored design docs, mapper-authored AS-IS docs, and a mechanical index. Dispatched nodes consume the mapped documents per the architecture loading map (E19 wires this).
- **Structural cartography.** ADR-009 accepts a queryable structural index under `.woof/codebase/structural/` as the next cartography pivot: files, symbols, and typed edges regenerated locally and consumed through `woof cartography`, not through a graph DB, daemon, MCP server, or `woof graph` API.
- **Evidence over confidence.** Reviewer blockers must carry resolvable evidence. Confidence scores, if ever added, are advisory eval metadata and never gate-affecting.
- **Expert workstation posture.** Woof assumes explicit local prerequisites. tmux may supervise long runs, but it must not become a second graph or state authority.

## Implementation Approach

Each epic is broken into small, reviewable coding-agent prompts when it starts. The operator reviews each prompt's output before the next prompt runs.

Per-epic plans live under `docs/plans/` only when an epic is active. Do not keep speculative plans for withdrawn directions.

When a per-epic plan introduces a new gate-decision verb or a new audit event, its plan row must carry two explicit clauses: a legality matrix (which gate types accept the verb, against which story or epic states) and an event-log consumer checklist (every reader of the affected status or event - status counters, terminal-state and completion checks, the commit-time completion-event writer, `observe`, the bench harness, and the trackers). E17's `retry_story` and `abandoned`-status slices each needed several adversarial-review rounds because those two clauses were left implicit; stating them at plan time closes the new-state-old-consumer gap before review.

## Epics

### E1. Cartography Prerequisite

Make the cartography artefact group mandatory infrastructure with preflight enforcement and per-language refresh templates.

Status: complete. Unblocks E2, E3. It established the `[cartography]` prerequisite contract (`staleness_floor_hours`, `summary_min_chars`, `languages`, `stub_marker`); `woof preflight` failing closed on a missing/non-executable `scripts/refresh-cartography`, missing or stub design docs, or a missing mechanical-layer file (`tags`, `files.txt`, `freshness.json`); a non-blocking `cartography.freshness` staleness warning derived from `freshness.json`; per-language `refresh-cartography` templates composed idempotently by `woof init --language <lang>`; the fail-loud post-commit refresh hook; and a legacy-consumer onboarding error pointing at the `/woof` setup and map-codebase flows.

Depends on: nothing. Completed prerequisite for: E2, E3.

### E2. Contract Readiness and Run Resilience

Add operational guardrails around the current `woof wf` runner without splitting the operator surface.

Shipped:
- S1: the deterministic Stage-2.5 readiness seam - `NodeType.CONTRACT_READINESS`, `next_node` routing after `definition_closed`, `readiness-result.schema.json`, and `readiness_gate` schema/event/write support.
- S2: the full readiness matrix in `src/woof/graph/readiness.py` - machine-checkable acceptance signals (a non-deprecated contract decision related to the outcome, or an acceptance criterion that names it with a concrete signal; a bare `O<n>`/`CD<n>` mention is not a signal), non-subjective acceptance prose, contract-decision concreteness, path resolution against `git ls-files`, cheap file-based symbol resolution, and Stage-3 decomposition sufficiency. The forward-created grammar (`` `path/or/symbol` (forward-created) `` or `` `path/or/symbol` (created by ticket <id>) ``) whitelists not-yet-existing refs from the EPIC body and contract-decision notes. A deterministic checker timeout emits a non-blocking `readiness_checker_budget` warning that never blocks the gate on its own.
- S3: readiness recycle escalation - after a configured number of failed readiness cycles (default 3), the `contract_readiness` node opens an escalation-flavoured `readiness_gate` with trigger `readiness_escalation` instead of looping on `readiness_unready` indefinitely. The cycle count is derived from `readiness_gate_opened` events in the current epic attempt (since the last `epic_reset`); `definition_closed` does NOT reset the count, so it accumulates across `revise_epic_contract` retries. The threshold is configurable via `.woof/prerequisites.toml` `[readiness].escalation_threshold`. The escalated gate has `type: readiness_gate` and resolves through the same verbs as an ordinary readiness gate (`approve_with_reason`, `revise_epic_contract`, `abandon_epic`).

Remaining open work:
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
- tmux-backed long-run supervision is deferred out of E2. If revisited later, it is panes/logs/status only with no direct state mutation outside Woof commands.
- Tests covering readiness pass/fail, readiness timeout non-blocking behaviour, readiness escalation, blocker evidence resolution, command-level baseline behaviour, baseline freshness/recapture, HEAD/branch drift, and circuit-breaker decision logic.

Depends on: E1. Gate-resolution semantics live in E17, which consumes the shipped S1/S2 readiness seam and is not blocked by E2's remaining work. E16 items may batch with E2 when they touch the same files. Blocks: E3, E4, E5.

### E3. Specwright Bootstrap

Make specwright ready as the first real consumer for the production-shape eval.

Open work:
- Author `specwright/.woof/codebase/TARGET-ARCHITECTURE.md` by hand or through the `/woof` setup reference.
- Author `specwright/.woof/codebase/PRINCIPLES.md`.
- Run the `/woof` map-codebase flow against specwright to produce the AS-IS docs.
- Add `specwright/scripts/refresh-cartography` from the Python language template.
- Install the Woof post-commit hook.
- Verify preflight and Stage-2.5 readiness pass against specwright.

Depends on: E1, E2 Stage-2.5 readiness (shipped), and E17's readiness-resolution slice — a readiness failure during consumer bootstrap must be legally resolvable, not a reset-or-stuck loop. E19 and E20 gate E5, not E3; running them before E3 is the recommended order so the bootstrap smoke run exercises the production shape.

### E4. Eval Instrumentation

Measure the production shape: `/woof` as the operator surface, `woof wf` as the runner, cartography on disk, dispatched producer/reviewer work, readiness, run-resilience telemetry, drift detection, blocker-evidence checks, and JSONL audit.

Open work:
- Add `node_type` to every dispatch event so per-node attribution is possible.
- Include dispatch-return outcome fields in the manifest that are not already shipped: `error_signature`, expected artefact presence/schema status, rate-limit classification, and HEAD/branch before/after. `exit_type` and `exit_code` are already required on `subprocess_returned` and already flow into the compacted manifest.
- Include run-resilience rows for circuit-breaker decisions, no-progress/same-error counters, readiness outcomes, baseline-gate mode/outcome, blocker-evidence resolution outcomes, and drift events.
- Retain `artefacts_loaded[]` per event in any compacted manifest produced by the eval harness.
- Persist prompt and output bodies into the eval output directory at run teardown.
- Add per-node rows in the comparison markdown: calls, tokens, prompt bytes, artefact bytes, and duration.
- Add an artefact-reload section to the manifest: `{artefact_path: [(call_index, node_type, bytes)]}`.
- Keep the Python bench as the eval harness; `/woof` may run it through Bash, but evals do not require a separate skill.

Depends on: E2 for run-resilience and drift fields; E20 for route attribution. Node type, retained `artefacts_loaded[]`, prompt persistence, per-node rows, and artefact reload accounting can land independently.

### E5. Baseline Eval Run

Run the eval against specwright in the production shape and capture the first manifest as a stable baseline for future iteration.

Open work:
- Single-variant run against specwright at HEAD.
- Inspect the per-node manifest; confirm `node_type` correlation works and artefact reloads surface duplication.
- Decide the first optimisation target from the data.
- The baseline is valid only over the intended production shape: per-stage roles routable and correctly configured (E20), cartography consumption wired (E19), and the fixed discovery prompt/token shape from E21 S1-S3.

Depends on: E3, E4, E19, E20, E21 S1-S3.

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
- Add resume-to-correct: when a producer artefact fails schema validation or a deterministic check, resume the producer's captured session (the shipped `cc_session_id` / transcript reference in dispatch telemetry) with the deterministic failure evidence as feedback, instead of a cold re-dispatch. Bounded retry budget.
- Add a graded recovery ladder ahead of gate-open: narrow deterministic salvage of a recoverable-but-malformed payload (trim unfinished trailing value, drop dangling comma, close open containers; never invent values); normalisation with safe defaults (missing optional -> default, missing required -> hard fail); bounded retry (compacted payload or resume-to-correct); gate only when exhausted. Salvage and normalisation fail loud; they are not a tolerant parser.
- Tests: resume-to-correct happy path and retry-budget exhaustion; salvage of truncated payloads and hard-fail on unrecoverable ones.

Depends on: completed E7 process supervision. E8 remains a dependency for lineage-joined recovery reporting, not for the basic resume-to-correct session reference.

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
  - Add a human-supervised "build a throwaway to learn" escape hatch to `woof-brainstorm`, preferably upstream in agent-toolkit. The probe must be named throwaway, answer a stated design question, feed the answer into the brainstorm bundle, and be deleted or absorbed. Acceptance signal: the bundle records the question, probe path, result, disposition, and deletion/absorption evidence.
  - Design a scoped bug-diagnosis lifecycle: a `kind: bug` spark path, a diagnosis playbook for reproduce/minimise/hypothesise/instrument, and a handoff into the existing Stage-5 red-green-refactor fix flow. Acceptance signal: a bug spark produces a diagnosis artefact with reproduction status, minimal failing command or fixture, hypotheses, instrumentation notes, and the chosen Stage-5 story handoff.
- **P0.4 review later, lower on the backlog:**
  - Review whether to build a standalone codebase-deepening review flow off the `CURRENT-ARCHITECTURE.md` / `TARGET-ARCHITECTURE.md` delta. Treat it as a small subsystem, not a prompt tweak; defer until the cheap heuristics or baseline eval data justify it.
  - Review an on-demand "zoom out this neighbourhood" operator gesture and the lifecycle for binding repo-durable cartography to per-epic `CONTEXT.md` glossary terms.

Sequencing: preserve the current E1/E2 chain. The P0.1 hygiene items (onboarding alignment, vendored-README fix) and the P0.2 prompt-doctrine imports are complete; P0.3 starts after the readiness and structural-cartography paths are stable, and P0.4 is explicitly deferred for later review.

Depends on: E2 readiness guardrails and the structural-cartography path for P0.3; otherwise follow-on.

### E12. Structural Cartography Index

Build the ADR-009 mechanical structural index as the foundation for impact queries and later context optimisation.

Open work:
- Use tree-sitter as the required V1 extraction substrate, per the 2026-06-07 spike, ADR-009, and `docs/research/code-mapping-landscape.md`. Do not add a Python `ast` generation fallback; Woof controls the operator environment, so missing tree-sitter is an infrastructure failure.
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
- Record the spike's LSP-assisted resolution pass disposition: deferred as a candidate `HEURISTIC` -> `EXTRACTED` upgrade pass for unresolved `obj.method()` sites; decide during E13 with eval data.
- Update `woof preflight`, `woof init`, `skills/woof/references/map-codebase.md`, and architecture docs so structural indexing is opt-in until the generator is present, then enforced by the declared cartography capability.
- Tests: extractor fixtures for outlines/imports/calls; stable-ID resilience to line shifts; ambiguous-edge labelling; query CLI output; refresh idempotency; gitignore coverage; preflight behaviour when structural indexing is declared but stale, missing, or missing tree-sitter tooling.

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

### E16. Defect Sweep: Silent-Wrong-Result Fixes

Close the verified small defects where Woof silently produces a wrong result, masks a failure, or blocks itself. Every item is independently shippable; none changes a contract shape.

Shipped:
- Interim rename/copy fix: `changed_paths` keeps the porcelain destination path so staged renames no longer wedge the commit transaction, with a staged `git mv` regression test. Superseded by the open centralisation item below, which makes both rename ends scope-visible.
- Terminal-marker predicates and dispatch token parsers now ignore valid JSON non-objects (`42`, `"ok"`, `[]`) instead of crashing.
- Dogfood gate parity: `.woof/quality-gates.toml` now includes `just lint` and gives `just test` a 360s timeout. Type-checker adoption is still deferred to baseline-mode gate work.
- Boolean timeout values in `[timeouts]` are rejected instead of being accepted as `1`/`0`.
- `mark_story_status` raises `StageStateError` on an unknown story id and leaves `plan.json` unchanged.
- Cartography toolchain conformance to ADR-004: `refresh-cartography` now exits 1 with the install command when languages are declared and ctags is absent (never writing an empty index); `preflight.py` adds a fail-closed `cartography.ctags` finding when `[cartography].languages` is non-empty and ctags is not on PATH; `scripts/first-time-setup.sh` adds ctags to the check-and-instruct tier. Tests use a real PATH-stripped seam: refresh exits non-zero with ctags off PATH; the preflight finding fires with ctags absent and passes with ctags present; the setup script includes ctags.

Open work:
- Centralise git path listing and force `--no-renames` as the path-set contract. `graph/git.py` becomes the only module that lists staged/changed paths: `changed_paths`, `staged_paths`, `staged_paths_matching` (pathspec), check 2's path listing, check 7 (delete its private `_git_z`/`_staged_paths`/`_status_entries`; consume a central `status_entries()`), and check 8 all route through it with explicit `--no-renames`, so a rename is always delete + add and both ends are scope-checked. The central parser raises on R/C entries: the flag makes them impossible, so one appearing means the contract broke. Carve-out: check 2's `--unified=0` content diff keeps rename detection so a moved test file cannot satisfy `tests.count` with re-added lines; record this in the module docstring. Add an enforcement test banning raw `git status --porcelain` / `git diff --cached --name-only` invocations outside `graph/git.py` across `src/woof/graph/` and `src/woof/checks/`, and flip the rename regression test to assert both ends appear. `bench/efficiency.py` may migrate for uniformity; it is measurement, not enforcement.
- Check 6 unknown critique severity fails closed as a validation blocker; it never maps to `info`.
- Check 9 review-valve event windowing respects `epic_reset` markers; pre-reset review gates cannot suppress the valve.
- Route checks 2/7/8 through the scrubbed `graph.git` environment; remove test masking that deletes `GIT_INDEX_FILE`/`GIT_DIR` before the bug can surface; add a regression with those variables set.
- Tighten supervision cleanup and idle detection: skip `terminate_group()` after a clean exit with closed streams; reset idle on byte progress, not only completed lines.
- `woof wf --resolve` refuses when no local epic dir exists instead of cold-starting from the tracker.
- `EPIC_COMPLETE` with tracker network failure prints node outputs before the non-zero exit.
- Remove dead contract surface: `NodeType.PLAN_GATE_RESOLVE`, `NodeType.GATE_RESOLVE`, unconsumed `NodeInput.decision`, empty `playbooks/disposition/`, and the dead disposition playbook read.
- `audit.enabled=false` means no audit artefacts are committed; capture may remain in gitignored `raw/`, but unredacted output never lands in a commit.
- Codify discovery output filenames per bucket instead of accepting any non-empty `.md`.
- Move `test_trackers.py` away from in-process `subprocess.run` monkeypatching to a stub `gh` on PATH.

Depends on: nothing. Start immediately; items may batch into E2 prompts when they touch the same files.

### E17. Gate Decision Semantics

Make every accepted gate decision verb produce its documented effect, and make the advertised decision surface provably equal to the implemented one. This owns the readiness-gate resolution work moved out of E2.

Status: complete. Unblocked E3 (the readiness-resolution slice). A single canonical per-gate-type decision table (`src/woof/graph/decisions.py`) now drives the CLI `--resolve` choices, `_apply_gate_resolution_effects`, the `GateDecision` literal, the jsonl decision enum, and the operator docs; resolving with a verb not valid for the open gate is a structured error naming the valid set, and `split_story` is dropped from every surface. Readiness gates resolve through `approve_with_reason` (the unchanged epic advances to planning without re-gating), `revise_epic_contract`, and `abandon_epic`. `retry_story` resets a crashed or aborted story to `pending` and clears its executor/check/critique artefacts, guarded against story-less gates and already-terminal stories. Stories and epics gained an honest `abandoned`/`epic_abandoned` terminal, distinct from `done`/`EPIC_COMPLETE` and closing the tracker issue as not delivered, propagated to every terminal-status consumer (commit-time completion event, `observe`, the bench harness, end-of-epic detection). `revise_epic_contract` is a real channel that archives the prior `EPIC.md` and re-dispatches Definition with the prior epic plus findings as declared inputs, including cold-start tracker epics with no discovery synthesis. A decision-surface conformance test pins advertised-equals-implemented across all six surfaces.

Depends on: E2 S1/S2 (shipped). Completed; unblocked E3. Was a member of the unattended-safety gate with E18 and E22, which remain open.

### E18. Artefact Integrity And The Commit Boundary

Enforce contracts on the read side of durable artefacts and pin what verification verified to what commit commits.

Open work:
- Add strict read-side loaders for `plan.json`, `executor_result.json`, `check-result.json`, and `gate.md` front-matter. Schema-validate on read and halt structurally on violation; Pydantic models match schema constraints, including `plan.stories` min length 1. Add a conformance test banning raw durable-artefact `json.loads` outside the loader module.
- Require `epic_id` and `story_id` in `executor-result.schema.json`; cross-check both against the active story before use. Validate commit subject/body for length, line shape, and control characters.
- Split approved plan from runtime story state: `plan.json` becomes immutable after plan-gate approval; graph-owned `story-state.json` carries `status` and `empty_diff`. Approval records the plan hash; Stage-5 nodes, verification, and commit verify it byte for byte. Drift opens a `plan_drift` gate.
- Record a verified staged tree hash (`git write-tree`) plus HEAD/branch in `check-result.json`; `commit_node` recomputes before commit and invalidates stale checks on mismatch.
- Move story done/completed events after the commit exists. Resume reconciles the commit window from the commit trailer back to story state. Replace hand-built crash state tests with a real kill-based mid-commit crash-resume integration test.
- Document the trusted-local residual: `epic.jsonl` and check inputs remain producer-writable; pins close accidental and over-helpful-agent classes, not malicious local tampering.

Depends on: E16 rename fix and E17 gate semantics. Blocks the first unattended run.

### E19. Cartography Consumption

Wire the architecture's per-node loading map into dispatch payloads and playbooks, and onboard Woof itself. E1 shipped the supply side; this epic makes dispatched work actually receive the required context.

Status: complete. Dispatched work now receives its mapped context and Woof dogfoods its own cartography. S1 wired per-node payloads: each dispatch-shaped node's `inputs`/`artefacts_loaded[]` carry the mapped `.woof/codebase/` documents, the executor additionally takes the story-scoped `files.txt` slice through the shared pathspec module, and missing mapped docs halt as `incomplete_stage_state` (with the fix that stage-state halts resolve non-approvingly across gate types). S2 added playbook context discipline: every producer/reviewer playbook starts from the payload's context-document list and reads those documents first. S3 onboarded the Woof repo itself - the seven mapper-authored AS-IS docs, `scripts/refresh-cartography`, the post-commit hook, the `.gitignore` block, `.woof/test-markers.toml`, and the `[cartography]` enforcement block. S4 (operator-authored) added the design layer - `.woof/codebase/TARGET-ARCHITECTURE.md` and `PRINCIPLES.md` - so `woof preflight` enforces and passes end-to-end in the Woof repo. The `[cartography]` enforcement block and the design docs it requires were pushed in one step so `origin/main` never carried an enforce-on-but-docs-missing window.

Depends on: E1. Completed; was required before E3/E5 so the baseline measures a workflow that consumes its own context system.

### E20. Per-Stage Role Routing

Implement ADR-002's route table so the documented per-stage producer/reviewer policy is expressible, and fix Woof's own inverted Stage-5 configuration.

Status: complete. ADR-002's per-stage policy is now data. `[routes.<node_group>.<role>]` overlays sit over the base `[roles.primary]`/`[roles.reviewer]` defaults in `agents.schema.json`; `resolve_agent_route` resolves override-then-default for a given `route_key`, model profiles compose with the overlay, and `RoleRoute`/dispatch audit record `route_key` and the resolved adapter. Every graph dispatch node threads its node group (`discovery`, `definition`, `planning`, `execution`) into `_run_dispatch`, which forwards `--route-key` to the `woof dispatch` subprocess. `.woof/agents.toml` and the `woof init` template carry the default policy with an explicit `execution` override (Claude producer, Codex reviewer); the other three groups fall through to the base defaults (Codex producer, Claude reviewer). Preflight's `_check_role_routes` validates all four node groups with group-qualified `agents.<group>.<role>.route` findings, mirroring every base-role check (MCP config, MCP server command, adapter auth markers) through a shared helper so the base-role and group-route paths cannot drift; `observe` reports per-group routes alongside base roles, and the preflight floor cache version was bumped so the new checks are not masked by a stale cache.

Depends on: nothing. Completed; was a hard dependency of E5.

### E21. Dispatch Token Economy And Solo-Operator Affordances

Cut measured dispatch overhead and add small-epic affordances without weakening the gates. S1-S3 change the measured dispatch shape and land before E5; S4-S6 are independent policy work.

Open work:
- S1 playbook menu: discovery research/thinking prompts carry a name + one-line-description menu with absolute playbook paths instead of embedding all playbook bodies. Record before/after `prompt_bytes`.
- S2 dispatch overhead: cache `agents.toml` schema validation per runner invocation; record the trusted-runtime policy block once per dispatch; cache repeated plan validation by content hash.
- S3 single denial epilogue: the graph appends the canonical "do not run graph commands" epilogue to every dispatch prompt; delete divergent playbook copies.
- S4 trivial-epic tier: `woof wf new --trivial` records `tier: trivial`; `next_node` skips Stage 1 and definition reads `spark.md`. Stage 2.5 readiness and plan gate remain unchanged.
- S5 plan-gate auto-approve: opt-in `[gates] plan_auto_approve = "info"` auto-resolves plan gates whose critique severity ceiling is `info` with zero findings, recorded as audited `auto_approved`. Default remains always-gate.
- S6 brainstorm bundle reaches breakdown as guidance: breakdown payload lists the accepted brainstorm bundle paths, including `work_units[]`, without mechanically mapping work units to stories.

Depends on: S1-S3 before E5. S4-S6 can wait until after the first baseline if comparability matters.

### E22. Runner Seam Hardening

Close the weak engine seams around lock discipline, parent-side liveness, malformed-state error boundaries, and duplicated state derivation.

Open work:
- `wf reset` and `wf --resolve` acquire the same per-epic lock as `run_graph`; reset refuses while the lock is held; stale-lock takeover is atomic; add a real two-process race test.
- Add a parent-side dispatch timeout above the child supervision budget. A wedged `woof dispatch` cannot block the runner indefinitely; timeout opens a `supervisor_hang` gate.
- Add a CLI error boundary: malformed `gate.md` YAML, non-JSON check stdout, undecodable artefacts, and unexpected handler exceptions produce structured halts; `--debug` can re-raise.
- Purify `next_node` by moving `_resumable_commit_story` side effects into execution, then derive `observe` from the same runner derivation instead of maintaining a parallel state machine.
- Harden test isolation so the suite cannot mutate the developer repo or leak global state. Route every repo-touching test through a `fixture_repo()` seam; supply git identity through a hermetic git-env conftest (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`, `GIT_CONFIG_GLOBAL`, `HOME`) rather than `git config user.*` writes; add a conftest guard asserting the developer repo's `.git/config` is never modified by a test run; set `tmp_path_retention_policy = "failed"`; and add a container-run option for `just check` so the gate runs hermetically. Origin: the 2026-06-11 delivery run found the suite had escaped a tmp fixture and written a `Test`/`test@example.com` git identity into the developer repo's `.git/config`, mis-authoring real commits.

Depends on: E16 by convention. Blocks the first unattended run, with E18 (E17 complete).

### E23. Architecture Doc Truth Pass

Finish making architecture and operator docs state-honest. The rollout-note marking pass (sections 8, 9, 10, 11.5, 13, and 14, plus the implementation-plan and vault Overview wording) landed with the 2026-06-10 review follow-up; this epic carries what remains.

Open work:
- Purge ADR-005-era playbook prose ("through `woof graph next-node`", "the skill performs the producer dispatch") and consumer-unresolvable schema paths from the critique, planning, and discovery playbooks and READMEs.
- Document known limits in the architecture: Check 1 validates the worktree, not the committed tree; Check 2 proves assertion-text presence, not executed assertion identity.
- Now that E17 has settled the verb table: finish the verb-table reconciliation — write story gates as `stage: 5` with legacy `stage: 6` read-tolerated (this schema/code migration is E23's own work, explicitly deferred out of E17), and re-check section 3, section 10, and schema agreement. E17 P6 already replaced the "current limitation" wording in `SKILL.md` and `references/gates.md` with the settled table; section 10's deeper prose reconciliation remains.

Depends on: E17 for the final verb table (now settled). Mostly docs; the story-gate `stage: 5` schema migration is E23's own work, explicitly deferred out of E17.

## Settled Choices

- Runtime action safety is trusted-local plus commit-safety, audit, and drift detection. Woof does not add a preventive sandbox unless real usage proves detection is insufficient. External sandbox-orchestration tooling (e.g. sandcastle) treats the sandbox as the product; Woof deliberately places the safety boundary at commit time instead. Git worktrees, if adopted for parallel dispatch, give collision avoidance but not the isolation boundary.
- Runtime story state splits from the approved plan. `plan.json` is immutable after plan-gate approval; graph-owned runtime state belongs in `story-state.json`.
- Woof owns the graph authority in deterministic Python. LangGraph and Temporal are not adopted; their transferable concepts (explicit conditional edges, reducer-based state merge, checkpoint/interrupt/replay vocabulary) are mined into Woof's own engine rather than ceding control flow to a framework. NetworkX is adopted only as a plan-graph algorithm library (cycle and topological analysis), because it is a data structure, not an orchestrator.
- Structural cartography is an embedded mechanical index, not an orchestration graph. Default storage is SQLite under `.woof/codebase/structural/`; the only public surface is `woof cartography`. Woof does not adopt Neo4j, LadybugDB, always-on code-intel daemons, or MCP graph tools.
- Structural-index confidence is advisory. Reviewer blockers still need resolvable evidence; an `AMBIGUOUS` or `HEURISTIC` edge can point a reviewer to source, but cannot block on its own.
- Gate decisions are owned by one canonical verb table. `split_story` is dropped; split guidance travels in the resolution payload and re-enters planning through `revise_plan`.
- Enforcement git path listings (scope, manifest, transaction, drift) use explicit `--no-renames` and are centralised in `graph/git.py`: a rename is delete + add, and both ends are scope-checked. Content diffs that measure added text (Check 2) keep rename detection so moved code does not count as new.
- `audit.enabled=false` means no commit-bound audit artefacts. Raw unredacted output may be captured only under gitignored raw retention.
- E5 is a measurement of the intended production shape, not the current flawed one; E19, E20, and E21 S1-S3 precede the baseline.
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
