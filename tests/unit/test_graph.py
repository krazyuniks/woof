from __future__ import annotations

import json
import subprocess
from pathlib import Path

from woof.graph.manifest import build_story_manifest, verify_staged_manifest
from woof.graph.runner import run_graph
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, StorySpec
from woof.graph.transitions import epic_dir, mark_story_status

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
