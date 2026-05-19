"""`woof wf` — deterministic graph entry point."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from woof.cli.github import (
    GithubSyncError,
    assert_github_runtime_reachable,
    create_epic_from_spark,
    has_github_sync_state,
    initialise_epic_from_issue,
    sync_epic_completion,
    sync_plan_summary,
)
from woof.graph.runner import run_graph
from woof.graph.state import GateDecision, NodeStatus
from woof.graph.transitions import (
    StageStateError,
    append_epic_event,
    append_epic_event_once,
    epic_dir,
)
from woof.paths import find_project_root


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gate_type(gate_path: Path) -> str | None:
    text = gate_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict):
        return None
    gate_type = front.get("type")
    return gate_type if isinstance(gate_type, str) else None


def _resolve_gate(repo_root: Path, epic_id: int, decision: GateDecision) -> int:
    gate = epic_dir(repo_root, epic_id) / "gate.md"
    if not gate.exists():
        sys.stderr.write(f"woof wf: no open gate at {gate}\n")
        return 2
    gate_type = _gate_type(gate)
    if gate_type == "plan_gate" and decision == "approve":
        try:
            sync_plan_summary(repo_root, epic_id)
        except GithubSyncError as exc:
            sys.stderr.write(f"woof wf: github sync failed: {exc}\n")
            return 2
    event = {
        "event": "gate_resolved",
        "at": _now(),
        "epic_id": epic_id,
        "decision": decision,
    }
    if gate_type:
        event["gate_type"] = gate_type
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

    def check_runtime() -> bool:
        try:
            assert_github_runtime_reachable(repo_root)
        except GithubSyncError as exc:
            sys.stderr.write(f"woof wf: github runtime check failed: {exc}\n")
            return False
        return True

    if args.action == "new":
        if args.epic is not None:
            sys.stderr.write("woof wf new: --epic is assigned by GitHub; omit --epic\n")
            return 2
        if not args.spark:
            sys.stderr.write('woof wf new: spark is required, e.g. `woof wf new "..."`\n')
            return 2
        if not check_runtime():
            return 2
        try:
            result = create_epic_from_spark(repo_root, args.spark)
        except GithubSyncError as exc:
            sys.stderr.write(f"woof wf new: github sync failed: {exc}\n")
            return 2
        if args.format == "json":
            payload = {
                "epic_id": result.epic_id,
                "status": "created",
                "issue_url": result.issue_url,
                "epic_dir": str(result.epic_dir),
                "current_epic_path": str(result.current_epic_path),
                "paths": [
                    str(result.spark_path),
                    str(result.last_sync_path),
                    str(result.current_epic_path),
                ],
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            sys.stdout.write(
                f"woof wf new: created E{result.epic_id} at {result.issue_url}; "
                f"initialised spark.md and .woof/.current-epic\n"
            )
        return 0

    if args.epic is None:
        sys.stderr.write('woof wf: --epic is required unless using `woof wf new "<spark>"`\n')
        return 2

    if not check_runtime():
        return 2

    if args.resolve:
        return _resolve_gate(repo_root, args.epic, args.resolve)

    directory = epic_dir(repo_root, args.epic)
    if not directory.exists():
        try:
            result = initialise_epic_from_issue(repo_root, args.epic)
        except GithubSyncError as exc:
            sys.stderr.write(f"woof wf: github sync failed: {exc}\n")
            return 2
        if args.format == "json":
            paths = [str(result.spark_path), str(result.last_sync_path)]
            if result.epic_path:
                paths.insert(1, str(result.epic_path))
            payload = {
                "epic_id": args.epic,
                "status": "initialised",
                "epic_dir": str(result.epic_dir),
                "paths": paths,
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            epic_state = " and EPIC.md" if result.epic_path else ""
            sys.stdout.write(
                f"woof wf: initialised E{args.epic} from GitHub issue with spark.md{epic_state}\n"
            )
        return 0

    try:
        outputs = run_graph(repo_root, args.epic, once=args.once)
    except StageStateError as exc:
        sys.stderr.write(f"woof wf: incomplete_stage_state: {exc}\n")
        return 2
    if any(
        output.status == NodeStatus.EPIC_COMPLETE for output in outputs
    ) and has_github_sync_state(repo_root, args.epic):
        try:
            sync_epic_completion(repo_root, args.epic)
        except GithubSyncError as exc:
            sys.stderr.write(f"woof wf: github sync failed: {exc}\n")
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
        help='optional action; use `new "<spark>"` to create a GitHub-backed epic',
    )
    wf.add_argument("spark", nargs="?", help="spark text for `woof wf new`")
    wf.add_argument("--epic", type=int, help="epic id (gh issue number)")
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
        ],
        help="resolve the currently open gate with a structured decision",
    )
    wf.add_argument("--format", choices=["text", "json"], default="text")
    wf.set_defaults(func=cmd_wf)
