"""Shared git-pathspec evaluation for Stage-5 path-discipline checks.

Stage 5 Check 3, Check 7, and the transaction manifest all need to answer
"is this path matched by the active work unit's git-pathspec set?". They must
agree on the answer or the same staged transaction can be approved by one
check and rejected by another.

Python's :mod:`fnmatch` is the wrong matcher for git pathspecs: ``*``
should not cross ``/``, ``**/`` should match zero or more directory
components, and magic prefixes such as ``:(glob)`` and ``:(literal)``
have no fnmatch analogue. This module delegates to git itself so the
runtime semantics agree with ``git diff --cached`` and ``git ls-files``.

Two helpers are exposed:

* :func:`staged_paths_matching` returns the staged paths git considers
  in scope of ``pathspecs`` (the question Check 3 and the staged side
  of Check 7 ask).
* :func:`filter_paths_matching` returns the subset of ``candidates``
  that git considers in scope of ``pathspecs``. Useful when the caller
  already has a curated list (for example ``git status --porcelain``
  output) and only needs to project it through the pathspec engine.

Both helpers raise :class:`PathspecEvaluationError` when git itself
rejects the pathspecs so callers can surface structured blocker
outcomes rather than silently passing or failing.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from woof.graph.git import git_env


class PathspecEvaluationError(RuntimeError):
    """Raised when git rejects pathspec evaluation."""

    def __init__(self, command: list[str], stderr: str, returncode: int) -> None:
        message = stderr.strip() or f"git pathspec evaluation failed (exit {returncode})"
        super().__init__(message)
        self.command = command
        self.stderr = stderr
        self.returncode = returncode

    def command_string(self) -> str:
        return shlex.join(self.command)


def staged_paths_matching(repo_root: Path, pathspecs: list[str]) -> list[str]:
    """Return staged paths matching any of ``pathspecs`` via git's engine."""

    if not pathspecs:
        return []
    command = ["git", "diff", "--cached", "--name-only", "-z", "--", *pathspecs]
    proc = subprocess.run(
        command,
        cwd=repo_root,
        env=git_env(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PathspecEvaluationError(
            command,
            proc.stderr.decode(errors="replace"),
            proc.returncode,
        )
    return sorted({part.decode() for part in proc.stdout.split(b"\0") if part})


def filter_paths_matching(
    repo_root: Path, candidates: list[str], pathspecs: list[str]
) -> list[str]:
    """Return the subset of ``candidates`` matched by ``pathspecs`` via git.

    Uses ``git ls-files -c -m -o -d --exclude-standard -- <pathspecs>`` so
    cached, modified, untracked (not gitignored), and staged-deletion paths
    are all considered. The intersection with ``candidates`` preserves the
    caller's existing path universe (typically the output of
    ``git status --porcelain``).
    """

    if not candidates or not pathspecs:
        return []
    command = [
        "git",
        "ls-files",
        "-c",
        "-m",
        "-o",
        "-d",
        "--exclude-standard",
        "-z",
        "--",
        *pathspecs,
    ]
    proc = subprocess.run(
        command,
        cwd=repo_root,
        env=git_env(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PathspecEvaluationError(
            command,
            proc.stderr.decode(errors="replace"),
            proc.returncode,
        )
    matched = {part.decode() for part in proc.stdout.split(b"\0") if part}
    return [path for path in candidates if path in matched]
