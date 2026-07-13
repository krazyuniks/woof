"""CLI acceptance coverage for the core Woof workflow.

The stub harnesses, the consumer bootstrap, and the project config all come from
``wf_gate_harness``: there is one definition of the acceptance consumer, not two.
What this module adds is the end-to-end assertion that the CLI drives a spark to
a checked commit, from the source checkout and from an installed wheel alike.

Since ADR-017 the engine writes nothing into the driven repository, so the
commit these tests expect contains delivery paths only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import DEFAULT_PROJECT_KEY
from woof import state

from .wf_gate_harness import (
    REPO_ROOT,
    acceptance_env,
    assert_ok,
    configure_consumer,
    json_stdout,
    jsonl,
    require_host_tools,
    run,
)

pytestmark = [pytest.mark.host_only, pytest.mark.tmux_substrate]

DELIVERY_PATHS = {"app.py", "tests/test_app.py", "schemas/acceptance.schema.json"}


def _install_wheel(tmp_path: Path, env: dict[str, str]) -> Path:
    dist_dir = tmp_path / "dist"
    build = run(
        ["uv", "build", "--wheel", "-o", str(dist_dir), str(REPO_ROOT)], cwd=tmp_path, env=env
    )
    assert_ok(build)
    wheels = sorted(dist_dir.glob("woof-*.whl"))
    assert wheels, build.stdout + build.stderr

    venv = tmp_path / "venv"
    assert_ok(run(["uv", "venv", str(venv)], cwd=tmp_path, env=env))
    python = venv / "bin" / "python"
    assert_ok(
        run(
            ["uv", "pip", "install", "--python", str(python), str(wheels[-1])],
            cwd=tmp_path,
            env=env,
        )
    )
    return python


def _drive_local_tracker_workflow(consumer: Path, env: dict[str, str], woof_cmd: list[str]) -> None:
    created = run(
        [*woof_cmd, "wf", "new", "ship acceptance artefact", "--format", "json"],
        cwd=consumer,
        env=env,
    )
    assert_ok(created)
    assert json_stdout(created)[0]["epic_id"] == 1

    planned = run([*woof_cmd, "wf", "--epic", "1", "--format", "json"], cwd=consumer, env=env)
    assert_ok(planned)
    planned_events = json_stdout(planned)
    assert planned_events[-1]["status"] == "gate_opened"
    assert planned_events[-1]["node_type"] == "plan_gate_open"

    gate = state.gate_path(DEFAULT_PROJECT_KEY, 1)
    assert gate.is_file()

    approved = run([*woof_cmd, "wf", "--epic", "1", "--resolve", "approve"], cwd=consumer, env=env)
    assert_ok(approved)
    assert not gate.exists()

    executed = run([*woof_cmd, "wf", "--epic", "1", "--format", "json"], cwd=consumer, env=env)
    assert_ok(executed)
    assert json_stdout(executed)[-1]["status"] == "epic_complete"

    log = run(["git", "log", "--oneline", "-1"], cwd=consumer, env=env)
    assert_ok(log)
    assert "feat: add gate acceptance artefact" in log.stdout

    # The delivery commit carries the work unit's paths and nothing else: no
    # plan, no event log, no critique, no disposition (ADR-017).
    committed_files = run(["git", "show", "--name-only", "--format="], cwd=consumer, env=env)
    assert_ok(committed_files)
    committed = {line for line in committed_files.stdout.splitlines() if line}
    assert committed == DELIVERY_PATHS, committed

    # The engine's own record of the run is intact - it just lives in the
    # operator home rather than in the checkout it drove.
    dispatch_events = jsonl(state.dispatch_events_path(DEFAULT_PROJECT_KEY, 1))
    spawned = [event for event in dispatch_events if event.get("event") == "subprocess_spawned"]
    assert len(spawned) >= 8
    assert {event["role"] for event in spawned} >= {"primary", "reviewer"}
    assert all(event["runtime_policy"]["mode"] == "trusted-local" for event in spawned)

    epic_events = jsonl(state.epic_events_path(DEFAULT_PROJECT_KEY, 1))
    assert any(event.get("event") == "plan_gate_resolved" for event in epic_events)
    assert any(event.get("event") == "transaction_manifest_verified" for event in epic_events)
    assert any(event.get("event") == "epic_completed" for event in epic_events)


def test_wf_cli_drives_local_tracker_epic_to_work_unit_commit(tmp_path: Path) -> None:
    """The source-checkout CLI can drive the product loop from spark to checked commit."""

    require_host_tools()
    env = acceptance_env(tmp_path, "happy")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    woof_cmd = [str(REPO_ROOT / "bin" / "woof")]
    configure_consumer(consumer, env, woof_cmd=woof_cmd)

    _drive_local_tracker_workflow(consumer, env, woof_cmd)


def test_installed_package_wf_cli_drives_local_tracker_epic_to_work_unit_commit(
    tmp_path: Path,
) -> None:
    """The installed package can drive the same workflow without checkout wrappers."""

    require_host_tools()
    env = acceptance_env(tmp_path, "happy", isolated=True)
    python = _install_wheel(tmp_path, env)
    woof_cmd = [str(python), "-m", "woof"]

    consumer = tmp_path / "installed-consumer"
    consumer.mkdir()
    configure_consumer(consumer, env, woof_cmd=woof_cmd)

    _drive_local_tracker_workflow(consumer, env, woof_cmd)
