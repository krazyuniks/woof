from __future__ import annotations

import json
import subprocess
from pathlib import Path

from woof.graph.manifest import build_story_manifest, verify_staged_manifest
from woof.graph.runner import run_graph
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, StorySpec
from woof.graph.transitions import epic_dir, mark_story_status


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
