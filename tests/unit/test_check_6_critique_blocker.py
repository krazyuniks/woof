"""Tests for check_6_critique_blocker — Stage-5 Check 6.

Covers O7 (E181 S2 regression: blocker critique halts pipeline) and the
basic pass/fail branches of the check logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from woof.checks import CheckContext

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "woof" / "e181_s2"

pytestmark = pytest.mark.host_only


def _make_ctx(epic_dir: Path, work_unit_id: str = "S2") -> CheckContext:
    from woof.checks import CheckContext

    return CheckContext(
        epic_id=181,
        work_unit_id=work_unit_id,
        repo_root=REPO_ROOT,
        epic_dir=epic_dir,
        plan={},
        critique=None,
    )


def _write_critique(critique_dir: Path, work_unit_id: str, content: str) -> Path:
    critique_dir.mkdir(parents=True, exist_ok=True)
    p = critique_dir / f"work-unit-{work_unit_id}.md"
    p.write_text(content)
    return p


def _write_disposition(epic_dir: Path, work_unit_id: str, *, severity: str = "info") -> Path:
    disposition_dir = epic_dir / "dispositions"
    disposition_dir.mkdir(parents=True, exist_ok=True)
    path = disposition_dir / f"work-unit-{work_unit_id}.md"
    dispositions = "[]"
    if severity == "minor":
        dispositions = (
            "\n  - finding_id: F1\n"
            "    decision: accepted\n"
            "    rationale: Addressed in staged artefacts."
        )
    path.write_text(
        f"""---
target: work_unit
target_id: {work_unit_id}
critique_path: .woof/epics/E181/critique/work-unit-{work_unit_id}.md
severity: {severity}
timestamp: "2026-04-27T05:47:00Z"
harness: test-primary
dispositions: {dispositions}
---
Primary disposition.
"""
    )
    return path


_BLOCKER_CRITIQUE = """\
---
target: work_unit
target_id: S2
severity: blocker
timestamp: "2026-04-27T05:46:49Z"
harness: codex-gpt-5
findings:
  - id: F1
    severity: blocker
    category: test_quality
    summary: "apply_size_cap corrupts UTF-8 at byte boundaries"
    evidence: "src/woof/checks/runners/check_6_critique_blocker.py:1 blocker check is insufficient"
---
Findings text here.
"""

_MINOR_CRITIQUE = """\
---
target: work_unit
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
target: work_unit
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
    assert "F1 [test_quality]" in (outcome.evidence or "")
    assert "src/woof/checks/runners/check_6_critique_blocker.py:1" in (outcome.evidence or "")
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

    shutil.copy(FIXTURE_DIR / "critique" / "work-unit-2.md", critique_dir / "work-unit-2.md")

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
    _write_disposition(epic_dir, "S2", severity="minor")
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
    _write_disposition(epic_dir, "S1", severity="info")
    ctx = _make_ctx(epic_dir, "S1")

    outcome = check_6_critique_blocker_runner(ctx)

    assert outcome.ok
    assert outcome.severity == "info"


def test_non_blocking_critique_requires_primary_disposition(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT))

    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E181"
    _write_critique(epic_dir / "critique", "S2", _MINOR_CRITIQUE)
    ctx = _make_ctx(epic_dir, "S2")

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "primary disposition" in outcome.summary


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


# ---------------------------------------------------------------------------
# S4 — blocker-evidence discipline
# ---------------------------------------------------------------------------


def _make_ctx_with_plan(
    epic_dir: Path, work_unit_id: str = "S1", plan: dict | None = None
) -> CheckContext:
    from woof.checks import CheckContext

    return CheckContext(
        epic_id=1,
        work_unit_id=work_unit_id,
        repo_root=REPO_ROOT,
        epic_dir=epic_dir,
        plan=plan or {"epic_id": 1, "goal": "test", "work_units": [{"id": work_unit_id}]},
        critique=None,
    )


def _write_epic_md(epic_dir: Path, outcomes: list[str], cds: list[str]) -> None:
    epic_dir.mkdir(parents=True, exist_ok=True)
    outcome_lines = "\n".join(
        f"  - id: {oid}\n    statement: test\n    verification: automated\n    deprecated: false"
        for oid in outcomes
    )
    cd_lines = "\n".join(
        f"  - id: {cdid}\n    title: test\n    related_outcomes: [{outcomes[0] if outcomes else 'O1'}]"
        for cdid in cds
    )
    (epic_dir / "EPIC.md").write_text(
        f"---\nepic_id: 1\ngoal: test\nobservable_outcomes:\n{outcome_lines}\n"
        f"contract_decisions:\n{cd_lines}\nacceptance_criteria: []\n---\nBody.\n"
    )


def _blocker_critique(work_unit_id: str, evidence: str) -> str:
    return (
        f"---\ntarget: work_unit\ntarget_id: {work_unit_id}\nseverity: blocker\n"
        f'timestamp: "2026-01-01T00:00:00Z"\nharness: test-reviewer\n'
        f"findings:\n  - id: F1\n    severity: blocker\n    summary: test finding\n"
        f"    evidence: {evidence!r}\n---\nBody.\n"
    )


def test_blocker_without_evidence_fails_O4(tmp_path: Path) -> None:
    """O4 S4: blocker finding with no evidence → check_6 fails (evidence discipline)."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    critique = (
        "---\ntarget: work_unit\ntarget_id: S1\nseverity: blocker\n"
        'timestamp: "2026-01-01T00:00:00Z"\nharness: test\n'
        "findings:\n  - id: F1\n    severity: blocker\n    summary: missing evidence\n---\n"
    )
    _write_critique(epic_dir / "critique", "S1", critique)
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "resolvable evidence" in outcome.summary
    assert "F1" in (outcome.evidence or "")
    assert "no evidence" in (outcome.evidence or "")


def test_blocker_with_unresolvable_evidence_fails_O4(tmp_path: Path) -> None:
    """O4 S4: blocker finding with prose-only evidence (no resolvable ref) → check_6 fails."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "The implementation is wrong and should be fixed"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "resolvable evidence" in outcome.summary
    assert "F1" in (outcome.evidence or "")


def test_blocker_with_file_line_evidence_passes_O4(tmp_path: Path) -> None:
    """O4 S4: blocker with file:line evidence resolving to a tracked file → check_6 reports blocker severity (not evidence error)."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "src/woof/graph/readiness.py:42 is the offending line"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_work_unit_id_evidence_passes_O4(tmp_path: Path) -> None:
    """O4 S4: blocker evidence containing a known story id (S1) resolves."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "S1 does not implement the required contract"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_outcome_id_evidence_passes_O4(tmp_path: Path) -> None:
    """O4 S4: blocker evidence containing a known outcome id (O1) resolves."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_epic_md(epic_dir, outcomes=["O1"], cds=["CD1"])
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "O1 has no test coverage"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_cd_id_evidence_passes_O4(tmp_path: Path) -> None:
    """O4 S4: blocker evidence containing a known CD id resolves."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_epic_md(epic_dir, outcomes=["O1"], cds=["CD1"])
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "CD1 is not implemented in the diff"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_schema_ref_evidence_passes_O4(tmp_path: Path) -> None:
    """O4 S4: blocker evidence containing a schema ref that exists resolves."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "schemas/critique.schema.json is violated by the output"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_quality_gate_id_evidence_passes_O4(tmp_path: Path) -> None:
    """O4 S4: blocker evidence with explicit gate:<name> prefix resolves against [gates.*]."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "gate:lint fails on the staged diff"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_non_blocking_findings_unaffected_by_evidence_rule_O4(tmp_path: Path) -> None:
    """O4 S4: minor/info findings do not require evidence; check_6 is unaffected."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E181"
    _write_critique(epic_dir / "critique", "S2", _MINOR_CRITIQUE)
    _write_disposition(epic_dir, "S2", severity="minor")
    ctx = _make_ctx(epic_dir, "S2")

    outcome = check_6_critique_blocker_runner(ctx)

    assert outcome.ok
    assert outcome.severity == "minor"


# ---------------------------------------------------------------------------
# R1 — file:line resolver accepts extensionless and dotfiles
# ---------------------------------------------------------------------------


def test_blocker_with_extensionless_file_line_evidence_passes_R1(tmp_path: Path) -> None:
    """R1: blocker evidence citing a tracked extensionless file (justfile:1) resolves."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "justfile:1 the build target is misconfigured"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_dotfile_line_evidence_passes_R1(tmp_path: Path) -> None:
    """R1: blocker evidence citing a tracked dotfile (.gitignore:1) resolves."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", ".gitignore:1 pattern incorrectly excludes artefacts"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_untracked_path_line_still_fails_R1(tmp_path: Path) -> None:
    """R1: blocker evidence citing an untracked path:line does not resolve."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "nonexistent_untracked_file_xyz:42 is wrong"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "resolvable evidence" in outcome.summary


# ---------------------------------------------------------------------------
# R2 — backtick/paren-wrapped file:line refs resolve
# ---------------------------------------------------------------------------


def test_blocker_with_backtick_wrapped_file_line_resolves_R2(tmp_path: Path) -> None:
    """R2: evidence like `src/foo.py:42` (backtick-wrapped) resolves when the path is tracked."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "See `src/woof/graph/nodes.py:1` for the offending line"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_paren_wrapped_file_line_resolves_R2(tmp_path: Path) -> None:
    """R2: evidence like (src/foo.py:42) (paren-wrapped) resolves when the path is tracked."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "The bug is at (src/woof/graph/nodes.py:1) in the node runner"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


# ---------------------------------------------------------------------------
# R5 — roll-up honesty enforced via shared helper
# ---------------------------------------------------------------------------


def _minor_top_blocker_finding_critique(work_unit_id: str, evidence: str = "") -> str:
    ev_line = f"    evidence: {evidence!r}\n" if evidence else ""
    return (
        f"---\ntarget: work_unit\ntarget_id: {work_unit_id}\nseverity: minor\n"
        f'timestamp: "2026-01-01T00:00:00Z"\nharness: test-reviewer\n'
        f"findings:\n  - id: F1\n    severity: blocker\n    summary: dishonest roll-up\n"
        f"{ev_line}---\nBody.\n"
    )


def test_rollup_mismatch_minor_top_blocker_finding_fails_R5(tmp_path: Path) -> None:
    """R5: critique with minor top-level severity but a blocker finding → roll-up mismatch → check_6 fails."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _minor_top_blocker_finding_critique("S1"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "minor" in outcome.summary
    assert "blocker" in outcome.summary


def test_rollup_mismatch_info_top_blocker_finding_fails_R5(tmp_path: Path) -> None:
    """R5: critique with info top-level severity but a blocker finding → roll-up mismatch → check_6 fails."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    critique = (
        "---\ntarget: work_unit\ntarget_id: S1\nseverity: info\n"
        'timestamp: "2026-01-01T00:00:00Z"\nharness: test-reviewer\n'
        "findings:\n  - id: F1\n    severity: blocker\n    summary: sneaky blocker\n---\nBody.\n"
    )
    _write_critique(epic_dir / "critique", "S1", critique)
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"


def test_rollup_mismatch_blocker_finding_bad_evidence_also_fails_R5(tmp_path: Path) -> None:
    """R5: minor top-level + blocker finding with no evidence → fails (roll-up catches it; evidence also bad)."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _minor_top_blocker_finding_critique("S1", evidence=""),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"


# ---------------------------------------------------------------------------
# R6 — strict explicit-reference grammar + blocker-needs-findings
# ---------------------------------------------------------------------------


def test_bare_gate_name_in_prose_does_not_resolve_R6(tmp_path: Path) -> None:
    """R6: bare gate name in prose ('the test is wrong') does NOT resolve; gate: prefix required."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    # 'test' is a real gate name but cited without the gate: prefix — must fail
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "the test is wrong and needs fixing"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "resolvable evidence" in outcome.summary


def test_gate_explicit_prefix_resolves_R6(tmp_path: Path) -> None:
    """R6: gate:test explicit prefix resolves when 'test' is a real gate name."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "gate:test fails on the staged diff"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary


def test_blocker_with_no_findings_fails_rollup_R6(tmp_path: Path) -> None:
    """R6: blocker top-level severity with no findings fails roll-up validation."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    critique = (
        "---\ntarget: work_unit\ntarget_id: S1\nseverity: blocker\n"
        'timestamp: "2026-01-01T00:00:00Z"\nharness: test\n'
        "findings: []\n---\nBody.\n"
    )
    _write_critique(epic_dir / "critique", "S1", critique)
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "blocker" in outcome.summary


def test_nonexistent_gate_name_with_prefix_does_not_resolve_R6(tmp_path: Path) -> None:
    """R6: gate:nonexistent does not resolve when 'nonexistent' is not a declared gate."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "gate:nonexistent_gate_xyz is the problem"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "resolvable evidence" in outcome.summary


def test_honest_blocker_critique_still_reported_as_blocker_R5(tmp_path: Path) -> None:
    """R5: honest blocker critique (roll-up matches, evidence resolves) → check_6 still reports blocker."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.runners.check_6_critique_blocker import check_6_critique_blocker_runner

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir / "critique",
        "S1",
        _blocker_critique("S1", "src/woof/graph/nodes.py:1 the offending call"),
    )
    ctx = _make_ctx_with_plan(epic_dir)

    outcome = check_6_critique_blocker_runner(ctx)

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert "critique severity is blocker" in outcome.summary
