"""check_9_review_valve - Stage-5 Check 9.

Opens a review valve when accumulated minor critique findings should be
surfaced for human review. The valve fires after every configured story count
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

CHECK_ID = "check_9_review_valve"
CONFIG_PATH = ".woof/agents.toml"
DEFAULT_EVERY_N_STORIES = 5
DEFAULT_END_OF_EPIC = True


@dataclass(frozen=True)
class _ReviewConfig:
    every_n_stories: int
    end_of_epic: bool


@dataclass(frozen=True)
class _MinorFinding:
    story_id: str
    finding_id: str
    summary: str


def check_9_review_valve_runner(ctx: CheckContext) -> CheckOutcome:
    config = _load_config(ctx.repo_root / CONFIG_PATH)
    if isinstance(config, CheckOutcome):
        return config

    stories = ctx.plan.get("stories")
    if not isinstance(stories, list) or not stories:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="plan contains no stories; review valve cannot determine cadence",
        )

    story_ids = [story.get("id") for story in stories if isinstance(story, dict)]
    if ctx.story_id not in story_ids:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"story {ctx.story_id!r} not found in plan",
        )

    boundary_index = story_ids.index(ctx.story_id)
    completed_count = _completed_count_through_boundary(stories, ctx.story_id)
    if completed_count < 1:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"{ctx.story_id} is not at a review-valve boundary",
        )

    if _open_review_gate_matches(ctx, ctx.story_id):
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"review gate already open for {ctx.story_id}",
        )

    last_review_index = _last_review_boundary_index(ctx.epic_dir / "epic.jsonl", story_ids)
    if last_review_index >= boundary_index:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"minor findings through {ctx.story_id} have already been review-gated",
        )

    periodic_due = completed_count % config.every_n_stories == 0
    end_due = config.end_of_epic and _is_end_of_epic(stories, ctx.story_id)
    if not periodic_due and not end_due:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=(
                f"review valve not due after {completed_count} completed story candidate(s); "
                f"cadence is every {config.every_n_stories}"
            ),
        )

    minor_findings = _minor_findings_since(
        ctx, story_ids[last_review_index + 1 : boundary_index + 1]
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
        reasons.append(f"every {config.every_n_stories} stories")
    if end_due:
        reasons.append("end of epic")

    return CheckOutcome(
        id=CHECK_ID,
        ok=False,
        severity="minor",
        summary=(
            f"review valve due at {ctx.story_id} ({' and '.join(reasons)}); "
            f"{len(minor_findings)} minor finding(s) require review"
        ),
        evidence="\n".join(
            f"{finding.story_id}/{finding.finding_id}: {finding.summary}"
            for finding in minor_findings
        ),
        paths=[
            str(
                (ctx.epic_dir / "critique" / f"story-{finding.story_id}.md").relative_to(
                    ctx.repo_root
                )
            )
            for finding in minor_findings
        ],
    )


def _load_config(path: Path) -> _ReviewConfig | CheckOutcome:
    if not path.exists():
        return _ReviewConfig(
            every_n_stories=DEFAULT_EVERY_N_STORIES,
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

    every_n = review_valve.get("every_n_stories", DEFAULT_EVERY_N_STORIES)
    end_of_epic = review_valve.get("end_of_epic", DEFAULT_END_OF_EPIC)
    if type(every_n) is not int or every_n < 1:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="malformed agents config: review_valve.every_n_stories must be an integer >= 1",
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

    return _ReviewConfig(every_n_stories=every_n, end_of_epic=end_of_epic)


def _completed_count_through_boundary(stories: list[Any], current_story_id: str) -> int:
    count = 0
    for story in stories:
        if not isinstance(story, dict):
            continue
        story_id = story.get("id")
        status = story.get("status")
        if story_id == current_story_id:
            return count + (1 if status in {"in_progress", "done"} else 0)
        if status == "done":
            count += 1
    return count


def _is_end_of_epic(stories: list[Any], current_story_id: str) -> bool:
    seen_current = False
    for story in stories:
        if not isinstance(story, dict):
            continue
        if story.get("id") == current_story_id:
            seen_current = True
            continue
        if seen_current and story.get("status") != "done":
            return False
    return seen_current


def _last_review_boundary_index(epic_jsonl: Path, story_ids: list[str]) -> int:
    if not epic_jsonl.exists():
        return -1

    index_by_story = {story_id: index for index, story_id in enumerate(story_ids)}
    last = -1
    for line in epic_jsonl.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") != "review_gate_opened":
            continue
        if CHECK_ID not in event.get("triggered_by", []):
            continue
        story_id = event.get("story_id")
        if isinstance(story_id, str) and story_id in index_by_story:
            last = max(last, index_by_story[story_id])
    return last


def _open_review_gate_matches(ctx: CheckContext, story_id: str) -> bool:
    gate_path = ctx.epic_dir / "gate.md"
    if not gate_path.exists():
        return False
    try:
        front = _load_front_matter(gate_path)
    except (ValueError, yaml.YAMLError):
        return False
    return (
        front.get("type") == "review_gate"
        and front.get("story_id") == story_id
        and CHECK_ID in front.get("triggered_by", [])
    )


def _minor_findings_since(
    ctx: CheckContext, story_ids: list[str]
) -> list[_MinorFinding] | CheckOutcome:
    findings: list[_MinorFinding] = []
    for story_id in story_ids:
        critique_path = ctx.epic_dir / "critique" / f"story-{story_id}.md"
        if not critique_path.exists():
            continue
        try:
            front = _load_front_matter(critique_path)
        except (ValueError, yaml.YAMLError) as exc:
            return CheckOutcome(
                id=CHECK_ID,
                ok=False,
                severity="blocker",
                summary=f"critique/story-{story_id}.md front-matter unreadable: {exc}",
                paths=[str(critique_path.relative_to(ctx.repo_root))],
            )
        for finding in front.get("findings") or []:
            if not isinstance(finding, dict) or finding.get("severity") != "minor":
                continue
            finding_id = str(finding.get("id") or "F?")
            summary = str(finding.get("summary") or "minor critique finding")
            findings.append(
                _MinorFinding(story_id=story_id, finding_id=finding_id, summary=summary)
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
