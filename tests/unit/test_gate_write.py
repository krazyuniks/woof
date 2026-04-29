"""Tests for 'woof gate write' subcommand and gate/write.py — O5.

O5: woof gate write produces gate.md whose YAML front-matter validates
    against gate.schema.json; gate_type, triggered_by[], opened_at fields
    are mechanically derived from the check-result; prose body is verbatim
    from the position file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
GATE_SCHEMA = REPO_ROOT / "schemas" / "gate.schema.json"

pytestmark = pytest.mark.host_only


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO_ROOT),
    )


def _validate_gate_fm(gate_path: Path) -> tuple[bool, str]:
    """Validate gate.md front-matter against gate.schema.json via ajv."""
    text = gate_path.read_text()
    assert text.startswith("---\n"), "gate.md must start with '---'"
    end = text.find("\n---\n", 4)
    assert end >= 0, "gate.md front-matter is unterminated"
    fm = yaml.safe_load(text[4:end])

    if not shutil.which("ajv"):
        pytest.skip("ajv not on PATH")

    data_file = gate_path.with_suffix(".fm.json")
    data_file.write_text(json.dumps(fm))
    proc = subprocess.run(
        [
            "ajv",
            "validate",
            "--spec=draft2020",
            "-c",
            "ajv-formats",
            "-s",
            str(GATE_SCHEMA),
            "-d",
            str(data_file),
        ],
        capture_output=True,
        text=True,
    )
    data_file.unlink(missing_ok=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def _setup_epic_dir(tmp_path: Path, epic_id: int = 999) -> Path:
    epic_dir = tmp_path / ".woof" / "epics" / f"E{epic_id}"
    epic_dir.mkdir(parents=True)
    (epic_dir / "epic.jsonl").touch()
    return epic_dir


def test_gate_write_from_check_result_validates_O5(tmp_path: Path) -> None:
    """O5: woof gate write --from-check-result produces schema-valid gate.md."""
    epic_dir = _setup_epic_dir(tmp_path, 181)

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
                "summary": "critique severity is blocker",
                "evidence": "F1: UTF-8 truncation",
                "paths": [],
                "command": None,
                "exit_code": None,
            }
        ],
    }
    cr_file = tmp_path / "check-result.json"
    cr_file.write_text(json.dumps(check_result))

    position_file = tmp_path / "position.md"
    position_file.write_text("Critique returned blocker; halting pending investigation.")

    proc = _run(
        "gate",
        "write",
        "--epic",
        "181",
        "--story",
        "S2",
        "--from-check-result",
        str(cr_file),
        "--position-file",
        str(position_file),
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr

    gate_path = epic_dir / "gate.md"
    assert gate_path.exists(), "gate.md not written"

    # Validate front-matter
    ok, msg = _validate_gate_fm(gate_path)
    assert ok, f"gate.md front-matter invalid: {msg}"

    # Check triggered_by contains check_6_critique_blocker
    text = gate_path.read_text()
    fm = yaml.safe_load(text[4 : text.find("\n---\n", 4)])
    assert "check_6_critique_blocker" in fm["triggered_by"]
    assert fm["type"] == "story_gate"
    assert fm["story_id"] == "S2"

    # Position body is copied verbatim
    assert "Critique returned blocker" in text


def test_gate_write_for_subprocess_crash_O5(tmp_path: Path) -> None:
    """O5: woof gate write --triggered-by subprocess_crash writes valid gate.md."""
    epic_dir = _setup_epic_dir(tmp_path, 182)

    proc = _run(
        "gate",
        "write",
        "--epic",
        "182",
        "--story",
        "S1",
        "--triggered-by",
        "subprocess_crash",
        "--exit-code",
        "1",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr

    gate_path = epic_dir / "gate.md"
    assert gate_path.exists()

    ok, msg = _validate_gate_fm(gate_path)
    assert ok, f"gate.md front-matter invalid: {msg}"

    text = gate_path.read_text()
    fm = yaml.safe_load(text[4 : text.find("\n---\n", 4)])
    assert "subprocess_crash" in fm["triggered_by"]
    assert fm["exit_code"] == 1


def test_gate_write_appends_epic_jsonl_O5(tmp_path: Path) -> None:
    """O5: gate write appends story_gate_opened event to epic.jsonl."""
    epic_dir = _setup_epic_dir(tmp_path, 183)

    proc = _run(
        "gate",
        "write",
        "--epic",
        "183",
        "--story",
        "S1",
        "--triggered-by",
        "executor_aborted",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = epic_dir / "epic.jsonl"
    lines = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    assert any(e["event"] == "story_gate_opened" for e in lines), (
        f"story_gate_opened not in epic.jsonl: {lines}"
    )
