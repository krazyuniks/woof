"""Deterministic Stage-2.5 contract-readiness checks.

Runs after ``EPIC.md`` exists and before ``breakdown_planning``. This module owns
the checks; ``graph.nodes.contract_readiness_node`` owns the artefact write,
schema validation, ``readiness_passed`` event, and ``readiness_gate``.

This is prompt 1 of E2: one structural check (machine-checkable acceptance
signal). The full readiness matrix - non-subjective acceptance prose, contract
concreteness, path/symbol resolution against ``git ls-files``, the forward-created
grammar, decomposition sufficiency, and the non-blocking checker timeout - lands
in prompt 2. The dataclasses and the ``evaluate_readiness`` signature are the
stable seam those later checks extend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Outcomes verified by machine (or partly by machine) must carry a
# machine-checkable acceptance signal; a purely manual outcome is exempt from
# this prompt-1 check.
_MACHINE_VERIFICATIONS = {"automated", "hybrid"}

ACCEPTANCE_SIGNAL_CHECK_ID = "readiness_acceptance_signal"


@dataclass(frozen=True)
class ReadinessFinding:
    """One offending artefact reference within a readiness check."""

    detail: str
    ref: str = ""

    def to_payload(self) -> dict[str, str]:
        payload: dict[str, str] = {"detail": self.detail}
        if self.ref:
            payload["ref"] = self.ref
        return payload


@dataclass(frozen=True)
class ReadinessCheck:
    """Outcome of a single readiness check."""

    id: str
    ok: bool
    severity: str
    summary: str
    findings: list[ReadinessFinding] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "ok": self.ok,
            "severity": self.severity,
            "summary": self.summary,
        }
        if self.findings:
            payload["findings"] = [finding.to_payload() for finding in self.findings]
        return payload


@dataclass(frozen=True)
class ReadinessResult:
    """Aggregate readiness result for an epic contract."""

    epic_id: int
    checks: list[ReadinessCheck]

    @property
    def ok(self) -> bool:
        # A ``warn`` check is a non-blocking performance/timeout finding (prompt
        # 2); it never pulls readiness to false on its own.
        return all(check.ok for check in self.checks if check.severity != "warn")

    def to_payload(self, timestamp: str) -> dict[str, Any]:
        return {
            "epic_id": self.epic_id,
            "ok": self.ok,
            "timestamp": timestamp,
            "checks": [check.to_payload() for check in self.checks],
        }


def evaluate_readiness(repo_root: Path, epic_id: int, epic_path: Path) -> ReadinessResult:
    """Evaluate the Stage-2.5 readiness of an epic contract.

    ``repo_root`` is unused by the prompt-1 check but is part of the stable seam:
    prompt 2's path/symbol resolution against ``git ls-files`` needs it.
    """

    front = _load_epic_front_matter(epic_path)
    checks = [_check_acceptance_signal(front)]
    return ReadinessResult(epic_id=epic_id, checks=checks)


def _check_acceptance_signal(front: dict[str, Any]) -> ReadinessCheck:
    """Every machine-verified outcome must carry a machine-checkable signal.

    A signal is a contract decision that realises the outcome (a
    ``contract_decision`` whose ``related_outcomes`` names it - those carry a
    concrete openapi/pydantic/json-schema ref by construction), or an
    ``acceptance_criteria`` entry that names the outcome id.
    """

    outcomes = front.get("observable_outcomes")
    outcomes = outcomes if isinstance(outcomes, list) else []
    contract_decisions = front.get("contract_decisions")
    contract_decisions = contract_decisions if isinstance(contract_decisions, list) else []
    acceptance_criteria = front.get("acceptance_criteria")
    acceptance_criteria = acceptance_criteria if isinstance(acceptance_criteria, list) else []

    realised_outcome_ids = _outcomes_with_contract_decision(contract_decisions)
    criteria_text = "\n".join(str(item) for item in acceptance_criteria)

    findings: list[ReadinessFinding] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        if outcome.get("deprecated") is True:
            continue
        if outcome.get("verification") not in _MACHINE_VERIFICATIONS:
            continue
        outcome_id = outcome.get("id")
        if not isinstance(outcome_id, str) or not outcome_id:
            continue
        if outcome_id in realised_outcome_ids:
            continue
        if _names_outcome(criteria_text, outcome_id):
            continue
        findings.append(
            ReadinessFinding(
                ref=outcome_id,
                detail=(
                    f"{outcome_id} is verified by machine but has no machine-checkable "
                    "acceptance signal: it is not realised by any contract_decision and "
                    "no acceptance_criteria entry names it"
                ),
            )
        )

    if findings:
        return ReadinessCheck(
            id=ACCEPTANCE_SIGNAL_CHECK_ID,
            ok=False,
            severity="blocker",
            summary=(
                f"{len(findings)} machine-verified outcome(s) lack a machine-checkable "
                "acceptance signal"
            ),
            findings=findings,
        )
    return ReadinessCheck(
        id=ACCEPTANCE_SIGNAL_CHECK_ID,
        ok=True,
        severity="info",
        summary="every machine-verified outcome carries a machine-checkable acceptance signal",
    )


def _outcomes_with_contract_decision(contract_decisions: list[Any]) -> set[str]:
    realised: set[str] = set()
    for decision in contract_decisions:
        if not isinstance(decision, dict):
            continue
        related = decision.get("related_outcomes")
        if not isinstance(related, list):
            continue
        for outcome_id in related:
            if isinstance(outcome_id, str) and outcome_id:
                realised.add(outcome_id)
    return realised


def _names_outcome(text: str, outcome_id: str) -> bool:
    return re.search(rf"\b{re.escape(outcome_id)}\b", text) is not None


def _load_epic_front_matter(epic_path: Path) -> dict[str, Any]:
    text = epic_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    payload = yaml.safe_load(text[4:end]) or {}
    return payload if isinstance(payload, dict) else {}
