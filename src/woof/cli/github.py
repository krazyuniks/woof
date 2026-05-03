"""GitHub issue synchronisation helpers for Woof CLI commands."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


class GithubSyncError(RuntimeError):
    """GitHub synchronisation failed and must not be silently ignored."""


@dataclass(frozen=True)
class ColdStartResult:
    epic_id: int
    epic_dir: Path
    spark_path: Path
    epic_path: Path | None
    last_sync_path: Path


STRUCTURED_HEADING = "## Observable Outcomes"
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
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


def initialise_epic_from_issue(repo_root: Path, epic_id: int) -> ColdStartResult:
    repo = load_github_repo(repo_root)
    issue = fetch_issue(repo, epic_id)
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


def epic_markdown_from_issue(*, epic_id: int, title: str, body: str) -> str | None:
    if STRUCTURED_HEADING not in body:
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

    prose = body.split(STRUCTURED_HEADING, 1)[0].strip()
    yaml_text = yaml.safe_dump(front, sort_keys=False)
    return f"---\n{yaml_text}---\n{prose}\n"


def _issue_updated_at(issue: dict[str, Any]) -> str:
    updated_at = issue.get("updated_at") or issue.get("updatedAt") or ""
    if not isinstance(updated_at, str):
        return ""
    return updated_at


def _spark_markdown(title: str, body: str) -> str:
    prose = body.split(STRUCTURED_HEADING, 1)[0].strip() if body else ""
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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
