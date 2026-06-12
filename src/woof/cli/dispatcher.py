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
import signal
import subprocess
import sys
import tempfile
import threading
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from woof.lib.audit_config import load_audit_config
from woof.lib.supervise import ExitType, supervise
from woof.paths import schema_dir

ADAPTERS = {"claude", "codex"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
CODEX_EFFORTS = {"low", "medium", "high", "xhigh"}
DEFAULT_TIMEOUT_MINUTES = 30
DEFAULT_IDLE_SECONDS = 600.0
DEFAULT_COMPLETION_GRACE_SECONDS = 60.0
DEFAULT_COMPLETION_TAIL_CAP_SECONDS = 120.0
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
MODEL_PROFILE_ENV = "WOOF_MODEL_PROFILE"
NODE_GROUPS = frozenset({"discovery", "definition", "planning", "execution"})
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
    if not isinstance(data, dict):
        return {}, None
    usage = data.get("usage") or {}
    tokens = {
        "tokens_in": int(usage.get("input_tokens", 0) or 0),
        "tokens_out": int(usage.get("output_tokens", 0) or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
    }
    return tokens, data.get("session_id")


def is_claude_terminal_line(line: str) -> bool:
    """Return true for Claude's final ``--output-format json`` result line."""
    try:
        data = json.loads(line.strip())
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("type") == "result"


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
        if not isinstance(evt, dict):
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


def is_codex_terminal_line(line: str) -> bool:
    """Return true for Codex's terminal ``turn.completed`` JSON event."""
    try:
        data = json.loads(line.strip())
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("type") == "turn.completed"


def terminal_detector(adapter: str):
    if adapter == "claude":
        return is_claude_terminal_line
    if adapter == "codex":
        return is_codex_terminal_line
    raise DispatchConfigError(f"cannot detect terminal marker for adapter {adapter!r}")


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
        if not isinstance(evt, dict):
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


def _canonical_role(requested_role: str) -> str:
    """Return the canonical primary/reviewer name for a requested or legacy role."""
    return LEGACY_ROLE_ALIASES.get(requested_role, requested_role)


def resolve_role_route(
    roles: dict[str, Any],
    requested_role: str,
    *,
    route_key: str | None = None,
    routes: dict[str, Any] | None = None,
) -> RoleRoute:
    """Resolve a semantic role to a configured public adapter route.

    When ``route_key`` names a node group with a declared override in
    ``routes.<group>.<role>``, that override wins over the base ``[roles.*]``
    default. Otherwise the base fallback chain resolves the route. The
    ``route_key`` is recorded on the returned route either way, including on
    fallback, so dispatch audit can attribute the route to its node group.
    Group routes use only the canonical ``primary``/``reviewer`` keys.
    """
    if route_key and route_key in NODE_GROUPS and isinstance(routes, dict):
        group = routes.get(route_key)
        if isinstance(group, dict):
            canonical = _canonical_role(requested_role)
            override = group.get(canonical)
            if isinstance(override, dict):
                return RoleRoute(
                    requested_role=requested_role,
                    config_role=canonical,
                    adapter=_adapter_from_role_config(canonical, override),
                    config=override,
                    route_key=route_key,
                )

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
                route_key=route_key,
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


def selected_model_profile(
    agents: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return the selected model profile from env or agents.toml."""

    env_map = os.environ if env is None else env
    env_value = env_map.get(MODEL_PROFILE_ENV)
    if env_value is not None and env_value.strip():
        return env_value.strip()

    configured = agents.get("model_profile")
    if configured is None:
        return None
    configured = str(configured).strip()
    return configured or None


def resolve_agent_route(
    agents: dict[str, Any],
    requested_role: str,
    *,
    model_profile: str | None = None,
    route_key: str | None = None,
) -> RoleRoute:
    """Resolve a role route after applying node-group overlays and the model profile.

    Resolution order, most specific first:

    1. ``[routes.<group>.<role>]`` adapter override for the dispatch ``route_key``.
    2. ``[roles.<role>]`` base route default.

    A selected ``[model_profiles.<profile>]`` then overlays model/effort/flags. Its
    ``routes.<group>.<role>`` entry wins over its ``roles.<role>`` entry for the same
    ``route_key``. The resolved adapter is read from the merged config so effort
    validity is checked against the adapter that will actually run.
    """

    roles = agents.get("roles") or {}
    if not isinstance(roles, dict):
        raise DispatchConfigError("roles table is not an object")

    routes = agents.get("routes") or {}
    if not isinstance(routes, dict):
        raise DispatchConfigError("routes table is not an object")

    route = resolve_role_route(roles, requested_role, route_key=route_key, routes=routes)
    profile_name = model_profile if model_profile is not None else selected_model_profile(agents)
    if not profile_name:
        return route

    profiles = agents.get("model_profiles") or {}
    if not isinstance(profiles, dict):
        raise DispatchConfigError("model_profiles table is not an object")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise DispatchConfigError(
            f"model profile {profile_name!r} is not declared in .woof/agents.toml"
        )

    profile_role_name, profile_role = _resolve_profile_overlay(
        profile, route, profile_name=profile_name
    )
    if profile_role is None:
        return RoleRoute(
            requested_role=route.requested_role,
            config_role=route.config_role,
            adapter=route.adapter,
            config=route.config,
            model_profile=profile_name,
            profile_role=None,
            route_key=route.route_key,
        )

    config = {**route.config, **profile_role}
    adapter = _adapter_from_role_config(route.config_role, config)
    return RoleRoute(
        requested_role=route.requested_role,
        config_role=route.config_role,
        adapter=adapter,
        config=config,
        model_profile=profile_name,
        profile_role=profile_role_name,
        route_key=route.route_key,
    )


def _resolve_profile_overlay(
    profile: dict[str, Any],
    route: RoleRoute,
    *,
    profile_name: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Select the model-profile overlay for a route, group entry first.

    Checks ``profile.routes.<route_key>.<role>`` before the base
    ``profile.roles.<role>`` table. Returns ``(name, config)`` for the most
    specific declared overlay, or ``(None, None)`` when the profile declares no
    entry for this route.
    """
    route_key = route.route_key
    if route_key and route_key in NODE_GROUPS:
        profile_routes = profile.get("routes") or {}
        if not isinstance(profile_routes, dict):
            raise DispatchConfigError(
                f"model profile {profile_name!r} routes table is not an object"
            )
        group = profile_routes.get(route_key)
        if isinstance(group, dict):
            canonical = _canonical_role(route.requested_role)
            override = group.get(canonical)
            if override is not None:
                if not isinstance(override, dict):
                    raise DispatchConfigError(
                        f"model profile {profile_name!r} route "
                        f"{route_key}.{canonical} is not an object"
                    )
                return canonical, override

    profile_roles = profile.get("roles") or {}
    if not isinstance(profile_roles, dict):
        raise DispatchConfigError(f"model profile {profile_name!r} roles table is not an object")
    profile_role_name = _select_profile_role_name(profile_roles, route)
    if profile_role_name is None:
        return None, None
    profile_role = profile_roles.get(profile_role_name)
    if not isinstance(profile_role, dict):
        raise DispatchConfigError(
            f"model profile {profile_name!r} role {profile_role_name!r} is not an object"
        )
    return profile_role_name, profile_role


def _select_profile_role_name(profile_roles: dict[str, Any], route: RoleRoute) -> str | None:
    candidates = [route.requested_role, route.config_role]
    alias = LEGACY_ROLE_ALIASES.get(route.requested_role)
    if alias:
        candidates.append(alias)
    for candidate in candidates:
        if candidate in profile_roles:
            return candidate
    return None


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


def dispatch_return_code(exit_type: ExitType, exit_code: int | None) -> int:
    if exit_type in {ExitType.CLEAN, ExitType.COMPLETED_LINGERING}:
        return 0
    if exit_type is ExitType.NONZERO:
        return exit_code if exit_code is not None else 1
    if exit_type is ExitType.OPERATOR_CANCEL:
        return 130
    return 124


def dispatch_kill_reason(exit_type: ExitType) -> str:
    if exit_type is ExitType.OPERATOR_CANCEL:
        return "manual_cancel"
    return exit_type.value


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

    try:
        route = resolve_agent_route(agents, args.role, route_key=args.route_key)
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

    try:
        timeouts = dispatch_timeouts(agents)
        audit_config = load_audit_config(agents)
    except (DispatchConfigError, TypeError, ValueError) as exc:
        sys.stderr.write(f"woof: {exc} in {agents_path}\n")
        return 2

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
        payload = {
            "argv": argv,
            "prompt_transport": "stdin",
            "runtime_policy": trusted_runtime_policy(),
            "epic": args.epic,
            "story": args.story,
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
    event_argv = audit_argv(argv)

    spawned_pid: int | None = None

    def on_spawn(pid: int) -> None:
        nonlocal spawned_pid
        spawned_pid = pid
        spawned_event: dict = {
            "event": "subprocess_spawned",
            "at": iso_utc(started_at),
            "epic_id": args.epic,
            "role": args.role,
            "harness": route.adapter,
            "adapter": route.adapter,
            "pid": pid,
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
        if route.model_profile:
            spawned_event["model_profile"] = route.model_profile
        if route.profile_role:
            spawned_event["profile_role"] = route.profile_role
        if route.route_key:
            spawned_event["route_key"] = route.route_key
        if args.story:
            spawned_event["story_id"] = args.story
        if route.config.get("model"):
            spawned_event["model"] = route.config["model"]
        if effort:
            spawned_event["effort"] = effort
        append_jsonl(dispatch_jsonl, spawned_event)

    cancel = threading.Event()
    previous_sigint = signal.getsignal(signal.SIGINT)

    def on_sigint(signum: int, frame: object) -> None:
        cancel.set()

    signal.signal(signal.SIGINT, on_sigint)
    try:
        result = supervise(
            argv,
            stdin=prompt,
            is_terminal=terminal_detector(route.adapter),
            idle_seconds=timeouts.idle_seconds,
            wallclock_seconds=timeouts.wallclock_seconds,
            completion_grace_seconds=timeouts.completion_grace_seconds,
            completion_tail_cap_seconds=timeouts.completion_tail_cap_seconds,
            max_captured_bytes=audit_config.max_bytes,
            stdout_path=output_file,
            stderr_path=stderr_file,
            on_spawn=on_spawn,
            cancel=cancel,
        )
    except KeyboardInterrupt:
        ended_at = datetime.now(UTC)
        if spawned_pid is not None:
            append_jsonl(
                dispatch_jsonl,
                {
                    "event": "subprocess_killed",
                    "at": iso_utc(ended_at),
                    "epic_id": args.epic,
                    "pid": spawned_pid,
                    "signal": "SIGINT",
                    "reason": dispatch_kill_reason(ExitType.OPERATOR_CANCEL),
                    "exit_type": ExitType.OPERATOR_CANCEL.value,
                },
            )
        return 130
    finally:
        signal.signal(signal.SIGINT, previous_sigint)

    ended_at = datetime.now(UTC)
    stdout = result.stdout
    stderr = result.stderr
    duration_ms = result.duration_ms
    output_bytes = (
        output_file.stat().st_size if output_file.exists() else len(stdout.encode("utf-8"))
    )
    stderr_bytes = (
        stderr_file.stat().st_size if stderr_file.exists() else len(stderr.encode("utf-8"))
    )

    if route.adapter == "claude":
        tokens, session_id = parse_claude_output(stdout)
        thread_id = None
        command_count = 0
    else:
        tokens, thread_id = parse_codex_output(stdout)
        session_id = None
        command_count = count_codex_command_executions(stdout)

    timed_out = result.exit_type in {ExitType.IDLE_KILL, ExitType.WALLCLOCK_TIMEOUT}
    reported_exit_code = dispatch_return_code(result.exit_type, result.exit_code)
    if result.exit_type in {
        ExitType.IDLE_KILL,
        ExitType.WALLCLOCK_TIMEOUT,
        ExitType.COMPLETED_LINGERING,
        ExitType.OPERATOR_CANCEL,
    }:
        append_jsonl(
            dispatch_jsonl,
            {
                "event": "subprocess_killed",
                "at": iso_utc(ended_at),
                "epic_id": args.epic,
                "pid": result.pid,
                "signal": result.signalled or "SIGTERM",
                "reason": dispatch_kill_reason(result.exit_type),
                "exit_type": result.exit_type.value,
            },
        )

    returned: dict = {
        "event": "subprocess_returned",
        "at": iso_utc(ended_at),
        "epic_id": args.epic,
        "role": args.role,
        "harness": route.adapter,
        "adapter": route.adapter,
        "pid": result.pid,
        "exit_type": result.exit_type.value,
        "exit_code": reported_exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "terminal_seen": result.terminal_seen,
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
    if route.model_profile:
        returned["model_profile"] = route.model_profile
    if route.profile_role:
        returned["profile_role"] = route.profile_role
    if route.route_key:
        returned["route_key"] = route.route_key
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
        "model_profile": route.model_profile,
        "profile_role": route.profile_role,
        "route_key": route.route_key,
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
        "pid": result.pid,
        "started_at": iso_utc(started_at),
        "ended_at": iso_utc(ended_at),
        "duration_ms": duration_ms,
        "exit_type": result.exit_type.value,
        "exit_code": reported_exit_code,
        "timed_out": timed_out,
        "terminal_seen": result.terminal_seen,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "signal": result.signalled,
        "timeouts": timeouts.as_payload(),
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

    return reported_exit_code
