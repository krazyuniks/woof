# PRINCIPLES — E182

The architectural moves rest on six principles. Every story in the plan must be justifiable against at least one. Future maintenance should reject patches that add prose-encoded checks, exit-code-encoded application state, or executor-side commits — those regressions are the bug class E182 was built to eliminate.

## P1 — Producer ≠ Verifier ≠ Committer

The actor that produced an artefact never decides whether it is acceptable, and the actor that decides acceptability never lands the result.

- **Producer:** the story executor LLM. Stages a diff and writes a critique artefact. Exits.
- **Verifier:** the deterministic driver process. Runs `woof check stage-5` against the staged state. Reads the structured check-result.
- **Committer:** the deterministic driver process. Either commits the staged transaction, or invokes `woof gate write` and exits.

The producer never invokes the verifier and never invokes git. The driver never produces diffs. Each role has a single source of authority.

## P2 — Single source of truth: the checker registry

Stage-boundary checks are defined in a Python registry inside `woof`. Skills, schemas, prompts, and documentation reference checks by ID; they never enumerate, describe, or duplicate the registry's contents.

- A snapshot test extracts every check ID mentioned in `.claude/commands/wf*.md` and `woof/playbooks/**/*.md` and asserts each appears in the registry. CI fails on drift.
- Adding a check is a single registry edit; renaming is a registry edit + grep-replace; removing is a registry edit. No skill body re-authoring required.

## P3 — Cheap classify, expensive verify

`woof state classify` (filesystem-only, side-effect-free) and `woof check stage-N` (runs commands, may take minutes) are different operations and live behind different subcommands. Reconstitution must never trigger a quality-gate run as a side effect.

- `state classify` reads `plan.json`, gate-file presence, `epic.jsonl` tail, `.last-sync`. Returns JSON. No git, no network, no shell-out.
- `check stage-N` runs the registry-defined checks. Slow checks may shell out, parse staged diffs, or invoke external validators.

## P4 — Structured artefacts over exit codes

Process exit codes carry process-level signal only (`0` = clean termination, non-zero = crash/abort). Application-level state — what happened, why a check failed, what the executor observed — travels in JSON artefacts validated against schemas.

The driver-executor protocol uses two artefacts:

- `executor_result.json` — written by the executor before clean exit. Carries `outcome ∈ {staged_for_verification, aborted_with_position, empty_diff}`, optional `commit_body`, and optional `position` prose for gate authoring. Driver branches on `outcome`.
- `check-result.json` — written by `woof check stage-N`. Carries `ok`, `triggered_by[]`, and a per-check breakdown matching `check-result.schema.json`.

Exit non-zero from the executor means "I crashed; no `executor_result.json` exists; treat as `subprocess_crash`". The driver branches on artefact presence, not on exit code value.

## P5 — Driver-owned commit transaction

`git commit` is invoked exclusively by the driver, with a deterministic message structure: subject `feat(woof): E<N> S<k> — <story.title>`, body sourced from `executor_result.json.commit_body` (optional). The executor never invokes git.

- The driver's commit step only runs when `check-result.ok == true`. There is no path from "executor staged a diff" to "diff is committed" that bypasses the verifier.
- Gate authoring uses `woof gate write --epic <N> --from-check-result result.json --position-file position.md` — mechanical front-matter construction from the check-result, prose body from the position file. Skills never write `gate.md` YAML directly.

## P6 — Drift detection at CI, not at production

Three drift-detection mechanisms:

- **Snapshot test** over skill prose vs. registry (P2).
- **Schema validation** of every artefact written by woof code paths, using `woof validate` in unit tests.
- **Registry coverage assertion**: `woof check stage-5 --self-test` enumerates the registry and exits non-zero if any check is unimplemented. Catches partial registry edits before they reach a real epic.

If a drift can only be detected by an epic failing in production, the architecture is incomplete.
