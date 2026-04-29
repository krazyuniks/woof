"""Tests for check_7_commit_transaction - Stage-5 Check 7."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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


def _plan(story: dict | None = None) -> dict:
    return {
        "epic_id": 7,
        "goal": "test commit transaction",
        "stories": [
            story
            or {
                "id": "S1",
                "title": "first",
                "paths": ["src/*.py"],
                "satisfies": ["O1"],
                "status": "in_progress",
            }
        ],
    }


def _ctx(repo_root: Path, plan: dict) -> CheckContext:
    return CheckContext(
        epic_id=7,
        story_id="S1",
        repo_root=repo_root,
        epic_dir=repo_root / ".woof" / "epics" / "E7",
        plan=plan,
        critique=None,
    )


def _write_required(repo_root: Path, plan: dict) -> list[str]:
    epic_dir = repo_root / ".woof" / "epics" / "E7"
    critique_dir = epic_dir / "critique"
    critique_dir.mkdir(parents=True)
    (epic_dir / "plan.json").write_text(json.dumps(plan))
    (epic_dir / "epic.jsonl").write_text("{}\n")
    (epic_dir / "dispatch.jsonl").write_text("{}\n")
    (critique_dir / "story-S1.md").write_text("---\nseverity: info\n---\n")
    return [
        ".woof/epics/E7/plan.json",
        ".woof/epics/E7/epic.jsonl",
        ".woof/epics/E7/dispatch.jsonl",
        ".woof/epics/E7/critique/story-S1.md",
    ]


def _write_story_file(repo_root: Path) -> str:
    src = repo_root / "src"
    src.mkdir()
    (src / "app.py").write_text("print('O1')\n")
    return "src/app.py"


def test_clean_commit_transaction_passes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    plan = _plan()
    required = _write_required(tmp_path, plan)
    story_path = _write_story_file(tmp_path)
    _git(tmp_path, "add", "--", *required, story_path)

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert outcome.ok
    assert outcome.id == "check_7_commit_transaction"
    assert story_path in outcome.paths


def test_empty_gated_commit_transaction_allows_no_story_paths(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    story = {
        "id": "S1",
        "title": "already realised",
        "paths": ["src/*.py"],
        "satisfies": ["O1"],
        "status": "done",
        "empty_diff": True,
    }
    plan = _plan(story)
    required = _write_required(tmp_path, plan)
    _git(tmp_path, "add", "--", *required)

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert outcome.ok
    assert "empty_diff" in outcome.summary


def test_missing_required_durable_file_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    plan = _plan()
    required = _write_required(tmp_path, plan)
    story_path = _write_story_file(tmp_path)
    staged = [path for path in required if not path.endswith("dispatch.jsonl")]
    _git(tmp_path, "add", "--", *staged, story_path)

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert ".woof/epics/E7/dispatch.jsonl" in outcome.paths
    assert "missing required staged paths" in (outcome.evidence or "")


def test_unstaged_path_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    plan = _plan()
    required = _write_required(tmp_path, plan)
    story_path = _write_story_file(tmp_path)
    _git(tmp_path, "add", "--", *required, story_path)
    (tmp_path / "scratch.txt").write_text("left behind\n")

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert not outcome.ok
    assert "scratch.txt" in outcome.paths
    assert "unstaged or untracked paths remain" in (outcome.evidence or "")


def test_foreign_staged_path_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    plan = _plan()
    required = _write_required(tmp_path, plan)
    story_path = _write_story_file(tmp_path)
    (tmp_path / "extra.txt").write_text("outside story scope\n")
    _git(tmp_path, "add", "--", *required, story_path, "extra.txt")

    outcome = check_7_commit_transaction_runner(_ctx(tmp_path, plan))

    assert not outcome.ok
    assert "extra.txt" in outcome.paths
    assert "foreign staged paths" in (outcome.evidence or "")
