"""Serial Profile A merge queue coordination."""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Self

from woof.gate.write import iso_utc, write_gate
from woof.graph.git import git, git_env, head_sha
from woof.project_config import load_project_config

MergeAction = Literal[
    "merged",
    "already_merged",
    "waiting",
    "gate_failed",
    "rebase_conflict",
    "mergeability_failed",
    "merge_failed",
    "checks_failed",
    "deploy_failed",
    "deploy_lock_contention",
]

_TRANSIENT_MERGEABILITY = {"UNKNOWN", "UNSTABLE"}
_MERGEABLE = {"CLEAN", "HAS_HOOKS", "MERGEABLE"}
_MERGE_MATCH_HEAD_ATTEMPTS = 5
_MERGE_MATCH_HEAD_INTERVAL_S = 3.0
_SIBLING_CONFLICT_TRIGGER = "sibling_conflict"
_MERGED_ACTIONS: set[MergeAction] = {"merged", "already_merged"}
_SUCCESSFUL_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}
_TERMINAL_CHECK_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "neutral",
    "skipped",
    "success",
    "timed_out",
}
_STATE_LOCK_MARKERS = (
    "state lock",
    "state-lock",
    "terraform lock",
    "error acquiring the state lock",
    "lock info",
    "conditionalcheckfailedexception",
)


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
    changed_paths: tuple[str, ...] = ()
    artefact_lineage: dict[str, str] = field(default_factory=dict)


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


@dataclass(frozen=True)
class CheckRunState:
    name: str
    status: str
    conclusion: str | None = None
    details: str = ""


@dataclass(frozen=True)
class ProfileAMergePolicy:
    github_repo: str
    ready_label: str
    base_branch: str
    terminal_deploy_checks: tuple[str, ...]
    mergeability_settle_timeout_s: int = 15
    deploy_wait_timeout_s: int = 300
    merge_attempts: int = _MERGE_MATCH_HEAD_ATTEMPTS
    merge_interval_s: float = _MERGE_MATCH_HEAD_INTERVAL_S

    @property
    def mergeability_attempts(self) -> int:
        return max(1, math.ceil(self.mergeability_settle_timeout_s / self.merge_interval_s))


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

    def restore_original_head(self, repo: Path, pr: ReadyPullRequest) -> None: ...

    def restore_remote_branch(
        self,
        repo: Path,
        remote: str,
        branch: str,
        original_sha: str,
        expected_remote_sha: str,
    ) -> None: ...


class GithubMergeOps(Protocol):
    def pr_mergeability(self, repo_slug: str, pr_number: int) -> str: ...

    def squash_merge(
        self, repo_slug: str, pr_number: int, head_sha: str, *, subject: str, body: str
    ) -> None: ...

    def is_pr_merged(self, repo_slug: str, pr_number: int) -> bool: ...

    def check_run_states(
        self, repo_slug: str, ref: str, check_names: tuple[str, ...]
    ) -> tuple[CheckRunState, ...]: ...


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

    def restore_original_head(self, repo: Path, pr: ReadyPullRequest) -> None:
        git(repo, "reset", "--hard", pr.head_sha)

    def restore_remote_branch(
        self,
        repo: Path,
        remote: str,
        branch: str,
        original_sha: str,
        expected_remote_sha: str,
    ) -> None:
        git(repo, "reset", "--hard", original_sha)
        git(
            repo,
            "push",
            f"--force-with-lease={branch}:{expected_remote_sha}",
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

    def check_run_states(
        self, repo_slug: str, ref: str, check_names: tuple[str, ...]
    ) -> tuple[CheckRunState, ...]:
        proc = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo_slug}/commits/{ref}/check-runs",
                "--paginate",
                "--jq",
                ".check_runs[] | {name,status,conclusion,details_url,html_url,output}",
            ],
            env=git_env(),
            capture_output=True,
            text=True,
            check=True,
        )
        return _check_run_states_from_json(proc.stdout, check_names)


def profile_a_merge_policy() -> ProfileAMergePolicy | None:
    """Build Profile A merge settings from the project's config."""

    config = load_project_config()
    if config.delivery.profile != "A":
        return None
    profile_a = config.profile_a
    if profile_a is None:
        return None

    return ProfileAMergePolicy(
        github_repo=profile_a.github_repo.strip(),
        ready_label=profile_a.ready_label.strip(),
        base_branch=config.delivery.base_branch.strip(),
        terminal_deploy_checks=tuple(
            item.strip() for item in profile_a.terminal_deploy_checks if item.strip()
        ),
        mergeability_settle_timeout_s=profile_a.mergeability_settle_timeout or 15,
        deploy_wait_timeout_s=profile_a.deploy_wait_timeout or 300,
        merge_attempts=profile_a.merge_attempts or _MERGE_MATCH_HEAD_ATTEMPTS,
        merge_interval_s=profile_a.merge_interval_s or _MERGE_MATCH_HEAD_INTERVAL_S,
    )


def _check_run_states_from_json(
    text: str, check_names: tuple[str, ...]
) -> tuple[CheckRunState, ...]:
    wanted = set(check_names)
    states: list[CheckRunState] = []
    for payload in _check_run_payloads(text):
        name = payload.get("name")
        if not isinstance(name, str) or name not in wanted:
            continue
        output = payload.get("output")
        output_parts: tuple[object, ...] = ()
        if isinstance(output, dict):
            output_parts = (output.get("title"), output.get("summary"))
        details = " ".join(
            str(value)
            for value in (
                payload.get("details_url"),
                payload.get("html_url"),
                *output_parts,
            )
            if value
        ).strip()
        states.append(
            CheckRunState(
                name=name,
                status=str(payload.get("status") or "").lower(),
                conclusion=(
                    str(payload.get("conclusion")).lower()
                    if payload.get("conclusion") is not None
                    else None
                ),
                details=details,
            )
        )
    return tuple(states)


def _check_run_payloads(text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        check_runs = payload.get("check_runs")
        if isinstance(check_runs, list):
            payloads.extend(item for item in check_runs if isinstance(item, dict))
        else:
            payloads.append(payload)
    return payloads


def _classify_check_runs(
    states: tuple[CheckRunState, ...], check_names: tuple[str, ...]
) -> Literal["success", "transient", "terminal_failure", "state_lock_contention"]:
    by_name = {state.name: state for state in states}
    selected = [by_name.get(name) for name in check_names]
    if any(state is None for state in selected):
        return "transient"

    terminal: list[CheckRunState] = []
    for state in selected:
        assert state is not None
        status = state.status.lower()
        conclusion = (state.conclusion or "").lower()
        if status != "completed" or conclusion not in _TERMINAL_CHECK_CONCLUSIONS:
            return "transient"
        terminal.append(state)

    failing = [
        state
        for state in terminal
        if (state.conclusion or "").lower() not in _SUCCESSFUL_CHECK_CONCLUSIONS
    ]
    if not failing:
        return "success"
    if any(_proves_state_lock_contention(state) for state in failing):
        return "state_lock_contention"
    return "terminal_failure"


def _proves_state_lock_contention(state: CheckRunState) -> bool:
    text = f"{state.name} {state.status} {state.conclusion or ''} {state.details}".lower()
    return any(marker in text for marker in _STATE_LOCK_MARKERS)


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
        merge_attempts: int = _MERGE_MATCH_HEAD_ATTEMPTS,
        merge_interval_s: float = _MERGE_MATCH_HEAD_INTERVAL_S,
        deploy_check_names: tuple[str, ...] = (),
        deploy_wait_timeout_s: int = 300,
        check_interval_s: float | None = None,
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
        self.merge_attempts = max(1, merge_attempts)
        self.merge_interval_s = merge_interval_s
        self.deploy_check_names = tuple(name for name in deploy_check_names if name)
        self.deploy_wait_timeout_s = max(1, deploy_wait_timeout_s)
        self.check_interval_s = (
            check_interval_s if check_interval_s is not None else merge_interval_s
        )
        self.sleep = sleep
        self._marked_done: set[str] = set()
        self._recorded_outcomes: set[tuple[str, int, MergeAction]] = set()

    @classmethod
    def from_policy(
        cls,
        *,
        repo_root: Path,
        epic_id: int,
        gate: Callable[[ReadyPullRequest], bool],
        mark_done: Callable[[str], None],
        git: GitMergeOps | None = None,
        github: GithubMergeOps | None = None,
        remote: str = "origin",
        sleep: Callable[[float], None] = time.sleep,
    ) -> Self:
        policy = profile_a_merge_policy()
        if policy is None:
            raise ValueError("Profile A merge coordinator requires delivery.profile=A policy")
        return cls(
            repo_root=repo_root,
            epic_id=epic_id,
            repo_slug=policy.github_repo,
            base_branch=policy.base_branch,
            ready_label=policy.ready_label,
            git=git,
            github=github,
            gate=gate,
            mark_done=mark_done,
            remote=remote,
            mergeability_attempts=policy.mergeability_attempts,
            mergeability_interval_s=policy.merge_interval_s,
            merge_attempts=policy.merge_attempts,
            merge_interval_s=policy.merge_interval_s,
            deploy_check_names=policy.terminal_deploy_checks,
            deploy_wait_timeout_s=policy.deploy_wait_timeout_s,
            check_interval_s=policy.merge_interval_s,
            sleep=sleep,
        )

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
                self._halt_sibling_conflict(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "rebase_conflict",
                        "rebase onto the moved base conflicted",
                        terminal=True,
                    ),
                    detection_trigger="rebase_conflict",
                )

            if not self.gate(pr):
                merged_siblings, overlapping_paths = self._merged_siblings_overlapping(
                    pr, outcomes, ordered
                )
                if merged_siblings:
                    self.git.restore_original_head(repo, pr)
                    self._halt_sibling_conflict(
                        ordered,
                        outcomes,
                        MergeOutcome(
                            pr.work_unit_id,
                            pr.pr_number,
                            "gate_failed",
                            "gate failed after rebase on paths touched by merged sibling(s)",
                            terminal=True,
                        ),
                        detection_trigger="gate_failed_after_rebase",
                        merged_siblings=merged_siblings,
                        overlapping_paths=overlapping_paths,
                    )
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
            if mergeability == "CONFLICTING":
                self.git.restore_remote_branch(
                    repo,
                    self.remote,
                    pr.head_ref,
                    pr.head_sha,
                    rebased_head,
                )
                self._halt_sibling_conflict(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "mergeability_failed",
                        "mergeability settled CONFLICTING after rebase",
                        terminal=True,
                    ),
                    detection_trigger="mergeability_conflicting",
                )
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

            check_result = self._wait_for_checks(
                ref=rebased_head,
                timeout_s=self.mergeability_attempts * self.mergeability_interval_s,
            )
            if check_result == "transient":
                self._append_outcome(
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "waiting",
                        "configured checks did not settle inside the retry budget",
                        terminal=False,
                    ),
                )
                return MergeQueueResult(outcomes)
            if check_result == "state_lock_contention":
                self._halt(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "deploy_lock_contention",
                        "configured check failure proves Terraform state-lock contention",
                        terminal=True,
                    ),
                )
            if check_result == "terminal_failure":
                self._halt(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "checks_failed",
                        "configured checks reached a terminal failing state",
                        terminal=True,
                    ),
                )

            try:
                self._squash_merge_settled(pr, repo, rebased_head)
            except subprocess.CalledProcessError as exc:
                self._halt(
                    ordered,
                    outcomes,
                    MergeOutcome(
                        pr.work_unit_id,
                        pr.pr_number,
                        "merge_failed",
                        f"merge command failed after {self.merge_attempts} attempt(s): {exc}",
                        terminal=True,
                    ),
                )

            self._append_outcome(
                outcomes,
                self._mark_done(pr, "merged", "merged after rebase and gate"),
            )
            if pr != ordered[-1]:
                deploy_result = self._wait_for_checks(
                    ref=self.base_branch,
                    timeout_s=self.deploy_wait_timeout_s,
                )
                if deploy_result == "transient":
                    self._halt(
                        ordered,
                        outcomes,
                        MergeOutcome(
                            pr.work_unit_id,
                            pr.pr_number,
                            "deploy_failed",
                            "deploy checks did not reach a terminal state before the next merge",
                            terminal=True,
                        ),
                    )
                if deploy_result == "state_lock_contention":
                    self._halt(
                        ordered,
                        outcomes,
                        MergeOutcome(
                            pr.work_unit_id,
                            pr.pr_number,
                            "deploy_lock_contention",
                            "deploy checks prove Terraform state-lock contention",
                            terminal=True,
                        ),
                    )
                if deploy_result == "terminal_failure":
                    self._halt(
                        ordered,
                        outcomes,
                        MergeOutcome(
                            pr.work_unit_id,
                            pr.pr_number,
                            "deploy_failed",
                            "deploy checks reached a terminal failing state",
                            terminal=True,
                        ),
                    )
        return MergeQueueResult(outcomes)

    def _settle_mergeability(self, pr: ReadyPullRequest) -> Literal["mergeable", "transient"] | str:
        for attempt in range(self.mergeability_attempts):
            try:
                state = self.github.pr_mergeability(self.repo_slug, pr.pr_number).upper()
            except subprocess.CalledProcessError:
                state = "UNKNOWN"
            if state in _MERGEABLE:
                return "mergeable"
            if state not in _TRANSIENT_MERGEABILITY:
                return state
            if attempt < self.mergeability_attempts - 1:
                self.sleep(self.mergeability_interval_s)
        return "transient"

    def _squash_merge_settled(self, pr: ReadyPullRequest, repo: Path, rebased_head: str) -> None:
        """Squash-merge after bounded settle-retry for GitHub head-view lag."""

        for attempt in range(self.merge_attempts):
            try:
                self.github.squash_merge(
                    self.repo_slug,
                    pr.pr_number,
                    rebased_head,
                    subject=f"{pr.work_unit_id}: merge PR #{pr.pr_number}",
                    body=self._merge_body(pr, rebased_head),
                )
                return
            except subprocess.CalledProcessError:
                if attempt == self.merge_attempts - 1:
                    raise
                self.sleep(self.merge_interval_s)
                self.git.fetch(repo, self.remote)

    def _wait_for_checks(
        self, *, ref: str, timeout_s: float
    ) -> Literal["success", "transient", "terminal_failure", "state_lock_contention"]:
        if not self.deploy_check_names:
            return "success"
        attempts = max(1, math.ceil(timeout_s / self.check_interval_s))
        for attempt in range(attempts):
            try:
                states = self.github.check_run_states(self.repo_slug, ref, self.deploy_check_names)
            except subprocess.CalledProcessError:
                states = ()
            status = _classify_check_runs(states, self.deploy_check_names)
            if status != "transient":
                return status
            if attempt < attempts - 1:
                self.sleep(self.check_interval_s)
        return "transient"

    def _merge_body(self, pr: ReadyPullRequest, rebased_head: str) -> str:
        lines = [
            f"Closes #{self.epic_id}",
            f"Work unit: {pr.work_unit_id}",
            f"PR: #{pr.pr_number}",
            f"Published head: {pr.head_sha}",
            f"Rebased head: {rebased_head}",
        ]
        if pr.artefact_lineage:
            lines.append("Artefact lineage:")
            lines.extend(f"{key}: {value}" for key, value in sorted(pr.artefact_lineage.items()))
        return "\n".join(lines) + "\n"

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

    def _merged_siblings_overlapping(
        self,
        pr: ReadyPullRequest,
        outcomes: list[MergeOutcome],
        prs: list[ReadyPullRequest],
    ) -> tuple[list[ReadyPullRequest], list[str]]:
        if not pr.changed_paths:
            return [], []

        merged_by_id = {
            outcome.work_unit_id
            for outcome in outcomes
            if outcome.action in _MERGED_ACTIONS and outcome.work_unit_id != pr.work_unit_id
        }
        prs_by_id = {candidate.work_unit_id: candidate for candidate in prs}
        merged_siblings: list[ReadyPullRequest] = []
        overlapping_paths: set[str] = set()
        changed_paths = set(pr.changed_paths)
        for work_unit_id in merged_by_id:
            sibling = prs_by_id.get(work_unit_id)
            if sibling is None:
                continue
            overlap = changed_paths.intersection(sibling.changed_paths)
            if not overlap:
                continue
            merged_siblings.append(sibling)
            overlapping_paths.update(overlap)
        merged_siblings.sort(key=lambda sibling: (sibling.ready_at, sibling.pr_number))
        return merged_siblings, sorted(overlapping_paths)

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

    def _halt_sibling_conflict(
        self,
        prs: list[ReadyPullRequest],
        outcomes: list[MergeOutcome],
        outcome: MergeOutcome,
        *,
        detection_trigger: str,
        merged_siblings: list[ReadyPullRequest] | None = None,
        overlapping_paths: list[str] | None = None,
    ) -> None:
        self._reconcile_merged(prs, outcomes)
        merged_siblings = merged_siblings or self._merged_siblings_for(outcome, outcomes, prs)
        overlapping_paths = overlapping_paths or self._overlapping_paths(
            outcome, merged_siblings, prs
        )
        self._open_sibling_conflict_gate(outcome, detection_trigger)
        self._record_sibling_conflict(
            outcome,
            detection_trigger=detection_trigger,
            merged_siblings=merged_siblings,
            overlapping_paths=overlapping_paths,
        )
        self._append_outcome(outcomes, outcome)
        raise MergeQueueHalt(outcome, outcomes)

    def _merged_siblings_for(
        self,
        outcome: MergeOutcome,
        outcomes: list[MergeOutcome],
        prs: list[ReadyPullRequest],
    ) -> list[ReadyPullRequest]:
        merged_by_id = {
            prior.work_unit_id
            for prior in outcomes
            if prior.action in _MERGED_ACTIONS and prior.work_unit_id != outcome.work_unit_id
        }
        siblings = [pr for pr in prs if pr.work_unit_id in merged_by_id]
        return sorted(siblings, key=lambda sibling: (sibling.ready_at, sibling.pr_number))

    def _overlapping_paths(
        self,
        outcome: MergeOutcome,
        merged_siblings: list[ReadyPullRequest],
        prs: list[ReadyPullRequest],
    ) -> list[str]:
        pr = next(
            (candidate for candidate in prs if candidate.work_unit_id == outcome.work_unit_id), None
        )
        if pr is None or not pr.changed_paths:
            return []
        changed_paths = set(pr.changed_paths)
        overlap: set[str] = set()
        for sibling in merged_siblings:
            overlap.update(changed_paths.intersection(sibling.changed_paths))
        return sorted(overlap)

    def _open_sibling_conflict_gate(self, outcome: MergeOutcome, detection_trigger: str) -> None:
        epic_dir = self.repo_root / ".woof" / "epics" / f"E{self.epic_id}"
        gate_path = epic_dir / "gate.md"
        if self._same_sibling_conflict_gate_is_open(gate_path, outcome):
            return
        epic_dir.mkdir(parents=True, exist_ok=True)
        write_gate(
            epic_dir=epic_dir,
            work_unit_id=outcome.work_unit_id,
            triggered_by=[_SIBLING_CONFLICT_TRIGGER],
            position_text=(
                f"Profile A merge halted for PR #{outcome.pr_number}: {outcome.detail}.\n\n"
                f"Detection trigger: {detection_trigger}.\n\n"
                "Do not automatically reapply the change. Resolve by reconciling the "
                "worktree and re-pushing through the full gate and fresh review, returning "
                "the unit to production against moved main, or withdrawing it."
            ),
            validate=False,
            gate_type="work_unit_gate",
        )

    def _same_sibling_conflict_gate_is_open(self, gate_path: Path, outcome: MergeOutcome) -> bool:
        if not gate_path.exists():
            return False
        text = gate_path.read_text()
        return _SIBLING_CONFLICT_TRIGGER in text and f"work_unit_id: {outcome.work_unit_id}" in text

    def _record_sibling_conflict(
        self,
        outcome: MergeOutcome,
        *,
        detection_trigger: str,
        merged_siblings: list[ReadyPullRequest],
        overlapping_paths: list[str],
    ) -> None:
        corpus_path = self.repo_root / ".woof" / "sibling-conflicts.jsonl"
        event = {
            "event": "sibling_conflict_detected",
            "at": iso_utc(),
            "epic_id": self.epic_id,
            "work_unit_id": outcome.work_unit_id,
            "pr_number": outcome.pr_number,
            "action": outcome.action,
            "detection_trigger": detection_trigger,
            "resolution_outcome": "human_gate_opened",
            "merged_siblings": [
                {"work_unit_id": sibling.work_unit_id, "pr_number": sibling.pr_number}
                for sibling in merged_siblings
            ],
            "overlapping_paths": overlapping_paths,
        }
        if self._sibling_conflict_record_exists(corpus_path, event):
            return
        corpus_path.parent.mkdir(parents=True, exist_ok=True)
        with corpus_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")

    def _sibling_conflict_record_exists(self, corpus_path: Path, event: dict) -> bool:
        if not corpus_path.exists():
            return False
        for line in corpus_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                existing.get("event") == event["event"]
                and existing.get("epic_id") == event["epic_id"]
                and existing.get("work_unit_id") == event["work_unit_id"]
                and existing.get("pr_number") == event["pr_number"]
                and existing.get("detection_trigger") == event["detection_trigger"]
            ):
                return True
        return False
