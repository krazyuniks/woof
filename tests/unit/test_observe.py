from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


def _write_project(tmp_path: Path, *, with_usage: bool = True) -> Path:
    project = tmp_path / "project"
    epic_dir = project / ".woof" / "epics" / "E5"
    audit_dir = epic_dir / "audit"
    raw_dir = audit_dir / "raw"
    raw_dir.mkdir(parents=True)
    (project / ".woof" / "agents.toml").write_text(
        """\
[audit]
max_bytes = 180
"""
    )
    (epic_dir / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": 5,
                "goal": "Expose workflow state.",
                "stories": [
                    {
                        "id": "S1",
                        "title": "Add reporting",
                        "intent": "Make state inspectable.",
                        "paths": ["src/woof/**/*.py"],
                        "satisfies": ["O1"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "depends_on": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "status": "in_progress",
                    }
                ],
            }
        )
    )
    (epic_dir / "gate.md").write_text(
        """---
type: story_gate
stage: 6
story_id: S1
triggered_by:
  - check_1_quality_gates
timestamp: '2026-05-23T10:02:00Z'
---

## Context

Quality failed.

## Findings

- check_1_quality_gates: lint failed

## Primary position

Fix the lint issue.

## Reviewer position

No separate reviewer position was available.
"""
    )
    (epic_dir / "epic.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "definition_closed",
                        "at": "2026-05-23T10:00:00Z",
                        "epic_id": 5,
                    }
                ),
                json.dumps(
                    {
                        "event": "story_gate_opened",
                        "at": "2026-05-23T10:02:00Z",
                        "epic_id": 5,
                        "story_id": "S1",
                        "gate_type": "story_gate",
                        "triggered_by": ["check_1_quality_gates"],
                    }
                ),
            ]
        )
        + "\n"
    )
    returned: dict[str, object] = {
        "event": "subprocess_returned",
        "at": "2026-05-23T10:01:30Z",
        "epic_id": 5,
        "story_id": "S1",
        "role": "primary",
        "adapter": "codex",
        "model": "gpt-5.5",
        "effort": "xhigh",
        "pid": 123,
        "exit_code": 0,
        "duration_ms": 1400,
        "codex_audit_path": ".woof/epics/E5/audit/codex-primary-run",
        "artefacts_loaded": [".woof/epics/E5/plan.json"],
    }
    if with_usage:
        returned.update(
            {
                "tokens_in": 100,
                "tokens_out": 25,
                "cache_read_tokens": 10,
                "cost_usd": 0.031,
            }
        )
    (epic_dir / "dispatch.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "subprocess_spawned",
                        "at": "2026-05-23T10:01:00Z",
                        "epic_id": 5,
                        "story_id": "S1",
                        "role": "primary",
                        "adapter": "codex",
                        "pid": 123,
                    }
                ),
                json.dumps(returned),
                json.dumps(
                    {
                        "event": "subprocess_returned",
                        "at": "2026-05-23T10:01:45Z",
                        "epic_id": 5,
                        "story_id": "S1",
                        "role": "reviewer",
                        "adapter": "claude",
                        "pid": 124,
                        "exit_code": 0,
                        "duration_ms": 900,
                        "claude_transcript_path": (
                            "~/.claude/projects/-tmp-project/"
                            "00000000-0000-0000-0000-000000000001.jsonl"
                        ),
                    }
                ),
            ]
        )
        + "\n"
    )
    (audit_dir / "codex-primary-run.prompt").write_text(
        "Bearer [REDACTED:bearer_token]\n"
        "... [truncated, full output at .woof/epics/E5/audit/raw/codex-primary-run.prompt]\n"
    )
    (raw_dir / "codex-primary-run.prompt").write_text("full raw output\n")
    return project


def _run_observe(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), "observe", "--epic", "5", *args],
        cwd=project,
        capture_output=True,
        text=True,
    )


def test_observe_all_json_reports_status_gate_timeline_and_audit(tmp_path: Path) -> None:
    project = _write_project(tmp_path)

    proc = _run_observe(project, "--view", "all", "--format", "json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"]["next"] == {
        "node": "human_review",
        "story_id": None,
        "reason": "gate_open",
    }
    assert payload["gate"]["open"] is True
    assert payload["gate"]["sections"]["Context"] == "Quality failed."
    assert [event["event"] for event in payload["timeline"]] == [
        "definition_closed",
        "subprocess_spawned",
        "subprocess_returned",
        "subprocess_returned",
        "story_gate_opened",
    ]
    assert payload["audit"]["raw_overflow_file_count"] == 1
    assert payload["audit"]["redacted_file_count"] == 1
    assert payload["audit"]["truncated_file_count"] == 1
    assert payload["audit"]["retention_archive"]["implemented"] is False
    assert payload["audit"]["usage"] == {
        "token_events": 1,
        "tokens": {
            "tokens_in": 100,
            "tokens_out": 25,
            "cache_read_tokens": 10,
            "cache_write_tokens": 0,
        },
        "cost_events": 1,
        "cost": {"cost_usd": 0.031},
    }
    returned = payload["audit"]["dispatch"]["returned_events"]
    assert returned[0]["tokens"] == {
        "tokens_in": 100,
        "tokens_out": 25,
        "cache_read_tokens": 10,
    }
    assert returned[0]["cost"] == {"cost_usd": 0.031}
    assert "tokens" not in returned[1]
    assert "cost" not in returned[1]


def test_observe_does_not_invent_usage_when_dispatch_events_do_not_report_it(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path, with_usage=False)

    proc = _run_observe(project, "--view", "audit", "--format", "json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["usage"] == {
        "token_events": 0,
        "tokens": {},
        "cost_events": 0,
        "cost": {},
    }
    assert "tokens" not in payload["dispatch"]["returned_events"][0]
    assert "cost" not in payload["dispatch"]["returned_events"][0]


def test_observe_audit_text_reports_raw_overflow_and_no_archive(tmp_path: Path) -> None:
    project = _write_project(tmp_path, with_usage=False)

    proc = _run_observe(project, "--view", "audit")

    assert proc.returncode == 0, proc.stderr
    assert "raw_overflow=1" in proc.stdout
    assert "retention_archive: not implemented" in proc.stdout
    assert "tokens: unavailable" in proc.stdout
    assert "cost: unavailable" in proc.stdout
