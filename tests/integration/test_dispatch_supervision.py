"""Dispatch integration tests through the tmux harness boundary."""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.support import DEFAULT_PROJECT_KEY, seed_project_config
from woof import state

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
WOOF_VALIDATE = [str(WOOF_BIN), "validate", "--schema", "jsonl-events"]

pytestmark = [pytest.mark.host_only, pytest.mark.tmux_substrate]


def _write_project(tmp_path: Path, *, default_minutes: float = 0.05) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    seed_project_config(
        {
            "default_run_profile": "integration",
            "verification": {"command": "true", "timeout_seconds": 30},
            "profiles": {"B": {"commit": False, "push": False}},
            "run_profiles": {
                "default": None,
                "integration": {
                    "producer": {"harness": "codex", "model": "gpt-5.5", "effort": "low"},
                    "reviewer": {"harness": "codex", "model": "gpt-5.5", "effort": "low"},
                },
            },
            "checks": {"floor": ["quality-gates"]},
            "cartography": {"floor": "none"},
            "dispatch": {"timeouts": {"default_minutes": default_minutes}},
            "review_valve": {"every_n_work_units": 5, "end_of_epic": False},
            "drain": {"merge_after_ready_pr": True},
        }
    )
    return project


def _write_codex_stub(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body).lstrip(), encoding="utf-8")
    script.chmod(0o755)


def _env(bin_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(bin_dir.parent)),
    }


def _events(project: Path, epic: int) -> list[dict]:
    """Dispatch events, read from the operator home where the engine records them."""

    jsonl = state.dispatch_events_path(DEFAULT_PROJECT_KEY, epic)
    return [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]


def _meta(project: Path, epic: int) -> dict:
    audit_dir = state.audit_dir(DEFAULT_PROJECT_KEY, epic)
    return json.loads(next(audit_dir.glob("*.meta")).read_text(encoding="utf-8"))


def test_tmux_dispatch_captures_structured_result_and_prompt_file(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    bin_dir = tmp_path / "bin"
    prompt_copy = tmp_path / "prompt-copy.txt"
    payload = json.dumps(
        {
            "verdict": "pass",
            "evidence": "S1",
            "usage": {"tokens_in": 3, "tokens_out": 5},
            "session": {"thread_id": "thr-integration"},
        }
    )
    _write_codex_stub(
        bin_dir,
        f"""
        import pathlib
        import re
        import sys

        print("ready > ", flush=True)
        buf = ""
        for line in sys.stdin:
            buf += line
            prompt = re.search(r"(\\S+/prompt\\.txt)", buf)
            answer = re.search(r"(\\S+/answer\\.txt)", buf)
            done = re.search(r"(\\S+/answer\\.done)", buf)
            if prompt and answer and done:
                original = pathlib.Path(prompt.group(1)).read_text(encoding="utf-8")
                pathlib.Path({str(prompt_copy)!r}).write_text(original, encoding="utf-8")
                pathlib.Path(answer.group(1)).write_text({payload!r}, encoding="utf-8")
                pathlib.Path(done.group(1)).write_text("DONE", encoding="utf-8")
                break
        """,
    )

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "primary", "--epic", "1"],
        capture_output=True,
        text=True,
        input="run the fake agent\n",
        cwd=project,
        env=_env(bin_dir),
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert prompt_copy.read_text(encoding="utf-8") == "run the fake agent\n"
    events = _events(project, 1)
    returned = next(event for event in events if event["event"] == "subprocess_returned")
    assert returned["exit_type"] == "clean"
    assert returned["prompt_transport"] == "harness_prompt_file"
    assert returned["argv"][-1] == "<prompt:file>"
    assert returned["verdict"] == "pass"
    assert returned["evidence"] == "S1"
    assert returned["tokens_in"] == 3
    assert returned["tokens_out"] == 5
    assert returned["worker_session_thread_id"] == "thr-integration"
    assert _meta(project, 1)["transport_backend"] == "herdr"

    validate = subprocess.run(
        [*WOOF_VALIDATE, str(state.dispatch_events_path(DEFAULT_PROJECT_KEY, 1))],
        capture_output=True,
        text=True,
    )
    assert validate.returncode == 0, validate.stdout + validate.stderr


def test_tmux_dispatch_missing_sentinel_records_failure(tmp_path: Path) -> None:
    project = _write_project(tmp_path, default_minutes=0.001)
    bin_dir = tmp_path / "bin"
    _write_codex_stub(
        bin_dir,
        """
        import sys
        import time

        print("ready > ", flush=True)
        print("agent saw prompt but did not finish", flush=True)
        for _line in sys.stdin:
            time.sleep(60)
        """,
    )

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "primary", "--epic", "2"],
        capture_output=True,
        text=True,
        input="run the fake agent\n",
        cwd=project,
        env=_env(bin_dir),
        timeout=20,
    )

    assert proc.returncode == 1
    returned = next(
        event for event in _events(project, 2) if event["event"] == "subprocess_returned"
    )
    assert returned["exit_type"] == "nonzero"
    assert returned["exit_code"] == 1
    assert "error_signature" in returned
    assert _meta(project, 2)["stderr_bytes"] > 0
