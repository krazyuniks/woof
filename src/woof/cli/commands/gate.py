"""woof gate write — mechanical gate.md authoring from check-result or trigger.

Writes .woof/epics/E<N>/gate.md and appends story_gate_opened to epic.jsonl.
Front-matter is derived deterministically; no LLM authors any YAML field.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from woof.paths import schema_dir


def _find_repo_root() -> Path:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".woof").is_dir():
            return candidate
    return Path.cwd()


def _schema_path(repo_root: Path) -> Path:
    return schema_dir() / "gate.schema.json"


def cmd_gate_write(args: argparse.Namespace) -> int:
    from woof.gate.write import write_gate_for_trigger, write_gate_from_check_result

    repo_root = _find_repo_root()
    epic_dir = repo_root / ".woof" / "epics" / f"E{args.epic}"
    schema = _schema_path(repo_root)

    story_id: str | None = args.story

    # Resolve story_id from plan.json if not given
    if story_id is None and not args.from_check_result:
        plan_path = epic_dir / "plan.json"
        if plan_path.exists():
            import json

            plan = json.loads(plan_path.read_text())
            for s in plan.get("stories", []):
                if s.get("status") == "in_progress":
                    story_id = s["id"]
                    break

    if args.from_check_result:
        cr_path = Path(args.from_check_result)
        if not cr_path.exists():
            sys.stderr.write(f"woof gate write: {cr_path} not found\n")
            return 2
        pos_path = Path(args.position_file) if args.position_file else None
        try:
            gate_path = write_gate_from_check_result(
                check_result_path=cr_path,
                position_path=pos_path,
                epic_dir=epic_dir,
                story_id=story_id,
                schema_path=schema if schema.exists() else None,
            )
        except ValueError as exc:
            sys.stderr.write(f"woof gate write: {exc}\n")
            return 1
    elif args.triggered_by:
        pos_path = Path(args.from_position) if args.from_position else None
        try:
            gate_path = write_gate_for_trigger(
                trigger=args.triggered_by,
                epic_dir=epic_dir,
                story_id=story_id,
                exit_code=args.exit_code,
                position_path=pos_path,
                schema_path=schema if schema.exists() else None,
            )
        except ValueError as exc:
            sys.stderr.write(f"woof gate write: {exc}\n")
            return 1
    else:
        sys.stderr.write("woof gate write: provide --from-check-result or --triggered-by\n")
        return 2

    sys.stdout.write(f"woof gate write: {gate_path}\n")
    return 0


def setup_gate_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    gate_p = sub.add_parser("gate", help="manage woof gates")
    gate_sub = gate_p.add_subparsers(dest="gate_cmd", required=True)

    write_p = gate_sub.add_parser("write", help="write gate.md from check-result or trigger")
    write_p.add_argument("--epic", type=int, required=True, help="epic id")
    write_p.add_argument("--story", help="story id (e.g. S1); auto-detected if omitted")
    # Mode 1: from check-result JSON
    write_p.add_argument(
        "--from-check-result",
        dest="from_check_result",
        help="path to check-result.json",
    )
    write_p.add_argument(
        "--position-file",
        dest="position_file",
        help="path to prose position file (used with --from-check-result)",
    )
    # Mode 2: direct trigger
    write_p.add_argument(
        "--triggered-by",
        dest="triggered_by",
        help="trigger ID (subprocess_crash | executor_aborted | empty_diff_review | ...)",
    )
    write_p.add_argument(
        "--exit-code",
        dest="exit_code",
        type=int,
        help="subprocess exit code (required with --triggered-by subprocess_crash)",
    )
    write_p.add_argument(
        "--from-position",
        dest="from_position",
        help="path to prose position file (used with --triggered-by)",
    )
    write_p.set_defaults(func=cmd_gate_write)
