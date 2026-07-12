"""Repo-wide pytest environment isolation."""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest

from tests.support import DEFAULT_PROJECT_KEY, MINIMAL_PROJECT_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tmux_substrate_available() -> bool:
    """Mirror the dispatcher's resolution of the tmux substrate.

    Dispatch subprocesses import ``tmux_harness`` from the active environment or
    from an agent-toolkit checkout next to this repo; without one of those plus
    a ``tmux`` binary, every real dispatch fails at launch.
    """
    if shutil.which("tmux") is None:
        return False
    if importlib.util.find_spec("tmux_harness") is not None:
        return True
    candidates = [
        REPO_ROOT.parent / "agent-toolkit" / "skills" / "tmux-harness",
        Path.home() / "Work" / "agent-toolkit" / "skills" / "tmux-harness",
    ]
    return any(candidate.is_dir() for candidate in candidates)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _tmux_substrate_available():
        return
    if os.environ.get("WOOF_REQUIRE_TMUX_SUBSTRATE") == "1":
        raise pytest.UsageError(
            "WOOF_REQUIRE_TMUX_SUBSTRATE=1 but the tmux substrate is unavailable "
            "(needs the tmux binary plus the tmux_harness package). Run: just setup"
        )
    skip = pytest.mark.skip(
        reason="tmux substrate unavailable: needs tmux plus the tmux_harness package"
    )
    for item in items:
        if "tmux_substrate" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def woof_home(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Root every test's engine config and state in a throwaway WOOF_HOME.

    Without this, any code path that resolves the operator home reads or writes
    the operator's real ``~/.woof``. The default project key is seeded with a
    working config so CLI tests resolve one; a key that was never written still
    raises, which is what the missing-config tests assert.
    """

    home = tmp_path_factory.mktemp("woof-home")
    monkeypatch.setenv("WOOF_HOME", str(home))
    monkeypatch.setenv("WOOF_PROJECT", DEFAULT_PROJECT_KEY)
    projects = home / "config" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / f"{DEFAULT_PROJECT_KEY}.toml").write_text(MINIMAL_PROJECT_CONFIG, encoding="utf-8")
    return home


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
