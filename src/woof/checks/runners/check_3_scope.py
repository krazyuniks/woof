"""check_3_scope — Stage-5 Check 3.

Verifies that every staged path is within the current work unit's ``paths[]``
allow-list, using Git's own pathspec matcher. Engine state lives in the operator
home (ADR-017), so the staged set is the delivery diff and nothing else: there is
no engine-owned path to exempt.
"""

from __future__ import annotations

import shlex

from woof.checks import CheckContext, CheckOutcome
from woof.graph.git import staged_paths
from woof.graph.pathspec import PathspecEvaluationError, staged_paths_matching

CHECK_ID = "check_3_scope"


def check_3_scope_runner(ctx: CheckContext) -> CheckOutcome:
    work_unit = _work_unit_for_context(ctx)
    if work_unit is None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"work unit {ctx.work_unit_id} not found in plan.json",
        )

    work_unit_pathspecs = work_unit.get("paths") or []
    if not all(isinstance(pathspec, str) and pathspec for pathspec in work_unit_pathspecs):
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"work unit {ctx.work_unit_id} has malformed paths[] entries",
            evidence=f"paths={work_unit_pathspecs!r}",
        )

    staged = staged_paths(ctx.repo_root)

    try:
        allowed_paths = staged_paths_matching(ctx.repo_root, work_unit_pathspecs)
    except PathspecEvaluationError as exc:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"git pathspec evaluation failed for work unit {ctx.work_unit_id}",
            evidence=str(exc) or None,
            command=exc.command_string(),
            exit_code=exc.returncode,
        )

    allowed_path_set = set(allowed_paths)
    forbidden_paths = sorted(path for path in staged if path not in allowed_path_set)

    if forbidden_paths:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=(
                f"{len(forbidden_paths)} staged path(s) outside work unit {ctx.work_unit_id} scope"
            ),
            evidence=f"allowed work-unit paths: {work_unit_pathspecs!r}",
            paths=forbidden_paths,
            command=_pathspec_command(work_unit_pathspecs),
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity=None,
        summary=(f"{len(staged)} staged path(s) within work unit {ctx.work_unit_id} scope"),
        paths=staged,
        command=_pathspec_command(work_unit_pathspecs),
    )


def _work_unit_for_context(ctx: CheckContext) -> dict | None:
    for work_unit in ctx.plan.get("work_units", []):
        if isinstance(work_unit, dict) and work_unit.get("id") == ctx.work_unit_id:
            return work_unit
    return None


def _pathspec_command(pathspecs: list[str]) -> str:
    return shlex.join(["git", "diff", "--cached", "--name-only", "-z", "--", *pathspecs])
