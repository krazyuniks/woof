from __future__ import annotations

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
) -> ReadyPullRequest:
    return ReadyPullRequest(
        work_unit_id=work_unit_id,
        pr_number=pr_number,
        head_ref=work_unit_id,
        base_ref="main",
        head_sha=head_sha or f"old-{pr_number}",
        worktree=Path(f"/tmp/{work_unit_id}"),
        ready_at=ready_at,
    )


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
