"""check_3_scope — Stage-5 Check 3.

Verifies that every staged non-.woof path is within the current work unit's
``paths[]`` allow-list, using Git's own pathspec matcher. Durable Stage-5
artefacts for the current epic/work unit are allowed separately, including the
reviewer-disposition file required by Check 7 and the transaction manifest.
"""

from __future__ import annotations

import shlex

from woof.checks import CheckContext, CheckOutcome
from woof.graph.dispositions import work_unit_disposition_relpath
from woof.graph.git import staged_paths
from woof.graph.manifest import durable_epic_paths
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
    staged_work_unit_paths = [path for path in staged if not path.startswith(".woof/")]
    allowed_woof_paths = [path for path in staged if _is_allowed_woof_path(ctx, path)]
    forbidden_woof_paths = [
        path for path in staged if path.startswith(".woof/") and path not in allowed_woof_paths
    ]

    try:
        allowed_work_unit_paths = staged_paths_matching(ctx.repo_root, work_unit_pathspecs)
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

    allowed_work_unit_path_set = set(allowed_work_unit_paths)
    forbidden_work_unit_paths = [
        path for path in staged_work_unit_paths if path not in allowed_work_unit_path_set
    ]
    forbidden_paths = sorted(forbidden_work_unit_paths + forbidden_woof_paths)

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
        summary=(
            f"{len(staged_work_unit_paths)} staged work-unit path(s) within "
            f"work unit {ctx.work_unit_id} scope; "
            f"{len(allowed_woof_paths)} durable .woof path(s) allowed"
        ),
        paths=staged,
        command=_pathspec_command(work_unit_pathspecs),
    )


def _work_unit_for_context(ctx: CheckContext) -> dict | None:
    for work_unit in ctx.plan.get("work_units", []):
        if isinstance(work_unit, dict) and work_unit.get("id") == ctx.work_unit_id:
            return work_unit
    return None


def _is_allowed_woof_path(ctx: CheckContext, path: str) -> bool:
    epic_prefix = f".woof/epics/E{ctx.epic_id}/"
    allowed_exact = {
        f"{epic_prefix}plan.json",
        f"{epic_prefix}epic.jsonl",
        f"{epic_prefix}dispatch.jsonl",
        f"{epic_prefix}critique/work-unit-{ctx.work_unit_id}.md",
        work_unit_disposition_relpath(ctx.epic_id, ctx.work_unit_id),
    }
    if path in allowed_exact:
        return True
    return path in set(durable_epic_paths(ctx.epic_dir, ctx.repo_root))


def _pathspec_command(pathspecs: list[str]) -> str:
    return shlex.join(["git", "diff", "--cached", "--name-only", "-z", "--", *pathspecs])
