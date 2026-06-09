"""Small-valid-epic efficiency benchmark harness.

The harness is intentionally outside the graph. It creates isolated consumer
worktrees, seeds the same valid EPIC.md and optional runtime config into each,
runs a Woof variant, and writes a redacted manifest that can be compared across
variants before any prompt/model/graph changes are made.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from woof.cli.commands.observe import ObserveError, build_observe_report
from woof.cli.dispatcher import MODEL_PROFILE_ENV
from woof.trackers.epic_body import split_epic_front_matter

MANIFEST_VERSION = 1
TERMINAL_STATUSES = {"gate_opened", "halted", "epic_complete"}
SUCCESS_EXIT_TYPES = {"clean", "completed_lingering"}
SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|auth|bearer|credential|jwt|oauth|password|secret|token)", re.IGNORECASE
)
REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE), "bearer_token"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "jwt"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "aws_access_key"),
    (
        re.compile(r"(?i)\b(?:api[_-]?key|password|secret|token)\b\s*[:=]\s*[\"']?[^\"'\s,}]+"),
        "secret_assignment",
    ),
)


class BenchmarkError(RuntimeError):
    """Raised when a benchmark run cannot be constructed safely."""


@dataclass(frozen=True)
class VariantSpec:
    """A Woof variant command and optional source checkout."""

    id: str
    woof_cmd: tuple[str, ...]
    woof_repo: Path | None = None
    model_profile: str | None = None


@dataclass(frozen=True)
class WorktreeSpec:
    """An isolated throwaway consumer worktree for one benchmark variant."""

    path: Path
    branch: str
    consumer_repo: Path
    consumer_base_sha: str
    scenario_id: str
    variant_id: str
    run_id: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    safe = safe.strip("-._")
    if not safe:
        raise BenchmarkError(f"invalid empty id from {value!r}")
    return safe


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=dict(env) if env is not None else None,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise BenchmarkError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return proc


def resolve_git_sha(repo: Path, ref: str) -> str:
    """Resolve ``ref`` to a commit SHA in ``repo``."""

    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def resolve_woof_sha(woof_repo: Path | None) -> tuple[str, bool]:
    """Return the Woof checkout SHA and dirty flag, or ``unknown`` outside git."""

    if woof_repo is None:
        return "unknown", False
    repo = woof_repo.resolve()
    if not (repo / ".git").exists():
        return "unknown", False
    sha = _git(repo, "rev-parse", "--verify", "HEAD").stdout.strip()
    dirty = bool(_git(repo, "status", "--porcelain").stdout.strip())
    return sha, dirty


def create_worktree(
    *,
    consumer_repo: Path,
    consumer_base_sha: str,
    scenario_id: str,
    variant_id: str,
    run_id: str,
    worktree_parent: Path,
) -> WorktreeSpec:
    """Create a fresh consumer worktree and branch from ``consumer_base_sha``."""

    scenario = _safe_id(scenario_id)
    variant = _safe_id(variant_id)
    run = _safe_id(run_id)
    branch = f"bench/{scenario}/{variant}/{run}"
    path = worktree_parent / f"{scenario}-{variant}-{run}"
    if path.exists():
        raise BenchmarkError(f"worktree path already exists: {path}")
    worktree_parent.mkdir(parents=True, exist_ok=True)
    _git(consumer_repo, "worktree", "add", "-B", branch, str(path), consumer_base_sha)
    _git(path, "reset", "--hard", consumer_base_sha)
    return WorktreeSpec(
        path=path,
        branch=branch,
        consumer_repo=consumer_repo,
        consumer_base_sha=consumer_base_sha,
        scenario_id=scenario_id,
        variant_id=variant_id,
        run_id=run_id,
    )


def remove_worktree(worktree: WorktreeSpec, *, delete_branch: bool = True) -> None:
    """Remove a throwaway worktree and its branch if they still exist."""

    if worktree.path.exists():
        _git(worktree.consumer_repo, "worktree", "remove", "--force", str(worktree.path))
    if delete_branch:
        _git(worktree.consumer_repo, "branch", "-D", worktree.branch, check=False)


def epic_id_from_fixture(epic_fixture: Path) -> int:
    """Read the benchmark epic id from an EPIC.md fixture."""

    front, _ = split_epic_front_matter(epic_fixture)
    epic_id = front.get("epic_id")
    if not isinstance(epic_id, int):
        raise BenchmarkError(f"{epic_fixture}: front-matter must contain integer epic_id")
    return epic_id


def seed_epic_fixture(
    repo_root: Path,
    *,
    epic_fixture: Path,
    config_dir: Path | None = None,
    stub_models: bool = False,
) -> int:
    """Seed a fresh EPIC.md and optional deterministic runtime config."""

    epic_id = epic_id_from_fixture(epic_fixture)
    woof_dir = repo_root / ".woof"
    woof_dir.mkdir(exist_ok=True)
    if config_dir is not None:
        _copy_config_dir(config_dir, woof_dir)
        _write_benchmark_excludes(repo_root, ignore_seeded_config=True)
    elif stub_models:
        _write_stub_config(woof_dir)
        _write_benchmark_excludes(repo_root, ignore_seeded_config=True)
    elif not (woof_dir / "prerequisites.toml").is_file():
        raise BenchmarkError(
            "consumer base has no .woof/prerequisites.toml; pass --config-dir or --stub-models"
        )
    else:
        _write_benchmark_excludes(repo_root, ignore_seeded_config=False)

    epic_dir = woof_dir / "epics" / f"E{epic_id}"
    if epic_dir.exists():
        shutil.rmtree(epic_dir)
    epic_dir.mkdir(parents=True)
    shutil.copy2(epic_fixture, epic_dir / "EPIC.md")
    (woof_dir / ".current-epic").write_text(f"E{epic_id}\n", encoding="utf-8")
    return epic_id


def _copy_config_dir(config_dir: Path, woof_dir: Path) -> None:
    source = config_dir / ".woof" if (config_dir / ".woof").is_dir() else config_dir
    if not source.is_dir():
        raise BenchmarkError(f"config directory not found: {config_dir}")
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if rel.parts and rel.parts[0] in {"epics", "codebase"}:
            continue
        if rel.name in {".current-epic", ".preflight-floor", ".preflight-runtime"}:
            continue
        target = woof_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _write_stub_config(woof_dir: Path) -> None:
    """Write local-tracker config for dry/stubbed benchmark runs."""

    woof_dir.joinpath("prerequisites.toml").write_text(
        """\
[infra]
git = "2.30+"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "local"
""",
        encoding="utf-8",
    )
    woof_dir.joinpath("agents.toml").write_text(
        """\
model_profile = "stub"

[roles.primary]
adapter = "codex"

[roles.reviewer]
adapter = "claude"
mcp = []

[roles.orchestrator]
adapter = "in-session"

[roles.gate-resolver]
adapter = "in-session"

[model_profiles.stub.roles.primary]
model = "stub-primary"
effort = "low"

[model_profiles.stub.roles.reviewer]
model = "stub-reviewer"
effort = "low"

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
    woof_dir.joinpath("quality-gates.toml").write_text(
        """\
[gates.compile]
command = '''PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
from pathlib import Path
import py_compile

paths = []
for root in ("src", "tests"):
    base = Path(root)
    if base.exists():
        paths.extend(sorted(base.rglob("*.py")))
if Path("bench_note.py").exists():
    paths.append(Path("bench_note.py"))
for path in paths:
    py_compile.compile(str(path), doraise=True)
PY'''
timeout_seconds = 30
""",
        encoding="utf-8",
    )
    woof_dir.joinpath("test-markers.toml").write_text(
        """\
[languages.python]
test_paths = ["tests/", "src/**/test_*.py"]
marker_regex = '(?<![A-Za-z0-9])O\\d+(?![A-Za-z0-9])'
cd_marker_regex = '(?<![A-Za-z0-9])CD\\d+(?![A-Za-z0-9])'
docstring_keyword = "outcomes:"
comment_prefix = "#"
context_lines = 3
""",
        encoding="utf-8",
    )


def _write_benchmark_excludes(repo_root: Path, *, ignore_seeded_config: bool) -> None:
    """Keep benchmark-only runtime files out of git status in throwaway worktrees."""

    proc = _git(repo_root, "rev-parse", "--git-path", "info/exclude", check=False)
    if proc.returncode != 0:
        return
    raw_path = proc.stdout.strip()
    if not raw_path:
        return
    exclude_path = Path(raw_path)
    if not exclude_path.is_absolute():
        exclude_path = repo_root / exclude_path
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    if not exclude_path.exists():
        exclude_path.write_text("", encoding="utf-8")
    patterns = [
        ".woof/.current-epic",
        ".woof/epics/*/gate.md",
        ".woof/epics/*/.wf.lock",
        ".woof/epics/*/executor_result.json",
        ".woof/epics/*/check-result.json",
        ".woof/epics/*/audit/raw/",
        "__pycache__/",
        "tests/__pycache__/",
        "*.pyc",
    ]
    if ignore_seeded_config:
        patterns.extend(
            [
                ".woof/agents.toml",
                ".woof/prerequisites.toml",
                ".woof/quality-gates.toml",
                ".woof/test-markers.toml",
                ".woof/docs-paths.toml",
            ]
        )
    existing = exclude_path.read_text(encoding="utf-8")
    missing = [pattern for pattern in patterns if pattern not in existing.splitlines()]
    if missing:
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        exclude_path.write_text(existing + suffix + "\n".join(missing) + "\n", encoding="utf-8")


def write_stub_model_bin(bin_dir: Path) -> None:
    """Create public-CLI-shaped codex/claude stubs for dry benchmark runs."""

    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_executable(bin_dir / "codex", _PRIMARY_STUB)
    _write_executable(bin_dir / "claude", _REVIEWER_STUB)


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o700)


def run_variant_workflow(
    *,
    worktree: WorktreeSpec,
    variant: VariantSpec,
    epic_id: int,
    env: Mapping[str, str],
    auto_approve_plan_gate: bool,
    max_cycles: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Run ``woof wf`` for one variant and return node outputs plus command summaries."""

    outputs: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    final_exit_code = 0
    for _ in range(max_cycles):
        proc = _run_command(
            [*variant.woof_cmd, "wf", "--epic", str(epic_id), "--format", "json"],
            cwd=worktree.path,
            env=env,
        )
        final_exit_code = proc.returncode
        commands.append(_command_summary("wf", proc))
        if proc.returncode != 0:
            break
        batch = _parse_json_lines(proc.stdout)
        outputs.extend(batch)
        if not batch:
            break
        last = batch[-1]
        if (
            auto_approve_plan_gate
            and last.get("node_type") == "plan_gate_open"
            and last.get("status") == "gate_opened"
        ):
            resolved = _run_command(
                [*variant.woof_cmd, "wf", "--epic", str(epic_id), "--resolve", "approve"],
                cwd=worktree.path,
                env=env,
            )
            final_exit_code = resolved.returncode
            commands.append(_command_summary("resolve_plan_gate", resolved))
            if resolved.returncode != 0:
                break
            continue
        if str(last.get("status") or "") in TERMINAL_STATUSES:
            break
    else:
        final_exit_code = 124
        commands.append(
            {
                "kind": "max_cycles",
                "exit_code": 124,
                "stdout_bytes": 0,
                "stderr_bytes": 0,
            }
        )
    return outputs, commands, final_exit_code


def _run_command(
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
    )


def _command_summary(kind: str, proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    summary = {
        "kind": kind,
        "exit_code": proc.returncode,
        "stdout_bytes": len(proc.stdout.encode("utf-8")),
        "stderr_bytes": len(proc.stderr.encode("utf-8")),
    }
    if proc.returncode != 0:
        summary["stderr_tail"] = _tail(proc.stderr)
    return summary


def _tail(text: str, *, limit: int = 600) -> str:
    stripped = text.strip()
    return stripped[-limit:]


def _parse_json_lines(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"woof wf returned non-JSON line: {line}") from exc
        if isinstance(payload, dict):
            items.append(payload)
    return items


def collect_run_manifest(
    *,
    repo_root: Path,
    epic_id: int,
    scenario_id: str,
    variant_id: str,
    run_id: str,
    woof_sha: str,
    woof_dirty: bool,
    consumer_base_sha: str,
    branch: str,
    command_outputs: Sequence[Mapping[str, Any]],
    commands: Sequence[Mapping[str, Any]],
    run_started_at: datetime,
    run_ended_at: datetime,
    run_exit_code: int,
    model_profile: str | None = None,
    operator_notes: str | None = None,
) -> dict[str, Any]:
    """Aggregate a redacted benchmark run manifest from a consumer worktree."""

    directory = repo_root / ".woof" / "epics" / f"E{epic_id}"
    epic_events = _read_jsonl(directory / "epic.jsonl")
    dispatch_events = _read_jsonl(directory / "dispatch.jsonl")
    observe_report, observe_error = _safe_observe(repo_root, epic_id)
    node_sequence = _node_sequence(command_outputs, epic_events, dispatch_events)
    route_policy = _route_policy(observe_report)
    effective_model_profile = model_profile or _route_policy_model_profile(route_policy)
    story_statuses = _story_statuses(observe_report)
    gate_summary = _gate_summary(observe_report, epic_events)
    checks = _checks_summary(observe_report)
    dispatch = _dispatch_summary(dispatch_events)
    diff = _diff_summary(repo_root, consumer_base_sha, story_statuses)
    final_state = _final_state(observe_report, node_sequence, gate_summary)
    quality = _quality_outcome(
        final_state=final_state,
        checks=checks,
        diff=diff,
        run_exit_code=run_exit_code,
        epic_events=epic_events,
        repo_root=repo_root,
        epic_id=epic_id,
        operator_notes=operator_notes,
    )
    consumer_result_sha = _consumer_result_sha(repo_root, consumer_base_sha)
    run_duration_ms = int((run_ended_at - run_started_at).total_seconds() * 1000)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "scenario": {
            "id": scenario_id,
            "seed": {
                "start_state": "EPIC.md",
                "epic_id": epic_id,
                "epic_path": f".woof/epics/E{epic_id}/EPIC.md",
            },
        },
        "variant": {
            "id": variant_id,
            "woof_sha": woof_sha,
            "woof_dirty": woof_dirty,
            "model_profile": effective_model_profile,
        },
        "run": {
            "id": run_id,
            "started_at": _iso(run_started_at),
            "ended_at": _iso(run_ended_at),
            "duration_ms": run_duration_ms,
            "exit_code": run_exit_code,
            "commands": list(commands),
        },
        "git": {
            "consumer_base_sha": consumer_base_sha,
            "consumer_result_sha": consumer_result_sha,
            "branch": branch,
            "dirty": bool(_git(repo_root, "status", "--porcelain").stdout.strip()),
        },
        "route_policy": route_policy,
        "node_sequence": node_sequence,
        "final_state": final_state,
        "gates": gate_summary,
        "checks": checks,
        "story_statuses": story_statuses,
        "dispatch": dispatch,
        "timing": {
            "run_duration_ms": run_duration_ms,
            "subprocess_duration_ms": dispatch["duration_ms"],
        },
        "diff": diff,
        "quality_outcome": quality,
        "warnings": [observe_error] if observe_error else [],
    }
    return redact_manifest(manifest)


def _safe_observe(repo_root: Path, epic_id: int) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return build_observe_report(repo_root, epic_id), None
    except (FileNotFoundError, ObserveError, ValueError) as exc:
        return None, str(exc)


def _node_sequence(
    command_outputs: Sequence[Mapping[str, Any]],
    epic_events: Sequence[Mapping[str, Any]],
    dispatch_events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    sequence = [
        {
            "node_type": item.get("node_type"),
            "story_id": item.get("story_id"),
            "status": item.get("status"),
        }
        for item in command_outputs
        if item.get("node_type") or item.get("status")
    ]
    if sequence:
        return sequence

    inferred: list[dict[str, Any]] = []
    event_to_node = {
        "definition_closed": "epic_definition",
        "breakdown_planned": "breakdown_planning",
        "plan_critiqued": "plan_critique",
        "plan_gate_opened": "plan_gate_open",
        "plan_gate_resolved": "plan_gate_resolve",
        "story_completed": "commit",
        "transaction_manifest_verified": "commit",
        "epic_completed": "human_review",
    }
    for event in epic_events:
        name = str(event.get("event") or "")
        node = event_to_node.get(name)
        if node:
            inferred.append(
                {
                    "node_type": node,
                    "story_id": event.get("story_id"),
                    "status": "completed" if node != "plan_gate_open" else "gate_opened",
                }
            )
    for event in dispatch_events:
        if event.get("event") != "subprocess_spawned":
            continue
        role = event.get("role")
        inferred.append(
            {
                "node_type": "dispatch",
                "story_id": event.get("story_id"),
                "status": "spawned",
                "role": role,
            }
        )
    return inferred


def _route_policy(observe_report: Mapping[str, Any] | None) -> dict[str, Any]:
    if not observe_report:
        return {"available": False}
    return {
        "available": True,
        "dispatch_routes": observe_report.get("dispatch_routes", {}),
        "runtime_policy": observe_report.get("runtime_policy", {}),
    }


def _route_policy_model_profile(route_policy: Mapping[str, Any]) -> str | None:
    routes = route_policy.get("dispatch_routes")
    if isinstance(routes, Mapping):
        value = routes.get("model_profile")
        if isinstance(value, str) and value:
            return value
    return None


def _story_statuses(observe_report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not observe_report:
        return []
    stories = observe_report.get("status", {}).get("plan", {}).get("stories", [])
    if not isinstance(stories, list):
        return []
    statuses = []
    for story in stories:
        if not isinstance(story, Mapping):
            continue
        statuses.append(
            {
                "id": story.get("id"),
                "title": story.get("title"),
                "status": story.get("status"),
                "satisfies": story.get("satisfies", []),
            }
        )
    return statuses


def _gate_summary(
    observe_report: Mapping[str, Any] | None,
    epic_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    current = observe_report.get("status", {}).get("gate", {}) if observe_report else {}
    gate_events = [
        {
            "event": event.get("event"),
            "gate_type": event.get("gate_type"),
            "story_id": event.get("story_id"),
            "decision": event.get("decision"),
            "triggered_by": event.get("triggered_by", []),
        }
        for event in epic_events
        if "gate" in str(event.get("event") or "")
    ]
    return {
        "current": current,
        "events": gate_events,
        "opened_count": sum(1 for event in gate_events if "opened" in str(event.get("event"))),
        "resolved_count": sum(1 for event in gate_events if "resolved" in str(event.get("event"))),
    }


def _checks_summary(observe_report: Mapping[str, Any] | None) -> dict[str, Any]:
    if not observe_report:
        return {"exists": False, "valid": False}
    checks = observe_report.get("checks", {})
    return checks if isinstance(checks, dict) else {"exists": False, "valid": False}


def _dispatch_summary(dispatch_events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    returned = [event for event in dispatch_events if event.get("event") == "subprocess_returned"]
    totals = {
        "prompt_bytes": 0,
        "artefact_bytes": 0,
        "output_bytes": 0,
        "stderr_bytes": 0,
        "command_count": 0,
    }
    tokens = {
        "tokens_in": 0,
        "tokens_out": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    duration_ms = 0
    for event in returned:
        duration_ms += _int(event.get("duration_ms"))
        for key in totals:
            totals[key] += _int(event.get(key))
        for key in tokens:
            tokens[key] += _int(event.get(key))
    return {
        "spawned": sum(
            1 for event in dispatch_events if event.get("event") == "subprocess_spawned"
        ),
        "returned": len(returned),
        "successful": _successful_dispatch_count(returned),
        "failed": _failed_dispatch_count(returned),
        "killed": _failed_kill_count(dispatch_events),
        "duration_ms": duration_ms,
        "tokens": tokens,
        "telemetry": totals,
        "events": [_compact_dispatch_event(event) for event in returned],
        "by_route": _dispatch_route_totals(returned),
    }


def _compact_dispatch_event(event: Mapping[str, Any]) -> dict[str, Any]:
    tokens = {
        key: _int(event.get(key))
        for key in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens")
    }
    telemetry = {
        key: _int(event.get(key))
        for key in (
            "duration_ms",
            "prompt_bytes",
            "artefact_bytes",
            "output_bytes",
            "stderr_bytes",
            "command_count",
        )
    }
    summary: dict[str, Any] = {
        "role": event.get("role"),
        "story_id": event.get("story_id"),
        "config_role": event.get("config_role"),
        "model_profile": event.get("model_profile"),
        "profile_role": event.get("profile_role"),
        "adapter": event.get("adapter") or event.get("harness"),
        "model": event.get("model"),
        "effort": event.get("effort"),
        "mcp": event.get("mcp") or [],
        "exit_type": event.get("exit_type"),
        "exit_code": event.get("exit_code"),
        "tokens": tokens,
        "telemetry": telemetry,
    }
    for key in ("cc_session_id", "claude_transcript_path", "codex_audit_path"):
        if event.get(key):
            summary[key] = event.get(key)
    return summary


def _dispatch_route_totals(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for event in events:
        key = (
            str(event.get("role") or ""),
            str(event.get("adapter") or event.get("harness") or ""),
            str(event.get("model") or ""),
            str(event.get("effort") or ""),
            str(event.get("model_profile") or ""),
        )
        bucket = buckets.setdefault(
            key,
            {
                "role": key[0],
                "adapter": key[1],
                "model": key[2],
                "effort": key[3],
                "model_profile": key[4] or None,
                "calls": 0,
                "duration_ms": 0,
                "tokens": {
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "telemetry": {
                    "prompt_bytes": 0,
                    "artefact_bytes": 0,
                    "output_bytes": 0,
                    "stderr_bytes": 0,
                    "command_count": 0,
                },
            },
        )
        bucket["calls"] += 1
        bucket["duration_ms"] += _int(event.get("duration_ms"))
        for token_key in bucket["tokens"]:
            bucket["tokens"][token_key] += _int(event.get(token_key))
        for telemetry_key in bucket["telemetry"]:
            bucket["telemetry"][telemetry_key] += _int(event.get(telemetry_key))
    return list(buckets.values())


def _successful_dispatch_count(events: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for event in events:
        exit_type = event.get("exit_type")
        if exit_type in SUCCESS_EXIT_TYPES or (exit_type is None and event.get("exit_code") == 0):
            count += 1
    return count


def _failed_dispatch_count(events: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for event in events:
        exit_type = event.get("exit_type")
        if exit_type in SUCCESS_EXIT_TYPES or (exit_type is None and event.get("exit_code") == 0):
            continue
        count += 1
    return count


def _failed_kill_count(events: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for event in events
        if event.get("event") == "subprocess_killed"
        and event.get("exit_type") != "completed_lingering"
    )


def _int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _diff_summary(
    repo_root: Path,
    consumer_base_sha: str,
    story_statuses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    committed = _diff_part(repo_root, consumer_base_sha, "HEAD")
    staged = _diff_cached_part(repo_root)
    unstaged = _diff_unstaged_part(repo_root)
    pathscope = _pathscope_summary(committed["files"], repo_root, story_statuses)
    return {
        "committed": committed,
        "staged": staged,
        "unstaged": unstaged,
        "pathscope": pathscope,
    }


def _diff_part(repo_root: Path, left: str, right: str) -> dict[str, Any]:
    files = _git(repo_root, "diff", "--name-only", f"{left}..{right}", "--").stdout.splitlines()
    numstat = _git(repo_root, "diff", "--numstat", f"{left}..{right}", "--").stdout.splitlines()
    shortstat = _git(repo_root, "diff", "--shortstat", f"{left}..{right}", "--").stdout.strip()
    insertions, deletions = _parse_numstat(numstat)
    return {
        "file_count": len(files),
        "files": files,
        "insertions": insertions,
        "deletions": deletions,
        "shortstat": shortstat,
    }


def _diff_cached_part(repo_root: Path) -> dict[str, Any]:
    files = _git(repo_root, "diff", "--cached", "--name-only", "--").stdout.splitlines()
    numstat = _git(repo_root, "diff", "--cached", "--numstat", "--").stdout.splitlines()
    shortstat = _git(repo_root, "diff", "--cached", "--shortstat", "--").stdout.strip()
    insertions, deletions = _parse_numstat(numstat)
    return {
        "file_count": len(files),
        "files": files,
        "insertions": insertions,
        "deletions": deletions,
        "shortstat": shortstat,
    }


def _diff_unstaged_part(repo_root: Path) -> dict[str, Any]:
    files = _git(repo_root, "diff", "--name-only", "--").stdout.splitlines()
    numstat = _git(repo_root, "diff", "--numstat", "--").stdout.splitlines()
    shortstat = _git(repo_root, "diff", "--shortstat", "--").stdout.strip()
    insertions, deletions = _parse_numstat(numstat)
    return {
        "file_count": len(files),
        "files": files,
        "insertions": insertions,
        "deletions": deletions,
        "shortstat": shortstat,
    }


def _parse_numstat(lines: Iterable[str]) -> tuple[int, int]:
    insertions = deletions = 0
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        if parts[0].isdigit():
            insertions += int(parts[0])
        if parts[1].isdigit():
            deletions += int(parts[1])
    return insertions, deletions


def _pathscope_summary(
    changed_files: Sequence[str],
    repo_root: Path,
    story_statuses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    plan_paths = _plan_story_paths(repo_root)
    if not plan_paths:
        return {"known": False, "ok": None, "allowed_pathspecs": [], "outside_scope": []}
    user_files = [path for path in changed_files if not path.startswith(".woof/")]
    outside = [
        path for path in user_files if not any(_pathspec_matches(path, spec) for spec in plan_paths)
    ]
    return {
        "known": True,
        "ok": not outside,
        "allowed_pathspecs": plan_paths,
        "outside_scope": outside,
        "story_status_count": len(story_statuses),
    }


def _plan_story_paths(repo_root: Path) -> list[str]:
    plan_paths = sorted((repo_root / ".woof" / "epics").glob("E*/plan.json"))
    if not plan_paths:
        return []
    try:
        payload = json.loads(plan_paths[-1].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    paths: list[str] = []
    for story in payload.get("stories") or []:
        if isinstance(story, Mapping):
            paths.extend(str(item) for item in story.get("paths") or [])
    return sorted(set(paths))


def _pathspec_matches(path: str, spec: str) -> bool:
    if fnmatch.fnmatch(path, spec):
        return True
    if spec.endswith("/"):
        return path.startswith(spec)
    return path == spec or path.startswith(spec.rstrip("/") + "/")


def _consumer_result_sha(repo_root: Path, consumer_base_sha: str) -> str | None:
    head = _git(repo_root, "rev-parse", "--verify", "HEAD").stdout.strip()
    return None if head == consumer_base_sha else head


def _final_state(
    observe_report: Mapping[str, Any] | None,
    node_sequence: Sequence[Mapping[str, Any]],
    gate_summary: Mapping[str, Any],
) -> dict[str, Any]:
    last = dict(node_sequence[-1]) if node_sequence else {}
    next_state = observe_report.get("status", {}).get("next") if observe_report else None
    return {
        "last_node": last.get("node_type"),
        "last_status": last.get("status"),
        "next": next_state,
        "gate_open": bool(gate_summary.get("current", {}).get("open")),
    }


def _quality_outcome(
    *,
    final_state: Mapping[str, Any],
    checks: Mapping[str, Any],
    diff: Mapping[str, Any],
    run_exit_code: int,
    epic_events: Sequence[Mapping[str, Any]],
    repo_root: Path,
    epic_id: int,
    operator_notes: str | None,
) -> dict[str, Any]:
    if run_exit_code != 0:
        status = "failed"
        reason = "workflow_command_failed"
    elif final_state.get("gate_open"):
        status = "gated"
        reason = "gate_open"
    elif final_state.get("last_status") == "epic_complete" or (
        isinstance(final_state.get("next"), Mapping)
        and final_state.get("next", {}).get("node") == "epic_complete"
    ):
        status = "passed"
        reason = "epic_complete"
    else:
        status = "incomplete"
        reason = "workflow_not_complete"

    pathscope = diff.get("pathscope", {}) if isinstance(diff.get("pathscope"), Mapping) else {}
    if status == "passed" and pathscope.get("ok") is False:
        status = "failed"
        reason = "result_diff_outside_story_scope"
    if status == "passed" and checks.get("exists") and checks.get("ok") is False:
        status = "failed"
        reason = "quality_checks_failed"

    return {
        "status": status,
        "reason": reason,
        "quality_command_ok": checks.get("ok") if checks.get("exists") else None,
        "pathscope_ok": pathscope.get("ok"),
        "reviewer_severity": _reviewer_severity(repo_root, epic_id, epic_events),
        "operator_notes": operator_notes or "",
    }


def _reviewer_severity(
    repo_root: Path,
    epic_id: int,
    epic_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    plan = [
        event.get("severity")
        for event in epic_events
        if event.get("event") == "plan_critiqued" and event.get("severity")
    ]
    critique_dir = repo_root / ".woof" / "epics" / f"E{epic_id}" / "critique"
    stories: dict[str, str] = {}
    if critique_dir.is_dir():
        for path in sorted(critique_dir.glob("story-*.md")):
            front = _markdown_front_matter(path)
            severity = front.get("severity")
            if isinstance(severity, str):
                stories[path.stem.removeprefix("story-")] = severity
    return {"plan": plan[-1] if plan else None, "stories": stories}


def _markdown_front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        loaded = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def redact_manifest(value: Any, *, key: str = "") -> Any:
    """Recursively redact secret-looking strings before writing manifests."""

    if isinstance(value, Mapping):
        return {str(k): redact_manifest(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_manifest(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [redact_manifest(item, key=key) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern, reason in REDACTION_PATTERNS:
            redacted = pattern.sub(f"[REDACTED:{reason}]", redacted)
        if SECRET_KEY_RE.search(key) and redacted == value and value:
            return "[REDACTED:sensitive_field]"
        return redacted
    return value


def write_manifest(manifest: Mapping[str, Any], output_dir: Path) -> Path:
    """Write a redacted JSON run manifest and return its path."""

    output_dir.mkdir(parents=True, exist_ok=True)
    scenario = _safe_id(str(manifest["scenario"]["id"]))
    variant = _safe_id(str(manifest["variant"]["id"]))
    run_id = _safe_id(str(manifest["run"]["id"]))
    started = str(manifest["run"]["started_at"]).replace(":", "").replace("-", "")
    path = output_dir / f"{started}-{scenario}-{variant}-{run_id}.json"
    path.write_text(json.dumps(redact_manifest(manifest), indent=2, sort_keys=True) + "\n")
    return path


def comparison_rows(manifests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic comparison rows for one or more manifests."""

    rows: list[dict[str, Any]] = []
    for manifest in manifests:
        dispatch = manifest.get("dispatch", {})
        tokens = dispatch.get("tokens", {}) if isinstance(dispatch, Mapping) else {}
        telemetry = dispatch.get("telemetry", {}) if isinstance(dispatch, Mapping) else {}
        diff = manifest.get("diff", {})
        committed = diff.get("committed", {}) if isinstance(diff, Mapping) else {}
        quality = manifest.get("quality_outcome", {})
        final = manifest.get("final_state", {})
        variant = manifest.get("variant", {})
        route_policy = manifest.get("route_policy", {})
        model_profile = None
        if isinstance(variant, Mapping):
            model_profile = variant.get("model_profile")
        if model_profile is None and isinstance(route_policy, Mapping):
            routes = route_policy.get("dispatch_routes", {})
            if isinstance(routes, Mapping):
                model_profile = routes.get("model_profile")
        rows.append(
            {
                "scenario": manifest.get("scenario", {}).get("id"),
                "variant": variant.get("id") if isinstance(variant, Mapping) else None,
                "model_profile": model_profile,
                "quality": quality.get("status") if isinstance(quality, Mapping) else None,
                "final": final.get("last_status") if isinstance(final, Mapping) else None,
                "dispatch": dispatch.get("returned") if isinstance(dispatch, Mapping) else 0,
                "tokens_in": tokens.get("tokens_in", 0),
                "tokens_out": tokens.get("tokens_out", 0),
                "cache_read": tokens.get("cache_read_tokens", 0),
                "cache_write": tokens.get("cache_write_tokens", 0),
                "commands": telemetry.get("command_count", 0),
                "subprocess_ms": dispatch.get("duration_ms", 0)
                if isinstance(dispatch, Mapping)
                else 0,
                "files": committed.get("file_count", 0),
                "insertions": committed.get("insertions", 0),
                "deletions": committed.get("deletions", 0),
            }
        )
    return rows


def render_comparison_markdown(manifests: Sequence[Mapping[str, Any]]) -> str:
    """Render a compact Markdown comparison table."""

    rows = comparison_rows(manifests)
    headers = [
        "Scenario",
        "Variant",
        "Profile",
        "Quality",
        "Final",
        "Dispatch",
        "Tokens In",
        "Tokens Out",
        "Cache Read",
        "Cache Write",
        "Commands",
        "Subprocess ms",
        "Files",
        "+/-",
    ]
    out = ["| " + " | ".join(headers) + " |\n"]
    out.append("|" + "|".join("---" for _ in headers) + "|\n")
    for row in rows:
        delta = f"{row['insertions']}/-{row['deletions']}"
        values = [
            row["scenario"],
            row["variant"],
            row["model_profile"],
            row["quality"],
            row["final"],
            row["dispatch"],
            row["tokens_in"],
            row["tokens_out"],
            row["cache_read"],
            row["cache_write"],
            row["commands"],
            row["subprocess_ms"],
            row["files"],
            delta,
        ]
        out.append("| " + " | ".join(_md_cell(value) for value in values) + " |\n")
    return "".join(out)


def _md_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|")


def load_manifests(paths: Sequence[Path]) -> list[dict[str, Any]]:
    manifests = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise BenchmarkError(f"{path}: manifest root must be an object")
        manifests.append(payload)
    return manifests


def parse_variant(
    raw: str,
    *,
    cwd: Path,
    repo_overrides: Mapping[str, Path],
    profile_overrides: Mapping[str, str],
) -> VariantSpec:
    """Parse ``ID=COMMAND`` variant syntax."""

    if "=" not in raw:
        raise BenchmarkError("--variant must use ID=COMMAND")
    raw_id, raw_cmd = raw.split("=", 1)
    variant_id = _safe_id(raw_id)
    command = shlex.split(raw_cmd)
    if not command:
        raise BenchmarkError(f"{raw}: command is empty")
    first = command[0]
    if "/" in first:
        first_path = Path(first).expanduser()
        if not first_path.is_absolute():
            first_path = cwd / first_path
        command[0] = str(first_path.resolve())
    return VariantSpec(
        id=variant_id,
        woof_cmd=tuple(command),
        woof_repo=repo_overrides.get(variant_id),
        model_profile=profile_overrides.get(variant_id),
    )


def parse_variant_repos(values: Sequence[str] | None) -> dict[str, Path]:
    repos: dict[str, Path] = {}
    for raw in values or []:
        if "=" not in raw:
            raise BenchmarkError("--variant-repo must use ID=PATH")
        raw_id, raw_path = raw.split("=", 1)
        repos[_safe_id(raw_id)] = Path(raw_path).expanduser().resolve()
    return repos


def parse_variant_model_profiles(values: Sequence[str] | None) -> dict[str, str]:
    profiles: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise BenchmarkError("--variant-model-profile must use ID=PROFILE")
        raw_id, raw_profile = raw.split("=", 1)
        profile = raw_profile.strip()
        if not profile:
            raise BenchmarkError("--variant-model-profile profile must not be empty")
        profiles[_safe_id(raw_id)] = profile
    return profiles


@contextmanager
def _temporary_env_value(name: str, value: str | None) -> Iterator[None]:
    previous = os.environ.get(name)
    try:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def run_benchmark(args: argparse.Namespace) -> int:
    consumer_repo = Path(args.consumer_repo).expanduser().resolve()
    consumer_base_sha = resolve_git_sha(consumer_repo, args.consumer_base)
    run_id = args.run_id or _utc_now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    worktree_parent = Path(args.worktree_parent or tempfile.mkdtemp(prefix="woof-eff-bench-"))
    repo_overrides = parse_variant_repos(args.variant_repo)
    profile_overrides = parse_variant_model_profiles(args.variant_model_profile)
    default_woof_repo = Path(args.woof_repo).expanduser().resolve() if args.woof_repo else None
    variants = [
        parse_variant(
            raw,
            cwd=Path.cwd(),
            repo_overrides=repo_overrides,
            profile_overrides=profile_overrides,
        )
        for raw in args.variant
    ]
    if not variants:
        variants = [
            VariantSpec(
                id="current",
                woof_cmd=(str((Path.cwd() / "bin" / "woof").resolve()),),
                woof_repo=default_woof_repo,
                model_profile=args.model_profile,
            )
        ]
    variants = [
        variant
        if variant.woof_repo is not None or default_woof_repo is None
        else VariantSpec(variant.id, variant.woof_cmd, default_woof_repo, variant.model_profile)
        for variant in variants
    ]

    env = os.environ.copy()
    if args.stub_models:
        stub_bin = worktree_parent / "stub-bin"
        write_stub_model_bin(stub_bin)
        env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"

    manifest_paths: list[Path] = []
    for variant in variants:
        selected_profile = variant.model_profile or args.model_profile
        variant_env = dict(env)
        if selected_profile:
            variant_env[MODEL_PROFILE_ENV] = selected_profile
        worktree = create_worktree(
            consumer_repo=consumer_repo,
            consumer_base_sha=consumer_base_sha,
            scenario_id=args.scenario,
            variant_id=variant.id,
            run_id=run_id,
            worktree_parent=worktree_parent,
        )
        started = _utc_now()
        outputs: list[dict[str, Any]] = []
        commands: list[dict[str, Any]] = []
        exit_code = 0
        try:
            epic_id = seed_epic_fixture(
                worktree.path,
                epic_fixture=Path(args.epic_fixture).expanduser().resolve(),
                config_dir=Path(args.config_dir).expanduser().resolve()
                if args.config_dir
                else None,
                stub_models=args.stub_models,
            )
            outputs, commands, exit_code = run_variant_workflow(
                worktree=worktree,
                variant=variant,
                epic_id=epic_id,
                env=variant_env,
                auto_approve_plan_gate=not args.no_auto_approve_plan_gate,
                max_cycles=args.max_cycles,
            )
            ended = _utc_now()
            woof_sha, woof_dirty = resolve_woof_sha(variant.woof_repo)
            with _temporary_env_value(MODEL_PROFILE_ENV, variant_env.get(MODEL_PROFILE_ENV)):
                manifest = collect_run_manifest(
                    repo_root=worktree.path,
                    epic_id=epic_id,
                    scenario_id=args.scenario,
                    variant_id=variant.id,
                    run_id=run_id,
                    woof_sha=woof_sha,
                    woof_dirty=woof_dirty,
                    consumer_base_sha=consumer_base_sha,
                    branch=worktree.branch,
                    command_outputs=outputs,
                    commands=commands,
                    run_started_at=started,
                    run_ended_at=ended,
                    run_exit_code=exit_code,
                    model_profile=variant_env.get(MODEL_PROFILE_ENV),
                    operator_notes=args.notes,
                )
            manifest_paths.append(write_manifest(manifest, Path(args.output_dir)))
        finally:
            if not args.keep_worktrees:
                remove_worktree(worktree)

    if args.compare and manifest_paths:
        comparison = render_comparison_markdown(load_manifests(manifest_paths))
        compare_path = Path(args.output_dir) / f"{run_id}-{_safe_id(args.scenario)}-comparison.md"
        compare_path.write_text(comparison, encoding="utf-8")
        print(compare_path)
    for path in manifest_paths:
        print(path)
    return 0


def compare_manifests(args: argparse.Namespace) -> int:
    manifests = load_manifests([Path(path) for path in args.manifests])
    comparison = render_comparison_markdown(manifests)
    if args.output:
        Path(args.output).write_text(comparison, encoding="utf-8")
    else:
        sys.stdout.write(comparison)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m woof.bench.efficiency",
        description="run and compare small-valid-epic Woof efficiency benchmarks",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run variants in isolated consumer worktrees")
    run.add_argument("--consumer-repo", required=True, help="consumer git repository")
    run.add_argument(
        "--consumer-base",
        default="HEAD",
        help="consumer base ref/SHA shared by all variants (default: HEAD)",
    )
    run.add_argument("--scenario", default="small-valid-epic", help="scenario id")
    run.add_argument("--epic-fixture", required=True, help="path to small valid EPIC.md")
    run.add_argument(
        "--config-dir",
        help="directory containing deterministic .woof config files to copy into each worktree",
    )
    run.add_argument(
        "--variant",
        action="append",
        default=[],
        help="variant as ID=COMMAND; repeatable (default: current=./bin/woof)",
    )
    run.add_argument(
        "--variant-repo",
        action="append",
        help="variant source checkout as ID=PATH; repeatable",
    )
    run.add_argument(
        "--model-profile",
        help=f"model profile to select through {MODEL_PROFILE_ENV} for all variants",
    )
    run.add_argument(
        "--variant-model-profile",
        action="append",
        help=("per-variant model profile as ID=PROFILE; repeatable and overrides --model-profile"),
    )
    run.add_argument("--woof-repo", default=".", help="default Woof checkout for woof_sha")
    run.add_argument("--output-dir", required=True, help="manifest output directory")
    run.add_argument("--worktree-parent", help="parent directory for throwaway worktrees")
    run.add_argument("--run-id", help="stable run id shared by variants")
    run.add_argument("--stub-models", action="store_true", help="use local codex/claude stubs")
    run.add_argument(
        "--no-auto-approve-plan-gate",
        action="store_true",
        help="stop at the mandatory plan gate instead of approving it in the throwaway worktree",
    )
    run.add_argument("--max-cycles", type=int, default=8, help="max wf cycles per variant")
    run.add_argument("--keep-worktrees", action="store_true", help="do not delete worktrees")
    run.add_argument("--compare", action="store_true", help="write a Markdown comparison table")
    run.add_argument("--notes", help="operator notes copied into the quality outcome")
    run.set_defaults(func=run_benchmark)

    compare = sub.add_parser("compare", help="compare redacted run manifests")
    compare.add_argument("manifests", nargs="+", help="manifest JSON paths")
    compare.add_argument("--output", help="write Markdown to this path instead of stdout")
    compare.set_defaults(func=compare_manifests)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BenchmarkError as exc:
        sys.stderr.write(f"woof efficiency-bench: {exc}\n")
        return 2


_PRIMARY_STUB = r'''#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


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
    raise SystemExit("story id not found in prompt")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_plan(prompt: str) -> None:
    eid = epic_id(prompt)
    plan = {
        "epic_id": eid,
        "goal": "Measure a small valid epic from deterministic Definition through one story.",
        "stories": [
            {
                "id": "S1",
                "title": "Add benchmark note helper",
                "intent": "Create a tiny helper, test marker, and JSON Schema contract.",
                "paths": [
                    "bench_note.py",
                    "tests/test_bench_note.py",
                    "schemas/bench-note.schema.json",
                ],
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
        Path("bench_note.py"),
        'def benchmark_note() -> dict[str, str]:\n    return {"status": "measured"}\n',
    )
    write(
        Path("tests/test_bench_note.py"),
        '"""outcomes: O1\ncontract-decisions: CD1"""\n\n'
        "from bench_note import benchmark_note\n\n\n"
        "def test_benchmark_note_reports_measured_status() -> None:\n"
        '    assert benchmark_note() == {"status": "measured"}\n',
    )
    write(
        Path("schemas/bench-note.schema.json"),
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {"status": {"type": "string", "const": "measured"}},
                "examples": [{"status": "measured"}],
            },
            indent=2,
        )
        + "\n",
    )
    subprocess.run(
        [
            "git",
            "add",
            "--",
            "bench_note.py",
            "tests/test_bench_note.py",
            "schemas/bench-note.schema.json",
        ],
        check=True,
    )
    write(
        Path(f".woof/epics/E{eid}/executor_result.json"),
        json.dumps(
            {
                "epic_id": eid,
                "story_id": sid,
                "outcome": "staged_for_verification",
                "commit_subject": "feat: add benchmark note helper",
                "commit_body": "Adds the small efficiency benchmark fixture output.",
                "position": None,
            },
            indent=2,
        )
        + "\n",
    )


def main() -> int:
    prompt = sys.stdin.read()
    if '"node_type": "breakdown_planning"' in prompt:
        write_plan(prompt)
    elif '"node_type": "executor_dispatch"' in prompt:
        execute_story(prompt)
    else:
        raise SystemExit("primary efficiency stub did not recognise prompt")
    print(json.dumps({"type": "thread.started", "thread_id": "efficiency-stub-primary"}))
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "cmd-1", "type": "command_execution"},
            }
        )
    )
    print(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 12,
                    "cached_input_tokens": 3,
                    "output_tokens": 7,
                    "reasoning_output_tokens": 1,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


_REVIEWER_STUB = r'''#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


NOW = "2026-05-26T00:00:00Z"


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
harness: efficiency-stub-reviewer
findings: []
---

Plan is acceptable for the dry efficiency harness.
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
harness: efficiency-stub-reviewer
findings: []
---

Story output is acceptable for the dry efficiency harness.
""",
    )


def main() -> int:
    prompt = sys.stdin.read()
    if '"node_type": "plan_critique"' in prompt:
        write_plan_critique(prompt)
    elif '"node_type": "critique_dispatch"' in prompt:
        write_story_critique(prompt)
    else:
        raise SystemExit("reviewer efficiency stub did not recognise prompt")
    print(
        json.dumps(
            {
                "type": "result",
                "session_id": "efficiency-stub-reviewer",
                "usage": {
                    "input_tokens": 9,
                    "output_tokens": 4,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 1,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


if __name__ == "__main__":
    raise SystemExit(main())
