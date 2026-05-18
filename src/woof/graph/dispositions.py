"""Reviewer disposition helpers for Stage-5 graph execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

NON_BLOCKING_SEVERITIES = {"info", "minor"}
SEVERITIES = {*NON_BLOCKING_SEVERITIES, "blocker"}
DISPOSITION_DECISIONS = {"accepted", "rejected", "deferred"}


class FrontMatterError(ValueError):
    """Raised when a markdown artefact has missing or malformed front-matter."""


@dataclass(frozen=True)
class MarkdownFrontMatter:
    front: dict[str, Any]
    body: str


@dataclass(frozen=True)
class DispositionValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    severity: str | None = None
    finding_count: int = 0


def story_critique_path(epic_dir: Path, story_id: str) -> Path:
    return epic_dir / "critique" / f"story-{story_id}.md"


def story_disposition_path(epic_dir: Path, story_id: str) -> Path:
    return epic_dir / "dispositions" / f"story-{story_id}.md"


def story_critique_relpath(epic_id: int, story_id: str) -> str:
    return f".woof/epics/E{epic_id}/critique/story-{story_id}.md"


def story_disposition_relpath(epic_id: int, story_id: str) -> str:
    return f".woof/epics/E{epic_id}/dispositions/story-{story_id}.md"


def read_markdown_front_matter(path: Path) -> MarkdownFrontMatter:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise FrontMatterError(f"{path}: missing YAML front-matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise FrontMatterError(f"{path}: unterminated YAML front-matter")
    try:
        front = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"{path}: YAML parse error: {exc}") from exc
    if not isinstance(front, dict):
        raise FrontMatterError(f"{path}: YAML front-matter must be a mapping")
    return MarkdownFrontMatter(
        front={str(key): value for key, value in front.items()}, body=text[end + 5 :]
    )


def critique_severity(critique_front: dict[str, Any]) -> str | None:
    severity = critique_front.get("severity")
    return severity if isinstance(severity, str) and severity in SEVERITIES else None


def critique_findings(critique_front: dict[str, Any]) -> list[dict[str, Any]]:
    raw_findings = critique_front.get("findings") or []
    if not isinstance(raw_findings, list):
        return []
    return [finding for finding in raw_findings if isinstance(finding, dict)]


def validate_story_disposition(
    epic_dir: Path, epic_id: int, story_id: str
) -> DispositionValidation:
    critique_path = story_critique_path(epic_dir, story_id)
    disposition_path = story_disposition_path(epic_dir, story_id)
    try:
        critique = read_markdown_front_matter(critique_path).front
    except (FileNotFoundError, FrontMatterError) as exc:
        return DispositionValidation(ok=False, errors=[str(exc)])

    severity = critique_severity(critique)
    if severity == "blocker":
        return DispositionValidation(
            ok=False,
            errors=["blocker critiques open a human gate and must not have a primary disposition"],
            severity=severity,
            finding_count=len(critique_findings(critique)),
        )
    if severity not in NON_BLOCKING_SEVERITIES:
        return DispositionValidation(
            ok=False,
            errors=["critique severity must be info, minor, or blocker"],
            severity=severity,
            finding_count=len(critique_findings(critique)),
        )
    if not disposition_path.exists():
        return DispositionValidation(
            ok=False,
            errors=[f"disposition file missing: {story_disposition_relpath(epic_id, story_id)}"],
            severity=severity,
            finding_count=len(critique_findings(critique)),
        )

    try:
        disposition = read_markdown_front_matter(disposition_path).front
    except FrontMatterError as exc:
        return DispositionValidation(
            ok=False,
            errors=[str(exc)],
            severity=severity,
            finding_count=len(critique_findings(critique)),
        )

    errors = validate_disposition_front_matter(
        disposition,
        critique,
        epic_id=epic_id,
        story_id=story_id,
    )
    return DispositionValidation(
        ok=not errors,
        errors=errors,
        severity=severity,
        finding_count=len(critique_findings(critique)),
    )


def validate_disposition_front_matter(
    disposition: dict[str, Any],
    critique: dict[str, Any],
    *,
    epic_id: int,
    story_id: str,
) -> list[str]:
    errors: list[str] = []
    expected_critique_path = story_critique_relpath(epic_id, story_id)
    severity = critique_severity(critique)

    if disposition.get("target") != "story":
        errors.append("disposition target must be 'story'")
    if disposition.get("target_id") != story_id:
        errors.append(f"disposition target_id must be {story_id}")
    if disposition.get("critique_path") != expected_critique_path:
        errors.append(f"disposition critique_path must be {expected_critique_path}")
    if disposition.get("severity") != severity:
        errors.append(f"disposition severity must match critique severity {severity!r}")
    if not isinstance(disposition.get("timestamp"), str) or not disposition["timestamp"].strip():
        errors.append("disposition timestamp must be a non-empty string")
    if not isinstance(disposition.get("harness"), str) or not disposition["harness"].strip():
        errors.append("disposition harness must be a non-empty string")

    raw_dispositions = disposition.get("dispositions")
    if not isinstance(raw_dispositions, list):
        errors.append("dispositions must be an array")
        raw_dispositions = []

    findings = critique_findings(critique)
    expected_ids = [
        str(finding.get("id"))
        for finding in findings
        if isinstance(finding.get("id"), str)
        and str(finding.get("severity", severity)) in NON_BLOCKING_SEVERITIES
    ]
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw_dispositions):
        if not isinstance(entry, dict):
            errors.append(f"dispositions[{index}] must be a table/object")
            continue
        finding_id = entry.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id:
            errors.append(f"dispositions[{index}].finding_id must be a non-empty string")
            continue
        if finding_id in seen_ids:
            errors.append(f"duplicate disposition for finding {finding_id}")
        seen_ids.add(finding_id)
        if finding_id not in expected_ids:
            errors.append(f"disposition references unknown finding {finding_id}")
        decision = entry.get("decision")
        if decision not in DISPOSITION_DECISIONS:
            errors.append(f"dispositions[{index}].decision must be accepted, rejected, or deferred")
        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"dispositions[{index}].rationale must be a non-empty string")

    missing = sorted(set(expected_ids) - seen_ids)
    if missing:
        errors.append(f"missing dispositions for findings: {missing}")
    return errors


def reviewer_blocker_gate_body(
    *,
    epic_id: int,
    story_id: str,
    critique: MarkdownFrontMatter,
) -> str:
    findings = [
        finding
        for finding in critique_findings(critique.front)
        if finding.get("severity") == "blocker"
    ]
    if not findings:
        findings = critique_findings(critique.front)

    finding_lines = []
    for finding in findings:
        finding_id = str(finding.get("id") or "finding")
        summary = str(finding.get("summary") or "reviewer blocker")
        finding_lines.append(f"- {finding_id}: {summary}")
        evidence = finding.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            finding_lines.append(f"  Evidence: {evidence.strip()}")
    if not finding_lines:
        finding_lines.append("- Reviewer marked the critique as blocker.")

    critique_rel = story_critique_relpath(epic_id, story_id)
    body = critique.body.strip() or "Reviewer body was empty."
    return (
        "## Context\n\n"
        f"Reviewer critique `{critique_rel}` marked story {story_id} as blocker. "
        "Woof does not start a model-to-model debate loop for blocker findings.\n\n"
        "## Findings\n\n" + "\n".join(finding_lines) + "\n\n## Primary position\n\n"
        "The primary story output remains staged for operator inspection. "
        "No primary disposition was requested because blocker findings require a human gate.\n\n"
        "## Reviewer position\n\n"
        f"Source: `{critique_rel}`\n\n"
        f"{body}\n"
    )
