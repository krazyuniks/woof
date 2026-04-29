# OPEN_QUESTIONS — E182

The six questions surfaced in `spark.md` are resolved by the synthesis (executor exit-code contract → P4; commit message origination → P5; observations channel → ARCHITECTURE §6 + a new `observations.jsonl`; classify scope → P3 / ARCHITECTURE §2; registry rollout scope → CONCEPT *Out of scope*; example honesty → P2 + P6 snapshot test). Four questions survive Discovery and need operator policy before Definition closes — or explicit deferral.

## OQ1 — ~~Audit-dir staging policy~~ — RESOLVED 2026-04-26

Resolved via E181 epic-contract revision (Stage 2 round-trip, see `.woof/epics/E181/epic.jsonl` `definition_started` / `definition_closed` events at 22:45:58Z). Original `head-truncate-at-256-KB` strategy was misframed; replaced by E181 O4: deterministic post-processor.

**Policy E182's Check 7 enforces:**

- Per dispatch, the four files `audit/<role>-<ts>.{prompt,meta,output,stderr}` are durable and must be staged in the story commit transaction.
- `.output` is the post-processed result (redaction applied; codex `command_execution.aggregated_output` content stripped; `agent_message` and `result` events preserved). Typical size 10–20 KB even for codex.
- `.meta` carries structured per-dispatch summary including the new `result_text` field (extracted final agent message).
- Raw subprocess stdout lands at `audit/raw/<role>-<ts>.output` and is gitignored — Check 7 must NOT flag it as missing-from-staged.
- Cap (default 256 KB, `.woof/agents.toml [audit].max_bytes`) acts as safety net only; engages on pathological cases via tail-truncate with footer pointing to `audit/raw/`.

Implementation lands as part of E181 S2 re-dispatch *after* E182 closes.

## OQ2 — ~~Auto-stage missing files vs. fail-loud in Check 7~~ — RESOLVED 2026-04-26

Resolved: **fail-loud.** Per P1 (producer ≠ committer), the driver does not retroactively extend the executor's staging set. When Check 7 detects missing `.woof` durable files, the driver writes a story_gate with `triggered_by: ["check_7_commit_transaction"]` and the staged diff remains uncommitted. The bootstrap-window argument (operator may prefer auto-stage while the executor contract is in flux) was rejected because it normalises the same boundary violation E181 hit.

## OQ3 — E181 S2 critique reuse

E181 S2 has an existing `.woof/epics/E181/critique/story-S2.md` written by codex with `severity: blocker`. After E182 lands, S2 will be re-dispatched. **Does the existing critique satisfy the new Check 6, or must S2 re-run its critique pass?**

The architectural answer: critiques are tied to a specific staged diff. S2's old critique is stale because the diff will be different (rewritten apply_size_cap). Re-running is correct. But the schema-validity question (does the old critique even parse against the new schema?) is independent and worth confirming as part of E182's snapshot tests.

**Decision needed by:** post-E182 Stage 5 (when S2 re-dispatches). Not blocking E182 itself; surfaced here as a follow-up.

## OQ4 — Registry coverage of legacy `woof check-cd`

`woof check-cd` already exists as a deterministic subcommand (commit `0ca1d75d`). E182 must wire it into the registry as `check_4_contract_refs` without duplicating implementation. **Does the existing CLI become the registry runner directly (subprocess invocation), or is the contract-refs logic refactored into an importable function the registry calls in-process?**

In-process is cleaner (no subprocess fork, structured outcomes naturally) but requires touching `check-cd`. Subprocess invocation is faster to land but duplicates argument handling. Either is valid; this is an implementation tradeoff the planner will weigh — surfacing here so the answer is recorded once rather than re-derived per story.

**Decision needed by:** Stage 3 Breakdown. Affects story sizing for Check 4.

## Resolved (recorded for traceability)

The following are settled in the synthesis files; they are listed here only so future readers can grep the OQ history:

- ~~Executor exit-code contract (spark Q1)~~ → P4: structured artefact (`executor_result.json`); exit non-zero means crash only.
- ~~Commit message origination (spark Q2)~~ → P5: deterministic prefix from story metadata + optional body from `executor_result.commit_body`.
- ~~Partial-state observations channel (spark Q3)~~ → ARCHITECTURE §6: optional `observations.json` per story, merged into `.woof/epics/E<N>/observations.jsonl`, surfaced by Check 9.
- ~~`state classify` git-awareness (spark Q4)~~ → P3 + ARCHITECTURE §2: filesystem-only.
- ~~Registry rollout scope (spark Q5)~~ → CONCEPT *Out of scope*: Stage 5 only; other stages reserved as registry slots, populated by a follow-up epic.
- ~~Skill example honesty (spark Q6)~~ → P2 + P6: snapshot test extracts check IDs from skill prose, validates against registry; CI fails on drift.
