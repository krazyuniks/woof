"""check_1_quality_gates — Stage-5 Check 1.

Runs each command declared in ``.woof/quality-gates.toml`` from the repository
root and reports failing gates as structured Stage-5 findings.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from woof.checks import CheckContext, CheckOutcome

CHECK_ID = "check_1_quality_gates"
CONFIG_PATH = ".woof/quality-gates.toml"
DEFAULT_TIMEOUT_SECONDS = 300
KILL_GRACE_SECONDS = 1
OUTPUT_LIMIT = 1200


@dataclass(frozen=True)
class _GateSpec:
    name: str
    command: str
    timeout_seconds: int
    blocking: bool


@dataclass(frozen=True)
class _GateRun:
    spec: _GateSpec
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str


def check_1_quality_gates_runner(ctx: CheckContext) -> CheckOutcome:
    config_path = ctx.repo_root / CONFIG_PATH
    specs, error = _load_gate_specs(config_path)
    if error is not None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=error,
            paths=[CONFIG_PATH],
        )

    runs = [_run_gate(ctx.repo_root, spec) for spec in specs]
    blocking_failures = [
        run for run in runs if run.timed_out or (run.exit_code != 0 and run.spec.blocking)
    ]
    non_blocking_findings = [
        run for run in runs if not run.timed_out and run.exit_code != 0 and not run.spec.blocking
    ]

    if blocking_failures:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"{len(blocking_failures)} quality gate command(s) failed",
            evidence=_format_evidence(blocking_failures, non_blocking_findings),
            paths=[CONFIG_PATH],
            command=_single_command(blocking_failures),
            exit_code=_single_exit_code(blocking_failures),
        )

    if non_blocking_findings:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="minor",
            summary=(
                f"{len(non_blocking_findings)} non-blocking quality gate command(s) "
                "reported findings; blocking gates passed"
            ),
            evidence=_format_evidence([], non_blocking_findings),
            paths=[CONFIG_PATH],
            command=_single_command(non_blocking_findings),
            exit_code=_single_exit_code(non_blocking_findings),
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=f"all {len(runs)} quality gate command(s) passed",
        paths=[CONFIG_PATH],
    )


def _load_gate_specs(config_path: Path) -> tuple[list[_GateSpec], str | None]:
    if not config_path.exists():
        return [], f"quality gates configuration missing: {CONFIG_PATH}"

    try:
        config = tomllib.loads(config_path.read_text())
    except OSError as exc:
        return [], f"quality gates configuration unreadable: {exc}"
    except tomllib.TOMLDecodeError as exc:
        return [], f"quality gates configuration is invalid TOML: {exc}"

    gates = config.get("gates")
    if not isinstance(gates, dict) or not gates:
        return [], "quality gates configuration must define at least one [gates.<name>] table"

    specs: list[_GateSpec] = []
    for name, raw_spec in gates.items():
        if not isinstance(raw_spec, dict):
            return [], f"quality gate {name!r} must be a table"

        spec, error = _parse_gate_spec(name, raw_spec)
        if error is not None:
            return [], error
        specs.append(spec)

    return specs, None


def _parse_gate_spec(name: str, raw_spec: dict[str, Any]) -> tuple[_GateSpec, str | None]:
    command = raw_spec.get("command")
    if not isinstance(command, str) or not command.strip():
        return _empty_spec(name), f"quality gate {name!r} must define a non-empty command"

    timeout_seconds = raw_spec.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        return _empty_spec(name), (f"quality gate {name!r} timeout_seconds must be an integer >= 1")

    blocking = raw_spec.get("blocking", True)
    if not isinstance(blocking, bool):
        return _empty_spec(name), f"quality gate {name!r} blocking must be a boolean"

    return (
        _GateSpec(
            name=name,
            command=command,
            timeout_seconds=timeout_seconds,
            blocking=blocking,
        ),
        None,
    )


def _empty_spec(name: str) -> _GateSpec:
    return _GateSpec(name=name, command="", timeout_seconds=DEFAULT_TIMEOUT_SECONDS, blocking=True)


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


def _format_evidence(blocking_failures: list[_GateRun], findings: list[_GateRun]) -> str:
    parts: list[str] = []
    for run in blocking_failures:
        parts.append(_format_run(run))
    for run in findings:
        parts.append(f"non-blocking finding: {_format_run(run)}")
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
