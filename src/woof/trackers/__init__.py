"""Issue-tracker abstraction (ADR-003).

Woof keeps the epic-level contract in an external issue tracker. A consumer
declares which tracker under ``.woof/prerequisites.toml`` ``[tracker]``:

    [tracker]
    kind = "github"          # or "local"
    repo = "<owner>/<name>"  # required when kind = "github"

``resolve_tracker`` reads that declaration and returns the matching adapter.
The graph, CLI, and gate code depend on the :class:`Tracker` protocol only.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from woof.trackers.base import (
    CONFLICT_DECISIONS,
    CONFLICT_TRIGGERS,
    ColdStartResult,
    ConflictResolutionResult,
    DefinitionSyncResult,
    LifecycleSyncResult,
    NewEpicResult,
    Tracker,
    TrackerError,
)
from woof.trackers.github import GitHubTracker
from woof.trackers.local import LocalTracker

__all__ = [
    "CONFLICT_DECISIONS",
    "CONFLICT_TRIGGERS",
    "ColdStartResult",
    "ConflictResolutionResult",
    "DefinitionSyncResult",
    "GitHubTracker",
    "LifecycleSyncResult",
    "LocalTracker",
    "NewEpicResult",
    "Tracker",
    "TrackerError",
    "load_tracker_config",
    "resolve_tracker",
]

TRACKER_KINDS = ("github", "local")


def load_tracker_config(repo_root: Path) -> dict[str, Any]:
    """Return the ``[tracker]`` table from ``.woof/prerequisites.toml``."""

    prereq = repo_root / ".woof" / "prerequisites.toml"
    if not prereq.is_file():
        raise TrackerError(f"{prereq} not found; cannot resolve [tracker]")
    try:
        with prereq.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise TrackerError(f"{prereq} is not valid TOML: {exc}") from exc
    tracker = data.get("tracker")
    if not isinstance(tracker, dict):
        raise TrackerError(f"{prereq} missing [tracker]")
    return tracker


def resolve_tracker(repo_root: Path) -> Tracker:
    """Resolve the configured issue-tracker adapter for a consumer checkout."""

    config = load_tracker_config(repo_root)
    kind = config.get("kind")
    if kind == "github":
        repo = config.get("repo")
        if not isinstance(repo, str) or not repo:
            raise TrackerError(
                f"{repo_root / '.woof' / 'prerequisites.toml'} [tracker] with "
                'kind = "github" requires a non-empty repo'
            )
        return GitHubTracker(repo_root, repo)
    if kind == "local":
        return LocalTracker(repo_root)
    raise TrackerError(f"[tracker].kind must be one of {', '.join(TRACKER_KINDS)}; got {kind!r}")
