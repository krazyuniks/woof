"""Read-only workflow observability surfaces."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import yaml

from woof.cli.dispatcher import (
    NODE_GROUPS,
    DispatchConfigError,
    _mcp_names,
    _role_effort,
    build_argv,
    resolve_agent_route,
    selected_model_profile,
    trusted_runtime_policy,
)
from woof.graph.dispositions import (
    critique_severity,
    read_markdown_front_matter,
    story_critique_path,
    story_disposition_path,
    validate_story_disposition,
)
from woof.graph.state import TERMINAL_STORY_STATUSES, Plan, StorySpec
from woof.graph.transitions import (
    definition_revision_requested,
    discovery_bucket_complete,
    discovery_synthesis_complete,
    epic_abandoned,
    epic_event_exists,
    interactive_brainstorm_bundle_present,
    next_ready_story,
    plan_critique_path,
    plan_gate_resolved,
)
from woof.lib.audit import load_project_audit_config
from woof.lib.audit_config import AuditConfig
from woof.paths import find_project_root

TOKEN_FIELDS = ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens")
COST_FIELDS = (
    "cost_usd",
    "input_cost_usd",
    "output_cost_usd",
    "cache_read_cost_usd",
    "cache_write_cost_usd",
    "total_cost_usd",
)
TELEMETRY_FIELDS = (
    "prompt_bytes",
    "artefact_bytes",
    "output_bytes",
    "stderr_bytes",
    "command_count",
)
SUCCESS_EXIT_TYPES = {"clean", "completed_lingering"}
VIEWS = ("status", "timeline", "gate", "audit", "all")
# Every legal StorySpec.status value, derived from the graph's Literal so the
# plan summary can never KeyError on a valid status nor silently drop a future
# one (E17 P4 added "abandoned"). Declaration order is preserved for rendering.
STORY_STATUSES: tuple[str, ...] = get_args(StorySpec.model_fields["status"].annotation)


class ObserveError(RuntimeError):
    """Raised when an observation request cannot be satisfied."""


@dataclass(frozen=True)
class JsonlRecord:
    source: str
    line: int
    payload: dict[str, Any]


def cmd_observe(args: argparse.Namespace) -> int:
    try:
        repo_root = find_project_root(Path.cwd())
        report = build_observe_report(repo_root, args.epic)
    except (FileNotFoundError, ObserveError) as exc:
        sys.stderr.write(f"woof observe: {exc}\n")
        return 2

    payload = _select_view(report, args.view)
    if args.format == "json":
        sys.stdout.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    else:
        _print_text(payload, args.view)
    return 0


def setup_observe_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    observe = sub.add_parser(
        "observe",
        help="inspect read-only workflow status, timeline, gate, and audit views",
    )
    observe.add_argument("--epic", type=int, required=True, help="epic id to inspect")
    observe.add_argument(
        "--view",
        choices=VIEWS,
        default="status",
        help="reporting view to render (default: status)",
    )
    observe.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="output format (default: text)",
    )
    observe.set_defaults(func=cmd_observe)


def build_observe_report(repo_root: Path, epic_id: int) -> dict[str, Any]:
    directory = repo_root / ".woof" / "epics" / f"E{epic_id}"
    if not directory.is_dir():
        raise ObserveError(f"{_display_path(repo_root, directory)} not found")

    current_epic = _current_epic_summary(repo_root, selected_epic_id=epic_id)
    epic_records, epic_warnings = _read_jsonl(directory / "epic.jsonl", "epic")
    dispatch_records, dispatch_warnings = _read_jsonl(directory / "dispatch.jsonl", "dispatch")
    gate = _gate_summary(repo_root, directory)
    plan_summary, plan = _plan_summary(repo_root, directory)
    timeline = _timeline([*epic_records, *dispatch_records])
    dispatch_events = [record.payload for record in dispatch_records]
    audit_pointers = _audit_pointers(repo_root, directory, dispatch_events)
    returned_events = [
        event for event in dispatch_events if event.get("event") == "subprocess_returned"
    ]
    usage = _usage_summary(returned_events)
    telemetry = _telemetry_summary(returned_events)
    audit = _audit_summary(repo_root, directory, dispatch_events, usage, telemetry, audit_pointers)
    checks = _check_summary(repo_root, directory)
    dispatch_routes = _dispatch_routes_summary(repo_root)
    runtime_policy = trusted_runtime_policy()
    status = _status_summary(
        repo_root,
        epic_id,
        directory,
        plan=plan,
        plan_summary=plan_summary,
        gate=gate,
        timeline=timeline,
        usage=usage,
        telemetry=telemetry,
        current_epic=current_epic,
        checks=checks,
        dispatch_routes=dispatch_routes,
        runtime_policy=runtime_policy,
        audit_pointers=audit_pointers,
    )

    return {
        "epic_id": epic_id,
        "epic_dir": _display_path(repo_root, directory),
        "current_epic": current_epic,
        "status": status,
        "timeline": timeline,
        "gate": gate,
        "audit": audit,
        "checks": checks,
        "dispatch_routes": dispatch_routes,
        "runtime_policy": runtime_policy,
        "warnings": [*epic_warnings, *dispatch_warnings],
    }


def build_operator_state_summary(repo_root: Path) -> dict[str, Any]:
    """Build the preflight operator-state summary without mutating workflow state."""

    current_epic = _current_epic_summary(repo_root)
    dispatch_routes = _dispatch_routes_summary(repo_root)
    runtime_policy = trusted_runtime_policy()
    selected = current_epic.get("epic_id")
    summary: dict[str, Any] = {
        "current_epic": current_epic,
        "dispatch_routes": dispatch_routes,
        "runtime_policy": runtime_policy,
        "epic": None,
    }
    if isinstance(selected, int):
        try:
            report = build_observe_report(repo_root, selected)
        except ObserveError as exc:
            summary["epic"] = {
                "epic_id": selected,
                "exists": False,
                "error": str(exc),
            }
        else:
            summary["epic"] = {
                "epic_id": selected,
                "exists": True,
                "epic_dir": report["epic_dir"],
                "next": report["status"]["next"],
                "next_action": report["status"]["next_action"],
                "gate": report["status"]["gate"],
                "checks": report["status"]["checks"],
                "audit_pointers": report["status"]["audit_pointers"],
                "latest_event": report["status"]["latest_event"],
                "warnings": report["warnings"],
            }
    return summary


def _select_view(report: dict[str, Any], view: str) -> dict[str, Any] | list[dict[str, Any]]:
    if view == "all":
        return report
    return report[view]


def _status_summary(
    repo_root: Path,
    epic_id: int,
    directory: Path,
    *,
    plan: Plan | None,
    plan_summary: dict[str, Any],
    gate: dict[str, Any],
    timeline: list[dict[str, Any]],
    usage: dict[str, Any],
    telemetry: dict[str, Any],
    current_epic: dict[str, Any],
    checks: dict[str, Any],
    dispatch_routes: dict[str, Any],
    runtime_policy: dict[str, Any],
    audit_pointers: dict[str, Any],
) -> dict[str, Any]:
    next_step = _derive_next_step(repo_root, epic_id, directory, plan, gate)
    return {
        "epic_id": epic_id,
        "epic_dir": _display_path(repo_root, directory),
        "current_epic": current_epic,
        "next": next_step,
        "next_action": _next_action(epic_id, next_step, gate),
        "gate": {
            "open": gate["open"],
            "type": gate.get("type"),
            "story_id": gate.get("story_id"),
            "triggered_by": gate.get("triggered_by", []),
            "cause": _gate_cause(gate),
            "path": gate.get("path"),
        },
        "plan": plan_summary,
        "checks": checks,
        "dispatch": _dispatch_counts(timeline),
        "dispatch_routes": dispatch_routes,
        "runtime_policy": runtime_policy,
        "audit_pointers": audit_pointers,
        "usage": usage,
        "telemetry": telemetry,
        "latest_event": timeline[-1] if timeline else None,
    }


def _derive_next_step(
    repo_root: Path,
    epic_id: int,
    directory: Path,
    plan: Plan | None,
    gate: dict[str, Any],
) -> dict[str, Any]:
    # Mirror transitions.next_node: an abandoned epic is unconditionally terminal
    # and short-circuits before any gate or plan read, so observe agrees with the
    # graph instead of reporting a lingering gate/plan as the next step (E17 P4 / D-AB).
    if epic_abandoned(repo_root, epic_id):
        return {"node": "epic_abandoned", "story_id": None}
    if gate["open"]:
        return {"node": "human_review", "story_id": None, "reason": "gate_open"}

    plan_path = directory / "plan.json"
    if not plan_path.exists():
        if (directory / "EPIC.md").exists():
            if epic_event_exists(
                repo_root, epic_id, event="definition_closed"
            ) and not definition_revision_requested(repo_root, epic_id):
                return {"node": "breakdown_planning", "story_id": None}
            return {"node": "epic_definition", "story_id": None}
        if discovery_synthesis_complete(repo_root, epic_id):
            return {"node": "epic_definition", "story_id": None}
        if (directory / "spark.md").exists():
            if interactive_brainstorm_bundle_present(repo_root, epic_id):
                return {"node": "discovery_synthesis", "story_id": None}
            for bucket in ("research", "thinking", "ideate"):
                if not discovery_bucket_complete(repo_root, epic_id, bucket):
                    return {"node": f"discovery_{bucket}", "story_id": None}
            return {"node": "discovery_synthesis", "story_id": None}
        return {
            "node": "incomplete_stage_state",
            "story_id": None,
            "reason": f"required planning artefact missing: {_display_path(repo_root, plan_path)}",
        }

    if plan is None:
        return {
            "node": "incomplete_stage_state",
            "story_id": None,
            "reason": f"required planning artefact is malformed: {_display_path(repo_root, plan_path)}",
        }

    in_progress = next((story for story in plan.stories if story.status == "in_progress"), None)
    if all(story.status in TERMINAL_STORY_STATUSES for story in plan.stories):
        if (directory / "executor_result.json").exists() and (
            directory / "check-result.json"
        ).exists():
            return {"node": "commit", "story_id": None, "reason": "commit_resume_candidate"}
        return {"node": "epic_complete", "story_id": None}

    if in_progress is None:
        critique_path = plan_critique_path(repo_root, epic_id)
        if (directory / "EPIC.md").exists() and not critique_path.exists():
            return {"node": "plan_critique", "story_id": None}
        if epic_event_exists(repo_root, epic_id, event="breakdown_planned"):
            if not epic_event_exists(repo_root, epic_id, event="plan_critiqued"):
                return {"node": "plan_critique", "story_id": None}
            if not plan_gate_resolved(repo_root, epic_id):
                return {"node": "plan_gate_open", "story_id": None}
        if critique_path.exists() and not plan_gate_resolved(repo_root, epic_id):
            return {"node": "plan_gate_open", "story_id": None}
        ready = next_ready_story(plan)
        if ready is not None:
            return {"node": "executor_dispatch", "story_id": ready.id}
        return {
            "node": "incomplete_stage_state",
            "story_id": None,
            "reason": "pending stories exist, but no story has satisfied dependencies",
        }

    story_id = in_progress.id
    result_path = directory / "executor_result.json"
    result = _load_json(result_path)
    if result is None:
        return {"node": "gate_open", "story_id": story_id, "reason": "missing_executor_result"}

    outcome = result.get("outcome")
    if outcome in {"aborted_with_position", "empty_diff"}:
        return {"node": "gate_open", "story_id": story_id, "reason": str(outcome)}
    if outcome != "staged_for_verification":
        return {"node": "gate_open", "story_id": story_id, "reason": "invalid_executor_result"}

    critique_path = story_critique_path(directory, story_id)
    if not critique_path.exists():
        return {"node": "critique_dispatch", "story_id": story_id}
    try:
        critique_front = read_markdown_front_matter(critique_path).front
    except (FileNotFoundError, ValueError):
        return {"node": "review_disposition", "story_id": story_id, "reason": "malformed_critique"}
    if critique_severity(critique_front) == "blocker":
        return {"node": "review_disposition", "story_id": story_id, "reason": "reviewer_blocker"}
    if not story_disposition_path(directory, story_id).exists():
        return {"node": "review_disposition", "story_id": story_id, "reason": "missing_disposition"}
    disposition = validate_story_disposition(directory, epic_id, story_id)
    if not disposition.ok:
        return {"node": "review_disposition", "story_id": story_id, "reason": "invalid_disposition"}

    check_result = _load_json(directory / "check-result.json")
    if check_result is None:
        return {"node": "verification", "story_id": story_id}
    if not check_result.get("ok", False):
        return {"node": "gate_open", "story_id": story_id, "reason": "failed_verification"}
    return {"node": "commit", "story_id": story_id}


def _next_action(epic_id: int, next_step: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    if gate["open"]:
        return {
            "action": "resolve_gate",
            "command": f"woof wf --epic {epic_id} --resolve <decision>",
            "inspect_command": f"woof observe --epic {epic_id} --view gate",
            "reason": _gate_cause(gate),
            "description": "Inspect the gate, then resolve it with a structured decision.",
        }
    node = str(next_step.get("node") or "")
    if node in {"epic_complete", "epic_abandoned"}:
        return {
            "action": "none",
            "command": None,
            "reason": node,
            "description": "No workflow action remains for this epic.",
        }
    return {
        "action": "run_graph",
        "command": f"woof wf --epic {epic_id}",
        "reason": next_step.get("reason") or node,
        "description": f"Run the graph to continue at {node}.",
    }


def _current_epic_summary(
    repo_root: Path,
    *,
    selected_epic_id: int | None = None,
) -> dict[str, Any]:
    marker = repo_root / ".woof" / ".current-epic"
    summary: dict[str, Any] = {
        "path": _display_path(repo_root, marker),
        "exists": marker.is_file(),
        "value": None,
        "epic_id": None,
        "epic_dir": None,
        "epic_dir_exists": False,
        "selected": False,
        "valid": False,
    }
    if not marker.is_file():
        return summary
    value = marker.read_text(encoding="utf-8").strip()
    summary["value"] = value
    if not value.startswith("E") or not value[1:].isdigit():
        summary["error"] = "current epic marker must contain E<N>"
        return summary
    epic_id = int(value[1:])
    epic_dir = repo_root / ".woof" / "epics" / value
    summary.update(
        {
            "epic_id": epic_id,
            "epic_dir": _display_path(repo_root, epic_dir),
            "epic_dir_exists": epic_dir.is_dir(),
            "selected": selected_epic_id == epic_id if selected_epic_id is not None else True,
            "valid": True,
        }
    )
    return summary


def _dispatch_routes_summary(repo_root: Path) -> dict[str, Any]:
    agents_path = repo_root / ".woof" / "agents.toml"
    summary: dict[str, Any] = {
        "path": _display_path(repo_root, agents_path),
        "exists": agents_path.is_file(),
        "roles": {},
        "timeout_min": None,
        "model_profile": None,
    }
    if not agents_path.is_file():
        summary["error"] = f"{_display_path(repo_root, agents_path)} not found"
        for role_name in ("primary", "reviewer"):
            summary["roles"][role_name] = {
                "ok": False,
                "role": role_name,
                "errors": ["agents.toml not found"],
            }
        return summary

    loaded = _load_toml(agents_path)
    if not isinstance(loaded, dict):
        summary["error"] = loaded
        for role_name in ("primary", "reviewer"):
            summary["roles"][role_name] = {"ok": False, "role": role_name, "errors": [loaded]}
        return summary

    timeout_min = 30
    timeout_error: str | None = None
    try:
        timeout_min = int((loaded.get("timeouts") or {}).get("default_minutes", 30))
    except (TypeError, ValueError):
        timeout_error = "timeouts.default_minutes must be an integer"
    summary["timeout_min"] = timeout_min
    summary["model_profile"] = selected_model_profile(loaded)
    roles = loaded.get("roles") or {}
    if not isinstance(roles, dict):
        summary["error"] = "roles table is not an object"
        for role_name in ("primary", "reviewer"):
            summary["roles"][role_name] = {
                "ok": False,
                "role": role_name,
                "errors": ["roles table is not an object"],
            }
        return summary
    mcp_servers = loaded.get("mcp_servers") or {}
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
    for role_name in ("primary", "reviewer"):
        route_summary = _dispatch_route_summary(
            role_name,
            loaded,
            mcp_servers,
            timeout_min,
        )
        if timeout_error:
            route_summary["ok"] = False
            route_summary["errors"].append(timeout_error)
        summary["roles"][role_name] = route_summary
    routes_by_group: dict[str, Any] = {}
    for group in sorted(NODE_GROUPS):
        group_routes: dict[str, Any] = {}
        for role_name in ("primary", "reviewer"):
            route_summary = _dispatch_route_summary(
                role_name,
                loaded,
                mcp_servers,
                timeout_min,
                route_key=group,
            )
            if timeout_error:
                route_summary["ok"] = False
                route_summary["errors"].append(timeout_error)
            group_routes[role_name] = route_summary
        routes_by_group[group] = group_routes
    summary["routes"] = routes_by_group
    return summary


def _dispatch_route_summary(
    role_name: str,
    agents: dict[str, Any],
    mcp_servers: dict[str, Any],
    timeout_min: int,
    *,
    route_key: str | None = None,
) -> dict[str, Any]:
    try:
        route = resolve_agent_route(agents, role_name, route_key=route_key)
    except DispatchConfigError as exc:
        return {"ok": False, "role": role_name, "errors": [str(exc)]}

    errors: list[str] = []
    effort: str | None = None
    mcp_names: list[str] = []
    if route.adapter == "in-session":
        errors.append("dispatchable role resolves to in-session")
    try:
        effort = _role_effort(route.adapter, route.config)
    except DispatchConfigError as exc:
        errors.append(str(exc))
    if effort is None:
        errors.append("effort is not declared")
    try:
        mcp_names = _mcp_names(route.config)
        build_argv(route.adapter, route.config, "observe route probe", mcp_servers=mcp_servers)
    except DispatchConfigError as exc:
        errors.append(str(exc))

    model = route.config.get("model")
    if not model:
        errors.append("model is not declared")

    return {
        "ok": not errors,
        "role": role_name,
        "config_role": route.config_role,
        "model_profile": route.model_profile,
        "profile_role": route.profile_role,
        "adapter": route.adapter,
        "model": model,
        "effort": effort,
        "mcp": mcp_names,
        "flags": route.config.get("flags") or [],
        "timeout_min": timeout_min,
        "prompt_transport": "stdin",
        "runtime_policy": trusted_runtime_policy(),
        "errors": errors,
    }


def _check_summary(repo_root: Path, directory: Path) -> dict[str, Any]:
    path = directory / "check-result.json"
    if not path.exists():
        return {
            "exists": False,
            "valid": False,
            "path": _display_path(repo_root, path),
        }

    payload, error = _load_json_object(path)
    if payload is None:
        return {
            "exists": True,
            "valid": False,
            "path": _display_path(repo_root, path),
            "error": error,
        }

    checks_raw = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    checks = [_check_entry_summary(check) for check in checks_raw if isinstance(check, dict)]
    failed_checks = [check for check in checks if not check["ok"]]
    return {
        "exists": True,
        "valid": True,
        "path": _display_path(repo_root, path),
        "ok": bool(payload.get("ok", False)),
        "stage": payload.get("stage"),
        "story_id": payload.get("story_id"),
        "triggered_by": [str(item) for item in payload.get("triggered_by") or []],
        "total": len(checks),
        "failed": len(failed_checks),
        "checks": checks,
        "failed_checks": failed_checks,
    }


def _check_entry_summary(check: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": str(check.get("id") or ""),
        "ok": bool(check.get("ok", False)),
        "severity": check.get("severity"),
        "summary": str(check.get("summary") or ""),
    }
    for key in ("evidence", "paths", "command", "exit_code"):
        if check.get(key) is not None:
            item[key] = check[key]
    return item


def _plan_summary(repo_root: Path, directory: Path) -> tuple[dict[str, Any], Plan | None]:
    path = directory / "plan.json"
    if not path.exists():
        return {"exists": False, "valid": False, "path": _display_path(repo_root, path)}, None
    try:
        plan = Plan.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {
            "exists": True,
            "valid": False,
            "path": _display_path(repo_root, path),
            "error": str(exc),
        }, None
    counts = {status: 0 for status in STORY_STATUSES}
    stories = []
    for story in plan.stories:
        counts[story.status] += 1
        stories.append(
            {
                "id": story.id,
                "title": story.title,
                "status": story.status,
                "depends_on": story.depends_on,
                "satisfies": story.satisfies,
            }
        )
    return {
        "exists": True,
        "valid": True,
        "path": _display_path(repo_root, path),
        "goal": plan.goal,
        "story_counts": counts,
        "stories": stories,
    }, plan


def _gate_summary(repo_root: Path, directory: Path) -> dict[str, Any]:
    gate_path = directory / "gate.md"
    if not gate_path.exists():
        return {"open": False, "path": _display_path(repo_root, gate_path), "cause": "none"}
    text = gate_path.read_text(encoding="utf-8")
    front: dict[str, Any] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            loaded = yaml.safe_load(text[4:end]) or {}
            if isinstance(loaded, dict):
                front = loaded
            body = text[end + 5 :].strip()
    triggered_by = front.get("triggered_by") if isinstance(front.get("triggered_by"), list) else []
    return {
        "open": True,
        "path": _display_path(repo_root, gate_path),
        "type": front.get("type"),
        "stage": front.get("stage"),
        "story_id": front.get("story_id"),
        "triggered_by": [str(item) for item in triggered_by],
        "cause": ", ".join(str(item) for item in triggered_by) if triggered_by else "unspecified",
        "timestamp": front.get("timestamp"),
        "sections": _markdown_sections(body),
    }


def _gate_cause(gate: dict[str, Any]) -> str:
    if not gate.get("open"):
        return "none"
    triggered_by = gate.get("triggered_by") or []
    return ", ".join(str(item) for item in triggered_by) if triggered_by else "unspecified"


def _audit_summary(
    repo_root: Path,
    directory: Path,
    dispatch_events: list[dict[str, Any]],
    usage: dict[str, Any],
    telemetry: dict[str, Any],
    audit_pointers: dict[str, Any],
) -> dict[str, Any]:
    audit_dir = directory / "audit"
    config, config_error = _audit_config(repo_root)
    files = _audit_files(repo_root, audit_dir)
    commit_bound = [item for item in files if not item["raw_overflow"]]
    raw = [item for item in files if item["raw_overflow"]]
    dispatch_returned = [
        _dispatch_return_summary(event)
        for event in dispatch_events
        if event.get("event") == "subprocess_returned"
    ]
    summary = {
        "audit_dir": _display_path(repo_root, audit_dir),
        "exists": audit_dir.is_dir(),
        "config": {
            "enabled": config.enabled,
            "max_bytes": config.max_bytes,
            "redact_pattern_count": len(config.redact_patterns),
        },
        "config_error": config_error,
        "commit_bound_file_count": len(commit_bound),
        "commit_bound_bytes": sum(int(item["bytes"]) for item in commit_bound),
        "raw_overflow_file_count": len(raw),
        "raw_overflow_bytes": sum(int(item["bytes"]) for item in raw),
        "redacted_file_count": sum(1 for item in commit_bound if item["redacted_markers"]),
        "truncated_file_count": sum(1 for item in commit_bound if item["truncated_footer"]),
        "raw_overflow_path": _display_path(repo_root, audit_dir / "raw"),
        "retention_archive": {
            "implemented": False,
            "mode": "not_implemented",
            "note": "Raw overflow remains local under audit/raw; Woof does not archive or expire it.",
        },
        "pointers": audit_pointers,
        "files": files,
        "dispatch": {
            "spawned": sum(
                1 for event in dispatch_events if event.get("event") == "subprocess_spawned"
            ),
            "returned": len(dispatch_returned),
            "successful": _successful_return_count(dispatch_events),
            "failed": _failed_return_count(dispatch_events),
            "killed": _failed_kill_count(dispatch_events),
            "returned_events": dispatch_returned,
        },
        "usage": usage,
        "telemetry": telemetry,
    }
    return summary


def _audit_pointers(
    repo_root: Path,
    directory: Path,
    dispatch_events: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_codex = _latest_event_field(dispatch_events, "codex_audit_path")
    latest_claude = _latest_event_field(dispatch_events, "claude_transcript_path")
    return {
        "epic_jsonl": _display_path(repo_root, directory / "epic.jsonl"),
        "dispatch_jsonl": _display_path(repo_root, directory / "dispatch.jsonl"),
        "audit_dir": _display_path(repo_root, directory / "audit"),
        "raw_overflow_dir": _display_path(repo_root, directory / "audit" / "raw"),
        "latest_codex_audit_path": latest_codex,
        "latest_claude_transcript_path": latest_claude,
    }


def _latest_event_field(events: list[dict[str, Any]], field: str) -> Any:
    for event in reversed(events):
        value = event.get(field)
        if value:
            return value
    return None


def _audit_config(repo_root: Path) -> tuple[AuditConfig, str | None]:
    try:
        return load_project_audit_config(repo_root), None
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        return AuditConfig(), str(exc)


def _audit_files(repo_root: Path, audit_dir: Path) -> list[dict[str, Any]]:
    if not audit_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(item for item in audit_dir.rglob("*") if item.is_file()):
        rel_parts = path.relative_to(audit_dir).parts
        text = path.read_text(encoding="utf-8", errors="replace")
        items.append(
            {
                "path": _display_path(repo_root, path),
                "bytes": path.stat().st_size,
                "raw_overflow": "raw" in rel_parts,
                "redacted_markers": "[REDACTED:" in text,
                "truncated_footer": "... [truncated, full output at " in text,
            }
        )
    return items


def _timeline(records: list[JsonlRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        payload = record.payload
        item: dict[str, Any] = {
            "at": payload.get("at"),
            "source": record.source,
            "line": record.line,
            "event": payload.get("event"),
        }
        for key in (
            "epic_id",
            "story_id",
            "role",
            "adapter",
            "model",
            "effort",
            "gate_type",
            "decision",
            "triggered_by",
            "exit_code",
            "exit_type",
            "duration_ms",
            "pid",
            "reason",
            "codex_audit_path",
            "claude_transcript_path",
            "artefacts_loaded",
            "prompt_bytes",
            "artefact_bytes",
            "output_bytes",
            "stderr_bytes",
            "command_count",
        ):
            if key in payload:
                item[key] = payload[key]
        tokens = _number_fields(payload, TOKEN_FIELDS)
        if tokens:
            item["tokens"] = tokens
        costs = _number_fields(payload, COST_FIELDS)
        if costs:
            item["cost"] = costs
        events.append(item)
    events.sort(
        key=lambda item: (str(item.get("at") or ""), str(item["source"]), int(item["line"]))
    )
    return events


def _usage_summary(events: Any) -> dict[str, Any]:
    token_totals = {field: 0 for field in TOKEN_FIELDS}
    cost_totals: dict[str, float] = {}
    token_events = 0
    cost_events = 0
    for event in events:
        tokens = _number_fields(event, TOKEN_FIELDS)
        if tokens:
            token_events += 1
            for field, value in tokens.items():
                token_totals[field] += int(value)
        costs = _number_fields(event, COST_FIELDS)
        if costs:
            cost_events += 1
            for field, value in costs.items():
                cost_totals[field] = cost_totals.get(field, 0.0) + float(value)

    usage: dict[str, Any] = {
        "token_events": token_events,
        "tokens": token_totals if token_events else {},
        "cost_events": cost_events,
        "cost": cost_totals if cost_events else {},
    }
    return usage


def _telemetry_summary(events: Any) -> dict[str, Any]:
    totals = {field: 0 for field in TELEMETRY_FIELDS}
    telemetry_events = 0
    for event in events:
        values = _number_fields(event, TELEMETRY_FIELDS)
        if not values:
            continue
        telemetry_events += 1
        for field, value in values.items():
            totals[field] += int(value)
    return {
        "events": telemetry_events,
        "totals": totals if telemetry_events else {},
    }


def _dispatch_counts(timeline: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "spawned": sum(1 for item in timeline if item["event"] == "subprocess_spawned"),
        "returned": sum(1 for item in timeline if item["event"] == "subprocess_returned"),
        "successful": _successful_return_count(timeline),
        "failed": _failed_return_count(timeline),
        "killed": _failed_kill_count(timeline),
    }


def _dispatch_return_summary(event: dict[str, Any]) -> dict[str, Any]:
    summary = {
        key: event[key]
        for key in (
            "at",
            "role",
            "story_id",
            "adapter",
            "model",
            "effort",
            "exit_code",
            "exit_type",
            "duration_ms",
            "codex_audit_path",
            "claude_transcript_path",
            "artefacts_loaded",
            "prompt_bytes",
            "artefact_bytes",
            "output_bytes",
            "stderr_bytes",
            "command_count",
        )
        if key in event
    }
    tokens = _number_fields(event, TOKEN_FIELDS)
    if tokens:
        summary["tokens"] = tokens
    costs = _number_fields(event, COST_FIELDS)
    if costs:
        summary["cost"] = costs
    return summary


def _successful_return_count(events: list[dict[str, Any]]) -> int:
    count = 0
    for event in events:
        if event.get("event") != "subprocess_returned":
            continue
        exit_type = event.get("exit_type")
        if exit_type in SUCCESS_EXIT_TYPES or (exit_type is None and event.get("exit_code") == 0):
            count += 1
    return count


def _failed_return_count(events: list[dict[str, Any]]) -> int:
    count = 0
    for event in events:
        if event.get("event") != "subprocess_returned":
            continue
        exit_type = event.get("exit_type")
        if exit_type in SUCCESS_EXIT_TYPES or (exit_type is None and event.get("exit_code") == 0):
            continue
        count += 1
    return count


def _failed_kill_count(events: list[dict[str, Any]]) -> int:
    return sum(
        1
        for event in events
        if event.get("event") == "subprocess_killed"
        and event.get("exit_type") != "completed_lingering"
    )


def _read_jsonl(path: Path, source: str) -> tuple[list[JsonlRecord], list[str]]:
    if not path.exists():
        return [], []
    records: list[JsonlRecord] = []
    warnings: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            warnings.append(f"{source}:{lineno}: invalid JSON: {exc}")
            continue
        if not isinstance(payload, dict):
            warnings.append(f"{source}:{lineno}: JSONL event is not an object")
            continue
        records.append(JsonlRecord(source=source, line=lineno, payload=payload))
    return records, warnings


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_toml(path: Path) -> dict[str, Any] | str:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return f"{_display_path(path.parent, path)} not found"
    except tomllib.TOMLDecodeError as exc:
        return f"{path}: TOML parse error: {exc}"


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "file not found"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "JSON root is not an object"
    return payload, None


def _number_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, int | float]:
    values: dict[str, int | float] = {}
    for field in fields:
        value = payload.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            values[field] = value
    return values


def _markdown_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in body.splitlines():
        if raw.startswith("## "):
            current = raw[3:].strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw)
    return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _print_text(payload: dict[str, Any] | list[dict[str, Any]], view: str) -> None:
    if view == "all":
        assert isinstance(payload, dict)
        _print_status(payload["status"])
        print()
        _print_gate(payload["gate"])
        print()
        _print_audit(payload["audit"])
        print()
        _print_timeline(payload["timeline"])
    elif view == "status":
        assert isinstance(payload, dict)
        _print_status(payload)
    elif view == "gate":
        assert isinstance(payload, dict)
        _print_gate(payload)
    elif view == "audit":
        assert isinstance(payload, dict)
        _print_audit(payload)
    elif view == "timeline":
        assert isinstance(payload, list)
        _print_timeline(payload)


def _print_status(status: dict[str, Any]) -> None:
    next_step = status["next"]
    print(f"E{status['epic_id']} status")
    print(f"epic_dir: {status['epic_dir']}")
    _print_current_epic(status["current_epic"])
    print(f"runtime_policy: {status['runtime_policy']['mode']}")
    story = f" story={next_step['story_id']}" if next_step.get("story_id") else ""
    reason = f" reason={next_step['reason']}" if next_step.get("reason") else ""
    print(f"next: {next_step['node']}{story}{reason}")
    action = status["next_action"]
    command = action.get("command") or "-"
    print(f"next_action: {action['action']} command={command} reason={action.get('reason')}")
    gate = status["gate"]
    if gate["open"]:
        print(
            "gate: open "
            f"type={gate.get('type')} story={gate.get('story_id') or '-'} "
            f"cause={gate.get('cause')}"
        )
    else:
        print("gate: closed")
    plan = status["plan"]
    if plan["valid"]:
        counts = plan["story_counts"]
        print(
            "stories: "
            f"pending={counts['pending']} in_progress={counts['in_progress']} "
            f"done={counts['done']} abandoned={counts['abandoned']}"
        )
    else:
        print(f"stories: unavailable plan_valid={plan['valid']}")
    _print_check_summary(status["checks"])
    _print_audit_pointers(status["audit_pointers"])
    _print_dispatch_routes(status["dispatch_routes"])
    _print_usage(status["usage"])
    _print_telemetry(status["telemetry"])


def _print_gate(gate: dict[str, Any]) -> None:
    if not gate["open"]:
        print(f"gate: closed ({gate['path']})")
        return
    print(f"gate: open at {gate['path']}")
    print(
        f"type: {gate.get('type')} stage: {gate.get('stage')} story: {gate.get('story_id') or '-'}"
    )
    print(f"triggered_by: {', '.join(gate.get('triggered_by') or [])}")
    if gate.get("timestamp"):
        print(f"timestamp: {gate['timestamp']}")
    for heading, text in gate.get("sections", {}).items():
        print(f"\n## {heading}")
        if text:
            print(text)


def _print_audit(audit: dict[str, Any]) -> None:
    print(f"audit_dir: {audit['audit_dir']}")
    _print_audit_pointers(audit["pointers"])
    print(
        "files: "
        f"commit_bound={audit['commit_bound_file_count']} "
        f"raw_overflow={audit['raw_overflow_file_count']} "
        f"redacted={audit['redacted_file_count']} truncated={audit['truncated_file_count']}"
    )
    print(f"raw_overflow_path: {audit['raw_overflow_path']}")
    print("retention_archive: not implemented")
    _print_usage(audit["usage"])
    _print_telemetry(audit["telemetry"])
    returned = audit["dispatch"]["returned"]
    spawned = audit["dispatch"]["spawned"]
    killed = audit["dispatch"]["killed"]
    successful = audit["dispatch"].get("successful", 0)
    failed = audit["dispatch"].get("failed", 0)
    print(
        "dispatch: "
        f"spawned={spawned} returned={returned} successful={successful} "
        f"failed={failed} killed={killed}"
    )


def _print_current_epic(current: dict[str, Any]) -> None:
    if not current["exists"]:
        print(f"current_epic: none path={current['path']}")
        return
    selected = "true" if current.get("selected") else "false"
    valid = "true" if current.get("valid") else "false"
    value = current.get("value") or "-"
    exists = "true" if current.get("epic_dir_exists") else "false"
    print(f"current_epic: {value} selected={selected} valid={valid} epic_dir_exists={exists}")


def _print_check_summary(checks: dict[str, Any]) -> None:
    if not checks["exists"]:
        print(f"checks: unavailable path={checks['path']}")
        return
    if not checks["valid"]:
        print(f"checks: malformed path={checks['path']} error={checks.get('error')}")
        return
    state = "OK" if checks["ok"] else "FAIL"
    print(
        "checks: "
        f"{state} stage={checks.get('stage')} story={checks.get('story_id') or '-'} "
        f"total={checks['total']} failed={checks['failed']} "
        f"triggered_by={','.join(checks.get('triggered_by') or []) or '-'}"
    )
    for check in checks["failed_checks"]:
        print(f"  FAIL {check['id']}: {check['summary']}")


def _print_audit_pointers(pointers: dict[str, Any]) -> None:
    print(
        "audit_pointers: "
        f"epic_jsonl={pointers['epic_jsonl']} "
        f"dispatch_jsonl={pointers['dispatch_jsonl']} "
        f"audit_dir={pointers['audit_dir']}"
    )
    if pointers.get("latest_codex_audit_path"):
        print(f"latest_codex_audit_path: {pointers['latest_codex_audit_path']}")
    if pointers.get("latest_claude_transcript_path"):
        print(f"latest_claude_transcript_path: {pointers['latest_claude_transcript_path']}")


def _print_dispatch_routes(routes: dict[str, Any]) -> None:
    print(f"dispatch_routes: {routes['path']}")
    if routes.get("model_profile"):
        print(f"model_profile: {routes['model_profile']}")
    for role_name in ("primary", "reviewer"):
        route = (routes.get("roles") or {}).get(role_name) or {}
        if route.get("ok"):
            mcp = ",".join(route.get("mcp") or []) or "-"
            profile = route.get("model_profile") or "-"
            print(
                f"  {role_name}: adapter={route.get('adapter')} "
                f"model={route.get('model')} effort={route.get('effort')} "
                f"profile={profile} config_role={route.get('config_role')} mcp={mcp} "
                f"timeout={route.get('timeout_min')}m"
            )
        else:
            errors = "; ".join(str(error) for error in route.get("errors") or [])
            print(f"  {role_name}: unavailable {errors}")
    for group, group_routes in sorted((routes.get("routes") or {}).items()):
        for role_name in ("primary", "reviewer"):
            route = (group_routes or {}).get(role_name) or {}
            if route.get("ok"):
                print(
                    f"  {group}/{role_name}: adapter={route.get('adapter')} "
                    f"model={route.get('model')} effort={route.get('effort')}"
                )
            else:
                errors = "; ".join(str(error) for error in route.get("errors") or [])
                print(f"  {group}/{role_name}: unavailable {errors}")


def _print_timeline(events: list[dict[str, Any]]) -> None:
    if not events:
        print("timeline: no events")
        return
    print("timeline:")
    for event in events:
        parts = [
            str(event.get("at") or "-"),
            str(event["source"]),
            str(event.get("event") or "-"),
        ]
        for key in (
            "story_id",
            "role",
            "adapter",
            "gate_type",
            "decision",
            "exit_code",
            "exit_type",
        ):
            if key in event:
                parts.append(f"{key}={event[key]}")
        tokens = event.get("tokens")
        if tokens:
            parts.append(_usage_values(tokens))
        costs = event.get("cost")
        if costs:
            parts.append("cost=" + ",".join(f"{key}={value}" for key, value in costs.items()))
        print("  " + " ".join(parts))


def _print_usage(usage: dict[str, Any]) -> None:
    if usage["token_events"]:
        print(f"tokens: {_usage_values(usage['tokens'])} events={usage['token_events']}")
    else:
        print("tokens: unavailable")
    if usage["cost_events"]:
        print(
            "cost: "
            + ",".join(f"{key}={value}" for key, value in usage["cost"].items())
            + f" events={usage['cost_events']}"
        )
    else:
        print("cost: unavailable")


def _print_telemetry(telemetry: dict[str, Any]) -> None:
    if telemetry["events"]:
        print("telemetry: " + _usage_values(telemetry["totals"]) + f" events={telemetry['events']}")
    else:
        print("telemetry: unavailable")


def _usage_values(values: dict[str, int | float]) -> str:
    return ",".join(f"{key}={values[key]}" for key in values)
