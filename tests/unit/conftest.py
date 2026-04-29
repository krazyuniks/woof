"""Shared fixtures for woof CLI tests.

These tests run on host (not Docker) — woof requires uv and ajv-cli on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"

_GIT_LOCAL_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)


pytestmark = pytest.mark.host_only


@pytest.fixture(autouse=True)
def _clear_git_hook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let tests create nested git repos while running inside Git hooks."""

    for name in _GIT_LOCAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session", autouse=True)
def _require_host_tools() -> None:
    for tool in ("uv", "ajv"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not on PATH; woof tests require host tooling")


@pytest.fixture
def run_woof() -> Callable[..., subprocess.CompletedProcess[str]]:
    """Invoke ``woof <args>`` and return the CompletedProcess."""

    def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(WOOF_BIN), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    return _run
