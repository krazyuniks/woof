"""Reusable CLI harness for Woof workflow acceptance tests."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"

PRIMARY_STUB = r'''#!/usr/bin/env python3
from __future__ import annotations

import json
import os
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
        f"# {bucket.title()}\n\nGate acceptance input for E{eid}.\n",
    )


def write_synthesis(prompt: str) -> None:
    eid = epic_id(prompt)
    base = Path(f".woof/epics/E{eid}/discovery/synthesis")
    write(base / "CONCEPT.md", "# Concept\n\n## Problem Framing\n\nProve gate recovery.\n")
    write(base / "PRINCIPLES.md", "# Principles\n\n- Keep gate recovery deterministic.\n")
    write(base / "ARCHITECTURE.md", "# Architecture\n\nA single story exercises gates.\n")
    write(base / "OPEN_QUESTIONS.md", "# Open Questions\n\nNo open questions.\n")


def write_epic(prompt: str) -> None:
    eid = epic_id(prompt)
    write(
        Path(f".woof/epics/E{eid}/EPIC.md"),
        f"""---
epic_id: {eid}
title: Gate recovery acceptance
intent: Prove Woof opens and resumes gate and commit recovery paths.
observable_outcomes:
  - id: O1
    statement: The gate acceptance artefact is produced or explicitly skipped.
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes: [O1]
    title: Gate acceptance artefact schema
    json_schema_ref: schemas/acceptance.schema.json
    notes: Realised by `schemas/acceptance.schema.json` (forward-created) during story S1.
acceptance_criteria:
  - The CLI exposes recoverable gates for failed story execution paths.
open_questions: []
resolved_open_questions: []
---

# Gate recovery acceptance
""",
    )


def write_plan(prompt: str) -> None:
    eid = epic_id(prompt)
    plan = {
        "epic_id": eid,
        "goal": "Prove gate and interrupted-commit recovery through the CLI.",
        "stories": [
            {
                "id": "S1",
                "title": "Produce gate acceptance artefact",
                "intent": "Create a small artefact, a test marker, and a schema contract.",
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


def write_story_files() -> None:
    write(
        Path("app.py"),
        'def acceptance_message() -> str:\n    return "woof gate acceptance complete"\n',
    )
    write(
        Path("tests/test_app.py"),
        '"""outcomes: O1"""\n\n'
        "from app import acceptance_message\n\n\n"
        "def test_acceptance_message_marks_completion() -> None:\n"
        '    assert acceptance_message() == "woof gate acceptance complete"\n',
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
                "examples": [{"message": "woof gate acceptance complete"}],
            },
            indent=2,
        )
        + "\n",
    )
    git_add("app.py", "tests/test_app.py", "schemas/acceptance.schema.json")


def execute_story(prompt: str) -> int:
    scenario = os.environ.get("WOOF_GATE_SCENARIO", "happy")
    eid = epic_id(prompt)
    sid = story_id(prompt)
    if scenario == "subprocess_crash":
        sys.stderr.write("executor exploded before writing a result\n")
        return 42
    if scenario == "malformed_state":
        write(Path(f".woof/epics/E{eid}/executor_result.json"), "{")
        return 0
    if scenario == "empty_diff":
        write(
            Path(f".woof/epics/E{eid}/executor_result.json"),
            json.dumps(
                {
                    "epic_id": eid,
                    "story_id": sid,
                    "outcome": "empty_diff",
                    "commit_body": None,
                    "position": "No diff because the outcome was already realised.",
                },
                indent=2,
            )
            + "\n",
        )
        return 0

    write_story_files()
    write(
        Path(f".woof/epics/E{eid}/executor_result.json"),
        json.dumps(
            {
                "epic_id": eid,
                "story_id": sid,
                "outcome": "staged_for_verification",
                "commit_subject": "feat: add gate acceptance artefact",
                "commit_body": "Adds a small artefact, test marker, and JSON Schema contract.",
                "position": None,
            },
            indent=2,
        )
        + "\n",
    )
    return 0


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
harness: gate-primary
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
        code = execute_story(prompt)
        if code != 0:
            return code
    elif "Primary disposition prompt" in prompt:
        write_disposition(prompt)
    else:
        raise SystemExit("primary stub did not recognise prompt")
    print(json.dumps({"type": "thread.started", "thread_id": "gate-thread"}))
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
import os
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
harness: gate-reviewer
findings: []
---

Plan is executable.
""",
    )


def write_story_critique(prompt: str) -> None:
    eid = epic_id(prompt)
    sid = story_id(prompt)
    if os.environ.get("WOOF_GATE_SCENARIO") == "reviewer_blocker":
        severity = "blocker"
        findings = (
            "findings:\n"
            "  - id: F1\n"
            "    severity: blocker\n"
            "    summary: missing recovery assertion\n"
            "    evidence: tests/test_app.py lacks the required recovery marker\n"
        )
        body = "Story must prove recovery before commit."
    else:
        severity = "info"
        findings = "findings: []\n"
        body = "Story is ready."
    write(
        Path(f".woof/epics/E{eid}/critique/story-{sid}.md"),
        f"""---
target: story
target_id: {sid}
severity: {severity}
timestamp: "{NOW}"
harness: gate-reviewer
{findings}---

{body}
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
                "session_id": "00000000-0000-0000-0000-000000000002",
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


def require_host_tools() -> None:
    for tool in ("uv", "ajv", "git"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for workflow acceptance")


def run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


def assert_ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, proc.stdout + proc.stderr


def write_executable(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def write_cli_stubs(bin_dir: Path) -> None:
    bin_dir.mkdir()
    write_executable(bin_dir / "codex", PRIMARY_STUB)
    write_executable(bin_dir / "claude", REVIEWER_STUB)


def acceptance_env(tmp_path: Path, scenario: str) -> dict[str, str]:
    stub_bin = tmp_path / "bin"
    write_cli_stubs(stub_bin)
    env = os.environ.copy()
    env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"
    env["WOOF_GATE_SCENARIO"] = scenario
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def configure_consumer(
    consumer: Path,
    env: dict[str, str],
    *,
    quality_gate_command: str = "python -m py_compile app.py tests/test_app.py",
) -> None:
    assert_ok(run(["git", "init"], cwd=consumer, env=env))
    assert_ok(run(["git", "config", "user.email", "test@example.com"], cwd=consumer, env=env))
    assert_ok(run(["git", "config", "user.name", "Workflow Test"], cwd=consumer, env=env))
    assert_ok(run([str(WOOF_BIN), "init", "--tracker", "local"], cwd=consumer, env=env))
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
        f"[gates.compile]\ncommand = {json.dumps(quality_gate_command)}\ntimeout_seconds = 30\n",
        encoding="utf-8",
    )
    assert_ok(run(["git", "add", ".gitignore", ".woof"], cwd=consumer, env=env))
    assert_ok(run(["git", "commit", "-m", "chore: bootstrap woof"], cwd=consumer, env=env))


def create_stage5_consumer(
    tmp_path: Path,
    *,
    scenario: str,
    quality_gate_command: str = "python -m py_compile app.py tests/test_app.py",
) -> tuple[Path, dict[str, str]]:
    require_host_tools()
    env = acceptance_env(tmp_path, scenario)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    configure_consumer(consumer, env, quality_gate_command=quality_gate_command)

    created = run(
        [str(WOOF_BIN), "wf", "new", "ship gate acceptance artefact", "--format", "json"],
        cwd=consumer,
        env=env,
    )
    assert_ok(created)
    assert json.loads(created.stdout)["epic_id"] == 1

    planned = run([str(WOOF_BIN), "wf", "--epic", "1", "--format", "json"], cwd=consumer, env=env)
    assert_ok(planned)
    planned_events = json_stdout(planned)
    assert planned_events[-1]["node_type"] == "plan_gate_open"
    assert planned_events[-1]["status"] == "gate_opened"

    approved = run(
        [str(WOOF_BIN), "wf", "--epic", "1", "--resolve", "approve"],
        cwd=consumer,
        env=env,
    )
    assert_ok(approved)
    return consumer, env


def json_stdout(proc: subprocess.CompletedProcess[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in proc.stdout.splitlines() if line]


def jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def gate_front(consumer: Path, epic_id: int = 1) -> dict[str, object]:
    gate = consumer / ".woof" / "epics" / f"E{epic_id}" / "gate.md"
    text = gate.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    assert end > 0
    front = yaml.safe_load(text[4:end]) or {}
    assert isinstance(front, dict)
    return front


def epic_dir(consumer: Path, epic_id: int = 1) -> Path:
    return consumer / ".woof" / "epics" / f"E{epic_id}"


def assert_gate(
    consumer: Path,
    *,
    gate_type: str,
    triggered_by: list[str],
    story_id: str | None = "S1",
) -> None:
    gate = epic_dir(consumer) / "gate.md"
    assert gate.is_file()
    front = gate_front(consumer)
    assert front["type"] == gate_type
    assert front["triggered_by"] == triggered_by
    assert front.get("story_id") == story_id
    text = gate.read_text(encoding="utf-8")
    assert "## Context" in text
    assert "## Findings" in text
    assert "## Primary position" in text
    assert "## Reviewer position" in text


def latest_commit_subject(consumer: Path, env: dict[str, str]) -> str:
    proc = run(["git", "log", "-1", "--pretty=%s"], cwd=consumer, env=env)
    assert_ok(proc)
    return proc.stdout.strip()
