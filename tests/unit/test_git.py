"""Tests for git helpers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from woof.graph.git import head_sha


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "test@woof.dev"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "f").write_text("x\n")
    subprocess.run(["git", "add", "f"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_head_sha_returns_full_40_char_sha(git_repo: Path) -> None:
    sha = head_sha(git_repo)
    assert sha is not None
    assert re.fullmatch(r"[0-9a-f]{40}", sha), f"expected 40-char SHA, got: {sha!r}"


def test_head_sha_unaffected_by_low_core_abbrev(git_repo: Path) -> None:
    subprocess.run(
        ["git", "config", "core.abbrev", "4"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    sha = head_sha(git_repo)
    assert sha is not None
    assert re.fullmatch(r"[0-9a-f]{40}", sha), (
        f"expected 40-char SHA with core.abbrev=4, got: {sha!r}"
    )


def test_head_sha_none_on_empty_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    assert head_sha(tmp_path) is None
