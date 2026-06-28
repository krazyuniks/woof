"""GitHub issue-tracker adapter.

The GitHub adapter keeps the epic-level contract in a GitHub issue: one issue
per epic, ``E<N>`` ≡ issue ``#<N>``. Push is conflict-detected against the
``.last-sync`` baseline recorded under the epic directory.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from woof.gate.write import write_gate
from woof.graph.state import TERMINAL_STORY_STATUSES, Plan
from woof.paths import schema_dir
from woof.trackers.base import (
    ColdStartResult,
    ConflictResolutionResult,
    DefinitionSyncResult,
    LifecycleSyncResult,
    NewEpicResult,
    TrackerError,
    append_jsonl,
    atomic_write_text,
    epic_directory,
    iso_utc,
    last_sync_body,
    last_sync_text,
    read_last_sync,
    sha256_text,
    write_last_sync,
)
from woof.trackers.epic_body import (
    epic_markdown_from_issue,
    render_epic_issue_body,
    seed_from_spark,
    spark_markdown,
    split_epic_front_matter,
)

GITHUB_RATE_LIMIT_SAFETY_MARGIN = 100
GITHUB_COMMAND_TIMEOUT_SECONDS = 20

# GitHub's REST API `state_reason` for an issue closed as "not planned" - the
# value produced by `gh issue close --reason "not planned"`. close_not_delivered
# uses it to detect an already-closed issue carrying the wrong close reason.
GITHUB_NOT_PLANNED_REASON = "not_planned"


def github_core_remaining(output: str) -> int | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    core = (payload.get("resources") or {}).get("core") or {}
    remaining = core.get("remaining")
    return remaining if isinstance(remaining, int) else None


class GitHubTracker:
    """Issue-tracker adapter backed by GitHub issues via the ``gh`` CLI."""

    kind = "github"

    def __init__(self, repo_root: Path, repo: str) -> None:
        self.repo_root = repo_root
        self.repo = repo

    # -- runtime ----------------------------------------------------------

    def assert_runtime_reachable(self) -> None:
        try:
            proc = subprocess.run(
                ["gh", "api", "/rate_limit"],
                capture_output=True,
                text=True,
                timeout=GITHUB_COMMAND_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise TrackerError("gh not found on PATH; run `gh auth login`") from exc
        except subprocess.TimeoutExpired as exc:
            raise TrackerError("gh api /rate_limit timed out; check GitHub connectivity") from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise TrackerError(
                f"gh api /rate_limit failed before workflow start for {self.repo}:\n"
                f"{detail}\n"
                "Run `gh auth login` and retry."
            )

        remaining = github_core_remaining(proc.stdout)
        if remaining is not None and remaining <= GITHUB_RATE_LIMIT_SAFETY_MARGIN:
            raise TrackerError(
                f"GitHub API core rate limit remaining {remaining}; "
                f"requires > {GITHUB_RATE_LIMIT_SAFETY_MARGIN}"
            )

    # -- epic lifecycle ---------------------------------------------------

    def create_epic(self, spark: str) -> NewEpicResult:
        title, body = seed_from_spark(spark)
        issue_url = self._create_issue(title=title, body=body)
        epic_id = _issue_number_from_url(issue_url)
        try:
            issue = self._fetch_issue(epic_id)
            result = self._initialise_epic_from_payload(epic_id, issue)
        except TrackerError as exc:
            try:
                self._close_issue(epic_id)
            except TrackerError as close_exc:
                raise TrackerError(
                    f"created {issue_url} but failed to initialise local E{epic_id}: {exc}. "
                    f"Automatic cleanup failed: {close_exc}. Close the created issue manually."
                ) from exc
            raise TrackerError(
                f"created {issue_url} but failed to initialise local E{epic_id}: {exc}. "
                "Closed the newly created issue."
            ) from exc

        current_epic_path = self.repo_root / ".woof" / ".current-epic"
        atomic_write_text(current_epic_path, f"E{epic_id}\n")
        append_jsonl(
            result.epic_dir / "epic.jsonl",
            {
                "event": "current_epic_selected",
                "at": iso_utc(),
                "epic_id": epic_id,
            },
        )
        return NewEpicResult(
            epic_id=result.epic_id,
            epic_dir=result.epic_dir,
            spark_path=result.spark_path,
            epic_path=result.epic_path,
            last_sync_path=result.last_sync_path,
            epic_ref=issue_url,
            current_epic_path=current_epic_path,
        )

    def fetch_epic(self, epic_id: int) -> ColdStartResult:
        issue = self._fetch_issue(epic_id)
        return self._initialise_epic_from_payload(epic_id, issue)

    def assert_epic_authority(self, epic_id: int) -> None:
        issue = self._fetch_issue(epic_id)
        if "pull_request" in issue:
            raise TrackerError(f"E{epic_id} resolves to a pull request, not a GitHub issue")
        number = issue.get("number")
        if isinstance(number, int) and number != epic_id:
            raise TrackerError(f"gh returned issue #{number}, expected #{epic_id}")

        last_sync_path = epic_directory(self.repo_root, epic_id) / ".last-sync"
        last_sync = read_last_sync(last_sync_path)
        if last_sync is None:
            raise TrackerError(
                f"{last_sync_path} not found; existing local epics must be initialised "
                'from GitHub with `woof wf --epic <N>` or `woof wf new "<spark>"`'
            )
        synced_issue = last_sync.get("issue_number")
        if synced_issue != epic_id:
            raise TrackerError(
                f"{last_sync_path} issue_number={synced_issue!r}, expected {epic_id}"
            )

    def has_sync_state(self, epic_id: int) -> bool:
        return (epic_directory(self.repo_root, epic_id) / ".last-sync").is_file()

    def push_epic_definition(
        self, epic_id: int, front: dict[str, Any], prose: str
    ) -> DefinitionSyncResult:
        remote = self._fetch_issue(epic_id)
        remote_updated_at = _issue_updated_at(remote)
        remote_body = _issue_body(remote)
        epic_dir = epic_directory(self.repo_root, epic_id)
        last_sync_path = epic_dir / ".last-sync"
        last_sync = read_last_sync(last_sync_path)

        body = render_epic_issue_body(
            front,
            prose,
            remote_body=last_sync_body(last_sync) if last_sync else remote_body,
        )
        self._raise_if_sync_conflict(
            epic_dir=epic_dir,
            epic_id=epic_id,
            last_sync=last_sync,
            remote_updated_at=remote_updated_at,
            remote_body=remote_body,
            local_body=body,
        )
        if (
            last_sync
            and last_sync.get("updated_at") == remote_updated_at
            and last_sync.get("body_sha256") == sha256_text(body)
        ):
            return DefinitionSyncResult(
                epic_id=epic_id,
                body=body,
                updated_at=remote_updated_at,
                last_sync_path=last_sync_path,
                changed=False,
            )

        self._edit_issue_body(epic_id, body)
        new_remote = self._fetch_issue(epic_id)
        updated_at = _issue_updated_at(new_remote)
        write_last_sync(
            last_sync_path,
            {
                "issue_number": epic_id,
                "updated_at": updated_at,
                "body_sha256": sha256_text(body),
                "body": body,
            },
        )
        append_jsonl(
            epic_dir / "epic.jsonl",
            {
                "event": "tracker_synced",
                "at": iso_utc(),
                "epic_id": epic_id,
            },
        )
        return DefinitionSyncResult(
            epic_id=epic_id,
            body=body,
            updated_at=updated_at,
            last_sync_path=last_sync_path,
            changed=True,
        )

    def push_plan_summary(self, epic_id: int) -> LifecycleSyncResult:
        front, prose = self._load_epic_markdown(epic_id)
        plan = self._load_plan(epic_id)
        return self._sync_lifecycle_body(
            epic_id,
            lambda remote_body: render_epic_issue_body(
                front,
                prose,
                remote_body=remote_body,
                plan=plan,
            ),
            close=False,
        )

    def complete_epic(self, epic_id: int) -> LifecycleSyncResult:
        front, prose = self._load_epic_markdown(epic_id)
        plan = self._load_plan(epic_id)
        if any(unit.status not in TERMINAL_STORY_STATUSES for unit in plan.work_units):
            raise TrackerError(f"E{epic_id} cannot be closed until all plan work units are done")
        return self._sync_lifecycle_body(
            epic_id,
            lambda remote_body: render_epic_issue_body(
                front,
                prose,
                remote_body=remote_body,
                plan=plan,
                completed=True,
            ),
            close=True,
        )

    def close_not_delivered(self, epic_id: int) -> LifecycleSyncResult:
        # The abandon_epic terminal: close the issue with the "not planned" close
        # reason so it reads as abandoned, not delivered. No plan/EPIC.md load and
        # no all-done guard - the epic is abandoned with work outstanding, possibly
        # before plan.json exists (a readiness-gate abandon). The body is left as
        # the current remote; the close reason carries the not-delivered semantics.
        remote = self._fetch_issue(epic_id)
        epic_dir = epic_directory(self.repo_root, epic_id)
        last_sync_path = epic_dir / ".last-sync"
        # Correct the close reason unless the issue is already closed as
        # not-planned. An open issue is closed with that reason; an issue closed
        # for a different reason (e.g. someone closed it manually as "completed")
        # is re-closed so GitHub's PATCH resets state_reason to not_planned - else
        # the tracker would read as delivered while the local terminal records an
        # abandon. An already-not-planned issue needs no API write (idempotent).
        already_not_planned = (
            remote.get("state") == "closed"
            and remote.get("state_reason") == GITHUB_NOT_PLANNED_REASON
        )
        closed = False
        if not already_not_planned:
            self._close_issue(epic_id, reason="not planned")
            closed = True
            remote = self._fetch_issue(epic_id)
        updated_at = _issue_updated_at(remote)
        remote_body = _issue_body(remote)
        write_last_sync(
            last_sync_path,
            {
                "issue_number": epic_id,
                "updated_at": updated_at,
                "body_sha256": sha256_text(remote_body),
                "body": remote_body,
            },
        )
        append_jsonl(
            epic_dir / "epic.jsonl",
            {
                "event": "tracker_synced",
                "at": iso_utc(),
                "epic_id": epic_id,
                "not_delivered": True,
            },
        )
        return LifecycleSyncResult(
            epic_id=epic_id,
            body=remote_body,
            updated_at=updated_at,
            last_sync_path=last_sync_path,
            changed=closed,
            closed=closed,
        )

    def resolve_conflict(self, epic_id: int, decision: str) -> ConflictResolutionResult:
        if decision not in {"keep_local", "accept_remote", "hand_merge"}:
            raise TrackerError(f"unsupported tracker_sync_conflict decision: {decision}")

        issue = self._fetch_issue(epic_id)
        remote_body = _issue_body(issue)
        updated_at = _issue_updated_at(issue)
        epic_dir = epic_directory(self.repo_root, epic_id)
        last_sync_path = epic_dir / ".last-sync"
        epic_path: Path | None = None

        if decision == "accept_remote":
            title = issue.get("title")
            if not isinstance(title, str) or not title.strip():
                raise TrackerError(f"GitHub issue #{epic_id} has no title")
            epic_text = epic_markdown_from_issue(epic_id=epic_id, title=title, body=remote_body)
            if epic_text is None:
                raise TrackerError(
                    "accept_remote requires a GitHub issue body with Woof managed sections; "
                    "use hand_merge for unstructured remote bodies"
                )
            epic_path = epic_dir / "EPIC.md"
            atomic_write_text(epic_path, epic_text)

        write_last_sync(
            last_sync_path,
            {
                "issue_number": epic_id,
                "updated_at": updated_at,
                "body_sha256": sha256_text(remote_body),
                "body": remote_body,
            },
        )
        event: dict[str, Any] = {
            "event": "tracker_synced",
            "at": iso_utc(),
            "epic_id": epic_id,
            "conflict_resolution": decision,
        }
        if epic_path is not None:
            event["paths"] = [epic_path.relative_to(self.repo_root).as_posix()]
        append_jsonl(epic_dir / "epic.jsonl", event)
        return ConflictResolutionResult(
            epic_id=epic_id,
            decision=decision,
            updated_at=updated_at,
            last_sync_path=last_sync_path,
            epic_path=epic_path,
        )

    # -- gh process helpers ----------------------------------------------

    def _run_gh(
        self,
        args: list[str],
        *,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["gh", *args],
                input=input,
                capture_output=True,
                text=True,
                timeout=GITHUB_COMMAND_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise TrackerError("gh not found on PATH; run `gh auth login`") from exc
        except subprocess.TimeoutExpired as exc:
            summary = " ".join(args[:3])
            raise TrackerError(
                f"gh {summary} timed out after {GITHUB_COMMAND_TIMEOUT_SECONDS}s; "
                "check GitHub connectivity"
            ) from exc

    def _fetch_issue(self, epic_id: int) -> dict[str, Any]:
        proc = self._run_gh(
            [
                "api",
                f"/repos/{self.repo}/issues/{epic_id}",
                "-H",
                "Accept: application/vnd.github+json",
            ],
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            if "not found" in detail.lower():
                raise TrackerError(
                    f'E{epic_id} not found. Use `woof wf new "<spark>"` to start '
                    "a new epic - gh assigns the issue number."
                )
            raise TrackerError(f"gh api /repos/{self.repo}/issues/{epic_id} failed:\n{detail}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise TrackerError(f"gh returned invalid JSON for issue {epic_id}: {exc}") from exc
        if not isinstance(payload, dict):
            raise TrackerError(f"gh returned a non-object JSON payload for issue {epic_id}")
        return payload

    def _create_issue(self, *, title: str, body: str) -> str:
        proc = self._run_gh(
            [
                "issue",
                "create",
                "--repo",
                self.repo,
                "--title",
                title,
                "--body-file",
                "-",
            ],
            input=body,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise TrackerError(f"gh issue create --repo {self.repo} failed:\n{detail}")
        issue_url = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
        if not issue_url:
            raise TrackerError("gh issue create returned no issue URL")
        return issue_url

    def _edit_issue_body(self, epic_id: int, body: str) -> None:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(body)
            body_path = Path(handle.name)
        try:
            proc = self._run_gh(
                [
                    "issue",
                    "edit",
                    str(epic_id),
                    "--repo",
                    self.repo,
                    "--body-file",
                    str(body_path),
                ],
            )
        finally:
            body_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise TrackerError(f"gh issue edit {epic_id} --repo {self.repo} failed:\n{detail}")

    def _close_issue(self, epic_id: int, *, reason: str | None = None) -> None:
        args = ["issue", "close", str(epic_id), "--repo", self.repo]
        if reason is not None:
            args += ["--reason", reason]
        proc = self._run_gh(args)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise TrackerError(f"gh issue close {epic_id} --repo {self.repo} failed:\n{detail}")

    # -- epic-state helpers ----------------------------------------------

    def _initialise_epic_from_payload(self, epic_id: int, issue: dict[str, Any]) -> ColdStartResult:
        number = issue.get("number", epic_id)
        if number != epic_id:
            raise TrackerError(f"gh returned issue #{number}, expected #{epic_id}")
        title = issue.get("title")
        if not isinstance(title, str) or not title.strip():
            raise TrackerError(f"GitHub issue #{epic_id} has no title")
        body = issue.get("body") or ""
        if not isinstance(body, str):
            raise TrackerError(f"GitHub issue #{epic_id} body is not a string")

        epic_text = epic_markdown_from_issue(epic_id=epic_id, title=title, body=body)
        updated_at = _issue_updated_at(issue)

        epic_dir = epic_directory(self.repo_root, epic_id)
        if epic_dir.exists():
            raise TrackerError(f"{epic_dir} already exists")
        epic_dir.mkdir(parents=True)

        spark_path = epic_dir / "spark.md"
        spark_path.write_text(spark_markdown(title, body), encoding="utf-8")
        epic_path: Path | None = None
        if epic_text is not None:
            epic_path = epic_dir / "EPIC.md"
            epic_path.write_text(epic_text, encoding="utf-8")

        last_sync_path = epic_dir / ".last-sync"
        write_last_sync(
            last_sync_path,
            {
                "issue_number": epic_id,
                "updated_at": updated_at,
                "body_sha256": sha256_text(body),
                "body": body,
            },
        )
        append_jsonl(
            epic_dir / "epic.jsonl",
            {
                "event": "spark_created",
                "at": iso_utc(),
                "epic_id": epic_id,
                "source": "github",
            },
        )
        append_jsonl(
            epic_dir / "epic.jsonl",
            {
                "event": "tracker_synced",
                "at": iso_utc(),
                "epic_id": epic_id,
                "updated_at": updated_at,
            },
        )
        return ColdStartResult(
            epic_id=epic_id,
            epic_dir=epic_dir,
            spark_path=spark_path,
            epic_path=epic_path,
            last_sync_path=last_sync_path,
        )

    def _load_epic_markdown(self, epic_id: int) -> tuple[dict[str, Any], str]:
        epic_path = epic_directory(self.repo_root, epic_id) / "EPIC.md"
        try:
            return split_epic_front_matter(epic_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise TrackerError(f"{epic_path} could not be loaded: {exc}") from exc

    def _load_plan(self, epic_id: int) -> Plan:
        plan_path = epic_directory(self.repo_root, epic_id) / "plan.json"
        try:
            return Plan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TrackerError(f"{plan_path} could not be loaded: {exc}") from exc

    def _sync_lifecycle_body(
        self,
        epic_id: int,
        render: Callable[[str], str],
        *,
        close: bool,
    ) -> LifecycleSyncResult:
        remote = self._fetch_issue(epic_id)
        remote_updated_at = _issue_updated_at(remote)
        remote_body = _issue_body(remote)
        epic_dir = epic_directory(self.repo_root, epic_id)
        last_sync_path = epic_dir / ".last-sync"
        last_sync = read_last_sync(last_sync_path)

        body = render(last_sync_body(last_sync) if last_sync else remote_body)
        self._raise_if_sync_conflict(
            epic_dir=epic_dir,
            epic_id=epic_id,
            last_sync=last_sync,
            remote_updated_at=remote_updated_at,
            remote_body=remote_body,
            local_body=body,
        )
        body_changed = not (
            last_sync
            and last_sync.get("updated_at") == remote_updated_at
            and last_sync.get("body_sha256") == sha256_text(body)
        )
        if body_changed:
            self._edit_issue_body(epic_id, body)
            remote = self._fetch_issue(epic_id)

        closed = False
        if close and remote.get("state") != "closed":
            self._close_issue(epic_id)
            closed = True
            remote = self._fetch_issue(epic_id)

        updated_at = _issue_updated_at(remote)
        if body_changed or closed:
            write_last_sync(
                last_sync_path,
                {
                    "issue_number": epic_id,
                    "updated_at": updated_at,
                    "body_sha256": sha256_text(body),
                    "body": body,
                },
            )
            append_jsonl(
                epic_dir / "epic.jsonl",
                {
                    "event": "tracker_synced",
                    "at": iso_utc(),
                    "epic_id": epic_id,
                },
            )

        return LifecycleSyncResult(
            epic_id=epic_id,
            body=body,
            updated_at=updated_at,
            last_sync_path=last_sync_path,
            changed=body_changed,
            closed=closed,
        )

    # -- conflict detection ----------------------------------------------

    def _raise_if_sync_conflict(
        self,
        *,
        epic_dir: Path,
        epic_id: int,
        last_sync: dict[str, Any] | None,
        remote_updated_at: str,
        remote_body: str,
        local_body: str,
    ) -> None:
        if last_sync is None:
            return

        last_updated_at = last_sync_text(last_sync, "updated_at")
        last_body_sha256 = last_sync_text(last_sync, "body_sha256")
        remote_body_sha256 = sha256_text(remote_body)
        reasons: list[str] = []

        if last_updated_at != remote_updated_at:
            reasons.append("updated_at")
        if last_body_sha256 and last_body_sha256 != remote_body_sha256:
            reasons.append("body_sha256")
        if not reasons:
            return

        gate_path = self._write_sync_conflict_gate(
            epic_dir=epic_dir,
            epic_id=epic_id,
            last_sync=last_sync,
            remote_updated_at=remote_updated_at,
            remote_body=remote_body,
            local_body=local_body,
            reasons=reasons,
        )
        raise TrackerError(
            f"tracker_sync_conflict for E{epic_id}\n"
            f"  last-sync updated_at: {last_updated_at}\n"
            f"  remote   updated_at: {remote_updated_at}\n"
            f"  last-sync body_sha256: {last_body_sha256 or '<missing>'}\n"
            f"  remote   body_sha256: {remote_body_sha256}\n"
            f"  gate: {gate_path}\n"
            "  push aborted - resolve via /wf gate"
        )

    def _write_sync_conflict_gate(
        self,
        *,
        epic_dir: Path,
        epic_id: int,
        last_sync: dict[str, Any],
        remote_updated_at: str,
        remote_body: str,
        local_body: str,
        reasons: list[str],
    ) -> Path:
        last_body = last_sync_body(last_sync)
        last_updated_at = last_sync_text(last_sync, "updated_at")
        last_body_sha256 = last_sync_text(last_sync, "body_sha256")
        remote_body_sha256 = sha256_text(remote_body)
        local_body_sha256 = sha256_text(local_body)
        position_text = _sync_conflict_gate_body(
            epic_id=epic_id,
            reasons=reasons,
            last_updated_at=last_updated_at,
            remote_updated_at=remote_updated_at,
            last_body_sha256=last_body_sha256,
            remote_body_sha256=remote_body_sha256,
            local_body_sha256=local_body_sha256,
            last_body=last_body,
            remote_body=remote_body,
            local_body=local_body,
        )
        gate_path = write_gate(
            epic_dir=epic_dir,
            story_id=None,
            triggered_by=["tracker_sync_conflict"],
            position_text=position_text,
            schema_path=schema_dir() / "gate.schema.json",
            gate_type="plan_gate",
        )
        append_jsonl(
            epic_dir / "epic.jsonl",
            {
                "event": "tracker_sync_conflict",
                "at": iso_utc(),
                "epic_id": epic_id,
                "triggered_by": ["tracker_sync_conflict"],
                "reasons": reasons,
                "last_sync_updated_at": last_updated_at,
                "remote_updated_at": remote_updated_at,
                "last_sync_body_sha256": last_body_sha256,
                "remote_body_sha256": remote_body_sha256,
                "local_body_sha256": local_body_sha256,
                "gate_path": str(gate_path),
            },
        )
        return gate_path


def _sync_conflict_gate_body(
    *,
    epic_id: int,
    reasons: list[str],
    last_updated_at: str,
    remote_updated_at: str,
    last_body_sha256: str,
    remote_body_sha256: str,
    local_body_sha256: str,
    last_body: str,
    remote_body: str,
    local_body: str,
) -> str:
    reason_text = ", ".join(reasons)
    remote_diff = _unified_body_diff(
        from_label="last-pushed",
        to_label="current remote",
        before=last_body,
        after=remote_body,
    )
    local_diff = _unified_body_diff(
        from_label="last-pushed",
        to_label="current local render",
        before=last_body,
        after=local_body,
    )
    return (
        f"## Context\n\n"
        f"Tracker sync conflict detected for E{epic_id}. Woof was about to push the "
        "GitHub issue body, but the remote issue no longer matches `.last-sync`.\n\n"
        "## Findings\n\n"
        f"- Conflict reasons: {reason_text}\n"
        f"- `.last-sync` updated_at: {last_updated_at or '<missing>'}\n"
        f"- Remote updated_at: {remote_updated_at or '<missing>'}\n"
        f"- `.last-sync` body_sha256: {last_body_sha256 or '<missing>'}\n"
        f"- Remote body_sha256: {remote_body_sha256}\n"
        f"- Current local render body_sha256: {local_body_sha256}\n\n"
        "### Diff: last-pushed -> current remote\n\n"
        "```diff\n"
        f"{remote_diff}\n"
        "```\n\n"
        "### Diff: last-pushed -> current local render\n\n"
        "```diff\n"
        f"{local_diff}\n"
        "```\n\n"
        "## Primary position\n\n"
        "No issue update was sent to GitHub. The current local render is shown above.\n\n"
        "## Reviewer position\n\n"
        "Resolve the conflict with one structured decision: `keep_local`, "
        "`accept_remote`, or `hand_merge`, then retry `/wf` or `woof render-epic --sync`.\n"
    )


def _unified_body_diff(*, from_label: str, to_label: str, before: str, after: str) -> str:
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=from_label,
            tofile=to_label,
            lineterm="",
        )
    )
    return "\n".join(diff) if diff else "(no textual diff)"


def _issue_number_from_url(issue_url: str) -> int:
    match = re.search(r"/issues/([1-9]\d*)/?$", issue_url)
    if not match:
        raise TrackerError(f"gh issue create returned an unparseable issue URL: {issue_url}")
    return int(match.group(1))


def _issue_updated_at(issue: dict[str, Any]) -> str:
    updated_at = issue.get("updated_at") or issue.get("updatedAt") or ""
    if not isinstance(updated_at, str):
        return ""
    return updated_at


def _issue_body(issue: dict[str, Any]) -> str:
    body = issue.get("body") or ""
    if not isinstance(body, str):
        raise TrackerError("GitHub issue body is not a string")
    return body
