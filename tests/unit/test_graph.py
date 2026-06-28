from __future__ import annotations

import ast
import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from woof.cli.commands.wf import _resolve_gate
from woof.graph import nodes, transitions
from woof.graph.git import git_env
from woof.graph.lock import LOCK_FILENAME, WorkflowLockError
from woof.graph.manifest import build_story_manifest, verify_staged_manifest
from woof.graph.runner import run_graph
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, WorkUnitSpec
from woof.graph.transitions import (
    StageStateError,
    append_epic_event,
    epic_dir,
    mark_story_status,
    next_node,
    plan_gate_resolved,
    readiness_satisfied,
)
from woof.trackers.base import LifecycleSyncResult, Tracker

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


def _dispatch_result(
    exit_type: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> nodes.DispatchRunResult:
    return nodes.DispatchRunResult(
        process=subprocess.CompletedProcess([], returncode, "", stderr),
        exit_type=exit_type,
    )


def _write_plan(root: Path, epic_id: int = 1) -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True)
    plan = {
        "epic_id": epic_id,
        "goal": "test graph",
        "work_units": [
            {
                "id": "S1",
                "title": "first",
                "summary": "do work",
                "paths": ["src/*.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "deps": [],
                "tests": {"count": 1, "types": ["unit"]},
                "status": "pending",
            }
        ],
    }
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "epic.jsonl").write_text("")
    return directory


def _write_tracker_prerequisites(root: Path) -> None:
    woof_dir = root / ".woof"
    woof_dir.mkdir(exist_ok=True)
    (woof_dir / "prerequisites.toml").write_text(
        '[tracker]\nkind = "github"\nrepo = "acme/widgets"\n'
    )


def test_mark_story_status_raises_for_unknown_story(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 1)

    with pytest.raises(StageStateError, match="story S404 not found"):
        mark_story_status(tmp_path, 1, "S404", "done")

    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    assert plan["work_units"][0]["status"] == "pending"


def test_story_prompt_is_portable_playbook_prompt() -> None:
    prompt = nodes._story_prompt(7, "S3")

    assert "/wf:execute-story" not in prompt
    assert '"node_type": "executor_dispatch"' in prompt
    assert '"epic_id": 7' in prompt
    assert '"story_id": "S3"' in prompt
    assert "Tracer-bullet red-green-refactor discipline" in prompt
    assert "commit_subject" in prompt


def test_executor_dispatch_uses_portable_prompt_with_stub_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _write_plan(tmp_path, 1)
    _write_codebase_docs(tmp_path)
    (tmp_path / ".woof" / ".current-epic").write_text("E1")
    (tmp_path / ".woof" / "agents.toml").write_text(
        """\
[roles.primary]
adapter = "claude"
model = "claude-opus-4-7"
effort = "max"

[timeouts]
default_minutes = 15
"""
    )
    (directory / "EPIC.md").write_text(
        "---\nepic_id: 1\n"
        "observable_outcomes:\n"
        "  - id: O1\n"
        "    statement: Stub outcome\n"
        "    verification: automated\n---\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stdin_capture = tmp_path / "executor.stdin"
    harness_response = json.dumps(
        {
            "verdict": "pass",
            "evidence": "Stub story executed.",
            "usage": {"tokens_in": 1, "tokens_out": 1},
            "session": {"id": "00000000-0000-0000-0000-000000000002"},
        }
    )
    for executable in ("claude", "cld"):
        script = bin_dir / executable
        script.write_text(
            f"""#!/usr/bin/env python3
import pathlib
import re
import sys
import os

payload = {harness_response!r}
stdin_capture = pathlib.Path({str(stdin_capture)!r})
repo_root = pathlib.Path(os.environ["WOOF_REPO_ROOT"])
print("ready > ", flush=True)
buf = ""
for line in sys.stdin:
    buf += line
    prompt = re.search(r"(\\S+/prompt\\.txt)", buf)
    answer = re.search(r"(\\S+/answer\\.txt)", buf)
    done = re.search(r"(\\S+/answer\\.done)", buf)
    if not (prompt and answer and done):
        continue
    original = pathlib.Path(prompt.group(1)).read_text(encoding="utf-8")
    stdin_capture.write_text(original, encoding="utf-8")
    (repo_root / "src").mkdir(exist_ok=True)
    (repo_root / ".woof/epics/E1").mkdir(parents=True, exist_ok=True)
    (repo_root / "src/app.py").write_text('print("O1")\\n', encoding="utf-8")
    (repo_root / ".woof/epics/E1/executor_result.json").write_text(
        '{{\\n'
        '  "epic_id": 1,\\n'
        '  "story_id": "S1",\\n'
        '  "outcome": "staged_for_verification",\\n'
        '  "commit_subject": "feat: E1 S1 - run stub story",\\n'
        '  "commit_body": "Stub story executed.",\\n'
        '  "position": null\\n'
        '}}\\n',
        encoding="utf-8",
    )
    pathlib.Path(answer.group(1)).write_text(payload, encoding="utf-8")
    pathlib.Path(done.group(1)).write_text("DONE", encoding="utf-8")
    break
""",
            encoding="utf-8",
        )
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    output = nodes.executor_dispatch_node(
        NodeInput(
            node_type=NodeType.EXECUTOR_DISPATCH,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.COMPLETED
    prompt = stdin_capture.read_text()
    assert "/wf:execute-story" not in prompt
    assert '"node_type": "executor_dispatch"' in prompt
    assert "commit_subject" in prompt
    result = json.loads((directory / "executor_result.json").read_text())
    assert result["commit_subject"] == "feat: E1 S1 - run stub story"
    events = [json.loads(line) for line in (directory / "dispatch.jsonl").read_text().splitlines()]
    assert events[0]["prompt_transport"] == "tmux_harness_prompt_file"
    assert events[0]["argv"][-1] == "<prompt:tmux-file>"


def test_executor_dispatch_completed_lingering_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_plan(tmp_path, 1)
    _write_codebase_docs(tmp_path)

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> nodes.DispatchRunResult:
        assert repo_root == tmp_path
        assert role == "primary"
        assert epic_id == 1
        assert story_id == "S1"
        assert '"node_type": "executor_dispatch"' in prompt
        assert artefacts_loaded == [
            ".woof/epics/E1/plan.json",
            ".woof/codebase/STRUCTURE.md",
            ".woof/codebase/CONVENTIONS.md",
            ".woof/codebase/TARGET-ARCHITECTURE.md",
            ".woof/codebase/PRINCIPLES.md",
            ".woof/codebase/files.txt",
        ]
        return _dispatch_result("completed_lingering")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.executor_dispatch_node(
        NodeInput(
            node_type=NodeType.EXECUTOR_DISPATCH,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.CRITIQUE_DISPATCH


@pytest.mark.parametrize(
    ("exit_type", "returncode"),
    [
        ("nonzero", 7),
        ("idle_kill", 124),
        ("wallclock_timeout", 124),
        ("operator_cancel", 130),
    ],
)
def test_executor_dispatch_failure_exit_types_open_existing_crash_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_type: str,
    returncode: int,
) -> None:
    _write_plan(tmp_path, 1)
    _write_codebase_docs(tmp_path)

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> nodes.DispatchRunResult:
        return _dispatch_result(exit_type, returncode=returncode, stderr=f"{exit_type} failed")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.executor_dispatch_node(
        NodeInput(
            node_type=NodeType.EXECUTOR_DISPATCH,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["subprocess_crash"]
    assert output.message == f"{exit_type} failed"
    gate_fm = _read_gate_fm(tmp_path / ".woof" / "epics" / "E1" / "gate.md")
    assert gate_fm["triggered_by"] == ["subprocess_crash"]


def _make_gh_rate_limit_stub(bin_dir: Path) -> dict[str, str]:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "gh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "api" && "${2:-}" == "/rate_limit" ]]; then\n'
        '  printf \'%s\' \'{"resources":{"core":{"remaining":5000}}}\'\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "api" && "${2:-}" == /repos/*/issues/* ]]; then\n'
        '  issue="${2##*/}"\n'
        '  printf \'{"number":%s,"title":"Stub issue","body":"","updated_at":"2026-01-01T00:00:00Z"}\' "$issue"\n'
        "  exit 0\n"
        "fi\n"
        'echo "unexpected gh invocation: $*" >&2\n'
        "exit 2\n"
    )
    script.chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", "/tmp"),
    }


def _write_spark(root: Path, epic_id: int = 1) -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spark.md").write_text("Build a useful thing.\n")
    (directory / "epic.jsonl").write_text("")
    return directory


def _write_discovery_synthesis(directory: Path) -> None:
    synthesis = directory / "discovery" / "synthesis"
    synthesis.mkdir(parents=True, exist_ok=True)
    (synthesis / "CONCEPT.md").write_text(
        "# Concept\n\n## Problem Framing\n\nThe current workflow needs a useful thing.\n"
    )
    (synthesis / "PRINCIPLES.md").write_text("# Principles\n\nFilled.\n")
    (synthesis / "ARCHITECTURE.md").write_text("# Architecture\n\nFilled.\n")
    (synthesis / "OPEN_QUESTIONS.md").write_text("# Open Questions\n\nNo open questions.\n")


def _write_discovery_bucket(directory: Path, bucket: str) -> None:
    bucket_dir = directory / "discovery" / bucket
    bucket_dir.mkdir(parents=True, exist_ok=True)
    (bucket_dir / f"{bucket}.md").write_text(f"# {bucket}\n\nFilled discovery {bucket} artefact.\n")


def _write_brainstorm_bundle(directory: Path, status: str = "accepted") -> None:
    bucket_dir = directory / "discovery" / "brainstorm"
    bucket_dir.mkdir(parents=True, exist_ok=True)
    (bucket_dir / "DESIGN.md").write_text(
        f"---\ntitle: Bundle\ntier: feature\nstatus: {status}\n---\n\n# Design\n"
    )


def _write_discovery_synthesis_with_open_question(directory: Path) -> None:
    _write_discovery_synthesis(directory)
    synthesis = directory / "discovery" / "synthesis"
    (synthesis / "OPEN_QUESTIONS.md").write_text(
        "# Open Questions\n\n"
        "## OQ1 - Which rollout path should be used?\n\n"
        "**Decision needed by:** Definition must decide once the user-visible surface is known.\n"
    )


def _run_woof(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def _git(root: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(["git", *args], cwd=root, env=git_env(), **kwargs)


def _init_git_repo(root: Path) -> None:
    _git(root, "init", check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com", check=True)
    _git(root, "config", "user.name", "Test", check=True)
    (root / ".gitignore").write_text(".woof/.current-epic\n")
    _git(root, "add", ".gitignore", check=True, capture_output=True)
    _git(root, "commit", "-m", "chore: test repo setup", check=True, capture_output=True)


def _write_codebase_docs(root: Path) -> None:
    codebase_dir = root / ".woof" / "codebase"
    codebase_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "CURRENT-ARCHITECTURE.md",
        "STACK.md",
        "INTEGRATIONS.md",
        "STRUCTURE.md",
        "CONVENTIONS.md",
        "TESTING.md",
        "CONCERNS.md",
        "TARGET-ARCHITECTURE.md",
        "PRINCIPLES.md",
    ]:
        (codebase_dir / name).write_text(f"# {name}\n\nStub.\n")
    (codebase_dir / "files.txt").write_text("")


def _read_gate_fm(gate_path: Path) -> dict:
    text = gate_path.read_text()
    return yaml.safe_load(text[4 : text.find("\n---\n", 4)])


def _read_yaml_front_matter(path: Path) -> dict:
    text = path.read_text()
    return yaml.safe_load(text[4 : text.find("\n---\n", 4)])


def _assert_node_output_schema(tmp_path: Path, payload: dict) -> None:
    path = tmp_path / "node-output.json"
    path.write_text(json.dumps(payload))
    proc = subprocess.run(
        [str(WOOF_BIN), "validate", "--schema", "node-output", str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _assert_planning_node_input_schema(tmp_path: Path, payload: dict) -> None:
    path = tmp_path / "planning-node-input.json"
    path.write_text(json.dumps(payload))
    proc = subprocess.run(
        [str(WOOF_BIN), "validate", "--schema", "planning-node-input", str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _write_minimal_epic(directory: Path, epic_id: int) -> None:
    directory.joinpath("EPIC.md").write_text(
        f"""---
epic_id: {epic_id}
title: Test epic
observable_outcomes:
  - id: O1
    statement: First outcome.
    verification: automated
contract_decisions: []
acceptance_criteria:
  - O1 verified by `just test`.
---
Test epic intent.
"""
    )


def _write_epic_with_resolved_open_question(directory: Path, epic_id: int) -> None:
    directory.joinpath("EPIC.md").write_text(
        f"""---
epic_id: {epic_id}
title: Test epic
observable_outcomes:
  - id: O1
    statement: First outcome.
    verification: automated
contract_decisions: []
acceptance_criteria:
  - Outcome verified.
resolved_open_questions:
  - id: OQ1
    resolution: Use the smallest release path.
---
Test epic intent.
"""
    )


def _write_stage3_plan(directory: Path, epic_id: int) -> None:
    plan = {
        "epic_id": epic_id,
        "goal": "Implement the test epic.",
        "work_units": [
            {
                "id": "S1",
                "title": "Build the first surface",
                "summary": "Create the first observable surface.",
                "paths": ["src/*.py", "tests/*.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "deps": [],
                "tests": {"count": 1, "types": ["unit"]},
                "status": "pending",
            }
        ],
    }
    (directory / "plan.json").write_text(json.dumps(plan))


def _write_plan_critique(directory: Path, severity: str = "minor") -> None:
    critique_dir = directory / "critique"
    critique_dir.mkdir(exist_ok=True)
    if severity == "info":
        findings = "findings: []\n"
    elif severity == "blocker":
        findings = (
            f"findings:\n  - id: F1\n    severity: {severity}\n"
            "    summary: tighten story scope\n"
            "    evidence: S1 does not implement the required outcome\n"
        )
    else:
        findings = (
            f"findings:\n  - id: F1\n    severity: {severity}\n    summary: tighten story scope\n"
        )
    (critique_dir / "plan.md").write_text(
        "---\n"
        "target: plan\n"
        "target_id: null\n"
        f"severity: {severity}\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "harness: test-reviewer\n"
        f"{findings}"
        "---\n"
        "Plan critique body.\n"
    )


def _write_last_sync(directory: Path, epic_id: int, *, body: str = "<previous>") -> None:
    directory.joinpath(".last-sync").write_text(
        json.dumps(
            {
                "issue_number": epic_id,
                "updated_at": "2026-01-01T00:00:00Z",
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "body": body,
            }
        )
        + "\n"
    )


def _write_disposition(directory: Path, epic_id: int, story_id: str = "S1") -> Path:
    disposition_dir = directory / "dispositions"
    disposition_dir.mkdir(exist_ok=True)
    path = disposition_dir / f"story-{story_id}.md"
    path.write_text(
        f"""---
target: story
target_id: {story_id}
critique_path: .woof/epics/E{epic_id}/critique/story-{story_id}.md
severity: info
timestamp: '2026-01-01T00:00:00Z'
harness: test-primary
dispositions: []
---
No reviewer findings.
"""
    )
    return path


def _make_gh_completion_stub(bin_dir: Path) -> dict[str, str]:
    bin_dir.mkdir(parents=True, exist_ok=True)
    last_body = bin_dir / "_last_body"
    closed = bin_dir / "_closed"
    before = json.dumps(
        {
            "updated_at": "2026-01-01T00:00:00Z",
            "body": "Remote intent.\n\n## Observable Outcomes\n\n- stale\n",
            "state": "open",
        }
    )
    after_edit = json.dumps(
        {"updated_at": "2026-01-02T00:00:00Z", "body": "<post-edit>", "state": "open"}
    )
    after_close = json.dumps(
        {"updated_at": "2026-01-03T00:00:00Z", "body": "<post-close>", "state": "closed"}
    )
    script = bin_dir / "gh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'mode="$1"; shift\n'
        'case "$mode" in\n'
        "  api)\n"
        '    if [[ "${1:-}" == "/rate_limit" ]]; then\n'
        '      printf \'%s\' \'{"resources":{"core":{"remaining":5000}}}\'\n'
        "      exit 0\n"
        "    fi\n"
        f'    if [[ -f "{closed}" ]]; then\n'
        f"      printf '%s' '{after_close}'\n"
        f'    elif [[ -f "{last_body}" ]]; then\n'
        f"      printf '%s' '{after_edit}'\n"
        "    else\n"
        f"      printf '%s' '{before}'\n"
        "    fi\n"
        "    ;;\n"
        "  issue)\n"
        '    sub="$1"; shift\n'
        '    case "$sub" in\n'
        "      edit)\n"
        '        body_file=""\n'
        "        while [[ $# -gt 0 ]]; do\n"
        '          case "$1" in\n'
        '            --body-file) body_file="$2"; shift 2;;\n'
        "            *) shift;;\n"
        "          esac\n"
        "        done\n"
        f'        cp "$body_file" "{last_body}"\n'
        "        ;;\n"
        "      close)\n"
        f'        printf "closed\\n" > "{closed}"\n'
        "        ;;\n"
        "      *) exit 2;;\n"
        "    esac\n"
        "    ;;\n"
        "  *) exit 2;;\n"
        "esac\n"
    )
    script.chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", "/tmp"),
    }


def _write_ready_commit_state(
    root: Path, epic_id: int = 1, commit_subject: str | None = None
) -> Path:
    directory = _write_plan(root, epic_id)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"][0]["status"] = "done"
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "dispatch.jsonl").write_text("{}\n")
    executor_result = {
        "epic_id": epic_id,
        "story_id": "S1",
        "outcome": "staged_for_verification",
        "commit_body": "done",
        "position": None,
    }
    if commit_subject:
        executor_result["commit_subject"] = commit_subject
    (directory / "executor_result.json").write_text(json.dumps(executor_result))
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": True,
                "stage": 5,
                "epic_id": epic_id,
                "story_id": "S1",
                "triggered_by": [],
                "checks": [],
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, epic_id, "S1")
    audit_dir = directory / "audit"
    audit_dir.mkdir()
    (audit_dir / "cod-critiquer-1.prompt").write_text("prompt")
    src = root / "src"
    src.mkdir()
    (src / "app.py").write_text("print('O1')\n")
    return directory


def test_graph_runs_executor_then_critique_then_verification_then_commit(tmp_path: Path) -> None:
    _write_plan(tmp_path, 1)
    seen: list[NodeType] = []

    def executor(inp: NodeInput) -> NodeOutput:
        seen.append(inp.node_type)
        mark_story_status(inp.repo_root, inp.epic_id, inp.story_id or "", "in_progress")
        (epic_dir(inp.repo_root, inp.epic_id) / "executor_result.json").write_text(
            json.dumps(
                {
                    "epic_id": inp.epic_id,
                    "story_id": inp.story_id,
                    "outcome": "staged_for_verification",
                    "commit_body": "done",
                    "position": None,
                }
            )
        )
        return NodeOutput(
            node_type=inp.node_type, status=NodeStatus.COMPLETED, epic_id=1, story_id=inp.story_id
        )

    def critique(inp: NodeInput) -> NodeOutput:
        seen.append(inp.node_type)
        critique_dir = epic_dir(inp.repo_root, inp.epic_id) / "critique"
        critique_dir.mkdir()
        (critique_dir / f"story-{inp.story_id}.md").write_text(
            "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
            "timestamp: '2026-01-01T00:00:00Z'\nharness: test\nfindings: []\n---\n"
        )
        return NodeOutput(
            node_type=inp.node_type, status=NodeStatus.COMPLETED, epic_id=1, story_id=inp.story_id
        )

    def disposition(inp: NodeInput) -> NodeOutput:
        seen.append(inp.node_type)
        _write_disposition(epic_dir(inp.repo_root, inp.epic_id), inp.epic_id, inp.story_id or "")
        return NodeOutput(
            node_type=inp.node_type, status=NodeStatus.COMPLETED, epic_id=1, story_id=inp.story_id
        )

    def verify(inp: NodeInput) -> NodeOutput:
        seen.append(inp.node_type)
        (epic_dir(inp.repo_root, inp.epic_id) / "check-result.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "stage": 5,
                    "epic_id": 1,
                    "story_id": "S1",
                    "triggered_by": [],
                    "checks": [],
                }
            )
        )
        return NodeOutput(
            node_type=inp.node_type, status=NodeStatus.COMPLETED, epic_id=1, story_id=inp.story_id
        )

    def commit(inp: NodeInput) -> NodeOutput:
        seen.append(inp.node_type)
        mark_story_status(inp.repo_root, inp.epic_id, inp.story_id or "", "done")
        directory = epic_dir(inp.repo_root, inp.epic_id)
        (directory / "executor_result.json").unlink(missing_ok=True)
        (directory / "check-result.json").unlink(missing_ok=True)
        return NodeOutput(
            node_type=inp.node_type, status=NodeStatus.COMPLETED, epic_id=1, story_id=inp.story_id
        )

    outputs = run_graph(
        tmp_path,
        1,
        registry={
            NodeType.EXECUTOR_DISPATCH: executor,
            NodeType.CRITIQUE_DISPATCH: critique,
            NodeType.REVIEW_DISPOSITION: disposition,
            NodeType.VERIFICATION: verify,
            NodeType.COMMIT: commit,
        },
    )

    assert seen == [
        NodeType.EXECUTOR_DISPATCH,
        NodeType.CRITIQUE_DISPATCH,
        NodeType.REVIEW_DISPOSITION,
        NodeType.VERIFICATION,
        NodeType.COMMIT,
    ]
    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE


def test_run_graph_refuses_live_workflow_lock(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 21)
    lock = directory / LOCK_FILENAME
    lock.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "created_at": "2026-01-01T00:00:00Z",
                "token": "held-by-test",
                "command": ["woof", "wf", "--epic", "21"],
            }
        )
        + "\n"
    )

    with pytest.raises(WorkflowLockError) as exc:
        run_graph(tmp_path, 21, once=True)

    assert "E21 is already locked" in str(exc.value)
    assert lock.exists()


def test_run_graph_removes_stale_workflow_lock_and_records_event(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 22)
    dead_process = subprocess.Popen(["true"])
    dead_process.wait(timeout=5)
    lock = directory / LOCK_FILENAME
    lock.write_text(
        json.dumps(
            {
                "pid": dead_process.pid,
                "hostname": socket.gethostname(),
                "created_at": "2026-01-01T00:00:00Z",
                "token": "stale-test-lock",
                "command": ["woof", "wf", "--epic", "22"],
            }
        )
        + "\n"
    )

    def halt(inp: NodeInput) -> NodeOutput:
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.HALTED,
            epic_id=inp.epic_id,
            story_id=inp.story_id,
        )

    outputs = run_graph(
        tmp_path,
        22,
        once=True,
        registry={NodeType.EXECUTOR_DISPATCH: halt},
    )

    assert outputs[0].status == NodeStatus.HALTED
    assert not lock.exists()
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[0]["event"] == "wf_lock_stale_removed"
    assert events[0]["epic_id"] == 22
    assert events[0]["pid"] == dead_process.pid
    assert events[0]["reason"] == "pid_not_running"
    assert events[0]["paths"] == [".woof/epics/E22/.wf.lock"]


def test_wf_reports_live_workflow_lock(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_plan(tmp_path, 23)
    _write_last_sync(directory, 23)
    lock = directory / LOCK_FILENAME
    lock.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "created_at": "2026-01-01T00:00:00Z",
                "token": "held-by-parent-test-process",
                "command": ["woof", "wf", "--epic", "23"],
            }
        )
        + "\n"
    )
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "23", "--once", env=env)

    assert proc.returncode == 2
    assert "woof wf: workflow lock active:" in proc.stderr
    assert "E23 is already locked" in proc.stderr
    assert lock.exists()


def test_dispatch_helper_uses_role_route_without_provider_target(
    tmp_path: Path, monkeypatch
) -> None:
    import sys

    captured: dict[str, Any] = {}

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = env
        captured["capture_output"] = capture_output
        captured["text"] = text
        dispatch_jsonl = cwd / ".woof" / "epics" / "E1" / "dispatch.jsonl"
        dispatch_jsonl.parent.mkdir(parents=True, exist_ok=True)
        dispatch_jsonl.write_text(
            json.dumps(
                {
                    "event": "subprocess_returned",
                    "epic_id": 1,
                    "role": "primary",
                    "story_id": "S1",
                    "pid": 1234,
                    "exit_type": "completed_lingering",
                    "exit_code": 0,
                }
            )
            + "\n"
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(nodes.subprocess, "run", fake_run)

    result = nodes._run_dispatch(
        tmp_path,
        role="primary",
        epic_id=1,
        story_id="S1",
        prompt="do work",
        artefacts_loaded=[".woof/epics/E1/plan.json"],
    )

    args = captured["args"]
    assert args[0] == sys.executable
    assert args[1:3] == ["-m", "woof"]
    assert args[3:6] == ["dispatch", "--role", "primary"]
    assert "claude" not in args[:6]
    assert "codex" not in args[:6]
    assert args[-2:] == ["--artefact", ".woof/epics/E1/plan.json"]
    assert captured["cwd"] == tmp_path
    env = captured["env"]
    assert env is not None
    assert "PYTHONPATH" in env
    assert result.exit_type == "completed_lingering"
    assert result.process.returncode == 0


def test_run_dispatch_appends_route_key_to_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_dispatch appends --route-key <group> to the woof dispatch argv when set."""
    captured: dict[str, Any] = {}

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        dispatch_jsonl = cwd / ".woof" / "epics" / "E1" / "dispatch.jsonl"
        dispatch_jsonl.parent.mkdir(parents=True, exist_ok=True)
        dispatch_jsonl.write_text(
            json.dumps(
                {
                    "event": "subprocess_returned",
                    "epic_id": 1,
                    "role": "primary",
                    "story_id": "S1",
                    "pid": 1234,
                    "exit_type": "completed_lingering",
                    "exit_code": 0,
                }
            )
            + "\n"
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(nodes.subprocess, "run", fake_run)

    nodes._run_dispatch(
        tmp_path,
        role="primary",
        epic_id=1,
        story_id="S1",
        prompt="do work",
        route_key="execution",
    )

    args = captured["args"]
    assert "--route-key" in args
    assert args[args.index("--route-key") + 1] == "execution"


def test_run_dispatch_omits_route_key_from_argv_when_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_dispatch does not include --route-key in argv when route_key is None."""
    captured: dict[str, Any] = {}

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        dispatch_jsonl = cwd / ".woof" / "epics" / "E1" / "dispatch.jsonl"
        dispatch_jsonl.parent.mkdir(parents=True, exist_ok=True)
        dispatch_jsonl.write_text(
            json.dumps(
                {
                    "event": "subprocess_returned",
                    "epic_id": 1,
                    "role": "primary",
                    "story_id": "S1",
                    "pid": 1234,
                    "exit_type": "completed_lingering",
                    "exit_code": 0,
                }
            )
            + "\n"
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(nodes.subprocess, "run", fake_run)

    nodes._run_dispatch(
        tmp_path,
        role="primary",
        epic_id=1,
        story_id="S1",
        prompt="do work",
    )

    assert "--route-key" not in captured["args"]


def test_dispatch_call_sites_pass_correct_route_key() -> None:
    """Each dispatch call site in nodes.py passes the correct node group as route_key."""
    import ast as _ast

    source = Path(nodes.__file__).read_text(encoding="utf-8")
    module = _ast.parse(source)

    expected = {
        "_discovery_bucket_node": "discovery",
        "discovery_synthesis_node": "discovery",
        "epic_definition_node": "definition",
        "breakdown_planning_node": "planning",
        "plan_critique_node": "planning",
        "executor_dispatch_node": "execution",
        "critique_dispatch_node": "execution",
    }

    functions = {item.name: item for item in module.body if isinstance(item, _ast.FunctionDef)}

    for func_name, expected_group in expected.items():
        func = functions[func_name]
        route_keys = []
        for node in _ast.walk(func):
            if (
                isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Name)
                and node.func.id == "_run_dispatch"
            ):
                for kw in node.keywords:
                    if kw.arg == "route_key":
                        route_keys.append(_ast.literal_eval(kw.value))
        assert route_keys, f"{func_name}: missing route_key in _run_dispatch call"
        assert all(k == expected_group for k in route_keys), (
            f"{func_name}: expected route_key={expected_group!r}, got {route_keys}"
        )


def test_dispatch_consumers_route_results_through_shared_classifier() -> None:
    source = Path(nodes.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)
    expected_consumers = {
        "_discovery_bucket_node",
        "discovery_synthesis_node",
        "epic_definition_node",
        "breakdown_planning_node",
        "plan_critique_node",
        "executor_dispatch_node",
        "critique_dispatch_node",
    }
    functions = {item.name: item for item in module.body if isinstance(item, ast.FunctionDef)}
    dispatch_consumers = {
        name
        for name, function in functions.items()
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_dispatch"
            for node in ast.walk(function)
        )
    }

    assert dispatch_consumers == expected_consumers
    for name in expected_consumers:
        function = functions[name]
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_classify_dispatch_result"
            for node in ast.walk(function)
        ), name
        assert not [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Attribute) and node.attr == "returncode"
        ], name


def test_pre_plan_transition_enters_discovery_when_spark_exists(tmp_path: Path) -> None:
    _write_spark(tmp_path, 21)

    assert next_node(tmp_path, 21) == (NodeType.DISCOVERY_RESEARCH, None)


def test_pre_plan_transition_walks_discovery_buckets_before_synthesis(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 210)

    assert next_node(tmp_path, 210) == (NodeType.DISCOVERY_RESEARCH, None)
    _write_discovery_bucket(directory, "research")
    assert next_node(tmp_path, 210) == (NodeType.DISCOVERY_THINKING, None)
    _write_discovery_bucket(directory, "thinking")
    assert next_node(tmp_path, 210) == (NodeType.DISCOVERY_IDEATE, None)
    _write_discovery_bucket(directory, "ideate")
    assert next_node(tmp_path, 210) == (NodeType.DISCOVERY_SYNTHESIS, None)


def test_pre_plan_transition_skips_headless_chain_when_accepted_bundle_present(
    tmp_path: Path,
) -> None:
    # An accepted interactive brainstorm bundle (the woof-brainstorm skill) stands in
    # for the headless research/thinking/ideate chain; the graph goes to synthesis.
    directory = _write_spark(tmp_path, 211)
    _write_brainstorm_bundle(directory, status="accepted")
    assert next_node(tmp_path, 211) == (NodeType.DISCOVERY_SYNTHESIS, None)


def test_pre_plan_transition_does_not_skip_on_unaccepted_brainstorm_bucket(
    tmp_path: Path,
) -> None:
    # A partial write (no front-matter) or a rejected back-edge bundle must NOT
    # short-circuit the headless chain; only a resolved status: accepted bundle does.
    directory = _write_spark(tmp_path, 212)
    _write_discovery_bucket(directory, "brainstorm")  # plain markdown, no front-matter
    assert next_node(tmp_path, 212) == (NodeType.DISCOVERY_RESEARCH, None)

    _write_brainstorm_bundle(directory, status="rejected")  # back-edge, not accepted
    assert next_node(tmp_path, 212) == (NodeType.DISCOVERY_RESEARCH, None)


def test_brainstorm_bundle_is_a_synthesis_source(tmp_path: Path) -> None:
    # Synthesis reads all of discovery/ (bar its own outputs), so the interactive
    # brainstorm bundle is part of the input synthesis decomposes from. Woof ingests
    # the bundle as discovery prose; it re-derives stories via the LLM synthesis ->
    # definition -> breakdown chain rather than mechanically carrying work_units[].
    from woof.graph.nodes import _discovery_source_paths

    directory = _write_spark(tmp_path, 213)
    _write_brainstorm_bundle(directory, status="accepted")
    sources = _discovery_source_paths(tmp_path, 213)
    assert any(path.endswith("discovery/brainstorm/DESIGN.md") for path in sources)


def test_discovery_research_node_dispatches_primary_and_bundles_playbooks(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 220)
    _write_codebase_docs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["role"] = role
        captured["prompt"] = prompt
        captured["artefacts_loaded"] = artefacts_loaded
        _write_discovery_bucket(directory, "research")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.discovery_research_node(
        NodeInput(node_type=NodeType.DISCOVERY_RESEARCH, epic_id=220, repo_root=tmp_path)
    )

    assert captured["role"] == "primary"
    assert '"node_type": "discovery_research"' in captured["prompt"]
    # The research node lists its building-block playbooks as a menu of resolvable
    # paths rather than embedding their bodies (E21 S1).
    assert "**landscape**" in captured["prompt"]
    assert "playbooks/discovery/research/landscape.md" in captured["prompt"]
    assert "AskUserQuestion" not in captured["prompt"]
    assert captured["artefacts_loaded"] == [
        ".woof/epics/E220/spark.md",
        ".woof/codebase/STACK.md",
        ".woof/codebase/INTEGRATIONS.md",
        ".woof/codebase/CONCERNS.md",
    ]
    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.DISCOVERY_THINKING
    assert output.validation_summary and output.validation_summary.stage == 1
    assert output.paths == [".woof/epics/E220/discovery/research/research.md"]
    _assert_planning_node_input_schema(
        tmp_path, nodes._discovery_bucket_payload(tmp_path, 220, "research")
    )
    _assert_node_output_schema(tmp_path, json.loads(output.model_dump_json()))
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "discovery_bucket_explored"
    assert events[-1]["bucket"] == "research"


def test_discovery_dispatch_completed_lingering_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _write_spark(tmp_path, 225)
    _write_codebase_docs(tmp_path)

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> nodes.DispatchRunResult:
        assert repo_root == tmp_path
        assert role == "primary"
        assert epic_id == 225
        assert story_id is None
        assert '"node_type": "discovery_research"' in prompt
        assert artefacts_loaded == [
            ".woof/epics/E225/spark.md",
            ".woof/codebase/STACK.md",
            ".woof/codebase/INTEGRATIONS.md",
            ".woof/codebase/CONCERNS.md",
        ]
        _write_discovery_bucket(directory, "research")
        return _dispatch_result("completed_lingering")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.discovery_research_node(
        NodeInput(node_type=NodeType.DISCOVERY_RESEARCH, epic_id=225, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.DISCOVERY_THINKING
    assert output.paths == [".woof/epics/E225/discovery/research/research.md"]


def test_discovery_thinking_node_passes_prior_bucket_artefacts(tmp_path: Path, monkeypatch) -> None:
    directory = _write_spark(tmp_path, 224)
    _write_discovery_bucket(directory, "research")
    _write_codebase_docs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["artefacts_loaded"] = artefacts_loaded
        _write_discovery_bucket(directory, "thinking")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.discovery_thinking_node(
        NodeInput(node_type=NodeType.DISCOVERY_THINKING, epic_id=224, repo_root=tmp_path)
    )

    assert captured["artefacts_loaded"] == [
        ".woof/epics/E224/spark.md",
        ".woof/epics/E224/discovery/research/research.md",
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STRUCTURE.md",
    ]
    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.DISCOVERY_IDEATE


def test_discovery_bucket_node_skips_dispatch_when_already_populated(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 221)
    _write_discovery_bucket(directory, "thinking")

    def fail_dispatch(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("a populated discovery bucket should not dispatch")

    monkeypatch.setattr(nodes, "_run_dispatch", fail_dispatch)

    output = nodes.discovery_thinking_node(
        NodeInput(node_type=NodeType.DISCOVERY_THINKING, epic_id=221, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.DISCOVERY_IDEATE


def test_discovery_bucket_node_halts_when_no_artefacts_produced(
    tmp_path: Path, monkeypatch
) -> None:
    _write_spark(tmp_path, 222)
    _write_codebase_docs(tmp_path)

    def empty_dispatch(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", empty_dispatch)

    output = nodes.discovery_ideate_node(
        NodeInput(node_type=NodeType.DISCOVERY_IDEATE, epic_id=222, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]


def test_discovery_ideate_node_bundles_no_building_blocks(tmp_path: Path, monkeypatch) -> None:
    directory = _write_spark(tmp_path, 223)
    _write_codebase_docs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["prompt"] = prompt
        _write_discovery_bucket(directory, "ideate")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.discovery_ideate_node(
        NodeInput(node_type=NodeType.DISCOVERY_IDEATE, epic_id=223, repo_root=tmp_path)
    )

    assert '"node_type": "discovery_ideate"' in captured["prompt"]
    assert "Building-block playbook:" not in captured["prompt"]
    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.DISCOVERY_SYNTHESIS


def test_discovery_synthesis_node_dispatches_primary_and_validates_outputs(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 22)
    _write_codebase_docs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["repo_root"] = repo_root
        captured["role"] = role
        captured["epic_id"] = epic_id
        captured["story_id"] = story_id
        captured["prompt"] = prompt
        captured["artefacts_loaded"] = artefacts_loaded
        _write_discovery_synthesis(directory)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.discovery_synthesis_node(
        NodeInput(
            node_type=NodeType.DISCOVERY_SYNTHESIS,
            epic_id=22,
            repo_root=tmp_path,
        )
    )

    assert captured["repo_root"] == tmp_path
    assert captured["role"] == "primary"
    assert captured["epic_id"] == 22
    assert captured["story_id"] is None
    assert '"node_type": "discovery_synthesis"' in captured["prompt"]
    assert captured["artefacts_loaded"] == [
        ".woof/epics/E22/spark.md",
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STACK.md",
        ".woof/codebase/INTEGRATIONS.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/CONVENTIONS.md",
        ".woof/codebase/TESTING.md",
        ".woof/codebase/CONCERNS.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
        ".woof/codebase/PRINCIPLES.md",
    ]
    from woof.graph.epilogue import DISPATCH_DENIAL_EPILOGUE

    assert DISPATCH_DENIAL_EPILOGUE.strip() in captured["prompt"]
    _assert_planning_node_input_schema(
        tmp_path,
        nodes._discovery_synthesis_payload(tmp_path, 22),
    )
    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.EPIC_DEFINITION
    assert output.validation_summary and output.validation_summary.stage == 1
    assert output.paths == [
        ".woof/epics/E22/discovery/synthesis/CONCEPT.md",
        ".woof/epics/E22/discovery/synthesis/PRINCIPLES.md",
        ".woof/epics/E22/discovery/synthesis/ARCHITECTURE.md",
        ".woof/epics/E22/discovery/synthesis/OPEN_QUESTIONS.md",
    ]
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "discovery_synthesised"
    _assert_node_output_schema(tmp_path, json.loads(output.model_dump_json()))


def test_discovery_synthesis_node_validates_existing_outputs_without_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 23)
    _write_discovery_synthesis(directory)

    def fail_dispatch(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("existing discovery synthesis should not dispatch")

    monkeypatch.setattr(nodes, "_run_dispatch", fail_dispatch)

    output = nodes.discovery_synthesis_node(
        NodeInput(
            node_type=NodeType.DISCOVERY_SYNTHESIS,
            epic_id=23,
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.EPIC_DEFINITION


def test_discovery_synthesis_node_rejects_missing_problem_framing(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 230)
    _write_discovery_synthesis(directory)
    (directory / "discovery" / "synthesis" / "CONCEPT.md").write_text(
        "# Concept\n\nUseful but missing the required section.\n"
    )

    output = nodes.discovery_synthesis_node(
        NodeInput(
            node_type=NodeType.DISCOVERY_SYNTHESIS,
            epic_id=230,
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]
    assert "Problem Framing" in output.message


def test_discovery_synthesis_node_rejects_open_question_without_deferral_reason(
    tmp_path: Path,
) -> None:
    directory = _write_spark(tmp_path, 231)
    _write_discovery_synthesis(directory)
    (directory / "discovery" / "synthesis" / "OPEN_QUESTIONS.md").write_text(
        "# Open Questions\n\n## OQ1 - Which rollout path should be used?\n\n"
    )

    output = nodes.discovery_synthesis_node(
        NodeInput(
            node_type=NodeType.DISCOVERY_SYNTHESIS,
            epic_id=231,
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]
    assert "OQ1" in output.message
    assert "Deferral reason" in output.message


def test_epic_definition_node_dispatches_primary_validates_epic_and_continues(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 24)
    _write_discovery_synthesis(directory)
    _write_codebase_docs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["repo_root"] = repo_root
        captured["role"] = role
        captured["epic_id"] = epic_id
        captured["story_id"] = story_id
        captured["prompt"] = prompt
        captured["artefacts_loaded"] = artefacts_loaded
        _write_minimal_epic(directory, epic_id)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.epic_definition_node(
        NodeInput(
            node_type=NodeType.EPIC_DEFINITION,
            epic_id=24,
            repo_root=tmp_path,
        )
    )

    assert captured["repo_root"] == tmp_path
    assert captured["role"] == "primary"
    assert captured["epic_id"] == 24
    assert captured["story_id"] is None
    assert '"node_type": "epic_definition"' in captured["prompt"]
    assert captured["artefacts_loaded"] == [
        ".woof/epics/E24/discovery/synthesis/CONCEPT.md",
        ".woof/epics/E24/discovery/synthesis/PRINCIPLES.md",
        ".woof/epics/E24/discovery/synthesis/ARCHITECTURE.md",
        ".woof/epics/E24/discovery/synthesis/OPEN_QUESTIONS.md",
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/CONCERNS.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
        ".woof/codebase/PRINCIPLES.md",
    ]
    _assert_planning_node_input_schema(
        tmp_path,
        nodes._epic_definition_payload(tmp_path, 24),
    )
    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.CONTRACT_READINESS
    assert output.validation_summary and output.validation_summary.stage == 2
    assert output.validation_summary.ok is True
    assert output.paths == [".woof/epics/E24/EPIC.md"]
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "definition_closed"
    _assert_node_output_schema(tmp_path, json.loads(output.model_dump_json()))


def test_epic_definition_node_rechecks_existing_discovery_contract(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 239)
    _write_discovery_synthesis(directory)
    (directory / "discovery" / "synthesis" / "CONCEPT.md").write_text("# Concept\n")

    output = nodes.epic_definition_node(
        NodeInput(
            node_type=NodeType.EPIC_DEFINITION,
            epic_id=239,
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.HALTED
    assert output.validation_summary and output.validation_summary.stage == 1
    assert "Problem Framing" in output.message


def test_epic_definition_node_requires_open_question_resolution(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 240)
    _write_discovery_synthesis_with_open_question(directory)
    _write_minimal_epic(directory, 240)

    output = nodes.epic_definition_node(
        NodeInput(
            node_type=NodeType.EPIC_DEFINITION,
            epic_id=240,
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]
    assert "OQ1" in output.message
    assert "resolve or carry forward" in output.message


def test_epic_definition_node_accepts_resolved_open_question(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 241)
    _write_discovery_synthesis_with_open_question(directory)
    _write_epic_with_resolved_open_question(directory, 241)

    output = nodes.epic_definition_node(
        NodeInput(
            node_type=NodeType.EPIC_DEFINITION,
            epic_id=241,
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.CONTRACT_READINESS


def test_epic_definition_node_halts_on_invalid_existing_epic(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 25)
    directory.joinpath("EPIC.md").write_text("---\nepic_id: 25\n---\n")

    output = nodes.epic_definition_node(
        NodeInput(
            node_type=NodeType.EPIC_DEFINITION,
            epic_id=25,
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]
    assert output.validation_summary and output.validation_summary.ok is False
    assert "INVALID" in output.message


def test_breakdown_planning_node_dispatches_primary_validates_plan_and_renders_markdown(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 26)
    _write_minimal_epic(directory, 26)
    _write_codebase_docs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["repo_root"] = repo_root
        captured["role"] = role
        captured["epic_id"] = epic_id
        captured["story_id"] = story_id
        captured["prompt"] = prompt
        captured["artefacts_loaded"] = artefacts_loaded
        _write_stage3_plan(directory, epic_id)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.breakdown_planning_node(
        NodeInput(
            node_type=NodeType.BREAKDOWN_PLANNING,
            epic_id=26,
            repo_root=tmp_path,
        )
    )

    assert captured["repo_root"] == tmp_path
    assert captured["role"] == "primary"
    assert captured["epic_id"] == 26
    assert captured["story_id"] is None
    assert '"node_type": "breakdown_planning"' in captured["prompt"]
    assert captured["artefacts_loaded"] == [
        ".woof/epics/E26/EPIC.md",
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
        ".woof/codebase/PRINCIPLES.md",
    ]
    assert "Right-sized work units" in captured["prompt"]
    assert "Do not author `PLAN.md`" in captured["prompt"]
    _assert_planning_node_input_schema(
        tmp_path,
        nodes._breakdown_planning_payload(tmp_path, 26),
    )
    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.PLAN_CRITIQUE
    assert output.validation_summary and output.validation_summary.stage == 3
    assert output.paths == [".woof/epics/E26/plan.json", ".woof/epics/E26/PLAN.md"]
    plan_md = (directory / "PLAN.md").read_text()
    assert "| S1 | Build the first surface | pending | O1 | - | - | - |" in plan_md
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "breakdown_planned"
    _assert_node_output_schema(tmp_path, json.loads(output.model_dump_json()))


def test_breakdown_planning_node_rejects_crossref_invalid_plan_before_critique(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 260)
    _write_minimal_epic(directory, 260)
    _write_codebase_docs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["role"] = role
        _write_stage3_plan(directory, epic_id)
        plan = json.loads((directory / "plan.json").read_text())
        plan["work_units"][0]["satisfies"] = ["O999"]
        (directory / "plan.json").write_text(json.dumps(plan))
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.breakdown_planning_node(
        NodeInput(
            node_type=NodeType.BREAKDOWN_PLANNING,
            epic_id=260,
            repo_root=tmp_path,
        )
    )

    assert captured["role"] == "primary"
    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]
    assert "satisfies unknown outcome O999" in output.message
    assert not (directory / "PLAN.md").exists()


def test_plan_critique_node_dispatches_reviewer_validates_critique_and_halts(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 27)
    _write_minimal_epic(directory, 27)
    _write_stage3_plan(directory, 27)
    _write_codebase_docs(tmp_path)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 27)))
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["repo_root"] = repo_root
        captured["role"] = role
        captured["epic_id"] = epic_id
        captured["story_id"] = story_id
        captured["prompt"] = prompt
        captured["artefacts_loaded"] = artefacts_loaded
        _write_plan_critique(directory)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.plan_critique_node(
        NodeInput(
            node_type=NodeType.PLAN_CRITIQUE,
            epic_id=27,
            repo_root=tmp_path,
        )
    )

    assert captured["repo_root"] == tmp_path
    assert captured["role"] == "reviewer"
    assert captured["epic_id"] == 27
    assert captured["story_id"] is None
    assert '"node_type": "plan_critique"' in captured["prompt"]
    assert captured["artefacts_loaded"] == [
        ".woof/epics/E27/EPIC.md",
        ".woof/epics/E27/plan.json",
        ".woof/epics/E27/PLAN.md",
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/CONCERNS.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
    ]
    _assert_planning_node_input_schema(
        tmp_path,
        nodes._plan_critique_payload(tmp_path, 27),
    )
    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.PLAN_GATE_OPEN
    assert output.validation_summary and output.validation_summary.stage == 3
    assert output.paths == [".woof/epics/E27/critique/plan.md"]
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "plan_critiqued"
    assert events[-1]["severity"] == "minor"
    assert events[-1]["finding_count"] == 1
    _assert_node_output_schema(tmp_path, json.loads(output.model_dump_json()))


def test_graph_runs_discovery_definition_breakdown_and_opens_plan_gate(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 28)
    _write_codebase_docs(tmp_path)

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert repo_root == tmp_path
        assert epic_id == 28
        assert story_id is None
        assert artefacts_loaded
        if '"node_type": "discovery_research"' in prompt:
            assert role == "primary"
            _write_discovery_bucket(directory, "research")
        elif '"node_type": "discovery_thinking"' in prompt:
            assert role == "primary"
            _write_discovery_bucket(directory, "thinking")
        elif '"node_type": "discovery_ideate"' in prompt:
            assert role == "primary"
            _write_discovery_bucket(directory, "ideate")
        elif '"node_type": "discovery_synthesis"' in prompt:
            assert role == "primary"
            _write_discovery_synthesis(directory)
        elif '"node_type": "epic_definition"' in prompt:
            assert role == "primary"
            _write_minimal_epic(directory, epic_id)
        elif '"node_type": "breakdown_planning"' in prompt:
            assert role == "primary"
            _write_stage3_plan(directory, epic_id)
        elif '"node_type": "plan_critique"' in prompt:
            assert role == "reviewer"
            _write_plan_critique(directory, "info")
        else:
            raise AssertionError(prompt)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    outputs = run_graph(tmp_path, 28)

    assert [output.node_type for output in outputs] == [
        NodeType.DISCOVERY_RESEARCH,
        NodeType.DISCOVERY_THINKING,
        NodeType.DISCOVERY_IDEATE,
        NodeType.DISCOVERY_SYNTHESIS,
        NodeType.EPIC_DEFINITION,
        NodeType.CONTRACT_READINESS,
        NodeType.BREAKDOWN_PLANNING,
        NodeType.PLAN_CRITIQUE,
        NodeType.PLAN_GATE_OPEN,
    ]
    assert outputs[-1].status == NodeStatus.GATE_OPENED
    assert outputs[-1].gate_path == ".woof/epics/E28/gate.md"
    gate = directory / "gate.md"
    gate_fm = _read_gate_fm(gate)
    assert gate_fm["type"] == "plan_gate"
    assert gate_fm["stage"] == 4
    assert gate_fm["story_id"] is None
    assert gate_fm["triggered_by"] == ["plan_review"]
    gate_text = gate.read_text()
    assert "## Context" in gate_text
    assert "## Primary position" in gate_text
    assert "## Reviewer position" in gate_text
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "plan_gate_opened"
    assert events[-1]["gate_type"] == "plan_gate"
    assert events[-1]["triggered_by"] == ["plan_review"]


def test_plan_gate_open_node_reconstitutes_missing_gate_after_valid_critique(
    tmp_path: Path,
) -> None:
    directory = _write_spark(tmp_path, 29)
    _write_minimal_epic(directory, 29)
    _write_stage3_plan(directory, 29)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 29)))
    _write_plan_critique(directory, "blocker")

    assert next_node(tmp_path, 29) == (NodeType.PLAN_GATE_OPEN, None)

    output = nodes.plan_gate_open_node(
        NodeInput(
            node_type=NodeType.PLAN_GATE_OPEN,
            epic_id=29,
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["plan_review"]
    assert output.validation_summary
    assert output.validation_summary.stage == 4
    assert output.validation_summary.ok is True
    _assert_node_output_schema(tmp_path, json.loads(output.model_dump_json()))
    _assert_planning_node_input_schema(tmp_path, nodes._plan_gate_open_payload(tmp_path, 29))
    gate = directory / "gate.md"
    gate_fm = _read_gate_fm(gate)
    assert gate_fm["type"] == "plan_gate"
    assert gate_fm["stage"] == 4
    assert gate_fm["story_id"] is None
    assert gate_fm["triggered_by"] == ["plan_review"]
    gate_text = gate.read_text()
    assert "F1 [blocker]: tighten story scope" in gate_text
    assert "Plan critique body." in gate_text
    assert next_node(tmp_path, 29) == (NodeType.HUMAN_REVIEW, None)


def test_plan_gate_resolution_unblocks_stage_5_story_execution(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 30)
    _write_minimal_epic(directory, 30)
    _write_stage3_plan(directory, 30)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 30)))
    _write_plan_critique(directory, "info")
    (directory / "epic.jsonl").write_text(
        json.dumps(
            {
                "event": "gate_resolved",
                "at": "2026-01-01T00:00:00Z",
                "epic_id": 30,
                "gate_type": "plan_gate",
                "decision": "approve",
            }
        )
        + "\n"
    )

    assert next_node(tmp_path, 30) == (NodeType.EXECUTOR_DISPATCH, "S1")


def test_critique_dispatch_failure_opens_reviewer_gate(tmp_path: Path, monkeypatch) -> None:
    _init_git_repo(tmp_path)
    directory = _write_plan(tmp_path, 1)
    _write_codebase_docs(tmp_path)
    (directory / "EPIC.md").write_text("---\nepic_id: 1\n---\n")

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert repo_root == tmp_path
        assert role == "reviewer"
        assert epic_id == 1
        assert story_id == "S1"
        assert "Graph-owned input:" in prompt
        assert '"node_type": "critique_dispatch"' in prompt
        assert '"story_id": "S1"' in prompt
        assert '"staged_diff_command": "git diff --staged"' in prompt
        assert artefacts_loaded == [
            ".woof/epics/E1/plan.json",
            ".woof/epics/E1/EPIC.md",
            ".woof/codebase/CONVENTIONS.md",
            ".woof/codebase/TESTING.md",
            ".woof/codebase/CONCERNS.md",
        ]
        return subprocess.CompletedProcess([], 2, "", "reviewer failed")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.critique_dispatch_node(
        NodeInput(
            node_type=NodeType.CRITIQUE_DISPATCH,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["reviewer_unreachable"]
    gate_fm = _read_gate_fm(tmp_path / ".woof" / "epics" / "E1" / "gate.md")
    assert gate_fm["triggered_by"] == ["reviewer_unreachable"]


def test_critique_dispatch_stages_changed_story_paths_before_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    directory = _write_plan(tmp_path, 1)
    _write_codebase_docs(tmp_path)
    (directory / "EPIC.md").write_text("---\nepic_id: 1\n---\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print('O1')\n")
    (tmp_path / "scratch.txt").write_text("outside story scope\n")

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        staged = _git(
            repo_root,
            "diff",
            "--cached",
            "--name-only",
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        assert role == "reviewer"
        assert story_id == "S1"
        assert staged == ["src/app.py"]
        assert "scratch.txt" not in staged
        assert '"staged_diff_command": "git diff --staged"' in prompt
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    output = nodes.critique_dispatch_node(
        NodeInput(
            node_type=NodeType.CRITIQUE_DISPATCH,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.REVIEW_DISPOSITION


def test_verification_stages_changed_story_paths_before_stage5_checks(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir(exist_ok=True)
    (tmp_path / ".gitignore").write_text(
        ".woof/epics/*/executor_result.json\n"
        ".woof/epics/*/check-result.json\n"
        "__pycache__/\n"
        "tests/__pycache__/\n"
        "*.pyc\n"
    )
    (woof_dir / "quality-gates.toml").write_text(
        "[gates.compile]\n"
        'command = "PYTHONDONTWRITEBYTECODE=1 python -m py_compile src/app.py tests/test_app.py"\n'
        "timeout_seconds = 30\n"
    )
    (woof_dir / "test-markers.toml").write_text(
        "[languages.python]\n"
        'test_paths = ["tests/"]\n'
        r"marker_regex = '(?<![A-Za-z0-9])O\d+(?![A-Za-z0-9])'" + "\n"
        'docstring_keyword = "outcomes:"\n'
        'comment_prefix = "#"\n'
        "context_lines = 3\n"
    )
    _git(
        tmp_path,
        "add",
        "--",
        ".gitignore",
        ".woof/quality-gates.toml",
        ".woof/test-markers.toml",
        check=True,
    )
    _git(tmp_path, "commit", "-m", "test: initialise consumer config", check=True)

    directory = _write_plan(tmp_path, 1)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"][0]["paths"] = ["src/*.py", "tests/*.py"]
    plan["work_units"][0]["status"] = "in_progress"
    (directory / "plan.json").write_text(json.dumps(plan))
    _write_minimal_epic(directory, 1)
    (directory / "dispatch.jsonl").write_text("{}\n")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, 1, "S1")
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "app.py").write_text("def marker() -> str:\n    return 'O1'\n")
    (tests / "test_app.py").write_text(
        '"""outcomes: O1"""\n\ndef test_marker_reports_o1() -> None:\n    assert True\n'
    )

    output = nodes.verification_node(
        NodeInput(
            node_type=NodeType.VERIFICATION,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.COMPLETED
    check_result = json.loads((directory / "check-result.json").read_text())
    assert check_result["ok"], check_result
    staged = _git(
        tmp_path,
        "diff",
        "--cached",
        "--name-only",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "src/app.py" in staged
    assert "tests/test_app.py" in staged


def test_review_disposition_writes_deterministic_non_blocking_disposition(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_plan(tmp_path, 1)
    (directory / "EPIC.md").write_text("---\nepic_id: 1\n---\n")
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: minor\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings:\n  - id: F1\n    severity: minor\n    summary: needs note\n---\n"
    )

    def fail_dispatch(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("non-blocking dispositions should be graph-owned")

    monkeypatch.setattr(nodes, "_run_dispatch", fail_dispatch)

    assert next_node(tmp_path, 1) == (NodeType.REVIEW_DISPOSITION, "S1")
    output = nodes.review_disposition_node(
        NodeInput(
            node_type=NodeType.REVIEW_DISPOSITION,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.VERIFICATION
    assert output.paths == [".woof/epics/E1/dispositions/story-S1.md"]
    disposition = _read_yaml_front_matter(directory / "dispositions" / "story-S1.md")
    assert disposition["timestamp"]
    assert disposition["harness"] == "woof-deterministic-disposition"
    assert disposition["severity"] == "minor"
    assert disposition["dispositions"] == [
        {
            "finding_id": "F1",
            "decision": "deferred",
            "rationale": (
                "Reviewer marked this finding non-blocking; Woof recorded a deterministic "
                "disposition and continued to verification without a primary model revision."
            ),
        }
    ]


def test_review_disposition_repairs_invalid_non_blocking_timestamp(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\nfindings: []\n---\n"
    )
    disposition_dir = directory / "dispositions"
    disposition_dir.mkdir()
    (disposition_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\n"
        "critique_path: .woof/epics/E1/critique/story-S1.md\n"
        "severity: info\n"
        "timestamp: ''\n"
        "harness: test-primary\n"
        "dispositions: []\n"
        "---\n"
    )

    def fail_dispatch(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("invalid non-blocking dispositions should be repaired")

    monkeypatch.setattr(nodes, "_run_dispatch", fail_dispatch)

    assert next_node(tmp_path, 1) == (NodeType.REVIEW_DISPOSITION, "S1")
    output = nodes.review_disposition_node(
        NodeInput(
            node_type=NodeType.REVIEW_DISPOSITION,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.VERIFICATION
    disposition = _read_yaml_front_matter(disposition_dir / "story-S1.md")
    assert disposition["timestamp"]
    assert disposition["harness"] == "woof-deterministic-disposition"


def test_reviewer_blocker_opens_gate_without_primary_debate(tmp_path: Path, monkeypatch) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings:\n  - id: F1\n    severity: blocker\n    summary: missing assertion\n"
        "    evidence: 'S1 does not assert the required outcome'\n"
        "---\nReviewer says the staged test does not assert O1.\n"
    )

    def fail_dispatch(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("blocker disposition must not dispatch the primary")

    monkeypatch.setattr(nodes, "_run_dispatch", fail_dispatch)

    assert next_node(tmp_path, 1) == (NodeType.REVIEW_DISPOSITION, "S1")
    output = nodes.review_disposition_node(
        NodeInput(
            node_type=NodeType.REVIEW_DISPOSITION,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["check_6_critique_blocker"]
    gate = directory / "gate.md"
    gate_fm = _read_gate_fm(gate)
    assert gate_fm["triggered_by"] == ["check_6_critique_blocker"]
    gate_text = gate.read_text()
    assert "## Primary position" in gate_text
    assert "## Reviewer position" in gate_text


def test_reviewer_blocker_without_evidence_opens_incomplete_gate(tmp_path: Path) -> None:
    """P1 regression: blocker finding with no evidence opens incomplete gate, not reviewer-blocker gate."""
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings:\n  - id: F1\n    severity: blocker\n    summary: missing assertion\n"
        "---\nNo evidence supplied.\n"
    )

    output = nodes.review_disposition_node(
        NodeInput(
            node_type=NodeType.REVIEW_DISPOSITION,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["incomplete_stage_state"]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_reviewer_blocker_with_unresolvable_evidence_opens_incomplete_gate(tmp_path: Path) -> None:
    """P1 regression: blocker finding with prose-only evidence (no resolvable ref) opens incomplete gate."""
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings:\n  - id: F1\n    severity: blocker\n    summary: bad impl\n"
        "    evidence: 'The implementation is wrong and should be fixed'\n"
        "---\nProse-only evidence; no resolvable ref.\n"
    )

    output = nodes.review_disposition_node(
        NodeInput(
            node_type=NodeType.REVIEW_DISPOSITION,
            epic_id=1,
            story_id="S1",
            repo_root=tmp_path,
        )
    )

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["incomplete_stage_state"]
    assert "F1" in (output.message or "")


def test_graph_resumes_interrupted_commit_transaction(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    (directory / "epic.jsonl").write_text(
        json.dumps({"event": "story_completed", "epic_id": 1, "story_id": "S1"}) + "\n"
    )

    assert next_node(tmp_path, 1) == (NodeType.COMMIT, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].node_type == NodeType.COMMIT
    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events].count("story_completed") == 1
    assert [event["event"] for event in events].count("transaction_manifest_verified") == 1
    assert not (directory / "executor_result.json").exists()
    assert not (directory / "check-result.json").exists()
    status = _git(
        tmp_path,
        "status",
        "--porcelain=v1",
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
    subject = _git(
        tmp_path,
        "log",
        "-1",
        "--pretty=%s",
        check=True,
        capture_output=True,
        text=True,
    )
    assert subject.stdout.strip() == "feat: E1 S1 - first"


def test_commit_gates_when_verified_staged_tree_changes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    story = transitions.load_plan(tmp_path, 1).work_units[0]
    manifest = build_story_manifest(tmp_path, 1, story)
    _git(tmp_path, "add", "--", *manifest.expected_paths, check=True)
    verified_tree = _git(
        tmp_path,
        "write-tree",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    verified_paths = _git(
        tmp_path,
        "diff",
        "--cached",
        "--name-only",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    check_result_path = directory / "check-result.json"
    check_result = json.loads(check_result_path.read_text())
    check_result["verified_tree"] = verified_tree
    check_result["verified_paths"] = verified_paths
    check_result_path.write_text(json.dumps(check_result))

    (tmp_path / "src" / "app.py").write_text("print('tampered after verification')\n")
    _git(tmp_path, "add", "--", "src/app.py", check=True)

    outputs = run_graph(tmp_path, 1, once=True)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].triggered_by == ["check_7_commit_transaction"]
    assert "Verified transaction changed before commit" in outputs[0].message


def test_commit_writes_epic_completed_into_commit_for_mixed_done_abandoned_epic(
    tmp_path: Path,
) -> None:
    # Completing the final story of an epic whose other stories are `abandoned`
    # still completes the epic: every story is terminal. commit_node must stage
    # `epic_completed` INTO the final commit (the in-commit gate now uses the
    # terminal-status set, not done-only), leaving no dangling/uncommitted marker.
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"] = [
        {**plan["work_units"][0], "id": "S1", "status": "done"},
        {**plan["work_units"][0], "id": "S2", "title": "second", "status": "abandoned"},
    ]
    (directory / "plan.json").write_text(json.dumps(plan))

    assert next_node(tmp_path, 1) == (NodeType.COMMIT, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].node_type == NodeType.COMMIT
    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE

    # epic_completed is written exactly once, by commit_node, before the commit.
    events = [
        json.loads(line)
        for line in (directory / "epic.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [event["event"] for event in events].count("epic_completed") == 1

    # The post-commit tree is clean: the marker landed inside the commit rather
    # than being appended after it.
    status = _git(
        tmp_path,
        "status",
        "--porcelain=v1",
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""

    # HEAD's epic.jsonl already carries the marker - proof it was committed, not
    # left dangling in the working tree.
    committed = _git(
        tmp_path,
        "show",
        "HEAD:.woof/epics/E1/epic.jsonl",
        check=True,
        capture_output=True,
        text=True,
    )
    committed_events = [json.loads(line) for line in committed.stdout.splitlines() if line.strip()]
    assert any(event["event"] == "epic_completed" for event in committed_events)


def test_commit_resume_git_failure_preserves_resume_artefacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _write_ready_commit_state(tmp_path, 1)

    def fail_manifest(*_args: object, **_kwargs: object) -> object:
        raise subprocess.CalledProcessError(
            128,
            ["git", "status"],
            stderr="fatal: not a git repository",
        )

    monkeypatch.setattr(transitions, "build_story_manifest", fail_manifest)

    with pytest.raises(StageStateError) as exc:
        next_node(tmp_path, 1)

    assert "could not inspect interrupted commit state" in str(exc.value)
    assert "preserving executor_result.json and check-result.json" in str(exc.value)
    assert (directory / "executor_result.json").exists()
    assert (directory / "check-result.json").exists()


def test_commit_uses_executor_commit_subject(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write_ready_commit_state(
        tmp_path,
        1,
        commit_subject="fix: E1 S1 - repair consumer checkout bootstrap",
    )

    outputs = run_graph(tmp_path, 1)

    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE
    subject = _git(
        tmp_path,
        "log",
        "-1",
        "--pretty=%s",
        check=True,
        capture_output=True,
        text=True,
    )
    assert subject.stdout.strip() == "fix: E1 S1 - repair consumer checkout bootstrap"


def test_commit_redacts_audit_before_staging_transaction(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    (tmp_path / ".woof" / "agents.toml").write_text(
        """\
[roles.story-executor]
harness = "cld"

[roles.critiquer]
harness = "cod"

[audit]
max_bytes = 4096
"""
    )
    audit_file = directory / "audit" / "cod-critiquer-1.prompt"
    audit_file.write_text("call API with Bearer live-oauth-token\n")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].node_type == NodeType.COMMIT
    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE
    text = audit_file.read_text()
    assert "live-oauth-token" not in text
    assert "[REDACTED:bearer_token]" in text
    committed = _git(
        tmp_path,
        "show",
        "HEAD:.woof/epics/E1/audit/cod-critiquer-1.prompt",
        check=True,
        capture_output=True,
        text=True,
    )
    assert "live-oauth-token" not in committed.stdout


def test_complete_epic_cleans_stale_transient_files(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    _git(
        tmp_path,
        "add",
        "src/app.py",
        ".woof/epics/E1/plan.json",
        ".woof/epics/E1/epic.jsonl",
        ".woof/epics/E1/dispatch.jsonl",
        ".woof/epics/E1/critique/story-S1.md",
        ".woof/epics/E1/dispositions/story-S1.md",
        ".woof/epics/E1/audit/cod-critiquer-1.prompt",
        check=True,
    )
    _git(tmp_path, "commit", "-m", "seed", check=True, capture_output=True)

    outputs = run_graph(tmp_path, 1)

    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE
    assert not (directory / "executor_result.json").exists()
    assert not (directory / "check-result.json").exists()


def test_in_progress_story_missing_executor_result_opens_incomplete_state_gate(
    tmp_path: Path,
) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")

    assert next_node(tmp_path, 1) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs == [
        NodeOutput(
            node_type=NodeType.GATE_OPEN,
            status=NodeStatus.GATE_OPENED,
            epic_id=1,
            story_id="S1",
            gate_path=".woof/epics/E1/gate.md",
            triggered_by=["incomplete_stage_state"],
            message="Required Stage-5 artefact missing: .woof/epics/E1/executor_result.json",
        )
    ]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_in_progress_story_malformed_executor_result_opens_incomplete_state_gate(
    tmp_path: Path,
) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text("{")

    assert next_node(tmp_path, 1) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].triggered_by == ["incomplete_stage_state"]
    assert "malformed JSON" in outputs[0].message
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_malformed_check_result_opens_incomplete_state_gate(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, 1, "S1")
    (directory / "check-result.json").write_text("{")

    assert next_node(tmp_path, 1) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].triggered_by == ["incomplete_stage_state"]
    assert "check-result.json" in outputs[0].message
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_failed_check_result_reopens_structured_gate_on_reentry(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings:\n  - id: F1\n    severity: blocker\n    summary: test\n"
        "    evidence: 'S1 does not implement the required contract'\n---\n"
    )
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 1,
                "story_id": "S1",
                "triggered_by": ["check_6_critique_blocker"],
                "checks": [
                    {
                        "id": "check_6_critique_blocker",
                        "ok": False,
                        "severity": "blocker",
                        "summary": "critique severity is blocker",
                        "evidence": None,
                        "paths": [],
                        "command": None,
                        "exit_code": None,
                    }
                ],
            }
        )
    )

    assert next_node(tmp_path, 1) == (NodeType.REVIEW_DISPOSITION, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].node_type == NodeType.REVIEW_DISPOSITION
    assert outputs[0].triggered_by == ["check_6_critique_blocker"]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["check_6_critique_blocker"]


def test_successor_selection_respects_dependency_closure(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 12)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"] = [
        {
            **plan["work_units"][0],
            "id": "S1",
            "title": "first",
            "status": "done",
            "deps": [],
        },
        {
            **plan["work_units"][0],
            "id": "S2",
            "title": "second",
            "status": "pending",
            "deps": ["S1"],
        },
    ]
    (directory / "plan.json").write_text(json.dumps(plan))

    assert next_node(tmp_path, 12) == (NodeType.EXECUTOR_DISPATCH, "S2")


def test_run_graph_opens_recoverable_gate_when_dependencies_are_unsatisfied(
    tmp_path: Path,
) -> None:
    directory = _write_plan(tmp_path, 13)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"][0]["deps"] = ["S99"]
    (directory / "plan.json").write_text(json.dumps(plan))

    outputs = run_graph(tmp_path, 13)

    assert outputs == [
        NodeOutput(
            node_type=NodeType.PLAN_GATE_OPEN,
            status=NodeStatus.GATE_OPENED,
            epic_id=13,
            gate_path=".woof/epics/E13/gate.md",
            triggered_by=["incomplete_stage_state"],
            message="E13 has pending work units, but no work unit has satisfied dependencies",
        )
    ]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["type"] == "plan_gate"
    assert gate_fm["story_id"] is None
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_run_graph_opens_recoverable_gate_for_malformed_plan_json(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 24)
    (directory / "plan.json").write_text("{")

    outputs = run_graph(tmp_path, 24)

    assert outputs[0].node_type == NodeType.PLAN_GATE_OPEN
    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].triggered_by == ["incomplete_stage_state"]
    assert "required Stage-5 artefact is malformed" in outputs[0].message
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["type"] == "plan_gate"
    assert gate_fm["story_id"] is None
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_gate_reentry_halts_at_human_review_with_gate_path(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 14)
    (directory / "gate.md").write_text("---\ntype: story_gate\n---\n")

    assert next_node(tmp_path, 14) == (NodeType.HUMAN_REVIEW, None)

    outputs = run_graph(tmp_path, 14)

    assert outputs == [
        NodeOutput(
            node_type=NodeType.HUMAN_REVIEW,
            status=NodeStatus.HALTED,
            epic_id=14,
            gate_path=".woof/epics/E14/gate.md",
            message="gate open at .woof/epics/E14/gate.md",
        )
    ]


def test_empty_diff_executor_result_opens_review_gate(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 15)
    mark_story_status(tmp_path, 15, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 15,
                "story_id": "S1",
                "outcome": "empty_diff",
                "commit_body": None,
                "position": "No diff was needed.",
            }
        )
    )

    assert next_node(tmp_path, 15) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 15)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].gate_path == ".woof/epics/E15/gate.md"
    assert outputs[0].triggered_by == ["empty_diff_review"]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["empty_diff_review"]


def test_wf_epic_reports_complete_epic_as_json(tmp_path: Path) -> None:
    (tmp_path / ".woof").mkdir(exist_ok=True)
    (tmp_path / ".woof" / "prerequisites.toml").write_text(
        '[tracker]\nkind = "github"\nrepo = "acme/widgets"\n'
    )
    directory = _write_plan(tmp_path, 7)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"][0]["status"] = "done"
    (directory / "plan.json").write_text(json.dumps(plan))
    _write_minimal_epic(directory, 7)
    remote_body = "Remote intent.\n\n## Observable Outcomes\n\n- stale\n"
    _write_last_sync(directory, 7, body=remote_body)
    env = _make_gh_completion_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "7", "--format", "json", env=env)

    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert lines == [
        {
            "node_type": "human_review",
            "status": "epic_complete",
            "epic_id": 7,
            "story_id": None,
            "next_node": None,
            "gate_path": None,
            "validation_summary": None,
            "triggered_by": [],
            "message": "E7 complete",
            "paths": [],
        }
    ]
    _assert_node_output_schema(tmp_path, lines[0])


def test_wf_opens_gate_for_recoverable_missing_plan_state(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = tmp_path / ".woof" / "epics" / "E10"
    directory.mkdir(parents=True)
    _write_last_sync(directory, 10)
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "10", env=env)

    assert proc.returncode == 0, proc.stderr
    assert (
        "woof wf: plan_gate_open -> gate_opened: required planning artefact missing" in proc.stdout
    )
    assert "spark.md" in proc.stdout
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["type"] == "plan_gate"
    assert gate_fm["story_id"] is None
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_wf_epic_halts_when_gate_is_open(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_plan(tmp_path, 8)
    _write_last_sync(directory, 8)
    (directory / "gate.md").write_text("---\ntype: story_gate\n---\n")
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "8", env=env)

    assert proc.returncode == 0, proc.stderr
    assert "woof wf: human_review -> halted: gate open at .woof/epics/E8/gate.md" in proc.stdout


def test_wf_gate_case_reports_stable_json_contract(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_plan(tmp_path, 11)
    mark_story_status(tmp_path, 11, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 11,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings:\n  - id: F1\n    severity: blocker\n    summary: test\n"
        "    evidence: 'S1 does not implement the required contract'\n---\n"
    )
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 11,
                "story_id": "S1",
                "triggered_by": ["check_6_critique_blocker"],
                "checks": [
                    {
                        "id": "check_6_critique_blocker",
                        "ok": False,
                        "severity": "blocker",
                        "summary": "critique severity is blocker",
                        "evidence": None,
                        "paths": [],
                        "command": None,
                        "exit_code": None,
                    }
                ],
            }
        )
    )

    _write_last_sync(directory, 11)
    env = _make_gh_rate_limit_stub(tmp_path / "bin")
    proc = _run_woof(tmp_path, "wf", "--epic", "11", "--format", "json", env=env)

    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert lines == [
        {
            "node_type": "review_disposition",
            "status": "gate_opened",
            "epic_id": 11,
            "story_id": "S1",
            "next_node": None,
            "gate_path": ".woof/epics/E11/gate.md",
            "validation_summary": None,
            "triggered_by": ["check_6_critique_blocker"],
            "message": (
                "## Context\n\n"
                "Reviewer critique `.woof/epics/E11/critique/story-S1.md` marked story S1 as blocker. "
                "Woof does not start a model-to-model debate loop for blocker findings.\n\n"
                "## Findings\n\n"
                "- F1: test\n"
                "  Evidence: S1 does not implement the required contract\n\n"
                "## Primary position\n\n"
                "The primary story output remains staged for operator inspection. "
                "No primary disposition was requested because blocker findings require a human gate.\n\n"
                "## Reviewer position\n\n"
                "Source: `.woof/epics/E11/critique/story-S1.md`\n\n"
                "Reviewer body was empty.\n"
            ),
            "paths": [],
        }
    ]
    _assert_node_output_schema(tmp_path, lines[0])


def test_wf_resolve_records_gate_decision_and_removes_gate(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_plan(tmp_path, 9)
    _write_last_sync(directory, 9)
    gate = directory / "gate.md"
    gate.write_text("---\ntype: story_gate\n---\n")
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "9", "--resolve", "approve", env=env)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "woof wf: gate resolved decision=approve\n"
    assert not gate.exists()
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "gate_resolved"
    assert events[-1]["epic_id"] == 9
    assert events[-1]["gate_type"] == "story_gate"
    assert events[-1]["decision"] == "approve"


def test_wf_resolve_reviewer_blocker_approval_requeues_critique(tmp_path: Path) -> None:
    (tmp_path / ".woof").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".woof" / "prerequisites.toml").write_text('[tracker]\nkind = "local"\n')
    directory = _write_plan(tmp_path, 33)
    mark_story_status(tmp_path, 33, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 33,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_subject": "feat: test",
                "commit_body": "body",
                "position": None,
            }
        )
    )
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 33,
                "story_id": "S1",
                "triggered_by": ["check_6_critique_blocker"],
                "checks": [],
            }
        )
    )
    critique_path = directory / "critique" / "story-S1.md"
    critique_path.parent.mkdir()
    critique_path.write_text(
        "---\n"
        "target: story\n"
        "target_id: S1\n"
        "severity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "harness: test-reviewer\n"
        "findings:\n"
        "  - id: F1\n"
        "    severity: blocker\n"
        "    summary: stale staged diff\n"
        "---\n"
    )
    disposition_path = _write_disposition(directory, 33)
    gate = directory / "gate.md"
    gate.write_text(
        "---\n"
        "type: story_gate\n"
        "story_id: S1\n"
        "triggered_by:\n"
        "  - check_6_critique_blocker\n"
        "---\n"
        "Operator fixed the staged diff.\n"
    )

    proc = _run_woof(tmp_path, "wf", "--epic", "33", "--resolve", "approve")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "woof wf: gate resolved decision=approve\n"
    assert not gate.exists()
    assert not (directory / "check-result.json").exists()
    assert not critique_path.exists()
    assert not disposition_path.exists()
    assert (directory / "executor_result.json").exists()
    assert next_node(tmp_path, 33) == (NodeType.CRITIQUE_DISPATCH, "S1")
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    story_event = next(event for event in events if event["event"] == "story_gate_resolved")
    assert story_event["decision"] == "approve"
    assert story_event["triggered_by"] == ["check_6_critique_blocker"]
    assert ".woof/epics/E33/check-result.json" in story_event["paths"]
    assert ".woof/epics/E33/critique/story-S1.md" in story_event["paths"]
    assert ".woof/epics/E33/dispositions/story-S1.md" in story_event["paths"]


def test_wf_resolve_commit_transaction_gate_preserves_ok_check_result(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".woof").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".woof" / "prerequisites.toml").write_text('[tracker]\nkind = "local"\n')
    directory = _write_plan(tmp_path, 34)
    mark_story_status(tmp_path, 34, "S1", "done")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 34,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_subject": "feat: test",
                "commit_body": "body",
                "position": None,
            }
        )
    )
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": True,
                "stage": 5,
                "epic_id": 34,
                "story_id": "S1",
                "triggered_by": [],
                "checks": [],
            }
        )
    )
    critique_path = directory / "critique" / "story-S1.md"
    critique_path.parent.mkdir()
    critique_path.write_text(
        "---\n"
        "target: story\n"
        "target_id: S1\n"
        "severity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "harness: test-reviewer\n"
        "findings: []\n"
        "---\n"
    )
    _write_disposition(directory, 34)
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print('O1')\n")
    gate = directory / "gate.md"
    gate.write_text(
        "---\n"
        "type: story_gate\n"
        "story_id: S1\n"
        "triggered_by:\n"
        "  - check_7_commit_transaction\n"
        "---\n"
        "Operator fixed the staged transaction.\n"
    )

    proc = _run_woof(tmp_path, "wf", "--epic", "34", "--resolve", "approve")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "woof wf: gate resolved decision=approve\n"
    assert not gate.exists()
    assert (directory / "check-result.json").exists()
    assert (directory / "executor_result.json").exists()
    assert next_node(tmp_path, 34) == (NodeType.COMMIT, "S1")


def test_wf_resolve_revise_plan_reenters_breakdown(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_spark(tmp_path, 31)
    _write_minimal_epic(directory, 31)
    _write_stage3_plan(directory, 31)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 31)))
    _write_plan_critique(directory, "info")
    _write_last_sync(directory, 31)
    (directory / "epic.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "definition_closed",
                        "at": "2026-01-01T00:00:00Z",
                        "epic_id": 31,
                    }
                ),
                json.dumps(
                    {
                        "event": "readiness_passed",
                        "at": "2026-01-01T00:00:00Z",
                        "epic_id": 31,
                    }
                ),
                json.dumps(
                    {
                        "event": "breakdown_planned",
                        "at": "2026-01-01T00:00:01Z",
                        "epic_id": 31,
                    }
                ),
                json.dumps(
                    {
                        "event": "plan_critiqued",
                        "at": "2026-01-01T00:00:02Z",
                        "epic_id": 31,
                    }
                ),
            ]
        )
        + "\n"
    )
    (directory / "gate.md").write_text(
        "---\n"
        "type: plan_gate\n"
        "stage: 4\n"
        "story_id: null\n"
        "triggered_by: [plan_review]\n"
        "timestamp: '2026-01-01T00:00:03Z'\n"
        "---\n"
        "## Context\n\nPlan gate.\n\n"
        "## Findings\n\n- revise\n\n"
        "## Primary position\n\nRevise plan.\n\n"
        "## Reviewer position\n\nReviewer agrees.\n"
    )
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "31", "--resolve", "revise_plan", env=env)

    assert proc.returncode == 0, proc.stderr
    assert not (directory / "gate.md").exists()
    assert not (directory / "plan.json").exists()
    assert not (directory / "PLAN.md").exists()
    assert not (directory / "critique" / "plan.md").exists()
    assert next_node(tmp_path, 31) == (NodeType.BREAKDOWN_PLANNING, None)


def test_wf_resolve_approve_clears_stale_failed_check_result(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_plan(tmp_path, 32)
    _write_last_sync(directory, 32)
    mark_story_status(tmp_path, 32, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 32,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, 32, "S1")
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 32,
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
    )
    (directory / "gate.md").write_text(
        "---\n"
        "type: story_gate\n"
        "stage: 6\n"
        "story_id: S1\n"
        "triggered_by: [check_1_quality_gates]\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "---\n"
        "## Context\n\nCheck gate.\n\n"
        "## Findings\n\n- failed\n\n"
        "## Primary position\n\nFix applied.\n\n"
        "## Reviewer position\n\nChecks should rerun.\n"
    )
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "32", "--resolve", "approve", env=env)

    assert proc.returncode == 0, proc.stderr
    assert not (directory / "check-result.json").exists()
    assert next_node(tmp_path, 32) == (NodeType.VERIFICATION, "S1")


def test_wf_resolve_revise_story_scope_clears_stale_failed_check_result(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_plan(tmp_path, 33)
    _write_last_sync(directory, 33)
    mark_story_status(tmp_path, 33, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 33,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 33,
                "story_id": "S1",
                "triggered_by": ["check_3_scope"],
                "checks": [],
            }
        )
    )
    (directory / "gate.md").write_text(
        "---\n"
        "type: story_gate\n"
        "stage: 6\n"
        "story_id: S1\n"
        "triggered_by: [check_3_scope]\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "---\n"
        "## Context\n\nScope gate.\n\n"
        "## Findings\n\n- split\n\n"
        "## Primary position\n\nSplit story.\n\n"
        "## Reviewer position\n\nRerun checks.\n"
    )
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "33", "--resolve", "revise_story_scope", env=env)

    assert proc.returncode == 0, proc.stderr
    assert not (directory / "check-result.json").exists()


def test_wf_resolve_abandon_story_skips_to_next_ready_story(tmp_path: Path) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_plan(tmp_path, 34)
    _write_last_sync(directory, 34)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"] = [
        {**plan["work_units"][0], "id": "S1", "status": "in_progress"},
        {**plan["work_units"][0], "id": "S2", "title": "second", "status": "pending"},
    ]
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 34,
                "story_id": "S1",
                "outcome": "aborted_with_position",
                "position": "Cannot continue.",
            }
        )
    )
    (directory / "gate.md").write_text(
        "---\n"
        "type: story_gate\n"
        "stage: 6\n"
        "story_id: S1\n"
        "triggered_by: [executor_aborted]\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "---\n"
        "## Context\n\nAbort gate.\n\n"
        "## Findings\n\n- abandon\n\n"
        "## Primary position\n\nAbandon story.\n\n"
        "## Reviewer position\n\nContinue.\n"
    )
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "34", "--resolve", "abandon_story", env=env)

    assert proc.returncode == 0, proc.stderr
    plan_after = json.loads((directory / "plan.json").read_text())
    # abandon_story is now honest: the story is terminal-abandoned, not done.
    assert plan_after["work_units"][0]["status"] == "abandoned"
    events = [
        json.loads(line)
        for line in (directory / "epic.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert any(e["event"] == "story_abandoned" and e["story_id"] == "S1" for e in events)
    assert not any(e["event"] == "story_completed" for e in events)
    # The abandoned story is skipped; the graph advances to the next ready story.
    assert next_node(tmp_path, 34) == (NodeType.EXECUTOR_DISPATCH, "S2")


def test_wf_resolve_retry_story_resets_and_re_dispatches_without_redoing_siblings(
    tmp_path: Path,
) -> None:
    _write_tracker_prerequisites(tmp_path)
    directory = _write_plan(tmp_path, 36)
    _write_last_sync(directory, 36)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"] = [
        {**plan["work_units"][0], "id": "S1", "status": "done"},
        {**plan["work_units"][0], "id": "S2", "title": "second", "status": "in_progress"},
    ]
    (directory / "plan.json").write_text(json.dumps(plan))
    # S2 crashed mid-execution, leaving stale executor/check/critique/disposition state.
    (directory / "executor_result.json").write_text(
        json.dumps({"epic_id": 36, "story_id": "S2", "outcome": "aborted_with_position"})
    )
    (directory / "check-result.json").write_text(json.dumps({"ok": False}))
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    for story_id in ("S1", "S2"):
        (critique_dir / f"story-{story_id}.md").write_text(
            f"---\ntarget: story\ntarget_id: {story_id}\nseverity: info\n"
            "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\nfindings: []\n---\n"
        )
    sibling_disposition = _write_disposition(directory, 36, "S1")
    target_disposition = _write_disposition(directory, 36, "S2")
    (directory / "gate.md").write_text(
        "---\n"
        "type: review_gate\n"
        "stage: 6\n"
        "story_id: S2\n"
        "triggered_by: [executor_aborted]\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "---\n"
        "## Context\n\nCrash gate.\n\n"
        "## Findings\n\n- retry\n\n"
        "## Primary position\n\nRetry story.\n\n"
        "## Reviewer position\n\nRe-run from scratch.\n"
    )
    env = _make_gh_rate_limit_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "36", "--resolve", "retry_story", env=env)

    assert proc.returncode == 0, proc.stderr
    by_id = {s["id"]: s for s in json.loads((directory / "plan.json").read_text())["work_units"]}
    assert by_id["S2"]["status"] == "pending"  # the crashed story is reset
    assert by_id["S1"]["status"] == "done"  # the done sibling is untouched
    # The crashed story's artefacts are cleared; the sibling's survive.
    assert not (directory / "executor_result.json").exists()
    assert not (directory / "check-result.json").exists()
    assert not (critique_dir / "story-S2.md").exists()
    assert not target_disposition.exists()
    assert (critique_dir / "story-S1.md").exists()
    assert sibling_disposition.exists()
    assert not (directory / "gate.md").exists()
    events = [
        json.loads(line)
        for line in (directory / "epic.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert any(e["event"] == "story_retried" and e["story_id"] == "S2" for e in events)
    # next_node re-dispatches the reset story, not the done sibling.
    assert next_node(tmp_path, 36) == (NodeType.EXECUTOR_DISPATCH, "S2")


class _RecordingTracker:
    """Stub tracker that records the abandon_epic close_not_delivered call.

    abandon_epic is the only gate verb that touches the tracker here; every other
    method is unused, so the stub need not implement them (it is cast to Tracker).
    """

    def __init__(self) -> None:
        self.closed_not_delivered: list[int] = []

    def close_not_delivered(self, epic_id: int) -> LifecycleSyncResult:
        self.closed_not_delivered.append(epic_id)
        return LifecycleSyncResult(
            epic_id=epic_id,
            body="",
            updated_at="2026-01-01T00:00:00Z",
            last_sync_path=Path(f".woof/epics/E{epic_id}/.last-sync"),
            changed=True,
            closed=True,
        )


def _write_story_gate(directory: Path, story_id: str, *, gate_type: str = "story_gate") -> None:
    (directory / "gate.md").write_text(
        f"---\n"
        f"type: {gate_type}\n"
        "stage: 6\n"
        f"story_id: {story_id}\n"
        "triggered_by: [executor_aborted]\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "---\n"
        "## Context\n\nAbort gate.\n\n"
        "## Findings\n\n- abandon\n\n"
        "## Primary position\n\nAbandon.\n\n"
        "## Reviewer position\n\nStop.\n"
    )


def test_wf_resolve_abandon_epic_closes_tracker_and_is_terminal(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 40)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"] = [
        {**plan["work_units"][0], "id": "S1", "status": "in_progress"},
        {**plan["work_units"][0], "id": "S2", "title": "second", "status": "pending"},
    ]
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 40,
                "story_id": "S1",
                "outcome": "aborted_with_position",
                "position": "Cannot continue.",
            }
        )
    )
    _write_story_gate(directory, "S1")
    tracker = _RecordingTracker()

    rc = _resolve_gate(tmp_path, 40, "abandon_epic", cast(Tracker, tracker))

    assert rc == 0
    assert not (directory / "gate.md").exists()
    # The tracker issue is closed as not delivered.
    assert tracker.closed_not_delivered == [40]

    events = [
        json.loads(line)
        for line in (directory / "epic.jsonl").read_text().splitlines()
        if line.strip()
    ]
    # The graph-owned terminal marker is written; the epic is not "completed".
    assert any(e["event"] == "epic_abandoned" and e["epic_id"] == 40 for e in events)
    assert not any(e["event"] == "epic_completed" for e in events)
    # abandon_epic abandons the whole epic; it does not selectively complete or
    # abandon the targeted story, which stays as it was.
    plan_after = json.loads((directory / "plan.json").read_text())
    assert plan_after["work_units"][0]["status"] == "in_progress"

    # next_node returns the abandoned-terminal outcome, distinct from EPIC_COMPLETE.
    assert transitions.epic_abandoned(tmp_path, 40) is True
    assert next_node(tmp_path, 40) == (NodeStatus.EPIC_ABANDONED, None)
    outputs = run_graph(tmp_path, 40)
    assert outputs[-1].status == NodeStatus.EPIC_ABANDONED
    assert outputs[-1].status != NodeStatus.EPIC_COMPLETE


def test_abandon_epic_keeps_gate_when_tracker_close_fails(tmp_path: Path) -> None:
    # The tracker close runs before the epic_abandoned marker: if it fails, the
    # gate stays open and the epic is never marked abandoned.
    directory = _write_plan(tmp_path, 44)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"][0]["status"] = "in_progress"
    (directory / "plan.json").write_text(json.dumps(plan))
    _write_story_gate(directory, "S1")

    class _FailingTracker:
        def close_not_delivered(self, epic_id: int) -> LifecycleSyncResult:
            from woof.trackers.base import TrackerError

            raise TrackerError("remote unreachable")

    rc = _resolve_gate(tmp_path, 44, "abandon_epic", cast(Tracker, _FailingTracker()))

    assert rc == 2
    assert (directory / "gate.md").exists()
    assert transitions.epic_abandoned(tmp_path, 44) is False
    events_path = directory / "epic.jsonl"
    events = (
        [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
        if events_path.exists()
        else []
    )
    assert not any(e.get("event") == "epic_abandoned" for e in events)


def test_reconstruction_distinguishes_abandoned_story_from_done(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 41)
    plan = json.loads((directory / "plan.json").read_text())
    plan["work_units"] = [
        {**plan["work_units"][0], "id": "S1", "status": "done"},
        {**plan["work_units"][0], "id": "S2", "title": "second", "status": "abandoned"},
    ]
    (directory / "plan.json").write_text(json.dumps(plan))

    # The plan reloads with distinct statuses: abandoned is not coerced to done.
    reloaded = transitions.load_plan(tmp_path, 41)
    assert {s.id: s.status for s in reloaded.work_units} == {"S1": "done", "S2": "abandoned"}

    # Every story is terminal (one done, one abandoned) and there is no
    # epic_abandoned marker: the epic completes - the abandoned story neither
    # strands it nor turns it into the abandoned-epic terminal.
    assert transitions.epic_abandoned(tmp_path, 41) is False
    assert next_node(tmp_path, 41) == (None, None)
    outputs = run_graph(tmp_path, 41)
    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE


def test_transaction_manifest_requires_audit_and_rejects_extra_staged_file(tmp_path: Path) -> None:
    _git(tmp_path, "init", check=True, capture_output=True)
    _git(tmp_path, "config", "user.email", "test@example.com", check=True)
    _git(tmp_path, "config", "user.name", "Test", check=True)
    directory = _write_plan(tmp_path, 1)
    (directory / "dispatch.jsonl").write_text("{}\n")
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, 1, "S1")
    audit_dir = directory / "audit"
    audit_dir.mkdir()
    (audit_dir / "cod-critiquer-1.prompt").write_text("prompt")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print('O1')\n")
    (tmp_path / "extra.txt").write_text("not in story scope\n")

    story = WorkUnitSpec(
        id="S1",
        title="first",
        paths=["src/*.py"],
        satisfies=["O1"],
        status="in_progress",
    )
    manifest = build_story_manifest(tmp_path, 1, story)

    assert ".woof/epics/E1/audit/cod-critiquer-1.prompt" in manifest.expected_paths
    assert ".woof/epics/E1/critique/story-S1.md" in manifest.expected_paths
    assert ".woof/epics/E1/dispositions/story-S1.md" in manifest.expected_paths
    assert "src/app.py" in manifest.expected_paths

    _git(tmp_path, "add", "--", *manifest.expected_paths, "extra.txt", check=True)
    result = verify_staged_manifest(tmp_path, manifest)

    assert result.ok is False
    assert result.extra_paths == ["extra.txt"]


def test_transaction_manifest_reports_missing_expected_index_paths(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_plan(tmp_path, 16)
    (directory / "dispatch.jsonl").write_text("{}\n")
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, 16, "S1")
    audit_dir = directory / "audit"
    audit_dir.mkdir()
    (audit_dir / "cod-critiquer-1.prompt").write_text("prompt")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print('O1')\n")

    story = WorkUnitSpec(
        id="S1",
        title="first",
        paths=["src/*.py"],
        satisfies=["O1"],
        status="in_progress",
    )
    manifest = build_story_manifest(tmp_path, 16, story)
    staged_subset = [
        path for path in manifest.expected_paths if not path.endswith("dispatch.jsonl")
    ]
    _git(tmp_path, "add", "--", *staged_subset, check=True)

    result = verify_staged_manifest(tmp_path, manifest)

    assert result.ok is False
    assert result.missing_paths == [".woof/epics/E16/dispatch.jsonl"]


def test_transaction_manifest_excludes_committed_prior_epic_artifacts(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_plan(tmp_path, 17)
    (directory / "EPIC.md").write_text("---\nepic_id: 17\n---\n")
    (directory / "PLAN.md").write_text("# Plan\n")
    (directory / "dispatch.jsonl").write_text("{}\n")
    (directory / "spark.md").write_text("initial spark\n")
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, 17, "S1")
    audit_dir = directory / "audit"
    audit_dir.mkdir()
    (audit_dir / "old-story.prompt").write_text("old prompt")
    _git(tmp_path, "add", ".woof", check=True)
    _git(tmp_path, "commit", "-m", "feat: first story", check=True)

    (directory / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": 17,
                "goal": "test graph",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "first",
                        "summary": "done",
                        "paths": ["src/*.py"],
                        "satisfies": ["O1"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "status": "done",
                    },
                    {
                        "id": "S2",
                        "title": "docs",
                        "summary": "document",
                        "paths": ["README.md"],
                        "satisfies": ["O2"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "deps": ["S1"],
                        "tests": {"count": 0, "types": ["documentation", "manual"]},
                        "status": "in_progress",
                    },
                ],
            }
        )
    )
    (directory / "epic.jsonl").write_text("{}\n{}\n")
    (critique_dir / "story-S2.md").write_text(
        "---\ntarget: story\ntarget_id: S2\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, 17, "S2")
    (audit_dir / "new-story.prompt").write_text("new prompt")
    (tmp_path / "README.md").write_text("manual story docs\n")

    story = WorkUnitSpec(
        id="S2",
        title="docs",
        paths=["README.md"],
        satisfies=["O2"],
        status="in_progress",
        tests={"count": 0, "types": ["documentation", "manual"]},
    )
    manifest = build_story_manifest(tmp_path, 17, story)

    assert ".woof/epics/E17/critique/story-S1.md" not in manifest.expected_paths
    assert ".woof/epics/E17/audit/old-story.prompt" not in manifest.expected_paths
    assert ".woof/epics/E17/spark.md" not in manifest.expected_paths
    assert ".woof/epics/E17/critique/story-S2.md" in manifest.expected_paths
    assert ".woof/epics/E17/audit/new-story.prompt" in manifest.expected_paths
    assert "README.md" in manifest.expected_paths


def test_transaction_manifest_honours_recursive_pathspec(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_plan(tmp_path, 23)
    (directory / "dispatch.jsonl").write_text("{}\n")
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text(
        "---\ntarget: story\ntarget_id: S1\nseverity: info\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings: []\n---\n"
    )
    _write_disposition(directory, 23, "S1")
    nested = tmp_path / "src" / "pkg" / "subpkg"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text("print('O1')\n")

    story = WorkUnitSpec(
        id="S1",
        title="first",
        paths=[":(glob)src/**/*.py"],
        satisfies=["O1"],
        status="in_progress",
    )
    manifest = build_story_manifest(tmp_path, 23, story)

    assert "src/pkg/subpkg/deep.py" in manifest.story_paths
    assert "src/pkg/subpkg/deep.py" in manifest.expected_paths


# --- revise_epic_contract re-opens definition with evidence (E17 P5 / D-RC) -----


def _seed_pending_contract_revision(directory: Path, epic_id: int) -> dict[str, Path]:
    """Lay down the on-disk state left by a resolved revise_epic_contract.

    The prior EPIC.md is archived under definition/ and its findings snapshot sits
    beside it; the event log records definition_closed then a revise_epic_contract
    gate resolution with no later definition_closed, so definition_revision_requested
    is True and next_node re-enters definition.
    """

    definition_dir = directory / "definition"
    definition_dir.mkdir(exist_ok=True)
    archived = definition_dir / "EPIC.1.archived.md"
    archived.write_text(
        f"---\nepic_id: {epic_id}\ntitle: prior contract\n---\n\nThe prior contract body.\n"
    )
    findings = definition_dir / "EPIC.1.findings.md"
    findings.write_text("## Findings\n\n- O1 lacks a machine-checkable signal.\n")
    (directory / "epic.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {"event": "definition_closed", "at": "2026-01-01T00:00:00Z", "epic_id": epic_id}
                ),
                json.dumps(
                    {
                        "event": "gate_resolved",
                        "at": "2026-01-01T00:00:01Z",
                        "epic_id": epic_id,
                        "decision": "revise_epic_contract",
                        "gate_type": "plan_gate",
                    }
                ),
            ]
        )
        + "\n"
    )
    return {"archived": archived, "findings": findings}


def test_epic_definition_payload_declares_prior_contract_revision_inputs(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 70)
    _write_discovery_synthesis(directory)
    _seed_pending_contract_revision(directory, 70)

    payload = nodes._epic_definition_payload(tmp_path, 70)

    assert payload["inputs"]["prior_epic_path"] == ".woof/epics/E70/definition/EPIC.1.archived.md"
    assert (
        payload["inputs"]["revision_findings_path"]
        == ".woof/epics/E70/definition/EPIC.1.findings.md"
    )
    # The revision-shaped payload stays valid against the planning-node-input schema.
    _assert_planning_node_input_schema(tmp_path, payload)


def test_epic_definition_node_redispatches_revision_with_prior_contract_and_findings(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 71)
    _write_discovery_synthesis(directory)
    _seed_pending_contract_revision(directory, 71)
    _write_codebase_docs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["prompt"] = prompt
        captured["artefacts_loaded"] = artefacts_loaded
        _write_minimal_epic(directory, epic_id)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    # next_node re-enters definition while the revision is pending and EPIC.md is
    # archived (absent), forbidding a hand-edit.
    assert not (directory / "EPIC.md").exists()
    assert next_node(tmp_path, 71) == (NodeType.EPIC_DEFINITION, None)

    output = nodes.epic_definition_node(
        NodeInput(node_type=NodeType.EPIC_DEFINITION, epic_id=71, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.COMPLETED, output.message
    # The re-dispatch declares the prior epic + findings as inputs and loads them as
    # artefacts, so the revision is evidence-driven.
    assert (
        '"prior_epic_path": ".woof/epics/E71/definition/EPIC.1.archived.md"' in captured["prompt"]
    )
    assert (
        '"revision_findings_path": ".woof/epics/E71/definition/EPIC.1.findings.md"'
        in captured["prompt"]
    )
    assert ".woof/epics/E71/definition/EPIC.1.archived.md" in captured["artefacts_loaded"]
    assert ".woof/epics/E71/definition/EPIC.1.findings.md" in captured["artefacts_loaded"]
    # The node re-closes definition; the request clears and a fresh EPIC.md exists.
    assert (directory / "EPIC.md").exists()
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "definition_closed"
    assert transitions.definition_revision_requested(tmp_path, 71) is False


def test_epic_definition_node_redispatches_cold_start_revision_without_synthesis(
    tmp_path: Path, monkeypatch
) -> None:
    # A cold-start tracker epic: EPIC.md was authored directly with no
    # discovery/synthesis/*, so a pending revision archives the only contract and
    # leaves no synthesis inputs behind.
    directory = _write_spark(tmp_path, 72)
    _seed_pending_contract_revision(directory, 72)
    _write_codebase_docs(tmp_path)
    assert not nodes.discovery_synthesis_complete(tmp_path, 72)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["prompt"] = prompt
        captured["artefacts_loaded"] = artefacts_loaded
        _write_minimal_epic(directory, epic_id)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    assert not (directory / "EPIC.md").exists()
    assert next_node(tmp_path, 72) == (NodeType.EPIC_DEFINITION, None)

    output = nodes.epic_definition_node(
        NodeInput(node_type=NodeType.EPIC_DEFINITION, epic_id=72, repo_root=tmp_path)
    )

    # The pending revision dispatches from the archived contract + findings instead
    # of halting on the absent (never-produced) synthesis inputs.
    assert output.status == NodeStatus.COMPLETED, output.message
    assert "synthesis inputs are missing" not in (output.message or "")
    assert "prompt" in captured, "dispatch did not run; the node halted on missing synthesis"
    assert (
        '"prior_epic_path": ".woof/epics/E72/definition/EPIC.1.archived.md"' in captured["prompt"]
    )
    assert (
        '"revision_findings_path": ".woof/epics/E72/definition/EPIC.1.findings.md"'
        in captured["prompt"]
    )
    assert ".woof/epics/E72/definition/EPIC.1.archived.md" in captured["artefacts_loaded"]
    assert ".woof/epics/E72/definition/EPIC.1.findings.md" in captured["artefacts_loaded"]
    assert transitions.definition_revision_requested(tmp_path, 72) is False


def test_epic_definition_node_halts_cold_discovery_without_revision(tmp_path: Path) -> None:
    # Regression: a genuine cold-discovery epic that has not produced synthesis and
    # has NO pending revision still halts on the missing-synthesis precondition.
    _write_spark(tmp_path, 74)

    output = nodes.epic_definition_node(
        NodeInput(node_type=NodeType.EPIC_DEFINITION, epic_id=74, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["incomplete_stage_state"]
    assert "Required Stage-2 synthesis inputs are missing" in output.message


def test_wf_resolve_revise_epic_contract_reenters_definition_from_plan_gate(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 73)
    _write_discovery_synthesis(directory)
    _write_minimal_epic(directory, 73)
    _write_stage3_plan(directory, 73)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 73)))
    _write_plan_critique(directory, "info")
    (directory / "epic.jsonl").write_text(
        "\n".join(
            json.dumps({"event": event, "at": f"2026-01-01T00:00:0{i}Z", "epic_id": 73})
            for i, event in enumerate(
                ["definition_closed", "readiness_passed", "breakdown_planned", "plan_critiqued"]
            )
        )
        + "\n"
    )
    (directory / "gate.md").write_text(
        "---\n"
        "type: plan_gate\n"
        "stage: 4\n"
        "story_id: null\n"
        "triggered_by: [plan_review]\n"
        "timestamp: '2026-01-01T00:00:03Z'\n"
        "---\n"
        "## Context\n\nPlan gate.\n\n"
        "## Findings\n\n- The contract under-specifies O1.\n\n"
        "## Primary position\n\nRevise the contract.\n\n"
        "## Reviewer position\n\nReviewer agrees.\n"
    )

    rc = _resolve_gate(tmp_path, 73, "revise_epic_contract", cast(Tracker, _RecordingTracker()))

    assert rc == 0
    assert not (directory / "gate.md").exists()
    # The prior EPIC.md is archived (not hand-editable in place) with its findings.
    assert not (directory / "EPIC.md").exists()
    archived = directory / "definition" / "EPIC.1.archived.md"
    findings = directory / "definition" / "EPIC.1.findings.md"
    assert archived.exists()
    assert "under-specifies O1" in findings.read_text()
    # The stale plan artefacts are cleared.
    assert not (directory / "plan.json").exists()
    assert not (directory / "PLAN.md").exists()
    assert not (directory / "critique" / "plan.md").exists()
    # The resolution is audited and re-enters definition rather than just deleting
    # plan files.
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    resolved = [e for e in events if e["event"] == "gate_resolved"]
    assert resolved and resolved[-1]["decision"] == "revise_epic_contract"
    assert transitions.definition_revision_requested(tmp_path, 73) is True


# ---------------------------------------------------------------------------
# E19 S1 R1 — stage-state halt resolution is non-approving (P1 regression)
# ---------------------------------------------------------------------------


def test_plan_gate_resolved_false_for_stage_state_halt_approve(tmp_path: Path) -> None:
    """An 'approve' on a cartography halt plan_gate must not satisfy the mandatory plan gate."""
    _write_plan(tmp_path, 74)
    # Simulate operator approving the cartography-halt gate with incomplete_stage_state.
    append_epic_event(
        tmp_path,
        74,
        {
            "event": "gate_resolved",
            "at": "2026-01-01T00:00:00Z",
            "epic_id": 74,
            "decision": "approve",
            "gate_type": "plan_gate",
            "triggered_by": ["incomplete_stage_state"],
        },
    )
    assert plan_gate_resolved(tmp_path, 74) is False


def test_story_gate_stage_state_halt_approve_leaves_story_pending(tmp_path: Path) -> None:
    """An 'approve' on a cartography halt story_gate must not mark the story done."""
    _write_plan(tmp_path, 75)
    # Simulate operator approving the cartography-halt gate with incomplete_stage_state.
    append_epic_event(
        tmp_path,
        75,
        {
            "event": "gate_resolved",
            "at": "2026-01-01T00:00:00Z",
            "epic_id": 75,
            "decision": "approve",
            "gate_type": "story_gate",
            "story_id": "S1",
            "triggered_by": ["incomplete_stage_state"],
        },
    )
    plan = transitions.load_plan(tmp_path, 75)
    story = next(s for s in plan.work_units if s.id == "S1")
    assert story.status == "pending"


# ---------------------------------------------------------------------------
# E19 S1 R2 — specific *_gate_resolved events not emitted for stage-state halts
# ---------------------------------------------------------------------------


def test_plan_gate_resolved_false_for_specific_event_with_stage_state_trigger(
    tmp_path: Path,
) -> None:
    """Exact Codex repro: both specific and generic events written; plan_gate_resolved still False."""
    _write_plan(tmp_path, 76)
    # Write the specific plan_gate_resolved event (pre-R2 source behaviour) with non-approving trigger.
    append_epic_event(
        tmp_path,
        76,
        {
            "event": "plan_gate_resolved",
            "at": "2026-01-01T00:00:00Z",
            "epic_id": 76,
            "decision": "approve",
            "gate_type": "plan_gate",
            "triggered_by": ["incomplete_stage_state"],
        },
    )
    # Also write the generic event (as _resolve_gate always does).
    append_epic_event(
        tmp_path,
        76,
        {
            "event": "gate_resolved",
            "at": "2026-01-01T00:00:00Z",
            "epic_id": 76,
            "decision": "approve",
            "gate_type": "plan_gate",
            "triggered_by": ["incomplete_stage_state"],
        },
    )
    assert plan_gate_resolved(tmp_path, 76) is False


def test_plan_gate_resolved_true_for_genuine_approval(tmp_path: Path) -> None:
    """A genuine plan-gate approval (no non-approving trigger) must still return True."""
    _write_plan(tmp_path, 77)
    append_epic_event(
        tmp_path,
        77,
        {
            "event": "plan_gate_resolved",
            "at": "2026-01-01T00:00:00Z",
            "epic_id": 77,
            "decision": "approve",
            "gate_type": "plan_gate",
            "triggered_by": [],
        },
    )
    append_epic_event(
        tmp_path,
        77,
        {
            "event": "gate_resolved",
            "at": "2026-01-01T00:00:00Z",
            "epic_id": 77,
            "decision": "approve",
            "gate_type": "plan_gate",
            "triggered_by": [],
        },
    )
    assert plan_gate_resolved(tmp_path, 77) is True


def test_readiness_satisfied_false_for_stage_state_specific_event(tmp_path: Path) -> None:
    """A readiness_gate_resolved event with a non-approving trigger must not satisfy readiness."""
    _write_plan(tmp_path, 78)
    # Need a definition_closed event for readiness_satisfied to scan past it.
    append_epic_event(
        tmp_path,
        78,
        {"event": "definition_closed", "at": "2026-01-01T00:00:00Z", "epic_id": 78},
    )
    # Write the specific readiness_gate_resolved event with a non-approving trigger.
    append_epic_event(
        tmp_path,
        78,
        {
            "event": "readiness_gate_resolved",
            "at": "2026-01-01T00:00:00Z",
            "epic_id": 78,
            "decision": "approve_with_reason",
            "gate_type": "readiness_gate",
            "triggered_by": ["incomplete_stage_state"],
        },
    )
    assert readiness_satisfied(tmp_path, 78) is False


def test_readiness_satisfied_true_for_genuine_approval(tmp_path: Path) -> None:
    """A genuine readiness approval (no non-approving trigger) must still satisfy readiness."""
    _write_plan(tmp_path, 79)
    append_epic_event(
        tmp_path,
        79,
        {"event": "definition_closed", "at": "2026-01-01T00:00:00Z", "epic_id": 79},
    )
    append_epic_event(
        tmp_path,
        79,
        {
            "event": "readiness_gate_resolved",
            "at": "2026-01-01T00:00:00Z",
            "epic_id": 79,
            "decision": "approve_with_reason",
            "gate_type": "readiness_gate",
            "triggered_by": [],
        },
    )
    assert readiness_satisfied(tmp_path, 79) is True
