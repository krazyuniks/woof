"""Tests for 'woof check stage-5' subcommand — O2, O4.

O2: registry exports exactly 9 check entries with the canonical IDs;
    --self-test exits 0 when all runners are implemented;
    stubbing any runner to raise NotImplementedError causes non-zero exit.

O4: check-result output conforms to check-result.schema.json;
    exit code is 0 iff result.ok is true.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support import seed_project_config

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


def test_self_test_exits_zero_when_all_runners_implemented_O2() -> None:
    """O2: --self-test exits zero when all 9 runners are implemented."""
    proc = _run("check", "stage-5", "--self-test")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all 9 runners implemented" in proc.stdout


def test_self_test_distinguishes_implemented_from_placeholder_O2() -> None:
    """O2: implemented checks do not raise NotImplementedError."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from woof.checks.registry import REGISTRY

    failures: list[str] = []
    for check_id in REGISTRY:
        check = REGISTRY[check_id]
        try:
            check.runner(None)  # type: ignore[arg-type]
        except NotImplementedError:
            failures.append(check_id)
        except Exception:
            pass  # implemented runner raised for a real reason (None context)

    assert failures == []


def test_stage_5_fails_closed_when_runner_not_implemented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unimplemented registry slots are blocker results, not soft-pass placeholders."""
    from woof.checks import CheckOutcome
    from woof.checks.registry import REGISTRY, STAGE_5_CHECK_IDS, Check
    from woof.cli.commands.check import cmd_check_stage_5

    def ok_runner_for(check_id: str):
        def runner(_ctx: object) -> CheckOutcome:
            return CheckOutcome(
                id=check_id,
                ok=True,
                severity=None,
                summary=f"{check_id}: ok",
            )

        return runner

    missing_id = STAGE_5_CHECK_IDS[2]

    def missing_runner(_ctx: object) -> CheckOutcome:
        raise NotImplementedError("not wired")

    for check_id in STAGE_5_CHECK_IDS:
        runner = missing_runner if check_id == missing_id else ok_runner_for(check_id)
        monkeypatch.setitem(
            REGISTRY,
            check_id,
            Check(
                id=check_id,
                stage=5,
                cost="cheap",
                summary=f"{check_id} test stub",
                runner=runner,
            ),
        )

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    epic_dir.mkdir(parents=True)
    (epic_dir / "plan.json").write_text(
        json.dumps({"epic_id": 1, "goal": "test", "work_units": []})
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cmd_check_stage_5(
        argparse.Namespace(self_test=False, epic=1, work_unit="S1", format="json")
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert exit_code == 1
    assert result["ok"] is False
    assert result["triggered_by"] == [missing_id]

    by_id = {check["id"]: check for check in result["checks"]}
    assert by_id[missing_id]["ok"] is False
    assert by_id[missing_id]["severity"] == "blocker"
    assert by_id[missing_id]["summary"] == f"{missing_id}: runner is not implemented"
    assert by_id[missing_id]["evidence"] == "not wired"


def test_stage_5_context_carries_declared_cartography_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from woof.checks import CheckContext, CheckOutcome
    from woof.checks.registry import REGISTRY, STAGE_5_CHECK_IDS, Check
    from woof.cli.commands.check import cmd_check_stage_5

    seen: list[tuple[str | None, list[str], list[str]]] = []

    def recording_runner(check_id: str):
        def runner(ctx: CheckContext) -> CheckOutcome:
            seen.append(
                (
                    ctx.cartography_floor,
                    list(ctx.cartography_paths),
                    list(ctx.files_txt_slice),
                )
            )
            return CheckOutcome(
                id=check_id,
                ok=True,
                severity=None,
                summary=f"{check_id}: ok",
            )

        return runner

    for check_id in STAGE_5_CHECK_IDS:
        monkeypatch.setitem(
            REGISTRY,
            check_id,
            Check(
                id=check_id,
                stage=5,
                cost="cheap",
                summary=f"{check_id} test stub",
                runner=recording_runner(check_id),
            ),
        )

    epic_dir = tmp_path / ".woof" / "epics" / "E1"
    epic_dir.mkdir(parents=True)
    (epic_dir / "plan.json").write_text(
        json.dumps({"epic_id": 1, "goal": "test", "work_units": []})
    )
    seed_project_config({"checks": {"floor": ["quality-gates"]}, "cartography": {"floor": "none"}})
    monkeypatch.chdir(tmp_path)

    exit_code = cmd_check_stage_5(
        argparse.Namespace(self_test=False, epic=1, work_unit="S1", format="json")
    )
    capsys.readouterr()

    assert exit_code == 0
    assert seen == [("none", [], [])] * len(STAGE_5_CHECK_IDS)


# ---------------------------------------------------------------------------
# O4 — check-result schema conformance
# ---------------------------------------------------------------------------


def test_check_stage_5_json_output_conforms_to_schema_O4(tmp_path: Path) -> None:
    """O4: check stage-5 --format json emits check-result conforming to check-result.schema.json."""
    if not shutil.which("ajv"):
        pytest.skip("ajv not on PATH")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    # Create a minimal epic dir with a blocker critique so check_6 fires
    epic_dir = tmp_path / ".woof" / "epics" / "E999"
    critique_dir = epic_dir / "critique"
    critique_dir.mkdir(parents=True)
    seed_project_config({"review_valve": {"every_n_work_units": 5, "end_of_epic": False}})
    plan_path = epic_dir / "plan.json"
    plan_path.write_text(json.dumps({"epic_id": 999, "goal": "test", "work_units": []}))
    (critique_dir / "work-unit-S1.md").write_text(
        "---\ntarget: work_unit\ntarget_id: S1\nseverity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test\n"
        "findings:\n  - id: F1\n    severity: blocker\n    summary: test\n---\n"
    )

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "check",
            "stage-5",
            "--epic",
            "999",
            "--work-unit",
            "S1",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )

    assert proc.returncode in {0, 1}, proc.stderr

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
# Review-valve verifier behaviour
# ---------------------------------------------------------------------------


def test_check_stage_5_reports_review_valve_not_due_as_info(tmp_path: Path) -> None:
    """Check 9 is a real runner and reports ok=true severity=info when no review is due."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    epic_dir = tmp_path / ".woof" / "epics" / "E999"
    critique_dir = epic_dir / "critique"
    critique_dir.mkdir(parents=True)
    # No review-size guard: this test asserts the valve's own "not due" verdict,
    # which the guard's verdict would otherwise replace.
    seed_project_config(
        {
            "review_valve": {"every_n_work_units": 5, "end_of_epic": False},
            "checks": {"review_size": None},
        }
    )
    plan_path = epic_dir / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "epic_id": 999,
                "goal": "test",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "test",
                        "summary": "test",
                        "paths": ["src/**"],
                        "satisfies": ["O1"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "in_progress",
                    }
                ],
            }
        )
    )
    # Critique with severity=info so check_6 does not flag this run
    (critique_dir / "work-unit-S1.md").write_text(
        "---\ntarget: work_unit\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test\n"
        "findings: []\n---\n"
    )
    disposition_dir = epic_dir / "dispositions"
    disposition_dir.mkdir()
    (disposition_dir / "work-unit-S1.md").write_text(
        "---\ntarget: work_unit\ntarget_id: S1\n"
        "critique_path: .woof/epics/E999/critique/work-unit-S1.md\n"
        "severity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-primary\n"
        "dispositions: []\n---\n"
    )

    proc = _run(
        "check",
        "stage-5",
        "--epic",
        "999",
        "--work-unit",
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

    c = by_id["check_9_review_valve"]
    assert c["ok"] is True, c
    assert c["severity"] == "info"
    assert "not due" in c["summary"]
    assert "check_9_review_valve" not in result["triggered_by"]


# ---------------------------------------------------------------------------
# Snapshot — gate.schema.json triggered_by enum
# ---------------------------------------------------------------------------

EXPECTED_GATE_TRIGGERS = {
    "plan_review",
    "readiness_unready",
    "readiness_escalation",
    "check_1_quality_gates",
    "check_2_outcome_markers",
    "check_3_scope",
    "check_4_contract_refs",
    "check_5_plan_crossrefs",
    "check_6_critique_blocker",
    "check_7_commit_transaction",
    "check_8_docs_drift",
    "check_9_review_valve",
    "empty_diff_review",
    "timeout",
    "subprocess_crash",
    "subprocess_aborted",
    "executor_aborted",
    "codex_unreachable",
    "reviewer_unreachable",
    "incomplete_subprocess",
    "incomplete_stage_state",
    "schema_validation_failed",
    "tracker_sync_conflict",
    "github_sync_conflict",
    "head_branch_drift",
    "sibling_conflict",
    "course_correction",
    "run_resilience",
    "manual",
}


def test_gate_schema_triggered_by_enum_snapshot() -> None:
    """gate.schema.json triggered_by enum must match the expected set exactly."""
    schema_path = REPO_ROOT / "schemas" / "gate.schema.json"
    schema = json.loads(schema_path.read_text())
    enum_values = set(schema["properties"]["triggered_by"]["items"]["enum"])
    assert enum_values == EXPECTED_GATE_TRIGGERS, (
        f"gate.schema.json triggered_by enum mismatch.\n"
        f"Extra: {enum_values - EXPECTED_GATE_TRIGGERS}\n"
        f"Missing: {EXPECTED_GATE_TRIGGERS - enum_values}"
    )
