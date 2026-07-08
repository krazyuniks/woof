"""Serial Profile A merge queue coordination."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from woof.graph.git import git, git_env, head_sha

MergeAction = Literal[
    "merged",
    "already_merged",
    "waiting",
    "gate_failed",
    "rebase_conflict",
    "mergeability_failed",
    "merge_failed",
]

_TRANSIENT_MERGEABILITY = {"UNKNOWN", "UNSTABLE"}
_MERGEABLE = {"CLEAN", "HAS_HOOKS", "MERGEABLE"}


@dataclass(frozen=True)
class ReadyPullRequest:
    """A Profile A pull request admitted to the serial ready queue."""

    work_unit_id: str
    pr_number: int
    head_ref: str
    base_ref: str
    head_sha: str
    worktree: Path
    ready_at: str


@dataclass(frozen=True)
class MergeOutcome:
    work_unit_id: str
    pr_number: int
    action: MergeAction
    detail: str
    terminal: bool = False


@dataclass(frozen=True)
class MergeQueueResult:
    outcomes: list[MergeOutcome] = field(default_factory=list)


class MergeQueueHalt(RuntimeError):
    """Raised when the serial queue reaches a terminal PR failure."""

    def __init__(self, outcome: MergeOutcome, outcomes: list[MergeOutcome]) -> None:
        super().__init__(outcome.detail)
        self.outcome = outcome
        self.outcomes = list(outcomes)


class GitMergeOps(Protocol):
    def fetch(self, repo: Path, remote: str) -> None: ...

    def rebase(self, repo: Path, onto: str, pr: ReadyPullRequest) -> bool: ...

    def head_sha(self, repo: Path, pr: ReadyPullRequest) -> str: ...

    def force_push_with_lease(
        self, repo: Path, remote: str, branch: str, expected_sha: str
    ) -> None: ...


class GithubMergeOps(Protocol):
    def pr_mergeability(self, repo_slug: str, pr_number: int) -> str: ...

    def squash_merge(
        self, repo_slug: str, pr_number: int, head_sha: str, *, subject: str, body: str
    ) -> None: ...

    def is_pr_merged(self, repo_slug: str, pr_number: int) -> bool: ...


class DefaultGitMergeOps:
    """Git implementation for the serial merge coordinator."""

    def fetch(self, repo: Path, remote: str) -> None:
        git(repo, "fetch", remote)

    def rebase(self, repo: Path, onto: str, pr: ReadyPullRequest) -> bool:
        result = git(repo, "rebase", onto, check=False)
        if result.returncode == 0:
            return True
        git(repo, "rebase", "--abort", check=False)
        git(repo, "reset", "--hard", pr.head_sha)
        return False

    def head_sha(self, repo: Path, pr: ReadyPullRequest) -> str:
        current = head_sha(repo)
        if current is None:
            raise RuntimeError(f"{repo} has no readable HEAD")
        return current

    def force_push_with_lease(
        self, repo: Path, remote: str, branch: str, expected_sha: str
    ) -> None:
        git(
            repo,
            "push",
            f"--force-with-lease={branch}:{expected_sha}",
            remote,
            f"HEAD:{branch}",
        )


class DefaultGithubMergeOps:
    """GitHub CLI implementation for the serial merge coordinator."""

    def pr_mergeability(self, repo_slug: str, pr_number: int) -> str:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repo_slug,
                "--json",
                "mergeStateStatus",
            ],
            env=git_env(),
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout or "{}")
        if not isinstance(payload, dict):
            return "UNKNOWN"
        state = payload.get("mergeStateStatus")
        return str(state or "UNKNOWN").upper()

    def squash_merge(
        self, repo_slug: str, pr_number: int, head_sha: str, *, subject: str, body: str
    ) -> None:
        subprocess.run(
            [
                "gh",
                "pr",
                "merge",
                str(pr_number),
                "--repo",
                repo_slug,
                "--squash",
                "--delete-branch",
                "--match-head-commit",
                head_sha,
                "--subject",
                subject,
                "--body",
                body,
            ],
            env=git_env(),
            capture_output=True,
            text=True,
            check=True,
        )

    def is_pr_merged(self, repo_slug: str, pr_number: int) -> bool:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repo_slug,
                "--json",
                "state,mergedAt",
            ],
            env=git_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return False
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        return payload.get("state") == "MERGED" or bool(payload.get("mergedAt"))


def fifo_ready_queue(prs: list[ReadyPullRequest]) -> list[ReadyPullRequest]:
    """Return ready PRs in stable FIFO order."""

    return sorted(prs, key=lambda pr: (pr.ready_at, pr.pr_number))


class SerialMergeCoordinator:
    """Serially rebase, gate, and merge ready Profile A pull requests."""

    def __init__(
        self,
        *,
        repo_root: Path,
        epic_id: int,
        repo_slug: str,
        base_branch: str,
        ready_label: str,
        git: GitMergeOps | None = None,
        github: GithubMergeOps | None = None,
        gate: Callable[[ReadyPullRequest], bool],
        mark_done: Callable[[str], None],
        remote: str = "origin",
        mergeability_attempts: int = 5,
        mergeability_interval_s: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.repo_root = repo_root
        self.epic_id = epic_id
        self.repo_slug = repo_slug
        self.base_branch = base_branch
        self.ready_label = ready_label
        self.git = git or DefaultGitMergeOps()
        self.github = github or DefaultGithubMergeOps()
        self.gate = gate
        self.mark_done = mark_done
        self.remote = remote
        self.mergeability_attempts = max(1, mergeability_attempts)
        self.mergeability_interval_s = mergeability_interval_s
        self.sleep = sleep
        self._marked_done: set[str] = set()
        self._recorded_outcomes: set[tuple[str, int, MergeAction]] = set()

    def process(self, prs: list[ReadyPullRequest]) -> MergeQueueResult:
        """Process the ready queue until it drains, waits, or halts terminally."""

        outcomes: list[MergeOutcome] = []
        ordered = fifo_ready_queue(prs)
        for pr in ordered:
            if self._pr_already_merged(pr):
                outcome = self._mark_done(pr, "already_merged", "PR is already merged")
                self._append_outcome(outcomes, outcome)
                continue

            repo = pr.worktree
            self.git.fetch(repo, self.remote)
            if not self.git.rebase(repo, f"{self.remote}/{pr.base_ref or self.base_branch}", pr):
                self._halt(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "rebase_conflict",
                        "rebase onto the moved base conflicted",
                        terminal=True,
                    ),
                )

            if not self.gate(pr):
                self._halt(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "gate_failed",
                        "gate failed after rebase; PR was not merged",
                        terminal=True,
                    ),
                )

            rebased_head = self.git.head_sha(repo, pr)
            self.git.force_push_with_lease(repo, self.remote, pr.head_ref, pr.head_sha)
            mergeability = self._settle_mergeability(pr)
            if mergeability == "transient":
                self._append_outcome(
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "waiting",
                        "mergeability did not settle inside the retry budget",
                        terminal=False,
                    ),
                )
                return MergeQueueResult(outcomes)
            if mergeability != "mergeable":
                self._halt(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "mergeability_failed",
                        f"mergeability is terminal: {mergeability}",
                        terminal=True,
                    ),
                )

            try:
                self.github.squash_merge(
                    self.repo_slug,
                    pr.pr_number,
                    rebased_head,
                    subject=f"{pr.work_unit_id}: merge PR #{pr.pr_number}",
                    body=f"Refs #{pr.pr_number}",
                )
            except subprocess.CalledProcessError as exc:
                self._halt(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "merge_failed",
                        f"merge command failed: {exc}",
                        terminal=True,
                    ),
                )

            self._append_outcome(
                outcomes,
                self._mark_done(pr, "merged", "merged after rebase and gate"),
            )
        return MergeQueueResult(outcomes)

    def _settle_mergeability(self, pr: ReadyPullRequest) -> Literal["mergeable", "transient"] | str:
        for attempt in range(self.mergeability_attempts):
            state = self.github.pr_mergeability(self.repo_slug, pr.pr_number).upper()
            if state in _MERGEABLE:
                return "mergeable"
            if state not in _TRANSIENT_MERGEABILITY:
                return state
            if attempt < self.mergeability_attempts - 1:
                self.sleep(self.mergeability_interval_s)
        return "transient"

    def _pr_already_merged(self, pr: ReadyPullRequest) -> bool:
        return self.github.is_pr_merged(self.repo_slug, pr.pr_number)

    def _mark_done(self, pr: ReadyPullRequest, action: MergeAction, detail: str) -> MergeOutcome:
        if pr.work_unit_id not in self._marked_done:
            self.mark_done(pr.work_unit_id)
            self._marked_done.add(pr.work_unit_id)
        return MergeOutcome(pr.work_unit_id, pr.pr_number, action, detail)

    def _reconcile_merged(self, prs: list[ReadyPullRequest], outcomes: list[MergeOutcome]) -> None:
        for pr in prs:
            if pr.work_unit_id in self._marked_done:
                continue
            if self._pr_already_merged(pr):
                self._append_outcome(
                    outcomes,
                    self._mark_done(pr, "already_merged", "PR is already merged"),
                )

    def _append_outcome(self, outcomes: list[MergeOutcome], outcome: MergeOutcome) -> None:
        key = (outcome.work_unit_id, outcome.pr_number, outcome.action)
        if key in self._recorded_outcomes:
            return
        outcomes.append(outcome)
        self._recorded_outcomes.add(key)

    def _halt(
        self,
        prs: list[ReadyPullRequest],
        outcomes: list[MergeOutcome],
        outcome: MergeOutcome,
    ) -> None:
        self._reconcile_merged(prs, outcomes)
        self._append_outcome(outcomes, outcome)
        raise MergeQueueHalt(outcome, outcomes)
