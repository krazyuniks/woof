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
| S1 | Supervised-subprocess primitive | A `supervise()` primitive runs an arbitrary argv with stdin, draining stdout and stderr concurrently (no pipe-buffer deadlock), on three clocks: idle (no output for N seconds -> kill, fail), completion-grace (terminal output seen but process not exited -> short grace then succeed with captured output), wall-clock (absolute ceiling -> kill, fail). The worker runs in its own process group; on any kill the whole group is signalled SIGTERM then SIGKILL after a grace window. Returns a typed result with `exit_type` in {clean, nonzero, idle_kill, wallclock_timeout, completed_lingering, operator_cancel}, exit code, stdout, stderr, and timing. A clean process exit always wins the race. Cancellation is idempotent and double-signal safe. |
| S2 | Wire `cmd_dispatch` onto the primitive | `cmd_dispatch` uses `supervise()` instead of `Popen(["timeout", ...]) + communicate()`. Idle and completion-grace seconds and the wall-clock ceiling are read from `[timeouts]` in `agents.toml` (`idle_seconds`, `completion_grace_seconds`, existing `default_minutes`). The external `timeout` wrapper is dropped from the real run path. `dispatch.jsonl` `subprocess_returned`/`subprocess_killed` events carry `exit_type` and the completion classification; `jsonl-events.schema.json` gains the `exit_type` enum. The terminal-output detector is adapter-specific (Claude final result line; Codex `turn.completed`). Dry-run output reflects the new argv (no `timeout` prefix). |
| S3 | Node-layer `exit_type` consumption | `_run_dispatch` propagates `exit_type`, not just the return code. Dispatch-consuming nodes distinguish `completed_lingering` (treat as success, proceed) from a genuine crash/timeout (open the existing gate). A completed-but-lingering worker no longer opens a `subprocess_crash` gate. |
| S4 | Fault-injection matrix | Integration tests drive the dispatch path end to end with real fake-agent scripts: child holds stdout open after the worker exits; child ignores SIGTERM; slow-drip output under the idle window; zero-output hang; oversized output; double-cancel. Each asserts the correct `exit_type` and that completed work (staged output) is preserved. |

## Prompt sequence

| # | Prompt summary | Files touched | Review checkpoint |
|---|---|---|---|
| 0 | ADR-008: dispatch process supervision. Record the three-clock model, completed-but-lingering classification, process-group reaping, and dropping `timeout(1)` from the classification path. Amend the architecture cross-reference (s11.5 already added) to cite ADR-008 alongside ADR-006. | `docs/adr/008-dispatch-process-supervision.md`, `docs/architecture.md` | Decision and consequences recorded; matches s9 lineage and s11.5 supervision text. |
| 1 | S1: add the `supervise()` primitive with the three clocks, process-group spawn, SIGTERM/SIGKILL escalation, concurrent stdout+stderr drain, completion detection via an injected terminal predicate, and a typed result. Unit tests with real fake scripts for every `exit_type`, including the stderr-fills-pipe deadlock guard. | `src/woof/lib/supervise.py`, `tests/unit/test_supervise.py` | Each `exit_type` is produced by a real fake process; clean exit beats the grace timer; a stderr-heavy fake does not deadlock; `just check` green. |
| 2 | S2: wire `cmd_dispatch` onto `supervise()`; read `[timeouts]` idle/completion/wall-clock; drop the `timeout` wrapper; emit `exit_type` and completion fields; extend `jsonl-events.schema.json`; update dry-run argv and dispatcher tests. | `src/woof/cli/dispatcher.py`, `schemas/jsonl-events.schema.json`, `tests/unit/test_dispatch.py` | Dispatch events carry a valid `exit_type`; dry-run argv has no `timeout` prefix; existing dispatch tests pass against the new path. |
| 3 | S3: surface `exit_type` through `_run_dispatch` and update node consumers so `completed_lingering` proceeds and genuine failures still gate. | `src/woof/graph/nodes.py`, `tests/unit/` node tests | A fake completed-but-lingering dispatch advances the graph; a genuine crash still opens a `subprocess_crash` gate. |
| 4 | S4: fault-injection integration matrix end to end through `woof dispatch`, plus double-cancel. | `tests/integration/test_dispatch_supervision.py`, fixture fake-agent scripts | All six pathologies classify correctly and preserve completed work; `just check` green. |

## Risk register

- **Pipe-buffer deadlock.** A naive single-stream read loop can block when the worker fills the stderr pipe while we read stdout (the failure `communicate()` hides today). Drain both streams concurrently (a reader per stream, or `selectors`); the S1 tests include a stderr-heavy fake to prove it.
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
| D4 | Default clock values? | `idle_seconds` 600, `completion_grace_seconds` 60, wall-clock from existing `default_minutes` (30). All overridable in `[timeouts]`. Mirrors sandcastle's defaults; revisit once real runs show idle behaviour during long tool calls. |
| D5 | Wall-clock versus completed-lingering precedence? | Once the terminal marker is seen, the completion-grace timer governs and the outcome is `completed_lingering` on grace expiry (group killed). The wall-clock ceiling only fails a worker that has not reached its terminal marker. |

## Out of scope

- `run_id` lineage and file-first replay (E8).
- Resume-to-correct and the graded recovery ladder (E8).
- Parallel/concurrent dispatch via worktrees (deferred; see Settled Choices).
- Any change to the trusted-local runtime-safety posture; E7 is liveness and classification, not a new safety boundary.

## Done definition

- All stories' acceptance criteria met.
- All review checkpoints passed.
- All decisions in the table resolved.
- A hanging-but-done worker advances the graph instead of opening a crash gate, proven by an integration test.
