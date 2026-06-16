"""Tests for E19 S1: per-node cartography payload wiring + incomplete_stage_state halt."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from woof.graph import nodes
from woof.graph.git import git_env
from woof.graph.state import NodeInput, NodeStatus, NodeType
from woof.graph.transitions import StageStateError


def _git(root: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(["git", *args], cwd=root, env=git_env(), **kwargs)


def _init_git_repo(root: Path) -> None:
    _git(root, "init", check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com", check=True)
    _git(root, "config", "user.name", "Test", check=True)


def _write_codebase_docs(root: Path, *, files_txt_content: str = "") -> None:
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
    (codebase_dir / "files.txt").write_text(files_txt_content)


def _write_spark(root: Path, epic_id: int = 1) -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spark.md").write_text("Build a useful thing.\n")
    (directory / "epic.jsonl").write_text("")
    return directory


def _write_plan(root: Path, epic_id: int = 1) -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True, exist_ok=True)
    plan = {
        "epic_id": epic_id,
        "goal": "test",
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


def _write_discovery_synthesis(directory: Path) -> None:
    synthesis = directory / "discovery" / "synthesis"
    synthesis.mkdir(parents=True, exist_ok=True)
    (synthesis / "CONCEPT.md").write_text(
        "# Concept\n\n## Problem Framing\n\nThe current workflow needs a useful thing.\n"
    )
    (synthesis / "PRINCIPLES.md").write_text("# Principles\n\nFilled.\n")
    (synthesis / "ARCHITECTURE.md").write_text("# Architecture\n\nFilled.\n")
    (synthesis / "OPEN_QUESTIONS.md").write_text("# Open Questions\n\nNo open questions.\n")


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


def _write_stage3_plan(directory: Path, epic_id: int) -> None:
    plan = {
        "epic_id": epic_id,
        "goal": "Implement the test epic.",
        "stories": [
            {
                "id": "S1",
                "title": "Build the first surface",
                "intent": "Create the first observable surface.",
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


# ---------------------------------------------------------------------------
# Missing-doc gate type tests
# ---------------------------------------------------------------------------


def test_discovery_research_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    _write_spark(tmp_path, 1)

    with pytest.raises(StageStateError) as exc_info:
        nodes.discovery_research_node(
            NodeInput(node_type=NodeType.DISCOVERY_RESEARCH, epic_id=1, repo_root=tmp_path)
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "plan_gate"
    assert exc.story_id is None
    assert "STACK.md" in str(exc)


def test_discovery_thinking_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 2)
    (directory / "discovery" / "research").mkdir(parents=True)
    (directory / "discovery" / "research" / "research.md").write_text("# Research\n\nDone.\n")

    with pytest.raises(StageStateError) as exc_info:
        nodes.discovery_thinking_node(
            NodeInput(node_type=NodeType.DISCOVERY_THINKING, epic_id=2, repo_root=tmp_path)
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "plan_gate"
    assert exc.story_id is None


def test_discovery_synthesis_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    _write_spark(tmp_path, 3)

    with pytest.raises(StageStateError) as exc_info:
        nodes.discovery_synthesis_node(
            NodeInput(node_type=NodeType.DISCOVERY_SYNTHESIS, epic_id=3, repo_root=tmp_path)
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "plan_gate"
    assert exc.story_id is None


def test_epic_definition_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 4)
    _write_discovery_synthesis(directory)

    with pytest.raises(StageStateError) as exc_info:
        nodes.epic_definition_node(
            NodeInput(node_type=NodeType.EPIC_DEFINITION, epic_id=4, repo_root=tmp_path)
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "plan_gate"
    assert exc.story_id is None


def test_breakdown_planning_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 5)
    _write_minimal_epic(directory, 5)

    with pytest.raises(StageStateError) as exc_info:
        nodes.breakdown_planning_node(
            NodeInput(node_type=NodeType.BREAKDOWN_PLANNING, epic_id=5, repo_root=tmp_path)
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "plan_gate"
    assert exc.story_id is None


def test_plan_critique_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 6)
    _write_minimal_epic(directory, 6)
    _write_stage3_plan(directory, 6)
    plan_md = nodes._render_plan_markdown(nodes.load_plan(tmp_path, 6))
    (directory / "PLAN.md").write_text(plan_md)

    with pytest.raises(StageStateError) as exc_info:
        nodes.plan_critique_node(
            NodeInput(node_type=NodeType.PLAN_CRITIQUE, epic_id=6, repo_root=tmp_path)
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "plan_gate"
    assert exc.story_id is None


def test_executor_dispatch_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    _write_plan(tmp_path, 7)

    with pytest.raises(StageStateError) as exc_info:
        nodes.executor_dispatch_node(
            NodeInput(
                node_type=NodeType.EXECUTOR_DISPATCH, epic_id=7, story_id="S1", repo_root=tmp_path
            )
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "story_gate"
    assert exc.story_id == "S1"


def test_critique_dispatch_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_plan(tmp_path, 8)
    (directory / "EPIC.md").write_text("---\nepic_id: 8\n---\n")

    with pytest.raises(StageStateError) as exc_info:
        nodes.critique_dispatch_node(
            NodeInput(
                node_type=NodeType.CRITIQUE_DISPATCH, epic_id=8, story_id="S1", repo_root=tmp_path
            )
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "story_gate"
    assert exc.story_id == "S1"


# ---------------------------------------------------------------------------
# Mapped cartography refs in artefacts_loaded
# ---------------------------------------------------------------------------


def test_research_node_artefacts_include_mapped_carto_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _write_spark(tmp_path, 9)
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
        (directory / "discovery" / "research").mkdir(parents=True, exist_ok=True)
        (directory / "discovery" / "research" / "research.md").write_text("# Research\n\nDone.\n")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    nodes.discovery_research_node(
        NodeInput(node_type=NodeType.DISCOVERY_RESEARCH, epic_id=9, repo_root=tmp_path)
    )

    loaded = captured["artefacts_loaded"]
    assert ".woof/codebase/STACK.md" in loaded
    assert ".woof/codebase/INTEGRATIONS.md" in loaded
    assert ".woof/codebase/CONCERNS.md" in loaded
    assert ".woof/codebase/CURRENT-ARCHITECTURE.md" not in loaded
    assert ".woof/codebase/STRUCTURE.md" not in loaded


def test_executor_dispatch_artefacts_include_mapped_carto_refs_and_files_txt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_plan(tmp_path, 10)
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
    ) -> nodes.DispatchRunResult:
        captured["artefacts_loaded"] = artefacts_loaded
        captured["prompt"] = prompt
        return nodes.DispatchRunResult(
            process=subprocess.CompletedProcess([], 0, "", ""),
            exit_type="completed_lingering",
        )

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    nodes.executor_dispatch_node(
        NodeInput(
            node_type=NodeType.EXECUTOR_DISPATCH, epic_id=10, story_id="S1", repo_root=tmp_path
        )
    )

    loaded = captured["artefacts_loaded"]
    assert ".woof/codebase/STRUCTURE.md" in loaded
    assert ".woof/codebase/CONVENTIONS.md" in loaded
    assert ".woof/codebase/TARGET-ARCHITECTURE.md" in loaded
    assert ".woof/codebase/PRINCIPLES.md" in loaded
    assert ".woof/codebase/files.txt" in loaded
    assert '"files_txt_slice"' in captured["prompt"]


# ---------------------------------------------------------------------------
# executor_dispatch files.txt slice
# ---------------------------------------------------------------------------


def test_executor_dispatch_files_txt_slice_filtered_by_story_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    _write_plan(tmp_path, 11)
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print('hello')\n")
    (tmp_path / "README.md").write_text("# README\n")
    _git(tmp_path, "add", "src/app.py", "README.md", check=True)
    _write_codebase_docs(
        tmp_path,
        files_txt_content="src/app.py\nREADME.md\ndocs/design.md\n",
    )
    captured: dict[str, Any] = {}

    def fake_dispatch(
        repo_root: Path,
        role: str,
        epic_id: int,
        story_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
    ) -> nodes.DispatchRunResult:
        captured["prompt"] = prompt
        return nodes.DispatchRunResult(
            process=subprocess.CompletedProcess([], 0, "", ""),
            exit_type="completed_lingering",
        )

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    nodes.executor_dispatch_node(
        NodeInput(
            node_type=NodeType.EXECUTOR_DISPATCH, epic_id=11, story_id="S1", repo_root=tmp_path
        )
    )

    prompt = captured["prompt"]
    payload = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
    files_txt_slice = payload["inputs"]["files_txt_slice"]
    assert "src/app.py" in files_txt_slice
    assert "README.md" not in files_txt_slice
    assert "docs/design.md" not in files_txt_slice


# ---------------------------------------------------------------------------
# Plan-critique blocker evidence enforcement (E2 S4 R4)
# ---------------------------------------------------------------------------


def _write_plan_critique_blocker(directory: Path, evidence: str) -> None:
    critique_dir = directory / "critique"
    critique_dir.mkdir(exist_ok=True)
    (critique_dir / "plan.md").write_text(
        "---\n"
        "target: plan\n"
        "target_id: null\n"
        "severity: blocker\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "harness: test-reviewer\n"
        "findings:\n"
        "  - id: F1\n"
        "    severity: blocker\n"
        "    summary: tighten story scope\n"
        f"    evidence: {evidence}\n"
        "---\n"
        "Plan critique body.\n"
    )


def test_plan_critique_node_rejects_blocker_with_unresolvable_evidence(
    tmp_path: Path,
) -> None:
    directory = _write_spark(tmp_path, 50)
    _write_minimal_epic(directory, 50)
    _write_stage3_plan(directory, 50)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 50)))
    _write_plan_critique_blocker(directory, "looks wrong")

    output = nodes.plan_critique_node(
        NodeInput(node_type=NodeType.PLAN_CRITIQUE, epic_id=50, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]
    assert "F1" in output.message


def test_plan_critique_node_accepts_blocker_with_resolvable_story_evidence(
    tmp_path: Path,
) -> None:
    directory = _write_spark(tmp_path, 51)
    _write_minimal_epic(directory, 51)
    _write_stage3_plan(directory, 51)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 51)))
    _write_plan_critique_blocker(directory, "S1 does not implement the required outcome")

    output = nodes.plan_critique_node(
        NodeInput(node_type=NodeType.PLAN_CRITIQUE, epic_id=51, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.COMPLETED
    assert output.next_node == NodeType.PLAN_GATE_OPEN
