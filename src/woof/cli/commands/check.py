"""woof check stage-5 - deterministic Stage-5 boundary check runner.

Iterates the registry for stage=5, runs each runner against the current
epic/work-unit context, and emits a check-result conforming to
woof/schemas/check-result.schema.json.

Exit codes:
    0   all checks ok (check-result.ok == true)
    1   one or more checks failed (check-result.ok == false)
    2   usage / missing artefact error
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


def _load_plan(plan_path: Path) -> dict:
    from woof.graph.state import Plan

    return Plan.model_validate_json(plan_path.read_text()).model_dump(exclude_none=True)


def _load_critique_fm(epic_dir: Path, work_unit_id: str) -> dict | None:
    p = epic_dir / "critique" / f"work-unit-{work_unit_id}.md"
    if not p.exists():
        return None
    import yaml

    text = p.read_text()
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    try:
        return yaml.safe_load(text[4:end]) or {}
    except Exception:
        return None


def _cartography_context(
    repo_root: Path, plan: dict, work_unit_id: str
) -> tuple[str | None, list[str], list[str]]:
    from woof.cli.policy import cartography_floor, load_policy
    from woof.graph.pathspec import PathspecEvaluationError, filter_paths_matching

    policy = load_policy(repo_root)
    floor = cartography_floor(policy) if isinstance(policy, dict) else None
    if floor == "none":
        return floor, [], []

    codebase = repo_root / ".woof" / "codebase"
    design = ["TARGET-ARCHITECTURE.md", "PRINCIPLES.md"]
    if floor == "design":
        return (
            floor,
            [f".woof/codebase/{name}" for name in design if (codebase / name).is_file()],
            [],
        )

    paths = [
        path.relative_to(repo_root).as_posix()
        for path in sorted(codebase.glob("*.md"))
        if path.is_file()
    ]
    files_txt = codebase / "files.txt"
    if not files_txt.is_file():
        return floor, paths, []
    candidates = [
        line for line in files_txt.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    work_unit = _work_unit_for_id(plan, work_unit_id)
    if work_unit is None or not work_unit.get("paths"):
        return floor, paths, candidates
    try:
        return floor, paths, filter_paths_matching(repo_root, candidates, list(work_unit["paths"]))
    except PathspecEvaluationError:
        return floor, paths, []


def _work_unit_for_id(plan: dict, work_unit_id: str) -> dict | None:
    work_units = plan.get("work_units")
    if not isinstance(work_units, list):
        return None
    for work_unit in work_units:
        if isinstance(work_unit, dict) and work_unit.get("id") == work_unit_id:
            return work_unit
    return None


def _outcome_to_dict(outcome: object) -> dict:
    """Convert a CheckOutcome dataclass to a JSON-serialisable dict."""
    d = asdict(outcome)  # type: ignore[arg-type]
    return d


def _not_implemented_to_dict(check_id: str, exc: NotImplementedError) -> dict:
    evidence = str(exc) or "runner raised NotImplementedError"
    return {
        "id": check_id,
        "ok": False,
        "severity": "blocker",
        "summary": f"{check_id}: runner is not implemented",
        "evidence": evidence,
        "paths": [],
        "command": None,
        "exit_code": None,
    }


def _self_test(registry: dict, stage_ids: list[str]) -> int:
    """Check every stage-5 runner is implemented. Exit 0 if all ok, 1 if any missing."""
    failures: list[str] = []
    for check_id in stage_ids:
        check = registry.get(check_id)
        if check is None:
            failures.append(f"{check_id}: not in registry")
            continue
        try:
            check.runner(None)  # type: ignore[arg-type]
        except NotImplementedError:
            failures.append(f"{check_id}: runner raises NotImplementedError (not implemented)")
        except Exception:
            pass  # raised for a real reason - runner IS implemented

    if failures:
        for msg in failures:
            sys.stderr.write(f"woof check stage-5 --self-test FAIL: {msg}\n")
        return 1

    sys.stdout.write(
        f"woof check stage-5 --self-test OK: all {len(stage_ids)} runners implemented\n"
    )
    return 0


def cmd_check_stage_5(args: argparse.Namespace) -> int:
    from woof.checks.registry import REGISTRY, STAGE_5_CHECK_IDS

    if args.self_test:
        return _self_test(REGISTRY, STAGE_5_CHECK_IDS)

    if not args.epic:
        sys.stderr.write("woof check stage-5: --epic required (unless --self-test)\n")
        return 2
    if not args.work_unit:
        sys.stderr.write("woof check stage-5: --work-unit required (unless --self-test)\n")
        return 2

    repo_root = _find_repo_root()
    epic_dir = repo_root / ".woof" / "epics" / f"E{args.epic}"
    plan_path = epic_dir / "plan.json"

    if not plan_path.exists():
        sys.stderr.write(f"woof check stage-5: {plan_path} not found\n")
        return 2

    from woof.checks import CheckContext

    plan = _load_plan(plan_path)
    work_unit_id = args.work_unit
    critique = _load_critique_fm(epic_dir, work_unit_id)
    cartography_floor, cartography_paths, files_txt_slice = _cartography_context(
        repo_root, plan, work_unit_id
    )
    ctx = CheckContext(
        epic_id=args.epic,
        work_unit_id=work_unit_id,
        repo_root=repo_root,
        epic_dir=epic_dir,
        plan=plan,
        critique=critique,
        cartography_floor=cartography_floor,
        cartography_paths=cartography_paths,
        files_txt_slice=files_txt_slice,
    )

    outcomes = []
    triggered_by: list[str] = []

    for check_id in STAGE_5_CHECK_IDS:
        check = REGISTRY[check_id]
        try:
            outcome = check.runner(ctx)
        except NotImplementedError as exc:
            outcomes.append(_not_implemented_to_dict(check_id, exc))
            triggered_by.append(check_id)
            continue
        d = _outcome_to_dict(outcome)
        outcomes.append(d)
        if not outcome.ok:
            triggered_by.append(check_id)

    result = {
        "ok": len(triggered_by) == 0,
        "stage": 5,
        "epic_id": args.epic,
        "work_unit_id": work_unit_id,
        "triggered_by": triggered_by,
        "checks": outcomes,
    }

    if args.format == "json":
        sys.stdout.write(json.dumps(result) + "\n")
    else:
        ok_str = "OK" if result["ok"] else "FAIL"
        sys.stdout.write(f"woof check stage-5: {ok_str}\n")
        for c in outcomes:
            status = "OK  " if c["ok"] else "FAIL"
            sys.stdout.write(f"  {status} {c['id']}: {c['summary']}\n")
            if not c["ok"] and c.get("evidence"):
                sys.stdout.write(f"       evidence: {c['evidence']}\n")

    return 0 if result["ok"] else 1


def _find_repo_root() -> Path:
    """Walk up to the first directory containing a .woof/ directory."""
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".woof").is_dir():
            return candidate
    # Fallback: assume cwd
    return Path.cwd()


def setup_check_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    check_p = sub.add_parser("check", help="run stage boundary checks")
    check_sub = check_p.add_subparsers(dest="check_stage", required=True)

    stage5 = check_sub.add_parser("stage-5", help="run Stage-5 checks for a work unit")
    stage5.add_argument("--epic", type=int, help="tracker-assigned epic id")
    stage5.add_argument("--work-unit", dest="work_unit", help="work-unit id (e.g. S1)")
    stage5.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="output format (default: text)",
    )
    stage5.add_argument(
        "--self-test",
        action="store_true",
        help="enumerate registry and exit non-zero if any runner is unimplemented",
    )
    stage5.set_defaults(func=cmd_check_stage_5)
