"""Deterministic transition table for the Woof graph."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from woof.graph.git import changed_paths, staged_paths
from woof.graph.manifest import build_story_manifest
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


def epic_event_exists(repo_root: Path, epic_id: int, **fields: object) -> bool:
    path = epic_dir(repo_root, epic_id) / "epic.jsonl"
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if all(event.get(key) == value for key, value in fields.items()):
            return True
    return False


def append_epic_event_once(
    repo_root: Path, epic_id: int, event_payload: dict, **identity: object
) -> None:
    if not epic_event_exists(repo_root, epic_id, **identity):
        append_epic_event(repo_root, epic_id, event_payload)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _has_uncommitted_commit_work(repo_root: Path, epic_id: int, story: StorySpec) -> bool:
    try:
        manifest = build_story_manifest(repo_root, epic_id, story)
        changed = set(changed_paths(repo_root))
        staged = set(staged_paths(repo_root))
    except (subprocess.CalledProcessError, ValueError):
        return False
    expected = set(manifest.expected_paths)
    return bool(staged or changed & expected)


def _resumable_commit_story(repo_root: Path, epic_id: int, plan: Plan) -> str | None:
    directory = epic_dir(repo_root, epic_id)
    result_path = directory / "executor_result.json"
    check_result_path = directory / "check-result.json"
    if not result_path.exists() or not check_result_path.exists():
        return None

    result = _load_json(result_path)
    check_result = _load_json(check_result_path)
    if result.get("outcome") != "staged_for_verification" or not check_result.get("ok", False):
        return None

    story_id = result.get("story_id")
    if not isinstance(story_id, str):
        return None
    try:
        story = story_by_id(plan, story_id)
    except ValueError:
        return None
    if story.status != "done":
        return None
    critique_path = directory / "critique" / f"story-{story.id}.md"
    if not critique_path.exists():
        return None
    if not _has_uncommitted_commit_work(repo_root, epic_id, story):
        result_path.unlink(missing_ok=True)
        check_result_path.unlink(missing_ok=True)
        return None
    return story.id


def next_node(repo_root: Path, epic_id: int) -> tuple[NodeType | None, str | None]:
    """Return the next node and story id from filesystem state."""

    directory = epic_dir(repo_root, epic_id)
    if (directory / "gate.md").exists():
        return NodeType.HUMAN_REVIEW, None

    plan = load_plan(repo_root, epic_id)
    resumable_story = _resumable_commit_story(repo_root, epic_id, plan)
    if resumable_story is not None:
        return NodeType.COMMIT, resumable_story

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
