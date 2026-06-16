"""Phase-scoped subprocess supervision for dispatched workers."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

SignalName = Literal["SIGTERM", "SIGKILL"]
StreamName = Literal["stdout", "stderr"]


class ExitType(StrEnum):
    CLEAN = "clean"
    NONZERO = "nonzero"
    IDLE_KILL = "idle_kill"
    WALLCLOCK_TIMEOUT = "wallclock_timeout"
    COMPLETED_LINGERING = "completed_lingering"
    OPERATOR_CANCEL = "operator_cancel"


@dataclass(frozen=True)
class SupervisedResult:
    pid: int
    exit_type: ExitType
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_path: Path | None
    stderr_path: Path | None
    terminal_seen: bool
    signalled: SignalName | None
    duration_ms: int


@dataclass
class _SharedState:
    last_activity: float
    terminal_seen: bool = False
    terminal_at: float | None = None
    stdout_done: bool = False
    stderr_done: bool = False
    predicate_error: Exception | None = None


class _BoundedCapture:
    _MARKER = b"\n[... truncated ...]\n"

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        if max_bytes > len(self._MARKER) + 2:
            view_budget = max_bytes - len(self._MARKER)
            self._marker = self._MARKER
        else:
            view_budget = max_bytes
            self._marker = b""
        self._head_cap = max(view_budget // 2, 0)
        self._tail_cap = max(view_budget - self._head_cap, 0)
        self._data = bytearray()
        self._head = bytearray()
        self._tail = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._max_bytes <= 0:
            self.truncated = True
            return

        if not self.truncated:
            if len(self._data) + len(chunk) <= self._max_bytes:
                self._data.extend(chunk)
                return
            combined = bytes(self._data) + chunk
            self._data.clear()
            self.truncated = True
            if self._head_cap:
                self._head.extend(combined[: self._head_cap])
            if self._tail_cap:
                self._tail.extend(combined[-self._tail_cap :])
            return

        if self._tail_cap:
            self._tail.extend(chunk)
            overflow = len(self._tail) - self._tail_cap
            if overflow > 0:
                del self._tail[:overflow]

    def bytes(self) -> bytes:
        if not self.truncated:
            return bytes(self._data)
        return bytes(self._head) + self._marker + bytes(self._tail)

    def text(self) -> str:
        return self.bytes().decode("utf-8", errors="replace")


def supervise(
    argv: Sequence[str | os.PathLike[str]],
    *,
    stdin: str | bytes | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    is_terminal: Callable[[str], bool],
    idle_seconds: float,
    wallclock_seconds: float,
    completion_grace_seconds: float,
    completion_tail_cap_seconds: float,
    max_captured_bytes: int,
    stdout_path: str | os.PathLike[str] | None = None,
    stderr_path: str | os.PathLike[str] | None = None,
    on_spawn: Callable[[int], None] | None = None,
    sigkill_grace_seconds: float = 5.0,
    cancel: threading.Event | None = None,
) -> SupervisedResult:
    """Run ``argv`` under process-group supervision and phase-scoped clocks."""

    argv_list = [os.fspath(part) for part in argv]
    if not argv_list:
        raise ValueError("argv must not be empty")
    _validate_non_negative("idle_seconds", idle_seconds)
    _validate_non_negative("wallclock_seconds", wallclock_seconds)
    _validate_non_negative("completion_grace_seconds", completion_grace_seconds)
    _validate_non_negative("completion_tail_cap_seconds", completion_tail_cap_seconds)
    _validate_non_negative("sigkill_grace_seconds", sigkill_grace_seconds)
    if max_captured_bytes < 0:
        raise ValueError("max_captured_bytes must be >= 0")

    stdout_spool = Path(stdout_path) if stdout_path is not None else None
    stderr_spool = Path(stderr_path) if stderr_path is not None else None
    start = time.monotonic()
    state = _SharedState(last_activity=start)
    state_lock = threading.Lock()
    stdout_capture = _BoundedCapture(max_captured_bytes)
    stderr_capture = _BoundedCapture(max_captured_bytes)

    proc = subprocess.Popen(
        argv_list,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.fspath(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        start_new_session=True,
    )
    pgid = _process_group_id(proc)
    if on_spawn is not None:
        try:
            on_spawn(proc.pid)
        except Exception:
            if pgid is not None:
                with suppress(ProcessLookupError):
                    os.killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=max(sigkill_grace_seconds, 0.1))
            except subprocess.TimeoutExpired:
                if pgid is not None:
                    with suppress(ProcessLookupError):
                        os.killpg(pgid, signal.SIGKILL)
                with suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=max(sigkill_grace_seconds, 0.1))
            raise

    signalled: SignalName | None = None
    term_sent = False
    kill_sent = False

    def readers_done() -> bool:
        with state_lock:
            return state.stdout_done and state.stderr_done

    def send_group(sig: signal.Signals) -> bool:
        if pgid is None:
            return False
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return False
        return True

    def terminate_group() -> None:
        nonlocal kill_sent, signalled, term_sent
        if not term_sent:
            if send_group(signal.SIGTERM):
                signalled = "SIGTERM"
            term_sent = True

        deadline = time.monotonic() + sigkill_grace_seconds
        while time.monotonic() < deadline:
            if proc.poll() is not None and readers_done():
                return
            time.sleep(min(0.02, max(deadline - time.monotonic(), 0.0)))

        if not kill_sent:
            if send_group(signal.SIGKILL):
                signalled = "SIGKILL"
            kill_sent = True

        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=max(sigkill_grace_seconds, 0.1))

    assert proc.stdout is not None
    assert proc.stderr is not None
    assert proc.stdin is not None
    stdout_thread = threading.Thread(
        target=_read_stream,
        args=(
            "stdout",
            proc.stdout,
            stdout_spool,
            stdout_capture,
            is_terminal,
            state,
            state_lock,
        ),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_stream,
        args=(
            "stderr",
            proc.stderr,
            stderr_spool,
            stderr_capture,
            is_terminal,
            state,
            state_lock,
        ),
        daemon=True,
    )
    stdin_thread = threading.Thread(
        target=_write_stdin,
        args=(proc.stdin, stdin),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    stdin_thread.start()

    exit_type: ExitType | None = None
    exit_code: int | None = None
    process_exit_at: float | None = None
    try:
        while True:
            return_code = proc.poll()
            if return_code is not None:
                exit_code = return_code
                if process_exit_at is None:
                    process_exit_at = time.monotonic()
                with state_lock:
                    terminal_seen = state.terminal_seen
                    streams_done = state.stdout_done and state.stderr_done
                if (
                    not terminal_seen
                    and not streams_done
                    and time.monotonic() - process_exit_at < 0.1
                ):
                    time.sleep(0.01)
                    continue
                if return_code == 0 and terminal_seen and not streams_done:
                    exit_type = ExitType.COMPLETED_LINGERING
                else:
                    exit_type = ExitType.CLEAN if return_code == 0 else ExitType.NONZERO
                break

            with state_lock:
                predicate_error = state.predicate_error
                terminal_seen = state.terminal_seen
                terminal_at = state.terminal_at
                last_activity = state.last_activity

            if predicate_error is not None:
                terminate_group()
                _join_threads(stdout_thread, stderr_thread, stdin_thread)
                raise predicate_error

            if cancel is not None and cancel.is_set():
                return_code = proc.poll()
                if return_code is not None:
                    exit_code = return_code
                    exit_type = ExitType.CLEAN if return_code == 0 else ExitType.NONZERO
                else:
                    exit_type = ExitType.OPERATOR_CANCEL
                break

            now = time.monotonic()
            if terminal_seen:
                assert terminal_at is not None
                grace_deadline = last_activity + completion_grace_seconds
                tail_deadline = terminal_at + completion_tail_cap_seconds
                if now >= min(grace_deadline, tail_deadline):
                    return_code = proc.poll()
                    if return_code is not None:
                        exit_code = return_code
                        exit_type = ExitType.CLEAN if return_code == 0 else ExitType.NONZERO
                    else:
                        exit_type = ExitType.COMPLETED_LINGERING
                    break
                next_deadline = min(grace_deadline, tail_deadline)
            else:
                idle_deadline = last_activity + idle_seconds
                wallclock_deadline = start + wallclock_seconds
                first_deadline = min(idle_deadline, wallclock_deadline)
                if now >= first_deadline:
                    return_code = proc.poll()
                    if return_code is not None:
                        exit_code = return_code
                        exit_type = ExitType.CLEAN if return_code == 0 else ExitType.NONZERO
                    else:
                        if idle_deadline <= wallclock_deadline:
                            exit_type = ExitType.IDLE_KILL
                        else:
                            exit_type = ExitType.WALLCLOCK_TIMEOUT
                    break
                next_deadline = first_deadline

            time.sleep(min(0.02, max(next_deadline - now, 0.0)))
    finally:
        if exit_type is None and proc.poll() is None:
            terminate_group()

    terminate_group()
    if exit_type not in {ExitType.CLEAN, ExitType.NONZERO}:
        exit_code = proc.poll()

    _join_threads(stdout_thread, stderr_thread, stdin_thread)

    with state_lock:
        terminal_seen = state.terminal_seen

    assert exit_type is not None
    duration_ms = int((time.monotonic() - start) * 1000)
    return SupervisedResult(
        pid=proc.pid,
        exit_type=exit_type,
        exit_code=exit_code,
        stdout=stdout_capture.text(),
        stderr=stderr_capture.text(),
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
        stdout_path=stdout_spool,
        stderr_path=stderr_spool,
        terminal_seen=terminal_seen,
        signalled=signalled,
        duration_ms=duration_ms,
    )


def _read_stream(
    stream: StreamName,
    pipe,
    spool_path: Path | None,
    capture: _BoundedCapture,
    is_terminal: Callable[[str], bool],
    state: _SharedState,
    state_lock: threading.Lock,
) -> None:
    spool = None
    try:
        if spool_path is not None:
            spool_path.parent.mkdir(parents=True, exist_ok=True)
            spool = spool_path.open("wb")
        while True:
            chunk = pipe.readline()
            if not chunk:
                return
            if spool is not None:
                spool.write(chunk)
                spool.flush()
            capture.append(chunk)
            now = time.monotonic()
            terminal = False
            predicate_error: Exception | None = None
            if stream == "stdout":
                try:
                    terminal = is_terminal(chunk.decode("utf-8", errors="replace"))
                except Exception as exc:
                    predicate_error = exc
            with state_lock:
                state.last_activity = max(state.last_activity, now)
                if terminal and not state.terminal_seen:
                    state.terminal_seen = True
                    state.terminal_at = now
                if predicate_error is not None and state.predicate_error is None:
                    state.predicate_error = predicate_error
    finally:
        if spool is not None:
            spool.close()
        with suppress(OSError):
            pipe.close()
        with state_lock:
            if stream == "stdout":
                state.stdout_done = True
            else:
                state.stderr_done = True


def _write_stdin(pipe, stdin: str | bytes | None) -> None:
    try:
        if stdin is not None:
            data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
            pipe.write(data)
            pipe.flush()
    except BrokenPipeError:
        pass
    except OSError:
        pass
    finally:
        with suppress(OSError):
            pipe.close()


def _join_threads(*threads: threading.Thread) -> None:
    for thread in threads:
        thread.join(timeout=0.5)


def _process_group_id(proc: subprocess.Popen[bytes]) -> int | None:
    try:
        return os.getpgid(proc.pid)
    except ProcessLookupError:
        return None


def _validate_non_negative(name: str, value: float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
