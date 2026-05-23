"""Black-box tests for ``woof preflight``."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

STANDARD_AGENTS = """\
[roles.primary]
adapter = "codex"
model = "gpt-5.5"
effort = "xhigh"

[roles.reviewer]
adapter = "claude"
model = "claude-opus-4-7"
effort = "max"
mcp = []
"""


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env sh\n" + body)
    path.chmod(0o755)


def _write_project(
    root: Path,
    *,
    prerequisites: str,
    agents: str | None = STANDARD_AGENTS,
    quality_gates: str | None = None,
) -> None:
    woof_dir = root / ".woof"
    woof_dir.mkdir()
    (woof_dir / "prerequisites.toml").write_text(prerequisites)
    if agents is not None:
        (woof_dir / "agents.toml").write_text(agents)
    if quality_gates is not None:
        (woof_dir / "quality-gates.toml").write_text(quality_gates)


def _env_with_path(bin_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    uv = shutil.which("uv")
    sh = shutil.which("sh")
    assert uv is not None
    assert sh is not None
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        [
            str(bin_dir),
            str(Path(uv).parent),
            str(Path(sh).parent),
        ]
    )
    env.setdefault("ANTHROPIC_API_KEY", "stub-anthropic")
    env.setdefault("OPENAI_API_KEY", "stub-openai")
    if extra:
        env.update(extra)
    return env


def _stub_core_tools(bin_dir: Path) -> None:
    _write_exe(
        bin_dir / "ajv",
        """\
if [ "$1" = "validate" ]; then
  exit 0
fi
echo "ajv 8.0.0"
""",
    )
    _write_exe(bin_dir / "just", 'echo "just 1.2.3"\n')
    _write_exe(bin_dir / "git", 'echo "git version 2.44.0"\n')
    _write_exe(
        bin_dir / "gh",
        """\
if [ "$1" = "api" ]; then
  echo '{"ok":true}'
  exit 0
fi
echo "unexpected gh $*" >&2
exit 2
""",
    )
    _write_exe(bin_dir / "claude", 'echo "claude stub"\n')
    _write_exe(bin_dir / "codex", 'echo "codex stub"\n')


def _write_current_epic_state(root: Path) -> None:
    epic_dir = root / ".woof" / "epics" / "E5"
    audit_dir = epic_dir / "audit"
    audit_dir.mkdir(parents=True)
    (root / ".woof" / ".current-epic").write_text("E5\n")
    (epic_dir / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": 5,
                "goal": "Expose operator state.",
                "stories": [
                    {
                        "id": "S1",
                        "title": "Report current state",
                        "intent": "Make the current graph state visible.",
                        "paths": ["src/woof/**/*.py"],
                        "satisfies": ["O1"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "depends_on": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "status": "in_progress",
                    }
                ],
            }
        )
        + "\n"
    )
    (epic_dir / "gate.md").write_text(
        """---
type: story_gate
stage: 6
story_id: S1
triggered_by:
  - check_1_quality_gates
timestamp: '2026-05-23T10:02:00Z'
---

## Context

Quality failed.
"""
    )
    (epic_dir / "epic.jsonl").write_text(
        json.dumps(
            {
                "event": "story_gate_opened",
                "at": "2026-05-23T10:02:00Z",
                "epic_id": 5,
                "story_id": "S1",
                "gate_type": "story_gate",
                "triggered_by": ["check_1_quality_gates"],
            }
        )
        + "\n"
    )
    (epic_dir / "dispatch.jsonl").write_text(
        json.dumps(
            {
                "event": "subprocess_returned",
                "at": "2026-05-23T10:01:00Z",
                "epic_id": 5,
                "story_id": "S1",
                "role": "primary",
                "adapter": "codex",
                "model": "gpt-5.5",
                "effort": "xhigh",
                "exit_code": 0,
                "codex_audit_path": ".woof/epics/E5/audit/codex-primary-run",
            }
        )
        + "\n"
    )
    (epic_dir / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 5,
                "story_id": "S1",
                "triggered_by": ["check_1_quality_gates"],
                "checks": [
                    {
                        "id": "check_1_quality_gates",
                        "ok": False,
                        "severity": "blocker",
                        "summary": "quality gate failed",
                        "evidence": "just test exited 1",
                        "paths": [],
                        "command": "just test",
                        "exit_code": 1,
                    }
                ],
            }
        )
        + "\n"
    )


def test_preflight_passes_with_mocked_prerequisites(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "pyright", 'echo "pyright 1.1.1"\n')
    _write_exe(
        bin_dir / "tree-sitter",
        """\
if [ "$1" = "--version" ]; then
  echo "tree-sitter 0.23.0"
  exit 0
fi
if [ "$1" = "parse" ]; then
  echo "(module)"
  exit 0
fi
echo "unexpected tree-sitter $*" >&2
exit 2
""",
    )

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "1.0+"
git = "2.30+"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[indexing.tree-sitter]
cli = "0.22+"
grammars = ["python"]

[lsp]
languages = ["python"]
""",
        quality_gates="""\
[gates.test]
command = "just test"
timeout_seconds = 30
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert {finding["id"] for finding in payload["findings"]} >= {
        "woof.install",
        "config.prerequisites",
        "config.agents",
        "agents.primary.route",
        "agents.reviewer.route",
        "agents.reviewer.mcp_config",
        "github.repo",
        "lsp.python.binary",
        "tree-sitter.python",
        "quality-gates.test",
    }
    primary_route = next(
        finding for finding in payload["findings"] if finding["id"] == "agents.primary.route"
    )
    assert "runtime=trusted-local" in primary_route["detail"]
    assert primary_route["required"] == (
        "explicit adapter, model, effort, and runtime-mode disclosure"
    )
    assert primary_route["notes"] == [
        "trusted-local runtime: Woof does not constrain dispatched agents at runtime; "
        "commit safety is enforced through deterministic checks, reviewer critique, "
        "human gates, transaction manifests, and commit decisions"
    ]


def test_preflight_validates_named_mcp_route(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "npx", 'echo "npx 10.0.0"\n')

    agents = (
        STANDARD_AGENTS.replace("mcp = []\n", 'mcp = ["chrome-devtools"]\n')
        + """
[mcp_servers.chrome-devtools]
command = "npx"
args = ["-y", "chrome-devtools-mcp@latest"]
"""
    )
    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
        agents=agents,
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert {
        "agents.reviewer.route",
        "agents.reviewer.mcp_config",
        "agents.reviewer.mcp.chrome-devtools",
    } <= {finding["id"] for finding in payload["findings"]}
    mcp = next(
        finding for finding in payload["findings"] if finding["id"] == "agents.reviewer.mcp_config"
    )
    assert '"chrome-devtools"' in mcp["detail"]


def test_preflight_fails_for_incomplete_role_route(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
        agents="""\
[roles.primary]
adapter = "codex"
model = "gpt-5.5"

[roles.reviewer]
adapter = "claude"
model = "claude-opus-4-7"
effort = "max"
mcp = []
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    primary = next(
        finding for finding in payload["findings"] if finding["id"] == "agents.primary.route"
    )
    assert primary["ok"] is False
    assert "effort is not declared" in primary["detail"]


def test_preflight_requires_agents_toml(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
        agents=None,
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    agents = next(finding for finding in payload["findings"] if finding["id"] == "agents.config")
    assert agents["ok"] is False
    assert "agents.toml" in agents["detail"]


def test_preflight_runs_host_and_server_checks(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "host-ready", "exit 0\n")
    _write_exe(bin_dir / "server-ready", "exit 0\n")
    if sys.platform.startswith("linux"):
        platform = "linux"
    elif sys.platform == "darwin":
        platform = "darwin"
    else:
        platform = "windows"

    _write_project(
        tmp_path,
        prerequisites=f"""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[host]
platforms = ["{platform}"]

[host.checks.project]
command = "host-ready"
required = "project host tooling ready"

[servers.dev]
command = "server-ready"
required = "local dev server ready"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert {
        "host.platform",
        "host.project",
        "servers.dev",
    } <= {finding["id"] for finding in payload["findings"]}


def test_preflight_reports_missing_prerequisites_template(tmp_path: Path, run_woof) -> None:
    (tmp_path / ".woof").mkdir()

    proc = run_woof("preflight", "--project-root", str(tmp_path), env=os.environ.copy())

    assert proc.returncode == 1
    assert "prerequisites.toml" in proc.stdout
    assert 'repo = "<replace>/<replace>"' in proc.stdout


def test_preflight_fails_for_missing_declared_command(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    (bin_dir / "codex").unlink()

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    codex = next(finding for finding in payload["findings"] if finding["id"] == "commands.codex")
    assert codex["ok"] is False
    assert "codex not found" in codex["detail"]


def test_preflight_checks_declared_lsp_plugin(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "pyright", 'echo "pyright 1.1.1"\n')
    _write_exe(bin_dir / "claude", 'echo "pyright-lsp@claude-plugins-official"\n')

    tool_root = tmp_path / "tool"
    (tool_root / "languages").mkdir(parents=True)
    (tool_root / "schemas").symlink_to(REPO_ROOT / "schemas")
    (tool_root / "languages" / "python.toml").write_text(
        """\
[lsp]
binary = "pyright"
binary_install = "npm install -g pyright"
plugin = "pyright-lsp@claude-plugins-official"
plugin_install = "claude plugin install pyright-lsp@claude-plugins-official"

[tree-sitter]
grammar_install = "npm install -g tree-sitter-python"
verify_snippet = "def f(): pass"
verify_scope = "source.python"
"""
    )
    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[lsp]
languages = ["python"]
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir, {"WOOF_TOOL_ROOT": str(tool_root)}),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    plugin = next(
        finding for finding in payload["findings"] if finding["id"] == "lsp.python.plugin"
    )
    assert plugin["ok"] is True


def test_preflight_reuses_floor_cache_until_forced(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "pyright", 'echo "pyright 1.1.1"\n')

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[lsp]
languages = ["python"]
""",
    )

    first = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert first.returncode == 0, first.stderr + first.stdout
    assert (tmp_path / ".woof" / ".preflight-floor").is_file()
    assert (tmp_path / ".woof" / ".preflight-runtime").is_file()

    (bin_dir / "pyright").unlink()
    cached = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert cached.returncode == 0, cached.stderr + cached.stdout
    cached_payload = json.loads(cached.stdout)
    lsp = next(
        finding for finding in cached_payload["findings"] if finding["id"] == "lsp.python.binary"
    )
    assert lsp["ok"] is True

    forced = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        "--force",
        env=_env_with_path(bin_dir),
    )

    assert forced.returncode == 1
    forced_payload = json.loads(forced.stdout)
    forced_lsp = next(
        finding for finding in forced_payload["findings"] if finding["id"] == "lsp.python.binary"
    )
    assert forced_lsp["ok"] is False
    assert "pyright not found" in forced_lsp["detail"]

    after_failed_force = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert after_failed_force.returncode == 1


def test_preflight_rechecks_stale_runtime_cache(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    first = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert first.returncode == 0, first.stderr + first.stdout
    runtime_cache = tmp_path / ".woof" / ".preflight-runtime"
    runtime_payload = json.loads(runtime_cache.read_text())
    runtime_payload["verified_at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    runtime_cache.write_text(json.dumps(runtime_payload))
    _write_exe(
        bin_dir / "gh",
        """\
echo "expired gh auth" >&2
exit 42
""",
    )

    stale = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert stale.returncode == 1
    stale_payload = json.loads(stale.stdout)
    rate_limit = next(
        finding for finding in stale_payload["findings"] if finding["id"] == "github.rate_limit"
    )
    assert rate_limit["ok"] is False
    assert "expired gh auth" in rate_limit["detail"]


def test_preflight_passes_adapter_auth_when_env_keys_set(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    primary = next(f for f in payload["findings"] if f["id"] == "agents.primary.auth")
    reviewer = next(f for f in payload["findings"] if f["id"] == "agents.reviewer.auth")
    assert primary["ok"] is True
    assert "ANTHROPIC_API_KEY" not in primary["detail"]
    assert "OPENAI_API_KEY" in primary["detail"]
    assert reviewer["ok"] is True
    assert "ANTHROPIC_API_KEY" in reviewer["detail"]


def test_preflight_passes_adapter_auth_when_credential_files_present(
    tmp_path: Path, run_woof
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    claude_home.mkdir()
    codex_home.mkdir()
    (claude_home / ".credentials.json").write_text("{}\n")
    (codex_home / "auth.json").write_text("{}\n")

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    env = _env_with_path(
        bin_dir,
        {
            "CLAUDE_CONFIG_DIR": str(claude_home),
            "CODEX_HOME": str(codex_home),
        },
    )
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=env,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    primary = next(f for f in payload["findings"] if f["id"] == "agents.primary.auth")
    reviewer = next(f for f in payload["findings"] if f["id"] == "agents.reviewer.auth")
    assert primary["ok"] is True
    assert str(codex_home / "auth.json") in primary["detail"]
    assert reviewer["ok"] is True
    assert str(claude_home / ".credentials.json") in reviewer["detail"]


def test_preflight_fails_when_adapter_auth_marker_missing(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    env = _env_with_path(
        bin_dir,
        {
            "CLAUDE_CONFIG_DIR": str(empty_home),
            "CODEX_HOME": str(empty_home),
        },
    )
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=env,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    primary = next(f for f in payload["findings"] if f["id"] == "agents.primary.auth")
    reviewer = next(f for f in payload["findings"] if f["id"] == "agents.reviewer.auth")
    assert primary["ok"] is False
    assert "codex dispatch will fail" in primary["detail"]
    assert primary["install"] == "codex login"
    assert reviewer["ok"] is False
    assert "claude dispatch will fail" in reviewer["detail"]
    assert reviewer["install"] == "claude /login"


def test_preflight_flags_non_executable_cartography_script(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    cartography = scripts_dir / "refresh-cartography"
    cartography.write_text("#!/usr/bin/env sh\necho cartography\n")
    cartography.chmod(0o644)

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    cart = next(f for f in payload["findings"] if f["id"] == "cartography.script")
    assert cart["ok"] is False
    assert "not executable" in cart["detail"]
    assert cart["install"] == f"chmod +x {cartography}"

    cartography.chmod(0o755)
    forced = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        "--force",
        env=_env_with_path(bin_dir),
    )
    assert forced.returncode == 0, forced.stderr + forced.stdout
    payload = json.loads(forced.stdout)
    cart = next(f for f in payload["findings"] if f["id"] == "cartography.script")
    assert cart["ok"] is True


def test_preflight_omits_cartography_finding_when_script_absent(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert all(f["id"] != "cartography.script" for f in payload["findings"])


def test_preflight_json_reports_operator_state_for_current_epic(
    tmp_path: Path,
    run_woof,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "local"
""",
    )
    _write_current_epic_state(tmp_path)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    state = payload["operator_state"]
    assert state["current_epic"]["epic_id"] == 5
    assert state["runtime_policy"]["mode"] == "trusted-local"
    assert state["dispatch_routes"]["roles"]["primary"]["adapter"] == "codex"
    assert state["epic"]["next"] == {
        "node": "human_review",
        "story_id": None,
        "reason": "gate_open",
    }
    assert state["epic"]["next_action"]["command"] == "woof wf --epic 5 --resolve <decision>"
    assert state["epic"]["gate"]["cause"] == "check_1_quality_gates"
    assert state["epic"]["checks"]["failed_checks"][0]["summary"] == "quality gate failed"
    assert state["epic"]["audit_pointers"]["latest_codex_audit_path"] == (
        ".woof/epics/E5/audit/codex-primary-run"
    )


def test_preflight_text_reports_operator_state_for_current_epic(
    tmp_path: Path,
    run_woof,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "local"
""",
    )
    _write_current_epic_state(tmp_path)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Operator state:" in proc.stdout
    assert "current_epic: E5 selected=true valid=true epic_dir_exists=true" in proc.stdout
    assert "runtime_policy: trusted-local" in proc.stdout
    assert "primary: adapter=codex model=gpt-5.5 effort=xhigh" in proc.stdout
    assert "next_action: resolve_gate command=woof wf --epic 5 --resolve <decision>" in proc.stdout
    assert "gate: open type=story_gate story=S1 cause=check_1_quality_gates" in proc.stdout
    assert "checks: FAIL total=1 failed=1 triggered_by=check_1_quality_gates" in proc.stdout
    assert "audit_pointers: epic_jsonl=.woof/epics/E5/epic.jsonl" in proc.stdout
