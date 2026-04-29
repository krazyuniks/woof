"""woof checks package.

CheckContext and CheckOutcome are the shared types for all check runners.
The registry (woof/checks/registry.py) maps check IDs to Check entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckContext:
    """Execution context passed to every check runner."""

    epic_id: int
    story_id: str
    repo_root: Path
    epic_dir: Path
    plan: dict
    critique: dict | None = None


@dataclass
class CheckOutcome:
    """Structured result produced by a single check runner."""

    id: str
    ok: bool
    severity: str | None  # null | "info" | "minor" | "blocker"
    summary: str
    evidence: str | None = None
    paths: list[str] = field(default_factory=list)
    command: str | None = None
    exit_code: int | None = None
