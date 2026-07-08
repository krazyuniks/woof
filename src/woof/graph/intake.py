"""Intake helpers for epic-backed and pre-decomposed work-unit sources."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from woof.graph.state import Plan, WorkUnitSetContext


@dataclass(frozen=True)
class IntakeResult:
    context: dict[str, Any]
    directory: Path
    plan_path: Path
    plan_markdown_path: Path
    metadata_path: Path
    work_unit_count: int


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_project_ref(repo_root: Path) -> str:
    return repo_root.resolve().name


def epic_work_unit_context(repo_root: Path, epic_id: int) -> dict[str, Any]:
    return {
        "kind": "epic",
        "project_ref": default_project_ref(repo_root),
        "epic_id": epic_id,
    }


def ensure_epic_plan_context(repo_root: Path, epic_id: int, plan_path: Path) -> None:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{plan_path} must contain a JSON object")
    context = payload.get("context")
    expected = epic_work_unit_context(repo_root, epic_id)
    if context is None:
        payload["context"] = expected
    elif context != expected:
        raise ValueError(f"plan context {context!r} does not match {expected!r}")
    if payload.get("epic_id") != epic_id:
        raise ValueError(f"plan epic_id {payload.get('epic_id')} does not match E{epic_id}")
    plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def ingest_predecomposed_work_units(
    repo_root: Path,
    source_path: Path,
    *,
    project_ref: str | None = None,
    set_id: str | None = None,
    source_ref: str | None = None,
    worktree_policy: dict[str, Any] | None = None,
) -> IntakeResult:
    source_path = source_path.resolve()
    payload = _load_source_payload(source_path)
    source_project_ref = _string(payload.get("project_ref"))
    resolved_project_ref = project_ref or source_project_ref or default_project_ref(repo_root)
    resolved_source_ref = (
        source_ref or _string(payload.get("source_ref")) or _rel(repo_root, source_path)
    )
    resolved_set_id = _resolve_set_id(repo_root, source_path, payload, explicit=set_id)
    context = {
        "kind": "work_unit_set",
        "project_ref": resolved_project_ref,
        "set_id": resolved_set_id,
        "source_ref": resolved_source_ref,
    }
    plan = Plan.model_validate(
        {
            "context": context,
            "goal": _string(payload.get("goal"))
            or _string(payload.get("title"))
            or f"Pre-decomposed work-unit set {resolved_set_id}.",
            "work_units": [_normalise_work_unit(unit) for unit in _work_units(payload)],
        }
    )

    directory = repo_root / ".woof" / "work-unit-sets" / resolved_set_id
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / "plan.json"
    plan_markdown_path = directory / "PLAN.md"
    metadata_path = directory / "intake.json"
    plan_path.write_text(plan.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    plan_markdown_path.write_text(_render_plan_markdown(plan), encoding="utf-8")
    metadata = {
        "schema_version": 1,
        "kind": "pre_decomposed_work_units",
        "ingested_at": now_utc(),
        "source": {
            "path": _rel(repo_root, source_path),
            "source_ref": resolved_source_ref,
        },
        "context": context,
        "plan_path": _rel(repo_root, plan_path),
        "plan_markdown_path": _rel(repo_root, plan_markdown_path),
        "qualified_work_unit_refs": [
            {"context": context, "work_unit_id": unit.id} for unit in plan.work_units
        ],
    }
    worktrees = _worktree_metadata(payload, plan, worktree_policy)
    if worktrees is not None:
        metadata["worktrees"] = worktrees
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return IntakeResult(
        context=context,
        directory=directory,
        plan_path=plan_path,
        plan_markdown_path=plan_markdown_path,
        metadata_path=metadata_path,
        work_unit_count=len(plan.work_units),
    )


def _load_source_payload(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".md", ".markdown"}:
        if not text.startswith("---\n"):
            raise ValueError(f"{path}: markdown intake source must start with YAML front matter")
        end = text.find("\n---\n", 4)
        if end < 0:
            raise ValueError(f"{path}: unterminated YAML front matter")
        payload = yaml.safe_load(text[4:end]) or {}
    else:
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: intake source must contain an object")
    return payload


def _work_units(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("work_units")
    if not isinstance(value, list) or not value:
        raise ValueError("pre-decomposed intake source must carry non-empty work_units")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError("pre-decomposed work_units entries must be objects")
    return value


def _normalise_work_unit(unit: dict[str, Any]) -> dict[str, Any]:
    state = _string(unit.get("state")) or _string(unit.get("status")) or "pending"
    state_map = {
        "todo": "pending",
        "blocked": "pending",
        "cancelled": "abandoned",
    }
    return {
        "id": unit.get("id"),
        "title": unit.get("title"),
        "summary": _string(unit.get("summary")) or _string(unit.get("body")) or unit.get("title"),
        "bounded_context": unit.get("bounded_context"),
        "paths": _string_list(unit.get("paths")) or ["**/*"],
        "acceptance": _string_list(unit.get("acceptance")),
        "deps": _string_list(unit.get("deps")) or _string_list(unit.get("depends_on")),
        "satisfies": _string_list(unit.get("satisfies")),
        "implements_contract_decisions": _string_list(unit.get("implements_contract_decisions")),
        "uses_contract_decisions": _string_list(unit.get("uses_contract_decisions")),
        "tests": unit.get("tests") or {"count": 0, "types": ["unspecified"]},
        "state": state_map.get(state, state),
    }


def _resolve_set_id(
    repo_root: Path,
    source_path: Path,
    payload: dict[str, Any],
    *,
    explicit: str | None,
) -> str:
    candidate = explicit or _string(payload.get("set_id"))
    context = payload.get("context")
    if not candidate and isinstance(context, dict):
        candidate = _string(context.get("set_id"))
    if candidate:
        return _slug(candidate)

    mapping_path = repo_root / ".woof" / "intake" / "sources.json"
    key = str(source_path)
    mapping = _load_mapping(mapping_path)
    existing = mapping.get(key)
    if isinstance(existing, str) and existing:
        return existing
    assigned = _slug(f"set-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}")
    mapping[key] = assigned
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return assigned


def _load_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def _worktree_metadata(
    source_payload: dict[str, Any],
    plan: Plan,
    policy: dict[str, Any] | None,
) -> dict[str, Any] | None:
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
    root = _string(worktree.get("root"))
    if not root:
        return None
    derivation = _string(worktree.get("derivation")) or "unit_id"
    unit_ids = [unit.id for unit in plan.work_units]
    if derivation == "manifest_map":
        unit_paths = _source_worktree_paths(source_payload)
    else:
        derivation = "unit_id"
        unit_paths = {unit_id: f"{root.rstrip('/')}/{unit_id}" for unit_id in unit_ids}

    return {
        "derivation": derivation,
        "root": root,
        "unit_paths": {
            unit_id: unit_paths[unit_id] for unit_id in unit_ids if unit_id in unit_paths
        },
    }


def _source_worktree_paths(source_payload: dict[str, Any]) -> dict[str, str]:
    candidates = []
    worktrees = source_payload.get("worktrees")
    if isinstance(worktrees, dict):
        candidates.extend([worktrees.get("unit_paths"), worktrees.get("paths")])
    candidates.append(source_payload.get("worktree_paths"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            return {
                str(unit_id): str(path)
                for unit_id, path in candidate.items()
                if isinstance(unit_id, str) and isinstance(path, str) and path
            }
    return {}


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    if not slug:
        slug = "set"
    if not slug[0].isalpha():
        slug = f"set-{slug}"
    return slug


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _render_plan_markdown(plan: Plan) -> str:
    heading = "Plan"
    if plan.epic_id is not None:
        heading = f"Plan E{plan.epic_id}"
    elif isinstance(plan.context, WorkUnitSetContext):
        heading = f"Plan {plan.context.set_id}"
    lines = [
        f"# {heading}\n\n",
        f"{plan.goal}\n\n",
        "## Work Units\n\n",
        "| ID | Title | State | Depends On | Paths |\n",
        "|---|---|---|---|---|\n",
    ]
    for unit in plan.work_units:
        deps = ", ".join(unit.deps) if unit.deps else "-"
        paths = ", ".join(unit.paths) if unit.paths else "-"
        lines.append(f"| {unit.id} | {unit.title} | {unit.state} | {deps} | {paths} |\n")
    return "".join(lines)
