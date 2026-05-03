"""woof checker registry — single source of truth for Stage-5 checks.

REGISTRY maps check ID → Check. Skills, schemas, and documentation reference
checks by ID only; they never enumerate or describe the registry's contents.

All Stage-5 checks have real runners.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from woof.checks import CheckContext, CheckOutcome
from woof.checks.runners.check_1_quality_gates import check_1_quality_gates_runner
from woof.checks.runners.check_2_outcome_markers import check_2_outcome_markers_runner
from woof.checks.runners.check_3_scope import check_3_scope_runner
from woof.checks.runners.check_4_contract_refs import check_4_contract_refs_runner
from woof.checks.runners.check_5_plan_crossrefs import check_5_plan_crossrefs_runner
from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner
from woof.checks.runners.check_7_commit_transaction import check_7_commit_transaction_runner
from woof.checks.runners.check_8_docs_drift import check_8_docs_drift_runner
from woof.checks.runners.check_9_review_valve import check_9_review_valve_runner


class Check(BaseModel):
    """Registry entry for one Stage-N boundary check."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    stage: int
    cost: Literal["cheap", "expensive"]
    summary: str
    runner: Callable[[CheckContext], CheckOutcome]


REGISTRY: dict[str, Check] = {
    "check_1_quality_gates": Check(
        id="check_1_quality_gates",
        stage=5,
        cost="expensive",
        summary="Run each command in .woof/quality-gates.toml; each must exit 0",
        runner=check_1_quality_gates_runner,
    ),
    "check_2_outcome_markers": Check(
        id="check_2_outcome_markers",
        stage=5,
        cost="cheap",
        summary="For each O<n> in story.satisfies[], regex-grep the staged test diff; ≥1 hit each",
        runner=check_2_outcome_markers_runner,
    ),
    "check_3_scope": Check(
        id="check_3_scope",
        stage=5,
        cost="cheap",
        summary="Staged file set ⊆ story.paths[] pathspec plus allowed .woof/ files",
        runner=check_3_scope_runner,
    ),
    "check_4_contract_refs": Check(
        id="check_4_contract_refs",
        stage=5,
        cost="expensive",
        summary="woof check-cd verifies every CD's contract reference resolves",
        runner=check_4_contract_refs_runner,
    ),
    "check_5_plan_crossrefs": Check(
        id="check_5_plan_crossrefs",
        stage=5,
        cost="cheap",
        summary="plan.json schema-valid; cross-refs intact; deps acyclic",
        runner=check_5_plan_crossrefs_runner,
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
        runner=check_7_commit_transaction_runner,
    ),
    "check_8_docs_drift": Check(
        id="check_8_docs_drift",
        stage=5,
        cost="cheap",
        summary="Per .woof/docs-paths.toml mappings; no-op when file absent",
        runner=check_8_docs_drift_runner,
    ),
    "check_9_review_valve": Check(
        id="check_9_review_valve",
        stage=5,
        cost="cheap",
        summary="Every-N stories and end-of-epic; surfaces accumulated minor critique findings",
        runner=check_9_review_valve_runner,
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
