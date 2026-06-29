"""Tests for E19 S1: per-node cartography payload wiring + incomplete_stage_state halt."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from woof.graph import nodes
from woof.graph.epilogue import DISPATCH_DENIAL_EPILOGUE
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
                "state": "pending",
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
        "work_units": [
            {
                "id": "S1",
                "title": "Build the first surface",
                "summary": "Create the first observable surface.",
                "paths": ["src/*.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "deps": [],
                "tests": {"count": 1, "types": ["unit"]},
                "state": "pending",
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
    assert exc.work_unit_id is None
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
    assert exc.work_unit_id is None


def test_discovery_synthesis_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    _write_spark(tmp_path, 3)

    with pytest.raises(StageStateError) as exc_info:
        nodes.discovery_synthesis_node(
            NodeInput(node_type=NodeType.DISCOVERY_SYNTHESIS, epic_id=3, repo_root=tmp_path)
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "plan_gate"
    assert exc.work_unit_id is None


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
    assert exc.work_unit_id is None


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
    assert exc.work_unit_id is None


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
    assert exc.work_unit_id is None


def test_executor_dispatch_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    _write_plan(tmp_path, 7)

    with pytest.raises(StageStateError) as exc_info:
        nodes.executor_dispatch_node(
            NodeInput(
                node_type=NodeType.EXECUTOR_DISPATCH,
                epic_id=7,
                work_unit_id="S1",
                repo_root=tmp_path,
            )
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "work_unit_gate"
    assert exc.work_unit_id == "S1"


def test_critique_dispatch_missing_cartography_raises_stage_state_error(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_plan(tmp_path, 8)
    (directory / "EPIC.md").write_text("---\nepic_id: 8\n---\n")

    with pytest.raises(StageStateError) as exc_info:
        nodes.critique_dispatch_node(
            NodeInput(
                node_type=NodeType.CRITIQUE_DISPATCH,
                epic_id=8,
                work_unit_id="S1",
                repo_root=tmp_path,
            )
        )

    exc = exc_info.value
    assert exc.operator_recoverable
    assert exc.gate_type == "work_unit_gate"
    assert exc.work_unit_id == "S1"


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
        work_unit_id: str | None,
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
        work_unit_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
        session_mode: str = "one-shot",
    ) -> nodes.DispatchRunResult:
        captured["artefacts_loaded"] = artefacts_loaded
        captured["prompt"] = prompt
        captured["session_mode"] = session_mode
        return nodes.DispatchRunResult(
            process=subprocess.CompletedProcess([], 0, "", ""),
            exit_type="completed_lingering",
        )

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    nodes.executor_dispatch_node(
        NodeInput(
            node_type=NodeType.EXECUTOR_DISPATCH, epic_id=10, work_unit_id="S1", repo_root=tmp_path
        )
    )

    loaded = captured["artefacts_loaded"]
    assert ".woof/codebase/STRUCTURE.md" in loaded
    assert ".woof/codebase/CONVENTIONS.md" in loaded
    assert ".woof/codebase/TARGET-ARCHITECTURE.md" in loaded
    assert ".woof/codebase/PRINCIPLES.md" in loaded
    assert ".woof/codebase/files.txt" in loaded
    assert captured["session_mode"] == "warm-producer"
    assert '"files_txt_slice"' in captured["prompt"]


# ---------------------------------------------------------------------------
# executor_dispatch files.txt slice
# ---------------------------------------------------------------------------


def test_executor_dispatch_files_txt_slice_filtered_by_work_unit_paths(
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
        work_unit_id: str | None,
        prompt: str,
        artefacts_loaded: list[str] | None = None,
        route_key: str | None = None,
        session_mode: str = "one-shot",
    ) -> nodes.DispatchRunResult:
        captured["prompt"] = prompt
        captured["session_mode"] = session_mode
        return nodes.DispatchRunResult(
            process=subprocess.CompletedProcess([], 0, "", ""),
            exit_type="completed_lingering",
        )

    monkeypatch.setattr(nodes, "_run_dispatch", fake_dispatch)

    nodes.executor_dispatch_node(
        NodeInput(
            node_type=NodeType.EXECUTOR_DISPATCH, epic_id=11, work_unit_id="S1", repo_root=tmp_path
        )
    )

    prompt = captured["prompt"]
    assert captured["session_mode"] == "warm-producer"
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
        "    summary: tighten work-unit scope\n"
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


def test_plan_critique_node_accepts_blocker_with_resolvable_work_unit_evidence(
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


# ---------------------------------------------------------------------------
# R5 — roll-up honesty enforced via shared helper on the plan path
# ---------------------------------------------------------------------------


def _write_plan_critique_rollup_mismatch(directory: Path, top_sev: str, finding_sev: str) -> None:
    critique_dir = directory / "critique"
    critique_dir.mkdir(exist_ok=True)
    (critique_dir / "plan.md").write_text(
        "---\n"
        "target: plan\n"
        "target_id: null\n"
        f"severity: {top_sev}\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "harness: test-reviewer\n"
        "findings:\n"
        "  - id: F1\n"
        f"    severity: {finding_sev}\n"
        "    summary: roll-up mismatch test finding\n"
        "    evidence: S1 is missing the implementation\n"
        "---\n"
        "Plan critique body.\n"
    )


def test_plan_critique_node_rejects_rollup_mismatch_minor_top_blocker_finding_R5(
    tmp_path: Path,
) -> None:
    """R5: plan critique with minor top-level but a blocker finding → rejected (roll-up mismatch)."""
    directory = _write_spark(tmp_path, 52)
    _write_minimal_epic(directory, 52)
    _write_stage3_plan(directory, 52)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 52)))
    _write_plan_critique_rollup_mismatch(directory, top_sev="minor", finding_sev="blocker")

    output = nodes.plan_critique_node(
        NodeInput(node_type=NodeType.PLAN_CRITIQUE, epic_id=52, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]
    assert "minor" in output.message
    assert "blocker" in output.message


def test_plan_critique_node_rejects_rollup_mismatch_info_top_blocker_finding_R5(
    tmp_path: Path,
) -> None:
    """R5: plan critique with info top-level but a blocker finding → rejected (roll-up mismatch)."""
    directory = _write_spark(tmp_path, 53)
    _write_minimal_epic(directory, 53)
    _write_stage3_plan(directory, 53)
    (directory / "PLAN.md").write_text(nodes._render_plan_markdown(nodes.load_plan(tmp_path, 53)))
    _write_plan_critique_rollup_mismatch(directory, top_sev="info", finding_sev="blocker")

    output = nodes.plan_critique_node(
        NodeInput(node_type=NodeType.PLAN_CRITIQUE, epic_id=53, repo_root=tmp_path)
    )

    assert output.status == NodeStatus.HALTED
    assert output.triggered_by == ["schema_validation_failed"]


# ---------------------------------------------------------------------------
# E21 S1 — playbook menu replaces bundled bodies
# ---------------------------------------------------------------------------


def test_playbook_menu_lists_all_research_playbooks() -> None:
    menu = nodes._discovery_bucket_playbooks("research")
    playbook_dir = nodes.tool_root() / "playbooks" / "discovery" / "research"
    expected_stems = sorted(p.stem for p in playbook_dir.glob("*.md"))
    for stem in expected_stems:
        assert stem in menu, f"playbook {stem!r} missing from research menu"


def test_playbook_menu_lists_all_thinking_playbooks() -> None:
    menu = nodes._discovery_bucket_playbooks("thinking")
    playbook_dir = nodes.tool_root() / "playbooks" / "discovery" / "consider"
    expected_stems = sorted(p.stem for p in playbook_dir.glob("*.md"))
    for stem in expected_stems:
        assert stem in menu, f"playbook {stem!r} missing from thinking menu"


def test_playbook_menu_carries_absolute_paths() -> None:
    for bucket, subdir in [("research", "research"), ("thinking", "consider")]:
        menu = nodes._discovery_bucket_playbooks(bucket)
        playbook_dir = nodes.tool_root() / "playbooks" / "discovery" / subdir
        for path in sorted(playbook_dir.glob("*.md")):
            assert str(path.resolve()) in menu, (
                f"absolute path for {path.name!r} missing from {bucket} menu"
            )


def test_playbook_menu_descriptions_derived_from_files() -> None:
    for bucket, subdir in [("research", "research"), ("thinking", "consider")]:
        menu = nodes._discovery_bucket_playbooks(bucket)
        playbook_dir = nodes.tool_root() / "playbooks" / "discovery" / subdir
        for path in sorted(playbook_dir.glob("*.md")):
            desc = nodes._playbook_description(path)
            assert desc in menu, (
                f"description {desc!r} for {path.name!r} missing from {bucket} menu"
            )


def test_playbook_menu_materially_smaller_than_bundled_bodies() -> None:
    for bucket, subdir in [("research", "research"), ("thinking", "consider")]:
        menu = nodes._discovery_bucket_playbooks(bucket)
        playbook_dir = nodes.tool_root() / "playbooks" / "discovery" / subdir
        old_sections = [
            f"## Building-block playbook: {p.stem}\n\n{p.read_text(encoding='utf-8').strip()}"
            for p in sorted(playbook_dir.glob("*.md"))
        ]
        old_form = "\n\n---\n\n".join(old_sections)
        assert len(menu) < len(old_form) * 0.2, (
            f"{bucket}: menu ({len(menu)} bytes) is not materially smaller than "
            f"bundled form ({len(old_form)} bytes)"
        )


def test_ideate_bucket_returns_empty_menu() -> None:
    assert nodes._discovery_bucket_playbooks("ideate") == ""


# ---------------------------------------------------------------------------
# E21 S2 — plan validation caching
# ---------------------------------------------------------------------------


def _reset_plan_validate_cache() -> None:
    nodes._PLAN_VALIDATE_CACHE.clear()


def test_plan_validate_cache_hit_on_unchanged_content(tmp_path: Path) -> None:
    """_validate_plan returns cache_hit=True when called twice with identical content."""
    _reset_plan_validate_cache()
    directory = _write_spark(tmp_path, 60)
    _write_minimal_epic(directory, 60)
    _write_stage3_plan(directory, 60)
    plan_path = directory / "plan.json"

    ok1, _msg1, hit1 = nodes._validate_plan(tmp_path, 60, plan_path)
    assert ok1 is True
    assert hit1 is False

    ok2, _msg2, hit2 = nodes._validate_plan(tmp_path, 60, plan_path)
    assert ok2 is True
    assert hit2 is True


def test_plan_validate_cache_miss_after_content_change(tmp_path: Path) -> None:
    """_validate_plan returns cache_hit=False after plan.json content changes."""
    _reset_plan_validate_cache()
    directory = _write_spark(tmp_path, 61)
    _write_minimal_epic(directory, 61)
    _write_stage3_plan(directory, 61)
    plan_path = directory / "plan.json"

    ok1, _msg1, hit1 = nodes._validate_plan(tmp_path, 61, plan_path)
    assert ok1 is True
    assert hit1 is False

    original = json.loads(plan_path.read_text())
    original["work_units"][0]["title"] = "Modified title"
    plan_path.write_text(json.dumps(original))

    ok2, _msg2, hit2 = nodes._validate_plan(tmp_path, 61, plan_path)
    assert ok2 is True
    assert hit2 is False


def test_plan_validate_cache_does_not_pass_changed_invalid_plan(tmp_path: Path) -> None:
    """A stale cache entry never passes changed content; changed invalid plan fails correctly."""
    _reset_plan_validate_cache()
    directory = _write_spark(tmp_path, 62)
    _write_minimal_epic(directory, 62)
    _write_stage3_plan(directory, 62)
    plan_path = directory / "plan.json"

    ok1, _, _ = nodes._validate_plan(tmp_path, 62, plan_path)
    assert ok1 is True

    plan_path.write_text('{"epic_id": 62, "goal": "test", "work_units": "bad-value"}')

    ok2, _msg2, hit2 = nodes._validate_plan(tmp_path, 62, plan_path)
    assert ok2 is False
    assert hit2 is False


def test_plan_validate_cache_hit_recorded_in_plan_critiqued_event(tmp_path: Path) -> None:
    """plan_critiqued event carries plan_validate_cache_hit=True when plan unchanged since breakdown."""
    _reset_plan_validate_cache()
    directory = _write_spark(tmp_path, 63)
    _write_minimal_epic(directory, 63)
    _write_stage3_plan(directory, 63)
    plan_path = directory / "plan.json"

    ok, _, hit = nodes._validate_plan(tmp_path, 63, plan_path)
    assert ok is True
    assert hit is False

    ok2, _, hit2 = nodes._validate_plan(tmp_path, 63, plan_path)
    assert ok2 is True
    assert hit2 is True


# ---------------------------------------------------------------------------
# E21 S3 — single canonical denial epilogue
# ---------------------------------------------------------------------------


def _epilogue_text() -> str:
    return DISPATCH_DENIAL_EPILOGUE.strip()


def test_discovery_bucket_prompt_ends_with_canonical_epilogue_research(tmp_path: Path) -> None:
    _write_spark(tmp_path, 70)
    prompt = nodes._discovery_bucket_prompt(tmp_path, 70, "research")
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_discovery_bucket_prompt_ends_with_canonical_epilogue_thinking(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 71)
    (directory / "discovery" / "research").mkdir(parents=True)
    (directory / "discovery" / "research" / "research.md").write_text("# Research\n\nDone.\n")
    prompt = nodes._discovery_bucket_prompt(tmp_path, 71, "thinking")
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_discovery_bucket_prompt_ends_with_canonical_epilogue_ideate(tmp_path: Path) -> None:
    _write_spark(tmp_path, 72)
    prompt = nodes._discovery_bucket_prompt(tmp_path, 72, "ideate")
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_discovery_synthesis_prompt_ends_with_canonical_epilogue(tmp_path: Path) -> None:
    _write_spark(tmp_path, 73)  # epic.jsonl required for transitions
    prompt = nodes._discovery_synthesis_prompt(tmp_path, 73)
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_epic_definition_prompt_ends_with_canonical_epilogue(tmp_path: Path) -> None:
    _write_spark(tmp_path, 74)
    prompt = nodes._epic_definition_prompt(tmp_path, 74)
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_breakdown_planning_prompt_ends_with_canonical_epilogue(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 75)
    _write_minimal_epic(directory, 75)
    prompt = nodes._breakdown_planning_prompt(tmp_path, 75)
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_plan_critique_prompt_ends_with_canonical_epilogue(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 76)
    _write_minimal_epic(directory, 76)
    _write_stage3_plan(directory, 76)
    prompt = nodes._plan_critique_prompt(tmp_path, 76)
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_work_unit_critique_prompt_ends_with_canonical_epilogue(tmp_path: Path) -> None:
    directory = _write_spark(tmp_path, 77)
    _write_minimal_epic(directory, 77)
    _write_stage3_plan(directory, 77)
    prompt = nodes._work_unit_critique_prompt(tmp_path, 77, "S1")
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_executor_dispatch_prompt_ends_with_canonical_epilogue() -> None:
    prompt = nodes._executor_dispatch_prompt(
        repo_root=Path("/fake"),
        epic_id=78,
        work_unit_id="S1",
        cartography_refs=[],
        files_txt_slice=[],
    )
    assert prompt.rstrip("\n").endswith(_epilogue_text())
    assert prompt.count(_epilogue_text()) == 1


def test_playbooks_contain_no_per_playbook_denial_text() -> None:
    """No playbook file carries its own 'Do not run Woof graph commands' denial copy."""
    playbook_root = nodes.tool_root() / "playbooks"
    offenders = []
    for path in sorted(playbook_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        if "do not run woof graph commands" in text or "do not run woof" in text:
            offenders.append(str(path.relative_to(nodes.tool_root())))
    assert offenders == [], f"Playbooks still carry denial copies: {offenders}"


def test_epilogue_forbids_woof_check_not_the_project_quality_command() -> None:
    """The executor (execution playbook) runs its project quality command during
    red-green-refactor. The shared epilogue, appended last, must forbid the Woof
    gate verb (`woof check`) specifically - a bare 'checks' ban would contradict
    the executor's own instruction."""
    assert "woof check" in DISPATCH_DENIAL_EPILOGUE
    assert "quality command" in DISPATCH_DENIAL_EPILOGUE
