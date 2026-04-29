"""Shared path helpers for the Woof tool and consumer projects."""

from __future__ import annotations

import os
from pathlib import Path


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


def find_project_root(start: Path | None = None) -> Path:
    """Walk upward to the first directory containing `.woof/`."""

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".woof").is_dir():
            return candidate
    raise FileNotFoundError(f"no .woof/ directory found at or above {current}")
