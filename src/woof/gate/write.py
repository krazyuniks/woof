"""woof gate write — mechanical gate.md authoring.

All YAML front-matter fields are derived deterministically from the
check-result or trigger arguments. No LLM authors any YAML field.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml


def iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def _validate_gate(gate_md: Path, schema_path: Path) -> tuple[bool, str]:
    """Validate gate.md front-matter via ajv. Returns (ok, message)."""
    text = gate_md.read_text()
    if not text.startswith("---\n"):
        return False, "gate.md has no YAML front-matter"
    end = text.find("\n---\n", 4)
    if end < 0:
        return False, "gate.md front-matter is unterminated"
    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        return False, f"gate.md YAML parse error: {exc}"

    payload = json.dumps(fm).encode()
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as fh:
        fh.write(payload)
        data_path = fh.name
    try:
        proc = subprocess.run(
            [
                "ajv",
                "validate",
                "--spec=draft2020",
                "-c",
                "ajv-formats",
                "-s",
                str(schema_path),
                "-d",
                data_path,
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(data_path).unlink(missing_ok=True)

    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output


def write_gate(
    epic_dir: Path,
    story_id: str | None,
    triggered_by: list[str],
    position_text: str,
    exit_code: int | None = None,
    *,
    schema_path: Path | None = None,
    validate: bool = True,
) -> Path:
    """Write gate.md and append gate_opened to epic.jsonl.

    Returns the path of the written gate.md.
    Raises ValueError if the resulting front-matter fails schema validation.
    """
    gate_path = epic_dir / "gate.md"
    epic_jsonl = epic_dir / "epic.jsonl"
    now = iso_utc()

    front: dict = {
        "type": "story_gate",
        "stage": 6,
        "story_id": story_id,
        "triggered_by": triggered_by,
        "timestamp": now,
    }
    if exit_code is not None:
        front["exit_code"] = exit_code

    fm_yaml = yaml.dump(front, default_flow_style=False, allow_unicode=True)
    body = position_text.strip()
    content = f"---\n{fm_yaml}---\n\n{body}\n"
    gate_path.write_text(content)

    if validate and schema_path and schema_path.is_file():
        ok, msg = _validate_gate(gate_path, schema_path)
        if not ok:
            gate_path.unlink(missing_ok=True)
            raise ValueError(f"gate.md front-matter failed schema validation: {msg}")

    epic_id_str = epic_dir.name  # e.g. "E182"
    epic_id = int(epic_id_str.lstrip("E")) if epic_id_str.startswith("E") else 0
    event: dict = {
        "event": "story_gate_opened",
        "at": now,
        "epic_id": epic_id,
        "triggered_by": triggered_by,
    }
    if story_id:
        event["story_id"] = story_id
    _append_jsonl(epic_jsonl, event)

    return gate_path


def write_gate_from_check_result(
    check_result_path: Path,
    position_path: Path | None,
    epic_dir: Path,
    story_id: str | None = None,
    *,
    schema_path: Path | None = None,
) -> Path:
    """Build gate.md from a check-result JSON file."""
    check_result = json.loads(check_result_path.read_text())
    triggered_by: list[str] = check_result.get("triggered_by") or []
    if not triggered_by:
        triggered_by = ["schema_validation_failed"]

    sid = story_id or check_result.get("story_id")

    if position_path and position_path.is_file():
        position_text = position_path.read_text()
    else:
        position_text = _auto_position(triggered_by, check_result)

    return write_gate(
        epic_dir=epic_dir,
        story_id=sid,
        triggered_by=triggered_by,
        position_text=position_text,
        schema_path=schema_path,
        validate=schema_path is not None,
    )


def write_gate_for_trigger(
    trigger: str,
    epic_dir: Path,
    story_id: str | None,
    exit_code: int | None = None,
    position_path: Path | None = None,
    *,
    schema_path: Path | None = None,
) -> Path:
    """Build gate.md for a driver-level trigger (crash, aborted, empty diff)."""
    if position_path and position_path.is_file():
        position_text = position_path.read_text()
    else:
        position_text = _auto_position_for_trigger(trigger, exit_code)

    return write_gate(
        epic_dir=epic_dir,
        story_id=story_id,
        triggered_by=[trigger],
        position_text=position_text,
        exit_code=exit_code if trigger == "subprocess_crash" else None,
        schema_path=schema_path,
        validate=schema_path is not None,
    )


def _auto_position(triggered_by: list[str], check_result: dict) -> str:
    checks = check_result.get("checks") or []
    failed = [c for c in checks if not c.get("ok")]
    lines = [f"Check stage-5 failed: {', '.join(triggered_by)}."]
    for c in failed:
        lines.append(f"\n- {c['id']}: {c.get('summary', '')}")
        if c.get("evidence"):
            lines.append(f"  Evidence: {c['evidence']}")
    lines.append("\n\nInvestigate the findings above and retry or revise the story scope.")
    return "".join(lines)


def _auto_position_for_trigger(trigger: str, exit_code: int | None) -> str:
    if trigger == "subprocess_crash":
        return (
            f"Story executor subprocess crashed with exit code {exit_code}.\n\n"
            "Investigate the dispatch.jsonl audit and harness output before re-dispatching."
        )
    if trigger == "executor_aborted":
        return (
            "Story executor reported aborted_with_position. "
            "No position prose was provided.\n\n"
            "Review the executor audit output for the reason."
        )
    if trigger == "empty_diff_review":
        return (
            "Story executor reported empty_diff. "
            "Confirm whether earlier stories already realised this outcome.\n\n"
            "Approve if confirmed; revise story scope otherwise."
        )
    return f"Gate opened with trigger: {trigger}."
