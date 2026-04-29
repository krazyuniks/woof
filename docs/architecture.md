# Woof

> **Purpose:** Active architecture spec for **Woof** — an inner-loop SDLC tool for AI-assisted development. Principles, architecture, contracts, and operating model.
> **Position:** Inner-loop counterpart to outer-loop / programme-level systems. Where outer-loop systems govern enterprise adoption across teams, providers, and lifecycle stages, Woof governs the developer's own AI-assisted work cycle: discovery → definition → breakdown → execution → gate, with schema-governed contracts and a JSONL audit trail per epic.
> **Evidence base:** `docs/research.md`.
> **Status:** Active. `guitar-tone-shootout` is Woof's first external consumer.
> **Rule:** All design work lives here. No parallel design docs. `.woof/` is runtime-state only.

> **ADR-001:** Stage-5 orchestration is graph-owned Python (`woof wf --epic <N>`). LLM prompts are producer nodes only.

---

## 0. Current implementation boundary

The Woof repository currently implements the ADR-001 Stage-5 execution path:

- `woof wf --epic <N>` is the operator entry point for the deterministic Python graph.
- Graph nodes dispatch the story executor, dispatch Codex critique, run Stage-5 verification, open gates, and commit through a transaction manifest.
- `.claude/commands/wf*.md` are thin wrappers or producer-node prompts. They do not own successor selection, critique dispatch, gate writing, or commits.
- Discovery, Definition, Breakdown, GitHub issue sync, and the full preflight/cartography lifecycle are implemented only where command code exists under `src/woof/cli/`; remaining work is tracked in `docs/implementation-plan.md`.

When this document conflicts with `docs/adr/001-orchestration-topology.md` or the current source under `src/woof/`, source and ADR-001 win.

## 1. Principles

1. **Epic contract is law.** User-facing observable outcomes are canonical. Implementation may bridge repo conventions; it must never replace the epic contract. (See `docs/research.md` §2.)
2. **Gates are human conversations, opened with a Context block.** Validator or critic produces structured findings → agent surfaces each with its own position → user dialogue → convergence. No auto-revision loops, no binary approval menus, no silent self-fixes. Context block: working doc, source inputs, stage, last decision.
3. **Python owns orchestration where determinism matters.** The graph is code; LLMs and humans are typed nodes. Infrastructure is selected by contract fit, not by prompt convenience.
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
- **Cross-AI critique** — multi-provider critique. Codex writes the critique document at `.woof/epics/E<N>/critique/<artefact>.md`. Claude reads and synthesises.

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
- Retention: audit files older than the epic's close timestamp + 90 days are eligible for archival via the configured archive command.

**Token usage logging.** Every subprocess return event (`subprocess_returned`) includes `tokens_in`, `tokens_out`, `cache_read_tokens`, `cache_write_tokens`, `duration_ms`, and `artefacts_loaded[]` (paths read into the prompt). In-session work logs `token_usage` events at stage transitions.

**Dispatch adapter layer.** Subprocesses are spawned via `woof dispatch <claude|codex> --role <role-name>`, not via direct calls to `cld`/`cod`. The adapter reads `.woof/agents.toml`, constructs the wrapper invocation, and emits dispatch events. This boundary stops wrapper interface drift from breaking Woof call sites.

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
| `woof wf --epic <N>` with no local dir | gh → fs | Fetch issue body; initialise `.woof/epics/E<N>/`; seed `spark.md` (title + prose); seed `EPIC.md` front-matter from structured sections if present |
| `woof wf new "<spark>"` | fs → gh → fs | `gh issue create` with stub body; capture returned number `<N>`; mkdir `.woof/epics/E<N>/`; write `spark.md`; set `.woof/.current-epic = E<N>` |
| Definition close (`EPIC.md` schema-valid) | fs → gh | Render `EPIC.md` front-matter to markdown per schema below; overwrite issue body |
| Plan gate approved | fs → gh | Append "Plan summary" section listing story IDs + titles; update body |
| Epic complete (all stories `done`) | fs → gh | Append closing summary; `gh issue close` |
| `woof wf --epic <N>` where neither local nor gh has it | — | Fail loud: "E<N> not found. Use `woof wf new \"<spark>\"` to start a new epic — gh assigns the issue number." |

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

**Implemented Stage 5 order of operations (per story):**

1. The Python graph reads `plan.json` and selects the next dependency-ready `pending` story.
2. `executor_dispatch` marks the story `in_progress` and dispatches the story-executor producer prompt. The producer writes `executor_result.json` only.
3. `critique_dispatch` dispatches Codex critique and expects `critique/story-S<k>.md`.
4. `verification` runs `woof check stage-5 --epic <N> --story <S<k>> --format json` and writes `check-result.json`.
5. `gate_open` writes `gate.md` if the executor outcome, subprocess result, or verifier result requires human review.
6. `commit` computes the transaction manifest, stages the exact expected file set, verifies the index, appends graph events, commits, and removes transient `executor_result.json` / `check-result.json`.
7. Existing `gate.md` halts at `human_review` until `woof wf --epic <N> --resolve <decision>` records the structured gate decision and removes the gate.

If a process dies during the commit transition after the plan has been marked `done` but before the git commit exists, the next `woof wf --epic <N>` run reconstitutes the interrupted transaction from `executor_result.json`, `check-result.json`, the critique, and uncommitted manifest paths. It resumes the `commit` node without duplicating durable JSONL events, then removes transient result files after the transaction is committed or after a previously committed transaction is detected.

**No auto-revision after `gate.md`.** First check is final within the block; revision authority lies with the human at Stage 6 (principle #2).

**Atomic writes.** Every structured artefact (`plan.json`, `EPIC.md` front-matter, `critique/*.md`) is written via tmp-file + `mv`. Logs (`epic.jsonl`, `dispatch.jsonl`) are appended under an advisory file lock to prevent torn writes when the driver and the story subprocess race.

**Empty-diff handling.** Some stories produce no code diff because earlier stories' broader changes already realised the outcomes. During dogfood, an empty diff opens a `story_gate` with `triggered_by: ["empty_diff_review"]` so the operator confirms the outcome was actually realised before the story is marked done. Once empirical confidence is established, this can be relaxed to auto-completion (the spec change at that point sets `empty_diff: true` and skips Check 7 for that story).

**Story commit transaction model.** Code changes and `.woof` state updates ship in one commit. This makes audit reconstruction trivial (one commit per story), keeps `git status` clean between stories, and avoids the failure mode where code commits succeed but metadata writes fail. The cost is that `git diff` for a story commit shows a mixture of code and workflow files; tooling that reads the diff must filter `.woof/` paths if it only wants code changes.

### Graph execution lifecycle

`woof wf --epic <N>` is the Stage-5 graph entry point. The graph owns story selection, dispatch, verification, gate opening, gate resolution, and commit transactions.

**Story selection.** The graph reads `plan.json`, selects the first dependency-ready `pending` story, and marks it `in_progress` before executor dispatch. Selection is deterministic: dependency readiness first, then story ID order.

**Dispatch.** The graph invokes producer nodes through `woof dispatch <claude|codex> --role <role-name>`. Producers receive structured input, write declared output artefacts, and do not choose successor nodes.

**Timeouts and crashes.** Role timeouts are configured in `.woof/agents.toml`. Timeout, non-zero subprocess exit, missing declared output, or malformed output opens a gate with a structured trigger and evidence. There is no automatic retry.

**Re-entry.** On every invocation, the graph reconstitutes state from the filesystem. Existing gates halt at `human_review`. Incomplete `in_progress` work opens a gate or requires an explicit structured reset decision; it is never silently re-executed.

**Streaming.** Human-facing stdout uses structured event lines. Durable state is recorded in `epic.jsonl` and `dispatch.jsonl`; stdout is visibility only.

**Concurrency lockfile.** The graph uses `.woof/epics/E<N>/.wf.lock` to prevent concurrent mutation of one epic. Live locks fail loud. Stale locks are removed with an audit event.

**Post-commit hook installation.** Explicit and idempotent. `just wf-preflight install-hook` appends a fenced block to `.git/hooks/post-commit`:

```bash
# >>> woof-cartography
[ -x ./scripts/refresh-cartography ] && ./scripts/refresh-cartography
# <<< woof-cartography
```

Re-running detects the fenced block and skips. User-owned hook content above and below the block is preserved. Per-worktree installation — each worktree runs preflight on first setup.

**Cartography artefacts and git.** `.woof/codebase/{tags,tree.txt,freshness.json}` are gitignored (per-worktree, regenerated by hook). `.woof/codebase/summary.md` is committed (human-authored, project-stable). Root `.gitignore` must include the three runtime artefacts.

### Codebase mapping

Cartography that serves outside-Claude consumers: deterministic gate checks, Codex critique, and fresh-session context. Inside-Claude semantic queries are handled by Claude Code's native LSP — no on-disk caching of LSP results.

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

JSON Schema is the canonical contract format. Runtime implementations may use Pydantic, zod, or shell helpers, but those implementations do not replace the schema contract.

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

**Schema versioning.** Schemas are tool-level (`schemas/*.schema.json`). Artefacts do not carry `schema_version`. The repository ships a single current schema set; breaking contract changes require explicit artefact migration in the same change that updates the schema.

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

**Config initialisation.** Preflight with no `.woof/prerequisites.toml` emits a template with `<replace>` placeholders and exits non-zero. Subsequent configs (`agents.toml`, `test-markers.toml`) use built-in defaults if absent — opt-in customisation, not required.

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

**Hard prereq.** `cld`, `cod`, `agent-sync` must be in `PATH`. Preflight verifies them. Absent → fail loud; there is no fallback to raw `claude`/`codex` invocations.

**Implementation constraints:**

- Dispatch must record a stable harness session reference or an audit-file path for every subprocess.
- Role-specific model and effort settings must be declared through `.woof/agents.toml`; command-specific flag details stay inside the dispatch adapter.

### User surface

The CLI is the operator surface. Prompt wrappers may call these commands, but they are not authoritative orchestration surfaces.

| Surface | Use |
|---|---|
| `woof wf --epic <N>` | Run the deterministic graph for the current epic. |
| `woof wf --epic <N> --resolve <decision>` | Resolve an open gate with a structured decision. |
| `woof validate ...` | Validate JSON, TOML, JSONL, and front-matter artefacts against shipped schemas. |
| `woof check stage-5 --epic <N> --story <S<k>>` | Run Stage-5 checks and emit structured results. |
| `woof dispatch <claude|codex> --role <role-name>` | Invoke configured producer subprocesses and record dispatch events. |
| `woof render-epic` | Render `EPIC.md` structured front-matter to a managed GitHub issue body. |
| `woof gate write` | Write a structured gate artefact. |

`just` recipes in this repository are development conveniences. Consumer projects may wrap the CLI, but the graph remains the source of truth.

---

## 3. Roadmap

The implementation roadmap and progress ledger live in `docs/implementation-plan.md`. Architecture changes that alter graph topology or stage contracts require an ADR.
