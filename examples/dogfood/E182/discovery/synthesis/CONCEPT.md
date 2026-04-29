# CONCEPT — E182

## Problem

woof's stage-boundary checks are encoded as prose inside skill bodies (`.claude/commands/wf*.md`) and executed by an LLM. The LLM also produces the artefact under check, *and* commits the result. Three roles — produce, verify, commit — collapse into one non-deterministic actor.

E181 demonstrated the failure mode: the executor staged a known-broken commit (UTF-8 byte-cap violation) and committed because the skill's Check 6 prose had drifted from canon. Codex correctly returned `severity: blocker`; nothing in the pipeline acted on it. The driver had no role at the gate; the skill's own list of checks was the only enforcement, and an LLM is free to misread or rewrite that list at any time.

This is not a bug in the executor's reasoning. It is a structural property: **prose-encoded gates run by the LLM that produced the artefact will silently drift, and drift is undetectable until production failure.** The same pattern exists at every stage; Stage 5 is just the first place it failed visibly.

## Direction

Codify all stage-boundary checks as deterministic woof subcommands, separate the producer from the verifier from the committer, and build the registry/schema infrastructure to keep prose and canon aligned at CI time rather than at production runtime.

The architectural reshape is bounded to **Stage 5 only** within E182 (the failure surface). Stages 1–4 + 6 keep their current behaviour; the new infrastructure (registry, schemas, subcommand surface) is designed extensible so a follow-up epic can populate them additively.

## Why this beats narrower fixes

A prose-only patch ("rewrite Check 6 in the skill body") restores correctness at one stage until the next drift. The registry + verifier-driver separation eliminates the *class* of bug: if the check list is in code, the LLM cannot drop a check by misreading prose; if the driver runs the verifier, the LLM cannot commit despite a failing check; if a snapshot test extracts every check ID mentioned in skill prose and validates against the registry, drift fails CI before it can ship.

## Bootstrap problem (load-bearing)

E182's own Stage 5 will execute under the broken pipeline until E182's early stories land the architectural fix. The plan must order stories so **Check 6 (critique severity) and driver-owned `git commit` land in the first executable story**. Until those land, manual operator vigilance (`grep '^severity:' .woof/epics/E182/critique/story-*.md` after every commit) is the safety net. This is an explicit, time-bounded operational risk — not part of the design.

## Out of scope (carried forward)

- Stages 1–4 + 6 check codification. Track as a follow-up epic if the value emerges; the registry has slots reserved.
- `cld`/`cod` wrapper hard-dependency removal; reasoning-effort flags (`--effort`, `model_reasoning_effort`); zero-MCP modes. Tracked separately per the E182 spark `Constraints`.
- E181 S2 re-dispatch under the new pipeline. That is a Stage 5 action *after* E182 closes, not part of E182 itself.
