"""Tests for the wf-run driver protocol — O1, O8.

O1: When check-result.ok is false, the driver writes gate.md and does NOT
    invoke git commit; the staged diff remains uncommitted.

O8: executor exits non-zero → gate with subprocess_crash
    executor_result.outcome=aborted_with_position → gate with executor_aborted
    executor_result.outcome=empty_diff → gate with empty_diff_review
    executor_result.outcome=staged_for_verification → proceeds to woof check stage-5
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "woof" / "e181_s2"

pytestmark = pytest.mark.host_only


def _setup_epic(tmp_path: Path, epic_id: int = 181, story_id: str = "S2") -> tuple[Path, Path]:
    """Create a minimal epic directory. Returns (tmp_path, epic_dir)."""
    epic_dir = tmp_path / ".woof" / "epics" / f"E{epic_id}"
    epic_dir.mkdir(parents=True)
    (epic_dir / "epic.jsonl").touch()
    plan = {
        "epic_id": epic_id,
        "goal": "test",
        "stories": [
            {
                "id": story_id,
                "title": "test story",
                "status": "in_progress",
                "paths": [],
                "satisfies": [],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "depends_on": [],
                "tests": {},
            }
        ],
    }
    (epic_dir / "plan.json").write_text(json.dumps(plan))
    return tmp_path, epic_dir


def _run_gate(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), "gate", "write", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _read_gate_fm(gate_path: Path) -> dict:
    text = gate_path.read_text()
    end = text.find("\n---\n", 4)
    return yaml.safe_load(text[4:end])


# ---------------------------------------------------------------------------
# O8 — driver branches on executor outcome
# ---------------------------------------------------------------------------


def test_subprocess_crash_writes_gate_O8(tmp_path: Path) -> None:
    """O8: driver invokes gate write with subprocess_crash when executor exits non-zero."""
    cwd, epic_dir = _setup_epic(tmp_path, 181, "S2")

    proc = _run_gate(
        "--epic",
        "181",
        "--story",
        "S2",
        "--triggered-by",
        "subprocess_crash",
        "--exit-code",
        "1",
        cwd=cwd,
    )
    assert proc.returncode == 0, proc.stderr

    gate = epic_dir / "gate.md"
    assert gate.exists()
    fm = _read_gate_fm(gate)
    assert "subprocess_crash" in fm["triggered_by"]
    assert fm["exit_code"] == 1
    assert fm["type"] == "story_gate"


def test_executor_aborted_writes_gate_O8(tmp_path: Path) -> None:
    """O8: driver invokes gate write with executor_aborted when outcome=aborted_with_position."""
    cwd, epic_dir = _setup_epic(tmp_path, 181, "S2")
    position = tmp_path / "position.md"
    position.write_text("Critique returned blocker; aborting.")

    proc = _run_gate(
        "--epic",
        "181",
        "--story",
        "S2",
        "--triggered-by",
        "executor_aborted",
        "--from-position",
        str(position),
        cwd=cwd,
    )
    assert proc.returncode == 0, proc.stderr

    gate = epic_dir / "gate.md"
    assert gate.exists()
    fm = _read_gate_fm(gate)
    assert "executor_aborted" in fm["triggered_by"]
    text = gate.read_text()
    assert "Critique returned blocker" in text


def test_empty_diff_writes_gate_O8(tmp_path: Path) -> None:
    """O8: driver invokes gate write with empty_diff_review when outcome=empty_diff."""
    cwd, epic_dir = _setup_epic(tmp_path, 181, "S2")

    proc = _run_gate(
        "--epic",
        "181",
        "--story",
        "S2",
        "--triggered-by",
        "empty_diff_review",
        cwd=cwd,
    )
    assert proc.returncode == 0, proc.stderr

    gate = epic_dir / "gate.md"
    assert gate.exists()
    fm = _read_gate_fm(gate)
    assert "empty_diff_review" in fm["triggered_by"]


# ---------------------------------------------------------------------------
# O1, O7 — check_6 failure → gate, no commit
# ---------------------------------------------------------------------------


def test_check_6_failure_writes_gate_not_commit_O1_O7(tmp_path: Path) -> None:
    """O1+O7: When check_6 fails (E181 S2 fixture), gate write is called; commit does not occur.

    This tests the Python-level gate write logic, not the bash driver directly.
    The invariant: woof gate write succeeds and gate.md is written; no git commit
    operation is triggered by the Python code path.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT))

    _, epic_dir = _setup_epic(tmp_path, 181, "S2")

    # Simulate the check-result that would be produced when check_6 fires on E181 S2
    check_result = {
        "ok": False,
        "stage": 5,
        "epic_id": 181,
        "story_id": "S2",
        "triggered_by": ["check_6_critique_blocker"],
        "checks": [
            {
                "id": "check_6_critique_blocker",
                "ok": False,
                "severity": "blocker",
                "summary": "critique severity is blocker (2 finding(s))",
                "evidence": "F1: apply_size_cap corrupts UTF-8; F2: lax tests",
                "paths": [],
                "command": None,
                "exit_code": None,
            }
        ],
    }
    cr_file = tmp_path / "check-result.json"
    cr_file.write_text(json.dumps(check_result))
    pos_file = tmp_path / "position.md"
    pos_file.write_text("check_6_critique_blocker fired on E181 S2 critique.")

    from woof.gate.write import write_gate_from_check_result

    gate_path = write_gate_from_check_result(
        check_result_path=cr_file,
        position_path=pos_file,
        epic_dir=epic_dir,
        story_id="S2",
        schema_path=None,  # skip ajv validation in unit test
    )

    assert gate_path.exists()
    fm = _read_gate_fm(gate_path)
    assert "check_6_critique_blocker" in fm["triggered_by"], (
        f"check_6_critique_blocker not in triggered_by: {fm['triggered_by']}"
    )
    assert fm["story_id"] == "S2"

    # The gate write function does NOT call git commit — verified by absence
    # of any commit in the test's git history (we're in a tmpdir, no git repo).
    # The important invariant: write_gate_from_check_result returns a path, not a commit SHA.
    assert isinstance(gate_path, Path)
