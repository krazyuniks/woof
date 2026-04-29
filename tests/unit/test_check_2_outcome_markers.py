"""Tests for check_2_outcome_markers — Stage-5 Check 2."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.host_only


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / ".woof").mkdir()


def _write_marker_config(
    root: Path,
    marker_regex: str = r"(?<![A-Za-z0-9])O\d+(?![A-Za-z0-9])",
) -> None:
    (root / ".woof" / "test-markers.toml").write_text(
        f"""\
[languages.python]
test_paths = ["tests/"]
marker_regex = '{marker_regex}'
docstring_keyword = "outcomes:"
comment_prefix = "#"
context_lines = 3
"""
    )


def _stage(root: Path, rel_path: str, content: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(["git", "add", "--", rel_path], cwd=root, check=True)


def _make_ctx(root: Path, satisfies: list[Any], story_id: str = "S1") -> object:
    from woof.checks import CheckContext

    return CheckContext(
        epic_id=1,
        story_id=story_id,
        repo_root=root,
        epic_dir=root / ".woof" / "epics" / "E1",
        plan={
            "epic_id": 1,
            "goal": "test",
            "stories": [
                {
                    "id": "S1",
                    "title": "story",
                    "intent": "test markers",
                    "paths": ["tests/"],
                    "satisfies": satisfies,
                    "implements_contract_decisions": [],
                    "uses_contract_decisions": [],
                    "depends_on": [],
                    "tests": {"count": 1, "types": ["unit"]},
                    "status": "in_progress",
                }
            ],
        },
        critique=None,
    )


def test_outcome_markers_present_in_staged_test_diff(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_2_outcome_markers import check_2_outcome_markers_runner

    _init_repo(tmp_path)
    _write_marker_config(tmp_path)
    _stage(
        tmp_path,
        "tests/test_publish.py",
        """\
def test_publish_comment_O1():
    assert True


# outcomes: [O2]
def test_unauthenticated_comment_rejected():
    assert True
""",
    )

    outcome = check_2_outcome_markers_runner(_make_ctx(tmp_path, ["O1", "O2"]))

    assert outcome.ok
    assert outcome.id == "check_2_outcome_markers"
    assert outcome.severity == "info"
    assert outcome.paths == ["tests/test_publish.py"]
    assert "O1" in (outcome.evidence or "")
    assert "O2" in (outcome.evidence or "")


def test_missing_outcome_marker_fails(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_2_outcome_markers import check_2_outcome_markers_runner

    _init_repo(tmp_path)
    _write_marker_config(tmp_path)
    _stage(
        tmp_path,
        "tests/test_publish.py",
        """\
def test_publish_comment_O1():
    assert True
""",
    )

    outcome = check_2_outcome_markers_runner(_make_ctx(tmp_path, ["O1", "O2"]))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "O2" in outcome.summary
    assert "O1" in (outcome.evidence or "")


def test_markers_in_non_test_staged_files_do_not_count(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_2_outcome_markers import check_2_outcome_markers_runner

    _init_repo(tmp_path)
    _write_marker_config(tmp_path)
    _stage(
        tmp_path,
        "src/app.py",
        """\
# O1
def publish_comment():
    return True
""",
    )

    outcome = check_2_outcome_markers_runner(_make_ctx(tmp_path, ["O1"]))

    assert not outcome.ok
    assert "no staged test files matched" in (outcome.evidence or "")


def test_malformed_marker_regex_fails(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_2_outcome_markers import check_2_outcome_markers_runner

    _init_repo(tmp_path)
    _write_marker_config(tmp_path, marker_regex="[")
    _stage(
        tmp_path,
        "tests/test_publish.py",
        """\
def test_publish_comment_O1():
    assert True
""",
    )

    outcome = check_2_outcome_markers_runner(_make_ctx(tmp_path, ["O1"]))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "marker_regex is invalid" in outcome.summary
    assert outcome.paths == [".woof/test-markers.toml"]


def test_malformed_satisfies_fails(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_2_outcome_markers import check_2_outcome_markers_runner

    _init_repo(tmp_path)
    _write_marker_config(tmp_path)
    _stage(
        tmp_path,
        "tests/test_publish.py",
        """\
def test_publish_comment_O1():
    assert True
""",
    )

    outcome = check_2_outcome_markers_runner(_make_ctx(tmp_path, ["O1", 2]))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "malformed satisfies" in outcome.summary
