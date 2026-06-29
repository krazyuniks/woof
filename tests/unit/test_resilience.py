"""E2 S9 — run-resilience circuit breaker tests.

All tests use real temp git repos and real dispatch.jsonl event sequences.
No unittest.mock.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from woof.graph.git import git_env
from woof.graph.resilience import (
    NO_PROGRESS_THRESHOLD,
    SAME_ERROR_THRESHOLD,
    detect_resilience_gate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=git_env(),
        **kwargs,  # type: ignore[arg-type]
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com", check=True, capture_output=True)
    _git(root, "config", "user.name", "Test", check=True, capture_output=True)
    (root / ".gitignore").write_text(".woof/.current-epic\n")
    _git(root, "add", ".gitignore", check=True, capture_output=True)
    _git(root, "commit", "-m", "chore: init", check=True, capture_output=True)


def _epic_dir(root: Path, epic_id: int = 1) -> Path:
    d = root / ".woof" / "epics" / f"E{epic_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_plan(root: Path, epic_id: int = 1, story_paths: list[str] | None = None) -> None:
    d = _epic_dir(root, epic_id)
    (d / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": epic_id,
                "goal": "test",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "first",
                        "summary": "test",
                        "paths": story_paths or ["src/**"],
                        "satisfies": ["O1"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "deps": [],
                        "tests": {"count": 0, "types": []},
                        "status": "in_progress",
                    }
                ],
            }
        )
    )


def _append_subprocess_returned(
    root: Path,
    epic_id: int = 1,
    work_unit_id: str = "S1",
    **fields: object,
) -> None:
    d = _epic_dir(root, epic_id)
    event = {
        "event": "subprocess_returned",
        "epic_id": epic_id,
        "work_unit_id": work_unit_id,
        **fields,
    }
    with (d / "dispatch.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# Test 1: same error on 3 consecutive events → course_correction
# ---------------------------------------------------------------------------


def test_same_error_threshold_opens_course_correction(tmp_path: Path) -> None:
    """SAME_ERROR_THRESHOLD consecutive events with the same error_signature → course_correction."""
    _init_repo(tmp_path)
    _write_plan(tmp_path)

    for _ in range(SAME_ERROR_THRESHOLD):
        _append_subprocess_returned(tmp_path, error_signature="same_error_A")

    result = detect_resilience_gate(tmp_path, 1, "S1")
    assert result == "course_correction"


# ---------------------------------------------------------------------------
# Test 2: no progress on 3 consecutive events (different errors) → run_resilience
# ---------------------------------------------------------------------------


def test_different_errors_no_progress_opens_run_resilience(tmp_path: Path) -> None:
    """NO_PROGRESS_THRESHOLD events with distinct errors (no staged files) → run_resilience."""
    _init_repo(tmp_path)
    _write_plan(tmp_path)

    for i in range(NO_PROGRESS_THRESHOLD):
        _append_subprocess_returned(tmp_path, error_signature=f"unique_error_{i}")

    result = detect_resilience_gate(tmp_path, 1, "S1")
    assert result == "run_resilience"


# ---------------------------------------------------------------------------
# Test 3: same error on 2 events, progress on 3rd → counters reset, no gate
# ---------------------------------------------------------------------------


def test_progress_resets_counters_no_gate(tmp_path: Path) -> None:
    """After SAME_ERROR_THRESHOLD same-error events, staged files reset counters → no gate."""
    _init_repo(tmp_path)
    _write_plan(tmp_path, story_paths=["src/**"])

    for _ in range(SAME_ERROR_THRESHOLD):
        _append_subprocess_returned(tmp_path, error_signature="repeated_error")

    # Stage a file matching the story's "src/**" pathspec.
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "foo.py").write_text("x = 1\n")
    _git(tmp_path, "add", "src/foo.py", check=True, capture_output=True)

    result = detect_resilience_gate(tmp_path, 1, "S1")
    assert result is None


# ---------------------------------------------------------------------------
# Test 4: rate-limit event between same-error events → skipped, counters unchanged
# ---------------------------------------------------------------------------


def test_rate_limit_event_skipped_does_not_increment_or_reset(tmp_path: Path) -> None:
    """A rate_limited subprocess_returned is skipped: neither counter incremented nor reset."""
    _init_repo(tmp_path)
    _write_plan(tmp_path)

    # One same-error event, then rate-limited, then same-error again — only 2 real events.
    _append_subprocess_returned(tmp_path, error_signature="error_X")
    _append_subprocess_returned(tmp_path, error_signature="any", rate_limit="rate_limited")
    _append_subprocess_returned(tmp_path, error_signature="error_X")

    # Only 2 non-rate-limited events → below threshold of 3.
    result = detect_resilience_gate(tmp_path, 1, "S1")
    assert result is None

    # Add one more same-error event → 3 non-rate-limited same-error events → course_correction.
    _append_subprocess_returned(tmp_path, error_signature="error_X")
    result2 = detect_resilience_gate(tmp_path, 1, "S1")
    assert result2 == "course_correction"


# ---------------------------------------------------------------------------
# Test 5: both thresholds hit simultaneously → course_correction wins
# ---------------------------------------------------------------------------


def test_both_thresholds_hit_course_correction_wins(tmp_path: Path) -> None:
    """When same-error and no-progress both hit threshold, course_correction takes priority."""
    _init_repo(tmp_path)
    _write_plan(tmp_path)

    # Same error every time → both counters hit threshold simultaneously.
    for _ in range(max(SAME_ERROR_THRESHOLD, NO_PROGRESS_THRESHOLD)):
        _append_subprocess_returned(tmp_path, error_signature="persistent_error")

    result = detect_resilience_gate(tmp_path, 1, "S1")
    assert result == "course_correction"


# ---------------------------------------------------------------------------
# Test 6: zero events → no gate
# ---------------------------------------------------------------------------


def test_empty_event_log_no_gate(tmp_path: Path) -> None:
    """No events in dispatch.jsonl → detect_resilience_gate returns None."""
    _init_repo(tmp_path)
    _epic_dir(tmp_path)  # create dir so load_plan call doesn't create it

    result = detect_resilience_gate(tmp_path, 1, "S1")
    assert result is None


# ---------------------------------------------------------------------------
# Test 7: fewer than threshold consecutive events → no gate
# ---------------------------------------------------------------------------


def test_below_threshold_no_gate(tmp_path: Path) -> None:
    """Fewer consecutive events than either threshold → no gate."""
    _init_repo(tmp_path)
    _write_plan(tmp_path)

    # Write one fewer event than would trigger either counter.
    for _ in range(min(SAME_ERROR_THRESHOLD, NO_PROGRESS_THRESHOLD) - 1):
        _append_subprocess_returned(tmp_path, error_signature="same_error")

    result = detect_resilience_gate(tmp_path, 1, "S1")
    assert result is None
