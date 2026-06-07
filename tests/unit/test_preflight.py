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

CARTOGRAPHY_PREREQS = """\
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

[cartography]
summary_min_chars = 40
"""

DESIGN_DOC_BODY = (
    "# Target Architecture\n\n"
    "The estate targets event-driven services behind an API gateway, with "
    "deterministic orchestration and on-disk state as the source of truth.\n"
)


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env sh\n" + body)
    path.chmod(0o755)


def _write_cartography(
    root: Path,
    *,
    target: str | None = DESIGN_DOC_BODY,
    principles: str | None = DESIGN_DOC_BODY,
    mechanical: bool = True,
    script: bool = True,
) -> None:
    """Author a cartography artefact set under ``root`` for a declared contract."""

    codebase = root / ".woof" / "codebase"
    codebase.mkdir(parents=True, exist_ok=True)
    if target is not None:
        (codebase / "TARGET-ARCHITECTURE.md").write_text(target)
    if principles is not None:
        (codebase / "PRINCIPLES.md").write_text(principles)
    if mechanical:
        (codebase / "tags").write_text("main\tsrc/main.py\t1\n")
        (codebase / "files.txt").write_text("src/main.py\n")
        # ts is now() so the default stamp is reliably fresh under the
        # ts-authoritative reader (age_s mirrors the generator's frozen 0).
        (codebase / "freshness.json").write_text(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "git_ref": "abc",
                    "age_s": 0,
                    "generator_version": 1,
                }
            )
            + "\n"
        )
    if script:
        scripts = root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        _write_exe(scripts / "refresh-cartography", "echo refresh\n")


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


def _with_cartography_contract(prerequisites: str) -> str:
    if "[cartography]" in prerequisites:
        return prerequisites
    return prerequisites.rstrip() + "\n\n[cartography]\nsummary_min_chars = 40\n"


def _write_ready_project(
    root: Path,
    *,
    prerequisites: str,
    agents: str | None = STANDARD_AGENTS,
    quality_gates: str | None = None,
) -> None:
    _write_project(
        root,
        prerequisites=_with_cartography_contract(prerequisites),
        agents=agents,
        quality_gates=quality_gates,
    )
    _write_cartography(root)


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

    _write_ready_project(
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
    _write_ready_project(
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

    _write_ready_project(
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

    _write_ready_project(
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

    _write_ready_project(
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

    _write_ready_project(
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
    _write_ready_project(
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

    _write_ready_project(
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

    _write_ready_project(
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

    _write_ready_project(
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

    _write_ready_project(
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

    _write_ready_project(
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
        prerequisites=CARTOGRAPHY_PREREQS,
    )
    _write_cartography(tmp_path, script=False)

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


def test_preflight_fails_with_onboarding_error_when_cartography_block_absent(
    tmp_path: Path, run_woof
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
    contract = next(f for f in payload["findings"] if f["id"] == "cartography.contract")
    assert contract["ok"] is False
    assert "no [cartography] block" in contract["detail"]
    assert "/woof setup" in contract["install"]
    assert "/woof map-codebase" in contract["install"]
    assert "skills/woof/references/setup.md" in contract["install"]
    assert "skills/woof/references/map-codebase.md" in contract["install"]


def _run_cartography_preflight(tmp_path: Path, run_woof):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )
    return proc, json.loads(proc.stdout)


def test_preflight_passes_with_declared_cartography(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    by_id = {f["id"]: f for f in payload["findings"]}
    for fid in (
        "cartography.script",
        "cartography.target_architecture",
        "cartography.principles",
        "cartography.mechanical",
    ):
        assert by_id[fid]["ok"] is True, by_id[fid]


def test_preflight_fails_for_missing_cartography_script_when_declared(
    tmp_path: Path, run_woof
) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, script=False)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    script = next(f for f in payload["findings"] if f["id"] == "cartography.script")
    assert script["ok"] is False
    assert "not found" in script["detail"]
    assert "map-codebase" in script["install"]


def test_preflight_fails_for_missing_cartography_design_doc(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, target=None)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    doc = next(f for f in payload["findings"] if f["id"] == "cartography.target_architecture")
    assert doc["ok"] is False
    assert "TARGET-ARCHITECTURE.md not found" in doc["detail"]
    assert "map-codebase" in doc["install"]


def test_preflight_fails_for_stub_marker_design_doc(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, target="<!-- woof:stub -->\n" + DESIGN_DOC_BODY)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    doc = next(f for f in payload["findings"] if f["id"] == "cartography.target_architecture")
    assert doc["ok"] is False
    assert "stub marker" in doc["detail"]


def test_preflight_fails_for_short_design_doc(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, target="Too short.\n")

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    doc = next(f for f in payload["findings"] if f["id"] == "cartography.target_architecture")
    assert doc["ok"] is False
    assert "is a stub" in doc["detail"]
    assert "40-char floor" in doc["detail"]


def test_preflight_accepts_short_design_doc_marked_complete(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, target="---\nstatus: complete\n---\nTiny but intentional.\n")

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    doc = next(f for f in payload["findings"] if f["id"] == "cartography.target_architecture")
    assert doc["ok"] is True
    assert "marked complete" in doc["detail"]


def test_preflight_fails_for_missing_mechanical_layer(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, mechanical=False)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    mech = next(f for f in payload["findings"] if f["id"] == "cartography.mechanical")
    assert mech["ok"] is False
    assert "missing mechanical file(s)" in mech["detail"]
    assert "files.txt" in mech["detail"]


def _write_freshness(root: Path, payload: dict | str) -> None:
    """Overwrite the mechanical freshness.json with a chosen stamp (or raw text)."""

    path = root / ".woof" / "codebase" / "freshness.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload) + "\n")


def test_preflight_warns_for_stale_cartography_freshness(tmp_path: Path, run_woof) -> None:
    # Models the production failure mode: the post-commit hook freezes age_s at 0
    # on every write, so a stamp only ages once commits stop. ts is authoritative
    # -- a deep-past ts is robustly stale regardless of the host wall-clock, and
    # the frozen age_s = 0 must NOT mask it. Default staleness floor is 168h.
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)
    _write_freshness(
        tmp_path,
        {
            "ts": "2020-01-01T00:00:00Z",
            "git_ref": "abc",
            "age_s": 0,
            "generator_version": 1,
        },
    )

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    # A stale stamp warns but does not fail preflight.
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert payload["ok"] is True
    assert payload["warnings"] == 1
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["ok"] is True
    assert fresh["warn"] is True
    assert "staleness floor" in fresh["detail"]
    # The warning carries the refresh prompt.
    assert any("./scripts/refresh-cartography" in note for note in fresh["notes"])


def test_preflight_does_not_warn_for_fresh_cartography_freshness(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)  # default stamp has ts = now() (fresh)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert payload["warnings"] == 0
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["ok"] is True
    assert fresh.get("warn") is not True
    assert "within the" in fresh["detail"]


def test_preflight_warns_for_stale_freshness_via_age_s_fallback(tmp_path: Path, run_woof) -> None:
    # No ts: age derives from the deterministic age_s fallback. A test injects a
    # precise age this way without coupling to wall-clock.
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)
    _write_freshness(tmp_path, {"git_ref": "abc", "age_s": 169 * 3600, "generator_version": 1})

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["warn"] is True
    assert any("refresh-cartography" in note for note in fresh["notes"])


def test_preflight_does_not_warn_for_fresh_freshness_via_age_s_fallback(
    tmp_path: Path, run_woof
) -> None:
    # No ts: the deterministic age_s fallback also drives the fresh verdict, so
    # the fallback path does not over-warn.
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)
    _write_freshness(tmp_path, {"git_ref": "abc", "age_s": 1, "generator_version": 1})

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert payload["warnings"] == 0
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["ok"] is True
    assert fresh.get("warn") is not True
    assert "within the" in fresh["detail"]


def test_preflight_warns_for_malformed_cartography_freshness(tmp_path: Path, run_woof) -> None:
    # An unparseable stamp is non-blocking: presence, not readability, is the
    # blocking concern (the mechanical check already covers presence).
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)
    _write_freshness(tmp_path, "{ not valid json")

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["ok"] is True
    assert fresh["warn"] is True
    assert "could not be read" in fresh["detail"]
    assert any("refresh-cartography" in note for note in fresh["notes"])


def test_preflight_json_reports_operator_state_for_current_epic(
    tmp_path: Path,
    run_woof,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_ready_project(
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
    _write_ready_project(
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


def test_preflight_flags_secret_in_committed_cartography_doc(tmp_path: Path, run_woof) -> None:
    _write_project(
        tmp_path,
        prerequisites=CARTOGRAPHY_PREREQS,
    )
    _write_cartography(tmp_path)
    leaked = tmp_path / ".woof" / "codebase" / "CONCERNS.md"
    leaked.write_text(
        "# Concerns\n\nThe staging deploy hardcodes aws = AKIA1234567890ABCDEF in the script.\n"
    )

    proc = run_woof("preflight", "--project-root", str(tmp_path), "--format", "json")

    assert proc.returncode != 0
    by_id = {finding["id"]: finding for finding in json.loads(proc.stdout)["findings"]}
    assert "cartography.secrets.CONCERNS" in by_id
    finding = by_id["cartography.secrets.CONCERNS"]
    assert finding["ok"] is False
    assert "aws_access_key" in finding["detail"]
    # The matched value must never be echoed into preflight output.
    assert "AKIA1234567890ABCDEF" not in proc.stdout


def test_preflight_secret_scan_passes_on_clean_cartography(tmp_path: Path, run_woof) -> None:
    _write_project(
        tmp_path,
        prerequisites=CARTOGRAPHY_PREREQS,
    )
    _write_cartography(tmp_path)

    proc = run_woof("preflight", "--project-root", str(tmp_path), "--format", "json")

    by_id = {finding["id"]: finding for finding in json.loads(proc.stdout)["findings"]}
    assert by_id["cartography.secrets"]["ok"] is True
