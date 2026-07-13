"""Durable engine state under the operator home (ADR-017).

One module owns every durable engine path. Nothing outside this module composes
a state path by hand, and nothing engine-owned is written into the driven
repository: a delivery checkout carries delivery content and nothing else.

Every path here hangs off ``project_state_root(project_key)``. The project key
is explicit at the entry point and is never derived from a checkout directory
name - worktree containers routinely hold directories called ``main``, so a
directory-derived key crosses unrelated projects' state.

The repo root and the state root are independent. The repo root comes from an
explicit argument or from git's top level; the state root comes from the project
key. Never derive one from the other.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from woof.paths import project_state_root, woof_home

__all__ = [
    "agents_schema_cache_path",
    "append_jsonl",
    "atomic_write_json",
    "atomic_write_text",
    "audit_dir",
    "audit_raw_dir",
    "audit_redacted_dir",
    "check_result_path",
    "codebase_dir",
    "current_epic_path",
    "dispatch_events_path",
    "dispositions_dir",
    "epic_contract_path",
    "epic_dir",
    "epic_events_path",
    "epics_root",
    "executor_result_path",
    "gate_path",
    "instability_path",
    "intake_sources_path",
    "last_sync_path",
    "lock_path",
    "plan_path",
    "preflight_cache_dir",
    "project_state_root",
    "quality_gates_baseline_path",
    "review_cache_dir",
    "runs_root",
    "sibling_conflicts_path",
    "spark_path",
    "usage_path",
    "woof_home",
    "work_source_lock_path",
    "work_unit_critique_path",
    "work_unit_disposition_path",
    "work_unit_set_dir",
    "work_unit_sets_root",
]

LOCK_FILENAME = ".wf.lock"


# --- epic-scoped state -----------------------------------------------------


def epics_root(project_key: str) -> Path:
    return project_state_root(project_key) / "epics"


def epic_dir(project_key: str, epic_id: int) -> Path:
    return epics_root(project_key) / f"E{epic_id}"


def plan_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "plan.json"


def epic_contract_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "EPIC.md"


def spark_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "spark.md"


def epic_events_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "epic.jsonl"


def dispatch_events_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "dispatch.jsonl"


def gate_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "gate.md"


def lock_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / LOCK_FILENAME


def executor_result_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "executor_result.json"


def check_result_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "check-result.json"


def last_sync_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / ".last-sync"


def critique_dir(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "critique"


def work_unit_critique_path(project_key: str, epic_id: int, work_unit_id: str) -> Path:
    return critique_dir(project_key, epic_id) / f"work-unit-{work_unit_id}.md"


def dispositions_dir(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "dispositions"


def work_unit_disposition_path(project_key: str, epic_id: int, work_unit_id: str) -> Path:
    return dispositions_dir(project_key, epic_id) / f"work-unit-{work_unit_id}.md"


def audit_dir(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "audit"


def audit_raw_dir(project_key: str, epic_id: int) -> Path:
    return audit_dir(project_key, epic_id) / "raw"


def audit_redacted_dir(project_key: str, epic_id: int) -> Path:
    return audit_dir(project_key, epic_id) / "redacted"


def review_cache_dir(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "review-cache"


def instability_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "instability.jsonl"


def runs_root(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "runs"


def usage_path(project_key: str, epic_id: int) -> Path:
    return epic_dir(project_key, epic_id) / "usage.jsonl"


# --- project-scoped state --------------------------------------------------


def current_epic_path(project_key: str) -> Path:
    return project_state_root(project_key) / ".current-epic"


def codebase_dir(project_key: str) -> Path:
    """The cartography tree: engine-consumed derived state, never in the repo."""

    return project_state_root(project_key) / "codebase"


def quality_gates_baseline_path(project_key: str) -> Path:
    return project_state_root(project_key) / "quality-gates-baseline.json"


def sibling_conflicts_path(project_key: str) -> Path:
    return project_state_root(project_key) / "sibling-conflicts.jsonl"


def intake_sources_path(project_key: str) -> Path:
    return project_state_root(project_key) / "intake" / "sources.json"


def work_unit_sets_root(project_key: str) -> Path:
    return project_state_root(project_key) / "work-unit-sets"


def work_unit_set_dir(project_key: str, set_id: str) -> Path:
    return work_unit_sets_root(project_key) / set_id


def work_source_lock_path(document: Path) -> Path:
    """Serialise writeback to one work-source document, keyed by its absolute path.

    The lock is engine state, so it lives in the operator home rather than beside
    the document: the document's repository takes the unit-state edit and nothing
    else - no engine directory, no artefact, no sidecar (ADR-017).
    """

    digest = hashlib.sha256(str(document).encode("utf-8")).hexdigest()[:32]
    return woof_home() / "locks" / "work-source" / f"{digest}.lock"


def preflight_cache_dir(project_key: str) -> Path:
    return project_state_root(project_key) / "cache"


def agents_schema_cache_path(project_key: str) -> Path:
    return preflight_cache_dir(project_key) / "agents-schema.json"


# --- durable writes --------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` via a temporary sibling and rename, so readers never see a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(payload, indent=indent) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
