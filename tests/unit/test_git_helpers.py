from __future__ import annotations

import subprocess
from pathlib import Path

from woof.graph.git import changed_paths, staged_paths


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_changed_paths_reports_rename_destination(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    tmp_path.joinpath("a.txt").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-qm", "init")

    _git(tmp_path, "mv", "a.txt", "b.txt")

    assert staged_paths(tmp_path) == ["b.txt"]
    assert changed_paths(tmp_path) == ["b.txt"]
