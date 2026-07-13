"""check_7_commit_transaction - Stage-5 Check 7.

Verifies that the pending work-unit transaction is commit-ready:
  1. Non-empty work units have at least one staged in-scope work-unit path
  2. Staged paths contain only work-unit paths
  3. No unstaged or untracked paths remain in the worktree

Since ADR-017 the git transaction is decoupled from engine state: the plan, the
event logs, the critique, and the disposition live in the operator home and can
never be staged, so a delivery commit contains the work unit's delivery paths and
nothing else.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from woof.checks import CheckContext, CheckOutcome
from woof.graph.pathspec import PathspecEvaluationError, staged_paths_matching

CHECK_ID = "check_7_commit_transaction"


def _git_z(repo_root: Path, *args: str) -> list[str]:
    proc = subprocess.run(
        ["git", *args, "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    return [part.decode() for part in proc.stdout.split(b"\0") if part]


def _staged_paths(repo_root: Path) -> list[str]:
    return sorted(_git_z(repo_root, "diff", "--cached", "--name-only"))


def _status_entries(repo_root: Path) -> list[tuple[str, str]]:
    raw = _git_z(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(raw):
        entry = raw[index]
        if len(entry) < 4:
            index += 1
            continue
        status = entry[:2]
        path = entry[3:]
        entries.append((status, path))
        if status.startswith(("R", "C")):
            index += 1
        index += 1
    return entries


def _work_unit(ctx: CheckContext) -> dict | None:
    for work_unit in ctx.plan.get("work_units", []):
        if isinstance(work_unit, dict) and work_unit.get("id") == ctx.work_unit_id:
            return work_unit
    return None


def _is_unstaged(status: str) -> bool:
    if status == "??":
        return True
    return len(status) >= 2 and status[1] != " "


def _failure(
    *,
    summary: str,
    evidence: list[str],
    paths: list[str],
) -> CheckOutcome:
    return CheckOutcome(
        id=CHECK_ID,
        ok=False,
        severity="blocker",
        summary=summary,
        evidence="\n".join(evidence),
        paths=sorted(set(paths)),
    )


def check_7_commit_transaction_runner(ctx: CheckContext) -> CheckOutcome:
    work_unit = _work_unit(ctx)
    if work_unit is None:
        return _failure(
            summary=f"work unit {ctx.work_unit_id!r} not found in plan.json",
            evidence=["plan.json has no matching work-unit entry"],
            paths=[],
        )

    work_unit_patterns = [str(pattern) for pattern in work_unit.get("paths", [])]
    empty_diff = bool(work_unit.get("empty_diff", False))

    try:
        staged = _staged_paths(ctx.repo_root)
        status_entries = _status_entries(ctx.repo_root)
    except subprocess.CalledProcessError as exc:
        return _failure(
            summary="git status inspection failed",
            evidence=[(exc.stderr or exc.stdout or str(exc)).strip()],
            paths=[],
        )

    try:
        work_unit_matched_staged = set(staged_paths_matching(ctx.repo_root, work_unit_patterns))
    except PathspecEvaluationError as exc:
        return _failure(
            summary=f"git pathspec evaluation failed for work unit {ctx.work_unit_id}",
            evidence=[str(exc) or exc.command_string()],
            paths=[],
        )

    staged_work_unit_paths = [path for path in staged if path in work_unit_matched_staged]
    foreign_staged = sorted(path for path in staged if path not in work_unit_matched_staged)
    unstaged = sorted(path for status, path in status_entries if _is_unstaged(status))

    evidence: list[str] = []
    paths: list[str] = []
    if not empty_diff and not staged_work_unit_paths:
        evidence.append("no staged work-unit paths matched work_unit.paths[]")
    if foreign_staged:
        evidence.append(f"foreign staged paths: {foreign_staged}")
        paths.extend(foreign_staged)
    if unstaged:
        evidence.append(f"unstaged or untracked paths remain: {unstaged}")
        paths.extend(unstaged)

    if evidence:
        return _failure(
            summary="commit transaction is not ready",
            evidence=evidence,
            paths=paths,
        )

    if empty_diff and not staged_work_unit_paths:
        summary = "empty_diff work unit has no staged paths and no unstaged paths remain"
    else:
        summary = f"{len(staged_work_unit_paths)} staged work-unit path(s) are commit-ready"
    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=summary,
        paths=staged,
    )
