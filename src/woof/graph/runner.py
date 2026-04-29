"""Graph runner for `woof wf`."""

from __future__ import annotations

from pathlib import Path

from woof.graph.nodes import NodeHandler, default_registry
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType
from woof.graph.transitions import next_node


def run_graph(
    repo_root: Path,
    epic_id: int,
    *,
    once: bool = False,
    registry: dict | None = None,
) -> list[NodeOutput]:
    """Run the deterministic graph until it halts, gates, or completes."""

    handlers: dict = registry or default_registry()
    outputs: list[NodeOutput] = []
    while True:
        node_type, story_id = next_node(repo_root, epic_id)
        if node_type is None:
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
        out = handler(
            NodeInput(
                node_type=node_type,
                epic_id=epic_id,
                story_id=story_id,
                repo_root=repo_root,
            )
        )
        outputs.append(out)
        if once or out.status in {
            NodeStatus.GATE_OPENED,
            NodeStatus.HALTED,
            NodeStatus.EPIC_COMPLETE,
        }:
            return outputs
