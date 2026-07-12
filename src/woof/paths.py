"""Path helpers for the Woof tool checkout and the operator home (ADR-017).

Two roots, two concerns. ``tool_root()`` locates the Woof checkout that ships
schemas, playbooks, and the language registry. ``woof_home()`` locates the
operator's engine home, which owns all per-project config and durable state. A
driven repository holds neither.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

WOOF_HOME_ENV = "WOOF_HOME"
WOOF_PROJECT_ENV = "WOOF_PROJECT"
DEFAULT_WOOF_HOME = "~/.woof"

# A project key names a file under the operator home, so it must be a safe
# single path segment: no separators, no traversal, no surprises across
# case-insensitive filesystems.
PROJECT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ProjectKeyError(RuntimeError):
    """Raised when no usable project key is available at an entry point."""


def tool_root() -> Path:
    """Return the root of the Woof checkout.

    `WOOF_TOOL_ROOT` supports packaged or vendored layouts. In the source
    checkout, this file lives at `src/woof/paths.py`, so parents[2] is root.
    """

    override = os.environ.get("WOOF_TOOL_ROOT")
    if override:
        return Path(override).resolve()
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "schemas").is_dir():
            return candidate
    return Path(__file__).resolve().parents[2]


def schema_dir() -> Path:
    return tool_root() / "schemas"


def woof_home() -> Path:
    """Return the operator's Woof home; ``WOOF_HOME`` overrides the default."""

    return Path(os.environ.get(WOOF_HOME_ENV) or DEFAULT_WOOF_HOME).expanduser()


def project_config_path(project_key: str) -> Path:
    """Return the one config file for ``project_key``. There is no other."""

    return woof_home() / "config" / "projects" / f"{project_key}.toml"


def project_state_root(project_key: str) -> Path:
    """Return the durable engine state root for ``project_key``."""

    return woof_home() / "state" / "projects" / project_key


def resolve_project_key(explicit: str | None = None) -> str:
    """Resolve the project key: explicit argument, then ``WOOF_PROJECT``.

    Never derived from a checkout directory name. Worktree containers routinely
    hold directories called ``main``, so a directory-derived key collides across
    unrelated projects and would cross their engine state.
    """

    key = explicit if explicit is not None else os.environ.get(WOOF_PROJECT_ENV)
    if not key:
        raise ProjectKeyError(
            "no project key: pass --project <key> or set the "
            f"{WOOF_PROJECT_ENV} environment variable"
        )
    if not PROJECT_KEY_RE.match(key):
        raise ProjectKeyError(
            f"invalid project key {key!r}: use lower-case letters, digits, "
            "dot, underscore, or hyphen (it names a file in the operator home)"
        )
    return key


def repo_root_from_git(start: Path | None = None) -> Path:
    """Return the delivery checkout root by asking git for its top level."""

    cwd = (start or Path.cwd()).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FileNotFoundError(f"could not run git at or above {cwd}: {exc}") from exc
    if proc.returncode != 0 or not proc.stdout.strip():
        raise FileNotFoundError(f"{cwd} is not inside a git checkout")
    return Path(proc.stdout.strip())
