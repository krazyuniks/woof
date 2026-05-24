# Archived Research Notes

> **Archive status:** This file preserves design research that influenced Woof.
> It is not current product guidance. Current behaviour is specified in
> `docs/architecture.md`, the ADRs, schemas, and source code.

## Design Inputs

Woof's current architecture was shaped by comparison with several AI-assisted
development workflows:

- prompt-only skill systems;
- router-pattern slash-command systems;
- file-backed agent command frameworks;
- earlier Python orchestration work with deterministic hooks, JSONL state, and
  multi-provider dispatch.

The reusable lessons are reflected in the current architecture:

- deterministic graph ownership instead of prompt-owned orchestration;
- schema-governed artefacts at stage boundaries;
- cross-provider production and review roles;
- human gates for blocker review and conflict resolution;
- manifest-checked commits;
- JSONL audit streams for resume and inspection;
- fail-loud prerequisite checks.

## Contract Fidelity

The current "epic contract is law" rule comes from a planning failure where an
implementation plan substituted repository convention for the user-facing epic
contract. Woof now encodes the contract in Definition:

- `EPIC.md.observable_outcomes[]` records user-facing assertions;
- `EPIC.md.contract_decisions[]` records technical surfaces and native contract
  artefact references;
- `plan.json.stories[].satisfies[]` links stories to outcomes;
- Stage 5 Check 4 verifies contract references before commit;
- consumer-owned quality gates verify behavioural conformance.

Repo conventions can inform adapters, aliases, redirects, or bridge code, but
they do not replace the epic contract unless a human gate revises that contract.

## Research Discipline

Research targets deltas: current API surfaces, compatibility matrices, pricing,
breaking changes, or specific unknowns. It does not re-learn basic technology
background when the operator can state the known baseline and the gap.

Required infrastructure must be present at invocation. Woof fails loud rather
than silently producing thinner evidence.
