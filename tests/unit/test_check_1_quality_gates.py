"""Tests for check_1_quality_gates — Stage-5 Check 1."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from woof.checks import CheckContext
from woof.checks.runners.check_1_quality_gates import capture_baseline, check_1_quality_gates_runner

pytestmark = pytest.mark.host_only

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_SCHEMA = REPO_ROOT / "schemas" / "quality-gates-baseline.schema.json"


def _make_ctx(repo_root: Path) -> CheckContext:
    epic_dir = repo_root / ".woof" / "epics" / "E999"
    epic_dir.mkdir(parents=True)
    return CheckContext(
        epic_id=999,
        story_id="S1",
        repo_root=repo_root,
        epic_dir=epic_dir,
        plan={},
        critique=None,
    )


def _write_quality_gates(repo_root: Path, content: str) -> None:
    woof_dir = repo_root / ".woof"
    woof_dir.mkdir(exist_ok=True)
    (woof_dir / "quality-gates.toml").write_text(content)


def _write_baseline(repo_root: Path, gates: dict) -> None:
    woof_dir = repo_root / ".woof"
    woof_dir.mkdir(exist_ok=True)
    record = {
        "captured_at": "2026-06-16T00:00:00Z",
        "gates": gates,
    }
    (woof_dir / "quality-gates-baseline.json").write_text(json.dumps(record))


def _python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def _toml_string(value: str) -> str:
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Original strict-mode tests (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_quality_gate_passes_when_all_commands_exit_zero(tmp_path: Path) -> None:
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.test]
command = {_toml_string(_python_command("print('ok')"))}
timeout_seconds = 5
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert outcome.ok
    assert outcome.id == "check_1_quality_gates"
    assert outcome.severity == "info"
    assert "all 1 quality gate" in outcome.summary
    assert outcome.evidence is None


def test_quality_gate_fails_and_captures_output(tmp_path: Path) -> None:
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(_python_command("import sys; print('bad stdout'); print('bad stderr', file=sys.stderr); sys.exit(3)"))}
timeout_seconds = 5
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.command is not None
    assert outcome.exit_code == 3
    assert outcome.evidence is not None
    assert "gate 'lint' exited 3" in outcome.evidence
    assert "bad stdout" in outcome.evidence
    assert "bad stderr" in outcome.evidence


def test_quality_gate_times_out(tmp_path: Path) -> None:
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.test]
command = {_toml_string(_python_command("import time; time.sleep(5)"))}
timeout_seconds = 1
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.exit_code is None
    assert outcome.evidence is not None
    assert "timed out after 1s" in outcome.evidence


def test_missing_quality_gate_command_fails(tmp_path: Path) -> None:
    _write_quality_gates(
        tmp_path,
        """\
[gates.test]
command = "__woof_missing_quality_gate_command__"
timeout_seconds = 5
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.command == "__woof_missing_quality_gate_command__"
    assert outcome.exit_code in {127, 9009}
    assert outcome.evidence is not None
    assert "__woof_missing_quality_gate_command__" in outcome.evidence


# ---------------------------------------------------------------------------
# Strict mode (explicit) — blocks on any failure  (S5)
# ---------------------------------------------------------------------------


def test_strict_mode_explicit_blocks_on_failure(tmp_path: Path) -> None:
    """Explicitly-declared strict mode blocks on any failure — same as default."""
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(_python_command("import sys; sys.exit(1)"))}
timeout_seconds = 5
mode = "strict"
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"


def test_default_mode_is_strict_blocks_on_failure(tmp_path: Path) -> None:
    """Omitting mode defaults to strict; a failure blocks even with a baseline present."""
    fail_cmd = _python_command("import sys; sys.exit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
""",
    )
    # Provide a baseline that marks lint as red, but the gate has no mode set.
    _write_baseline(tmp_path, {"lint": {"command": fail_cmd, "passed": False}})

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"


# ---------------------------------------------------------------------------
# Baseline mode — suppresses a command red at capture  (S5)
# ---------------------------------------------------------------------------


def test_baseline_suppresses_pre_existing_red(tmp_path: Path) -> None:
    """A baseline-mode gate that was red at capture is reported but does not block."""
    fail_cmd = _python_command("import sys; sys.exit(2)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    _write_baseline(tmp_path, {"lint": {"command": fail_cmd, "passed": False}})

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert outcome.ok
    assert outcome.severity == "minor"
    assert "baseline-suppressed" in outcome.summary
    assert outcome.evidence is not None
    assert "baseline-suppressed finding" in outcome.evidence


def test_baseline_default_mode_suppresses_pre_existing_red(tmp_path: Path) -> None:
    """Project-level default_mode = baseline suppresses a known-red gate."""
    fail_cmd = _python_command("import sys; sys.exit(2)")
    _write_quality_gates(
        tmp_path,
        f"""\
default_mode = "baseline"

[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
""",
    )
    _write_baseline(tmp_path, {"lint": {"command": fail_cmd, "passed": False}})

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert outcome.ok
    assert outcome.severity == "minor"


def test_baseline_without_record_blocks(tmp_path: Path) -> None:
    """A baseline-mode gate with no baseline record on disk blocks normally."""
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(_python_command("import sys; sys.exit(1)"))}
timeout_seconds = 5
mode = "baseline"
""",
    )
    # No baseline file written.

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"


# ---------------------------------------------------------------------------
# Identity change re-arms blocking  (S5)
# ---------------------------------------------------------------------------


def test_identity_change_rearms_blocking(tmp_path: Path) -> None:
    """When the command string differs from the captured identity, the gate blocks again."""
    original_cmd = _python_command("import sys; sys.exit(1)")
    changed_cmd = _python_command("import sys; sys.exit(2)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(changed_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    # Baseline captured with original_cmd, but gate now runs changed_cmd.
    _write_baseline(tmp_path, {"lint": {"command": original_cmd, "passed": False}})

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"


# ---------------------------------------------------------------------------
# New failure beyond the baseline blocks  (S5)
# ---------------------------------------------------------------------------


def test_green_at_capture_going_red_blocks(tmp_path: Path) -> None:
    """A gate that was green at capture but now fails is a regression and always blocks."""
    fail_cmd = _python_command("import sys; sys.exit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.test]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    # Baseline says the gate was passing (green).
    _write_baseline(tmp_path, {"test": {"command": fail_cmd, "passed": True}})

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"


def test_mixed_baseline_blocks_on_new_failure(tmp_path: Path) -> None:
    """With two baseline gates, a new failure (green->red) blocks even if another is suppressed."""
    suppressed_cmd = _python_command("import sys; sys.exit(1)")
    regressed_cmd = _python_command("import sys; sys.exit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(suppressed_cmd)}
timeout_seconds = 5
mode = "baseline"

[gates.test]
command = {_toml_string(regressed_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    _write_baseline(
        tmp_path,
        {
            "lint": {"command": suppressed_cmd, "passed": False},  # red at capture → suppressed
            "test": {"command": regressed_cmd, "passed": True},  # green at capture → regression
        },
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "baseline-suppressed finding" in outcome.evidence


# ---------------------------------------------------------------------------
# Baseline record schema validation  (S5)
# ---------------------------------------------------------------------------


def test_baseline_record_validates_against_schema(tmp_path: Path) -> None:
    """A well-formed baseline record is valid against quality-gates-baseline.schema.json."""
    record = {
        "captured_at": "2026-06-16T12:00:00Z",
        "gates": {
            "lint": {"command": "just lint", "passed": False},
            "test": {"command": "just test", "passed": True},
        },
    }
    record_path = tmp_path / "quality-gates-baseline.json"
    record_path.write_text(json.dumps(record))

    proc = subprocess.run(
        [
            "ajv",
            "validate",
            "--spec=draft2020",
            "-c",
            "ajv-formats",
            "-s",
            str(BASELINE_SCHEMA),
            "-d",
            str(record_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Timeout always blocks — R1 fixes
# ---------------------------------------------------------------------------


def test_advisory_gate_timeout_blocks(tmp_path: Path) -> None:
    """A blocking=false gate that times out must still block Check 1."""
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.slow]
command = {_toml_string(_python_command("import time; time.sleep(5)"))}
timeout_seconds = 1
blocking = false
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "timed out" in outcome.evidence


def test_baseline_mode_gate_timeout_blocks(tmp_path: Path) -> None:
    """A baseline-mode gate that times out must block regardless of the baseline record."""
    slow_cmd = _python_command("import time; time.sleep(5)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.slow]
command = {_toml_string(slow_cmd)}
timeout_seconds = 1
mode = "baseline"
""",
    )
    # Provide a baseline that marks slow as red — baseline suppression must not apply to timeouts.
    _write_baseline(tmp_path, {"slow": {"command": slow_cmd, "passed": False}})

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "timed out" in outcome.evidence


# ---------------------------------------------------------------------------
# Non-string mode returns config error — R1 fixes
# ---------------------------------------------------------------------------


def test_non_string_default_mode_returns_config_error(tmp_path: Path) -> None:
    """A non-string default_mode (e.g. a TOML array) must return a config error, not crash."""
    _write_quality_gates(
        tmp_path,
        f"""\
default_mode = ["baseline"]

[gates.lint]
command = {_toml_string(_python_command("print('ok')"))}
timeout_seconds = 5
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert "default_mode" in outcome.summary


def test_non_string_per_command_mode_returns_config_error(tmp_path: Path) -> None:
    """A non-string per-command mode must return a config error, not crash."""
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(_python_command("print('ok')"))}
timeout_seconds = 5
mode = ["baseline"]
""",
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert "mode" in outcome.summary


# ---------------------------------------------------------------------------
# Non-object baseline JSON fails closed — R2 fix
# ---------------------------------------------------------------------------

BASELINE_PATH = ".woof/quality-gates-baseline.json"


def _write_raw_baseline(repo_root: Path, content: str) -> None:
    woof_dir = repo_root / ".woof"
    woof_dir.mkdir(exist_ok=True)
    (woof_dir / "quality-gates-baseline.json").write_text(content)


def _write_failing_gate(repo_root: Path) -> str:
    fail_cmd = _python_command("raise SystemExit(1)")
    _write_quality_gates(
        repo_root,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    return fail_cmd


@pytest.mark.parametrize("raw", ["[]", "null", "42", '"x"'])
def test_non_object_baseline_fails_closed(tmp_path: Path, raw: str) -> None:
    """A baseline file containing valid JSON that is not an object must not crash the runner.

    It should degrade to 'no baseline' — no suppression, so a red gate still blocks.
    """
    _write_failing_gate(tmp_path)
    _write_raw_baseline(tmp_path, raw)

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"


def test_well_formed_baseline_still_suppresses(tmp_path: Path) -> None:
    """Regression guard: a valid baseline object continues to suppress pre-existing red gates."""
    fail_cmd = _python_command("raise SystemExit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    _write_baseline(tmp_path, {"lint": {"command": fail_cmd, "passed": False}})

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert outcome.ok
    assert "baseline-suppressed" in outcome.summary


# ---------------------------------------------------------------------------
# Schema-invalid baseline fails closed — R3 fix
# ---------------------------------------------------------------------------


def test_baseline_missing_captured_at_fails_closed(tmp_path: Path) -> None:
    """A baseline missing required captured_at suppresses nothing; the gate blocks."""
    fail_cmd = _python_command("raise SystemExit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir(exist_ok=True)
    (woof_dir / "quality-gates-baseline.json").write_text(
        json.dumps({"gates": {"lint": {"command": fail_cmd, "passed": False}}})
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "ignored" in outcome.evidence


def test_baseline_gate_missing_passed_field_fails_closed(tmp_path: Path) -> None:
    """A baseline where a gate entry is missing the required 'passed' field suppresses nothing."""
    fail_cmd = _python_command("raise SystemExit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir(exist_ok=True)
    (woof_dir / "quality-gates-baseline.json").write_text(
        json.dumps(
            {
                "captured_at": "2026-06-16T00:00:00Z",
                "gates": {"lint": {"command": fail_cmd}},  # missing 'passed'
            }
        )
    )

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "ignored" in outcome.evidence


def test_schema_valid_baseline_with_captured_at_suppresses(tmp_path: Path) -> None:
    """A fully schema-valid baseline (with captured_at) still suppresses a red-at-capture gate."""
    fail_cmd = _python_command("raise SystemExit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    # _write_baseline always writes a captured_at field — this is the schema-valid form.
    _write_baseline(tmp_path, {"lint": {"command": fail_cmd, "passed": False}})

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert outcome.ok
    assert outcome.severity == "minor"
    assert "baseline-suppressed" in outcome.summary
    assert outcome.evidence is not None
    assert "baseline-suppressed finding" in outcome.evidence


# ---------------------------------------------------------------------------
# Real-schema validation replaces Pydantic — R4 fixes
#
# Pydantic was looser: it coerced "passed": "false" → False, ignored extra
# fields, and accepted any string for captured_at.  The schema rejects all
# three.  Verify that the runner matches woof-validate's accept/reject.
# ---------------------------------------------------------------------------

_INVALID_BASELINES: list[tuple[str, object]] = [
    (
        "string_passed_field",
        {
            "captured_at": "2026-06-16T00:00:00Z",
            "gates": {"lint": {"command": "just lint", "passed": "false"}},
        },
    ),
    (
        "extra_top_level_field",
        {
            "captured_at": "2026-06-16T00:00:00Z",
            "gates": {"lint": {"command": "just lint", "passed": False}},
            "unknown_field": "should be rejected",
        },
    ),
    (
        "non_datetime_captured_at",
        {
            "captured_at": "2026-06-16",
            "gates": {"lint": {"command": "just lint", "passed": False}},
        },
    ),
    (
        "extra_gate_field",
        {
            "captured_at": "2026-06-16T00:00:00Z",
            "gates": {"lint": {"command": "just lint", "passed": False, "extra": 1}},
        },
    ),
]


def _ajv_rejects(payload: object, tmp_path: Path) -> bool:
    """Return True when ajv-cli rejects ``payload`` against the baseline schema."""
    data_file = tmp_path / "_ajv_payload.json"
    data_file.write_text(json.dumps(payload))
    proc = subprocess.run(
        [
            "ajv",
            "validate",
            "--spec=draft2020",
            "-c",
            "ajv-formats",
            "-s",
            str(BASELINE_SCHEMA),
            "-d",
            str(data_file),
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode != 0


@pytest.mark.parametrize(
    "label,payload", _INVALID_BASELINES, ids=[x[0] for x in _INVALID_BASELINES]
)
def test_schema_invalid_baseline_fails_closed_and_warns(
    tmp_path: Path, label: str, payload: object
) -> None:
    """Any baseline that woof-validate rejects suppresses nothing and warns 'ignored'."""
    fail_cmd = _python_command("raise SystemExit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    (tmp_path / ".woof" / "quality-gates-baseline.json").write_text(json.dumps(payload))

    # Confirm ajv also rejects this payload (runtime strictness == schema).
    assert _ajv_rejects(payload, tmp_path), f"expected ajv to reject payload for case {label!r}"

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok, f"expected blocking for case {label!r}"
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "ignored" in outcome.evidence, f"expected 'ignored' warning for case {label!r}"


def test_schema_valid_baseline_suppresses_and_runtime_matches_ajv(tmp_path: Path) -> None:
    """A fully schema-valid baseline suppresses a red-at-capture gate; ajv also accepts it."""
    fail_cmd = _python_command("raise SystemExit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    valid_payload = {
        "captured_at": "2026-06-16T00:00:00Z",
        "gates": {"lint": {"command": fail_cmd, "passed": False}},
    }
    (tmp_path / ".woof" / "quality-gates-baseline.json").write_text(json.dumps(valid_payload))

    # Confirm ajv accepts the payload too.
    assert not _ajv_rejects(valid_payload, tmp_path), "expected ajv to accept the valid payload"

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert outcome.ok
    assert "baseline-suppressed" in outcome.summary


# ---------------------------------------------------------------------------
# ajv absent from PATH — R5 fix
# ---------------------------------------------------------------------------


def test_ajv_absent_from_path_fails_closed_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ajv is not on PATH, _load_baseline fails closed: no crash, no suppression, warns."""
    fail_cmd = _python_command("raise SystemExit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    # Write a syntactically valid baseline so the only failure is the missing validator.
    valid_payload = {
        "captured_at": "2026-06-16T00:00:00Z",
        "gates": {"lint": {"command": fail_cmd, "passed": False}},
    }
    (tmp_path / ".woof" / "quality-gates-baseline.json").write_text(json.dumps(valid_payload))

    # Strip ajv from PATH; Python gate commands use a full path so gates still execute.
    monkeypatch.setenv("PATH", str(tmp_path / "empty_bin"))

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    # Must not crash; baseline is ignored so the failing gate blocks.
    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "ignored" in outcome.evidence
    assert "ajv" in outcome.evidence


# ---------------------------------------------------------------------------
# Baseline freshness — S6
#
# Wall-clock and iteration expiry both cause the baseline to stop suppressing.
# Only `woof baseline capture` (capture_baseline()) writes the baseline.
# ---------------------------------------------------------------------------


def _write_fresh_baseline(repo_root: Path, gates: dict, *, expiry_seconds: int = 86400) -> None:
    """Write a baseline with freshness metadata and a future wall-clock expiry."""
    woof_dir = repo_root / ".woof"
    woof_dir.mkdir(exist_ok=True)
    captured_at = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "captured_at": captured_at,
        "captured_iteration": 0,
        "expiry_seconds": expiry_seconds,
        "expiry_iterations": 100,
        "gates": gates,
    }
    (woof_dir / "quality-gates-baseline.json").write_text(json.dumps(record))


def _write_run_count(repo_root: Path, count: int) -> None:
    woof_dir = repo_root / ".woof"
    woof_dir.mkdir(exist_ok=True)
    (woof_dir / "wf-run-count").write_text(json.dumps({"count": count}))


def test_fresh_baseline_suppresses_pre_existing_red(tmp_path: Path) -> None:
    """O1 A fresh baseline (within expiry) suppresses a gate that was red at capture."""
    fail_cmd = _python_command("import sys; sys.exit(2)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    _write_fresh_baseline(tmp_path, {"lint": {"command": fail_cmd, "passed": False}})
    _write_run_count(tmp_path, 5)  # within expiry_iterations = 100

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert outcome.ok
    assert "baseline-suppressed" in outcome.summary


def test_wall_clock_expired_baseline_blocks(tmp_path: Path) -> None:
    """O1 A baseline past its wall-clock expiry stops suppressing; the red gate blocks."""
    fail_cmd = _python_command("import sys; sys.exit(2)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    # expiry_seconds = 1: captured in 2020 means it's long expired
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir(exist_ok=True)
    record = {
        "captured_at": "2020-01-01T00:00:00Z",
        "captured_iteration": 0,
        "expiry_seconds": 1,
        "expiry_iterations": 100,
        "gates": {"lint": {"command": fail_cmd, "passed": False}},
    }
    (woof_dir / "quality-gates-baseline.json").write_text(json.dumps(record))

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "ignored" in outcome.evidence
    assert "expired" in outcome.evidence


def test_iteration_expired_baseline_blocks(tmp_path: Path) -> None:
    """O1 A baseline past its iteration expiry stops suppressing; the red gate blocks."""
    fail_cmd = _python_command("import sys; sys.exit(2)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    # captured_iteration=0, expiry_iterations=5, current run count=10 → expired
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir(exist_ok=True)
    record = {
        "captured_at": "2026-06-16T00:00:00Z",
        "captured_iteration": 0,
        "expiry_seconds": 86400 * 30,
        "expiry_iterations": 5,
        "gates": {"lint": {"command": fail_cmd, "passed": False}},
    }
    (woof_dir / "quality-gates-baseline.json").write_text(json.dumps(record))
    _write_run_count(tmp_path, 10)  # 10 > 0 + 5 → expired

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "ignored" in outcome.evidence
    assert "expired" in outcome.evidence


def test_absent_counter_treats_iteration_axis_as_expired(tmp_path: Path) -> None:
    """R2 A committed baseline with captured_iteration > 0 and no counter file fails closed.

    The iteration axis must be treated as expired (baseline ignored) rather than
    suppressing failures, because a missing counter is an untrusted state (fresh
    checkout or reset counter).
    """
    fail_cmd = _python_command("import sys; sys.exit(2)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir(exist_ok=True)
    # Baseline was captured at iteration 5; no wf-run-count file exists (fresh checkout).
    record = {
        "captured_at": (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captured_iteration": 5,
        "expiry_seconds": 86400,
        "expiry_iterations": 100,
        "gates": {"lint": {"command": fail_cmd, "passed": False}},
    }
    (woof_dir / "quality-gates-baseline.json").write_text(json.dumps(record))
    # No wf-run-count file written — counter is absent.

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    # Baseline must be ignored (fail closed); the red gate must block.
    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "ignored" in outcome.evidence
    assert "expired" in outcome.evidence


def test_regressed_counter_treats_iteration_axis_as_expired(tmp_path: Path) -> None:
    """R2 A counter value less than captured_iteration fails closed.

    A counter that is lower than the captured baseline iteration is an impossible
    state going forward; the baseline cannot be trusted to gate suppression.
    """
    fail_cmd = _python_command("import sys; sys.exit(2)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir(exist_ok=True)
    record = {
        "captured_at": (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captured_iteration": 10,
        "expiry_seconds": 86400,
        "expiry_iterations": 100,
        "gates": {"lint": {"command": fail_cmd, "passed": False}},
    }
    (woof_dir / "quality-gates-baseline.json").write_text(json.dumps(record))
    # Counter at 3 — less than captured_iteration 10 (regressed/reset).
    _write_run_count(tmp_path, 3)

    outcome = check_1_quality_gates_runner(_make_ctx(tmp_path))

    assert not outcome.ok
    assert outcome.severity == "blocker"
    assert outcome.evidence is not None
    assert "ignored" in outcome.evidence
    assert "expired" in outcome.evidence


def test_capture_baseline_writes_freshness_metadata(tmp_path: Path) -> None:
    """O1 capture_baseline() is the only path that writes the baseline; it records freshness fields."""
    pass_cmd = _python_command("print('ok')")
    fail_cmd = _python_command("import sys; sys.exit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.pass_gate]
command = {_toml_string(pass_cmd)}
timeout_seconds = 5

[gates.fail_gate]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
""",
    )
    _write_run_count(tmp_path, 42)

    result, error = capture_baseline(tmp_path, expiry_seconds=86400, expiry_iterations=50)

    assert error is None
    assert result.gate_count == 2
    assert result.red_count == 1

    raw = json.loads(result.baseline_path.read_text())
    assert raw["captured_iteration"] == 42
    assert raw["expiry_seconds"] == 86400
    assert raw["expiry_iterations"] == 50
    assert raw["gates"]["pass_gate"]["passed"] is True
    assert raw["gates"]["fail_gate"]["passed"] is False


def test_captured_baseline_validates_against_schema(tmp_path: Path) -> None:
    """O1 A baseline written by capture_baseline() is valid against quality-gates-baseline.schema.json."""
    pass_cmd = _python_command("print('ok')")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(pass_cmd)}
timeout_seconds = 5
""",
    )

    result, error = capture_baseline(tmp_path, expiry_seconds=86400, expiry_iterations=100)
    assert error is None

    proc = subprocess.run(
        [
            "ajv",
            "validate",
            "--spec=draft2020",
            "-c",
            "ajv-formats",
            "-s",
            str(BASELINE_SCHEMA),
            "-d",
            str(result.baseline_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_recapture_only_via_explicit_action(tmp_path: Path) -> None:
    """O1 Running the check runner does not rewrite the baseline — only capture_baseline() does."""
    fail_cmd = _python_command("import sys; sys.exit(1)")
    _write_quality_gates(
        tmp_path,
        f"""\
[gates.lint]
command = {_toml_string(fail_cmd)}
timeout_seconds = 5
mode = "baseline"
""",
    )
    # Write a fresh baseline that suppresses the failure
    _write_fresh_baseline(tmp_path, {"lint": {"command": fail_cmd, "passed": False}})
    original_content = (tmp_path / ".woof" / "quality-gates-baseline.json").read_text()

    check_1_quality_gates_runner(_make_ctx(tmp_path))

    # Baseline must be unchanged — the runner never rewrites it
    current_content = (tmp_path / ".woof" / "quality-gates-baseline.json").read_text()
    assert current_content == original_content
