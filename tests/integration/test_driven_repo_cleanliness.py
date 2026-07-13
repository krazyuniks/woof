"""The driven repository carries no trace of the engine (ADR-017).

This is the executable form of the ADR's headline criterion: after a work unit
runs all the way through the graph to a landed commit, the delivery checkout
must contain only delivery content. No engine file, no engine directory, in the
commit, in the index, or untracked in the working tree.

The graph runs here with the real review_disposition and commit nodes. The
dispatch nodes are stubbed because the point under test is the transaction
boundary, not the harness; verification is stubbed to the staging and tree
record the commit node reads back, because the check runners are not under test.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tests.support import DEFAULT_PROJECT_KEY, seed_project_config
from woof import state
from woof.graph import nodes
from woof.graph.git import git_env
from woof.graph.runner import run_graph
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType
from woof.graph.transitions import mark_work_unit_state

EPIC_ID = 1
WORK_UNIT_ID = "S1"

# Anything the engine used to write into the driven repo. If a path in the
# delivery checkout starts with one of these, the retirement has regressed.
ENGINE_TRACES = (".woof", ".wf.lock")


def _git(root: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(["git", *args], cwd=root, env=git_env(), **kwargs)


def _init_delivery_repo(root: Path) -> None:
    """A throwaway delivery checkout with one committed source file."""

    _git(root, "init", check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com", check=True)
    _git(root, "config", "user.name", "Test", check=True)
    source = root / "src"
    source.mkdir()
    (source / "widget.py").write_text("VALUE = 0\n")
    _git(root, "add", "--", "src/widget.py", check=True, capture_output=True)
    _git(root, "commit", "-m", "chore: initial", check=True, capture_output=True)


def _seed_epic_state(key: str, epic_id: int) -> Path:
    """Seed plan and epic contract in the operator home, where engine state lives."""

    directory = state.epic_dir(key, epic_id)
    directory.mkdir(parents=True, exist_ok=True)
    plan = {
        "epic_id": epic_id,
        "goal": "keep the driven repo clean",
        "work_units": [
            {
                "id": WORK_UNIT_ID,
                "title": "change the widget",
                "summary": "bump the widget value",
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
    (directory / "plan.json").write_text(json.dumps(plan) + "\n")
    (directory / "epic.jsonl").write_text("")
    (directory / "dispatch.jsonl").write_text("")
    return directory


def _tracked_and_untracked(root: Path) -> list[str]:
    """Every path git can see in the checkout: tracked, staged, or untracked."""

    tracked = _git(root, "ls-files", check=True, capture_output=True, text=True).stdout.splitlines()
    untracked = _git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return sorted({*tracked, *untracked})


def _commit_paths(root: Path) -> list[str]:
    return sorted(
        _git(
            root,
            "show",
            "--pretty=format:",
            "--name-only",
            "HEAD",
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
    )


def _engine_traces(paths: list[str]) -> list[str]:
    return [
        path
        for path in paths
        if any(path == trace or path.startswith(f"{trace}/") for trace in ENGINE_TRACES)
    ]


def test_landed_work_unit_leaves_no_engine_trace_in_the_driven_repo(tmp_path: Path) -> None:
    key = DEFAULT_PROJECT_KEY
    seed_project_config({"delivery": {"profile": "B"}, "profiles": {"B": {"commit": True}}})
    _init_delivery_repo(tmp_path)
    directory = _seed_epic_state(key, EPIC_ID)

    def executor(inp: NodeInput) -> NodeOutput:
        # The producer's only repo-visible effect: it edits its work-unit paths.
        (inp.repo_root / "src" / "widget.py").write_text("VALUE = 1\n")
        mark_work_unit_state(inp.project_key, inp.epic_id, WORK_UNIT_ID, "in_progress")
        (directory / "executor_result.json").write_text(
            json.dumps(
                {
                    "epic_id": inp.epic_id,
                    "work_unit_id": WORK_UNIT_ID,
                    "outcome": "staged_for_verification",
                    "commit_body": "bump the widget",
                    "position": None,
                }
            )
        )
        # A dispatch leaves an audit trail; the commit node requires one.
        audit = directory / "audit" / "redacted"
        audit.mkdir(parents=True, exist_ok=True)
        (audit / f"{WORK_UNIT_ID}-executor.md").write_text("dispatch transcript\n")
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.COMPLETED,
            epic_id=inp.epic_id,
            work_unit_id=WORK_UNIT_ID,
        )

    def critique(inp: NodeInput) -> NodeOutput:
        critique_dir = directory / "critique"
        critique_dir.mkdir(parents=True, exist_ok=True)
        (critique_dir / f"work-unit-{WORK_UNIT_ID}.md").write_text(
            "---\ntarget: work_unit\ntarget_id: S1\nseverity: info\n"
            "timestamp: '2026-01-01T00:00:00Z'\nharness: test\nfindings: []\n---\n"
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.COMPLETED,
            epic_id=inp.epic_id,
            work_unit_id=WORK_UNIT_ID,
        )

    def verification(inp: NodeInput) -> NodeOutput:
        # Stage exactly the work-unit paths, as the real verification node does,
        # and record the tree the checks verified.
        nodes._stage_work_unit_transaction_paths(
            inp.project_key, inp.repo_root, inp.epic_id, WORK_UNIT_ID
        )
        verified_tree = _git(
            inp.repo_root, "write-tree", check=True, capture_output=True, text=True
        ).stdout.strip()
        verified_paths = _git(
            inp.repo_root,
            "diff",
            "--cached",
            "--name-only",
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        (directory / "check-result.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "stage": 5,
                    "epic_id": inp.epic_id,
                    "work_unit_id": WORK_UNIT_ID,
                    "triggered_by": [],
                    "checks": [],
                    "verified_tree": verified_tree,
                    "verified_paths": verified_paths,
                }
            )
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.COMPLETED,
            epic_id=inp.epic_id,
            work_unit_id=WORK_UNIT_ID,
        )

    outputs = run_graph(
        key,
        tmp_path,
        EPIC_ID,
        registry={
            NodeType.EXECUTOR_DISPATCH: executor,
            NodeType.CRITIQUE_DISPATCH: critique,
            NodeType.REVIEW_DISPOSITION: nodes.review_disposition_node,
            NodeType.VERIFICATION: verification,
            NodeType.COMMIT: nodes.commit_node,
        },
    )

    statuses = [output.status for output in outputs]
    assert NodeStatus.GATE_OPENED not in statuses, f"graph gated instead of committing: {outputs}"

    # The work unit really landed: the delivery change is in HEAD.
    committed = _commit_paths(tmp_path)
    assert committed == ["src/widget.py"], committed
    assert (tmp_path / "src" / "widget.py").read_text() == "VALUE = 1\n"

    # The commit carries only delivery content.
    assert _engine_traces(committed) == []

    # And so does the checkout: nothing engine-owned tracked, staged, or untracked.
    visible = _tracked_and_untracked(tmp_path)
    assert _engine_traces(visible) == [], f"engine files left in the driven repo: {visible}"
    assert not (tmp_path / ".woof").exists()

    # The engine's own state is intact - it just lives in the operator home.
    assert (state.epic_dir(key, EPIC_ID) / "plan.json").is_file()
    assert (state.epic_dir(key, EPIC_ID) / "epic.jsonl").is_file()
