"""Tests for the issue-tracker abstraction (ADR-003)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from woof.trackers import (
    GitHubTracker,
    LocalTracker,
    Tracker,
    TrackerError,
    resolve_tracker,
)
from woof.trackers.epic_body import (
    epic_markdown_from_issue,
    render_epic_issue_body,
    seed_from_spark,
)
from woof.trackers.github import github_core_remaining

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


def _write_prereq(project: Path, body: str) -> None:
    (project / ".woof").mkdir(parents=True, exist_ok=True)
    (project / ".woof" / "prerequisites.toml").write_text(body)


def _epic_front() -> dict:
    return {
        "epic_id": 1,
        "title": "Comment publishing",
        "observable_outcomes": [
            {"id": "O1", "statement": "Users can post a comment.", "verification": "automated"},
        ],
        "contract_decisions": [],
        "acceptance_criteria": ["All outcomes covered by tests."],
    }


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def test_resolve_tracker_returns_github_adapter(tmp_path: Path) -> None:
    _write_prereq(tmp_path, '[tracker]\nkind = "github"\nrepo = "acme/widgets"\n')
    tracker = resolve_tracker(tmp_path)
    assert isinstance(tracker, GitHubTracker)
    assert tracker.kind == "github"
    assert tracker.repo == "acme/widgets"


def test_resolve_tracker_returns_local_adapter(tmp_path: Path) -> None:
    _write_prereq(tmp_path, '[tracker]\nkind = "local"\n')
    tracker = resolve_tracker(tmp_path)
    assert isinstance(tracker, LocalTracker)
    assert tracker.kind == "local"


def test_resolve_tracker_rejects_missing_table(tmp_path: Path) -> None:
    _write_prereq(tmp_path, '[infra]\njust = "any"\n')
    with pytest.raises(TrackerError, match=r"missing \[tracker\]"):
        resolve_tracker(tmp_path)


def test_resolve_tracker_rejects_unknown_kind(tmp_path: Path) -> None:
    _write_prereq(tmp_path, '[tracker]\nkind = "linear"\n')
    with pytest.raises(TrackerError, match="kind must be one of"):
        resolve_tracker(tmp_path)


def test_resolve_tracker_github_requires_repo(tmp_path: Path) -> None:
    _write_prereq(tmp_path, '[tracker]\nkind = "github"\n')
    with pytest.raises(TrackerError, match="requires a non-empty repo"):
        resolve_tracker(tmp_path)


def test_resolve_tracker_fails_without_prerequisites(tmp_path: Path) -> None:
    with pytest.raises(TrackerError, match="not found"):
        resolve_tracker(tmp_path)


# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tracker",
    [GitHubTracker(Path("/tmp/x"), "acme/widgets"), LocalTracker(Path("/tmp/x"))],
)
def test_adapters_satisfy_tracker_protocol(tracker: Tracker) -> None:
    assert isinstance(tracker, Tracker)
    assert tracker.kind in {"github", "local"}


# ---------------------------------------------------------------------------
# local adapter
# ---------------------------------------------------------------------------


def test_local_create_epic_assigns_first_id(tmp_path: Path) -> None:
    _write_prereq(tmp_path, '[tracker]\nkind = "local"\n')
    tracker = LocalTracker(tmp_path)
    result = tracker.create_epic("Build a thing\n\nMore detail.")

    assert result.epic_id == 1
    assert result.epic_ref == ".woof/epics/E1"
    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    assert (epic_dir / "spark.md").read_text() == "# Build a thing\n\nMore detail.\n"
    assert (tmp_path / ".woof" / ".current-epic").read_text() == "E1\n"
    assert not (epic_dir / ".last-sync").exists()
    events = [json.loads(line) for line in (epic_dir / "epic.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["spark_created", "current_epic_selected"]
    assert events[0]["source"] == "local"


def test_local_create_epic_increments_past_existing(tmp_path: Path) -> None:
    _write_prereq(tmp_path, '[tracker]\nkind = "local"\n')
    (tmp_path / ".woof" / "epics" / "E4").mkdir(parents=True)
    (tmp_path / ".woof" / "epics" / "E2").mkdir(parents=True)
    result = LocalTracker(tmp_path).create_epic("Another epic")
    assert result.epic_id == 5


def test_local_fetch_epic_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(TrackerError, match="no remote"):
        LocalTracker(tmp_path).fetch_epic(7)


def test_local_resolve_conflict_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(TrackerError, match="no remote"):
        LocalTracker(tmp_path).resolve_conflict(1, "keep_local")


def test_local_runtime_and_authority_checks_are_noops(tmp_path: Path) -> None:
    tracker = LocalTracker(tmp_path)
    tracker.assert_runtime_reachable()
    tracker.assert_epic_authority(1)
    assert tracker.has_sync_state(1) is True


def test_local_push_operations_keep_everything_local(tmp_path: Path) -> None:
    tracker = LocalTracker(tmp_path)
    definition = tracker.push_epic_definition(1, _epic_front(), "Intent prose.\n")
    assert definition.changed is False
    assert "## Observable Outcomes" in definition.body

    plan_summary = tracker.push_plan_summary(1)
    assert plan_summary.changed is False
    assert plan_summary.closed is False

    completion = tracker.complete_epic(1)
    assert completion.changed is False
    assert completion.closed is True


# ---------------------------------------------------------------------------
# tracker-agnostic epic body
# ---------------------------------------------------------------------------


def test_epic_body_render_then_parse_round_trips() -> None:
    body = render_epic_issue_body(_epic_front(), "Intent prose.\n", remote_body=None)
    epic_md = epic_markdown_from_issue(epic_id=1, title="Comment publishing", body=body)
    assert epic_md is not None
    assert "id: O1" in epic_md
    assert "Users can post a comment." in epic_md
    assert "All outcomes covered by tests." in epic_md


def test_seed_from_spark_splits_title_and_body() -> None:
    title, body = seed_from_spark("My title\n\nThe body.")
    assert title == "My title"
    assert body == "The body.\n"
    title, body = seed_from_spark("Only a title")
    assert title == "Only a title"
    assert body == "Only a title\n"


def test_seed_from_spark_rejects_empty() -> None:
    with pytest.raises(TrackerError, match="must not be empty"):
        seed_from_spark("   ")


def test_github_core_remaining_parses_rate_limit() -> None:
    assert github_core_remaining('{"resources":{"core":{"remaining":4321}}}') == 4321
    assert github_core_remaining("not json") is None


# ---------------------------------------------------------------------------
# local adapter end-to-end through the CLI
# ---------------------------------------------------------------------------


def test_woof_wf_new_local_tracker_never_calls_gh(tmp_path: Path) -> None:
    """`woof wf new` with kind=local must create the epic without touching gh."""
    project = tmp_path / "project"
    _write_prereq(project, '[tracker]\nkind = "local"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_stub = bin_dir / "gh"
    gh_stub.write_text(
        "#!/usr/bin/env bash\necho 'gh must not run for the local tracker' >&2\nexit 99\n"
    )
    gh_stub.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", "/tmp"),
    }

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "wf",
            "new",
            "Portable epic\n\nRuns without GitHub.",
            "--format",
            "json",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "created"
    assert payload["epic_id"] == 1
    assert payload["epic_ref"] == ".woof/epics/E1"
    epic_dir = project / ".woof" / "epics" / "E1"
    assert (epic_dir / "spark.md").read_text() == "# Portable epic\n\nRuns without GitHub.\n"
    assert (project / ".woof" / ".current-epic").read_text() == "E1\n"
