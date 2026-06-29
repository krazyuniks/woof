"""Typed graph contracts for ADR-001."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class NodeType(StrEnum):
    DISCOVERY_RESEARCH = "discovery_research"
    DISCOVERY_THINKING = "discovery_thinking"
    DISCOVERY_IDEATE = "discovery_ideate"
    DISCOVERY_SYNTHESIS = "discovery_synthesis"
    EPIC_DEFINITION = "epic_definition"
    CONTRACT_READINESS = "contract_readiness"
    BREAKDOWN_PLANNING = "breakdown_planning"
    PLAN_CRITIQUE = "plan_critique"
    PLAN_GATE_OPEN = "plan_gate_open"
    PLAN_GATE_RESOLVE = "plan_gate_resolve"
    EXECUTOR_DISPATCH = "executor_dispatch"
    CRITIQUE_DISPATCH = "critique_dispatch"
    REVIEW_DISPOSITION = "review_disposition"
    VERIFICATION = "verification"
    COMMIT = "commit"
    GATE_OPEN = "gate_open"
    GATE_RESOLVE = "gate_resolve"
    HUMAN_REVIEW = "human_review"


class NodeStatus(StrEnum):
    COMPLETED = "completed"
    GATE_OPENED = "gate_opened"
    HALTED = "halted"
    EPIC_COMPLETE = "epic_complete"
    # Terminal outcome for an operator-abandoned epic (E17 P4 / D-AB). Distinct
    # from EPIC_COMPLETE: the epic stopped without delivering and its tracker
    # issue is closed as not delivered, rather than completing successfully.
    EPIC_ABANDONED = "epic_abandoned"


# Story statuses that are terminal: the story will never be dispatched again.
# "done" delivered the story; "abandoned" skipped it at a gate (E17 P4 / D-AB).
# Both let the epic reach a terminal outcome; neither is re-run. Dependency
# satisfaction still keys on "done" alone - depending on an abandoned story
# leaves the dependent unschedulable, which is the honest result of skipping it.
TERMINAL_STORY_STATUSES = ("done", "abandoned")


# The legal verb set is canonical in woof.graph.decisions.GATE_DECISIONS; this
# literal is the union of that table and is conformance-checked against it in
# tests/unit/test_gate_decisions.py (it is asserted-equal rather than derived to
# avoid a state -> decisions -> transitions -> state import cycle). split_story
# was dropped in E17 P1 (D-SS); approve_with_reason was added for readiness gates
# in E17 P2 (D-RA); retry_story was added for crashed/aborted executors in E17 P3 (S3).
GateDecision = Literal[
    "approve",
    "approve_with_reason",
    "retry_story",
    "revise_epic_contract",
    "revise_plan",
    "revise_story_scope",
    "abandon_story",
    "abandon_epic",
    "keep_local",
    "accept_remote",
    "hand_merge",
]


class WorkUnitSpec(BaseModel):
    """Work-unit entity inside the runtime plan aggregate."""

    id: str
    title: str
    summary: str = ""
    bounded_context: str | None = None
    paths: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    deps: list[str] = Field(default_factory=list)
    satisfies: list[str] = Field(default_factory=list)
    implements_contract_decisions: list[str] = Field(default_factory=list)
    uses_contract_decisions: list[str] = Field(default_factory=list)
    tests: dict = Field(default_factory=dict)
    status: Literal["pending", "in_progress", "done", "abandoned"]
    empty_diff: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalise_legacy_story_shape(cls, data: object) -> object:
        """Accept legacy story-shaped plans at the single durable inbound point."""
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if "summary" not in payload and "intent" in payload:
            payload["summary"] = payload.pop("intent")
        if "deps" in payload and "depends_on" in payload:
            raise ValueError("work unit cannot carry both deps and legacy depends_on")
        if "deps" not in payload and "depends_on" in payload:
            payload["deps"] = payload.pop("depends_on")
        return payload

    @property
    def intent(self) -> str:
        return self.summary

    @property
    def depends_on(self) -> list[str]:
        return self.deps


class Plan(BaseModel):
    """Aggregate root for an ordered executable set of work units."""

    epic_id: int
    goal: str = ""
    work_units: list[WorkUnitSpec]

    @model_validator(mode="before")
    @classmethod
    def _normalise_legacy_story_shape(cls, data: object) -> object:
        """Normalise legacy ``stories[]`` into canonical ``work_units[]`` once."""
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if "work_units" in payload and "stories" in payload:
            raise ValueError("plan cannot carry both work_units and legacy stories")
        if "work_units" not in payload and "stories" in payload:
            payload["work_units"] = payload.pop("stories")
        return payload

    @model_validator(mode="after")
    def _validate_work_unit_aggregate(self) -> Plan:
        """Enforce identity and dependency invariants at the runtime boundary."""
        work_unit_ids = [unit.id for unit in self.work_units]
        duplicate_ids = [
            item_id for item_id, count in sorted(Counter(work_unit_ids).items()) if count > 1
        ]
        if duplicate_ids:
            duplicates = ", ".join(
                f"work_unit id {item_id} appears {Counter(work_unit_ids)[item_id]} times"
                for item_id in duplicate_ids
            )
            raise ValueError(duplicates)

        work_unit_id_set = set(work_unit_ids)
        deps_by_id: dict[str, list[str]] = {}
        order = {unit_id: index for index, unit_id in enumerate(work_unit_ids)}
        for unit in self.work_units:
            duplicate_deps = [
                item_id for item_id, count in sorted(Counter(unit.deps).items()) if count > 1
            ]
            if duplicate_deps:
                raise ValueError(
                    f"{unit.id}: deps contains duplicate work unit {', '.join(duplicate_deps)}"
                )

            deps_by_id[unit.id] = list(unit.deps)
            for dep_id in unit.deps:
                if dep_id == unit.id:
                    raise ValueError(f"{unit.id}: deps references itself")
                if dep_id not in work_unit_id_set:
                    raise ValueError(f"{unit.id}: deps references unknown work unit {dep_id}")

        _validate_acyclic_dependencies(deps_by_id)
        for unit in self.work_units:
            for dep_id in unit.deps:
                if order[dep_id] > order[unit.id]:
                    raise ValueError(
                        f"{unit.id}: deps {dep_id} appears after dependent work unit; "
                        "work_units must be topologically sorted"
                    )
        return self


def _validate_acyclic_dependencies(deps_by_id: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(unit_id: str, stack: list[str]) -> None:
        if unit_id in visited:
            return
        if unit_id in visiting:
            start = stack.index(unit_id)
            cycle = stack[start:]
            raise ValueError(f"dependency cycle detected: {' -> '.join(cycle)}")

        visiting.add(unit_id)
        for dep_id in deps_by_id.get(unit_id, []):
            visit(dep_id, [*stack, dep_id])
        visiting.remove(unit_id)
        visited.add(unit_id)

    for unit_id in deps_by_id:
        visit(unit_id, [unit_id])


class NodeInput(BaseModel):
    node_type: NodeType
    epic_id: int
    story_id: str | None = None
    repo_root: Path
    reason: str | None = None
    decision: GateDecision | None = None

    model_config = {"arbitrary_types_allowed": True}


class ValidationSummary(BaseModel):
    ok: bool
    stage: int | None = None
    triggered_by: list[str] = Field(default_factory=list)
    check_count: int = 0
    failed_check_count: int = 0


class NodeOutput(BaseModel):
    node_type: NodeType
    status: NodeStatus
    epic_id: int
    story_id: str | None = None
    next_node: NodeType | None = None
    gate_path: str | None = None
    validation_summary: ValidationSummary | None = None
    triggered_by: list[str] = Field(default_factory=list)
    message: str = ""
    paths: list[str] = Field(default_factory=list)


class TransactionManifest(BaseModel):
    epic_id: int
    story_id: str
    expected_paths: list[str]
    story_paths: list[str]
    required_paths: list[str]
    audit_paths: list[str]


class ManifestVerification(BaseModel):
    ok: bool
    manifest: TransactionManifest
    staged_paths: list[str]
    missing_paths: list[str] = Field(default_factory=list)
    extra_paths: list[str] = Field(default_factory=list)
