from __future__ import annotations

import os
import sys
import textwrap
import threading
import time
from pathlib import Path

from woof.lib.supervise import ExitType, supervise

DEFAULT_SUPERVISE_KWARGS = {
    "idle_seconds": 0.5,
    "wallclock_seconds": 1.0,
    "completion_grace_seconds": 0.5,
    "completion_tail_cap_seconds": 1.0,
    "max_captured_bytes": 4096,
    "sigkill_grace_seconds": 0.2,
}


def _fake(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(
        "#!/usr/bin/env python3\n" + textwrap.dedent(body).lstrip(),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _run_fake(path: Path, *args: str, **kwargs: object):
    params = DEFAULT_SUPERVISE_KWARGS | kwargs
    return supervise(
        [sys.executable, str(path), *args],
        is_terminal=lambda line: "TERMINAL" in line,
        **params,
    )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _assert_pid_gone(pid_path: Path, *, timeout: float = 2.0) -> None:
    pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return
        time.sleep(0.02)
    assert not _pid_exists(pid), f"pid {pid} was not reaped"


def test_clean_process_with_terminal_marker_exits_cleanly(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        "clean.py",
        """
        print("working", flush=True)
        print("TERMINAL: done", flush=True)
        """,
    )

    result = _run_fake(script)

    assert result.exit_type is ExitType.CLEAN
    assert result.exit_code == 0
    assert result.terminal_seen is True
    assert "TERMINAL: done" in result.stdout
    assert result.stderr == ""


def test_nonzero_exit_records_return_code(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        "nonzero.py",
        """
        import sys

        print("before failure", flush=True)
        sys.exit(3)
        """,
    )

    result = _run_fake(script)

    assert result.exit_type is ExitType.NONZERO
    assert result.exit_code == 3
    assert "before failure" in result.stdout


def test_stdin_is_written_while_output_is_drained(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        "stdin.py",
        """
        import sys

        print("ready", flush=True)
        data = sys.stdin.read()
        print(f"got:{data}", flush=True)
        print("TERMINAL: done", flush=True)
        """,
    )

    result = _run_fake(script, stdin="payload")

    assert result.exit_type is ExitType.CLEAN
    assert "ready" in result.stdout
    assert "got:payload" in result.stdout
    assert result.terminal_seen is True


def test_idle_kill_fires_before_wallclock_when_process_goes_silent(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        "idle.py",
        """
        import time

        print("one line", flush=True)
        time.sleep(5)
        """,
    )

    result = _run_fake(script, idle_seconds=0.3, wallclock_seconds=3.0)

    assert result.exit_type is ExitType.IDLE_KILL
    assert result.duration_ms < 1500
    assert "one line" in result.stdout


def test_wallclock_timeout_fires_while_process_keeps_emitting_output(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        "wallclock.py",
        """
        import time

        while True:
            print("still working", flush=True)
            time.sleep(0.1)
        """,
    )

    result = _run_fake(script, idle_seconds=0.3, wallclock_seconds=0.75)

    assert result.exit_type is ExitType.WALLCLOCK_TIMEOUT
    assert result.duration_ms < 1600
    assert "still working" in result.stdout


def test_completed_lingering_when_parent_stays_alive_after_terminal_marker(
    tmp_path: Path,
) -> None:
    script = _fake(
        tmp_path,
        "linger_parent.py",
        """
        import time

        print("TERMINAL: done", flush=True)
        time.sleep(5)
        """,
    )

    result = _run_fake(
        script,
        wallclock_seconds=5.0,
        completion_grace_seconds=0.3,
        completion_tail_cap_seconds=2.0,
    )

    assert result.exit_type is ExitType.COMPLETED_LINGERING
    assert result.terminal_seen is True
    assert result.duration_ms < 1400
    assert result.signalled == "SIGTERM"


def test_sigkill_escalates_when_process_ignores_sigterm(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        "ignore_sigterm.py",
        """
        import signal
        import time

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("TERMINAL: done", flush=True)
        time.sleep(5)
        """,
    )

    result = _run_fake(
        script,
        wallclock_seconds=5.0,
        completion_grace_seconds=0.2,
        completion_tail_cap_seconds=2.0,
        sigkill_grace_seconds=0.1,
    )

    assert result.exit_type is ExitType.COMPLETED_LINGERING
    assert result.terminal_seen is True
    assert result.signalled == "SIGKILL"
    assert result.duration_ms < 1200


def test_process_exit_wins_when_grandchild_holds_stdout_pipe_open(tmp_path: Path) -> None:
    pid_file = tmp_path / "sleeper.pid"
    script = _fake(
        tmp_path,
        "held_pipe.py",
        """
        import subprocess
        import sys

        print("TERMINAL: done", flush=True)
        child = subprocess.Popen(["sleep", "5"])
        pathlib = __import__("pathlib")
        pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
        sys.exit(0)
        """,
    )

    started = time.monotonic()
    result = _run_fake(
        script,
        str(pid_file),
        wallclock_seconds=5.0,
        completion_grace_seconds=0.3,
        completion_tail_cap_seconds=2.0,
    )
    elapsed = time.monotonic() - started

    assert result.exit_type is ExitType.CLEAN
    assert result.exit_code == 0
    assert result.terminal_seen is True
    assert elapsed < 1.5
    _assert_pid_gone(pid_file)


def test_stderr_heavy_process_does_not_deadlock(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        "stderr_heavy.py",
        """
        import sys

        for idx in range(2048):
            print("E" * 1024, file=sys.stderr, flush=True)
            if idx % 128 == 0:
                print(f"stdout {idx}", flush=True)
        print("TERMINAL: done", flush=True)
        """,
    )

    result = _run_fake(script, max_captured_bytes=8192, wallclock_seconds=5.0)

    assert result.exit_type is ExitType.CLEAN
    assert result.terminal_seen is True
    assert "TERMINAL: done" in result.stdout
    assert result.stderr_truncated is True


def test_post_terminal_activity_resets_grace_but_tail_cap_bounds_it(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        "dribble.py",
        """
        import time

        print("TERMINAL: done", flush=True)
        while True:
            print("tail", flush=True)
            time.sleep(0.1)
        """,
    )

    result = _run_fake(
        script,
        wallclock_seconds=5.0,
        completion_grace_seconds=0.3,
        completion_tail_cap_seconds=0.8,
    )

    assert result.exit_type is ExitType.COMPLETED_LINGERING
    assert result.terminal_seen is True
    assert 650 <= result.duration_ms < 1600


def test_oversized_output_is_truncated_in_memory_but_spooled_in_full(tmp_path: Path) -> None:
    stdout_path = tmp_path / "stdout.spool"
    script = _fake(
        tmp_path,
        "oversized.py",
        """
        for idx in range(200):
            print(f"line-{idx:03d}-" + ("x" * 40), flush=True)
        print("TERMINAL: done", flush=True)
        """,
    )

    result = _run_fake(script, max_captured_bytes=256, stdout_path=stdout_path)

    spooled = stdout_path.read_text(encoding="utf-8")
    assert result.exit_type is ExitType.CLEAN
    assert result.stdout_truncated is True
    assert len(result.stdout.encode("utf-8")) <= 256
    assert result.stdout_path == stdout_path
    assert spooled.startswith("line-000-")
    assert spooled.endswith("TERMINAL: done\n")
    assert len(spooled.encode("utf-8")) > 256


def test_operator_cancel_kills_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    cancel = threading.Event()
    script = _fake(
        tmp_path,
        "cancel.py",
        """
        import pathlib
        import subprocess
        import sys
        import time

        child = subprocess.Popen(["sleep", "5"])
        pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
        print("started", flush=True)
        time.sleep(5)
        """,
    )

    def cancel_twice() -> None:
        cancel.set()
        cancel.set()

    timer = threading.Timer(0.2, cancel_twice)
    timer.start()
    try:
        result = _run_fake(
            script,
            str(pid_file),
            cancel=cancel,
            idle_seconds=2.0,
            wallclock_seconds=5.0,
        )
    finally:
        timer.cancel()

    assert result.exit_type is ExitType.OPERATOR_CANCEL
    assert result.duration_ms < 1400
    assert result.signalled == "SIGTERM"
    _assert_pid_gone(pid_file)
