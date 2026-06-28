"""Reviewer disposition helpers for Stage-5 graph execution."""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from woof.graph.git import git

# ---------------------------------------------------------------------------
# Blocker-evidence resolution
# ---------------------------------------------------------------------------

# file:line — any non-whitespace path followed by a colon and digits; tracked-set membership
# is the real validator so no extension whitelist is needed here.
_FILE_LINE_RE = re.compile(r"([^\s:]+):(\d+)\b")

# Typed artefact IDs
_STORY_ID_RE = re.compile(r"\bS([1-9]\d*)\b")
_OUTCOME_ID_RE = re.compile(r"\bO([1-9]\d*)\b")
_CD_ID_RE = re.compile(r"\bCD([1-9]\d*)\b")

# Schema refs — path rooted at schemas/ with .schema.json suffix
_SCHEMA_REF_RE = re.compile(r"\b(schemas/[\w./\-]+\.schema\.json)\b")

# Quality-gate refs — explicit gate:<name> prefix required; bare names in prose do not resolve
_GATE_REF_RE = re.compile(r"\bgate:([\w\-]+)")


def resolve_evidence_reference(
    evidence: str,
    *,
    repo_root: Path,
    plan: dict[str, Any],
    epic_dir: Path,
) -> bool:
    """Return True if evidence contains at least one resolvable artefact reference.

    The six reference kinds checked in order:
    - file:line (tracked by git)
    - story id (S<n> present in plan.work_units)
    - observable outcome id (O<n> present in EPIC.md)
    - contract-decision id (CD<n> present in EPIC.md)
    - schema ref (schemas/*.schema.json exists under repo_root)
    - gate:<name> (explicit prefix; <name> present in .woof/quality-gates.toml)
    """
    ev = evidence.strip()
    if not ev:
        return False

    tracked = _evidence_tracked_paths(repo_root)
    if _has_file_line_ref(ev, tracked):
        return True

    story_ids = {
        s["id"]
        for s in plan.get("work_units", [])
        if isinstance(s, dict) and isinstance(s.get("id"), str)
    }
    if _has_pattern_ref(ev, _STORY_ID_RE, story_ids):
        return True

    outcome_ids, cd_ids = _epic_artefact_ids(epic_dir)
    if _has_pattern_ref(ev, _OUTCOME_ID_RE, outcome_ids):
        return True
    if _has_pattern_ref(ev, _CD_ID_RE, cd_ids):
        return True

    if _has_schema_ref(ev, repo_root):
        return True

    gate_names = _quality_gate_names(repo_root)
    return bool(_has_gate_ref(ev, gate_names))


def _evidence_tracked_paths(repo_root: Path) -> set[str]:
    try:
        proc = git(repo_root, "ls-files")
    except (subprocess.CalledProcessError, OSError):
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _has_file_line_ref(evidence: str, tracked: set[str]) -> bool:
    for match in _FILE_LINE_RE.finditer(evidence):
        path = match.group(1).strip("`()")
        if path in tracked:
            return True
    return False


def _has_pattern_ref(evidence: str, pattern: re.Pattern[str], known_ids: set[str]) -> bool:
    return any(match.group(0) in known_ids for match in pattern.finditer(evidence))


def _has_schema_ref(evidence: str, repo_root: Path) -> bool:
    return any((repo_root / match.group(1)).exists() for match in _SCHEMA_REF_RE.finditer(evidence))


def _has_gate_ref(evidence: str, gate_names: set[str]) -> bool:
    return any(m.group(1) in gate_names for m in _GATE_REF_RE.finditer(evidence))


def _epic_artefact_ids(epic_dir: Path) -> tuple[set[str], set[str]]:
    """Return (outcome_ids, cd_ids) from EPIC.md front-matter, or empty sets."""
    epic_path = epic_dir / "EPIC.md"
    try:
        import yaml as _yaml

        text = epic_path.read_text(encoding="utf-8")
    except OSError:
        return set(), set()
    if not text.startswith("---\n"):
        return set(), set()
    end = text.find("\n---\n", 4)
    if end < 0:
        return set(), set()
    try:
        front = _yaml.safe_load(text[4:end]) or {}
    except Exception:
        return set(), set()
    if not isinstance(front, dict):
        return set(), set()

    outcome_ids: set[str] = set()
    for o in front.get("observable_outcomes") or []:
        if isinstance(o, dict) and isinstance(o.get("id"), str):
            outcome_ids.add(o["id"])

    cd_ids: set[str] = set()
    for cd in front.get("contract_decisions") or []:
        if isinstance(cd, dict) and isinstance(cd.get("id"), str):
            cd_ids.add(cd["id"])

    return outcome_ids, cd_ids


def _quality_gate_names(repo_root: Path) -> set[str]:
    toml_path = repo_root / ".woof" / "quality-gates.toml"
    try:
        with toml_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    gates = data.get("gates")
    if not isinstance(gates, dict):
        return set()
    return {str(name) for name in gates}


def check_blocker_findings_evidence(
    blocker_findings: list[dict[str, Any]],
    *,
    repo_root: Path,
    plan: dict[str, Any],
    epic_dir: Path,
) -> list[str]:
    """Return one error message per blocker finding whose evidence is absent or unresolvable."""
    errors: list[str] = []
    for finding in blocker_findings:
        finding_id = str(finding.get("id") or "<unknown>")
        evidence = finding.get("evidence")
        ev_str = str(evidence).strip() if isinstance(evidence, str) else ""
        if not ev_str:
            errors.append(
                f"{finding_id}: blocker finding has no evidence; "
                "blockers must cite a resolvable artefact reference "
                "(file:line, story id, outcome id, contract-decision id, "
                "schema ref, or quality-gate id)"
            )
            continue
        if not resolve_evidence_reference(
            ev_str,
            repo_root=repo_root,
            plan=plan,
            epic_dir=epic_dir,
        ):
            errors.append(
                f"{finding_id}: blocker evidence does not resolve to a known artefact reference; "
                f"evidence was: {ev_str!r}"
            )
    return errors


NON_BLOCKING_SEVERITIES = {"info", "minor"}
SEVERITIES = {*NON_BLOCKING_SEVERITIES, "blocker"}
DISPOSITION_DECISIONS = {"accepted", "rejected", "deferred"}
_SEVERITY_ORDER: dict[str, int] = {"info": 0, "minor": 1, "blocker": 2}


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


def render_deterministic_story_disposition(
    *,
    epic_id: int,
    story_id: str,
    critique: MarkdownFrontMatter,
    timestamp: str,
) -> str:
    """Render a schema-valid disposition for non-blocking reviewer critiques."""
    severity = critique_severity(critique.front)
    if severity not in NON_BLOCKING_SEVERITIES:
        raise ValueError("deterministic dispositions require info or minor severity")

    findings = [
        finding
        for finding in critique_findings(critique.front)
        if str(finding.get("severity", severity)) in NON_BLOCKING_SEVERITIES
        and isinstance(finding.get("id"), str)
    ]
    dispositions = [
        {
            "finding_id": str(finding["id"]),
            "decision": "deferred",
            "rationale": (
                "Reviewer marked this finding non-blocking; Woof recorded a deterministic "
                "disposition and continued to verification without a primary model revision."
            ),
        }
        for finding in findings
    ]
    front = {
        "target": "story",
        "target_id": story_id,
        "critique_path": story_critique_relpath(epic_id, story_id),
        "severity": severity,
        "timestamp": timestamp,
        "harness": "woof-deterministic-disposition",
        "dispositions": dispositions,
    }
    body = (
        "Woof recorded this disposition deterministically because the reviewer critique "
        f"severity is `{severity}`. Non-blocking findings proceed to verification; blocker "
        "findings still open a human gate.\n"
    )
    return "---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n" + body


def write_deterministic_story_disposition(
    *,
    epic_dir: Path,
    epic_id: int,
    story_id: str,
    critique: MarkdownFrontMatter,
    timestamp: str,
) -> Path:
    path = story_disposition_path(epic_dir, story_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        render_deterministic_story_disposition(
            epic_id=epic_id,
            story_id=story_id,
            critique=critique,
            timestamp=timestamp,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


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


def check_critique_rollup(critique_front: dict[str, Any]) -> list[str]:
    """Return an error if top-level severity != max(findings[].severity).

    An empty list means the roll-up is honest. Callers must validate that the
    top-level severity field is a known value before calling this.
    """
    top_sev = critique_severity(critique_front)
    findings = critique_findings(critique_front)
    if not findings:
        if top_sev == "blocker":
            return [
                "critique top-level severity is 'blocker' but has no findings; "
                "a blocker critique requires at least one finding with severity 'blocker'"
            ]
        return []
    max_sev = max(
        (f.get("severity", "info") for f in findings),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
    )
    if _SEVERITY_ORDER.get(top_sev or "", 0) != _SEVERITY_ORDER.get(max_sev, 0):
        return [
            f"critique top-level severity {top_sev!r} != max finding severity {max_sev!r}; "
            "top-level severity must equal the highest finding severity"
        ]
    return []


def validate_critique_invariants(
    critique_front: dict[str, Any],
    *,
    repo_root: Path,
    plan: dict[str, Any],
    epic_dir: Path,
) -> list[str]:
    """Validate roll-up honesty and per-finding blocker evidence.

    Returns one error string per violated invariant; an empty list means both
    invariants hold. Callers must validate that the top-level severity field is
    a known value before calling this.
    """
    errors = check_critique_rollup(critique_front)
    blocker_findings = [
        f for f in critique_findings(critique_front) if f.get("severity") == "blocker"
    ]
    if blocker_findings:
        errors = errors + check_blocker_findings_evidence(
            blocker_findings,
            repo_root=repo_root,
            plan=plan,
            epic_dir=epic_dir,
        )
    return errors


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
