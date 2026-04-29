---
description: Drive an epic through woof's discovery → definition → plan → execute → gate workflow.
allowed-tools: Bash(just:*), Bash(./woof/bin/woof:*), Bash(gh:*), Bash(git:*), Bash(test:*), Bash(ls:*), Bash(cat:*), Bash(jq:*), Read, Edit, Write, Glob, Grep
argument-hint: "[<gh-issue-#> | new]"
---

# /wf — woof workflow orchestrator

You are the in-session orchestrator for woof. Drive the active epic through stages 1–6 by reading filesystem state, deciding the next step, and surfacing gates conversationally to the user.

The canonical design lives at `wiki/Workflow.md`. This command is the operational front-end. Treat the design doc as authoritative; if this prompt and the doc disagree, the doc wins and you flag the divergence to the user.

## Tools you will invoke

- `./woof/bin/woof validate <path>` — schema-check artefacts before writing.
- `./woof/bin/woof render-epic --epic <N> [--sync]` — render `EPIC.md` → gh issue body; `--sync` pushes with conflict detection.
- `./woof/bin/woof dispatch <claude|codex> --role <name> --epic <N> [--story <Sk>]` — spawn cld/cod for a role declared in `.woof/agents.toml`.
- `gh api /repos/<owner>/<repo>/issues/<N>` — read remote issue state.
- `git` — commit story transactions (code + `.woof` state in one commit).

## Preflight (every invocation)

Before doing anything else:

1. `test -d .woof || echo "no .woof/ — run 'just wf-preflight install' first"` — woof bootstraps need a `.woof/` directory with `prerequisites.toml`. Halt if missing.
2. `gh api /rate_limit -H "Accept: application/vnd.github+json"` — confirm gh is authenticated and reachable. Fail loud on non-200; never silently degrade.
3. Read `.woof/prerequisites.toml` `[github].repo` — every gh call needs this scope.
4. Confirm exactly one `.woof/.current-epic` file exists, OR resolve the epic from `$ARGUMENTS` (gh issue number, or the literal `new`).

If preflight fails, surface the exact failing command + remediation to the user and stop.

## Reconstitution

Given `E<N>` (the active epic), inspect `.woof/epics/E<N>/` and decide the current stage. Filesystem is canonical; `epic.jsonl` is audit only.

| Filesystem state | Stage |
|---|---|
| No `.woof/epics/E<N>/` directory | Stage 1 — Discovery (or epic creation if `$ARGUMENTS = new`) |
| `discovery/synthesis/{CONCEPT,PRINCIPLES,ARCHITECTURE,OPEN_QUESTIONS}.md` exist; no `EPIC.md` | Stage 2 — Definition |
| `EPIC.md` exists; no `plan.json` | Stage 3 — Breakdown |
| `plan.json` + `critique/plan.md` exist; `gate.md` present | Stage 4 — Plan gate (open) |
| `plan.json` exists; no open `gate.md`; stories `pending` remain | Stage 5 — Story execution |
| `gate.md` with `triggered_by` from a story or check | Stage 6 — Story gate (or review/conflict gate) |
| All stories `done`; no `gate.md` | Epic complete — push gh "epic complete" + close issue |

If a story has `status: in_progress` and there is no `gate.md`, the driver crashed mid-story. Ask the user whether to reset (`pending`) or open a story gate with `triggered_by: ["incomplete_subprocess"]` and the partial-state inventory. No automatic re-execution.

## Stage 1 — Discovery

Inputs: `spark.md` (the operator's framing), tool-level `wiki/Workflow.md` `PHILOSOPHY` and `PRINCIPLES` blocks.
Outputs: `.woof/epics/E<N>/discovery/synthesis/{CONCEPT,PRINCIPLES,ARCHITECTURE,OPEN_QUESTIONS}.md`.

Run the discovery playbooks (see `.woof/playbooks/discovery/` once installed; until then, conduct discovery inline asking one question at a time per the orchestrator's brainstorming protocol). Every `OPEN_QUESTION` must have an ID and a deferral reason; surviving questions either resolve in Stage 2 or are explicitly carried forward.

Do not skip ahead to Stage 2 until all four synthesis files exist and have non-empty problem framing.

## Stage 2 — Definition

Author `EPIC.md` with YAML front-matter conforming to `woof/schemas/epic.schema.json`. The structured header captures:

- `epic_id` (gh issue number; integer)
- `title`
- `observable_outcomes[]` — `{id: O<n>, statement, verification: automated|manual|hybrid}`
- `contract_decisions[]` — `{id: CD<n>, related_outcomes[], title}` plus exactly one of `openapi_ref` / `pydantic_ref` / `json_schema_ref` (Stage 5 Check 4 verifies via the artefact's native tooling — schemathesis / Pydantic / ajv-cli — woof never reinvents validation)
- `acceptance_criteria[]`

Validate before writing: `./woof/bin/woof validate .woof/epics/E<N>/EPIC.md`. Re-edit until valid.

Then push to gh: `./woof/bin/woof render-epic --epic <N> --sync`. Conflict (exit 3) opens a sync gate — see Gates below.

After Definition closes, outcome IDs and contract-decision IDs are append-only. Edits require a gate-driven revision; deprecation marks the entry and adds a replacement with a new ID. Splits are not supported (deprecate + add new).

## Stage 3 — Breakdown

Dispatch the planner via `/wf:plan`:

```
./woof/bin/woof dispatch claude --role planner --epic <N> --prompt-file <bootstrap>
```

The bootstrap prompt is short — tell the dispatched cld to read `.woof/.current-epic`, `EPIC.md`, `plan.schema.json`, and CLAUDE.md/AGENTS.md, then invoke `/wf:plan <E<N>>`. The skill body at `.claude/commands/wf/plan.md` carries the planner contract (outcome coverage, scope hygiene, right-sizing, forbidden patterns).

Planner output: `.woof/epics/E<N>/plan.json` validated against `woof/schemas/plan.schema.json`. On planner failure, the dispatched subprocess writes `gate.md` and exits non-zero; you surface the gate.

Then dispatch Codex for the plan critique using `woof/playbooks/critique/plan.md` as the prompt template:

```
./woof/bin/woof dispatch codex --role critiquer --epic <N> \
    --prompt-file woof/playbooks/critique/plan.md
```

The critique writes to `.woof/epics/E<N>/critique/plan.md` (front-matter conforming to `critique.schema.json`).

## Stage 4 — Plan gate (always opens)

Stage 4 always opens a `plan_gate` — there is no auto-approve at plan stage. Synthesise:

- The plan critique findings (Codex output)
- Your own reading of the plan against the EPIC contract
- Your position (recommend approve / revise / split / abandon)

Surface to the user. Decision taxonomy (record on resolution into `epic.jsonl` `plan_gate_resolved.decision`): `approve` / `revise_epic_contract` / `revise_plan` / `revise_story_scope` / `split_story` / `abandon_story` / `abandon_epic`.

On `approve`: append the resolved event, delete `gate.md`, proceed to Stage 5.
On any revision decision: re-enter the appropriate earlier stage; do not auto-revise.

## Stage 5 — Story execution

Driven by `just wf-run`. Per story, the driver dispatches `cld -p` invoking `/wf:execute-story` (sibling skill, task 6). After each story commit, the driver runs the 9 deterministic gate checks; on failure, writes `gate.md` and exits to Stage 6.

In-session, do not reimplement Stage 5. Either:

- Hand off to the driver (`just wf-run`) and wait, OR
- For dogfood / debugging only, run a single story directly via `/wf:execute-story` and verify behaviour.

The Stage 5 gate checks are: build/lint/type/test (1), outcome coverage in tests (2), contract-decision implementation completeness (3), contract artefact validation (4 — schemathesis / Pydantic / ajv-cli), no scope creep beyond `paths[]` (5), dependency satisfaction (6), non-empty diff (7) [empty diff opens a story_gate during dogfood], story commit transaction integrity (8), and the periodic-review valve (9 — every-N stories and end-of-epic).

## Stage 6 — Story gate (and other in-flight gates)

Triggered by any failing gate check, `subprocess_crash`, `timeout`, `incomplete_subprocess`, `empty_diff_review`, `github_sync_conflict`, or the periodic review valve. `gate.md` records `triggered_by[]`, the triggering Context block, findings, and your synthesised position.

Resolve conversationally. On revision: re-enter the appropriate stage. On `approve` of an empty-diff or review gate: append `gate_resolved`, delete `gate.md`, return to Stage 5.

## Gate authorship (inline — no separate gate skill)

You write `gate.md` directly. YAML front-matter conforms to `woof/schemas/gate.schema.json`. Required fields: `epic_id`, `gate_type` (`plan_gate` / `story_gate` / `review_gate`), `triggered_by[]`, `opened_at`. Then prose: Context, Findings, Position. Validate before writing: `./woof/bin/woof validate .woof/epics/E<N>/gate.md`.

Resolution: append the corresponding `*_gate_resolved` event to `epic.jsonl` with the structured `decision` enum, then `rm .woof/epics/E<N>/gate.md`. The gate is the authoritative open-state marker; `epic.jsonl` is audit.

## Story commit transaction

Stage 5 commits each story as one transaction: code paths under `story.paths[]` PLUS `.woof/epics/E<N>/plan.json` (status update) PLUS `.woof/epics/E<N>/critique/story-S<k>.md` PLUS `.woof/epics/E<N>/epic.jsonl`. One commit per story. Never split code from `.woof` state across commits.

## Locking

`.woof/epics/E<N>/.wf.lock` is mandatory on entry to any session that mutates the epic state. Format: `{"pid": ..., "invoker": "wf"|"wf-run", "started_at": "...", "host": "..."}`. Honoured by both `/wf` and `just wf-run`. Released on clean exit. Stale locks (PID dead) auto-cleanup with a warning.

## Boundaries

- One worktree per epic. Parallelism is across epics, not within an epic.
- One gh issue per epic — never create child issues for stories.
- Filesystem is canonical; `epic.jsonl` is audit. On disagreement, filesystem wins.
- Outcome / contract-decision IDs are append-only after Definition closes.
- Network is required (always-online). No offline mode, no silent fallback.

## Reporting back

When you stop (gate opened, stage transitioned, or epic complete), tell the user:

- Current stage and what just changed
- Any open gate (`gate.md`) and the decision menu
- Next operator action (review gate / dispatch driver / push)

Keep it terse. The user reads `gate.md` directly when they want detail.
