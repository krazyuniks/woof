"""check_9_review_valve - Stage-5 Check 9.

Opens a review valve when accumulated minor critique findings should be
surfaced for human review. The valve fires after every configured work-unit count
and, by default, at the end of the epic. It only fires when there are minor
findings since the last review gate.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from woof.checks import CheckContext, CheckOutcome
from woof.graph.state import TERMINAL_WORK_UNIT_STATES
from woof.project_config import ProjectConfigError, load_project_config

CHECK_ID = "check_9_review_valve"
KNOWN_GENERATED_PATHS = {
    ".woof/codebase/files.txt",
    ".woof/codebase/freshness.json",
    ".woof/codebase/tags",
}
KNOWN_GENERATED_PREFIXES = (".woof/codebase/structural/",)


@dataclass(frozen=True)
class _ReviewConfig:
    every_n_work_units: int
    end_of_epic: bool


@dataclass(frozen=True)
class _ReviewSizeConfig:
    max_non_generated_changed_lines: int


@dataclass(frozen=True)
class _DiffStat:
    path: str
    changed_lines: int


@dataclass(frozen=True)
class _MinorFinding:
    work_unit_id: str
    finding_id: str
    summary: str


def check_9_review_valve_runner(ctx: CheckContext) -> CheckOutcome:
    config = _load_config()
    if isinstance(config, CheckOutcome):
        return config

    work_units = ctx.plan.get("work_units")
    if not isinstance(work_units, list) or not work_units:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="plan contains no work units; review valve cannot determine cadence",
        )

    work_unit_ids = [
        sid
        for unit in work_units
        if isinstance(unit, dict) and isinstance(sid := unit.get("id"), str)
    ]
    if ctx.work_unit_id not in work_unit_ids:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"work unit {ctx.work_unit_id!r} not found in plan",
        )

    boundary_index = work_unit_ids.index(ctx.work_unit_id)
    completed_count = _completed_count_through_boundary(work_units, ctx.work_unit_id)
    if completed_count < 1:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"{ctx.work_unit_id} is not at a review-valve boundary",
        )

    if _open_review_gate_matches(ctx, ctx.work_unit_id):
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"review gate already open for {ctx.work_unit_id}",
        )

    last_review_index = _last_review_boundary_index(ctx.epic_dir / "epic.jsonl", work_unit_ids)
    if last_review_index >= boundary_index:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"minor findings through {ctx.work_unit_id} have already been review-gated",
        )

    review_size = _review_size_outcome(ctx)
    if isinstance(review_size, CheckOutcome) and not review_size.ok:
        return review_size

    periodic_due = completed_count % config.every_n_work_units == 0
    end_due = config.end_of_epic and _is_end_of_epic(work_units, ctx.work_unit_id)
    if not periodic_due and not end_due:
        if review_size is not None:
            return review_size
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=(
                f"review valve not due after {completed_count} completed work-unit candidate(s); "
                f"cadence is every {config.every_n_work_units}"
            ),
        )

    minor_findings = _minor_findings_since(
        ctx, work_unit_ids[last_review_index + 1 : boundary_index + 1]
    )
    if isinstance(minor_findings, CheckOutcome):
        return minor_findings

    if not minor_findings:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary="review valve due, but no minor critique findings accumulated",
        )

    reasons = []
    if periodic_due:
        reasons.append(f"every {config.every_n_work_units} work units")
    if end_due:
        reasons.append("end of epic")

    return CheckOutcome(
        id=CHECK_ID,
        ok=False,
        severity="minor",
        summary=(
            f"review valve due at {ctx.work_unit_id} ({' and '.join(reasons)}); "
            f"{len(minor_findings)} minor finding(s) require review"
        ),
        evidence="\n".join(
            f"{finding.work_unit_id}/{finding.finding_id}: {finding.summary}"
            for finding in minor_findings
        ),
        paths=[
            str(
                (ctx.epic_dir / "critique" / f"work-unit-{finding.work_unit_id}.md").relative_to(
                    ctx.repo_root
                )
            )
            for finding in minor_findings
        ],
    )


def _load_config() -> _ReviewConfig | CheckOutcome:
    try:
        config = load_project_config()
    except ProjectConfigError as exc:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=str(exc),
            paths=[],
        )
    return _ReviewConfig(
        every_n_work_units=config.review_valve.every_n_work_units,
        end_of_epic=config.review_valve.end_of_epic,
    )


def _review_size_outcome(ctx: CheckContext) -> CheckOutcome | None:
    config = _load_review_size_config()
    if config is None or isinstance(config, CheckOutcome):
        return config

    diff_stats = _staged_diff_stats(ctx.repo_root)
    if isinstance(diff_stats, CheckOutcome):
        return diff_stats

    attrs = _linguist_generated_paths(ctx.repo_root, [stat.path for stat in diff_stats])
    if isinstance(attrs, CheckOutcome):
        return attrs

    counted = 0
    excluded = 0
    counted_lines: list[str] = []
    excluded_lines: list[str] = []
    paths: list[str] = []

    for stat in diff_stats:
        paths.append(stat.path)
        reason = _generated_reason(ctx.repo_root, stat.path, attrs)
        if reason is not None:
            excluded += stat.changed_lines
            excluded_lines.append(
                f"{stat.path}: {stat.changed_lines} generated changed line(s) excluded ({reason})"
            )
            continue
        counted += stat.changed_lines
        counted_lines.append(f"{stat.path}: {stat.changed_lines} non-generated changed line(s)")

    threshold = config.max_non_generated_changed_lines
    evidence_lines = counted_lines + excluded_lines
    if counted > threshold:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="minor",
            summary=(
                f"review-size guard due at {ctx.work_unit_id}; "
                f"{counted} non-generated changed line(s) exceed policy threshold {threshold}"
            ),
            evidence="\n".join(evidence_lines) if evidence_lines else None,
            paths=sorted(paths),
            command="git diff --cached --numstat --",
        )

    if excluded:
        summary = (
            f"review-size guard below threshold: {counted} non-generated changed line(s) "
            f"(policy threshold {threshold}); excluded {excluded} generated changed line(s)"
        )
    else:
        summary = (
            f"review-size guard below threshold: {counted} non-generated changed line(s) "
            f"(policy threshold {threshold})"
        )
    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=summary,
        evidence="\n".join(excluded_lines) if excluded_lines else None,
        paths=sorted(paths),
        command="git diff --cached --numstat --",
    )


def _load_review_size_config() -> _ReviewSizeConfig | CheckOutcome | None:
    """Return the review-size guard, or None when the project declares none."""

    try:
        config = load_project_config()
    except ProjectConfigError as exc:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=str(exc),
            paths=[],
        )
    if config.checks.review_size is None:
        return None
    return _ReviewSizeConfig(
        max_non_generated_changed_lines=(config.checks.review_size.max_non_generated_changed_lines)
    )


def _staged_diff_stats(repo_root: Path) -> list[_DiffStat] | CheckOutcome:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--numstat", "--"],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="failed to read staged diff size",
            evidence=proc.stderr.strip() or None,
            command="git diff --cached --numstat --",
            exit_code=proc.returncode,
        )

    stats: list[_DiffStat] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted, path = parts[0], parts[1], parts[2]
        changed_lines = (int(added) if added.isdigit() else 0) + (
            int(deleted) if deleted.isdigit() else 0
        )
        stats.append(_DiffStat(path=path, changed_lines=changed_lines))
    return stats


def _linguist_generated_paths(repo_root: Path, paths: list[str]) -> set[str] | CheckOutcome:
    if not paths:
        return set()
    proc = subprocess.run(
        ["git", "check-attr", "-z", "linguist-generated", "--", *paths],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="failed to read linguist-generated attributes",
            evidence=proc.stderr.decode(errors="replace").strip() or None,
            command="git check-attr -z linguist-generated -- <staged paths>",
            exit_code=proc.returncode,
        )

    generated: set[str] = set()
    parts = [part.decode(errors="replace") for part in proc.stdout.split(b"\0") if part]
    for index in range(0, len(parts) - 2, 3):
        path, _attr, value = parts[index], parts[index + 1], parts[index + 2]
        if value in {"set", "true"}:
            generated.add(path)
    return generated


def _generated_reason(repo_root: Path, path: str, linguist_generated: set[str]) -> str | None:
    if path in linguist_generated:
        return "linguist-generated"
    if path in KNOWN_GENERATED_PATHS or path.startswith(KNOWN_GENERATED_PREFIXES):
        return "known generated artefact"
    if _has_generated_header(repo_root, path):
        return "generated header"
    return None


def _has_generated_header(repo_root: Path, path: str) -> bool:
    text = _git_blob_text(repo_root, f":{path}")
    if text is None:
        text = _git_blob_text(repo_root, f"HEAD:{path}")
    if text is None:
        return False

    header = "\n".join(text.splitlines()[:5]).lower()
    return (
        "@generated" in header
        or "code generated" in header
        or "auto-generated" in header
        or "autogenerated" in header
        or ("generated" in header and "do not edit" in header)
    )


def _git_blob_text(repo_root: Path, revision: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", revision],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout[:8192].decode(errors="replace")


def _completed_count_through_boundary(work_units: list[Any], current_work_unit_id: str) -> int:
    count = 0
    for unit in work_units:
        if not isinstance(unit, dict):
            continue
        work_unit_id = unit.get("id")
        state = unit.get("state")
        if work_unit_id == current_work_unit_id:
            return count + (1 if state in {"in_progress", "done"} else 0)
        if state == "done":
            count += 1
    return count


def _is_end_of_epic(work_units: list[Any], current_work_unit_id: str) -> bool:
    seen_current = False
    for unit in work_units:
        if not isinstance(unit, dict):
            continue
        if unit.get("id") == current_work_unit_id:
            seen_current = True
            continue
        if seen_current and unit.get("state") not in TERMINAL_WORK_UNIT_STATES:
            return False
    return seen_current


def _last_review_boundary_index(epic_jsonl: Path, work_unit_ids: list[str]) -> int:
    if not epic_jsonl.exists():
        return -1

    index_by_work_unit = {work_unit_id: index for index, work_unit_id in enumerate(work_unit_ids)}
    # Replay the audit log in order. A review gate at work unit K bundles every minor
    # finding from the previous boundary through K, so it arms the boundary at K.
    # A later work_unit_retried at K re-arms only that work unit by event order: its fresh
    # critique (recorded after the retry, on a file the retry deleted) must be able
    # to open a new gate, while the earlier siblings already bundled into the prior
    # gate stay suppressed. So a retry lowers K's armed boundary to K-1 rather than
    # discarding it (which would drop the boundary below the siblings and re-surface
    # their already-gated findings). Tracking armed boundaries per work unit rather than
    # a running max keeps a still-armed later gate from being clobbered by an
    # earlier work-unit retry.
    gated_index_by_work_unit: dict[str, int] = {}
    for line in epic_jsonl.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        work_unit_id = event.get("work_unit_id")
        if not isinstance(work_unit_id, str) or work_unit_id not in index_by_work_unit:
            continue
        event_name = event.get("event")
        if event_name == "review_gate_opened" and CHECK_ID in event.get("triggered_by", []):
            gated_index_by_work_unit[work_unit_id] = index_by_work_unit[work_unit_id]
        elif event_name == "work_unit_retried" and work_unit_id in gated_index_by_work_unit:
            # Re-arm only the retried work unit: keep the siblings gated through the
            # index just before it. (No-op when the work unit was never review-gated,
            # e.g. a work_unit_gate retried before any review valve fired.)
            gated_index_by_work_unit[work_unit_id] = index_by_work_unit[work_unit_id] - 1
    return max(gated_index_by_work_unit.values(), default=-1)


def _open_review_gate_matches(ctx: CheckContext, work_unit_id: str) -> bool:
    gate_path = ctx.epic_dir / "gate.md"
    if not gate_path.exists():
        return False
    try:
        front = _load_front_matter(gate_path)
    except (ValueError, yaml.YAMLError):
        return False
    return (
        front.get("type") == "review_gate"
        and front.get("work_unit_id") == work_unit_id
        and CHECK_ID in front.get("triggered_by", [])
    )


def _minor_findings_since(
    ctx: CheckContext, work_unit_ids: list[str]
) -> list[_MinorFinding] | CheckOutcome:
    findings: list[_MinorFinding] = []
    for work_unit_id in work_unit_ids:
        critique_path = ctx.epic_dir / "critique" / f"work-unit-{work_unit_id}.md"
        if not critique_path.exists():
            continue
        try:
            front = _load_front_matter(critique_path)
        except (ValueError, yaml.YAMLError) as exc:
            return CheckOutcome(
                id=CHECK_ID,
                ok=False,
                severity="blocker",
                summary=f"critique/work-unit-{work_unit_id}.md front-matter unreadable: {exc}",
                paths=[str(critique_path.relative_to(ctx.repo_root))],
            )
        for finding in front.get("findings") or []:
            if not isinstance(finding, dict) or finding.get("severity") != "minor":
                continue
            finding_id = str(finding.get("id") or "F?")
            summary = str(finding.get("summary") or "minor critique finding")
            findings.append(
                _MinorFinding(work_unit_id=work_unit_id, finding_id=finding_id, summary=summary)
            )
    return findings


def _load_front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError("missing YAML front-matter")
    match = re.search(r"\n---\n", text[4:])
    if not match:
        raise ValueError("unterminated YAML front-matter")
    end = 4 + match.start()
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict):
        raise ValueError("YAML front-matter must be a mapping")
    return front
