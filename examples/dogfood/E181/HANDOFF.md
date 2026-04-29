# Handoff prompt — execute woof's first dogfood epic (E181)

Open this in a fresh Claude Code session in the GTS main worktree. Treat the entire file below the `## Prompt` heading as your starting instructions.

## Context summary

- **Epic:** GitHub issue #181 — "woof: audit redaction + 256 KB cap on dispatched subprocess output"
- **Repo:** `krazyuniks/guitar-tone-shootout` (gh CLI authenticated; remote already wired)
- **Branch:** `main`. The main worktree commits direct to main per `AGENTS.md`. Do **not** create feature branches in this worktree.
- **State:** Definition closed (Stage 2). `EPIC.md` validated, contract decisions verify, gh issue body synced. `plan.json` does not yet exist — Stage 3 (Breakdown) is your starting point.
- **Token budget:** ~200–400k tokens for the full epic. The user has explicitly authorised the spend; no further confirmation needed.

## Prompt

You are the woof orchestrator. Drive epic E181 from Stage 3 to completion using the `/wf` workflow. Read the design canon, verify the bootstrap, then dispatch the planner and proceed through the gates.

### Step 1 — Orient

Read in this order:

1. `wiki/Workflow.md` — canonical design (stages, gates, principles). The §"Stage contracts" table and §"Stage 5 deterministic gate checks" are non-negotiable.
2. `wiki/Workflow-Research.md` §2 — E146 contract-fidelity lessons. The fixture you helped ship is the regression net.
3. `.claude/commands/wf.md` — your operational playbook. Stages 1–6 in skill prose.
4. `.claude/commands/wf/plan.md` — what `/wf:plan` will do when dispatched.
5. `.claude/commands/wf/execute-story.md` — what `/wf:execute-story` will do per story (Stage 5 inner sequence + 9 deterministic checks).
6. `woof/playbooks/critique/{plan,story}.md` — Codex critique prompt templates.
7. `.woof/agents.toml` — role configuration (planner uses cld + claude-opus-4-7 + think; story-executor uses cld + claude-sonnet-4-6 + think; critiquer uses cod + gpt-5.3-codex).
8. `.woof/epics/E181/EPIC.md` — the epic you're about to execute.

Then verify the bootstrap:

```bash
just woof-validate .woof/prerequisites.toml .woof/agents.toml .woof/epics/E181/EPIC.md
./woof/bin/woof check-cd .woof/epics/E181/EPIC.md
gh api /rate_limit -H "Accept: application/vnd.github+json" >/dev/null  # confirm gh reachability
```

All three must succeed. If any fails, surface the failing command and its output to the user — do not proceed.

### Step 2 — Stage 3 (Breakdown)

Build a planner bootstrap prompt and dispatch the planner:

```bash
prompt=$(mktemp)
cat > "$prompt" <<EOF
You are executing Stage 3 (Breakdown) for epic E181.

Read in order:
  1. .woof/.current-epic (verify E181)
  2. .woof/epics/E181/EPIC.md (front-matter is canonical)
  3. woof/schemas/plan.schema.json (your output contract)
  4. CLAUDE.md / AGENTS.md (project conventions)
  5. .woof/codebase/{tree.txt,freshness.json} (current code map)

Then invoke /wf:plan E181.

Exit 0 only after writing a schema-valid plan.json to
.woof/epics/E181/plan.json. Exit non-zero only after writing gate.md.
EOF

echo "E181" > .woof/.current-epic

./woof/bin/woof dispatch claude --role planner --epic 181 --prompt-file "$prompt"
rc=$?
rm -f "$prompt"
```

When dispatch returns:

- If `plan.json` exists and validates: continue to plan critique.
- If `gate.md` exists: surface the gate and stop.
- If neither: investigate `.woof/epics/E181/audit/cld-planner-*.{output,stderr,meta}` and `dispatch.jsonl`; do not retry blindly.

Self-validate before proceeding:

```bash
./woof/bin/woof validate .woof/epics/E181/plan.json
./woof/bin/woof check-cd .woof/epics/E181/EPIC.md   # CDs must still verify
```

### Step 3 — Plan critique (codex)

Dispatch Codex with the canonical critique template:

```bash
./woof/bin/woof dispatch codex --role critiquer --epic 181 \
    --prompt-file woof/playbooks/critique/plan.md
```

The critique lands at `.woof/epics/E181/critique/plan.md` with severity-tagged findings. Validate:

```bash
./woof/bin/woof validate .woof/epics/E181/critique/plan.md
```

### Step 4 — Plan gate (Stage 4) — ALWAYS opens

Author `.woof/epics/E181/gate.md` with front-matter conforming to `woof/schemas/gate.schema.json`. Body sections: Context (what was planned), Findings (Codex critique synthesis + your independent reading), Position (recommend approve / revise_plan / revise_epic_contract / split_story / abandon_story / abandon_epic).

Surface the gate to the user. **Wait for their decision.** Never auto-resolve.

On `approve`: append `plan_gate_resolved` event with `decision: "approve"` to `epic.jsonl`, delete `gate.md`, proceed to Stage 5.

### Step 5 — Stage 5 (Story execution) via the driver

```bash
just wf-run --epic 181
```

The driver:
- Acquires `.woof/epics/E181/.wf.lock`
- Picks the first pending story whose `depends_on[]` are satisfied
- Marks it `in_progress` in `plan.json`
- Dispatches `/wf:execute-story` via `woof dispatch claude --role story-executor`
- The story executor: codes within `story.paths[]`, adds tests for `satisfies[]` outcomes, runs the project quality gate to green, dispatches the story critique (cod), updates plan.json status, stages the four-way commit transaction, runs the 9 deterministic checks, then commits (exit 0) or writes gate.md (exit non-zero)
- On gate.md: driver halts. Surface to user, resolve via Stage 6.
- On all done: driver exits 0. Append `epic_completed` to `epic.jsonl`. Push the gh issue closure.

The 9 checks codified in `.claude/commands/wf/execute-story.md`. Check 4 invokes `./woof/bin/woof check-cd .woof/epics/E181/EPIC.md` — that is the E146 regression net.

### Step 6 — Audit bundle + epic close

Once every story is `done`:

```bash
just wf-audit-bundle 181   # copies CC transcripts into audit/claude-code/
gh issue close 181 --repo krazyuniks/guitar-tone-shootout --comment "Epic E181 complete — see commit log."
```

Append `epic_completed` to `.woof/epics/E181/epic.jsonl`. Final commit if needed (the per-story commits will already include `.woof/` state updates).

## Where to look when something breaks

| Symptom | Look here |
|---|---|
| Planner failed without writing plan.json or gate.md | `.woof/epics/E181/audit/cld-planner-*.{output,stderr,meta}` |
| Planner said "schema invalid" | `.woof/epics/E181/audit/cld-planner-*.output` — final JSON line is the result; earlier turns are tool calls |
| Codex critique didn't land | `.woof/epics/E181/audit/cod-critiquer-*.{output,stderr,meta}` |
| Story execution exited 0 with no commit | Check `git log -1` and `git diff HEAD~1`; the story may have produced an empty diff (Check 7 should have caught it as `empty_diff_review` gate) |
| Driver halted but no gate.md | Race or crash; check `dispatch.jsonl` last events + `.wf.lock` |
| Need full CC transcript with tool calls | `~/.claude/projects/<slug>/<cc_session_id>.jsonl` (cc_session_id is captured per dispatch in `dispatch.jsonl`'s `subprocess_returned` events) |
| gh sync conflict | `epic.jsonl` `github_sync_conflict` event; `.last-sync.{updated_at,body_sha256}` vs current remote |

## Audit observability — what's captured automatically

Every `woof dispatch` invocation writes:

- `.woof/epics/E181/audit/<cld|cod>-<role>-<ts>.prompt` — the input prompt verbatim
- `.woof/epics/E181/audit/<cld|cod>-<role>-<ts>.output` — captured stdout (Claude: single JSON line with `result`, `usage`, `session_id`; Codex: JSONL events)
- `.woof/epics/E181/audit/<cld|cod>-<role>-<ts>.stderr` — captured stderr
- `.woof/epics/E181/audit/<cld|cod>-<role>-<ts>.meta` — JSON sidecar: `harness`, `role`, `model`, `mcp[]`, `flags[]`, `argv[]`, `pid`, `started_at`, `ended_at`, `duration_ms`, `exit_code`, `timed_out`, `tokens.{tokens_in,tokens_out,cache_read_tokens,cache_write_tokens}`, `cc_session_id` or `codex_thread_id`
- `.woof/epics/E181/dispatch.jsonl` — `subprocess_spawned` + `subprocess_returned` events with the same fields, schema-validated against `woof/schemas/jsonl-events.schema.json`

`just wf-audit-bundle 181` collects every referenced CC session transcript from `~/.claude/projects/` into `.woof/epics/E181/audit/claude-code/` so the bundle survives even if CC's native location is pruned.

## Hard rules

- **Never bypass quality gates.** No `--no-verify`, no `--no-gpg-sign`. If hooks fail, fix the underlying issue.
- **Never create branches in main worktree.** Per `.claude/rules/worktree-branching.md`. Each worktree IS a branch.
- **Filesystem is canonical; epic.jsonl is audit.** On disagreement, the filesystem wins.
- **One commit per story** (the four-way transaction). Never split code from `.woof` state.
- **No auto-revision after gate.md.** First check is final within the block. Revision authority is the human at Stage 6.
- **Push policy:** local commits stay local during execution. Push only after epic close, with `git pull --rebase && git push` per `AGENTS.md`.

## Pointers — git log

Recent commits that built this stack (most recent last; full chain on `main`):

```
f20e290c  feat(woof): language-registry entries for python/typescript/rust/go
aa02faba  feat(woof): dispatch adapter for cld/cod subprocess invocation
f33f2fc2  feat(woof): render-epic subcommand with gh sync + conflict detection
4e30da35  fix(tests): migrate gear-detail tests to make_gear factory
cc331967  feat(woof): /wf orchestrator + /wf:execute-story + wf-run driver
f9182a25  feat(woof): post-commit cartography script
ee11891a  feat(woof): port discovery playbooks from taches-cc-resources
0ca1d75d  feat(woof): check-cd subcommand + E146 contract-fidelity fixture
169f3e34  feat(woof): dogfood bootstrap — agents.toml + first real epic E181
329f4b8f  feat(woof): planner skill + critique playbooks (close the workflow loop)
```

`git log --oneline -- woof/ .woof/ .claude/commands/wf*` will give the full chain in chronological order.

## Pointers — on-disk

```
woof/bin/woof                                CLI: validate, dispatch, render-epic, check-cd
woof/schemas/*.schema.json                   11 typed-artefact schemas (JSON Schema 2020-12)
woof/languages/{python,typescript,rust,go}.toml   Tool-side language registry
woof/playbooks/discovery/                    21 ported taches playbooks (research/, consider/, ask-me-questions)
woof/playbooks/critique/{plan,story}.md      Codex critique prompts
.claude/commands/wf.md                       /wf orchestrator (in-session)
.claude/commands/wf/plan.md                  /wf:plan (Stage 3 planner subprocess skill)
.claude/commands/wf/execute-story.md         /wf:execute-story (Stage 5 subprocess skill)
.woof/prerequisites.toml                     GTS prereqs (infra, wrappers, validators, indexing, lsp, github)
.woof/agents.toml                            Role configuration (planner, story-executor, critiquer, ...)
.woof/epics/E181/EPIC.md                     The dogfood epic
.woof/codebase/{tree.txt,freshness.json}     Cartography (gitignored; refreshed by post-commit hook)
scripts/wf-run                               Stage 5 driver (Bash)
scripts/refresh-cartography                  Post-commit cartography refresh
tests/fixtures/woof/e146/                    E146 regression fixture (3 CDs, one per ref type)
tests/unit/woof/                             60 woof unit tests
tests/conftest.py                            DB SAVEPOINT pattern (real Postgres, no mocking)
```

## What success looks like

- Every story in `plan.json` ends `status: "done"`.
- One git commit per story, each containing code + `.woof/` state in the same diff.
- `just test-woof` and `just test` both green at every commit.
- `gh issue 181` closed with reference to the final commit.
- `.woof/epics/E181/audit/` contains a complete prompt+output+meta tuple for every dispatched subprocess plus bundled CC transcripts under `audit/claude-code/`.
- `dispatch.jsonl` and `epic.jsonl` together tell the full story (per-event token usage, durations, decisions, gates).
- E181's actual code change: `woof dispatch` learns to redact secrets and cap audit files at 256 KB before they land in `.woof/epics/*/audit/`, with overflow to `.woof/epics/*/audit/raw/` (gitignored).

## What to flag back to the user

- **Any blocker-severity Codex finding** — surface verbatim with your position.
- **Any check failure that opens a gate** — surface the gate, your synthesis, and recommendation.
- **Any failure mode the design didn't anticipate** — examples: dispatch hangs, plan.json validates but is obviously wrong, Codex returns malformed JSONL, audit files end up empty. These are bugs in the woof tool itself; record them and propose fixes after the gate is resolved.
- **Final summary at epic close** — token totals from `dispatch.jsonl`, wall-clock from event timestamps, count of gates opened/resolved, anything you'd want recorded in MEMORY.md.

The user is a senior architect; brief, precise, and honest. No filler. Match the tone of `wiki/Workflow.md`.
