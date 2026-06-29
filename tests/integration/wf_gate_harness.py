"""Reusable CLI harness for Woof workflow acceptance tests."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path
from typing import Any

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


def work_unit_id(prompt: str) -> str:
    match = re.search(r'"work_unit_id":\s*"(S\d+)"', prompt)
    if match:
        return match.group(1)
    match = re.search(r"work-unit-(S\d+)\.md", prompt)
    if match:
        return match.group(1)
    raise SystemExit("work unit id not found in prompt")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git_add(*paths: str) -> None:
    subprocess.run(["git", "add", "--", *paths], check=True)


def read_tmux_prompt() -> tuple[str, Path, Path]:
    print("ready > ", flush=True)
    buf = ""
    for line in sys.stdin:
        buf += line
        prompt = re.search(r"(\S+/prompt\.txt)", buf)
        answer = re.search(r"(\S+/answer\.txt)", buf)
        done = re.search(r"(\S+/answer\.done)", buf)
        if prompt and answer and done:
            text = Path(prompt.group(1)).read_text(encoding="utf-8")
            root = repo_root(text)
            if root is not None:
                os.chdir(root)
            return text, Path(answer.group(1)), Path(done.group(1))
    raise SystemExit("tmux prompt paths not found")


def repo_root(prompt: str) -> str | None:
    launched = os.environ.get("WOOF_REPO_ROOT")
    if launched:
        return launched
    match = re.search(r'"repo_root":\s*"([^"]+)"', prompt)
    if match:
        return match.group(1)
    match = re.search(r"(/[^\"'\s]+?)/\.woof/epics/E\d+/", prompt)
    if match:
        return match.group(1)
    return None


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
    write(base / "ARCHITECTURE.md", "# Architecture\n\nA single work unit exercises gates.\n")
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
    notes: Realised by `schemas/acceptance.schema.json` (forward-created) during work unit S1.
acceptance_criteria:
  - The CLI exposes recoverable gates for failed work-unit execution paths.
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
        "work_units": [
            {
                "id": "S1",
                "title": "Produce gate acceptance artefact",
                "summary": "Create a small artefact, a test marker, and a schema contract.",
                "paths": ["app.py", "tests/test_app.py", "schemas/acceptance.schema.json"],
                "satisfies": ["O1"],
                "implements_contract_decisions": ["CD1"],
                "uses_contract_decisions": [],
                "deps": [],
                "tests": {"count": 1, "types": ["unit"]},
                "state": "pending",
            }
        ],
    }
    write(Path(f".woof/epics/E{eid}/plan.json"), json.dumps(plan, indent=2) + "\n")


def write_work_unit_files() -> None:
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


def execute_work_unit(prompt: str) -> int:
    scenario = os.environ.get("WOOF_GATE_SCENARIO", "happy")
    eid = epic_id(prompt)
    sid = work_unit_id(prompt)
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
                    "work_unit_id": sid,
                    "outcome": "empty_diff",
                    "commit_body": None,
                    "position": "No diff because the outcome was already realised.",
                },
                indent=2,
            )
            + "\n",
        )
        return 0

    write_work_unit_files()
    write(
        Path(f".woof/epics/E{eid}/executor_result.json"),
        json.dumps(
            {
                "epic_id": eid,
                "work_unit_id": sid,
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
    sid = work_unit_id(prompt)
    write(
        Path(f".woof/epics/E{eid}/dispositions/work-unit-{sid}.md"),
        f"""---
target: work_unit
target_id: {sid}
critique_path: .woof/epics/E{eid}/critique/work-unit-{sid}.md
severity: info
timestamp: "{NOW}"
harness: gate-primary
dispositions: []
---

No reviewer findings.
""",
    )


def main() -> int:
    prompt, answer_path, done_path = read_tmux_prompt()
    verdict = "pass"
    evidence = None
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
        code = execute_work_unit(prompt)
        if code != 0:
            verdict = "error"
            evidence = f"executor exited with code {code}"
    elif "Primary disposition prompt" in prompt:
        write_disposition(prompt)
    else:
        raise SystemExit("primary stub did not recognise prompt")
    answer_path.write_text(
        json.dumps(
            {
                "verdict": verdict,
                "evidence": evidence,
                "usage": {"tokens_in": 10, "tokens_out": 5},
                "session": {"thread_id": "gate-thread"},
            }
        ),
        encoding="utf-8",
    )
    done_path.write_text("DONE", encoding="utf-8")
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


def work_unit_id(prompt: str) -> str:
    match = re.search(r'"work_unit_id":\s*"(S\d+)"', prompt)
    if match:
        return match.group(1)
    raise SystemExit("work unit id not found in prompt")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_tmux_prompt() -> tuple[str, Path, Path]:
    print("ready > ", flush=True)
    buf = ""
    for line in sys.stdin:
        buf += line
        prompt = re.search(r"(\S+/prompt\.txt)", buf)
        answer = re.search(r"(\S+/answer\.txt)", buf)
        done = re.search(r"(\S+/answer\.done)", buf)
        if prompt and answer and done:
            text = Path(prompt.group(1)).read_text(encoding="utf-8")
            root = repo_root(text)
            if root is not None:
                os.chdir(root)
            return text, Path(answer.group(1)), Path(done.group(1))
    raise SystemExit("tmux prompt paths not found")


def repo_root(prompt: str) -> str | None:
    launched = os.environ.get("WOOF_REPO_ROOT")
    if launched:
        return launched
    match = re.search(r'"repo_root":\s*"([^"]+)"', prompt)
    if match:
        return match.group(1)
    match = re.search(r"(/[^\"'\s]+?)/\.woof/epics/E\d+/", prompt)
    if match:
        return match.group(1)
    return None


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


def write_work_unit_critique(prompt: str) -> None:
    eid = epic_id(prompt)
    sid = work_unit_id(prompt)
    if os.environ.get("WOOF_GATE_SCENARIO") == "reviewer_blocker":
        severity = "blocker"
        findings = (
            "findings:\n"
            "  - id: F1\n"
            "    severity: blocker\n"
            "    summary: missing recovery assertion\n"
            "    evidence: tests/test_app.py:1 lacks the required recovery marker\n"
        )
        body = "Work unit must prove recovery before commit."
    else:
        severity = "info"
        findings = "findings: []\n"
        body = "Work unit is ready."
    write(
        Path(f".woof/epics/E{eid}/critique/work-unit-{sid}.md"),
        f"""---
target: work_unit
target_id: {sid}
severity: {severity}
timestamp: "{NOW}"
harness: gate-reviewer
{findings}---

{body}
""",
    )


def main() -> int:
    prompt, answer_path, done_path = read_tmux_prompt()
    if '"node_type": "plan_critique"' in prompt:
        write_plan_critique(prompt)
    elif '"node_type": "critique_dispatch"' in prompt:
        write_work_unit_critique(prompt)
    else:
        raise SystemExit("reviewer stub did not recognise prompt")
    answer_path.write_text(
        json.dumps(
            {
                "verdict": "pass",
                "usage": {"tokens_in": 10, "tokens_out": 5},
                "session": {"id": "00000000-0000-0000-0000-000000000002"},
            }
        ),
        encoding="utf-8",
    )
    done_path.write_text("DONE", encoding="utf-8")
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
    write_executable(bin_dir / "cld", REVIEWER_STUB)


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

    (consumer / ".woof" / "policy.toml").write_text(
        """\
schema_version = 1
default_run_profile = "acceptance"

[delivery]
profile = "B"
repo_root = "."
toolchain_root = "."
base_branch = "main"

[profiles.B]
commit = true
push = false

[verification]
command = "python -m py_compile app.py tests/test_app.py"
timeout_seconds = 30

[run_profiles.acceptance.producer]
harness = "codex"
model = "gpt-5.5"
effort = "xhigh"

[run_profiles.acceptance.reviewer]
harness = "claude"
model = "claude-opus-4-7"
effort = "max"

[checks]
floor = [
  "quality-gates",
  "outcome-markers",
  "scope",
  "contract-refs",
  "plan-crossrefs",
  "critique-blocker",
  "commit-transaction",
  "docs-drift",
  "review-valve",
]

[cartography]
floor = "structural"
""",
        encoding="utf-8",
    )
    (consumer / ".woof" / "agents.toml").write_text(
        """\

[timeouts]
default_minutes = 5

[review_valve]
every_n_work_units = 5
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
    codebase_dir = consumer / ".woof" / "codebase"
    codebase_dir.mkdir(parents=True, exist_ok=True)
    for _doc in [
        "CURRENT-ARCHITECTURE.md",
        "STACK.md",
        "INTEGRATIONS.md",
        "STRUCTURE.md",
        "CONVENTIONS.md",
        "TESTING.md",
        "CONCERNS.md",
        "TARGET-ARCHITECTURE.md",
        "PRINCIPLES.md",
    ]:
        (codebase_dir / _doc).write_text(
            f"# {_doc}\n\nStub for integration test.\n", encoding="utf-8"
        )
    (codebase_dir / "files.txt").write_text("")
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


def json_stdout(proc: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in proc.stdout.splitlines() if line]


def jsonl(path: Path) -> list[dict[str, Any]]:
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
    work_unit_id: str | None = "S1",
) -> None:
    gate = epic_dir(consumer) / "gate.md"
    assert gate.is_file()
    front = gate_front(consumer)
    assert front["type"] == gate_type
    assert front["triggered_by"] == triggered_by
    assert front.get("work_unit_id") == work_unit_id
    text = gate.read_text(encoding="utf-8")
    assert "## Context" in text
    assert "## Findings" in text
    assert "## Primary position" in text
    assert "## Reviewer position" in text


def latest_commit_subject(consumer: Path, env: dict[str, str]) -> str:
    proc = run(["git", "log", "-1", "--pretty=%s"], cwd=consumer, env=env)
    assert_ok(proc)
    return proc.stdout.strip()
