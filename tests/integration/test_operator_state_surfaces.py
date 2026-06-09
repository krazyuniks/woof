"""CLI integration coverage for operator state surfaces."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"

pytestmark = pytest.mark.host_only


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env sh\n" + body)
    path.chmod(0o755)


def _env_with_path(bin_dir: Path) -> dict[str, str]:
    uv = shutil.which("uv")
    sh = shutil.which("sh")
    assert uv is not None
    assert sh is not None
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(bin_dir), str(Path(uv).parent), str(Path(sh).parent)])
    env["ANTHROPIC_API_KEY"] = "stub-anthropic"
    env["OPENAI_API_KEY"] = "stub-openai"
    return env


def _stub_tools(bin_dir: Path) -> None:
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
    _write_exe(bin_dir / "claude", 'echo "claude stub"\n')
    _write_exe(bin_dir / "codex", 'echo "codex stub"\n')


def _write_consumer(root: Path) -> None:
    epic_dir = root / ".woof" / "epics" / "E5"
    epic_dir.mkdir(parents=True)
    (root / ".woof" / "prerequisites.toml").write_text(
        """\
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

[cartography]
summary_min_chars = 40
"""
    )
    codebase = root / ".woof" / "codebase"
    codebase.mkdir()
    design_doc = (
        "# Target Architecture\n\n"
        "The consumer keeps operator state on disk, exposes the current gate "
        "and next action, and keeps dispatched work resumable through Woof."
    )
    (codebase / "TARGET-ARCHITECTURE.md").write_text(design_doc)
    (codebase / "PRINCIPLES.md").write_text(design_doc)
    (codebase / "tags").write_text("main\tsrc/main.py\t1\n")
    (codebase / "files.txt").write_text("src/main.py\n")
    (codebase / "freshness.json").write_text(
        json.dumps({"git_ref": "abc", "age_s": 0, "generator_version": 1}) + "\n"
    )
    scripts = root / "scripts"
    scripts.mkdir()
    _write_exe(scripts / "refresh-cartography", "echo refresh\n")
    (root / ".woof" / "agents.toml").write_text(
        """\
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
    )
    (root / ".woof" / ".current-epic").write_text("E5\n")
    (epic_dir / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": 5,
                "goal": "Expose operator state.",
                "stories": [
                    {
                        "id": "S1",
                        "title": "Report state",
                        "intent": "Expose current state to operators.",
                        "paths": ["src/**/*.py"],
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
                    }
                ],
            }
        )
        + "\n"
    )


def test_observe_json_and_preflight_text_expose_resume_state(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_tools(bin_dir)
    _write_consumer(tmp_path)
    env = _env_with_path(bin_dir)

    observe = subprocess.run(
        [str(WOOF_BIN), "observe", "--epic", "5", "--view", "status", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )

    assert observe.returncode == 0, observe.stderr + observe.stdout
    status = json.loads(observe.stdout)
    assert status["current_epic"]["value"] == "E5"
    assert status["next_action"]["command"] == "woof wf --epic 5 --resolve <decision>"
    assert status["gate"]["cause"] == "check_1_quality_gates"
    assert status["dispatch_routes"]["roles"]["primary"]["adapter"] == "codex"
    assert status["runtime_policy"]["mode"] == "trusted-local"
    assert status["checks"]["failed_checks"][0]["summary"] == "quality gate failed"
    assert status["audit_pointers"]["latest_codex_audit_path"] == (
        ".woof/epics/E5/audit/codex-primary-run"
    )

    preflight = subprocess.run(
        [str(WOOF_BIN), "preflight", "--project-root", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
    )

    assert preflight.returncode == 0, preflight.stderr + preflight.stdout
    assert "Operator state:" in preflight.stdout
    assert "current_epic: E5 selected=true valid=true epic_dir_exists=true" in preflight.stdout
    assert "next_action: resolve_gate command=woof wf --epic 5 --resolve <decision>" in (
        preflight.stdout
    )
    assert "gate: open type=story_gate story=S1 cause=check_1_quality_gates" in preflight.stdout
    assert "checks: FAIL total=1 failed=1 triggered_by=check_1_quality_gates" in preflight.stdout
