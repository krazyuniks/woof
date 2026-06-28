"""check_5_plan_crossrefs — Stage-5 Check 5.

Validates plan integrity against the shipped JSON Schema and the Definition
artefact it references.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from woof.checks import CheckContext, CheckOutcome
from woof.graph.state import Plan
from woof.paths import schema_dir

CHECK_ID = "check_5_plan_crossrefs"


def check_5_plan_crossrefs_runner(ctx: CheckContext) -> CheckOutcome:
    plan_path = ctx.epic_dir / "plan.json"
    epic_path = ctx.epic_dir / "EPIC.md"
    paths = [_display_path(plan_path, ctx.repo_root), _display_path(epic_path, ctx.repo_root)]

    failures: list[str] = []
    plan = _load_plan(plan_path, ctx.plan, failures)
    epic = _load_epic_front_matter(epic_path, failures)

    if plan is not None:
        ok, output = _validate_plan_schema(plan_path, plan)
        if not ok:
            failures.append(f"plan.json schema invalid: {output}")

    if plan is not None and epic is not None:
        failures.extend(stage5_plan_contract_failures(plan, epic, ctx.story_id))

    if failures:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"plan cross-reference validation failed ({len(failures)} issue(s))",
            evidence="\n".join(failures),
            paths=paths,
        )

    work_unit_count = len(plan.get("work_units", [])) if plan else 0
    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=f"plan schema and cross-reference invariants valid ({work_unit_count} work unit(s))",
        paths=paths,
    )


def _load_plan(
    plan_path: Path, context_plan: dict[str, Any], failures: list[str]
) -> dict[str, Any] | None:
    if plan_path.exists():
        try:
            payload = Plan.model_validate_json(plan_path.read_text()).model_dump(exclude_none=True)
        except ValueError as exc:
            failures.append(f"plan.json parse error: {exc}")
            return None
        return payload

    if context_plan:
        return context_plan

    failures.append("plan.json missing")
    return None


def _load_epic_front_matter(epic_path: Path, failures: list[str]) -> dict[str, Any] | None:
    if not epic_path.exists():
        failures.append("EPIC.md missing")
        return None

    text = epic_path.read_text()
    if not text.startswith("---\n"):
        failures.append("EPIC.md missing YAML front-matter")
        return None

    end = text.find("\n---\n", 4)
    if end < 0:
        end_alt = text.find("\n---", 4)
        if end_alt < 0 or text[end_alt:].rstrip() != "---":
            failures.append("EPIC.md has unterminated YAML front-matter")
            return None
        end = end_alt

    try:
        payload = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        failures.append(f"EPIC.md front-matter parse error: {exc}")
        return None

    if not isinstance(payload, dict):
        failures.append("EPIC.md front-matter root must be an object")
        return None
    return payload


def _validate_plan_schema(plan_path: Path, plan: dict[str, Any]) -> tuple[bool, str]:
    if shutil.which("ajv") is None:
        return False, "ajv-cli not found on PATH"

    schema_path = schema_dir() / "plan.schema.json"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(plan, fh)
        data_path = Path(fh.name)

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
                str(data_path),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        data_path.unlink(missing_ok=True)

    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0:
        return True, f"{plan_path}: valid"
    return False, output or f"ajv exited {proc.returncode}"


def stage3_plan_contract_failures(plan: dict[str, Any], epic: dict[str, Any]) -> list[str]:
    """Return plan invariant failures that must be fixed before the plan gate."""

    return _crossref_failures(plan, epic, current_story_id=None, stage=3)


def stage5_plan_contract_failures(
    plan: dict[str, Any], epic: dict[str, Any], story_id: str
) -> list[str]:
    """Return plan invariant failures checked during Stage 5 verification."""

    return _crossref_failures(plan, epic, current_story_id=story_id, stage=5)


def _crossref_failures(
    plan: dict[str, Any],
    epic: dict[str, Any],
    *,
    current_story_id: str | None,
    stage: int,
) -> list[str]:
    failures: list[str] = []
    work_units = _object_items(plan.get("work_units"))
    outcomes = _object_items(epic.get("observable_outcomes"))
    cds = _object_items(epic.get("contract_decisions"))

    work_unit_ids = [unit_id for unit in work_units if isinstance(unit_id := unit.get("id"), str)]
    active_outcome_ids = {
        outcome["id"]
        for outcome in outcomes
        if isinstance(outcome.get("id"), str) and not outcome.get("deprecated", False)
    }
    deprecated_outcome_ids = {
        outcome["id"]
        for outcome in outcomes
        if isinstance(outcome.get("id"), str) and outcome.get("deprecated", False)
    }
    active_cd_ids = {
        cd["id"] for cd in cds if isinstance(cd.get("id"), str) and not cd.get("deprecated", False)
    }
    deprecated_cd_ids = {
        cd["id"] for cd in cds if isinstance(cd.get("id"), str) and cd.get("deprecated", False)
    }

    failures.extend(_duplicate_id_failures("work_unit", work_unit_ids))
    failures.extend(
        _duplicate_id_failures(
            "observable_outcome",
            [oid for outcome in outcomes if isinstance(oid := outcome.get("id"), str)],
        )
    )
    failures.extend(
        _duplicate_id_failures(
            "contract_decision",
            [cdid for cd in cds if isinstance(cdid := cd.get("id"), str)],
        )
    )

    work_unit_id_set = set(work_unit_ids)
    satisfied_by: dict[str, list[str]] = defaultdict(list)
    implemented_by: dict[str, list[str]] = defaultdict(list)

    for unit in work_units:
        unit_id = unit.get("id", "<missing>")
        for outcome_id in _string_list(unit.get("satisfies")):
            satisfied_by[outcome_id].append(str(unit_id))
            if outcome_id in deprecated_outcome_ids:
                failures.append(f"{unit_id}: satisfies deprecated outcome {outcome_id}")
            elif outcome_id not in active_outcome_ids:
                failures.append(f"{unit_id}: satisfies unknown outcome {outcome_id}")

        for field in ("implements_contract_decisions", "uses_contract_decisions"):
            for cd_id in _string_list(unit.get(field)):
                if field == "implements_contract_decisions":
                    implemented_by[cd_id].append(str(unit_id))
                if cd_id in deprecated_cd_ids:
                    failures.append(
                        f"{unit_id}: {field} references deprecated contract decision {cd_id}"
                    )
                elif cd_id not in active_cd_ids:
                    failures.append(
                        f"{unit_id}: {field} references unknown contract decision {cd_id}"
                    )

        for dep_id in _string_list(unit.get("deps")):
            if dep_id == unit_id:
                failures.append(f"{unit_id}: deps references itself")
            elif dep_id not in work_unit_id_set:
                failures.append(f"{unit_id}: deps references unknown work unit {dep_id}")

    for outcome_id in sorted(active_outcome_ids):
        if outcome_id not in satisfied_by:
            failures.append(
                f"{outcome_id}: active observable outcome is not covered by any work unit"
            )

    for cd in cds:
        cd_id = cd.get("id")
        if not isinstance(cd_id, str) or cd.get("deprecated", False):
            continue
        for outcome_id in _string_list(cd.get("related_outcomes")):
            if outcome_id in deprecated_outcome_ids:
                failures.append(
                    f"{cd_id}: related_outcomes references deprecated outcome {outcome_id}"
                )
            elif outcome_id not in active_outcome_ids:
                failures.append(
                    f"{cd_id}: related_outcomes references unknown outcome {outcome_id}"
                )

    for cd_id in sorted(active_cd_ids):
        owners = implemented_by.get(cd_id, [])
        if len(owners) != 1:
            failures.append(
                f"{cd_id}: active contract decision must be implemented by exactly one work unit; owners={owners}"
            )

    failures.extend(_cycle_failures(work_units, work_unit_id_set))
    failures.extend(_topological_order_failures(work_units, work_unit_id_set))
    failures.extend(_path_scope_failures(work_units))
    if stage == 3:
        failures.extend(_stage3_status_failures(work_units))
    else:
        if current_story_id is None:
            raise ValueError("current_story_id is required for Stage-5 plan validation")
        failures.extend(_stage5_status_failures(work_units, current_story_id, work_unit_id_set))
    return failures


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _object_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _duplicate_id_failures(label: str, ids: list[str]) -> list[str]:
    return [
        f"{label} id {item_id} appears {count} times"
        for item_id, count in sorted(Counter(ids).items())
        if count > 1
    ]


def _cycle_failures(work_units: list[dict[str, Any]], work_unit_id_set: set[str]) -> list[str]:
    deps = {
        unit["id"]: [dep for dep in _string_list(unit.get("deps")) if dep in work_unit_id_set]
        for unit in work_units
        if isinstance(unit.get("id"), str)
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: set[tuple[str, ...]] = set()

    def visit(sid: str, stack: list[str]) -> None:
        if sid in visited:
            return
        if sid in visiting:
            start = stack.index(sid)
            cycle = tuple(stack[start:])
            cycles.add(cycle)
            return

        visiting.add(sid)
        for dep_id in deps.get(sid, []):
            visit(dep_id, [*stack, dep_id])
        visiting.remove(sid)
        visited.add(sid)

    for sid in deps:
        visit(sid, [sid])

    return [f"dependency cycle detected: {' -> '.join(cycle)}" for cycle in sorted(cycles)]


def _topological_order_failures(
    work_units: list[dict[str, Any]], work_unit_id_set: set[str]
) -> list[str]:
    work_unit_order = {
        unit["id"]: index
        for index, unit in enumerate(work_units)
        if isinstance(unit.get("id"), str)
    }
    failures: list[str] = []
    for unit in work_units:
        unit_id = unit.get("id")
        if not isinstance(unit_id, str):
            continue
        for dep_id in _string_list(unit.get("deps")):
            if dep_id in work_unit_id_set and work_unit_order[dep_id] > work_unit_order[unit_id]:
                failures.append(
                    f"{unit_id}: deps {dep_id} appears after dependent work unit; "
                    "work_units must be topologically sorted"
                )
    return failures


def _path_scope_failures(work_units: list[dict[str, Any]]) -> list[str]:
    owners_by_pathspec: dict[str, list[str]] = defaultdict(list)
    for unit in work_units:
        unit_id = unit.get("id")
        if not isinstance(unit_id, str):
            continue
        for pathspec in _string_list(unit.get("paths")):
            owners_by_pathspec[pathspec].append(unit_id)
    return [
        f"pathspec {pathspec!r} appears in multiple work units: {owners}"
        for pathspec, owners in sorted(owners_by_pathspec.items())
        if len(owners) > 1
    ]


def _stage3_status_failures(work_units: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for unit in work_units:
        unit_id = unit.get("id")
        if not isinstance(unit_id, str):
            continue
        status = unit.get("status")
        if status != "pending":
            failures.append(
                f"{unit_id}: Stage-3 plans must enter the plan gate with status=pending, got {status}"
            )
    return failures


def _stage5_status_failures(
    work_units: list[dict[str, Any]], current_story_id: str, work_unit_id_set: set[str]
) -> list[str]:
    failures: list[str] = []
    by_id = {unit.get("id"): unit for unit in work_units if isinstance(unit.get("id"), str)}
    in_progress = [
        unit["id"]
        for unit in work_units
        if isinstance(unit.get("id"), str) and unit.get("status") == "in_progress"
    ]

    if current_story_id not in work_unit_id_set:
        failures.append(f"{current_story_id}: current work unit is not present in plan")
    elif by_id[current_story_id].get("status") == "pending":
        failures.append(
            f"{current_story_id}: current work unit is still pending during Stage-5 checks"
        )

    if len(in_progress) > 1:
        failures.append(f"multiple work units are in_progress: {in_progress}")
    if in_progress and current_story_id not in in_progress:
        failures.append(
            f"{current_story_id}: current work unit does not match in_progress unit {in_progress[0]}"
        )

    for unit in work_units:
        unit_id = unit.get("id")
        if not isinstance(unit_id, str):
            continue
        status = unit.get("status")
        if status not in {"in_progress", "done"}:
            continue
        for dep_id in _string_list(unit.get("deps")):
            dep = by_id.get(dep_id)
            if dep is not None and dep.get("status") != "done":
                failures.append(
                    f"{unit_id}: status={status} but dependency {dep_id} is status={dep.get('status')}"
                )
    return failures
