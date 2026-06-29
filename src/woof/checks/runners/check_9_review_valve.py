"""check_9_review_valve - Stage-5 Check 9.

Opens a review valve when accumulated minor critique findings should be
surfaced for human review. The valve fires after every configured work-unit count
and, by default, at the end of the epic. It only fires when there are minor
findings since the last review gate.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from woof.checks import CheckContext, CheckOutcome
from woof.graph.state import TERMINAL_WORK_UNIT_STATES

CHECK_ID = "check_9_review_valve"
CONFIG_PATH = ".woof/agents.toml"
DEFAULT_EVERY_N_WORK_UNITS = 5
DEFAULT_END_OF_EPIC = True


@dataclass(frozen=True)
class _ReviewConfig:
    every_n_work_units: int
    end_of_epic: bool


@dataclass(frozen=True)
class _MinorFinding:
    work_unit_id: str
    finding_id: str
    summary: str


def check_9_review_valve_runner(ctx: CheckContext) -> CheckOutcome:
    config = _load_config(ctx.repo_root / CONFIG_PATH)
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

    periodic_due = completed_count % config.every_n_work_units == 0
    end_due = config.end_of_epic and _is_end_of_epic(work_units, ctx.work_unit_id)
    if not periodic_due and not end_due:
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


def _load_config(path: Path) -> _ReviewConfig | CheckOutcome:
    if not path.exists():
        return _ReviewConfig(
            every_n_work_units=DEFAULT_EVERY_N_WORK_UNITS,
            end_of_epic=DEFAULT_END_OF_EPIC,
        )

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"malformed agents config: TOML parse error: {exc}",
            paths=[CONFIG_PATH],
        )

    review_valve = data.get("review_valve", {})
    if review_valve is None:
        review_valve = {}
    if not isinstance(review_valve, dict):
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="malformed agents config: review_valve must be a table",
            paths=[CONFIG_PATH],
        )

    every_n = review_valve.get("every_n_work_units", DEFAULT_EVERY_N_WORK_UNITS)
    end_of_epic = review_valve.get("end_of_epic", DEFAULT_END_OF_EPIC)
    if type(every_n) is not int or every_n < 1:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=(
                "malformed agents config: review_valve.every_n_work_units must be an integer >= 1"
            ),
            paths=[CONFIG_PATH],
        )
    if not isinstance(end_of_epic, bool):
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="malformed agents config: review_valve.end_of_epic must be a boolean",
            paths=[CONFIG_PATH],
        )

    return _ReviewConfig(every_n_work_units=every_n, end_of_epic=end_of_epic)


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
