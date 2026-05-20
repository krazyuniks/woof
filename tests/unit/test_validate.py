"""Black-box tests for ``woof validate``.

Tests run on host (woof requires uv and ajv-cli). Each test invokes the CLI as
a subprocess and asserts against exit code + output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
SCHEMA_DIR = REPO_ROOT / "schemas"
SCHEMA_FILES = sorted(SCHEMA_DIR.glob("*.schema.json"))


pytestmark = pytest.mark.host_only


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema_path", SCHEMA_FILES, ids=lambda p: p.name)
def test_shipped_schema_compiles(schema_path: Path) -> None:
    """Every schema in woof/schemas/ must itself be valid JSON Schema 2020-12."""
    proc = subprocess.run(
        ["ajv", "compile", "--spec=draft2020", "-c", "ajv-formats", "-s", str(schema_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"{schema_path.name}: {proc.stdout}{proc.stderr}"


def test_shipped_schema_count() -> None:
    """Schema directory holds the designed artefact and graph schemas."""
    expected = {
        "epic.schema.json",
        "plan.schema.json",
        "gate.schema.json",
        "critique.schema.json",
        "disposition.schema.json",
        "jsonl-events.schema.json",
        "prerequisites.schema.json",
        "agents.schema.json",
        "test-markers.schema.json",
        "language-registry.schema.json",
        "quality-gates.schema.json",
        "docs-paths.schema.json",
        "check-result.schema.json",
        "executor-result.schema.json",
        "node-input.schema.json",
        "node-output.schema.json",
        "planning-node-input.schema.json",
        "planning-node-output.schema.json",
        "transaction-manifest.schema.json",
    }
    assert {p.name for p in SCHEMA_FILES} == expected


# ---------------------------------------------------------------------------
# Real-file validation (the bootstrapped GTS prerequisites)
# ---------------------------------------------------------------------------


def test_validate_real_prerequisites(run_woof) -> None:
    proc = run_woof("validate", str(REPO_ROOT / ".woof" / "prerequisites.toml"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (prerequisites)" in proc.stdout


# ---------------------------------------------------------------------------
# Plan (JSON)
# ---------------------------------------------------------------------------


def _minimal_plan() -> dict:
    return {
        "epic_id": 1,
        "goal": "demo plan for tests",
        "stories": [
            {
                "id": "S1",
                "title": "first story",
                "intent": "do the thing",
                "paths": ["src/**/*.py"],
                "satisfies": ["O1"],
                "implements_contract_decisions": [],
                "uses_contract_decisions": [],
                "depends_on": [],
                "tests": {"count": 1, "types": ["unit"]},
                "status": "pending",
            }
        ],
    }


def test_validate_plan_valid(tmp_path: Path, run_woof) -> None:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(_minimal_plan()))
    proc = run_woof("validate", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (plan)" in proc.stdout


def test_validate_plan_missing_goal_fails(tmp_path: Path, run_woof) -> None:
    payload = _minimal_plan()
    del payload["goal"]
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload))
    proc = run_woof("validate", str(path))
    assert proc.returncode == 1
    assert "INVALID" in proc.stdout


# ---------------------------------------------------------------------------
# Planning graph node contracts (JSON)
# ---------------------------------------------------------------------------


def _base_planning_input(node_type: str, inputs: dict) -> dict:
    return {
        "node_type": node_type,
        "epic_id": 7,
        "repo_root": "/tmp/consumer",
        "epic_dir": ".woof/epics/E7",
        "inputs": inputs,
    }


def _base_planning_output(
    node_type: str,
    *,
    next_node: str | None,
    stage: int,
    status: str = "completed",
    gate_path: str | None = None,
    paths: list[str] | None = None,
    triggered_by: list[str] | None = None,
) -> dict:
    return {
        "node_type": node_type,
        "status": status,
        "epic_id": 7,
        "story_id": None,
        "next_node": next_node,
        "gate_path": gate_path,
        "validation_summary": {
            "ok": True,
            "stage": stage,
            "triggered_by": triggered_by or [],
            "check_count": len(paths or []),
            "failed_check_count": 0,
        },
        "message": "",
        "paths": paths or [],
        "triggered_by": triggered_by or [],
    }


@pytest.mark.parametrize(
    ("payload", "schema"),
    [
        (
            _base_planning_input(
                "discovery_research",
                {
                    "spark_path": ".woof/epics/E7/spark.md",
                    "discovery_dir": ".woof/epics/E7/discovery",
                    "bucket_dir": ".woof/epics/E7/discovery/research",
                },
            ),
            "planning-node-input",
        ),
        (
            _base_planning_input(
                "discovery_thinking",
                {
                    "spark_path": ".woof/epics/E7/spark.md",
                    "discovery_dir": ".woof/epics/E7/discovery",
                    "bucket_dir": ".woof/epics/E7/discovery/thinking",
                    "source_paths": [".woof/epics/E7/discovery/research/landscape.md"],
                },
            ),
            "planning-node-input",
        ),
        (
            _base_planning_input(
                "discovery_synthesis",
                {
                    "spark_path": ".woof/epics/E7/spark.md",
                    "discovery_dir": ".woof/epics/E7/discovery",
                    "synthesis_dir": ".woof/epics/E7/discovery/synthesis",
                    "source_paths": [".woof/epics/E7/discovery/research/options.md"],
                },
            ),
            "planning-node-input",
        ),
        (
            _base_planning_input(
                "epic_definition",
                {
                    "synthesis_dir": ".woof/epics/E7/discovery/synthesis",
                    "epic_path": ".woof/epics/E7/EPIC.md",
                    "carried_open_questions": ["OQ1"],
                },
            ),
            "planning-node-input",
        ),
        (
            _base_planning_input(
                "breakdown_planning",
                {
                    "epic_path": ".woof/epics/E7/EPIC.md",
                    "plan_path": ".woof/epics/E7/plan.json",
                    "plan_markdown_path": ".woof/epics/E7/PLAN.md",
                },
            ),
            "planning-node-input",
        ),
        (
            _base_planning_input(
                "plan_critique",
                {
                    "epic_path": ".woof/epics/E7/EPIC.md",
                    "plan_path": ".woof/epics/E7/plan.json",
                    "plan_markdown_path": ".woof/epics/E7/PLAN.md",
                    "critique_path": ".woof/epics/E7/critique/plan.md",
                },
            ),
            "planning-node-input",
        ),
        (
            _base_planning_input(
                "plan_gate_open",
                {
                    "plan_path": ".woof/epics/E7/plan.json",
                    "plan_markdown_path": ".woof/epics/E7/PLAN.md",
                    "critique_path": ".woof/epics/E7/critique/plan.md",
                    "gate_path": ".woof/epics/E7/gate.md",
                    "triggered_by": ["plan_review"],
                },
            ),
            "planning-node-input",
        ),
        (
            _base_planning_input(
                "plan_gate_resolve",
                {
                    "gate_path": ".woof/epics/E7/gate.md",
                    "decision": "approve",
                },
            ),
            "planning-node-input",
        ),
        (
            _base_planning_output(
                "discovery_research",
                paths=[".woof/epics/E7/discovery/research/landscape.md"],
                next_node="discovery_thinking",
                stage=1,
            ),
            "planning-node-output",
        ),
        (
            _base_planning_output(
                "discovery_brainstorm",
                paths=[".woof/epics/E7/discovery/brainstorm/options.md"],
                next_node="discovery_synthesis",
                stage=1,
            ),
            "planning-node-output",
        ),
        (
            _base_planning_output(
                "discovery_synthesis",
                paths=[
                    ".woof/epics/E7/discovery/synthesis/CONCEPT.md",
                    ".woof/epics/E7/discovery/synthesis/PRINCIPLES.md",
                    ".woof/epics/E7/discovery/synthesis/ARCHITECTURE.md",
                    ".woof/epics/E7/discovery/synthesis/OPEN_QUESTIONS.md",
                ],
                next_node="epic_definition",
                stage=1,
            ),
            "planning-node-output",
        ),
        (
            _base_planning_output(
                "epic_definition",
                paths=[".woof/epics/E7/EPIC.md"],
                next_node="breakdown_planning",
                stage=2,
            ),
            "planning-node-output",
        ),
        (
            _base_planning_output(
                "breakdown_planning",
                paths=[".woof/epics/E7/plan.json", ".woof/epics/E7/PLAN.md"],
                next_node="plan_critique",
                stage=3,
            ),
            "planning-node-output",
        ),
        (
            _base_planning_output(
                "plan_critique",
                paths=[".woof/epics/E7/critique/plan.md"],
                next_node="plan_gate_open",
                stage=3,
            ),
            "planning-node-output",
        ),
        (
            _base_planning_output(
                "plan_gate_open",
                next_node=None,
                stage=4,
                status="gate_opened",
                gate_path=".woof/epics/E7/gate.md",
                paths=[
                    ".woof/epics/E7/plan.json",
                    ".woof/epics/E7/PLAN.md",
                    ".woof/epics/E7/critique/plan.md",
                    ".woof/epics/E7/gate.md",
                ],
                triggered_by=["plan_review"],
            ),
            "planning-node-output",
        ),
        (
            _base_planning_output(
                "plan_gate_resolve",
                next_node=None,
                stage=4,
                paths=[".woof/epics/E7/epic.jsonl"],
            ),
            "planning-node-output",
        ),
    ],
)
def test_validate_planning_node_contract_fixtures(
    tmp_path: Path, run_woof, payload: dict, schema: str
) -> None:
    path = tmp_path / f"{schema}.json"
    path.write_text(json.dumps(payload))
    proc = run_woof("validate", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"valid ({schema})" in proc.stdout


def test_validate_planning_node_rejects_wrong_input_for_node_type(tmp_path: Path, run_woof) -> None:
    payload = _base_planning_input(
        "plan_gate_resolve",
        {
            "gate_path": ".woof/epics/E7/gate.md",
            "triggered_by": ["plan_review"],
        },
    )
    path = tmp_path / "planning-node-input.json"
    path.write_text(json.dumps(payload))
    proc = run_woof("validate", str(path))
    assert proc.returncode == 1
    assert "INVALID" in proc.stdout


# ---------------------------------------------------------------------------
# Epic (YAML front-matter)
# ---------------------------------------------------------------------------


VALID_EPIC = """\
---
epic_id: 1
title: demo epic
observable_outcomes:
  - id: O1
    statement: user can do the thing
    verification: automated
contract_decisions: []
acceptance_criteria:
  - all outcomes verified by tests
---

Free-form prose below the front-matter is ignored by validate.
"""


def test_validate_epic_valid(tmp_path: Path, run_woof) -> None:
    path = tmp_path / "EPIC.md"
    path.write_text(VALID_EPIC)
    proc = run_woof("validate", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (epic)" in proc.stdout


def test_validate_epic_no_front_matter_fails(tmp_path: Path, run_woof) -> None:
    path = tmp_path / "EPIC.md"
    path.write_text("# Just markdown, no front-matter\n")
    proc = run_woof("validate", str(path))
    assert proc.returncode == 1
    assert "no YAML front-matter" in proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Gate (YAML front-matter)
# ---------------------------------------------------------------------------


VALID_GATE = """\
---
type: plan_gate
stage: 4
story_id: null
triggered_by:
  - plan_review
timestamp: "2026-04-26T10:00:00Z"
---

## Context

Plan generated; awaiting operator review.
"""


def test_validate_gate_valid(tmp_path: Path, run_woof) -> None:
    path = tmp_path / "gate.md"
    path.write_text(VALID_GATE)
    proc = run_woof("validate", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (gate)" in proc.stdout


# ---------------------------------------------------------------------------
# Disposition (path-based detection)
# ---------------------------------------------------------------------------


VALID_DISPOSITION = """\
---
target: story
target_id: S1
critique_path: .woof/epics/E7/critique/story-S1.md
severity: minor
timestamp: "2026-01-01T00:00:00Z"
harness: test-primary
dispositions:
  - finding_id: F1
    decision: accepted
    rationale: Updated the staged artefact.
    updated_paths:
      - src/app.py
updated_paths:
  - src/app.py
---

Disposition body.
"""


def test_validate_disposition_via_path(tmp_path: Path, run_woof) -> None:
    disposition_dir = tmp_path / ".woof" / "epics" / "E7" / "dispositions"
    disposition_dir.mkdir(parents=True)
    path = disposition_dir / "story-S1.md"
    path.write_text(VALID_DISPOSITION)
    proc = run_woof("validate", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (disposition)" in proc.stdout


# ---------------------------------------------------------------------------
# Critique (path-based detection)
# ---------------------------------------------------------------------------


VALID_CRITIQUE = """\
---
target: plan
target_id: null
severity: info
timestamp: "2026-04-26T10:05:00Z"
harness: claude-opus-4-7
---

Looks fine.
"""


def test_validate_critique_via_path(tmp_path: Path, run_woof) -> None:
    crit_dir = tmp_path / "critique"
    crit_dir.mkdir()
    path = crit_dir / "plan.md"
    path.write_text(VALID_CRITIQUE)
    proc = run_woof("validate", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (critique)" in proc.stdout


GATE_WITH_UNQUOTED_TIMESTAMP = """\
---
type: plan_gate
stage: 4
story_id: null
triggered_by:
  - plan_review
timestamp: 2026-04-26T10:00:00Z
---

## Context

PyYAML parses bare ISO-8601 strings as datetime objects; the validator
must coerce them back to a JSON-serialisable string before piping to ajv.
"""


def test_validate_gate_unquoted_timestamp(tmp_path: Path, run_woof) -> None:
    """Regression: bare ISO-8601 timestamps in YAML front-matter are parsed
    as datetime objects by PyYAML. The validator must serialise them via
    json.dumps(..., default=str) — otherwise it crashes with TypeError."""
    path = tmp_path / "gate.md"
    path.write_text(GATE_WITH_UNQUOTED_TIMESTAMP)
    proc = run_woof("validate", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (gate)" in proc.stdout


# ---------------------------------------------------------------------------
# JSONL events (per-line)
# ---------------------------------------------------------------------------


def test_validate_jsonl_per_line(tmp_path: Path, run_woof) -> None:
    path = tmp_path / "epic.jsonl"
    lines = [
        {"event": "spark_created", "at": "2026-04-26T10:00:00Z", "epic_id": 1},
        {"event": "current_epic_selected", "at": "2026-04-26T10:00:01Z", "epic_id": 1},
        {"event": "definition_closed", "at": "2026-04-26T10:30:00Z", "epic_id": 1},
        {
            "event": "breakdown_planned",
            "at": "2026-04-26T11:00:00Z",
            "epic_id": 1,
            "paths": [".woof/epics/E1/plan.json", ".woof/epics/E1/PLAN.md"],
        },
        {
            "event": "transaction_manifest_verified",
            "at": "2026-04-26T12:00:00Z",
            "epic_id": 1,
            "story_id": "S1",
            "manifest": {"expected_paths": [".woof/epics/E1/plan.json"]},
        },
        {
            "event": "wf_lock_stale_removed",
            "at": "2026-04-26T12:01:00Z",
            "epic_id": 1,
            "pid": 4242,
            "reason": "pid_not_running",
            "hostname": "build-host",
            "paths": [".woof/epics/E1/.wf.lock"],
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    proc = run_woof("validate", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (jsonl-events, 6 line(s))" in proc.stdout


def test_validate_jsonl_bad_line_fails(tmp_path: Path, run_woof) -> None:
    path = tmp_path / "epic.jsonl"
    path.write_text(
        json.dumps({"event": "not_a_valid_event_kind", "at": "2026-04-26T10:00:00Z"}) + "\n"
    )
    proc = run_woof("validate", str(path))
    assert proc.returncode == 1
    assert "INVALID" in proc.stdout


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def test_no_schema_match_fails(tmp_path: Path, run_woof) -> None:
    path = tmp_path / "random.txt"
    path.write_text("hello")
    proc = run_woof("validate", str(path))
    assert proc.returncode == 1
    assert "no schema rule matches" in proc.stdout


def test_schema_override(tmp_path: Path, run_woof) -> None:
    """--schema lets validate pick a schema for unrecognised filenames."""
    path = tmp_path / "renamed-prereqs.toml"
    path.write_text((REPO_ROOT / ".woof" / "prerequisites.toml").read_text())
    proc = run_woof("validate", "--schema", "prerequisites", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid (prerequisites)" in proc.stdout


def test_missing_ajv_fails_loud(tmp_path: Path, run_woof) -> None:
    """Stripping ajv from PATH must produce a clear install hint, exit 2."""
    uv_path = shutil.which("uv")
    assert uv_path is not None
    (tmp_path / "uv").symlink_to(uv_path)

    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    proc = run_woof("validate", str(REPO_ROOT / ".woof" / "prerequisites.toml"), env=env)
    assert proc.returncode == 2
    assert "ajv-cli not found" in proc.stderr
    assert "volta install ajv-cli" in proc.stderr


def test_missing_subcommand_errors() -> None:
    proc = subprocess.run([str(WOOF_BIN)], capture_output=True, text=True)
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "validate" in combined
