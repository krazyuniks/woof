"""Efficiency benchmark harness tests."""

from __future__ import annotations

import json
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from woof.bench.efficiency import (
    TERMINAL_STATUSES,
    _quality_outcome,
    collect_run_manifest,
    comparison_rows,
    create_worktree,
    redact_manifest,
    remove_worktree,
    render_comparison_markdown,
    resolve_git_sha,
    seed_epic_fixture,
)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _assert_ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _git(repo: Path, *args: str) -> str:
    proc = _run(["git", *args], cwd=repo)
    _assert_ok(proc)
    return proc.stdout.strip()


def _init_repo(repo: Path) -> str:
    repo.mkdir()
    _assert_ok(_run(["git", "init"], cwd=repo))
    _assert_ok(_run(["git", "config", "user.email", "bench@example.com"], cwd=repo))
    _assert_ok(_run(["git", "config", "user.name", "Bench Test"], cwd=repo))
    (repo / "README.md").write_text("# consumer\n", encoding="utf-8")
    _assert_ok(_run(["git", "add", "README.md"], cwd=repo))
    _assert_ok(_run(["git", "commit", "-m", "chore: base"], cwd=repo))
    return resolve_git_sha(repo, "HEAD")


def _epic_fixture(path: Path) -> Path:
    path.write_text(
        """\
---
epic_id: 1
title: Small valid efficiency benchmark
intent: Measure a small valid epic.
observable_outcomes:
  - id: O1
    statement: A tiny benchmark note helper reports measured status.
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes: [O1]
    title: Benchmark note result schema
    json_schema_ref: schemas/bench-note.schema.json
acceptance_criteria:
  - The helper, test marker, and schema contract exist.
open_questions: []
resolved_open_questions: []
---

# Fixture
""",
        encoding="utf-8",
    )
    return path


def test_packaged_small_valid_epic_keeps_consumer_story_behaviour_scoped() -> None:
    text = Path("examples/efficiency/small-valid-epic/EPIC.md").read_text(encoding="utf-8")

    assert "helper in the consumer source reports measured status" in text
    assert "test calls the helper and asserts the measured status result" in text
    assert "must not inspect Woof runtime files under .woof/" in text
    assert "benchmark harness records graph state" in text
    assert "The manifest records graph state" not in text


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, *events: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def test_manifest_aggregation_and_comparison(tmp_path: Path) -> None:
    repo = tmp_path / "consumer"
    base_sha = _init_repo(repo)
    epic_fixture = _epic_fixture(tmp_path / "EPIC.md")
    epic_id = seed_epic_fixture(repo, epic_fixture=epic_fixture, stub_models=True)
    epic_dir = repo / ".woof" / "epics" / "E1"

    _write_json(
        epic_dir / "plan.json",
        {
            "epic_id": 1,
            "goal": "Measure the harness.",
            "work_units": [
                {
                    "id": "S1",
                    "title": "Add helper",
                    "summary": "Create the helper output.",
                    "paths": [
                        "bench_note.py",
                        "tests/test_bench_note.py",
                        "schemas/bench-note.schema.json",
                    ],
                    "satisfies": ["O1"],
                    "implements_contract_decisions": ["CD1"],
                    "uses_contract_decisions": [],
                    "deps": [],
                    "tests": {"count": 1},
                    "status": "done",
                }
            ],
        },
    )
    (epic_dir / "critique").mkdir(exist_ok=True)
    (epic_dir / "critique" / "story-S1.md").write_text(
        """\
---
target: story
target_id: S1
severity: info
timestamp: "2026-05-26T00:00:00Z"
findings: []
---

Looks fine.
""",
        encoding="utf-8",
    )
    (repo / "bench_note.py").write_text(
        'def benchmark_note() -> dict[str, str]:\n    return {"status": "measured"}\n',
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_bench_note.py").write_text('"""outcomes: O1"""\n', encoding="utf-8")
    (repo / "schemas").mkdir()
    _write_json(
        repo / "schemas" / "bench-note.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"status": {"const": "measured"}},
        },
    )
    _append_jsonl(
        epic_dir / "epic.jsonl",
        {"event": "definition_closed", "at": "2026-05-26T00:00:00Z", "epic_id": 1},
        {
            "event": "plan_critiqued",
            "at": "2026-05-26T00:00:01Z",
            "epic_id": 1,
            "severity": "info",
        },
        {
            "event": "plan_gate_resolved",
            "at": "2026-05-26T00:00:02Z",
            "epic_id": 1,
            "decision": "approve",
            "gate_type": "plan_gate",
            "triggered_by": ["plan_review"],
        },
        {
            "event": "epic_completed",
            "at": "2026-05-26T00:00:03Z",
            "epic_id": 1,
        },
    )
    _append_jsonl(
        epic_dir / "dispatch.jsonl",
        {
            "event": "subprocess_spawned",
            "at": "2026-05-26T00:00:00Z",
            "epic_id": 1,
            "role": "primary",
            "adapter": "codex",
            "model_profile": "stub",
            "model": "stub-primary",
            "effort": "low",
            "prompt_bytes": 100,
            "artefact_bytes": 50,
        },
        {
            "event": "subprocess_killed",
            "at": "2026-05-26T00:00:01Z",
            "epic_id": 1,
            "pid": 321,
            "signal": "SIGTERM",
            "reason": "completed_lingering",
            "exit_type": "completed_lingering",
        },
        {
            "event": "subprocess_returned",
            "at": "2026-05-26T00:00:01Z",
            "epic_id": 1,
            "role": "primary",
            "adapter": "codex",
            "model_profile": "stub",
            "model": "stub-primary",
            "effort": "low",
            "duration_ms": 250,
            "exit_type": "completed_lingering",
            "exit_code": -15,
            "tokens_in": 10,
            "tokens_out": 5,
            "cache_read_tokens": 3,
            "prompt_bytes": 100,
            "artefact_bytes": 50,
            "output_bytes": 20,
            "stderr_bytes": 0,
            "command_count": 2,
        },
        {
            "event": "subprocess_returned",
            "at": "2026-05-26T00:00:02Z",
            "epic_id": 1,
            "role": "reviewer",
            "adapter": "claude",
            "model_profile": "stub",
            "model": "stub-reviewer",
            "effort": "low",
            "duration_ms": 100,
            "exit_type": "clean",
            "exit_code": 0,
            "tokens_in": 4,
            "tokens_out": 2,
            "cache_write_tokens": 1,
            "prompt_bytes": 80,
            "artefact_bytes": 60,
            "output_bytes": 10,
            "stderr_bytes": 0,
        },
    )
    _assert_ok(
        _run(
            [
                "git",
                "add",
                ".woof",
                "bench_note.py",
                "tests/test_bench_note.py",
                "schemas/bench-note.schema.json",
            ],
            cwd=repo,
        )
    )
    _assert_ok(_run(["git", "commit", "-m", "feat: add helper"], cwd=repo))
    _write_json(
        epic_dir / "check-result.json",
        {"ok": True, "stage": 5, "story_id": "S1", "checks": []},
    )

    started = datetime(2026, 5, 26, 0, 0, tzinfo=UTC)
    manifest = collect_run_manifest(
        repo_root=repo,
        epic_id=epic_id,
        scenario_id="small-valid-epic",
        variant_id="stub-a",
        run_id="run-1",
        woof_sha="abc123",
        woof_dirty=False,
        consumer_base_sha=base_sha,
        branch="bench/small-valid-epic/stub-a/run-1",
        command_outputs=[
            {"node_type": "epic_definition", "status": "completed"},
            {"node_type": "human_review", "status": "epic_complete"},
        ],
        commands=[{"kind": "wf", "exit_code": 0}],
        run_started_at=started,
        run_ended_at=started,
        run_exit_code=0,
        operator_notes="Bearer live-token",
    )

    assert manifest["variant"]["woof_sha"] == "abc123"
    assert manifest["variant"]["model_profile"] == "stub"
    assert manifest["git"]["consumer_base_sha"] == base_sha
    assert manifest["git"]["consumer_result_sha"]
    assert manifest["route_policy"]["available"] is True
    assert manifest["route_policy"]["dispatch_routes"]["model_profile"] == "stub"
    assert manifest["story_statuses"] == [
        {"id": "S1", "title": "Add helper", "status": "done", "satisfies": ["O1"]}
    ]
    assert manifest["dispatch"]["returned"] == 2
    assert manifest["dispatch"]["successful"] == 2
    assert manifest["dispatch"]["failed"] == 0
    assert manifest["dispatch"]["killed"] == 0
    assert manifest["dispatch"]["tokens"] == {
        "tokens_in": 14,
        "tokens_out": 7,
        "cache_read_tokens": 3,
        "cache_write_tokens": 1,
    }
    assert manifest["dispatch"]["telemetry"]["command_count"] == 2
    assert manifest["dispatch"]["events"][0]["model_profile"] == "stub"
    assert manifest["dispatch"]["events"][0]["exit_type"] == "completed_lingering"
    assert manifest["dispatch"]["events"][0]["tokens"]["tokens_in"] == 10
    assert manifest["dispatch"]["by_route"][0]["model_profile"] == "stub"
    assert manifest["diff"]["committed"]["file_count"] >= 4
    assert manifest["diff"]["pathscope"]["ok"] is True
    assert manifest["quality_outcome"]["status"] == "passed"
    assert "live-token" not in json.dumps(manifest)
    assert "[REDACTED:bearer_token]" in manifest["quality_outcome"]["operator_notes"]

    slower = json.loads(json.dumps(manifest))
    slower["variant"]["id"] = "stub-b"
    slower["dispatch"]["tokens"]["tokens_in"] = 99
    rows = comparison_rows([manifest, slower])
    assert [row["variant"] for row in rows] == ["stub-a", "stub-b"]
    assert rows[0]["model_profile"] == "stub"
    assert rows[0]["tokens_in"] == 14
    assert rows[1]["tokens_in"] == 99
    table = render_comparison_markdown([manifest, slower])
    assert "| small-valid-epic | stub-a | stub | passed | epic_complete | 2 | 14 |" in table
    assert "| small-valid-epic | stub-b | stub | passed | epic_complete | 2 | 99 |" in table


def test_throwaway_worktrees_start_from_same_base_and_do_not_share_woof_state(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "consumer"
    base_sha = _init_repo(repo)
    fixture = _epic_fixture(tmp_path / "EPIC.md")
    parent = tmp_path / "worktrees"
    worktrees = []

    try:
        first = create_worktree(
            consumer_repo=repo,
            consumer_base_sha=base_sha,
            scenario_id="small-valid-epic",
            variant_id="stub-a",
            run_id="same-run",
            worktree_parent=parent,
        )
        second = create_worktree(
            consumer_repo=repo,
            consumer_base_sha=base_sha,
            scenario_id="small-valid-epic",
            variant_id="stub-b",
            run_id="same-run",
            worktree_parent=parent,
        )
        worktrees.extend([first, second])

        seed_epic_fixture(first.path, epic_fixture=fixture, stub_models=True)
        dirty = first.path / ".woof" / "epics" / "E1" / "audit" / "dirty.txt"
        dirty.parent.mkdir(parents=True)
        dirty.write_text("variant-local runtime state\n", encoding="utf-8")

        seed_epic_fixture(second.path, epic_fixture=fixture, stub_models=True)

        assert _git(first.path, "rev-parse", "--verify", "HEAD") == base_sha
        assert _git(second.path, "rev-parse", "--verify", "HEAD") == base_sha
        assert dirty.is_file()
        assert not (second.path / ".woof" / "epics" / "E1" / "audit" / "dirty.txt").exists()
        assert (second.path / ".woof" / "epics" / "E1" / "EPIC.md").read_text(
            encoding="utf-8"
        ) == fixture.read_text(encoding="utf-8")
    finally:
        for worktree in worktrees:
            remove_worktree(worktree)


def test_stub_quality_gate_is_plan_neutral_for_src_and_tests_paths(tmp_path: Path) -> None:
    repo = tmp_path / "consumer"
    _init_repo(repo)
    fixture = _epic_fixture(tmp_path / "EPIC.md")
    seed_epic_fixture(repo, epic_fixture=fixture, stub_models=True)
    (repo / "src" / "specwright").mkdir(parents=True)
    (repo / "src" / "specwright" / "benchmark.py").write_text(
        "def fixture_benchmark_note() -> dict[str, object]:\n    return {'measured': True}\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_benchmark_note.py").write_text(
        '"""outcomes: O1"""\n\n'
        "from specwright.benchmark import fixture_benchmark_note\n\n\n"
        "def test_fixture_benchmark_note_reports_measured() -> None:\n"
        "    assert fixture_benchmark_note()['measured'] is True\n",
        encoding="utf-8",
    )
    config = tomllib.loads((repo / ".woof" / "quality-gates.toml").read_text())
    command = config["gates"]["compile"]["command"]

    proc = subprocess.run(command, cwd=repo, shell=True, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "bench_note.py tests/test_bench_note.py" not in command


def test_manifest_redaction_covers_sensitive_fields_and_known_patterns() -> None:
    redacted = redact_manifest(
        {
            "operator_notes": "Bearer abc.def",
            "metadata": {"secret": "plain-value", "tokens_in": 42},
            "argv": ["cmd", "--api-key=live-secret"],
        }
    )
    text = json.dumps(redacted)
    assert "abc.def" not in text
    assert "plain-value" not in text
    assert "live-secret" not in text
    assert redacted["metadata"]["tokens_in"] == 42


def test_epic_abandoned_final_state_is_terminal_and_classified(tmp_path: Path) -> None:
    # The run loop stops once the final node-output status is terminal; an
    # abandoned epic emits status "epic_abandoned" (E17 P4 / D-AB).
    assert "epic_abandoned" in TERMINAL_STATUSES

    # Runner path: the abandoned terminal surfaces as last_status=epic_abandoned.
    by_last_status = _quality_outcome(
        final_state={
            "last_node": "human_review",
            "last_status": "epic_abandoned",
            "next": None,
            "gate_open": False,
        },
        checks={},
        diff={},
        run_exit_code=0,
        epic_events=[],
        repo_root=tmp_path,
        epic_id=1,
        operator_notes=None,
    )
    assert by_last_status["reason"] == "epic_abandoned"
    assert by_last_status["status"] == "abandoned"
    assert by_last_status["status"] not in {"failed", "incomplete"}

    # Observe path: the derived next-node is epic_abandoned; the classifier honours
    # it via the same branch (mirroring the epic_complete next-node case).
    by_next_node = _quality_outcome(
        final_state={
            "last_node": "human_review",
            "last_status": "completed",
            "next": {"node": "epic_abandoned", "story_id": None},
            "gate_open": False,
        },
        checks={},
        diff={},
        run_exit_code=0,
        epic_events=[],
        repo_root=tmp_path,
        epic_id=1,
        operator_notes=None,
    )
    assert by_next_node["reason"] == "epic_abandoned"
    assert by_next_node["status"] == "abandoned"
