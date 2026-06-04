# E7. Dispatch Process Supervision

Active per-epic plan for E7 in `docs/backlog.md`. The backlog defines the open
work; this file sequences it into small, reviewable coding-agent prompts. Delete
this file when E7 is done.

## Goal

A dispatched worker's outcome is classified correctly, and a worker that finished
its work is never discarded as a failure. Today `woof dispatch` spawns
`timeout 30m <agent>` and blocks on `communicate()`. A worker that emits its
terminal result and then lingers - because a spawned child (a long-lived MCP
server, a `gh`/git subprocess) inherited the stdout pipe and holds it open -
blocks until the wall-clock kill, returns exit 124, and is reported as a timeout.
The node layer then treats `returncode != 0` as a crash and opens a gate,
throwing away completed work.

E7 replaces the single coarse clock with in-process supervision on three
independent clocks (idle, completion-grace, wall-clock), spawns each worker in its
own process group so orphaned children are reaped, and classifies the outcome with
an `exit_type` the node layer consumes so a completed-but-lingering worker
proceeds instead of gating. The external pattern reference is sandcastle's
idle-timeout-versus-completion-timeout split (ADR-0019 there); the decision is
recorded for Woof in ADR-008.

This is a prerequisite for E2's run-resilience policy: the same-error and
no-progress circuit breakers consume `exit_type`, so a misclassified worker would
poison those counters.

## Stories

| ID | Story | Acceptance criteria |
|---|---|---|
| S1 | Supervised-subprocess primitive | A `supervise()` primitive runs an arbitrary argv with stdin, draining stdout and stderr concurrently (no pipe-buffer deadlock), on phase-scoped clocks split by an injected terminal-marker predicate. Pre-terminal: idle (`idle_seconds`, resets on output -> `idle_kill`, fail) and wall-clock (`default_minutes`, absolute -> `wallclock_timeout`, fail). Post-terminal: completion-grace (`completion_grace_seconds`, resets on output) and tail cap (`completion_tail_cap_seconds`, absolute), both -> `completed_lingering`, success. A clean process exit pre-empts every clock. The worker runs in its own process group; on any kill the whole group is signalled SIGTERM then SIGKILL after a grace window. Output is bounded: streamed to caller-provided sinks with a max-captured-bytes in-memory cap; the result carries a bounded head/tail view, spooled paths, `exit_type` in {clean, nonzero, idle_kill, wallclock_timeout, completed_lingering, operator_cancel}, exit code, and timing. Cancellation is idempotent and double-signal safe. |
| S2 | Wire `cmd_dispatch` onto the primitive | `cmd_dispatch` uses `supervise()` instead of `Popen(["timeout", ...]) + communicate()`, streaming output to the audit `.output`/`.stderr` files as it arrives. Idle, completion-grace, tail-cap, and wall-clock bounds are read from `[timeouts]` in `agents.toml` (`idle_seconds`, `completion_grace_seconds`, `completion_tail_cap_seconds`, existing `default_minutes`). The external `timeout` wrapper is dropped from the real run path. `dispatch.jsonl` `subprocess_returned`/`subprocess_killed` events carry `exit_type`; `jsonl-events.schema.json` gains the `exit_type` enum. The terminal-output detector is adapter-specific (Claude final result line; Codex `turn.completed`). Dry-run output reflects the new argv (no `timeout` prefix). `observe` and the eval bench understand `exit_type`: a `completed_lingering` dispatch is summarised as success, not a failed kill. |
| S3 | Node-layer `exit_type` consumption | `_run_dispatch` propagates `exit_type`, not just the return code. A single shared dispatch-result classifier maps a result to proceed-or-gate(reason); discovery, synthesis, planning, executor, and critique nodes call it instead of each testing `returncode != 0`. `completed_lingering` proceeds; a genuine crash/timeout opens the existing gate. A completed-but-lingering worker no longer opens a `subprocess_crash` gate. |
| S4 | Fault-injection matrix | Integration tests drive the dispatch path end to end with real fake-agent scripts: child holds stdout open after the worker exits; child ignores SIGTERM; slow-drip output under the idle window; zero-output hang; oversized output; double-cancel. Each asserts the correct `exit_type` and that completed work (staged output) is preserved. |

## Prompt sequence

| # | Prompt summary | Files touched | Review checkpoint |
|---|---|---|---|
| 0 | ADR-008: dispatch process supervision. Record the three-clock model, completed-but-lingering classification, process-group reaping, and dropping `timeout(1)` from the classification path. Amend the architecture cross-reference (s11.5 already added) to cite ADR-008 alongside ADR-006. | `docs/adr/008-dispatch-process-supervision.md`, `docs/architecture.md` | Decision and consequences recorded; matches s9 lineage and s11.5 supervision text. |
| 1 | S1: add the `supervise()` primitive with the phase-scoped clocks (idle + wall-clock pre-terminal; completion-grace + tail cap post-terminal), process-group spawn, SIGTERM/SIGKILL escalation, concurrent stdout+stderr drain, bounded streamed capture, completion detection via an injected terminal predicate, and a typed result. Unit tests with real fake scripts for every `exit_type`, including the stderr-fills-pipe deadlock guard, the grace-reset-forever case bounded by the tail cap, and oversized-output truncation. | `src/woof/lib/supervise.py`, `tests/unit/test_supervise.py` | Each `exit_type` is produced by a real fake process; clean exit beats the grace timer; a stderr-heavy fake does not deadlock; a dribbling child is bounded by the tail cap; oversized output is truncated to the cap with the spool preserved; `just check` green. |
| 2 | S2: wire `cmd_dispatch` onto `supervise()`, streaming to the audit files; read `[timeouts]` idle/grace/tail-cap/wall-clock; drop the `timeout` wrapper; emit `exit_type`; extend `jsonl-events.schema.json`; update dry-run argv and dispatcher tests; update `observe` and the eval bench so `completed_lingering` is summarised as success, not a kill. | `src/woof/cli/dispatcher.py`, `schemas/jsonl-events.schema.json`, `src/woof/cli/commands/observe.py`, `src/woof/bench/efficiency.py`, `tests/unit/test_dispatch.py`, `tests/unit/` observe/bench tests | Dispatch events carry a valid `exit_type`; dry-run argv has no `timeout` prefix; a `completed_lingering` event is not counted as a kill in observe or bench; existing dispatch tests pass against the new path. |
| 3 | S3: surface `exit_type` through `_run_dispatch`; add one shared dispatch-result classifier (proceed-or-gate) and route discovery, synthesis, planning, executor, and critique nodes through it. | `src/woof/graph/nodes.py`, `tests/unit/` node tests | A fake completed-but-lingering dispatch advances the graph; a genuine crash still opens a `subprocess_crash` gate; all dispatch-consuming nodes go through the shared classifier. |
| 4 | S4: fault-injection integration matrix end to end through `woof dispatch`, plus double-cancel. | `tests/integration/test_dispatch_supervision.py`, fixture fake-agent scripts | All six pathologies classify correctly and preserve completed work; `just check` green. |

## Risk register

- **Pipe-buffer deadlock.** A naive single-stream read loop can block when the worker fills the stderr pipe while we read stdout (the failure `communicate()` hides today). Drain both streams concurrently (a reader per stream, or `selectors`); the S1 tests include a stderr-heavy fake to prove it.
- **Unbounded in-memory capture.** A verbose worker over a long run could hold large stdout/stderr in memory if `supervise()` buffered everything. Output streams to the audit files as it arrives; the in-memory footprint is a bounded head/tail window plus completion-detection state, capped by max-captured-bytes. The S1 tests assert oversized output truncates to the cap with the spool preserved.
- **Grace reset forever.** A post-terminal child that keeps dribbling output would reset the completion-grace timer indefinitely. The absolute tail cap (`completion_tail_cap_seconds`) bounds the post-terminal phase regardless of activity; an S1 test drives a dribbling child and asserts the tail cap fires.
- **Killing the wrong processes.** Group-killing must target only the worker's own new session group, never the parent Woof process group. Spawn with `start_new_session=True` and kill `os.getpgid(child.pid)` only.
- **Terminal-detection false positives.** A premature "terminal" classification would cut a worker off mid-stream. The completion-grace timer resets on every subsequent output line, so trailing data after the terminal marker is still captured; detection is conservative and adapter-specific.
- **Idle window too tight.** A legitimately quiet long tool call must not trip the idle clock. Default the idle window generously (see D4) and make it configurable per consumer.
- **Fake-script portability.** Fault-injection fakes are POSIX `sh`/Python with explicit signal handling, runnable in CI without `claude`/`codex` on PATH.

## Decisions resolved during the epic

| ID | Decision | Resolution |
|---|---|---|
| D1 | Where does the primitive live? | `src/woof/lib/supervise.py` as a trusted in-process utility returning a dataclass result (architecture s8 data-model boundary: internal carrier, not a durable JSON contract). |
| D2 | How is "terminal output seen" detected without a magic completion string? | Adapter-specific predicate injected by the dispatcher: Claude's final `--output-format json` result line (carries `usage`/`session_id`), Codex's `turn.completed`. The primitive stays agent-agnostic. |
| D3 | Keep `timeout(1)` as an outer belt-and-braces bound? | No. It cannot distinguish completed-lingering from stuck and cannot reap the process group with classification. The wall-clock ceiling is owned in-process. |
| D4 | Default clock values? | `idle_seconds` 600, `completion_grace_seconds` 60, `completion_tail_cap_seconds` 120, wall-clock from existing `default_minutes` (30). All overridable in `[timeouts]`. Idle/grace mirror sandcastle; revisit once real runs show idle behaviour during long tool calls. |
| D5 | Clock precedence across the two phases? | Phase-scoped, per the precedence table in architecture s11.5. A clean exit pre-empts every clock. The terminal marker switches phase. Pre-terminal: idle or wall-clock, first to fire, fail. Post-terminal: completion-grace or tail cap, first to fire, `completed_lingering` success. The wall-clock is a pre-terminal ceiling only; it never converts a finished-but-lingering worker into a failure. This supersedes the earlier "wall-clock absolute regardless of activity" wording. |
| D6 | Output capture bound? | `supervise()` streams stdout/stderr to caller sinks (the dispatcher points them at the audit `.output`/`.stderr` files) and keeps only a bounded head/tail window in memory, capped by max-captured-bytes. Oversized output truncates to the cap with a pointer to the spool, consistent with the s9 audit cap. The default cap is the existing 256 KB audit per-file cap unless a `[timeouts]`/audit override raises it. |

## Out of scope

- `run_id` lineage and file-first replay (E8).
- Resume-to-correct and the graded recovery ladder (E9).
- Plan-graph algorithm swap / NetworkX (E10).
- Parallel/concurrent dispatch via worktrees (deferred; see Settled Choices).
- Any change to the trusted-local runtime-safety posture; E7 is liveness and classification, not a new safety boundary.

## Done definition

- All stories' acceptance criteria met.
- All review checkpoints passed.
- All decisions in the table resolved.
- A hanging-but-done worker advances the graph instead of opening a crash gate, proven by an integration test.
