"""Tests for check_6_critique_blocker — Stage-5 Check 6.

Covers O7 (E181 S2 regression: blocker critique halts pipeline) and the
basic pass/fail branches of the check logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "woof" / "e181_s2"

pytestmark = pytest.mark.host_only


def _make_ctx(epic_dir: Path, story_id: str = "S2") -> object:
    from woof.checks import CheckContext

    return CheckContext(
        epic_id=181,
        story_id=story_id,
        repo_root=REPO_ROOT,
        epic_dir=epic_dir,
        plan={},
        critique=None,
    )


def _write_critique(critique_dir: Path, story_id: str, content: str) -> Path:
    critique_dir.mkdir(parents=True, exist_ok=True)
    p = critique_dir / f"story-{story_id}.md"
    p.write_text(content)
    return p


_BLOCKER_CRITIQUE = """\
---
target: story
target_id: S2
severity: blocker
timestamp: "2026-04-27T05:46:49Z"
harness: codex-gpt-5
findings:
  - id: F1
    severity: blocker
    summary: "apply_size_cap corrupts UTF-8 at byte boundaries"
---
Findings text here.
"""

_MINOR_CRITIQUE = """\
---
target: story
target_id: S2
severity: minor
timestamp: "2026-04-27T05:46:49Z"
harness: codex-gpt-5
findings:
  - id: F1
    severity: minor
    summary: "Minor style nit in variable name"
---
Minor findings.
"""

_INFO_CRITIQUE = """\
---
target: story
target_id: S1
severity: info
timestamp: "2026-04-27T05:46:49Z"
harness: codex-gpt-5
findings: []
---
No findings.
"""


def test_blocker_critique_fails_O7(tmp_path: Path) -> None:
    """O7: E181 S2 blocker critique → check_6 returns ok=False, severity=blocker."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))

    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E181"
    _write_critique(epic_dir / "critique", "S2", _BLOCKER_CRITIQUE)
    ctx = _make_ctx(epic_dir, "S2")

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "blocker" in outcome.summary.lower()
    assert outcome.id == "check_6_critique_blocker"


def test_e181_s2_fixture_is_blocker_O7(tmp_path: Path) -> None:
    """O7: The canonical E181 S2 fixture triggers check_6 failure (regression guard)."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))

    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E181"
    critique_dir = epic_dir / "critique"
    critique_dir.mkdir(parents=True)
    import shutil

    shutil.copy(FIXTURE_DIR / "critique" / "story-S2.md", critique_dir / "story-S2.md")

    ctx = _make_ctx(epic_dir, "S2")
    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok, f"Expected failure but got: {outcome}"
    assert outcome.severity == "blocker"
    assert outcome.id == "check_6_critique_blocker"


def test_minor_critique_passes(tmp_path: Path) -> None:
    """Minor severity critique → check_6 returns ok=True."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))

    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E181"
    _write_critique(epic_dir / "critique", "S2", _MINOR_CRITIQUE)
    ctx = _make_ctx(epic_dir, "S2")

    outcome = check_6_critique_blocker_runner(ctx)

    assert outcome.ok
    assert outcome.severity == "minor"


def test_info_critique_passes(tmp_path: Path) -> None:
    """Info severity (no findings) critique → check_6 returns ok=True."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))

    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E181"
    _write_critique(epic_dir / "critique", "S1", _INFO_CRITIQUE)
    ctx = _make_ctx(epic_dir, "S1")

    outcome = check_6_critique_blocker_runner(ctx)

    assert outcome.ok
    assert outcome.severity == "info"


def test_missing_critique_file_fails(tmp_path: Path) -> None:
    """Missing critique file → check_6 returns ok=False."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))

    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E181"
    epic_dir.mkdir(parents=True)
    ctx = _make_ctx(epic_dir, "S2")

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert "missing" in outcome.summary.lower()
