"""Tests for gate/write.py.

Gate helpers produce gate.md whose YAML front-matter validates against
gate.schema.json; gate_type, triggered_by[], and event fields are mechanically
derived from the check-result or trigger.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.support import DEFAULT_PROJECT_KEY
from woof import state
from woof.gate import write as gate_write

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCHEMA = REPO_ROOT / "schemas" / "gate.schema.json"

pytestmark = pytest.mark.host_only


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


def _setup_epic_dir(epic_id: int = 999) -> Path:
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, epic_id)
    epic_dir.mkdir(parents=True)
    state.epic_events_path(DEFAULT_PROJECT_KEY, epic_id).touch()
    return epic_dir


def test_gate_write_from_check_result_validates(tmp_path: Path) -> None:
    """write_gate_from_check_result produces schema-valid gate.md."""
    epic_dir = _setup_epic_dir(181)

    check_result = {
        "ok": False,
        "stage": 5,
        "epic_id": 181,
        "work_unit_id": "S2",
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

    gate_write.write_gate_from_check_result(
        check_result_path=cr_file,
        position_path=position_file,
        project_key=DEFAULT_PROJECT_KEY,
        epic_id=181,
        work_unit_id="S2",
        schema_path=GATE_SCHEMA,
    )

    gate_path = epic_dir / "gate.md"
    assert gate_path.exists(), "gate.md not written"

    # Validate front-matter
    ok, msg = _validate_gate_fm(gate_path)
    assert ok, f"gate.md front-matter invalid: {msg}"

    # Check triggered_by contains check_6_critique_blocker
    text = gate_path.read_text()
    fm = yaml.safe_load(text[4 : text.find("\n---\n", 4)])
    assert "check_6_critique_blocker" in fm["triggered_by"]
    assert fm["type"] == "work_unit_gate"
    assert fm["work_unit_id"] == "S2"

    # Position body is preserved inside the structured operator sections.
    assert "Critique returned blocker" in text
    assert "## Context" in text
    assert "## Findings" in text
    assert "## Primary position" in text
    assert "## Reviewer position" in text


def test_gate_write_for_subprocess_crash() -> None:
    """write_gate_for_trigger with subprocess_crash writes valid gate.md."""
    epic_dir = _setup_epic_dir(182)

    gate_write.write_gate_for_trigger(
        trigger="subprocess_crash",
        project_key=DEFAULT_PROJECT_KEY,
        epic_id=182,
        work_unit_id="S1",
        exit_code=1,
        schema_path=GATE_SCHEMA,
    )

    gate_path = epic_dir / "gate.md"
    assert gate_path.exists()

    ok, msg = _validate_gate_fm(gate_path)
    assert ok, f"gate.md front-matter invalid: {msg}"

    text = gate_path.read_text()
    fm = yaml.safe_load(text[4 : text.find("\n---\n", 4)])
    assert "subprocess_crash" in fm["triggered_by"]
    assert fm["exit_code"] == 1
    assert "## Context" in text
    assert "## Primary position" in text


def test_gate_write_for_tracker_sync_conflict_is_epic_level_gate() -> None:
    epic_dir = _setup_epic_dir(184)

    gate_write.write_gate_for_trigger(
        trigger="tracker_sync_conflict",
        project_key=DEFAULT_PROJECT_KEY,
        epic_id=184,
        work_unit_id=None,
        schema_path=GATE_SCHEMA,
    )

    gate_path = epic_dir / "gate.md"
    ok, msg = _validate_gate_fm(gate_path)
    assert ok, f"gate.md front-matter invalid: {msg}"
    fm = yaml.safe_load(gate_path.read_text()[4 : gate_path.read_text().find("\n---\n", 4)])
    assert fm["type"] == "plan_gate"
    assert fm["work_unit_id"] is None
    assert fm["triggered_by"] == ["tracker_sync_conflict"]
    gate_text = gate_path.read_text()
    assert "## Context" in gate_text
    assert "## Reviewer position" in gate_text


def test_gate_write_appends_epic_jsonl() -> None:
    """gate write appends work_unit_gate_opened event to epic.jsonl."""
    epic_dir = _setup_epic_dir(183)

    gate_write.write_gate_for_trigger(
        trigger="executor_aborted",
        project_key=DEFAULT_PROJECT_KEY,
        epic_id=183,
        work_unit_id="S1",
        schema_path=GATE_SCHEMA,
    )

    jsonl = epic_dir / "epic.jsonl"
    lines = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    assert any(e["event"] == "work_unit_gate_opened" for e in lines), (
        f"work_unit_gate_opened not in epic.jsonl: {lines}"
    )


def test_gate_write_schema_failure_is_loud_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    epic_dir = _setup_epic_dir(185)
    schema_path = tmp_path / "gate.schema.json"
    schema_path.write_text("{}")

    monkeypatch.setattr(gate_write, "_validate_gate", lambda *_args: (False, "invalid test gate"))

    with pytest.raises(ValueError, match=r"gate\.md front-matter failed schema validation"):
        gate_write.write_gate(
            project_key=DEFAULT_PROJECT_KEY,
            epic_id=185,
            work_unit_id="S1",
            triggered_by=["manual"],
            position_text="Manual gate.",
            schema_path=schema_path,
        )

    assert not (epic_dir / "gate.md").exists()
    assert (epic_dir / "epic.jsonl").read_text() == ""
