"""Typed graph contracts for ADR-001."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


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


# The legal verb set is canonical in woof.graph.decisions.GATE_DECISIONS; this
# literal is the union of that table and is conformance-checked against it in
# tests/unit/test_gate_decisions.py (it is asserted-equal rather than derived to
# avoid a state -> decisions -> transitions -> state import cycle). split_story
# was dropped in E17 P1 (D-SS); approve_with_reason was added for readiness gates
# in E17 P2 (D-RA).
GateDecision = Literal[
    "approve",
    "approve_with_reason",
    "revise_epic_contract",
    "revise_plan",
    "revise_story_scope",
    "abandon_story",
    "abandon_epic",
    "keep_local",
    "accept_remote",
    "hand_merge",
]


class StorySpec(BaseModel):
    id: str
    title: str
    intent: str = ""
    paths: list[str] = Field(default_factory=list)
    satisfies: list[str] = Field(default_factory=list)
    implements_contract_decisions: list[str] = Field(default_factory=list)
    uses_contract_decisions: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    tests: dict = Field(default_factory=dict)
    status: Literal["pending", "in_progress", "done"]
    empty_diff: bool = False


class Plan(BaseModel):
    epic_id: int
    goal: str = ""
    stories: list[StorySpec]


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
