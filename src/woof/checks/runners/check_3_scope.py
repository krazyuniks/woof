"""check_3_scope — Stage-5 Check 3.

Verifies that every staged non-.woof path is within the current story's
``paths[]`` allow-list, using Git's own pathspec matcher. Durable Stage-5
artefacts for the current epic/story are allowed separately.
"""

from __future__ import annotations

import shlex
import subprocess

from woof.checks import CheckContext, CheckOutcome
from woof.graph.git import staged_paths

CHECK_ID = "check_3_scope"


def check_3_scope_runner(ctx: CheckContext) -> CheckOutcome:
    story = _story_for_context(ctx)
    if story is None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"story {ctx.story_id} not found in plan.json",
        )

    story_pathspecs = story.get("paths") or []
    if not all(isinstance(pathspec, str) and pathspec for pathspec in story_pathspecs):
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"story {ctx.story_id} has malformed paths[] entries",
            evidence=f"paths={story_pathspecs!r}",
        )

    staged = staged_paths(ctx.repo_root)
    staged_story_paths = [path for path in staged if not path.startswith(".woof/")]
    allowed_woof_paths = [path for path in staged if _is_allowed_woof_path(ctx, path)]
    forbidden_woof_paths = [
        path for path in staged if path.startswith(".woof/") and path not in allowed_woof_paths
    ]

    allowed_story_paths = _git_pathspec_matches(ctx, story_pathspecs)
    if isinstance(allowed_story_paths, CheckOutcome):
        return allowed_story_paths

    allowed_story_path_set = set(allowed_story_paths)
    forbidden_story_paths = [
        path for path in staged_story_paths if path not in allowed_story_path_set
    ]
    forbidden_paths = sorted(forbidden_story_paths + forbidden_woof_paths)

    if forbidden_paths:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=(f"{len(forbidden_paths)} staged path(s) outside story {ctx.story_id} scope"),
            evidence=f"allowed story paths: {story_pathspecs!r}",
            paths=forbidden_paths,
            command=_pathspec_command(story_pathspecs),
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity=None,
        summary=(
            f"{len(staged_story_paths)} staged story path(s) within story {ctx.story_id} scope; "
            f"{len(allowed_woof_paths)} durable .woof path(s) allowed"
        ),
        paths=staged,
        command=_pathspec_command(story_pathspecs),
    )


def _story_for_context(ctx: CheckContext) -> dict | None:
    for story in ctx.plan.get("stories", []):
        if isinstance(story, dict) and story.get("id") == ctx.story_id:
            return story
    return None


def _git_pathspec_matches(ctx: CheckContext, pathspecs: list[str]) -> list[str] | CheckOutcome:
    command = ["git", "diff", "--cached", "--name-only", "-z", "--", *pathspecs]
    proc = subprocess.run(
        command,
        cwd=ctx.repo_root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"git pathspec evaluation failed for story {ctx.story_id}",
            evidence=proc.stderr.decode(errors="replace").strip() or None,
            command=_pathspec_command(pathspecs),
            exit_code=proc.returncode,
        )
    return sorted(path.decode() for path in proc.stdout.split(b"\0") if path)


def _is_allowed_woof_path(ctx: CheckContext, path: str) -> bool:
    epic_prefix = f".woof/epics/E{ctx.epic_id}/"
    allowed_exact = {
        f"{epic_prefix}plan.json",
        f"{epic_prefix}epic.jsonl",
        f"{epic_prefix}dispatch.jsonl",
        f"{epic_prefix}critique/story-{ctx.story_id}.md",
    }
    if path in allowed_exact:
        return True
    audit_prefix = f"{epic_prefix}audit/"
    if not path.startswith(audit_prefix):
        return False
    audit_relative = path[len(audit_prefix) :]
    return "raw" not in audit_relative.split("/")


def _pathspec_command(pathspecs: list[str]) -> str:
    return shlex.join(["git", "diff", "--cached", "--name-only", "-z", "--", *pathspecs])
