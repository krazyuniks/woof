"""CLI acceptance coverage for the core Woof workflow."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"

pytestmark = pytest.mark.host_only

PRIMARY_STUB = r'''#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


NOW = "2026-05-23T00:00:00Z"


def epic_id(prompt: str) -> int:
    match = re.search(r'"epic_id":\s*(\d+)', prompt)
    if match:
        return int(match.group(1))
    match = re.search(r"\.woof/epics/E(\d+)/", prompt)
    if match:
        return int(match.group(1))
    raise SystemExit("epic id not found in prompt")


def story_id(prompt: str) -> str:
    match = re.search(r'"story_id":\s*"(S\d+)"', prompt)
    if match:
        return match.group(1)
    match = re.search(r"story-(S\d+)\.md", prompt)
    if match:
        return match.group(1)
    raise SystemExit("story id not found in prompt")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git_add(*paths: str) -> None:
    subprocess.run(["git", "add", "--", *paths], check=True)


def discovery_bucket(prompt: str) -> str | None:
    for bucket in ("research", "thinking", "ideate"):
        if f'"node_type": "discovery_{bucket}"' in prompt:
            return bucket
    return None


def write_discovery_bucket(prompt: str) -> None:
    eid = epic_id(prompt)
    bucket = discovery_bucket(prompt)
    assert bucket is not None
    write(
        Path(f".woof/epics/E{eid}/discovery/{bucket}/{bucket}.md"),
        f"# {bucket.title()}\n\nAcceptance input for E{eid}.\n",
    )


def write_synthesis(prompt: str) -> None:
    eid = epic_id(prompt)
    base = Path(f".woof/epics/E{eid}/discovery/synthesis")
    write(base / "CONCEPT.md", "# Concept\n\n## Problem Framing\n\nProve the CLI loop.\n")
    write(base / "PRINCIPLES.md", "# Principles\n\n- Keep the graph deterministic.\n")
    write(base / "ARCHITECTURE.md", "# Architecture\n\nA single story lands the artefact.\n")
    write(base / "OPEN_QUESTIONS.md", "# Open Questions\n\nNo open questions.\n")


def write_epic(prompt: str) -> None:
    eid = epic_id(prompt)
    write(
        Path(f".woof/epics/E{eid}/EPIC.md"),
        f"""---
epic_id: {eid}
title: CLI workflow acceptance
intent: Prove Woof can drive a complete local-tracker software delivery loop.
observable_outcomes:
  - id: O1
    statement: The acceptance artefact is produced by the story executor.
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes: [O1]
    title: Acceptance artefact schema
    json_schema_ref: schemas/acceptance.schema.json
acceptance_criteria:
  - The story commit contains application code, a test marker, and the schema contract.
open_questions: []
resolved_open_questions: []
---

# CLI workflow acceptance
""",
    )


def write_plan(prompt: str) -> None:
    eid = epic_id(prompt)
    plan = {
        "epic_id": eid,
        "goal": "Prove the local-tracker Woof workflow can reach a checked commit.",
        "stories": [
            {
                "id": "S1",
                "title": "Produce acceptance artefact",
                "intent": "Create a small application artefact, a test marker, and a schema contract.",
                "paths": ["app.py", "tests/test_app.py", "schemas/acceptance.schema.json"],
                "satisfies": ["O1"],
                "implements_contract_decisions": ["CD1"],
                "uses_contract_decisions": [],
                "depends_on": [],
                "tests": {"count": 1, "types": ["unit"]},
                "status": "pending",
            }
        ],
    }
    write(Path(f".woof/epics/E{eid}/plan.json"), json.dumps(plan, indent=2) + "\n")


def execute_story(prompt: str) -> None:
    eid = epic_id(prompt)
    sid = story_id(prompt)
    write(
        Path("app.py"),
        'def acceptance_message() -> str:\n    return "woof acceptance complete"\n',
    )
    write(
        Path("tests/test_app.py"),
        '"""outcomes: O1"""\n\n'
        "from app import acceptance_message\n\n\n"
        "def test_acceptance_message_marks_completion() -> None:\n"
        '    assert acceptance_message() == "woof acceptance complete"\n',
    )
    write(
        Path("schemas/acceptance.schema.json"),
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["message"],
                "properties": {"message": {"type": "string"}},
                "examples": [{"message": "woof acceptance complete"}],
            },
            indent=2,
        )
        + "\n",
    )
    git_add("app.py", "tests/test_app.py", "schemas/acceptance.schema.json")
    write(
        Path(f".woof/epics/E{eid}/executor_result.json"),
        json.dumps(
            {
                "epic_id": eid,
                "story_id": sid,
                "outcome": "staged_for_verification",
                "commit_subject": "feat: add workflow acceptance artefact",
                "commit_body": "Adds a small application artefact, test marker, and JSON Schema contract.",
                "position": None,
            },
            indent=2,
        )
        + "\n",
    )


def write_disposition(prompt: str) -> None:
    eid = epic_id(prompt)
    sid = story_id(prompt)
    write(
        Path(f".woof/epics/E{eid}/dispositions/story-{sid}.md"),
        f"""---
target: story
target_id: {sid}
critique_path: .woof/epics/E{eid}/critique/story-{sid}.md
severity: info
timestamp: "{NOW}"
harness: acceptance-primary
dispositions: []
---

No reviewer findings.
""",
    )


def main() -> int:
    prompt = sys.stdin.read()
    bucket = discovery_bucket(prompt)
    if bucket:
        write_discovery_bucket(prompt)
    elif '"node_type": "discovery_synthesis"' in prompt:
        write_synthesis(prompt)
    elif '"node_type": "epic_definition"' in prompt:
        write_epic(prompt)
    elif '"node_type": "breakdown_planning"' in prompt:
        write_plan(prompt)
    elif '"node_type": "executor_dispatch"' in prompt:
        execute_story(prompt)
    elif "Primary disposition prompt" in prompt:
        write_disposition(prompt)
    else:
        raise SystemExit("primary stub did not recognise prompt")
    print(json.dumps({"type": "thread.started", "thread_id": "acceptance-thread"}))
    print(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 0,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

REVIEWER_STUB = r'''#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


NOW = "2026-05-23T00:00:00Z"


def epic_id(prompt: str) -> int:
    match = re.search(r'"epic_id":\s*(\d+)', prompt)
    if match:
        return int(match.group(1))
    raise SystemExit("epic id not found in prompt")


def story_id(prompt: str) -> str:
    match = re.search(r'"story_id":\s*"(S\d+)"', prompt)
    if match:
        return match.group(1)
    raise SystemExit("story id not found in prompt")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_plan_critique(prompt: str) -> None:
    eid = epic_id(prompt)
    write(
        Path(f".woof/epics/E{eid}/critique/plan.md"),
        f"""---
target: plan
target_id: null
severity: info
timestamp: "{NOW}"
harness: acceptance-reviewer
findings: []
---

Plan is executable.
""",
    )


def write_story_critique(prompt: str) -> None:
    eid = epic_id(prompt)
    sid = story_id(prompt)
    write(
        Path(f".woof/epics/E{eid}/critique/story-{sid}.md"),
        f"""---
target: story
target_id: {sid}
severity: info
timestamp: "{NOW}"
harness: acceptance-reviewer
findings: []
---

Story is ready.
""",
    )


def main() -> int:
    prompt = sys.stdin.read()
    if '"node_type": "plan_critique"' in prompt:
        write_plan_critique(prompt)
    elif '"node_type": "critique_dispatch"' in prompt:
        write_story_critique(prompt)
    else:
        raise SystemExit("reviewer stub did not recognise prompt")
    print(
        json.dumps(
            {
                "type": "result",
                "session_id": "00000000-0000-0000-0000-000000000001",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} is required for workflow acceptance")


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


def _assert_ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _write_executable(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_cli_stubs(bin_dir: Path) -> None:
    bin_dir.mkdir()
    _write_executable(bin_dir / "codex", PRIMARY_STUB)
    _write_executable(bin_dir / "claude", REVIEWER_STUB)


def _acceptance_env(tmp_path: Path, *, isolated: bool = False) -> dict[str, str]:
    stub_bin = tmp_path / "bin"
    _write_cli_stubs(stub_bin)
    env = os.environ.copy()
    if isolated:
        for key in ("PYTHONPATH", "WOOF_TOOL_ROOT", "VIRTUAL_ENV"):
            env.pop(key, None)
        env["HOME"] = str(tmp_path / "home")
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        env.setdefault("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def _run_woof(
    woof_cmd: list[str],
    *args: str,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return _run([*woof_cmd, *args], cwd=cwd, env=env)


def _configure_consumer(consumer: Path, env: dict[str, str], woof_cmd: list[str]) -> None:
    _assert_ok(_run(["git", "init"], cwd=consumer, env=env))
    _assert_ok(_run(["git", "config", "user.email", "test@example.com"], cwd=consumer, env=env))
    _assert_ok(_run(["git", "config", "user.name", "Workflow Test"], cwd=consumer, env=env))
    _assert_ok(_run_woof(woof_cmd, "init", "--tracker", "local", cwd=consumer, env=env))
    gitignore = consumer / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "\n__pycache__/\n*.pyc\n")

    (consumer / ".woof" / "agents.toml").write_text(
        """\
[roles.primary]
adapter = "codex"
model = "gpt-5.5"
effort = "xhigh"

[roles.reviewer]
adapter = "claude"
model = "claude-opus-4-7"
effort = "max"
mcp = []

[roles.gate-resolver]
adapter = "in-session"

[timeouts]
default_minutes = 5

[review_valve]
every_n_stories = 5
end_of_epic = false

[audit]
enabled = true
max_bytes = 262144
redact_patterns = []
""",
        encoding="utf-8",
    )
    (consumer / ".woof" / "quality-gates.toml").write_text(
        """\
[gates.compile]
command = "python -m py_compile app.py tests/test_app.py"
timeout_seconds = 30
""",
        encoding="utf-8",
    )
    _assert_ok(_run(["git", "add", ".gitignore", ".woof"], cwd=consumer, env=env))
    _assert_ok(_run(["git", "commit", "-m", "chore: bootstrap woof"], cwd=consumer, env=env))


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _install_wheel(tmp_path: Path, env: dict[str, str]) -> Path:
    dist_dir = tmp_path / "dist"
    build = _run(
        ["uv", "build", "--wheel", "-o", str(dist_dir), str(REPO_ROOT)], cwd=tmp_path, env=env
    )
    _assert_ok(build)
    wheels = sorted(dist_dir.glob("woof-*.whl"))
    assert wheels, build.stdout + build.stderr

    venv = tmp_path / "venv"
    created = _run(["uv", "venv", str(venv)], cwd=tmp_path, env=env)
    _assert_ok(created)
    python = venv / "bin" / "python"
    installed = _run(
        ["uv", "pip", "install", "--python", str(python), str(wheels[-1])], cwd=tmp_path, env=env
    )
    _assert_ok(installed)
    return python


def _drive_local_tracker_workflow(consumer: Path, env: dict[str, str], woof_cmd: list[str]) -> None:
    created = _run_woof(
        woof_cmd,
        "wf",
        "new",
        "ship acceptance artefact",
        "--format",
        "json",
        cwd=consumer,
        env=env,
    )
    _assert_ok(created)
    assert json.loads(created.stdout)["epic_id"] == 1

    planned = _run_woof(woof_cmd, "wf", "--epic", "1", "--format", "json", cwd=consumer, env=env)
    _assert_ok(planned)
    planned_events = [json.loads(line) for line in planned.stdout.splitlines() if line]
    assert planned_events[-1]["status"] == "gate_opened"
    assert planned_events[-1]["node_type"] == "plan_gate_open"

    gate = consumer / ".woof" / "epics" / "E1" / "gate.md"
    assert gate.is_file()

    approved = _run_woof(
        woof_cmd, "wf", "--epic", "1", "--resolve", "approve", cwd=consumer, env=env
    )
    _assert_ok(approved)
    assert not gate.exists()

    executed = _run_woof(woof_cmd, "wf", "--epic", "1", "--format", "json", cwd=consumer, env=env)
    _assert_ok(executed)
    executed_events = [json.loads(line) for line in executed.stdout.splitlines() if line]
    assert executed_events[-1]["status"] == "epic_complete"

    log = _run(["git", "log", "--oneline", "-1"], cwd=consumer, env=env)
    _assert_ok(log)
    assert "feat: add workflow acceptance artefact" in log.stdout

    committed_files = _run(["git", "show", "--name-only", "--format="], cwd=consumer, env=env)
    _assert_ok(committed_files)
    committed = set(committed_files.stdout.splitlines())
    assert {
        "app.py",
        "tests/test_app.py",
        "schemas/acceptance.schema.json",
        ".woof/epics/E1/EPIC.md",
        ".woof/epics/E1/plan.json",
        ".woof/epics/E1/critique/story-S1.md",
        ".woof/epics/E1/dispositions/story-S1.md",
    } <= committed

    dispatch_events = _jsonl(consumer / ".woof" / "epics" / "E1" / "dispatch.jsonl")
    spawned = [event for event in dispatch_events if event.get("event") == "subprocess_spawned"]
    assert len(spawned) >= 8
    assert {event["role"] for event in spawned} >= {"primary", "reviewer"}
    assert all(event["runtime_policy"]["mode"] == "trusted-local" for event in spawned)

    epic_events = _jsonl(consumer / ".woof" / "epics" / "E1" / "epic.jsonl")
    assert any(event.get("event") == "plan_gate_resolved" for event in epic_events)
    assert any(event.get("event") == "transaction_manifest_verified" for event in epic_events)
    assert any(event.get("event") == "epic_completed" for event in epic_events)


def test_wf_cli_drives_local_tracker_epic_to_story_commit(tmp_path: Path) -> None:
    """The source-checkout CLI can drive the product loop from spark to checked commit."""

    for tool in ("uv", "ajv", "git"):
        _require_tool(tool)

    env = _acceptance_env(tmp_path)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    woof_cmd = [str(WOOF_BIN)]
    _configure_consumer(consumer, env, woof_cmd)

    _drive_local_tracker_workflow(consumer, env, woof_cmd)


def test_installed_package_wf_cli_drives_local_tracker_epic_to_story_commit(tmp_path: Path) -> None:
    """The installed package can drive the same workflow without checkout wrappers."""

    for tool in ("uv", "ajv", "git"):
        _require_tool(tool)

    env = _acceptance_env(tmp_path, isolated=True)
    python = _install_wheel(tmp_path, env)
    woof_cmd = [str(python), "-m", "woof"]

    consumer = tmp_path / "installed-consumer"
    consumer.mkdir()
    _configure_consumer(consumer, env, woof_cmd)

    _drive_local_tracker_workflow(consumer, env, woof_cmd)
