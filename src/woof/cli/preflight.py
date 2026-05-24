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

from woof.cli.commands.observe import build_operator_state_summary
from woof.cli.dispatcher import (
    TRUSTED_RUNTIME_MODE,
    TRUSTED_RUNTIME_NOTE,
    DispatchConfigError,
    _claude_mcp_config,
    _mcp_names,
    _role_effort,
    build_argv,
    resolve_role_route,
)
from woof.cli.main import (
    SCHEMAS,
    load_payload,
    run_ajv,
)
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
"""

CACHE_VERSION = 1
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

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
        }
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "ok": self.ok,
            "total": len(self.findings),
            "failed": len(self.failed),
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
    findings.extend(_check_cartography_script(repo_root))
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
    roles = loaded.get("roles") or {}
    mcp_servers = loaded.get("mcp_servers") or {}
    for role_name in ("primary", "reviewer"):
        findings.extend(_check_dispatch_role_route(role_name, roles, mcp_servers, repo_root))
    return findings


def _check_dispatch_role_route(
    role_name: str,
    roles: dict[str, Any],
    mcp_servers: dict[str, Any],
    repo_root: Path,
) -> list[PreflightFinding]:
    label = f"{role_name} route"
    try:
        route = resolve_role_route(roles, role_name)
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
                f"model={model}, effort={effort}, runtime={TRUSTED_RUNTIME_MODE}"
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


def _check_claude_mcp_config(
    role_name: str,
    role: dict[str, Any],
    mcp_servers: dict[str, Any],
    repo_root: Path,
) -> list[PreflightFinding]:
    try:
        mcp_config = _claude_mcp_config(role, mcp_servers)
        parsed = json.loads(mcp_config)
    except (DispatchConfigError, json.JSONDecodeError) as exc:
        return [
            PreflightFinding(
                id=f"agents.{role_name}.mcp_config",
                label=f"{role_name} Claude MCP config",
                ok=False,
                detail=str(exc),
            )
        ]

    findings = [
        PreflightFinding(
            id=f"agents.{role_name}.mcp_config",
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
            findings.append(_check_mcp_server_command(role_name, name, server, repo_root))
    return findings


def _check_mcp_server_command(
    role_name: str,
    server_name: str,
    server: dict[str, Any],
    repo_root: Path,
) -> PreflightFinding:
    command = str(server["command"])
    resolved = _resolve_declared_command(command, repo_root)
    ok = resolved is not None
    return PreflightFinding(
        id=f"agents.{role_name}.mcp.{server_name}",
        label=f"{role_name} MCP server: {server_name}",
        ok=ok,
        detail=f"{command} resolves to {resolved}" if ok else f"{command} not found",
        required=command,
    )


def _agents_template() -> str:
    return """Create .woof/agents.toml, for example:
# Runtime model: trusted-local automation. Woof does not sandbox dispatched
# agents, restrict writable paths, allow-list commands, block network access, or
# add MCP restrictions; commit-safety checks and gates guard what lands.

[roles.primary]
adapter = "codex"
model = "gpt-5.5"
effort = "xhigh"

[roles.reviewer]
adapter = "claude"
model = "claude-opus-4-7"
effort = "max"
mcp = []
"""


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
    return tuple([*parts, 0, 0, 0][:3])


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
    """

    agents_path = repo_root / ".woof" / "agents.toml"
    if not agents_path.is_file():
        return []
    loaded = _load_toml(agents_path)
    if not isinstance(loaded, dict):
        return []
    roles = loaded.get("roles") or {}
    findings: list[PreflightFinding] = []
    seen: set[tuple[str, str]] = set()
    for role_name in ("primary", "reviewer"):
        try:
            route = resolve_role_route(roles, role_name)
        except DispatchConfigError:
            continue
        if route.adapter not in ADAPTER_AUTH_MARKERS:
            continue
        key = (role_name, route.adapter)
        if key in seen:
            continue
        seen.add(key)
        findings.append(_check_adapter_auth(role_name, route.adapter))
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


def _check_adapter_auth(role_name: str, adapter: str) -> PreflightFinding:
    spec = ADAPTER_AUTH_MARKERS[adapter]
    finding_id = f"agents.{role_name}.auth"
    label = f"{role_name} adapter auth ({adapter})"
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


def _check_cartography_script(repo_root: Path) -> list[PreflightFinding]:
    """Validate the optional consumer-owned cartography script.

    Cartography is consumer-owned: the Woof post-commit hook block invokes
    ``./scripts/refresh-cartography`` only when present. If the script exists,
    preflight verifies it is a regular executable file so a stale or
    non-executable script fails loud rather than silently no-oping in the
    post-commit hook.
    """

    script = repo_root / "scripts" / "refresh-cartography"
    if not script.exists():
        return []
    if not script.is_file():
        return [
            PreflightFinding(
                id="cartography.script",
                label="cartography script",
                ok=False,
                detail=f"{script} exists but is not a regular file",
            )
        ]
    if not os.access(script, os.X_OK):
        return [
            PreflightFinding(
                id="cartography.script",
                label="cartography script",
                ok=False,
                detail=f"{script} is not executable",
                install=f"chmod +x {script}",
            )
        ]
    return [
        PreflightFinding(
            id="cartography.script",
            label="cartography script",
            ok=True,
            detail=f"{script} is present and executable",
        )
    ]


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
        output = ((exc.stdout or "") + (exc.stderr or "")).strip()
        detail = f"timed out after {timeout}s"
        return 124, f"{detail}\n{output}".strip()
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _print_text_result(result: PreflightResult) -> None:
    failed = result.failed
    if failed:
        print(f"[INFRA PREFLIGHT FAILED - {len(failed)} missing prerequisite(s)]")
    else:
        print(f"[INFRA PREFLIGHT PASSED - {len(result.findings)} check(s)]")

    for finding in result.findings:
        mark = "OK" if finding.ok else "FAIL"
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
    roles = routes.get("roles") or {}
    for role_name in ("primary", "reviewer"):
        route = roles.get(role_name) or {}
        if route.get("ok"):
            mcp = ",".join(route.get("mcp") or []) or "-"
            print(
                f"    {role_name}: adapter={route.get('adapter')} "
                f"model={route.get('model')} effort={route.get('effort')} "
                f"config_role={route.get('config_role')} mcp={mcp} "
                f"timeout={route.get('timeout_min')}m"
            )
        else:
            errors = "; ".join(str(error) for error in route.get("errors") or [])
            print(f"    {role_name}: unavailable {errors}")
