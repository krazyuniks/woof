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

from woof.cli.commands.wf import _apply_gate_resolution_effects, _resolve_gate, setup_wf_parser
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

# The canonical verb sets per gate type. E17 P1 dropped split_story; E17 P2 added
# the readiness_gate row and its approve_with_reason verb (D-RA); E17 P3 added
# retry_story to the story/review rows (S3).
_SURVIVING_SETS = {
    "readiness_gate": {"approve_with_reason", "revise_epic_contract", "abandon_epic"},
    "plan_gate": {"approve", "revise_plan", "revise_epic_contract", "abandon_epic"},
    "story_gate": {
        "approve",
        "retry_story",
        "revise_story_scope",
        "revise_plan",
        "abandon_story",
        "abandon_epic",
    },
    "review_gate": {
        "approve",
        "retry_story",
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


def _node_input_decision_enum() -> list[str]:
    schema = json.loads(
        (REPO_ROOT / "schemas" / "node-input.schema.json").read_text(encoding="utf-8")
    )
    (string_branch,) = [
        branch for branch in schema["properties"]["decision"]["oneOf"] if "enum" in branch
    ]
    return string_branch["enum"]


# --- The decision surface derives from the table -------------------------------


def test_resolve_argparse_choices_derive_from_table() -> None:
    choices = list(_resolve_action().choices or [])
    assert choices == list(all_decisions())
    assert "split_story" not in choices


def test_gate_decision_literal_matches_table_union() -> None:
    assert set(get_args(GateDecision)) == set(all_decisions())


def test_jsonl_decision_enum_matches_table_union() -> None:
    assert set(_jsonl_decision_enum()) == set(all_decisions())


def test_node_input_decision_enum_matches_table_union() -> None:
    # The published node-input I/O contract must carry every verb the typed
    # GateDecision literal does, or a NodeInput.decision using a new verb fails
    # `woof validate --schema node-input`.
    assert set(_node_input_decision_enum()) == set(all_decisions())


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


# --- Readiness-gate verbs (E17 P2 / D-RA) --------------------------------------


def test_readiness_gate_allows_its_three_verbs() -> None:
    assert set(allowed_decisions("readiness_gate")) == {
        "approve_with_reason",
        "revise_epic_contract",
        "abandon_epic",
    }
    for verb in ("approve_with_reason", "revise_epic_contract", "abandon_epic"):
        validate_decision("readiness_gate", verb)  # must not raise


def test_approve_with_reason_is_readiness_only() -> None:
    # approve_with_reason is in the table union (so the CLI/literal/enum carry it)
    # but is valid for no gate type other than readiness_gate.
    assert "approve_with_reason" in all_decisions()
    for gate_type in GATE_DECISIONS:
        if gate_type == "readiness_gate":
            continue
        with pytest.raises(StageStateError, match="approve_with_reason is not valid"):
            validate_decision(gate_type, "approve_with_reason")


def test_invalid_readiness_verb_names_valid_set() -> None:
    # `approve` is a real verb for the plan/story/review gates but not readiness.
    with pytest.raises(StageStateError) as excinfo:
        validate_decision("readiness_gate", "approve")
    message = str(excinfo.value)
    assert "approve is not valid for readiness_gate" in message
    for verb in ("approve_with_reason", "revise_epic_contract", "abandon_epic"):
        assert verb in message


def _write_epic(root: Path, epic_id: int, *, story_status: str = "in_progress") -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True)
    plan = {
        "epic_id": epic_id,
        "goal": "test gate decisions",
        "work_units": [
            {
                "id": "S1",
                "title": "first",
                "summary": "do work",
                "paths": ["src/*.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "deps": [],
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


def test_abandon_story_marks_abandoned_and_records_story_abandoned(tmp_path: Path) -> None:
    # E17 P4 / D-AB: abandon_story is now honest - it marks the story "abandoned"
    # (not "done") and appends story_abandoned, never story_completed.
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
    assert plan["work_units"][0]["status"] == "abandoned"
    events = [
        json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines() if line
    ]
    abandoned = [e for e in events if e["event"] == "story_abandoned"]
    assert abandoned and abandoned[-1]["decision"] == "abandon_story"
    assert not any(e["event"] == "story_completed" for e in events)
    assert tracker.pushed == []  # story-level abandon touches no tracker method


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


def test_readiness_approve_with_reason_applies_no_file_effects(tmp_path: Path) -> None:
    # The readiness branch validates and applies no file effects at Stage 2.5; the
    # readiness_gate_resolved event _resolve_gate appends is what advances the epic.
    directory = _write_epic(tmp_path, 56)
    tracker = _RecordingTracker(directory)

    changed = _apply_gate_resolution_effects(
        tmp_path,
        56,
        decision="approve_with_reason",
        gate_type="readiness_gate",
        story_id=None,
        triggered_by=["readiness_unready"],
        tracker=cast(Tracker, tracker),
    )

    assert changed == []
    assert tracker.pushed == []
    assert tracker.conflicts == []


def test_readiness_gate_rejects_verb_invalid_for_its_type(tmp_path: Path) -> None:
    directory = _write_epic(tmp_path, 57)
    tracker = _RecordingTracker(directory)

    with pytest.raises(StageStateError) as excinfo:
        _apply_gate_resolution_effects(
            tmp_path,
            57,
            decision="abandon_story",  # valid for story/review gates, not readiness
            gate_type="readiness_gate",
            story_id=None,
            triggered_by=["readiness_unready"],
            tracker=cast(Tracker, tracker),
        )
    message = str(excinfo.value)
    assert "abandon_story is not valid for readiness_gate" in message
    for verb in ("approve_with_reason", "revise_epic_contract", "abandon_epic"):
        assert verb in message


# --- retry_story for crashed/aborted executors (E17 P3 / S3) -------------------


def _write_story_artefacts(directory: Path, story_id: str) -> dict[str, Path]:
    """Lay down the per-story executor/check/critique/disposition artefacts a
    crashed executor leaves behind. The check/executor results are epic-level
    (shared) files; the critique/disposition are per-story."""
    (directory / "check-result.json").write_text(json.dumps({"ok": False}))
    (directory / "executor_result.json").write_text(
        json.dumps({"story_id": story_id, "outcome": "aborted_with_position"})
    )
    critique = directory / "critique" / f"story-{story_id}.md"
    critique.parent.mkdir(exist_ok=True)
    critique.write_text("---\ntarget: story\nseverity: minor\n---\nbody\n")
    disposition = directory / "dispositions" / f"story-{story_id}.md"
    disposition.parent.mkdir(exist_ok=True)
    disposition.write_text("---\ntarget: story\n---\nbody\n")
    return {
        "check_result": directory / "check-result.json",
        "executor_result": directory / "executor_result.json",
        "critique": critique,
        "disposition": disposition,
    }


def test_retry_story_resets_to_pending_and_clears_artefacts(tmp_path: Path) -> None:
    directory = _write_epic(tmp_path, 60, story_status="in_progress")
    artefacts = _write_story_artefacts(directory, "S1")
    tracker = _RecordingTracker(directory)

    changed = _apply_gate_resolution_effects(
        tmp_path,
        60,
        decision="retry_story",
        gate_type="story_gate",
        story_id="S1",
        triggered_by=["executor_aborted"],
        tracker=cast(Tracker, tracker),
    )

    plan = json.loads((directory / "plan.json").read_text())
    assert plan["work_units"][0]["status"] == "pending"
    for path in artefacts.values():
        assert not path.exists()
    # The reset rewrites plan.json and reports every removed artefact as changed.
    assert any("plan.json" in path for path in changed)
    for name in ("check-result.json", "executor_result.json", "story-S1.md"):
        assert any(name in path for path in changed)
    assert tracker.pushed == []  # retry never touches the tracker


def test_retry_story_audits_the_reset(tmp_path: Path) -> None:
    directory = _write_epic(tmp_path, 61, story_status="in_progress")
    _write_story_artefacts(directory, "S1")
    tracker = _RecordingTracker(directory)

    _apply_gate_resolution_effects(
        tmp_path,
        61,
        decision="retry_story",
        gate_type="review_gate",
        story_id="S1",
        triggered_by=["executor_crash"],
        tracker=cast(Tracker, tracker),
    )

    events = [
        json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines() if line
    ]
    retried = [e for e in events if e["event"] == "story_retried"]
    assert retried and retried[-1]["story_id"] == "S1"
    assert retried[-1]["epic_id"] == 61


def test_retry_story_leaves_sibling_stories_untouched(tmp_path: Path) -> None:
    epic_id = 62
    directory = tmp_path / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True)

    def _story(story_id: str, status: str, deps: list[str]) -> dict:
        return {
            "id": story_id,
            "title": story_id,
            "summary": "do work",
            "paths": ["src/*.py"],
            "satisfies": ["O1"],
            "implements_contract_decisions": [],
            "uses_contract_decisions": [],
            "deps": deps,
            "tests": {"count": 1, "types": ["unit"]},
            "status": status,
        }

    plan = {
        "epic_id": epic_id,
        "goal": "two stories",
        "work_units": [_story("S1", "done", []), _story("S2", "in_progress", ["S1"])],
    }
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "epic.jsonl").write_text("")
    sibling = _write_story_artefacts(directory, "S1")  # the completed sibling
    target = _write_story_artefacts(directory, "S2")  # the crashed story (retried)
    tracker = _RecordingTracker(directory)

    _apply_gate_resolution_effects(
        tmp_path,
        epic_id,
        decision="retry_story",
        gate_type="review_gate",
        story_id="S2",
        triggered_by=["executor_crash"],
        tracker=cast(Tracker, tracker),
    )

    by_id = {s["id"]: s for s in json.loads((directory / "plan.json").read_text())["work_units"]}
    assert by_id["S2"]["status"] == "pending"
    assert by_id["S1"]["status"] == "done"  # sibling status untouched
    # Only the retried story's per-story artefacts are cleared.
    assert not target["critique"].exists()
    assert not target["disposition"].exists()
    assert sibling["critique"].exists()
    assert sibling["disposition"].exists()


def test_retry_story_without_story_id_is_rejected(tmp_path: Path) -> None:
    # End-of-epic review_gates carry story_id: null (the schema permits it), yet
    # retry_story is a valid review_gate verb. With no story to target it must be a
    # structured error, not a silent successful retry that mutates nothing.
    directory = _write_epic(tmp_path, 63, story_status="in_progress")
    tracker = _RecordingTracker(directory)

    with pytest.raises(StageStateError, match="retry_story requires a targeted story"):
        _apply_gate_resolution_effects(
            tmp_path,
            63,
            decision="retry_story",
            gate_type="review_gate",
            story_id=None,
            triggered_by=["epic_review"],
            tracker=cast(Tracker, tracker),
        )

    # The guard fires before any effect: the story is untouched and no audit ran.
    plan = json.loads((directory / "plan.json").read_text())
    assert plan["work_units"][0]["status"] == "in_progress"
    events = [
        json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines() if line
    ]
    assert not any(e["event"] == "story_retried" for e in events)


def test_resolve_gate_retry_story_without_story_keeps_gate(tmp_path: Path) -> None:
    # End-to-end through _resolve_gate: a story-less review_gate resolved with
    # retry_story exits 2, the gate stays open on disk, and neither a story_retried
    # nor a gate-resolved event is written.
    directory = _write_epic(tmp_path, 64, story_status="in_progress")
    gate = directory / "gate.md"
    gate.write_text(
        "---\ntype: review_gate\ntriggered_by:\n- epic_review\n---\n\nReview gate body.\n",
        encoding="utf-8",
    )
    tracker = _RecordingTracker(directory)

    rc = _resolve_gate(tmp_path, 64, "retry_story", cast(Tracker, tracker))

    assert rc == 2
    assert gate.exists()  # the gate stays open and unresolved
    events = [
        json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines() if line
    ]
    assert not any(e["event"] == "story_retried" for e in events)
    assert not any(e["event"] in {"gate_resolved", "review_gate_resolved"} for e in events)


def test_retry_story_on_done_story_is_rejected(tmp_path: Path) -> None:
    # A story/review gate can open after the commit node already marked the story
    # done (e.g. a post-staging check-7 gate). retry_story recovers crashed/aborted
    # executors, not completed stories: resetting a done story to pending would
    # strand its prior story_completed event (the rerun's re-emission is deduped).
    # So a done target is a structured error, not a silent reset.
    directory = _write_epic(tmp_path, 65, story_status="done")
    artefacts = _write_story_artefacts(directory, "S1")
    tracker = _RecordingTracker(directory)

    with pytest.raises(StageStateError, match=r"S1.*already done"):
        _apply_gate_resolution_effects(
            tmp_path,
            65,
            decision="retry_story",
            gate_type="review_gate",
            story_id="S1",
            triggered_by=["check_7_commit_transaction"],
            tracker=cast(Tracker, tracker),
        )

    # The guard fires before any effect: status stays done, no audit ran, and the
    # per-story artefacts a successful retry would clear are all still present.
    plan = json.loads((directory / "plan.json").read_text())
    assert plan["work_units"][0]["status"] == "done"
    events = [
        json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines() if line
    ]
    assert not any(e["event"] == "story_retried" for e in events)
    for path in artefacts.values():
        assert path.exists()


def test_retry_story_on_abandoned_story_is_rejected(tmp_path: Path) -> None:
    # abandoned is the other terminal status (E17 P4): like done, it is out of the
    # crashed/aborted-executor domain retry_story recovers. Resetting it to pending
    # would strand its prior story_abandoned event, so an abandoned target is a
    # structured error naming the actual terminal status, not a silent reset.
    directory = _write_epic(tmp_path, 67, story_status="abandoned")
    artefacts = _write_story_artefacts(directory, "S1")
    tracker = _RecordingTracker(directory)

    with pytest.raises(StageStateError, match=r"S1.*already abandoned"):
        _apply_gate_resolution_effects(
            tmp_path,
            67,
            decision="retry_story",
            gate_type="review_gate",
            story_id="S1",
            triggered_by=["check_7_commit_transaction"],
            tracker=cast(Tracker, tracker),
        )

    # The guard fires before any effect: status stays abandoned, no audit ran, and
    # the per-story artefacts a successful retry would clear are all still present.
    plan = json.loads((directory / "plan.json").read_text())
    assert plan["work_units"][0]["status"] == "abandoned"
    events = [
        json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines() if line
    ]
    assert not any(e["event"] == "story_retried" for e in events)
    for path in artefacts.values():
        assert path.exists()


def test_resolve_gate_retry_story_on_done_story_keeps_gate(tmp_path: Path) -> None:
    # End-to-end through _resolve_gate: retrying a done story exits 2, the gate
    # stays open on disk, and neither a story_retried nor a gate-resolved event is
    # written - identical contract to the story-less guard.
    directory = _write_epic(tmp_path, 66, story_status="done")
    _write_story_artefacts(directory, "S1")
    gate = directory / "gate.md"
    gate.write_text(
        "---\ntype: review_gate\nstory_id: S1\ntriggered_by:\n"
        "- check_7_commit_transaction\n---\n\nReview gate body.\n",
        encoding="utf-8",
    )
    tracker = _RecordingTracker(directory)

    rc = _resolve_gate(tmp_path, 66, "retry_story", cast(Tracker, tracker))

    assert rc == 2
    assert gate.exists()  # the gate stays open and unresolved
    plan = json.loads((directory / "plan.json").read_text())
    assert plan["work_units"][0]["status"] == "done"
    events = [
        json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines() if line
    ]
    assert not any(e["event"] == "story_retried" for e in events)
    assert not any(e["event"] in {"gate_resolved", "review_gate_resolved"} for e in events)


# --- revise_epic_contract archives the prior contract (E17 P5 / D-RC) ----------


def _write_epic_md(directory: Path, epic_id: int) -> Path:
    epic_path = directory / "EPIC.md"
    epic_path.write_text(
        f"---\nepic_id: {epic_id}\ntitle: prior contract\n---\n\nThe prior contract body.\n",
        encoding="utf-8",
    )
    return epic_path


def test_plan_gate_revise_epic_contract_archives_and_clears_plan(tmp_path: Path) -> None:
    # The plan-gate revise_epic_contract channel archives the prior EPIC.md to
    # definition/EPIC.1.archived.md, snapshots the gate findings, and still clears
    # the now-stale plan artefacts.
    directory = _write_epic(tmp_path, 70)
    epic_path = _write_epic_md(directory, 70)
    (directory / "PLAN.md").write_text("# Plan\n")
    critique = directory / "critique" / "plan.md"
    critique.parent.mkdir()
    critique.write_text("---\ntarget: plan\n---\nbody\n")
    (directory / "gate.md").write_text(
        "---\ntype: plan_gate\nstory_id: null\ntriggered_by:\n- plan_review\n---\n\n"
        "## Findings\n\n- The contract under-specifies O1.\n",
        encoding="utf-8",
    )
    tracker = _RecordingTracker(directory)

    changed = _apply_gate_resolution_effects(
        tmp_path,
        70,
        decision="revise_epic_contract",
        gate_type="plan_gate",
        story_id=None,
        triggered_by=["plan_review"],
        tracker=cast(Tracker, tracker),
    )

    # Hand-editing stays forbidden: the prior EPIC.md is moved out of place (so the
    # definition node must re-dispatch) and archived under definition/.
    assert not epic_path.exists()
    archived = directory / "definition" / "EPIC.1.archived.md"
    assert archived.exists()
    assert "prior contract body" in archived.read_text()
    findings = directory / "definition" / "EPIC.1.findings.md"
    assert findings.exists()
    assert "under-specifies O1" in findings.read_text()
    # The stale plan artefacts are cleared.
    assert not (directory / "plan.json").exists()
    assert not (directory / "PLAN.md").exists()
    assert not critique.exists()
    # Every effect is reported as a changed path.
    assert any("definition/EPIC.1.archived.md" in path for path in changed)
    assert any("definition/EPIC.1.findings.md" in path for path in changed)
    assert any("plan.json" in path for path in changed)
    assert tracker.pushed == []  # contract revision touches no tracker method


def test_readiness_gate_revise_epic_contract_archives_prior_contract(tmp_path: Path) -> None:
    # The readiness-gate revise_epic_contract channel archives the prior EPIC.md and
    # snapshots the readiness findings; there is no plan to clear pre-planning.
    directory = _write_epic(tmp_path, 71)
    epic_path = _write_epic_md(directory, 71)
    (directory / "readiness-result.json").write_text(json.dumps({"ok": False}))
    (directory / "gate.md").write_text(
        "---\ntype: readiness_gate\nstory_id: null\ntriggered_by:\n- readiness_unready\n---\n\n"
        "## Findings\n\n- O1 lacks a machine-checkable signal.\n",
        encoding="utf-8",
    )
    tracker = _RecordingTracker(directory)

    changed = _apply_gate_resolution_effects(
        tmp_path,
        71,
        decision="revise_epic_contract",
        gate_type="readiness_gate",
        story_id=None,
        triggered_by=["readiness_unready"],
        tracker=cast(Tracker, tracker),
    )

    assert not epic_path.exists()
    archived = directory / "definition" / "EPIC.1.archived.md"
    findings = directory / "definition" / "EPIC.1.findings.md"
    assert archived.exists()
    assert findings.exists()
    assert "machine-checkable signal" in findings.read_text()
    # The persistent readiness result is left in place; only EPIC.md is archived.
    assert (directory / "readiness-result.json").exists()
    assert any("definition/EPIC.1.archived.md" in path for path in changed)
    assert any("definition/EPIC.1.findings.md" in path for path in changed)
    assert tracker.pushed == []


def test_revise_epic_contract_increments_archive_index(tmp_path: Path) -> None:
    # A second revision archives to definition/EPIC.2.archived.md, never clobbering
    # the first archived contract.
    directory = _write_epic(tmp_path, 72)
    archived_dir = directory / "definition"
    archived_dir.mkdir()
    (archived_dir / "EPIC.1.archived.md").write_text("first archive\n")
    _write_epic_md(directory, 72)
    (directory / "gate.md").write_text(
        "---\ntype: readiness_gate\nstory_id: null\ntriggered_by:\n- readiness_unready\n---\n\n"
        "## Findings\n\n- second pass.\n",
        encoding="utf-8",
    )
    tracker = _RecordingTracker(directory)

    _apply_gate_resolution_effects(
        tmp_path,
        72,
        decision="revise_epic_contract",
        gate_type="readiness_gate",
        story_id=None,
        triggered_by=["readiness_unready"],
        tracker=cast(Tracker, tracker),
    )

    assert (archived_dir / "EPIC.1.archived.md").read_text() == "first archive\n"
    assert (archived_dir / "EPIC.2.archived.md").exists()
    assert "prior contract body" in (archived_dir / "EPIC.2.archived.md").read_text()
