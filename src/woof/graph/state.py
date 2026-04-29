"""Typed graph contracts for ADR-001."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class NodeType(StrEnum):
    EXECUTOR_DISPATCH = "executor_dispatch"
    CRITIQUE_DISPATCH = "critique_dispatch"
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


GateDecision = Literal[
    "approve",
    "revise_epic_contract",
    "revise_plan",
    "revise_story_scope",
    "split_story",
    "abandon_story",
    "abandon_epic",
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


class NodeOutput(BaseModel):
    node_type: NodeType
    status: NodeStatus
    epic_id: int
    story_id: str | None = None
    next_node: NodeType | None = None
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
