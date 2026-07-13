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

from woof import state
from woof.cli.commands.observe import build_operator_state_summary
from woof.cli.dispatcher import (
    TRUSTED_RUNTIME_MODE,
    TRUSTED_RUNTIME_NOTE,
)
from woof.cli.harness_registry import (
    BACKEND_HERDR,
    HarnessError,
    build_launch_argv,
    get_profile,
    resolve_harness_config,
)
from woof.cli.herdr import (
    HERDR_PROTOCOL,
    SocketClient,
    preflight_server,
    session_socket_path,
    socket_alive,
)
from woof.cli.main import (
    SCHEMAS,
    run_ajv,
)
from woof.cli.transport import declared_session
from woof.cli.transport_errors import WorkerError
from woof.graph.git import git_env
from woof.graph.state import Plan
from woof.lib.audit import scan_text_for_secrets
from woof.paths import (
    ProjectKeyError,
    project_config_path,
    repo_root_from_git,
    resolve_project_key,
    schema_dir,
    tool_root,
)
from woof.project_config import (
    RUN_PROFILE_ROLES,
    ProjectConfig,
    ProjectConfigError,
    RunProfileSlot,
    load_project_config,
    load_raw_project_config,
)
from woof.trackers.github import GITHUB_RATE_LIMIT_SAFETY_MARGIN, github_core_remaining

CACHE_VERSION = 7  # v7: single operator-home project config (ADR-017)
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
    try:
        repo_root = _resolve_repo_root(args.project_root)
        project_key = resolve_project_key(args.project)
    except ProjectKeyError as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2
    result = run_preflight(repo_root, project_key=project_key, force=args.force)
    if args.format == "json":
        print(json.dumps(result.as_dict(), indent=2))
    else:
        _print_text_result(result)
    return 0 if result.ok else 1


def run_preflight(
    repo_root: Path, *, project_key: str | None = None, force: bool = False
) -> PreflightResult:
    """Validate the project's prerequisites, config, and cartography.

    A missing project config is a hard failure and the only finding: nothing
    downstream can be judged without it, and there is no in-repo fallback to
    fall back to.
    """

    key = resolve_project_key(project_key)
    operator_state = build_operator_state_summary(key)
    try:
        config = load_project_config(key)
        raw = load_raw_project_config(key)
    except ProjectConfigError as exc:
        return PreflightResult(
            repo_root=repo_root,
            operator_state=operator_state,
            findings=[
                PreflightFinding(
                    id="config.project",
                    label="project config",
                    ok=False,
                    detail=str(exc),
                    required=f"{project_config_path(key)}",
                    install=f"woof init --project {key}",
                )
            ],
        )

    cache_key = _preflight_cache_key(config)
    cache_dir = state.preflight_cache_dir(key)
    findings: list[PreflightFinding] = list(
        _cached_findings(
            cache_dir / "preflight-floor",
            cache_key=cache_key,
            ttl=FLOOR_CACHE_TTL,
            force=force,
            producer=lambda: _run_floor_checks(key, repo_root, config, raw),
        )
    )
    findings.extend(
        _cached_findings(
            cache_dir / "preflight-runtime",
            cache_key=cache_key,
            ttl=RUNTIME_CACHE_TTL,
            force=force,
            producer=lambda: _run_runtime_checks(config),
        )
    )
    # Uncached: the floor/runtime cache keys do not track cartography doc content,
    # so a cached pass could mask a secret a mapper just wrote. A security gate
    # must re-read the docs every run.
    findings.extend(_check_profile_a_worktrees(key, repo_root, config))
    findings.extend(_check_cartography_secrets(key))
    return PreflightResult(repo_root=repo_root, findings=findings, operator_state=operator_state)


def _resolve_repo_root(project_root: str | None) -> Path:
    if project_root:
        return Path(project_root).resolve()
    try:
        return repo_root_from_git()
    except FileNotFoundError as exc:
        sys.stderr.write(f"woof: {exc}\n")
        sys.exit(2)


def _load_toml(path: Path) -> dict[str, Any] | str:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return f"{path}: TOML parse error: {exc}"


def _run_floor_checks(
    project_key: str, repo_root: Path, config: ProjectConfig, raw: dict[str, Any]
) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    findings.extend(_check_woof_install())
    findings.extend(_check_config_schema(config, raw))
    findings.extend(_check_repo_policy(config))
    findings.extend(_check_declared_binaries(config))
    findings.extend(_check_role_routes(config))
    findings.extend(_check_ajv_formats(config))
    findings.extend(_check_language_tools(config))
    findings.extend(_check_tree_sitter(config))
    findings.extend(_check_quality_gate_commands(repo_root, config))
    findings.extend(_check_cartography(project_key, repo_root, config))
    findings.extend(_check_host_prerequisites(repo_root, config))
    findings.extend(_check_server_prerequisites(repo_root, config))
    return findings


def _run_runtime_checks(config: ProjectConfig) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    findings.extend(_check_tracker(config))
    findings.extend(_check_adapter_auth_markers(config))
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


def _preflight_cache_key(config: ProjectConfig) -> str:
    digest = hashlib.sha256()
    for path in _cache_input_paths(config):
        digest.update(str(path).encode())
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            digest.update(f"ERROR:{exc}".encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _cache_input_paths(config: ProjectConfig) -> list[Path]:
    """Every file whose content can change a floor or runtime verdict."""

    paths = [config.source]
    languages = set(config.prerequisites.lsp_languages)
    languages.update(
        ((config.prerequisites.indexing.get("tree-sitter") or {}).get("grammars")) or []
    )
    paths.extend(_language_registry_path(str(language)) for language in sorted(languages))
    return paths


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _check_woof_install() -> list[PreflightFinding]:
    root = tool_root()
    required = [
        root / "schemas" / "project-config.schema.json",
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


def _check_config_schema(config: ProjectConfig, raw: dict[str, Any]) -> list[PreflightFinding]:
    if shutil.which("ajv") is None:
        return [
            PreflightFinding(
                id="config.schemas",
                label="project config schema",
                ok=False,
                detail=f"ajv-cli not found; cannot validate {config.source}",
                install="volta install ajv-cli ajv-formats",
            )
        ]

    ok, output = run_ajv(schema_dir() / SCHEMAS["project-config"], json.dumps(raw).encode())
    return [
        PreflightFinding(
            id="config.project",
            label="project config schema",
            ok=ok,
            detail="schema valid" if ok else output,
        )
    ]


def _check_role_routes(config: ProjectConfig) -> list[PreflightFinding]:
    profile = config.run_profile
    findings = [
        _check_dispatch_profile_slot(profile.name, "producer", profile.producer),
        _check_dispatch_profile_slot(profile.name, "reviewer", profile.reviewer),
    ]
    server = _check_declared_herdr_server() if _any_herdr_slot(profile) else None
    if server is not None:
        findings.append(server)
    return findings


def _any_herdr_slot(profile: Any) -> bool:
    for slot in (profile.producer, profile.reviewer):
        try:
            if get_profile(slot.harness).backend == BACKEND_HERDR:
                return True
        except HarnessError:
            continue
    return False


def _check_declared_herdr_server() -> PreflightFinding | None:
    """Check the herdr server this project would actually dispatch into.

    Compatibility is a property of the running server reached through the named
    session's socket, not of the herdr binary on PATH, so this connects.

    The named session is a runtime choice, not a host prerequisite: with none
    declared there is no server to check and preflight stays silent. Dispatch is the
    authority there, and it refuses a herdr profile with no declared session before
    any worker starts.
    """
    session = declared_session()
    if not session:
        return None
    socket_path = session_socket_path(session)
    alive = socket_alive(socket_path)
    client = SocketClient(str(socket_path)) if alive else None
    return check_herdr_server(session, socket_path=socket_path, alive=alive, client=client)


def check_herdr_server(
    session: str,
    *,
    socket_path: Path,
    alive: bool,
    client: Any,
) -> PreflightFinding:
    """Report the running server behind a named session: its socket, version, and protocol.

    Liveness is an accepted connection, never the presence of the socket file. A
    socket file with no listener behind it is a dead session whose orphaned socket
    would make every dispatch fail with a refused connection; dispatch reaps and
    respawns it, and preflight names it rather than reporting a healthy session.
    """
    label = "herdr running server"
    finding_id = "dispatch.herdr.server"
    if not alive or client is None:
        # A dead session is a warning, not a blocker: dispatch reaps the orphaned
        # socket and respawns the server. What it must never do is treat the socket
        # file as proof of a live server, which is how every dispatch ends up failing
        # with a connection refusal that never self-heals.
        detail = (
            f"herdr session {session!r} at {socket_path} has no listener"
            f"{' (an orphaned socket left by a dead server)' if socket_path.exists() else ''}; "
            "dispatch will reap it and respawn the server"
        )
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=True,
            warn=True,
            detail=detail,
            required=f"a herdr server serving session {session!r} at socket protocol "
            f"{HERDR_PROTOCOL}",
        )
    try:
        preflight = preflight_server(client, session=session, socket=str(socket_path))
    except WorkerError as exc:
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=False,
            detail=str(exc),
            required=f"a herdr server serving session {session!r} at socket protocol "
            f"{HERDR_PROTOCOL}",
        )
    return PreflightFinding(
        id=finding_id,
        label=label,
        ok=True,
        detail=(
            f"herdr session {session!r} at {preflight.socket} serves herdr "
            f"{preflight.version}, protocol {preflight.protocol}"
        ),
        required=f"a herdr server serving session {session!r} at socket protocol {HERDR_PROTOCOL}",
    )


def _check_dispatch_profile_slot(
    profile_name: str,
    role_name: str,
    slot: RunProfileSlot,
) -> PreflightFinding:
    """Check one dispatch slot resolves to a runnable harness on this host.

    The loader has already checked the declaration's shape, so what is left is
    what only the host can answer: the harness is known to the registry, its
    binary is on PATH, and the model/effort pair builds a launch argv.
    """

    label = f"{role_name} dispatch route"
    errors: list[str] = []
    profile = None
    try:
        profile = get_profile(slot.harness)
    except HarnessError as exc:
        errors.append(str(exc))

    resolved_model = slot.model
    resolved_effort = slot.effort
    if profile is not None:
        command = profile.base[0] if profile.base else profile.name
        if shutil.which(command) is None:
            errors.append(f"{command} not found on PATH")
        try:
            resolved = resolve_harness_config(
                profile.name,
                model=slot.model,
                effort=slot.effort,
            )
            resolved_model = resolved.model
            resolved_effort = resolved.effort
            build_launch_argv(
                resolved.harness,
                model=resolved.model,
                effort=resolved.effort,
            )
        except HarnessError as exc:
            errors.append(str(exc))

    return PreflightFinding(
        id=f"policy.run_profile.{role_name}.route",
        label=label,
        ok=not errors,
        detail=(
            f"[run_profiles.{profile_name}.{role_name}] resolves harness={profile.name}, "
            f"model={resolved_model}, effort={resolved_effort}, runtime={TRUSTED_RUNTIME_MODE}"
            if not errors and profile is not None
            else "; ".join(errors)
        ),
        required="harness plus registry-resolved model, effort, and runtime-mode disclosure",
        notes=[TRUSTED_RUNTIME_NOTE] if not errors else [],
    )


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


def _check_repo_policy(config: ProjectConfig) -> list[PreflightFinding]:
    """Report on the declaration the loader has already resolved.

    The loader is the shape gate: a malformed config never reaches here, it
    fails preflight outright. What is left for these findings is the semantics
    the loader deliberately does not enforce - the settings a delivery profile
    only needs once another setting turns a feature on, and placeholders a
    scaffold leaves behind.
    """

    return [
        _check_policy_delivery(config),
        _check_policy_verification(config),
        _check_policy_run_profile(config),
        _check_policy_check_floor(config),
        _check_policy_cartography_floor(config),
    ]


def _check_policy_delivery(config: ProjectConfig) -> PreflightFinding:
    delivery = config.delivery
    errors: list[str] = []

    if delivery.profile == "A":
        profile_a = config.profile_a
        if profile_a is None:
            errors.append("[profiles.A] is required for delivery.profile=A")
        else:
            # Profile A runs each unit in its own linked worktree, so a Profile A
            # project without a declared worktree root has nowhere to run. This
            # fails closed rather than defaulting to a path nobody chose.
            if profile_a.worktree is None:
                errors.append("profiles.A.worktree must be declared")
            if config.drain.merge_after_ready_pr:
                # Deploy-aware merge is the only mode that needs these, so they are
                # required here rather than in the loader.
                if not profile_a.terminal_deploy_checks:
                    errors.append("profiles.A.terminal_deploy_checks must list at least one check")
                if not profile_a.mergeability_settle_timeout:
                    errors.append(
                        "profiles.A.mergeability_settle_timeout must be a positive integer"
                    )
                if not profile_a.deploy_wait_timeout:
                    errors.append("profiles.A.deploy_wait_timeout must be a positive integer")
    if delivery.profile == "B" and config.profile_b is None:
        errors.append("[profiles.B] is required for delivery.profile=B")

    return PreflightFinding(
        id="policy.delivery",
        label="repo policy delivery profile",
        ok=not errors,
        detail=(
            f"profile={delivery.profile}, base_branch={delivery.base_branch}, "
            f"toolchain_root={delivery.toolchain_root}"
            if not errors
            else "; ".join(errors)
        ),
        required="delivery.profile A or B with selected profile settings",
    )


def _check_policy_verification(config: ProjectConfig) -> PreflightFinding:
    command = config.verification.command
    errors: list[str] = []
    if "<replace" in command:
        errors.append("verification.command still contains a <replace> placeholder")

    return PreflightFinding(
        id="policy.verification",
        label="repo policy verification command",
        ok=not errors,
        detail=f"command={command}" if not errors else "; ".join(errors),
        required="project verification command",
    )


def _check_policy_run_profile(config: ProjectConfig) -> PreflightFinding:
    return PreflightFinding(
        id="policy.run_profile",
        label="repo policy run profile",
        ok=True,
        detail=f"default_run_profile={config.run_profile.name}",
        required="default run profile with producer and reviewer slots",
    )


def _check_policy_check_floor(config: ProjectConfig) -> PreflightFinding:
    return PreflightFinding(
        id="policy.check_floor",
        label="repo policy check floor",
        ok=True,
        detail=", ".join(config.checks.floor),
        required="deterministic check floor",
    )


def _check_policy_cartography_floor(config: ProjectConfig) -> PreflightFinding:
    floor = config.cartography.floor
    errors: list[str] = []
    if floor != "none" and not config.cartography.declared:
        errors.append(f"cartography.floor={floor} requires a [cartography] section")

    return PreflightFinding(
        id="policy.cartography_floor",
        label="repo policy cartography floor",
        ok=not errors,
        detail=f"floor={floor}" if not errors else "; ".join(errors),
        required="cartography floor and matching cartography details when required",
    )


def _check_profile_a_worktrees(
    project_key: str, repo_root: Path, config: ProjectConfig
) -> list[PreflightFinding]:
    return _check_profile_a_worktrees_for_plans(repo_root, _plan_paths(project_key), config=config)


def _check_profile_a_worktrees_for_plans(
    repo_root: Path, plan_paths: list[Path], *, config: ProjectConfig | None = None
) -> list[PreflightFinding]:
    resolved = config
    if resolved is None:
        try:
            resolved = load_project_config()
        except ProjectConfigError:
            return []
    if resolved.delivery.profile != "A" or resolved.profile_a is None:
        return []
    worktree = resolved.profile_a.worktree
    if worktree is None or not worktree.root.strip():
        return []

    root = worktree.root
    derivation = worktree.derivation
    base_branch = resolved.delivery.base_branch.strip() or "main"
    ready_units = _ready_worktree_units(plan_paths)
    if not ready_units:
        return [
            PreflightFinding(
                id="profile_a.worktree",
                label="Profile A worktrees",
                ok=True,
                detail="no ready work units found",
            )
        ]

    findings: list[PreflightFinding] = []
    seen_paths: dict[Path, str] = {}
    duplicate_details: list[str] = []
    target_common_dir = _git_common_dir(repo_root)
    for unit in ready_units:
        resolved = _resolve_worktree_path(
            repo_root,
            root=root,
            derivation=derivation,
            work_unit_id=unit["work_unit_id"],
            metadata_path=unit["metadata_path"],
        )
        if isinstance(resolved, str):
            findings.append(
                PreflightFinding(
                    id=f"profile_a.worktree.{unit['work_unit_id']}",
                    label=f"Profile A worktree {unit['work_unit_id']}",
                    ok=False,
                    detail=resolved,
                    required="existing clean linked worktree on base or unit branch",
                )
            )
            continue
        path = resolved.resolve()
        previous = seen_paths.get(path)
        if previous is not None:
            duplicate_details.append(
                f"{previous} and {unit['work_unit_id']} both resolve to {path}"
            )
        else:
            seen_paths[path] = unit["work_unit_id"]
        findings.append(
            _validate_profile_a_worktree(
                repo_root,
                path,
                target_common_dir=target_common_dir,
                work_unit_id=unit["work_unit_id"],
                base_branch=base_branch,
                metadata_path=unit["metadata_path"],
            )
        )

    if duplicate_details:
        findings.append(
            PreflightFinding(
                id="profile_a.worktree.paths",
                label="Profile A worktree path uniqueness",
                ok=False,
                detail="; ".join(duplicate_details),
                required="one unique worktree path per ready work unit",
            )
        )
    return findings


def _ready_worktree_units(plan_paths: list[Path]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for plan_path in plan_paths:
        try:
            plan = Plan.model_validate(json.loads(plan_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        done = {unit.id for unit in plan.work_units if unit.state == "done"}
        metadata_path = _worktree_metadata_path(plan_path)
        for unit in plan.work_units:
            if unit.state == "pending" and all(dep in done for dep in unit.deps):
                units.append(
                    {
                        "work_unit_id": unit.id,
                        "plan_path": plan_path,
                        "metadata_path": metadata_path,
                    }
                )
    return units


def _plan_paths(project_key: str) -> list[Path]:
    roots = [state.epics_root(project_key), state.work_unit_sets_root(project_key)]
    paths: list[Path] = []
    for root in roots:
        if root.is_dir():
            paths.extend(sorted(root.glob("*/plan.json")))
    return paths


def _worktree_metadata_path(plan_path: Path) -> Path:
    if plan_path.parent.parent.name == "work-unit-sets":
        return plan_path.with_name("intake.json")
    return plan_path.with_name("run.json")


def _resolve_worktree_path(
    repo_root: Path,
    *,
    root: str,
    derivation: str,
    work_unit_id: str,
    metadata_path: Path,
) -> Path | str:
    if derivation == "manifest_map":
        unit_paths = _metadata_unit_paths(metadata_path)
        value = unit_paths.get(work_unit_id)
        if not value:
            return f"{metadata_path} does not map work unit {work_unit_id} to a worktree path"
        path = Path(value)
        return path if path.is_absolute() else repo_root / path
    return repo_root / root / work_unit_id


def _metadata_unit_paths(metadata_path: Path) -> dict[str, str]:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    worktrees = payload.get("worktrees")
    if not isinstance(worktrees, dict):
        return {}
    unit_paths = worktrees.get("unit_paths") or worktrees.get("paths")
    if not isinstance(unit_paths, dict):
        return {}
    return {
        str(unit_id): str(path)
        for unit_id, path in unit_paths.items()
        if isinstance(unit_id, str) and isinstance(path, str) and path
    }


def _metadata_unit_branches(metadata_path: Path) -> dict[str, str]:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    worktrees = payload.get("worktrees")
    if not isinstance(worktrees, dict):
        return {}
    branches = worktrees.get("unit_branches") or worktrees.get("branches")
    if not isinstance(branches, dict):
        return {}
    return {
        str(unit_id): str(branch)
        for unit_id, branch in branches.items()
        if isinstance(unit_id, str) and isinstance(branch, str) and branch
    }


def _validate_profile_a_worktree(
    repo_root: Path,
    path: Path,
    *,
    target_common_dir: Path | None,
    work_unit_id: str,
    base_branch: str,
    metadata_path: Path,
) -> PreflightFinding:
    required = "existing clean linked worktree on base or unit branch"
    if not path.exists():
        return PreflightFinding(
            id=f"profile_a.worktree.{work_unit_id}",
            label=f"Profile A worktree {work_unit_id}",
            ok=False,
            detail=f"{path} does not exist; Woof will not create it",
            required=required,
        )
    if not path.is_dir():
        return PreflightFinding(
            id=f"profile_a.worktree.{work_unit_id}",
            label=f"Profile A worktree {work_unit_id}",
            ok=False,
            detail=f"{path} is not a directory",
            required=required,
        )

    common_dir = _git_common_dir(path)
    if common_dir is None or target_common_dir is None or common_dir != target_common_dir:
        return PreflightFinding(
            id=f"profile_a.worktree.{work_unit_id}",
            label=f"Profile A worktree {work_unit_id}",
            ok=False,
            detail=f"{path} is not a linked worktree of {repo_root}",
            required=required,
        )

    branch = _git_output(path, "symbolic-ref", "--short", "HEAD")
    expected = {base_branch, work_unit_id}
    expected.update(_metadata_unit_branches(metadata_path).get(work_unit_id, "").split())
    expected.discard("")
    if branch is None:
        return PreflightFinding(
            id=f"profile_a.worktree.{work_unit_id}",
            label=f"Profile A worktree {work_unit_id}",
            ok=False,
            detail=f"{path} is detached; expected one of: {', '.join(sorted(expected))}",
            required=required,
        )
    if branch not in expected:
        return PreflightFinding(
            id=f"profile_a.worktree.{work_unit_id}",
            label=f"Profile A worktree {work_unit_id}",
            ok=False,
            detail=f"{path} branch {branch!r} is not one of: {', '.join(sorted(expected))}",
            required=required,
        )

    status = _git_output(path, "status", "--porcelain")
    if status is None:
        return PreflightFinding(
            id=f"profile_a.worktree.{work_unit_id}",
            label=f"Profile A worktree {work_unit_id}",
            ok=False,
            detail=f"{path} git status failed",
            required=required,
        )
    if status:
        return PreflightFinding(
            id=f"profile_a.worktree.{work_unit_id}",
            label=f"Profile A worktree {work_unit_id}",
            ok=False,
            detail=f"{path} is dirty",
            required=required,
        )

    return PreflightFinding(
        id=f"profile_a.worktree.{work_unit_id}",
        label=f"Profile A worktree {work_unit_id}",
        ok=True,
        detail=f"{path} on branch {branch}",
        required=required,
    )


def _git_common_dir(path: Path) -> Path | None:
    value = _git_output(path, "rev-parse", "--git-common-dir")
    if value is None:
        return None
    common = Path(value)
    if not common.is_absolute():
        common = path / common
    try:
        return common.resolve()
    except OSError:
        return None


def _git_output(cwd: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=git_env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _check_declared_binaries(config: ProjectConfig) -> list[PreflightFinding]:
    prerequisites = config.prerequisites
    findings: list[PreflightFinding] = []
    sections = (
        ("infra", prerequisites.infra),
        ("commands", prerequisites.commands),
        ("validators", prerequisites.validators),
    )
    for section, declared in sections:
        for binary, version_spec in declared.items():
            if binary == "ajv-formats":
                continue
            findings.append(_check_binary(section, binary, str(version_spec)))

    indexing = prerequisites.indexing
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


def _check_ajv_formats(config: ProjectConfig) -> list[PreflightFinding]:
    if "ajv-formats" not in config.prerequisites.validators:
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


def _check_tracker(config: ProjectConfig) -> list[PreflightFinding]:
    tracker = config.tracker
    if tracker.kind == "local":
        return [
            PreflightFinding(
                id="tracker.kind",
                label="Issue tracker",
                ok=True,
                detail="tracker kind 'local'; filesystem-only, no remote reachability checks",
            )
        ]
    repo = tracker.repo
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


def _check_adapter_auth_markers(config: ProjectConfig) -> list[PreflightFinding]:
    """Probe credential markers for auth-sensitive harnesses in the default profile."""

    selected = config.run_profile
    findings: list[PreflightFinding] = []
    checked: set[str] = set()
    for role_name in RUN_PROFILE_ROLES:
        slot: RunProfileSlot = getattr(selected, role_name)
        try:
            profile = get_profile(slot.harness)
        except HarnessError:
            continue
        adapter = profile.name
        if adapter not in ADAPTER_AUTH_MARKERS or adapter in checked:
            continue
        checked.add(adapter)
        findings.append(
            _check_adapter_auth(
                role_name,
                adapter,
                id_prefix=f"policy.run_profile.{role_name}",
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
    prefix = id_prefix if id_prefix is not None else f"policy.run_profile.{role_name}"
    finding_id = f"{prefix}.auth"
    label_role = prefix.removeprefix("policy.run_profile.")
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


def _cartography_author_hint(project_key: str) -> str:
    return (
        f"Author {state.codebase_dir(project_key)}/ through the /woof map-codebase flow "
        "(see skills/woof/references/setup.md and skills/woof/references/map-codebase.md)."
    )


def _cartography_onboarding_install(project_key: str) -> str:
    codebase = state.codebase_dir(project_key)
    return f"""\
Follow the /woof setup onboarding path:
1. Run `woof init --project {project_key} --language <lang>` (repeat --language as needed) so the project config carries [cartography] and `scripts/refresh-cartography` is composed.
2. Author `{codebase}/TARGET-ARCHITECTURE.md` and `{codebase}/PRINCIPLES.md` during setup.
3. Run the /woof map-codebase flow to write the AS-IS cartography docs, then run `./scripts/refresh-cartography` and `woof hooks install`.
References:
- skills/woof/references/setup.md
- skills/woof/references/map-codebase.md
"""


def _check_cartography(
    project_key: str, repo_root: Path, config: ProjectConfig
) -> list[PreflightFinding]:
    """Validate the cartography artefact group in the operator home (ADR-004, ADR-017).

    The project config declares the required floor. ``none`` skips the artefact
    group. ``design`` requires the human-authored target/principles docs.
    ``lexical`` and ``structural`` additionally require the refresh script and
    current mechanical files (``tags``, ``files.txt``, ``freshness.json``). The
    docs and the mechanical layer live under the project's cartography directory
    in the operator home; only the refresh script itself is repo-owned.
    """

    cartography = config.cartography
    if cartography.floor == "none":
        return []
    if not cartography.declared:
        return [_check_cartography_onboarding(project_key, config)]

    findings: list[PreflightFinding] = [
        _check_cartography_doc(
            project_key,
            finding_id,
            filename,
            min_chars=cartography.summary_min_chars,
            stub_marker=cartography.stub_marker,
        )
        for finding_id, filename in CARTOGRAPHY_DESIGN_DOCS
    ]

    if cartography.floor in {"lexical", "structural"}:
        findings.append(_check_cartography_script(project_key, repo_root))
        findings.append(_check_cartography_mechanical(project_key))
        if cartography.languages:
            findings.append(_check_cartography_ctags())
        freshness = _check_cartography_freshness(
            project_key, floor_hours=cartography.staleness_floor_hours
        )
        if freshness is not None:
            findings.append(freshness)
    return findings


def _check_cartography_secrets(project_key: str) -> list[PreflightFinding]:
    """Scan cartography prose for leaked secrets (ADR-004 hygiene).

    The design and AS-IS layers are authored partly by mapper subagents and are
    read back into producer and reviewer prompts, so a leaked key propagates into
    every dispatched worker's context. This gate runs uncached (the preflight floor
    cache key does not track cartography doc content, so a cached pass would mask a
    freshly written secret) and fails closed on a high-signal token match. Only the
    file, line, and pattern reason are reported; the matched value is never surfaced.
    """

    codebase = state.codebase_dir(project_key)
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
                    detail=f"could not read {doc}: {exc}",
                )
            )
            continue
        hits = scan_text_for_secrets(text)
        if not hits:
            continue
        shown = hits[:10]
        locations = ", ".join(f"{doc.name}:{hit.line} ({hit.reason})" for hit in shown)
        if len(hits) > len(shown):
            locations += f", +{len(hits) - len(shown)} more"
        findings.append(
            PreflightFinding(
                id=f"cartography.secrets.{doc.stem}",
                label=f"cartography secret scan ({doc.name})",
                ok=False,
                required="no secrets in cartography docs",
                detail=(
                    f"potential secret(s) in cartography doc: {locations}. "
                    "Cartography docs are fed to dispatched workers (ADR-004); remove "
                    "the secret."
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
                detail="no high-signal secrets detected in cartography docs",
            )
        )
    return findings


def _check_cartography_onboarding(project_key: str, config: ProjectConfig) -> PreflightFinding:
    return PreflightFinding(
        id="cartography.contract",
        label="cartography contract",
        ok=False,
        required=(
            f"project config [cartography], design and mapper docs in "
            f"{state.codebase_dir(project_key)}/, scripts/refresh-cartography, "
            "and the Woof post-commit hook"
        ),
        detail=(
            f"{config.source} has no [cartography] section; Woof requires the "
            "cartography onboarding path before preflight can pass"
        ),
        install=_cartography_onboarding_install(project_key),
    )


def _check_cartography_script(project_key: str, repo_root: Path) -> PreflightFinding:
    """Check the consumer-owned ``scripts/refresh-cartography``.

    The script is the one cartography artefact that stays in the driven repo: it
    is the project's own generator, invoked by the project's post-commit hook, and
    it writes into the operator home. A missing, stale, or non-executable script
    must fail loud rather than silently no-op.
    """

    script = repo_root / "scripts" / "refresh-cartography"
    if not script.exists():
        return PreflightFinding(
            id="cartography.script",
            label="cartography script",
            ok=False,
            detail=f"{script} not found",
            install=_cartography_author_hint(project_key),
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
    project_key: str,
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

    path = state.codebase_dir(project_key) / filename
    label = f"cartography doc: {filename}"
    if not path.is_file():
        return PreflightFinding(
            id=finding_id,
            label=label,
            ok=False,
            detail=f"{path} not found",
            install=_cartography_author_hint(project_key),
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
            install=_cartography_author_hint(project_key),
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
            install=_cartography_author_hint(project_key),
        )
    return PreflightFinding(
        id=finding_id,
        label=label,
        ok=True,
        detail=f"{path} authored ({body_len} chars)",
    )


def _check_cartography_mechanical(project_key: str) -> PreflightFinding:
    """Check the mechanical layer (``tags``, ``files.txt``, ``freshness.json``)."""

    codebase = state.codebase_dir(project_key)
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
    project_key: str,
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

    path = state.codebase_dir(project_key) / CARTOGRAPHY_FRESHNESS_FILE
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


def _check_language_tools(config: ProjectConfig) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    plugin_list: str | None = None
    for language in config.prerequisites.lsp_languages:
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


def _check_tree_sitter(config: ProjectConfig) -> list[PreflightFinding]:
    tree_sitter = config.prerequisites.indexing.get("tree-sitter") or {}
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


def _check_quality_gate_commands(repo_root: Path, config: ProjectConfig) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    for gate in config.gates:
        try:
            first = shlex.split(gate.command)[0]
        except (IndexError, ValueError) as exc:
            findings.append(
                PreflightFinding(
                    id=f"quality-gates.{gate.name}",
                    label=f"quality gate command: {gate.name}",
                    ok=False,
                    detail=f"cannot parse command {gate.command!r}: {exc}",
                )
            )
            continue

        exists = _command_exists(first, repo_root)
        findings.append(
            PreflightFinding(
                id=f"quality-gates.{gate.name}",
                label=f"quality gate command: {gate.name}",
                ok=exists,
                detail=f"{first} resolves" if exists else f"{first} not found on PATH",
            )
        )
    return findings


def _check_host_prerequisites(
    repo_root: Path,
    config: ProjectConfig,
) -> list[PreflightFinding]:
    host = config.prerequisites.host
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
    config: ProjectConfig,
) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    for name, check in config.prerequisites.servers.items():
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
        f"work_unit={next_step.get('work_unit_id') or '-'} "
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
            f"  gate: open type={gate.get('type')} "
            f"work_unit={gate.get('work_unit_id') or '-'} "
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
    for role_name in ("producer", "reviewer"):
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
