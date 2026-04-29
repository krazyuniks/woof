# Woof

> **Purpose:** Active architecture spec for **Woof** — an inner-loop SDLC tool for AI-assisted development. Principles, architecture, open questions.
> **Position:** Inner-loop counterpart to outer-loop / programme-level systems. Where outer-loop systems govern enterprise adoption across teams, providers, and lifecycle stages, Woof governs the developer's own AI-assisted work cycle: discovery → definition → breakdown → execution → gate, with schema-governed contracts and a JSONL audit trail per epic.
> **Evidence base:** `Workflow-Research.md` (framework evaluation, E146 lessons, rejected axioms).
> **Status:** Active. `guitar-tone-shootout` is Woof's first external consumer.
> **Rule:** All design work lives here. No parallel design docs. `.woof/` is runtime-state only.

> **ADR-001 update:** Stage-5 orchestration is now graph-owned Python (`woof wf --epic <N>`). Historical text below that describes `/wf` or `just wf-run` as the orchestrator is retained as design lineage; ADR-001 supersedes that topology for implementation.

---

## 1. Principles

1. **Epic contract is law.** User-facing observable outcomes are canonical. Implementation may bridge repo conventions; it must never replace the epic contract. (E146 lesson — `Workflow-Research.md` §2.)
2. **Gates are human conversations, opened with a Context block.** Validator or critic produces structured findings → agent surfaces each with its own position → user dialogue → convergence. No auto-revision loops, no binary approval menus, no silent self-fixes. Context block: working doc, source inputs, stage, last decision.
3. **Evolutionary rebuild; Python where it serves.** Start from the old workflow's proven strengths (`Workflow-Research.md` §1). Remove the deadlocking agent loop. Add the missing capabilities from the evaluation. The "no Python" axiom is rejected — infrastructure is a tool, not an enemy.
4. **No silent degradation.** Required infrastructure must be present at invocation. Fail loud if missing; no fallbacks.
5. **`.woof/` is runtime only.** Epic execution artefacts live at `.woof/epics/E<N>/`. System-design work does not live in `.woof/`.

---

## 2. Architecture

### Stages

Six stages plus an autonomous driver. Autonomy gradient runs from fully human-in-the-loop (Stage 1) to agent-driven with conditional human gates (Stages 5–6).

| # | Stage | Nature | Input → Output |
|---|---|---|---|
| 1 | **Discovery** | Human + AI, iterative, divergent | Vague spark → `discovery/` folder, synthesised into `CONCEPT.md`, `PRINCIPLES.md`, `ARCHITECTURE.md`, `OPEN_QUESTIONS.md` |
| 2 | **Definition** | Human + AI, convergent | `discovery/synthesis/` → `EPIC.md` |
| 3 | **Breakdown** | AI-led, human-gated | `EPIC.md` → `plan.json` + `PLAN.md` + `critique/plan.md` |
| 4 | **Plan gate** | Human conversation | Stage-3 artefacts → approved/revised plan |
| 5 | **Story execution** | Agent-driven, batch-capable | `plan.json` → diff + tests + `critique/story-S<k>.md` + commit-or-gate |
| 6 | **Story gate** | Human conversation | Triggered by `gate.md` → resolution |
| — | **Autonomous driver** | Shell loop | Iterates Stage 5 until `gate.md` halts |

### Stage 1 — Discovery (locks *direction*)

Divergent, iterative, conversational. Embeds granular thinking phases (ideate → research → brainstorm → solutionise) inside a single stage boundary, producing a folder of artefacts synthesised into commitment documents at the end.

Folder shape:

```
.woof/epics/E<N>/discovery/
  research/      # landscape, history, competitive, technical, open-source, feasibility
  thinking/      # first-principles, inversion, second-order, etc. (ad hoc)
  brainstorm/    # ideation, options-with-tradeoffs
  inputs/        # exogenous sources (KB vault refs, prior-epic refs)
  synthesis/     # CONCEPT.md, PRINCIPLES.md, ARCHITECTURE.md, OPEN_QUESTIONS.md
```

Discovery owns: philosophy, principles (epic-specific; inherits from tool-level, overrides recorded explicitly), architecture *concepts* at family-of-approaches level, technology-family choices, rejected alternatives, open questions deferred to Definition.

### Stage 2 — Definition (locks *surface*)

Convergent. Takes Discovery synthesis, resolves open questions, produces `EPIC.md`.

Definition owns: observable outcomes (user-facing verifiable truths), contract decisions (routes, schemas, data shapes, user-visible strings), acceptance criteria — everything external consumers or users depend on.

### The seam

> **Discovery locks direction. Definition locks surface.**

Direction is *reversible with pain* (swapping CRDT for OT rewrites the data layer); surface is *locked by external contract* (shipping `PATCH /api/v1/comments/{id}` binds consumers). Direction mistakes surface during implementation; surface mistakes surface during integration.

E146 was a surface failure enabled by a direction vacuum — no principle had locked "epic contract is law" as the tie-breaker. The fix requires both ends: direction-lock in Discovery (the principle), surface-lock in Definition (contract-decision table in `EPIC.md`).

### Stages 3–6 — Breakdown, gates, Story execution

**Gate asymmetry.** Plan gate (Stage 4) and Story gate (Stage 6) trigger differently:

| Stage | Gate behaviour | Why |
|---|---|---|
| 4 Plan gate | **Always opens** — even if Codex returns `severity: info` | Plans are architectural commitments; Class-2 errors at plan time cascade into N stories of rework. Mandatory review is cheap insurance. |
| 6 Story gate | **Conditional** — opens only when the 8 deterministic checks fail OR critique returns `severity: blocker` | Stories are mechanical execution. Background autonomy depends on this asymmetry. |

This makes the autonomy gradient (§2 Stages overview) concrete: humans review architectural commitments; automation handles mechanical execution.

**Gate mechanism (Stages 4 and 6).** Both gates use the same skill surface (`/wf`). Triggered by presence of `.woof/epics/E<N>/gate.md`. Skill reads gate.md, renders the pre-written Context block, surfaces findings + agent position, drives human dialogue. Human decision → skill updates referenced artefacts + deletes gate.md. Autonomous driver resumes only after gate.md is gone.

`gate.md` schema (YAML front-matter + structured prose):

```yaml
---
type: plan_gate | story_gate
stage: <stage>
story_id: <id or null>
triggered_by: [<criterion>, ...]
timestamp: <ISO 8601>
---

## Context
## Findings
## Claude's position
```

**Autonomous driver.** External shell loop shipped with the tool: `while ! test -f gate.md; do /epic-next; done`. Parses nothing from gate.md — existence check only. Decouples driver from gate semantics.

### Core loop (within a stage)

1. **Claude builds** the artefact against a typed schema
2. **Codex dispatched** with the artefact + source inputs → writes a **critique document**
3. **Claude reads** the critique, synthesises per principle #2, surfaces to user
4. Moves on — the human drives further iterations, never an agent loop

### Validation & critique

Two complementary advisory layers, both surfaced via conversational gates (principle #2):

- **Structural validation** — deterministic checks against typed artefact schemas (JSON Schema). Fast, mechanical. Produces structured findings.
- **Cross-AI critique** — multi-provider (Claude + Codex) per `Workflow-Research.md` §1 strength #3. Codex writes the critique document at `.woof/epics/E<N>/critique/<artefact>.md`. Claude reads and synthesises.

Neither auto-rejects. Neither runs in a loop. Both produce findings the agent surfaces with its own position for the human to engage with.

### Storage

All runtime state under `.woof/epics/E<N>/`. Typed artefacts carry JSON Schemas (`plan.schema.json`, `gate.schema.json`, `critique.schema.json`, `jsonl-events.schema.json`). Narrative artefacts (`CONCEPT.md`, `EPIC.md`, `PLAN.md`) have front-matter schemas where structured data lives.

JSONL event logs (`epic.jsonl`, `dispatch.jsonl`) enable crash-resume and post-hoc debugging. Complement Claude Code's session transcript; do not duplicate it.

**Canonical authority.** Filesystem state is canonical; `epic.jsonl` is audit. On crash-resume, if the two disagree (e.g., last jsonl event says `stage_3_plan_generated` but no `plan.json` exists), the filesystem wins and the jsonl is treated as incomplete.

**Mandatory gate write.** After Stage 3 plan generation completes, `gate.md` MUST be written before `/wf` returns control to the user. There is no valid filesystem state where `plan.json` + `critique/plan.md` exist without either an open `gate.md` or a `gate_resolved` event in `epic.jsonl`. Reconstitution detects the illegal state and synthesises the plan_gate that should have been opened.

**Audit-trail reconstruction.** Every dispatched subprocess records its harness session ID in `dispatch.jsonl` (`{event: "subprocess_spawned", role, story_id, harness, cc_session_id|codex_audit_path, at}`). CC subprocess transcripts live in `~/.claude/projects/<slug>/<uuid>.jsonl` natively; woof references rather than copies them. Codex output is tee'd to `.woof/epics/E<N>/audit/codex-<stage>-<scope>-<ts>.{prompt,output,meta}` because Codex CLI does not persist sessions in a standard location. `just wf-audit-bundle <E<N>>` (recipe) bundles referenced CC transcripts into `.woof/epics/E<N>/audit/claude-code/` for archival or hand-off; default mode is reference-only.

**Audit redaction and retention.** Codex prompt and output files committed under `.woof/epics/E<N>/audit/` can leak secrets, internal API output, or private issue text. Before commit:

- Each `audit/codex-*.{prompt,output}` file is run through a redaction filter that strips known secret patterns (env-var values from `env.local.sh`, JWT tokens, OAuth bearer tokens, AWS keys, `.gts-auth.json` token blobs). The filter is conservative — false positives leave a `[REDACTED:<reason>]` marker; false negatives are an audit failure.
- Per-file size cap (default 256 KB). Output exceeding the cap is truncated to the cap with a `... [truncated, full output at .woof/epics/E<N>/audit/raw/]` footer; the raw output stays in `.woof/epics/E<N>/audit/raw/` which is gitignored.
- Retention: audit files older than the epic's close timestamp + 90 days are eligible for archival via `just wf-audit-archive <E<N>>` which moves them to a separate archive path (post-MVP recipe).

**Token usage logging.** Every subprocess return event (`subprocess_returned`) includes `tokens_in`, `tokens_out`, `cache_read_tokens`, `cache_write_tokens`, `duration_ms`, and `artefacts_loaded[]` (paths read into the prompt). In-session work logs `token_usage` events at stage transitions. This is the data source for any future token-budget analysis (#171); woof captures it from day one even though the dashboard is post-MVP.

**Dispatch adapter layer.** Subprocesses are spawned via `woof dispatch <claude|codex> --role <role-name>`, not via direct calls to `cld`/`cod`. The adapter reads `.woof/agents.toml`, constructs the wrapper invocation, and emits dispatch events. This boundary stops `cld`/`cod` interface drift from breaking woof and makes the eventual standalone extraction (when woof leaves the GTS bootstrap) a matter of swapping the adapter's underlying implementation, not rewriting call sites.

### GitHub integration

**Model.** Hybrid: gh owns the epic-level contract; filesystem owns runtime. Per `AGENTS.md`, one gh issue per epic; no child issues for stories.

| Layer | Source of truth | Contents |
|---|---|---|
| gh issue body | gh | Intent prose, `observable_outcomes[]`, `contract_decisions[]`, `acceptance_criteria[]` |
| `.woof/epics/E<N>/` | filesystem | Everything else: spark, discovery, plan.json, critiques, gates, jsonl logs, story progress |

**Epic IDs.** Always the gh issue number. `E<N>` ≡ gh issue `#<N>`. No local-only epics; every epic has a gh issue. Issue numbers are assigned by gh on creation — the user does not pick them.

**Network requirement.** Woof is always-online. gh CLI must be authenticated and the declared repo accessible; `just wf-preflight` verifies `gh api /repos/<org>/<repo>` returns 200. No offline mode; no silent fallback (per §1 principle #4).

**Repo scope.** Declared once in `.woof/prerequisites.toml`:

```toml
[github]
repo = "<org>/<repo>"
```

All gh invocations pass `--repo <scope>` explicitly (consumer's project rule).

**Lifecycle — sync points.**

| Event | Direction | Action |
|---|---|---|
| `/wf E<N>` with no local dir | gh → fs | Fetch issue body; bootstrap `.woof/epics/E<N>/`; seed `spark.md` (title + prose); seed `EPIC.md` front-matter from structured sections if present |
| `/wf new "<spark>"` | fs → gh → fs | `gh issue create` with stub body; capture returned number `<N>`; mkdir `.woof/epics/E<N>/`; write `spark.md`; set `.woof/.current-epic = E<N>` |
| Definition close (`EPIC.md` schema-valid) | fs → gh | Render `EPIC.md` front-matter to markdown per schema below; overwrite issue body |
| Plan gate approved | fs → gh | Append "Plan summary" section listing story IDs + titles; update body |
| Epic complete (all stories `done`) | fs → gh | Append closing summary; `gh issue close` |
| `/wf E<N>` where neither local nor gh has it | — | Fail loud: "E<N> not found. Use `/wf new \"<spark>\"` to start a new epic — gh assigns the issue number." |

**Push policy.** Local is authoritative on push, but push is conflict-detected. Each successful push records the gh issue's `updatedAt` timestamp and the SHA-256 of the rendered body in `.woof/epics/E<N>/.last-sync`. Before the next push, `gh api /repos/.../issues/<N>` is fetched: if the remote `updatedAt` differs from the recorded value, woof opens a `gate.md` with `triggered_by: ["github_sync_conflict"]` containing a three-way diff (last-pushed body, current remote body, current local render). Resolution is via gate conversation: keep local, accept remote, or hand-merge. No silent overwrite. Worktree-level handover convention still holds: an epic is active in exactly one worktree at a time; `.last-sync` is per-worktree.

**Body rendering schema.** Deterministic transform from `EPIC.md` front-matter to gh markdown body:

```markdown
<intent paragraph — from EPIC.md front-matter `intent` field, or first paragraph of body>

## Observable Outcomes

- **O1** — <statement>
  - Evidence: <evidence[0]>; <evidence[1]>
  - Verification: <verification>
- **O2** — <statement>
  ...

## Contract Decisions

| ID | Related Outcomes | Title | Contract Reference |
|---|---|---|---|
| CD1 | O1, O2 | Comment publishing route | `openapi: spec/openapi.yaml#/paths/~1api~1v1~1shootouts~1{id}~1comments/post` |

## Acceptance Criteria

- All observable_outcomes verified by tests in diff
- Each contract decision's referenced artefact validates against its native tool (schemathesis / Pydantic / ajv-cli)

---

<!-- woof — structured sections above are rewritten on Definition/plan changes. Free-form prose above `## Observable Outcomes` is preserved on overwrite. Do not edit structured sections directly in gh. -->
```

**Preservation rule.** Content above the first structured heading (`## Observable Outcomes`) is preserved on overwrite. Everything from `## Observable Outcomes` onward is woof-owned and rewritten wholesale. The trailing HTML comment is the sentinel marking woof-managed bodies.

**Renderer.** Small helper (`scripts/render-epic-for-gh`) reads `EPIC.md`, emits the markdown body. Language per-helper decision (likely `jq` + heredoc template; upgrade to Python if templating gets gnarly). Sits alongside the preflight script.

**Push-sync failure.** Network failure or `gh api` rate limit during a scheduled push: `/wf` fails loud, exits non-zero. User retries `/wf` — skill detects local `EPIC.md` mtime newer than last successful push (stored in `.woof/epics/E<N>/.last-sync`) and re-pushes.

**Preflight runtime check.** Beyond the one-shot `just wf-preflight` check, every `/wf` invocation verifies gh reachability (`gh api /rate_limit`, ~200ms). Missing auth or unreachable API → fail loud; no silent degradation.

### Stage contracts

Each stage has a typed interface — defined inputs, defined outputs, invariants enforced at the boundary. Contracts are expressed as JSON Schema files (language-neutral) co-located with the tool; validation at boundaries catches stage-interface errors before they cascade.

| Stage | Input | Output | Boundary invariants |
|---|---|---|---|
| 1 Discovery | `spark.md`, tool-level `PHILOSOPHY.md`, `PRINCIPLES.md` | `discovery/synthesis/{CONCEPT,PRINCIPLES,ARCHITECTURE,OPEN_QUESTIONS}.md` | Non-empty problem framing; every `OPEN_QUESTION` has ID + deferral reason |
| 2 Definition | `discovery/synthesis/*` | `EPIC.md` with front-matter `observable_outcomes[]`, `contract_decisions[]`, `acceptance_criteria[]` | Every `OPEN_QUESTION` resolved or explicitly carried forward; every `contract_decision` has `epic_contract` + `resolution ∈ {epic, bridge}` (no `repo`) — E146 invariant |
| 3 Breakdown | `EPIC.md` | `plan.json`, `PLAN.md`, `critique/plan.md` | Every `observable_outcome.id` referenced by ≥1 story; every `contract_decision.id` referenced; no story-scope overlap; dependency order topologically sorted |
| 4 Plan gate | `gate.md` + Stage-3 artefacts | Revised artefacts + `gate.md` deleted, OR session terminated | Resolution action recorded in `epic.jsonl` |
| 5 Story execution | `plan.json` + `story_id` | git commit (code + `.woof` state in one transaction) + updated `plan.json` + `critique/story-S<k>.md`, OR `gate.md` | 9 deterministic checks (see "Stage 5 deterministic gate checks" below); diff ⊆ `story.paths[]` |
| 6 Story gate | `gate.md` + story artefacts | Revision + `gate.md` deleted, OR session terminated | Same as Stage 4 |

Invariants are **mechanically checkable, fail loud, no agent judgement**. Validation failure produces a `gate.md` rather than a silent proceed. Contract format is JSON Schema; Pydantic / zod / equivalents are implementations of a contract, not alternatives to one.

### Observable Outcomes contract

The spine of traceability from Definition through tests. Referenced by Stage 2 invariants, Stage 3 `satisfies[]` references, and Stage 5 Checks 2 (spec coverage) and 4 (contract fidelity).

**ID format:** sequential integers prefixed `O` (e.g., `O1`, `O2`, `O3`). Stable within an epic; decoupled from descriptive wording so renames don't break references. Contract decisions use `CD<n>`.

**Schema (lives in `EPIC.md` front-matter):**

```yaml
---
epic_id: 17
title: Shootout commenting

observable_outcomes:
  - id: O1
    statement: "Authenticated user can publish a comment on a shootout"
    verification: automated
  - id: O2
    statement: "Unauthenticated request to comment endpoint returns 401"
    verification: automated

contract_decisions:
  - id: CD1
    related_outcomes: [O1, O2]
    title: "Comment publishing route"
    openapi_ref: "spec/openapi.yaml#/paths/~1api~1v1~1shootouts~1{id}~1comments/post"
    notes: "Replaces the legacy /api/shootouts/{id}/comments/{id} route. If a transition adapter is needed, declare both paths in spec/openapi.yaml — the contract is whatever the OpenAPI doc says."

acceptance_criteria:
  - "All observable_outcomes verified by tests in diff"
  - "Every contract_decision's referenced artefact validates against its native tool (schemathesis for OpenAPI, Pydantic for pydantic_ref, ajv-cli for json_schema_ref)"
---
```

**Field semantics:**

| Field | Required | Meaning |
|---|---|---|
| `observable_outcomes[].id` | yes | `O<n>`, sequential per epic |
| `observable_outcomes[].statement` | yes | User-perspective verifiable assertion (prose) |
| `observable_outcomes[].verification` | yes | `automated` / `manual` / `hybrid` |
| `contract_decisions[].id` | yes | `CD<n>` |
| `contract_decisions[].related_outcomes` | yes | Outcome IDs this CD realises |
| `contract_decisions[].title` | yes | Short prose label for the contract |
| `contract_decisions[].openapi_ref` | conditional | JSON-Pointer-style ref into a project OpenAPI document. One of `openapi_ref` / `pydantic_ref` / `json_schema_ref` is required. |
| `contract_decisions[].pydantic_ref` | conditional | `module/path.py:ClassName` ref to a Pydantic model. |
| `contract_decisions[].json_schema_ref` | conditional | Path to a JSON Schema file. |
| `contract_decisions[].notes` | optional | Free-form prose for rationale (not contract content). |

**Why standard contract artefacts.** Surfaces that already have industry-standard contract formats (OpenAPI for HTTP, Pydantic / JSON Schema for data shapes) are referenced by their native ref form rather than re-encoded inline. Stage 5 Check 4 delegates verification to that artefact's native tooling (schemathesis, Pydantic itself, ajv-cli). woof never reinvents validation. If a surface doesn't fit any of those three artefact types, it shouldn't be a contract decision — capture it as an `acceptance_criteria` prose statement instead.

**ID immutability.** Once Definition closes, outcome and CD IDs are append-only. Wording and evidence edits are free; ID removal requires explicit deprecation via gate-conversation revision. `/wf` validates EPIC.md edits — any removed ID surfaces every `satisfies[]` reference in `plan.json` and every test marker location, requiring an explicit propagation decision. Splits (`O2 → O2a + O2b`) are not supported; use `deprecate O2; add O5 (narrower scope replacing O2)` instead. Story-level rule: pre-commit, plan.json stories are freely revisable; post-commit, stories are immutable (new work goes into new stories appended to `plan.json`).

**Traceability chain:**

| Layer | Form | Reference |
|---|---|---|
| Discovery (`CONCEPT.md`) | Prose intent | No IDs yet |
| Definition (`EPIC.md.observable_outcomes`) | Structured with `id` | IDs assigned |
| Breakdown (`plan.json.stories[].satisfies[]`) | `[O1, O2, ...]` per story | References Definition IDs |
| Test (in source) | Name / docstring / adjacent comment includes outcome ID | E.g., `def test_publish_comment_O1():` |
| Stage 5 Check 2 | Regex grep over diff | Verifies tests cover `satisfies[]` |

ID is the spine. Lose it and traceability collapses.

**Test→outcome marker convention** (any one suffices):

1. In test name: `def test_publish_comment_returns_201_O1():`
2. In docstring (first line): `"""outcomes: [O1, O2]"""`
3. In adjacent comment (≤`context_lines` above test definition): `# outcomes: [O1]`

Generic across languages — Stage 5 Check 2 is a regex grep, not a parse. Authors pick whichever fits the language idiom.

**Marker regex precision.** Word-boundary anchored to prevent substring false-positives:

```
\bO\d+\b      # outcomes
\bCD\d+\b     # contract decisions
```

Codified per-language in `.woof/test-markers.toml`; consumers override per project idiom:

```toml
[python]
test_paths = ["tests/", "src/**/test_*.py"]
marker_regex = '\bO\d+\b'
docstring_keyword = "outcomes:"
comment_prefix = "#"
context_lines = 3

[typescript]
test_paths = ["tests/", "src/**/*.test.ts"]
marker_regex = '\bO\d+\b'
docstring_keyword = "outcomes:"
comment_prefix = "//"
context_lines = 3
```

Default config ships with woof for python + typescript; consumers extend for rust, go, etc. Codex critique provides the semantic safety net — verifies each marker's test actually asserts the named outcome (catches "marker present but test asserts something else"); flags as `severity: minor` in critique.

**Cross-epic traceability.** IDs are scoped per-epic (`O1` in E17 ≠ `O1` in E22). No cross-epic ID linkage. If E22 builds on E17's surface unchanged, E22 doesn't declare a CD for it (stable contract; consumed without modification). If E22 modifies the surface, that's a new CD in E22 (`E22.CD1`) — same surface string, distinct epic-scoped ID. Cross-epic queries like "all epics touching `POST /api/v1/comments/{id}`" run against the surface string, not via ID graph.

### Stage 3 Breakdown prompt philosophy

What `EPIC.md → plan.json` embeds. The skill produces a plan that satisfies Stage-3 contract invariants (every outcome covered, every CD implemented, no scope overlap, deps topologically sorted) and that Stage 5 can iterate over story-by-story.

**`plan.json` shape:**

```yaml
epic_id: 17
goal: <one-sentence prose>
stories:
  - id: S1
    title: <prose>
    intent: <prose, 1–2 sentences: what this story produces>
    paths:                                 # git-pathspec globs the story may touch
      - "webapp/api/comments.py"
      - "tests/api/test_comments.py"
    satisfies: [O1, O2]                    # outcome IDs covered
    implements_contract_decisions: [CD1]   # CDs this story is the surface creator for
    uses_contract_decisions: []            # CDs this story consumes (no implementation)
    depends_on: [<story IDs>]
    tests:
      count: 4                             # estimate, not lock-in
      types: [unit, integration]           # families
    status: pending | in_progress | done
```

`PLAN.md` is a deterministic render of `plan.json` — no authoring at this layer.

**Prompt rules:**

1. **Outcome-driven granularity.** Each story realises 1–3 related outcomes. Group by shared concern or dependency. Reject zero-outcome stories or all-outcome stories.
2. **Path discipline.** Each story declares the glob patterns it is allowed to touch via `paths[]`. Stage 5 Check 3 fails if the diff includes files outside the declared globs. Stories that must share a file declare overlapping globs explicitly; the planner should flag overlap so the operator decides whether to split, merge, or accept the shared edit.
3. **Explicit dependencies.** Inferred deps (S2 modifies a file S1 creates) are declared in `depends_on[]`. Implicit deps are a planning bug.
4. **Contract ownership.** Every `contract_decision` in `EPIC.md` appears in exactly one story's `implements_contract_decisions[]` (one-to-one ownership of the surface creator). Other stories that consume the CD list it under `uses_contract_decisions[]`. Stage 5 helper validates this invariant.
5. **No implementation pseudocode.** Stories declare *what* they produce (outcomes covered, surfaces created), not *how*. Implementation is Stage 5's job; planning predicts intent.
6. **Test surface estimation, not enumeration.** Each story declares `tests.count` (estimate) and `tests.types` (families). Specific test names emerge during execution.
7. **Right-sized stories.** Heuristic: each story fits ~30–40k tokens of agent work. Roughly 5–10 files touched, 3–10 tests, 200–800 LOC. Above the upper bound → split. Well below → consider merging.
8. **Self-validation before Codex dispatch.** Skill validates `plan.json` against `plan.schema.json` + cross-refs before dispatching Codex. Cap internal iteration at 2 attempts; if still invalid, write `gate.md`. (Intra-skill structured work, not agent-to-agent — distinguish from the deadlock pattern.)
9. **Codex critique focus.** Dispatch prompt asks Codex specifically to evaluate: outcome coverage, decomposition quality (over/under), scope hygiene, dependency correctness, contract-decision implementation completeness, missed Class-2 (architectural) concerns. Severity scale: `info` / `minor` / `blocker`.
10. **Plan gate is mandatory.** Stage 4 always opens — no auto-approve at plan stage (see Gate asymmetry above).

**The prompt forbids:**
- Pre-writing implementation code or pseudocode
- Pre-naming specific variables / classes / signatures
- Predicting every test in advance
- Auto-revising after Codex blocker (one-shot critique)
- "Catch-all" stories that bundle unrelated outcomes

### Stage 5 deterministic gate checks

Nine checks, derived from a failure-class taxonomy. Checks 1–8 run after the story's inner sequence completes; Check 9 is the periodic-review valve and runs on a cadence (every-N stories and at end-of-epic). Checks operate against repo HEAD plus staged-but-uncommitted state, not just the diff (a contract surface created by S1 and committed earlier is still present in HEAD when S3 runs). Failures collate into `gate.md.triggered_by[]`.

**Failure classes:**

| Class | Failure | Detection |
|---|---|---|
| A | Build / lint / type / test broken | Project quality-gate command exits non-zero |
| B | Built the wrong thing (story spec uncovered) | No test references some `outcome_id` in `story.satisfies[]` |
| C | Path discipline broken (creep, wrong files) | Diff touches files outside `story.paths[]` globs |
| D | Epic contract violated (E146-class) | Some `contract_decisions[].(openapi|pydantic|json_schema)_ref` artefact validates as broken or implementation drifts from the referenced contract |
| E | Plan integrity broken | `plan.json` invalid against schema or cross-refs |
| F | Cross-AI critique flags blocker | `critique/story-S<k>.md` front-matter `severity == blocker` |
| G | Story incomplete / not commit-ready | Working tree dirty, no staged changes, or status not `done` |
| H | Docs drift (per project convention) | Touched code path has no corresponding doc-path touch |
| I | Accumulated minor critique findings | Sum of `severity: minor` findings across recent stories warrants holistic review |

**Checks (one per class):**

| # | Class | Mechanism | Tooling |
|---|---|---|---|
| 1 | A | Each gate command in `.woof/quality-gates.toml` exits 0 within its declared timeout | shell |
| 2 | B | Every `outcome_id` in `satisfies[]` has an asserting test reachable in the diff (test-name / docstring / adjacent comment, per `.woof/test-markers.toml`) | jq + grep; helper |
| 3 | C | `git diff --name-only --staged` ⊆ `story.paths[]` globs (matched via git-pathspec) | shell + git pathspec |
| 4 | D | For every CD with `implements_contract_decisions` ownership in this story: the referenced artefact is present, parses, and the implementation conforms to it. Tooling: `schemathesis run` for OpenAPI; Pydantic import + model resolution for `pydantic_ref`; `ajv-cli` validate-self for `json_schema_ref` and validate-fixtures where provided. Runs against repo HEAD + staged. | external native validators |
| 5 | E | `plan.json` validates against `plan.schema.json`; cross-refs (`satisfies[]` ⊆ `observable_outcomes[].id`, both `*_contract_decisions[]` arrays ⊆ `contract_decisions[].id`, every CD owned by exactly one story, `depends_on[]` ⊆ `stories[].id`); status coherence | `ajv-cli` + jq + helper |
| 6 | F | `critique/story-S<k>.md` exists; front-matter validates against `critique.schema.json`; top-level `severity` equals max severity over `findings[]`; `severity != blocker` | `ajv-cli` + helper |
| 7 | G | `git diff --staged` non-empty AND staged paths match `story.paths[]` AND `.woof/epics/E<N>/{plan.json,critique/story-S<k>.md,epic.jsonl}` are also staged AND `git status --porcelain` shows nothing unstaged outside scope. Honours `empty_diff` (see below). | shell |
| 8 | H | If `.woof/docs-paths.toml` defines `code_pattern → doc_pattern` mappings, touched code triggers required doc paths in same diff. No-op when file absent. | helper |
| 9 | I | After every N completed stories (configurable in `.woof/agents.toml.review_valve.every_n_stories`, default 5) AND once before epic close (`review_valve.end_of_epic = true`), open a `review_gate` summarising the cumulative `severity: minor` findings since the last review. Resolution decision via standard taxonomy (approve / revise_plan / split_story / etc.). | helper |

**Stage 5 order of operations (per story):**

1. Skill reads `plan.json`, picks next story (`status != done`).
2. Inner sequence (Claude works in-session):
   a. Code (files within `story.paths[]`).
   b. Tests (asserting `satisfies[]` outcomes).
   c. Refactor.
   d. Continuous validate — quality-gate command must be green before proceeding.
3. Dispatch Codex critique → `critique/story-S<k>.md`.
4. Update `plan.json.stories[k].status = done`, append story_completed event to `epic.jsonl`.
5. Stage the story commit transaction: `git add` the code paths under `story.paths[]` PLUS `.woof/epics/E<N>/plan.json` PLUS `.woof/epics/E<N>/critique/story-S<k>.md` PLUS `.woof/epics/E<N>/epic.jsonl`. The story commit is one transaction containing both the code change and its workflow-state update.
6. Run Checks 1–8 against staged state; if the story closed an every-N boundary or is the last pending story, also run Check 9.
7. **All pass:** commit (single commit per story) → exit success (driver loops to next).
8. **Any fail:** write `gate.md` with full `triggered_by[]` + Context block + findings + Claude's position → no commit → exit (driver halts).

**No auto-revision after `gate.md`.** First check is final within the block; revision authority lies with the human at Stage 6 (principle #2).

**Atomic writes.** Every structured artefact (`plan.json`, `EPIC.md` front-matter, `critique/*.md`) is written via tmp-file + `mv`. Logs (`epic.jsonl`, `dispatch.jsonl`) are appended under an advisory file lock to prevent torn writes when the driver and the story subprocess race.

**Empty-diff handling.** Some stories produce no code diff because earlier stories' broader changes already realised the outcomes. During dogfood, an empty diff opens a `story_gate` with `triggered_by: ["empty_diff_review"]` so the operator confirms the outcome was actually realised before the story is marked done. Once empirical confidence is established, this can be relaxed to auto-completion (the spec change at that point sets `empty_diff: true` and skips Check 7 for that story).

**Story commit transaction model.** Code changes and `.woof` state updates ship in one commit. This makes audit reconstruction trivial (one commit per story), keeps `git status` clean between stories, and avoids the failure mode where code commits succeed but metadata writes fail. The cost is that `git diff` for a story commit shows a mixture of code and workflow files; tooling that reads the diff must filter `.woof/` paths if it only wants code changes.

### Autonomous driver lifecycle

The driver (`just wf-run`) is a Bash loop that spawns one `cld -p` subprocess per pending story until a `gate.md` appears or all stories are `done`. The skeleton is simple; the failure-mode plumbing below is non-negotiable.

**Story selection.** Read `plan.json`; pick first `status: pending` story whose `depends_on[]` are all `status: done`. Tie-break on `id` ascending (deterministic, replayable). Mark `status: in_progress` before dispatch; on subprocess success the inner skill sets `done`; on failure see crash recovery below.

**Subprocess dispatch.** Driver constructs invocation per `agents.toml.roles.story-executor`; example:

```bash
timeout "${timeout_min}m" \
  cld -p "<bootstrap-prompt>" -- --model "${model}" \
  > >(tee -a "${log}") \
  2> >(tee -a "${log}" >&2)
child_pid=$!
```

Bootstrap prompt is minimal (~200 tokens):

```
You are executing story S<k> in epic E<N>.
Read in order:
  1. .woof/.current-epic (verify E<N>)
  2. .woof/epics/E<N>/plan.json (find S<k>)
  3. .woof/epics/E<N>/EPIC.md (front-matter for outcomes / contract decisions)
  4. CLAUDE.md / AGENTS.md (project conventions)
Then invoke /wf:execute-story.
```

The Stage 5 inner sequence playbook lives in the `/wf:execute-story` skill body, not in the dispatch prompt.

**Subprocess timeouts.** `agents.toml.roles.<role>.timeout` (default 30 min). Driver wraps with `timeout` command. Fire → kill subprocess → write `gate.md` with `triggered_by: ["timeout"]`.

**Subprocess crash (non-zero exit).** No retry — per principle #2 ("no auto-revision"). Driver reverts story `status: in_progress` → `pending`, writes `gate.md` with `triggered_by: ["subprocess_crash"]` and exit code in front-matter, exits.

**SIGINT propagation.** Driver traps SIGINT/SIGTERM, sends INT to child, waits for clean exit, records the interruption to `dispatch.jsonl`, exits 130. Without explicit propagation, Bash kills the loop while orphaning the child subprocess.

**Driver-loop exit detection.** Three terminal states distinguished by filesystem:

| Exit | Signal | Parent `/wf` action |
|---|---|---|
| `gate.md` exists | Halt | Stage 6 conversation |
| All stories `status: done` | Success | Push gh "epic complete", `gh issue close`, celebrate |
| Story `status: in_progress` + no gate | Driver crashed | See "Re-entry from in_progress" below |

**Re-entry from `in_progress`.** State after driver crash: plan.json story marked `in_progress`, possibly dirty git tree, possibly half-staged files, possibly partial `critique/story-S<k>.md`. `/wf` reconstitution detects this and prompts the human:

> S<k> was in progress when the driver exited. Discard work and reset to `pending`, or open a story_gate to review the partial state?

On reset: `git restore --staged <files>; git checkout -- <files>`; revert plan.json status; clear partial critique. On gate: synthesise `gate.md` with `triggered_by: ["incomplete_subprocess"]` and the partial-state inventory, drive Stage 6.

No automatic re-execution.

**Driver streaming UX.** One structured line per story event to stdout, surfaced into the parent `/wf` conversation as Bash tool result:

```
[wf-run E17] S3: starting (cld claude-sonnet-4-6, no MCPs, timeout 30m)
[wf-run E17] S3: done in 4m12s (commit abc1234)
[wf-run E17] S4: starting
[wf-run E17] S4: gate fired (triggered_by: scope_violation, critique_blocker) — driver halted
```

Durable record is `dispatch.jsonl`; stdout summary is for human visibility only.

**Concurrency lockfile.** Mandatory at `.woof/epics/E<N>/.wf.lock`. Format:

```json
{"pid": 12345, "invoker": "wf-run", "started_at": "2026-04-25T14:32:11Z", "host": "<hostname>"}
```

Written on entry; checked on entry. If present and PID alive → fail loud ("Another woof process holds the lock; PID 12345 since 14:32. Wait or kill it."). PID not alive → stale; auto-cleanup with warning. Released on clean exit. Both `/wf` and `just wf-run` honour the lockfile.

**Post-commit hook installation.** Explicit and idempotent. `just wf-preflight install-hook` appends a fenced block to `.git/hooks/post-commit`:

```bash
# >>> woof-cartography
[ -x ./scripts/refresh-cartography ] && ./scripts/refresh-cartography
# <<< woof-cartography
```

Re-running detects the fenced block and skips. User-owned hook content above and below the block is preserved. Per-worktree installation — each worktree runs preflight on first setup.

**Cartography artefacts and git.** `.woof/codebase/{tags,tree.txt,freshness.json}` are gitignored (per-worktree, regenerated by hook). `.woof/codebase/summary.md` is committed (human-authored, project-stable). Root `.gitignore` must include the three runtime artefacts.

### Codebase mapping

Cartography that serves outside-Claude consumers (deterministic gate checks, Codex critique, fresh sessions bootstrapping fast). Inside-Claude semantic queries are handled by Claude Code's native LSP — no on-disk caching of LSP results.

**Stack:**

```
.woof/codebase/
  tags              # ctags universal index, post-commit hook
  tree.txt          # git ls-files, post-commit hook
  summary.md        # human-authored, LLM-scaffolded once
  freshness.json    # {ts, git_ref}
```

**Static artefacts** (file-based):

- `tags` — symbol → file:line index via `ctags -R --output-format=u-ctags`
- `tree.txt` — gitignore-aware file enumeration via `git ls-files`
- `summary.md` — human-curated architecture overview; LLM scaffolds template + seed data on first run, human authors prose; tool never re-touches
- `freshness.json` — staleness metadata (`{ts, git_ref}`)

**Runtime tooling** (no on-disk artefact):

- **Tree-sitter** — on-demand structural queries via `tree-sitter parse` for cross-file syntactic walks (route wiring, scope precision, decorator chains). Multi-language uniform query interface.
- **Claude Code native LSP** (v2.0.74+) — in-session semantic depth (types, refs, hover) via CC's plugin model; transparent to skills, automatic during code reading/editing.
- **Codex critique** — covers the ~5% of verifications that require semantic judgement Tree-sitter can't deterministically express (cross-AI second opinion; not a deterministic gate check).

**Refresh:** post-commit git hook regenerates `tags` + `tree.txt` + `freshness.json`. ~1s for typical repo. `summary.md` is human-only; tool never modifies. LSP results stay in-process; never cached to disk.

**Why Tree-sitter and not AST:** Tree-sitter gives multi-language uniform queries with one CLI; AST per-language tooling adds 4× operational footprint without coverage gain for our verifications (syntactic checks). AST would earn its place if RAG-style code embedding chunking is added — defer until then.

**Why on-disk static + runtime semantic:** outside-Claude consumers (gate checks, Codex) need file-readable artefacts; Claude's in-session reasoning has LSP transparently. Caching semantic info to disk would silently degrade as code changes — LSP servers cache internally; we don't reproduce that.

### Implementation

**Skill-first.** Primitives live as Bash invocations from skill prompts until a specific primitive fails a readability stress-test. Compiled helpers earn their keep one at a time; language-of-helper is a per-helper decision, not a global one.

**Tooling split:**

| Concern | Tool | Language |
|---|---|---|
| Define contract | JSON Schema (`*.schema.json`) | Neutral |
| Validate structural conformance | `ajv-cli` | Node/npx |
| Extract / transform JSON | `jq` | Neutral |
| Cross-artefact invariants (e.g., route-coverage, outcome-coverage) | Small script | Python / TS / shell per complexity |
| Generate JSON Schema from typed class (optional convenience) | Pydantic / zod / equivalent | Per-helper choice |

`ajv-cli` ≠ `jq` — they solve different problems (validation vs. extraction). Use both, not interchangeably.

JSON Schema files from the deleted `workflow/schemas/` (git history, commit `38bac9d9^`) are reusable language-agnostically. Python modules from the deleted `workflow/` tree are *not* assumed to be resurrected.

**Standalone, opinionated, portable.** Woof assumes `just`, Docker, GitHub, worktrees, and the `.woof/` convention. Does *not* assume an existing project — Stages 1–2 support blank-project starts. `guitar-tone-shootout` is Woof's first external consumer.

### Infrastructure prerequisites (hard-gated)

§1 #4 made operational. Preflight runs at every Woof invocation; missing prerequisite → exit non-zero with concrete install commands inline. No partial-mode fallback.

**Two-tier configuration.**

**Project-level** (`.woof/prerequisites.toml`) — declares *what* the project needs:

```toml
[infra]
docker = "20.10+"
just = "1.0+"
git = "2.30+"
gh = "2.0+"

[wrappers]                                # required dispatch wrappers
cld = "any"                               # Claude wrapper with MCP control
cod = "any"                               # Codex wrapper with rules+memory injection
agent-sync = "any"                        # CC → Codex/Gemini config sync (used by cod)

[github]
repo = "<org>/<repo>"                     # required; verified via gh api at every /wf invocation

[indexing]
ctags = "5.9+"

[indexing.tree-sitter]
cli = "0.22+"
grammars = ["python", "typescript", "rust", "go"]   # subset per project

[lsp]
languages = ["python", "typescript", "rust", "go"]
```

**Tool-level language registry** (ships with Woof — `woof/languages/<lang>.toml`) — declares *how* to install per language:

```toml
# woof/languages/python.toml
[lsp]
binary = "pyright"
binary_install = "npm install -g pyright"
plugin = "pyright-lsp@claude-plugins-official"
plugin_install = "claude plugin install pyright-lsp@claude-plugins-official"
gotchas = [
  "Configure in pyproject.toml [tool.pyright]",
  "Virtualenv: set venvPath",
  "Monorepo: use executionEnvironments",
]

[tree-sitter]
grammar_install = "npm install -g tree-sitter-python@latest"
verify_snippet = "def f(): pass"
verify_scope = "source.python"
```

```toml
# woof/languages/rust.toml
[lsp]
binary = "rust-analyzer"
binary_install = "rustup component add rust-analyzer"
plugin = "rust-analyzer-lsp@claude-plugins-official"
plugin_install = "claude plugin install rust-analyzer-lsp@claude-plugins-official"
gotchas = [
  "Initial workspace indexing can take minutes on large workspaces",
  "Significant memory footprint",
  "For proc-macros: rust-analyzer.cargo.runBuildScripts = true",
  "Exclude target/ from indexing",
]

[tree-sitter]
grammar_install = "npm install -g tree-sitter-rust@latest"
verify_snippet = "fn f() {}"
verify_scope = "source.rust"
```

Adding a new supported language = add a TOML to `woof/languages/`.

**Preflight contract.**

For each declared prereq, in order:

1. Binary in PATH (`command -v <binary>` exit 0)
2. Version meets floor (parse `<binary> --version`, semver compare)
3. Per Tree-sitter grammar: parse `verify_snippet` with `verify_scope`; success = grammar working
4. Per LSP plugin: `claude plugin list | grep <plugin>`

ANY failure → exit non-zero with structured output (install commands + gotchas inline). The preflight output IS the per-language documentation — no separate setup docs maintained.

**Worked failure output:**

```
[INFRA PREFLIGHT FAILED — 3 missing prerequisites]

✗ tree-sitter CLI
  Required: 0.22+ (latest preferred)
  Install:  npm install -g tree-sitter-cli@latest

✗ pyright (Python LSP)
  Install:  npm install -g pyright
  Plugin:   claude plugin install pyright-lsp@claude-plugins-official
  Notes:
    - Configure in pyproject.toml [tool.pyright]
    - Virtualenv: set venvPath
    - Monorepo: use executionEnvironments

✗ tree-sitter grammar: rust
  Install:  npm install -g tree-sitter-rust@latest
  Notes:
    - Initial workspace indexing can take minutes
    - For proc-macros: rust-analyzer.cargo.runBuildScripts = true
    - Exclude target/ from indexing

Re-run `woof preflight` after installing.
```

**Version policy:** floor-with-latest-preferred per global rule (latest stable releases unless specifically pinned for compatibility). `just upgrade-prereqs` recipe bumps everything to current latest.

**Preflight caching.** Two-tier:

1. **Floor checks** (binaries exist, version meets floor, LSP plugin installed, Tree-sitter grammars parse) cached at `.woof/.preflight-floor` keyed by SHA256 of `.woof/prerequisites.toml` + language-registry TOML contents. Skipped if hash unchanged and `verified-at < 24h`. Force re-run via `just wf-preflight --force`.
2. **Runtime checks** (gh auth + reachability via `gh api /rate_limit`, Codex auth) cached at `.woof/.preflight-runtime` for 5 min, with a rate-remaining safety margin (`> 100` reqs/hr). Stale → re-verify; fail loud on auth expiry with exact re-auth command. Subprocesses inherit parent's runtime cache via stat()-based checks; no fresh network calls per `claude -p`.

Both cache files are gitignored.

**Schema versioning.** Schemas are tool-level (`woof/schemas/*.schema.json`). Each artefact (`plan.json`, `gate.md`, `critique/*.md`) carries a `schema_version` field in front-matter. Validation chooses the schema by version; tool ships with current schema set plus prior versions for migration. Migration tooling (`just wf-migrate-schemas E<N> v1 v2`) is post-MVP.

**`.woof/` commit policy.**

| Layer | Tracked in git | Rationale |
|---|---|---|
| `spark.md`, `discovery/`, `EPIC.md`, `plan.json`, `PLAN.md`, `critique/`, `audit/codex-*` | Yes | Durable narrative + audit; reproducible epic history |
| `epic.jsonl`, `dispatch.jsonl` | Yes | Post-hoc debugging requires the event stream |
| `summary.md` (codebase) | Yes | Human-authored architectural overview |
| `gate.md`, `.wf.lock`, `.last-sync`, `.current-epic` | No | In-flight runtime / per-worktree |
| `tags`, `tree.txt`, `freshness.json` (codebase) | No | Per-worktree, regenerated by post-commit hook |
| `.woof/.preflight-*` | No | Local cache |

Required `.gitignore` entries (consumer adds at first setup):

```gitignore
.woof/.current-epic
.woof/epics/*/gate.md
.woof/epics/*/.wf.lock
.woof/epics/*/.last-sync
.woof/codebase/tags
.woof/codebase/tree.txt
.woof/codebase/freshness.json
.woof/.preflight-*
```

**Cross-worktree epic activity.** An epic is active in exactly one worktree at a time. `.woof/` is per-worktree by convention; cross-worktree handover happens via gh issue (the canonical contract), not by copying `.woof/`. No mechanical enforcement; document-level rule.

**Config bootstrapping.** `just wf-preflight` on first run, with no `.woof/prerequisites.toml` present, emits a template with `<replace>` placeholders and exits non-zero. Subsequent configs (`agents.toml`, `test-markers.toml`) use built-in defaults if absent — opt-in customisation, not required.

### Agent role configuration

Roles in the woof pipeline are configurable per-project via `.woof/agents.toml`. Each role declares the dispatch wrapper, model, MCP set, and pass-through flags. Woof constructs the full invocation dynamically — no hard-coded model IDs, no shell aliases.

| Role | Invoked for | Default mechanism | Configurable |
|---|---|---|---|
| Orchestrator | `/wf` driving conversation | User's interactive CC session (no subprocess) | No — runs where user is |
| Planner | Generating `plan.json` at Stage 3 | In-session orchestrator (no subprocess) | Yes — can dispatch via `cld` to subprocess |
| Story executor | Stage 5 per-story subprocess | `cld -p "<prompt>"` (default model, no MCPs) | Yes |
| Critiquer | Codex critique at Stage 3 (plan) and Stage 5 (story) | `cod "<prompt>"` (default model, no MCPs, with rules+memory preamble) | Yes |
| Gate-resolver | Synthesising critique + surfacing position in gate conversation | In-session orchestrator | No — must share human's session |

**Config schema (`.woof/agents.toml`):**

```toml
# harness: cld | cod
# model: harness-specific ID, passed through to underlying CLI
# think: bool, cld only — toggles extended thinking via -t
# mcp: array of MCP server names — empty = no MCPs (token-saving default)
# flags: arbitrary additional pass-through args (after the wrapper's own options)

[roles.planner]
harness = "cld"
model = "claude-opus-4-7"
think = true
mcp = []

[roles.story-executor]
harness = "cld"
model = "claude-sonnet-4-6"
think = false
mcp = []

[roles.critiquer]
harness = "cod"
model = "gpt-5.4-codex"
mcp = []
```

**MVP defaults if `agents.toml` absent:**

- planner: in-session (no subprocess); inherits user's session model
- story-executor: `cld -p "<prompt>"` — default model, no MCPs, no thinking
- critiquer: `cod "<prompt>"` — default model, no MCPs (rules+memory preamble injected by `cod`)

**Dispatch construction.** Woof's role resolver reads `agents.toml` and emits invocations:

```
# planner with above config
cld -t -- --model claude-opus-4-7 -p "<prompt>"

# story-executor
cld -p "<prompt>" -- --model claude-sonnet-4-6

# critiquer
cod -- --model gpt-5.4-codex "<prompt>"
```

The `--` separator hands subsequent flags to the underlying CLI; the wrapper consumes only its own options.

**Wrapper guarantees relied on:**

- `cld` defaults to **zero MCP servers** when no `-m` flag is passed (token discipline by default)
- `cod` injects `<project-root>/.claude/rules/*.md` + the project's auto-memory (first 200 lines of `MEMORY.md`) into every Codex prompt — Codex critiques receive project context for free
- `cod` runs `agent-sync --quiet` first, mirroring CC skills/rules/commands into `~/.codex/` so woof's playbooks are visible to Codex on every invocation
- Both wrappers pass `--dangerously-skip-permissions` (CC) / `-s danger-full-access -a never` (Codex) automatically — fits trusted-automation context for Stage 5

**Hard prereq.** `cld`, `cod`, `agent-sync` must be in `PATH`. `just wf-preflight` verifies. Absent → fail loud (no fallback to raw `claude`/`codex` invocations). Standalone-extraction post-dogfood: woof vendors simplified copies of the wrappers under its own `scripts/`.

**Implementation uncertainties to verify at MVP start:**

- Whether `cld -p` paired with `--output-format stream-json` exposes the CC session UUID in a parsable form (needed for audit linkage; see Storage section).
- Whether Codex CLI 0.121.0 supports an `--effort` flag, or whether effort must be embedded in the prompt body. Either way, `flags = ["..."]` in `agents.toml` carries it.

### User surface

Three commands total. The skill drives all sequencing conversationally; the user remembers nothing beyond the entry point.

| Surface | Use |
|---|---|
| `/wf` | Single slash command. Reads `.woof/epics/E<N>/` state, drives conversation for whatever stage is next — Discovery sub-phases, Definition, Breakdown, gate review, status. |
| `just wf-run` | Autonomous driver for Stage 5. Shell loop that spawns fresh `claude -p` per story until a `gate.md` appears. |
| `just wf-preflight` | Hard-gate infrastructure check (per "Infrastructure prerequisites" above). Run once at setup. |

**No flag-soup, no menus.** Discovery sub-phases (`research`, `brainstorm`, `synthesise`) live inside `/wf`'s conversation, not on the command line. The skill suggests a next move based on current `.woof/` state; the user redirects with a word; it proceeds. Single questions, never multiple-choice prompts.

**Single CC session throughout.** The user stays in Claude Code:

1. **Stages 1–4** (Discovery → Definition → Breakdown → Plan gate): all inside `/wf` conversation. No shell switch.
2. **Stage 5 autonomous execution:** `/wf` shells out via Bash to the driver loop. Each story spawns a fresh `claude -p` subprocess so the parent session's context doesn't blow up; the parent waits on Bash with zero token consumption.
3. **Stage 6 gate firing mid-execution:** Bash loop exits → `/wf` returns control with the gate conversation, in the same session.
4. **Gate resolution → loop resumes** automatically.

`Ctrl-Z` is never needed. The only reason to use a separate terminal is if the user explicitly wants to detach the Stage-5 driver into a `tmux`/screen pane and walk away — optional, not required.

---

## 3. Open Questions

All major architectural questions resolved (see Resolved). No residual design questions.

### Implementation tasks (not architectural)

**Done:**

- [x] **Path rename** — `workflow/`→`woof/`, `.workflow/`+`.planning/`→`.woof/`, `<woof>/`→`woof/`. Wiki commit `503585a` (2026-04-25).
- [x] **JSON Schemas** — 11 schema files at `woof/schemas/`: `plan`, `gate`, `critique`, `jsonl-events`, `epic`, `prerequisites`, `agents`, `test-markers`, `language-registry`, `quality-gates`, `docs-paths`. Single-version (no `schema_version` field). Cross-artefact invariants deferred to Stage-5 helpers.
- [x] **Spec rewrite per cod_report (2026-04-25)** — contract refs (openapi/pydantic/json_schema), story scope as glob list, contract-decision split (implements vs uses), GitHub conflict detection, story commit transaction, periodic review valve, gate decision taxonomy, audit redaction, token logging, dispatch adapter layer.

**Pending:**

- [ ] **E146 contract-fidelity fixture** — first dogfood test: epic spec says `POST /api/v1/comments` with `body`; repo convention is different. Plan that substitutes the repo convention must fail Stage 5 Check 4. Single most important regression test.
- [ ] Implement `woof validate` command (validates EPIC.md, plan.json, critique/*.md, gate.md against schemas via ajv-cli).
- [ ] Write `woof dispatch <claude|codex> --role <role-name>` adapter (reads `.woof/agents.toml`; constructs `cld`/`cod` invocations; tees Codex output to `.woof/epics/E<N>/audit/`; captures token usage into JSONL).
- [ ] Write `scripts/render-epic-for-gh` helper (`EPIC.md` front-matter → gh issue body markdown per deterministic schema; SHA256 + `updatedAt` capture for conflict detection).
- [ ] Author `woof/languages/{python,typescript,rust,go}.toml` registry entries.
- [ ] Draft the `/wf` skill prompt — state-machine + conversational sequencing (gates inline; no separate gate skill).
- [ ] Draft the `/wf:execute-story` subprocess skill — Stage-5 inner sequence with the 9 deterministic checks.
- [ ] Port taches-derived Discovery playbooks into `skills/wf/playbooks/{think,research}/*.md` (copy-with-attribution; wrap with preconditions + persist sections).
- [ ] Author root `ACKNOWLEDGEMENTS.md` with blanket MIT attribution for adapted taches content.
- [ ] Write the autonomous driver shell loop (`just wf-run`) and the post-commit cartography hook.
- [ ] Dogfood: bootstrap woof's own first epic using these designs (the dogfood epic IS the workflow tool itself, post-extraction-prep).

### Backlog (post-MVP)

- **Log-analysis tooling.** Token accounting per stage/role (prompt + completion separately; cache-read vs cache-write). Turn-count distribution per stage. Playbook utilisation (which thinking/research models actually get invoked). Re-dispatch frequency. Gate-cycle wall-clock (open → resolve, agent thinking time vs human dwell). Model/effort A/B comparisons (does Opus-max planning yield fewer story-gate failures than Sonnet-medium?).
- **KB-search integration.** Personal Obsidian-based deep-search system at `~/Work/obsidian-personal/docs/` (under development). When ready, a new `research-search` role in `agents.toml` lets `playbooks/research/*.md` dispatch queries to it for grounded research instead of conversational synthesis.
- **Standalone extraction vendor step.** When woof splits into its own repo, vendor simplified copies of `cld`/`cod` under `scripts/` so the tool stops depending on user dotfiles. Maintain interface compatibility with the user's wrappers so consumers using either work transparently.
- **Effort-flag CLI migration.** When `claude` / `codex` CLIs expose `--effort` (or equivalent) directly, deprecate the prompt-prefix workaround and switch `agents.toml.flags` to use the flag form.
- **GSD decision-categorisation enrichment.** Extend `EPIC.md.open_questions[]` schema with `resolution: locked | deferred | discretion` field if Definition-stage ambiguity surfaces during dogfood.
- **AST-based scope verification.** Tree-sitter currently handles syntactic checks at Stage 5 Check 4. Upgrade to per-language AST tooling only if RAG-style code-embedding chunking is added to Codebase mapping.

### Resolved (2026-04-18 → 2026-04-19)

- **Stage model** — six stages + autonomous driver; Option-C-intent-inside-Option-B-boundary for Stages 1–2.
- **Discovery/Definition seam** — direction vs surface; confirmed via CRDT/OT, OAuth, pgmq worked examples.
- **Brainstorming boundary** (previously OQ2) — absorbed into Discovery Stage 1 `discovery/thinking/` sub-folder; thinking-model skills (taches-style) invoked as needed.
- **Python resurrection commitment** — rejected; skill-first with per-helper language decisions; JSON Schemas reused language-agnostically.
- **Per-stage typed contracts** — adopted. JSON Schema as canonical contract format; `ajv-cli` for structural validation; small helpers for cross-artefact invariants (language per-helper). Pydantic / zod / equivalents are implementations of a contract, not alternatives.
- **Stage 5 deterministic gate checks** — eight checks derived from a failure-class taxonomy (build/test, spec coverage, scope, contract fidelity, plan integrity, critique blocker, completion, docs drift). All run against staged-but-uncommitted state; failures collate into `gate.md.triggered_by[]`; no auto-revision.
- **Observable Outcomes contract** — `O<n>` sequential IDs; lean schema (`statement`, prose `evidence[]`, `verification`); `contract_decisions[]` separate with `CD<n>` IDs (E146 invariant: `resolution ∈ {epic, bridge}`, no `repo`); test→outcome marker convention (3 forms accepted); ID is the traceability spine across Discovery → Definition → Breakdown → tests.
- **Stage 3 Breakdown prompt philosophy** — outcome-driven granularity (1–3 outcomes per story); exhaustive non-overlapping scope; explicit deps; one-to-one contract-decision ownership; no implementation pseudocode; test surface estimated not enumerated; ~30–40k token story heuristic; self-validation before Codex (≤2 internal iterations).
- **Plan-gate vs story-gate asymmetry** — plan gate always opens (Class-2 prevention); story gate conditional (autonomy enabler).
- **Codebase mapping** — cartography stack on disk (ctags + tree.txt + summary.md + freshness.json); Tree-sitter on-demand for structural queries; Claude Code native LSP for in-session semantic depth (no on-disk caching); post-commit hook refreshes static artefacts; AST deferred until RAG layer (if ever); Codex critique covers semantic 5% gap.
- **Infrastructure prerequisites (hard-gated)** — two-tier config (project declares languages, tool registry declares install commands + gotchas); preflight runs at every invocation, fails loud with inline install instructions; preflight output IS the per-language documentation; floor-with-latest-preferred version policy.
- **User surface** — three commands total: `/wf` (single conversational entry, drives all stages and gates from `.woof/` state), `just wf-run` (autonomous Stage-5 driver), `just wf-preflight` (one-time infra check). No flag-soup, no menus; sub-phases sequenced inside `/wf` conversation. Single CC session throughout; autonomous loop runs as Bash subprocess that doesn't consume parent context. `Ctrl-Z` never needed; `tmux` detach optional.
- **`/wf` multi-epic re-entry** — per-worktree `.woof/` scope; `/wf` enumerates `.woof/epics/E*/` on invocation, classifies each by filesystem signature, priority-orders (open gate > mid-execution > approved-pending-execution > mid-plan > mid-definition > mid-discovery > spark > complete), auto-selects highest-priority epic with mtime tie-break, announces full active set to user. `.woof/.current-epic` marker is an optimisation, not authority; filesystem state is canonical. `/wf E<N>` explicit override; `/wf new "<spark>"` creates a new epic.
- **Discovery as brainstorm meta-stage; playbooks pattern** — Stage 1 Discovery is a single conversational meta-stage; thinking/research/brainstorm are sub-phases invoked inside `/wf`'s conversation, not separately-registered skills. Content lives as on-demand playbook files under `skills/wf/playbooks/{think,research,brainstorm}/*.md` that `/wf` reads via Read tool when the conversation calls for them. Keeps the Skill tool autocomplete uncluttered (one `/wf` entry only; `/wf:execute-story` is the sole sibling skill, used only by the Stage 5 subprocess). Discovery playbooks authored from taches-derived prose (12 thinking + 8 research) copied with blanket MIT attribution in `ACKNOWLEDGEMENTS.md`; output captured by `/wf` and written to `.woof/epics/E<N>/discovery/<subphase>/<model>-<topic-slug>.md`. Topic-slug filenames; overwrite on same-topic re-application. Superpowers-inspired convergence-gate language embedded directly in `/wf` prompt (not a separate playbook). GSD decision-categorisation deferred post-v1 as a schema enrichment on `EPIC.md.open_questions[]` if Definition ambiguity proves painful in dogfood.
- **GitHub integration** — gh issue owns the epic-level contract (intent prose, `observable_outcomes[]`, `contract_decisions[]`, `acceptance_criteria[]`); filesystem owns runtime (everything else). Unified IDs (`E<N>` ≡ gh issue `#<N>`; no local-only epics; gh assigns numbers). Always-online; `gh` + repo access verified on every `/wf` invocation, not only at `just wf-preflight`. Local is authoritative on push; issue body overwritten wholesale via deterministic markdown render from `EPIC.md` front-matter; free-form prose above `## Observable Outcomes` preserved. No conflict detection (operator responsibility). Sync points: pull at `/wf E<N>` cold-start, push at Definition close, plan-gate approval (append), epic complete (append + close issue).
- **Default-supported languages** — `python | typescript | rust | go`. Each has a `woof/languages/<lang>.toml` shipped with woof (LSP binary + install, plugin + install, Tree-sitter grammar + verify snippet/scope, gotchas). Consumer's `.woof/prerequisites.toml` declares a subset. `just wf-preflight` verifies: binary in PATH, version floor, Tree-sitter grammar parses verify_snippet, LSP plugin present.
- **Token discipline & session hygiene** — `/wf` reconstitutes state from filesystem on every invocation; reads only the artefacts the current stage requires (not cascades). Playbooks load progressively during Discovery only. Natural `/clear` boundaries at stage transitions (Discovery → Definition most important). Stage 5 subprocess (`claude -p`) is auto-isolated; parent session's context is irrelevant. No `/clear` needed mid-stage — only at stage boundaries.
- **Agent role configuration** — `.woof/agents.toml` declares dispatch wrappers (`cld` for Claude, `cod` for Codex), models, MCP sets, and effort flags per role (orchestrator / planner / story-executor / critiquer / gate-resolver). Woof builds invocations dynamically; no hard-coded model IDs or shell aliases. MVP defaults if absent: in-session planner, `cld -p` for story executor, `cod` for critiquer, all with empty MCP set (token-saving). `cld`/`cod`/`agent-sync` are hard prereqs in `PATH`. `cod`'s preamble injects project rules + auto-memory into Codex prompts automatically, so critiques see project context without coordination.
- **Audit-trail reconstruction** — dispatch logs (`dispatch.jsonl`) record CC session UUIDs and Codex audit-file paths; CC transcripts referenced in-place at `~/.claude/projects/<slug>/<uuid>.jsonl`; Codex output tee'd to `.woof/epics/E<N>/audit/codex-*.{prompt,output,meta}` because Codex CLI lacks standard persistence. `just wf-audit-bundle` archives referenced CC transcripts on demand.
- **Autonomous driver lifecycle** — `just wf-run` is a Bash loop with mandatory plumbing: per-role `timeout` (default 30 min) → `gate.md` on fire; non-zero subprocess exit → revert `in_progress`→`pending` + `gate.md` (no retry); SIGINT propagation to child + clean exit; driver-exit detection distinguishes gate / all-done / crash by filesystem state; `/wf` re-entry from `in_progress`+no-gate prompts human (reset or review-gate, never auto-re-execute); structured stdout streaming surfaces story events to parent conversation; mandatory lockfile at `.woof/epics/E<N>/.wf.lock` (PID-aware stale detection); explicit idempotent post-commit hook install via `just wf-preflight install-hook` (fenced block preserves user content); cartography runtime artefacts (`tags`, `tree.txt`, `freshness.json`) gitignored; `summary.md` committed. Atomic plan.json writes via tmp+mv. Empty-diff stories complete via `empty_diff: true` flag without a commit.
- **Traceability spine maintenance** — outcome and CD IDs are append-only post-Definition; ID removal requires explicit deprecation via gate revision; `/wf` lists every reference (plan.json `satisfies[]` + test markers) on EPIC.md edits affecting IDs. Plan-level: pre-commit stories freely revisable, post-commit stories immutable (new work → new stories). Marker regex word-boundary anchored (`\bO\d+\b`); per-language config in `.woof/test-markers.toml`. No cross-epic ID linkage; surface-string queries cover cross-epic traceability. Codex critique semantically verifies each marker's test asserts the named outcome (covers regex-blind cases as `severity: minor`).
- **Operational discipline** — preflight two-tier cached (floor 24h via prereq-hash, runtime 5min for gh/codex auth); subprocesses inherit parent runtime cache. Schemas tool-level (single version, no `schema_version` field in artefacts — see 2026-04-25 entry). `.woof/` commit policy: durable narrative+audit committed, runtime/lock/sync/cartography-runtime gitignored. Cross-worktree handover via gh issue only (epics active in exactly one worktree at a time). Bootstrap: prerequisites.toml template emitted on first preflight; agents.toml + test-markers.toml use built-in defaults.
- **Tool name** — **woof** (Workflow Orchestrator Flow). Slash command `/wf`, just recipes `wf-run` / `wf-preflight`. Repo creation deferred as a separate extraction exercise after first dogfood run.

### Resolved (2026-04-25 — post-cod_report rewrite)

- **Contract decisions reference standard contract artefacts.** Replaces the earlier `epic_contract: string` / `repo_convention` / `resolution: epic|bridge` / `bridge` model. Each CD now declares one of `openapi_ref`, `pydantic_ref`, or `json_schema_ref` pointing at the project's existing contract artefact. Stage 5 Check 4 delegates verification to that artefact's native tooling (schemathesis, Pydantic, ajv-cli) rather than reinventing surface-grep. CDs that don't fit any of those three artefact types should be expressed as `acceptance_criteria` prose instead. The E146 invariant becomes "the implementation conforms to the referenced contract artefact"; "bridges" (parallel canonical + legacy routes) are encoded by declaring both paths in the OpenAPI doc.
- **Plan story scope = git-pathspec globs.** `scope.{create,modify}` collapsed into a single `paths[]` array of glob patterns. Stage 5 Check 3 verifies the diff's touched files are subset of the declared globs. Drops the create/modify split (the planner doesn't always know which path will be created vs modified; git tells you at diff time anyway).
- **Plan stories split contract-decision ownership from consumption.** Each story declares `implements_contract_decisions[]` (one-to-one ownership: the surface creator) and `uses_contract_decisions[]` (consumers; any number). Stage 5 Check 5 helper validates that every CD in `EPIC.md.contract_decisions[]` is implemented by exactly one story.
- **Story commit transaction.** Code changes and `.woof` state updates ship in one commit per story. Stage 5 stages `story.paths[]` plus `.woof/epics/E<N>/{plan.json, critique/story-S<k>.md, epic.jsonl}` as one transaction; atomic for audit reconstruction and crash recovery.
- **GitHub conflict detection.** Pre-push, woof fetches the gh issue's `updatedAt`; if it differs from `.last-sync`, opens `gate.md` with `triggered_by: ["github_sync_conflict"]` and a three-way diff. Replaces the earlier "blind overwrite, operator responsibility" rule.
- **Periodic review valve (Stage 5 Check 9).** Conditional story-gate asymmetry preserved, but a new periodic-review valve opens a `review_gate` every N completed stories (configurable, default 5) AND once before epic close. Surfaces accumulated `severity: minor` critique findings before they compound into architectural rot. Replaces "story gates only fire on blockers".
- **Empty-diff stories open a gate during dogfood.** Empty-diff completion is no longer auto-success; until empirical confidence is established, an empty diff opens a `story_gate` with `triggered_by: ["empty_diff_review"]` for operator confirmation that the outcome was actually realised.
- **Gate decision taxonomy.** `gate_resolved` and `*_gate_resolved` events carry a structured `decision` field with enum values: `approve`, `revise_epic_contract`, `revise_plan`, `revise_story_scope`, `split_story`, `abandon_story`, `abandon_epic`. Replaces free-form `reason` prose, which becomes audit context only.
- **Stage 5 Check 4 operates on repo HEAD + staged.** Surface presence is verified against the full repo state at story-end, not just the diff. A surface created by S1 and committed earlier is still present in HEAD when S3 runs.
- **Test-markers config restructured.** `.woof/test-markers.toml` now nests language blocks under a top-level `languages:` map (was patternProperties at root, which collided with future top-level keys). Schema fix.
- **In-session-only roles enforced in schema.** `agents.toml` schema constrains `orchestrator` and `gate-resolver` to `harness: in-session`. The other three roles (planner, story-executor, critiquer) accept `cld` / `cod` / `in-session`.
- **`agent-sync` is a required wrapper prereq.** Aligned with prose. `prerequisites.schema.json` `wrappers` block requires all three of `cld`, `cod`, `agent-sync`.
- **Trigger names align with schema enum.** All `triggered_by[]` values in driver UX, prose, and examples use the `check_<n>_<class>` form matching `gate.schema.json` (e.g., `check_3_scope`, not `scope_violation`).
- **Stale `truth.id` term removed.** All references replaced with `outcome_id` to match the schema.
- **Single-version schemas, no `schema_version` field.** `woof/schemas/v1/` collapsed into `woof/schemas/`. Artefact front-matter no longer carries `schema_version`. F2 schema-versioning model dropped; future breaking changes will be handled by manual artefact migration.
- **Two extra schemas authored.** `quality-gates.schema.json` and `docs-paths.schema.json` cover Stage 5 Check 1 and Check 8 configs.
- **Stub `/epic-gate` reference removed.** Gates are inline within `/wf` (the only public skill). No separate gate skill.
- **Audit redaction + size cap.** Codex prompt and output files committed under `.woof/epics/E<N>/audit/` are redacted (env-var values, JWTs, OAuth tokens, AWS keys, T3K auth blobs) before commit. Per-file size cap 256 KB; raw output overflows to gitignored `.woof/epics/E<N>/audit/raw/`.
- **Token-usage logging from day one.** Every `subprocess_returned` event records `tokens_in`, `tokens_out`, cache reads/writes, duration_ms, and `artefacts_loaded[]`. In-session work logs `token_usage` events at stage transitions. The dashboard is post-MVP; the data capture is not.
- **Dispatch adapter layer.** Subprocesses are spawned via `woof dispatch <claude|codex> --role <role-name>` rather than direct `cld`/`cod` calls, so wrapper interface drift doesn't break woof and standalone extraction is a one-file swap.

### Evaluation residuals

Defaults stated but not yet confirmed through implementation:

- **S3** (JSONL audit + crash-resume) — schemas reused; implementation skill-first
- **S4–S7** (hooks, worktrees, testing, auto-teardown) — already kept; confirm no regression
- **G2** (context / token awareness) — separate stream (#171)
- **G3** (debugging) — separate stream (#168)
- **G5** (extensibility) — taches installed ephemerally; don't reinvent

---

## Appendix — Related GitHub Issues

- **#165** — Parent epic (augment capabilities)
- **#163** — Cleanup stale references
- **#168** — Debugging skill
- **#170** — Per-story code review (two-stage)
- **#171** — Context / token awareness
- **#174** — Wire discovery → planning (wording reflects contaminated design; revisit after v2 architecture lands)
- **#175** — Codebase mapping (Open Question 4)
- **#176** — Observable Outcomes (Open Question 3)
- **#179** — Standalone investigation tools (v2)

Issue bodies will be updated after the v2 architecture is concrete.
