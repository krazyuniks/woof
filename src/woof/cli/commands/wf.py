"""`woof wf` — deterministic graph entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from woof.graph.runner import run_graph
from woof.graph.state import GateDecision
from woof.graph.transitions import append_epic_event, epic_dir
from woof.paths import find_project_root


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_gate(repo_root: Path, epic_id: int, decision: GateDecision) -> int:
    gate = epic_dir(repo_root, epic_id) / "gate.md"
    if not gate.exists():
        sys.stderr.write(f"woof wf: no open gate at {gate}\n")
        return 2
    append_epic_event(
        repo_root,
        epic_id,
        {
            "event": "gate_resolved",
            "at": _now(),
            "epic_id": epic_id,
            "decision": decision,
        },
    )
    gate.unlink()
    sys.stdout.write(f"woof wf: gate resolved decision={decision}\n")
    return 0


def cmd_wf(args: argparse.Namespace) -> int:
    try:
        repo_root = find_project_root(Path.cwd())
    except FileNotFoundError as exc:
        sys.stderr.write(f"woof wf: {exc}\n")
        return 2

    if args.resolve:
        return _resolve_gate(repo_root, args.epic, args.resolve)

    outputs = run_graph(repo_root, args.epic, once=args.once)
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
    wf.add_argument("--epic", type=int, required=True, help="epic id (gh issue number)")
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
