"""Dispatch adapter boundary for Woof worker sessions.

This module owns policy run-profile resolution, tmux harness launch argv
construction, durable dispatch audit events, and structured result capture. The
top-level CLI module only wires the ``woof dispatch`` command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from woof.cli.harness_registry import build_launch_argv, canonical_harness, get_profile
from woof.cli.policy import load_policy, policy_path
from woof.graph.git import current_branch, git_env, head_sha
from woof.lib.audit_config import load_audit_config
from woof.lib.error_signature import normalise as _normalise_error_sig
from woof.lib.rate_limit import classify as _classify_rate_limit
from woof.paths import schema_dir

DEFAULT_TIMEOUT_MINUTES = 30
DEFAULT_IDLE_SECONDS = 600.0
DEFAULT_COMPLETION_GRACE_SECONDS = 60.0
DEFAULT_COMPLETION_TAIL_CAP_SECONDS = 120.0
WORK_UNIT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
AUDIT_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_-]+")
AGENTS_SCHEMA_PATH = schema_dir() / "agents.schema.json"
TRUSTED_RUNTIME_MODE = "trusted-local"
MODEL_PROFILE_ENV = "WOOF_MODEL_PROFILE"
NODE_GROUPS = frozenset({"discovery", "definition", "planning", "execution"})
TRUSTED_RUNTIME_NOTE = (
    "trusted-local runtime: Woof dispatches subscription CLIs through tmux_harness; "
    "commit safety is enforced through deterministic checks, reviewer critique, "
    "human gates, transaction manifests, and commit decisions"
)
POLICY_ROLE_SLOTS = {"primary": "producer", "reviewer": "reviewer"}
REVIEW_PROMPT_VERSION_PREFIX = "sha256:"
DEFAULT_READINESS_SECONDS = 60


@dataclass(frozen=True)
class RoleRoute:
    requested_role: str
    config_role: str
    adapter: str
    config: dict[str, Any]
    model_profile: str | None = None
    profile_role: str | None = None
    route_key: str | None = None


@dataclass(frozen=True)
class DispatchTimeouts:
    default_minutes: int | float = DEFAULT_TIMEOUT_MINUTES
    idle_seconds: float = DEFAULT_IDLE_SECONDS
    completion_grace_seconds: float = DEFAULT_COMPLETION_GRACE_SECONDS
    completion_tail_cap_seconds: float = DEFAULT_COMPLETION_TAIL_CAP_SECONDS

    @property
    def wallclock_seconds(self) -> float:
        return float(self.default_minutes) * 60.0

    def as_payload(self) -> dict[str, int | float]:
        return {
            "default_minutes": self.default_minutes,
            "idle_seconds": self.idle_seconds,
            "completion_grace_seconds": self.completion_grace_seconds,
            "completion_tail_cap_seconds": self.completion_tail_cap_seconds,
        }


class DispatchConfigError(ValueError):
    """Raised when a dispatch role exists but cannot be mapped to a public adapter."""


def trusted_runtime_policy() -> dict[str, Any]:
    """Return the operator-facing runtime policy summary for dispatch surfaces."""
    return {
        "mode": TRUSTED_RUNTIME_MODE,
        "woof_runtime_constraints": [],
        "cli_permission_mode": "interactive TUI harness profile flags",
        "safety_boundary": (
            "commit-safety checks, reviewer critique, human gates, transaction manifests, "
            "and commit decisions"
        ),
    }


def find_woof_root(start: Path) -> Path:
    """Walk up from ``start`` to the first directory containing ``.woof/``."""
    for candidate in (start, *start.parents):
        if (candidate / ".woof").is_dir():
            return candidate
    sys.stderr.write(f"woof: no .woof/ directory found at or above {start}; not a woof project\n")
    sys.exit(2)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_jsonl(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relpath(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def artefacts_byte_count(repo_root: Path, artefacts_loaded: list[str]) -> int:
    """Return the current byte size of explicitly audited repo artefacts."""
    total = 0
    for relpath in artefacts_loaded:
        total += (repo_root / relpath).stat().st_size
    return total


def _role_effort(adapter: str, role: dict[str, Any]) -> str | None:
    effort = role.get("effort")
    if effort is None:
        return None
    effort = str(effort)
    profile = get_profile(adapter)
    if profile.effort_levels and effort not in profile.effort_levels:
        raise DispatchConfigError(
            f"{profile.name} effort {effort!r} is not supported; "
            f"expected one of {sorted(profile.effort_levels)}"
        )
    return effort


def _policy_route(repo_root: Path, requested_role: str, route_key: str | None) -> RoleRoute | None:
    path = policy_path(repo_root)
    if not path.is_file():
        return None
    policy = load_policy(repo_root)
    if not isinstance(policy, dict):
        raise DispatchConfigError(str(policy))
    slot_name = POLICY_ROLE_SLOTS.get(requested_role)
    if slot_name is None:
        return None
    profile_name = os.environ.get(MODEL_PROFILE_ENV) or policy.get("default_run_profile")
    profiles = policy.get("run_profiles") or {}
    if not isinstance(profile_name, str) or not isinstance(profiles, dict):
        raise DispatchConfigError("policy default_run_profile and run_profiles must be declared")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise DispatchConfigError(f"policy run profile {profile_name!r} is not declared")
    slot = profile.get(slot_name)
    if not isinstance(slot, dict):
        raise DispatchConfigError(f"policy run profile {profile_name!r} has no {slot_name} slot")
    harness = slot.get("harness")
    model = slot.get("model")
    effort = slot.get("effort")
    if not isinstance(harness, str) or not harness.strip():
        raise DispatchConfigError(
            f"policy run profile {profile_name!r}.{slot_name}.harness missing"
        )
    if not isinstance(model, str) or not model.strip():
        raise DispatchConfigError(f"policy run profile {profile_name!r}.{slot_name}.model missing")
    if not isinstance(effort, str) or not effort.strip():
        raise DispatchConfigError(f"policy run profile {profile_name!r}.{slot_name}.effort missing")
    canonical = canonical_harness(harness)
    get_profile(canonical)
    return RoleRoute(
        requested_role=requested_role,
        config_role=slot_name,
        adapter=canonical,
        config={"harness": canonical, "model": model, "effort": effort},
        model_profile=profile_name,
        profile_role=slot_name,
        route_key=route_key,
    )


def dispatch_timeouts(agents: dict[str, Any]) -> DispatchTimeouts:
    block = agents.get("timeouts") or {}
    if not isinstance(block, dict):
        raise DispatchConfigError("timeouts table is not an object")

    timeout_fields = {
        "default_minutes": block.get("default_minutes", DEFAULT_TIMEOUT_MINUTES),
        "idle_seconds": block.get("idle_seconds", DEFAULT_IDLE_SECONDS),
        "completion_grace_seconds": block.get(
            "completion_grace_seconds", DEFAULT_COMPLETION_GRACE_SECONDS
        ),
        "completion_tail_cap_seconds": block.get(
            "completion_tail_cap_seconds", DEFAULT_COMPLETION_TAIL_CAP_SECONDS
        ),
    }
    for name, value in timeout_fields.items():
        if isinstance(value, bool):
            raise DispatchConfigError(f"timeouts.{name} must be numeric, not boolean")

    try:
        raw_default_minutes = timeout_fields["default_minutes"]
        default_minutes = float(raw_default_minutes)
        if isinstance(raw_default_minutes, int):
            default_minutes = raw_default_minutes
        idle_seconds = float(timeout_fields["idle_seconds"])
        completion_grace_seconds = float(timeout_fields["completion_grace_seconds"])
        completion_tail_cap_seconds = float(timeout_fields["completion_tail_cap_seconds"])
    except (TypeError, ValueError) as exc:
        raise DispatchConfigError(
            "timeouts.default_minutes and timeout seconds must be numeric"
        ) from exc

    if default_minutes <= 0:
        raise DispatchConfigError("timeouts.default_minutes must be > 0")
    for name, value in (
        ("idle_seconds", idle_seconds),
        ("completion_grace_seconds", completion_grace_seconds),
        ("completion_tail_cap_seconds", completion_tail_cap_seconds),
    ):
        if value < 0:
            raise DispatchConfigError(f"timeouts.{name} must be >= 0")

    return DispatchTimeouts(
        default_minutes=default_minutes,
        idle_seconds=idle_seconds,
        completion_grace_seconds=completion_grace_seconds,
        completion_tail_cap_seconds=completion_tail_cap_seconds,
    )


def claude_project_slug(repo_root: Path) -> str:
    """Return Claude Code's standard project directory slug for a repository path."""
    return str(repo_root.resolve()).replace("/", "-")


def claude_transcript_path(repo_root: Path, session_id: str) -> str:
    return f"~/.claude/projects/{claude_project_slug(repo_root)}/{session_id}.jsonl"


def normalise_artefacts_loaded(repo_root: Path, values: list[str] | None) -> list[str]:
    """Validate and canonicalise repo-relative artefact references for dispatch audit."""
    artefacts: list[str] = []
    seen: set[str] = set()
    root = repo_root.resolve()
    for raw in values or []:
        value = str(raw).strip()
        path = Path(value)
        if (
            not value
            or value.startswith("~")
            or path.is_absolute()
            or any(part == ".." for part in path.parts)
        ):
            raise DispatchConfigError(
                f"artefact reference {raw!r} is not repo-relative; "
                "use a file path below the project root"
            )
        resolved = (root / path).resolve()
        try:
            relpath = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise DispatchConfigError(
                f"artefact reference {raw!r} resolves outside the project root"
            ) from exc
        if not resolved.is_file():
            raise DispatchConfigError(f"artefact reference {raw!r} does not exist as a file")
        if relpath not in seen:
            artefacts.append(relpath)
            seen.add(relpath)
    return artefacts


def audit_argv(wrapped_argv: list[str]) -> list[str]:
    """Return argv suitable for durable audit events without duplicating the prompt."""
    return [*wrapped_argv, "<prompt:tmux-file>"]


def audit_file_stem(
    adapter: str,
    role: str,
    started_at: datetime,
    *,
    process_id: int | None = None,
    sequence: int | None = None,
) -> str:
    """Return a portable, path-safe stem for dispatch audit files."""
    pid = os.getpid() if process_id is None else process_id
    ts = started_at.strftime("%Y%m%dT%H%M%S%fZ")
    components = [
        _safe_audit_component(adapter, fallback="adapter"),
        _safe_audit_component(role, fallback="role"),
        ts,
        f"p{pid}",
    ]
    if sequence is not None:
        components.append(str(sequence))
    return "-".join(components)


def reserve_audit_base(
    audit_dir: Path,
    adapter: str,
    role: str,
    started_at: datetime,
    prompt: str,
    *,
    process_id: int | None = None,
) -> Path:
    """Atomically reserve a dispatch audit stem by creating its prompt file."""
    sequence: int | None = None
    while True:
        stem = audit_file_stem(
            adapter,
            role,
            started_at,
            process_id=process_id,
            sequence=sequence,
        )
        base = audit_dir / stem
        prompt_file = base.with_suffix(".prompt")
        try:
            fd = os.open(prompt_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            sequence = 2 if sequence is None else sequence + 1
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(prompt)
        return base


def _safe_audit_component(value: str, *, fallback: str) -> str:
    safe = AUDIT_COMPONENT_RE.sub("-", value).strip("-_")
    return safe or fallback


def _ensure_ajv() -> None:
    if shutil.which("ajv") is None:
        sys.stderr.write(
            "woof: ajv-cli not found on PATH.\nInstall: volta install ajv-cli ajv-formats\n"
        )
        sys.exit(2)


def _run_ajv(schema_path: Path, data_json: bytes) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as fh:
        fh.write(data_json)
        data_path = fh.name
    try:
        proc = subprocess.run(
            [
                "ajv",
                "validate",
                "--spec=draft2020",
                "-c",
                "ajv-formats",
                "-s",
                str(schema_path),
                "-d",
                data_path,
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(data_path).unlink(missing_ok=True)
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output


def _agents_schema_cache_path(repo_root: Path) -> Path:
    return repo_root / ".woof" / ".agents-schema-cache"


def _agents_schema_cache_key(agents_bytes: bytes, schema_bytes: bytes) -> str:
    """Cache key over both the config and the schema it was validated against.

    Folding the schema in means a Woof upgrade that changes agents.schema.json
    invalidates a pass recorded for an unchanged agents.toml under the old schema,
    instead of skipping re-validation against the new (possibly stricter) rules.
    """
    return hashlib.sha256(agents_bytes + b"\0" + schema_bytes).hexdigest()


def _check_agents_schema_cache(repo_root: Path, cache_key: str) -> bool:
    """Return True if agents.toml has already been validated under this cache key."""
    try:
        return _agents_schema_cache_path(repo_root).read_text().strip() == cache_key
    except OSError:
        return False


def _write_agents_schema_cache(repo_root: Path, cache_key: str) -> None:
    """Record that agents.toml passed schema validation under this cache key."""
    cache_path = _agents_schema_cache_path(repo_root)
    try:
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(cache_key + "\n")
        tmp.replace(cache_path)
    except OSError:
        pass


def _structured_result(answer: str) -> dict[str, Any]:
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tmux_capture_api():
    try:
        from tmux_harness import HarnessDispatchError, capture_argv
    except ModuleNotFoundError:
        candidates = [
            Path(__file__).resolve().parents[4] / "agent-toolkit" / "skills" / "tmux-harness",
            Path.home() / "Work" / "agent-toolkit" / "skills" / "tmux-harness",
            Path("/home/ryan/Work/agent-toolkit/skills/tmux-harness"),
        ]
        for candidate in candidates:
            if candidate.is_dir():
                sys.path.insert(0, str(candidate))
                from tmux_harness import HarnessDispatchError, capture_argv

                break
        else:
            raise
    return capture_argv, HarnessDispatchError


def _tmux_warm_api():
    try:
        from tmux_harness import deliver_prompt_file, read_file_kickoff, tmux
    except ModuleNotFoundError:
        candidates = [
            Path(__file__).resolve().parents[4] / "agent-toolkit" / "skills" / "tmux-harness",
            Path.home() / "Work" / "agent-toolkit" / "skills" / "tmux-harness",
            Path("/home/ryan/Work/agent-toolkit/skills/tmux-harness"),
        ]
        for candidate in candidates:
            if candidate.is_dir():
                sys.path.insert(0, str(candidate))
                from tmux_harness import deliver_prompt_file, read_file_kickoff, tmux

                break
        else:
            raise
    return deliver_prompt_file, read_file_kickoff, tmux


def _structured_usage(result: dict[str, Any]) -> dict[str, int]:
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return {}
    mapped: dict[str, int] = {}
    aliases = {
        "tokens_in": ("tokens_in", "input_tokens"),
        "tokens_out": ("tokens_out", "output_tokens"),
        "cache_read_tokens": ("cache_read_tokens", "cache_read_input_tokens"),
        "cache_write_tokens": ("cache_write_tokens", "cache_creation_input_tokens"),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                mapped[target] = value
                break
    return mapped


def _result_session_metadata(result: dict[str, Any], tmux_meta: dict[str, Any]) -> dict[str, Any]:
    session = result.get("session")
    metadata: dict[str, Any] = {}
    if isinstance(session, dict):
        for key in ("id", "path", "transcript_path", "thread_id"):
            value = session.get(key)
            if isinstance(value, str) and value:
                metadata[f"worker_session_{key}"] = value
    tmux_session = tmux_meta.get("session")
    if isinstance(tmux_session, str) and tmux_session:
        metadata["tmux_session"] = tmux_session
    transport = tmux_meta.get("transport")
    if isinstance(transport, str) and transport:
        metadata["tmux_transport"] = transport
    return metadata


def _copy_result_fields(event: dict[str, Any], result: dict[str, Any]) -> None:
    verdict = result.get("verdict")
    if isinstance(verdict, str) and verdict:
        event["verdict"] = verdict
    evidence = result.get("evidence")
    if isinstance(evidence, str | list | dict):
        event["evidence"] = evidence
    artefacts = result.get("artefacts")
    if isinstance(artefacts, list):
        event["result_artefacts"] = [str(item) for item in artefacts]


def _warm_session_name(run_id: str, work_unit_id: str, role: str) -> str:
    run = _safe_audit_component(run_id, fallback="run")
    unit = _safe_audit_component(work_unit_id, fallback="unit")
    role_component = _safe_audit_component(role, fallback="role")
    return f"woof-{run}-{unit}-{role_component}"


def _executor_result_ready(path: Path, epic_id: int, work_unit_id: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    except json.JSONDecodeError:
        # Readiness, not validity: a present-but-malformed result means the executor wrote
        # its final artefact, so stop polling. Stage-5 verification (graph/nodes.py) validates
        # the JSON and opens the incomplete_stage_state gate on corruption, so a malformed
        # result reaches abandon_work_unit rather than stranding at a wallclock timeout.
        return True
    return payload.get("epic_id") == epic_id and payload.get("work_unit_id") == work_unit_id


def ensure_run_metadata(epic_dir: Path, epic_id: int, created_at: datetime) -> str:
    """Return the durable run id for this epic, creating run metadata once."""

    path = epic_dir / "run.json"
    repo_root = epic_dir.resolve().parents[2]
    worktrees = _profile_a_run_worktrees(repo_root, epic_dir)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if isinstance(run_id, str) and run_id:
            if worktrees is not None and isinstance(payload, dict) and "worktrees" not in payload:
                payload["worktrees"] = worktrees
                path.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            return run_id

    run_id = f"run-{epic_id}-{uuid.uuid4().hex[:12]}"
    payload = {"run_id": run_id, "epic_id": epic_id, "created_at": iso_utc(created_at)}
    if worktrees is not None:
        payload["worktrees"] = worktrees
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return run_id


def _profile_a_run_worktrees(repo_root: Path, epic_dir: Path) -> dict[str, Any] | None:
    policy = load_policy(repo_root)
    if not isinstance(policy, dict):
        return None
    delivery = policy.get("delivery")
    profiles = policy.get("profiles")
    if not isinstance(delivery, dict) or delivery.get("profile") != "A":
        return None
    if not isinstance(profiles, dict):
        return None
    profile_a = profiles.get("A")
    if not isinstance(profile_a, dict):
        return None
    worktree = profile_a.get("worktree")
    if not isinstance(worktree, dict):
        return None
    root = worktree.get("root")
    engine = worktree.get("engine")
    if (
        not isinstance(root, str)
        or not root.strip()
        or not isinstance(engine, str)
        or not engine.strip()
    ):
        return None
    derivation = (
        worktree.get("derivation") if isinstance(worktree.get("derivation"), str) else "unit_id"
    )
    metadata = {"derivation": derivation, "engine": engine, "root": root}
    if derivation != "unit_id":
        return metadata
    unit_ids = _plan_work_unit_ids(epic_dir / "plan.json")
    metadata["unit_paths"] = {unit_id: f"{root.rstrip('/')}/{unit_id}" for unit_id in unit_ids}
    return metadata


def _plan_work_unit_ids(plan_path: Path) -> list[str]:
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    units = payload.get("work_units") if isinstance(payload, dict) else None
    if not isinstance(units, list):
        return []
    return [
        unit["id"] for unit in units if isinstance(unit, dict) and isinstance(unit.get("id"), str)
    ]


def _attempt_id(run_id: str, role: str, work_unit_id: str | None, started_at: datetime) -> str:
    parts = [
        _safe_audit_component(run_id, fallback="run"),
        _safe_audit_component(work_unit_id or "epic", fallback="unit"),
        _safe_audit_component(role, fallback="role"),
        started_at.strftime("%Y%m%dT%H%M%S%fZ"),
        f"p{os.getpid()}",
    ]
    return "-".join(parts)


def _staged_diff_hash(repo_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--binary"],
            cwd=repo_root,
            env=git_env(),
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return _sha256_bytes(proc.stdout)


def _review_key(
    *,
    work_unit_id: str,
    diff_hash: str,
    prompt_version: str,
) -> str:
    payload = {
        "diff_hash": diff_hash,
        "prompt_version": prompt_version,
        "work_unit_id": work_unit_id,
    }
    return _sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _review_cache_path(epic_dir: Path, review_cache_key: str) -> Path:
    return epic_dir / "reviews" / "cache" / f"{review_cache_key}.json"


def _review_attempts_dir(epic_dir: Path) -> Path:
    return epic_dir / "reviews" / "attempts"


def _attempts_dir(epic_dir: Path) -> Path:
    return epic_dir / "attempts"


def _load_review_cache(epic_dir: Path, review_cache_key: str) -> dict[str, Any] | None:
    path = _review_cache_path(epic_dir, review_cache_key)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_attempt_file(epic_dir: Path, attempt_id: str, payload: dict[str, Any]) -> Path:
    path = _attempts_dir(epic_dir) / f"{attempt_id}.json"
    _write_json_file(path, payload)
    return path


def _write_review_attempt_file(epic_dir: Path, attempt_id: str, payload: dict[str, Any]) -> Path:
    path = _review_attempts_dir(epic_dir) / f"{attempt_id}.json"
    _write_json_file(path, payload)
    return path


def _prior_review_verdicts(epic_dir: Path, review_cache_key: str) -> set[str]:
    verdicts: set[str] = set()
    attempts_dir = _review_attempts_dir(epic_dir)
    if not attempts_dir.is_dir():
        return verdicts
    for path in sorted(attempts_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("review_cache_key") != review_cache_key:
            continue
        verdict = payload.get("verdict")
        if isinstance(verdict, str) and verdict:
            verdicts.add(verdict)
    return verdicts


def _record_review_instability(
    epic_dir: Path,
    *,
    repo_root: Path,
    run_id: str,
    attempt_id: str,
    work_unit_id: str,
    review_cache_key: str,
    diff_hash: str,
    prompt_version: str,
    prior_verdicts: set[str],
    new_verdict: str,
) -> str:
    path = epic_dir / "reviews" / "instability" / f"{review_cache_key}.jsonl"
    event = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "work_unit_id": work_unit_id,
        "review_cache_key": review_cache_key,
        "diff_hash": diff_hash,
        "prompt_version": prompt_version,
        "prior_verdicts": sorted(prior_verdicts),
        "new_verdict": new_verdict,
        "at": iso_utc(datetime.now(UTC)),
    }
    append_jsonl(path, event)
    return _relpath(repo_root, path)


def _write_review_cache_entry(
    epic_dir: Path,
    *,
    repo_root: Path,
    review_cache_key: str,
    run_id: str,
    attempt_id: str,
    work_unit_id: str,
    diff_hash: str,
    prompt_hash: str,
    prompt_version: str,
    verdict: str,
    answer: str,
    stderr: str,
    structured: dict[str, Any],
    exit_code: int,
    exit_type: str,
) -> None:
    critique_path = epic_dir / "critique" / f"work-unit-{work_unit_id}.md"
    critique_text = critique_path.read_text(encoding="utf-8") if critique_path.is_file() else None
    payload: dict[str, Any] = {
        "review_cache_key": review_cache_key,
        "run_id": run_id,
        "source_attempt_id": attempt_id,
        "work_unit_id": work_unit_id,
        "diff_hash": diff_hash,
        "prompt_hash": prompt_hash,
        "prompt_version": prompt_version,
        "verdict": verdict,
        "answer": answer,
        "stderr": stderr,
        "structured_result": structured,
        "exit_code": exit_code,
        "exit_type": exit_type,
        "created_at": iso_utc(datetime.now(UTC)),
    }
    if critique_text is not None:
        payload["critique_path"] = _relpath(repo_root, critique_path)
        payload["critique_text"] = critique_text
    _write_json_file(_review_cache_path(epic_dir, review_cache_key), payload)


def _restore_cached_critique(repo_root: Path, cached: dict[str, Any]) -> None:
    critique_path = cached.get("critique_path")
    critique_text = cached.get("critique_text")
    if isinstance(critique_path, str) and isinstance(critique_text, str):
        path = repo_root / critique_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(critique_text, encoding="utf-8")


def cmd_dispatch(args: argparse.Namespace) -> int:
    repo_root = find_woof_root(Path.cwd().resolve())
    agents_path = repo_root / ".woof" / "agents.toml"
    agents: dict[str, Any] = {}
    agents_schema_cache_hit = False
    if agents_path.is_file():
        agents_bytes = agents_path.read_bytes()
        agents = tomllib.loads(agents_bytes.decode("utf-8"))
        try:
            schema_bytes = AGENTS_SCHEMA_PATH.read_bytes()
        except OSError:
            # Missing schema: empty key still misses, then ajv runs and fails cleanly.
            schema_bytes = b""
        agents_schema_cache_key = _agents_schema_cache_key(agents_bytes, schema_bytes)
        agents_schema_cache_hit = _check_agents_schema_cache(repo_root, agents_schema_cache_key)
        if not agents_schema_cache_hit:
            _ensure_ajv()
            ok, output = _run_ajv(AGENTS_SCHEMA_PATH, json.dumps(agents).encode())
            if not ok:
                sys.stderr.write(f"woof: {agents_path}: schema invalid\n{output}\n")
                return 2
            _write_agents_schema_cache(repo_root, agents_schema_cache_key)

    try:
        route = _policy_route(repo_root, args.role, args.route_key)
        if route is None:
            sys.stderr.write(
                f"woof: {policy_path(repo_root)} does not declare dispatch role {args.role!r}\n"
            )
            return 2
    except DispatchConfigError as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2

    if route.adapter == "in-session":
        sys.stderr.write(f"woof: role '{args.role}' is in-session; cannot be dispatched\n")
        return 2
    if args.target and args.target != route.adapter:
        sys.stderr.write(
            f"woof: role '{args.role}' resolves adapter={route.adapter!r}; "
            f"legacy target {args.target!r} does not match\n"
        )
        return 2

    if args.work_unit is not None and not WORK_UNIT_ID_RE.match(args.work_unit):
        sys.stderr.write(
            f"woof: --work-unit {args.work_unit!r}: must match ^[A-Za-z][A-Za-z0-9._-]*$\n"
        )
        return 2
    if args.session_mode == "warm-producer" and (args.role != "primary" or args.work_unit is None):
        sys.stderr.write(
            "woof: --session-mode warm-producer requires --role primary and --work-unit\n"
        )
        return 2

    prompt = Path(args.prompt_file).read_text() if args.prompt_file else sys.stdin.read()
    if not prompt.strip():
        sys.stderr.write("woof: empty prompt\n")
        return 2

    try:
        timeouts = dispatch_timeouts(agents)
        audit_config = load_audit_config(agents)
    except (DispatchConfigError, TypeError, ValueError) as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2

    try:
        effort = _role_effort(route.adapter, route.config)
        mcp_names: list[str] = []
        argv = build_launch_argv(
            route.adapter,
            model=route.config.get("model"),
            effort=effort,
        )
        argv.extend(str(flag) for flag in route.config.get("flags") or [])
        artefacts_loaded = normalise_artefacts_loaded(repo_root, args.artefacts_loaded)
        artefact_bytes = artefacts_byte_count(repo_root, artefacts_loaded)
    except (DispatchConfigError, ValueError) as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2

    prompt_bytes = len(prompt.encode("utf-8"))
    prompt_hash = _sha256_text(prompt)
    prompt_version = f"{REVIEW_PROMPT_VERSION_PREFIX}{prompt_hash}"
    diff_hash = _staged_diff_hash(repo_root)
    work_unit_id = args.work_unit
    review_cache_key = (
        _review_key(
            work_unit_id=work_unit_id,
            diff_hash=diff_hash,
            prompt_version=prompt_version,
        )
        if args.role == "reviewer" and work_unit_id and diff_hash is not None
        else None
    )
    prompt_transport = "tmux_harness_prompt_file"

    if args.dry_run:
        payload = {
            "argv": argv,
            "prompt_transport": prompt_transport,
            "runtime_policy": trusted_runtime_policy(),
            "epic": args.epic,
            "role": args.role,
            "config_role": route.config_role,
            "adapter": route.adapter,
            "harness": route.adapter,
            "model_profile": route.model_profile,
            "profile_role": route.profile_role,
            "route_key": route.route_key,
            "model": route.config.get("model"),
            "effort": effort,
            "mcp": mcp_names,
            "flags": route.config.get("flags") or [],
            "timeout_min": timeouts.default_minutes,
            "timeouts": timeouts.as_payload(),
            "artefacts_loaded": artefacts_loaded,
            "prompt_bytes": prompt_bytes,
            "artefact_bytes": artefact_bytes,
            "prompt_hash": prompt_hash,
            "prompt_version": prompt_version,
            "diff_hash": diff_hash,
            "work_unit_id": work_unit_id,
            "review_cache_key": review_cache_key,
            "session_mode": args.session_mode,
        }
        print(json.dumps(payload))
        return 0

    epic_dir = repo_root / ".woof" / "epics" / f"E{args.epic}"
    audit_dir = epic_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    run_id = ensure_run_metadata(epic_dir, args.epic, started_at)
    attempt_id = _attempt_id(run_id, args.role, work_unit_id, started_at)
    base = reserve_audit_base(audit_dir, route.adapter, args.role, started_at, prompt)
    output_file = base.with_suffix(".output")
    stderr_file = base.with_suffix(".stderr")
    meta_file = base.with_suffix(".meta")

    dispatch_jsonl = epic_dir / "dispatch.jsonl"
    event_argv = audit_argv(argv)
    launcher_pid = os.getpid()

    head_before = head_sha(repo_root)
    branch_before = current_branch(repo_root)
    lineage_fields: dict[str, Any] = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "prompt_hash": prompt_hash,
        "prompt_version": prompt_version,
    }
    if work_unit_id:
        lineage_fields["work_unit_id"] = work_unit_id
    if diff_hash is not None:
        lineage_fields["diff_hash"] = diff_hash
    if review_cache_key is not None:
        lineage_fields["review_cache_key"] = review_cache_key

    if review_cache_key is not None:
        assert work_unit_id is not None
        assert diff_hash is not None
        cached = _load_review_cache(epic_dir, review_cache_key)
        if cached is not None:
            ended_at = datetime.now(UTC)
            answer = str(cached.get("answer") or "")
            stderr = str(cached.get("stderr") or "")
            structured = cached.get("structured_result")
            structured = structured if isinstance(structured, dict) else _structured_result(answer)
            exit_code = int(cached.get("exit_code") or 0)
            exit_type = str(cached.get("exit_type") or ("clean" if exit_code == 0 else "nonzero"))
            _restore_cached_critique(repo_root, cached)
            output_file.write_text(answer, encoding="utf-8")
            stderr_file.write_text(stderr, encoding="utf-8")
            output_bytes = len(answer.encode("utf-8"))
            stderr_bytes = len(stderr.encode("utf-8"))
            duration_ms = int((ended_at - started_at).total_seconds() * 1000)
            verdict = str(cached.get("verdict") or structured.get("verdict") or "").strip().lower()
            prior_verdicts = _prior_review_verdicts(epic_dir, review_cache_key)
            instability_path = None
            if verdict and any(item != verdict for item in prior_verdicts):
                instability_path = _record_review_instability(
                    epic_dir,
                    repo_root=repo_root,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    work_unit_id=work_unit_id,
                    review_cache_key=review_cache_key,
                    diff_hash=diff_hash,
                    prompt_version=prompt_version,
                    prior_verdicts=prior_verdicts,
                    new_verdict=verdict,
                )

            cache_event: dict[str, Any] = {
                "event": "review_cache_hit",
                "at": iso_utc(ended_at),
                "epic_id": args.epic,
                "role": args.role,
                "harness": route.adapter,
                "adapter": route.adapter,
                "pid": launcher_pid,
                "exit_type": exit_type,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "output_bytes": output_bytes,
                "stderr_bytes": stderr_bytes,
                "artefacts_loaded": artefacts_loaded,
                "prompt_bytes": prompt_bytes,
                "artefact_bytes": artefact_bytes,
                "review_cache_hit": True,
                **lineage_fields,
            }
            if args.work_unit:
                cache_event["work_unit_id"] = args.work_unit
            if route.route_key:
                cache_event["route_key"] = route.route_key
            if route.config.get("model"):
                cache_event["model"] = route.config["model"]
            if effort:
                cache_event["effort"] = effort
            _copy_result_fields(cache_event, structured)
            if instability_path:
                cache_event["review_instability_path"] = instability_path
            append_jsonl(dispatch_jsonl, cache_event)

            audit_paths = {
                "prompt": _relpath(repo_root, base.with_suffix(".prompt")),
                "output": _relpath(repo_root, output_file),
                "stderr": _relpath(repo_root, stderr_file),
                "meta": _relpath(repo_root, meta_file),
            }
            attempt_payload = {
                "attempt_kind": "review_cache_hit",
                "audit_paths": audit_paths,
                "cached_from_attempt_id": cached.get("source_attempt_id"),
                "ended_at": iso_utc(ended_at),
                "epic_id": args.epic,
                "exit_code": exit_code,
                "exit_type": exit_type,
                "role": args.role,
                "started_at": iso_utc(started_at),
                "verdict": verdict,
                **lineage_fields,
            }
            if instability_path:
                attempt_payload["review_instability_path"] = instability_path
            attempt_path = _write_attempt_file(epic_dir, attempt_id, attempt_payload)

            meta = {
                "harness": route.adapter,
                "adapter": route.adapter,
                "role": args.role,
                "config_role": route.config_role,
                "model_profile": route.model_profile,
                "profile_role": route.profile_role,
                "route_key": route.route_key,
                "epic_id": args.epic,
                "work_unit_id": args.work_unit,
                "model": route.config.get("model"),
                "effort": effort,
                "mcp": mcp_names,
                "flags": route.config.get("flags") or [],
                "argv": event_argv,
                "prompt_transport": prompt_transport,
                "runtime_policy": trusted_runtime_policy(),
                "artefacts_loaded": artefacts_loaded,
                "pid": launcher_pid,
                "started_at": iso_utc(started_at),
                "ended_at": iso_utc(ended_at),
                "duration_ms": duration_ms,
                "exit_type": exit_type,
                "exit_code": exit_code,
                "timed_out": False,
                "terminal_seen": True,
                "timeouts": timeouts.as_payload(),
                "prompt_bytes": prompt_bytes,
                "artefact_bytes": artefact_bytes,
                "output_bytes": output_bytes,
                "stderr_bytes": stderr_bytes,
                "tokens": _structured_usage(structured),
                "structured_result": structured,
                "review_cache_hit": True,
                "attempt_path": _relpath(repo_root, attempt_path),
                **lineage_fields,
            }
            if instability_path:
                meta["review_instability_path"] = instability_path
            _copy_result_fields(meta, structured)
            meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            return exit_code

    spawned_event: dict[str, Any] = {
        "event": "subprocess_spawned",
        "at": iso_utc(started_at),
        "epic_id": args.epic,
        "role": args.role,
        "harness": route.adapter,
        "adapter": route.adapter,
        "pid": launcher_pid,
        "mcp": mcp_names,
        "argv": event_argv,
        "prompt_transport": prompt_transport,
        "session_mode": args.session_mode,
        "runtime_policy": trusted_runtime_policy(),
        "agents_schema_cache_hit": agents_schema_cache_hit,
        "artefacts_loaded": artefacts_loaded,
        "prompt_bytes": prompt_bytes,
        "artefact_bytes": artefact_bytes,
        **lineage_fields,
    }
    if route.config_role != args.role:
        spawned_event["config_role"] = route.config_role
    if route.model_profile:
        spawned_event["model_profile"] = route.model_profile
    if route.profile_role:
        spawned_event["profile_role"] = route.profile_role
    if route.route_key:
        spawned_event["route_key"] = route.route_key
    if args.work_unit:
        spawned_event["work_unit_id"] = args.work_unit
    if route.config.get("model"):
        spawned_event["model"] = route.config["model"]
    if effort:
        spawned_event["effort"] = effort
    append_jsonl(dispatch_jsonl, spawned_event)

    if args.session_mode == "warm-producer":
        assert work_unit_id is not None
        ended_at = datetime.now(UTC)
        answer = ""
        stderr = ""
        reported_exit_code = 0
        exit_type = "clean"
        tmux_meta: dict[str, Any] = {}
        session = _warm_session_name(run_id, work_unit_id, args.role)
        result_path = epic_dir / "executor_result.json"
        try:
            deliver_prompt_file, read_file_kickoff, tmux = _tmux_warm_api()
            reattached = tmux.has_session(session)
            respawned = not reattached
            if respawned:
                tmux.launch_session(session, repo_root, argv)
                tmux.wait_for_input_ready(
                    session,
                    readiness_timeout_s=DEFAULT_READINESS_SECONDS,
                )
            deliver_prompt_file(
                session,
                base.with_suffix(".prompt"),
                read_file_kickoff(base.with_suffix(".prompt")),
                prompt=None,
            )
            deadline = time.monotonic() + timeouts.wallclock_seconds
            while time.monotonic() < deadline:
                if _executor_result_ready(result_path, args.epic, work_unit_id):
                    break
                if not tmux.has_session(session):
                    reported_exit_code = 1
                    exit_type = "nonzero"
                    stderr = (
                        f"warm producer session {session!r} exited before writing "
                        f"{_relpath(repo_root, result_path)}"
                    )
                    break
                time.sleep(1.0)
            else:
                reported_exit_code = 124
                exit_type = "wallclock_timeout"
                stderr = (
                    f"warm producer session {session!r} did not write "
                    f"{_relpath(repo_root, result_path)} within {int(timeouts.wallclock_seconds)}s"
                )
            answer = tmux.capture_pane_tail(session, 80)
            tmux_meta = {
                "transport": f"tmux:{route.adapter}",
                "harness": route.adapter,
                "model": route.config.get("model"),
                "effort": effort,
                "session": session,
                "reattached": reattached,
                "respawned": respawned,
            }
        except Exception as exc:
            stderr = str(exc)
            reported_exit_code = 1
            exit_type = "nonzero"
        ended_at = datetime.now(UTC)
        output_file.write_text(answer, encoding="utf-8")
        stderr_file.write_text(stderr, encoding="utf-8")
        output_bytes = len(answer.encode("utf-8"))
        stderr_bytes = len(stderr.encode("utf-8"))
        duration_ms = int((ended_at - started_at).total_seconds() * 1000)
        returned: dict[str, Any] = {
            "event": "subprocess_returned",
            "at": iso_utc(ended_at),
            "epic_id": args.epic,
            "role": args.role,
            "harness": route.adapter,
            "adapter": route.adapter,
            "pid": launcher_pid,
            "exit_type": exit_type,
            "exit_code": reported_exit_code,
            "duration_ms": duration_ms,
            "timed_out": exit_type == "wallclock_timeout",
            "terminal_seen": reported_exit_code == 0,
            "mcp": mcp_names,
            "argv": event_argv,
            "prompt_transport": prompt_transport,
            "session_mode": args.session_mode,
            "artefacts_loaded": artefacts_loaded,
            "prompt_bytes": prompt_bytes,
            "artefact_bytes": artefact_bytes,
            "output_bytes": output_bytes,
            "stderr_bytes": stderr_bytes,
            **lineage_fields,
        }
        if route.config_role != args.role:
            returned["config_role"] = route.config_role
        if route.model_profile:
            returned["model_profile"] = route.model_profile
        if route.profile_role:
            returned["profile_role"] = route.profile_role
        if route.route_key:
            returned["route_key"] = route.route_key
        if args.work_unit:
            returned["work_unit_id"] = args.work_unit
        if route.config.get("model"):
            returned["model"] = route.config["model"]
        if effort:
            returned["effort"] = effort
        returned.update(_result_session_metadata({}, tmux_meta))
        if "reattached" in tmux_meta:
            returned["producer_session_reattached"] = bool(tmux_meta["reattached"])
            returned["producer_session_respawned"] = bool(tmux_meta["respawned"])
        append_jsonl(dispatch_jsonl, returned)

        audit_paths = {
            "prompt": _relpath(repo_root, base.with_suffix(".prompt")),
            "output": _relpath(repo_root, output_file),
            "stderr": _relpath(repo_root, stderr_file),
            "meta": _relpath(repo_root, meta_file),
        }
        attempt_payload = {
            "attempt_kind": "dispatch",
            "audit_paths": audit_paths,
            "ended_at": iso_utc(ended_at),
            "epic_id": args.epic,
            "exit_code": reported_exit_code,
            "exit_type": exit_type,
            "role": args.role,
            "started_at": iso_utc(started_at),
            "verdict": "",
            "session_mode": args.session_mode,
            **lineage_fields,
        }
        attempt_path = _write_attempt_file(epic_dir, attempt_id, attempt_payload)

        meta = {
            "harness": route.adapter,
            "adapter": route.adapter,
            "role": args.role,
            "config_role": route.config_role,
            "model_profile": route.model_profile,
            "profile_role": route.profile_role,
            "route_key": route.route_key,
            "epic_id": args.epic,
            "work_unit_id": args.work_unit,
            "model": route.config.get("model"),
            "effort": effort,
            "mcp": mcp_names,
            "flags": route.config.get("flags") or [],
            "argv": event_argv,
            "prompt_transport": prompt_transport,
            "session_mode": args.session_mode,
            "runtime_policy": trusted_runtime_policy(),
            "artefacts_loaded": artefacts_loaded,
            "pid": launcher_pid,
            "started_at": iso_utc(started_at),
            "ended_at": iso_utc(ended_at),
            "duration_ms": duration_ms,
            "exit_type": exit_type,
            "exit_code": reported_exit_code,
            "timed_out": exit_type == "wallclock_timeout",
            "terminal_seen": reported_exit_code == 0,
            "timeouts": timeouts.as_payload(),
            "prompt_bytes": prompt_bytes,
            "artefact_bytes": artefact_bytes,
            "output_bytes": output_bytes,
            "stderr_bytes": stderr_bytes,
            "tokens": {},
            "structured_result": {},
            "tmux_harness": tmux_meta,
            "attempt_path": _relpath(repo_root, attempt_path),
            **lineage_fields,
        }
        meta.update(_result_session_metadata({}, tmux_meta))
        if "reattached" in tmux_meta:
            meta["producer_session_reattached"] = bool(tmux_meta["reattached"])
            meta["producer_session_respawned"] = bool(tmux_meta["respawned"])
        meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return reported_exit_code

    answer = ""
    stderr = ""
    tmux_meta: dict[str, Any] = {}
    exit_type = "clean"
    reported_exit_code = 0
    tmux_capture_argv, tmux_error = _tmux_capture_api()
    launch_env = {key: value for key, value in os.environ.items() if key.startswith("WOOF_")}
    launch_env["WOOF_REPO_ROOT"] = str(repo_root)
    try:
        answer, tmux_meta = tmux_capture_argv(
            ["env", *(f"{key}={value}" for key, value in sorted(launch_env.items())), *argv],
            prompt,
            label=route.adapter,
            model=route.config.get("model"),
            effort=effort,
            completion_timeout_s=int(timeouts.wallclock_seconds),
        )
    except tmux_error as exc:
        stderr = str(exc)
        exit_type = "nonzero"
        reported_exit_code = 1

    ended_at = datetime.now(UTC)
    head_after = head_sha(repo_root)
    branch_after = current_branch(repo_root)
    if len(answer.encode("utf-8")) > audit_config.max_bytes:
        answer = answer.encode("utf-8")[: audit_config.max_bytes].decode("utf-8", errors="replace")
    structured = _structured_result(answer)
    verdict = str(structured.get("verdict") or "").strip().lower()
    if exit_type == "clean" and verdict in {"error", "fail", "failed", "blocked"}:
        exit_type = "nonzero"
        reported_exit_code = 1
        evidence = structured.get("evidence")
        if not stderr and isinstance(evidence, str):
            stderr = evidence
    output_file.write_text(answer, encoding="utf-8")
    stderr_file.write_text(stderr, encoding="utf-8")
    output_bytes = len(answer.encode("utf-8"))
    stderr_bytes = len(stderr.encode("utf-8"))
    duration_ms = int(tmux_meta.get("latency_ms") or (ended_at - started_at).total_seconds() * 1000)
    tokens = _structured_usage(structured)

    returned: dict[str, Any] = {
        "event": "subprocess_returned",
        "at": iso_utc(ended_at),
        "epic_id": args.epic,
        "role": args.role,
        "harness": route.adapter,
        "adapter": route.adapter,
        "pid": launcher_pid,
        "exit_type": exit_type,
        "exit_code": reported_exit_code,
        "duration_ms": duration_ms,
        "timed_out": False,
        "terminal_seen": True,
        "mcp": mcp_names,
        "argv": event_argv,
        "prompt_transport": prompt_transport,
        "artefacts_loaded": artefacts_loaded,
        "prompt_bytes": prompt_bytes,
        "artefact_bytes": artefact_bytes,
        "output_bytes": output_bytes,
        "stderr_bytes": stderr_bytes,
        **lineage_fields,
    }
    if route.config_role != args.role:
        returned["config_role"] = route.config_role
    if route.model_profile:
        returned["model_profile"] = route.model_profile
    if route.profile_role:
        returned["profile_role"] = route.profile_role
    if route.route_key:
        returned["route_key"] = route.route_key
    if args.work_unit:
        returned["work_unit_id"] = args.work_unit
    if route.config.get("model"):
        returned["model"] = route.config["model"]
    if effort:
        returned["effort"] = effort
    if tokens:
        returned.update(tokens)
    returned.update(_result_session_metadata(structured, tmux_meta))
    _copy_result_fields(returned, structured)
    error_sig = _normalise_error_sig(stderr) if stderr.strip() else None
    if error_sig:
        returned["error_signature"] = error_sig
    rl = _classify_rate_limit(answer, stderr)
    if rl is not None:
        returned["rate_limit"] = rl
    if head_before is not None:
        returned["head_before"] = head_before
    if head_after is not None:
        returned["head_after"] = head_after
    if branch_before is not None:
        returned["branch_before"] = branch_before
    if branch_after is not None:
        returned["branch_after"] = branch_after

    verdict = str(structured.get("verdict") or "").strip().lower()
    instability_path = None
    if review_cache_key is not None and work_unit_id is not None and diff_hash is not None:
        prior_verdicts = _prior_review_verdicts(epic_dir, review_cache_key)
        if verdict and any(item != verdict for item in prior_verdicts):
            instability_path = _record_review_instability(
                epic_dir,
                repo_root=repo_root,
                run_id=run_id,
                attempt_id=attempt_id,
                work_unit_id=work_unit_id,
                review_cache_key=review_cache_key,
                diff_hash=diff_hash,
                prompt_version=prompt_version,
                prior_verdicts=prior_verdicts,
                new_verdict=verdict,
            )
            returned["review_instability_path"] = instability_path

    append_jsonl(dispatch_jsonl, returned)

    meta = {
        "harness": route.adapter,
        "adapter": route.adapter,
        "role": args.role,
        "config_role": route.config_role,
        "model_profile": route.model_profile,
        "profile_role": route.profile_role,
        "route_key": route.route_key,
        "epic_id": args.epic,
        "work_unit_id": args.work_unit,
        "model": route.config.get("model"),
        "effort": effort,
        "mcp": mcp_names,
        "flags": route.config.get("flags") or [],
        "argv": event_argv,
        "prompt_transport": prompt_transport,
        "runtime_policy": trusted_runtime_policy(),
        "artefacts_loaded": artefacts_loaded,
        "pid": launcher_pid,
        "started_at": iso_utc(started_at),
        "ended_at": iso_utc(ended_at),
        "duration_ms": duration_ms,
        "exit_type": exit_type,
        "exit_code": reported_exit_code,
        "timed_out": False,
        "terminal_seen": True,
        "timeouts": timeouts.as_payload(),
        "prompt_bytes": prompt_bytes,
        "artefact_bytes": artefact_bytes,
        "output_bytes": output_bytes,
        "stderr_bytes": stderr_bytes,
        "tokens": tokens,
        "structured_result": structured,
        "tmux_harness": tmux_meta,
        **lineage_fields,
    }
    if instability_path:
        meta["review_instability_path"] = instability_path
    _copy_result_fields(meta, structured)
    meta.update(_result_session_metadata(structured, tmux_meta))

    audit_paths = {
        "prompt": _relpath(repo_root, base.with_suffix(".prompt")),
        "output": _relpath(repo_root, output_file),
        "stderr": _relpath(repo_root, stderr_file),
        "meta": _relpath(repo_root, meta_file),
    }
    attempt_payload = {
        "attempt_kind": "dispatch",
        "audit_paths": audit_paths,
        "ended_at": iso_utc(ended_at),
        "epic_id": args.epic,
        "exit_code": reported_exit_code,
        "exit_type": exit_type,
        "role": args.role,
        "started_at": iso_utc(started_at),
        "verdict": verdict,
        **lineage_fields,
    }
    if instability_path:
        attempt_payload["review_instability_path"] = instability_path
    attempt_path = _write_attempt_file(epic_dir, attempt_id, attempt_payload)
    meta["attempt_path"] = _relpath(repo_root, attempt_path)

    if review_cache_key is not None and work_unit_id is not None and diff_hash is not None:
        review_attempt_path = _write_review_attempt_file(epic_dir, attempt_id, attempt_payload)
        meta["review_attempt_path"] = _relpath(repo_root, review_attempt_path)
        if verdict:
            _write_review_cache_entry(
                epic_dir,
                repo_root=repo_root,
                review_cache_key=review_cache_key,
                run_id=run_id,
                attempt_id=attempt_id,
                work_unit_id=work_unit_id,
                diff_hash=diff_hash,
                prompt_hash=prompt_hash,
                prompt_version=prompt_version,
                verdict=verdict,
                answer=answer,
                stderr=stderr,
                structured=structured,
                exit_code=reported_exit_code,
                exit_type=exit_type,
            )

    meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return reported_exit_code
