"""Canonical gate-decision table (E17 D-DT).

Single source of truth mapping each gate type to its ordered allowed
resolution verbs and a per-verb effect tag. The CLI ``--resolve`` choices, the
``_apply_gate_resolution_effects`` validity checks, and the ``GateDecision``
literal all derive from or are conformance-checked against this table; the
schemas and operator docs are conformance-checked against it (E17 P6).

Add or remove a verb here and nowhere else. ``split_story`` was dropped in
E17 P1 (D-SS): split guidance now travels as an optional note in the
resolution payload and re-enters planning through ``revise_plan``.

The ``tracker_sync_conflict`` row mirrors the tracker layer's
:data:`woof.trackers.base.CONFLICT_DECISIONS`, which stays owned there.

P1 was consolidation only. E17 P2 (D-RA) adds the ``readiness_gate`` row and its
``approve_with_reason`` verb (the E3 unblocker); the retry, abandoned, and deeper
revise-epic-contract effects land in later E17 prompts.
"""

from __future__ import annotations

from woof.graph.transitions import StageStateError
from woof.trackers.base import CONFLICT_DECISIONS

# Per gate type, the ordered allowed verbs mapped to a per-verb effect tag.
# The effect tag names the kind of state change the verb produces so the
# decision-surface conformance test (E17 P6) can assert advertised ==
# implemented. Verbs that share one effect (the tracker-conflict verbs) share
# a tag; otherwise the tag is the verb's canonical effect name.
GATE_DECISIONS: dict[str, dict[str, str]] = {
    "readiness_gate": {
        "approve_with_reason": "approve_with_reason",
        "revise_epic_contract": "revise_epic_contract",
        "abandon_epic": "abandon_epic",
    },
    "plan_gate": {
        "approve": "approve",
        "revise_plan": "revise_plan",
        "revise_epic_contract": "revise_epic_contract",
        "abandon_epic": "abandon_epic",
    },
    "story_gate": {
        "approve": "approve",
        "revise_story_scope": "revise_story_scope",
        "revise_plan": "revise_plan",
        "abandon_story": "abandon_story",
        "abandon_epic": "abandon_epic",
    },
    "review_gate": {
        "approve": "approve",
        "revise_story_scope": "revise_story_scope",
        "revise_plan": "revise_plan",
        "abandon_story": "abandon_story",
        "abandon_epic": "abandon_epic",
    },
    "tracker_sync_conflict": {verb: "tracker_conflict" for verb in CONFLICT_DECISIONS},
}


def allowed_decisions(gate_type: str | None) -> tuple[str, ...]:
    """Return the ordered allowed verbs for ``gate_type`` (``()`` if unknown)."""
    return tuple(GATE_DECISIONS.get(gate_type or "", {}))


def all_decisions() -> tuple[str, ...]:
    """Ordered, de-duplicated union of every verb across all gate types."""
    union: dict[str, None] = {}
    for verbs in GATE_DECISIONS.values():
        for verb in verbs:
            union.setdefault(verb, None)
    return tuple(union)


def validate_decision(gate_type: str | None, decision: str) -> None:
    """Raise :class:`StageStateError` if ``decision`` is invalid for ``gate_type``.

    The error names the valid set for that gate so the operator sees the legal
    verbs.
    """
    allowed = allowed_decisions(gate_type)
    if not allowed:
        raise StageStateError(f"no decision verbs are defined for gate type {gate_type!r}")
    if decision not in allowed:
        raise StageStateError(
            f"{decision} is not valid for {gate_type}; valid: " + ", ".join(allowed)
        )
