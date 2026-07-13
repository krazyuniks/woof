"""Tests for check_3_scope — Stage-5 Check 3."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.support import DEFAULT_PROJECT_KEY
from woof import state
from woof.checks import CheckContext
from woof.checks.runners.check_3_scope import check_3_scope_runner

pytestmark = pytest.mark.host_only


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _write(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _git_add(root: Path, *paths: str) -> None:
    subprocess.run(["git", "add", "--", *paths], cwd=root, check=True)


def _commit(root: Path, message: str = "seed") -> None:
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True)


def _ctx(root: Path, paths: list[str], work_unit_id: str = "S1") -> CheckContext:
    plan = {
        "epic_id": 7,
        "goal": "test scope",
        "work_units": [
            {
                "id": "S1",
                "title": "Scoped story",
                "summary": "Touch allowed files only",
                "paths": paths,
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "deps": [],
                "tests": {"count": 1, "types": ["unit"]},
                "state": "in_progress",
            }
        ],
    }
    return CheckContext(
        epic_id=7,
        work_unit_id=work_unit_id,
        project_key=DEFAULT_PROJECT_KEY,
        repo_root=root,
        epic_dir=state.epic_dir(DEFAULT_PROJECT_KEY, 7),
        plan=plan,
        critique=None,
    )


def test_staged_paths_within_work_unit_scope_pass(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path / "src/app.py")
    _write(tmp_path / "tests/test_app.py")
    _git_add(tmp_path, "src/app.py", "tests/test_app.py")

    outcome = check_3_scope_runner(_ctx(tmp_path, ["src/", "tests/test_app.py"]))

    assert outcome.ok
    assert outcome.severity is None
    assert outcome.paths == ["src/app.py", "tests/test_app.py"]


def test_staged_paths_outside_work_unit_scope_fail(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path / "src/app.py")
    _write(tmp_path / "docs/notes.md")
    _write(tmp_path / "scripts/tool.sh")
    _git_add(tmp_path, "src/app.py", "docs/notes.md", "scripts/tool.sh")

    outcome = check_3_scope_runner(_ctx(tmp_path, ["src/"]))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.paths == ["docs/notes.md", "scripts/tool.sh"]
    assert "outside work unit S1 scope" in outcome.summary


def test_deleted_file_is_checked_against_work_unit_scope(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path / "src/allowed.py")
    _write(tmp_path / "docs/removed.md")
    _git_add(tmp_path, "src/allowed.py", "docs/removed.md")
    _commit(tmp_path)

    (tmp_path / "src/allowed.py").unlink()
    (tmp_path / "docs/removed.md").unlink()
    _git_add(tmp_path, "src/allowed.py", "docs/removed.md")

    outcome = check_3_scope_runner(_ctx(tmp_path, ["src/"]))

    assert not outcome.ok
    assert outcome.paths == ["docs/removed.md"]


def test_git_pathspec_edge_cases_use_git_matching(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path / "src/package/service.py")
    _write(tmp_path / "literal[1].txt")
    _write(tmp_path / "src/package/data.json")
    _git_add(tmp_path, "src/package/service.py", "literal[1].txt", "src/package/data.json")

    outcome = check_3_scope_runner(
        _ctx(tmp_path, [":(glob)src/**/*.py", ":(literal)literal[1].txt"])
    )

    assert not outcome.ok
    assert outcome.paths == ["src/package/data.json"]


def test_recursive_glob_pathspec_matches_via_git_engine(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path / "src/pkg/subpkg/deep.py")
    _write(tmp_path / "src/pkg/shallow.py")
    _git_add(tmp_path, "src/pkg/subpkg/deep.py", "src/pkg/shallow.py")

    outcome = check_3_scope_runner(_ctx(tmp_path, [":(glob)src/**/*.py"]))

    assert outcome.ok
    assert outcome.severity is None
    assert "src/pkg/subpkg/deep.py" in outcome.paths
    assert "src/pkg/shallow.py" in outcome.paths


def test_missing_story_fails_with_structured_outcome(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    outcome = check_3_scope_runner(_ctx(tmp_path, ["src/"], work_unit_id="S9"))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.paths == []
    assert "not found" in outcome.summary


def test_malformed_paths_fail(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    outcome = check_3_scope_runner(_ctx(tmp_path, [""]))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "malformed paths[]" in outcome.summary
