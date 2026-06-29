"""Issue-tracker abstraction — protocol, errors, result records, helpers.

Woof keeps the epic-level contract in an external issue tracker and the
per-epic runtime under ``.woof/epics/E<N>/``. ADR-003 records the boundary.
A :class:`Tracker` adapter owns every interaction with that external system;
the graph, CLI, and gate code depend on this protocol, not on a provider.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class TrackerError(RuntimeError):
    """An issue-tracker operation failed and must not be silently ignored."""


# Gate triggers and resolution decisions for tracker sync conflicts. The
# legacy ``github_sync_conflict`` spelling is retained so gate.md files and
# epic.jsonl logs written before RC-B2 still resolve and validate.
CONFLICT_TRIGGERS = ("tracker_sync_conflict", "github_sync_conflict")
CONFLICT_DECISIONS = ("keep_local", "accept_remote", "hand_merge")

# Triggers that mark a gate resolution as non-approving at every gate type.
# A gate_resolved event carrying one of these triggers must never be counted
# as a genuine plan/work-unit/readiness/review approval, regardless of decision.
NON_APPROVING_TRIGGERS = ("incomplete_stage_state",)


@dataclass(frozen=True)
class ColdStartResult:
    epic_id: int
    epic_dir: Path
    spark_path: Path
    epic_path: Path | None
    last_sync_path: Path


@dataclass(frozen=True)
class NewEpicResult(ColdStartResult):
    epic_ref: str
    current_epic_path: Path


@dataclass(frozen=True)
class DefinitionSyncResult:
    epic_id: int
    body: str
    updated_at: str
    last_sync_path: Path
    changed: bool


@dataclass(frozen=True)
class LifecycleSyncResult(DefinitionSyncResult):
    closed: bool = False


@dataclass(frozen=True)
class ConflictResolutionResult:
    epic_id: int
    decision: str
    updated_at: str
    last_sync_path: Path
    epic_path: Path | None = None


@runtime_checkable
class Tracker(Protocol):
    """The issue-tracker contract every adapter implements (ADR-003).

    Conflict detection is intrinsic to the push operations: a push that finds
    the tracker has diverged from ``.last-sync`` writes a conflict gate and
    raises :class:`TrackerError`. ``resolve_conflict`` applies the structured
    operator decision. A tracker with no mutable remote (``local``) never
    detects a conflict.
    """

    kind: str

    def assert_runtime_reachable(self) -> None:
        """Fail loud when a workflow invocation cannot reach the tracker."""
        ...

    def create_epic(self, spark: str) -> NewEpicResult:
        """Create a new epic from a spark and initialise local state."""
        ...

    def fetch_epic(self, epic_id: int) -> ColdStartResult:
        """Cold-start an existing epic's local directory from the tracker."""
        ...

    def assert_epic_authority(self, epic_id: int) -> None:
        """Verify an existing local epic is still backed by the tracker."""
        ...

    def has_sync_state(self, epic_id: int) -> bool:
        """Return whether the epic has established tracker sync state."""
        ...

    def push_epic_definition(
        self, epic_id: int, front: dict[str, Any], prose: str
    ) -> DefinitionSyncResult:
        """Push the rendered Definition-stage body to the tracker."""
        ...

    def push_plan_summary(self, epic_id: int) -> LifecycleSyncResult:
        """Push the plan-summary body to the tracker."""
        ...

    def complete_epic(self, epic_id: int) -> LifecycleSyncResult:
        """Push the closing summary and mark the epic complete."""
        ...

    def close_not_delivered(self, epic_id: int) -> LifecycleSyncResult:
        """Close the tracker issue as abandoned/not delivered (E17 P4 / D-AB).

        Distinct from :meth:`complete_epic`: this is the terminal side of the
        ``abandon_epic`` gate verb. It does not require the plan to be done -
        the epic is being abandoned with work outstanding - and marks the issue
        as not delivered rather than completed.
        """
        ...

    def resolve_conflict(self, epic_id: int, decision: str) -> ConflictResolutionResult:
        """Apply the structured resolution for an open sync-conflict gate."""
        ...


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def epic_directory(repo_root: Path, epic_id: int) -> Path:
    return repo_root / ".woof" / "epics" / f"E{epic_id}"


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_last_sync(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TrackerError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TrackerError(f"{path} must contain a JSON object")
    return payload


def write_last_sync(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".last-sync.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def last_sync_text(last_sync: dict[str, Any], field: str) -> str:
    value = last_sync.get(field)
    return value if isinstance(value, str) else ""


def last_sync_body(last_sync: dict[str, Any]) -> str:
    return last_sync_text(last_sync, "body")
