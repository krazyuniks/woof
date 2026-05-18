from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from woof.graph import nodes
from woof.graph.git import git_env
from woof.graph.manifest import build_story_manifest, verify_staged_manifest
from woof.graph.runner import run_graph
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, StorySpec
from woof.graph.transitions import StageStateError, epic_dir, mark_story_status, next_node

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


def _write_plan(root: Path, epic_id: int = 1) -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True)
    plan = {
        "epic_id": epic_id,
        "goal": "test graph",
        "stories": [
            {
                "id": "S1",
                "title": "first",
                "intent": "do work",
                "paths": ["src/*.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "depends_on": [],
                "tests": {"count": 1, "types": ["unit"]},
                "status": "pending",
            }
        ],
    }
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "epic.jsonl").write_text("")
    return directory


def _write_spark(root: Path, epic_id: int = 1) -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spark.md").write_text("Build a useful thing.\n")
    (directory / "epic.jsonl").write_text("")
    return directory


def _write_discovery_synthesis(directory: Path) -> None:
    synthesis = directory / "discovery" / "synthesis"
    synthesis.mkdir(parents=True, exist_ok=True)
    for name in ("CONCEPT.md", "PRINCIPLES.md", "ARCHITECTURE.md", "OPEN_QUESTIONS.md"):
        (synthesis / name).write_text(f"# {name}\n\nFilled.\n")


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


def _read_gate_fm(gate_path: Path) -> dict:
    text = gate_path.read_text()
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
  - Outcome verified.
---
Test epic intent.
"""
    )


def _write_stage3_plan(directory: Path, epic_id: int) -> None:
    plan = {
        "epic_id": epic_id,
        "goal": "Implement the test epic.",
        "stories": [
            {
                "id": "S1",
                "title": "Build the first surface",
                "intent": "Create the first observable surface.",
                "paths": ["src/*.py", "tests/*.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "depends_on": [],
                "tests": {"count": 1, "types": ["unit"]},
                "status": "pending",
            }
        ],
    }
    (directory / "plan.json").write_text(json.dumps(plan))


def _write_plan_critique(directory: Path, severity: str = "minor") -> None:
    critique_dir = directory / "critique"
    critique_dir.mkdir(exist_ok=True)
    findings = (
        f"findings:\n  - id: F1\n    severity: {severity}\n    summary: tighten story scope\n"
        if severity != "info"
        else "findings: []\n"
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


def _write_ready_commit_state(root: Path, epic_id: int = 1) -> Path:
    directory = _write_plan(root, epic_id)
    plan = json.loads((directory / "plan.json").read_text())
    plan["stories"][0]["status"] = "done"
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "dispatch.jsonl").write_text("{}\n")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": epic_id,
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


def test_dispatch_helper_uses_role_route_without_provider_target(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["cwd"] = cwd
        captured["capture_output"] = capture_output
        captured["text"] = text
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(nodes.subprocess, "run", fake_run)

    nodes._run_dispatch(
        tmp_path,
        role="primary",
        epic_id=1,
        story_id="S1",
        prompt="do work",
    )

    args = captured["args"]
    assert args[1:4] == ["dispatch", "--role", "primary"]
    assert "claude" not in args[1:4]
    assert "codex" not in args[1:4]
    assert captured["cwd"] == tmp_path


def test_pre_plan_transition_enters_discovery_when_spark_exists(tmp_path: Path) -> None:
    _write_spark(tmp_path, 21)

    assert next_node(tmp_path, 21) == (NodeType.DISCOVERY_SYNTHESIS, None)


def test_discovery_synthesis_node_dispatches_primary_and_validates_outputs(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 22)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        captured["repo_root"] = repo_root
        captured["role"] = role
        captured["epic_id"] = epic_id
        captured["story_id"] = story_id
        captured["prompt"] = prompt
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
    assert "The graph validates the files and selects the next node." in captured["prompt"]
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


def test_epic_definition_node_dispatches_primary_validates_epic_and_continues(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 24)
    _write_discovery_synthesis(directory)
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        captured["repo_root"] = repo_root
        captured["role"] = role
        captured["epic_id"] = epic_id
        captured["story_id"] = story_id
        captured["prompt"] = prompt
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
    _assert_planning_node_input_schema(
        tmp_path,
        nodes._epic_definition_payload(tmp_path, 24),
    )
    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.BREAKDOWN_PLANNING
    assert output.validation_summary and output.validation_summary.stage == 2
    assert output.validation_summary.ok is True
    assert output.paths == [".woof/epics/E24/EPIC.md"]
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "definition_closed"
    _assert_node_output_schema(tmp_path, json.loads(output.model_dump_json()))


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
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        captured["repo_root"] = repo_root
        captured["role"] = role
        captured["epic_id"] = epic_id
        captured["story_id"] = story_id
        captured["prompt"] = prompt
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


def test_plan_critique_node_dispatches_reviewer_validates_critique_and_halts(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 27)
    _write_minimal_epic(directory, 27)
    _write_stage3_plan(directory, 27)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 27)))
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        captured["repo_root"] = repo_root
        captured["role"] = role
        captured["epic_id"] = epic_id
        captured["story_id"] = story_id
        captured["prompt"] = prompt
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
    _assert_planning_node_input_schema(
        tmp_path,
        nodes._plan_critique_payload(tmp_path, 27),
    )
    assert output.status == NodeStatus.HALTED
    assert output.next_node == NodeType.PLAN_GATE_OPEN
    assert output.validation_summary and output.validation_summary.stage == 3
    assert output.paths == [".woof/epics/E27/critique/plan.md"]
    assert "plan gate node is not implemented yet" in output.message
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "plan_critiqued"
    assert events[-1]["severity"] == "minor"
    assert events[-1]["finding_count"] == 1
    _assert_node_output_schema(tmp_path, json.loads(output.model_dump_json()))


def test_graph_runs_discovery_definition_breakdown_until_plan_gate_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    directory = _write_spark(tmp_path, 28)

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        assert repo_root == tmp_path
        assert epic_id == 28
        assert story_id is None
        if '"node_type": "discovery_synthesis"' in prompt:
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
        NodeType.DISCOVERY_SYNTHESIS,
        NodeType.EPIC_DEFINITION,
        NodeType.BREAKDOWN_PLANNING,
        NodeType.PLAN_CRITIQUE,
    ]
    assert outputs[-1].status == NodeStatus.HALTED
    assert outputs[-1].next_node == NodeType.PLAN_GATE_OPEN


def test_critique_dispatch_failure_opens_reviewer_gate(tmp_path: Path, monkeypatch) -> None:
    _write_plan(tmp_path, 1)

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        assert repo_root == tmp_path
        assert role == "reviewer"
        assert epic_id == 1
        assert story_id == "S1"
        assert prompt
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


def test_review_disposition_dispatches_primary_for_non_blocking_critique(
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
        "---\ntarget: story\ntarget_id: S1\nseverity: minor\n"
        "timestamp: '2026-01-01T00:00:00Z'\nharness: test-reviewer\n"
        "findings:\n  - id: F1\n    severity: minor\n    summary: needs note\n---\n"
    )

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        assert repo_root == tmp_path
        assert role == "primary"
        assert epic_id == 1
        assert story_id == "S1"
        assert "dispositions/story-S1.md" in prompt
        disposition_dir = directory / "dispositions"
        disposition_dir.mkdir()
        (disposition_dir / "story-S1.md").write_text(
            "---\ntarget: story\ntarget_id: S1\n"
            "critique_path: .woof/epics/E1/critique/story-S1.md\n"
            "severity: minor\n"
            "timestamp: '2026-01-01T00:00:00Z'\nharness: test-primary\n"
            "dispositions:\n"
            "  - finding_id: F1\n"
            "    decision: accepted\n"
            "    rationale: Added a note.\n"
            "---\n"
        )
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

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
        "findings:\n  - id: F1\n    severity: blocker\n    summary: test\n---\n"
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
    plan["stories"] = [
        {
            **plan["stories"][0],
            "id": "S1",
            "title": "first",
            "status": "done",
            "depends_on": [],
        },
        {
            **plan["stories"][0],
            "id": "S2",
            "title": "second",
            "status": "pending",
            "depends_on": ["S1"],
        },
    ]
    (directory / "plan.json").write_text(json.dumps(plan))

    assert next_node(tmp_path, 12) == (NodeType.EXECUTOR_DISPATCH, "S2")


def test_successor_selection_fails_loud_when_dependencies_are_unsatisfied(
    tmp_path: Path,
) -> None:
    directory = _write_plan(tmp_path, 13)
    plan = json.loads((directory / "plan.json").read_text())
    plan["stories"][0]["depends_on"] = ["S99"]
    (directory / "plan.json").write_text(json.dumps(plan))

    try:
        next_node(tmp_path, 13)
    except StageStateError as exc:
        assert "no story has satisfied dependencies" in str(exc)
    else:
        raise AssertionError("expected StageStateError")


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
    (tmp_path / ".woof" / "prerequisites.toml").write_text('[github]\nrepo = "acme/widgets"\n')
    directory = _write_plan(tmp_path, 7)
    plan = json.loads((directory / "plan.json").read_text())
    plan["stories"][0]["status"] = "done"
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


def test_wf_reports_missing_plan_as_structured_failure(tmp_path: Path) -> None:
    (tmp_path / ".woof" / "epics" / "E10").mkdir(parents=True)

    proc = _run_woof(tmp_path, "wf", "--epic", "10")

    assert proc.returncode == 2
    assert "woof wf: incomplete_stage_state:" in proc.stderr
    assert "required planning artefact missing" in proc.stderr
    assert "spark.md" in proc.stderr


def test_wf_epic_halts_when_gate_is_open(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 8)
    (directory / "gate.md").write_text("---\ntype: story_gate\n---\n")

    proc = _run_woof(tmp_path, "wf", "--epic", "8")

    assert proc.returncode == 0, proc.stderr
    assert "woof wf: human_review -> halted: gate open at .woof/epics/E8/gate.md" in proc.stdout


def test_wf_gate_case_reports_stable_json_contract(tmp_path: Path) -> None:
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
        "findings:\n  - id: F1\n    severity: blocker\n    summary: test\n---\n"
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

    proc = _run_woof(tmp_path, "wf", "--epic", "11", "--format", "json")

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
                "- F1: test\n\n"
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
    directory = _write_plan(tmp_path, 9)
    gate = directory / "gate.md"
    gate.write_text("---\ntype: story_gate\n---\n")

    proc = _run_woof(tmp_path, "wf", "--epic", "9", "--resolve", "approve")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "woof wf: gate resolved decision=approve\n"
    assert not gate.exists()
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "gate_resolved"
    assert events[-1]["epic_id"] == 9
    assert events[-1]["decision"] == "approve"


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

    story = StorySpec(
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

    story = StorySpec(
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
