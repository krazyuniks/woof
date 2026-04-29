"""Tests for check_3_scope — Stage-5 Check 3."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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


def _ctx(root: Path, paths: list[str], story_id: str = "S1") -> CheckContext:
    plan = {
        "epic_id": 7,
        "goal": "test scope",
        "stories": [
            {
                "id": "S1",
                "title": "Scoped story",
                "intent": "Touch allowed files only",
                "paths": paths,
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "depends_on": [],
                "tests": {"count": 1, "types": ["unit"]},
                "status": "in_progress",
            }
        ],
    }
    return CheckContext(
        epic_id=7,
        story_id=story_id,
        repo_root=root,
        epic_dir=root / ".woof" / "epics" / "E7",
        plan=plan,
        critique=None,
    )


def test_allowed_story_paths_and_durable_woof_paths_pass(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path / "src/app.py")
    _write(tmp_path / "tests/test_app.py")
    _write(tmp_path / ".woof/epics/E7/plan.json", json.dumps({"ok": True}))
    _write(tmp_path / ".woof/epics/E7/epic.jsonl", "{}\n")
    _write(tmp_path / ".woof/epics/E7/dispatch.jsonl", "{}\n")
    _write(tmp_path / ".woof/epics/E7/critique/story-S1.md", "---\nseverity: info\n---\n")
    _write(tmp_path / ".woof/epics/E7/audit/codex-story.output")
    _git_add(
        tmp_path,
        "src/app.py",
        "tests/test_app.py",
        ".woof/epics/E7/plan.json",
        ".woof/epics/E7/epic.jsonl",
        ".woof/epics/E7/dispatch.jsonl",
        ".woof/epics/E7/critique/story-S1.md",
        ".woof/epics/E7/audit/codex-story.output",
    )

    outcome = check_3_scope_runner(_ctx(tmp_path, ["src/", "tests/test_app.py"]))

    assert outcome.ok
    assert outcome.severity is None
    assert outcome.paths == [
        ".woof/epics/E7/audit/codex-story.output",
        ".woof/epics/E7/critique/story-S1.md",
        ".woof/epics/E7/dispatch.jsonl",
        ".woof/epics/E7/epic.jsonl",
        ".woof/epics/E7/plan.json",
        "src/app.py",
        "tests/test_app.py",
    ]


def test_forbidden_story_and_woof_paths_fail(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path / "src/app.py")
    _write(tmp_path / "docs/notes.md")
    _write(tmp_path / ".woof/epics/E7/executor_result.json", "{}\n")
    _write(tmp_path / ".woof/epics/E8/plan.json", "{}\n")
    _write(tmp_path / ".woof/epics/E7/audit/raw/full-output.txt")
    _git_add(
        tmp_path,
        "src/app.py",
        "docs/notes.md",
        ".woof/epics/E7/executor_result.json",
        ".woof/epics/E8/plan.json",
        ".woof/epics/E7/audit/raw/full-output.txt",
    )

    outcome = check_3_scope_runner(_ctx(tmp_path, ["src/"]))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.paths == [
        ".woof/epics/E7/audit/raw/full-output.txt",
        ".woof/epics/E7/executor_result.json",
        ".woof/epics/E8/plan.json",
        "docs/notes.md",
    ]
    assert "outside story S1 scope" in outcome.summary


def test_deleted_file_is_checked_against_story_scope(tmp_path: Path) -> None:
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


def test_missing_story_fails_with_structured_outcome(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    outcome = check_3_scope_runner(_ctx(tmp_path, ["src/"], story_id="S9"))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.paths == []
    assert "not found" in outcome.summary
