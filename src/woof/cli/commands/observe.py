"""Read-only workflow observability surfaces."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from woof.graph.dispositions import (
    critique_severity,
    read_markdown_front_matter,
    story_critique_path,
    story_disposition_path,
    validate_story_disposition,
)
from woof.graph.state import Plan
from woof.graph.transitions import (
    definition_revision_requested,
    discovery_bucket_complete,
    discovery_synthesis_complete,
    epic_event_exists,
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
VIEWS = ("status", "timeline", "gate", "audit", "all")


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

    epic_records, epic_warnings = _read_jsonl(directory / "epic.jsonl", "epic")
    dispatch_records, dispatch_warnings = _read_jsonl(directory / "dispatch.jsonl", "dispatch")
    gate = _gate_summary(repo_root, directory)
    plan_summary, plan = _plan_summary(repo_root, directory)
    timeline = _timeline([*epic_records, *dispatch_records])
    dispatch_events = [record.payload for record in dispatch_records]
    usage = _usage_summary(
        event for event in dispatch_events if event.get("event") == "subprocess_returned"
    )
    audit = _audit_summary(repo_root, directory, dispatch_events, usage)
    status = _status_summary(
        repo_root,
        epic_id,
        directory,
        plan=plan,
        plan_summary=plan_summary,
        gate=gate,
        timeline=timeline,
        usage=usage,
    )

    return {
        "epic_id": epic_id,
        "epic_dir": _display_path(repo_root, directory),
        "status": status,
        "timeline": timeline,
        "gate": gate,
        "audit": audit,
        "warnings": [*epic_warnings, *dispatch_warnings],
    }


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
) -> dict[str, Any]:
    return {
        "epic_id": epic_id,
        "epic_dir": _display_path(repo_root, directory),
        "next": _derive_next_step(repo_root, epic_id, directory, plan, gate),
        "gate": {
            "open": gate["open"],
            "type": gate.get("type"),
            "story_id": gate.get("story_id"),
            "triggered_by": gate.get("triggered_by", []),
            "path": gate.get("path"),
        },
        "plan": plan_summary,
        "dispatch": _dispatch_counts(timeline),
        "usage": usage,
        "latest_event": timeline[-1] if timeline else None,
    }


def _derive_next_step(
    repo_root: Path,
    epic_id: int,
    directory: Path,
    plan: Plan | None,
    gate: dict[str, Any],
) -> dict[str, Any]:
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
            for bucket in ("research", "thinking", "brainstorm"):
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
    if all(story.status == "done" for story in plan.stories):
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
    counts = {"pending": 0, "in_progress": 0, "done": 0}
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
        return {"open": False, "path": _display_path(repo_root, gate_path)}
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
        "timestamp": front.get("timestamp"),
        "sections": _markdown_sections(body),
    }


def _audit_summary(
    repo_root: Path,
    directory: Path,
    dispatch_events: list[dict[str, Any]],
    usage: dict[str, Any],
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
        "files": files,
        "dispatch": {
            "spawned": sum(
                1 for event in dispatch_events if event.get("event") == "subprocess_spawned"
            ),
            "returned": len(dispatch_returned),
            "killed": sum(
                1 for event in dispatch_events if event.get("event") == "subprocess_killed"
            ),
            "returned_events": dispatch_returned,
        },
        "usage": usage,
    }
    return summary


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
            "duration_ms",
            "pid",
            "reason",
            "codex_audit_path",
            "claude_transcript_path",
            "artefacts_loaded",
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


def _dispatch_counts(timeline: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "spawned": sum(1 for item in timeline if item["event"] == "subprocess_spawned"),
        "returned": sum(1 for item in timeline if item["event"] == "subprocess_returned"),
        "killed": sum(1 for item in timeline if item["event"] == "subprocess_killed"),
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
            "duration_ms",
            "codex_audit_path",
            "claude_transcript_path",
            "artefacts_loaded",
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
    story = f" story={next_step['story_id']}" if next_step.get("story_id") else ""
    reason = f" reason={next_step['reason']}" if next_step.get("reason") else ""
    print(f"next: {next_step['node']}{story}{reason}")
    gate = status["gate"]
    if gate["open"]:
        print(
            "gate: open "
            f"type={gate.get('type')} story={gate.get('story_id') or '-'} "
            f"triggered_by={','.join(gate.get('triggered_by') or [])}"
        )
    else:
        print("gate: closed")
    plan = status["plan"]
    if plan["valid"]:
        counts = plan["story_counts"]
        print(
            "stories: "
            f"pending={counts['pending']} in_progress={counts['in_progress']} done={counts['done']}"
        )
    else:
        print(f"stories: unavailable plan_valid={plan['valid']}")
    _print_usage(status["usage"])


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
    print(
        "files: "
        f"commit_bound={audit['commit_bound_file_count']} "
        f"raw_overflow={audit['raw_overflow_file_count']} "
        f"redacted={audit['redacted_file_count']} truncated={audit['truncated_file_count']}"
    )
    print(f"raw_overflow_path: {audit['raw_overflow_path']}")
    print("retention_archive: not implemented")
    _print_usage(audit["usage"])
    returned = audit["dispatch"]["returned"]
    spawned = audit["dispatch"]["spawned"]
    killed = audit["dispatch"]["killed"]
    print(f"dispatch: spawned={spawned} returned={returned} killed={killed}")


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
        for key in ("story_id", "role", "adapter", "gate_type", "decision", "exit_code"):
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


def _usage_values(values: dict[str, int | float]) -> str:
    return ",".join(f"{key}={values[key]}" for key in values)
