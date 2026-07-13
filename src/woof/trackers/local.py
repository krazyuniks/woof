"""Local filesystem issue-tracker adapter.

The local adapter has no external remote: the epic directory in the operator
home is the sole authority for an epic. It lets Woof run against any repository
without a GitHub (or other hosted) issue tracker. Epic IDs are integers assigned
locally; push operations are no-ops because there is no second copy of the
contract to keep in sync, and a sync conflict can never arise.

The adapter takes no repository checkout: it does no git or remote work, so the
project key alone selects everything it touches.
"""

from __future__ import annotations

from typing import Any

import yaml

from woof import state
from woof.graph.state import TERMINAL_WORK_UNIT_STATES, Plan
from woof.state import append_jsonl, atomic_write_text
from woof.trackers.base import (
    CONFLICT_DECISIONS,
    ColdStartResult,
    ConflictResolutionResult,
    DefinitionSyncResult,
    LifecycleSyncResult,
    NewEpicResult,
    TrackerError,
    iso_utc,
)
from woof.trackers.epic_body import (
    render_epic_issue_body,
    seed_from_spark,
    spark_markdown,
    split_epic_front_matter,
)


class LocalTracker:
    """Issue-tracker adapter backed only by the project's durable engine state."""

    kind = "local"

    def __init__(self, project_key: str) -> None:
        self.project_key = project_key

    # -- runtime ----------------------------------------------------------

    def assert_runtime_reachable(self) -> None:
        """The local filesystem is always reachable; nothing to verify."""

    # -- epic lifecycle ---------------------------------------------------

    def create_epic(self, spark: str) -> NewEpicResult:
        title, body = seed_from_spark(spark)
        epic_id = self._next_epic_id()
        epic_dir = state.epic_dir(self.project_key, epic_id)
        if epic_dir.exists():
            raise TrackerError(f"{epic_dir} already exists")
        epic_dir.mkdir(parents=True)

        spark_path = state.spark_path(self.project_key, epic_id)
        spark_path.write_text(spark_markdown(title, body), encoding="utf-8")
        events_path = state.epic_events_path(self.project_key, epic_id)
        append_jsonl(
            events_path,
            {
                "event": "spark_created",
                "at": iso_utc(),
                "epic_id": epic_id,
                "source": "local",
            },
        )

        current_epic_path = state.current_epic_path(self.project_key)
        atomic_write_text(current_epic_path, f"E{epic_id}\n")
        append_jsonl(
            events_path,
            {
                "event": "current_epic_selected",
                "at": iso_utc(),
                "epic_id": epic_id,
            },
        )
        return NewEpicResult(
            epic_id=epic_id,
            epic_dir=epic_dir,
            spark_path=spark_path,
            epic_path=None,
            last_sync_path=state.last_sync_path(self.project_key, epic_id),
            epic_ref=epic_dir.as_posix(),
            current_epic_path=current_epic_path,
        )

    def fetch_epic(self, epic_id: int) -> ColdStartResult:
        raise TrackerError(
            f"E{epic_id} not found. The local tracker has no remote to fetch from; "
            'use `woof wf new "<spark>"` to create a new epic.'
        )

    def assert_epic_authority(self, epic_id: int) -> None:
        epic_dir = state.epic_dir(self.project_key, epic_id)
        if not epic_dir.is_dir():
            raise TrackerError(
                f'E{epic_id} not found. Use `woof wf new "<spark>"` to start a new epic.'
            )

    def has_sync_state(self, epic_id: int) -> bool:
        return state.epic_dir(self.project_key, epic_id).is_dir()

    def push_epic_definition(
        self, epic_id: int, front: dict[str, Any], prose: str
    ) -> DefinitionSyncResult:
        body = render_epic_issue_body(front, prose, remote_body=None)
        return DefinitionSyncResult(
            epic_id=epic_id,
            body=body,
            updated_at=iso_utc(),
            last_sync_path=state.last_sync_path(self.project_key, epic_id),
            changed=False,
        )

    def push_plan_summary(self, epic_id: int) -> LifecycleSyncResult:
        front, prose = self._load_epic_markdown(epic_id)
        plan = self._load_plan(epic_id)
        body = render_epic_issue_body(front, prose, remote_body=None, plan=plan)
        return self._lifecycle_result(epic_id, body=body, closed=False)

    def complete_epic(self, epic_id: int) -> LifecycleSyncResult:
        front, prose = self._load_epic_markdown(epic_id)
        plan = self._load_plan(epic_id)
        if any(unit.state not in TERMINAL_WORK_UNIT_STATES for unit in plan.work_units):
            raise TrackerError(f"E{epic_id} cannot be closed until all plan work units are done")
        body = render_epic_issue_body(
            front,
            prose,
            remote_body=None,
            plan=plan,
            completed=True,
        )
        return self._lifecycle_result(epic_id, body=body, closed=True)

    def close_not_delivered(self, epic_id: int) -> LifecycleSyncResult:
        # No remote to close: abandoning an epic is a local-only terminal marker.
        # Unlike complete_epic there is no all-done guard - the epic is abandoned
        # with work outstanding - and no plan/EPIC.md load, so it works at any
        # stage (including a readiness gate, before plan.json exists).
        return self._lifecycle_result(epic_id, body="", closed=True)

    def resolve_conflict(self, epic_id: int, decision: str) -> ConflictResolutionResult:
        if decision not in CONFLICT_DECISIONS:
            raise TrackerError(f"unsupported tracker_sync_conflict decision: {decision}")
        raise TrackerError(
            "the local tracker has no remote, so a sync conflict cannot occur; "
            f"E{epic_id} has no tracker_sync_conflict gate to resolve"
        )

    # -- helpers ----------------------------------------------------------

    def _lifecycle_result(self, epic_id: int, *, body: str, closed: bool) -> LifecycleSyncResult:
        return LifecycleSyncResult(
            epic_id=epic_id,
            body=body,
            updated_at=iso_utc(),
            last_sync_path=state.last_sync_path(self.project_key, epic_id),
            changed=False,
            closed=closed,
        )

    def _load_epic_markdown(self, epic_id: int) -> tuple[dict[str, Any], str]:
        epic_path = state.epic_contract_path(self.project_key, epic_id)
        try:
            return split_epic_front_matter(epic_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise TrackerError(f"{epic_path} could not be loaded: {exc}") from exc

    def _load_plan(self, epic_id: int) -> Plan:
        plan_path = state.plan_path(self.project_key, epic_id)
        try:
            return Plan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TrackerError(f"{plan_path} could not be loaded: {exc}") from exc

    def _next_epic_id(self) -> int:
        epics_dir = state.epics_root(self.project_key)
        highest = 0
        if epics_dir.is_dir():
            for child in epics_dir.iterdir():
                if not child.is_dir() or not child.name.startswith("E"):
                    continue
                suffix = child.name[1:]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        return highest + 1
