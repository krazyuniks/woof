"""Deterministic transition table for the Woof graph."""

from __future__ import annotations

import json
from pathlib import Path

from woof.graph.state import NodeType, Plan, StorySpec


def epic_dir(repo_root: Path, epic_id: int) -> Path:
    return repo_root / ".woof" / "epics" / f"E{epic_id}"


def load_plan(repo_root: Path, epic_id: int) -> Plan:
    path = epic_dir(repo_root, epic_id) / "plan.json"
    return Plan.model_validate_json(path.read_text())


def write_plan(repo_root: Path, plan: Plan) -> None:
    path = epic_dir(repo_root, plan.epic_id) / "plan.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(plan.model_dump_json(indent=2) + "\n")
    tmp.replace(path)


def story_by_id(plan: Plan, story_id: str) -> StorySpec:
    for story in plan.stories:
        if story.id == story_id:
            return story
    raise ValueError(f"story {story_id} not found in E{plan.epic_id} plan")


def next_ready_story(plan: Plan) -> StorySpec | None:
    done = {story.id for story in plan.stories if story.status == "done"}
    for story in plan.stories:
        if story.status != "pending":
            continue
        if all(dep in done for dep in story.depends_on):
            return story
    return None


def mark_story_status(repo_root: Path, epic_id: int, story_id: str, status: str) -> None:
    plan = load_plan(repo_root, epic_id)
    stories = []
    for story in plan.stories:
        if story.id == story_id:
            data = story.model_dump()
            data["status"] = status
            stories.append(StorySpec.model_validate(data))
        else:
            stories.append(story)
    write_plan(repo_root, Plan(epic_id=plan.epic_id, goal=plan.goal, stories=stories))


def append_epic_event(repo_root: Path, epic_id: int, event: dict) -> None:
    path = epic_dir(repo_root, epic_id) / "epic.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def next_node(repo_root: Path, epic_id: int) -> tuple[NodeType | None, str | None]:
    """Return the next node and story id from filesystem state."""

    directory = epic_dir(repo_root, epic_id)
    if (directory / "gate.md").exists():
        return NodeType.HUMAN_REVIEW, None

    plan = load_plan(repo_root, epic_id)
    if all(story.status == "done" for story in plan.stories):
        return None, None

    in_progress = next((story for story in plan.stories if story.status == "in_progress"), None)
    if in_progress is None:
        ready = next_ready_story(plan)
        if ready is None:
            return NodeType.GATE_OPEN, None
        return NodeType.EXECUTOR_DISPATCH, ready.id

    result_path = directory / "executor_result.json"
    critique_path = directory / "critique" / f"story-{in_progress.id}.md"
    check_result_path = directory / "check-result.json"

    if not result_path.exists():
        return NodeType.EXECUTOR_DISPATCH, in_progress.id

    result = json.loads(result_path.read_text())
    outcome = result.get("outcome")
    if outcome in {"aborted_with_position", "empty_diff"}:
        return NodeType.GATE_OPEN, in_progress.id
    if outcome != "staged_for_verification":
        return NodeType.GATE_OPEN, in_progress.id
    if not critique_path.exists():
        return NodeType.CRITIQUE_DISPATCH, in_progress.id
    if not check_result_path.exists():
        return NodeType.VERIFICATION, in_progress.id

    check_result = json.loads(check_result_path.read_text())
    if not check_result.get("ok", False):
        return NodeType.GATE_OPEN, in_progress.id
    return NodeType.COMMIT, in_progress.id
