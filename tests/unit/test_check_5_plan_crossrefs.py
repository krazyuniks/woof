"""Tests for check_5_plan_crossrefs — Stage-5 Check 5."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from woof.checks import CheckContext
from woof.checks.runners.check_5_plan_crossrefs import (
    check_5_plan_crossrefs_runner,
    stage3_plan_contract_failures,
)

pytestmark = pytest.mark.host_only


def _epic_front_matter() -> dict[str, Any]:
    return {
        "epic_id": 42,
        "title": "Plan cross references",
        "observable_outcomes": [
            {"id": "O1", "statement": "First outcome", "verification": "automated"},
            {"id": "O2", "statement": "Second outcome", "verification": "automated"},
        ],
        "contract_decisions": [
            {
                "id": "CD1",
                "related_outcomes": ["O1"],
                "title": "Primary contract",
                "json_schema_ref": "schemas/primary.schema.json",
            }
        ],
        "acceptance_criteria": ["All planned checks pass"],
    }


def _story(story_id: str, **overrides: Any) -> dict[str, Any]:
    story = {
        "id": story_id,
        "title": f"Story {story_id}",
        "summary": f"Produce {story_id}",
        "paths": [f"src/{story_id}.py"],
        "satisfies": ["O1"],
        "implements_contract_decisions": [],
        "uses_contract_decisions": [],
        "deps": [],
        "tests": {"count": 1, "types": ["unit"]},
        "status": "pending",
    }
    story.update(overrides)
    return story


def _valid_plan() -> dict[str, Any]:
    return {
        "epic_id": 42,
        "goal": "Validate plan cross references.",
        "work_units": [
            _story(
                "S1",
                satisfies=["O1"],
                implements_contract_decisions=["CD1"],
                status="done",
            ),
            _story(
                "S2",
                satisfies=["O2"],
                uses_contract_decisions=["CD1"],
                deps=["S1"],
                status="in_progress",
            ),
        ],
    }


def _write_epic(epic_dir: Path, front_matter: dict[str, Any]) -> None:
    import yaml

    epic_dir.mkdir(parents=True, exist_ok=True)
    (epic_dir / "EPIC.md").write_text("---\n" + yaml.safe_dump(front_matter) + "---\n")


def _write_plan(epic_dir: Path, plan: dict[str, Any]) -> None:
    epic_dir.mkdir(parents=True, exist_ok=True)
    (epic_dir / "plan.json").write_text(json.dumps(plan))


def _ctx(tmp_path: Path, plan: dict[str, Any], story_id: str = "S2") -> CheckContext:
    epic_dir = tmp_path / ".woof" / "epics" / "E42"
    _write_epic(epic_dir, _epic_front_matter())
    _write_plan(epic_dir, plan)
    return CheckContext(
        epic_id=42,
        story_id=story_id,
        repo_root=tmp_path,
        epic_dir=epic_dir,
        plan=plan,
        critique=None,
    )


def _run(tmp_path: Path, plan: dict[str, Any], story_id: str = "S2"):
    if shutil.which("ajv") is None:
        pytest.skip("ajv not on PATH")
    return check_5_plan_crossrefs_runner(_ctx(tmp_path, plan, story_id))


def _failing_evidence(tmp_path: Path, plan: dict[str, Any], story_id: str = "S2") -> str:
    outcome = _run(tmp_path, plan, story_id)
    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence
    return outcome.evidence


def test_valid_plan_passes(tmp_path: Path) -> None:
    outcome = _run(tmp_path, _valid_plan())

    assert outcome.ok
    assert outcome.id == "check_5_plan_crossrefs"
    assert outcome.severity == "info"


def test_plan_schema_failure_fails(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    del plan["goal"]

    evidence = _failing_evidence(tmp_path, plan)

    assert "plan.json schema invalid" in evidence
    assert "must NOT have fewer than 1 characters" in evidence


def test_unknown_and_uncovered_outcome_refs_fail(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][1]["satisfies"] = ["O999"]

    evidence = _failing_evidence(tmp_path, plan)

    assert "S2: satisfies unknown outcome O999" in evidence
    assert "O2: active observable outcome is not covered by any work unit" in evidence


def test_contract_decision_refs_and_ownership_fail(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][0]["implements_contract_decisions"] = []
    plan["work_units"][1]["uses_contract_decisions"] = ["CD999"]

    evidence = _failing_evidence(tmp_path, plan)

    assert "S2: uses_contract_decisions references unknown contract decision CD999" in evidence
    assert (
        "CD1: active contract decision must be implemented by exactly one work unit; owners=[]"
        in evidence
    )


def test_duplicate_contract_decision_ownership_fails(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][1]["implements_contract_decisions"] = ["CD1"]

    evidence = _failing_evidence(tmp_path, plan)

    assert "CD1: active contract decision must be implemented by exactly one work unit" in evidence
    assert "owners=['S1', 'S2']" in evidence


def test_dependency_closure_fails(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][1]["deps"] = ["S999"]

    evidence = _failing_evidence(tmp_path, plan)

    assert "S2: deps references unknown work unit S999" in evidence


def test_self_dependency_fails(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][1]["deps"] = ["S2"]

    evidence = _failing_evidence(tmp_path, plan)

    assert "S2: deps references itself" in evidence


def test_dependency_order_fails_when_dependency_appears_later(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"] = [plan["work_units"][1], plan["work_units"][0]]

    evidence = _failing_evidence(tmp_path, plan)

    assert "S2: deps S1 appears after dependent work unit" in evidence


def test_dependency_cycle_fails(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][0]["deps"] = ["S2"]

    evidence = _failing_evidence(tmp_path, plan)

    assert "dependency cycle detected: S1 -> S2 -> S1" in evidence


def test_duplicate_story_pathspec_fails(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][1]["paths"] = ["src/S1.py"]

    evidence = _failing_evidence(tmp_path, plan)

    assert "pathspec 'src/S1.py' appears in multiple work units: ['S1', 'S2']" in evidence


def test_status_coherence_fails(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][0]["status"] = "in_progress"

    evidence = _failing_evidence(tmp_path, plan)

    assert "multiple work units are in_progress: ['S1', 'S2']" in evidence
    assert "S2: status=in_progress but dependency S1 is status=in_progress" in evidence


def test_current_story_pending_fails(tmp_path: Path) -> None:
    plan = deepcopy(_valid_plan())
    plan["work_units"][1]["status"] = "pending"

    evidence = _failing_evidence(tmp_path, plan)

    assert "S2: current work unit is still pending during Stage-5 checks" in evidence


def test_stage3_plan_contract_requires_pending_statuses() -> None:
    plan = deepcopy(_valid_plan())

    failures = stage3_plan_contract_failures(plan, _epic_front_matter())

    assert "S1: Stage-3 plans must enter the plan gate with status=pending, got done" in failures
    assert (
        "S2: Stage-3 plans must enter the plan gate with status=pending, got in_progress"
        in failures
    )
