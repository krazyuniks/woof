"""Tests for check_1_quality_gates — Stage-5 Check 1."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from woof.checks import CheckContext
from woof.checks.runners.check_1_quality_gates import check_1_quality_gates_runner

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
