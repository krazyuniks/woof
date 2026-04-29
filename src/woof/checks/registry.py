"""woof checker registry — single source of truth for Stage-5 checks.

REGISTRY maps check ID → Check. Skills, schemas, and documentation reference
checks by ID only; they never enumerate or describe the registry's contents.

In S1 only check_6_critique_blocker has a real runner. The other eight
entries have placeholder runners that raise NotImplementedError; they will be
populated in S2 and S3.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from woof.checks import CheckContext, CheckOutcome
from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner


class Check(BaseModel):
    """Registry entry for one Stage-N boundary check."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    stage: int
    cost: Literal["cheap", "expensive"]
    summary: str
    runner: Callable[[CheckContext], CheckOutcome]


def _placeholder(check_id: str) -> Callable[[CheckContext], CheckOutcome]:
    """Return a runner that raises NotImplementedError (populated in later stories)."""

    def runner(ctx: CheckContext) -> CheckOutcome:
        raise NotImplementedError(f"{check_id}: runner not yet implemented")

    runner.__name__ = f"{check_id}_runner"
    return runner


REGISTRY: dict[str, Check] = {
    "check_1_quality_gates": Check(
        id="check_1_quality_gates",
        stage=5,
        cost="expensive",
        summary="Run each command in .woof/quality-gates.toml; each must exit 0",
        runner=_placeholder("check_1_quality_gates"),
    ),
    "check_2_outcome_markers": Check(
        id="check_2_outcome_markers",
        stage=5,
        cost="cheap",
        summary="For each O<n> in story.satisfies[], regex-grep the staged test diff; ≥1 hit each",
        runner=_placeholder("check_2_outcome_markers"),
    ),
    "check_3_scope": Check(
        id="check_3_scope",
        stage=5,
        cost="cheap",
        summary="Staged file set ⊆ story.paths[] pathspec plus allowed .woof/ files",
        runner=_placeholder("check_3_scope"),
    ),
    "check_4_contract_refs": Check(
        id="check_4_contract_refs",
        stage=5,
        cost="expensive",
        summary="woof check-cd verifies every CD's contract reference resolves",
        runner=_placeholder("check_4_contract_refs"),
    ),
    "check_5_plan_crossrefs": Check(
        id="check_5_plan_crossrefs",
        stage=5,
        cost="cheap",
        summary="plan.json schema-valid; cross-refs intact; deps acyclic",
        runner=_placeholder("check_5_plan_crossrefs"),
    ),
    "check_6_critique_blocker": Check(
        id="check_6_critique_blocker",
        stage=5,
        cost="cheap",
        summary="critique/story-S<k>.md exists; schema-valid; severity != blocker",
        runner=check_6_critique_blocker_runner,
    ),
    "check_7_commit_transaction": Check(
        id="check_7_commit_transaction",
        stage=5,
        cost="cheap",
        summary="Staged set contains the four .woof durable files; no foreign .woof/ paths",
        runner=_placeholder("check_7_commit_transaction"),
    ),
    "check_8_docs_drift": Check(
        id="check_8_docs_drift",
        stage=5,
        cost="cheap",
        summary="Per .woof/docs-paths.toml mappings; no-op when file absent",
        runner=_placeholder("check_8_docs_drift"),
    ),
    "check_9_review_valve": Check(
        id="check_9_review_valve",
        stage=5,
        cost="cheap",
        summary="Every-N stories and end-of-epic; surfaces accumulated minor critique findings",
        runner=_placeholder("check_9_review_valve"),
    ),
}

STAGE_5_CHECK_IDS: list[str] = [
    "check_1_quality_gates",
    "check_2_outcome_markers",
    "check_3_scope",
    "check_4_contract_refs",
    "check_5_plan_crossrefs",
    "check_6_critique_blocker",
    "check_7_commit_transaction",
    "check_8_docs_drift",
    "check_9_review_valve",
]
