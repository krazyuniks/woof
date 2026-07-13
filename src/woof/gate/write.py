"""Mechanical gate.md authoring for graph-owned gates.

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

from woof import state


def iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    project_key: str,
    epic_id: int,
    work_unit_id: str | None,
    triggered_by: list[str],
    position_text: str,
    exit_code: int | None = None,
    *,
    schema_path: Path | None = None,
    validate: bool = True,
    gate_type: str | None = None,
) -> Path:
    """Write gate.md and append gate_opened to epic.jsonl.

    Returns the path of the written gate.md.
    Raises ValueError if the resulting front-matter fails schema validation.
    """
    gate_path = state.gate_path(project_key, epic_id)
    epic_jsonl = state.epic_events_path(project_key, epic_id)
    now = iso_utc()

    resolved_gate_type = gate_type or _gate_type_for_triggers(triggered_by)
    front: dict = {
        "type": resolved_gate_type,
        "stage": _stage_for_gate_type(resolved_gate_type, work_unit_id),
        "work_unit_id": work_unit_id,
        "triggered_by": triggered_by,
        "timestamp": now,
    }
    if exit_code is not None:
        front["exit_code"] = exit_code

    fm_yaml = yaml.dump(front, default_flow_style=False, allow_unicode=True)
    body = _ensure_gate_sections(
        position_text.strip(),
        epic_id=epic_id,
        work_unit_id=work_unit_id,
        gate_type=resolved_gate_type,
        triggered_by=triggered_by,
    )
    content = f"---\n{fm_yaml}---\n\n{body}\n"
    state.atomic_write_text(gate_path, content)

    if validate and schema_path and schema_path.is_file():
        ok, msg = _validate_gate(gate_path, schema_path)
        if not ok:
            gate_path.unlink(missing_ok=True)
            raise ValueError(f"gate.md front-matter failed schema validation: {msg}")

    event: dict = {
        "event": _opened_event_for_gate_type(resolved_gate_type),
        "at": now,
        "epic_id": epic_id,
        "gate_type": resolved_gate_type,
        "triggered_by": triggered_by,
    }
    if work_unit_id:
        event["work_unit_id"] = work_unit_id
    state.append_jsonl(epic_jsonl, event)

    return gate_path


def write_gate_from_check_result(
    check_result_path: Path,
    position_path: Path | None,
    project_key: str,
    epic_id: int,
    work_unit_id: str | None = None,
    *,
    schema_path: Path | None = None,
) -> Path:
    """Build gate.md from a check-result JSON file."""
    check_result = json.loads(check_result_path.read_text())
    triggered_by: list[str] = check_result.get("triggered_by") or []
    if not triggered_by:
        triggered_by = ["schema_validation_failed"]

    sid = work_unit_id or check_result.get("work_unit_id")

    if position_path and position_path.is_file():
        position_text = position_path.read_text()
    else:
        position_text = _auto_position(triggered_by, check_result)

    return write_gate(
        project_key=project_key,
        epic_id=epic_id,
        work_unit_id=sid,
        triggered_by=triggered_by,
        position_text=position_text,
        schema_path=schema_path,
        validate=schema_path is not None,
        gate_type=_gate_type_for_triggers(triggered_by),
    )


def write_gate_for_trigger(
    trigger: str,
    project_key: str,
    epic_id: int,
    work_unit_id: str | None,
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
        project_key=project_key,
        epic_id=epic_id,
        work_unit_id=work_unit_id,
        triggered_by=[trigger],
        position_text=position_text,
        exit_code=exit_code if trigger == "subprocess_crash" else None,
        schema_path=schema_path,
        validate=schema_path is not None,
        gate_type=_gate_type_for_triggers([trigger]),
    )


def _gate_type_for_triggers(triggered_by: list[str]) -> str:
    if triggered_by == ["plan_review"]:
        return "plan_gate"
    if triggered_by in (["readiness_unready"], ["readiness_escalation"]):
        return "readiness_gate"
    if triggered_by == ["check_9_review_valve"]:
        return "review_gate"
    if triggered_by in (["tracker_sync_conflict"], ["github_sync_conflict"]):
        return "plan_gate"
    if triggered_by in (["course_correction"], ["run_resilience"]):
        return "work_unit_gate"
    return "work_unit_gate"


def _stage_for_gate_type(gate_type: str, work_unit_id: str | None) -> int:
    if gate_type == "readiness_gate":
        return 2
    if gate_type == "review_gate":
        return 5 if work_unit_id else 6
    if gate_type == "plan_gate":
        return 4
    return 5


def _opened_event_for_gate_type(gate_type: str) -> str:
    if gate_type == "readiness_gate":
        return "readiness_gate_opened"
    if gate_type == "review_gate":
        return "review_gate_opened"
    if gate_type == "plan_gate":
        return "plan_gate_opened"
    return "work_unit_gate_opened"


def _ensure_gate_sections(
    text: str,
    *,
    epic_id: int,
    work_unit_id: str | None,
    gate_type: str,
    triggered_by: list[str],
) -> str:
    required = (
        "## Context",
        "## Findings",
        "## Primary position",
        "## Reviewer position",
    )
    if all(section in text for section in required):
        return text

    epic = f"E{epic_id}"
    work_unit = f" work unit {work_unit_id}" if work_unit_id else ""
    trigger_text = ", ".join(triggered_by)
    body = text or "No additional position text was provided."
    return (
        "## Context\n\n"
        f"{gate_type} opened for {epic}{work_unit}. Triggered by: {trigger_text}.\n\n"
        "## Findings\n\n"
        f"{body}\n\n"
        "## Primary position\n\n"
        "No accepted primary revision has been recorded after this gate trigger.\n\n"
        "## Reviewer position\n\n"
        "No separate reviewer position was available for this gate.\n"
    )


def _auto_position(triggered_by: list[str], check_result: dict) -> str:
    checks = check_result.get("checks") or []
    failed = [c for c in checks if not c.get("ok")]
    lines = [
        "## Context\n\n"
        f"Stage 5 verification opened a gate. Triggered by: {', '.join(triggered_by)}.\n\n"
        "## Findings\n\n"
    ]
    for c in failed:
        lines.append(f"- {c['id']}: {c.get('summary', '')}\n")
        if c.get("evidence"):
            lines.append(f"  Evidence: {c['evidence']}\n")
    if not failed:
        lines.append("- No individual failed check entries were present in check-result.json.\n")
    lines.append(
        "\n## Primary position\n\n"
        "The primary output remains available for operator inspection. No accepted "
        "revision has been recorded after this failed check result.\n\n"
        "## Reviewer position\n\n"
        "The deterministic Stage 5 check runner produced the findings above.\n"
    )
    return "".join(lines)


def _auto_position_for_trigger(trigger: str, exit_code: int | None) -> str:
    context = f"Gate opened with trigger: {trigger}."
    if trigger == "subprocess_crash":
        finding = f"Work-unit producer subprocess crashed with exit code {exit_code}."
        primary = "No primary result was accepted because the subprocess did not complete cleanly."
    elif trigger == "executor_aborted":
        finding = (
            "Work-unit producer reported aborted_with_position, but no separate position prose "
            "was provided."
        )
        primary = "Review the executor audit output for the primary's reason."
    elif trigger == "empty_diff_review":
        finding = "Work-unit producer reported empty_diff."
        primary = (
            "Confirm whether earlier work units already realised this outcome. Approve if "
            "confirmed; revise work-unit scope otherwise."
        )
    elif trigger in {"tracker_sync_conflict", "github_sync_conflict"}:
        finding = "Issue-tracker sync conflict detected."
        primary = "Review the remote tracker body, local render, and .last-sync before retrying."
    else:
        finding = context
        primary = "No accepted primary revision has been recorded after this trigger."
    return (
        "## Context\n\n"
        f"{context}\n\n"
        "## Findings\n\n"
        f"- {finding}\n\n"
        "## Primary position\n\n"
        f"{primary}\n\n"
        "## Reviewer position\n\n"
        "No separate reviewer position was available for this gate.\n"
    )
