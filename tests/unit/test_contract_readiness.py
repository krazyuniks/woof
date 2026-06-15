"""Tests for the Stage-2.5 contract-readiness checks and node wiring (E2 S1+S2).

The node is deterministic (no dispatch): it reads EPIC.md, writes
readiness-result.json, and either records readiness_passed and advances to
breakdown_planning or opens a readiness_gate. The node tests build an epic on
disk and call the node and next_node directly, mirroring tests/unit/test_graph.py.

The S2 matrix tests call ``evaluate_readiness`` directly against a real git repo
(no mocks): referenced files are created and ``git add``-ed so path and symbol
resolution run against ``git ls-files``.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import cast

import pytest
import yaml

from woof.cli.commands.wf import _resolve_gate
from woof.graph import nodes, transitions
from woof.graph.nodes import DEFAULT_READINESS_ESCALATION_THRESHOLD
from woof.graph.readiness import (
    ACCEPTANCE_PROSE_CHECK_ID,
    ACCEPTANCE_SIGNAL_CHECK_ID,
    CHECKER_BUDGET_CHECK_ID,
    CONTRACT_CONCRETENESS_CHECK_ID,
    DECOMPOSITION_SUFFICIENCY_CHECK_ID,
    PATH_RESOLUTION_CHECK_ID,
    SYMBOL_RESOLUTION_CHECK_ID,
    ReadinessCheck,
    ReadinessResult,
    evaluate_readiness,
    has_concrete_signal,
)
from woof.graph.state import NodeInput, NodeStatus, NodeType
from woof.trackers.base import LifecycleSyncResult, Tracker

pytestmark = pytest.mark.host_only

_DEMO_SCHEMA = '{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}\n'


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
  - the system works
---

Free-form prose below the front-matter.
"""


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


def _track(root: Path, relpath: str, content: str) -> None:
    if not (root / ".git").exists():
        _git_init(root)
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--", relpath], cwd=root, check=True)


def _setup_epic(
    root: Path,
    epic_md: str,
    epic_id: int = 1,
    tracked_files: dict[str, str] | None = None,
) -> Path:
    _git_init(root)
    directory = root / ".woof" / "epics" / f"E{epic_id}"
    directory.mkdir(parents=True)
    (directory / "EPIC.md").write_text(epic_md)
    (directory / "epic.jsonl").write_text(
        json.dumps({"event": "definition_closed", "at": "2026-06-09T10:00:00Z", "epic_id": epic_id})
        + "\n"
    )
    for relpath, content in (tracked_files or {}).items():
        _track(root, relpath, content)
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


def _epic_text(front: dict, body: str = "") -> str:
    dumped = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{dumped}---\n\n{body}\n"


def _evaluate(root: Path, front: dict, body: str = "", **kwargs) -> ReadinessResult:
    epic_path = root / ".woof" / "epics" / "E1" / "EPIC.md"
    epic_path.parent.mkdir(parents=True, exist_ok=True)
    epic_path.write_text(_epic_text(front, body), encoding="utf-8")
    return evaluate_readiness(root, 1, epic_path, **kwargs)


def _check(result: ReadinessResult, check_id: str) -> ReadinessCheck:
    return next(check for check in result.checks if check.id == check_id)


def _check_ids(result: ReadinessResult) -> set[str]:
    return {check.id for check in result.checks}


def _outcome(outcome_id: str, verification: str = "automated", **extra) -> dict:
    return {
        "id": outcome_id,
        "statement": f"{outcome_id} statement",
        "verification": verification,
        **extra,
    }


def _cd(cd_id: str, outcomes: list[str], **extra) -> dict:
    base = {"id": cd_id, "related_outcomes": outcomes, "title": f"{cd_id} title"}
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# Prompt-1 node wiring (now against real git repos)
# --------------------------------------------------------------------------- #


def test_next_node_routes_definition_closed_to_readiness(tmp_path: Path) -> None:
    """A closed definition with no readiness_passed yet routes to contract_readiness."""
    _setup_epic(tmp_path, READY_EPIC)

    node, story_id = transitions.next_node(tmp_path, 1)

    assert node == NodeType.CONTRACT_READINESS
    assert story_id is None


def test_ready_epic_passes_and_advances_to_breakdown(tmp_path: Path) -> None:
    directory = _setup_epic(
        tmp_path, READY_EPIC, tracked_files={"schemas/demo.schema.json": _DEMO_SCHEMA}
    )

    output = nodes.contract_readiness_node(_readiness_input(tmp_path))

    assert output.status == NodeStatus.COMPLETED, output.message
    assert output.next_node == NodeType.BREAKDOWN_PLANNING

    result = json.loads((directory / "readiness-result.json").read_text())
    assert result["ok"] is True
    assert result["epic_id"] == 1
    assert any(c["id"] == ACCEPTANCE_SIGNAL_CHECK_ID and c["ok"] for c in result["checks"])
    # The full matrix ran and every blocking check passed.
    assert all(c["ok"] for c in result["checks"])

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
    assert failing and failing[0]["id"] == ACCEPTANCE_SIGNAL_CHECK_ID
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


# --------------------------------------------------------------------------- #
# Prompt-2 readiness-gate resolution verbs (E17 P2 / D-RA)
# --------------------------------------------------------------------------- #


class _NoopTracker:
    """Stub tracker for readiness resolution verbs.

    approve_with_reason and revise_epic_contract touch no tracker method;
    abandon_epic calls close_not_delivered (E17 P4 / D-AB), which this stub
    records so tests can assert the issue was closed as not delivered.
    """

    def __init__(self) -> None:
        self.closed_not_delivered: list[int] = []

    def close_not_delivered(self, epic_id: int) -> LifecycleSyncResult:
        self.closed_not_delivered.append(epic_id)
        return LifecycleSyncResult(
            epic_id=epic_id,
            body="",
            updated_at="2026-06-09T10:00:00Z",
            last_sync_path=Path(f".woof/epics/E{epic_id}/.last-sync"),
            changed=True,
            closed=True,
        )


def _open_readiness_gate(root: Path) -> Path:
    """Build an unready epic and run the node so a readiness_gate is open on disk."""
    directory = _setup_epic(root, UNREADY_EPIC)
    output = nodes.contract_readiness_node(_readiness_input(root))
    assert output.status == NodeStatus.GATE_OPENED
    assert (directory / "gate.md").exists()
    return directory


def test_approve_with_reason_advances_unready_epic_to_planning(tmp_path: Path) -> None:
    directory = _open_readiness_gate(tmp_path)

    rc = _resolve_gate(tmp_path, 1, "approve_with_reason", cast(Tracker, _NoopTracker()))
    assert rc == 0
    assert not (directory / "gate.md").exists()

    resolved = [e for e in _epic_events(directory) if e["event"] == "readiness_gate_resolved"]
    assert resolved and resolved[-1]["decision"] == "approve_with_reason"
    assert resolved[-1]["gate_type"] == "readiness_gate"

    # The unchanged contract is now readiness-satisfied and advances to planning
    # without re-running the readiness node (no re-gate).
    assert transitions.readiness_satisfied(tmp_path, 1) is True
    assert transitions.next_node(tmp_path, 1) == (NodeType.BREAKDOWN_PLANNING, None)


def test_reclosed_contract_rearms_readiness_after_approve_with_reason(tmp_path: Path) -> None:
    _open_readiness_gate(tmp_path)
    _resolve_gate(tmp_path, 1, "approve_with_reason", cast(Tracker, _NoopTracker()))
    assert transitions.readiness_satisfied(tmp_path, 1) is True

    # A revised+re-closed contract appends a new definition_closed, which re-arms
    # readiness: the prior approval no longer counts and the graph re-runs the node.
    transitions.append_epic_event(
        tmp_path, 1, {"event": "definition_closed", "at": "2026-06-09T11:00:00Z", "epic_id": 1}
    )
    assert transitions.readiness_satisfied(tmp_path, 1) is False
    assert transitions.next_node(tmp_path, 1) == (NodeType.CONTRACT_READINESS, None)


def test_readiness_abandon_epic_closes_tracker_and_is_terminal(tmp_path: Path) -> None:
    # E17 P4 / D-AB: abandon_epic at the readiness gate closes the tracker issue
    # as not delivered, appends the graph-owned epic_abandoned marker, and makes
    # next_node terminal for the epic - even before plan.json exists.
    directory = _open_readiness_gate(tmp_path)
    tracker = _NoopTracker()

    rc = _resolve_gate(tmp_path, 1, "abandon_epic", cast(Tracker, tracker))
    assert rc == 0
    assert not (directory / "gate.md").exists()

    # The tracker issue is closed as not delivered.
    assert tracker.closed_not_delivered == [1]

    resolved = [e for e in _epic_events(directory) if e["event"] == "gate_resolved"]
    assert resolved and resolved[-1]["decision"] == "abandon_epic"
    assert resolved[-1]["gate_type"] == "readiness_gate"
    # The graph-owned terminal marker was written.
    assert any(
        e["event"] == "epic_abandoned" and e["epic_id"] == 1 for e in _epic_events(directory)
    )

    # abandon is not approval: readiness stays unsatisfied.
    assert transitions.readiness_satisfied(tmp_path, 1) is False
    # next_node is terminal for the abandoned epic, distinct from EPIC_COMPLETE.
    assert transitions.epic_abandoned(tmp_path, 1) is True
    assert transitions.next_node(tmp_path, 1) == (NodeStatus.EPIC_ABANDONED, None)


def test_invalid_readiness_verb_resolution_errors_and_keeps_gate(tmp_path: Path) -> None:
    directory = _open_readiness_gate(tmp_path)

    # `approve` is valid for the plan/story/review gates but not readiness; the
    # structured StageStateError maps to exit code 2 and the gate stays open.
    rc = _resolve_gate(tmp_path, 1, "approve", cast(Tracker, _NoopTracker()))
    assert rc == 2
    assert (directory / "gate.md").exists()
    assert not any(e["event"] == "readiness_gate_resolved" for e in _epic_events(directory))


def test_readiness_revise_epic_contract_archives_and_reenters_definition(tmp_path: Path) -> None:
    # E17 P5 / D-RC: revise_epic_contract at the readiness gate archives the prior
    # EPIC.md with the readiness findings and re-enters definition rather than just
    # deleting plan files (there is no plan yet at Stage 2.5).
    directory = _open_readiness_gate(tmp_path)

    rc = _resolve_gate(tmp_path, 1, "revise_epic_contract", cast(Tracker, _NoopTracker()))
    assert rc == 0
    assert not (directory / "gate.md").exists()

    # The prior EPIC.md is archived out of place (hand-editing stays forbidden) with
    # its readiness findings snapshot beside it.
    assert not (directory / "EPIC.md").exists()
    archived = directory / "definition" / "EPIC.1.archived.md"
    findings = directory / "definition" / "EPIC.1.findings.md"
    assert archived.exists()
    assert "Findings" in findings.read_text()

    resolved = [e for e in _epic_events(directory) if e["event"] == "gate_resolved"]
    assert resolved and resolved[-1]["decision"] == "revise_epic_contract"
    assert resolved[-1]["gate_type"] == "readiness_gate"

    # revise is not approval: readiness stays unsatisfied, and the graph re-enters
    # definition with the revision pending.
    assert transitions.readiness_satisfied(tmp_path, 1) is False
    assert transitions.definition_revision_requested(tmp_path, 1) is True
    assert transitions.next_node(tmp_path, 1) == (NodeType.EPIC_DEFINITION, None)


# --------------------------------------------------------------------------- #
# Check 1: acceptance signal (tightened)
# --------------------------------------------------------------------------- #


def test_acceptance_signal_fails_on_deprecated_cd_and_bare_mention(tmp_path: Path) -> None:
    """A deprecated CD does not realise an outcome and a bare O<n> mention is no signal."""
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [
            _cd("CD1", ["O1"], json_schema_ref="schemas/demo.schema.json", deprecated=True)
        ],
        "acceptance_criteria": ["see O1 in the suite"],
    }
    result = _evaluate(tmp_path, front)

    check = _check(result, ACCEPTANCE_SIGNAL_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "O1" for finding in check.findings)
    assert result.ok is False


def test_acceptance_signal_passes_via_machinable_criterion(tmp_path: Path) -> None:
    """No contract decision, but a criterion names O1 with a concrete signal."""
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [],
        "acceptance_criteria": ["O1 verified by `pytest tests/test_thing.py::test_o1`"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, ACCEPTANCE_SIGNAL_CHECK_ID).ok is True
    assert _check(result, DECOMPOSITION_SUFFICIENCY_CHECK_ID).ok is True


# --------------------------------------------------------------------------- #
# Check 2: acceptance prose
# --------------------------------------------------------------------------- #


def test_acceptance_prose_blocks_subjective_without_signal(tmp_path: Path) -> None:
    _track(tmp_path, "schemas/demo.schema.json", _DEMO_SCHEMA)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="schemas/demo.schema.json")],
        "acceptance_criteria": [
            "O1 covered by `just test`",
            "the UI should feel intuitive and clean",
        ],
    }
    result = _evaluate(tmp_path, front)

    check = _check(result, ACCEPTANCE_PROSE_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "acceptance_criteria[1]" for finding in check.findings)
    # The signal check is satisfied by CD1, so prose is the isolated failure.
    assert _check(result, ACCEPTANCE_SIGNAL_CHECK_ID).ok is True


def test_acceptance_prose_passes_when_subjective_is_paired_with_signal(tmp_path: Path) -> None:
    _track(tmp_path, "schemas/demo.schema.json", _DEMO_SCHEMA)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="schemas/demo.schema.json")],
        "acceptance_criteria": ["responses feel fast: p95 of `GET /x` under 200ms"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, ACCEPTANCE_PROSE_CHECK_ID).ok is True


# --------------------------------------------------------------------------- #
# Check 3: contract-decision concreteness
# --------------------------------------------------------------------------- #


def test_contract_concreteness_blocks_placeholder_ref(tmp_path: Path) -> None:
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="TODO")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    check = _check(result, CONTRACT_CONCRETENESS_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "CD1" for finding in check.findings)


def test_contract_concreteness_passes_on_concrete_ref(tmp_path: Path) -> None:
    _track(tmp_path, "schemas/demo.schema.json", _DEMO_SCHEMA)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="schemas/demo.schema.json")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, CONTRACT_CONCRETENESS_CHECK_ID).ok is True


# --------------------------------------------------------------------------- #
# Check 4: path resolution + forward-created grammar
# --------------------------------------------------------------------------- #


def test_path_resolution_blocks_unannotated_missing_path(tmp_path: Path) -> None:
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="schemas/missing.schema.json")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    check = _check(result, PATH_RESOLUTION_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "schemas/missing.schema.json" for finding in check.findings)
    # The missing path is concrete prose, so concreteness still passes.
    assert _check(result, CONTRACT_CONCRETENESS_CHECK_ID).ok is True


def test_path_resolution_blocks_unannotated_backtick_body_path(tmp_path: Path) -> None:
    _track(tmp_path, "schemas/demo.schema.json", _DEMO_SCHEMA)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="schemas/demo.schema.json")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front, body="The handler lives in `src/ghost.py` today.")

    check = _check(result, PATH_RESOLUTION_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "src/ghost.py" for finding in check.findings)


def test_path_resolution_passes_when_tracked(tmp_path: Path) -> None:
    _track(tmp_path, "schemas/demo.schema.json", _DEMO_SCHEMA)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="schemas/demo.schema.json")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, PATH_RESOLUTION_CHECK_ID).ok is True


def test_forward_created_annotation_exempts_missing_path(tmp_path: Path) -> None:
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [
            _cd(
                "CD1",
                ["O1"],
                json_schema_ref="schemas/future.schema.json",
                notes="Realised by `schemas/future.schema.json` (forward-created).",
            )
        ],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, PATH_RESOLUTION_CHECK_ID).ok is True
    assert _check(result, CONTRACT_CONCRETENESS_CHECK_ID).ok is True
    assert result.ok is True


def test_forward_created_created_by_ticket_form_exempts_missing_path(tmp_path: Path) -> None:
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="schemas/future.schema.json")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    body = "Schema `schemas/future.schema.json` (created by ticket WOOF-42) lands next sprint."
    result = _evaluate(tmp_path, front, body=body)

    assert _check(result, PATH_RESOLUTION_CHECK_ID).ok is True


def test_malformed_forward_created_annotation_does_not_exempt(tmp_path: Path) -> None:
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [
            _cd(
                "CD1",
                ["O1"],
                json_schema_ref="schemas/future.schema.json",
                # "forward created" (no hyphen) is not the exact grammar.
                notes="Realised by `schemas/future.schema.json` (forward created).",
            )
        ],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    check = _check(result, PATH_RESOLUTION_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "schemas/future.schema.json" for finding in check.findings)


# --------------------------------------------------------------------------- #
# Check 5: symbol resolution
# --------------------------------------------------------------------------- #


def test_symbol_resolution_passes_for_defined_top_level_class(tmp_path: Path) -> None:
    _track(
        tmp_path,
        "app/models.py",
        "from pydantic import BaseModel\n\n\nclass Thing(BaseModel):\n    name: str\n",
    )
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], pydantic_ref="app/models.py:Thing")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, SYMBOL_RESOLUTION_CHECK_ID).ok is True


def test_symbol_resolution_blocks_missing_symbol(tmp_path: Path) -> None:
    _track(
        tmp_path,
        "app/models.py",
        "from pydantic import BaseModel\n\n\nclass Other(BaseModel):\n    name: str\n",
    )
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], pydantic_ref="app/models.py:Missing")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    check = _check(result, SYMBOL_RESOLUTION_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "app/models.py:Missing" for finding in check.findings)


def test_symbol_resolution_blocks_untracked_file(tmp_path: Path) -> None:
    _git_init(tmp_path)
    # File exists on disk but is not git-tracked.
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("class Thing:\n    pass\n", encoding="utf-8")
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], pydantic_ref="app/models.py:Thing")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, SYMBOL_RESOLUTION_CHECK_ID).ok is False


def test_symbol_resolution_module_ref_requires_forward_created(tmp_path: Path) -> None:
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], pydantic_ref="app.models:Thing")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    blocked = _evaluate(tmp_path, front)
    assert _check(blocked, SYMBOL_RESOLUTION_CHECK_ID).ok is False

    front_marked = dict(front)
    front_marked["contract_decisions"] = [
        _cd(
            "CD1",
            ["O1"],
            pydantic_ref="app.models:Thing",
            notes="Imported from `app.models:Thing` (created by ticket WOOF-9).",
        )
    ]
    exempt = _evaluate(tmp_path, front_marked)
    assert _check(exempt, SYMBOL_RESOLUTION_CHECK_ID).ok is True


def test_symbol_resolution_file_token_forward_created_exempts(tmp_path: Path) -> None:
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [
            _cd(
                "CD1",
                ["O1"],
                pydantic_ref="app/new_models.py:Thing",
                notes="Model file `app/new_models.py` (forward-created).",
            )
        ],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, SYMBOL_RESOLUTION_CHECK_ID).ok is True


# --------------------------------------------------------------------------- #
# Check 6: decomposition sufficiency
# --------------------------------------------------------------------------- #


def test_decomposition_sufficiency_blocks_uncovered_manual_outcome(tmp_path: Path) -> None:
    """A manual outcome is exempt from the signal check but still needs decomposition."""
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "manual")],
        "contract_decisions": [],
        "acceptance_criteria": ["O1 is reviewed by a human"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, ACCEPTANCE_SIGNAL_CHECK_ID).ok is True
    check = _check(result, DECOMPOSITION_SUFFICIENCY_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "O1" for finding in check.findings)


def test_decomposition_sufficiency_blocks_orphan_contract_decision(tmp_path: Path) -> None:
    _track(tmp_path, "schemas/demo.schema.json", _DEMO_SCHEMA)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [
            _cd("CD1", ["O1"], json_schema_ref="schemas/demo.schema.json"),
            # CD2 relates only to O2, which is not a declared outcome.
            _cd("CD2", ["O2"], json_schema_ref="schemas/demo.schema.json"),
        ],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    check = _check(result, DECOMPOSITION_SUFFICIENCY_CHECK_ID)
    assert check.ok is False
    assert any(finding.ref == "CD2" for finding in check.findings)


def test_decomposition_sufficiency_passes_when_mutually_realised(tmp_path: Path) -> None:
    _track(tmp_path, "schemas/demo.schema.json", _DEMO_SCHEMA)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated"), _outcome("O2", "manual")],
        "contract_decisions": [
            _cd("CD1", ["O1", "O2"], json_schema_ref="schemas/demo.schema.json")
        ],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front)

    assert _check(result, DECOMPOSITION_SUFFICIENCY_CHECK_ID).ok is True
    assert result.ok is True


# --------------------------------------------------------------------------- #
# Timeout: non-blocking warn
# --------------------------------------------------------------------------- #


def test_zero_budget_skips_resolution_with_nonblocking_warn(tmp_path: Path) -> None:
    _track(tmp_path, "schemas/demo.schema.json", _DEMO_SCHEMA)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [_cd("CD1", ["O1"], json_schema_ref="schemas/demo.schema.json")],
        "acceptance_criteria": ["O1 covered by `just test`"],
    }
    result = _evaluate(tmp_path, front, time_budget_s=0)

    # Resolution checks were skipped, not run.
    assert PATH_RESOLUTION_CHECK_ID not in _check_ids(result)
    assert SYMBOL_RESOLUTION_CHECK_ID not in _check_ids(result)

    budget = _check(result, CHECKER_BUDGET_CHECK_ID)
    assert budget.severity == "warn"
    assert budget.ok is True
    assert PATH_RESOLUTION_CHECK_ID in budget.summary
    assert SYMBOL_RESOLUTION_CHECK_ID in budget.summary

    # A checker timeout never makes the result unready on its own.
    assert result.ok is True


def test_zero_budget_warn_does_not_mask_a_real_blocker(tmp_path: Path) -> None:
    """The cheap checks still run under a zero budget; only resolution is skipped."""
    _git_init(tmp_path)
    front = {
        "epic_id": 1,
        "title": "demo",
        "observable_outcomes": [_outcome("O1", "automated")],
        "contract_decisions": [],
        "acceptance_criteria": ["the system works"],
    }
    result = _evaluate(tmp_path, front, time_budget_s=0)

    assert _check(result, CHECKER_BUDGET_CHECK_ID).severity == "warn"
    assert _check(result, ACCEPTANCE_SIGNAL_CHECK_ID).ok is False
    assert result.ok is False


# --------------------------------------------------------------------------- #
# has_concrete_signal lexicon
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("O1", False),
        ("see O1 and CD2", False),
        ("the system is robust", False),
        ("O1 verified by `just test`", True),
        ("CD1 realised by `schemas/x.schema.json`", True),
        ("p95 latency < 200ms", True),
        ("returns exactly 3 rows", True),
        ("module `app/models.py:Thing` exists", True),
        ("covered by tests/test_x.py::test_y", True),
    ],
)
def test_has_concrete_signal(text: str, expected: bool) -> None:
    assert has_concrete_signal(text) is expected


# --------------------------------------------------------------------------- #
# S3: readiness recycle escalation
# --------------------------------------------------------------------------- #


def _append_gate_opened_event(directory: Path, epic_id: int) -> None:
    """Simulate a prior failed readiness cycle by appending the gate event."""
    transitions.append_epic_event(
        directory.parent.parent.parent,
        epic_id,
        {
            "event": "readiness_gate_opened",
            "at": "2026-06-09T10:30:00Z",
            "epic_id": epic_id,
            "gate_type": "readiness_gate",
            "triggered_by": ["readiness_unready"],
        },
    )


def _write_prereqs(root: Path, escalation_threshold: int | None = None) -> None:
    woof_dir = root / ".woof"
    woof_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        '[infra]\ngit = "any"\njust = "any"\n',
        '[commands]\nclaude = "any"\ncodex = "any"\n',
        '[validators]\najv = "any"\n"ajv-formats" = "any"\n',
        '[tracker]\nkind = "local"\n',
    ]
    if escalation_threshold is not None:
        lines.append(f"[readiness]\nescalation_threshold = {escalation_threshold}\n")
    (woof_dir / "prerequisites.toml").write_text("\n".join(lines), encoding="utf-8")


def test_failed_readiness_cycles_counts_gate_opened_events(tmp_path: Path) -> None:
    directory = _setup_epic(tmp_path, UNREADY_EPIC)
    assert transitions.failed_readiness_cycles(tmp_path, 1) == 0

    _append_gate_opened_event(directory, 1)
    assert transitions.failed_readiness_cycles(tmp_path, 1) == 1

    _append_gate_opened_event(directory, 1)
    assert transitions.failed_readiness_cycles(tmp_path, 1) == 2


def test_failed_readiness_cycles_resets_after_definition_closed(tmp_path: Path) -> None:
    directory = _setup_epic(tmp_path, UNREADY_EPIC)
    _append_gate_opened_event(directory, 1)
    _append_gate_opened_event(directory, 1)
    assert transitions.failed_readiness_cycles(tmp_path, 1) == 2

    transitions.append_epic_event(
        tmp_path,
        1,
        {"event": "definition_closed", "at": "2026-06-09T12:00:00Z", "epic_id": 1},
    )
    assert transitions.failed_readiness_cycles(tmp_path, 1) == 0


def test_below_threshold_opens_ordinary_readiness_gate(tmp_path: Path) -> None:
    # With threshold=3 (default), 2 prior cycles (< 3) → ordinary gate.
    directory = _setup_epic(tmp_path, UNREADY_EPIC)
    _append_gate_opened_event(directory, 1)
    _append_gate_opened_event(directory, 1)

    output = nodes.contract_readiness_node(_readiness_input(tmp_path))

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["readiness_unready"]

    front = _gate_front_matter(directory / "gate.md")
    assert front["triggered_by"] == ["readiness_unready"]
    assert front["type"] == "readiness_gate"


def test_escalation_fires_at_threshold(tmp_path: Path) -> None:
    # With threshold=3 (default), exactly 3 prior cycles → escalation gate.
    directory = _setup_epic(tmp_path, UNREADY_EPIC)
    for _ in range(DEFAULT_READINESS_ESCALATION_THRESHOLD):
        _append_gate_opened_event(directory, 1)

    output = nodes.contract_readiness_node(_readiness_input(tmp_path))

    assert output.status == NodeStatus.GATE_OPENED
    assert output.triggered_by == ["readiness_escalation"]

    front = _gate_front_matter(directory / "gate.md")
    assert front["triggered_by"] == ["readiness_escalation"]
    assert front["type"] == "readiness_gate"
    assert front["stage"] == 2
    assert front["story_id"] is None

    # The opened event is still readiness_gate_opened (same event name for the
    # same gate type): consumers reading by event type are not broken.
    events = _epic_events(directory)
    opened = [e for e in events if e.get("event") == "readiness_gate_opened"]
    assert opened
    assert opened[-1]["gate_type"] == "readiness_gate"


def test_escalated_gate_resolves_through_same_verbs(tmp_path: Path) -> None:
    # An escalated gate has gate_type=readiness_gate, so it accepts the same
    # resolution verbs as an ordinary readiness gate: approve_with_reason,
    # revise_epic_contract, abandon_epic. Verify approve_with_reason works.
    directory = _setup_epic(tmp_path, UNREADY_EPIC)
    for _ in range(DEFAULT_READINESS_ESCALATION_THRESHOLD):
        _append_gate_opened_event(directory, 1)

    output = nodes.contract_readiness_node(_readiness_input(tmp_path))
    assert output.triggered_by == ["readiness_escalation"]
    assert (directory / "gate.md").exists()

    rc = _resolve_gate(tmp_path, 1, "approve_with_reason", cast(Tracker, _NoopTracker()))
    assert rc == 0
    assert not (directory / "gate.md").exists()

    resolved = [e for e in _epic_events(directory) if e["event"] == "readiness_gate_resolved"]
    assert resolved and resolved[-1]["decision"] == "approve_with_reason"


def test_threshold_from_config(tmp_path: Path) -> None:
    # A custom threshold of 1 escalates after just one prior failed cycle.
    directory = _setup_epic(tmp_path, UNREADY_EPIC)
    _write_prereqs(tmp_path, escalation_threshold=1)
    _append_gate_opened_event(directory, 1)

    output = nodes.contract_readiness_node(_readiness_input(tmp_path))

    assert output.triggered_by == ["readiness_escalation"]
    front = _gate_front_matter(directory / "gate.md")
    assert front["triggered_by"] == ["readiness_escalation"]


def test_default_threshold_when_config_absent(tmp_path: Path) -> None:
    # No prerequisites.toml: default threshold applies.
    directory = _setup_epic(tmp_path, UNREADY_EPIC)
    # With only 1 prior cycle and default=3, expect ordinary gate.
    _append_gate_opened_event(directory, 1)

    output = nodes.contract_readiness_node(_readiness_input(tmp_path))

    assert output.triggered_by == ["readiness_unready"]
    front = _gate_front_matter(directory / "gate.md")
    assert front["triggered_by"] == ["readiness_unready"]


def test_config_threshold_is_valid_toml(tmp_path: Path) -> None:
    # Smoke-test that a prerequisites.toml with [readiness].escalation_threshold
    # is valid TOML and the value is read correctly.
    _write_prereqs(tmp_path, escalation_threshold=5)
    prereq_path = tmp_path / ".woof" / "prerequisites.toml"
    with prereq_path.open("rb") as fh:
        data = tomllib.load(fh)
    assert data["readiness"]["escalation_threshold"] == 5
