"""Black-box tests for ``woof dispatch``.

Most tests use ``--dry-run`` so they do not actually spawn cld/cod subprocesses.
Token-parser helpers are exercised via fixtures of recorded harness output.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


pytestmark = pytest.mark.host_only


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def woof_project(tmp_path: Path) -> Path:
    """Skeleton woof project: ``.woof/agents.toml`` with the standard roles."""
    project = tmp_path / "proj"
    woof_dir = project / ".woof"
    woof_dir.mkdir(parents=True)
    (woof_dir / "agents.toml").write_text("""\
[roles.story-executor]
harness = "cld"
model = "claude-sonnet-4-6"
think = true
mcp = ["chrome-devtools"]
flags = ["--max-turns", "20"]

[roles.critiquer]
harness = "cod"
model = "gpt-5.3-codex"

[roles.gate-resolver]
harness = "in-session"

[timeouts]
default_minutes = 15
""")
    return project


def run_dispatch(
    project: Path,
    *args: str,
    stdin: str = "do the thing\n",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), "dispatch", *args],
        capture_output=True,
        text=True,
        input=stdin,
        cwd=project,
    )


# ---------------------------------------------------------------------------
# argv construction (via --dry-run)
# ---------------------------------------------------------------------------


def test_dry_run_claude_argv(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "claude",
        "--role",
        "story-executor",
        "--epic",
        "42",
        "--story",
        "S3",
        "--dry-run",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["argv"] == [
        "timeout",
        "15m",
        "cld",
        "-t",
        "-m",
        "chrome-devtools",
        "--",
        "-p",
        "--output-format",
        "json",
        "--model",
        "claude-sonnet-4-6",
        "--max-turns",
        "20",
        "do the thing\n",
    ]
    assert payload["epic"] == 42
    assert payload["story"] == "S3"
    assert payload["harness"] == "cld"
    assert payload["timeout_min"] == 15


def test_dry_run_codex_argv(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "codex",
        "--role",
        "critiquer",
        "--epic",
        "42",
        "--dry-run",
        stdin="critique this\n",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["argv"] == [
        "timeout",
        "15m",
        "cod",
        "--",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--model",
        "gpt-5.3-codex",
        "critique this\n",
    ]
    assert payload["story"] is None
    assert payload["harness"] == "cod"


def test_prompt_file_overrides_stdin(woof_project: Path, tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("from file")
    proc = run_dispatch(
        woof_project,
        "claude",
        "--role",
        "story-executor",
        "--epic",
        "1",
        "--prompt-file",
        str(prompt_file),
        "--dry-run",
        stdin="ignored",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["argv"][-1] == "from file"


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def test_missing_woof_root(tmp_path: Path) -> None:
    proc = run_dispatch(
        tmp_path,
        "claude",
        "--role",
        "story-executor",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "no .woof/ directory" in proc.stderr


def test_missing_agents_toml(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / ".woof").mkdir(parents=True)
    proc = run_dispatch(
        project,
        "claude",
        "--role",
        "story-executor",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "agents.toml" in proc.stderr


def test_unknown_role(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "claude",
        "--role",
        "ghost",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "role 'ghost' not declared" in proc.stderr


def test_in_session_role_rejected(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "claude",
        "--role",
        "gate-resolver",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "in-session" in proc.stderr


def test_harness_mismatch(woof_project: Path) -> None:
    """Calling 'dispatch claude' on a cod-harness role is a hard error."""
    proc = run_dispatch(
        woof_project,
        "claude",
        "--role",
        "critiquer",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "expected 'cld'" in proc.stderr


def test_invalid_story_id(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "claude",
        "--role",
        "story-executor",
        "--epic",
        "1",
        "--story",
        "story-1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "S<n>" in proc.stderr


def test_empty_prompt(woof_project: Path) -> None:
    proc = run_dispatch(
        woof_project,
        "claude",
        "--role",
        "story-executor",
        "--epic",
        "1",
        "--dry-run",
        stdin="   \n",
    )
    assert proc.returncode == 2
    assert "empty prompt" in proc.stderr


def test_schema_invalid_agents_toml(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / ".woof").mkdir(parents=True)
    (project / ".woof" / "agents.toml").write_text("""\
[roles.story-executor]
harness = "wrong-harness"
""")
    proc = run_dispatch(
        project,
        "claude",
        "--role",
        "story-executor",
        "--epic",
        "1",
        "--dry-run",
    )
    assert proc.returncode == 2
    assert "schema invalid" in proc.stderr


# ---------------------------------------------------------------------------
# token parsing — exercised by importing the module
# ---------------------------------------------------------------------------


def _import_woof_module():
    """Import the CLI module that owns dispatch token parsing."""
    from woof.cli import main

    return main


def test_parse_claude_output() -> None:
    mod = _import_woof_module()
    line = json.dumps(
        {
            "type": "result",
            "session_id": "80e44829-ba61-4640-960c-a3445110b9c3",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 136,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 45262,
            },
        }
    )
    tokens, session = mod.parse_claude_output(line + "\n")
    assert session == "80e44829-ba61-4640-960c-a3445110b9c3"
    assert tokens == {
        "tokens_in": 10,
        "tokens_out": 136,
        "cache_read_tokens": 5,
        "cache_write_tokens": 45262,
    }


def test_parse_claude_output_empty() -> None:
    mod = _import_woof_module()
    tokens, session = mod.parse_claude_output("")
    assert tokens == {}
    assert session is None


def test_parse_codex_output() -> None:
    mod = _import_woof_module()
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "019dc9a3-1bd8"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 30,
                        "output_tokens": 50,
                        "reasoning_output_tokens": 20,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 200,
                        "cached_input_tokens": 0,
                        "output_tokens": 60,
                        "reasoning_output_tokens": 0,
                    },
                }
            ),
        ]
    )
    tokens, thread = mod.parse_codex_output(stream)
    assert thread == "019dc9a3-1bd8"
    assert tokens == {
        "tokens_in": 300,
        "tokens_out": 130,  # 50 + 20 + 60
        "cache_read_tokens": 30,
    }


def test_parse_codex_output_no_turns() -> None:
    mod = _import_woof_module()
    line = json.dumps({"type": "thread.started", "thread_id": "abc"})
    tokens, thread = mod.parse_codex_output(line)
    assert tokens == {}
    assert thread == "abc"


def test_build_argv_minimal_role() -> None:
    """A role with no model, mcp, think, or flags produces a minimal argv."""
    mod = _import_woof_module()
    argv = mod.build_argv("cld", {"harness": "cld"}, "hi")
    assert argv == ["cld", "--", "-p", "--output-format", "json", "hi"]

    argv = mod.build_argv("cod", {"harness": "cod"}, "hi")
    assert argv == ["cod", "--", "exec", "--json", "--skip-git-repo-check", "hi"]


# ---------------------------------------------------------------------------
# end-to-end with a stub harness on PATH
# ---------------------------------------------------------------------------


SCHEMA_JSONL_EVENTS = REPO_ROOT / "schemas" / "jsonl-events.schema.json"
WOOF_VALIDATE = [str(WOOF_BIN), "validate", "--schema", "jsonl-events"]


def _make_stub(bin_dir: Path, name: str, payload: str) -> None:
    """Write an executable shell script at ``bin_dir/name`` that prints ``payload``."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\ncat <<'__WOOF_PAYLOAD__'\n{payload}\n__WOOF_PAYLOAD__\n")
    script.chmod(0o755)


def test_end_to_end_claude_writes_audit_and_jsonl(woof_project: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    claude_response = json.dumps(
        {
            "type": "result",
            "session_id": "00000000-0000-0000-0000-000000000001",
            "usage": {
                "input_tokens": 7,
                "output_tokens": 11,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 13,
            },
        }
    )
    _make_stub(bin_dir, "cld", claude_response)
    # ``timeout`` is needed in PATH too — let it resolve from the original PATH.
    env = {
        "PATH": f"{bin_dir}:{__import__('os').environ['PATH']}",
        "HOME": __import__("os").environ.get("HOME", str(tmp_path)),
    }

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "claude",
            "--role",
            "story-executor",
            "--epic",
            "7",
            "--story",
            "S2",
        ],
        capture_output=True,
        text=True,
        input="run the story\n",
        cwd=woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    # Audit artefacts written
    audit_dir = woof_project / ".woof" / "epics" / "E7" / "audit"
    files = sorted(p.name for p in audit_dir.iterdir())
    suffixes = {Path(f).suffix for f in files}
    assert {".prompt", ".output", ".stderr", ".meta"} <= suffixes

    meta_file = next(audit_dir.glob("*.meta"))
    meta = json.loads(meta_file.read_text())
    assert meta["harness"] == "cld"
    assert meta["role"] == "story-executor"
    assert meta["epic_id"] == 7
    assert meta["story_id"] == "S2"
    assert meta["exit_code"] == 0
    assert meta["tokens"] == {
        "tokens_in": 7,
        "tokens_out": 11,
        "cache_read_tokens": 0,
        "cache_write_tokens": 13,
    }
    assert meta["cc_session_id"] == "00000000-0000-0000-0000-000000000001"

    # dispatch.jsonl events validate against the shipped schema
    jsonl = woof_project / ".woof" / "epics" / "E7" / "dispatch.jsonl"
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    events = [json.loads(ln) for ln in lines]
    assert events[0]["event"] == "subprocess_spawned"
    assert events[1]["event"] == "subprocess_returned"
    assert events[1]["tokens_in"] == 7
    assert events[1]["tokens_out"] == 11
    assert events[1]["cc_session_id"] == "00000000-0000-0000-0000-000000000001"

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr


def test_end_to_end_codex_records_thread_and_audit_path(woof_project: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    codex_stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thr-1"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 50,
                        "cached_input_tokens": 10,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 2,
                    },
                }
            ),
        ]
    )
    _make_stub(bin_dir, "cod", codex_stream)
    env = {
        "PATH": f"{bin_dir}:{__import__('os').environ['PATH']}",
        "HOME": __import__("os").environ.get("HOME", str(tmp_path)),
    }

    proc = subprocess.run(
        [
            str(WOOF_BIN),
            "dispatch",
            "codex",
            "--role",
            "critiquer",
            "--epic",
            "9",
        ],
        capture_output=True,
        text=True,
        input="critique me\n",
        cwd=woof_project,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    jsonl = woof_project / ".woof" / "epics" / "E9" / "dispatch.jsonl"
    events = [json.loads(ln) for ln in jsonl.read_text().splitlines() if ln.strip()]
    returned = events[1]
    assert returned["tokens_in"] == 50
    assert returned["tokens_out"] == 7  # 5 + 2 reasoning
    assert returned["cache_read_tokens"] == 10
    assert returned["codex_audit_path"].startswith(".woof/epics/E9/audit/cod-critiquer-")

    validate = subprocess.run([*WOOF_VALIDATE, str(jsonl)], capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr
