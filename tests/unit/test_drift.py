"""S8 — head/branch drift gate tests.

Four scenarios for the commit_node drift check:
1. No prior subprocess_returned event → commit_node proceeds (no drift gate).
2. HEAD and branch match head_after/branch_after → no drift gate (clean pass).
3. HEAD has moved since last dispatch → head_branch_drift gate opened.
4. Branch has switched since last dispatch → head_branch_drift gate opened.

All tests use real temp git repos; no unittest.mock.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.support import DEFAULT_PROJECT_KEY
from woof import state
from woof.graph.git import git_env, head_branch_drift_detected
from woof.graph.nodes import commit_node
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=git_env(),
        **kwargs,  # type: ignore[arg-type]
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com", check=True)
    _git(root, "config", "user.name", "Test", check=True)
    (root / ".gitignore").write_text("*.tmp\n")
    _git(root, "add", ".gitignore", check=True, capture_output=True)
    _git(root, "commit", "-m", "chore: init", check=True, capture_output=True)


def _head_sha(root: Path) -> str:
    r = _git(root, "rev-parse", "HEAD", check=True, capture_output=True, text=True)
    return r.stdout.strip()


def _branch_name(root: Path) -> str:
    r = _git(root, "symbolic-ref", "--short", "HEAD", check=True, capture_output=True, text=True)
    return r.stdout.strip()


def _epic_dir(root: Path, epic_id: int = 1) -> Path:
    d = state.epic_dir(DEFAULT_PROJECT_KEY, epic_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_plan(root: Path, epic_id: int = 1) -> None:
    d = _epic_dir(root, epic_id)
    (d / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": epic_id,
                "goal": "test",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "first",
                        "summary": "test",
                        "paths": ["src/**"],
                        "satisfies": ["O1"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "status": "in_progress",
                    }
                ],
            }
        )
    )


def _write_executor_result(root: Path, epic_id: int = 1) -> None:
    d = _epic_dir(root, epic_id)
    (d / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": epic_id,
                "work_unit_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )


def _append_subprocess_returned(root: Path, epic_id: int = 1, **fields: object) -> None:
    d = _epic_dir(root, epic_id)
    event = {"event": "subprocess_returned", "epic_id": epic_id, **fields}
    with (d / "dispatch.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _node_input(root: Path, epic_id: int = 1, work_unit_id: str = "S1") -> NodeInput:
    return NodeInput(
        node_type=NodeType.COMMIT,
        project_key=DEFAULT_PROJECT_KEY,
        repo_root=root,
        epic_id=epic_id,
        work_unit_id=work_unit_id,
    )


# ---------------------------------------------------------------------------
# Unit tests for head_branch_drift_detected (no commit_node overhead)
# ---------------------------------------------------------------------------


def test_drift_detected_none_expected_values_no_drift(tmp_path: Path) -> None:
    """Both expected values None → skip both axes → no drift."""
    _init_repo(tmp_path)
    drift, _ = head_branch_drift_detected(tmp_path, None, None)
    assert not drift


def test_drift_detected_matching_sha_and_branch(tmp_path: Path) -> None:
    """Current HEAD and branch match expectations → no drift."""
    _init_repo(tmp_path)
    sha = _head_sha(tmp_path)
    branch = _branch_name(tmp_path)
    drift, _ = head_branch_drift_detected(tmp_path, sha, branch)
    assert not drift


def test_drift_detected_sha_mismatch(tmp_path: Path) -> None:
    """HEAD moved after dispatch was recorded → drift detected."""
    _init_repo(tmp_path)
    sha_at_dispatch = _head_sha(tmp_path)
    (tmp_path / "x.txt").write_text("y")
    _git(tmp_path, "add", "x.txt", check=True, capture_output=True)
    _git(tmp_path, "commit", "-m", "extra", check=True, capture_output=True)
    drift, desc = head_branch_drift_detected(tmp_path, sha_at_dispatch, None)
    assert drift
    assert sha_at_dispatch[:12] in desc


def test_drift_detected_branch_mismatch(tmp_path: Path) -> None:
    """Branch switched after dispatch → drift detected."""
    _init_repo(tmp_path)
    sha = _head_sha(tmp_path)
    original_branch = _branch_name(tmp_path)
    _git(tmp_path, "checkout", "-b", "other", check=True, capture_output=True)
    drift, desc = head_branch_drift_detected(tmp_path, sha, original_branch)
    assert drift
    assert original_branch in desc


# ---------------------------------------------------------------------------
# Scenario 1: no prior subprocess_returned → no drift gate
# ---------------------------------------------------------------------------


def test_no_prior_dispatch_event_skips_drift_check(tmp_path: Path) -> None:
    """No subprocess_returned in dispatch.jsonl → commit_node does not open drift gate."""
    _init_repo(tmp_path)
    _epic_dir(tmp_path)
    _write_plan(tmp_path)
    _write_executor_result(tmp_path)

    out: NodeOutput = commit_node(_node_input(tmp_path))

    assert out.triggered_by != ["head_branch_drift"]


# ---------------------------------------------------------------------------
# Scenario 2: HEAD and branch both match → no drift gate
# ---------------------------------------------------------------------------


def test_head_and_branch_match_no_drift(tmp_path: Path) -> None:
    """When head_after and branch_after match current state, no drift gate opens."""
    _init_repo(tmp_path)
    sha = _head_sha(tmp_path)
    branch = _branch_name(tmp_path)

    _epic_dir(tmp_path)
    _append_subprocess_returned(tmp_path, work_unit_id="S1", head_after=sha, branch_after=branch)
    _write_plan(tmp_path)
    _write_executor_result(tmp_path)

    out: NodeOutput = commit_node(_node_input(tmp_path))

    assert out.triggered_by != ["head_branch_drift"]


# ---------------------------------------------------------------------------
# Scenario 3: HEAD moved since last dispatch → head_branch_drift gate
# ---------------------------------------------------------------------------


def test_head_moved_opens_drift_gate(tmp_path: Path) -> None:
    """When HEAD advances after the last dispatch, commit_node opens head_branch_drift."""
    _init_repo(tmp_path)
    sha_at_dispatch = _head_sha(tmp_path)
    branch = _branch_name(tmp_path)

    _epic_dir(tmp_path)
    _append_subprocess_returned(
        tmp_path, work_unit_id="S1", head_after=sha_at_dispatch, branch_after=branch
    )

    # Advance HEAD by making a new commit after the dispatch event was recorded.
    (tmp_path / "extra.txt").write_text("drift\n")
    _git(tmp_path, "add", "extra.txt", check=True, capture_output=True)
    _git(tmp_path, "commit", "-m", "chore: drift", check=True, capture_output=True)

    out: NodeOutput = commit_node(_node_input(tmp_path))

    assert out.status == NodeStatus.GATE_OPENED
    assert out.triggered_by == ["head_branch_drift"]


# ---------------------------------------------------------------------------
# Scenario 4: branch switched since last dispatch → head_branch_drift gate
# ---------------------------------------------------------------------------


def test_branch_switched_opens_drift_gate(tmp_path: Path) -> None:
    """When the branch changes after the last dispatch, commit_node opens head_branch_drift."""
    _init_repo(tmp_path)
    sha = _head_sha(tmp_path)
    original_branch = _branch_name(tmp_path)

    _epic_dir(tmp_path)
    _append_subprocess_returned(
        tmp_path, work_unit_id="S1", head_after=sha, branch_after=original_branch
    )

    # Switch to a new branch after the dispatch event was recorded.
    _git(tmp_path, "checkout", "-b", "feature/drift", check=True, capture_output=True)

    out: NodeOutput = commit_node(_node_input(tmp_path))

    assert out.status == NodeStatus.GATE_OPENED
    assert out.triggered_by == ["head_branch_drift"]


# ---------------------------------------------------------------------------
# Story-id filtering: prior-story dispatch events must not trigger drift
# ---------------------------------------------------------------------------


def test_prior_story_dispatch_event_does_not_trigger_drift(tmp_path: Path) -> None:
    """A subprocess_returned for a different story must not be used as drift baseline.

    Reproduces the scenario where S1's graph-owned commit advances HEAD, then
    commit_node for S2 (no dispatch yet) would incorrectly see S1's head_after
    as the expected position and open a false drift gate.
    """
    _init_repo(tmp_path)
    sha_s1 = _head_sha(tmp_path)
    branch = _branch_name(tmp_path)

    _epic_dir(tmp_path)
    # Write a subprocess_returned for S1 (the prior story).
    _append_subprocess_returned(tmp_path, work_unit_id="S1", head_after=sha_s1, branch_after=branch)

    # Simulate S1's graph-owned commit advancing HEAD.
    (tmp_path / "s1_file.txt").write_text("s1 work\n")
    _git(tmp_path, "add", "s1_file.txt", check=True, capture_output=True)
    _git(tmp_path, "commit", "-m", "feat: E1 S1", check=True, capture_output=True)

    # S2 is being committed; it has no subprocess_returned of its own.
    d = _epic_dir(tmp_path)
    (d / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "goal": "test",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "first",
                        "summary": "s1",
                        "paths": [],
                        "satisfies": ["O1"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "deps": [],
                        "tests": {"count": 0, "types": []},
                        "status": "done",
                    },
                    {
                        "id": "S2",
                        "title": "second",
                        "summary": "s2",
                        "paths": ["src/**"],
                        "satisfies": ["O2"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "deps": ["S1"],
                        "tests": {"count": 0, "types": []},
                        "status": "in_progress",
                    },
                ],
            }
        )
    )
    (d / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "work_unit_id": "S2",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    out: NodeOutput = commit_node(
        NodeInput(
            node_type=NodeType.COMMIT,
            project_key=DEFAULT_PROJECT_KEY,
            repo_root=tmp_path,
            epic_id=1,
            work_unit_id="S2",
        )
    )
    # S2 has no prior dispatch — drift gate must NOT open regardless of S1's head_after.
    assert out.triggered_by != ["head_branch_drift"]
