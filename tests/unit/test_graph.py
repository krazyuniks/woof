from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from woof.graph.manifest import build_story_manifest, verify_staged_manifest
from woof.graph.runner import run_graph
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, StorySpec
from woof.graph.transitions import epic_dir, mark_story_status, next_node

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


def _run_woof(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _read_gate_fm(gate_path: Path) -> dict:
    text = gate_path.read_text()
    return yaml.safe_load(text[4 : text.find("\n---\n", 4)])


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
    (critique_dir / "story-S1.md").write_text("---\nseverity: info\n---\n")
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
            NodeType.VERIFICATION: verify,
            NodeType.COMMIT: commit,
        },
    )

    assert seen == [
        NodeType.EXECUTOR_DISPATCH,
        NodeType.CRITIQUE_DISPATCH,
        NodeType.VERIFICATION,
        NodeType.COMMIT,
    ]
    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE


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
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_complete_epic_cleans_stale_transient_files(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    subprocess.run(
        [
            "git",
            "add",
            "src/app.py",
            ".woof/epics/E1/plan.json",
            ".woof/epics/E1/epic.jsonl",
            ".woof/epics/E1/dispatch.jsonl",
            ".woof/epics/E1/critique/story-S1.md",
            ".woof/epics/E1/audit/cod-critiquer-1.prompt",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "seed"], cwd=tmp_path, check=True, capture_output=True)

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
    (critique_dir / "story-S1.md").write_text("---\nseverity: info\n---\n")
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
    (critique_dir / "story-S1.md").write_text("---\nseverity: blocker\n---\n")
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

    assert next_node(tmp_path, 1) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].triggered_by == ["check_6_critique_blocker"]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["check_6_critique_blocker"]


def test_wf_epic_reports_complete_epic_as_json(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 7)
    plan = json.loads((directory / "plan.json").read_text())
    plan["stories"][0]["status"] = "done"
    (directory / "plan.json").write_text(json.dumps(plan))

    proc = _run_woof(tmp_path, "wf", "--epic", "7", "--format", "json")

    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert lines == [
        {
            "node_type": "human_review",
            "status": "epic_complete",
            "epic_id": 7,
            "story_id": None,
            "next_node": None,
            "triggered_by": [],
            "message": "E7 complete",
            "paths": [],
        }
    ]


def test_wf_reports_missing_plan_as_structured_failure(tmp_path: Path) -> None:
    (tmp_path / ".woof" / "epics" / "E10").mkdir(parents=True)

    proc = _run_woof(tmp_path, "wf", "--epic", "10")

    assert proc.returncode == 2
    assert "woof wf: incomplete_stage_state:" in proc.stderr
    assert "plan.json" in proc.stderr


def test_wf_epic_halts_when_gate_is_open(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 8)
    (directory / "gate.md").write_text("---\ntype: story_gate\n---\n")

    proc = _run_woof(tmp_path, "wf", "--epic", "8")

    assert proc.returncode == 0, proc.stderr
    assert "woof wf: human_review -> halted: gate open at .woof/epics/E8/gate.md" in proc.stdout


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
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    directory = _write_plan(tmp_path, 1)
    (directory / "dispatch.jsonl").write_text("{}\n")
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text("---\nseverity: info\n---\n")
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
    assert "src/app.py" in manifest.expected_paths

    subprocess.run(
        ["git", "add", "--", *manifest.expected_paths, "extra.txt"], cwd=tmp_path, check=True
    )
    result = verify_staged_manifest(tmp_path, manifest)

    assert result.ok is False
    assert result.extra_paths == ["extra.txt"]
