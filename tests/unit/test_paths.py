"""Operator-home path helpers and project-key resolution (ADR-017)."""

from __future__ import annotations

from pathlib import Path

import pytest

import woof.paths as paths_module
from woof.paths import (
    ProjectKeyError,
    project_config_path,
    project_state_root,
    repo_root_from_git,
    resolve_project_key,
    woof_home,
)


def test_woof_home_defaults_to_dot_woof_in_the_operator_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WOOF_HOME", raising=False)
    assert woof_home() == Path.home() / ".woof"


def test_woof_home_honours_the_woof_home_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOOF_HOME", "~/somewhere/else")
    assert woof_home() == Path.home() / "somewhere" / "else"


def test_project_config_path_is_keyed_under_the_operator_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WOOF_HOME", str(tmp_path))
    assert project_config_path("woof") == tmp_path / "config" / "projects" / "woof.toml"


def test_project_state_root_is_keyed_under_the_operator_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WOOF_HOME", str(tmp_path))
    assert project_state_root("woof") == tmp_path / "state" / "projects" / "woof"


def test_resolve_project_key_prefers_the_explicit_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WOOF_PROJECT", "from-env")
    assert resolve_project_key("explicit") == "explicit"


def test_resolve_project_key_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WOOF_PROJECT", "from-env")
    assert resolve_project_key(None) == "from-env"


def test_resolve_project_key_without_a_key_names_the_missing_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WOOF_PROJECT", raising=False)
    with pytest.raises(ProjectKeyError) as excinfo:
        resolve_project_key(None)
    message = str(excinfo.value)
    assert "--project" in message
    assert "WOOF_PROJECT" in message


@pytest.mark.parametrize("key", ["../escape", "a/b", "", ".", "Upper Case"])
def test_resolve_project_key_rejects_keys_that_are_not_safe_filenames(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WOOF_PROJECT", raising=False)
    with pytest.raises(ProjectKeyError):
        resolve_project_key(key)


def test_repo_root_from_git_resolves_the_git_toplevel(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "checkout"
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    assert repo_root_from_git(nested).resolve() == repo.resolve()


def test_repo_root_from_git_fails_outside_a_git_checkout(tmp_path: Path) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    with pytest.raises(FileNotFoundError):
        repo_root_from_git(outside)


def test_find_project_root_is_retired() -> None:
    """The `.woof/` sentinel walk is deleted; nothing may resolve a repo that way."""

    assert not hasattr(paths_module, "find_project_root")
