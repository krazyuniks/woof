"""Tests for the runtime work-unit aggregate."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from woof.graph.state import Plan


def _unit(unit_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "id": unit_id,
        "title": f"Unit {unit_id}",
        "summary": f"Deliver {unit_id}",
        "paths": [f"src/{unit_id}.py"],
        "acceptance": [],
        "deps": [],
        "satisfies": [],
        "implements_contract_decisions": [],
        "uses_contract_decisions": [],
        "tests": {"count": 1, "types": ["unit"]},
        "status": "pending",
    }
    payload.update(overrides)
    return payload


def _plan() -> dict[str, Any]:
    return {
        "epic_id": 1,
        "goal": "Prove aggregate invariants.",
        "work_units": [_unit("S1"), _unit("S2", deps=["S1"])],
    }


def _validation_message(payload: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as exc_info:
        Plan.model_validate(payload)
    return str(exc_info.value)


def test_plan_accepts_legacy_stories_at_single_inbound_boundary() -> None:
    payload = _plan()
    payload["stories"] = payload.pop("work_units")
    payload["stories"][0]["intent"] = payload["stories"][0].pop("summary")
    payload["stories"][1]["depends_on"] = payload["stories"][1].pop("deps")

    plan = Plan.model_validate(payload)

    assert [unit.id for unit in plan.work_units] == ["S1", "S2"]
    assert plan.work_units[0].summary == "Deliver S1"
    assert plan.work_units[1].deps == ["S1"]


def test_plan_rejects_dual_current_and_legacy_shapes() -> None:
    payload = _plan()
    payload["stories"] = deepcopy(payload["work_units"])

    assert "plan cannot carry both work_units and legacy stories" in _validation_message(payload)


def test_plan_rejects_duplicate_work_unit_ids() -> None:
    payload = _plan()
    payload["work_units"][1]["id"] = "S1"

    assert "work_unit id S1 appears 2 times" in _validation_message(payload)


def test_plan_rejects_dangling_dependencies() -> None:
    payload = _plan()
    payload["work_units"][1]["deps"] = ["S99"]

    assert "S2: deps references unknown work unit S99" in _validation_message(payload)


def test_plan_rejects_self_dependencies() -> None:
    payload = _plan()
    payload["work_units"][1]["deps"] = ["S2"]

    assert "S2: deps references itself" in _validation_message(payload)


def test_plan_rejects_dependency_cycles() -> None:
    payload = _plan()
    payload["work_units"][0]["deps"] = ["S2"]

    assert "dependency cycle detected: S1 -> S2 -> S1" in _validation_message(payload)


def test_plan_rejects_unsorted_dependency_order() -> None:
    payload = _plan()
    payload["work_units"] = [payload["work_units"][1], payload["work_units"][0]]

    assert "S2: deps S1 appears after dependent work unit" in _validation_message(payload)


def test_plan_accepts_work_unit_set_context_without_epic() -> None:
    payload = _plan()
    del payload["epic_id"]
    payload["context"] = {
        "kind": "work_unit_set",
        "project_ref": "woof",
        "set_id": "wave-4",
        "source_ref": "docs/backlog.md",
    }

    plan = Plan.model_validate(payload)

    assert plan.epic_id is None
    assert plan.context is not None
    assert plan.context.kind == "work_unit_set"


def test_plan_rejects_mismatched_epic_context() -> None:
    payload = _plan()
    payload["context"] = {"kind": "epic", "project_ref": "woof", "epic_id": 99}

    assert "context epic_id 99 does not match plan epic_id 1" in _validation_message(payload)
