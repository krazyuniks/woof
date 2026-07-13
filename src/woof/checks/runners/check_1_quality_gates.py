"""check_1_quality_gates — Stage-5 Check 1.

Runs each command declared in the project config's ``[gates.<name>]`` sections
from the repository root and reports failing gates as structured Stage-5
findings.

Gate modes
----------
strict (default)
    Any failure from a blocking gate fails Check 1. Unchanged from the original
    behaviour; the default when neither the gate nor the project sets a mode.

baseline
    A gate that was **red at capture time** is reported as a finding but does
    not block, provided its command identity (the exact configured command
    string) is unchanged and a durable baseline record exists in the operator
    home at ``state.quality_gates_baseline_path``.  Two conditions re-arm
    blocking:

    1. The command string differs from the captured identity — the gate was
       reconfigured and its behaviour may have changed.
    2. A gate that was **green at capture** now fails — this is a new
       regression and always blocks regardless of mode.

    No per-failure subtraction is performed; suppression is at command
    granularity only.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from woof import state
from woof.checks import CheckContext, CheckOutcome
from woof.lib.schema_validate import validate_against_schema
from woof.project_config import ProjectConfigError, load_project_config

CHECK_ID = "check_1_quality_gates"
CONFIG_LABEL = "project config [gates.*]"
# Gate mode, timeout, and blocking defaults are resolved by the project-config
# loader; this runner receives the resolved specs.
KILL_GRACE_SECONDS = 1
OUTPUT_LIMIT = 1200

_MODE_BASELINE = "baseline"


@dataclass(frozen=True)
class _GateSpec:
    name: str
    command: str
    timeout_seconds: int
    blocking: bool
    mode: str  # "strict" | "baseline"


@dataclass(frozen=True)
class _GateRun:
    spec: _GateSpec
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _BaselineEntry:
    command: str
    passed: bool


def check_1_quality_gates_runner(ctx: CheckContext) -> CheckOutcome:
    specs, error = _load_gate_specs()
    if error is not None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=error,
            paths=[],
        )

    baseline, baseline_warning = _load_baseline(ctx.project_key)
    runs = [_run_gate(ctx.repo_root, spec) for spec in specs]

    blocking_failures: list[_GateRun] = []
    suppressed_findings: list[_GateRun] = []
    non_blocking_findings: list[_GateRun] = []

    for run in runs:
        failed = run.timed_out or run.exit_code != 0
        if not failed:
            continue
        if run.timed_out:
            # A hung command is always a hard failure: independent of blocking flag and mode.
            blocking_failures.append(run)
            continue
        if not run.spec.blocking:
            non_blocking_findings.append(run)
            continue
        if run.spec.mode == _MODE_BASELINE and _is_suppressed(run, baseline):
            suppressed_findings.append(run)
        else:
            blocking_failures.append(run)

    def _append_warning(evidence: str | None) -> str | None:
        if not baseline_warning:
            return evidence
        return f"{evidence}\n\n{baseline_warning}" if evidence else baseline_warning

    if blocking_failures:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"{len(blocking_failures)} quality gate command(s) failed",
            evidence=_append_warning(
                _format_evidence(blocking_failures, non_blocking_findings, suppressed_findings)
            ),
            paths=[],
            command=_single_command(blocking_failures),
            exit_code=_single_exit_code(blocking_failures),
        )

    if suppressed_findings or non_blocking_findings:
        all_findings = suppressed_findings + non_blocking_findings
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="minor",
            summary=(
                f"{len(suppressed_findings)} baseline-suppressed and "
                f"{len(non_blocking_findings)} non-blocking quality gate finding(s); "
                "blocking gates passed"
            ),
            evidence=_append_warning(
                _format_evidence([], non_blocking_findings, suppressed_findings)
            ),
            paths=[],
            command=_single_command(all_findings),
            exit_code=_single_exit_code(all_findings),
        )

    if baseline_warning:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="minor",
            summary=f"all {len(runs)} quality gate command(s) passed; baseline file was ignored",
            evidence=baseline_warning,
            paths=[],
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=f"all {len(runs)} quality gate command(s) passed",
        paths=[],
    )


def _is_suppressed(run: _GateRun, baseline: dict[str, _BaselineEntry]) -> bool:
    """Return True only if this failure is suppressed by an applicable baseline entry.

    Suppression requires:
    - a baseline entry exists for the gate name,
    - the entry's command matches the current command (identity unchanged),
    - the gate was red (not passed) at capture time.

    A green-at-capture gate that now fails is a new regression and is never suppressed.
    A gate with a changed command identity is re-armed and never suppressed.
    """
    entry = baseline.get(run.spec.name)
    if entry is None:
        return False
    if entry.command != run.spec.command:
        return False
    return not entry.passed


def _check_freshness(data: dict[str, Any]) -> str | None:
    """Return an expiry reason string if the baseline is expired, or None if fresh.

    Wall-clock only: expired when UTC now > captured_at + expiry_seconds.
    On any parse failure of captured_at, the baseline is treated as expired (fail closed).
    A baseline without expiry_seconds has no expiry (backwards-compatible with S5).
    """
    expiry_seconds = data.get("expiry_seconds")
    if expiry_seconds is None:
        return None

    captured_at_str = data.get("captured_at", "")
    try:
        # Normalise trailing lowercase 'z' (accepted by ajv but not by fromisoformat).
        if isinstance(captured_at_str, str) and captured_at_str.endswith("z"):
            captured_at_str = captured_at_str[:-1] + "Z"
        captured_at = datetime.fromisoformat(captured_at_str.replace("Z", "+00:00"))
        elapsed = (datetime.now(UTC) - captured_at).total_seconds()
        if elapsed > expiry_seconds:
            return (
                f"baseline expired: wall-clock age {elapsed:.0f}s exceeds "
                f"expiry_seconds {expiry_seconds}s"
            )
    except (ValueError, AttributeError, TypeError):
        return "baseline expired: captured_at is unparseable; treating as expired (fail closed)"

    return None


def _load_baseline(project_key: str) -> tuple[dict[str, _BaselineEntry], str | None]:
    baseline_path = state.quality_gates_baseline_path(project_key)
    if not baseline_path.exists():
        return {}, None
    try:
        data = json.loads(baseline_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}, None
    if not isinstance(data, dict):
        return {}, None
    ok, errors = validate_against_schema(data, "quality-gates-baseline")
    if not ok:
        return {}, f"baseline file ignored (could not validate — {errors})"
    expiry_reason = _check_freshness(data)
    if expiry_reason is not None:
        return {}, f"baseline file ignored ({expiry_reason})"
    gates: dict[str, Any] = data.get("gates", {})
    return {
        name: _BaselineEntry(command=entry["command"], passed=entry["passed"])
        for name, entry in gates.items()
    }, None


def _load_gate_specs() -> tuple[list[_GateSpec], str | None]:
    try:
        config = load_project_config()
    except ProjectConfigError as exc:
        return [], str(exc)

    if not config.gates:
        return [], f"{CONFIG_LABEL} must define at least one gate"

    return [
        _GateSpec(
            name=gate.name,
            command=gate.command,
            timeout_seconds=gate.timeout_seconds,
            blocking=gate.blocking,
            mode=gate.mode,
        )
        for gate in config.gates
    ], None


def _run_gate(repo_root: Path, spec: _GateSpec) -> _GateRun:
    proc = subprocess.Popen(
        spec.command,
        cwd=repo_root,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    try:
        stdout, stderr = proc.communicate(timeout=spec.timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        return _GateRun(
            spec=spec,
            exit_code=None,
            timed_out=True,
            stdout=stdout or "",
            stderr=stderr or "",
        )

    return _GateRun(
        spec=spec,
        exit_code=proc.returncode,
        timed_out=False,
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _kill_process_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return


@dataclass(frozen=True)
class CaptureResult:
    baseline_path: Path
    gate_count: int
    red_count: int


def capture_baseline(
    project_key: str,
    repo_root: Path,
    expiry_seconds: int,
) -> tuple[CaptureResult, str | None]:
    """Run all configured gates and write a fresh baseline record.

    The gates run in the delivery checkout; the record is durable engine state and
    is written to the operator home. Returns (CaptureResult, error_message). On error
    the baseline is not written. This is the ONLY path that writes the baseline; no
    implicit recapture occurs.
    """
    baseline_path = state.quality_gates_baseline_path(project_key)
    specs, error = _load_gate_specs()
    if error is not None:
        return CaptureResult(baseline_path, 0, 0), error

    runs = [_run_gate(repo_root, spec) for spec in specs]

    captured_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    gates_record: dict[str, Any] = {}
    red_count = 0
    for run in runs:
        passed = not (run.timed_out or run.exit_code != 0)
        gates_record[run.spec.name] = {"command": run.spec.command, "passed": passed}
        if not passed:
            red_count += 1

    record: dict[str, Any] = {
        "captured_at": captured_at,
        "expiry_seconds": expiry_seconds,
        "gates": gates_record,
    }

    ok, errors = validate_against_schema(record, "quality-gates-baseline")
    if not ok:
        return CaptureResult(baseline_path, 0, 0), f"baseline record invalid: {errors}"

    state.atomic_write_text(baseline_path, json.dumps(record, indent=2))

    return CaptureResult(baseline_path, len(runs), red_count), None


def _format_evidence(
    blocking_failures: list[_GateRun],
    findings: list[_GateRun],
    suppressed: list[_GateRun],
) -> str:
    parts: list[str] = []
    for run in blocking_failures:
        parts.append(_format_run(run))
    for run in findings:
        parts.append(f"non-blocking finding: {_format_run(run)}")
    for run in suppressed:
        parts.append(f"baseline-suppressed finding: {_format_run(run)}")
    return "\n\n".join(parts)


def _format_run(run: _GateRun) -> str:
    if run.timed_out:
        status = f"gate {run.spec.name!r} timed out after {run.spec.timeout_seconds}s"
    else:
        status = f"gate {run.spec.name!r} exited {run.exit_code}"

    sections = [f"{status}: {run.spec.command}"]
    stdout = _truncate_output(run.stdout)
    stderr = _truncate_output(run.stderr)
    if stdout:
        sections.append(f"stdout:\n{stdout}")
    if stderr:
        sections.append(f"stderr:\n{stderr}")
    return "\n".join(sections)


def _truncate_output(output: str) -> str:
    text = output.strip()
    if len(text) <= OUTPUT_LIMIT:
        return text
    return f"{text[:OUTPUT_LIMIT]}... [truncated]"


def _single_command(runs: list[_GateRun]) -> str | None:
    if len(runs) == 1:
        return runs[0].spec.command
    return None


def _single_exit_code(runs: list[_GateRun]) -> int | None:
    if len(runs) == 1:
        return runs[0].exit_code
    return None
