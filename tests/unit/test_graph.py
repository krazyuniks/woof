from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from woof.graph.git import git_env
from woof.graph.manifest import build_story_manifest, verify_staged_manifest
from woof.graph.runner import run_graph
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, StorySpec
from woof.graph.transitions import StageStateError, epic_dir, mark_story_status, next_node

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


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


def _run_woof(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def _git(root: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(["git", *args], cwd=root, env=git_env(), **kwargs)


def _init_git_repo(root: Path) -> None:
    _git(root, "init", check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com", check=True)
    _git(root, "config", "user.name", "Test", check=True)


def _read_gate_fm(gate_path: Path) -> dict:
    text = gate_path.read_text()
    return yaml.safe_load(text[4 : text.find("\n---\n", 4)])


def _assert_node_output_schema(tmp_path: Path, payload: dict) -> None:
    path = tmp_path / "node-output.json"
    path.write_text(json.dumps(payload))
    proc = subprocess.run(
        [str(WOOF_BIN), "validate", "--schema", "node-output", str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


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
  - Outcome verified.
---
Test epic intent.
"""
    )


def _write_last_sync(directory: Path, epic_id: int, *, body: str = "<previous>") -> None:
    directory.joinpath(".last-sync").write_text(
        json.dumps(
            {
                "issue_number": epic_id,
                "updated_at": "2026-01-01T00:00:00Z",
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "body": body,
            }
        )
        + "\n"
    )


def _make_gh_completion_stub(bin_dir: Path) -> dict[str, str]:
    bin_dir.mkdir(parents=True, exist_ok=True)
    last_body = bin_dir / "_last_body"
    closed = bin_dir / "_closed"
    before = json.dumps(
        {
            "updated_at": "2026-01-01T00:00:00Z",
            "body": "Remote intent.\n\n## Observable Outcomes\n\n- stale\n",
            "state": "open",
        }
    )
    after_edit = json.dumps(
        {"updated_at": "2026-01-02T00:00:00Z", "body": "<post-edit>", "state": "open"}
    )
    after_close = json.dumps(
        {"updated_at": "2026-01-03T00:00:00Z", "body": "<post-close>", "state": "closed"}
    )
    script = bin_dir / "gh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'mode="$1"; shift\n'
        'case "$mode" in\n'
        "  api)\n"
        f'    if [[ -f "{closed}" ]]; then\n'
        f"      printf '%s' '{after_close}'\n"
        f'    elif [[ -f "{last_body}" ]]; then\n'
        f"      printf '%s' '{after_edit}'\n"
        "    else\n"
        f"      printf '%s' '{before}'\n"
        "    fi\n"
        "    ;;\n"
        "  issue)\n"
        '    sub="$1"; shift\n'
        '    case "$sub" in\n'
        "      edit)\n"
        '        body_file=""\n'
        "        while [[ $# -gt 0 ]]; do\n"
        '          case "$1" in\n'
        '            --body-file) body_file="$2"; shift 2;;\n'
        "            *) shift;;\n"
        "          esac\n"
        "        done\n"
        f'        cp "$body_file" "{last_body}"\n'
        "        ;;\n"
        "      close)\n"
        f'        printf "closed\\n" > "{closed}"\n'
        "        ;;\n"
        "      *) exit 2;;\n"
        "    esac\n"
        "    ;;\n"
        "  *) exit 2;;\n"
        "esac\n"
    )
    script.chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", "/tmp"),
    }


def _write_ready_commit_state(root: Path, epic_id: int = 1) -> Path:
    directory = _write_plan(root, epic_id)
    plan = json.loads((directory / "plan.json").read_text())
    plan["stories"][0]["status"] = "done"
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "dispatch.jsonl").write_text("{}\n")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": epic_id,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": True,
                "stage": 5,
                "epic_id": epic_id,
                "story_id": "S1",
                "triggered_by": [],
                "checks": [],
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text("---\nseverity: info\n---\n")
    audit_dir = directory / "audit"
    audit_dir.mkdir()
    (audit_dir / "cod-critiquer-1.prompt").write_text("prompt")
    src = root / "src"
    src.mkdir()
    (src / "app.py").write_text("print('O1')\n")
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


def test_graph_resumes_interrupted_commit_transaction(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    (directory / "epic.jsonl").write_text(
        json.dumps({"event": "story_completed", "epic_id": 1, "story_id": "S1"}) + "\n"
    )

    assert next_node(tmp_path, 1) == (NodeType.COMMIT, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].node_type == NodeType.COMMIT
    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events].count("story_completed") == 1
    assert [event["event"] for event in events].count("transaction_manifest_verified") == 1
    assert not (directory / "executor_result.json").exists()
    assert not (directory / "check-result.json").exists()
    status = _git(
        tmp_path,
        "status",
        "--porcelain=v1",
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_commit_redacts_audit_before_staging_transaction(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    (tmp_path / ".woof" / "agents.toml").write_text(
        """\
[roles.story-executor]
harness = "cld"

[roles.critiquer]
harness = "cod"

[audit]
max_bytes = 4096
"""
    )
    audit_file = directory / "audit" / "cod-critiquer-1.prompt"
    audit_file.write_text("call API with Bearer live-oauth-token\n")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].node_type == NodeType.COMMIT
    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE
    text = audit_file.read_text()
    assert "live-oauth-token" not in text
    assert "[REDACTED:bearer_token]" in text
    committed = _git(
        tmp_path,
        "show",
        "HEAD:.woof/epics/E1/audit/cod-critiquer-1.prompt",
        check=True,
        capture_output=True,
        text=True,
    )
    assert "live-oauth-token" not in committed.stdout


def test_complete_epic_cleans_stale_transient_files(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_ready_commit_state(tmp_path, 1)
    _git(
        tmp_path,
        "add",
        "src/app.py",
        ".woof/epics/E1/plan.json",
        ".woof/epics/E1/epic.jsonl",
        ".woof/epics/E1/dispatch.jsonl",
        ".woof/epics/E1/critique/story-S1.md",
        ".woof/epics/E1/audit/cod-critiquer-1.prompt",
        check=True,
    )
    _git(tmp_path, "commit", "-m", "seed", check=True, capture_output=True)

    outputs = run_graph(tmp_path, 1)

    assert outputs[-1].status == NodeStatus.EPIC_COMPLETE
    assert not (directory / "executor_result.json").exists()
    assert not (directory / "check-result.json").exists()


def test_in_progress_story_missing_executor_result_opens_incomplete_state_gate(
    tmp_path: Path,
) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")

    assert next_node(tmp_path, 1) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs == [
        NodeOutput(
            node_type=NodeType.GATE_OPEN,
            status=NodeStatus.GATE_OPENED,
            epic_id=1,
            story_id="S1",
            gate_path=".woof/epics/E1/gate.md",
            triggered_by=["incomplete_stage_state"],
            message="Required Stage-5 artefact missing: .woof/epics/E1/executor_result.json",
        )
    ]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_in_progress_story_malformed_executor_result_opens_incomplete_state_gate(
    tmp_path: Path,
) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text("{")

    assert next_node(tmp_path, 1) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].triggered_by == ["incomplete_stage_state"]
    assert "malformed JSON" in outputs[0].message
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_malformed_check_result_opens_incomplete_state_gate(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text("---\nseverity: info\n---\n")
    (directory / "check-result.json").write_text("{")

    assert next_node(tmp_path, 1) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].triggered_by == ["incomplete_stage_state"]
    assert "check-result.json" in outputs[0].message
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["incomplete_stage_state"]


def test_failed_check_result_reopens_structured_gate_on_reentry(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 1)
    mark_story_status(tmp_path, 1, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text("---\nseverity: blocker\n---\n")
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 1,
                "story_id": "S1",
                "triggered_by": ["check_6_critique_blocker"],
                "checks": [
                    {
                        "id": "check_6_critique_blocker",
                        "ok": False,
                        "severity": "blocker",
                        "summary": "critique severity is blocker",
                        "evidence": None,
                        "paths": [],
                        "command": None,
                        "exit_code": None,
                    }
                ],
            }
        )
    )

    assert next_node(tmp_path, 1) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 1)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].triggered_by == ["check_6_critique_blocker"]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["check_6_critique_blocker"]


def test_successor_selection_respects_dependency_closure(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 12)
    plan = json.loads((directory / "plan.json").read_text())
    plan["stories"] = [
        {
            **plan["stories"][0],
            "id": "S1",
            "title": "first",
            "status": "done",
            "depends_on": [],
        },
        {
            **plan["stories"][0],
            "id": "S2",
            "title": "second",
            "status": "pending",
            "depends_on": ["S1"],
        },
    ]
    (directory / "plan.json").write_text(json.dumps(plan))

    assert next_node(tmp_path, 12) == (NodeType.EXECUTOR_DISPATCH, "S2")


def test_successor_selection_fails_loud_when_dependencies_are_unsatisfied(
    tmp_path: Path,
) -> None:
    directory = _write_plan(tmp_path, 13)
    plan = json.loads((directory / "plan.json").read_text())
    plan["stories"][0]["depends_on"] = ["S99"]
    (directory / "plan.json").write_text(json.dumps(plan))

    try:
        next_node(tmp_path, 13)
    except StageStateError as exc:
        assert "no story has satisfied dependencies" in str(exc)
    else:
        raise AssertionError("expected StageStateError")


def test_gate_reentry_halts_at_human_review_with_gate_path(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 14)
    (directory / "gate.md").write_text("---\ntype: story_gate\n---\n")

    assert next_node(tmp_path, 14) == (NodeType.HUMAN_REVIEW, None)

    outputs = run_graph(tmp_path, 14)

    assert outputs == [
        NodeOutput(
            node_type=NodeType.HUMAN_REVIEW,
            status=NodeStatus.HALTED,
            epic_id=14,
            gate_path=".woof/epics/E14/gate.md",
            message="gate open at .woof/epics/E14/gate.md",
        )
    ]


def test_empty_diff_executor_result_opens_review_gate(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 15)
    mark_story_status(tmp_path, 15, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 15,
                "story_id": "S1",
                "outcome": "empty_diff",
                "commit_body": None,
                "position": "No diff was needed.",
            }
        )
    )

    assert next_node(tmp_path, 15) == (NodeType.GATE_OPEN, "S1")

    outputs = run_graph(tmp_path, 15)

    assert outputs[0].status == NodeStatus.GATE_OPENED
    assert outputs[0].gate_path == ".woof/epics/E15/gate.md"
    assert outputs[0].triggered_by == ["empty_diff_review"]
    gate_fm = _read_gate_fm(directory / "gate.md")
    assert gate_fm["triggered_by"] == ["empty_diff_review"]


def test_wf_epic_reports_complete_epic_as_json(tmp_path: Path) -> None:
    (tmp_path / ".woof").mkdir(exist_ok=True)
    (tmp_path / ".woof" / "prerequisites.toml").write_text('[github]\nrepo = "acme/widgets"\n')
    directory = _write_plan(tmp_path, 7)
    plan = json.loads((directory / "plan.json").read_text())
    plan["stories"][0]["status"] = "done"
    (directory / "plan.json").write_text(json.dumps(plan))
    _write_minimal_epic(directory, 7)
    remote_body = "Remote intent.\n\n## Observable Outcomes\n\n- stale\n"
    _write_last_sync(directory, 7, body=remote_body)
    env = _make_gh_completion_stub(tmp_path / "bin")

    proc = _run_woof(tmp_path, "wf", "--epic", "7", "--format", "json", env=env)

    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert lines == [
        {
            "node_type": "human_review",
            "status": "epic_complete",
            "epic_id": 7,
            "story_id": None,
            "next_node": None,
            "gate_path": None,
            "validation_summary": None,
            "triggered_by": [],
            "message": "E7 complete",
            "paths": [],
        }
    ]
    _assert_node_output_schema(tmp_path, lines[0])


def test_wf_reports_missing_plan_as_structured_failure(tmp_path: Path) -> None:
    (tmp_path / ".woof" / "epics" / "E10").mkdir(parents=True)

    proc = _run_woof(tmp_path, "wf", "--epic", "10")

    assert proc.returncode == 2
    assert "woof wf: incomplete_stage_state:" in proc.stderr
    assert "plan.json" in proc.stderr


def test_wf_epic_halts_when_gate_is_open(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 8)
    (directory / "gate.md").write_text("---\ntype: story_gate\n---\n")

    proc = _run_woof(tmp_path, "wf", "--epic", "8")

    assert proc.returncode == 0, proc.stderr
    assert "woof wf: human_review -> halted: gate open at .woof/epics/E8/gate.md" in proc.stdout


def test_wf_gate_case_reports_stable_json_contract(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 11)
    mark_story_status(tmp_path, 11, "S1", "in_progress")
    (directory / "executor_result.json").write_text(
        json.dumps(
            {
                "epic_id": 11,
                "story_id": "S1",
                "outcome": "staged_for_verification",
                "commit_body": "done",
                "position": None,
            }
        )
    )
    critique_dir = directory / "critique"
    critique_dir.mkdir()
    (critique_dir / "story-S1.md").write_text("---\nseverity: blocker\n---\n")
    (directory / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 11,
                "story_id": "S1",
                "triggered_by": ["check_6_critique_blocker"],
                "checks": [
                    {
                        "id": "check_6_critique_blocker",
                        "ok": False,
                        "severity": "blocker",
                        "summary": "critique severity is blocker",
                        "evidence": None,
                        "paths": [],
                        "command": None,
                        "exit_code": None,
                    }
                ],
            }
        )
    )

    proc = _run_woof(tmp_path, "wf", "--epic", "11", "--format", "json")

    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert lines == [
        {
            "node_type": "gate_open",
            "status": "gate_opened",
            "epic_id": 11,
            "story_id": "S1",
            "next_node": None,
            "gate_path": ".woof/epics/E11/gate.md",
            "validation_summary": {
                "ok": False,
                "stage": 5,
                "triggered_by": ["check_6_critique_blocker"],
                "check_count": 1,
                "failed_check_count": 1,
            },
            "triggered_by": ["check_6_critique_blocker"],
            "message": "",
            "paths": [],
        }
    ]
    _assert_node_output_schema(tmp_path, lines[0])


def test_wf_resolve_records_gate_decision_and_removes_gate(tmp_path: Path) -> None:
    directory = _write_plan(tmp_path, 9)
    gate = directory / "gate.md"
    gate.write_text("---\ntype: story_gate\n---\n")

    proc = _run_woof(tmp_path, "wf", "--epic", "9", "--resolve", "approve")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "woof wf: gate resolved decision=approve\n"
    assert not gate.exists()
    events = [json.loads(line) for line in (directory / "epic.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "gate_resolved"
    assert events[-1]["epic_id"] == 9
    assert events[-1]["decision"] == "approve"


def test_transaction_manifest_requires_audit_and_rejects_extra_staged_file(tmp_path: Path) -> None:
    _git(tmp_path, "init", check=True, capture_output=True)
    _git(tmp_path, "config", "user.email", "test@example.com", check=True)
    _git(tmp_path, "config", "user.name", "Test", check=True)
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

    _git(tmp_path, "add", "--", *manifest.expected_paths, "extra.txt", check=True)
    result = verify_staged_manifest(tmp_path, manifest)

    assert result.ok is False
    assert result.extra_paths == ["extra.txt"]


def test_transaction_manifest_reports_missing_expected_index_paths(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    directory = _write_plan(tmp_path, 16)
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

    story = StorySpec(
        id="S1",
        title="first",
        paths=["src/*.py"],
        satisfies=["O1"],
        status="in_progress",
    )
    manifest = build_story_manifest(tmp_path, 16, story)
    staged_subset = [
        path for path in manifest.expected_paths if not path.endswith("dispatch.jsonl")
    ]
    _git(tmp_path, "add", "--", *staged_subset, check=True)

    result = verify_staged_manifest(tmp_path, manifest)

    assert result.ok is False
    assert result.missing_paths == [".woof/epics/E16/dispatch.jsonl"]
