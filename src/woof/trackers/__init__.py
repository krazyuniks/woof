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

from woof.paths import resolve_project_key
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


def resolve_tracker(project_key: str | None = None) -> Tracker:
    """Resolve the configured issue-tracker adapter for a project.

    No adapter takes a repository checkout: the GitHub adapter names the
    repository in every ``gh`` call, and the local adapter has no remote at all.
    The project key selects both the config and the durable state.
    """

    key = resolve_project_key(project_key)
    config = load_tracker_config(key)
    if config.kind == "github":
        if not config.repo:
            raise TrackerError('[tracker] with kind = "github" requires a non-empty repo')
        return GitHubTracker(key, config.repo)
    if config.kind == "local":
        return LocalTracker(key)
    raise TrackerError(
        f"[tracker].kind must be one of {', '.join(TRACKER_KINDS)}; got {config.kind!r}"
    )
