"""Tests for Woof-managed git hook installation."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from woof.cli.hooks import HOOK_BEGIN, install_woof_hooks


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)


def _post_commit_hook(repo: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks/post-commit"],
        check=True,
        capture_output=True,
        text=True,
    )
    raw = Path(proc.stdout.strip())
    return raw if raw.is_absolute() else repo / raw


def test_hook_install_creates_post_commit_block(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    result = install_woof_hooks(tmp_path)

    hook_path = _post_commit_hook(tmp_path)
    assert result.changed is True
    assert result.hook_path == hook_path
    assert hook_path.read_text() == (
        "#!/usr/bin/env sh\n\n"
        "# >>> woof-cartography\n"
        "[ -x ./scripts/refresh-cartography ] && ./scripts/refresh-cartography\n"
        "# <<< woof-cartography\n"
    )
    assert hook_path.stat().st_mode & stat.S_IXUSR


def test_hook_install_preserves_user_content_and_is_idempotent(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    hook_path = _post_commit_hook(tmp_path)
    hook_path.write_text("#!/usr/bin/env sh\necho before\n")

    first = install_woof_hooks(tmp_path)
    installed = hook_path.read_text()
    second = install_woof_hooks(tmp_path)

    assert first.changed is True
    assert second.changed is False
    assert hook_path.read_text() == installed
    assert installed.startswith("#!/usr/bin/env sh\necho before\n")
    assert installed.count(HOOK_BEGIN) == 1


def test_hook_install_preserves_hook_without_trailing_newline(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    hook_path = _post_commit_hook(tmp_path)
    hook_path.write_text("#!/usr/bin/env sh\necho before")

    install_woof_hooks(tmp_path)

    assert hook_path.read_text().startswith("#!/usr/bin/env sh\necho before\n\n")


def test_hook_install_replaces_existing_managed_block(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    hook_path = _post_commit_hook(tmp_path)
    hook_path.write_text(
        "#!/usr/bin/env sh\n"
        "echo before\n"
        "# >>> woof-cartography\n"
        "echo stale\n"
        "# <<< woof-cartography\n"
        "echo after\n"
    )

    install_woof_hooks(tmp_path)
    installed = hook_path.read_text()

    assert "echo before\n" in installed
    assert "echo after\n" in installed
    assert "echo stale" not in installed
    assert "[ -x ./scripts/refresh-cartography ] && ./scripts/refresh-cartography" in installed
    assert installed.count(HOOK_BEGIN) == 1


def test_hooks_install_cli_reports_already_installed(tmp_path: Path, run_woof) -> None:
    _init_repo(tmp_path)
    env = os.environ.copy()

    first = run_woof("hooks", "install", "--project-root", str(tmp_path), env=env)
    second = run_woof("hooks", "install", "--project-root", str(tmp_path), env=env)

    assert first.returncode == 0, first.stderr
    assert "installed:" in first.stdout
    assert second.returncode == 0, second.stderr
    assert "already installed:" in second.stdout
    assert _post_commit_hook(tmp_path).read_text().count(HOOK_BEGIN) == 1
