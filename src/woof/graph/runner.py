"""Graph runner for `woof wf`."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

from woof.gate.write import write_gate
from woof.graph.lock import epic_workflow_lock
from woof.graph.nodes import NodeHandler, default_registry
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType
from woof.graph.transitions import StageStateError, epic_dir, next_node
from woof.paths import schema_dir

_WF_RUN_COUNT_PATH = ".woof/wf-run-count"
_WF_RUN_COUNT_LOCK_PATH = ".woof/wf-run-count.lock"


def _increment_run_count(repo_root: Path) -> None:
    """Increment .woof/wf-run-count exactly once per woof wf invocation.

    A dedicated repo-wide flock on wf-run-count.lock serialises concurrent
    runs for different epics so no increment is lost to a read-modify-write race.
    """
    count_path = repo_root / _WF_RUN_COUNT_PATH
    lock_path = repo_root / _WF_RUN_COUNT_LOCK_PATH
    count_path.parent.mkdir(exist_ok=True)
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(count_path.read_text()) if count_path.exists() else {}
                count = data.get("count") if isinstance(data, dict) else None
                new_count = (int(count) + 1) if isinstance(count, int) and count >= 0 else 1
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                new_count = 1
            count_path.write_text(json.dumps({"count": new_count}))
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _gate_path(epic_id: int) -> str:
    return f".woof/epics/E{epic_id}/gate.md"


def _stage_state_gate_body(epic_id: int, message: str) -> str:
    return (
        "## Context\n\n"
        f"Woof could not map E{epic_id} filesystem state to a valid next graph node.\n\n"
        "## Findings\n\n"
        f"- {message}\n\n"
        "## Primary position\n\n"
        "No producer output was accepted after this malformed governance state was detected. "
        "Restore the required artefact or revise the plan state before resolving this gate.\n\n"
        "## Reviewer position\n\n"
        "The deterministic graph opened this gate because continuing would require guessing "
        "the intended workflow state.\n"
    )


def _open_stage_state_gate(repo_root: Path, epic_id: int, exc: StageStateError) -> NodeOutput:
    gate_type = exc.gate_type
    story_id = exc.story_id
    node_type = NodeType.PLAN_GATE_OPEN if gate_type == "plan_gate" else NodeType.GATE_OPEN
    write_gate(
        epic_dir=epic_dir(repo_root, epic_id),
        story_id=story_id,
        triggered_by=["incomplete_stage_state"],
        position_text=_stage_state_gate_body(epic_id, str(exc)),
        schema_path=schema_dir() / "gate.schema.json",
        validate=True,
        gate_type=gate_type,
    )
    return NodeOutput(
        node_type=node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=epic_id,
        story_id=story_id,
        gate_path=_gate_path(epic_id),
        triggered_by=["incomplete_stage_state"],
        message=str(exc),
    )


def run_graph(
    repo_root: Path,
    epic_id: int,
    *,
    once: bool = False,
    registry: dict | None = None,
) -> list[NodeOutput]:
    """Run the deterministic graph until it halts, gates, or completes."""

    with epic_workflow_lock(repo_root, epic_id):
        _increment_run_count(repo_root)
        handlers: dict = registry or default_registry()
        outputs: list[NodeOutput] = []
        while True:
            try:
                node_type, story_id = next_node(repo_root, epic_id)
            except StageStateError as exc:
                if exc.operator_recoverable:
                    outputs.append(_open_stage_state_gate(repo_root, epic_id, exc))
                    return outputs
                raise
            if node_type is NodeStatus.EPIC_ABANDONED:
                # next_node returns the abandoned-terminal sentinel (E17 P4 / D-AB);
                # surface it as a distinct NodeOutput status, never EPIC_COMPLETE.
                outputs.append(
                    NodeOutput(
                        node_type=NodeType.HUMAN_REVIEW,
                        status=NodeStatus.EPIC_ABANDONED,
                        epic_id=epic_id,
                        message=f"E{epic_id} abandoned",
                    )
                )
                return outputs
            if not isinstance(node_type, NodeType):
                # The only remaining non-NodeType sentinel is None: epic complete.
                outputs.append(
                    NodeOutput(
                        node_type=NodeType.HUMAN_REVIEW,
                        status=NodeStatus.EPIC_COMPLETE,
                        epic_id=epic_id,
                        message=f"E{epic_id} complete",
                    )
                )
                return outputs
            handler: NodeHandler = handlers[node_type]
            try:
                out = handler(
                    NodeInput(
                        node_type=node_type,
                        epic_id=epic_id,
                        story_id=story_id,
                        repo_root=repo_root,
                    )
                )
            except StageStateError as exc:
                if exc.operator_recoverable:
                    outputs.append(_open_stage_state_gate(repo_root, epic_id, exc))
                    return outputs
                raise
            outputs.append(out)
            if once or out.status in {
                NodeStatus.GATE_OPENED,
                NodeStatus.HALTED,
                NodeStatus.EPIC_COMPLETE,
            }:
                return outputs
