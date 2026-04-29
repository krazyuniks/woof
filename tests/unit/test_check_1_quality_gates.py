"""Tests for check_1_quality_gates — Stage-5 Check 1."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from woof.checks import CheckContext
from woof.checks.runners.check_1_quality_gates import check_1_quality_gates_runner

pytestmark = pytest.mark.host_only


def _make_ctx(repo_root: Path) -> CheckContext:
    epic_dir = repo_root / ".woof" / "epics" / "E999"
    epic_dir.mkdir(parents=True)
    return CheckContext(
        epic_id=999,
        story_id="S1",
        repo_root=repo_root,
        epic_dir=epic_dir,
        plan={},
        critique=None,
    )


def _write_quality_gates(repo_root: Path, content: str) -> None:
    woof_dir = repo_root / ".woof"
    woof_dir.mkdir()
    (woof_dir / "quality-gates.toml").write_text(content)


def _python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def test_quality_gate_passes_when_all_commands_exit_zero(tmp_path: Path) -> None:
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.test]
command = {_toml_string(_python_command("print('ok')"))}
timeout_seconds = 5
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert outcome.ok
    assert outcome.id == "check_1_quality_gates"
    assert outcome.severity == "info"
    assert "all 1 quality gate" in outcome.summary
    assert outcome.evidence is None


def test_quality_gate_fails_and_captures_output(tmp_path: Path) -> None:
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(_python_command("import sys; print('bad stdout'); print('bad stderr', file=sys.stderr); sys.exit(3)"))}
timeout_seconds = 5
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.command is not None
    assert outcome.exit_code == 3
    assert outcome.evidence is not None
    assert "gate 'lint' exited 3" in outcome.evidence
    assert "bad stdout" in outcome.evidence
    assert "bad stderr" in outcome.evidence


def test_quality_gate_times_out(tmp_path: Path) -> None:
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.test]
command = {_toml_string(_python_command("import time; time.sleep(5)"))}
timeout_seconds = 1
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.exit_code is None
    assert outcome.evidence is not None
    assert "timed out after 1s" in outcome.evidence


def test_missing_quality_gate_command_fails(tmp_path: Path) -> None:
    _write_quality_gates(
        tmp_path,
        """\
[gates.test]
command = "__woof_missing_quality_gate_command__"
timeout_seconds = 5
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.command == "__woof_missing_quality_gate_command__"
    assert outcome.exit_code in {127, 9009}
    assert outcome.evidence is not None
    assert "__woof_missing_quality_gate_command__" in outcome.evidence
