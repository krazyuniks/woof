# Spark — E182

> Source: gh issue #182 — "woof: codify all stage-boundary checks + gate authoring as deterministic subcommands"
> Status: Spark — incorporates 2026-04-27 operator review of the original spark. Discovery has not started.

## Framing

E181's Stage 5 silently shipped a known-broken commit (`c64066f6`, since reverted in `e5d42c37`) because the `/wf:execute-story` skill body's Check 6 had drifted from canon. Codex correctly returned `severity: blocker` with two real findings against the new `apply_size_cap()`; the executor proceeded anyway because the skill's prose-encoded Check 6 read "Dependencies satisfied" instead of the canonical "Cross-AI critique flags blocker". Nothing told the executor to halt.

The bug is the *pattern*: skill prose claiming to enumerate deterministic checks, executed by a non-deterministic LLM that may quietly diverge from the prose. The same pattern exists at every stage — Stage 5 is just where it failed first.

## Direction

Discovery should challenge the wording, but these architectural moves are load-bearing — they answer the same class of failure E181 hit:

### 1. Three-role separation: producer / verifier / committer

The story executor (LLM) **produces** a staged diff plus a critique artefact, then **exits**. It does not invoke the check binary. It does not write `gate.md`. It does not run `git commit`. The deterministic driver process (today `scripts/wf-run`) **verifies** by running `woof check stage-5` against the staged state, then **commits or writes the gate**.

Why stricter than the prior "skill calls the check binary" framing: a non-deterministic executor can drop the check call, claim success, and commit anyway. Even with a one-line skill body that says "call this binary, react to exit code", the step that matters (`git commit`) is in LLM hands. The fix is to separate the actor that produces the diff from the actor that decides whether the diff lands.

### 2. Checker registry — single source of truth

Check definitions live in a Python registry inside `woof`. Skills, schemas, and prompts must not duplicate the list. Where prompt examples or skill prose mention specific checks, those examples are either generated from the registry at build time, or covered by a snapshot test that extracts the front-matter / check references from the prompt files and validates them against the schemas. Drift between prose and schema is detected at CI time, not at production runtime.

### 3. Separate state-classification from boundary-validation

Two conceptually different operations:

- **`woof state classify --epic N --format json`** — cheap, side-effect-free filesystem classification. Reads `plan.json`, gate-file presence, .last-sync, etc. Returns `{stage, current_story, has_open_gate, ...}`. Used by `/wf` reconstitution to decide where we are.
- **`woof check stage-N --epic N [--story Sk] --format json`** — boundary validation. May be expensive (runs `just test-woof`, parses critique severity, walks staged diffs). Used at stage transitions, not at reconstitution.

Reconstitution should never trigger an expensive quality-gate run as a side effect of "where are we?".

### 4. Structured check-result schema + driver-owned gate write

New schema `check-result.schema.json` covering the structured output of any `woof check stage-N`:

```json
{
  "ok": false,
  "stage": 5,
  "epic_id": 181,
  "story_id": "S2",
  "triggered_by": ["check_6_critique_blocker"],
  "checks": [
    {
      "id": "check_6_critique_blocker",
      "ok": false,
      "severity": "blocker",
      "summary": "critique severity is blocker",
      "evidence": "...",
      "paths": [".woof/epics/E181/critique/story-S2.md"],
      "command": null,
      "exit_code": null
    },
    ...
  ]
}
```

Gate authoring becomes:

```
woof gate write --epic 181 --from-check-result result.json --position-file position.md
```

The deterministic driver supplies the check-result; the LLM (orchestrator at Stage 6, or executor before exit if it wants to surface something) authors a `position.md` prose file. `woof gate write` mechanically constructs `gate.md` with schema-correct front-matter; the prose body comes from the position file. Skills cannot misname YAML fields because they never write the YAML.

## Stage 5 check inventory (canonical, replacing skill-body prose)

For codification under `woof check stage-5`:

1. **Quality gates** — run each `command` in `.woof/quality-gates.toml`; each must exit 0.
2. **Outcome markers** — for each `O<n>` in `story.satisfies[]`, regex-grep the staged test diff per `.woof/test-markers.toml`; ≥1 hit each.
3. **Scope** — `git diff --staged --name-only` ⊆ `story.paths[]` (pathspec match), plus the allowed `.woof/` files.
4. **Contract refs** — `woof check-cd` (existing, already deterministic).
5. **Plan cross-refs + statuses** — `plan.json` schema-valid; cross-refs intact (every outcome covered, every CD owned by exactly one story, deps acyclic, dep-target statuses honoured).
6. **Critique** — `critique/story-S<k>.md` exists; schema-valid; top-level `severity` equals `max(findings[].severity)`; `severity != blocker`.
7. **Commit transaction integrity** — staged set contains the four `.woof` durable files (`plan.json`, `critique/story-S<k>.md`, `epic.jsonl`, `dispatch.jsonl`) PLUS any audit files that should be tracked (per `.woof` commit policy, codex `audit/` artefacts are durable; the current executor leaves them untracked — Check 7 catches this). No foreign `.woof/` paths staged.
8. **Docs drift** — per `.woof/docs-paths.toml` mappings (no-op when file absent).
9. **Periodic review valve** — every-N stories AND end-of-epic; surfaces accumulated `severity: minor` findings via a `review_gate`.

## Bootstrapping order (load-bearing)

E182's own Stage 5 will run under the broken executor until E182's own early stories land the architectural fix. To minimise the at-risk window, the planner must order stories so the **critique-severity check (Check 6) and driver-owned gate write land first**. Once those land, E182's subsequent stories execute under a self-protecting pipeline. Until then, manual operator vigilance (`grep '^severity:' .woof/epics/E182/critique/story-*.md` after every commit) is the safety net — explicitly a temporary operational risk, not part of the design.

## Constraints

- E181 sits at S1 done, S2 reverted to pending. E182's outcomes must be sufficient to enable a clean S2 re-dispatch under the new architecture (specifically: critique-severity now enforced by deterministic driver, not by skill prose).
- Single-epic scope. The dropping of `cld`/`cod` wrapper hard-deps and the `--effort` / reasoning-level controls are tracked separately; they do not bundle into E182 unless Discovery surfaces a hard coupling.
- Audit files (`.woof/epics/E<N>/audit/`) are durable per `.woof` commit policy but the current executor's commit transaction does not stage them. Check 7's expanded scope (above) catches this; some E182 story must also fix the executor to stage them.
- Schemas in `woof/schemas/` are 2020-12 JSON Schema; new `check-result.schema.json` follows the same conventions.

## Open questions for Discovery

- **What is the executor's exit-code contract** when it doesn't commit? Today the skill body says "exit 0 = committed, non-zero = gate written". Under the new model the executor never commits and never writes gate.md. Proposed: exit 0 = "diff staged, ready for verification"; exit non-zero = "abort without staging (no diff, abandon)". Driver-side action: on 0, run check-stage5; on non-zero, write gate with `triggered_by: ["subprocess_aborted"]` and the exit code.
- **How does the commit message originate** under driver-owned commit? Options: (a) executor writes a `--message-file` artefact the driver consumes; (b) driver constructs a deterministic message from the story metadata (`feat(woof): E<N> S<k> — <story.title>`) and ignores the executor's preference; (c) hybrid — deterministic prefix + executor-supplied body.
- **What does the executor write to surface partial-state observations** (something it noticed that doesn't fail any check but warrants a review_gate)? Probably a structured `observations.json` artefact the driver merges into the next periodic review valve.
- **Is `state classify` purely filesystem, or does it also peek at git** (HEAD vs staged)? Affects whether reconstitution can run before the working tree is fully synced.
- **Can the registry-driven approach extend to Stages 1–4 + 6** within the same epic, or should this epic stay strictly Stage-5-focused with the broader rollout deferred? Discovery should weigh scope vs. atomic-fix value.
- **How are skill prompt-examples kept honest**? Snapshot test (extract examples, validate against schemas, fail CI on drift) vs. generated-from-registry includes vs. simply removing examples from prompts. Discovery should pick one and codify it.
