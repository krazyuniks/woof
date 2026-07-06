"""CLI acceptance coverage for gate and recovery paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from .wf_gate_harness import (
    WOOF_BIN,
    assert_gate,
    assert_ok,
    create_stage5_consumer,
    epic_dir,
    json_stdout,
    jsonl,
    latest_commit_subject,
    run,
)

pytestmark = [pytest.mark.host_only, pytest.mark.tmux_substrate]


def _wf(consumer: Path, env: dict[str, str], *args: str):
    return run([str(WOOF_BIN), "wf", *args], cwd=consumer, env=env)


def _resolve(consumer: Path, env: dict[str, str], decision: str) -> None:
    proc = _wf(consumer, env, "--epic", "1", "--resolve", decision)
    assert_ok(proc)
    assert proc.stdout == f"woof wf: gate resolved decision={decision}\n"


def _run_epic_json(consumer: Path, env: dict[str, str]) -> list[dict[str, Any]]:
    proc = _wf(consumer, env, "--epic", "1", "--format", "json")
    assert_ok(proc)
    return json_stdout(proc)


def _repair_work_unit_critique_to_info(consumer: Path) -> None:
    (epic_dir(consumer) / "critique" / "work-unit-S1.md").write_text(
        """\
---
target: work_unit
target_id: S1
severity: info
timestamp: "2026-05-23T00:00:00Z"
harness: gate-operator
findings: []
---

Operator repaired the blocker and requested verification.
""",
        encoding="utf-8",
    )


def test_executor_subprocess_crash_gate_can_abandon_work_unit(tmp_path: Path) -> None:
    consumer, env = create_stage5_consumer(tmp_path, scenario="subprocess_crash")

    outputs = _run_epic_json(consumer, env)

    assert outputs[-1]["node_type"] == "executor_dispatch"
    assert outputs[-1]["status"] == "gate_opened"
    assert outputs[-1]["triggered_by"] == ["subprocess_crash"]
    assert_gate(consumer, gate_type="work_unit_gate", triggered_by=["subprocess_crash"])
    assert (epic_dir(consumer) / "gate.md").read_text(encoding="utf-8").find("exit_code: 1") >= 0

    _resolve(consumer, env, "abandon_work_unit")
    completed = _run_epic_json(consumer, env)

    assert completed[-1]["status"] == "epic_complete"
    plan = json.loads((epic_dir(consumer) / "plan.json").read_text(encoding="utf-8"))
    assert plan["work_units"][0]["state"] == "abandoned"
    epic_events = jsonl(epic_dir(consumer) / "epic.jsonl")
    assert any(event.get("event") == "work_unit_abandoned" for event in epic_events)
    assert not any(event.get("event") == "work_unit_completed" for event in epic_events)
    assert not (epic_dir(consumer) / "gate.md").exists()


def test_reviewer_blocker_gate_resumes_after_critique_repair(tmp_path: Path) -> None:
    consumer, env = create_stage5_consumer(tmp_path, scenario="reviewer_blocker")

    outputs = _run_epic_json(consumer, env)

    assert outputs[-1]["node_type"] == "review_disposition"
    assert outputs[-1]["status"] == "gate_opened"
    assert outputs[-1]["triggered_by"] == ["check_6_critique_blocker"]
    assert_gate(consumer, gate_type="work_unit_gate", triggered_by=["check_6_critique_blocker"])
    assert not (epic_dir(consumer) / "dispositions" / "work-unit-S1.md").exists()

    _repair_work_unit_critique_to_info(consumer)
    _resolve(consumer, env, "approve")
    completed = _run_epic_json(consumer, env)

    assert completed[-1]["status"] == "epic_complete"
    assert latest_commit_subject(consumer, env) == "feat: add gate acceptance artefact"
    assert (epic_dir(consumer) / "dispositions" / "work-unit-S1.md").is_file()


def test_failed_check_gate_reruns_and_commits_after_resolution(tmp_path: Path) -> None:
    gate_command = (
        "python -c 'import os, sys; "
        'sys.exit(0 if os.environ.get("WOOF_GATE_QUALITY_PASS") == "1" else 7)\''
    )
    consumer, env = create_stage5_consumer(
        tmp_path,
        scenario="happy",
        quality_gate_command=gate_command,
    )

    outputs = _run_epic_json(consumer, env)

    assert outputs[-1]["node_type"] == "verification"
    assert outputs[-1]["status"] == "gate_opened"
    assert outputs[-1]["triggered_by"] == ["check_1_quality_gates"]
    assert_gate(consumer, gate_type="work_unit_gate", triggered_by=["check_1_quality_gates"])
    check_result = json.loads((epic_dir(consumer) / "check-result.json").read_text())
    assert check_result["ok"] is False

    env = {**env, "WOOF_GATE_QUALITY_PASS": "1"}
    _resolve(consumer, env, "approve")
    assert not (epic_dir(consumer) / "check-result.json").exists()
    completed = _run_epic_json(consumer, env)

    assert completed[-1]["status"] == "epic_complete"
    assert latest_commit_subject(consumer, env) == "feat: add gate acceptance artefact"
    epic_events = jsonl(epic_dir(consumer) / "epic.jsonl")
    assert any(event.get("event") == "transaction_manifest_verified" for event in epic_events)


def test_empty_diff_gate_approval_marks_work_unit_complete_without_commit(tmp_path: Path) -> None:
    consumer, env = create_stage5_consumer(tmp_path, scenario="empty_diff")
    before = latest_commit_subject(consumer, env)

    outputs = _run_epic_json(consumer, env)

    assert outputs[-1]["node_type"] == "gate_open"
    assert outputs[-1]["status"] == "gate_opened"
    assert outputs[-1]["triggered_by"] == ["empty_diff_review"]
    assert_gate(consumer, gate_type="work_unit_gate", triggered_by=["empty_diff_review"])

    _resolve(consumer, env, "approve")
    completed = _run_epic_json(consumer, env)

    assert completed[-1]["status"] == "epic_complete"
    assert latest_commit_subject(consumer, env) == before
    plan = json.loads((epic_dir(consumer) / "plan.json").read_text(encoding="utf-8"))
    assert plan["work_units"][0]["state"] == "done"
    assert plan["work_units"][0]["empty_diff"] is True
    assert not (epic_dir(consumer) / "executor_result.json").exists()


def test_malformed_stage_state_gate_can_abandon_work_unit(tmp_path: Path) -> None:
    consumer, env = create_stage5_consumer(tmp_path, scenario="malformed_state")

    outputs = _run_epic_json(consumer, env)

    assert outputs[-1]["node_type"] == "gate_open"
    assert outputs[-1]["status"] == "gate_opened"
    assert outputs[-1]["triggered_by"] == ["incomplete_stage_state"]
    assert_gate(consumer, gate_type="work_unit_gate", triggered_by=["incomplete_stage_state"])
    assert "malformed JSON" in outputs[-1]["message"]

    _resolve(consumer, env, "abandon_work_unit")
    completed = _run_epic_json(consumer, env)

    assert completed[-1]["status"] == "epic_complete"
    assert not (epic_dir(consumer) / "executor_result.json").exists()


def test_interrupted_commit_resume_commits_existing_transaction(tmp_path: Path) -> None:
    consumer, env = create_stage5_consumer(tmp_path, scenario="happy")

    once_outputs = []
    for _ in range(4):
        proc = _wf(consumer, env, "--epic", "1", "--once", "--format", "json")
        assert_ok(proc)
        once_outputs.extend(json_stdout(proc))

    assert [output["node_type"] for output in once_outputs] == [
        "executor_dispatch",
        "critique_dispatch",
        "review_disposition",
        "verification",
    ]
    assert once_outputs[-1]["status"] == "completed"
    assert (epic_dir(consumer) / "executor_result.json").is_file()
    assert (epic_dir(consumer) / "check-result.json").is_file()

    plan_path = epic_dir(consumer) / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["work_units"][0]["state"] = "done"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    with (epic_dir(consumer) / "epic.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event": "work_unit_completed",
                    "at": "2026-05-23T00:00:00Z",
                    "epic_id": 1,
                    "work_unit_id": "S1",
                }
            )
            + "\n"
        )
    assert_ok(
        run(
            ["git", "add", ".woof/epics/E1/plan.json", ".woof/epics/E1/epic.jsonl"],
            cwd=consumer,
            env=env,
        )
    )

    resumed = _run_epic_json(consumer, env)

    assert resumed[0]["node_type"] == "commit"
    assert resumed[0]["status"] == "completed"
    assert resumed[-1]["status"] == "epic_complete"
    assert latest_commit_subject(consumer, env) == "feat: add gate acceptance artefact"
    epic_events = jsonl(epic_dir(consumer) / "epic.jsonl")
    assert [event.get("event") for event in epic_events].count("work_unit_completed") == 1
    assert [event.get("event") for event in epic_events].count("transaction_manifest_verified") == 1
    assert not (epic_dir(consumer) / "executor_result.json").exists()
    assert not (epic_dir(consumer) / "check-result.json").exists()
    status = run(["git", "status", "--porcelain=v1"], cwd=consumer, env=env)
    assert_ok(status)
    assert status.stdout == ""
