# Concerns

## Runtime action-safety posture is trusted-local only

Woof does not sandbox dispatched agents, restrict writable paths, allow-list shell commands, block network access, or add MCP restrictions. Commit safety (checks, reviewer critique, gates, transaction manifests) is the only protective layer once an agent is running. This posture is documented as OD-1 in `docs/backlog.md` and accepted for the current expert-workstation use case, but it means a misconfigured agent can freely read or write outside the epic's intended scope.

## No run-resilience circuit-breaker yet

`woof wf` does not yet detect repeated dispatch failures, consecutive no-progress cycles, or same-error signatures. A stuck agent (e.g. rate-limited, repeatedly timing out, or writing the same incorrect output) will continue dispatching until the operator manually intervenes or cancels. Run-resilience logic is in `docs/backlog.md` E2 remaining work.

## No HEAD/branch drift detection

The commit node does not record `head_before`/`head_after` or detect when another process has moved HEAD or the current branch during a dispatch. An external push or rebase while an agent is running can cause the graph to commit onto a diverged branch without warning. Tracked in `docs/backlog.md` E2.

## Blocker evidence is advisory, not enforced

Stage-5 Check 6 (`check_6_critique_blocker`) verifies that blocker findings exist but does not require them to carry resolvable evidence references (file:line, work-unit id, outcome id, schema ref). A reviewer can produce an unsubstantiated blocker that cannot be verified. Tracked in `docs/backlog.md` E2.

## Readiness recycle escalation not yet shipped

After a configured number of failed readiness cycles, Woof should open an escalation gate rather than looping revise/fail indefinitely. Currently the loop is unbounded. Tracked in `docs/backlog.md` E2.

## quality-gates.toml has no mode support yet

Only a `strict` mode is effectively implemented (any gate failure blocks). The planned `baseline` mode (per-command baselines that ignore pre-existing failures) is not yet shipped. Tracked in `docs/backlog.md` E2.

## Cartography AS-IS docs go stale between refreshes

The AS-IS layer (this file and its siblings) is authored at map-codebase time and updated only when the operator re-runs the flow. Mid-epic source tree changes are not automatically reflected. The post-commit hook keeps the mechanical layer fresh but does not regenerate the prose. ADR-004 accepts this: stale AS-IS is a warning, not a blocker.

## Structural cartography index not yet built

ADR-009 accepts a queryable symbol/edge index under `.woof/codebase/structural/` for file, symbol, and typed-edge lookups. It is designed but not yet implemented. Current cartography is prose plus a ctags index; there is no programmatic query surface. Tracked as a separate epic (E12 or later).

## `woof dispatch` adapter set is limited

Only `claude` and `codex` adapters are supported. Any other LLM CLI would require a new adapter in `src/woof/cli/dispatcher.py` and matching config in `agents.toml`. There is no generic adapter interface for arbitrary command-line tools.

## Duplicate gitignore entries after `woof init`

Running `woof init` on a repo that already has `.woof/codebase/tags` and `.woof/codebase/freshness.json` in its pre-existing `.gitignore` produces duplicate entries. This is harmless (git processes all matching rules) but visually noisy. The woof-managed block takes precedence and can be audited with `woof init`.
