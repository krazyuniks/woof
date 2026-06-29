"""Run-resilience policy over dispatch telemetry (E2 S9)."""

from __future__ import annotations

from pathlib import Path

from woof.graph.git import staged_paths
from woof.graph.pathspec import PathspecEvaluationError, staged_paths_matching
from woof.graph.transitions import StageStateError, iter_dispatch_events, load_plan, work_unit_by_id

SAME_ERROR_THRESHOLD = 3
NO_PROGRESS_THRESHOLD = 3

_UNKNOWN_SIG_PREFIX = "__unknown__"


def _work_unit_path_patterns(repo_root: Path, epic_id: int, work_unit_id: str | None) -> list[str]:
    if not work_unit_id:
        return []
    try:
        plan = load_plan(repo_root, epic_id)
        work_unit = work_unit_by_id(plan, work_unit_id)
        return list(work_unit.paths)
    except (StageStateError, ValueError):
        return []


def _has_work_unit_progress(repo_root: Path, epic_id: int, work_unit_id: str | None) -> bool:
    """Return True when current staged paths signal work-unit progress (signal 2)."""
    current_staged = staged_paths(repo_root)
    if not current_staged:
        return False
    patterns = _work_unit_path_patterns(repo_root, epic_id, work_unit_id)
    if not patterns:
        return bool(current_staged)
    try:
        return bool(staged_paths_matching(repo_root, patterns))
    except PathspecEvaluationError:
        return bool(current_staged)


def detect_resilience_gate(repo_root: Path, epic_id: int, work_unit_id: str | None) -> str | None:
    """Scan dispatch telemetry for run-resilience conditions.

    Returns "course_correction", "run_resilience", or None.

    Counter logic:
    - Rate-limited events are skipped (counters neither increment nor reset).
    - consecutive_no_progress increments each non-rate-limited event; resets on
      current staged-path progress (signal 2).
    - consecutive_same_error increments when error_signature matches the
      previous non-rate-limited event; starts a new streak of 1 when it differs.
    - course_correction (same-error threshold) is checked before run_resilience.
    """
    events = iter_dispatch_events(repo_root, epic_id)
    subprocess_events = [
        e
        for e in events
        if e.get("event") == "subprocess_returned"
        and (work_unit_id is None or e.get("work_unit_id") == work_unit_id)
    ]

    if not subprocess_events:
        return None

    consecutive_same_error = 0
    consecutive_no_progress = 0
    prev_error_sig: str | None = None

    for event in subprocess_events:
        if event.get("rate_limit") == "rate_limited":
            continue

        raw_sig = event.get("error_signature")
        if not isinstance(raw_sig, str) or not raw_sig:
            raw_sig = f"{_UNKNOWN_SIG_PREFIX}{id(event)}"

        consecutive_no_progress += 1
        if prev_error_sig is None or raw_sig == prev_error_sig:
            consecutive_same_error += 1
        else:
            consecutive_same_error = 1

        prev_error_sig = raw_sig

    if _has_work_unit_progress(repo_root, epic_id, work_unit_id):
        consecutive_same_error = 0
        consecutive_no_progress = 0

    if consecutive_same_error >= SAME_ERROR_THRESHOLD:
        return "course_correction"
    if consecutive_no_progress >= NO_PROGRESS_THRESHOLD:
        return "run_resilience"
    return None
