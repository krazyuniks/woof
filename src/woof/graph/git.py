"""Small git helpers used by the deterministic graph."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_LOCAL_GIT_ENV_VARS = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_WORK_TREE",
}


def git_env() -> dict[str, str]:
    """Return an environment safe for running git against an explicit cwd."""

    env = os.environ.copy()
    for name in _LOCAL_GIT_ENV_VARS:
        env.pop(name, None)
    return env


def git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        env=git_env(),
        capture_output=True,
        text=True,
        check=check,
    )


def git_z(repo_root: Path, *args: str) -> list[str]:
    proc = subprocess.run(
        ["git", *args, "-z"],
        cwd=repo_root,
        env=git_env(),
        capture_output=True,
        check=True,
    )
    return [p.decode() for p in proc.stdout.split(b"\0") if p]


def staged_paths(repo_root: Path) -> list[str]:
    return sorted(git_z(repo_root, "diff", "--cached", "--name-only"))


def changed_paths(repo_root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "-z"],
        cwd=repo_root,
        env=git_env(),
        capture_output=True,
        check=True,
    )
    raw = [p.decode() for p in proc.stdout.split(b"\0") if p]
    paths: list[str] = []
    index = 0
    while index < len(raw):
        entry = raw[index]
        if len(entry) < 4:
            index += 1
            continue
        status = entry[:2]
        path = entry[3:]
        if status.startswith("R") or status.startswith("C"):
            index += 1
            if index < len(raw):
                path = raw[index]
        paths.append(path)
        index += 1
    return sorted(set(paths))
