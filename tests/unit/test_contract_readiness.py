"""Tests for the Stage-2.5 contract-readiness node and its graph wiring (E2 S1).

The node is deterministic (no dispatch): it reads EPIC.md, writes
readiness-result.json, and either records readiness_passed and advances to
breakdown_planning or opens a readiness_gate. These tests build an epic on disk
and call the node and next_node directly, mirroring tests/unit/test_graph.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from woof.graph import nodes, transitions
from woof.graph.state import NodeInput, NodeStatus, NodeType

pytestmark = pytest.mark.host_only


READY_EPIC = """\
---
epic_id: 1
title: demo epic
observable_outcomes:
  - id: O1
    statement: user can do the thing
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes:
      - O1
    title: the realising contract
    json_schema_ref: schemas/demo.schema.json
acceptance_criteria:
  - O1 verified by the unit suite
---

Free-form prose below the front-matter.
"""

# Schema-valid but readiness-thin: O1 is machine-verified yet no contract
# decision realises it and no acceptance criterion names it.
UNREADY_EPIC = """\
---
epic_id: 1
title: demo epic
observable_outcomes:
  - id: O1
    statement: user can do the thing
    verification: automated
contract_decisions: []
acceptance_criteria:
  - the system works well
---

Free-form prose below the front-matter.
"""


def _setup_epic(root: Path, epic_md: str, epic_id: int = 1) -> Path:
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True)
    (directory / "EPIC.md").write_text(epic_md)
    (directory / "epic.jsonl").write_text(
        json.dumps({"event": "definition_closed", "at": "2026-06-09T10:00:00Z", "epic_id": epic_id})
        + "\n"
    )
    return directory


def _readiness_input(root: Path, epic_id: int = 1) -> NodeInput:
    return NodeInput(
        node_type=NodeType.CONTRACT_READINESS,
        epic_id=epic_id,
        repo_root=root,
    )


def _gate_front_matter(gate_path: Path) -> dict:
    text = gate_path.read_text()
    end = text.find("\n---\n", 4)
    return yaml.safe_load(text[4:end])


def _epic_events(directory: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (directory / "epic.jsonl").read_text().splitlines()
        if line.strip()
    ]


def test_next_node_routes_definition_closed_to_readiness(tmp_path: Path) -> None:
    """A closed definition with no readiness_passed yet routes to contract_readiness."""
    _setup_epic(tmp_path, READY_EPIC)

    node, story_id = transitions.next_node(tmp_path, 1)

    assert node == NodeType.CONTRACT_READINESS
    assert story_id is None


def test_ready_epic_passes_and_advances_to_breakdown(tmp_path: Path) -> None:
    directory = _setup_epic(tmp_path, READY_EPIC)

    output = nodes.contract_readiness_node(_readiness_input(tmp_path))

    assert output.status == NodeStatus.COMPLETED, output.message
    assert output.next_node == NodeType.BREAKDOWN_PLANNING

    result = json.loads((directory / "readiness-result.json").read_text())
    assert result["ok"] is True
    assert result["epic_id"] == 1
    assert any(c["id"] == "readiness_acceptance_signal" and c["ok"] for c in result["checks"])

    events = _epic_events(directory)
    assert any(e["event"] == "readiness_passed" for e in events)
    assert not (directory / "gate.md").exists()

    # The graph now advances past readiness.
    assert transitions.next_node(tmp_path, 1) == (NodeType.BREAKDOWN_PLANNING, None)


def test_unready_epic_opens_readiness_gate(tmp_path: Path) -> None:
    directory = _setup_epic(tmp_path, UNREADY_EPIC)

    output = nodes.contract_readiness_node(_readiness_input(tmp_path))

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["readiness_unready"]

    result = json.loads((directory / "readiness-result.json").read_text())
    assert result["ok"] is False
    failing = [c for c in result["checks"] if not c["ok"]]
    assert failing and failing[0]["id"] == "readiness_acceptance_signal"
    assert any(f["ref"] == "O1" for f in failing[0]["findings"])

    gate_path = directory / "gate.md"
    assert gate_path.exists()
    front = _gate_front_matter(gate_path)
    assert front["type"] == "readiness_gate"
    assert front["stage"] == 2
    assert front["story_id"] is None
    assert front["triggered_by"] == ["readiness_unready"]

    events = _epic_events(directory)
    assert any(e["event"] == "readiness_gate_opened" for e in events)
    assert not any(e["event"] == "readiness_passed" for e in events)

    # An open gate halts the graph for the operator.
    assert transitions.next_node(tmp_path, 1) == (NodeType.HUMAN_REVIEW, None)


def test_readiness_result_conforms_to_schema(tmp_path: Path, run_woof) -> None:
    directory = _setup_epic(tmp_path, UNREADY_EPIC)
    nodes.contract_readiness_node(_readiness_input(tmp_path))

    proc = run_woof("validate", str(directory / "readiness-result.json"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (readiness-result)" in proc.stdout


def test_readiness_node_rejects_story_id(tmp_path: Path) -> None:
    _setup_epic(tmp_path, READY_EPIC)
    with pytest.raises(ValueError, match="does not accept story_id"):
        nodes.contract_readiness_node(
            NodeInput(
                node_type=NodeType.CONTRACT_READINESS,
                epic_id=1,
                story_id="S1",
                repo_root=tmp_path,
            )
        )
