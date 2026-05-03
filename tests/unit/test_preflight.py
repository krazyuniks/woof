"""Black-box tests for ``woof preflight``."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env sh\n" + body)
    path.chmod(0o755)


def _write_project(
    root: Path,
    *,
    prerequisites: str,
    quality_gates: str | None = None,
) -> None:
    woof_dir = root / ".woof"
    woof_dir.mkdir()
    (woof_dir / "prerequisites.toml").write_text(prerequisites)
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
    _write_exe(bin_dir / "cld", 'echo "cld stub"\n')
    _write_exe(bin_dir / "cod", 'echo "cod stub"\n')
    _write_exe(bin_dir / "agent-sync", 'echo "agent-sync stub"\n')


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

[wrappers]
cld = "any"
cod = "any"
agent-sync = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[github]
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
        "config.prerequisites",
        "github.repo",
        "lsp.python.binary",
        "tree-sitter.python",
        "quality-gates.test",
    }


def test_preflight_reports_missing_prerequisites_template(tmp_path: Path, run_woof) -> None:
    (tmp_path / ".woof").mkdir()

    proc = run_woof("preflight", "--project-root", str(tmp_path), env=os.environ.copy())

    assert proc.returncode == 1
    assert "prerequisites.toml" in proc.stdout
    assert 'repo = "<owner>/<repo>"' in proc.stdout


def test_preflight_fails_for_missing_declared_wrapper(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    (bin_dir / "cod").unlink()

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[wrappers]
cld = "any"
cod = "any"
agent-sync = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[github]
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
    cod = next(finding for finding in payload["findings"] if finding["id"] == "wrappers.cod")
    assert cod["ok"] is False
    assert "cod not found" in cod["detail"]


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

[wrappers]
cld = "any"
cod = "any"
agent-sync = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[github]
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

[wrappers]
cld = "any"
cod = "any"
agent-sync = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[github]
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

[wrappers]
cld = "any"
cod = "any"
agent-sync = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[github]
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
