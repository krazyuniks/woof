"""Tests for check_9_review_valve - Stage-5 Check 9."""

from __future__ import annotations

import json
from pathlib import Path

from woof.checks import CheckContext
from woof.checks.runners.check_9_review_valve import (
    _is_end_of_epic,
    check_9_review_valve_runner,
)
from woof.gate.write import write_gate_from_check_result


def _story(story_id: str, status: str) -> dict:
    return {"id": story_id, "status": status}


def _ctx(repo_root: Path, stories: list[dict], story_id: str = "S2") -> CheckContext:
    epic_dir = repo_root / ".woof" / "epics" / "E1"
    epic_dir.mkdir(parents=True, exist_ok=True)
    return CheckContext(
        epic_id=1,
        story_id=story_id,
        repo_root=repo_root,
        epic_dir=epic_dir,
        plan={"epic_id": 1, "goal": "test", "work_units": stories},
        critique=None,
    )


def _write_agents(repo_root: Path, *, every_n: int = 2, end_of_epic: bool = False) -> None:
    woof_dir = repo_root / ".woof"
    woof_dir.mkdir(exist_ok=True)
    woof_dir.joinpath("agents.toml").write_text(
        "[roles]\n"
        "\n"
        "[review_valve]\n"
        f"every_n_stories = {every_n}\n"
        f"end_of_epic = {str(end_of_epic).lower()}\n"
    )


def _write_critique(epic_dir: Path, story_id: str, findings: list[dict]) -> None:
    critique_dir = epic_dir / "critique"
    critique_dir.mkdir(parents=True, exist_ok=True)
    severity = "minor" if findings else "info"
    critique_dir.joinpath(f"story-{story_id}.md").write_text(
        "---\n"
        "target: story\n"
        f"target_id: {story_id}\n"
        f"severity: {severity}\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "harness: test\n"
        f"findings: {json.dumps(findings)}\n"
        "---\n"
    )


def test_threshold_due_with_minor_findings_fails(tmp_path: Path) -> None:
    _write_agents(tmp_path, every_n=2, end_of_epic=False)
    ctx = _ctx(tmp_path, [_story("S1", "done"), _story("S2", "in_progress")])
    _write_critique(
        ctx.epic_dir,
        "S2",
        [{"id": "F1", "severity": "minor", "summary": "Follow-up refactor is worth review"}],
    )

    outcome = check_9_review_valve_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "minor"
    assert "every 2 stories" in outcome.summary
    assert "S2/F1: Follow-up refactor is worth review" in (outcome.evidence or "")


def test_end_of_epic_due_with_minor_findings_fails(tmp_path: Path) -> None:
    _write_agents(tmp_path, every_n=5, end_of_epic=True)
    ctx = _ctx(tmp_path, [_story("S1", "done"), _story("S2", "in_progress")])
    _write_critique(
        ctx.epic_dir,
        "S2",
        [{"id": "F1", "severity": "minor", "summary": "End-of-epic polish note"}],
    )

    outcome = check_9_review_valve_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "minor"
    assert "end of epic" in outcome.summary


def test_end_of_epic_due_when_only_later_stories_are_abandoned(tmp_path: Path) -> None:
    # A later story that is `abandoned` is terminal and will never be worked, so
    # the current story is still end-of-epic. The valve must fire on its minor
    # findings rather than be suppressed by the abandoned tail. every_n is set
    # high so only the end-of-epic trigger can fire.
    _write_agents(tmp_path, every_n=5, end_of_epic=True)
    ctx = _ctx(
        tmp_path,
        [_story("S1", "done"), _story("S2", "in_progress"), _story("S3", "abandoned")],
    )
    _write_critique(
        ctx.epic_dir,
        "S2",
        [{"id": "F1", "severity": "minor", "summary": "End-of-epic polish note"}],
    )

    outcome = check_9_review_valve_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "minor"
    assert "end of epic" in outcome.summary


def test_is_end_of_epic_treats_abandoned_tail_as_terminal() -> None:
    # An abandoned tail story is terminal: the current story is still end-of-epic.
    abandoned_tail = [
        _story("S1", "done"),
        _story("S2", "in_progress"),
        _story("S3", "abandoned"),
    ]
    assert _is_end_of_epic(abandoned_tail, "S2") is True
    # A pending tail story still needs work: not end-of-epic.
    pending_tail = [
        _story("S1", "done"),
        _story("S2", "in_progress"),
        _story("S3", "pending"),
    ]
    assert _is_end_of_epic(pending_tail, "S2") is False


def test_due_boundary_with_no_minor_findings_passes(tmp_path: Path) -> None:
    _write_agents(tmp_path, every_n=2, end_of_epic=True)
    ctx = _ctx(tmp_path, [_story("S1", "done"), _story("S2", "in_progress")])
    _write_critique(ctx.epic_dir, "S2", [])

    outcome = check_9_review_valve_runner(ctx)

    assert outcome.ok
    assert outcome.severity == "info"
    assert "no minor critique findings" in outcome.summary


def test_already_review_gated_boundary_passes(tmp_path: Path) -> None:
    _write_agents(tmp_path, every_n=2, end_of_epic=False)
    ctx = _ctx(tmp_path, [_story("S1", "done"), _story("S2", "in_progress")])
    _write_critique(
        ctx.epic_dir,
        "S2",
        [{"id": "F1", "severity": "minor", "summary": "Already surfaced"}],
    )
    ctx.epic_dir.joinpath("epic.jsonl").write_text(
        json.dumps(
            {
                "event": "review_gate_opened",
                "story_id": "S2",
                "triggered_by": ["check_9_review_valve"],
            }
        )
        + "\n"
    )

    outcome = check_9_review_valve_runner(ctx)

    assert outcome.ok
    assert "already been review-gated" in outcome.summary


def test_retried_boundary_re_arms_valve(tmp_path: Path) -> None:
    _write_agents(tmp_path, every_n=2, end_of_epic=False)
    ctx = _ctx(tmp_path, [_story("S1", "done"), _story("S2", "in_progress")])
    _write_critique(
        ctx.epic_dir,
        "S2",
        [{"id": "F2", "severity": "minor", "summary": "Fresh finding after retry"}],
    )
    # The valve already fired and opened a review gate at S2, but retry_story
    # then reset S2 and appended story_retried. The stale review_gate_opened must
    # not suppress a new gate once the retried story produces a fresh critique.
    ctx.epic_dir.joinpath("epic.jsonl").write_text(
        json.dumps(
            {
                "event": "review_gate_opened",
                "story_id": "S2",
                "triggered_by": ["check_9_review_valve"],
            }
        )
        + "\n"
        + json.dumps({"event": "story_retried", "story_id": "S2"})
        + "\n"
    )

    outcome = check_9_review_valve_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "minor"
    assert "S2/F2: Fresh finding after retry" in (outcome.evidence or "")


def test_retried_boundary_keeps_prior_sibling_gated_when_clean(tmp_path: Path) -> None:
    _write_agents(tmp_path, every_n=2, end_of_epic=False)
    ctx = _ctx(tmp_path, [_story("S1", "done"), _story("S2", "in_progress")])
    # The S2 review gate bundled S1's minor finding. retry_story on S2 reset S2
    # and deleted its critique; the retried run produced a clean S2 critique. The
    # already-gated S1 finding must not re-open a gate now that S2 has nothing new.
    _write_critique(
        ctx.epic_dir,
        "S1",
        [{"id": "F1", "severity": "minor", "summary": "Already gated at the S2 review"}],
    )
    _write_critique(ctx.epic_dir, "S2", [])
    ctx.epic_dir.joinpath("epic.jsonl").write_text(
        json.dumps(
            {
                "event": "review_gate_opened",
                "story_id": "S2",
                "triggered_by": ["check_9_review_valve"],
            }
        )
        + "\n"
        + json.dumps({"event": "story_retried", "story_id": "S2"})
        + "\n"
    )

    outcome = check_9_review_valve_runner(ctx)

    assert outcome.ok
    assert outcome.severity == "info"
    assert "no minor critique findings" in outcome.summary


def test_retried_boundary_gates_only_fresh_finding(tmp_path: Path) -> None:
    _write_agents(tmp_path, every_n=2, end_of_epic=False)
    ctx = _ctx(tmp_path, [_story("S1", "done"), _story("S2", "in_progress")])
    # Same setup as above, but the retried S2 critique surfaces a new minor
    # finding. The new gate must carry only that fresh finding, not the S1 finding
    # that was already bundled into the previous review gate.
    _write_critique(
        ctx.epic_dir,
        "S1",
        [{"id": "F1", "severity": "minor", "summary": "Already gated at the S2 review"}],
    )
    _write_critique(
        ctx.epic_dir,
        "S2",
        [{"id": "F3", "severity": "minor", "summary": "New issue from the retried run"}],
    )
    ctx.epic_dir.joinpath("epic.jsonl").write_text(
        json.dumps(
            {
                "event": "review_gate_opened",
                "story_id": "S2",
                "triggered_by": ["check_9_review_valve"],
            }
        )
        + "\n"
        + json.dumps({"event": "story_retried", "story_id": "S2"})
        + "\n"
    )

    outcome = check_9_review_valve_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "minor"
    assert "S2/F3: New issue from the retried run" in (outcome.evidence or "")
    assert "F1" not in (outcome.evidence or "")
    assert "S1" not in (outcome.evidence or "")


def test_check_9_gate_is_written_as_review_gate(tmp_path: Path) -> None:
    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    epic_dir.mkdir(parents=True)
    check_result = epic_dir / "check-result.json"
    check_result.write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 1,
                "story_id": "S2",
                "triggered_by": ["check_9_review_valve"],
                "checks": [
                    {
                        "id": "check_9_review_valve",
                        "ok": False,
                        "severity": "minor",
                        "summary": "review due",
                        "evidence": "S2/F1: review this",
                        "paths": [],
                        "command": None,
                        "exit_code": None,
                    }
                ],
            }
        )
    )

    gate_path = write_gate_from_check_result(check_result, None, epic_dir, "S2")

    gate_text = gate_path.read_text()
    assert "type: review_gate" in gate_text
    events = [json.loads(line) for line in epic_dir.joinpath("epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "review_gate_opened"
    assert events[-1]["gate_type"] == "review_gate"
