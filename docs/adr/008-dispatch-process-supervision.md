---
type: adr
status: superseded by ADR-012
date: 2026-06-03
---

# ADR-008: Dispatch Process Supervision

Superseded by ADR-012. Worker supervision and completion handling move behind the shared interactive harness transport boundary.

Extends ADR-006 (operational resilience). ADR-006 made dispatch telemetry and
runaway protection part of the graph contract; this ADR fixes how a single
dispatch is supervised and classified, which the run-resilience counters in
ADR-006 depend on.

## Context

A dispatched node spawns a child `woof dispatch` (`nodes.py:_run_dispatch`), which
in turn spawns the agent as `timeout 30m <claude|codex>` and blocks on
`subprocess.communicate()` (`dispatcher.py:cmd_dispatch`). Completion is inferred
from stdout reaching EOF.

That inference is wrong when the agent finishes but a process it spawned - a
long-lived MCP server, a `gh` or git subprocess - inherited the agent's stdout
pipe and holds the write end open. The agent has produced its terminal result and
is logically done, but EOF never arrives, so `communicate()` blocks until the
`timeout(1)` wrapper sends SIGTERM at the wall-clock bound. The dispatch then
returns exit 124, the node sees `returncode != 0`, and a `subprocess_crash` gate
is opened - discarding work the agent already completed.

Three deficiencies underlie this:

1. One coarse clock. A genuinely stuck worker (no output at all) and a finished-but-
   lingering worker are both only caught at the 30-minute wall-clock bound, and both
   are reported identically.
2. No process-group ownership. SIGTERM reaches only the direct child (the `timeout`
   process); orphaned grandchildren such as MCP servers are not reaped.
3. Buffered read. `communicate()` hides a latent pipe-buffer deadlock and yields no
   incremental signal for idle detection or progress.

The external reference for the fix is sandcastle's idle-timeout-versus-completion-
timeout split (its ADR-0019): once the terminal result is observed, a short grace
window replaces the idle clock and the run resolves successfully even if the process
has not exited.

## Decision

Dispatch supervision moves in-process, onto phase-scoped clocks and explicit
process-group lifecycle.

1. A `supervise()` primitive runs an arbitrary argv with stdin and drains stdout and
   stderr concurrently. It is a trusted in-process utility returning a typed result.
2. Phase-scoped clocks:
   - Idle: no output for `idle_seconds` before the terminal marker is seen -> kill,
     fail. Catches a stuck worker early.
   - Wall-clock: an absolute ceiling (`default_minutes`) that fails a worker which has
     not reached its terminal marker.
   - Completion-grace: once the terminal marker is seen, a `completion_grace_seconds`
     window takes over from the idle clock and resets on post-terminal output; on expiry
     the dispatch resolves successfully with the captured output.
   - Tail cap: once the terminal marker is seen, `completion_tail_cap_seconds` is the
     absolute post-terminal bound; it resolves `completed_lingering` even if a child
     keeps dribbling output.

   A clean process exit with closed output streams wins the race, so healthy runs add no
   latency. If the terminal marker has been seen but inherited streams remain open after
   the worker exits, cleanup is audited as `completed_lingering`.
3. The terminal marker is detected by an adapter-specific predicate (Claude's final
   `--output-format json` result line; Codex's `turn.completed`). The primitive stays
   agent-agnostic.
4. Each worker is spawned in its own session/process group. On any kill the whole
   group is signalled SIGTERM, then SIGKILL after a grace window, so orphaned children
   are reaped. Only the worker's own group is targeted, never Woof's.
5. The outcome is classified by `exit_type`: clean, nonzero, idle_kill,
   wallclock_timeout, completed_lingering, operator_cancel. `exit_type` is added to the
   dispatch-event schema and surfaced through `_run_dispatch` to node consumers; a
   `completed_lingering` worker advances the graph rather than opening a crash gate.
6. The external `timeout(1)` wrapper is dropped from the real run path; it cannot
   distinguish completed-lingering from stuck, nor reap the process group with
   classification. The wall-clock bound is owned in-process.
7. Cancellation is idempotent and double-signal safe.

This is a liveness and classification change. It does not alter the trusted-local
runtime-safety posture: nothing new constrains what a dispatched agent may do, and
commit-safety remains the boundary on what lands.

## Consequences

- `cmd_dispatch` is rebuilt onto `supervise()`; `Popen(["timeout", ...]) +
  communicate()` is removed from the run path.
- `jsonl-events.schema.json` gains the `exit_type` enum; `dispatch.jsonl` events carry
  it. This refines ADR-006 point 2 (exit type is now a closed enum that includes
  completed_lingering) and feeds ADR-006 point 3 (the run-resilience counters classify
  on it).
- Node consumers branch on `exit_type`: `completed_lingering` proceeds, genuine
  crash/timeout gates as before.
- `[timeouts]` in `agents.toml` gains `idle_seconds`, `completion_grace_seconds`, and
  `completion_tail_cap_seconds` alongside `default_minutes`.
- Supervision is testable in isolation with real fake-agent scripts (no mocks): the
  fault-injection matrix exercises stdout-held-open, SIGTERM-ignoring, slow-drip,
  zero-output-hang, oversized-output, and double-cancel cases.
- Dry-run dispatch output no longer carries a `timeout` argv prefix.
- The architecture s11.5 dispatch-process-supervision subsection and s9 run-lineage
  note are the declarative home for this decision.
