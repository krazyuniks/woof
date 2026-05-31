"""`woof brainstorm` - ingest an interactive Stage-0 design bundle.

The interactive design loops (Brainstorm then Grill Me) run in the `brainstorm`
skill, outside the deterministic graph: the graph dispatches headless batch
agents and cannot host an interactive conversation. This command is the bridge.
It validates the skill's resolved bundle (its Contract 2) against
``schemas/brainstorm.schema.json`` and lands it in the epic's
``discovery/brainstorm/`` bucket as a Stage-1 discovery source. ``woof wf
--epic N`` then skips the headless research/thinking/ideate chain (the autonomy
fallback) and ``discovery_synthesis`` ingests the bundle directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from woof.cli.dispatcher import find_woof_root
from woof.cli.main import ensure_ajv, run_ajv
from woof.graph.dispositions import FrontMatterError, read_markdown_front_matter
from woof.graph.transitions import append_epic_event_once, discovery_bucket_dir, epic_dir
from woof.paths import schema_dir

INTERACTIVE_BUCKET = "brainstorm"


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def _fail(message: str) -> int:
    sys.stderr.write(f"woof brainstorm: {message}\n")
    return 1


def cmd_brainstorm(args: argparse.Namespace) -> int:
    ensure_ajv()

    bundle_path = Path(args.from_bundle).expanduser()
    if not bundle_path.is_file():
        return _fail(f"bundle not found: {bundle_path}")
    try:
        parsed = read_markdown_front_matter(bundle_path)
    except FrontMatterError as exc:
        return _fail(str(exc))
    meta = parsed.front

    schema_path = schema_dir() / "brainstorm.schema.json"
    ok, output = run_ajv(schema_path, json.dumps(meta).encode("utf-8"))
    if not ok:
        sys.stderr.write(output + "\n")
        return _fail("bundle failed validation against brainstorm.schema.json")
    if meta.get("status") == "rejected":
        return _fail(
            "bundle status is 'rejected' (grill returned it to Loop 1); "
            "resolve it in the brainstorm skill before ingesting"
        )

    start = Path(args.project_root).resolve() if args.project_root else Path.cwd()
    repo_root = find_woof_root(start)

    bucket_dir = discovery_bucket_dir(repo_root, args.epic, INTERACTIVE_BUCKET)
    bucket_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    design_dest = bucket_dir / "design.md"
    design_dest.write_text(bundle_path.read_text(encoding="utf-8"), encoding="utf-8")
    written.append(_rel(repo_root, design_dest))

    # Land CONTEXT.md alongside the design when it resolves relative to the bundle,
    # so synthesis ingests the glossary too. ADRs stay referenced by path.
    context_ref = meta.get("context_ref")
    if isinstance(context_ref, str):
        context_src = bundle_path.parent / context_ref
        if context_src.is_file():
            context_dest = bucket_dir / "CONTEXT.md"
            context_dest.write_text(context_src.read_text(encoding="utf-8"), encoding="utf-8")
            written.append(_rel(repo_root, context_dest))

    # Ensure a spark.md exists so `woof wf` can enter discovery.
    spark = epic_dir(repo_root, args.epic) / "spark.md"
    if not spark.exists():
        spark.parent.mkdir(parents=True, exist_ok=True)
        title = meta.get("title") or f"Epic {args.epic}"
        spark.write_text(
            f"# {title}\n\nSeeded from an interactive brainstorm bundle: "
            f"{_rel(repo_root, design_dest)}\n",
            encoding="utf-8",
        )
        written.append(_rel(repo_root, spark))

    work_units = meta.get("work_units") or []
    append_epic_event_once(
        repo_root,
        args.epic,
        {
            "event": "discovery_bucket_explored",
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "epic_id": args.epic,
            "bucket": INTERACTIVE_BUCKET,
            "paths": written,
        },
        event="discovery_bucket_explored",
        bucket=INTERACTIVE_BUCKET,
    )

    print(f"woof brainstorm: ingested bundle into {_rel(repo_root, bucket_dir)}/")
    print(f"  tier={meta.get('tier')} status={meta.get('status')} work_units={len(work_units)}")
    for path in written:
        print(f"  wrote {path}")
    print(
        f"  next: woof wf --epic {args.epic}  (synthesis ingests the bundle; headless chain skipped)"
    )
    return 0


def setup_brainstorm_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "brainstorm",
        help="ingest an interactive Stage-0 design bundle into an epic's discovery/brainstorm/ bucket",
        description=(
            "Validate a design bundle from the brainstorm skill (Contract 2) and land it as a "
            "Stage-1 discovery source. The interactive loops run in the skill, not here; this "
            "command is the validation-and-landing bridge before `woof wf`."
        ),
    )
    parser.add_argument("--epic", type=int, required=True, help="tracker-assigned epic id")
    parser.add_argument(
        "--from-bundle",
        required=True,
        help="path to the resolved design document (the bundle's primary file, with front-matter)",
    )
    parser.add_argument(
        "--project-root",
        help="woof project root; defaults to the nearest ancestor of cwd containing .woof/",
    )
    parser.set_defaults(func=cmd_brainstorm)
