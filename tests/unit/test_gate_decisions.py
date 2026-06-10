"""E17 P1 — the canonical gate-decision table is the only verb surface.

These tests lock the consolidation: the CLI ``--resolve`` choices, the
``GateDecision`` literal, and the ``jsonl-events`` decision enum all equal the
union of ``GATE_DECISIONS``; an invalid verb for a gate type is a structured
error naming the valid set; ``split_story`` is gone from every surface; and the
verbs that already had effects still behave exactly as before.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast, get_args

import pytest

from woof.cli.commands.wf import _apply_gate_resolution_effects, setup_wf_parser
from woof.graph.decisions import (
    GATE_DECISIONS,
    all_decisions,
    allowed_decisions,
    validate_decision,
)
from woof.graph.state import GateDecision
from woof.graph.transitions import StageStateError
from woof.trackers.base import CONFLICT_DECISIONS, ConflictResolutionResult, Tracker

pytestmark = pytest.mark.host_only

REPO_ROOT = Path(__file__).resolve().parents[2]

# The surviving verb sets after E17 P1 drops split_story. P1 changes no effect
# behaviour, so these must equal the pre-E17 sets minus split_story.
_SURVIVING_SETS = {
    "plan_gate": {"approve", "revise_plan", "revise_epic_contract", "abandon_epic"},
    "story_gate": {"approve", "revise_story_scope", "revise_plan", "abandon_story", "abandon_epic"},
    "review_gate": {
        "approve",
        "revise_story_scope",
        "revise_plan",
        "abandon_story",
        "abandon_epic",
    },
    "tracker_sync_conflict": set(CONFLICT_DECISIONS),
}


def _resolve_action() -> argparse.Action:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    setup_wf_parser(sub)
    wf = sub.choices["wf"]
    (action,) = [a for a in wf._actions if a.dest == "resolve"]
    return action


def _jsonl_decision_enum() -> list[str]:
    schema = json.loads(
        (REPO_ROOT / "schemas" / "jsonl-events.schema.json").read_text(encoding="utf-8")
    )
    return schema["properties"]["decision"]["enum"]


# --- The decision surface derives from the table -------------------------------


def test_resolve_argparse_choices_derive_from_table() -> None:
    choices = list(_resolve_action().choices or [])
    assert choices == list(all_decisions())
    assert "split_story" not in choices


def test_gate_decision_literal_matches_table_union() -> None:
    assert set(get_args(GateDecision)) == set(all_decisions())


def test_jsonl_decision_enum_matches_table_union() -> None:
    assert set(_jsonl_decision_enum()) == set(all_decisions())


def test_table_allowed_sets_are_the_surviving_sets() -> None:
    assert {gate: set(verbs) for gate, verbs in GATE_DECISIONS.items()} == _SURVIVING_SETS


def test_allowed_decisions_is_ordered_and_empty_for_unknown_gate() -> None:
    assert allowed_decisions("plan_gate") == tuple(GATE_DECISIONS["plan_gate"])
    assert allowed_decisions("not_a_gate") == ()
    assert allowed_decisions(None) == ()


# --- An invalid verb is a structured error naming the valid set ----------------


def test_invalid_verb_per_gate_type_raises_naming_valid_set() -> None:
    for gate_type, verbs in GATE_DECISIONS.items():
        with pytest.raises(StageStateError) as excinfo:
            validate_decision(gate_type, "not_a_real_verb")
        message = str(excinfo.value)
        assert f"not_a_real_verb is not valid for {gate_type}" in message
        for verb in verbs:
            assert verb in message


# --- split_story is gone from every P1 surface ---------------------------------


def test_split_story_rejected_everywhere() -> None:
    assert "split_story" not in all_decisions()
    assert "split_story" not in get_args(GateDecision)
    assert "split_story" not in _jsonl_decision_enum()
    assert all("split_story" not in verbs for verbs in GATE_DECISIONS.values())
    for gate_type in GATE_DECISIONS:
        with pytest.raises(StageStateError, match="split_story is not valid"):
            validate_decision(gate_type, "split_story")


def test_split_story_is_rejected_by_argparse() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    setup_wf_parser(sub)
    with pytest.raises(SystemExit):
        parser.parse_args(["wf", "--resolve", "split_story"])


# --- Surviving verbs behave unchanged ------------------------------------------


def test_surviving_verbs_accepted_by_table() -> None:
    for gate_type, verbs in GATE_DECISIONS.items():
        for verb in verbs:
            validate_decision(gate_type, verb)  # must not raise


def _write_epic(root: Path, epic_id: int, *, story_status: str = "in_progress") -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True)
    plan = {
        "epic_id": epic_id,
        "goal": "test gate decisions",
        "stories": [
            {
                "id": "S1",
                "title": "first",
                "intent": "do work",
                "paths": ["src/*.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "depends_on": [],
                "tests": {"count": 1, "types": ["unit"]},
                "status": story_status,
            }
        ],
    }
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "epic.jsonl").write_text("")
    return directory


class _RecordingTracker:
    """Minimal Tracker stub: records the surviving-verb calls we exercise."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self.pushed: list[int] = []
        self.conflicts: list[tuple[int, str]] = []

    def push_plan_summary(self, epic_id: int) -> None:
        self.pushed.append(epic_id)

    def resolve_conflict(self, epic_id: int, decision: str) -> ConflictResolutionResult:
        self.conflicts.append((epic_id, decision))
        return ConflictResolutionResult(
            epic_id=epic_id,
            decision=decision,
            updated_at="2026-01-01T00:00:00Z",
            last_sync_path=self._directory / ".last-sync",
            epic_path=None,
        )


def test_plan_gate_approve_effect_unchanged(tmp_path: Path) -> None:
    directory = _write_epic(tmp_path, 51)
    tracker = _RecordingTracker(directory)

    changed = _apply_gate_resolution_effects(
        tmp_path,
        51,
        decision="approve",
        gate_type="plan_gate",
        story_id=None,
        triggered_by=["plan_review"],
        tracker=cast(Tracker, tracker),
    )

    assert changed == []
    assert tracker.pushed == [51]


def test_story_gate_revise_story_scope_effect_unchanged(tmp_path: Path) -> None:
    directory = _write_epic(tmp_path, 52)
    (directory / "check-result.json").write_text(json.dumps({"ok": False}))
    tracker = _RecordingTracker(directory)

    changed = _apply_gate_resolution_effects(
        tmp_path,
        52,
        decision="revise_story_scope",
        gate_type="story_gate",
        story_id="S1",
        triggered_by=["check_3_scope"],
        tracker=cast(Tracker, tracker),
    )

    assert not (directory / "check-result.json").exists()
    assert any("check-result.json" in path for path in changed)
    assert tracker.pushed == []  # no tracker interaction for story-scope revision


def test_abandon_story_effect_unchanged(tmp_path: Path) -> None:
    # P1 deliberately leaves abandon_story's pre-E17 behaviour intact: it marks
    # the story "done" and appends story_completed. P4 makes it honest.
    directory = _write_epic(tmp_path, 53)
    tracker = _RecordingTracker(directory)

    _apply_gate_resolution_effects(
        tmp_path,
        53,
        decision="abandon_story",
        gate_type="story_gate",
        story_id="S1",
        triggered_by=["executor_aborted"],
        tracker=cast(Tracker, tracker),
    )

    plan = json.loads((directory / "plan.json").read_text())
    assert plan["stories"][0]["status"] == "done"
    events = [
        json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines() if line
    ]
    completed = [e for e in events if e["event"] == "story_completed"]
    assert completed and completed[-1]["decision"] == "abandon_story"


def test_tracker_conflict_keep_local_validates_through_table(tmp_path: Path) -> None:
    directory = _write_epic(tmp_path, 54)
    tracker = _RecordingTracker(directory)

    changed = _apply_gate_resolution_effects(
        tmp_path,
        54,
        decision="keep_local",
        gate_type="plan_gate",  # conflict gates carry plan_gate type + conflict trigger
        story_id=None,
        triggered_by=["tracker_sync_conflict"],
        tracker=cast(Tracker, tracker),
    )

    assert tracker.conflicts == [(54, "keep_local")]
    assert any(".last-sync" in path for path in changed)


def test_tracker_conflict_rejects_non_conflict_verb_naming_valid_set(tmp_path: Path) -> None:
    directory = _write_epic(tmp_path, 55)
    tracker = _RecordingTracker(directory)

    with pytest.raises(StageStateError) as excinfo:
        _apply_gate_resolution_effects(
            tmp_path,
            55,
            decision="approve",
            gate_type="plan_gate",
            story_id=None,
            triggered_by=["tracker_sync_conflict"],
            tracker=cast(Tracker, tracker),
        )
    message = str(excinfo.value)
    assert "approve is not valid for tracker_sync_conflict" in message
    for verb in CONFLICT_DECISIONS:
        assert verb in message
