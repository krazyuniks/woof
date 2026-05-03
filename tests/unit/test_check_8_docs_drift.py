"""Tests for check_8_docs_drift - Stage-5 Check 8."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from woof.checks import CheckContext
from woof.checks.runners.check_8_docs_drift import check_8_docs_drift_runner

pytestmark = pytest.mark.host_only


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True)


def _init_repo(repo_root: Path) -> None:
    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")


def _write(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _ctx(repo_root: Path) -> CheckContext:
    return CheckContext(
        epic_id=7,
        story_id="S1",
        repo_root=repo_root,
        epic_dir=repo_root / ".woof" / "epics" / "E7",
        plan={"epic_id": 7, "goal": "docs drift", "stories": []},
        critique=None,
    )


def _write_docs_paths(repo_root: Path, content: str) -> None:
    _write(repo_root / ".woof" / "docs-paths.toml", content)


def test_missing_config_is_noop(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / "src/app.py")
    _git(tmp_path, "add", "--", "src/app.py")

    outcome = check_8_docs_drift_runner(_ctx(tmp_path))

    assert outcome.ok
    assert outcome.severity == "info"
    assert "absent" in outcome.summary
    assert outcome.paths == []


def test_configured_mapping_requires_staged_docs_path(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_docs_paths(
        tmp_path,
        """\
[[mappings]]
code_pattern = "src/**/*.py"
doc_pattern = "docs/**/*.md"
rationale = "public behaviour changed"
""",
    )
    _write(tmp_path / "src/package/service.py")
    _git(tmp_path, "add", "--", ".woof/docs-paths.toml", "src/package/service.py")

    outcome = check_8_docs_drift_runner(_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.paths == ["src/package/service.py"]
    assert "docs drift detected" in outcome.summary
    assert "docs/**/*.md" in (outcome.evidence or "")
    assert "public behaviour changed" in (outcome.evidence or "")


def test_configured_mapping_passes_with_matching_docs_path(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_docs_paths(
        tmp_path,
        """\
[[mappings]]
code_pattern = "src/**/*.py"
doc_pattern = "docs/**/*.md"
""",
    )
    _write(tmp_path / "src/package/service.py")
    _write(tmp_path / "docs/package/service.md")
    _git(
        tmp_path,
        "add",
        "--",
        ".woof/docs-paths.toml",
        "src/package/service.py",
        "docs/package/service.md",
    )

    outcome = check_8_docs_drift_runner(_ctx(tmp_path))

    assert outcome.ok
    assert outcome.severity == "info"
    assert outcome.paths == ["docs/package/service.md", "src/package/service.py"]


def test_unmapped_paths_do_not_require_docs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_docs_paths(
        tmp_path,
        """\
[[mappings]]
code_pattern = "src/api/"
doc_pattern = "docs/api/"
""",
    )
    _write(tmp_path / "src/ui/view.py")
    _git(tmp_path, "add", "--", ".woof/docs-paths.toml", "src/ui/view.py")

    outcome = check_8_docs_drift_runner(_ctx(tmp_path))

    assert outcome.ok
    assert "no staged paths matched" in outcome.summary


def test_docs_only_changes_pass(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_docs_paths(
        tmp_path,
        """\
[[mappings]]
code_pattern = "src/api/"
doc_pattern = "docs/api/"
""",
    )
    _write(tmp_path / "docs/api/runbook.md")
    _git(tmp_path, "add", "--", ".woof/docs-paths.toml", "docs/api/runbook.md")

    outcome = check_8_docs_drift_runner(_ctx(tmp_path))

    assert outcome.ok
    assert "no staged paths matched" in outcome.summary


def test_malformed_config_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_docs_paths(
        tmp_path,
        """\
[[mappings]]
code_pattern = ""
doc_pattern = "docs/api/"
""",
    )

    outcome = check_8_docs_drift_runner(_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.paths == [".woof/docs-paths.toml"]
    assert "malformed docs-paths config" in outcome.summary
