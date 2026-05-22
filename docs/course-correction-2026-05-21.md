# Course Correction - 2026-05-21

This note records the project-level correction after RC-B5 and the deep code review audit. It is a planning and architecture artefact, not an implementation report.

## Priority Order

1. Ryan's own development use is the urgent requirement.
2. Portfolio exemplar value comes after the tool works reliably in Ryan's projects.
3. OSS and stranger-consumer distribution comes later.

Distribution, PyPI, GitHub install polish, release tagging, and package consumer messaging are deliberately deferred. Packaging smoke tests can remain as engineering evidence, but packaging is not the active product priority.

## Preserved Inputs

- `docs/implementation-plan.md` contains the original release-closure audit, second pass, RC-B workstreams, and reconciliation notes.
- `docs/audit-2026-05-19-deep-code-review.md` contains the deep code review of load-bearing files.
- This document converts those findings and Ryan's direction into the active correction programme.

Do not discard either audit trail. New workstreams should cite the source finding rather than rewriting history.

## Guardrail Taxonomy

Woof has two first-class guardrail systems. They overlap, but they are not the same system.

### Commit-Safety Guardrails

These protect the repository from bad committed output. They include staged-diff checks, story scope checks, manifest checks, reviewer blockers, lint/test execution, gate creation, and final commit decisions.

Current direction:

- Keep bad-commit prevention first-class.
- Treat failed or incomplete governance state as a gate, not as an ordinary traceback where a human can reasonably recover.
- Commit messages should describe the actual work performed. A hard-coded `feat(woof)` scope is a defect in a consumer-project tool, not an architectural principle.

### Runtime Action-Safety Guardrails

These protect the host and working project while agents are running. They cover sandboxing, writable paths, shell permissions, network access, secrets exposure, browser/MCP access, and external side effects.

Current state:

- Dispatch currently grants broad CLI permissions to both Claude and Codex adapters.
- The implemented safety boundary is mainly around what can be committed, not what an agent can read, write, execute, or exfiltrate during runtime.

Current direction:

- Runtime action safety must become a documented, testable governance surface.
- The first implementation target is Ryan's self-use path, so the policy can start pragmatic and local rather than trying to solve public untrusted execution immediately.

## Stage 5 Producer Guidance

The Stage 5 story executor is the producer node that turns the selected story into code changes. The important guidance currently lives in `.claude/commands/wf/execute-story.md`; it tells the producer how to read the epic/story material, follow red-green-refactor discipline, and write `executor_result.json`.

The deep audit found that the graph prompted the primary agent to invoke `/wf:execute-story`. That is a Claude Code slash command and is not portable to Codex or consumer repositories without Woof's `.claude/` directory. This is the same class of problem that the Stage 1 portability work corrected.

Decision:

- The graph should render portable Woof-owned Stage 5 producer guidance directly.
- `.claude/commands/wf/execute-story.md` may remain as a local convenience wrapper, but graph execution must not depend on a Claude slash command.
- This is self-use critical because the preferred primary route is Codex.

## Check 4 Reference Contract Meaning

At architecture level, "Check 4" is the contract-reference guardrail. It verifies that declared contract surfaces exist, such as OpenAPI paths, JSON Schema entries, and Pydantic models.

Current direction:

- Keep existence/reference validation as a baseline.
- Add deeper behavioural conformance only after the self-use path is reliable.
- Treat deeper conformance as a governance-depth workstream, not as branching logic Ryan needs to decide now.

## Gate Surface

The current gate surface is simple:

- Woof writes `gate.md` plus structured gate metadata.
- A human resolves the gate with a structured command such as `woof wf --resolve approve`, `woof wf --resolve revise_plan`, or `woof wf --resolve split_story`.
- Resolution is recorded as events in the stage log.

Benefits:

- Deterministic.
- Easy to audit.
- Easy to operate from a terminal.

Costs:

- Weak conversational UX.
- Limited status/reporting surface.
- Current resolution should be hardened so a crash cannot leave `gate.md` and resolved events in an inconsistent state.

Current direction:

- Keep the file-and-command gate surface for self-use.
- Add atomic resolution and better status/reporting to the backlog.
- Revisit richer human-review UX after the core loop works reliably.

## Audit Overflow And Retention

"Raw audit overflow" means full dispatch output that is too large or too sensitive to commit into normal stage artefacts. Woof can cap and redact committed audit summaries, while preserving the larger raw material under ignored `.woof/.../audit/raw/` paths for local inspection.

Current state:

- Redaction and capping exist.
- Architecture documentation overstates retention/archive behaviour that is not yet implemented.

Current direction:

- Keep raw audit overflow local and ignored.
- Decide later whether retention is implemented, documented as a manual operation, or removed from the architectural promise.

## Active Correction Backlog

| ID | Workstream | Purpose | Source |
|---|---|---|---|
| CC-001 | Documentation and backlog realignment | Align architecture, README, implementation plan, continuation prompt, and audit provenance with the self-use-first correction. | Ryan direction, RC-B5, deep audit |
| CC-002 | Self-use Stage 5 portability | Remove the `/wf:execute-story` graph dependency, make Stage 5 guidance portable, pass large dispatch prompts through stdin instead of one argv element, derive commit messages from actual work, and add a source/self-use real-subprocess smoke with stub `claude`/`codex` executables on `PATH`. | DRH-001, DRH-003, DRH-004, DRH-006, DRH-010 |
| CC-003 | Graph failure and gate transaction hardening | Lead with the silent lost-commit resume bug, then convert malformed governance state into gates, handle gate-writing schema failures consistently, read and harden the `woof wf --resolve` gate-resolution path, and harden tracker edge cases. | DRH-002 lead, DRH-005, DRH-008, DRH-009, DRH-012, follow-up gate-resolution audit |
| CC-004 | Runtime action-safety model | Define and implement the governance surface for what dispatched agents may read, write, execute, and access. Blocked until Ryan makes a runtime-permission policy decision. | Ryan direction, architecture gap |
| CC-005 | Observability and audit UX | Add operator-facing status, timeline, gate, cost/token, and audit reporting surfaces; reconcile retention/archive promises. | Deep audit, architecture gap |
| CC-006 | Governance depth | Strengthen contract checks, reviewer evidence, and gate review ergonomics after the self-use path is stable. Keep DRH-007 and REF-1..10 recorded as deferred low/refactor material, not lost. | Release audit, deep audit |
| CC-007 | Distribution and release polish | Return to install, packaging, tagging, PyPI/GitHub consumer docs, and external OSS onboarding after self-use and portfolio readiness. | Deferred by Ryan |

## Sequencing

CC-001 is the current docs-only workstream.

After CC-001, the next implementation target should be CC-002. Stage 5 portability blocks the tool's value in Ryan's own projects because the graph can dispatch Codex but still instructs it to use a Claude-only slash command.

CC-003 should follow closely because failure recovery and gate consistency determine whether the tool can be trusted during real development.

CC-004 is first-class, but it is blocked until Ryan makes a runtime-permission policy decision. Until then, documentation must be honest that current runtime permissions are broad; future sessions must not auto-start CC-004 from the continuation loop.
