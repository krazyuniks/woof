"""`woof wf reset` - reset an epic to its spark.

The CLI behaviour runs through the real `bin/woof` against a local-tracker
project (no network, no gh stub). The event-log scoping that keeps a rebuilt
epic correct is exercised at the library level against
``graph.transitions.iter_epic_events``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from woof.graph.transitions import (
    append_epic_event,
    epic_event_exists,
    iter_epic_events,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


def _local_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".woof").mkdir(parents=True)
    (project / ".woof" / "prerequisites.toml").write_text('[tracker]\nkind = "local"\n')
    return project


def _seed_epic(project: Path, epic_id: int) -> Path:
    """Create an epic that has run well past spark, with kept and derived state."""
    epic_dir = project / ".woof" / "epics" / f"E{epic_id}"
    epic_dir.mkdir(parents=True)

    # Kept: inputs and lineage only.
    (epic_dir / "spark.md").write_text("# Spark\n\nThe original idea.\n")
    (epic_dir / ".last-sync").write_text(json.dumps({"issue_number": epic_id}) + "\n")
    (epic_dir / "epic.jsonl").write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {"event": "spark_created", "epic_id": epic_id},
                {"event": "breakdown_planned", "epic_id": epic_id},
                {"event": "plan_critiqued", "epic_id": epic_id},
                {"event": "plan_gate_resolved", "epic_id": epic_id, "decision": "approve"},
            )
        )
        + "\n"
    )

    # Removed: every derived artefact, telemetry log, and runtime file.
    (epic_dir / "EPIC.md").write_text(f"---\nepic_id: {epic_id}\n---\n")
    (epic_dir / "plan.json").write_text(f'{{"epic_id":{epic_id},"goal":"x","stories":[]}}\n')
    (epic_dir / "PLAN.md").write_text("# Plan\n")
    (epic_dir / "executor_result.json").write_text('{"outcome":"staged_for_verification"}\n')
    (epic_dir / "check-result.json").write_text('{"ok":true}\n')
    (epic_dir / "gate.md").write_text("---\ntype: plan_gate\n---\n")
    (epic_dir / "gate-position.md").write_text("position prose\n")
    (epic_dir / "dispatch.jsonl").write_text('{"event":"dispatch_started"}\n')
    (epic_dir / ".wf.lock").write_text('{"pid":1}\n')
    (epic_dir / "dispositions").mkdir()
    (epic_dir / "dispositions" / "story-S1.md").write_text("---\nverdict: accept\n---\n")
    (epic_dir / "audit" / "raw").mkdir(parents=True)
    (epic_dir / "audit" / "raw" / "transcript.txt").write_text("old transcript\n")
    (epic_dir / "discovery" / "brainstorm").mkdir(parents=True)
    (epic_dir / "discovery" / "brainstorm" / "design.md").write_text("bundle\n")
    (epic_dir / "discovery" / "synthesis").mkdir(parents=True)
    (epic_dir / "discovery" / "synthesis" / "ARCHITECTURE.md").write_text("arch\n")
    (epic_dir / "critique").mkdir()
    (epic_dir / "critique" / "plan.md").write_text("---\nseverity: blocker\n---\n")
    return epic_dir


def _run(project: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), *args],
        cwd=project,
        input=stdin,
        capture_output=True,
        text=True,
    )


def _assert_lineage_kept(epic_dir: Path) -> None:
    # Deny-by-default: only the inputs and lineage survive.
    assert sorted(child.name for child in epic_dir.iterdir()) == [
        ".last-sync",
        "epic.jsonl",
        "spark.md",
    ]


def _assert_derived_gone(epic_dir: Path) -> None:
    for name in (
        "EPIC.md",
        "plan.json",
        "PLAN.md",
        "executor_result.json",
        "check-result.json",
        "gate.md",
        "gate-position.md",
        "dispatch.jsonl",
        ".wf.lock",
    ):
        assert not (epic_dir / name).exists(), name
    for subdir in ("discovery", "critique", "dispositions", "audit"):
        assert not (epic_dir / subdir).exists(), subdir


def test_reset_removes_derived_keeps_lineage(tmp_path: Path) -> None:
    project = _local_project(tmp_path)
    epic_dir = _seed_epic(project, 5)

    proc = _run(project, "wf", "reset", "--epic", "5", "--yes")

    assert proc.returncode == 0, proc.stderr
    assert "reset to spark" in proc.stdout
    _assert_derived_gone(epic_dir)
    _assert_lineage_kept(epic_dir)

    # The append-only log keeps its history and gains an epic_reset marker.
    events = [json.loads(line) for line in (epic_dir / "epic.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "spark_created",
        "breakdown_planned",
        "plan_critiqued",
        "plan_gate_resolved",
        "epic_reset",
    ]


def test_reset_without_yes_aborts_on_no(tmp_path: Path) -> None:
    project = _local_project(tmp_path)
    epic_dir = _seed_epic(project, 5)

    proc = _run(project, "wf", "reset", "--epic", "5", stdin="n\n")

    assert proc.returncode == 1
    assert "aborted" in proc.stdout
    # Nothing was touched.
    assert (epic_dir / "EPIC.md").is_file()
    assert (epic_dir / "discovery").is_dir()
    events = [json.loads(line) for line in (epic_dir / "epic.jsonl").read_text().splitlines()]
    assert "epic_reset" not in [event["event"] for event in events]


def test_reset_without_yes_proceeds_on_yes(tmp_path: Path) -> None:
    project = _local_project(tmp_path)
    epic_dir = _seed_epic(project, 5)

    proc = _run(project, "wf", "reset", "--epic", "5", stdin="y\n")

    assert proc.returncode == 0, proc.stderr
    _assert_derived_gone(epic_dir)
    _assert_lineage_kept(epic_dir)


def test_reset_unknown_epic_fails(tmp_path: Path) -> None:
    project = _local_project(tmp_path)

    proc = _run(project, "wf", "reset", "--epic", "99", "--yes")

    assert proc.returncode == 2
    assert "E99 not found" in proc.stderr


def test_reset_requires_epic(tmp_path: Path) -> None:
    project = _local_project(tmp_path)

    proc = _run(project, "wf", "reset", "--yes")

    assert proc.returncode == 2
    assert "--epic is required" in proc.stderr


def test_reset_is_idempotent_at_spark(tmp_path: Path) -> None:
    project = _local_project(tmp_path)
    _seed_epic(project, 5)

    first = _run(project, "wf", "reset", "--epic", "5", "--yes")
    assert first.returncode == 0, first.stderr

    second = _run(project, "wf", "reset", "--epic", "5", "--yes")
    assert second.returncode == 0, second.stderr
    assert "already at spark" in second.stdout


def test_iter_epic_events_scopes_to_after_last_reset(tmp_path: Path) -> None:
    """State-derivation readers ignore events from before an epic_reset."""
    repo_root = tmp_path
    (repo_root / ".woof" / "epics" / "E1").mkdir(parents=True)

    append_epic_event(repo_root, 1, {"event": "plan_gate_resolved", "decision": "approve"})
    append_epic_event(repo_root, 1, {"event": "breakdown_planned"})
    assert epic_event_exists(repo_root, 1, event="plan_gate_resolved")

    append_epic_event(repo_root, 1, {"event": "epic_reset", "epic_id": 1})

    # Pre-reset events are now invisible to the state readers...
    assert iter_epic_events(repo_root, 1) == []
    assert not epic_event_exists(repo_root, 1, event="plan_gate_resolved")
    assert not epic_event_exists(repo_root, 1, event="breakdown_planned")

    # ...but a fresh event after the reset is seen.
    append_epic_event(repo_root, 1, {"event": "breakdown_planned"})
    assert epic_event_exists(repo_root, 1, event="breakdown_planned")
    assert [event["event"] for event in iter_epic_events(repo_root, 1)] == ["breakdown_planned"]
