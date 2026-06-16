"""woof baseline capture - explicit operator recapture of the quality-gates baseline.

Runs every gate declared in .woof/quality-gates.toml, records their pass/fail state
and command identity, and writes a fresh .woof/quality-gates-baseline.json with
wall-clock freshness metadata.

Recapture is NEVER implicit: this command is the ONLY path that writes the baseline.
Any other mechanism that suppressed failures without explicit operator intent would be
a silent bypass of the quality gate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_EXPIRY_DAYS = 30


def _find_repo_root() -> Path:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".woof").is_dir():
            return candidate
    return Path.cwd()


def cmd_baseline_capture(args: argparse.Namespace) -> int:
    from woof.checks.runners.check_1_quality_gates import capture_baseline

    repo_root = Path(args.project_root).resolve() if args.project_root else _find_repo_root()

    expiry_seconds = args.expiry_days * 86400

    result, error = capture_baseline(repo_root, expiry_seconds)
    if error is not None:
        sys.stderr.write(f"woof baseline capture: {error}\n")
        return 2

    status = (
        f"captured {result.gate_count} gate(s), {result.red_count} red"
        f" — written to {result.baseline_path}"
    )
    print(status)
    return 0


def setup_baseline_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    baseline = sub.add_parser(
        "baseline",
        help="manage the quality-gates baseline record",
    )
    baseline_sub = baseline.add_subparsers(dest="baseline_cmd", required=True)

    capture = baseline_sub.add_parser(
        "capture",
        help=(
            "run all quality gates and write a fresh baseline record; "
            "this is the ONLY path that recaptures the baseline"
        ),
    )
    capture.add_argument(
        "--project-root",
        help="woof project root; defaults to the nearest ancestor containing .woof/",
    )
    capture.add_argument(
        "--expiry-days",
        type=int,
        default=DEFAULT_EXPIRY_DAYS,
        metavar="N",
        help=f"wall-clock expiry in days (default: {DEFAULT_EXPIRY_DAYS})",
    )
    capture.set_defaults(func=cmd_baseline_capture)

    def _no_subcommand(_args: argparse.Namespace) -> int:
        sys.stderr.write("woof baseline: subcommand required\n")
        return 2

    baseline.set_defaults(func=_no_subcommand)
