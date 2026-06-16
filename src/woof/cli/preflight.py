"""Preflight checks for Woof consumer projects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from woof.cli.commands.observe import build_operator_state_summary
from woof.cli.dispatcher import (
    NODE_GROUPS,
    TRUSTED_RUNTIME_MODE,
    TRUSTED_RUNTIME_NOTE,
    DispatchConfigError,
    _claude_mcp_config,
    _mcp_names,
    _role_effort,
    build_argv,
    resolve_agent_route,
)
from woof.cli.init import AGENTS_TEMPLATE
from woof.cli.main import (
    SCHEMAS,
    load_payload,
    run_ajv,
)
from woof.lib.audit import scan_text_for_secrets
from woof.paths import schema_dir, tool_root
from woof.trackers.github import GITHUB_RATE_LIMIT_SAFETY_MARGIN, github_core_remaining

CONFIG_SCHEMAS = {
    "prerequisites.toml": "prerequisites",
    "agents.toml": "agents",
    "quality-gates.toml": "quality-gates",
    "test-markers.toml": "test-markers",
    "docs-paths.toml": "docs-paths",
}

PREREQUISITES_TEMPLATE = """\
# Woof project prerequisites. Verified by `woof preflight`.
# Replace every <replace> placeholder before invoking `woof wf`.

[infra]
just = "1.0+"
git = "2.30+"
gh = "2.0+"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

# Issue tracker for epic-level contracts. The default GitHub adapter keeps
# each epic in a GitHub issue and requires `repo`. Use `woof init --tracker
# local` to scaffold a no-remote setup for repositories without a hosted
# tracker.
[tracker]
kind = "github"
repo = "<replace>/<replace>"

# Cartography contract (ADR-004), enforced below. Author the design docs
# (.woof/codebase/TARGET-ARCHITECTURE.md and PRINCIPLES.md) through the /woof
# setup flow, then run the /woof map-codebase flow before preflight passes.
[cartography]
staleness_floor_hours = 168
summary_min_chars = 200
"""

CACHE_VERSION = 4  # v4: adds cartography ctags floor check
FLOOR_CACHE_TTL = timedelta(hours=24)
RUNTIME_CACHE_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class PreflightFinding:
    id: str
    label: str
    ok: bool
    detail: str
    required: str | None = None
    install: str | None = None
    notes: list[str] = field(default_factory=list)
    warn: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
        }
        if self.warn:
            payload["warn"] = True
        if self.required is not None:
            payload["required"] = self.required
        if self.install is not None:
            payload["install"] = self.install
        if self.notes:
            payload["notes"] = self.notes
        return payload


@dataclass(frozen=True)
class PreflightResult:
    repo_root: Path
    findings: list[PreflightFinding]
    operator_state: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(finding.ok for finding in self.findings)

    @property
    def failed(self) -> list[PreflightFinding]:
        return [finding for finding in self.findings if not finding.ok]

    @property
    def warnings(self) -> list[PreflightFinding]:
        return [finding for finding in self.findings if finding.warn and finding.ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "ok": self.ok,
            "total": len(self.findings),
            "failed": len(self.failed),
            "warnings": len(self.warnings),
            "findings": [finding.as_dict() for finding in self.findings],
            "operator_state": self.operator_state,
        }


def cmd_preflight(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_root(args.project_root)
    result = run_preflight(repo_root, force=args.force)
    if args.format == "json":
        print(json.dumps(result.as_dict(), indent=2))
    else:
        _print_text_result(result)
    return 0 if result.ok else 1


def run_preflight(repo_root: Path, *, force: bool = False) -> PreflightResult:
    operator_state = build_operator_state_summary(repo_root)
    prereq_path = repo_root / ".woof" / "prerequisites.toml"
    if not prereq_path.is_file():
        return PreflightResult(
            repo_root=repo_root,
            operator_state=operator_state,
            findings=[
                PreflightFinding(
                    id="config.prerequisites",
                    label="prerequisites.toml",
                    ok=False,
                    detail=f"{prereq_path} not found",
                    install=f"Create {prereq_path} from this template:\n{PREREQUISITES_TEMPLATE}",
                )
            ],
        )

    findings: list[PreflightFinding] = []
    prereq = _load_toml(prereq_path)
    if isinstance(prereq, dict):
        cache_key = _preflight_cache_key(repo_root, prereq)
        findings.extend(
            _cached_findings(
                repo_root / ".woof" / ".preflight-floor",
                cache_key=cache_key,
                ttl=FLOOR_CACHE_TTL,
                force=force,
                producer=lambda: _run_floor_checks(repo_root, prereq),
            )
        )
        findings.extend(
            _cached_findings(
                repo_root / ".woof" / ".preflight-runtime",
                cache_key=cache_key,
                ttl=RUNTIME_CACHE_TTL,
                force=force,
                producer=lambda: _run_runtime_checks(repo_root, prereq),
            )
        )
    else:
        findings.append(
            PreflightFinding(
                id="config.prerequisites",
                label="prerequisites.toml",
                ok=False,
                detail=prereq,
            )
        )
    # Uncached: the floor/runtime cache keys do not track cartography doc content,
    # so a cached pass could mask a secret a mapper just wrote. A security gate
    # must re-read the docs every run.
    findings.extend(_check_cartography_secrets(repo_root))
    return PreflightResult(repo_root=repo_root, findings=findings, operator_state=operator_state)


def _resolve_repo_root(project_root: str | None) -> Path:
    if project_root:
        root = Path(project_root).resolve()
        if not (root / ".woof").is_dir():
            sys.stderr.write(f"woof: {root}/.woof not found; not a woof project\n")
            sys.exit(2)
        return root

    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".woof").is_dir():
            return candidate
    sys.stderr.write(f"woof: no .woof/ directory found at or above {current}; not a woof project\n")
    sys.exit(2)


def _load_toml(path: Path) -> dict[str, Any] | str:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return f"{path}: TOML parse error: {exc}"


def _run_floor_checks(repo_root: Path, prereq: dict[str, Any]) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    findings.extend(_check_woof_install())
    findings.extend(_check_config_schemas(repo_root))
    findings.extend(_check_declared_binaries(prereq))
    findings.extend(_check_role_routes(repo_root))
    findings.extend(_check_ajv_formats(prereq))
    findings.extend(_check_language_tools(prereq))
    findings.extend(_check_tree_sitter(prereq))
    findings.extend(_check_quality_gate_commands(repo_root))
    findings.extend(_check_cartography(repo_root, prereq))
    findings.extend(_check_host_prerequisites(repo_root, prereq))
    findings.extend(_check_server_prerequisites(repo_root, prereq))
    return findings


def _run_runtime_checks(repo_root: Path, prereq: dict[str, Any]) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    findings.extend(_check_tracker(prereq))
    findings.extend(_check_adapter_auth_markers(repo_root))
    return findings


def _cached_findings(
    cache_path: Path,
    *,
    cache_key: str,
    ttl: timedelta,
    force: bool,
    producer: Callable[[], list[PreflightFinding]],
) -> list[PreflightFinding]:
    if not force:
        cached = _read_preflight_cache(cache_path, cache_key=cache_key, ttl=ttl)
        if cached is not None:
            return cached

    findings = producer()
    if all(finding.ok for finding in findings):
        _write_preflight_cache(cache_path, cache_key=cache_key, findings=findings)
    else:
        cache_path.unlink(missing_ok=True)
    return findings


def _read_preflight_cache(
    cache_path: Path,
    *,
    cache_key: str,
    ttl: timedelta,
) -> list[PreflightFinding] | None:
    try:
        payload = json.loads(cache_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if payload.get("version") != CACHE_VERSION or payload.get("key") != cache_key:
        return None
    verified_at = _parse_cache_time(payload.get("verified_at"))
    if verified_at is None or _utc_now() - verified_at >= ttl:
        return None

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return None
    try:
        return [_finding_from_dict(finding) for finding in raw_findings]
    except (KeyError, TypeError, ValueError):
        return None


def _write_preflight_cache(
    cache_path: Path,
    *,
    cache_key: str,
    findings: list[PreflightFinding],
) -> None:
    payload = {
        "version": CACHE_VERSION,
        "key": cache_key,
        "verified_at": _utc_now().isoformat(),
        "findings": [finding.as_dict() for finding in findings],
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_name(f"{cache_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    tmp_path.replace(cache_path)


def _parse_cache_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _finding_from_dict(payload: dict[str, Any]) -> PreflightFinding:
    return PreflightFinding(
        id=str(payload["id"]),
        label=str(payload["label"]),
        ok=bool(payload["ok"]),
        detail=str(payload["detail"]),
        required=str(payload["required"]) if payload.get("required") is not None else None,
        install=str(payload["install"]) if payload.get("install") is not None else None,
        notes=[str(note) for note in payload.get("notes") or []],
        warn=bool(payload.get("warn", False)),
    )


def _preflight_cache_key(repo_root: Path, prereq: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    for path in _cache_input_paths(repo_root, prereq):
        digest.update(str(path).encode())
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            digest.update(f"ERROR:{exc}".encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _cache_input_paths(repo_root: Path, prereq: dict[str, Any]) -> list[Path]:
    woof_dir = repo_root / ".woof"
    paths = [
        woof_dir / filename
        for filename in CONFIG_SCHEMAS
        if (woof_dir / filename).is_file() or filename == "prerequisites.toml"
    ]
    languages = set((prereq.get("lsp") or {}).get("languages") or [])
    languages.update(
        ((prereq.get("indexing") or {}).get("tree-sitter") or {}).get("grammars") or []
    )
    paths.extend(_language_registry_path(str(language)) for language in sorted(languages))
    return paths


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _check_woof_install() -> list[PreflightFinding]:
    root = tool_root()
    required = [
        root / "schemas" / "prerequisites.schema.json",
        root / "schemas" / "agents.schema.json",
        root / "languages",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        return [
            PreflightFinding(
                id="woof.install",
                label="Woof installation",
                ok=False,
                detail="missing Woof tool asset(s): " + ", ".join(str(path) for path in missing),
                install="Install Woof with bundled schemas/ and languages/ assets.",
            )
        ]
    return [
        PreflightFinding(
            id="woof.install",
            label="Woof installation",
            ok=True,
            detail=f"tool assets found at {root}",
        )
    ]


def _check_config_schemas(repo_root: Path) -> list[PreflightFinding]:
    if shutil.which("ajv") is None:
        return [
            PreflightFinding(
                id="config.schemas",
                label="consumer config schemas",
                ok=False,
                detail="ajv-cli not found; cannot validate .woof/*.toml schemas",
                install="volta install ajv-cli ajv-formats",
            )
        ]

    findings: list[PreflightFinding] = []
    for filename, schema in CONFIG_SCHEMAS.items():
        path = repo_root / ".woof" / filename
        if filename != "prerequisites.toml" and not path.is_file():
            continue
        if not path.is_file():
            findings.append(
                PreflightFinding(
                    id=f"config.{schema}",
                    label=filename,
                    ok=False,
                    detail=f"{path} not found",
                )
            )
            continue
        try:
            payload = load_payload(path, schema)
        except (ValueError, tomllib.TOMLDecodeError) as exc:
            findings.append(
                PreflightFinding(
                    id=f"config.{schema}",
                    label=filename,
                    ok=False,
                    detail=f"parse error: {exc}",
                )
            )
            continue
        ok, output = run_ajv(schema_dir() / SCHEMAS[schema], json.dumps(payload).encode())
        findings.append(
            PreflightFinding(
                id=f"config.{schema}",
                label=filename,
                ok=ok,
                detail="schema valid" if ok else output,
            )
        )
    return findings


def _check_role_routes(repo_root: Path) -> list[PreflightFinding]:
    agents_path = repo_root / ".woof" / "agents.toml"
    if not agents_path.is_file():
        return [
            PreflightFinding(
                id="agents.config",
                label="agents.toml",
                ok=False,
                detail=f"{agents_path} not found; cannot verify primary/reviewer routes",
                install=_agents_template(),
            )
        ]

    loaded = _load_toml(agents_path)
    if not isinstance(loaded, dict):
        return [
            PreflightFinding(
                id="agents.config",
                label="agents.toml",
                ok=False,
                detail=loaded,
            )
        ]

    findings: list[PreflightFinding] = []
    mcp_servers = loaded.get("mcp_servers") or {}
    for role_name in ("primary", "reviewer"):
        findings.extend(_check_dispatch_role_route(role_name, loaded, mcp_servers, repo_root))
    for group in sorted(NODE_GROUPS):
        for role_name in ("primary", "reviewer"):
            findings.extend(
                _check_dispatch_group_route(group, role_name, loaded, mcp_servers, repo_root)
            )
    return findings


def _check_dispatch_role_route(
    role_name: str,
    agents: dict[str, Any],
    mcp_servers: dict[str, Any],
    repo_root: Path,
) -> list[PreflightFinding]:
    label = f"{role_name} route"
    try:
        route = resolve_agent_route(agents, role_name)
    except DispatchConfigError as exc:
        return [
            PreflightFinding(
                id=f"agents.{role_name}.route",
                label=label,
                ok=False,
                detail=str(exc),
            )
        ]

    errors: list[str] = []
    if route.adapter == "in-session":
        errors.append("dispatchable role resolves to in-session")
    elif shutil.which(route.adapter) is None:
        errors.append(f"{route.adapter} not found on PATH")

    model = route.config.get("model")
    if not model:
        errors.append("model is not declared")

    try:
        effort = _role_effort(route.adapter, route.config)
    except DispatchConfigError as exc:
        effort = None
        errors.append(str(exc))
    if effort is None:
        errors.append("effort is not declared")

    try:
        build_argv(route.adapter, route.config, "preflight route probe", mcp_servers=mcp_servers)
    except DispatchConfigError as exc:
        errors.append(str(exc))

    findings = [
        PreflightFinding(
            id=f"agents.{role_name}.route",
            label=label,
            ok=not errors,
            detail=(
                f"[roles.{route.config_role}] resolves adapter={route.adapter}, "
                f"model={model}, effort={effort}, "
                f"profile={route.model_profile or '-'}, runtime={TRUSTED_RUNTIME_MODE}"
                if not errors
                else "; ".join(errors)
            ),
            required="explicit adapter, model, effort, and runtime-mode disclosure",
            notes=[TRUSTED_RUNTIME_NOTE] if not errors else [],
        )
    ]

    if route.adapter == "claude":
        findings.extend(_check_claude_mcp_config(role_name, route.config, mcp_servers, repo_root))
    return findings


def _check_dispatch_group_route(
    group: str,
    role_name: str,
    agents: dict[str, Any],
    mcp_servers: dict[str, Any],
    repo_root: Path,
) -> list[PreflightFinding]:
    label = f"{group}/{role_name} route"
    try:
        route = resolve_agent_route(agents, role_name, route_key=group)
    except DispatchConfigError as exc:
        return [
            PreflightFinding(
                id=f"agents.{group}.{role_name}.route",
                label=label,
                ok=False,
                detail=str(exc),
            )
        ]

    errors: list[str] = []
    if route.adapter == "in-session":
        errors.append("dispatchable role resolves to in-session")
    elif shutil.which(route.adapter) is None:
        errors.append(f"{route.adapter} not found on PATH")

    model = route.config.get("model")
    if not model:
        errors.append("model is not declared")

    try:
        effort = _role_effort(route.adapter, route.config)
    except DispatchConfigError as exc:
        effort = None
        errors.append(str(exc))
    if effort is None:
        errors.append("effort is not declared")

    try:
        build_argv(route.adapter, route.config, "preflight route probe", mcp_servers=mcp_servers)
    except DispatchConfigError as exc:
        errors.append(str(exc))

    findings = [
        PreflightFinding(
            id=f"agents.{group}.{role_name}.route",
            label=label,
            ok=not errors,
            detail=(
                f"[{group}.{route.config_role}] resolves adapter={route.adapter}, "
                f"model={model}, effort={effort}, "
                f"profile={route.model_profile or '-'}, runtime={TRUSTED_RUNTIME_MODE}"
                if not errors
                else "; ".join(errors)
            ),
            required="explicit adapter, model, effort, and runtime-mode disclosure",
            notes=[TRUSTED_RUNTIME_NOTE] if not errors else [],
        )
    ]
    if route.adapter == "claude" and not errors:
        findings.extend(
            _check_claude_mcp_config(
                role_name,
                route.config,
                mcp_servers,
                repo_root,
                id_prefix=f"agents.{group}.{role_name}",
            )
        )
    return findings


def _check_claude_mcp_config(
    role_name: str,
    role: dict[str, Any],
    mcp_servers: dict[str, Any],
    repo_root: Path,
    *,
    id_prefix: str | None = None,
) -> list[PreflightFinding]:
    prefix = id_prefix if id_prefix is not None else f"agents.{role_name}"
    try:
        mcp_config = _claude_mcp_config(role, mcp_servers)
        parsed = json.loads(mcp_config)
    except (DispatchConfigError, json.JSONDecodeError) as exc:
        return [
            PreflightFinding(
                id=f"{prefix}.mcp_config",
                label=f"{role_name} Claude MCP config",
                ok=False,
                detail=str(exc),
            )
        ]

    findings = [
        PreflightFinding(
            id=f"{prefix}.mcp_config",
            label=f"{role_name} Claude MCP config",
            ok=isinstance(parsed.get("mcpServers"), dict),
            detail=mcp_config
            if isinstance(parsed.get("mcpServers"), dict)
            else "generated MCP config is missing mcpServers object",
        )
    ]
    for name in _mcp_names(role):
        server = (mcp_servers or {}).get(name)
        if isinstance(server, dict):
            findings.append(
                _check_mcp_server_command(role_name, name, server, repo_root, id_prefix=prefix)
            )
    return findings


def _check_mcp_server_command(
    role_name: str,
    server_name: str,
    server: dict[str, Any],
    repo_root: Path,
    *,
    id_prefix: str | None = None,
) -> PreflightFinding:
    prefix = id_prefix if id_prefix is not None else f"agents.{role_name}"
    command = str(server["command"])
    resolved = _resolve_declared_command(command, repo_root)
    ok = resolved is not None
    return PreflightFinding(
        id=f"{prefix}.mcp.{server_name}",
        label=f"{role_name} MCP server: {server_name}",
        ok=ok,
        detail=f"{command} resolves to {resolved}" if ok else f"{command} not found",
        required=command,
    )


def _agents_template() -> str:
    return f"Create .woof/agents.toml, for example:\n{AGENTS_TEMPLATE}"


def _check_declared_binaries(prereq: dict[str, Any]) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    for section in ("infra", "commands", "validators"):
        for binary, version_spec in (prereq.get(section) or {}).items():
            if binary == "ajv-formats":
                continue
            findings.append(_check_binary(section, binary, str(version_spec)))

    indexing = prereq.get("indexing") or {}
    for binary, version_spec in indexing.items():
        if binary == "tree-sitter":
            continue
        findings.append(_check_binary("indexing", binary, str(version_spec)))

    tree_sitter = indexing.get("tree-sitter") or {}
    if tree_sitter:
        findings.append(_check_binary("tree-sitter", "tree-sitter", str(tree_sitter["cli"])))
    return findings


def _check_binary(section: str, binary: str, version_spec: str) -> PreflightFinding:
    path = shutil.which(binary)
    label = f"{binary} ({section})"
    if path is None:
        return PreflightFinding(
            id=f"{section}.{binary}",
            label=label,
            ok=False,
            detail=f"{binary} not found on PATH",
            required=version_spec,
        )
    if version_spec == "any":
        return PreflightFinding(
            id=f"{section}.{binary}",
            label=label,
            ok=True,
            detail=f"found at {path}",
            required=version_spec,
        )

    ok, found = _version_meets_floor(binary, version_spec)
    return PreflightFinding(
        id=f"{section}.{binary}",
        label=label,
        ok=ok,
        detail=f"version {found} meets floor" if ok else f"version {found} below required floor",
        required=version_spec,
    )


def _version_meets_floor(binary: str, version_spec: str) -> tuple[bool, str]:
    returncode, output = _run_capture([binary, "--version"], timeout=10)
    if returncode != 0:
        return False, f"unknown ({output})"
    match = re.search(r"\d+(?:\.\d+){0,2}", output)
    if not match:
        return False, f"unknown ({output})"
    found = match.group(0)
    return _version_tuple(found) >= _version_tuple(version_spec.rstrip("+")), found


def _version_tuple(version: str) -> tuple[int, int, int]:
    parts = [int(part) for part in version.split(".")]
    return (
        parts[0] if len(parts) > 0 else 0,
        parts[1] if len(parts) > 1 else 0,
        parts[2] if len(parts) > 2 else 0,
    )


def _check_ajv_formats(prereq: dict[str, Any]) -> list[PreflightFinding]:
    if "ajv-formats" not in (prereq.get("validators") or {}):
        return []
    if shutil.which("ajv") is None:
        return [
            PreflightFinding(
                id="validators.ajv-formats",
                label="ajv-formats",
                ok=False,
                detail="ajv-cli not found, so ajv-formats cannot be loaded",
                install="volta install ajv-cli ajv-formats",
            )
        ]

    schema = '{"type":"string","format":"date-time"}\n'
    data = '"2026-05-03T00:00:00Z"\n'
    with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False) as schema_fh:
        schema_fh.write(schema)
        schema_path = Path(schema_fh.name)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as data_fh:
        data_fh.write(data)
        data_path = Path(data_fh.name)
    try:
        returncode, output = _run_capture(
            [
                "ajv",
                "validate",
                "--spec=draft2020",
                "-c",
                "ajv-formats",
                "-s",
                str(schema_path),
                "-d",
                str(data_path),
            ],
            timeout=20,
        )
    finally:
        schema_path.unlink(missing_ok=True)
        data_path.unlink(missing_ok=True)

    return [
        PreflightFinding(
            id="validators.ajv-formats",
            label="ajv-formats",
            ok=returncode == 0,
            detail="ajv-formats loaded" if returncode == 0 else output,
            install="volta install ajv-cli ajv-formats",
        )
    ]


def _check_tracker(prereq: dict[str, Any]) -> list[PreflightFinding]:
    tracker = prereq.get("tracker") or {}
    kind = tracker.get("kind")
    if kind == "local":
        return [
            PreflightFinding(
                id="tracker.kind",
                label="Issue tracker",
                ok=True,
                detail="tracker kind 'local'; filesystem-only, no remote reachability checks",
            )
        ]
    if kind != "github":
        return []
    repo = tracker.get("repo")
    if not repo:
        return []
    findings = [
        _check_github_rate_limit(),
        _run_command_check(
            id_="github.repo",
            label=f"GitHub repo {repo}",
            argv=["gh", "api", f"/repos/{repo}", "-H", "Accept: application/vnd.github+json"],
            ok_detail=f"gh can access {repo}",
            install=f"gh repo view {repo}",
        ),
    ]
    return findings


def _check_github_rate_limit() -> PreflightFinding:
    id_ = "github.rate_limit"
    label = "GitHub auth"
    install = "gh auth login"
    if shutil.which("gh") is None:
        return PreflightFinding(
            id=id_,
            label=label,
            ok=False,
            detail="gh not found on PATH",
            install=install,
        )

    returncode, output = _run_capture(["gh", "api", "/rate_limit"], timeout=20)
    if returncode != 0:
        return PreflightFinding(
            id=id_,
            label=label,
            ok=False,
            detail=output,
            install=install,
        )

    remaining = github_core_remaining(output)
    if remaining is not None and remaining <= GITHUB_RATE_LIMIT_SAFETY_MARGIN:
        return PreflightFinding(
            id=id_,
            label=label,
            ok=False,
            detail=(
                f"GitHub API core rate limit remaining {remaining}; "
                f"requires > {GITHUB_RATE_LIMIT_SAFETY_MARGIN}"
            ),
            install=install,
        )

    return PreflightFinding(
        id=id_,
        label=label,
        ok=True,
        detail=(
            f"gh api /rate_limit succeeded; {remaining} core request(s) remaining"
            if remaining is not None
            else "gh api /rate_limit succeeded"
        ),
        install=install,
    )


def _check_adapter_auth_markers(repo_root: Path) -> list[PreflightFinding]:
    """Probe Claude/Codex credential markers for each configured dispatchable role.

    The marker check is intentionally conservative: it confirms either an API-key
    environment variable is set or the adapter has been logged in once (its
    credential file exists). Live API auth state and model availability are only
    validated at first dispatch - runtime token expiry, revoked credentials, or
    a model the underlying API has retired surface as a fail-loud dispatch
    error rather than a preflight finding.

    Covers both base roles and any adapter introduced exclusively via a node-group
    route overlay, so an execution-group Claude route is auth-checked even when
    the base roles use only Codex.
    """

    agents_path = repo_root / ".woof" / "agents.toml"
    if not agents_path.is_file():
        return []
    loaded = _load_toml(agents_path)
    if not isinstance(loaded, dict):
        return []
    findings: list[PreflightFinding] = []
    seen_keys: set[tuple[str, str]] = set()  # (role_name, adapter) already checked
    seen_adapters: set[str] = set()  # adapters auth-checked via base roles

    for role_name in ("primary", "reviewer"):
        try:
            route = resolve_agent_route(loaded, role_name)
        except DispatchConfigError:
            continue
        if route.adapter not in ADAPTER_AUTH_MARKERS:
            continue
        key = (role_name, route.adapter)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        seen_adapters.add(route.adapter)
        findings.append(_check_adapter_auth(role_name, route.adapter))

    for group in sorted(NODE_GROUPS):
        for role_name in ("primary", "reviewer"):
            try:
                route = resolve_agent_route(loaded, role_name, route_key=group)
            except DispatchConfigError:
                continue
            if route.adapter not in ADAPTER_AUTH_MARKERS:
                continue
            if route.adapter in seen_adapters:
                continue
            seen_adapters.add(route.adapter)
            findings.append(
                _check_adapter_auth(
                    role_name,
                    route.adapter,
                    id_prefix=f"agents.{group}.{role_name}",
                )
            )

    return findings


ADAPTER_AUTH_MARKERS: dict[str, dict[str, str]] = {
    "claude": {
        "env_key": "ANTHROPIC_API_KEY",
        "config_env": "CLAUDE_CONFIG_DIR",
        "default_dir": "~/.claude",
        "marker_name": ".credentials.json",
        "install": "claude /login",
    },
    "codex": {
        "env_key": "OPENAI_API_KEY",
        "config_env": "CODEX_HOME",
        "default_dir": "~/.codex",
        "marker_name": "auth.json",
        "install": "codex login",
    },
}


def _check_adapter_auth(
    role_name: str,
    adapter: str,
    *,
    id_prefix: str | None = None,
) -> PreflightFinding:
    spec = ADAPTER_AUTH_MARKERS[adapter]
    prefix = id_prefix if id_prefix is not None else f"agents.{role_name}"
    finding_id = f"{prefix}.auth"
    label_role = prefix.removeprefix("agents.")
    label = f"{label_role} adapter auth ({adapter})"
    env_key = spec["env_key"]
    if os.environ.get(env_key):
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=True,
            detail=(
                f"{env_key} set; live auth and model availability are validated at first dispatch"
            ),
        )
    config_dir = os.environ.get(spec["config_env"])
    home = Path(config_dir).expanduser() if config_dir else Path(spec["default_dir"]).expanduser()
    marker = home / spec["marker_name"]
    if marker.is_file():
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=True,
            detail=(
                f"{marker} present; live auth and model availability are validated "
                "at first dispatch"
            ),
        )
    return PreflightFinding(
        id=finding_id,
        label=label,
        ok=False,
        detail=(
            f"no {env_key} environment variable and no credential file at {marker}; "
            f"{adapter} dispatch will fail"
        ),
        install=spec["install"],
    )


CARTOGRAPHY_DESIGN_DOCS: tuple[tuple[str, str], ...] = (
    ("cartography.target_architecture", "TARGET-ARCHITECTURE.md"),
    ("cartography.principles", "PRINCIPLES.md"),
)
CARTOGRAPHY_MECHANICAL_FILES = ("tags", "files.txt", "freshness.json")
CARTOGRAPHY_FRESHNESS_FILE = "freshness.json"
DEFAULT_STUB_MARKER = "<!-- woof:stub -->"
DEFAULT_SUMMARY_MIN_CHARS = 200
DEFAULT_STALENESS_FLOOR_HOURS = 168
CARTOGRAPHY_REFRESH_PROMPT = (
    "Run ./scripts/refresh-cartography to regenerate the cartography mechanical layer."
)
CARTOGRAPHY_AUTHOR_HINT = (
    "Author .woof/codebase/ through the /woof map-codebase flow "
    "(see skills/woof/references/setup.md and skills/woof/references/map-codebase.md)."
)
CARTOGRAPHY_ONBOARDING_INSTALL = """\
Follow the /woof setup onboarding path:
1. Run `woof init --language <lang>` (repeat --language as needed) so `.woof/prerequisites.toml` contains `[cartography]` and `scripts/refresh-cartography` is composed.
2. Author `.woof/codebase/TARGET-ARCHITECTURE.md` and `.woof/codebase/PRINCIPLES.md` during setup.
3. Run the /woof map-codebase flow to write the AS-IS cartography docs, then run `./scripts/refresh-cartography` and `woof hooks install`.
References:
- skills/woof/references/setup.md
- skills/woof/references/map-codebase.md
"""


def _check_cartography(repo_root: Path, prereq: dict[str, Any]) -> list[PreflightFinding]:
    """Validate the cartography artefact group at ``.woof/codebase/`` (ADR-004).

    Cartography is mandatory infrastructure. A missing ``[cartography]`` block
    now means a legacy consumer has not been onboarded to the current setup path,
    so preflight fails with an actionable setup/map-codebase pointer. When the
    block is declared, preflight fails closed on a missing or non-executable
    ``scripts/refresh-cartography``, a missing or stub design doc
    (``TARGET-ARCHITECTURE.md``, ``PRINCIPLES.md``), or a missing
    mechanical-layer file (``tags``, ``files.txt``, ``freshness.json``).
    """

    cartography = prereq.get("cartography")
    required = isinstance(cartography, dict)
    findings: list[PreflightFinding] = []
    if not required:
        return [_check_cartography_onboarding(repo_root)]

    script = _check_cartography_script(repo_root)
    findings.append(script)

    min_chars = int(cartography.get("summary_min_chars") or DEFAULT_SUMMARY_MIN_CHARS)
    stub_marker = str(cartography.get("stub_marker") or DEFAULT_STUB_MARKER)
    for finding_id, filename in CARTOGRAPHY_DESIGN_DOCS:
        findings.append(
            _check_cartography_doc(
                repo_root,
                finding_id,
                filename,
                min_chars=min_chars,
                stub_marker=stub_marker,
            )
        )
    findings.append(_check_cartography_mechanical(repo_root))
    if cartography.get("languages"):
        findings.append(_check_cartography_ctags())
    floor_hours = int(cartography.get("staleness_floor_hours") or DEFAULT_STALENESS_FLOOR_HOURS)
    freshness = _check_cartography_freshness(repo_root, floor_hours=floor_hours)
    if freshness is not None:
        findings.append(freshness)
    return findings


def _check_cartography_secrets(repo_root: Path) -> list[PreflightFinding]:
    """Scan committed cartography prose for leaked secrets (ADR-004 hygiene).

    The design and AS-IS layers under ``.woof/codebase/`` are committed planning
    state, authored partly by mapper subagents, so a leaked key lands in git
    history. This gate runs uncached (the preflight floor cache key does not track
    cartography doc content, so a cached pass would mask a freshly written secret)
    and fails closed on a high-signal token match. Only the file, line, and
    pattern reason are reported; the matched value is never surfaced.
    """

    codebase = repo_root / ".woof" / "codebase"
    if not codebase.is_dir():
        return []

    findings: list[PreflightFinding] = []
    for doc in sorted(codebase.glob("*.md")):
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                PreflightFinding(
                    id=f"cartography.secrets.{doc.stem}",
                    label=f"cartography secret scan ({doc.name})",
                    ok=False,
                    detail=f"could not read {doc.relative_to(repo_root).as_posix()}: {exc}",
                )
            )
            continue
        hits = scan_text_for_secrets(text)
        if not hits:
            continue
        rel = doc.relative_to(repo_root).as_posix()
        shown = hits[:10]
        locations = ", ".join(f"{rel}:{hit.line} ({hit.reason})" for hit in shown)
        if len(hits) > len(shown):
            locations += f", +{len(hits) - len(shown)} more"
        findings.append(
            PreflightFinding(
                id=f"cartography.secrets.{doc.stem}",
                label=f"cartography secret scan ({doc.name})",
                ok=False,
                required="no secrets in committed cartography docs",
                detail=(
                    f"potential secret(s) in committed cartography doc: {locations}. "
                    "Cartography docs are committed planning state (ADR-004); remove "
                    "the secret before committing."
                ),
                notes=[
                    "Mapper subagents must not read or quote secret-bearing files; "
                    "see skills/woof/references/map-codebase.md.",
                ],
            )
        )
    if not findings:
        findings.append(
            PreflightFinding(
                id="cartography.secrets",
                label="cartography secret scan",
                ok=True,
                detail="no high-signal secrets detected in committed cartography docs",
            )
        )
    return findings


def _check_cartography_onboarding(repo_root: Path) -> PreflightFinding:
    prereq_path = repo_root / ".woof" / "prerequisites.toml"
    return PreflightFinding(
        id="cartography.contract",
        label="cartography contract",
        ok=False,
        required=(
            ".woof/prerequisites.toml [cartography], .woof/codebase/ design and "
            "mapper docs, scripts/refresh-cartography, and the Woof post-commit hook"
        ),
        detail=(
            f"{prereq_path} has no [cartography] block; Woof requires the "
            "cartography onboarding path before preflight can pass"
        ),
        install=CARTOGRAPHY_ONBOARDING_INSTALL,
    )


def _check_cartography_script(repo_root: Path) -> PreflightFinding:
    """Check the consumer-owned ``scripts/refresh-cartography``.

    The Woof post-commit hook block invokes ``./scripts/refresh-cartography``, so
    a missing, stale, or non-executable script must fail loud rather than
    silently no-op.
    """

    script = repo_root / "scripts" / "refresh-cartography"
    if not script.exists():
        return PreflightFinding(
            id="cartography.script",
            label="cartography script",
            ok=False,
            detail=f"{script} not found",
            install=CARTOGRAPHY_AUTHOR_HINT,
        )
    if not script.is_file():
        return PreflightFinding(
            id="cartography.script",
            label="cartography script",
            ok=False,
            detail=f"{script} exists but is not a regular file",
        )
    if not os.access(script, os.X_OK):
        return PreflightFinding(
            id="cartography.script",
            label="cartography script",
            ok=False,
            detail=f"{script} is not executable",
            install=f"chmod +x {script}",
        )
    return PreflightFinding(
        id="cartography.script",
        label="cartography script",
        ok=True,
        detail=f"{script} is present and executable",
    )


def _check_cartography_doc(
    repo_root: Path,
    finding_id: str,
    filename: str,
    *,
    min_chars: int,
    stub_marker: str,
) -> PreflightFinding:
    """Check one human-authored design doc for presence and stub state.

    A doc is a stub if it still contains ``stub_marker`` or if its body (front
    matter excluded) is shorter than ``min_chars``. A short-but-intentional doc
    can opt out by marking itself complete in front matter (``status: complete``
    or ``complete: true``).
    """

    path = repo_root / ".woof" / "codebase" / filename
    label = f"cartography doc: {filename}"
    if not path.is_file():
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=False,
            detail=f"{path} not found",
            install=CARTOGRAPHY_AUTHOR_HINT,
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=False,
            detail=f"{path} unreadable: {exc}",
        )
    if stub_marker in text:
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=False,
            detail=(
                f"{path} still contains the stub marker {stub_marker!r}; "
                "author real content and remove the marker"
            ),
            install=CARTOGRAPHY_AUTHOR_HINT,
        )
    front, body = _split_front_matter(text)
    if _doc_marked_complete(front):
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=True,
            detail=f"{path} is marked complete in front matter",
        )
    body_len = len(body.strip())
    if body_len < min_chars:
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=False,
            detail=(
                f"{path} is a stub: {body_len} chars of content, below the "
                f"{min_chars}-char floor; author content or mark it complete in "
                "front matter (status: complete)"
            ),
            install=CARTOGRAPHY_AUTHOR_HINT,
        )
    return PreflightFinding(
        id=finding_id,
        label=label,
        ok=True,
        detail=f"{path} authored ({body_len} chars)",
    )


def _check_cartography_mechanical(repo_root: Path) -> PreflightFinding:
    """Check the mechanical layer (``tags``, ``files.txt``, ``freshness.json``)."""

    codebase = repo_root / ".woof" / "codebase"
    missing = [name for name in CARTOGRAPHY_MECHANICAL_FILES if not (codebase / name).is_file()]
    ok = not missing
    return PreflightFinding(
        id="cartography.mechanical",
        label="cartography mechanical layer",
        ok=ok,
        detail=(
            f"{', '.join(CARTOGRAPHY_MECHANICAL_FILES)} present in {codebase}"
            if ok
            else f"missing mechanical file(s) in {codebase}: {', '.join(missing)}"
        ),
        install=(
            None
            if ok
            else "Run ./scripts/refresh-cartography (or commit with the woof post-commit hook installed)."
        ),
    )


def _check_cartography_ctags() -> PreflightFinding:
    """Check that universal-ctags is on PATH when cartography languages are declared (ADR-004).

    ADR-004 mandates ctags as a hard prerequisite. ``scripts/refresh-cartography``
    uses ``--languages``, a Universal Ctags-only flag; a BSD or Exuberant Ctags
    binary passes a bare ``which`` check but fails at index time. Preflight
    therefore verifies both presence and variant.
    """
    _INSTALL_HINT = (
        "Install universal-ctags:\n"
        "  Debian/Ubuntu: sudo apt install -y universal-ctags\n"
        "  macOS:          brew install universal-ctags\n"
        "  Arch/CachyOS:   sudo pacman -S ctags"
    )
    path = shutil.which("ctags")
    if path is None:
        return PreflightFinding(
            id="cartography.ctags",
            label="cartography ctags",
            ok=False,
            detail="ctags not found on PATH; scripts/refresh-cartography will exit 1",
            install=_INSTALL_HINT,
        )
    _, version_output = _run_capture([path, "--version"], timeout=10)
    if "Universal Ctags" not in version_output:
        return PreflightFinding(
            id="cartography.ctags",
            label="cartography ctags",
            ok=False,
            detail=(
                f"ctags at {path} is not Universal Ctags; "
                "scripts/refresh-cartography requires Universal Ctags (--languages flag)"
            ),
            install=_INSTALL_HINT,
        )
    return PreflightFinding(
        id="cartography.ctags",
        label="cartography ctags",
        ok=True,
        detail=f"Universal Ctags found at {path}",
    )


def _check_cartography_freshness(
    repo_root: Path,
    *,
    floor_hours: int,
) -> PreflightFinding | None:
    """Report a stale ``freshness.json`` as a non-blocking warning (ADR-004, S2).

    Presence of ``freshness.json`` is the mechanical-layer check's concern and
    blocks (``_check_cartography_mechanical``); this check only governs age. A
    stamp older than ``floor_hours`` surfaces a warning carrying the
    ``./scripts/refresh-cartography`` prompt; a fresh stamp passes. Either way the
    finding is ``ok=True`` so it never affects preflight's pass/fail or exit code.

    Age derivation prefers ``_utc_now()`` minus the ISO ``ts`` (the authoritative
    production staleness signal, since ``age_s`` written at generation freezes
    once commits stop) and falls back to a non-negative numeric ``age_s`` when
    ``ts`` is absent or unparseable. A missing stamp yields no finding (the
    mechanical check already blocks on absence); an unparseable stamp or one with
    no usable age field warns non-blockingly, since presence -- not readability --
    is the blocking concern.

    Cache note: this is a floor check (24h TTL, keyed on config files, not on
    ``freshness.json``). Staleness can therefore be under-reported for up to one
    cache window. That is acceptable for a non-blocking warning: the window (24h)
    is far below the default floor (168h), and a stamp only ages past the floor
    after days without a commit, by which point the cache has long expired and the
    check has re-run. Keeping the sub-check in the floor tier keeps the whole
    cartography group coherent rather than splitting one finding into the runtime
    cache.
    """

    path = repo_root / ".woof" / "codebase" / CARTOGRAPHY_FRESHNESS_FILE
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _cartography_freshness_warn(f"{path} could not be read for a freshness age: {exc}")

    age_s = _freshness_age_seconds(payload)
    if age_s is None:
        return _cartography_freshness_warn(
            f"{path} has no usable age_s or ts field; cannot derive a freshness age"
        )

    age_label = _format_age(age_s)
    if age_s >= floor_hours * 3600:
        return _cartography_freshness_warn(
            f"{path} is {age_label} old, beyond the {floor_hours}h staleness floor"
        )
    return PreflightFinding(
        id="cartography.freshness",
        label="cartography freshness",
        ok=True,
        detail=f"{path} is {age_label} old, within the {floor_hours}h staleness floor",
    )


def _cartography_freshness_warn(detail: str) -> PreflightFinding:
    return PreflightFinding(
        id="cartography.freshness",
        label="cartography freshness",
        ok=True,
        warn=True,
        detail=detail,
        notes=[CARTOGRAPHY_REFRESH_PROMPT],
    )


def _freshness_age_seconds(payload: Any) -> float | None:
    """Derive a freshness age in seconds, preferring ``ts`` over ``age_s``.

    ``ts`` (wall-clock now minus the stamp's ISO timestamp) is the authoritative
    production staleness signal. The post-commit hook rewrites the stamp on every
    commit, so a stamp only ages once commits stop -- exactly the case where the
    static ``age_s`` written at generation (always 0) stays frozen while ``ts``
    keeps ageing. Preferring ``ts`` keeps that production staleness detectable; a
    frozen ``age_s`` can no longer mask it.

    ``age_s`` is the deterministic test input and the fallback when ``ts`` is
    absent or unparseable: a non-negative number is taken verbatim, so a test can
    inject a precise age without coupling to wall-clock by writing ``age_s`` and
    omitting ``ts``.
    """

    if not isinstance(payload, dict):
        return None
    ts = _parse_cache_time(payload.get("ts"))
    if ts is not None:
        return max((_utc_now() - ts).total_seconds(), 0.0)
    age_s = payload.get("age_s")
    if isinstance(age_s, (int, float)) and not isinstance(age_s, bool) and age_s >= 0:
        return float(age_s)
    return None


def _format_age(seconds: float) -> str:
    hours = seconds / 3600
    if hours >= 48:
        return f"{hours / 24:.1f} days"
    return f"{hours:.1f} hours"


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split leading YAML front matter from a markdown body, tolerating neither."""

    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            try:
                front = yaml.safe_load(text[4:end])
            except yaml.YAMLError:
                front = None
            body = text[end + len("\n---\n") :]
            return (front if isinstance(front, dict) else {}), body
    return {}, text


def _doc_marked_complete(front: dict[str, Any]) -> bool:
    status = str(front.get("status") or "").strip().lower()
    return status == "complete" or front.get("complete") is True


def _check_language_tools(prereq: dict[str, Any]) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    plugin_list: str | None = None
    for language in (prereq.get("lsp") or {}).get("languages") or []:
        registry = _load_language_registry(str(language))
        if isinstance(registry, PreflightFinding):
            findings.append(registry)
            continue

        lsp = registry["lsp"]
        binary = lsp["binary"]
        binary_path = shutil.which(binary)
        findings.append(
            PreflightFinding(
                id=f"lsp.{language}.binary",
                label=f"{binary} ({language} LSP)",
                ok=binary_path is not None,
                detail=f"found at {binary_path}" if binary_path else f"{binary} not found on PATH",
                install=lsp["binary_install"],
                notes=list(lsp.get("gotchas") or []),
            )
        )

        plugin = lsp.get("plugin")
        if plugin:
            if plugin_list is None:
                plugin_list = _claude_plugin_list()
            plugin_list_ok = not plugin_list.startswith("ERROR:")
            findings.append(
                PreflightFinding(
                    id=f"lsp.{language}.plugin",
                    label=f"{plugin} ({language} Claude plugin)",
                    ok=plugin_list_ok and plugin in plugin_list,
                    detail=(
                        "plugin installed"
                        if plugin_list_ok and plugin in plugin_list
                        else plugin_list.removeprefix("ERROR: ")
                        if not plugin_list_ok
                        else "plugin not installed"
                    ),
                    install=lsp["plugin_install"],
                    notes=list(lsp.get("gotchas") or []),
                )
            )
    return findings


def _claude_plugin_list() -> str:
    if shutil.which("claude") is None:
        return "ERROR: claude not found on PATH"
    returncode, output = _run_capture(["claude", "plugin", "list"], timeout=20)
    if returncode != 0:
        return f"ERROR: {output}"
    return output


def _check_tree_sitter(prereq: dict[str, Any]) -> list[PreflightFinding]:
    tree_sitter = ((prereq.get("indexing") or {}).get("tree-sitter")) or {}
    findings: list[PreflightFinding] = []
    for language in tree_sitter.get("grammars") or []:
        registry = _load_language_registry(str(language))
        if isinstance(registry, PreflightFinding):
            findings.append(registry)
            continue
        if shutil.which("tree-sitter") is None:
            findings.append(
                PreflightFinding(
                    id=f"tree-sitter.{language}",
                    label=f"tree-sitter grammar: {language}",
                    ok=False,
                    detail="tree-sitter not found on PATH",
                    install=registry["tree-sitter"]["grammar_install"],
                )
            )
            continue
        ts = registry["tree-sitter"]
        with tempfile.NamedTemporaryFile("w", suffix=f".{language}", delete=False) as fh:
            fh.write(ts["verify_snippet"] + "\n")
            snippet_path = Path(fh.name)
        try:
            returncode, output = _run_capture(
                [
                    "tree-sitter",
                    "parse",
                    "--scope",
                    ts["verify_scope"],
                    str(snippet_path),
                ],
                timeout=20,
            )
        finally:
            snippet_path.unlink(missing_ok=True)
        findings.append(
            PreflightFinding(
                id=f"tree-sitter.{language}",
                label=f"tree-sitter grammar: {language}",
                ok=returncode == 0,
                detail="verify snippet parsed" if returncode == 0 else output,
                install=ts["grammar_install"],
            )
        )
    return findings


def _load_language_registry(language: str) -> dict[str, Any] | PreflightFinding:
    path = _language_registry_path(language)
    if not path.is_file():
        return PreflightFinding(
            id=f"language.{language}",
            label=f"language registry: {language}",
            ok=False,
            detail=f"{path} not found",
        )
    loaded = _load_toml(path)
    if not isinstance(loaded, dict):
        return PreflightFinding(
            id=f"language.{language}",
            label=f"language registry: {language}",
            ok=False,
            detail=loaded,
        )
    if shutil.which("ajv") is None:
        return loaded
    ok, output = run_ajv(
        schema_dir() / SCHEMAS["language-registry"],
        json.dumps(loaded).encode(),
    )
    if not ok:
        return PreflightFinding(
            id=f"language.{language}",
            label=f"language registry: {language}",
            ok=False,
            detail=output,
        )
    return loaded


def _language_registry_path(language: str) -> Path:
    return tool_root() / "languages" / f"{language}.toml"


def _check_quality_gate_commands(repo_root: Path) -> list[PreflightFinding]:
    config_path = repo_root / ".woof" / "quality-gates.toml"
    if not config_path.is_file():
        return []
    loaded = _load_toml(config_path)
    if not isinstance(loaded, dict):
        return [
            PreflightFinding(
                id="quality-gates.config",
                label="quality-gates.toml",
                ok=False,
                detail=loaded,
            )
        ]
    findings: list[PreflightFinding] = []
    for name, gate in (loaded.get("gates") or {}).items():
        command = str(gate.get("command") or "")
        try:
            first = shlex.split(command)[0]
        except (IndexError, ValueError) as exc:
            findings.append(
                PreflightFinding(
                    id=f"quality-gates.{name}",
                    label=f"quality gate command: {name}",
                    ok=False,
                    detail=f"cannot parse command {command!r}: {exc}",
                )
            )
            continue

        exists = _command_exists(first, repo_root)
        findings.append(
            PreflightFinding(
                id=f"quality-gates.{name}",
                label=f"quality gate command: {name}",
                ok=exists,
                detail=f"{first} resolves" if exists else f"{first} not found on PATH",
            )
        )
    return findings


def _check_host_prerequisites(
    repo_root: Path,
    prereq: dict[str, Any],
) -> list[PreflightFinding]:
    host = prereq.get("host") or {}
    findings: list[PreflightFinding] = []
    platforms = [str(platform) for platform in host.get("platforms") or []]
    if platforms:
        current = _current_platform()
        findings.append(
            PreflightFinding(
                id="host.platform",
                label="host platform",
                ok=current in platforms,
                detail=f"current platform is {current}",
                required=", ".join(platforms),
            )
        )

    for name, check in (host.get("checks") or {}).items():
        findings.append(
            _run_configured_command_check(
                id_=f"host.{name}",
                label=f"host check: {name}",
                check=check,
                repo_root=repo_root,
            )
        )
    return findings


def _check_server_prerequisites(
    repo_root: Path,
    prereq: dict[str, Any],
) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    for name, check in (prereq.get("servers") or {}).items():
        if "url" in check:
            findings.append(_run_server_url_check(name, check))
        else:
            findings.append(
                _run_configured_command_check(
                    id_=f"servers.{name}",
                    label=f"server check: {name}",
                    check=check,
                    repo_root=repo_root,
                )
            )
    return findings


def _current_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith(("win32", "cygwin", "msys")):
        return "windows"
    return sys.platform


def _run_configured_command_check(
    *,
    id_: str,
    label: str,
    check: dict[str, Any],
    repo_root: Path,
) -> PreflightFinding:
    command = str(check.get("command") or "")
    install = check.get("install")
    notes = [str(note) for note in check.get("notes") or []]
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return PreflightFinding(
            id=id_,
            label=label,
            ok=False,
            detail=f"cannot parse command {command!r}: {exc}",
            required=check.get("required") or command,
            install=str(install) if install is not None else None,
            notes=notes,
        )
    if not argv:
        return PreflightFinding(
            id=id_,
            label=label,
            ok=False,
            detail="command is empty",
            required=check.get("required") or command,
            install=str(install) if install is not None else None,
            notes=notes,
        )

    resolved = _resolve_declared_command(argv[0], repo_root)
    if resolved is None:
        return PreflightFinding(
            id=id_,
            label=label,
            ok=False,
            detail=f"{argv[0]} not found",
            required=check.get("required") or command,
            install=str(install) if install is not None else None,
            notes=notes,
        )

    timeout = int(check.get("timeout_seconds") or 20)
    returncode, output = _run_capture(argv, timeout=timeout, cwd=repo_root)
    return PreflightFinding(
        id=id_,
        label=label,
        ok=returncode == 0,
        detail=(
            f"command succeeded: {command}"
            if returncode == 0
            else f"command exited {returncode}: {output}"
        ),
        required=check.get("required") or command,
        install=str(install) if install is not None else None,
        notes=notes,
    )


def _run_server_url_check(name: str, check: dict[str, Any]) -> PreflightFinding:
    url = str(check["url"])
    timeout = int(check.get("timeout_seconds") or 5)
    expected_status = int(check.get("expected_status") or 200)
    install = check.get("install")
    notes = [str(note) for note in check.get("notes") or []]
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return PreflightFinding(
            id=f"servers.{name}",
            label=f"server check: {name}",
            ok=False,
            detail=f"{url} unreachable: {exc}",
            required=f"HTTP {expected_status} from {url}",
            install=str(install) if install is not None else None,
            notes=notes,
        )

    return PreflightFinding(
        id=f"servers.{name}",
        label=f"server check: {name}",
        ok=status == expected_status,
        detail=(
            f"{url} returned HTTP {status}"
            if status == expected_status
            else f"{url} returned HTTP {status}; expected {expected_status}"
        ),
        required=f"HTTP {expected_status} from {url}",
        install=str(install) if install is not None else None,
        notes=notes,
    )


def _resolve_declared_command(command: str, repo_root: Path) -> str | None:
    if "/" in command:
        candidate = Path(command).expanduser()
        if not candidate.is_absolute():
            candidate = (repo_root / candidate).resolve()
        return str(candidate) if candidate.exists() and os.access(candidate, os.X_OK) else None
    return shutil.which(command)


def _command_exists(command: str, repo_root: Path) -> bool:
    return _resolve_declared_command(command, repo_root) is not None


def _run_command_check(
    *,
    id_: str,
    label: str,
    argv: list[str],
    ok_detail: str,
    install: str | None = None,
) -> PreflightFinding:
    if shutil.which(argv[0]) is None:
        return PreflightFinding(
            id=id_,
            label=label,
            ok=False,
            detail=f"{argv[0]} not found on PATH",
            install=install,
        )
    returncode, output = _run_capture(argv, timeout=20)
    return PreflightFinding(
        id=id_,
        label=label,
        ok=returncode == 0,
        detail=ok_detail if returncode == 0 else output,
        install=install,
    )


def _exc_str(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _run_capture(
    argv: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        return 127, f"{argv[0]} not found on PATH"
    except subprocess.TimeoutExpired as exc:
        output = (_exc_str(exc.stdout) + _exc_str(exc.stderr)).strip()
        detail = f"timed out after {timeout}s"
        return 124, f"{detail}\n{output}".strip()
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _print_text_result(result: PreflightResult) -> None:
    failed = result.failed
    warnings = result.warnings
    if failed:
        print(f"[INFRA PREFLIGHT FAILED - {len(failed)} missing prerequisite(s)]")
    else:
        summary = f"{len(result.findings)} check(s)"
        if warnings:
            summary += f", {len(warnings)} warning(s)"
        print(f"[INFRA PREFLIGHT PASSED - {summary}]")

    for finding in result.findings:
        mark = "FAIL" if not finding.ok else "WARN" if finding.warn else "OK"
        print(f"{mark} {finding.label}")
        if finding.required:
            print(f"  Required: {finding.required}")
        print(f"  {finding.detail}")
        if not finding.ok and finding.install:
            print("  Install:")
            for line in finding.install.rstrip().splitlines():
                print(f"    {line}")
        if finding.notes:
            print("  Notes:")
            for note in finding.notes:
                print(f"    - {note}")

    _print_operator_state(result.operator_state)

    if failed:
        print()
        print("Re-run `woof preflight` after installing.")


def _print_operator_state(operator_state: dict[str, Any]) -> None:
    print()
    print("Operator state:")
    current = operator_state.get("current_epic") or {}
    if current.get("exists"):
        selected = "true" if current.get("selected") else "false"
        valid = "true" if current.get("valid") else "false"
        exists = "true" if current.get("epic_dir_exists") else "false"
        print(
            f"  current_epic: {current.get('value') or '-'} "
            f"selected={selected} valid={valid} epic_dir_exists={exists}"
        )
    else:
        print(f"  current_epic: none path={current.get('path')}")

    runtime_policy = operator_state.get("runtime_policy") or {}
    print(f"  runtime_policy: {runtime_policy.get('mode') or 'unknown'}")
    _print_operator_routes(operator_state.get("dispatch_routes") or {})

    epic = operator_state.get("epic")
    if not epic:
        print("  epic_state: unavailable")
        return
    if not epic.get("exists", False):
        print(f"  epic_state: unavailable error={epic.get('error')}")
        return
    next_step = epic.get("next") or {}
    next_action = epic.get("next_action") or {}
    print(
        f"  next: {next_step.get('node') or '-'} "
        f"story={next_step.get('story_id') or '-'} "
        f"reason={next_step.get('reason') or '-'}"
    )
    print(
        f"  next_action: {next_action.get('action') or '-'} "
        f"command={next_action.get('command') or '-'} "
        f"reason={next_action.get('reason') or '-'}"
    )
    gate = epic.get("gate") or {}
    if gate.get("open"):
        print(
            f"  gate: open type={gate.get('type')} story={gate.get('story_id') or '-'} "
            f"cause={gate.get('cause') or '-'}"
        )
    else:
        print("  gate: closed")
    checks = epic.get("checks") or {}
    if checks.get("exists") and checks.get("valid"):
        state = "OK" if checks.get("ok") else "FAIL"
        print(
            f"  checks: {state} total={checks.get('total')} failed={checks.get('failed')} "
            f"triggered_by={','.join(checks.get('triggered_by') or []) or '-'}"
        )
    elif checks.get("exists"):
        print(f"  checks: malformed path={checks.get('path')} error={checks.get('error')}")
    else:
        print(f"  checks: unavailable path={checks.get('path')}")
    pointers = epic.get("audit_pointers") or {}
    if pointers:
        print(
            f"  audit_pointers: epic_jsonl={pointers.get('epic_jsonl')} "
            f"dispatch_jsonl={pointers.get('dispatch_jsonl')} "
            f"audit_dir={pointers.get('audit_dir')}"
        )


def _print_operator_routes(routes: dict[str, Any]) -> None:
    print(f"  dispatch_routes: {routes.get('path') or '-'}")
    if routes.get("model_profile"):
        print(f"  model_profile: {routes.get('model_profile')}")
    roles = routes.get("roles") or {}
    for role_name in ("primary", "reviewer"):
        route = roles.get(role_name) or {}
        if route.get("ok"):
            mcp = ",".join(route.get("mcp") or []) or "-"
            profile = route.get("model_profile") or "-"
            print(
                f"    {role_name}: adapter={route.get('adapter')} "
                f"model={route.get('model')} effort={route.get('effort')} "
                f"profile={profile} config_role={route.get('config_role')} mcp={mcp} "
                f"timeout={route.get('timeout_min')}m"
            )
        else:
            errors = "; ".join(str(error) for error in route.get("errors") or [])
            print(f"    {role_name}: unavailable {errors}")
    for group, group_routes in sorted((routes.get("routes") or {}).items()):
        for role_name in ("primary", "reviewer"):
            route = (group_routes or {}).get(role_name) or {}
            if route.get("ok"):
                print(
                    f"    {group}/{role_name}: adapter={route.get('adapter')} "
                    f"model={route.get('model')} effort={route.get('effort')}"
                )
            else:
                errors = "; ".join(str(error) for error in route.get("errors") or [])
                print(f"    {group}/{role_name}: unavailable {errors}")
