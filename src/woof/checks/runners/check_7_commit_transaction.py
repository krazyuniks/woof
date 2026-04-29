"""check_7_commit_transaction - Stage-5 Check 7.

Verifies that the pending story transaction is commit-ready:
  1. Required durable .woof artefacts are staged
  2. Non-empty stories have at least one staged in-scope story path
  3. Staged paths contain only story paths plus allowed durable/audit .woof paths
  4. No unstaged or untracked paths remain in the worktree
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from woof.checks import CheckContext, CheckOutcome

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


def _story(ctx: CheckContext) -> dict | None:
    for story in ctx.plan.get("stories", []):
        if isinstance(story, dict) and story.get("id") == ctx.story_id:
            return story
    return None


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _required_paths(ctx: CheckContext) -> list[str]:
    epic = f"E{ctx.epic_id}"
    return [
        f".woof/epics/{epic}/plan.json",
        f".woof/epics/{epic}/epic.jsonl",
        f".woof/epics/{epic}/dispatch.jsonl",
        f".woof/epics/{epic}/critique/story-{ctx.story_id}.md",
    ]


def _is_allowed_woof_path(ctx: CheckContext, path: str, required: set[str]) -> bool:
    if path in required:
        return True
    audit_prefix = f".woof/epics/E{ctx.epic_id}/audit/"
    if not path.startswith(audit_prefix):
        return False
    audit_relative = path[len(audit_prefix) :]
    return "raw/" not in audit_relative and audit_relative != "raw"


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
    story = _story(ctx)
    if story is None:
        return _failure(
            summary=f"story {ctx.story_id!r} not found in plan.json",
            evidence=["plan.json has no matching story entry"],
            paths=[f".woof/epics/E{ctx.epic_id}/plan.json"],
        )

    story_patterns = [str(pattern) for pattern in story.get("paths", [])]
    empty_diff = bool(story.get("empty_diff", False))
    required = set(_required_paths(ctx))

    try:
        staged = _staged_paths(ctx.repo_root)
        status_entries = _status_entries(ctx.repo_root)
    except subprocess.CalledProcessError as exc:
        return _failure(
            summary="git status inspection failed",
            evidence=[(exc.stderr or exc.stdout or str(exc)).strip()],
            paths=[],
        )

    staged_story_paths = [
        path
        for path in staged
        if not path.startswith(".woof/") and _matches_any(path, story_patterns)
    ]
    missing_required = sorted(path for path in required if path not in staged)
    foreign_staged = sorted(
        path
        for path in staged
        if (
            (path.startswith(".woof/") and not _is_allowed_woof_path(ctx, path, required))
            or (not path.startswith(".woof/") and not _matches_any(path, story_patterns))
        )
    )
    unstaged = sorted(path for status, path in status_entries if _is_unstaged(status))

    evidence: list[str] = []
    paths: list[str] = []
    if missing_required:
        evidence.append(f"missing required staged paths: {missing_required}")
        paths.extend(missing_required)
    if not empty_diff and not staged_story_paths:
        evidence.append("no staged story paths matched story.paths[]")
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

    story_count = len(staged_story_paths)
    if empty_diff:
        summary = "empty_diff story has required durable artefacts staged and no unstaged paths"
    else:
        summary = (
            f"{story_count} staged story path(s) plus required durable artefacts are commit-ready"
        )
    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=summary,
        paths=staged,
    )
