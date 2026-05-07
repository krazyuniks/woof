"""GitHub issue synchronisation helpers for Woof CLI commands."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from woof.gate.write import write_gate
from woof.graph.state import Plan
from woof.paths import schema_dir


class GithubSyncError(RuntimeError):
    """GitHub synchronisation failed and must not be silently ignored."""


@dataclass(frozen=True)
class ColdStartResult:
    epic_id: int
    epic_dir: Path
    spark_path: Path
    epic_path: Path | None
    last_sync_path: Path


@dataclass(frozen=True)
class NewEpicResult(ColdStartResult):
    issue_url: str
    current_epic_path: Path


@dataclass(frozen=True)
class DefinitionSyncResult:
    epic_id: int
    body: str
    updated_at: str
    last_sync_path: Path
    changed: bool


@dataclass(frozen=True)
class LifecycleSyncResult(DefinitionSyncResult):
    closed: bool = False


STRUCTURED_HEADING = "## Observable Outcomes"
WOOF_SENTINEL = (
    "<!-- woof — structured sections above are rewritten on Definition/plan "
    "changes. Free-form prose above `## Observable Outcomes` is preserved on "
    "overwrite. Do not edit structured sections directly in gh. -->"
)
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_STRUCTURED_HEADING_RE = re.compile(r"^##\s+Observable Outcomes\s*$", re.MULTILINE)
_OUTCOME_RE = re.compile(r"^- \*\*(O[1-9]\d*)\*\*\s+(?:\u2014|-)\s+(.+?)\s*$")
_VERIFICATION_RE = re.compile(r"^\s+- Verification:\s+(.+?)\s*$")
_DEPRECATED_OUTCOME_RE = re.compile(r"\s+_\(deprecated(?:\s+\u2192\s+(O[1-9]\d*))?\)_$")
_DEPRECATED_CD_RE = re.compile(r"\s+_\(deprecated(?:\s+\u2192\s+(CD[1-9]\d*))?\)_$")


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_github_repo(repo_root: Path) -> str:
    prereq = repo_root / ".woof" / "prerequisites.toml"
    if not prereq.is_file():
        raise GithubSyncError(f"{prereq} not found; cannot resolve [github].repo")
    try:
        with prereq.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise GithubSyncError(f"{prereq} is not valid TOML: {exc}") from exc
    repo = (data.get("github") or {}).get("repo")
    if not isinstance(repo, str) or not repo:
        raise GithubSyncError(f"{prereq} missing [github].repo")
    return repo


def fetch_issue(repo: str, issue_number: int) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "gh",
            "api",
            f"/repos/{repo}/issues/{issue_number}",
            "-H",
            "Accept: application/vnd.github+json",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        if "not found" in detail.lower():
            raise GithubSyncError(
                f'E{issue_number} not found. Use `woof wf new "<spark>"` to start '
                "a new epic - gh assigns the issue number."
            )
        raise GithubSyncError(f"gh api /repos/{repo}/issues/{issue_number} failed:\n{detail}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GithubSyncError(f"gh returned invalid JSON for issue {issue_number}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GithubSyncError(f"gh returned a non-object JSON payload for issue {issue_number}")
    return payload


def split_epic_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    """Return ``EPIC.md`` front-matter and prose body."""

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: no YAML front-matter (file must start with '---\\n')")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{path}: unterminated YAML front-matter")
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict):
        raise ValueError(f"{path}: YAML front-matter root must be an object")
    prose = text[end + len("\n---\n") :]
    return front, prose


def render_epic_issue_body(
    front: dict[str, Any],
    prose: str,
    remote_body: str | None = None,
    *,
    plan: Plan | None = None,
    completed: bool = False,
) -> str:
    """Render ``EPIC.md`` front-matter into the managed GitHub issue body.

    If a remote issue body already has the managed heading, only the free-form
    prefix above that heading is preserved. Managed sections are rewritten
    wholesale from schema-valid front-matter.
    """

    out: list[str] = []
    remote_prefix = _remote_prefix(remote_body)
    if remote_prefix is not None:
        out.append(remote_prefix.rstrip() + "\n\n")
    else:
        intent = _front_intent(front) or _first_paragraph(prose)
        out.append((intent or "_(intent pending)_") + "\n\n")

    out.append("## Observable Outcomes\n\n")
    for outcome in front["observable_outcomes"]:
        suffix = _deprecation_suffix(outcome)
        out.append(f"- **{outcome['id']}** — {_single_line(outcome['statement'])}{suffix}\n")
        out.append(f"  - Verification: {outcome['verification']}\n")
    out.append("\n")

    decisions = front.get("contract_decisions") or []
    if decisions:
        out.append("## Contract Decisions\n\n")
        out.append("| ID | Related Outcomes | Title | Contract Reference |\n")
        out.append("|---|---|---|---|\n")
        for decision in decisions:
            related = ", ".join(decision["related_outcomes"])
            title = _table_cell(decision["title"] + _deprecation_suffix(decision))
            out.append(f"| {decision['id']} | {related} | {title} | {_contract_ref(decision)} |\n")
        out.append("\n")

    out.append("## Acceptance Criteria\n\n")
    for criterion in front["acceptance_criteria"]:
        out.append(f"- {_single_line(criterion)}\n")
    out.append("\n")

    open_questions = front.get("open_questions") or []
    if open_questions:
        out.append("## Open Questions\n\n")
        for question in open_questions:
            out.append(f"- {_single_line(question)}\n")
        out.append("\n")

    if plan is not None:
        out.append(render_plan_summary(plan))

    if completed:
        if plan is None:
            raise GithubSyncError("completion rendering requires plan.json")
        out.append(render_completion_summary(plan))

    out.append("---\n\n")
    out.append(WOOF_SENTINEL + "\n")
    return "".join(out)


def render_plan_summary(plan: Plan) -> str:
    out = ["## Plan Summary\n\n"]
    for story in plan.stories:
        out.append(f"- **{story.id}** — {_single_line(story.title)}\n")
    out.append("\n")
    return "".join(out)


def render_completion_summary(plan: Plan) -> str:
    total = len(plan.stories)
    done = sum(1 for story in plan.stories if story.status == "done")
    noun = "story" if total == 1 else "stories"
    return f"## Closing Summary\n\nEpic completed with {done}/{total} planned {noun} done.\n\n"


def sync_epic_definition(
    repo_root: Path, epic_id: int, front: dict[str, Any], prose: str
) -> DefinitionSyncResult:
    """Push the rendered Definition-stage issue body to GitHub."""

    repo = load_github_repo(repo_root)
    remote = fetch_issue(repo, epic_id)
    remote_updated_at = _issue_updated_at(remote)
    remote_body = _issue_body(remote)
    epic_directory = repo_root / ".woof" / "epics" / f"E{epic_id}"
    last_sync_path = epic_directory / ".last-sync"
    last_sync = _read_last_sync(last_sync_path)

    body = render_epic_issue_body(
        front,
        prose,
        remote_body=_last_sync_body(last_sync) if last_sync else remote_body,
    )
    _raise_if_sync_conflict(
        epic_directory=epic_directory,
        epic_id=epic_id,
        last_sync=last_sync,
        remote_updated_at=remote_updated_at,
        remote_body=remote_body,
        local_body=body,
    )
    if (
        last_sync
        and last_sync.get("updated_at") == remote_updated_at
        and last_sync.get("body_sha256") == _sha256(body)
    ):
        return DefinitionSyncResult(
            epic_id=epic_id,
            body=body,
            updated_at=remote_updated_at,
            last_sync_path=last_sync_path,
            changed=False,
        )

    _edit_issue_body(repo, epic_id, body)
    new_remote = fetch_issue(repo, epic_id)
    updated_at = _issue_updated_at(new_remote)
    _write_last_sync(
        last_sync_path,
        {
            "issue_number": epic_id,
            "updated_at": updated_at,
            "body_sha256": _sha256(body),
            "body": body,
        },
    )
    _append_jsonl(
        epic_directory / "epic.jsonl",
        {
            "event": "github_synced",
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


def sync_plan_summary(repo_root: Path, epic_id: int) -> LifecycleSyncResult:
    front, prose = _load_epic_markdown(repo_root, epic_id)
    plan = _load_plan(repo_root, epic_id)
    return _sync_lifecycle_body(
        repo_root,
        epic_id,
        lambda remote_body: render_epic_issue_body(
            front,
            prose,
            remote_body=remote_body,
            plan=plan,
        ),
        close=False,
    )


def sync_epic_completion(repo_root: Path, epic_id: int) -> LifecycleSyncResult:
    front, prose = _load_epic_markdown(repo_root, epic_id)
    plan = _load_plan(repo_root, epic_id)
    if any(story.status != "done" for story in plan.stories):
        raise GithubSyncError(f"E{epic_id} cannot be closed until all plan stories are done")
    return _sync_lifecycle_body(
        repo_root,
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


def has_github_sync_state(repo_root: Path, epic_id: int) -> bool:
    return (repo_root / ".woof" / "epics" / f"E{epic_id}" / ".last-sync").is_file()


def create_epic_from_spark(repo_root: Path, spark: str) -> NewEpicResult:
    title, body = _issue_seed_from_spark(spark)
    repo = load_github_repo(repo_root)
    issue_url = _create_issue(repo, title=title, body=body)
    epic_id = _issue_number_from_url(issue_url)
    issue = fetch_issue(repo, epic_id)
    result = _initialise_epic_from_payload(repo_root, epic_id, issue)

    current_epic_path = repo_root / ".woof" / ".current-epic"
    _atomic_write_text(current_epic_path, f"E{epic_id}\n")
    _append_jsonl(
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
        issue_url=issue_url,
        current_epic_path=current_epic_path,
    )


def initialise_epic_from_issue(repo_root: Path, epic_id: int) -> ColdStartResult:
    repo = load_github_repo(repo_root)
    issue = fetch_issue(repo, epic_id)
    return _initialise_epic_from_payload(repo_root, epic_id, issue)


def _initialise_epic_from_payload(
    repo_root: Path, epic_id: int, issue: dict[str, Any]
) -> ColdStartResult:
    number = issue.get("number", epic_id)
    if number != epic_id:
        raise GithubSyncError(f"gh returned issue #{number}, expected #{epic_id}")
    title = issue.get("title")
    if not isinstance(title, str) or not title.strip():
        raise GithubSyncError(f"GitHub issue #{epic_id} has no title")
    body = issue.get("body") or ""
    if not isinstance(body, str):
        raise GithubSyncError(f"GitHub issue #{epic_id} body is not a string")

    epic_text = epic_markdown_from_issue(epic_id=epic_id, title=title, body=body)
    updated_at = _issue_updated_at(issue)

    epic_dir = repo_root / ".woof" / "epics" / f"E{epic_id}"
    if epic_dir.exists():
        raise GithubSyncError(f"{epic_dir} already exists")
    epic_dir.mkdir(parents=True)

    spark_path = epic_dir / "spark.md"
    spark_path.write_text(_spark_markdown(title, body), encoding="utf-8")
    epic_path: Path | None = None
    if epic_text is not None:
        epic_path = epic_dir / "EPIC.md"
        epic_path.write_text(epic_text, encoding="utf-8")

    last_sync_path = epic_dir / ".last-sync"
    last_sync_path.write_text(
        json.dumps(
            {
                "issue_number": epic_id,
                "updated_at": updated_at,
                "body_sha256": _sha256(body),
                "body": body,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _append_jsonl(
        epic_dir / "epic.jsonl",
        {
            "event": "spark_created",
            "at": iso_utc(),
            "epic_id": epic_id,
            "source": "github",
        },
    )
    _append_jsonl(
        epic_dir / "epic.jsonl",
        {
            "event": "github_synced",
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


def _issue_seed_from_spark(spark: str) -> tuple[str, str]:
    text = spark.strip()
    if not text:
        raise GithubSyncError("spark must not be empty")
    lines = text.splitlines()
    title = lines[0].strip()
    if not title:
        raise GithubSyncError("spark must contain a non-empty first line")
    body = "\n".join(lines[1:]).strip() or title
    return title, body + "\n"


def _create_issue(repo: str, *, title: str, body: str) -> str:
    proc = subprocess.run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--body-file",
            "-",
        ],
        input=body,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise GithubSyncError(f"gh issue create --repo {repo} failed:\n{detail}")
    issue_url = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
    if not issue_url:
        raise GithubSyncError("gh issue create returned no issue URL")
    return issue_url


def _edit_issue_body(repo: str, issue_number: int, body: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(body)
        body_path = Path(handle.name)
    try:
        proc = subprocess.run(
            [
                "gh",
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                repo,
                "--body-file",
                str(body_path),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        body_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise GithubSyncError(f"gh issue edit {issue_number} --repo {repo} failed:\n{detail}")


def _close_issue(repo: str, issue_number: int) -> None:
    proc = subprocess.run(
        ["gh", "issue", "close", str(issue_number), "--repo", repo],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise GithubSyncError(f"gh issue close {issue_number} --repo {repo} failed:\n{detail}")


def _load_epic_markdown(repo_root: Path, epic_id: int) -> tuple[dict[str, Any], str]:
    epic_path = repo_root / ".woof" / "epics" / f"E{epic_id}" / "EPIC.md"
    try:
        return split_epic_front_matter(epic_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise GithubSyncError(f"{epic_path} could not be loaded: {exc}") from exc


def _load_plan(repo_root: Path, epic_id: int) -> Plan:
    plan_path = repo_root / ".woof" / "epics" / f"E{epic_id}" / "plan.json"
    try:
        return Plan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GithubSyncError(f"{plan_path} could not be loaded: {exc}") from exc


def _sync_lifecycle_body(
    repo_root: Path,
    epic_id: int,
    render: Callable[[str], str],
    *,
    close: bool,
) -> LifecycleSyncResult:
    repo = load_github_repo(repo_root)
    remote = fetch_issue(repo, epic_id)
    remote_updated_at = _issue_updated_at(remote)
    remote_body = _issue_body(remote)
    epic_directory = repo_root / ".woof" / "epics" / f"E{epic_id}"
    last_sync_path = epic_directory / ".last-sync"
    last_sync = _read_last_sync(last_sync_path)

    body = render(_last_sync_body(last_sync) if last_sync else remote_body)
    _raise_if_sync_conflict(
        epic_directory=epic_directory,
        epic_id=epic_id,
        last_sync=last_sync,
        remote_updated_at=remote_updated_at,
        remote_body=remote_body,
        local_body=body,
    )
    body_changed = not (
        last_sync
        and last_sync.get("updated_at") == remote_updated_at
        and last_sync.get("body_sha256") == _sha256(body)
    )
    if body_changed:
        _edit_issue_body(repo, epic_id, body)
        remote = fetch_issue(repo, epic_id)

    closed = False
    if close and remote.get("state") != "closed":
        _close_issue(repo, epic_id)
        closed = True
        remote = fetch_issue(repo, epic_id)

    updated_at = _issue_updated_at(remote)
    if body_changed or closed:
        _write_last_sync(
            last_sync_path,
            {
                "issue_number": epic_id,
                "updated_at": updated_at,
                "body_sha256": _sha256(body),
                "body": body,
            },
        )
        _append_jsonl(
            epic_directory / "epic.jsonl",
            {
                "event": "github_synced",
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


def _raise_if_sync_conflict(
    *,
    epic_directory: Path,
    epic_id: int,
    last_sync: dict[str, Any] | None,
    remote_updated_at: str,
    remote_body: str,
    local_body: str,
) -> None:
    if last_sync is None:
        return

    last_updated_at = _last_sync_text(last_sync, "updated_at")
    last_body_sha256 = _last_sync_text(last_sync, "body_sha256")
    remote_body_sha256 = _sha256(remote_body)
    reasons: list[str] = []

    if last_updated_at != remote_updated_at:
        reasons.append("updated_at")
    if last_body_sha256 and last_body_sha256 != remote_body_sha256:
        reasons.append("body_sha256")
    if not reasons:
        return

    gate_path = _write_sync_conflict_gate(
        epic_directory=epic_directory,
        epic_id=epic_id,
        last_sync=last_sync,
        remote_updated_at=remote_updated_at,
        remote_body=remote_body,
        local_body=local_body,
        reasons=reasons,
    )
    raise GithubSyncError(
        f"github_sync_conflict for E{epic_id}\n"
        f"  last-sync updated_at: {last_updated_at}\n"
        f"  remote   updated_at: {remote_updated_at}\n"
        f"  last-sync body_sha256: {last_body_sha256 or '<missing>'}\n"
        f"  remote   body_sha256: {remote_body_sha256}\n"
        f"  gate: {gate_path}\n"
        "  push aborted - resolve via /wf gate"
    )


def _write_sync_conflict_gate(
    *,
    epic_directory: Path,
    epic_id: int,
    last_sync: dict[str, Any],
    remote_updated_at: str,
    remote_body: str,
    local_body: str,
    reasons: list[str],
) -> Path:
    last_body = _last_sync_body(last_sync)
    last_updated_at = _last_sync_text(last_sync, "updated_at")
    last_body_sha256 = _last_sync_text(last_sync, "body_sha256")
    remote_body_sha256 = _sha256(remote_body)
    local_body_sha256 = _sha256(local_body)
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
        epic_dir=epic_directory,
        story_id=None,
        triggered_by=["github_sync_conflict"],
        position_text=position_text,
        schema_path=schema_dir() / "gate.schema.json",
        gate_type="plan_gate",
    )
    _append_jsonl(
        epic_directory / "epic.jsonl",
        {
            "event": "github_sync_conflict",
            "at": iso_utc(),
            "epic_id": epic_id,
            "triggered_by": ["github_sync_conflict"],
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
        f"GitHub sync conflict detected for E{epic_id}. Woof was about to push the "
        "issue body, but the remote issue no longer matches `.last-sync`.\n\n"
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
        "## Position\n\n"
        "No issue update was sent to GitHub. Resolve the conflict by choosing keep "
        "local, accept remote, or hand-merge, then retry `/wf`.\n"
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
        raise GithubSyncError(f"gh issue create returned an unparseable issue URL: {issue_url}")
    return int(match.group(1))


def epic_markdown_from_issue(*, epic_id: int, title: str, body: str) -> str | None:
    split = _split_managed_body(body)
    if split is None:
        return None
    sections = _sections(body)
    if "Observable Outcomes" not in sections or "Acceptance Criteria" not in sections:
        raise GithubSyncError(
            "GitHub issue has managed Woof headings but is missing required structured sections"
        )
    front: dict[str, Any] = {
        "epic_id": epic_id,
        "title": title,
        "observable_outcomes": _parse_observable_outcomes(sections["Observable Outcomes"]),
        "contract_decisions": _parse_contract_decisions(sections.get("Contract Decisions", "")),
        "acceptance_criteria": _parse_bullets(
            sections["Acceptance Criteria"], label="Acceptance Criteria", require_items=True
        ),
    }
    open_questions = _parse_bullets(sections.get("Open Questions", ""), label="Open Questions")
    if open_questions:
        front["open_questions"] = open_questions

    prose = split[0].strip()
    yaml_text = yaml.safe_dump(front, sort_keys=False)
    return f"---\n{yaml_text}---\n{prose}\n"


def _issue_updated_at(issue: dict[str, Any]) -> str:
    updated_at = issue.get("updated_at") or issue.get("updatedAt") or ""
    if not isinstance(updated_at, str):
        return ""
    return updated_at


def _issue_body(issue: dict[str, Any]) -> str:
    body = issue.get("body") or ""
    if not isinstance(body, str):
        raise GithubSyncError("GitHub issue body is not a string")
    return body


def _spark_markdown(title: str, body: str) -> str:
    split = _split_managed_body(body) if body else None
    prose = (split[0] if split else body).strip()
    if prose:
        return f"# {title.strip()}\n\n{prose}\n"
    return f"# {title.strip()}\n"


def _sections(body: str) -> dict[str, str]:
    matches = list(_HEADING_RE.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[heading] = body[start:end].strip()
    return sections


def _parse_observable_outcomes(section: str) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    lines = section.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        match = _OUTCOME_RE.match(line)
        if not match:
            index += 1
            continue
        outcome_id, statement = match.groups()
        statement, deprecated, replaced_by = _strip_deprecation(statement, _DEPRECATED_OUTCOME_RE)
        verification: str | None = None
        lookahead = index + 1
        while lookahead < len(lines) and not lines[lookahead].lstrip().startswith("- **"):
            verification_match = _VERIFICATION_RE.match(lines[lookahead])
            if verification_match:
                verification = verification_match.group(1).strip()
                break
            lookahead += 1
        if verification not in {"automated", "manual", "hybrid"}:
            raise GithubSyncError(
                f"Observable outcome {outcome_id} has invalid or missing verification"
            )
        item: dict[str, Any] = {
            "id": outcome_id,
            "statement": statement,
            "verification": verification,
        }
        if deprecated:
            item["deprecated"] = True
        if replaced_by:
            item["replaced_by"] = replaced_by
        outcomes.append(item)
        index = lookahead + 1
    if not outcomes:
        raise GithubSyncError("GitHub issue has no parseable observable outcomes")
    return outcomes


def _parse_contract_decisions(section: str) -> list[dict[str, Any]]:
    if not section.strip():
        return []
    decisions: list[dict[str, Any]] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4 or cells[0] == "ID":
            continue
        decision_id, related_raw, title_raw, ref_raw = cells
        if not re.fullmatch(r"CD[1-9]\d*", decision_id):
            continue
        title, deprecated, replaced_by = _strip_deprecation(title_raw, _DEPRECATED_CD_RE)
        item: dict[str, Any] = {
            "id": decision_id,
            "related_outcomes": [
                value.strip() for value in related_raw.split(",") if value.strip()
            ],
            "title": title,
        }
        kind, ref = _parse_contract_ref(ref_raw)
        item[kind] = ref
        if deprecated:
            item["deprecated"] = True
        if replaced_by:
            item["replaced_by"] = replaced_by
        decisions.append(item)
    return decisions


def _parse_contract_ref(raw: str) -> tuple[str, str]:
    text = raw.strip().strip("`").strip()
    if not text:
        raise GithubSyncError("Contract decision row is missing a contract reference")
    if ":" not in text:
        raise GithubSyncError(f"Unsupported contract reference: {raw}")
    kind, ref = [part.strip() for part in text.split(":", 1)]
    field_by_kind = {
        "openapi": "openapi_ref",
        "pydantic": "pydantic_ref",
        "json_schema": "json_schema_ref",
    }
    try:
        field = field_by_kind[kind]
    except KeyError as exc:
        raise GithubSyncError(f"Unsupported contract reference kind: {kind}") from exc
    if not ref:
        raise GithubSyncError(f"Contract reference for {kind} is empty")
    return field, ref


def _strip_deprecation(text: str, pattern: re.Pattern[str]) -> tuple[str, bool, str | None]:
    match = pattern.search(text)
    if not match:
        return text.strip(), False, None
    return text[: match.start()].strip(), True, match.group(1)


def _split_managed_body(body: str) -> tuple[str, str] | None:
    match = _STRUCTURED_HEADING_RE.search(body)
    if not match:
        return None
    return body[: match.start()], body[match.start() :]


def _remote_prefix(remote_body: str | None) -> str | None:
    if remote_body is None:
        return None
    split = _split_managed_body(remote_body)
    if split is None:
        return None
    return split[0]


def _front_intent(front: dict[str, Any]) -> str:
    intent = front.get("intent")
    return intent.strip() if isinstance(intent, str) else ""


def _first_paragraph(prose: str) -> str:
    paragraphs = re.split(r"\n\s*\n", prose.strip())
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        if stripped:
            return _single_line(stripped)
    return ""


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _table_cell(value: str) -> str:
    return _single_line(value).replace("|", r"\|")


def _deprecation_suffix(item: dict[str, Any]) -> str:
    if not item.get("deprecated"):
        return ""
    replaced_by = item.get("replaced_by")
    return f" _(deprecated → {replaced_by})_" if replaced_by else " _(deprecated)_"


def _contract_ref(decision: dict[str, Any]) -> str:
    if decision.get("openapi_ref"):
        return f"`openapi: {decision['openapi_ref']}`"
    if decision.get("pydantic_ref"):
        return f"`pydantic: {decision['pydantic_ref']}`"
    if decision.get("json_schema_ref"):
        return f"`json_schema: {decision['json_schema_ref']}`"
    return ""


def _read_last_sync(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GithubSyncError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise GithubSyncError(f"{path} must contain a JSON object")
    return payload


def _last_sync_text(last_sync: dict[str, Any], field: str) -> str:
    value = last_sync.get(field)
    return value if isinstance(value, str) else ""


def _last_sync_body(last_sync: dict[str, Any]) -> str:
    return _last_sync_text(last_sync, "body")


def _write_last_sync(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".last-sync.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _parse_bullets(markdown: str, *, label: str, require_items: bool = False) -> list[str]:
    items = [
        line.strip()[2:].strip() for line in markdown.splitlines() if line.strip().startswith("- ")
    ]
    if require_items and not items:
        raise GithubSyncError(f"{label} has no parseable bullet items")
    return items


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
