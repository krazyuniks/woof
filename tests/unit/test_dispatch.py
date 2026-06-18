"""Black-box tests for ``woof dispatch``.

Most tests use ``--dry-run`` so they do not actually spawn claude/codex subprocesses.
Token-parser helpers are exercised via fixtures of recorded harness output.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
EXPECTED_TRUSTED_RUNTIME_POLICY = {
    "mode": "trusted-local",
    "woof_runtime_constraints": [],
    "cli_permission_mode": "broad public CLI permission flags",
    "safety_boundary": (
        "commit-safety checks, reviewer critique, human gates, transaction manifests, "
        "and commit decisions"
    ),
}


pytestmark = pytest.mark.host_only


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def woof_project(tmp_path: Path) -> Path:
    """Skeleton woof project: ``.woof/agents.toml`` with the standard roles."""
    project = tmp_path / "proj"
    woof_dir = project / ".woof"
    woof_dir.mkdir(parents=True)
    (woof_dir / "agents.toml").write_text("""\
[roles.primary]
adapter = "codex"
model = "gpt-5.5"
effort = "xhigh"
flags = ["--max-turns", "20"]

[roles.reviewer]
adapter = "claude"
model = "claude-opus-4-7"
effort = "max"

[roles.gate-resolver]
adapter = "in-session"

[timeouts]
default_minutes = 15
""")
    return project


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


# ---------------------------------------------------------------------------
# argv construction (via --dry-run)
# ---------------------------------------------------------------------------


def test_dry_run_reviewer_uses_raw_claude_argv(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "reviewer",
        "--epic",
        "42",
        "--story",
        "S3",
        "--dry-run",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["argv"] == [
        "claude",
        "--dangerously-skip-permissions",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "-p",
        "--output-format",
        "json",
        "--model",
        "claude-opus-4-7",
        "--effort",
        "max",
    ]
    assert payload["prompt_transport"] == "stdin"
    assert payload["epic"] == 42
    assert payload["story"] == "S3"
    assert payload["role"] == "reviewer"
    assert payload["adapter"] == "claude"
    assert payload["harness"] == "claude"
    assert payload["effort"] == "max"
    assert payload["mcp"] == []
    assert payload["mcp_config"] == '{"mcpServers":{}}'
    assert payload["timeout_min"] == 15
    assert payload["timeouts"] == {
        "default_minutes": 15,
        "idle_seconds": 600.0,
        "completion_grace_seconds": 60.0,
        "completion_tail_cap_seconds": 120.0,
    }
    assert payload["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY


def test_dry_run_primary_uses_raw_codex_argv(woof_project: Path) -> None:
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
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "-s",
        "danger-full-access",
        "--model",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="xhigh"',
        "--max-turns",
        "20",
    ]
    assert payload["prompt_transport"] == "stdin"
    assert payload["story"] is None
    assert payload["adapter"] == "codex"
    assert payload["harness"] == "codex"
    assert payload["effort"] == "xhigh"
    assert payload["timeouts"]["default_minutes"] == 15
    assert payload["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY


def test_model_profile_overrides_route_model_and_effort(woof_project: Path) -> None:
    (woof_project / ".woof" / "agents.toml").write_text("""\
model_profile = "smoke"

[roles.primary]
adapter = "codex"

[roles.reviewer]
adapter = "claude"
mcp = []

[model_profiles.smoke.roles.primary]
model = "gpt-5.5-mini"
effort = "low"

[model_profiles.smoke.roles.reviewer]
model = "claude-sonnet-4-6"
effort = "high"

[timeouts]
default_minutes = 15
""")

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
    assert payload["model_profile"] == "smoke"
    assert payload["profile_role"] == "primary"
    assert payload["model"] == "gpt-5.5-mini"
    assert payload["effort"] == "low"
    assert "--model" in payload["argv"]
    assert "gpt-5.5-mini" in payload["argv"]
    assert 'model_reasoning_effort="low"' in payload["argv"]


def test_env_model_profile_selects_alternate_profile(woof_project: Path) -> None:
    (woof_project / ".woof" / "agents.toml").write_text("""\
model_profile = "default"

[roles.primary]
adapter = "codex"

[roles.reviewer]
adapter = "claude"
mcp = []

[model_profiles.default.roles.primary]
model = "gpt-5.5"
effort = "xhigh"

[model_profiles.default.roles.reviewer]
model = "claude-opus-4-7"
effort = "max"

[model_profiles.cheap.roles.primary]
model = "gpt-5.5-mini"
effort = "low"

[model_profiles.cheap.roles.reviewer]
model = "claude-sonnet-4-6"
effort = "low"

[timeouts]
default_minutes = 15
""")
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


# ---------------------------------------------------------------------------
# per-node-group route overlays (E20)
# ---------------------------------------------------------------------------


_ROUTES_AGENTS_TOML = """\
[roles.primary]
adapter = "codex"

[roles.reviewer]
adapter = "claude"

[routes.execution.primary]
adapter = "claude"

[timeouts]
default_minutes = 15
"""


def test_route_key_group_override_flips_adapter(woof_project: Path) -> None:
    """A declared [routes.<group>.<role>] override wins over the base [roles.*] adapter."""
    (woof_project / ".woof" / "agents.toml").write_text(_ROUTES_AGENTS_TOML)

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
    assert payload["adapter"] == "claude"
    assert payload["route_key"] == "execution"
    assert payload["argv"][0] == "claude"


def test_route_key_falls_back_to_base_role_when_group_undeclared(woof_project: Path) -> None:
    """An undeclared group falls through to the base role, but still records route_key."""
    (woof_project / ".woof" / "agents.toml").write_text(_ROUTES_AGENTS_TOML)

    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--route-key",
        "discovery",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["adapter"] == "codex"
    assert payload["route_key"] == "discovery"
    assert payload["argv"][0] == "codex"


def test_profile_overlay_composes_with_group_route_override(woof_project: Path) -> None:
    """A profile group entry refines model/effort on top of the resolved group adapter.

    The effort ``max`` is only valid for the claude adapter; codex would raise. A
    returncode of 0 with effort ``max`` proves the profile overlay read the
    route-overridden adapter, not the base [roles.primary] codex adapter.
    """
    (woof_project / ".woof" / "agents.toml").write_text("""\
model_profile = "default"

[roles.primary]
adapter = "codex"

[roles.reviewer]
adapter = "claude"

[routes.execution.primary]
adapter = "claude"

[model_profiles.default.roles.reviewer]
model = "claude-opus-4-7"

[model_profiles.default.routes.execution.primary]
model = "claude-opus-4-8"
effort = "max"

[timeouts]
default_minutes = 15
""")

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
    assert payload["adapter"] == "claude"
    assert payload["model"] == "claude-opus-4-8"
    assert payload["effort"] == "max"
    assert payload["route_key"] == "execution"
    assert "--effort" in payload["argv"]
    assert "max" in payload["argv"]


def test_profile_group_entry_beats_profile_roles_entry(woof_project: Path) -> None:
    """For a route_key dispatch, profile.routes.<group>.<role> wins over profile.roles.<role>."""
    (woof_project / ".woof" / "agents.toml").write_text("""\
model_profile = "foo"

[roles.primary]
adapter = "codex"

[roles.reviewer]
adapter = "claude"

[routes.execution.primary]
adapter = "claude"

[model_profiles.foo.roles.primary]
model = "from-roles"
effort = "high"

[model_profiles.foo.routes.execution.primary]
model = "from-routes"
effort = "max"

[timeouts]
default_minutes = 15
""")

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
    assert payload["adapter"] == "claude"
    assert payload["model"] == "from-routes"
    assert payload["effort"] == "max"
    assert payload["profile_role"] == "primary"
    assert payload["route_key"] == "execution"


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
    (woof_project / ".woof" / "agents.toml").write_text("""\
[roles.primary]
adapter = "codex"

[roles.reviewer]
adapter = "claude"

[timeouts]
default_minutes = 15
""")
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


def test_resolve_role_route_rejects_unknown_route_key() -> None:
    """resolve_role_route raises DispatchConfigError for a non-NODE_GROUPS route_key."""
    from woof.cli.dispatcher import DispatchConfigError, resolve_role_route

    roles = {"primary": {"adapter": "codex"}}
    with pytest.raises(DispatchConfigError, match="unknown route_key"):
        resolve_role_route(roles, "primary", route_key="executon", routes={})


def test_dispatch_timeouts_reject_boolean_values() -> None:
    mod = _import_woof_module()

    with pytest.raises(mod.DispatchConfigError, match=r"timeouts\.default_minutes"):
        mod.dispatch_timeouts({"timeouts": {"default_minutes": True}})

    with pytest.raises(mod.DispatchConfigError, match=r"timeouts\.idle_seconds"):
        mod.dispatch_timeouts({"timeouts": {"idle_seconds": False}})


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
    assert payload["prompt_transport"] == "stdin"
    assert "from file" not in payload["argv"]


def test_dry_run_records_repo_relative_artefacts(woof_project: Path) -> None:
    epic_dir = woof_project / ".woof" / "epics" / "E1"
    epic_dir.mkdir(parents=True)
    (epic_dir / "EPIC.md").write_text("contract\n")
    (epic_dir / "plan.json").write_text("{}\n")

    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--artefact",
        ".woof/epics/E1/EPIC.md",
        "--artefact-loaded",
        ".woof/epics/E1/plan.json",
        "--artefact",
        ".woof/epics/E1/EPIC.md",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["artefacts_loaded"] == [
        ".woof/epics/E1/EPIC.md",
        ".woof/epics/E1/plan.json",
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
        ".woof/epics/E1/missing.md",
        "--dry-run",
    )

    assert proc.returncode == 2
    assert "does not exist as a file" in proc.stderr


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def test_missing_woof_root(tmp_path: Path) -> None:
    proc = run_dispatch(
        tmp_path,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "no .woof/ directory" in proc.stderr


def test_missing_agents_toml(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / ".woof").mkdir(parents=True)
    proc = run_dispatch(
        project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "agents.toml" in proc.stderr


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
    assert "role 'ghost' not declared" in proc.stderr


def test_in_session_role_rejected(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "gate-resolver",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "in-session" in proc.stderr


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


def test_invalid_story_id(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--story",
        "story-1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "S<n>" in proc.stderr


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


def test_schema_invalid_agents_toml(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / ".woof").mkdir(parents=True)
    (project / ".woof" / "agents.toml").write_text("""\
[roles.primary]
adapter = "wrong-adapter"
""")
    proc = run_dispatch(
        project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "schema invalid" in proc.stderr


# ---------------------------------------------------------------------------
# token parsing — exercised by importing the module
# ---------------------------------------------------------------------------


def _import_woof_module():
    """Import the CLI module that owns dispatch token parsing."""
    from woof.cli import dispatcher

    return dispatcher


def test_parse_claude_output() -> None:
    mod = _import_woof_module()
    line = json.dumps(
        {
            "type": "result",
            "session_id": "80e44829-ba61-4640-960c-a3445110b9c3",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 136,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 45262,
            },
        }
    )
    tokens, session = mod.parse_claude_output(line + "\n")
    assert session == "80e44829-ba61-4640-960c-a3445110b9c3"
    assert tokens == {
        "tokens_in": 10,
        "tokens_out": 136,
        "cache_read_tokens": 5,
        "cache_write_tokens": 45262,
    }


def test_parse_claude_output_empty() -> None:
    mod = _import_woof_module()
    tokens, session = mod.parse_claude_output("")
    assert tokens == {}
    assert session is None


def test_claude_terminal_and_parser_ignore_json_non_objects() -> None:
    mod = _import_woof_module()

    for line in ("42", '"ok"', "[]"):
        assert mod.is_claude_terminal_line(line) is False

    tokens, session = mod.parse_claude_output("[]\n")
    assert tokens == {}
    assert session is None


def test_parse_codex_output() -> None:
    mod = _import_woof_module()
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "019dc9a3-1bd8"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 30,
                        "output_tokens": 50,
                        "reasoning_output_tokens": 20,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 200,
                        "cached_input_tokens": 0,
                        "output_tokens": 60,
                        "reasoning_output_tokens": 0,
                    },
                }
            ),
        ]
    )
    tokens, thread = mod.parse_codex_output(stream)
    assert thread == "019dc9a3-1bd8"
    assert tokens == {
        "tokens_in": 300,
        "tokens_out": 130,  # 50 + 20 + 60
        "cache_read_tokens": 30,
    }


def test_count_codex_command_executions_counts_completed_items_once() -> None:
    mod = _import_woof_module()
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"id": "item_1", "type": "command_execution"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "command_execution"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "command_execution"},
                }
            ),
            json.dumps(
                {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message"}}
            ),
            "not json",
        ]
    )

    assert mod.count_codex_command_executions(stream) == 1


def test_codex_terminal_and_parsers_ignore_json_non_objects() -> None:
    mod = _import_woof_module()

    for line in ("42", '"ok"', "[]"):
        assert mod.is_codex_terminal_line(line) is False

    stream = "\n".join(
        [
            "42",
            '"ok"',
            "[]",
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
        ]
    )
    tokens, thread = mod.parse_codex_output(stream)
    assert tokens == {}
    assert thread == "abc"
    assert mod.count_codex_command_executions(stream) == 0


def test_parse_codex_output_no_turns() -> None:
    mod = _import_woof_module()
    line = json.dumps({"type": "thread.started", "thread_id": "abc"})
    tokens, thread = mod.parse_codex_output(line)
    assert tokens == {}
    assert thread == "abc"


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


def test_build_argv_minimal_role() -> None:
    """A role with no model, mcp, think, or flags produces a minimal argv."""
    mod = _import_woof_module()
    argv = mod.build_argv("claude", {"adapter": "claude"}, "hi")
    assert argv == [
        "claude",
        "--dangerously-skip-permissions",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "-p",
        "--output-format",
        "json",
    ]

    argv = mod.build_argv("codex", {"adapter": "codex"}, "hi")
    assert argv == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "-s",
        "danger-full-access",
    ]


def test_legacy_role_config_routes_to_public_adapter(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    woof_dir = project / ".woof"
    woof_dir.mkdir(parents=True)
    (woof_dir / "agents.toml").write_text("""\
[roles.story-executor]
harness = "cld"
model = "claude-sonnet-4-6"

[timeouts]
default_minutes = 15
""")

    proc = run_dispatch(
        project,
        "--role",
        "primary",
        "--epic",
        "1",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["config_role"] == "story-executor"
    assert payload["adapter"] == "claude"
    assert payload["argv"][0] == "claude"


def test_named_mcp_generates_strict_claude_config(woof_project: Path) -> None:
    agents = woof_project / ".woof" / "agents.toml"
    agents.write_text(
        agents.read_text().replace(
            'model = "claude-opus-4-7"\n',
            'model = "claude-opus-4-7"\nmcp = ["chrome-devtools"]\n',
        )
        + """\

[mcp_servers.chrome-devtools]
command = "npx"
args = ["-y", "chrome-devtools-mcp@latest"]
"""
    )

    proc = run_dispatch(
        woof_project,
        "--role",
        "reviewer",
        "--epic",
        "1",
        "--dry-run",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mcp"] == ["chrome-devtools"]
    assert json.loads(payload["mcp_config"]) == {
        "mcpServers": {
            "chrome-devtools": {
                "command": "npx",
                "args": ["-y", "chrome-devtools-mcp@latest"],
            }
        }
    }
    assert "--strict-mcp-config" in payload["argv"]
    assert "--mcp-config" in payload["argv"]


def test_named_mcp_requires_declared_server(woof_project: Path) -> None:
    agents = woof_project / ".woof" / "agents.toml"
    agents.write_text(
        agents.read_text().replace(
            'model = "claude-opus-4-7"\n',
            'model = "claude-opus-4-7"\nmcp = ["chrome-devtools"]\n',
        )
    )

    proc = run_dispatch(
        woof_project,
        "--role",
        "reviewer",
        "--epic",
        "1",
        "--dry-run",
    )

    assert proc.returncode == 2
    assert "[mcp_servers.chrome-devtools]" in proc.stderr


def test_named_mcp_rejects_absolute_host_paths(woof_project: Path) -> None:
    agents = woof_project / ".woof" / "agents.toml"
    agents.write_text(
        agents.read_text().replace(
            'model = "claude-opus-4-7"\n',
            'model = "claude-opus-4-7"\nmcp = ["local-server"]\n',
        )
        + """\

[mcp_servers.local-server]
command = "/usr/local/bin/local-mcp"
"""
    )

    proc = run_dispatch(
        woof_project,
        "--role",
        "reviewer",
        "--epic",
        "1",
        "--dry-run",
    )

    assert proc.returncode == 2
    assert "host-specific path" in proc.stderr


# ---------------------------------------------------------------------------
# end-to-end with a stub harness on PATH
# ---------------------------------------------------------------------------


SCHEMA_JSONL_EVENTS = REPO_ROOT / "schemas" / "jsonl-events.schema.json"
WOOF_VALIDATE = [str(WOOF_BIN), "validate", "--schema", "jsonl-events"]


def _make_stub(bin_dir: Path, name: str, payload: str, stdin_path: Path | None = None) -> None:
    """Write an executable shell script at ``bin_dir/name`` that prints ``payload``."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    stdin_line = f"cat > {shlex.quote(str(stdin_path))}" if stdin_path else "cat >/dev/null"
    script.write_text(
        f"#!/bin/sh\n{stdin_line}\ncat <<'__WOOF_PAYLOAD__'\n{payload}\n__WOOF_PAYLOAD__\n"
    )
    script.chmod(0o755)


def _make_lingering_stub(bin_dir: Path, name: str, payload: str, stdin_path: Path) -> None:
    """Write a stub that emits a terminal payload and then lingers."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(
        f"""#!/usr/bin/env python3
import pathlib
import sys
import time

pathlib.Path({str(stdin_path)!r}).write_text(sys.stdin.read(), encoding="utf-8")
print({payload!r}, flush=True)
time.sleep(5)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)


def test_end_to_end_claude_writes_audit_and_jsonl(woof_project: Path, tmp_path: Path) -> None:
    mod = _import_woof_module()
    bin_dir = tmp_path / "bin"
    claude_response = json.dumps(
        {
            "type": "result",
            "session_id": "00000000-0000-0000-0000-000000000001",
            "usage": {
                "input_tokens": 7,
                "output_tokens": 11,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 13,
            },
        }
    )
    stdin_path = tmp_path / "claude.stdin"
    _make_stub(bin_dir, "claude", claude_response, stdin_path=stdin_path)
    epic_dir = woof_project / ".woof" / "epics" / "E7"
    epic_dir.mkdir(parents=True)
    (epic_dir / "EPIC.md").write_text("contract\n")
    env = {
        "PATH": f"{bin_dir}:{__import__('os').environ['PATH']}",
        "HOME": __import__("os").environ.get("HOME", str(tmp_path)),
    }

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "--role",
            "reviewer",
            "--epic",
            "7",
            "--story",
            "S2",
            "--artefact",
            ".woof/epics/E7/EPIC.md",
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
    audit_dir = woof_project / ".woof" / "epics" / "E7" / "audit"
    files = sorted(p.name for p in audit_dir.iterdir())
    suffixes = {Path(f).suffix for f in files}
    assert {".prompt", ".output", ".stderr", ".meta"} <= suffixes

    meta_file = next(audit_dir.glob("*.meta"))
    meta = json.loads(meta_file.read_text())
    assert meta["harness"] == "claude"
    assert meta["adapter"] == "claude"
    assert meta["role"] == "reviewer"
    assert meta["effort"] == "max"
    assert meta["mcp_config"] == '{"mcpServers":{}}'
    assert meta["epic_id"] == 7
    assert meta["story_id"] == "S2"
    assert meta["artefacts_loaded"] == [".woof/epics/E7/EPIC.md"]
    assert meta["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY
    assert meta["exit_type"] == "clean"
    assert meta["exit_code"] == 0
    assert meta["terminal_seen"] is True
    assert meta["prompt_bytes"] == len(b"run the story\n")
    assert meta["artefact_bytes"] == len(b"contract\n")
    assert meta["output_bytes"] == len(claude_response.encode()) + 1
    assert meta["stderr_bytes"] == 0
    assert meta["tokens"] == {
        "tokens_in": 7,
        "tokens_out": 11,
        "cache_read_tokens": 0,
        "cache_write_tokens": 13,
    }
    assert meta["cc_session_id"] == "00000000-0000-0000-0000-000000000001"
    assert (
        meta["claude_transcript_path"]
        == f"~/.claude/projects/{mod.claude_project_slug(woof_project)}/"
        "00000000-0000-0000-0000-000000000001.jsonl"
    )

    # dispatch.jsonl events validate against the shipped schema
    jsonl = woof_project / ".woof" / "epics" / "E7" / "dispatch.jsonl"
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    events = [json.loads(ln) for ln in lines]
    assert events[0]["event"] == "subprocess_spawned"
    assert events[1]["event"] == "subprocess_returned"
    assert events[0]["effort"] == "max"
    assert events[0]["mcp"] == []
    assert events[0]["argv"][-1] == "<prompt:stdin>"
    assert events[0]["argv"][0] == "claude"
    assert events[0]["prompt_transport"] == "stdin"
    assert events[0]["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY
    assert events[0]["artefacts_loaded"] == [".woof/epics/E7/EPIC.md"]
    assert events[0]["prompt_bytes"] == len(b"run the story\n")
    assert events[0]["artefact_bytes"] == len(b"contract\n")
    assert events[1]["artefacts_loaded"] == [".woof/epics/E7/EPIC.md"]
    assert events[1]["prompt_transport"] == "stdin"
    assert "runtime_policy" not in events[1]
    assert events[1]["exit_type"] == "clean"
    assert events[1]["prompt_bytes"] == len(b"run the story\n")
    assert events[1]["artefact_bytes"] == len(b"contract\n")
    assert events[1]["output_bytes"] == len(claude_response.encode()) + 1
    assert events[1]["stderr_bytes"] == 0
    assert events[1]["tokens_in"] == 7
    assert events[1]["tokens_out"] == 11
    assert events[1]["cc_session_id"] == "00000000-0000-0000-0000-000000000001"
    assert events[1]["claude_transcript_path"].startswith("~/.claude/projects/")

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
    """Stub that emits ``stderr_text`` on stderr before printing ``payload`` on stdout."""
    import shlex

    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    stdin_line = f"cat > {shlex.quote(str(stdin_path))}" if stdin_path else "cat >/dev/null"
    script.write_text(
        f"#!/bin/sh\n{stdin_line}\n"
        f"printf '%s\\n' {shlex.quote(stderr_text)} >&2\n"
        f"cat <<'__WOOF_PAYLOAD__'\n{payload}\n__WOOF_PAYLOAD__\n"
    )
    script.chmod(0o755)


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
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "20"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = git_woof_project / ".woof" / "epics" / "E20" / "dispatch.jsonl"
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


def test_subprocess_returned_records_error_signature_from_stderr(
    git_woof_project: Path, tmp_path: Path
) -> None:
    """subprocess_returned carries error_signature when the subprocess writes to stderr."""
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
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "21"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = git_woof_project / ".woof" / "epics" / "E21" / "dispatch.jsonl"
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


def test_subprocess_returned_records_rate_limit_when_detected(
    git_woof_project: Path, tmp_path: Path
) -> None:
    """subprocess_returned carries rate_limit='rate_limited' when adapter signals it."""
    bin_dir = tmp_path / "bin"
    claude_response = json.dumps(
        {
            "type": "result",
            "session_id": "00000000-0000-0000-0000-000000000012",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )
    stderr_text = "API error: 429 too many requests - rate limit exceeded"
    stdin_path = tmp_path / "claude.stdin"
    _make_stderr_stub(bin_dir, "claude", claude_response, stderr_text, stdin_path)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "22"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = git_woof_project / ".woof" / "epics" / "E22" / "dispatch.jsonl"
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = next(e for e in events if e["event"] == "subprocess_returned")

    assert returned.get("rate_limit") == "rate_limited"

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


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
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "23"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=git_woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = git_woof_project / ".woof" / "epics" / "E23" / "dispatch.jsonl"
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
        story_id=None,
    )
    assert exit_type == "clean"
    assert exit_code == 0


def test_end_to_end_completed_lingering_counts_as_success(
    woof_project: Path, tmp_path: Path
) -> None:
    agents_path = woof_project / ".woof" / "agents.toml"
    agents_path.write_text(
        agents_path.read_text().replace(
            "[timeouts]\ndefault_minutes = 15\n",
            "[timeouts]\ndefault_minutes = 1\n"
            "idle_seconds = 5\n"
            "completion_grace_seconds = 0.2\n"
            "completion_tail_cap_seconds = 1\n",
        )
    )
    bin_dir = tmp_path / "bin"
    claude_response = json.dumps(
        {
            "type": "result",
            "session_id": "00000000-0000-0000-0000-000000000002",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )
    stdin_path = tmp_path / "claude.stdin"
    _make_lingering_stub(bin_dir, "claude", claude_response, stdin_path)
    env = {
        "PATH": f"{bin_dir}:{__import__('os').environ['PATH']}",
        "HOME": __import__("os").environ.get("HOME", str(tmp_path)),
    }

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
        timeout=3,
    )

    assert proc.returncode == 0, proc.stderr
    assert stdin_path.read_text() == "finish then linger\n"
    jsonl = woof_project / ".woof" / "epics" / "E8" / "dispatch.jsonl"
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert [event["event"] for event in events] == [
        "subprocess_spawned",
        "subprocess_killed",
        "subprocess_returned",
    ]
    assert events[1]["exit_type"] == "completed_lingering"
    assert events[1]["reason"] == "completed_lingering"
    assert events[2]["exit_type"] == "completed_lingering"
    assert events[2]["tokens_in"] == 1
    assert events[2]["tokens_out"] == 2

    meta_file = next((woof_project / ".woof" / "epics" / "E8" / "audit").glob("*.meta"))
    meta = json.loads(meta_file.read_text())
    assert meta["exit_type"] == "completed_lingering"
    assert meta["timed_out"] is False
    assert meta["terminal_seen"] is True

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


def test_end_to_end_codex_records_thread_and_audit_path(woof_project: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    codex_stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thr-1"}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"id": "item_1", "type": "command_execution"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "command_execution"},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 50,
                        "cached_input_tokens": 10,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 2,
                    },
                }
            ),
        ]
    )
    stdin_path = tmp_path / "codex.stdin"
    _make_stub(bin_dir, "codex", codex_stream, stdin_path=stdin_path)
    env = {
        "PATH": f"{bin_dir}:{__import__('os').environ['PATH']}",
        "HOME": __import__("os").environ.get("HOME", str(tmp_path)),
    }

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

    jsonl = woof_project / ".woof" / "epics" / "E9" / "dispatch.jsonl"
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = events[1]
    assert returned["effort"] == "xhigh"
    assert returned["argv"][-1] == "<prompt:stdin>"
    assert returned["argv"][0] == "codex"
    assert returned["prompt_transport"] == "stdin"
    assert "runtime_policy" not in returned
    assert returned["exit_type"] == "clean"
    assert returned["tokens_in"] == 50
    assert returned["tokens_out"] == 7  # 5 + 2 reasoning
    assert returned["cache_read_tokens"] == 10
    assert returned["prompt_bytes"] == len(b"critique me\n")
    assert returned["artefact_bytes"] == 0
    assert returned["output_bytes"] == len(codex_stream.encode()) + 1
    assert returned["stderr_bytes"] == 0
    assert returned["command_count"] == 1
    assert returned["codex_audit_path"].startswith(".woof/epics/E9/audit/codex-primary-")

    meta_file = next((woof_project / ".woof" / "epics" / "E9" / "audit").glob("*.meta"))
    meta = json.loads(meta_file.read_text())
    assert meta["command_count"] == 1
    assert meta["exit_type"] == "clean"
    assert meta["prompt_bytes"] == len(b"critique me\n")

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


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
    env = {
        "PATH": f"{bin_dir}:{__import__('os').environ['PATH']}",
        "HOME": __import__("os").environ.get("HOME", str(tmp_path)),
    }

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

    jsonl = woof_project / ".woof" / "epics" / "E11" / "dispatch.jsonl"
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert events[0]["event"] == "subprocess_spawned"
    assert events[0]["route_key"] == "execution"
    assert events[1]["event"] == "subprocess_returned"
    assert events[1]["route_key"] == "execution"

    meta_file = next((woof_project / ".woof" / "epics" / "E11" / "audit").glob("*.meta"))
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


def test_agents_schema_cache_hit_on_second_dispatch(woof_project: Path, tmp_path: Path) -> None:
    """Second dispatch with unchanged agents.toml records agents_schema_cache_hit=True."""
    bin_dir = tmp_path / "bin"
    _make_claude_stub_simple(bin_dir)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }

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
        jsonl = woof_project / ".woof" / "epics" / f"E{epic}" / "dispatch.jsonl"
        events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
        return next(e for e in events if e["event"] == "subprocess_spawned")

    assert _spawned_event(30)["agents_schema_cache_hit"] is False
    assert _spawned_event(31)["agents_schema_cache_hit"] is True


def test_agents_schema_cache_miss_after_content_change(woof_project: Path, tmp_path: Path) -> None:
    """Changing agents.toml invalidates the cache; next dispatch re-validates."""
    bin_dir = tmp_path / "bin"
    _make_claude_stub_simple(bin_dir)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }

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

    agents_path = woof_project / ".woof" / "agents.toml"
    original = agents_path.read_text()
    agents_path.write_text(
        original.replace('model = "claude-opus-4-7"', 'model = "claude-sonnet-4-6"')
    )

    proc2 = _dispatch(33)
    assert proc2.returncode == 0, proc2.stderr

    def _spawned_event(epic: int) -> dict:
        jsonl = woof_project / ".woof" / "epics" / f"E{epic}" / "dispatch.jsonl"
        events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
        return next(e for e in events if e["event"] == "subprocess_spawned")

    assert _spawned_event(32)["agents_schema_cache_hit"] is False
    assert _spawned_event(33)["agents_schema_cache_hit"] is False


def test_agents_schema_cache_key_includes_schema() -> None:
    """A schema change invalidates a pass recorded under the old schema."""
    from woof.cli.dispatcher import _agents_schema_cache_key

    agents = b'[roles.primary]\nadapter = "claude"\n'
    key_v1 = _agents_schema_cache_key(agents, b'{"schema": "v1"}')
    # Same config, different schema -> different key -> re-validation, not a stale pass.
    assert key_v1 != _agents_schema_cache_key(agents, b'{"schema": "v2"}')
    # Stable for identical inputs (a genuine cache hit).
    assert key_v1 == _agents_schema_cache_key(agents, b'{"schema": "v1"}')
    # Different config, same schema -> different key.
    assert key_v1 != _agents_schema_cache_key(b"[roles]\n", b'{"schema": "v1"}')


def test_runtime_policy_in_spawned_not_in_returned(woof_project: Path, tmp_path: Path) -> None:
    """runtime_policy is emitted once per dispatch: in subprocess_spawned, not subprocess_returned."""
    bin_dir = tmp_path / "bin"
    _make_claude_stub_simple(bin_dir)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }

    proc = subprocess.run(
        [str(WOOF_BIN), "dispatch", "--role", "reviewer", "--epic", "34"],
        capture_output=True,
        text=True,
        input="do work\n",
        cwd=woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = woof_project / ".woof" / "epics" / "E34" / "dispatch.jsonl"
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    spawned = next(e for e in events if e["event"] == "subprocess_spawned")
    returned = next(e for e in events if e["event"] == "subprocess_returned")

    assert spawned["runtime_policy"] == EXPECTED_TRUSTED_RUNTIME_POLICY
    assert "runtime_policy" not in returned
