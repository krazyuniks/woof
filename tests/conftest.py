"""Repo-wide pytest environment isolation."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_git_global_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Keep user-level git ignores/config out of temporary test repositories."""

    git_home = tmp_path_factory.mktemp("git-global")
    gitconfig = git_home / ".gitconfig"
    excludes = git_home / ".gitignore_global"
    gitconfig.write_text(f"[core]\n\texcludesfile = {excludes}\n", encoding="utf-8")
    excludes.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
