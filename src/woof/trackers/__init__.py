"""Issue-tracker abstraction (ADR-003).

Woof keeps the epic-level contract in an external issue tracker. The operator
declares which tracker in the project config's ``[tracker]`` section:

    [tracker]
    kind = "github"          # or "local"
    repo = "<owner>/<name>"  # required when kind = "github"

``resolve_tracker`` reads that declaration and returns the matching adapter.
The graph, CLI, and gate code depend on the :class:`Tracker` protocol only.
"""

from __future__ import annotations

from pathlib import Path

from woof.project_config import ProjectConfigError, TrackerConfig, load_project_config
from woof.trackers.base import (
    CONFLICT_DECISIONS,
    CONFLICT_TRIGGERS,
    NON_APPROVING_TRIGGERS,
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
    "NON_APPROVING_TRIGGERS",
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


def load_tracker_config(project_key: str | None = None) -> TrackerConfig:
    """Return the ``[tracker]`` section of the project's config."""

    try:
        return load_project_config(project_key).tracker
    except ProjectConfigError as exc:
        raise TrackerError(str(exc)) from exc


def resolve_tracker(repo_root: Path, project_key: str | None = None) -> Tracker:
    """Resolve the configured issue-tracker adapter for a delivery checkout."""

    config = load_tracker_config(project_key)
    if config.kind == "github":
        if not config.repo:
            raise TrackerError('[tracker] with kind = "github" requires a non-empty repo')
        return GitHubTracker(repo_root, config.repo)
    if config.kind == "local":
        return LocalTracker(repo_root)
    raise TrackerError(
        f"[tracker].kind must be one of {', '.join(TRACKER_KINDS)}; got {config.kind!r}"
    )
