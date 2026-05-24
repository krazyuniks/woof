"""`woof wf` - deterministic graph entry point."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from woof.graph.lock import WorkflowLockError
from woof.graph.runner import run_graph
from woof.graph.state import GateDecision, NodeStatus, Plan, StorySpec
from woof.graph.transitions import (
    StageStateError,
    append_epic_event,
    append_epic_event_once,
    epic_dir,
    load_plan,
    write_plan,
)
from woof.paths import find_project_root
from woof.trackers import (
    CONFLICT_DECISIONS,
    CONFLICT_TRIGGERS,
    Tracker,
    TrackerError,
    resolve_tracker,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gate_front(gate_path: Path) -> dict:
    text = gate_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict):
        return {}
    return front


def _gate_resolved_event_name(gate_type: str | None) -> str | None:
    if gate_type == "plan_gate":
        return "plan_gate_resolved"
    if gate_type == "story_gate":
        return "story_gate_resolved"
    if gate_type == "review_gate":
        return "review_gate_resolved"
    return None


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _remove_paths(repo_root: Path, *paths: Path) -> list[str]:
    removed: list[str] = []
    for path in paths:
        if path.exists():
            path.unlink()
            removed.append(_display_path(repo_root, path))
    return removed


def _update_story(repo_root: Path, epic_id: int, story_id: str, **updates: object) -> None:
    plan = load_plan(repo_root, epic_id)
    stories: list[StorySpec] = []
    found = False
    for story in plan.stories:
        if story.id == story_id:
            data = story.model_dump()
            data.update(updates)
            stories.append(StorySpec.model_validate(data))
            found = True
        else:
            stories.append(story)
    if not found:
        raise StageStateError(f"story {story_id} not found in E{epic_id} plan")
    write_plan(repo_root, Plan(epic_id=plan.epic_id, goal=plan.goal, stories=stories))


def _apply_gate_resolution_effects(
    repo_root: Path,
    epic_id: int,
    *,
    decision: GateDecision,
    gate_type: str | None,
    story_id: str | None,
    triggered_by: list[str],
    tracker: Tracker,
) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    changed: list[str] = []

    if any(trigger in CONFLICT_TRIGGERS for trigger in triggered_by):
        if decision not in CONFLICT_DECISIONS:
            raise StageStateError(
                "tracker_sync_conflict gates require one of: "
                + ", ".join(sorted(CONFLICT_DECISIONS))
            )
        result = tracker.resolve_conflict(epic_id, decision)
        changed.append(_display_path(repo_root, result.last_sync_path))
        if result.epic_path is not None:
            changed.append(_display_path(repo_root, result.epic_path))
        return changed

    if decision in CONFLICT_DECISIONS:
        raise StageStateError(f"{decision} is only valid for tracker_sync_conflict gates")

    if gate_type == "plan_gate":
        if decision not in {"approve", "revise_epic_contract", "revise_plan", "abandon_epic"}:
            raise StageStateError(f"{decision} is not valid for plan_gate")
        if decision == "approve":
            tracker.push_plan_summary(epic_id)
            return changed
        if decision in {"revise_plan", "revise_epic_contract"}:
            changed.extend(
                _remove_paths(
                    repo_root,
                    directory / "plan.json",
                    directory / "PLAN.md",
                    directory / "critique" / "plan.md",
                )
            )
            return changed
        return changed

    if gate_type in {"story_gate", "review_gate"}:
        if decision not in {
            "approve",
            "revise_story_scope",
            "split_story",
            "revise_plan",
            "abandon_story",
            "abandon_epic",
        }:
            raise StageStateError(f"{decision} is not valid for {gate_type}")
        check_result = directory / "check-result.json"
        executor_result = directory / "executor_result.json"
        if decision == "approve":
            changed.extend(_remove_paths(repo_root, check_result))
            if story_id and "empty_diff_review" in triggered_by:
                _update_story(repo_root, epic_id, story_id, status="done", empty_diff=True)
                changed.append(_display_path(repo_root, directory / "plan.json"))
                changed.extend(_remove_paths(repo_root, executor_result))
                append_epic_event_once(
                    repo_root,
                    epic_id,
                    {
                        "event": "story_completed",
                        "at": _now(),
                        "epic_id": epic_id,
                        "story_id": story_id,
                        "empty_diff": True,
                    },
                    event="story_completed",
                    story_id=story_id,
                )
            return changed
        if decision in {"revise_story_scope", "split_story", "revise_plan"}:
            changed.extend(_remove_paths(repo_root, check_result))
            if decision == "revise_plan":
                changed.extend(
                    _remove_paths(
                        repo_root,
                        directory / "plan.json",
                        directory / "PLAN.md",
                        directory / "critique" / "plan.md",
                    )
                )
            return changed
        if decision == "abandon_story" and story_id:
            _update_story(repo_root, epic_id, story_id, status="done")
            changed.append(_display_path(repo_root, directory / "plan.json"))
            changed.extend(_remove_paths(repo_root, check_result, executor_result))
            append_epic_event_once(
                repo_root,
                epic_id,
                {
                    "event": "story_completed",
                    "at": _now(),
                    "epic_id": epic_id,
                    "story_id": story_id,
                    "decision": "abandon_story",
                },
                event="story_completed",
                story_id=story_id,
            )
            return changed
    return changed


def _resolve_gate(repo_root: Path, epic_id: int, decision: GateDecision, tracker: Tracker) -> int:
    gate = epic_dir(repo_root, epic_id) / "gate.md"
    if not gate.exists():
        sys.stderr.write(f"woof wf: no open gate at {gate}\n")
        return 2
    front = _gate_front(gate)
    gate_type = front.get("type") if isinstance(front.get("type"), str) else None
    story_id = front.get("story_id") if isinstance(front.get("story_id"), str) else None
    raw_triggered_by = front.get("triggered_by")
    triggered_by = (
        [str(item) for item in raw_triggered_by if isinstance(item, str)]
        if isinstance(raw_triggered_by, list)
        else []
    )
    try:
        changed_paths = _apply_gate_resolution_effects(
            repo_root,
            epic_id,
            decision=decision,
            gate_type=gate_type,
            story_id=story_id,
            triggered_by=triggered_by,
            tracker=tracker,
        )
    except TrackerError as exc:
        sys.stderr.write(f"woof wf: tracker error: {exc}\n")
        return 2
    except StageStateError as exc:
        sys.stderr.write(f"woof wf: gate resolution failed: {exc}\n")
        return 2
    specific_event_name = _gate_resolved_event_name(gate_type)
    if specific_event_name and not (specific_event_name == "story_gate_resolved" and not story_id):
        specific_event = {
            "event": specific_event_name,
            "at": _now(),
            "epic_id": epic_id,
            "decision": decision,
            "gate_type": gate_type,
            "triggered_by": triggered_by,
        }
        if story_id:
            specific_event["story_id"] = story_id
        if changed_paths:
            specific_event["paths"] = changed_paths
        append_epic_event(repo_root, epic_id, specific_event)
    event = {
        "event": "gate_resolved",
        "at": _now(),
        "epic_id": epic_id,
        "decision": decision,
        "triggered_by": triggered_by,
    }
    if gate_type:
        event["gate_type"] = gate_type
    if story_id:
        event["story_id"] = story_id
    if changed_paths:
        event["paths"] = changed_paths
    append_epic_event(repo_root, epic_id, event)
    gate.unlink()
    sys.stdout.write(f"woof wf: gate resolved decision={decision}\n")
    return 0


def cmd_wf(args: argparse.Namespace) -> int:
    try:
        repo_root = find_project_root(Path.cwd())
    except FileNotFoundError as exc:
        sys.stderr.write(f"woof wf: {exc}\n")
        return 2

    try:
        tracker = resolve_tracker(repo_root)
    except TrackerError as exc:
        sys.stderr.write(f"woof wf: tracker not configured: {exc}\n")
        return 2

    def check_runtime() -> bool:
        try:
            tracker.assert_runtime_reachable()
        except TrackerError as exc:
            sys.stderr.write(f"woof wf: tracker not reachable: {exc}\n")
            return False
        return True

    if args.action == "new":
        if args.epic is not None:
            sys.stderr.write("woof wf new: --epic is assigned by the tracker; omit --epic\n")
            return 2
        if not args.spark:
            sys.stderr.write('woof wf new: spark is required, e.g. `woof wf new "..."`\n')
            return 2
        if not check_runtime():
            return 2
        try:
            result = tracker.create_epic(args.spark)
        except TrackerError as exc:
            sys.stderr.write(f"woof wf new: tracker error: {exc}\n")
            return 2
        if args.format == "json":
            paths = [str(result.spark_path)]
            if result.last_sync_path.exists():
                paths.append(str(result.last_sync_path))
            paths.append(str(result.current_epic_path))
            payload = {
                "epic_id": result.epic_id,
                "status": "created",
                "epic_ref": result.epic_ref,
                "epic_dir": str(result.epic_dir),
                "current_epic_path": str(result.current_epic_path),
                "next_command": f"woof wf --epic {result.epic_id}",
                "paths": paths,
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            sys.stdout.write(
                f"woof wf new: created E{result.epic_id} at {result.epic_ref}; "
                f"initialised spark.md and .woof/.current-epic\n"
                f"Next: woof wf --epic {result.epic_id}\n"
            )
        return 0

    if args.epic is None:
        sys.stderr.write('woof wf: --epic is required unless using `woof wf new "<spark>"`\n')
        return 2

    if not check_runtime():
        return 2

    directory = epic_dir(repo_root, args.epic)
    if not directory.exists():
        try:
            result = tracker.fetch_epic(args.epic)
        except TrackerError as exc:
            sys.stderr.write(f"woof wf: tracker error: {exc}\n")
            return 2
        if args.format == "json":
            paths = [str(result.spark_path), str(result.last_sync_path)]
            if result.epic_path:
                paths.insert(1, str(result.epic_path))
            payload = {
                "epic_id": args.epic,
                "status": "initialised",
                "epic_dir": str(result.epic_dir),
                "next_command": f"woof wf --epic {args.epic}",
                "paths": paths,
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            epic_state = " and EPIC.md" if result.epic_path else ""
            sys.stdout.write(
                f"woof wf: initialised E{args.epic} from the tracker with spark.md{epic_state}\n"
                f"Next: woof wf --epic {args.epic}\n"
            )
        return 0

    try:
        tracker.assert_epic_authority(args.epic)
    except TrackerError as exc:
        sys.stderr.write(f"woof wf: tracker error: {exc}\n")
        return 2

    if args.resolve:
        return _resolve_gate(repo_root, args.epic, args.resolve, tracker)

    try:
        outputs = run_graph(repo_root, args.epic, once=args.once)
    except WorkflowLockError as exc:
        sys.stderr.write(f"woof wf: workflow lock active: {exc}\n")
        return 2
    except StageStateError as exc:
        sys.stderr.write(f"woof wf: incomplete_stage_state: {exc}\n")
        return 2
    if any(
        output.status == NodeStatus.EPIC_COMPLETE for output in outputs
    ) and tracker.has_sync_state(args.epic):
        try:
            tracker.complete_epic(args.epic)
        except TrackerError as exc:
            sys.stderr.write(f"woof wf: tracker error: {exc}\n")
            return 2
        append_epic_event_once(
            repo_root,
            args.epic,
            {
                "event": "epic_completed",
                "at": _now(),
                "epic_id": args.epic,
            },
            event="epic_completed",
        )
    for output in outputs:
        if args.format == "json":
            sys.stdout.write(output.model_dump_json() + "\n")
        else:
            story = f" {output.story_id}" if output.story_id else ""
            msg = f": {output.message}" if output.message else ""
            sys.stdout.write(
                f"woof wf: {output.node_type.value}{story} -> {output.status.value}{msg}\n"
            )
    return 0


def setup_wf_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    wf = sub.add_parser("wf", help="run the deterministic Woof graph")
    wf.add_argument(
        "action",
        nargs="?",
        choices=["new"],
        help='optional action; use `new "<spark>"` to create a tracker-backed epic',
    )
    wf.add_argument("spark", nargs="?", help="spark text for `woof wf new`")
    wf.add_argument("--epic", type=int, help="epic id (tracker-assigned epic identifier)")
    wf.add_argument("--once", action="store_true", help="run a single graph node and stop")
    wf.add_argument(
        "--resolve",
        choices=[
            "approve",
            "revise_epic_contract",
            "revise_plan",
            "revise_story_scope",
            "split_story",
            "abandon_story",
            "abandon_epic",
            "keep_local",
            "accept_remote",
            "hand_merge",
        ],
        help="resolve the currently open gate with a structured decision",
    )
    wf.add_argument("--format", choices=["text", "json"], default="text")
    wf.set_defaults(func=cmd_wf)
