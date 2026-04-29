---
epic_id: 182
title: Codify Stage-5 boundary checks + gate authoring as deterministic woof subcommands
observable_outcomes:
  - id: O1
    statement: Given a story executor that produces a staged diff with executor_result.outcome == staged_for_verification, and a Stage-5 check returns ok == false, the wf-run driver writes .woof/epics/E<N>/gate.md and does NOT invoke git commit; the staged diff remains uncommitted on the working tree.
    verification: automated
  - id: O2
    statement: The Stage-5 checker registry (woof/checks/registry.py) exports exactly nine check entries with IDs check_1_quality_gates, check_2_outcome_markers, check_3_scope, check_4_contract_refs, check_5_plan_crossrefs, check_6_critique_blocker, check_7_commit_transaction, check_8_docs_drift, check_9_review_valve; woof check stage-5 --self-test enumerates all nine and exits 0; stubbing any runner to raise NotImplementedError causes self-test to exit non-zero.
    verification: automated
  - id: O3
    statement: woof state classify --epic <N> --format json returns a structured JSON document conforming to a documented schema without invoking subprocess.run, opening a network socket, or shelling to git; mocking subprocess and socket modules during a unit-test invocation does not produce any call record.
    verification: automated
  - id: O4
    statement: woof check stage-5 --epic <N> --story <Sk> --format json writes a check-result document conforming to woof/schemas/check-result.schema.json containing top-level ok, stage, epic_id, story_id, triggered_by[], and a per-check breakdown; the process exit code is 0 iff ok == true.
    verification: automated
  - id: O5
    statement: woof gate write --epic <N> --from-check-result <result.json> --position-file <position.md> writes .woof/epics/E<N>/gate.md whose YAML front-matter validates against gate.schema.json; the front-matter fields gate_type, triggered_by[], opened_at are mechanically derived from the check-result, while the prose body is verbatim copied from position.md; no LLM authors any YAML field.
    verification: automated
  - id: O6
    statement: A snapshot test in tests/unit/woof/test_skill_prose_drift.py extracts every check-ID reference (matching pattern check_[1-9]\\d*_[a-z_]+) from .claude/commands/wf*.md and woof/playbooks/**/*.md and asserts each appears in the Stage-5 registry; introducing a non-existent check ID into any of those files causes the test to fail.
    verification: automated
  - id: O7
    statement: Replaying E181 S2's existing critique fixture (severity == blocker) against the new pipeline causes wf-run to write a story_gate with check_6_critique_blocker in triggered_by[] and to NOT invoke git commit; this is the canonical regression test for the E181 failure mode.
    verification: automated
  - id: O8
    statement: A story-executor subprocess that exits non-zero without writing executor_result.json causes wf-run to write a story_gate with triggered_by ["subprocess_crash"]; one that exits 0 with executor_result.outcome == aborted_with_position causes wf-run to write a story_gate with triggered_by ["executor_aborted"] and the position prose copied through; one that exits 0 with executor_result.outcome == staged_for_verification proceeds to woof check stage-5.
    verification: automated
  - id: O9
    statement: The .claude/commands/wf/execute-story.md skill body does not contain the literal strings "git commit", "gate.md" used as a write target, or any enumerated list of the boundary checks; a static-string assertion test passes against the skill body.
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes: [O1, O4, O7]
    title: Stage-N check-result schema
    json_schema_ref: woof/schemas/check-result.schema.json
    notes: |
      New schema covering the structured output of woof check stage-N.
      Top-level ok, stage, epic_id, story_id, triggered_by[], plus a
      per-check breakdown matching {id, ok, severity, summary, evidence,
      paths, command, exit_code}. severity ∈ {null, minor, major, blocker}.
      triggered_by[] is the list of check IDs where ok == false.
  - id: CD2
    related_outcomes: [O1, O8]
    title: Executor-result schema
    json_schema_ref: woof/schemas/executor-result.schema.json
    notes: |
      New schema for the producer↔verifier protocol. The story-executor
      writes executor_result.json before clean exit; the wf-run driver
      branches on outcome ∈ {staged_for_verification, aborted_with_position,
      empty_diff}. Carries optional commit_body (used by the driver when
      constructing git commit) and optional position prose (used by gate
      write when the executor surfaces a halt).
  - id: CD3
    related_outcomes: [O2]
    title: Check registry typed entry
    pydantic_ref: woof/checks/registry.py:Check
    notes: |
      The single source of truth for stage-boundary checks. Pydantic model
      fields {id, stage, cost, summary, runner}. Skills, schemas, prompts
      reference checks by ID; never enumerate or describe their contents.
      Drift between skill prose and registry is caught by O6's snapshot test.
  - id: CD4
    related_outcomes: [O5]
    title: Gate triggered_by enum extension
    json_schema_ref: woof/schemas/gate.schema.json
    notes: |
      gate.schema.json's triggered_by[] enum extends to include the registry
      check IDs (check_1_quality_gates ... check_9_review_valve), plus the
      executor-protocol triggers subprocess_crash, executor_aborted,
      empty_diff_review. Schema enum members are generated from the registry
      at build time and snapshot-tested.
  - id: CD5
    related_outcomes: [O1, O8]
    title: jsonl-events additions for executor protocol
    json_schema_ref: woof/schemas/jsonl-events.schema.json
    notes: |
      New event types: executor_result_recorded, check_run, story_committed.
      executor_result_recorded carries epic_id, story_id, outcome, exit_code.
      check_run carries epic_id, story_id, stage, ok, triggered_by[].
      story_committed carries epic_id, story_id, commit_sha. Existing
      story_completed continues to mark workflow-level completion (after
      check + commit).
acceptance_criteria:
  - "The Stage-5 checker registry at woof/checks/registry.py exports nine entries matching the IDs in O2; each runner produces a structured outcome consumable by check-result.schema.json."
  - "woof check stage-5 --self-test exits 0 when all nine runners are implemented; stubbing any runner to raise NotImplementedError causes a non-zero exit and a structured error naming the failing check ID."
  - "woof state classify is filesystem-only — covered by a unit test asserting subprocess.run, subprocess.Popen, and socket.socket are not called during invocation."
  - "woof check stage-5 emits check-result.json conforming to check-result.schema.json; exit code is 0 iff result.ok is true."
  - "woof gate write produces gate.md whose YAML front-matter validates against gate.schema.json; the gate_type, triggered_by[], opened_at fields are derived from the check-result; the prose body is verbatim from the position file."
  - "The wf-run driver invokes git commit only when the most recent check-result.ok is true and the executor_result.outcome was staged_for_verification; both conditions are unit-test-asserted."
  - "When Check 7 (commit transaction integrity) detects missing .woof durable files, the driver writes a story_gate (fail-loud); it does NOT auto-stage the missing files. Asserted via fixture test."
  - "An E181 S2 fixture (the existing severity:blocker codex critique) is replayed against the new pipeline; the assertion is that the driver writes a story_gate, no commit lands, and the working tree's staged set is preserved."
  - "A snapshot test in tests/unit/woof/test_skill_prose_drift.py extracts every check-ID reference in .claude/commands/wf*.md and woof/playbooks/**/*.md and asserts each appears in the Stage-5 registry."
  - "A static-string test asserts .claude/commands/wf/execute-story.md does not contain the strings 'git commit', 'gate.md' as a write target, or any enumerated list of the boundary checks."
  - "Bootstrap order: the first executable story (S1 in plan.json) lands check_6_critique_blocker + driver-owned git commit so all subsequent E182 stories execute under the new safety. The orchestrator and planner must reject a plan that violates this constraint."
  - "just test-woof is green on completion."
---

# Codify Stage-5 boundary checks + gate authoring as deterministic woof subcommands

## Why now

E181 S2 silently shipped a known-broken commit (since reverted in `e5d42c37`) because the `/wf:execute-story` skill body's Check 6 had drifted from canon. Codex correctly returned `severity: blocker` with two real findings against the new `apply_size_cap()`; the executor proceeded anyway because the skill's prose-encoded Check 6 read "Dependencies satisfied" instead of the canonical "Cross-AI critique flags blocker". Nothing told the executor to halt. The same pattern — skill prose claiming to enumerate deterministic checks, executed by a non-deterministic LLM that may quietly diverge — exists at every stage; Stage 5 is just where it failed first.

This epic eliminates the **class** of bug rather than patching its first occurrence.

## What changes

Three architectural moves, scoped to Stage 5 only:

1. **Producer ≠ Verifier ≠ Committer.** The story executor produces a staged diff plus a structured `executor_result.json` and exits. The deterministic driver process (`scripts/wf-run`) verifies via `woof check stage-5` against the staged state, then either invokes `git commit` (when `check-result.ok == true`) or writes `gate.md` via `woof gate write`. The executor never calls the check binary; never invokes git; never writes gate YAML.

2. **Single source of truth.** Check definitions live in a Python registry inside `woof`. Skills, schemas, prompts, and documentation reference checks by ID; they never enumerate, describe, or duplicate the registry. A snapshot test extracts every check-ID reference in skill prose and validates against the registry — drift fails CI.

3. **Cheap classify vs. expensive verify.** `woof state classify` (filesystem-only, side-effect-free) and `woof check stage-N` (runs commands, may take minutes) are different operations behind different subcommands. Reconstitution must never trigger a quality-gate run as a side effect.

## What does not change in this epic

Stages 1–4 + 6 keep their current skill-driven behaviour. The registry has slots reserved for them; population is a follow-up epic. The audit cap / post-processor work tracked in E181 is independent (E181 is paused; it re-dispatches under E182's pipeline once E182 closes). Wrapper-removal, reasoning-effort flags, and zero-MCP work are tracked separately.

## Bootstrap risk window

E182's own Stage 5 will execute under the broken pipeline until the first story lands. The plan must order stories so that **`check_6_critique_blocker` and driver-owned `git commit` land in S1**. Until S1 lands, manual operator vigilance (`grep '^severity:' .woof/epics/E182/critique/story-*.md` after every commit) is the safety net. This is an explicit, time-bounded operational risk — not part of the design.

## Synthesis references

- `discovery/synthesis/CONCEPT.md` — problem framing, scope boundaries.
- `discovery/synthesis/PRINCIPLES.md` — six operating principles (P1–P6).
- `discovery/synthesis/ARCHITECTURE.md` — components, data contracts, bootstrap order.
- `discovery/synthesis/OPEN_QUESTIONS.md` — surviving questions; OQ1 (audit-staging policy) and OQ2 (Check 7 missing-file behaviour) resolved 2026-04-26; OQ3 (S2 critique reuse) deferred to post-E182; OQ4 (check-cd integration shape) deferred to Stage 3 planner.
