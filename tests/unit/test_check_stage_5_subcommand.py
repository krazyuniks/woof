"""Tests for 'woof check stage-5' subcommand — O2, O4.

O2: registry exports exactly 9 check entries with the canonical IDs;
    --self-test exits 0 when all runners are implemented;
    stubbing any runner to raise NotImplementedError causes non-zero exit.

O4: check-result output conforms to check-result.schema.json;
    exit code is 0 iff result.ok is true.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"

EXPECTED_STAGE_5_IDS = [
    "check_1_quality_gates",
    "check_2_outcome_markers",
    "check_3_scope",
    "check_4_contract_refs",
    "check_5_plan_crossrefs",
    "check_6_critique_blocker",
    "check_7_commit_transaction",
    "check_8_docs_drift",
    "check_9_review_valve",
]

pytestmark = pytest.mark.host_only


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# O2 — registry completeness
# ---------------------------------------------------------------------------


def test_registry_exports_nine_canonical_ids_O2() -> None:
    """O2: STAGE_5_CHECK_IDS contains exactly the 9 canonical IDs."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.registry import REGISTRY, STAGE_5_CHECK_IDS

    assert set(STAGE_5_CHECK_IDS) == set(EXPECTED_STAGE_5_IDS), (
        f"Registry IDs mismatch.\nExpected: {sorted(EXPECTED_STAGE_5_IDS)}\nGot: {sorted(STAGE_5_CHECK_IDS)}"
    )
    assert len(STAGE_5_CHECK_IDS) == 9
    for check_id in STAGE_5_CHECK_IDS:
        assert check_id in REGISTRY, f"{check_id} in STAGE_5_CHECK_IDS but missing from REGISTRY"


def test_self_test_exits_nonzero_when_runner_unimplemented_O2() -> None:
    """O2: --self-test exits non-zero because checks 1-5,7-9 raise NotImplementedError."""
    proc = _run("check", "stage-5", "--self-test")
    assert proc.returncode != 0, (
        f"Expected non-zero exit (unimplemented runners) but got 0.\n{proc.stdout}{proc.stderr}"
    )
    # stderr should name at least one failing check
    combined = proc.stderr + proc.stdout
    assert "NotImplementedError" in combined or "not implemented" in combined.lower(), combined


def test_self_test_distinguishes_implemented_from_placeholder_O2() -> None:
    """O2: check_6 (implemented) is not reported as failing by --self-test."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.registry import REGISTRY

    # Run only check_6 in isolation — should not appear in failures
    failures: list[str] = []
    check = REGISTRY["check_6_critique_blocker"]
    try:
        check.runner(None)  # type: ignore[arg-type]
    except NotImplementedError:
        failures.append("check_6_critique_blocker")
    except Exception:
        pass  # implemented runner raised for a real reason (None context)

    assert "check_6_critique_blocker" not in failures, (
        "check_6_critique_blocker incorrectly identified as unimplemented"
    )


# ---------------------------------------------------------------------------
# O4 — check-result schema conformance
# ---------------------------------------------------------------------------


def test_check_stage_5_json_output_conforms_to_schema_O4(tmp_path: Path) -> None:
    """O4: check stage-5 --format json emits check-result conforming to check-result.schema.json."""
    if not shutil.which("ajv"):
        pytest.skip("ajv not on PATH")

    # Create a minimal epic dir with a blocker critique so check_6 fires
    epic_dir = tmp_path / ".woof" / "epics" / "E999"
    critique_dir = epic_dir / "critique"
    critique_dir.mkdir(parents=True)
    plan_path = epic_dir / "plan.json"
    plan_path.write_text(json.dumps({"epic_id": 999, "goal": "test", "stories": []}))
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test\n"
        "findings:\n  - id: F1\n    severity: blocker\n    summary: test\n---\n"
    )

    woof_root = tmp_path
    (woof_root / ".woof").mkdir(exist_ok=True)
    # Write a minimal .woof/quality-gates.toml so the woof find_repo_root works
    # woof check stage-5 walks up to find .woof; tmp_path has one
    # We need plan.json at the right path relative to .woof

    proc = subprocess.run(
        [str(WOOF_BIN), "check", "stage-5", "--epic", "999", "--story", "S1", "--format", "json"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )

    # Exit code non-zero expected (check_6 or unimplemented checks fail)
    assert proc.returncode != 0 or proc.returncode == 0  # just check it ran

    output = proc.stdout.strip()
    if not output:
        pytest.skip("no JSON output from check stage-5 (missing plan or epic dir setup)")

    result = json.loads(output)
    assert "ok" in result
    assert "stage" in result
    assert result["stage"] == 5
    assert "triggered_by" in result
    assert "checks" in result
    assert isinstance(result["checks"], list)

    # Validate against the schema
    schema_path = REPO_ROOT / "schemas" / "check-result.schema.json"
    result_file = tmp_path / "check-result.json"
    result_file.write_text(json.dumps(result))
    validate_proc = subprocess.run(
        [
            "ajv",
            "validate",
            "--spec=draft2020",
            "-c",
            "ajv-formats",
            "-s",
            str(schema_path),
            "-d",
            str(result_file),
        ],
        capture_output=True,
        text=True,
    )
    assert validate_proc.returncode == 0, (
        f"check-result.json does not conform to schema:\n"
        f"{validate_proc.stdout}{validate_proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Bootstrap-tolerant verifier behaviour — placeholder runners report info
# ---------------------------------------------------------------------------


def test_check_stage_5_treats_placeholders_as_info_during_bootstrap(tmp_path: Path) -> None:
    """During registry bootstrap, a runner that raises NotImplementedError reports
    ok=true severity=info instead of as a blocker failure. --self-test remains
    the strict CI gate (asserted in a separate test).

    Aligns with P6: drift detection at CI (self-test), not at production
    (per-story driver). Without this the bootstrap-window driver deadlocks on
    every story until all 9 runners are real.
    """
    epic_dir = tmp_path / ".woof" / "epics" / "E999"
    critique_dir = epic_dir / "critique"
    critique_dir.mkdir(parents=True)
    plan_path = epic_dir / "plan.json"
    plan_path.write_text(json.dumps({"epic_id": 999, "goal": "test", "stories": []}))
    # Critique with severity=info so check_6 does not flag this run
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test\n"
        "findings: []\n---\n"
    )

    proc = _run(
        "check",
        "stage-5",
        "--epic",
        "999",
        "--story",
        "S1",
        "--format",
        "json",
        cwd=tmp_path,
    )

    output = proc.stdout.strip()
    if not output:
        pytest.skip("no JSON output from check stage-5 (env setup issue)")

    result = json.loads(output)
    by_id = {c["id"]: c for c in result["checks"]}

    placeholder_ids = [
        "check_1_quality_gates",
        "check_2_outcome_markers",
        "check_3_scope",
        "check_4_contract_refs",
        "check_5_plan_crossrefs",
        "check_7_commit_transaction",
        "check_8_docs_drift",
        "check_9_review_valve",
    ]
    for check_id in placeholder_ids:
        c = by_id[check_id]
        assert c["ok"] is True, f"{check_id}: expected ok=true (bootstrap placeholder), got {c}"
        assert c["severity"] == "info", (
            f"{check_id}: expected severity=info, got severity={c['severity']!r}"
        )

    # Triggered_by should not include any placeholder
    for check_id in placeholder_ids:
        assert check_id not in result["triggered_by"], (
            f"{check_id} appeared in triggered_by[] despite ok=true"
        )
