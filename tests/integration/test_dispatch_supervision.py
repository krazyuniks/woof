"""Dispatch supervision fault-injection tests through the real CLI."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import textwrap
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
WOOF_VALIDATE = [str(WOOF_BIN), "validate", "--schema", "jsonl-events"]

pytestmark = pytest.mark.host_only


@dataclass(frozen=True)
class DispatchArtifacts:
    proc: subprocess.CompletedProcess[str]
    jsonl: Path
    events: list[dict]
    meta: dict
    output_file: Path
    stderr_file: Path


def _terminal_event(tokens_in: int = 1, tokens_out: int = 1) -> str:
    return json.dumps(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": tokens_in,
                "cached_input_tokens": 0,
                "output_tokens": tokens_out,
                "reasoning_output_tokens": 0,
            },
        }
    )


def _write_project(
    tmp_path: Path,
    *,
    default_minutes: float = 0.05,
    idle_seconds: float = 0.3,
    completion_grace_seconds: float = 0.2,
    completion_tail_cap_seconds: float = 0.6,
    audit_max_bytes: int = 4096,
) -> Path:
    project = tmp_path / "project"
    woof_dir = project / ".woof"
    woof_dir.mkdir(parents=True)
    (woof_dir / "agents.toml").write_text(
        f"""\
[audit]
max_bytes = {audit_max_bytes}

[timeouts]
default_minutes = {default_minutes}
idle_seconds = {idle_seconds}
completion_grace_seconds = {completion_grace_seconds}
completion_tail_cap_seconds = {completion_tail_cap_seconds}

[roles.primary]
adapter = "codex"
model = "gpt-5.5"
effort = "low"
""",
        encoding="utf-8",
    )
    return project


def _write_fake_agent(bin_dir: Path, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text(
        "#!/usr/bin/env python3\n" + textwrap.dedent(body).lstrip(),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _env(bin_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(bin_dir.parent)),
    }


def _run_dispatch(
    project: Path,
    bin_dir: Path,
    *,
    epic: int,
    timeout: float = 8.0,
) -> DispatchArtifacts:
    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "primary", "--epic", str(epic)],
        capture_output=True,
        text=True,
        input="run the fake agent\n",
        cwd=project,
        env=_env(bin_dir),
        timeout=timeout,
    )
    return _collect_dispatch(project, epic, proc)


def _collect_dispatch(
    project: Path,
    epic: int,
    proc: subprocess.CompletedProcess[str],
) -> DispatchArtifacts:
    epic_dir = project / ".woof" / "epics" / f"E{epic}"
    jsonl = epic_dir / "dispatch.jsonl"
    events = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    audit_dir = epic_dir / "audit"
    meta_file = next(audit_dir.glob("*.meta"))
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    return DispatchArtifacts(
        proc=proc,
        jsonl=jsonl,
        events=events,
        meta=meta,
        output_file=meta_file.with_suffix(".output"),
        stderr_file=meta_file.with_suffix(".stderr"),
    )


def _returned(events: list[dict]) -> dict:
    returned = [event for event in events if event["event"] == "subprocess_returned"]
    assert len(returned) == 1
    return returned[0]


def _kills(events: list[dict]) -> list[dict]:
    return [event for event in events if event["event"] == "subprocess_killed"]


def _assert_dispatch_audit(
    artifacts: DispatchArtifacts,
    *,
    exit_type: str,
    returncode: int,
    exit_code: int,
    timed_out: bool,
    terminal_seen: bool,
    killed_reason: str | None = None,
    killed_signal: str | None = None,
) -> None:
    assert artifacts.proc.returncode == returncode, artifacts.proc.stderr
    returned = _returned(artifacts.events)
    assert returned["exit_type"] == artifacts.meta["exit_type"] == exit_type
    assert returned["exit_code"] == artifacts.meta["exit_code"] == exit_code
    assert returned["timed_out"] == artifacts.meta["timed_out"] == timed_out
    assert returned["terminal_seen"] == artifacts.meta["terminal_seen"] == terminal_seen

    killed = _kills(artifacts.events)
    if killed_reason is None:
        assert killed == []
    else:
        assert len(killed) == 1
        assert killed[0]["exit_type"] == exit_type
        assert killed[0]["reason"] == killed_reason
        assert killed[0]["signal"] == killed_signal

    validate = subprocess.run(
        [*WOOF_VALIDATE, str(artifacts.jsonl)],
        capture_output=True,
        text=True,
    )
    assert validate.returncode == 0, validate.stdout + validate.stderr


def test_completed_lingering_with_stdout_holder_preserves_completed_work(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    bin_dir = tmp_path / "bin"
    terminal = _terminal_event(tokens_in=3, tokens_out=5)
    _write_fake_agent(
        bin_dir,
        f"""
        import pathlib
        import subprocess
        import sys
        import time

        pathlib.Path("completed.txt").write_text("finished\\n", encoding="utf-8")
        print("completed work before terminal", flush=True)
        print({terminal!r}, flush=True)
        time.sleep(0.05)
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        sys.exit(0)
        """,
    )

    artifacts = _run_dispatch(project, bin_dir, epic=1)

    _assert_dispatch_audit(
        artifacts,
        exit_type="completed_lingering",
        returncode=0,
        exit_code=0,
        timed_out=False,
        terminal_seen=True,
        killed_reason="completed_lingering",
        killed_signal="SIGTERM",
    )
    assert (project / "completed.txt").read_text(encoding="utf-8") == "finished\n"
    output = artifacts.output_file.read_text(encoding="utf-8")
    assert "completed work before terminal" in output
    assert terminal in output
    assert artifacts.meta["tokens"] == {
        "tokens_in": 3,
        "tokens_out": 5,
        "cache_read_tokens": 0,
    }


def test_sigterm_ignoring_child_escalates_to_sigkill(tmp_path: Path) -> None:
    project = _write_project(tmp_path, idle_seconds=0.2)
    bin_dir = tmp_path / "bin"
    _write_fake_agent(
        bin_dir,
        """
        import signal
        import time

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("started and ignoring sigterm", flush=True)
        while True:
            time.sleep(1)
        """,
    )

    artifacts = _run_dispatch(project, bin_dir, epic=2)

    _assert_dispatch_audit(
        artifacts,
        exit_type="idle_kill",
        returncode=124,
        exit_code=124,
        timed_out=True,
        terminal_seen=False,
        killed_reason="idle_kill",
        killed_signal="SIGKILL",
    )


def test_slow_drip_output_defers_idle_until_wallclock_timeout(tmp_path: Path) -> None:
    project = _write_project(tmp_path, default_minutes=0.012, idle_seconds=0.3)
    bin_dir = tmp_path / "bin"
    _write_fake_agent(
        bin_dir,
        """
        import time

        while True:
            print("drip", flush=True)
            time.sleep(0.1)
        """,
    )

    artifacts = _run_dispatch(project, bin_dir, epic=3)

    _assert_dispatch_audit(
        artifacts,
        exit_type="wallclock_timeout",
        returncode=124,
        exit_code=124,
        timed_out=True,
        terminal_seen=False,
        killed_reason="wallclock_timeout",
        killed_signal="SIGTERM",
    )
    assert "drip" in artifacts.output_file.read_text(encoding="utf-8")


def test_zero_output_hang_is_idle_kill(tmp_path: Path) -> None:
    project = _write_project(tmp_path, default_minutes=0.05, idle_seconds=0.2)
    bin_dir = tmp_path / "bin"
    _write_fake_agent(
        bin_dir,
        """
        import sys
        import time

        sys.stdin.read()
        time.sleep(30)
        """,
    )

    artifacts = _run_dispatch(project, bin_dir, epic=4)

    _assert_dispatch_audit(
        artifacts,
        exit_type="idle_kill",
        returncode=124,
        exit_code=124,
        timed_out=True,
        terminal_seen=False,
        killed_reason="idle_kill",
        killed_signal="SIGTERM",
    )
    assert artifacts.output_file.read_text(encoding="utf-8") == ""


def test_oversized_output_uses_bounded_capture_and_preserves_spool_files(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path, audit_max_bytes=512)
    bin_dir = tmp_path / "bin"
    terminal = _terminal_event(tokens_in=7, tokens_out=11)
    _write_fake_agent(
        bin_dir,
        f"""
        for idx in range(250):
            print(f"line-{{idx:03d}}-" + ("x" * 80), flush=True)
        print({terminal!r}, flush=True)
        """,
    )

    artifacts = _run_dispatch(project, bin_dir, epic=5)

    _assert_dispatch_audit(
        artifacts,
        exit_type="clean",
        returncode=0,
        exit_code=0,
        timed_out=False,
        terminal_seen=True,
    )
    assert artifacts.meta["stdout_truncated"] is True
    assert artifacts.output_file.is_file()
    assert artifacts.stderr_file.is_file()
    output = artifacts.output_file.read_text(encoding="utf-8")
    assert output.startswith("line-000-")
    assert output.endswith(terminal + "\n")
    assert artifacts.output_file.stat().st_size > 512
    assert artifacts.meta["output_bytes"] == artifacts.output_file.stat().st_size
    assert artifacts.meta["tokens"] == {
        "tokens_in": 7,
        "tokens_out": 11,
        "cache_read_tokens": 0,
    }


def test_double_cancel_records_operator_cancel_once(tmp_path: Path) -> None:
    project = _write_project(tmp_path, default_minutes=0.2, idle_seconds=10.0)
    bin_dir = tmp_path / "bin"
    started = project / "agent-started"
    _write_fake_agent(
        bin_dir,
        f"""
        import pathlib
        import signal
        import time

        def on_term(signum, frame):
            time.sleep(0.4)
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, on_term)
        pathlib.Path({str(started)!r}).write_text("started\\n", encoding="utf-8")
        print("waiting for operator cancel", flush=True)
        while True:
            time.sleep(1)
        """,
    )

    proc = subprocess.Popen(
        [str(WOOF_BIN), "dispatch", "--role", "primary", "--epic", "6"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=project,
        env=_env(bin_dir),
    )
    assert proc.stdin is not None
    proc.stdin.write("cancel me\n")
    proc.stdin.close()

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not started.exists():
        time.sleep(0.02)
    assert started.exists()

    proc.send_signal(signal.SIGINT)
    time.sleep(0.05)
    with suppress(ProcessLookupError):
        proc.send_signal(signal.SIGINT)

    returncode = proc.wait(timeout=6)
    stdout = proc.stdout.read() if proc.stdout is not None else ""
    stderr = proc.stderr.read() if proc.stderr is not None else ""
    completed = subprocess.CompletedProcess(proc.args, returncode, stdout, stderr)
    artifacts = _collect_dispatch(project, 6, completed)

    _assert_dispatch_audit(
        artifacts,
        exit_type="operator_cancel",
        returncode=130,
        exit_code=130,
        timed_out=False,
        terminal_seen=False,
        killed_reason="manual_cancel",
        killed_signal="SIGTERM",
    )
    assert "Traceback" not in artifacts.proc.stderr
