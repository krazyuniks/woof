# Woof Architecture

This document is the system-design source of truth. It is declarative: it describes the project as it is to be. ADRs under `docs/adr/` carry the individual design decisions; this document assembles them into a coherent architecture. The backlog at `docs/backlog.md` and the implementation plan at `docs/implementation-plan.md` describe what work remains to be done.

## 0. Scope

Woof is an inner-loop SDLC tool for AI-assisted development. The operator runs it against a consumer repository to deliver software through an agentic multi-step process: capture an epic; run discovery, definition, contract readiness, breakdown, plan-gate, and story-execution stages; dispatch producer and reviewer subagents through declared roles; verify generated work with deterministic checks; commit only through manifest-verified graph transactions; leave an auditable epic trail.

Woof assumes a Git worktree, the `.woof/` convention, its bundled schemas / playbooks / language registries, and the public CLIs declared by the consumer config. The operator's runtime is Claude Code.

## 1. Principles

- **Deterministic orchestration.** Graph transitions, gates, schema validation, and transaction manifests are deterministic Python. LLM inference is typed producer / reviewer / mapper work within the graph; no LLM picks successors.
- **State on disk is authoritative.** Filesystem state under `.woof/` is the canonical record. The orchestrator's in-memory context is opportunistic and reconstructable from disk on crash or session switch.
- **Contract-first.** JSON Schemas define artefact shape; Python code implements transitions and validation; prompt files provide producer or reviewer guidance only. Shell snippets are examples, not orchestration authority.
- **One entry point per operator task.** Three Claude Code skills cover setup, map, and run. Project-setup commands (`woof init`, `woof preflight`, `woof hooks install`) are non-interactive Python utilities used once per project. No parallel surfaces for the same job.
- **Idempotent.** Migrations, ingestion, and setup scripts are safe to replay.
- **Cartography mandatory.** Every consumer repo has `.woof/codebase/` with design, AS-IS, and mechanical layers. Preflight blocks the workflow if the prerequisite is missing.
- **Fail loud.** Missing, malformed, or unsafe state opens a gate or fails preflight. It is never silently repaired by a prompt.
- **Opinionated expert workstation.** Woof may require expert-local tooling when it materially improves supervision or correctness. tmux is allowed as a long-run monitor/supervisor, but it never owns workflow state or graph transitions.

### Guardrail taxonomy

Woof has two first-class guardrail systems. They overlap, but they are not the same.

| Guardrail | Protects | Implementation |
|---|---|---|
| Commit-safety | The repository from bad committed output. | Staged-diff checks, story-scope checks, manifest checks, reviewer blockers, lint and test execution, gate creation, final commit decisions. |
| Runtime action-safety | The host and working project while agents are running. | Currently trusted-local automation: no Woof sandbox, no command allow-list, no writable-path restriction, no network or MCP restriction layer. Commit-safety checks and gates guard what lands. Posture is documented in skill output. |

The runtime action-safety policy posture is tracked as decision OD-1 in the backlog.

## 2. Layered topology

See ADR-001 for the decision and rationale. The four layers:

| Layer | Responsibility | Implementation |
|---|---|---|
| State | Durable, schema-governed record. | Files under `.woof/`. |
| Graph | Pure deterministic transitions; schema validation; typed audit/event writes; state-token guarded mutation. | Python library at `src/woof/`, exposed via `woof graph <command>`. |
| Orchestrator | In-memory context warm across an epic; cartography slicing; subagent dispatch; gate surfacing. | Claude Code skill at `skills/woof-run/`. |
| Dispatched | Stateless, isolated LLM invocations. | `Task` subagents for Claude; `Bash + codex exec` for Codex. |

State on disk is the only authoritative record. The orchestrator and dispatched layers are reconstructable; the state and graph layers are persistent infrastructure.

### Graph command contract

`woof graph` is the skill-facing API. It is not a second operator workflow.

`woof graph next-node` is a pure state query. It returns JSON with:

- `node_type` and optional `story_id`;
- `kind`: `dispatch`, `deterministic`, `gate`, or `complete`;
- `route_key` for dispatch-shaped nodes;
- `prompt_template_path`, `expected_output_paths[]`, and `expected_output_schemas[]` for dispatch-shaped nodes;
- `loaded_docs[]` as repo-relative cartography references for the skill to load and cache;
- `allowed_decisions[]` for gate-shaped nodes;
- `state_token`, a hash over canonical state files at read time.

Mutation commands require the last observed `state_token`. A mutation fails compare-and-set if canonical state has changed. Commands take a short filesystem lock only while reading, validating, writing state, and appending typed audit events. No lock is held across LLM dispatch.

Model-produced artefacts are accepted through typed record verbs, not through a generic blob writer:

- `woof graph record-discovery-bucket`
- `woof graph record-discovery-synthesis`
- `woof graph record-epic-definition`
- `woof graph record-plan`
- `woof graph record-plan-critique`
- `woof graph record-executor-result`
- `woof graph record-story-critique`

Graph-owned deterministic outputs are not accepted through record verbs. They are produced by `woof graph run-deterministic-node <node-type>` for nodes such as `plan_gate_open`, `review_disposition`, `verification`, `commit`, and `gate_open`.

The skill has no raw `append_epic_event` command. Audit writes happen as a consequence of typed graph commands such as `record-dispatch-started`, `record-dispatch-returned`, `record-*`, `run-deterministic-node`, `resolve-gate`, and `mark-cartography-refreshed`.

## 3. Stages

Five stages plus a Stage 2.5 readiness boundary, per-story commit, and gate halts.

### Stage 1 — Discovery (locks direction)

Producer surface for open-ended exploration; reviewer critiques. Four discovery node buckets:

- `discovery_research` — produces research notes from spark.
- `discovery_thinking` — produces reasoning notes from spark + research.
- `discovery_brainstorm` — produces `ideas.md` and `options.md` from spark + research + thinking.
- `discovery_synthesis` — produces `CONCEPT.md`, `PRINCIPLES.md`, `ARCHITECTURE.md`, and `OPEN_QUESTIONS.md` under `discovery/synthesis/`.

Discovery is the only stage where the producer prompt scope is intentionally wide (full cartography load). Discovery outputs are markdown narratives; structured data lives in front-matter.

### Stage 2 — Definition (locks surface)

`epic_definition` producer reads the four synthesis files and produces `EPIC.md` with YAML front-matter. Front-matter declares `observable_outcomes[]`, `contract_decisions[]`, and `acceptance_criteria[]`. Reviewer is optional at this stage; defaults to none for a single-author epic.

### Stage 2.5 — Contract readiness (pre-plan)

Deterministic. Runs after `EPIC.md` exists and before `breakdown_planning`.

This is the first useful readiness boundary. Immediately after epic creation Woof only has `spark.md`; at that point it can check infrastructure and cartography, but it cannot prove that the epic contract has machine-checkable acceptance criteria, resolvable references, or enough contract decisions for story planning.

The readiness node validates the Stage-2 contract for:

- machine-checkable acceptance criteria;
- observable outcomes with concrete verification signals;
- contract decisions with exact paths, schema refs, API refs, or explicit forward-created markers;
- referenced existing paths and symbols that resolve against the current repository;
- absence of placeholder prose such as "good UX", "robust", or "performant" unless paired with a measurable assertion;
- enough information for Stage 3 to decompose without inventing interfaces.

Forward-created references use an exact annotation outside the cited path or symbol: `` `path/to/file` (forward-created) `` or `` `path/to/file` (created by ticket <id>) ``. Unannotated references to non-existent project paths or symbols are readiness failures.

Readiness checker timeouts are reported as non-blocking performance findings; a gate that fails only because the checker exhausted its own budget does not block the epic. Repeated failed readiness cycles escalate to the operator instead of creating an indefinite revise/fail loop.

If readiness passes, the graph proceeds to Stage 3. If readiness fails, the graph opens a `readiness_gate` and halts until the operator revises the epic contract, explicitly approves with a recorded reason, or abandons the epic.

### Stage 3 — Breakdown / Plan

- `breakdown_planning` producer reads `EPIC.md` and produces `plan.json` (validated against `plan.schema.json`).
- `plan_critique` reviewer reads `EPIC.md`, `plan.json`, and the rendered `PLAN.md`; produces `critique/plan.md` with severity classification (`info`, `minor`, `blocker`).

The producer prompt for plan generation lives at `playbooks/planning/breakdown.md`. Architecture defines the contract; the playbook owns prompt-level planning rules such as story sizing, path discipline, output limits, and the instruction not to run graph commands or select successors.

### Stage 4 — Plan gate

Deterministic. Renders `gate.md` from plan critique. Halts until the operator resolves the gate with one of:

- `approve`
- `revise_epic_contract`
- `revise_plan`
- `abandon_epic`

The graph re-enters on the operator's structured resolution.

### Stage 5 — Story execution

For each story in declared order:

- `executor_dispatch` producer (Claude — has LSP) reads `EPIC.md`, `plan.json`, story-scoped cartography (`STRUCTURE.md`, `CONVENTIONS.md`, `TARGET-ARCHITECTURE.md`, `PRINCIPLES.md`, files matching story `paths[]`), and writes the story's code and tests. Produces `executor_result.json`.
- `critique_dispatch` reviewer (Codex — independent verifier) reads the staged diff plus relevant cartography (`CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md`); produces `critique/story-S<k>.md`.
- `review_disposition` (deterministic for non-blocker; gate-open for blocker) writes `dispositions/story-S<k>.md`.
- `verification` runs the Stage-5 deterministic check matrix; produces `check-result.json`.
- `commit` (deterministic, manifest-verified) commits the story.
- On any failure: `gate_open` writes `gate.md`; the operator resolves and the graph re-enters.

There is no Stage 6. The "story gate" is a halt within Stage 5 on `gate.md` presence.

Stage-5 producer discipline is tracer-bullet red-green-refactor: for each declared outcome, write one assertion-bearing RED test before implementation, make the smallest vertical GREEN slice pass, and refactor with tests as the harness. The process explicitly rejects the horizontal-slicing anti-pattern because it tends to create the imagined-behaviour fingerprint: tests that mirror guessed data structures or setup plumbing rather than proving the declared behaviour. Verification then runs the deterministic Stage-5 check matrix, Checks 1-9.

## 4. Cartography

See ADR-004 for the decision. Per-node loading map:

| Stage / Node | Loads |
|---|---|
| Stage 1 — discovery_research | `STACK.md`, `INTEGRATIONS.md`, `CONCERNS.md` |
| Stage 1 — discovery_thinking | `CURRENT-ARCHITECTURE.md`, `STRUCTURE.md` |
| Stage 1 — discovery_brainstorm | Full set (broad ideation) |
| Stage 1 — discovery_synthesis | Full set |
| Stage 2 — epic_definition | `CURRENT-ARCHITECTURE.md`, `STRUCTURE.md`, `CONCERNS.md`, `TARGET-ARCHITECTURE.md`, `PRINCIPLES.md` |
| Stage 2.5 — contract_readiness | `CURRENT-ARCHITECTURE.md`, `STRUCTURE.md`, `CONVENTIONS.md`, `TESTING.md`, `TARGET-ARCHITECTURE.md`, `PRINCIPLES.md`; mechanical `files.txt` |
| Stage 3 — breakdown_planning | `CURRENT-ARCHITECTURE.md`, `STRUCTURE.md`, `TARGET-ARCHITECTURE.md`, `PRINCIPLES.md` |
| Stage 3 — plan_critique | `CURRENT-ARCHITECTURE.md`, `STRUCTURE.md`, `CONCERNS.md`, `TARGET-ARCHITECTURE.md` |
| Stage 5 — executor_dispatch | `STRUCTURE.md`, `CONVENTIONS.md`, `TARGET-ARCHITECTURE.md`, `PRINCIPLES.md`; story-scoped `files.txt` slice; on-demand LSP and tree-sitter |
| Stage 5 — critique_dispatch | `CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md`; staged diff |

Refresh model:

- Mechanical layer (`tags`, `files.txt`, `freshness.json`): regenerated by the post-commit hook every commit.
- Mapper docs: regenerated on demand via `/woof:map-codebase`. The skill checks freshness at epic start and prompts the operator if the map is stale.
- Design layer (`TARGET-ARCHITECTURE.md`, `PRINCIPLES.md`): human-authored; no automatic refresh.

## 5. Role routing

See ADR-002. Semantic roles: `producer`, `reviewer`, `mapper`, `gate-resolver`. Route configuration is a table keyed by `route_key` or node group. Defaults are declared for `producer` and `reviewer`; Stage 5 explicitly overrides those defaults so Claude produces code and Codex reviews it. Per-stage policy:

| Stage | Producer | Reviewer |
|---|---|---|
| 1. Discovery | Codex | Claude |
| 2. Definition | Codex | Claude |
| 2.5. Contract readiness | (deterministic) | n/a |
| 3. Breakdown / planning | Codex | Claude |
| 4. Plan gate | (deterministic) | n/a |
| 5. Story execution | Claude (LSP) | Codex |
| 5. Verification | (deterministic checks) | n/a |

Mapper subagents are Claude.

## 6. Skill suite

See ADR-005. Three operator-facing skills plus the nested target-architecture authoring skill.

| Skill | Purpose |
|---|---|
| `/woof:setup` | Onboard a new consumer repo. Invokes `woof init`, the target-architecture skill, optionally `/woof:map-codebase`. |
| `/woof:map-codebase` | Regenerate the cartography mapper documents in parallel. Refreshes the mechanical layer and writes `freshness.json`. |
| `/woof:run` | Create or resume an epic. Calls `woof graph create-epic` for a new spark, `woof graph resume-epic` for tracker-backed cold start, `woof graph next-node` for progression, dispatches producer and reviewer subagents, records typed outputs, runs deterministic nodes, surfaces gates, records resolutions. |

Skill bundles ship under `skills/woof-<name>/` in the Woof repo and install to the operator's Claude Code skill directory.

## 7. Tracker abstraction

See ADR-003. `Tracker` protocol in `src/woof/trackers/`. Two adapters ship: `github` (one issue per epic) and `local` (filesystem-only). Tracker choice is declared in `.woof/prerequisites.toml`. The skill orchestrator surfaces tracker-sync conflicts conversationally.

## 8. Schemas and contracts

JSON Schema is the canonical contract format. Implementations may use Pydantic (at schema boundaries) or dataclasses (for trusted in-process records). The schema artefact remains the portable contract.

### Tooling split

| Concern | Tool |
|---|---|
| Define contract | JSON Schema (`*.schema.json`) |
| Validate structural conformance | `ajv-cli` |
| Extract / transform JSON | `jq` |
| Cross-artefact invariants | Small script (Python / shell per complexity) |
| Generate JSON Schema from typed class | Pydantic / equivalent (per-helper choice) |

### Python data-model boundary

- **Pydantic** at schema and serialisation boundaries: graph node I/O, `plan.json`, transaction manifests, durable JSON artefacts. Pydantic is the Python runtime representation; the matching JSON Schema is the portable contract.
- **Dataclasses** for trusted in-process records: check-runner context and outcomes, preflight findings, tracker sync return values, audit summaries.

Types that cross a durable JSON, CLI, LLM-node, or consumer-facing boundary use Pydantic. Types that are internal carriers between Python functions use dataclasses.

### Schema catalogue

| Schema | Contract |
|---|---|
| `epic.schema.json` | `EPIC.md` front-matter |
| `plan.schema.json` | `plan.json` |
| `critique.schema.json` | `critique/*.md` front-matter |
| `disposition.schema.json` | `dispositions/*.md` front-matter |
| `gate.schema.json` | `gate.md` front-matter |
| `jsonl-events.schema.json` | `epic.jsonl` and `dispatch.jsonl` events |
| `agents.schema.json` | `.woof/agents.toml` |
| `prerequisites.schema.json` | `.woof/prerequisites.toml` |
| `quality-gates.schema.json` | `.woof/quality-gates.toml` |
| `test-markers.schema.json` | `.woof/test-markers.toml` |
| `language-registry.schema.json` | `languages/<lang>.toml` |
| `node-input.schema.json`, `node-output.schema.json` | Graph node I/O |
| `planning-node-input.schema.json`, `planning-node-output.schema.json` | Planning-stage node I/O |
| `readiness-result.schema.json` | Stage-2.5 contract readiness result |
| `conformance-result.schema.json` | Post-baseline contract-vs-diff conformance audit result |
| `transaction-manifest.schema.json` | Commit transaction manifests |
| `executor-result.schema.json` | Stage-5 producer output |
| `check-result.schema.json` | Verification check matrix output |

## 9. Storage layout

All runtime state under `.woof/epics/E<N>/`. Typed artefacts carry JSON Schemas. Narrative artefacts (`CONCEPT.md`, `EPIC.md`, `PLAN.md`) carry front-matter schemas where structured data lives.

JSONL event logs (`epic.jsonl`, `dispatch.jsonl`) enable crash-resume and post-hoc debugging. They reference model session transcripts or audit files; they do not duplicate raw transcripts.

**Canonical authority.** Filesystem state is canonical; `epic.jsonl` is audit. On crash-resume, if the two disagree, the filesystem wins and the JSONL is treated as incomplete.

**Audit redaction.** Commit-bound files under `.woof/epics/E<N>/audit/` are redacted before the commit transaction. Redaction strips known secret patterns. Per-file size cap defaults to 256 KB; output exceeding the cap is truncated with a footer pointing at the raw output, which lives in `.woof/epics/E<N>/audit/raw/` (gitignored). Retention beyond the local repo is the operator's responsibility.

**Dispatch telemetry.** Every `subprocess_returned` event records enough information for audit, evals, orchestrator-level runaway protection, and git-position drift detection. Required fields include `node_type`, `route_key`, `duration_ms`, `artefacts_loaded[]`, `prompt_bytes`, `artefact_bytes`, `output_bytes`, `stderr_bytes`, `exit_type`, `exit_code`, `error_signature`, `head_before`, `head_after`, `branch_before`, `branch_after`, `expected_outputs[]` with presence and schema status, and `rate_limit` metadata when the adapter exposes it. It records `tokens_in`, `tokens_out`, `cache_read_tokens`, `cache_write_tokens` when the adapter provides them. Codex dispatches also record `command_count`. `artefacts_loaded[]` contains explicit repo-relative artefact references; absolute paths, home-relative paths, and parent traversal are rejected.

## 10. Gates

A gate is a graph state recorded by `gate.md` plus a structured event in `epic.jsonl`. The graph halts on `gate.md` presence; the orchestrator surfaces the gate to the operator; resolution via `woof graph resolve-gate <verdict>` records the structured decision and removes `gate.md`.

Mandatory gates:

- **Readiness gate** after Stage 2.5 only when the epic contract is not ready for planning. Resolved with `revise_epic_contract`, `approve_with_reason`, or `abandon_epic`.
- **Plan gate** after Stage 3 plan critique. Always opens; resolved with `approve`, `revise_plan`, or `split_story`.
- **Story gate** for any Stage-5 failure, reviewer `blocker` finding, manifest mismatch, or `tracker_sync_conflict`.

Reconstitution: if `plan.json` and `critique/plan.md` exist without either an open `gate.md` or a `gate_resolved` event with `gate_type=plan_gate`, the graph synthesises the missing plan_gate.

## 11. Transaction manifests

Stage-5 commits are graph-owned transactions. Before commit:

- The producer's staged diff is computed.
- A transaction manifest enumerates the expected file set, derived from `executor_result.json` and the story's declared `paths[]`.
- If the staged set differs from the manifest, the commit is aborted and a gate is opened.
- The graph compares current HEAD and branch with the expected git position from the dispatch/verification window. Unexpected movement opens a drift gate unless the movement is explained by a graph-owned commit.

The manifest is the commit-safety boundary. The producer cannot land changes outside its declared scope.

## 11.5 Operational resilience

See ADR-006. Operational resilience wraps the graph without replacing it.

### Runaway protection

`/woof:run` observes dispatch telemetry and graph progress. It can pause a run and open a gate when a session repeats the same normalised error signature, makes no graph or git progress for a configured number of turns, or repeatedly times out without producing expected artefacts. The graph records the durable gate and state; the skill only detects the condition and invokes typed graph commands.

Progress is stage-aware:

- Stage 1-3 progress means the expected `.woof/` artefact exists and validates, or the graph state advanced.
- Stage 5 progress means expected story artefacts changed, a valid critique/disposition/check result appeared, or a graph-owned story commit advanced HEAD.

The circuit breaker tracks separate counters for consecutive no-progress turns and consecutive same-error signatures. Same-error signatures are normalised by stripping volatile paths, line/column spans, timestamps, UUIDs, and excess whitespace, preserving standalone numbers, and truncating to a bounded length. If the repeated signature indicates a newly discovered constraint, invariant, or contract gap, `/woof:run` opens a course-correction gate instead of treating the worker as merely stuck.

### Quality-gate modes

Quality gates can run in two modes:

- `strict`: any failure blocks.
- `baseline`: the first capture records an existing failure baseline; subsequent runs block only deterioration beyond that baseline.

Baseline mode is for brownfield repositories with known existing failures. It is not a bypass. Because Woof quality gates are arbitrary shell commands, the first implementation is command-level: a command that was already red can be recorded as pre-existing and reported without blocking, but Woof does not claim per-failure subtraction unless the gate declares a structured parser or machine-readable output. Baselines have both wall-clock and graph-iteration freshness metadata and can be recaptured only through an explicit operator action. Known-flake allowlists are deferred until failures have structured identities and expiry metadata.

### Reviewer evidence

Reviewer findings carry severity and evidence. A `blocker` must cite concrete evidence that resolves to the current artefacts: a file:line reference, story id, observable outcome id, contract-decision id, schema ref, or quality-gate id. Confidence is not part of the gate decision. If a future schema adds confidence, it is advisory metadata for evals and triage only.

### Git-position drift

Woof is trusted-local and does not currently prevent dispatched agents from running arbitrary commands. It therefore detects unexpected branch/HEAD movement at the graph boundary. Dispatch telemetry records branch and HEAD before and after the worker. Commit and gate paths can halt when branch/HEAD changed in a way that was not produced by a graph-owned commit.

### Conformance audit

After the first production-shape baseline, Woof should grow a deterministic conformance audit inspired by Pickle Rick's Citadel shape. The transferable idea is a contract-doc-driven audit over the production diff, not Pickle Rick's project-specific analyzers. Candidate checks include: every observable outcome has production evidence in the changed files; every contract decision's declared surface exists; declared guards were not bypassed; and consumer-supplied invariants from cartography are still respected.

### tmux supervision

Long-running `/woof:run` sessions may use tmux for panes, logs, progress dashboards, and child-process lifecycle visibility. tmux is an operator shell/supervision layer only. It does not choose graph successors, mutate `.woof/` directly, or replace on-disk graph state.

## 12. Infrastructure prerequisites

`woof preflight` is the startup infrastructure check. Two-tier configuration:

### Project-level: `.woof/prerequisites.toml`

Declares what the project needs.

```toml
[infra]
git = "2.30+"
just = "1.0+"
docker = "20.10+"
gh = "2.0+"

[commands]
claude = "any"
codex = "any"

[tracker]
kind = "github"
repo = "<org>/<repo>"

[indexing]
ctags = "5.9+"

[indexing.tree-sitter]
cli = "0.22+"
grammars = ["python", "typescript", "rust", "go"]

[lsp]
languages = ["python", "typescript", "rust", "go"]

[cartography]
staleness_floor_hours = 168
summary_min_chars = 200
```

### Tool-level: `languages/<lang>.toml`

Per-language registry of install instructions, LSP binaries, tree-sitter grammar install commands, and `refresh-cartography` template fragments. Read by `woof preflight` and `woof init`.

### Preflight enforcement

Fails closed on:

- Missing or non-executable `scripts/refresh-cartography`.
- Missing or stub `TARGET-ARCHITECTURE.md`, `PRINCIPLES.md`.
- Missing mechanical-layer files (`tags`, `files.txt`, `freshness.json`).
- Missing `ctags`, `tree-sitter`, or declared LSP binaries on PATH.
- Missing public CLI binaries (`claude`, `codex`).
- Missing tracker reachability (for hosted trackers).
- Missing or unresolvable quality-gate commands.

A stale `freshness.json` beyond `staleness_floor_hours` emits a warning with a refresh prompt; it does not block.

## 13. Operator surface

The three skills are the operator entry points for the inner loop. Python CLI utilities exist for project setup and as the engine library.

| Surface | Use |
|---|---|
| `/woof:setup` | Onboard a new consumer repo. |
| `/woof:map-codebase` | Regenerate cartography mapper documents. |
| `/woof:run` | Execute an epic. |
| `woof init` | Scaffold a fresh `.woof/` consumer config and the required `.gitignore` block. |
| `woof preflight` | Validate Woof assets, prerequisites, role routes, MCP config, tracker reachability, credential markers, language tooling, quality-gate command resolution, cartography artefact presence, and `.woof/` config schemas. |
| `woof hooks install` | Install the Woof-managed post-commit hook block without overwriting user-managed hook content. |
| `woof graph <command>` | Internal library surface used by the skill orchestrator: `create-epic`, `resume-epic`, `next-node`, typed `record-*` verbs, `run-deterministic-node`, `resolve-gate`, `record-dispatch-started`, `record-dispatch-returned`, `mark-cartography-refreshed`. |
| `woof validate ...` | Validate JSON, TOML, JSONL, and front-matter artefacts against shipped schemas. |
| `woof check stage-5 --epic <N> --story <S<k>>` | Run Stage-5 checks and emit structured results. |
| `woof render-epic` | Render `EPIC.md` structured front-matter to the managed tracker body; `--sync` pushes through the configured tracker. |

`just` recipes in the Woof repository are development conveniences. They are not authoritative orchestration surfaces.

## 14. Project layout

```
woof/
├── docs/
│   ├── architecture.md          # this file
│   ├── backlog.md               # work-to-be-done
│   ├── implementation-plan.md   # how the backlog gets executed
│   ├── adr/                     # design decisions
│   └── plans/                   # per-epic implementation plans
├── src/woof/                    # Python library + setup CLI
│   ├── graph/                   # graph transitions, validation
│   ├── cli/                     # CLI command surface
│   ├── checks/                  # Stage-5 check runners
│   ├── gate/                    # gate authoring
│   ├── trackers/                # tracker abstraction + adapters
│   ├── bench/                   # eval harness
│   └── lib/                     # shared utilities
├── schemas/                     # JSON Schema files
├── playbooks/                   # producer/reviewer prompt templates
├── languages/                   # per-language registry + refresh templates
├── skills/                      # Claude Code skill bundles
│   ├── woof-setup/
│   ├── woof-map-codebase/
│   ├── woof-run/
│   └── woof-target-architecture/
├── tests/                       # unit and integration tests
└── pyproject.toml
```

In a consumer repository:

```
<consumer>/
├── .woof/
│   ├── agents.toml              # role routes and model profiles
│   ├── prerequisites.toml       # declared dependencies
│   ├── quality-gates.toml       # declared verification commands
│   ├── test-markers.toml        # optional test-marker config
│   ├── codebase/
│   │   ├── TARGET-ARCHITECTURE.md   # human-authored
│   │   ├── PRINCIPLES.md            # human-authored
│   │   ├── CURRENT-ARCHITECTURE.md  # mapper-authored
│   │   ├── STACK.md                 # mapper-authored
│   │   ├── INTEGRATIONS.md          # mapper-authored
│   │   ├── STRUCTURE.md             # mapper-authored
│   │   ├── CONVENTIONS.md           # mapper-authored
│   │   ├── TESTING.md               # mapper-authored
│   │   ├── CONCERNS.md              # mapper-authored
│   │   ├── tags                     # ctags index (gitignored)
│   │   ├── files.txt                # git ls-files output (gitignored)
│   │   └── freshness.json           # staleness stamp (gitignored)
│   ├── epics/
│   │   └── E<N>/                # per-epic state
│   └── .current-epic            # operator marker
├── scripts/
│   └── refresh-cartography      # consumer-shippable, composed by `woof init`
└── ...                          # consumer source
```

## 15. Change control

Architecture changes that alter graph topology, stage contracts, tracker authority, runtime safety boundaries, role policy, or operator surfaces require an ADR plus matching tests.

ADRs live under `docs/adr/`. The backlog under `docs/backlog.md` lists open work; the implementation plan under `docs/implementation-plan.md` sequences it; per-epic implementation plans live under `docs/plans/<epic>.md` and are written when each epic starts.
