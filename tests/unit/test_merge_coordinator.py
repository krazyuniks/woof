from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from woof.graph.merge import (
    MergeQueueHalt,
    ReadyPullRequest,
    SerialMergeCoordinator,
)


@dataclass
class FakeGit:
    heads: dict[int, str]
    calls: list[str] = field(default_factory=list)
    fail_rebase_for: set[int] = field(default_factory=set)

    def fetch(self, repo: Path, remote: str) -> None:
        self.calls.append(f"fetch:{repo.name}:{remote}")

    def rebase(self, repo: Path, onto: str, pr: ReadyPullRequest) -> bool:
        self.calls.append(f"rebase:{pr.work_unit_id}:{onto}")
        return pr.pr_number not in self.fail_rebase_for

    def head_sha(self, repo: Path, pr: ReadyPullRequest) -> str:
        self.calls.append(f"head:{pr.work_unit_id}")
        return self.heads[pr.pr_number]

    def force_push_with_lease(
        self, repo: Path, remote: str, branch: str, expected_sha: str
    ) -> None:
        self.calls.append(f"push:{branch}:{expected_sha}")

    def restore_original_head(self, repo: Path, pr: ReadyPullRequest) -> None:
        self.calls.append(f"restore-head:{pr.work_unit_id}:{pr.head_sha}")

    def restore_remote_branch(
        self,
        repo: Path,
        remote: str,
        branch: str,
        original_sha: str,
        expected_remote_sha: str,
    ) -> None:
        self.calls.append(f"restore-branch:{branch}:{original_sha}:{expected_remote_sha}")


@dataclass
class FakeGithub:
    mergeability: dict[int, list[str]]
    already_merged: set[int] = field(default_factory=set)
    fail_merge_for: set[int] = field(default_factory=set)
    transient_merge_failures: dict[int, int] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def pr_mergeability(self, repo_slug: str, pr_number: int) -> str:
        self.calls.append(f"mergeability:{pr_number}")
        values = self.mergeability.setdefault(pr_number, ["MERGEABLE"])
        value = values[0] if len(values) == 1 else values.pop(0)
        if value == "RAISE":
            raise subprocess.CalledProcessError(1, ["gh", "pr", "view", str(pr_number)])
        return value

    def squash_merge(
        self, repo_slug: str, pr_number: int, head_sha: str, *, subject: str, body: str
    ) -> None:
        self.calls.append(f"merge:{pr_number}:{head_sha}")
        if pr_number in self.fail_merge_for:
            raise subprocess.CalledProcessError(1, ["gh", "pr", "merge", str(pr_number)])
        remaining_failures = self.transient_merge_failures.get(pr_number, 0)
        if remaining_failures > 0:
            self.transient_merge_failures[pr_number] = remaining_failures - 1
            raise subprocess.CalledProcessError(1, ["gh", "pr", "merge", str(pr_number)])
        self.already_merged.add(pr_number)

    def is_pr_merged(self, repo_slug: str, pr_number: int) -> bool:
        self.calls.append(f"is-merged:{pr_number}")
        return pr_number in self.already_merged


@dataclass
class FakeGate:
    failing: set[int] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def __call__(self, pr: ReadyPullRequest) -> bool:
        self.calls.append(f"gate:{pr.work_unit_id}")
        return pr.pr_number not in self.failing


def _ready(
    work_unit_id: str,
    pr_number: int,
    *,
    ready_at: str,
    head_sha: str | None = None,
    changed_paths: tuple[str, ...] = (),
) -> ReadyPullRequest:
    return ReadyPullRequest(
        work_unit_id=work_unit_id,
        pr_number=pr_number,
        head_ref=work_unit_id,
        base_ref="main",
        head_sha=head_sha or f"old-{pr_number}",
        worktree=Path(f"/tmp/{work_unit_id}"),
        ready_at=ready_at,
        changed_paths=changed_paths,
    )


def _sibling_conflict_events(repo_root: Path) -> list[dict[str, object]]:
    corpus = repo_root / ".woof" / "sibling-conflicts.jsonl"
    if not corpus.exists():
        return []
    return [json.loads(line) for line in corpus.read_text().splitlines() if line.strip()]


def test_ready_queue_rebases_gates_merges_and_marks_done_in_fifo_order(tmp_path: Path) -> None:
    first = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    second = _ready("S2", 11, ready_at="2026-07-08T10:01:00Z")
    git = FakeGit({10: "rebased-10", 11: "rebased-11"})
    github = FakeGithub({10: ["MERGEABLE"], 11: ["MERGEABLE"]})
    gate = FakeGate()
    marked_done: list[str] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=gate,
        mark_done=marked_done.append,
        sleep=lambda _seconds: None,
    )

    result = coordinator.process([second, first])

    assert [outcome.work_unit_id for outcome in result.outcomes] == ["S1", "S2"]
    assert [outcome.action for outcome in result.outcomes] == ["merged", "merged"]
    assert marked_done == ["S1", "S2"]
    assert git.calls == [
        "fetch:S1:origin",
        "rebase:S1:origin/main",
        "head:S1",
        "push:S1:old-10",
        "fetch:S2:origin",
        "rebase:S2:origin/main",
        "head:S2",
        "push:S2:old-11",
    ]
    assert gate.calls == ["gate:S1", "gate:S2"]
    assert github.calls == [
        "is-merged:10",
        "mergeability:10",
        "merge:10:rebased-10",
        "is-merged:11",
        "mergeability:11",
        "merge:11:rebased-11",
    ]


def test_partial_merge_reconciliation_marks_prior_merged_units_before_terminal_halt(
    tmp_path: Path,
) -> None:
    first = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    second = _ready("S2", 11, ready_at="2026-07-08T10:01:00Z")
    git = FakeGit({11: "rebased-11"})
    github = FakeGithub({11: ["MERGEABLE"]}, already_merged={10})
    gate = FakeGate(failing={11})
    marked_done: list[str] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=gate,
        mark_done=marked_done.append,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(MergeQueueHalt) as excinfo:
        coordinator.process([second, first])

    assert marked_done == ["S1"]
    assert excinfo.value.outcomes[0].action == "already_merged"
    assert excinfo.value.outcomes[1].action == "gate_failed"
    assert excinfo.value.outcomes[1].terminal is True


@pytest.mark.parametrize(
    ("failure", "expected_action"),
    [
        ("rebase", "rebase_conflict"),
        ("mergeability", "mergeability_failed"),
        ("merge", "merge_failed"),
    ],
)
def test_partial_merge_reconciliation_runs_before_each_terminal_halt(
    tmp_path: Path, failure: str, expected_action: str
) -> None:
    first = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    second = _ready("S2", 11, ready_at="2026-07-08T10:01:00Z")
    git = FakeGit(
        {11: "rebased-11"},
        fail_rebase_for={11} if failure == "rebase" else set(),
    )
    github = FakeGithub(
        {11: ["DIRTY"] if failure == "mergeability" else ["MERGEABLE"]},
        already_merged={10},
        fail_merge_for={11} if failure == "merge" else set(),
    )
    gate = FakeGate()
    marked_done: list[str] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=gate,
        mark_done=marked_done.append,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(MergeQueueHalt) as excinfo:
        coordinator.process([second, first])

    assert marked_done == ["S1"]
    assert [outcome.action for outcome in excinfo.value.outcomes] == [
        "already_merged",
        expected_action,
    ]
    assert excinfo.value.outcomes[1].terminal is True


def test_rebase_conflict_halts_without_gate_head_or_force_push(tmp_path: Path) -> None:
    pr = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    git = FakeGit({10: "rebased-10"}, fail_rebase_for={10})
    github = FakeGithub({10: ["MERGEABLE"]})
    gate = FakeGate()

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=gate,
        mark_done=lambda _work_unit_id: None,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(MergeQueueHalt) as excinfo:
        coordinator.process([pr])

    assert excinfo.value.outcome.action == "rebase_conflict"
    assert git.calls == ["fetch:S1:origin", "rebase:S1:origin/main"]
    assert gate.calls == []
    assert "mergeability:10" not in github.calls
    assert "merge:10:rebased-10" not in github.calls


def test_rebase_conflict_opens_durable_sibling_gate_and_is_idempotent(
    tmp_path: Path,
) -> None:
    pr = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    marked_done: list[str] = []

    for _ in range(2):
        coordinator = SerialMergeCoordinator(
            repo_root=tmp_path,
            epic_id=5,
            repo_slug="example/project",
            base_branch="main",
            ready_label="ready",
            git=FakeGit({10: "rebased-10"}, fail_rebase_for={10}),
            github=FakeGithub({10: ["MERGEABLE"]}),
            gate=FakeGate(),
            mark_done=marked_done.append,
            sleep=lambda _seconds: None,
        )
        with pytest.raises(MergeQueueHalt):
            coordinator.process([pr])

    gate_path = tmp_path / ".woof" / "epics" / "E5" / "gate.md"
    assert gate_path.exists()
    gate_text = gate_path.read_text()
    assert "sibling_conflict" in gate_text
    assert "work_unit_id: S1" in gate_text

    epic_events = [
        json.loads(line)
        for line in (tmp_path / ".woof" / "epics" / "E5" / "epic.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [event["event"] for event in epic_events] == ["work_unit_gate_opened"]

    events = _sibling_conflict_events(tmp_path)
    assert len(events) == 1
    assert events[0]["detection_trigger"] == "rebase_conflict"
    assert events[0]["resolution_outcome"] == "human_gate_opened"
    assert marked_done == []


def test_conflicting_mergeability_restores_branch_and_opens_sibling_gate(
    tmp_path: Path,
) -> None:
    first = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    second = _ready("S2", 11, ready_at="2026-07-08T10:01:00Z")
    git = FakeGit({11: "rebased-11"})
    github = FakeGithub({11: ["UNKNOWN", "CONFLICTING"]}, already_merged={10})
    marked_done: list[str] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=FakeGate(),
        mark_done=marked_done.append,
        mergeability_attempts=2,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(MergeQueueHalt) as excinfo:
        coordinator.process([second, first])

    assert marked_done == ["S1"]
    assert excinfo.value.outcome.action == "mergeability_failed"
    assert "restore-branch:S2:old-11:rebased-11" in git.calls
    assert "merge:11:rebased-11" not in github.calls
    assert _sibling_conflict_events(tmp_path)[0]["detection_trigger"] == (
        "mergeability_conflicting"
    )


def test_gate_failure_with_merged_sibling_path_overlap_opens_sibling_gate(
    tmp_path: Path,
) -> None:
    first = _ready(
        "S1",
        10,
        ready_at="2026-07-08T10:00:00Z",
        changed_paths=("src/shared.py",),
    )
    second = _ready(
        "S2",
        11,
        ready_at="2026-07-08T10:01:00Z",
        changed_paths=("src/shared.py", "tests/test_shared.py"),
    )
    git = FakeGit({11: "rebased-11"})
    github = FakeGithub({11: ["MERGEABLE"]}, already_merged={10})

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=FakeGate(failing={11}),
        mark_done=lambda _work_unit_id: None,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(MergeQueueHalt) as excinfo:
        coordinator.process([second, first])

    assert excinfo.value.outcome.action == "gate_failed"
    assert "restore-head:S2:old-11" in git.calls
    assert "push:S2:old-11" not in git.calls
    event = _sibling_conflict_events(tmp_path)[0]
    assert event["detection_trigger"] == "gate_failed_after_rebase"
    assert event["overlapping_paths"] == ["src/shared.py"]
    assert event["merged_siblings"] == [{"work_unit_id": "S1", "pr_number": 10}]


def test_queued_sibling_path_overlap_does_not_preempt_merge(tmp_path: Path) -> None:
    first = _ready(
        "S1",
        10,
        ready_at="2026-07-08T10:00:00Z",
        changed_paths=("src/shared.py",),
    )
    second = _ready(
        "S2",
        11,
        ready_at="2026-07-08T10:01:00Z",
        changed_paths=("src/shared.py",),
    )
    git = FakeGit({10: "rebased-10", 11: "rebased-11"})
    github = FakeGithub({10: ["MERGEABLE"], 11: ["MERGEABLE"]})

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=FakeGate(),
        mark_done=lambda _work_unit_id: None,
        sleep=lambda _seconds: None,
    )

    result = coordinator.process([second, first])

    assert [outcome.action for outcome in result.outcomes] == ["merged", "merged"]
    assert not _sibling_conflict_events(tmp_path)


def test_unknown_and_unstable_mergeability_settle_with_bounded_retry(tmp_path: Path) -> None:
    pr = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    git = FakeGit({10: "rebased-10"})
    github = FakeGithub({10: ["UNKNOWN", "UNSTABLE", "MERGEABLE"]})
    gate = FakeGate()
    sleeps: list[float] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=gate,
        mark_done=lambda _work_unit_id: None,
        mergeability_attempts=3,
        mergeability_interval_s=2.0,
        sleep=sleeps.append,
    )

    result = coordinator.process([pr])

    assert result.outcomes[0].action == "merged"
    assert github.calls.count("mergeability:10") == 3
    assert sleeps == [2.0, 2.0]


def test_mergeability_command_failure_consumes_retry_budget_then_merges(
    tmp_path: Path,
) -> None:
    pr = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    git = FakeGit({10: "rebased-10"})
    github = FakeGithub({10: ["RAISE", "MERGEABLE"]})
    sleeps: list[float] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=FakeGate(),
        mark_done=lambda _work_unit_id: None,
        mergeability_attempts=2,
        mergeability_interval_s=2.0,
        sleep=sleeps.append,
    )

    result = coordinator.process([pr])

    assert result.outcomes[0].action == "merged"
    assert github.calls.count("mergeability:10") == 2
    assert sleeps == [2.0]


def test_persistent_mergeability_command_failure_waits_after_reconciling_prior_merge(
    tmp_path: Path,
) -> None:
    first = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    second = _ready("S2", 11, ready_at="2026-07-08T10:01:00Z")
    git = FakeGit({11: "rebased-11"})
    github = FakeGithub({11: ["RAISE"]}, already_merged={10})
    marked_done: list[str] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=FakeGate(),
        mark_done=marked_done.append,
        mergeability_attempts=2,
        sleep=lambda _seconds: None,
    )

    result = coordinator.process([second, first])

    assert marked_done == ["S1"]
    assert [outcome.action for outcome in result.outcomes] == ["already_merged", "waiting"]
    assert result.outcomes[1].terminal is False
    assert github.calls.count("mergeability:11") == 2


def test_unsettled_transient_mergeability_waits_without_halt_or_deploy_pacing(
    tmp_path: Path,
) -> None:
    pr = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    git = FakeGit({10: "rebased-10"})
    github = FakeGithub({10: ["UNKNOWN", "UNKNOWN"]})
    gate = FakeGate()
    marked_done: list[str] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=gate,
        mark_done=marked_done.append,
        mergeability_attempts=2,
        sleep=lambda _seconds: None,
    )

    result = coordinator.process([pr])

    assert result.outcomes[0].action == "waiting"
    assert result.outcomes[0].terminal is False
    assert marked_done == []
    assert "merge:10:rebased-10" not in github.calls


def test_transient_squash_merge_refusal_retries_then_merges(tmp_path: Path) -> None:
    pr = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    git = FakeGit({10: "rebased-10"})
    github = FakeGithub({10: ["MERGEABLE"]}, transient_merge_failures={10: 2})
    sleeps: list[float] = []
    marked_done: list[str] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=FakeGate(),
        mark_done=marked_done.append,
        merge_attempts=3,
        merge_interval_s=2.0,
        sleep=sleeps.append,
    )

    result = coordinator.process([pr])

    assert result.outcomes[0].action == "merged"
    assert marked_done == ["S1"]
    assert github.calls.count("merge:10:rebased-10") == 3
    assert git.calls.count("fetch:S1:origin") == 3
    assert sleeps == [2.0, 2.0]


def test_persistent_squash_merge_refusal_halts_after_retry_budget(tmp_path: Path) -> None:
    pr = _ready("S1", 10, ready_at="2026-07-08T10:00:00Z")
    git = FakeGit({10: "rebased-10"})
    github = FakeGithub({10: ["MERGEABLE"]}, fail_merge_for={10})
    sleeps: list[float] = []
    marked_done: list[str] = []

    coordinator = SerialMergeCoordinator(
        repo_root=tmp_path,
        epic_id=5,
        repo_slug="example/project",
        base_branch="main",
        ready_label="ready",
        git=git,
        github=github,
        gate=FakeGate(),
        mark_done=marked_done.append,
        merge_attempts=3,
        merge_interval_s=2.0,
        sleep=sleeps.append,
    )

    with pytest.raises(MergeQueueHalt) as excinfo:
        coordinator.process([pr])

    assert excinfo.value.outcome.action == "merge_failed"
    assert excinfo.value.outcome.terminal is True
    assert marked_done == []
    assert github.calls.count("merge:10:rebased-10") == 3
    assert git.calls.count("fetch:S1:origin") == 3
    assert sleeps == [2.0, 2.0]
