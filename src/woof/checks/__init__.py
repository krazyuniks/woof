"""woof checks package.

CheckContext and CheckOutcome are the shared types for all check runners.
The registry (woof/checks/registry.py) maps check IDs to Check entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckContext:
    """Execution context passed to every check runner.

    ``project_key`` locates engine state in the operator home; ``repo_root``
    locates the delivery checkout. They are independent (ADR-017), so a check
    that reads an engine artefact reads it from ``epic_dir`` or the state root,
    while a check that inspects the delivery diff uses ``repo_root``.

    ``cartography_paths`` are names within the project's cartography directory
    (for example ``TARGET-ARCHITECTURE.md``), not repo-relative paths: cartography
    is engine-consumed derived state and no longer lives in the repo.
    """

    epic_id: int
    work_unit_id: str
    project_key: str
    repo_root: Path
    epic_dir: Path
    plan: dict
    critique: dict | None = None
    cartography_floor: str | None = None
    cartography_paths: list[str] = field(default_factory=list)
    files_txt_slice: list[str] = field(default_factory=list)


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
