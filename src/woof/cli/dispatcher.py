"""Dispatch adapter boundary for Woof subprocesses.

This module owns public CLI adapter route resolution, command construction,
Claude MCP JSON rendering, durable dispatch audit events, and token parsing.
The top-level CLI module only wires the ``woof dispatch`` command.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from woof.paths import schema_dir

ADAPTERS = {"claude", "codex"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
CODEX_EFFORTS = {"low", "medium", "high", "xhigh"}
LEGACY_HARNESS_TO_ADAPTER = {"cld": "claude", "cod": "codex", "in-session": "in-session"}
LEGACY_ROLE_ALIASES = {
    "planner": "primary",
    "story-executor": "primary",
    "critiquer": "reviewer",
}
ROLE_CONFIG_FALLBACKS = {
    "primary": ("primary", "story-executor", "planner"),
    "reviewer": ("reviewer", "critiquer"),
}
STORY_ID_RE = re.compile(r"^S[1-9]\d*$")
AUDIT_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_-]+")
AGENTS_SCHEMA_PATH = schema_dir() / "agents.schema.json"
TRUSTED_RUNTIME_MODE = "trusted-local"
TRUSTED_RUNTIME_NOTE = (
    "trusted-local runtime: Woof does not constrain dispatched agents at runtime; "
    "commit safety is enforced through deterministic checks, reviewer critique, "
    "human gates, transaction manifests, and commit decisions"
)


@dataclass(frozen=True)
class RoleRoute:
    requested_role: str
    config_role: str
    adapter: str
    config: dict[str, Any]


class DispatchConfigError(ValueError):
    """Raised when a dispatch role exists but cannot be mapped to a public adapter."""


def trusted_runtime_policy() -> dict[str, Any]:
    """Return the operator-facing runtime policy summary for dispatch surfaces."""
    return {
        "mode": TRUSTED_RUNTIME_MODE,
        "woof_runtime_constraints": [],
        "cli_permission_mode": "broad public CLI permission flags",
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


def parse_claude_output(stdout: str) -> tuple[dict, str | None]:
    """Extract token usage and session_id from ``claude -p --output-format json``."""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return {}, None
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}, None
    usage = data.get("usage") or {}
    tokens = {
        "tokens_in": int(usage.get("input_tokens", 0) or 0),
        "tokens_out": int(usage.get("output_tokens", 0) or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
    }
    return tokens, data.get("session_id")


def parse_codex_output(stdout: str) -> tuple[dict, str | None]:
    """Sum token usage across ``turn.completed`` events from ``codex exec --json``."""
    tokens_in = tokens_out = cache_read = 0
    saw_turn = False
    thread_id: str | None = None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = evt.get("type")
        if kind == "thread.started":
            thread_id = evt.get("thread_id")
        elif kind == "turn.completed":
            saw_turn = True
            usage = evt.get("usage") or {}
            tokens_in += int(usage.get("input_tokens", 0) or 0)
            tokens_out += int(usage.get("output_tokens", 0) or 0)
            tokens_out += int(usage.get("reasoning_output_tokens", 0) or 0)
            cache_read += int(usage.get("cached_input_tokens", 0) or 0)
    if not saw_turn:
        return {}, thread_id
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read_tokens": cache_read,
    }, thread_id


def count_codex_command_executions(stdout: str) -> int:
    """Count completed shell-command tool calls in ``codex exec --json`` output."""
    count = 0
    seen_ids: set[str] = set()
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") != "item.completed":
            continue
        item = evt.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
        count += 1
    return count


def artefacts_byte_count(repo_root: Path, artefacts_loaded: list[str]) -> int:
    """Return the current byte size of explicitly audited repo artefacts."""
    total = 0
    for relpath in artefacts_loaded:
        total += (repo_root / relpath).stat().st_size
    return total


def _adapter_from_role_config(role_name: str, role: dict[str, Any]) -> str:
    raw = role.get("adapter", role.get("harness"))
    if raw is None:
        raise DispatchConfigError(
            f"role '{role_name}' must declare adapter='claude|codex' or a legacy harness to migrate"
        )
    adapter = LEGACY_HARNESS_TO_ADAPTER.get(str(raw), str(raw))
    if adapter not in {*ADAPTERS, "in-session"}:
        raise DispatchConfigError(
            f"role '{role_name}' resolves unsupported adapter {raw!r}; "
            "expected 'claude', 'codex', or 'in-session'"
        )
    return adapter


def resolve_role_route(roles: dict[str, Any], requested_role: str) -> RoleRoute:
    """Resolve a semantic role to a configured public adapter route."""
    candidates = ROLE_CONFIG_FALLBACKS.get(requested_role, (requested_role,))
    if requested_role in LEGACY_ROLE_ALIASES:
        candidates = (requested_role, LEGACY_ROLE_ALIASES[requested_role])

    for config_role in candidates:
        role = roles.get(config_role)
        if isinstance(role, dict):
            return RoleRoute(
                requested_role=requested_role,
                config_role=config_role,
                adapter=_adapter_from_role_config(config_role, role),
                config=role,
            )

    if requested_role in LEGACY_ROLE_ALIASES:
        semantic_role = LEGACY_ROLE_ALIASES[requested_role]
        raise DispatchConfigError(
            f"legacy role '{requested_role}' is not declared; configure "
            f"[roles.{semantic_role}] or keep a legacy [roles.{requested_role}] entry"
        )

    if requested_role in ROLE_CONFIG_FALLBACKS:
        fallbacks = ", ".join(f"[roles.{name}]" for name in candidates)
        raise DispatchConfigError(
            f"role '{requested_role}' not declared; expected one of {fallbacks}"
        )

    raise DispatchConfigError(f"role '{requested_role}' not declared")


def _role_effort(adapter: str, role: dict[str, Any]) -> str | None:
    effort = role.get("effort")
    if effort is None:
        return None
    effort = str(effort)
    if effort not in EFFORTS:
        raise DispatchConfigError(
            f"{adapter} effort {effort!r} is not supported; expected one of {sorted(EFFORTS)}"
        )
    if adapter == "codex" and effort not in CODEX_EFFORTS:
        raise DispatchConfigError(
            f"Codex effort {effort!r} is not supported; use low, medium, high, or xhigh"
        )
    return effort


def _mcp_names(role: dict[str, Any]) -> list[str]:
    return [str(name) for name in role.get("mcp") or []]


def _validate_portable_mcp_value(value: str, *, context: str) -> None:
    if (
        value.startswith("/")
        or value.startswith("~/.dotfiles")
        or "/.dotfiles" in value
        or "/agent-sync" in value
    ):
        raise DispatchConfigError(
            f"MCP server {context} uses host-specific path {value!r}; "
            "use a PATH command, project-relative path, or portable home-relative path"
        )


def _normalise_mcp_server(name: str, server: dict[str, Any]) -> dict[str, Any]:
    command = str(server["command"])
    if command in {"cld", "cod", "agent-sync"}:
        raise DispatchConfigError(
            f"MCP server {name!r} uses private/local command {command!r}; use a public command"
        )
    _validate_portable_mcp_value(command, context=f"{name}.command")

    rendered: dict[str, Any] = {"command": command}
    args = [str(arg) for arg in server.get("args") or []]
    for index, arg in enumerate(args):
        _validate_portable_mcp_value(arg, context=f"{name}.args[{index}]")
    if args:
        rendered["args"] = args

    env = {str(key): str(value) for key, value in (server.get("env") or {}).items()}
    for key, value in env.items():
        _validate_portable_mcp_value(value, context=f"{name}.env.{key}")
    if env:
        rendered["env"] = env

    cwd = server.get("cwd")
    if cwd is not None:
        cwd = str(cwd)
        _validate_portable_mcp_value(cwd, context=f"{name}.cwd")
        rendered["cwd"] = cwd
    return rendered


def _claude_mcp_config(role: dict[str, Any], mcp_servers: dict[str, Any] | None = None) -> str:
    mcp = role.get("mcp") or []
    if mcp and mcp_servers is None:
        raise DispatchConfigError(
            "role declares MCP servers but no top-level mcp_servers table was loaded"
        )
    rendered: dict[str, Any] = {}
    for name in _mcp_names(role):
        raw = (mcp_servers or {}).get(name)
        if not isinstance(raw, dict):
            raise DispatchConfigError(
                f"role references MCP server {name!r}, but [mcp_servers.{name}] is not declared"
            )
        rendered[name] = _normalise_mcp_server(name, raw)
    return json.dumps({"mcpServers": rendered}, separators=(",", ":"), sort_keys=True)


def build_argv(
    adapter: str,
    role: dict[str, Any],
    prompt: str,
    *,
    mcp_servers: dict[str, Any] | None = None,
) -> list[str]:
    """Construct a public claude/codex invocation argv from a role definition.

    The prompt is intentionally not appended to argv. ``cmd_dispatch`` sends it
    on stdin so large playbook-bundled prompts do not hit Linux MAX_ARG_STRLEN.
    """
    flags = [str(flag) for flag in role.get("flags") or []]
    model = role.get("model")
    effort = _role_effort(adapter, role)

    if adapter == "claude":
        argv = [
            "claude",
            "--dangerously-skip-permissions",
            "--strict-mcp-config",
            "--mcp-config",
            _claude_mcp_config(role, mcp_servers),
            "-p",
            "--output-format",
            "json",
        ]
        if model:
            argv += ["--model", str(model)]
        if effort:
            argv += ["--effort", effort]
        argv += flags
    elif adapter == "codex":
        if role.get("mcp"):
            raise DispatchConfigError(
                "Codex roles cannot declare MCP servers; MCP route config is Claude-only"
            )
        argv = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-s",
            "danger-full-access",
        ]
        if model:
            argv += ["--model", str(model)]
        if effort:
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        argv += flags
    else:
        raise DispatchConfigError(f"cannot dispatch adapter {adapter!r}")
    return argv


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
    return [*wrapped_argv, "<prompt:stdin>"]


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


def cmd_dispatch(args: argparse.Namespace) -> int:
    _ensure_ajv()

    repo_root = find_woof_root(Path.cwd().resolve())
    agents_path = repo_root / ".woof" / "agents.toml"
    if not agents_path.is_file():
        sys.stderr.write(f"woof: {agents_path} not found; cannot dispatch\n")
        return 2

    with agents_path.open("rb") as fh:
        agents = tomllib.load(fh)

    ok, output = _run_ajv(AGENTS_SCHEMA_PATH, json.dumps(agents).encode())
    if not ok:
        sys.stderr.write(f"woof: {agents_path}: schema invalid\n{output}\n")
        return 2

    roles = agents.get("roles") or {}
    try:
        route = resolve_role_route(roles, args.role)
    except DispatchConfigError as exc:
        sys.stderr.write(f"woof: {exc} in {agents_path}\n")
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

    if args.story is not None and not STORY_ID_RE.match(args.story):
        sys.stderr.write(f"woof: --story {args.story!r}: must match S<n> (n>=1)\n")
        return 2

    prompt = Path(args.prompt_file).read_text() if args.prompt_file else sys.stdin.read()
    if not prompt.strip():
        sys.stderr.write("woof: empty prompt\n")
        return 2

    timeout_min = int((agents.get("timeouts") or {}).get("default_minutes", 30))
    mcp_servers = agents.get("mcp_servers") or {}
    try:
        effort = _role_effort(route.adapter, route.config)
        mcp_names = _mcp_names(route.config)
        argv = build_argv(route.adapter, route.config, prompt, mcp_servers=mcp_servers)
        artefacts_loaded = normalise_artefacts_loaded(repo_root, args.artefacts_loaded)
        artefact_bytes = artefacts_byte_count(repo_root, artefacts_loaded)
    except DispatchConfigError as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2

    prompt_bytes = len(prompt.encode("utf-8"))

    if args.dry_run:
        wrapped = ["timeout", f"{timeout_min}m", *argv]
        payload = {
            "argv": wrapped,
            "prompt_transport": "stdin",
            "runtime_policy": trusted_runtime_policy(),
            "epic": args.epic,
            "story": args.story,
            "role": args.role,
            "config_role": route.config_role,
            "adapter": route.adapter,
            "harness": route.adapter,
            "model": route.config.get("model"),
            "effort": effort,
            "mcp": mcp_names,
            "flags": route.config.get("flags") or [],
            "timeout_min": timeout_min,
            "artefacts_loaded": artefacts_loaded,
            "prompt_bytes": prompt_bytes,
            "artefact_bytes": artefact_bytes,
        }
        if route.adapter == "claude":
            payload["mcp_config"] = _claude_mcp_config(route.config, mcp_servers)
        print(json.dumps(payload))
        return 0

    epic_dir = repo_root / ".woof" / "epics" / f"E{args.epic}"
    audit_dir = epic_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    base = reserve_audit_base(audit_dir, route.adapter, args.role, started_at, prompt)
    output_file = base.with_suffix(".output")
    stderr_file = base.with_suffix(".stderr")
    meta_file = base.with_suffix(".meta")

    dispatch_jsonl = epic_dir / "dispatch.jsonl"
    wrapped_argv = ["timeout", f"{timeout_min}m", *argv]
    event_argv = audit_argv(wrapped_argv)

    proc = subprocess.Popen(
        wrapped_argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    spawned_event: dict = {
        "event": "subprocess_spawned",
        "at": iso_utc(started_at),
        "epic_id": args.epic,
        "role": args.role,
        "harness": route.adapter,
        "adapter": route.adapter,
        "pid": proc.pid,
        "mcp": mcp_names,
        "argv": event_argv,
        "prompt_transport": "stdin",
        "runtime_policy": trusted_runtime_policy(),
        "artefacts_loaded": artefacts_loaded,
        "prompt_bytes": prompt_bytes,
        "artefact_bytes": artefact_bytes,
    }
    if route.config_role != args.role:
        spawned_event["config_role"] = route.config_role
    if args.story:
        spawned_event["story_id"] = args.story
    if route.config.get("model"):
        spawned_event["model"] = route.config["model"]
    if effort:
        spawned_event["effort"] = effort
    append_jsonl(dispatch_jsonl, spawned_event)

    try:
        stdout, stderr = proc.communicate(prompt)
    except KeyboardInterrupt:
        proc.terminate()
        stdout, stderr = proc.communicate()
        ended_at = datetime.now(UTC)
        append_jsonl(
            dispatch_jsonl,
            {
                "event": "subprocess_killed",
                "at": iso_utc(ended_at),
                "epic_id": args.epic,
                "pid": proc.pid,
                "signal": "SIGINT",
                "reason": "manual_cancel",
            },
        )
        output_file.write_text(stdout or "", encoding="utf-8")
        stderr_file.write_text(stderr or "", encoding="utf-8")
        return 130

    ended_at = datetime.now(UTC)
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    output_file.write_text(stdout, encoding="utf-8")
    stderr_file.write_text(stderr, encoding="utf-8")
    output_bytes = len(stdout.encode("utf-8"))
    stderr_bytes = len(stderr.encode("utf-8"))

    if route.adapter == "claude":
        tokens, session_id = parse_claude_output(stdout)
        thread_id = None
        command_count = 0
    else:
        tokens, thread_id = parse_codex_output(stdout)
        session_id = None
        command_count = count_codex_command_executions(stdout)

    timed_out = proc.returncode == 124
    if timed_out:
        append_jsonl(
            dispatch_jsonl,
            {
                "event": "subprocess_killed",
                "at": iso_utc(ended_at),
                "epic_id": args.epic,
                "pid": proc.pid,
                "signal": "SIGTERM",
                "reason": "timeout",
            },
        )

    returned: dict = {
        "event": "subprocess_returned",
        "at": iso_utc(ended_at),
        "epic_id": args.epic,
        "role": args.role,
        "harness": route.adapter,
        "adapter": route.adapter,
        "pid": proc.pid,
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
        "mcp": mcp_names,
        "argv": event_argv,
        "prompt_transport": "stdin",
        "runtime_policy": trusted_runtime_policy(),
        "artefacts_loaded": artefacts_loaded,
        "prompt_bytes": prompt_bytes,
        "artefact_bytes": artefact_bytes,
        "output_bytes": output_bytes,
        "stderr_bytes": stderr_bytes,
    }
    if route.config_role != args.role:
        returned["config_role"] = route.config_role
    if args.story:
        returned["story_id"] = args.story
    if route.config.get("model"):
        returned["model"] = route.config["model"]
    if effort:
        returned["effort"] = effort
    if tokens:
        returned.update(tokens)
    if session_id:
        returned["cc_session_id"] = session_id
        returned["claude_transcript_path"] = claude_transcript_path(repo_root, session_id)
    if route.adapter == "codex":
        returned["codex_audit_path"] = str(base.relative_to(repo_root))
        returned["command_count"] = command_count
    append_jsonl(dispatch_jsonl, returned)

    meta = {
        "harness": route.adapter,
        "adapter": route.adapter,
        "role": args.role,
        "config_role": route.config_role,
        "epic_id": args.epic,
        "story_id": args.story,
        "model": route.config.get("model"),
        "effort": effort,
        "mcp": mcp_names,
        "flags": route.config.get("flags") or [],
        "argv": event_argv,
        "prompt_transport": "stdin",
        "runtime_policy": trusted_runtime_policy(),
        "artefacts_loaded": artefacts_loaded,
        "pid": proc.pid,
        "started_at": iso_utc(started_at),
        "ended_at": iso_utc(ended_at),
        "duration_ms": duration_ms,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "prompt_bytes": prompt_bytes,
        "artefact_bytes": artefact_bytes,
        "output_bytes": output_bytes,
        "stderr_bytes": stderr_bytes,
        "tokens": tokens,
    }
    if route.adapter == "claude":
        meta["mcp_config"] = _claude_mcp_config(route.config, mcp_servers)
    else:
        meta["command_count"] = command_count
    if session_id:
        meta["cc_session_id"] = session_id
        meta["claude_transcript_path"] = claude_transcript_path(repo_root, session_id)
    if thread_id:
        meta["codex_thread_id"] = thread_id
    meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return proc.returncode
