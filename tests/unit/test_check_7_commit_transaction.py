"""Tests for check_7_commit_transaction - Stage-5 Check 7."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.support import DEFAULT_PROJECT_KEY
from woof import state
from woof.checks import CheckContext
from woof.checks.runners.check_7_commit_transaction import (
    check_7_commit_transaction_runner,
)

pytestmark = pytest.mark.host_only


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True)


def _init_repo(repo_root: Path) -> None:
    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")


def _plan(work_unit: dict | None = None) -> dict:
    return {
        "epic_id": 7,
        "goal": "test commit transaction",
        "work_units": [
            work_unit
            or {
                "id": "S1",
                "title": "first",
                "paths": ["src/*.py"],
                "satisfies": ["O1"],
                "state": "in_progress",
            }
        ],
    }


def _ctx(repo_root: Path, plan: dict) -> CheckContext:
    return CheckContext(
        epic_id=7,
        work_unit_id="S1",
        project_key=DEFAULT_PROJECT_KEY,
        repo_root=repo_root,
        epic_dir=state.epic_dir(DEFAULT_PROJECT_KEY, 7),
        plan=plan,
        critique=None,
    )


def _write_work_unit_file(repo_root: Path) -> str:
    src = repo_root / "src"
    src.mkdir()
    (src / "app.py").write_text("print('O1')\n")
    return "src/app.py"


def test_clean_commit_transaction_passes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    plan = _plan()
    work_unit_path = _write_work_unit_file(tmp_path)
    _git(tmp_path, "add", "--", work_unit_path)

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert outcome.ok
    assert outcome.id == "check_7_commit_transaction"
    assert outcome.paths == [work_unit_path]


def test_empty_gated_commit_transaction_allows_no_work_unit_paths(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    work_unit = {
        "id": "S1",
        "title": "already realised",
        "paths": ["src/*.py"],
        "satisfies": ["O1"],
        "state": "done",
        "empty_diff": True,
    }
    plan = _plan(work_unit)

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert outcome.ok
    assert "empty_diff" in outcome.summary


def test_non_empty_work_unit_with_nothing_staged_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    plan = _plan()

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "no staged work-unit paths matched" in (outcome.evidence or "")


def test_unstaged_path_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    plan = _plan()
    work_unit_path = _write_work_unit_file(tmp_path)
    _git(tmp_path, "add", "--", work_unit_path)
    (tmp_path / "scratch.txt").write_text("left behind\n")

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert not outcome.ok
    assert "scratch.txt" in outcome.paths
    assert "unstaged or untracked paths remain" in (outcome.evidence or "")


def test_recursive_glob_pathspec_accepts_nested_work_unit_paths(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    work_unit = {
        "id": "S1",
        "title": "first",
        "paths": [":(glob)src/**/*.py"],
        "satisfies": ["O1"],
        "state": "in_progress",
    }
    plan = _plan(work_unit)
    nested = tmp_path / "src" / "pkg" / "subpkg"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text("print('O1')\n")
    _git(tmp_path, "add", "--", "src/pkg/subpkg/deep.py")

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert outcome.ok, outcome.evidence
    assert "src/pkg/subpkg/deep.py" in outcome.paths


def test_foreign_staged_path_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    plan = _plan()
    work_unit_path = _write_work_unit_file(tmp_path)
    (tmp_path / "extra.txt").write_text("outside work-unit scope\n")
    _git(tmp_path, "add", "--", work_unit_path, "extra.txt")

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert not outcome.ok
    assert "extra.txt" in outcome.paths
    assert "foreign staged paths" in (outcome.evidence or "")
