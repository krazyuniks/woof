"""Tests for the issue-tracker abstraction (ADR-003)."""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import woof.trackers.github as github_module
from tests.support import DEFAULT_PROJECT_KEY, seed_project_config
from woof import state
from woof.trackers import (
    CONFLICT_DECISIONS,
    GitHubTracker,
    LocalTracker,
    Tracker,
    TrackerError,
    resolve_tracker,
)
from woof.trackers.base import sha256_text
from woof.trackers.epic_body import (
    epic_markdown_from_issue,
    render_epic_issue_body,
    seed_from_spark,
)
from woof.trackers.github import GITHUB_COMMAND_TIMEOUT_SECONDS, github_core_remaining

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"

PROFILE_A_OVERRIDES: dict[str, Any] = {
    "delivery": {"profile": "A"},
    "profiles": {
        "A": {
            "github_repo": "example/project",
            "ready_label": "ready",
            "merge_path_groups": [],
            "worktree": {"root": "worktrees"},
        }
    },
    "cartography": {"floor": "none"},
    "drain": {"merge_after_ready_pr": True},
}


def _seed_local_tracker(overrides: dict[str, Any] | None = None) -> None:
    """Declare a local tracker in the operator-home project config."""

    config: dict[str, Any] = {"tracker": {"kind": "local", "repo": None}}
    for key, value in (overrides or {}).items():
        config[key] = value
    seed_project_config(config)


def _git_init(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)


def _epic_front(epic_id: int = 1) -> dict[str, Any]:
    return {
        "epic_id": epic_id,
        "title": "Comment publishing",
        "observable_outcomes": [
            {"id": "O1", "statement": "Users can post a comment.", "verification": "automated"},
        ],
        "contract_decisions": [],
        "acceptance_criteria": ["All outcomes covered by tests."],
    }


def _epic_md(epic_id: int = 1) -> str:
    return textwrap.dedent(
        f"""\
        ---
        epic_id: {epic_id}
        title: Comment publishing
        observable_outcomes:
          - id: O1
            statement: Users can post a comment.
            verification: automated
          - id: O2
            statement: Comments appear in real time.
            verification: hybrid
        contract_decisions:
          - id: CD1
            related_outcomes: [O1, O2]
            title: Comment publishing route
            openapi_ref: spec/openapi.yaml#/paths/~1comments/post
        acceptance_criteria:
          - All outcomes covered by tests.
        ---
        Enable users to publish comments.
        """
    )


def _plan_payload(epic_id: int = 1, *, done: bool = False) -> dict[str, Any]:
    status = "done" if done else "pending"
    return {
        "epic_id": epic_id,
        "goal": "Ship comment publishing.",
        "work_units": [
            {
                "id": "S1",
                "title": "Create comment API",
                "summary": "Add the write API.",
                "paths": ["src/comments.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": ["CD1"],
                "uses_contract_decisions": [],
                "deps": [],
                "tests": {"count": 2, "types": ["unit"]},
                "status": status,
            },
            {
                "id": "S2",
                "title": "Render live comments",
                "summary": "Show new comments in real time.",
                "paths": ["src/live.py"],
                "satisfies": ["O2"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": ["CD1"],
                "deps": ["S1"],
                "tests": {"count": 1, "types": ["integration"]},
                "status": status,
            },
        ],
    }


def _write_epic_contract_files(project_key: str, epic_id: int, *, done: bool = False) -> Path:
    epic_dir = state.epic_dir(project_key, epic_id)
    epic_dir.mkdir(parents=True, exist_ok=True)
    state.epic_contract_path(project_key, epic_id).write_text(_epic_md(epic_id), encoding="utf-8")
    state.plan_path(project_key, epic_id).write_text(json.dumps(_plan_payload(epic_id, done=done)))
    return epic_dir


def _write_last_sync(project_key: str, epic_id: int, *, updated_at: str, body: str) -> None:
    state.atomic_write_json(
        state.last_sync_path(project_key, epic_id),
        {
            "issue_number": epic_id,
            "updated_at": updated_at,
            "body_sha256": sha256_text(body),
            "body": body,
        },
    )


class GhCommandStub:
    def __init__(
        self,
        *,
        repo: str = "acme/widgets",
        issue_number: int = 7,
        title: str = "Tracker contract",
        body: str = "Remote tracker body.\n",
        updated_at: str = "2026-01-01T00:00:00Z",
    ) -> None:
        self.repo = repo
        self.issue_number = issue_number
        self.issue: dict[str, Any] = {
            "number": issue_number,
            "title": title,
            "body": body,
            "updated_at": updated_at,
            "state": "open",
        }
        self.calls: list[list[str]] = []
        self.created_body: str | None = None
        self.edited_bodies: list[str] = []
        self.close_count = 0
        self._tick = 1

    def set_remote(
        self,
        *,
        body: str,
        title: str | None = None,
        updated_at: str = "2026-01-01T00:00:00Z",
        state: str = "open",
        state_reason: str | None = None,
    ) -> None:
        self.issue.update(
            {
                "body": body,
                "updated_at": updated_at,
                "state": state,
            }
        )
        if title is not None:
            self.issue["title"] = title
        if state_reason is not None:
            self.issue["state_reason"] = state_reason

    def run(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "gh"
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == GITHUB_COMMAND_TIMEOUT_SECONDS
        self.calls.append(argv[1:])

        if argv[1:3] == ["api", "/rate_limit"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                '{"resources":{"core":{"remaining":5000}}}',
                "",
            )

        issue_api = f"/repos/{self.repo}/issues/{self.issue_number}"
        if argv[1:3] == ["api", issue_api]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.issue), "")

        if argv[1:3] == ["issue", "create"]:
            title = argv[argv.index("--title") + 1]
            self.created_body = str(kwargs.get("input") or "")
            self.issue.update(
                {
                    "title": title,
                    "body": self.created_body,
                    "updated_at": self._next_updated_at(),
                    "state": "open",
                }
            )
            return subprocess.CompletedProcess(
                argv,
                0,
                f"https://github.com/{self.repo}/issues/{self.issue_number}\n",
                "",
            )

        if argv[1:3] == ["issue", "edit"]:
            body_path = Path(argv[argv.index("--body-file") + 1])
            body = body_path.read_text(encoding="utf-8")
            self.issue["body"] = body
            self.issue["updated_at"] = self._next_updated_at()
            self.edited_bodies.append(body)
            return subprocess.CompletedProcess(argv, 0, "", "")

        if argv[1:3] == ["issue", "close"]:
            self.issue["state"] = "closed"
            if "--reason" in argv:
                reason = argv[argv.index("--reason") + 1]
                self.issue["state_reason"] = (
                    "not_planned" if reason == "not planned" else "completed"
                )
            self.issue["updated_at"] = self._next_updated_at()
            self.close_count += 1
            return subprocess.CompletedProcess(argv, 0, "", "")

        raise AssertionError(f"unexpected gh argv: {argv}")

    def _next_updated_at(self) -> str:
        self._tick += 1
        return f"2026-01-{self._tick:02d}T00:00:00Z"


@dataclass(frozen=True)
class TrackerContractCase:
    kind: str
    tracker: Tracker
    project_key: str
    gh: GhCommandStub | None = None


@pytest.fixture(params=("local", "github"))
def tracker_contract(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> TrackerContractCase:
    if request.param == "local":
        return TrackerContractCase(
            kind="local",
            tracker=LocalTracker(DEFAULT_PROJECT_KEY),
            project_key=DEFAULT_PROJECT_KEY,
        )

    gh = GhCommandStub()
    monkeypatch.setattr(github_module.subprocess, "run", gh.run)
    return TrackerContractCase(
        kind="github",
        tracker=GitHubTracker(DEFAULT_PROJECT_KEY, gh.repo),
        project_key=DEFAULT_PROJECT_KEY,
        gh=gh,
    )


def _prepare_contract_epic(
    case: TrackerContractCase,
    *,
    done: bool = False,
    remote_body: str = "Remote intent.\n\n## Observable Outcomes\n\n- stale\n",
) -> int:
    epic_id = case.gh.issue_number if case.gh else 1
    _write_epic_contract_files(case.project_key, epic_id, done=done)
    if case.gh is not None:
        case.gh.set_remote(body=remote_body, updated_at="2026-01-01T00:00:00Z")
        _write_last_sync(
            case.project_key,
            epic_id,
            updated_at="2026-01-01T00:00:00Z",
            body=remote_body,
        )
    return epic_id


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def test_resolve_tracker_returns_github_adapter() -> None:
    seed_project_config({"tracker": {"kind": "github", "repo": "acme/widgets"}})
    tracker = resolve_tracker()
    assert isinstance(tracker, GitHubTracker)
    assert tracker.kind == "github"
    assert tracker.repo == "acme/widgets"
    assert tracker.project_key == DEFAULT_PROJECT_KEY


def test_resolve_tracker_returns_local_adapter() -> None:
    _seed_local_tracker()
    tracker = resolve_tracker()
    assert isinstance(tracker, LocalTracker)
    assert tracker.kind == "local"
    assert tracker.project_key == DEFAULT_PROJECT_KEY


def test_resolve_tracker_rejects_missing_table() -> None:
    seed_project_config({"tracker": None})
    with pytest.raises(TrackerError, match=r"tracker\.kind must be one of"):
        resolve_tracker()


def test_resolve_tracker_rejects_unknown_kind() -> None:
    seed_project_config({"tracker": {"kind": "linear", "repo": None}})
    with pytest.raises(TrackerError, match="kind must be one of"):
        resolve_tracker()


def test_resolve_tracker_github_requires_repo() -> None:
    seed_project_config({"tracker": {"kind": "github", "repo": None}})
    with pytest.raises(TrackerError, match="requires a non-empty repo"):
        resolve_tracker()


def test_resolve_tracker_fails_without_project_config() -> None:
    with pytest.raises(TrackerError, match="missing Woof project config"):
        resolve_tracker("never-seeded")


# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tracker",
    [GitHubTracker("test-project", "acme/widgets"), LocalTracker("test-project")],
)
def test_adapters_satisfy_tracker_protocol(tracker: Tracker) -> None:
    assert isinstance(tracker, Tracker)
    assert tracker.kind in {"github", "local"}


def test_tracker_contract_create_epic_initialises_runtime_state(
    tracker_contract: TrackerContractCase,
) -> None:
    tracker_contract.tracker.assert_runtime_reachable()

    result = tracker_contract.tracker.create_epic("Contract matrix\n\nShared create behaviour.")

    assert result.epic_dir == state.epic_dir(tracker_contract.project_key, result.epic_id)
    assert result.spark_path == result.epic_dir / "spark.md"
    assert result.current_epic_path == state.current_epic_path(tracker_contract.project_key)
    assert result.current_epic_path.read_text(encoding="utf-8") == f"E{result.epic_id}\n"
    assert result.spark_path.read_text(encoding="utf-8").startswith("# Contract matrix\n\n")
    assert tracker_contract.tracker.has_sync_state(result.epic_id) is True

    events = [
        json.loads(line)
        for line in (result.epic_dir / "epic.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["event"] == "spark_created"
    assert events[0]["source"] == tracker_contract.kind
    assert events[-1]["event"] == "current_epic_selected"

    if tracker_contract.kind == "github":
        assert result.epic_ref == "https://github.com/acme/widgets/issues/7"
        assert result.last_sync_path.is_file()
    else:
        assert result.epic_ref == result.epic_dir.as_posix()
        assert not result.last_sync_path.exists()


def test_tracker_contract_fetch_epic_cold_start(
    tracker_contract: TrackerContractCase,
) -> None:
    if tracker_contract.kind == "local":
        with pytest.raises(TrackerError, match="no remote to fetch from"):
            tracker_contract.tracker.fetch_epic(7)
        return

    result = tracker_contract.tracker.fetch_epic(7)

    assert result.epic_id == 7
    assert result.epic_dir == state.epic_dir(tracker_contract.project_key, 7)
    assert result.spark_path.read_text(encoding="utf-8").startswith("# Tracker contract\n\n")
    assert result.last_sync_path.is_file()
    assert tracker_contract.tracker.has_sync_state(7) is True


def test_tracker_contract_authority_checks(
    tracker_contract: TrackerContractCase,
) -> None:
    if tracker_contract.kind == "local":
        _write_epic_contract_files(tracker_contract.project_key, 1)
        tracker_contract.tracker.assert_epic_authority(1)
        assert tracker_contract.tracker.has_sync_state(1) is True
        assert tracker_contract.tracker.has_sync_state(99) is False
        with pytest.raises(TrackerError, match="E99 not found"):
            tracker_contract.tracker.assert_epic_authority(99)
        return

    result = tracker_contract.tracker.fetch_epic(7)
    tracker_contract.tracker.assert_epic_authority(7)
    assert tracker_contract.tracker.has_sync_state(7) is True

    payload = json.loads(result.last_sync_path.read_text(encoding="utf-8"))
    payload["issue_number"] = 99
    result.last_sync_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(TrackerError, match="expected 7"):
        tracker_contract.tracker.assert_epic_authority(7)


@pytest.mark.parametrize("decision", CONFLICT_DECISIONS)
def test_tracker_contract_conflict_resolution_decisions(
    tracker_contract: TrackerContractCase,
    decision: str,
) -> None:
    if tracker_contract.kind == "local":
        _write_epic_contract_files(tracker_contract.project_key, 1)
        with pytest.raises(TrackerError, match="no remote"):
            tracker_contract.tracker.resolve_conflict(1, decision)
        return

    epic_id = 7
    epic_dir = state.epic_dir(tracker_contract.project_key, epic_id)
    epic_dir.mkdir(parents=True)
    assert tracker_contract.gh is not None
    remote_body = render_epic_issue_body(
        _epic_front(epic_id),
        "Remote canonical intent.\n",
        remote_body=None,
    )
    tracker_contract.gh.set_remote(
        body=remote_body,
        title="Remote title",
        updated_at="2026-02-01T00:00:00Z",
    )

    result = tracker_contract.tracker.resolve_conflict(epic_id, decision)

    assert result.epic_id == epic_id
    assert result.decision == decision
    last_sync = json.loads(result.last_sync_path.read_text(encoding="utf-8"))
    assert last_sync["issue_number"] == epic_id
    assert last_sync["updated_at"] == "2026-02-01T00:00:00Z"
    assert last_sync["body"] == remote_body
    if decision == "accept_remote":
        assert result.epic_path == epic_dir / "EPIC.md"
        assert result.epic_path is not None
        assert "Remote canonical intent." in result.epic_path.read_text(encoding="utf-8")
    else:
        assert result.epic_path is None


def test_tracker_contract_conflict_resolution_rejects_unknown_decision(
    tracker_contract: TrackerContractCase,
) -> None:
    with pytest.raises(TrackerError, match="unsupported tracker_sync_conflict decision"):
        tracker_contract.tracker.resolve_conflict(1, "overwrite_remote")


def test_tracker_contract_plan_summary_push_renders_stories(
    tracker_contract: TrackerContractCase,
) -> None:
    epic_id = _prepare_contract_epic(tracker_contract)

    result = tracker_contract.tracker.push_plan_summary(epic_id)

    assert result.epic_id == epic_id
    assert result.closed is False
    assert "## Plan Summary" in result.body
    assert "- **S1**" in result.body
    assert "Create comment API" in result.body
    assert "## Closing Summary" not in result.body
    if tracker_contract.kind == "github":
        assert tracker_contract.gh is not None
        assert result.changed is True
        assert tracker_contract.gh.edited_bodies[-1] == result.body
        assert result.last_sync_path.is_file()
    else:
        assert result.changed is False
        assert not result.last_sync_path.exists()


def test_tracker_contract_epic_completion_requires_done_plan(
    tracker_contract: TrackerContractCase,
) -> None:
    epic_id = _prepare_contract_epic(tracker_contract)

    with pytest.raises(TrackerError, match="cannot be closed until all plan work units are done"):
        tracker_contract.tracker.complete_epic(epic_id)


def test_tracker_contract_epic_completion_renders_and_closes(
    tracker_contract: TrackerContractCase,
) -> None:
    epic_id = _prepare_contract_epic(tracker_contract, done=True)

    result = tracker_contract.tracker.complete_epic(epic_id)

    assert result.epic_id == epic_id
    assert result.closed is True
    assert "## Plan Summary" in result.body
    assert "## Closing Summary" in result.body
    assert "Epic completed with 2/2 planned work units done." in result.body
    if tracker_contract.kind == "github":
        assert tracker_contract.gh is not None
        assert result.changed is True
        assert tracker_contract.gh.close_count == 1
        assert tracker_contract.gh.issue["state"] == "closed"
        assert result.last_sync_path.is_file()
    else:
        assert result.changed is False
        assert not result.last_sync_path.exists()


def test_tracker_contract_close_not_delivered_abandons_without_done_guard(
    tracker_contract: TrackerContractCase,
) -> None:
    # abandon_epic's terminal (E17 P4 / D-AB): close the issue as not delivered.
    # Unlike complete_epic there is no all-done guard - the plan still has work.
    epic_id = _prepare_contract_epic(tracker_contract)

    result = tracker_contract.tracker.close_not_delivered(epic_id)

    assert result.epic_id == epic_id
    assert result.closed is True
    if tracker_contract.kind == "github":
        assert tracker_contract.gh is not None
        assert tracker_contract.gh.close_count == 1
        assert tracker_contract.gh.issue["state"] == "closed"
        # Closed with the not-delivered reason, never a successful completion.
        close_calls = [c for c in tracker_contract.gh.calls if c[:2] == ["issue", "close"]]
        assert close_calls and "--reason" in close_calls[-1]
        assert close_calls[-1][close_calls[-1].index("--reason") + 1] == "not planned"
        assert result.last_sync_path.is_file()
    else:
        assert result.changed is False
        assert not result.last_sync_path.exists()


def _github_close_tracker(monkeypatch: pytest.MonkeyPatch, gh: GhCommandStub) -> GitHubTracker:
    monkeypatch.setattr(github_module.subprocess, "run", gh.run)
    state.epic_dir(DEFAULT_PROJECT_KEY, gh.issue_number).mkdir(parents=True)
    return GitHubTracker(DEFAULT_PROJECT_KEY, gh.repo)


def test_close_not_delivered_corrects_already_closed_wrong_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An epic issue already closed as "completed" must be re-closed as
    # not-planned so the tracker stops reading as delivered once the local
    # epic_abandoned terminal is recorded.
    gh = GhCommandStub()
    gh.set_remote(body="Remote body.\n", state="closed", state_reason="completed")
    tracker = _github_close_tracker(monkeypatch, gh)

    result = tracker.close_not_delivered(gh.issue_number)

    close_calls = [c for c in gh.calls if c[:2] == ["issue", "close"]]
    assert len(close_calls) == 1
    assert close_calls[-1][close_calls[-1].index("--reason") + 1] == "not planned"
    assert gh.issue["state_reason"] == "not_planned"
    assert result.changed is True
    assert result.closed is True
    assert result.last_sync_path.is_file()


def test_close_not_delivered_idempotent_when_already_not_planned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Already closed as not-planned: no redundant API write, but the local
    # not-delivered sync is still recorded.
    gh = GhCommandStub()
    gh.set_remote(body="Remote body.\n", state="closed", state_reason="not_planned")
    tracker = _github_close_tracker(monkeypatch, gh)

    result = tracker.close_not_delivered(gh.issue_number)

    assert gh.close_count == 0
    assert [c for c in gh.calls if c[:2] == ["issue", "close"]] == []
    assert gh.issue["state_reason"] == "not_planned"
    assert result.changed is False
    assert result.closed is False
    assert result.last_sync_path.is_file()
    events = [
        json.loads(line)
        for line in state.epic_events_path(DEFAULT_PROJECT_KEY, gh.issue_number)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event"] == "tracker_synced"
    assert events[-1]["not_delivered"] is True


# ---------------------------------------------------------------------------
# local adapter
# ---------------------------------------------------------------------------


def test_local_create_epic_assigns_first_id() -> None:
    _seed_local_tracker()
    tracker = LocalTracker(DEFAULT_PROJECT_KEY)
    result = tracker.create_epic("Build a thing\n\nMore detail.")

    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 1)
    assert result.epic_id == 1
    assert result.epic_ref == epic_dir.as_posix()
    assert (epic_dir / "spark.md").read_text() == "# Build a thing\n\nMore detail.\n"
    assert state.current_epic_path(DEFAULT_PROJECT_KEY).read_text() == "E1\n"
    assert not state.last_sync_path(DEFAULT_PROJECT_KEY, 1).exists()
    events = [json.loads(line) for line in (epic_dir / "epic.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["spark_created", "current_epic_selected"]
    assert events[0]["source"] == "local"


def test_local_create_epic_increments_past_existing() -> None:
    _seed_local_tracker()
    state.epic_dir(DEFAULT_PROJECT_KEY, 4).mkdir(parents=True)
    state.epic_dir(DEFAULT_PROJECT_KEY, 2).mkdir(parents=True)
    result = LocalTracker(DEFAULT_PROJECT_KEY).create_epic("Another epic")
    assert result.epic_id == 5


def test_local_fetch_epic_fails_loud() -> None:
    with pytest.raises(TrackerError, match="no remote"):
        LocalTracker(DEFAULT_PROJECT_KEY).fetch_epic(7)


def test_local_resolve_conflict_fails_loud() -> None:
    with pytest.raises(TrackerError, match="no remote"):
        LocalTracker(DEFAULT_PROJECT_KEY).resolve_conflict(1, "keep_local")


def test_local_runtime_check_noop_and_authority_uses_epic_directory() -> None:
    tracker = LocalTracker(DEFAULT_PROJECT_KEY)
    tracker.assert_runtime_reachable()
    _write_epic_contract_files(DEFAULT_PROJECT_KEY, 1)
    tracker.assert_epic_authority(1)
    assert tracker.has_sync_state(1) is True
    assert tracker.has_sync_state(2) is False


def test_local_push_operations_keep_everything_local() -> None:
    tracker = LocalTracker(DEFAULT_PROJECT_KEY)
    definition = tracker.push_epic_definition(1, _epic_front(), "Intent prose.\n")
    assert definition.changed is False
    assert "## Observable Outcomes" in definition.body

    _write_epic_contract_files(DEFAULT_PROJECT_KEY, 1, done=True)
    plan_summary = tracker.push_plan_summary(1)
    assert plan_summary.changed is False
    assert plan_summary.closed is False
    assert "## Plan Summary" in plan_summary.body

    completion = tracker.complete_epic(1)
    assert completion.changed is False
    assert completion.closed is True
    assert "## Closing Summary" in completion.body


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


def test_github_tracker_gh_helpers_use_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        assert argv[0] == "gh"
        assert kwargs["timeout"] == GITHUB_COMMAND_TIMEOUT_SECONDS
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        if argv[1:3] == ["api", "/repos/acme/widgets/issues/3"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "number": 3,
                        "title": "Issue",
                        "body": "Body",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                ),
                "",
            )
        if argv[1:3] == ["issue", "create"]:
            assert kwargs["input"] == "Body\n"
            return subprocess.CompletedProcess(
                argv, 0, "https://github.com/acme/widgets/issues/3\n", ""
            )
        if argv[1:3] == ["issue", "edit"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:3] == ["issue", "close"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected gh argv: {argv}")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)
    tracker = GitHubTracker(DEFAULT_PROJECT_KEY, "acme/widgets")

    assert tracker._fetch_issue(3)["number"] == 3
    assert tracker._create_issue(title="Title", body="Body\n").endswith("/issues/3")
    tracker._edit_issue_body(3, "Updated body.\n")
    tracker._close_issue(3)

    assert [call[0][1:3] for call in calls] == [
        ["api", "/repos/acme/widgets/issues/3"],
        ["issue", "create"],
        ["issue", "edit"],
        ["issue", "close"],
    ]


def test_github_tracker_timeout_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(github_module.subprocess, "run", timeout_run)

    with pytest.raises(TrackerError, match="timed out after 20s"):
        GitHubTracker(DEFAULT_PROJECT_KEY, "acme/widgets")._fetch_issue(3)


def test_github_create_epic_closes_created_issue_when_local_initialisation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state.epic_dir(DEFAULT_PROJECT_KEY, 88).mkdir(parents=True)
    tracker = GitHubTracker(DEFAULT_PROJECT_KEY, "acme/widgets")
    closed: list[int] = []

    monkeypatch.setattr(
        tracker,
        "_create_issue",
        lambda *, title, body: "https://github.com/acme/widgets/issues/88",
    )
    monkeypatch.setattr(
        tracker,
        "_fetch_issue",
        lambda epic_id: {
            "number": epic_id,
            "title": "New keyboard flow",
            "body": "Body",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(tracker, "_close_issue", lambda epic_id: closed.append(epic_id))

    with pytest.raises(TrackerError, match="Closed the newly created issue"):
        tracker.create_epic("New keyboard flow")

    assert closed == [88]
    assert not state.current_epic_path(DEFAULT_PROJECT_KEY).exists()


# ---------------------------------------------------------------------------
# local adapter end-to-end through the CLI
# ---------------------------------------------------------------------------


def test_woof_wf_new_local_tracker_never_calls_gh(tmp_path: Path) -> None:
    """`woof wf new` with kind=local must create the epic without touching gh."""
    project = tmp_path / "project"
    _seed_local_tracker()
    _git_init(project)
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
        "WOOF_HOME": os.environ["WOOF_HOME"],
        "WOOF_PROJECT": os.environ["WOOF_PROJECT"],
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
    assert payload["next_command"] == "woof wf --epic 1"
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 1)
    assert payload["epic_ref"] == epic_dir.as_posix()
    assert (epic_dir / "spark.md").read_text() == "# Portable epic\n\nRuns without GitHub.\n"
    assert state.current_epic_path(DEFAULT_PROJECT_KEY).read_text() == "E1\n"
    # The engine leaves no trace in the driven repo.
    assert not (project / ".woof").exists()


def test_woof_wf_intake_predecomposed_work_units_without_epic(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _seed_local_tracker(PROFILE_A_OVERRIDES)
    _git_init(project)
    source = project / "backlog.md"
    source.write_text(
        textwrap.dedent(
            """\
            ---
            schema_version: 1
            type: backlog
            project_ref: woof
            status: active
            work_units:
              - id: foundation
                title: Foundation
                kind: build
                state: todo
                priority: high
                summary: Build the foundation.
                acceptance:
                  - Foundation is testable.
                deps: []
              - id: follow-up
                title: Follow up
                kind: build
                state: todo
                priority: medium
                summary: Build on the foundation.
                deps: [foundation]
            ---
            # Backlog
            """
        )
    )

    first = subprocess.run(
        [str(WOOF_BIN), "wf", "intake", "--source", str(source), "--format", "json"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [str(WOOF_BIN), "wf", "intake", "--source", str(source), "--format", "json"],
        cwd=project,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["context"] == second_payload["context"]
    assert first_payload["context"]["kind"] == "work_unit_set"
    assert first_payload["context"]["project_ref"] == "woof"
    assert first_payload["work_unit_count"] == 2
    directory = Path(first_payload["directory"])
    assert directory.is_relative_to(state.project_state_root(DEFAULT_PROJECT_KEY))
    assert not (directory / "EPIC.md").exists()

    plan = json.loads(Path(first_payload["paths"][0]).read_text())
    assert "epic_id" not in plan
    assert plan["context"] == first_payload["context"]
    assert [unit["id"] for unit in plan["work_units"]] == ["foundation", "follow-up"]
    assert plan["work_units"][0]["state"] == "pending"
    assert plan["work_units"][0]["paths"] == ["**/*"]

    metadata = json.loads(Path(first_payload["paths"][2]).read_text())
    assert metadata["kind"] == "pre_decomposed_work_units"
    assert metadata["qualified_work_unit_refs"][1] == {
        "context": first_payload["context"],
        "work_unit_id": "follow-up",
    }
    assert metadata["worktrees"] == {
        "derivation": "unit_id",
        "root": "worktrees",
        "unit_paths": {
            "foundation": "worktrees/foundation",
            "follow-up": "worktrees/follow-up",
        },
    }
