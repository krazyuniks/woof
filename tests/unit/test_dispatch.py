"""Black-box tests for ``woof dispatch``.

Most tests use ``--dry-run`` so they do not actually spawn claude/codex subprocesses.
Token-parser helpers are exercised via fixtures of recorded harness output.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.support import DEFAULT_PROJECT_KEY, seed_project_config
from woof import state

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
KEY = DEFAULT_PROJECT_KEY
EXPECTED_TRUSTED_RUNTIME_POLICY = {
    "mode": "trusted-local",
    "woof_runtime_constraints": [],
    "cli_permission_mode": "interactive TUI harness profile flags",
    "safety_boundary": (
        "commit-safety checks, reviewer critique, human gates, transaction manifests, "
        "and commit decisions"
    ),
}


pytestmark = pytest.mark.host_only


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


_CONFIG_OVERRIDES: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _reset_config_overrides() -> Iterator[None]:
    """Each test starts from the default project config."""

    _CONFIG_OVERRIDES.clear()
    yield
    _CONFIG_OVERRIDES.clear()


def _merge_overrides(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_overrides(target[key], value)
        else:
            target[key] = value


def _seed_config(overrides: dict[str, Any]) -> None:
    """Seed the operator-home project config, accumulating across helpers."""

    _merge_overrides(_CONFIG_OVERRIDES, overrides)
    seed_project_config(_CONFIG_OVERRIDES)


def _dispatch_env(bin_dir: Path, home: Path | None = None) -> dict[str, str]:
    """Environment for a dispatch subprocess: stub PATH plus the operator home."""

    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(home) if home else "/tmp"),
        "WOOF_HOME": os.environ["WOOF_HOME"],
        "WOOF_PROJECT": os.environ["WOOF_PROJECT"],
    }


@pytest.fixture
def woof_project(tmp_path: Path) -> Path:
    """Delivery checkout plus the operator-home config the dispatcher reads."""
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    _write_policy(project)
    return project


def _write_policy(
    project: Path,
    *,
    default_run_profile: str = "default",
    producer_harness: str = "codex",
    producer_model: str | None = "gpt-5.5",
    producer_effort: str | None = "xhigh",
    reviewer_harness: str = "claude",
    reviewer_model: str | None = "claude-opus-4-7",
    reviewer_effort: str | None = "max",
    extra_profiles: dict[str, Any] | None = None,
) -> None:
    run_profiles: dict[str, Any] = {
        "default": {
            "producer": {
                "harness": producer_harness,
                "model": producer_model,
                "effort": producer_effort,
            },
            "reviewer": {
                "harness": reviewer_harness,
                "model": reviewer_model,
                "effort": reviewer_effort,
            },
        }
    }
    run_profiles.update(extra_profiles or {})
    _seed_config(
        {
            "default_run_profile": default_run_profile,
            "profiles": {"B": {"commit": True, "push": True}},
            "verification": {"command": "just check", "timeout_seconds": 600},
            "run_profiles": run_profiles,
            "checks": {"floor": ["quality-gates"], "review_size": None},
            "cartography": {"floor": "none"},
            "drain": {"merge_after_ready_pr": True},
            "dispatch": {"timeouts": {"default_minutes": 15}},
        }
    )


def run_dispatch(
    project: Path,
    *args: str,
    stdin: str = "do the thing\n",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), "dispatch", *args],
        capture_output=True,
        text=True,
        input=stdin,
        cwd=project,
        env=env,
    )


def test_profile_a_run_metadata_records_worktree_derivation() -> None:
    from woof.cli.dispatcher import ensure_run_metadata

    state.epic_dir(KEY, 7).mkdir(parents=True)
    _seed_config(
        {
            "delivery": {"profile": "A"},
            "profiles": {
                "B": None,
                "A": {
                    "github_repo": "example/project",
                    "ready_label": "ready",
                    "merge_path_groups": [],
                    "worktree": {"root": "worktrees"},
                },
            },
            "cartography": {"floor": "none"},
        }
    )
    state.plan_path(KEY, 7).write_text(
        json.dumps(
            {
                "epic_id": 7,
                "goal": "Record worktree metadata.",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "One",
                        "summary": "One.",
                        "paths": ["a"],
                        "deps": [],
                        "tests": {},
                        "state": "pending",
                    },
                    {
                        "id": "S2",
                        "title": "Two",
                        "summary": "Two.",
                        "paths": ["b"],
                        "deps": ["S1"],
                        "tests": {},
                        "state": "pending",
                    },
                ],
            }
        )
        + "\n"
    )

    run_id = ensure_run_metadata(KEY, 7, datetime(2026, 7, 7, tzinfo=UTC))

    payload = json.loads((state.runs_root(KEY, 7) / "run.json").read_text())
    assert payload["run_id"] == run_id
    assert payload["worktrees"] == {
        "derivation": "unit_id",
        "root": "worktrees",
        "unit_paths": {"S1": "worktrees/S1", "S2": "worktrees/S2"},
    }


# ---------------------------------------------------------------------------
# argv construction (via --dry-run)
# ---------------------------------------------------------------------------


def test_dry_run_reviewer_uses_tmux_harness_profile_argv(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "reviewer",
        "--epic",
        "42",
        "--work-unit",
        "S3",
        "--dry-run",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["argv"] == [
        "cld",
        "--model",
        "claude-opus-4-7",
        "--effort",
        "max",
        "--dangerously-skip-permissions",
    ]
    assert payload["prompt_transport"] == "tmux_harness_prompt_file"
    assert payload["epic"] == 42
    assert payload["work_unit_id"] == "S3"
    assert payload["role"] == "reviewer"
    assert payload["adapter"] == "claude"
    assert payload["harness"] == "claude"
    assert payload["effort"] == "max"
    assert payload["mcp"] == []
    assert payload["timeout_min"] == 15
    assert payload["timeouts"] == {
        "default_minutes": 15,
        "idle_seconds": 600.0,
        "completion_grace_seconds": 60.0,
        "completion_tail_cap_seconds": 120.0,
    }
    assert payload["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY


def test_dry_run_primary_uses_tmux_harness_profile_argv(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "42",
        "--dry-run",
        stdin="critique this\n",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["argv"] == [
        "codex",
        "-s",
        "danger-full-access",
        "-a",
        "never",
        "-m",
        "gpt-5.5",
        "-c",
        "model_reasoning_effort=xhigh",
    ]
    assert payload["prompt_transport"] == "tmux_harness_prompt_file"
    assert payload["work_unit_id"] is None
    assert payload["adapter"] == "codex"
    assert payload["harness"] == "codex"
    assert payload["effort"] == "xhigh"
    assert payload["timeouts"]["default_minutes"] == 15
    assert payload["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY


def test_dry_run_warm_producer_records_session_mode(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "42",
        "--work-unit",
        "S1",
        "--session-mode",
        "warm-producer",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["session_mode"] == "warm-producer"
    assert payload["role"] == "primary"
    assert payload["work_unit_id"] == "S1"


def test_warm_producer_requires_primary_work_unit(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "reviewer",
        "--epic",
        "42",
        "--work-unit",
        "S1",
        "--session-mode",
        "warm-producer",
        "--dry-run",
    )

    assert proc.returncode == 2
    assert "warm-producer requires --role primary and --work-unit" in proc.stderr


def test_model_profile_overrides_route_model_and_effort(woof_project: Path) -> None:
    _write_policy(
        woof_project,
        default_run_profile="default",
        producer_model="gpt-5.5-mini",
        producer_effort="low",
        reviewer_model="claude-sonnet-4-6",
        reviewer_effort="high",
    )

    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "42",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["model_profile"] == "default"
    assert payload["profile_role"] == "producer"
    assert payload["model"] == "gpt-5.5-mini"
    assert payload["effort"] == "low"
    assert "-m" in payload["argv"]
    assert "gpt-5.5-mini" in payload["argv"]
    assert "model_reasoning_effort=low" in payload["argv"]


def test_env_model_profile_selects_alternate_profile(woof_project: Path) -> None:
    _write_policy(
        woof_project,
        extra_profiles={
            "cheap": {
                "producer": {"harness": "codex", "model": "gpt-5.5-mini", "effort": "low"},
                "reviewer": {"harness": "claude", "model": "claude-sonnet-4-6", "effort": "low"},
            }
        },
    )
    env = {**os.environ, "WOOF_MODEL_PROFILE": "cheap"}

    proc = run_dispatch(
        woof_project,
        "--role",
        "reviewer",
        "--epic",
        "42",
        "--dry-run",
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["model_profile"] == "cheap"
    assert payload["profile_role"] == "reviewer"
    assert payload["model"] == "claude-sonnet-4-6"
    assert payload["effort"] == "low"
    assert "--model" in payload["argv"]
    assert "claude-sonnet-4-6" in payload["argv"]


def test_env_model_profile_harness_change_uses_target_defaults(woof_project: Path) -> None:
    _write_policy(
        woof_project,
        extra_profiles={
            "claude_primary": {
                "producer": {"harness": "claude"},
                "reviewer": {"harness": "claude", "model": "claude-sonnet-4-6", "effort": "low"},
            }
        },
    )
    env = {**os.environ, "WOOF_MODEL_PROFILE": "claude_primary"}

    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "42",
        "--dry-run",
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["adapter"] == "claude"
    assert payload["model"] == "sonnet"
    assert payload["effort"] == "high"
    assert payload["argv"] == [
        "cld",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--dangerously-skip-permissions",
    ]


# ---------------------------------------------------------------------------
# node route keys
# ---------------------------------------------------------------------------


def test_route_key_records_node_group_without_changing_policy_route(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--route-key",
        "execution",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["adapter"] == "codex"
    assert payload["route_key"] == "execution"
    assert payload["config_role"] == "producer"
    assert payload["argv"][0] == "codex"


def test_dry_run_without_route_key_records_null(woof_project: Path) -> None:
    """Dispatching without --route-key leaves route_key null in the structured payload."""
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["route_key"] is None


def test_route_key_cli_rejects_unknown_group(woof_project: Path) -> None:
    """CLI rejects a --route-key value that is not a known node group."""
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--route-key",
        "executon",  # typo
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "executon" in proc.stderr


def test_dispatch_timeouts_reject_boolean_values() -> None:
    from woof.project_config import ProjectConfigError, load_project_config

    _seed_config({"dispatch": {"timeouts": {"default_minutes": True}}})
    with pytest.raises(ProjectConfigError, match=r"timeouts\.default_minutes"):
        load_project_config()

    _seed_config({"dispatch": {"timeouts": {"default_minutes": 30, "idle_seconds": False}}})
    with pytest.raises(ProjectConfigError, match=r"timeouts\.idle_seconds"):
        load_project_config()


def test_prompt_file_overrides_stdin(woof_project: Path, tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("from file")
    proc = run_dispatch(
        woof_project,
        "--role",
        "reviewer",
        "--epic",
        "1",
        "--prompt-file",
        str(prompt_file),
        "--dry-run",
        stdin="ignored",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["prompt_transport"] == "tmux_harness_prompt_file"
    assert "from file" not in payload["argv"]


def test_dry_run_records_repo_relative_artefacts(woof_project: Path) -> None:
    docs = woof_project / "docs"
    docs.mkdir()
    (docs / "contract.md").write_text("contract\n")
    (docs / "notes.json").write_text("{}\n")

    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--artefact",
        "docs/contract.md",
        "--artefact-loaded",
        "docs/notes.json",
        "--artefact",
        "docs/contract.md",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["artefacts_loaded"] == [
        "docs/contract.md",
        "docs/notes.json",
    ]
    assert payload["prompt_bytes"] == len(b"do the thing\n")
    assert payload["artefact_bytes"] == len(b"contract\n") + len(b"{}\n")


@pytest.mark.parametrize(
    "artefact",
    ["/tmp/outside.md", "../outside.md", "~/.claude/projects/session.jsonl"],
)
def test_dispatch_rejects_non_repo_relative_artefacts(woof_project: Path, artefact: str) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--artefact",
        artefact,
        "--dry-run",
    )

    assert proc.returncode == 2
    assert "not repo-relative" in proc.stderr


def test_dispatch_rejects_missing_artefact(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--artefact",
        "docs/missing.md",
        "--dry-run",
    )

    assert proc.returncode == 2
    assert "does not exist as a file" in proc.stderr


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def test_dispatch_outside_a_git_checkout(tmp_path: Path) -> None:
    """The delivery root comes from git; outside a checkout dispatch fails loud."""
    proc = run_dispatch(
        tmp_path,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "not inside a git checkout" in proc.stderr


def test_missing_project_config(woof_project: Path) -> None:
    """A project key with no config in the operator home fails loud; no repo fallback."""
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
        env={**os.environ, "WOOF_PROJECT": "never-seeded"},
    )
    assert proc.returncode == 2
    assert "missing Woof project config" in proc.stderr


def test_unknown_role(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "ghost",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "dispatch role 'ghost'" in proc.stderr


def test_non_dispatch_role_rejected(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "gate-resolver",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "dispatch role 'gate-resolver'" in proc.stderr


def test_legacy_target_mismatch(woof_project: Path) -> None:
    """The deprecated positional target must agree with the resolved role adapter."""
    proc = run_dispatch(
        woof_project,
        "claude",
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "resolves adapter='codex'" in proc.stderr


def test_invalid_work_unit_id(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--work-unit",
        "1-story",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "--work-unit" in proc.stderr


def test_empty_prompt(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
        stdin="   \n",
    )
    assert proc.returncode == 2
    assert "empty prompt" in proc.stderr


def test_schema_invalid_project_config(woof_project: Path) -> None:
    """The project config is schema-validated before dispatch; a bad section is rejected."""
    _seed_config({"agents": {"timeouts": {"default_minutes": 15}}})
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "schema invalid" in proc.stderr


# ---------------------------------------------------------------------------
# dispatcher helpers — exercised by importing the module
# ---------------------------------------------------------------------------


def _import_woof_module():
    """Import the CLI module that owns dispatch token parsing."""
    from woof.cli import dispatcher

    return dispatcher


def test_audit_file_stem_is_path_safe() -> None:
    mod = _import_woof_module()
    started_at = datetime(2026, 5, 19, 12, 0, 1, 123456, tzinfo=UTC)

    stem = mod.audit_file_stem(
        "codex",
        "primary/../../reviewer?x",
        started_at,
        process_id=99,
    )

    assert stem == "codex-primary-reviewer-x-20260519T120001123456Z-p99"
    assert "/" not in stem
    assert "." not in stem


def test_reserve_audit_base_avoids_existing_prompt_collision(tmp_path: Path) -> None:
    mod = _import_woof_module()
    started_at = datetime(2026, 5, 19, 12, 0, 1, 123456, tzinfo=UTC)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    first = mod.reserve_audit_base(
        audit_dir,
        "codex",
        "primary",
        started_at,
        "first prompt",
        process_id=99,
    )
    second = mod.reserve_audit_base(
        audit_dir,
        "codex",
        "primary",
        started_at,
        "second prompt",
        process_id=99,
    )

    assert first.name == "codex-primary-20260519T120001123456Z-p99"
    assert second.name == "codex-primary-20260519T120001123456Z-p99-2"
    assert first.with_suffix(".prompt").read_text() == "first prompt"
    assert second.with_suffix(".prompt").read_text() == "second prompt"


def test_executor_result_ready_signals_readiness_not_validity(tmp_path: Path) -> None:
    mod = _import_woof_module()
    path = tmp_path / "executor_result.json"

    # Missing file is not ready: keep polling.
    assert mod._executor_result_ready(path, 7, "S1") is False

    # A present-but-malformed result is READY: the executor wrote its final artefact, so the
    # poll loop must stop. Validity (malformed JSON -> incomplete_stage_state gate ->
    # abandon_work_unit) is the Stage-5 verification node's job, not this poll predicate's;
    # returning False here would strand a malformed result at wallclock timeout instead of the
    # abandon gate (see test_malformed_stage_state_gate_can_abandon_work_unit).
    path.write_text('{"epic_id": 7, "work_unit_id"', encoding="utf-8")
    assert mod._executor_result_ready(path, 7, "S1") is True

    # A complete result for a different unit is not this unit's result: keep polling.
    path.write_text(json.dumps({"epic_id": 7, "work_unit_id": "S2"}), encoding="utf-8")
    assert mod._executor_result_ready(path, 7, "S1") is False

    # A complete, matching result is ready.
    path.write_text(json.dumps({"epic_id": 7, "work_unit_id": "S1"}), encoding="utf-8")
    assert mod._executor_result_ready(path, 7, "S1") is True


def test_harness_registry_builds_minimal_launch_argv() -> None:
    """The consolidated harness registry produces interactive TUI launch argv."""
    mod = _import_woof_module()
    argv = mod.build_launch_argv("claude", model="", effort="")
    assert argv == ["cld", "--dangerously-skip-permissions"]

    argv = mod.build_launch_argv("codex", model="", effort="")
    assert argv == [
        "codex",
        "-s",
        "danger-full-access",
        "-a",
        "never",
    ]


def test_codex_harness_defaults_to_gpt_56_sol_high_and_accepts_max() -> None:
    mod = _import_woof_module()
    resolved = mod.resolve_harness_config("codex")

    assert resolved.model == "gpt-5.6-sol"
    assert resolved.effort == "high"
    assert mod.build_launch_argv("codex", effort="max")[-2:] == [
        "-c",
        "model_reasoning_effort=max",
    ]


def test_codex_harness_rejects_unsupported_effort() -> None:
    mod = _import_woof_module()

    with pytest.raises(mod.HarnessError, match="codex effort 'minimal' is not supported"):
        mod.build_launch_argv("codex", model=None, effort="minimal")


# ---------------------------------------------------------------------------
# end-to-end with a stub harness on PATH
# ---------------------------------------------------------------------------


SCHEMA_JSONL_EVENTS = REPO_ROOT / "schemas" / "jsonl-events.schema.json"
WOOF_VALIDATE = [str(WOOF_BIN), "validate", "--schema", "jsonl-events"]


def _make_stub(bin_dir: Path, name: str, payload: str, stdin_path: Path | None = None) -> None:
    """Write an interactive TUI stub that honours tmux_harness' file protocol."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    names = {name}
    if name == "claude":
        names.add("cld")
    for executable in names:
        script = bin_dir / executable
        script.write_text(
            f"""#!/usr/bin/env python3
import pathlib
import re
import sys

payload = {payload!r}
stdin_path = {str(stdin_path)!r}
print("ready > ", flush=True)
buf = ""
for line in sys.stdin:
    buf += line
    prompt = re.search(r"(\\S+/prompt\\.txt)", buf)
    answer = re.search(r"(\\S+/answer\\.txt)", buf)
    done = re.search(r"(\\S+/answer\\.done)", buf)
    if prompt and answer and done:
        original = pathlib.Path(prompt.group(1)).read_text(encoding="utf-8")
        if stdin_path != "None":
            pathlib.Path(stdin_path).write_text(original, encoding="utf-8")
        pathlib.Path(answer.group(1)).write_text(payload, encoding="utf-8")
        pathlib.Path(done.group(1)).write_text("DONE", encoding="utf-8")
        break
""",
            encoding="utf-8",
        )
        script.chmod(0o755)


def _make_lingering_stub(bin_dir: Path, name: str, payload: str, stdin_path: Path) -> None:
    """The tmux harness completes on sentinel files; lingering stdout is no longer used."""
    _make_stub(bin_dir, name, payload, stdin_path)


def _structured_payload(
    *,
    verdict: str = "pass",
    evidence: str = "S1",
    session_id: str = "worker-session-1",
    tokens_in: int = 7,
    tokens_out: int = 11,
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "evidence": evidence,
            "usage": {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 13,
            },
            "session": {"id": session_id, "thread_id": "thread-1"},
        }
    )


@pytest.mark.tmux_substrate
def test_end_to_end_claude_writes_audit_and_jsonl(woof_project: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    claude_response = _structured_payload(session_id="worker-session-claude")
    stdin_path = tmp_path / "claude.stdin"
    _make_stub(bin_dir, "claude", claude_response, stdin_path=stdin_path)
    (woof_project / "CONTRACT.md").write_text("contract\n")
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "--role",
            "reviewer",
            "--epic",
            "7",
            "--work-unit",
            "S2",
            "--artefact",
            "CONTRACT.md",
        ],
        capture_output=True,
        text=True,
        input="run the story\n",
        cwd=woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert stdin_path.read_text() == "run the story\n"

    # Audit artefacts written
    audit_dir = state.audit_dir(KEY, 7)
    files = sorted(p.name for p in audit_dir.iterdir())
    suffixes = {Path(f).suffix for f in files}
    assert {".prompt", ".output", ".stderr", ".meta"} <= suffixes

    meta_file = next(audit_dir.glob("*.meta"))
    meta = json.loads(meta_file.read_text())
    assert meta["harness"] == "claude"
    assert meta["adapter"] == "claude"
    assert meta["role"] == "reviewer"
    assert meta["effort"] == "max"
    assert meta["epic_id"] == 7
    assert meta["work_unit_id"] == "S2"
    assert meta["artefacts_loaded"] == ["CONTRACT.md"]
    assert meta["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY
    assert meta["exit_type"] == "clean"
    assert meta["exit_code"] == 0
    assert meta["terminal_seen"] is True
    assert meta["prompt_bytes"] == len(b"run the story\n")
    assert meta["artefact_bytes"] == len(b"contract\n")
    assert meta["output_bytes"] == len(claude_response.encode())
    assert meta["stderr_bytes"] == 0
    assert meta["tokens"] == {
        "tokens_in": 7,
        "tokens_out": 11,
        "cache_read_tokens": 0,
        "cache_write_tokens": 13,
    }
    assert meta["verdict"] == "pass"
    assert meta["evidence"] == "S1"
    assert meta["worker_session_id"] == "worker-session-claude"
    assert meta["worker_session_thread_id"] == "thread-1"
    assert meta["tmux_transport"] == "tmux:claude"
    assert meta["run_id"].startswith("run-7-")
    assert meta["work_unit_id"] == "S2"
    assert meta["attempt_id"]
    assert meta["prompt_hash"]
    assert meta["prompt_version"] == f"sha256:{meta['prompt_hash']}"
    assert Path(meta["attempt_path"]).parent == state.runs_root(KEY, 7) / "attempts"

    # dispatch.jsonl events validate against the shipped schema
    jsonl = state.dispatch_events_path(KEY, 7)
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    events = [json.loads(ln) for ln in lines]
    assert events[0]["event"] == "subprocess_spawned"
    assert events[1]["event"] == "subprocess_returned"
    assert events[0]["effort"] == "max"
    assert events[0]["mcp"] == []
    assert events[0]["argv"][-1] == "<prompt:tmux-file>"
    assert events[0]["argv"][0] == "cld"
    assert events[0]["prompt_transport"] == "tmux_harness_prompt_file"
    assert events[0]["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY
    assert events[0]["artefacts_loaded"] == ["CONTRACT.md"]
    assert events[0]["prompt_bytes"] == len(b"run the story\n")
    assert events[0]["artefact_bytes"] == len(b"contract\n")
    assert events[0]["run_id"] == meta["run_id"]
    assert events[0]["work_unit_id"] == "S2"
    assert events[0]["attempt_id"] == meta["attempt_id"]
    assert events[1]["artefacts_loaded"] == ["CONTRACT.md"]
    assert events[1]["prompt_transport"] == "tmux_harness_prompt_file"
    assert "runtime_policy" not in events[1]
    assert events[1]["exit_type"] == "clean"
    assert events[1]["prompt_bytes"] == len(b"run the story\n")
    assert events[1]["artefact_bytes"] == len(b"contract\n")
    assert events[1]["output_bytes"] == len(claude_response.encode())
    assert events[1]["stderr_bytes"] == 0
    assert events[1]["tokens_in"] == 7
    assert events[1]["tokens_out"] == 11
    assert events[1]["verdict"] == "pass"
    assert events[1]["evidence"] == "S1"
    assert events[1]["worker_session_id"] == "worker-session-claude"
    assert events[1]["tmux_transport"] == "tmux:claude"
    assert events[1]["run_id"] == meta["run_id"]
    assert events[1]["work_unit_id"] == "S2"
    assert events[1]["attempt_id"] == meta["attempt_id"]

    attempt = json.loads(Path(meta["attempt_path"]).read_text())
    assert attempt["attempt_kind"] == "dispatch"
    assert attempt["run_id"] == meta["run_id"]
    assert attempt["work_unit_id"] == "S2"

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


@pytest.mark.tmux_substrate
def test_repeated_review_uses_cached_verdict(git_woof_project: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    response = _structured_payload(verdict="pass", evidence="first pass")
    stdin_path = tmp_path / "claude.stdin"
    _make_stub(bin_dir, "claude", response, stdin_path=stdin_path)
    (git_woof_project / "feature.py").write_text("print('hello')\n")
    subprocess.run(
        ["git", "add", "feature.py"], cwd=git_woof_project, check=True, capture_output=True
    )
    env = _dispatch_env(bin_dir, tmp_path)

    proc1 = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "--role",
            "reviewer",
            "--epic",
            "35",
            "--work-unit",
            "S1",
        ],
        capture_output=True,
        text=True,
        input="review staged diff\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc1.returncode == 0, proc1.stderr

    for stub in bin_dir.iterdir():
        stub.unlink()
    proc2 = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "--role",
            "reviewer",
            "--epic",
            "35",
            "--work-unit",
            "S1",
        ],
        capture_output=True,
        text=True,
        input="review staged diff\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc2.returncode == 0, proc2.stderr

    jsonl = state.dispatch_events_path(KEY, 35)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    cache_hit = next(e for e in events if e["event"] == "review_cache_hit")
    returned = next(e for e in events if e["event"] == "subprocess_returned")
    assert cache_hit["review_cache_hit"] is True
    assert cache_hit["review_cache_key"] == returned["review_cache_key"]
    assert cache_hit["diff_hash"] == returned["diff_hash"]
    assert cache_hit["prompt_version"] == returned["prompt_version"]
    assert cache_hit["work_unit_id"] == "S1"
    assert cache_hit["verdict"] == "pass"

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


@pytest.mark.tmux_substrate
def test_conflicting_review_verdict_records_instability(
    git_woof_project: Path, tmp_path: Path
) -> None:
    from woof.cli.dispatcher import _review_key, _sha256_text, _staged_diff_hash

    prompt = "review staged diff\n"
    (git_woof_project / "feature.py").write_text("print('hello')\n")
    subprocess.run(
        ["git", "add", "feature.py"], cwd=git_woof_project, check=True, capture_output=True
    )
    diff_hash = _staged_diff_hash(git_woof_project)
    assert diff_hash is not None
    prompt_hash = _sha256_text(prompt)
    prompt_version = f"sha256:{prompt_hash}"
    review_cache_key = _review_key(
        work_unit_id="S1", diff_hash=diff_hash, prompt_version=prompt_version
    )
    attempts_dir = state.review_cache_dir(KEY, 36) / "attempts"
    attempts_dir.mkdir(parents=True)
    (attempts_dir / "prior.json").write_text(
        json.dumps(
            {
                "review_cache_key": review_cache_key,
                "attempt_id": "prior",
                "work_unit_id": "S1",
                "verdict": "pass",
            }
        )
    )

    bin_dir = tmp_path / "bin"
    response = _structured_payload(verdict="blocker", evidence="conflict")
    _make_stub(bin_dir, "claude", response)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "--role",
            "reviewer",
            "--epic",
            "36",
            "--work-unit",
            "S1",
        ],
        capture_output=True,
        text=True,
        input=prompt,
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = state.dispatch_events_path(KEY, 36)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = next(e for e in events if e["event"] == "subprocess_returned")
    instability_path = Path(returned["review_instability_path"])
    record = json.loads(instability_path.read_text().splitlines()[0])
    assert record["review_cache_key"] == review_cache_key
    assert record["prior_verdicts"] == ["pass"]
    assert record["new_verdict"] == "blocker"

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


# ---------------------------------------------------------------------------
# S7: error_signature, rate_limit, and HEAD/branch fields
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialise a git repo with one commit so HEAD is readable."""
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "test@woof.dev"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(cmd, cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


@pytest.fixture
def git_woof_project(woof_project: Path) -> Path:
    """Woof project with a real git repository so HEAD/branch are readable."""
    _init_git_repo(woof_project)
    return woof_project


def _make_stderr_stub(
    bin_dir: Path,
    name: str,
    payload: str,
    stderr_text: str,
    stdin_path: Path | None = None,
) -> None:
    """Interactive TUI stub that never writes a sentinel and leaves an error in the pane."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    names = {name}
    if name == "claude":
        names.add("cld")
    for executable in names:
        script = bin_dir / executable
        script.write_text(
            f"""#!/usr/bin/env python3
import sys
import time

print("ready > ", flush=True)
print({stderr_text!r}, flush=True)
for _line in sys.stdin:
    time.sleep(60)
""",
            encoding="utf-8",
        )
        script.chmod(0o755)


@pytest.mark.tmux_substrate
def test_subprocess_returned_records_head_branch_fields(
    git_woof_project: Path, tmp_path: Path
) -> None:
    """subprocess_returned carries head_before/after and branch_before/after from a git repo."""
    bin_dir = tmp_path / "bin"
    claude_response = json.dumps(
        {
            "type": "result",
            "session_id": "00000000-0000-0000-0000-000000000010",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )
    stdin_path = tmp_path / "claude.stdin"
    _make_stub(bin_dir, "claude", claude_response, stdin_path=stdin_path)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "20"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = state.dispatch_events_path(KEY, 20)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = next(e for e in events if e["event"] == "subprocess_returned")

    import re

    assert "head_before" in returned
    assert "head_after" in returned
    assert "branch_before" in returned
    assert "branch_after" in returned
    assert re.fullmatch(r"[0-9a-f]{7,40}", returned["head_before"])
    assert returned["head_before"] == returned["head_after"]
    assert returned["branch_before"] == returned["branch_after"]

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


@pytest.mark.tmux_substrate
def test_subprocess_returned_records_error_signature_from_stderr(
    git_woof_project: Path, tmp_path: Path
) -> None:
    """subprocess_returned carries error_signature when the subprocess writes to stderr."""
    _seed_config({"dispatch": {"timeouts": {"default_minutes": 0.001}}})
    bin_dir = tmp_path / "bin"
    claude_response = json.dumps(
        {
            "type": "result",
            "session_id": "00000000-0000-0000-0000-000000000011",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )
    stderr_text = "error: bad value in /home/ci/project/src/foo.py:42:10"
    stdin_path = tmp_path / "claude.stdin"
    _make_stderr_stub(bin_dir, "claude", claude_response, stderr_text, stdin_path)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "21"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 1, proc.stderr

    jsonl = state.dispatch_events_path(KEY, 21)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = next(e for e in events if e["event"] == "subprocess_returned")

    assert "error_signature" in returned
    sig = returned["error_signature"]
    assert "/home" not in sig
    assert ":42:10" not in sig
    assert "bad value" in sig
    assert len(sig) <= 256

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


@pytest.mark.tmux_substrate
def test_subprocess_returned_records_rate_limit_when_detected(
    git_woof_project: Path, tmp_path: Path
) -> None:
    """subprocess_returned carries rate_limit='rate_limited' when adapter signals it."""
    bin_dir = tmp_path / "bin"
    claude_response = _structured_payload(
        evidence="API error: 429 too many requests - rate limit exceeded",
        session_id="worker-session-rate-limit",
    )
    stdin_path = tmp_path / "claude.stdin"
    _make_stub(bin_dir, "claude", claude_response, stdin_path)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "22"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = state.dispatch_events_path(KEY, 22)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = next(e for e in events if e["event"] == "subprocess_returned")

    assert returned.get("rate_limit") == "rate_limited"

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


@pytest.mark.tmux_substrate
def test_subprocess_returned_no_rate_limit_field_on_clean_run(
    git_woof_project: Path, tmp_path: Path
) -> None:
    """rate_limit field is absent when no rate-limit signal is detected."""
    bin_dir = tmp_path / "bin"
    claude_response = json.dumps(
        {
            "type": "result",
            "session_id": "00000000-0000-0000-0000-000000000013",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )
    stdin_path = tmp_path / "claude.stdin"
    _make_stub(bin_dir, "claude", claude_response, stdin_path=stdin_path)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "23"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = state.dispatch_events_path(KEY, 23)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = next(e for e in events if e["event"] == "subprocess_returned")

    assert "rate_limit" not in returned

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


def test_existing_consumers_unaffected_by_new_optional_fields() -> None:
    """nodes._dispatch_outcome_from_events still parses returned events with S7 fields
    (additive, nothing breaks)."""
    from woof.graph.nodes import _dispatch_outcome_from_events  # type: ignore[attr-defined]

    event_with_new_fields = {
        "event": "subprocess_returned",
        "at": "2026-06-16T12:00:00Z",
        "epic_id": 99,
        "role": "reviewer",
        "pid": 1234,
        "exit_code": 0,
        "exit_type": "clean",
        "duration_ms": 500,
        "timed_out": False,
        "terminal_seen": True,
        "harness": "claude",
        "adapter": "claude",
        "error_signature": "error: something went wrong at <path>",
        "rate_limit": "rate_limited",
        "head_before": "abc1234",
        "head_after": "abc1234",
        "branch_before": "main",
        "branch_after": "main",
    }
    exit_type, exit_code = _dispatch_outcome_from_events(
        [event_with_new_fields],
        role="reviewer",
        epic_id=99,
        work_unit_id=None,
    )
    assert exit_type == "clean"
    assert exit_code == 0


@pytest.mark.tmux_substrate
def test_end_to_end_tmux_sentinel_completion_counts_as_success(
    woof_project: Path, tmp_path: Path
) -> None:
    _seed_config(
        {
            "dispatch": {
                "timeouts": {
                    "default_minutes": 1,
                    "idle_seconds": 5,
                    "completion_grace_seconds": 0.2,
                    "completion_tail_cap_seconds": 1,
                }
            }
        }
    )
    bin_dir = tmp_path / "bin"
    claude_response = _structured_payload(
        session_id="worker-session-sentinel",
        tokens_in=1,
        tokens_out=2,
    )
    stdin_path = tmp_path / "claude.stdin"
    _make_lingering_stub(bin_dir, "claude", claude_response, stdin_path)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "--role",
            "reviewer",
            "--epic",
            "8",
        ],
        capture_output=True,
        text=True,
        input="finish then linger\n",
        cwd=woof_project,
        env=env,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert stdin_path.read_text() == "finish then linger\n"
    jsonl = state.dispatch_events_path(KEY, 8)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert [event["event"] for event in events] == [
        "subprocess_spawned",
        "subprocess_returned",
    ]
    assert events[1]["exit_type"] == "clean"
    assert events[1]["tokens_in"] == 1
    assert events[1]["tokens_out"] == 2
    assert events[1]["worker_session_id"] == "worker-session-sentinel"

    meta_file = next(state.audit_dir(KEY, 8).glob("*.meta"))
    meta = json.loads(meta_file.read_text())
    assert meta["exit_type"] == "clean"
    assert meta["timed_out"] is False
    assert meta["terminal_seen"] is True

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


@pytest.mark.tmux_substrate
def test_end_to_end_codex_records_thread_and_audit_path(woof_project: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    codex_stream = json.dumps(
        {
            "verdict": "pass",
            "evidence": "S1",
            "usage": {
                "tokens_in": 50,
                "cache_read_tokens": 10,
                "tokens_out": 7,
            },
            "session": {"thread_id": "thr-1"},
        }
    )
    stdin_path = tmp_path / "codex.stdin"
    _make_stub(bin_dir, "codex", codex_stream, stdin_path=stdin_path)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "--role",
            "primary",
            "--epic",
            "9",
        ],
        capture_output=True,
        text=True,
        input="critique me\n",
        cwd=woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert stdin_path.read_text() == "critique me\n"

    jsonl = state.dispatch_events_path(KEY, 9)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = events[1]
    assert returned["effort"] == "xhigh"
    assert returned["argv"][-1] == "<prompt:tmux-file>"
    assert returned["argv"][0] == "codex"
    assert returned["prompt_transport"] == "tmux_harness_prompt_file"
    assert "runtime_policy" not in returned
    assert returned["exit_type"] == "clean"
    assert returned["tokens_in"] == 50
    assert returned["tokens_out"] == 7
    assert returned["cache_read_tokens"] == 10
    assert returned["prompt_bytes"] == len(b"critique me\n")
    assert returned["artefact_bytes"] == 0
    assert returned["output_bytes"] == len(codex_stream.encode())
    assert returned["stderr_bytes"] == 0
    assert returned["verdict"] == "pass"
    assert returned["worker_session_thread_id"] == "thr-1"

    meta_file = next(state.audit_dir(KEY, 9).glob("*.meta"))
    meta = json.loads(meta_file.read_text())
    assert meta["exit_type"] == "clean"
    assert meta["prompt_bytes"] == len(b"critique me\n")
    assert meta["worker_session_thread_id"] == "thr-1"

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


@pytest.mark.tmux_substrate
def test_end_to_end_records_route_key_in_jsonl_and_meta(woof_project: Path, tmp_path: Path) -> None:
    """--route-key is recorded on both dispatch events and the meta file, and stays schema-valid.

    The base [roles.primary] is codex with no execution override declared, so the
    route falls back to the base adapter while still recording route_key.
    """
    bin_dir = tmp_path / "bin"
    codex_stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thr-rk"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 5,
                        "cached_input_tokens": 0,
                        "output_tokens": 2,
                        "reasoning_output_tokens": 0,
                    },
                }
            ),
        ]
    )
    stdin_path = tmp_path / "codex.stdin"
    _make_stub(bin_dir, "codex", codex_stream, stdin_path=stdin_path)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "--role",
            "primary",
            "--epic",
            "11",
            "--route-key",
            "execution",
        ],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = state.dispatch_events_path(KEY, 11)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert events[0]["event"] == "subprocess_spawned"
    assert events[0]["route_key"] == "execution"
    assert events[1]["event"] == "subprocess_returned"
    assert events[1]["route_key"] == "execution"

    meta_file = next(state.audit_dir(KEY, 11).glob("*.meta"))
    meta = json.loads(meta_file.read_text())
    assert meta["route_key"] == "execution"

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


# ---------------------------------------------------------------------------
# E21 S2 — dispatch overhead caching
# ---------------------------------------------------------------------------


def _make_claude_stub_simple(bin_dir: Path) -> None:
    """Minimal claude stub that echoes a valid result JSON."""
    payload = json.dumps(
        {
            "type": "result",
            "session_id": "00000000-0000-0000-0000-00000000ee21",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )
    _make_stub(bin_dir, "claude", payload)


@pytest.mark.tmux_substrate
def test_config_schema_cache_hit_on_second_dispatch(woof_project: Path, tmp_path: Path) -> None:
    """Second dispatch with an unchanged project config records config_schema_cache_hit=True."""
    bin_dir = tmp_path / "bin"
    _make_claude_stub_simple(bin_dir)
    env = _dispatch_env(bin_dir, tmp_path)

    def _dispatch(epic: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", str(epic)],
            capture_output=True,
            text=True,
            input="do work\n",
            cwd=woof_project,
            env=env,
        )

    proc1 = _dispatch(30)
    assert proc1.returncode == 0, proc1.stderr
    proc2 = _dispatch(31)
    assert proc2.returncode == 0, proc2.stderr

    def _spawned_event(epic: int) -> dict:
        jsonl = state.dispatch_events_path(KEY, epic)
        events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
        return next(e for e in events if e["event"] == "subprocess_spawned")

    assert _spawned_event(30)["config_schema_cache_hit"] is False
    assert _spawned_event(31)["config_schema_cache_hit"] is True


@pytest.mark.tmux_substrate
def test_config_schema_cache_miss_after_content_change(woof_project: Path, tmp_path: Path) -> None:
    """Changing the project config invalidates the cache; next dispatch re-validates."""
    bin_dir = tmp_path / "bin"
    _make_claude_stub_simple(bin_dir)
    env = _dispatch_env(bin_dir, tmp_path)

    def _dispatch(epic: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", str(epic)],
            capture_output=True,
            text=True,
            input="do work\n",
            cwd=woof_project,
            env=env,
        )

    proc1 = _dispatch(32)
    assert proc1.returncode == 0, proc1.stderr

    _seed_config({"dispatch": {"timeouts": {"default_minutes": 16}}})

    proc2 = _dispatch(33)
    assert proc2.returncode == 0, proc2.stderr

    def _spawned_event(epic: int) -> dict:
        jsonl = state.dispatch_events_path(KEY, epic)
        events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
        return next(e for e in events if e["event"] == "subprocess_spawned")

    assert _spawned_event(32)["config_schema_cache_hit"] is False
    assert _spawned_event(33)["config_schema_cache_hit"] is False


def test_config_schema_cache_key_includes_schema() -> None:
    """A schema change invalidates a pass recorded under the old schema."""
    from woof.cli.dispatcher import _config_schema_cache_key

    config = b"[dispatch.timeouts]\ndefault_minutes = 15\n"
    key_v1 = _config_schema_cache_key(config, b'{"schema": "v1"}')
    # Same config, different schema -> different key -> re-validation, not a stale pass.
    assert key_v1 != _config_schema_cache_key(config, b'{"schema": "v2"}')
    # Stable for identical inputs (a genuine cache hit).
    assert key_v1 == _config_schema_cache_key(config, b'{"schema": "v1"}')
    # Different config, same schema -> different key.
    assert key_v1 != _config_schema_cache_key(
        b"[dispatch.timeouts]\ndefault_minutes = 16\n", b'{"schema": "v1"}'
    )


@pytest.mark.tmux_substrate
def test_runtime_policy_in_spawned_not_in_returned(woof_project: Path, tmp_path: Path) -> None:
    """runtime_policy is emitted once per dispatch: in subprocess_spawned, not subprocess_returned."""
    bin_dir = tmp_path / "bin"
    _make_claude_stub_simple(bin_dir)
    env = _dispatch_env(bin_dir, tmp_path)

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "34"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = state.dispatch_events_path(KEY, 34)
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    spawned = next(e for e in events if e["event"] == "subprocess_spawned")
    returned = next(e for e in events if e["event"] == "subprocess_returned")

    assert spawned["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY
    assert "runtime_policy" not in returned
