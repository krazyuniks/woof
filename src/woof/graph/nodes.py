"""Node registry and implementations for ADR-001."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from woof.cli.policy import cartography_floor, load_policy
from woof.gate.write import write_gate, write_gate_for_trigger, write_gate_from_check_result
from woof.graph.dispositions import (
    FrontMatterError,
    MarkdownFrontMatter,
    critique_findings,
    critique_severity,
    read_markdown_front_matter,
    reviewer_blocker_gate_body,
    validate_critique_invariants,
    validate_work_unit_disposition,
    work_unit_critique_path,
    work_unit_disposition_path,
    work_unit_disposition_relpath,
    write_deterministic_work_unit_disposition,
)
from woof.graph.epilogue import DISPATCH_DENIAL_EPILOGUE
from woof.graph.git import changed_paths, git, head_branch_drift_detected, staged_paths
from woof.graph.intake import ensure_epic_plan_context, epic_work_unit_context
from woof.graph.manifest import build_work_unit_manifest, verify_staged_manifest
from woof.graph.pathspec import PathspecEvaluationError, filter_paths_matching
from woof.graph.planning_contracts import (
    validate_definition_open_questions,
    validate_discovery_synthesis_contract,
    validate_stage3_plan_contract,
)
from woof.graph.readiness import ReadinessResult, evaluate_readiness
from woof.graph.state import (
    TERMINAL_WORK_UNIT_STATES,
    NodeInput,
    NodeOutput,
    NodeStatus,
    NodeType,
    Plan,
    ValidationSummary,
    WorkUnitSpec,
)
from woof.graph.transitions import (
    StageStateError,
    append_epic_event,
    append_epic_event_once,
    archived_epic_contracts,
    archived_epic_findings_path,
    definition_revision_requested,
    discovery_bucket_complete,
    discovery_bucket_dir,
    discovery_synthesis_complete,
    discovery_synthesis_dir,
    discovery_synthesis_paths,
    epic_dir,
    epic_event_exists,
    failed_readiness_cycles,
    load_plan,
    mark_work_unit_state,
    plan_critique_path,
    plan_markdown_path,
    work_unit_by_id,
)
from woof.graph.transitions import (
    gate_path as graph_gate_path,
)
from woof.lib.audit import prepare_commit_audit
from woof.paths import schema_dir, tool_root

NodeHandler = Callable[[NodeInput], NodeOutput]
DispatchExitType = str

# Default number of failed readiness cycles before escalation. Configurable via
# .woof/prerequisites.toml [readiness].escalation_threshold.
DEFAULT_READINESS_ESCALATION_THRESHOLD = 3

# In-process cache for plan.json schema validation keyed by SHA-256 content hash.
# Valid across multiple node calls within a single runner invocation; a changed
# plan.json produces a different hash and always re-validates.
_PLAN_VALIDATE_CACHE: dict[str, tuple[bool, str]] = {}

_DISPATCH_SUCCESS_EXIT_TYPES = {"clean", "completed_lingering"}
_DISPATCH_FAILURE_EXIT_TYPES = {
    "nonzero",
    "idle_kill",
    "wallclock_timeout",
    "operator_cancel",
}
DEFAULT_FIX_ROUNDS_PER_BLOCKER = 2


@dataclass(frozen=True)
class DispatchRunResult:
    process: subprocess.CompletedProcess[str]
    exit_type: DispatchExitType
    exit_code: int | None = None


@dataclass(frozen=True)
class DispatchClassification:
    ok: bool
    exit_type: DispatchExitType
    gate_exit_code: int
    message: str


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _readiness_escalation_threshold(repo_root: Path) -> int:
    """Return the configured readiness-escalation threshold.

    Reads ``[readiness].escalation_threshold`` from ``.woof/prerequisites.toml``.
    Falls back to ``DEFAULT_READINESS_ESCALATION_THRESHOLD`` when the file is
    absent, unreadable, or the key is not set.
    """
    prereq_path = repo_root / ".woof" / "prerequisites.toml"
    try:
        with prereq_path.open("rb") as fh:
            data = tomllib.load(fh)
        threshold = data.get("readiness", {}).get("escalation_threshold")
        if isinstance(threshold, int) and threshold >= 1:
            return threshold
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return DEFAULT_READINESS_ESCALATION_THRESHOLD


def _fix_round_budget(repo_root: Path) -> int:
    path = repo_root / ".woof" / "agents.toml"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_FIX_ROUNDS_PER_BLOCKER
    block = data.get("fix_rounds")
    if not isinstance(block, dict):
        return DEFAULT_FIX_ROUNDS_PER_BLOCKER
    value = block.get("max_rounds_per_blocker", DEFAULT_FIX_ROUNDS_PER_BLOCKER)
    return (
        value
        if isinstance(value, int) and value >= 0 and not isinstance(value, bool)
        else DEFAULT_FIX_ROUNDS_PER_BLOCKER
    )


def _woof_subprocess_argv() -> list[str]:
    """Return a portable argv prefix for shelling back into Woof.

    Uses the active Python interpreter plus the ``woof`` module so the
    invocation works from an installed wheel as well as the source checkout.
    The source-checkout ``bin/woof`` wrapper depends on ``uv`` + script-mode
    metadata that does not declare the ``woof`` package itself, so it is
    unsafe to execute from an isolated wheel install.
    """

    return [sys.executable, "-m", "woof"]


def _woof_subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env dict that lets the child Python import ``woof``.

    The active interpreter's ``sys.path`` is not inherited by subprocesses,
    so prepend a ``PYTHONPATH`` entry covering both the source-checkout
    ``src`` directory (when present) and the resolved ``tool_root()``. This
    keeps `[sys.executable, "-m", "woof"]` working from a uv-run-script
    parent that imported the woof package by manipulating ``sys.path``.
    Wheel installs already have ``woof`` on ``sys.path``; the extra entries
    are harmless.
    """

    env = dict(os.environ)
    if extra:
        env.update(extra)
    root = tool_root()
    src = root / "src"
    candidates: list[str] = []
    if (src / "woof" / "__init__.py").is_file():
        candidates.append(str(src))
    if (root / "woof" / "__init__.py").is_file():
        candidates.append(str(root))
    existing = env.get("PYTHONPATH", "")
    existing_parts = existing.split(os.pathsep) if existing else []
    new_parts = [path for path in candidates if path not in existing_parts]
    if new_parts:
        env["PYTHONPATH"] = (
            os.pathsep.join([*new_parts, *existing_parts])
            if existing_parts
            else os.pathsep.join(new_parts)
        )
    env.setdefault("WOOF_TOOL_ROOT", str(root))
    return env


def _gate_path(epic_id: int) -> str:
    return f".woof/epics/E{epic_id}/gate.md"


def _gate_operator_message(repo_root: Path, epic_id: int) -> str:
    relpath = _gate_path(epic_id)
    path = repo_root / relpath
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return f"gate open at {relpath}"
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            body = text[end + len("\n---\n") :]
    body = body.strip()
    return f"gate open at {relpath}\n\n{body}" if body else f"gate open at {relpath}"


def _relpath(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _existing_prompt_artefacts(repo_root: Path, paths: list[Path]) -> list[str]:
    return [_relpath(repo_root, path) for path in paths if path.is_file()]


def _validation_summary(check_result: dict) -> ValidationSummary:
    checks = check_result.get("checks")
    if not isinstance(checks, list):
        checks = []
    triggered_by = check_result.get("triggered_by")
    if not isinstance(triggered_by, list):
        triggered_by = []
    stage = check_result.get("stage")
    return ValidationSummary(
        ok=bool(check_result.get("ok", False)),
        stage=stage if isinstance(stage, int) else None,
        triggered_by=[str(item) for item in triggered_by],
        check_count=len(checks),
        failed_check_count=sum(
            1 for check in checks if isinstance(check, dict) and not check.get("ok")
        ),
    )


def _validation_summary_from_path(path: Path) -> ValidationSummary | None:
    try:
        return _validation_summary(json.loads(path.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _staged_tree(repo_root: Path) -> str:
    return git(repo_root, "write-tree").stdout.strip()


def _record_verified_index(repo_root: Path, result_path: Path) -> None:
    payload = json.loads(result_path.read_text())
    payload["verified_tree"] = _staged_tree(repo_root)
    payload["verified_paths"] = staged_paths(repo_root)
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _check_verified_index(repo_root: Path, check_result: dict) -> str | None:
    expected_tree = check_result.get("verified_tree")
    expected_paths = check_result.get("verified_paths")
    if expected_tree is None and expected_paths is None:
        return None
    if not isinstance(expected_tree, str):
        return "check-result.json has no verified_tree pin"
    if not isinstance(expected_paths, list) or not all(
        isinstance(path, str) for path in expected_paths
    ):
        return "check-result.json has no valid verified_paths pin"

    actual_tree = _staged_tree(repo_root)
    actual_paths = staged_paths(repo_root)
    if actual_tree != expected_tree:
        return f"staged tree changed after verification: {expected_tree} -> {actual_tree}"
    if actual_paths != expected_paths:
        return f"staged paths changed after verification: {expected_paths} -> {actual_paths}"
    return None


def _tree_blob(repo_root: Path, tree: str, path: str) -> str | None:
    proc = git(repo_root, "show", f"{tree}:{path}", check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


def _plan_matches_completed_resume_delta(
    repo_root: Path, epic_id: int, work_unit_id: str, verified_tree: str
) -> bool:
    plan_path = f".woof/epics/E{epic_id}/plan.json"
    base_text = _tree_blob(repo_root, verified_tree, plan_path)
    if base_text is None:
        return False
    try:
        base_plan = Plan.model_validate_json(base_text)
        current_plan = load_plan(repo_root, epic_id)
    except ValueError:
        return False

    expected_units: list[WorkUnitSpec] = []
    found = False
    for unit in base_plan.work_units:
        data = unit.model_dump(exclude_none=True)
        if unit.id == work_unit_id:
            data["state"] = "done"
            found = True
        expected_units.append(WorkUnitSpec.model_validate(data))
    if not found:
        return False
    expected_plan = Plan(
        epic_id=base_plan.epic_id,
        context=base_plan.context,
        goal=base_plan.goal,
        work_units=expected_units,
    )
    return current_plan.model_dump(exclude_none=True) == expected_plan.model_dump(exclude_none=True)


def _epic_events_match_completed_resume_delta(
    repo_root: Path, epic_id: int, work_unit_id: str, verified_tree: str
) -> bool:
    event_path = f".woof/epics/E{epic_id}/epic.jsonl"
    base_text = _tree_blob(repo_root, verified_tree, event_path)
    if base_text is None:
        return False
    current_path = repo_root / event_path
    try:
        current_text = current_path.read_text(encoding="utf-8")
    except OSError:
        return False

    base_lines = [line for line in base_text.splitlines() if line.strip()]
    current_lines = [line for line in current_text.splitlines() if line.strip()]
    if current_lines[: len(base_lines)] != base_lines:
        return False
    added_lines = current_lines[len(base_lines) :]
    if not added_lines:
        return True

    for raw in added_lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if not isinstance(event, dict):
            return False
        event_name = event.get("event")
        if event_name == "work_unit_completed" or event_name == "transaction_manifest_verified":
            if event.get("work_unit_id") != work_unit_id:
                return False
        elif event_name == "epic_completed":
            if event.get("epic_id") != epic_id:
                return False
        else:
            return False
    return True


def _completed_resume_delta_is_graph_owned(
    repo_root: Path, epic_id: int, work_unit_id: str, check_result: dict
) -> bool:
    verified_tree = check_result.get("verified_tree")
    if not isinstance(verified_tree, str):
        return False
    try:
        delta_paths = sorted(
            git(repo_root, "diff", "--cached", "--name-only", verified_tree).stdout.splitlines()
        )
    except subprocess.CalledProcessError:
        return False
    allowed_paths = {
        f".woof/epics/E{epic_id}/plan.json",
        f".woof/epics/E{epic_id}/epic.jsonl",
    }
    if not delta_paths or any(path not in allowed_paths for path in delta_paths):
        return False
    if not epic_event_exists(
        repo_root,
        epic_id,
        event="work_unit_completed",
        work_unit_id=work_unit_id,
    ):
        return False
    if (
        f".woof/epics/E{epic_id}/plan.json" in delta_paths
        and not _plan_matches_completed_resume_delta(
            repo_root, epic_id, work_unit_id, verified_tree
        )
    ):
        return False
    return (
        f".woof/epics/E{epic_id}/epic.jsonl" not in delta_paths
        or _epic_events_match_completed_resume_delta(
            repo_root, epic_id, work_unit_id, verified_tree
        )
    )


def _write_prompt_file(text: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        return Path(handle.name)


def _dispatch_exit_type_from_returncode(returncode: int) -> DispatchExitType:
    return "clean" if returncode == 0 else "nonzero"


def _dispatch_jsonl_offset(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_appended_dispatch_events(path: Path, offset: int) -> list[dict]:
    try:
        with path.open("rb") as handle:
            if offset > 0 and path.stat().st_size >= offset:
                handle.seek(offset)
            raw = handle.read()
    except OSError:
        return []

    events: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _event_work_unit_matches(event: dict, work_unit_id: str | None) -> bool:
    if work_unit_id is None:
        return "work_unit_id" not in event or event.get("work_unit_id") is None
    return event.get("work_unit_id") == work_unit_id


def _dispatch_outcome_from_events(
    events: list[dict],
    *,
    role: str,
    epic_id: int,
    work_unit_id: str | None,
) -> tuple[DispatchExitType | None, int | None]:
    spawned_pids = {
        event.get("pid")
        for event in events
        if event.get("event") == "subprocess_spawned"
        and event.get("epic_id") == epic_id
        and event.get("role") == role
        and _event_work_unit_matches(event, work_unit_id)
    }
    spawned_pids.discard(None)

    for event in reversed(events):
        if event.get("event") not in {"subprocess_returned", "subprocess_killed"}:
            continue
        if event.get("epic_id") != epic_id:
            continue
        if spawned_pids and event.get("pid") not in spawned_pids:
            continue
        if event.get("event") == "subprocess_returned" and (
            event.get("role") != role or not _event_work_unit_matches(event, work_unit_id)
        ):
            continue
        exit_type = event.get("exit_type")
        exit_code = event.get("exit_code")
        return (
            exit_type if isinstance(exit_type, str) else None,
            exit_code if isinstance(exit_code, int) else None,
        )
    return None, None


def _classify_dispatch_result(
    result: DispatchRunResult | subprocess.CompletedProcess[str],
) -> DispatchClassification:
    if isinstance(result, DispatchRunResult):
        process = result.process
        exit_type = result.exit_type
    else:
        process = result
        exit_type = _dispatch_exit_type_from_returncode(process.returncode)

    if exit_type in _DISPATCH_SUCCESS_EXIT_TYPES:
        ok = True
    elif exit_type in _DISPATCH_FAILURE_EXIT_TYPES:
        ok = False
    else:
        ok = process.returncode == 0

    return DispatchClassification(
        ok=ok,
        exit_type=exit_type,
        gate_exit_code=process.returncode,
        message=process.stderr.strip(),
    )


def _prompt_template(path: Path, replacements: dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace("{" + key + "}", value)
    return text


def _planning_validation(
    *,
    ok: bool,
    stage: int,
    triggered_by: list[str] | None = None,
    check_count: int,
    failed_check_count: int,
) -> ValidationSummary:
    return ValidationSummary(
        ok=ok,
        stage=stage,
        triggered_by=triggered_by or [],
        check_count=check_count,
        failed_check_count=failed_check_count,
    )


def _planning_halt(
    inp: NodeInput,
    *,
    stage: int,
    message: str,
    triggered_by: list[str],
    check_count: int,
    failed_check_count: int,
    paths: list[str] | None = None,
) -> NodeOutput:
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.HALTED,
        epic_id=inp.epic_id,
        validation_summary=_planning_validation(
            ok=False,
            stage=stage,
            triggered_by=triggered_by,
            check_count=check_count,
            failed_check_count=failed_check_count,
        ),
        triggered_by=triggered_by,
        message=message,
        paths=paths or [],
    )


def _discovery_source_paths(repo_root: Path, epic_id: int) -> list[str]:
    discovery_dir = epic_dir(repo_root, epic_id) / "discovery"
    synthesis_dir = discovery_synthesis_dir(repo_root, epic_id)
    if not discovery_dir.exists():
        return []
    return [
        _relpath(repo_root, path)
        for path in sorted(discovery_dir.rglob("*.md"))
        if not path.is_relative_to(synthesis_dir)
    ]


_DISCOVERY_BUCKET_NODE_TYPE = {
    "research": NodeType.DISCOVERY_RESEARCH,
    "thinking": NodeType.DISCOVERY_THINKING,
    "ideate": NodeType.DISCOVERY_IDEATE,
}
_DISCOVERY_BUCKET_NEXT_NODE = {
    "research": NodeType.DISCOVERY_THINKING,
    "thinking": NodeType.DISCOVERY_IDEATE,
    "ideate": NodeType.DISCOVERY_SYNTHESIS,
}
# Building-block playbook directory for each producer's menu. The ideate
# bucket has no building blocks; its node prompt is self-contained.
_DISCOVERY_BUCKET_PLAYBOOK_SUBDIR = {
    "research": "research",
    "thinking": "consider",
    "ideate": None,
}

# Cartography document loading map (per docs/architecture.md §4).
# discovery_ideate and discovery_synthesis load the full set (architecture wins
# over the plan's Prompt-1 table which incorrectly listed synthesis as
# "thinking bucket only").
# contract_readiness is a deterministic node — it has no dispatch payload, so
# it is not wired here (S1 scope is dispatch nodes only).
_FULL_CARTOGRAPHY_SET = [
    "CURRENT-ARCHITECTURE.md",
    "STACK.md",
    "INTEGRATIONS.md",
    "STRUCTURE.md",
    "CONVENTIONS.md",
    "TESTING.md",
    "CONCERNS.md",
    "TARGET-ARCHITECTURE.md",
    "PRINCIPLES.md",
]
_DISCOVERY_BUCKET_CARTOGRAPHY_DOCS: dict[str, list[str]] = {
    "research": ["STACK.md", "INTEGRATIONS.md", "CONCERNS.md"],
    "thinking": ["CURRENT-ARCHITECTURE.md", "STRUCTURE.md"],
    "ideate": _FULL_CARTOGRAPHY_SET,
}
_EPIC_DEFINITION_CARTOGRAPHY_DOCS = [
    "CURRENT-ARCHITECTURE.md",
    "STRUCTURE.md",
    "CONCERNS.md",
    "TARGET-ARCHITECTURE.md",
    "PRINCIPLES.md",
]
_BREAKDOWN_PLANNING_CARTOGRAPHY_DOCS = [
    "CURRENT-ARCHITECTURE.md",
    "STRUCTURE.md",
    "TARGET-ARCHITECTURE.md",
    "PRINCIPLES.md",
]
_PLAN_CRITIQUE_CARTOGRAPHY_DOCS = [
    "CURRENT-ARCHITECTURE.md",
    "STRUCTURE.md",
    "CONCERNS.md",
    "TARGET-ARCHITECTURE.md",
]
_EXECUTOR_CARTOGRAPHY_DOCS = [
    "STRUCTURE.md",
    "CONVENTIONS.md",
    "TARGET-ARCHITECTURE.md",
    "PRINCIPLES.md",
]
_CRITIQUE_DISPATCH_CARTOGRAPHY_DOCS = ["CONVENTIONS.md", "TESTING.md", "CONCERNS.md"]
_DESIGN_CARTOGRAPHY_DOCS = {"TARGET-ARCHITECTURE.md", "PRINCIPLES.md"}


def _codebase_doc_relpath(doc_name: str) -> str:
    return f".woof/codebase/{doc_name}"


def _declared_cartography_floor(repo_root: Path) -> str | None:
    policy = load_policy(repo_root)
    return cartography_floor(policy) if isinstance(policy, dict) else None


def _cartography_docs_for_floor(repo_root: Path, doc_names: list[str]) -> list[str]:
    floor = _declared_cartography_floor(repo_root)
    if floor == "none":
        return []
    if floor == "design":
        return [name for name in doc_names if name in _DESIGN_CARTOGRAPHY_DOCS]
    return doc_names


def _require_cartography_docs(
    repo_root: Path,
    doc_names: list[str],
    gate_type: str,
    *,
    work_unit_id: str | None = None,
) -> list[str]:
    """Return repo-relative paths for each named codebase doc.

    Raises StageStateError(operator_recoverable=True, gate_type=gate_type) if
    any document is absent from .woof/codebase/. The check lives here, at
    payload-build time, not in preflight — so a missing doc always halts
    rather than silently dispatching cold.
    """
    refs = [
        _codebase_doc_relpath(name) for name in _cartography_docs_for_floor(repo_root, doc_names)
    ]
    missing = [ref for ref in refs if not (repo_root / ref).is_file()]
    if missing:
        raise StageStateError(
            "Missing cartography document(s) required before dispatch: "
            + ", ".join(missing)
            + ". Run `scripts/refresh-cartography` or author the missing document,"
            " then re-run `woof wf --epic <N>`.",
            operator_recoverable=True,
            gate_type=gate_type,
            work_unit_id=work_unit_id,
        )
    return refs


def _work_unit_files_txt_slice(repo_root: Path, work_unit: WorkUnitSpec) -> list[str] | None:
    """Return the work-unit-scoped subset of .woof/codebase/files.txt lines.

    Raises StageStateError(work_unit_gate) if files.txt is missing or the pathspec
    evaluation fails. Decision D1 (E19): filter through work_unit.paths[] at build
    time so the executor receives only its slice.
    """
    floor = _declared_cartography_floor(repo_root)
    if floor in {"none", "design"}:
        return None
    files_txt_path = repo_root / ".woof" / "codebase" / "files.txt"
    if not files_txt_path.is_file():
        raise StageStateError(
            "Missing mechanical cartography file: .woof/codebase/files.txt. "
            "Run `scripts/refresh-cartography` to generate it.",
            operator_recoverable=True,
            gate_type="work_unit_gate",
            work_unit_id=work_unit.id,
        )
    candidates = [
        line for line in files_txt_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not candidates or not work_unit.paths:
        return candidates
    try:
        return filter_paths_matching(repo_root, candidates, list(work_unit.paths))
    except PathspecEvaluationError as exc:
        raise StageStateError(
            f"files.txt pathspec slice evaluation failed: {exc}",
            operator_recoverable=True,
            gate_type="work_unit_gate",
            work_unit_id=work_unit.id,
        ) from exc


def _discovery_bucket_source_paths(repo_root: Path, epic_id: int, bucket: str) -> list[str]:
    """Return prior-bucket discovery artefacts visible to a producer bucket node."""

    discovery_dir = epic_dir(repo_root, epic_id) / "discovery"
    if not discovery_dir.exists():
        return []
    synthesis_dir = discovery_synthesis_dir(repo_root, epic_id)
    bucket_dir = discovery_bucket_dir(repo_root, epic_id, bucket)
    return [
        _relpath(repo_root, path)
        for path in sorted(discovery_dir.rglob("*.md"))
        if not path.is_relative_to(synthesis_dir) and not path.is_relative_to(bucket_dir)
    ]


def _discovery_bucket_payload(
    repo_root: Path,
    epic_id: int,
    bucket: str,
    cartography_refs: list[str] | None = None,
) -> dict:
    directory = epic_dir(repo_root, epic_id)
    payload = {
        "node_type": _DISCOVERY_BUCKET_NODE_TYPE[bucket].value,
        "epic_id": epic_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": {
            "spark_path": _relpath(repo_root, directory / "spark.md"),
            "discovery_dir": _relpath(repo_root, directory / "discovery"),
            "bucket_dir": _relpath(repo_root, discovery_bucket_dir(repo_root, epic_id, bucket)),
        },
    }
    source_paths = _discovery_bucket_source_paths(repo_root, epic_id, bucket)
    if source_paths:
        payload["inputs"]["source_paths"] = source_paths
    if cartography_refs:
        payload["inputs"]["cartography_paths"] = cartography_refs
    return payload


def _discovery_bucket_artefacts(repo_root: Path, epic_id: int, bucket: str) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    source_paths = [
        repo_root / path for path in _discovery_bucket_source_paths(repo_root, epic_id, bucket)
    ]
    return _existing_prompt_artefacts(repo_root, [directory / "spark.md", *source_paths])


def _playbook_description(path: Path) -> str:
    """Extract the one-line description from a playbook's YAML frontmatter.

    Returns the ``summary`` field when present; falls back to the first
    Markdown heading in the file body.
    """
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                front = yaml.safe_load(parts[1])
                if isinstance(front, dict) and front.get("summary"):
                    return str(front["summary"])
            except yaml.YAMLError:
                pass
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem


def _discovery_bucket_playbooks(bucket: str) -> str:
    """Return a playbook menu for a producer bucket.

    Each line names a playbook, gives its one-line description, and provides
    the absolute path so the producer can open whichever it needs on demand.
    The ideate bucket has no building blocks and returns an empty string.
    """
    subdir = _DISCOVERY_BUCKET_PLAYBOOK_SUBDIR[bucket]
    if subdir is None:
        return ""
    playbook_dir = tool_root() / "playbooks" / "discovery" / subdir
    lines = [
        f"- **{path.stem}**: {_playbook_description(path)} — `{path.resolve()}`"
        for path in sorted(playbook_dir.glob("*.md"))
    ]
    return "\n".join(lines)


def _discovery_bucket_prompt(
    repo_root: Path,
    epic_id: int,
    bucket: str,
    cartography_refs: list[str] | None = None,
) -> str:
    payload = _discovery_bucket_payload(
        repo_root, epic_id, bucket, cartography_refs=cartography_refs
    )
    prompt = _prompt_template(
        tool_root() / "playbooks" / "discovery" / f"{bucket}.md",
        {"planning_input_json": json.dumps(payload, indent=2, sort_keys=True)},
    )
    playbooks = _discovery_bucket_playbooks(bucket)
    if playbooks:
        prompt = f"{prompt}\n\n---\n\n# Building-block playbooks\n\n{playbooks}\n"
    return prompt + DISPATCH_DENIAL_EPILOGUE


def _discovery_synthesis_payload(
    repo_root: Path,
    epic_id: int,
    cartography_refs: list[str] | None = None,
) -> dict:
    directory = epic_dir(repo_root, epic_id)
    synthesis_dir = discovery_synthesis_dir(repo_root, epic_id)
    payload = {
        "node_type": NodeType.DISCOVERY_SYNTHESIS.value,
        "epic_id": epic_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": {
            "spark_path": _relpath(repo_root, directory / "spark.md"),
            "discovery_dir": _relpath(repo_root, directory / "discovery"),
            "synthesis_dir": _relpath(repo_root, synthesis_dir),
        },
    }
    source_paths = _discovery_source_paths(repo_root, epic_id)
    if source_paths:
        payload["inputs"]["source_paths"] = source_paths
    if cartography_refs:
        payload["inputs"]["cartography_paths"] = cartography_refs
    return payload


def _discovery_synthesis_artefacts(repo_root: Path, epic_id: int) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    source_paths = [repo_root / path for path in _discovery_source_paths(repo_root, epic_id)]
    return _existing_prompt_artefacts(repo_root, [directory / "spark.md", *source_paths])


def _epic_contract_revision_paths(repo_root: Path, epic_id: int) -> list[Path]:
    """Return the prior epic (and its findings) feeding a pending contract revision.

    Empty unless a ``revise_epic_contract`` resolution is awaiting re-definition
    (E17 P5 / D-RC). When pending, the definition node re-dispatch declares these as
    inputs and loads them as artefacts so the revision is evidence-driven; once the
    node re-closes definition the request clears and this returns ``[]`` again. The
    list is ``[prior_epic]`` or ``[prior_epic, findings]`` when the findings snapshot
    exists.
    """

    if not definition_revision_requested(repo_root, epic_id):
        return []
    archives = archived_epic_contracts(repo_root, epic_id)
    if not archives:
        return []
    index, archived = archives[-1]
    paths = [archived]
    findings = archived_epic_findings_path(repo_root, epic_id, index)
    if findings.is_file():
        paths.append(findings)
    return paths


def _epic_definition_payload(
    repo_root: Path,
    epic_id: int,
    cartography_refs: list[str] | None = None,
) -> dict:
    directory = epic_dir(repo_root, epic_id)
    inputs: dict[str, object] = {
        "synthesis_dir": _relpath(repo_root, discovery_synthesis_dir(repo_root, epic_id)),
        "epic_path": _relpath(repo_root, directory / "EPIC.md"),
    }
    revision_paths = _epic_contract_revision_paths(repo_root, epic_id)
    if revision_paths:
        inputs["prior_epic_path"] = _relpath(repo_root, revision_paths[0])
        if len(revision_paths) > 1:
            inputs["revision_findings_path"] = _relpath(repo_root, revision_paths[1])
    if cartography_refs:
        inputs["cartography_paths"] = cartography_refs
    return {
        "node_type": NodeType.EPIC_DEFINITION.value,
        "epic_id": epic_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": inputs,
    }


def _epic_definition_artefacts(repo_root: Path, epic_id: int) -> list[str]:
    return _existing_prompt_artefacts(
        repo_root,
        [
            *discovery_synthesis_paths(repo_root, epic_id).values(),
            *_epic_contract_revision_paths(repo_root, epic_id),
        ],
    )


def _breakdown_planning_payload(
    repo_root: Path,
    epic_id: int,
    cartography_refs: list[str] | None = None,
) -> dict:
    directory = epic_dir(repo_root, epic_id)
    inputs: dict[str, object] = {
        "epic_path": _relpath(repo_root, directory / "EPIC.md"),
        "plan_path": _relpath(repo_root, directory / "plan.json"),
        "plan_markdown_path": _relpath(repo_root, plan_markdown_path(repo_root, epic_id)),
    }
    if cartography_refs:
        inputs["cartography_paths"] = cartography_refs
    return {
        "node_type": NodeType.BREAKDOWN_PLANNING.value,
        "epic_id": epic_id,
        "aggregate_context": epic_work_unit_context(repo_root, epic_id),
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": inputs,
    }


def _breakdown_planning_artefacts(repo_root: Path, epic_id: int) -> list[str]:
    return _existing_prompt_artefacts(repo_root, [epic_dir(repo_root, epic_id) / "EPIC.md"])


def _plan_critique_payload(
    repo_root: Path,
    epic_id: int,
    cartography_refs: list[str] | None = None,
) -> dict:
    directory = epic_dir(repo_root, epic_id)
    inputs: dict[str, object] = {
        "epic_path": _relpath(repo_root, directory / "EPIC.md"),
        "plan_path": _relpath(repo_root, directory / "plan.json"),
        "plan_markdown_path": _relpath(repo_root, plan_markdown_path(repo_root, epic_id)),
        "critique_path": _relpath(repo_root, plan_critique_path(repo_root, epic_id)),
    }
    if cartography_refs:
        inputs["cartography_paths"] = cartography_refs
    return {
        "node_type": NodeType.PLAN_CRITIQUE.value,
        "epic_id": epic_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": inputs,
    }


def _plan_critique_artefacts(repo_root: Path, epic_id: int) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    return _existing_prompt_artefacts(
        repo_root,
        [
            directory / "EPIC.md",
            directory / "plan.json",
            plan_markdown_path(repo_root, epic_id),
        ],
    )


def _work_unit_critique_payload(
    repo_root: Path,
    epic_id: int,
    work_unit_id: str,
    cartography_refs: list[str] | None = None,
) -> dict:
    directory = epic_dir(repo_root, epic_id)
    plan = load_plan(repo_root, epic_id)
    work_unit = work_unit_by_id(plan, work_unit_id)
    inputs: dict[str, object] = {
        "epic_path": _relpath(repo_root, directory / "EPIC.md"),
        "plan_path": _relpath(repo_root, directory / "plan.json"),
        "critique_path": _relpath(repo_root, work_unit_critique_path(directory, work_unit_id)),
        "staged_diff_command": "git diff --staged",
        "staged_paths_command": "git diff --staged --name-only",
        "work_unit": work_unit.model_dump(),
    }
    if cartography_refs:
        inputs["cartography_paths"] = cartography_refs
    return {
        "node_type": NodeType.CRITIQUE_DISPATCH.value,
        "epic_id": epic_id,
        "work_unit_id": work_unit_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": inputs,
    }


def _plan_gate_open_payload(repo_root: Path, epic_id: int) -> dict:
    directory = epic_dir(repo_root, epic_id)
    return {
        "node_type": NodeType.PLAN_GATE_OPEN.value,
        "epic_id": epic_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": {
            "plan_path": _relpath(repo_root, directory / "plan.json"),
            "plan_markdown_path": _relpath(repo_root, plan_markdown_path(repo_root, epic_id)),
            "critique_path": _relpath(repo_root, plan_critique_path(repo_root, epic_id)),
            "gate_path": _gate_path(epic_id),
            "triggered_by": ["plan_review"],
        },
    }


def _missing_discovery_outputs(repo_root: Path, epic_id: int) -> list[str]:
    missing: list[str] = []
    for path in discovery_synthesis_paths(repo_root, epic_id).values():
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            missing.append(_relpath(repo_root, path))
    return missing


def _discovery_synthesis_prompt(
    repo_root: Path,
    epic_id: int,
    cartography_refs: list[str] | None = None,
) -> str:
    payload = _discovery_synthesis_payload(repo_root, epic_id, cartography_refs=cartography_refs)
    return (
        _prompt_template(
            tool_root() / "playbooks" / "discovery" / "synthesis.md",
            {"planning_input_json": json.dumps(payload, indent=2, sort_keys=True)},
        )
        + DISPATCH_DENIAL_EPILOGUE
    )


def _epic_definition_prompt(
    repo_root: Path,
    epic_id: int,
    cartography_refs: list[str] | None = None,
) -> str:
    payload = _epic_definition_payload(repo_root, epic_id, cartography_refs=cartography_refs)
    return (
        _prompt_template(
            tool_root() / "playbooks" / "discovery" / "definition.md",
            {"planning_input_json": json.dumps(payload, indent=2, sort_keys=True)},
        )
        + DISPATCH_DENIAL_EPILOGUE
    )


def _breakdown_planning_prompt(
    repo_root: Path,
    epic_id: int,
    cartography_refs: list[str] | None = None,
) -> str:
    payload = _breakdown_planning_payload(repo_root, epic_id, cartography_refs=cartography_refs)
    return (
        _prompt_template(
            tool_root() / "playbooks" / "planning" / "breakdown.md",
            {"planning_input_json": json.dumps(payload, indent=2, sort_keys=True)},
        )
        + DISPATCH_DENIAL_EPILOGUE
    )


def _plan_critique_prompt(
    repo_root: Path,
    epic_id: int,
    cartography_refs: list[str] | None = None,
) -> str:
    payload = _plan_critique_payload(repo_root, epic_id, cartography_refs=cartography_refs)
    template = (tool_root() / "playbooks" / "critique" / "plan.md").read_text(encoding="utf-8")
    return (
        "Graph-owned input:\n\n"
        "```json\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
        "```\n\n"
        f"{template}"
    ) + DISPATCH_DENIAL_EPILOGUE


def _work_unit_critique_prompt(
    repo_root: Path,
    epic_id: int,
    work_unit_id: str,
    cartography_refs: list[str] | None = None,
) -> str:
    payload = _work_unit_critique_payload(
        repo_root, epic_id, work_unit_id, cartography_refs=cartography_refs
    )
    template = (tool_root() / "playbooks" / "critique" / "work-unit.md").read_text(encoding="utf-8")
    return (
        "Graph-owned input:\n\n"
        "```json\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
        "```\n\n"
        f"{template}"
    ) + DISPATCH_DENIAL_EPILOGUE


def _executor_dispatch_prompt(
    repo_root: Path,
    epic_id: int,
    work_unit_id: str,
    cartography_refs: list[str] | None,
    files_txt_slice: list[str] | None,
) -> str:
    """Build the executor dispatch prompt with cartography payload prepended."""
    base = _work_unit_prompt(epic_id, work_unit_id)
    inputs: dict[str, object] = {}
    if cartography_refs:
        inputs["cartography_paths"] = cartography_refs
    if files_txt_slice is not None:
        inputs["files_txt_slice"] = files_txt_slice
    if not inputs:
        return base + DISPATCH_DENIAL_EPILOGUE
    payload = {
        "node_type": NodeType.EXECUTOR_DISPATCH.value,
        "epic_id": epic_id,
        "work_unit_id": work_unit_id,
        "inputs": inputs,
    }
    return (
        "Graph-owned cartography input:\n\n"
        "```json\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
        "```\n\n"
        f"{base}"
    ) + DISPATCH_DENIAL_EPILOGUE


def _validate_epic(repo_root: Path, epic_path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [*_woof_subprocess_argv(), "validate", "--schema", "epic", str(epic_path)],
        cwd=repo_root,
        env=_woof_subprocess_env(),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def _validate_plan(repo_root: Path, epic_id: int, plan_path: Path) -> tuple[bool, str, bool]:
    """Validate plan.json; returns (ok, message, cache_hit).

    Results are cached by SHA-256 of plan.json content within this process.
    A changed plan.json always re-validates; a stale cache never passes new content.
    """
    content = plan_path.read_bytes()
    content_hash = hashlib.sha256(content).hexdigest()
    cached = _PLAN_VALIDATE_CACHE.get(content_hash)
    if cached is not None:
        return cached[0], cached[1], True

    proc = subprocess.run(
        [*_woof_subprocess_argv(), "validate", "--schema", "plan", str(plan_path)],
        cwd=repo_root,
        env=_woof_subprocess_env(),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        result: tuple[bool, str] = (False, (proc.stdout + proc.stderr).strip())
        _PLAN_VALIDATE_CACHE[content_hash] = result
        return result[0], result[1], False
    try:
        plan = load_plan(repo_root, epic_id)
    except (StageStateError, ValueError) as exc:
        result = (False, str(exc))
        _PLAN_VALIDATE_CACHE[content_hash] = result
        return result[0], result[1], False
    if plan.epic_id != epic_id:
        result = (False, f"plan epic_id {plan.epic_id} does not match E{epic_id}")
        _PLAN_VALIDATE_CACHE[content_hash] = result
        return result[0], result[1], False
    result = (True, (proc.stdout + proc.stderr).strip())
    _PLAN_VALIDATE_CACHE[content_hash] = result
    return result[0], result[1], False


def _validate_plan_critique(
    repo_root: Path,
    critique_path: Path,
    plan: dict,
    epic_dir_path: Path,
) -> tuple[bool, str]:
    proc = subprocess.run(
        [*_woof_subprocess_argv(), "validate", "--schema", "critique", str(critique_path)],
        cwd=repo_root,
        env=_woof_subprocess_env(),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return False, (proc.stdout + proc.stderr).strip()
    try:
        critique = read_markdown_front_matter(critique_path)
    except (FileNotFoundError, FrontMatterError) as exc:
        return False, str(exc)
    if critique.front.get("target") != "plan" or critique.front.get("target_id") is not None:
        return False, "plan critique front-matter must set target=plan and target_id=null"
    if critique_severity(critique.front) is None:
        return False, "plan critique severity must be info, minor, or blocker"
    errors = validate_critique_invariants(
        critique.front,
        repo_root=repo_root,
        plan=plan,
        epic_dir=epic_dir_path,
    )
    if errors:
        return False, "\n".join(errors)
    return True, (proc.stdout + proc.stderr).strip()


def _table_cell(value: object) -> str:
    text = str(value)
    return text.replace("\n", " ").replace("|", "\\|").strip()


def _csv(items: list[str]) -> str:
    return ", ".join(items) if items else "-"


def _failure_message(failures: list[str]) -> str:
    return "\n".join(failures)


def _render_plan_markdown(plan: Plan) -> str:
    out = [
        f"# Plan E{plan.epic_id}\n\n",
        f"{plan.goal}\n\n",
        "## Work Units\n\n",
        "| ID | Title | State | Satisfies | Implements CDs | Uses CDs | Depends On | Paths | Tests |\n",
        "|---|---|---|---|---|---|---|---|---|\n",
    ]
    for work_unit in plan.work_units:
        tests = work_unit.tests if isinstance(work_unit.tests, dict) else {}
        test_types = tests.get("types", [])
        if not isinstance(test_types, list):
            test_types = []
        test_count = tests.get("count", 0)
        out.append(
            "| "
            f"{_table_cell(work_unit.id)} | "
            f"{_table_cell(work_unit.title)} | "
            f"{_table_cell(work_unit.state)} | "
            f"{_table_cell(_csv(work_unit.satisfies))} | "
            f"{_table_cell(_csv(work_unit.implements_contract_decisions))} | "
            f"{_table_cell(_csv(work_unit.uses_contract_decisions))} | "
            f"{_table_cell(_csv(work_unit.deps))} | "
            f"{_table_cell(_csv(work_unit.paths))} | "
            f"{_table_cell(str(test_count) + ' ' + _csv([str(item) for item in test_types]))} |\n"
        )
    out.append("\n")
    return "".join(out)


def _work_unit_prompt(epic_id: int, work_unit_id: str) -> str:
    return _prompt_template(
        tool_root() / "playbooks" / "execution" / "work-unit.md",
        {"epic_id": str(epic_id), "work_unit_id": work_unit_id},
    )


def _blocker_signature(critique: MarkdownFrontMatter) -> str:
    blockers = [
        {
            "id": str(finding.get("id") or ""),
            "summary": str(finding.get("summary") or ""),
            "evidence": str(finding.get("evidence") or ""),
        }
        for finding in critique_findings(critique.front)
        if finding.get("severity") == "blocker"
    ]
    payload = json.dumps(blockers, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fix_rounds_used(
    repo_root: Path, epic_id: int, work_unit_id: str, blocker_signature: str
) -> int:
    events_path = epic_dir(repo_root, epic_id) / "epic.jsonl"
    if not events_path.exists():
        return 0
    used = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(event, dict)
            and event.get("event") == "work_unit_fix_round_started"
            and event.get("work_unit_id") == work_unit_id
            and event.get("blocker_signature") == blocker_signature
        ):
            used += 1
    return used


def _fix_round_prompt(
    repo_root: Path,
    epic_id: int,
    work_unit_id: str,
    *,
    critique: MarkdownFrontMatter,
    base_prompt: str,
) -> str:
    critique_rel = work_unit_critique_path(epic_dir(repo_root, epic_id), work_unit_id)
    return (
        "# Work Unit Fix Round\n\n"
        "Continue the same work-unit producer session. Address the reviewer blocker "
        "evidence below, update the staged work-unit diff, and rewrite "
        f"`.woof/epics/E{epic_id}/executor_result.json` when complete.\n\n"
        "Do not dispatch the reviewer. Do not commit. Do not open or edit gate.md.\n\n"
        "## Reviewer Blocker Evidence\n\n"
        f"Source: `{_relpath(repo_root, critique_rel)}`\n\n"
        f"{reviewer_blocker_gate_body(epic_id=epic_id, work_unit_id=work_unit_id, critique=critique)}"
        "\n\n## Producer Contract\n\n"
        f"{base_prompt}"
    )


def _clear_fix_round_artefacts(repo_root: Path, epic_id: int, work_unit_id: str) -> None:
    directory = epic_dir(repo_root, epic_id)
    for path in (
        directory / "executor_result.json",
        directory / "check-result.json",
        work_unit_disposition_path(directory, work_unit_id),
    ):
        path.unlink(missing_ok=True)


def _work_unit_context_artefacts(repo_root: Path, epic_id: int) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    return _existing_prompt_artefacts(
        repo_root,
        [
            repo_root / ".woof" / ".current-epic",
            directory / "plan.json",
            directory / "EPIC.md",
            repo_root / "CLAUDE.md",
            repo_root / "AGENTS.md",
        ],
    )


def _disposition_artefacts(repo_root: Path, epic_id: int, work_unit_id: str) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    return _existing_prompt_artefacts(
        repo_root,
        [
            directory / "EPIC.md",
            directory / "plan.json",
            work_unit_critique_path(directory, work_unit_id),
        ],
    )


def _disposition_prompt(epic_id: int, work_unit_id: str) -> str:
    template = (tool_root() / "playbooks" / "disposition" / "work-unit.md").read_text()
    return template.format(epic_id=epic_id, work_unit_id=work_unit_id)


def _run_dispatch(
    repo_root: Path,
    role: str,
    epic_id: int,
    work_unit_id: str | None,
    prompt: str,
    artefacts_loaded: list[str] | None = None,
    route_key: str | None = None,
    session_mode: str = "one-shot",
) -> DispatchRunResult:
    prompt_file = _write_prompt_file(prompt)
    dispatch_jsonl = epic_dir(repo_root, epic_id) / "dispatch.jsonl"
    dispatch_offset = _dispatch_jsonl_offset(dispatch_jsonl)
    try:
        args = [
            *_woof_subprocess_argv(),
            "dispatch",
            "--role",
            role,
            "--epic",
            str(epic_id),
            "--prompt-file",
            str(prompt_file),
        ]
        if work_unit_id:
            args.extend(["--work-unit", work_unit_id])
        if route_key:
            args.extend(["--route-key", route_key])
        if session_mode != "one-shot":
            args.extend(["--session-mode", session_mode])
        for artefact in artefacts_loaded or []:
            args.extend(["--artefact", artefact])
        proc = subprocess.run(
            args,
            cwd=repo_root,
            env=_woof_subprocess_env(),
            capture_output=True,
            text=True,
        )
        exit_type, exit_code = _dispatch_outcome_from_events(
            _read_appended_dispatch_events(dispatch_jsonl, dispatch_offset),
            role=role,
            epic_id=epic_id,
            work_unit_id=work_unit_id,
        )
        return DispatchRunResult(
            process=proc,
            exit_type=exit_type or _dispatch_exit_type_from_returncode(proc.returncode),
            exit_code=exit_code,
        )
    finally:
        prompt_file.unlink(missing_ok=True)


def _discovery_bucket_node(inp: NodeInput, bucket: str) -> NodeOutput:
    """Run a Stage-1 producer bucket node (research, thinking, ideate)."""

    if inp.work_unit_id:
        raise ValueError(f"discovery_{bucket} does not accept work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    spark_path = directory / "spark.md"
    if not spark_path.is_file() or not spark_path.read_text(encoding="utf-8").strip():
        return _planning_halt(
            inp,
            stage=1,
            message=(
                f"Required Stage-1 input missing or empty: {_relpath(inp.repo_root, spark_path)}"
            ),
            triggered_by=["incomplete_stage_state"],
            check_count=1,
            failed_check_count=1,
        )

    bucket_dir = discovery_bucket_dir(inp.repo_root, inp.epic_id, bucket)
    bucket_relpath = _relpath(inp.repo_root, bucket_dir)
    if not discovery_bucket_complete(inp.repo_root, inp.epic_id, bucket):
        carto_refs = _require_cartography_docs(
            inp.repo_root,
            _DISCOVERY_BUCKET_CARTOGRAPHY_DOCS[bucket],
            "plan_gate",
        )
        bucket_dir.mkdir(parents=True, exist_ok=True)
        proc = _run_dispatch(
            inp.repo_root,
            role="primary",
            epic_id=inp.epic_id,
            work_unit_id=None,
            prompt=_discovery_bucket_prompt(
                inp.repo_root, inp.epic_id, bucket, cartography_refs=carto_refs
            ),
            artefacts_loaded=[
                *_discovery_bucket_artefacts(inp.repo_root, inp.epic_id, bucket),
                *carto_refs,
            ],
            route_key="discovery",
        )
        dispatch = _classify_dispatch_result(proc)
        if not dispatch.ok:
            return _planning_halt(
                inp,
                stage=1,
                message=dispatch.message,
                triggered_by=["subprocess_crash"],
                check_count=1,
                failed_check_count=1,
                paths=[bucket_relpath],
            )
        if not discovery_bucket_complete(inp.repo_root, inp.epic_id, bucket):
            return _planning_halt(
                inp,
                stage=1,
                message=f"Discovery {bucket} produced no artefacts under {bucket_relpath}",
                triggered_by=["schema_validation_failed"],
                check_count=1,
                failed_check_count=1,
                paths=[bucket_relpath],
            )

    paths = sorted(
        _relpath(inp.repo_root, path)
        for path in bucket_dir.glob("*.md")
        if path.is_file() and path.read_text(encoding="utf-8").strip()
    )
    append_epic_event_once(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "discovery_bucket_explored",
            "at": _now(),
            "epic_id": inp.epic_id,
            "bucket": bucket,
            "paths": paths,
        },
        event="discovery_bucket_explored",
        bucket=bucket,
    )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        next_node=_DISCOVERY_BUCKET_NEXT_NODE[bucket],
        validation_summary=_planning_validation(
            ok=True,
            stage=1,
            check_count=len(paths) or 1,
            failed_check_count=0,
        ),
        paths=paths,
    )


def discovery_research_node(inp: NodeInput) -> NodeOutput:
    return _discovery_bucket_node(inp, "research")


def discovery_thinking_node(inp: NodeInput) -> NodeOutput:
    return _discovery_bucket_node(inp, "thinking")


def discovery_ideate_node(inp: NodeInput) -> NodeOutput:
    return _discovery_bucket_node(inp, "ideate")


def discovery_synthesis_node(inp: NodeInput) -> NodeOutput:
    if inp.work_unit_id:
        raise ValueError("discovery_synthesis does not accept work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    spark_path = directory / "spark.md"
    if not spark_path.is_file() or not spark_path.read_text(encoding="utf-8").strip():
        return _planning_halt(
            inp,
            stage=1,
            message=f"Required Stage-1 input missing or empty: {_relpath(inp.repo_root, spark_path)}",
            triggered_by=["incomplete_stage_state"],
            check_count=1,
            failed_check_count=1,
        )

    paths = [
        _relpath(inp.repo_root, path)
        for path in discovery_synthesis_paths(inp.repo_root, inp.epic_id).values()
    ]
    missing = _missing_discovery_outputs(inp.repo_root, inp.epic_id)
    if missing:
        carto_refs = _require_cartography_docs(inp.repo_root, _FULL_CARTOGRAPHY_SET, "plan_gate")
        discovery_synthesis_dir(inp.repo_root, inp.epic_id).mkdir(parents=True, exist_ok=True)
        proc = _run_dispatch(
            inp.repo_root,
            role="primary",
            epic_id=inp.epic_id,
            work_unit_id=None,
            prompt=_discovery_synthesis_prompt(
                inp.repo_root, inp.epic_id, cartography_refs=carto_refs
            ),
            artefacts_loaded=[
                *_discovery_synthesis_artefacts(inp.repo_root, inp.epic_id),
                *carto_refs,
            ],
            route_key="discovery",
        )
        dispatch = _classify_dispatch_result(proc)
        if not dispatch.ok:
            return _planning_halt(
                inp,
                stage=1,
                message=dispatch.message,
                triggered_by=["subprocess_crash"],
                check_count=len(paths),
                failed_check_count=len(paths),
                paths=paths,
            )
        missing = _missing_discovery_outputs(inp.repo_root, inp.epic_id)
        if missing:
            return _planning_halt(
                inp,
                stage=1,
                message="Discovery synthesis did not produce required non-empty files: "
                + ", ".join(missing),
                triggered_by=["schema_validation_failed"],
                check_count=len(paths),
                failed_check_count=len(missing),
                paths=paths,
            )

    contract = validate_discovery_synthesis_contract(inp.repo_root, inp.epic_id)
    if not contract.ok:
        return _planning_halt(
            inp,
            stage=1,
            message=_failure_message(contract.failures),
            triggered_by=["schema_validation_failed"],
            check_count=len(paths) + 2,
            failed_check_count=len(contract.failures),
            paths=paths,
        )

    append_epic_event_once(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "discovery_synthesised",
            "at": _now(),
            "epic_id": inp.epic_id,
            "paths": paths,
        },
        event="discovery_synthesised",
    )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        next_node=NodeType.EPIC_DEFINITION,
        validation_summary=_planning_validation(
            ok=True,
            stage=1,
            check_count=len(paths),
            failed_check_count=0,
        ),
        paths=paths,
    )


def epic_definition_node(inp: NodeInput) -> NodeOutput:
    if inp.work_unit_id:
        raise ValueError("epic_definition does not accept work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    epic_path = directory / "EPIC.md"
    epic_relpath = _relpath(inp.repo_root, epic_path)

    if discovery_synthesis_complete(inp.repo_root, inp.epic_id):
        contract = validate_discovery_synthesis_contract(inp.repo_root, inp.epic_id)
        if not contract.ok:
            return _planning_halt(
                inp,
                stage=1,
                message=_failure_message(contract.failures),
                triggered_by=["schema_validation_failed"],
                check_count=6,
                failed_check_count=len(contract.failures),
                paths=[
                    _relpath(inp.repo_root, path)
                    for path in discovery_synthesis_paths(inp.repo_root, inp.epic_id).values()
                ],
            )

    if not epic_path.exists():
        if not discovery_synthesis_complete(
            inp.repo_root, inp.epic_id
        ) and not definition_revision_requested(inp.repo_root, inp.epic_id):
            missing = _missing_discovery_outputs(inp.repo_root, inp.epic_id)
            return _planning_halt(
                inp,
                stage=2,
                message="Required Stage-2 synthesis inputs are missing: " + ", ".join(missing),
                triggered_by=["incomplete_stage_state"],
                check_count=4,
                failed_check_count=len(missing) or 4,
            )
        carto_refs = _require_cartography_docs(
            inp.repo_root, _EPIC_DEFINITION_CARTOGRAPHY_DOCS, "plan_gate"
        )
        proc = _run_dispatch(
            inp.repo_root,
            role="primary",
            epic_id=inp.epic_id,
            work_unit_id=None,
            prompt=_epic_definition_prompt(inp.repo_root, inp.epic_id, cartography_refs=carto_refs),
            artefacts_loaded=[
                *_epic_definition_artefacts(inp.repo_root, inp.epic_id),
                *carto_refs,
            ],
            route_key="definition",
        )
        dispatch = _classify_dispatch_result(proc)
        if not dispatch.ok:
            return _planning_halt(
                inp,
                stage=2,
                message=dispatch.message,
                triggered_by=["subprocess_crash"],
                check_count=1,
                failed_check_count=1,
                paths=[epic_relpath],
            )

    if not epic_path.exists():
        return _planning_halt(
            inp,
            stage=2,
            message=f"Epic definition did not produce required file: {epic_relpath}",
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[epic_relpath],
        )

    ok, message = _validate_epic(inp.repo_root, epic_path)
    if not ok:
        return _planning_halt(
            inp,
            stage=2,
            message=message,
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[epic_relpath],
        )

    open_question_failures = validate_definition_open_questions(
        inp.repo_root, inp.epic_id, epic_path
    )
    if open_question_failures:
        return _planning_halt(
            inp,
            stage=2,
            message=_failure_message(open_question_failures),
            triggered_by=["schema_validation_failed"],
            check_count=2,
            failed_check_count=len(open_question_failures),
            paths=[
                epic_relpath,
                _relpath(
                    inp.repo_root,
                    discovery_synthesis_paths(inp.repo_root, inp.epic_id)["open_questions_path"],
                ),
            ],
        )

    append_epic_event(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "definition_closed",
            "at": _now(),
            "epic_id": inp.epic_id,
            "paths": [epic_relpath],
        },
    )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        next_node=NodeType.CONTRACT_READINESS,
        validation_summary=_planning_validation(
            ok=True,
            stage=2,
            check_count=1,
            failed_check_count=0,
        ),
        paths=[epic_relpath],
    )


def _validate_readiness_result(repo_root: Path, result_path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [*_woof_subprocess_argv(), "validate", "--schema", "readiness-result", str(result_path)],
        cwd=repo_root,
        env=_woof_subprocess_env(),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def _readiness_gate_body(epic_id: int, epic_relpath: str, result: ReadinessResult) -> str:
    finding_lines: list[str] = []
    for check in result.checks:
        if check.ok:
            continue
        finding_lines.append(f"- {check.id} [{check.severity}]: {check.summary}")
        for finding in check.findings:
            ref = f"{finding.ref}: " if finding.ref else ""
            finding_lines.append(f"  - {ref}{finding.detail}")
    if not finding_lines:
        finding_lines.append("- Readiness failed but no findings were recorded.")
    return (
        "## Context\n\n"
        f"Stage 2.5 contract readiness for E{epic_id}. The deterministic readiness checker "
        f"found `{epic_relpath}` not ready for planning.\n\n"
        "## Findings\n\n" + "\n".join(finding_lines) + "\n\n"
        "## Primary position\n\n"
        f"Source: `{epic_relpath}`\n\n"
        "Revise the epic contract so each finding resolves - add the machine-checkable "
        "acceptance signal, concrete reference, or forward-created marker the checker asked "
        f"for - then re-run `woof wf --epic {epic_id}`.\n\n"
        "## Reviewer position\n\n"
        "The deterministic Stage-2.5 readiness checker produced the findings above; no model "
        "critique is involved in this gate.\n"
    )


def contract_readiness_node(inp: NodeInput) -> NodeOutput:
    if inp.work_unit_id:
        raise ValueError("contract_readiness does not accept work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    epic_path = directory / "EPIC.md"
    epic_relpath = _relpath(inp.repo_root, epic_path)
    result_path = directory / "readiness-result.json"
    result_relpath = _relpath(inp.repo_root, result_path)

    if not epic_path.exists():
        return _planning_halt(
            inp,
            stage=2,
            message=f"Required Stage-2.5 input missing: {epic_relpath}",
            triggered_by=["incomplete_stage_state"],
            check_count=1,
            failed_check_count=1,
            paths=[epic_relpath],
        )

    epic_ok, epic_message = _validate_epic(inp.repo_root, epic_path)
    if not epic_ok:
        return _planning_halt(
            inp,
            stage=2,
            message=epic_message,
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[epic_relpath],
        )

    result = evaluate_readiness(inp.repo_root, inp.epic_id, epic_path)
    result_path.write_text(json.dumps(result.to_payload(_now()), indent=2) + "\n", encoding="utf-8")

    valid, validate_message = _validate_readiness_result(inp.repo_root, result_path)
    if not valid:
        return _planning_halt(
            inp,
            stage=2,
            message=f"readiness-result.json failed schema validation: {validate_message}",
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[result_relpath],
        )

    if result.ok:
        append_epic_event(
            inp.repo_root,
            inp.epic_id,
            {
                "event": "readiness_passed",
                "at": _now(),
                "epic_id": inp.epic_id,
                "paths": [result_relpath],
            },
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.COMPLETED,
            epic_id=inp.epic_id,
            next_node=NodeType.BREAKDOWN_PLANNING,
            validation_summary=_planning_validation(
                ok=True,
                stage=2,
                check_count=len(result.checks),
                failed_check_count=0,
            ),
            paths=[result_relpath],
        )

    cycles = failed_readiness_cycles(inp.repo_root, inp.epic_id)
    threshold = _readiness_escalation_threshold(inp.repo_root)
    trigger = "readiness_escalation" if cycles >= threshold else "readiness_unready"
    gate = graph_gate_path(inp.repo_root, inp.epic_id)
    if not gate.exists():
        write_gate(
            epic_dir=directory,
            work_unit_id=None,
            triggered_by=[trigger],
            position_text=_readiness_gate_body(inp.epic_id, epic_relpath, result),
            schema_path=schema_dir() / "gate.schema.json",
            validate=True,
            gate_type="readiness_gate",
        )
    failed = sum(1 for check in result.checks if not check.ok and check.severity != "warn")
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=inp.epic_id,
        gate_path=_gate_path(inp.epic_id),
        validation_summary=_planning_validation(
            ok=False,
            stage=2,
            triggered_by=[trigger],
            check_count=len(result.checks),
            failed_check_count=failed,
        ),
        triggered_by=[trigger],
        message="contract readiness gate opened: EPIC.md is not ready for planning",
        paths=[epic_relpath, result_relpath, _gate_path(inp.epic_id)],
    )


def breakdown_planning_node(inp: NodeInput) -> NodeOutput:
    if inp.work_unit_id:
        raise ValueError("breakdown_planning does not accept work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    epic_path = directory / "EPIC.md"
    plan_path = directory / "plan.json"
    plan_md_path = plan_markdown_path(inp.repo_root, inp.epic_id)
    paths = [_relpath(inp.repo_root, plan_path), _relpath(inp.repo_root, plan_md_path)]

    if not epic_path.exists():
        return _planning_halt(
            inp,
            stage=3,
            message=f"Required Stage-3 input missing: {_relpath(inp.repo_root, epic_path)}",
            triggered_by=["incomplete_stage_state"],
            check_count=1,
            failed_check_count=1,
            paths=[_relpath(inp.repo_root, epic_path)],
        )

    epic_ok, epic_message = _validate_epic(inp.repo_root, epic_path)
    if not epic_ok:
        return _planning_halt(
            inp,
            stage=3,
            message=epic_message,
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[_relpath(inp.repo_root, epic_path)],
        )

    if not plan_path.exists():
        carto_refs = _require_cartography_docs(
            inp.repo_root, _BREAKDOWN_PLANNING_CARTOGRAPHY_DOCS, "plan_gate"
        )
        proc = _run_dispatch(
            inp.repo_root,
            role="primary",
            epic_id=inp.epic_id,
            work_unit_id=None,
            prompt=_breakdown_planning_prompt(
                inp.repo_root, inp.epic_id, cartography_refs=carto_refs
            ),
            artefacts_loaded=[
                *_breakdown_planning_artefacts(inp.repo_root, inp.epic_id),
                *carto_refs,
            ],
            route_key="planning",
        )
        dispatch = _classify_dispatch_result(proc)
        if not dispatch.ok:
            return _planning_halt(
                inp,
                stage=3,
                message=dispatch.message,
                triggered_by=["subprocess_crash"],
                check_count=len(paths),
                failed_check_count=len(paths),
                paths=paths,
            )

    if not plan_path.exists():
        return _planning_halt(
            inp,
            stage=3,
            message=f"Breakdown planning did not produce required file: {paths[0]}",
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=paths,
        )

    try:
        ensure_epic_plan_context(inp.repo_root, inp.epic_id, plan_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _planning_halt(
            inp,
            stage=3,
            message=str(exc),
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=paths,
        )

    plan_ok, plan_message, _plan_cache_hit = _validate_plan(inp.repo_root, inp.epic_id, plan_path)
    if not plan_ok:
        return _planning_halt(
            inp,
            stage=3,
            message=plan_message,
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=paths,
        )

    plan_contract_failures = validate_stage3_plan_contract(inp.repo_root, epic_path, plan_path)
    if plan_contract_failures:
        return _planning_halt(
            inp,
            stage=3,
            message=_failure_message(plan_contract_failures),
            triggered_by=["schema_validation_failed"],
            check_count=2,
            failed_check_count=len(plan_contract_failures),
            paths=paths,
        )

    plan = load_plan(inp.repo_root, inp.epic_id)
    plan_md_path.write_text(_render_plan_markdown(plan), encoding="utf-8")
    append_epic_event(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "breakdown_planned",
            "at": _now(),
            "epic_id": inp.epic_id,
            "plan_validate_cache_hit": _plan_cache_hit,
            "paths": paths,
        },
    )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        next_node=NodeType.PLAN_CRITIQUE,
        validation_summary=_planning_validation(
            ok=True,
            stage=3,
            check_count=2,
            failed_check_count=0,
        ),
        paths=paths,
    )


def plan_critique_node(inp: NodeInput) -> NodeOutput:
    if inp.work_unit_id:
        raise ValueError("plan_critique does not accept work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    plan_path = directory / "plan.json"
    plan_md_path = plan_markdown_path(inp.repo_root, inp.epic_id)
    critique_path = plan_critique_path(inp.repo_root, inp.epic_id)
    critique_relpath = _relpath(inp.repo_root, critique_path)

    required = [plan_path, plan_md_path]
    missing = [_relpath(inp.repo_root, path) for path in required if not path.exists()]
    if missing:
        return _planning_halt(
            inp,
            stage=3,
            message="Required Stage-3 critique inputs are missing: " + ", ".join(missing),
            triggered_by=["incomplete_stage_state"],
            check_count=len(required),
            failed_check_count=len(missing),
            paths=[_relpath(inp.repo_root, path) for path in required],
        )

    plan_ok, plan_message, plan_cache_hit = _validate_plan(inp.repo_root, inp.epic_id, plan_path)
    if not plan_ok:
        return _planning_halt(
            inp,
            stage=3,
            message=plan_message,
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[_relpath(inp.repo_root, plan_path)],
        )

    plan_contract_failures = validate_stage3_plan_contract(
        inp.repo_root, directory / "EPIC.md", plan_path
    )
    if plan_contract_failures:
        return _planning_halt(
            inp,
            stage=3,
            message=_failure_message(plan_contract_failures),
            triggered_by=["schema_validation_failed"],
            check_count=2,
            failed_check_count=len(plan_contract_failures),
            paths=[_relpath(inp.repo_root, plan_path)],
        )

    if not critique_path.exists():
        carto_refs = _require_cartography_docs(
            inp.repo_root, _PLAN_CRITIQUE_CARTOGRAPHY_DOCS, "plan_gate"
        )
        critique_path.parent.mkdir(parents=True, exist_ok=True)
        proc = _run_dispatch(
            inp.repo_root,
            role="reviewer",
            epic_id=inp.epic_id,
            work_unit_id=None,
            prompt=_plan_critique_prompt(inp.repo_root, inp.epic_id, cartography_refs=carto_refs),
            artefacts_loaded=[
                *_plan_critique_artefacts(inp.repo_root, inp.epic_id),
                *carto_refs,
            ],
            route_key="planning",
        )
        dispatch = _classify_dispatch_result(proc)
        if not dispatch.ok:
            return _planning_halt(
                inp,
                stage=3,
                message=dispatch.message,
                triggered_by=["reviewer_unreachable"],
                check_count=1,
                failed_check_count=1,
                paths=[critique_relpath],
            )

    if not critique_path.exists():
        return _planning_halt(
            inp,
            stage=3,
            message=f"Plan critique did not produce required file: {critique_relpath}",
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[critique_relpath],
        )

    plan_dict = Plan.model_validate_json(plan_path.read_text()).model_dump(exclude_none=True)
    critique_ok, critique_message = _validate_plan_critique(
        inp.repo_root, critique_path, plan_dict, directory
    )
    if not critique_ok:
        return _planning_halt(
            inp,
            stage=3,
            message=critique_message,
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[critique_relpath],
        )

    critique = read_markdown_front_matter(critique_path)
    severity = critique_severity(critique.front) or "info"
    finding_count = len(critique_findings(critique.front))
    append_epic_event(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "plan_critiqued",
            "at": _now(),
            "epic_id": inp.epic_id,
            "severity": severity,
            "finding_count": finding_count,
            "plan_validate_cache_hit": plan_cache_hit,
            "paths": [critique_relpath],
        },
    )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        next_node=NodeType.PLAN_GATE_OPEN,
        validation_summary=_planning_validation(
            ok=True,
            stage=3,
            check_count=1,
            failed_check_count=0,
        ),
        paths=[critique_relpath],
    )


def _plan_gate_body(
    *,
    epic_id: int,
    plan_relpath: str,
    critique_relpath: str,
    critique: MarkdownFrontMatter,
) -> str:
    front = critique.front
    severity = critique_severity(front) or "info"
    finding_lines = []
    for finding in critique_findings(front):
        finding_id = str(finding.get("id") or "finding")
        finding_severity = str(finding.get("severity") or severity)
        summary = str(finding.get("summary") or "reviewer finding")
        finding_lines.append(f"- {finding_id} [{finding_severity}]: {summary}")
        evidence = finding.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            finding_lines.append(f"  Evidence: {evidence.strip()}")
    if not finding_lines:
        finding_lines.append(f"- Reviewer severity: {severity}; no findings recorded.")

    reviewer_body = critique.body.strip() or "Reviewer body was empty."
    return (
        "## Context\n\n"
        f"Stage 4 plan gate for E{epic_id}. "
        f"`{plan_relpath}` and `{critique_relpath}` are present and valid. "
        "Woof always opens this gate before work-unit execution.\n\n"
        "## Findings\n\n" + "\n".join(finding_lines) + "\n\n## Primary position\n\n"
        f"Source: `{plan_relpath}`\n\n"
        "The primary plan is ready for human review before Stage 5 starts.\n\n"
        "## Reviewer position\n\n"
        f"Source: `{critique_relpath}`\n\n"
        f"{reviewer_body}\n"
    )


def plan_gate_open_node(inp: NodeInput) -> NodeOutput:
    if inp.work_unit_id:
        raise ValueError("plan_gate_open does not accept work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    plan_path = directory / "plan.json"
    plan_md_path = plan_markdown_path(inp.repo_root, inp.epic_id)
    critique_path = plan_critique_path(inp.repo_root, inp.epic_id)
    gate = graph_gate_path(inp.repo_root, inp.epic_id)
    plan_relpath = _relpath(inp.repo_root, plan_path)
    plan_md_relpath = _relpath(inp.repo_root, plan_md_path)
    critique_relpath = _relpath(inp.repo_root, critique_path)
    gate_relpath = _gate_path(inp.epic_id)
    paths = [plan_relpath, plan_md_relpath, critique_relpath, gate_relpath]

    missing = [
        _relpath(inp.repo_root, path)
        for path in (plan_path, plan_md_path, critique_path)
        if not path.exists()
    ]
    if missing:
        return _planning_halt(
            inp,
            stage=4,
            message="Required Stage-4 plan gate inputs are missing: " + ", ".join(missing),
            triggered_by=["incomplete_stage_state"],
            check_count=3,
            failed_check_count=len(missing),
            paths=paths,
        )

    plan_ok, plan_message, _ = _validate_plan(inp.repo_root, inp.epic_id, plan_path)
    if not plan_ok:
        return _planning_halt(
            inp,
            stage=4,
            message=plan_message,
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[plan_relpath],
        )

    plan_contract_failures = validate_stage3_plan_contract(
        inp.repo_root, directory / "EPIC.md", plan_path
    )
    if plan_contract_failures:
        return _planning_halt(
            inp,
            stage=4,
            message=_failure_message(plan_contract_failures),
            triggered_by=["schema_validation_failed"],
            check_count=2,
            failed_check_count=len(plan_contract_failures),
            paths=[plan_relpath],
        )

    plan_dict = Plan.model_validate_json(plan_path.read_text()).model_dump(exclude_none=True)
    critique_ok, critique_message = _validate_plan_critique(
        inp.repo_root, critique_path, plan_dict, directory
    )
    if not critique_ok:
        return _planning_halt(
            inp,
            stage=4,
            message=critique_message,
            triggered_by=["schema_validation_failed"],
            check_count=1,
            failed_check_count=1,
            paths=[critique_relpath],
        )

    if not gate.exists():
        critique = read_markdown_front_matter(critique_path)
        write_gate(
            epic_dir=directory,
            work_unit_id=None,
            triggered_by=["plan_review"],
            position_text=_plan_gate_body(
                epic_id=inp.epic_id,
                plan_relpath=plan_md_relpath,
                critique_relpath=critique_relpath,
                critique=critique,
            ),
            schema_path=schema_dir() / "gate.schema.json",
            validate=True,
            gate_type="plan_gate",
        )

    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=inp.epic_id,
        gate_path=gate_relpath,
        validation_summary=_planning_validation(
            ok=True,
            stage=4,
            triggered_by=["plan_review"],
            check_count=3,
            failed_check_count=0,
        ),
        triggered_by=["plan_review"],
        message="plan gate opened after valid plan and critique",
        paths=paths,
    )


def executor_dispatch_node(inp: NodeInput) -> NodeOutput:
    if not inp.work_unit_id:
        raise ValueError("executor_dispatch requires work_unit_id")
    carto_refs = _require_cartography_docs(
        inp.repo_root, _EXECUTOR_CARTOGRAPHY_DOCS, "work_unit_gate", work_unit_id=inp.work_unit_id
    )
    plan = load_plan(inp.repo_root, inp.epic_id)
    work_unit = work_unit_by_id(plan, inp.work_unit_id)
    files_txt_slice = _work_unit_files_txt_slice(inp.repo_root, work_unit)
    mark_work_unit_state(inp.repo_root, inp.epic_id, inp.work_unit_id, "in_progress")
    proc = _run_dispatch(
        inp.repo_root,
        role="primary",
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        prompt=_executor_dispatch_prompt(
            inp.repo_root,
            inp.epic_id,
            inp.work_unit_id,
            cartography_refs=carto_refs,
            files_txt_slice=files_txt_slice,
        ),
        artefacts_loaded=[
            *_work_unit_context_artefacts(inp.repo_root, inp.epic_id),
            *carto_refs,
            *([_codebase_doc_relpath("files.txt")] if files_txt_slice is not None else []),
        ],
        route_key="execution",
        session_mode="warm-producer",
    )
    dispatch = _classify_dispatch_result(proc)
    if not dispatch.ok:
        write_gate_for_trigger(
            trigger="subprocess_crash",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=inp.work_unit_id,
            exit_code=dispatch.gate_exit_code,
            schema_path=schema_dir() / "gate.schema.json",
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["subprocess_crash"],
            message=dispatch.message,
        )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        next_node=NodeType.CRITIQUE_DISPATCH,
    )


def critique_dispatch_node(inp: NodeInput) -> NodeOutput:
    if not inp.work_unit_id:
        raise ValueError("critique_dispatch requires work_unit_id")
    carto_refs = _require_cartography_docs(
        inp.repo_root,
        _CRITIQUE_DISPATCH_CARTOGRAPHY_DOCS,
        "work_unit_gate",
        work_unit_id=inp.work_unit_id,
    )
    try:
        _stage_changed_work_unit_paths(inp.repo_root, inp.epic_id, inp.work_unit_id)
    except (subprocess.CalledProcessError, StageStateError, ValueError) as exc:
        return _write_position_gate(
            inp,
            trigger="incomplete_stage_state",
            position=f"Work-unit paths could not be staged before reviewer critique: {exc}",
        )
    prompt = _work_unit_critique_prompt(
        inp.repo_root, inp.epic_id, inp.work_unit_id, cartography_refs=carto_refs
    )
    proc = _run_dispatch(
        inp.repo_root,
        role="reviewer",
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        prompt=prompt,
        artefacts_loaded=[
            *_work_unit_context_artefacts(inp.repo_root, inp.epic_id),
            *carto_refs,
        ],
        route_key="execution",
    )
    dispatch = _classify_dispatch_result(proc)
    if not dispatch.ok:
        write_gate_for_trigger(
            trigger="reviewer_unreachable",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=inp.work_unit_id,
            exit_code=None,
            schema_path=schema_dir() / "gate.schema.json",
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["reviewer_unreachable"],
            message=dispatch.message,
        )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        next_node=NodeType.REVIEW_DISPOSITION,
    )


def _write_position_gate(inp: NodeInput, *, trigger: str, position: str) -> NodeOutput:
    position_path = epic_dir(inp.repo_root, inp.epic_id) / "gate-position.md"
    position_path.write_text(position)
    try:
        write_gate_for_trigger(
            trigger=trigger,
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=inp.work_unit_id,
            position_path=position_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
    finally:
        position_path.unlink(missing_ok=True)
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        gate_path=_gate_path(inp.epic_id),
        triggered_by=[trigger],
        message=position,
    )


def _write_disposition_incomplete_gate(inp: NodeInput, message: str) -> NodeOutput:
    return _write_position_gate(
        inp,
        trigger="incomplete_stage_state",
        position=(
            f"{message}\n\n"
            "The graph cannot continue until the reviewer critique and primary disposition "
            "are restored to a valid, matching state."
        ),
    )


def review_disposition_node(inp: NodeInput) -> NodeOutput:
    if not inp.work_unit_id:
        raise ValueError("review_disposition requires work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    critique_path = work_unit_critique_path(directory, inp.work_unit_id)

    try:
        critique = read_markdown_front_matter(critique_path)
    except (FileNotFoundError, FrontMatterError) as exc:
        return _write_disposition_incomplete_gate(inp, f"Reviewer critique is unreadable: {exc}")

    severity = critique_severity(critique.front)
    if severity not in {"info", "minor", "blocker"}:
        return _write_disposition_incomplete_gate(
            inp,
            "Reviewer critique severity must be info, minor, or blocker.",
        )

    plan_path = epic_dir(inp.repo_root, inp.epic_id) / "plan.json"
    try:
        plan_dict = Plan.model_validate_json(plan_path.read_text()).model_dump(exclude_none=True)
    except (OSError, ValueError):
        plan_dict = {}
    invariant_errors = validate_critique_invariants(
        critique.front,
        repo_root=inp.repo_root,
        plan=plan_dict,
        epic_dir=epic_dir(inp.repo_root, inp.epic_id),
    )
    if invariant_errors:
        return _write_disposition_incomplete_gate(inp, "\n".join(invariant_errors))

    if severity == "blocker":
        blocker_signature = _blocker_signature(critique)
        budget = _fix_round_budget(inp.repo_root)
        rounds_used = _fix_rounds_used(
            inp.repo_root, inp.epic_id, inp.work_unit_id, blocker_signature
        )
        if rounds_used < budget:
            carto_refs = _require_cartography_docs(
                inp.repo_root,
                _EXECUTOR_CARTOGRAPHY_DOCS,
                "work_unit_gate",
                work_unit_id=inp.work_unit_id,
            )
            work_unit = work_unit_by_id(load_plan(inp.repo_root, inp.epic_id), inp.work_unit_id)
            files_txt_slice = _work_unit_files_txt_slice(inp.repo_root, work_unit)
            base_prompt = _executor_dispatch_prompt(
                inp.repo_root,
                inp.epic_id,
                inp.work_unit_id,
                cartography_refs=carto_refs,
                files_txt_slice=files_txt_slice,
            )
            round_number = rounds_used + 1
            prompt = _fix_round_prompt(
                inp.repo_root,
                inp.epic_id,
                inp.work_unit_id,
                critique=critique,
                base_prompt=base_prompt,
            )
            append_epic_event(
                inp.repo_root,
                inp.epic_id,
                {
                    "event": "work_unit_fix_round_started",
                    "at": _now(),
                    "epic_id": inp.epic_id,
                    "work_unit_id": inp.work_unit_id,
                    "round": round_number,
                    "max_rounds_per_blocker": budget,
                    "blocker_signature": blocker_signature,
                    "critique_path": work_unit_critique_path(directory, inp.work_unit_id)
                    .relative_to(inp.repo_root)
                    .as_posix(),
                },
            )
            _clear_fix_round_artefacts(inp.repo_root, inp.epic_id, inp.work_unit_id)
            proc = _run_dispatch(
                inp.repo_root,
                role="primary",
                epic_id=inp.epic_id,
                work_unit_id=inp.work_unit_id,
                prompt=prompt,
                artefacts_loaded=[
                    *_work_unit_context_artefacts(inp.repo_root, inp.epic_id),
                    *carto_refs,
                    _codebase_doc_relpath("files.txt"),
                ],
                route_key="execution",
                session_mode="warm-producer",
            )
            dispatch = _classify_dispatch_result(proc)
            if not dispatch.ok:
                write_gate_for_trigger(
                    trigger="subprocess_crash",
                    epic_dir=directory,
                    work_unit_id=inp.work_unit_id,
                    exit_code=dispatch.gate_exit_code,
                    schema_path=schema_dir() / "gate.schema.json",
                )
                return NodeOutput(
                    node_type=inp.node_type,
                    status=NodeStatus.GATE_OPENED,
                    epic_id=inp.epic_id,
                    work_unit_id=inp.work_unit_id,
                    gate_path=_gate_path(inp.epic_id),
                    triggered_by=["subprocess_crash"],
                    message=dispatch.message,
                )
            work_unit_critique_path(directory, inp.work_unit_id).unlink(missing_ok=True)
            append_epic_event(
                inp.repo_root,
                inp.epic_id,
                {
                    "event": "work_unit_fix_round_completed",
                    "at": _now(),
                    "epic_id": inp.epic_id,
                    "work_unit_id": inp.work_unit_id,
                    "round": round_number,
                    "max_rounds_per_blocker": budget,
                    "blocker_signature": blocker_signature,
                },
            )
            return NodeOutput(
                node_type=inp.node_type,
                status=NodeStatus.COMPLETED,
                epic_id=inp.epic_id,
                work_unit_id=inp.work_unit_id,
                next_node=NodeType.CRITIQUE_DISPATCH,
                message=(
                    f"reviewer blocker returned to warm producer "
                    f"({round_number}/{budget} fix rounds)"
                ),
            )
        body = reviewer_blocker_gate_body(
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            critique=critique,
        )
        return _write_position_gate(
            inp,
            trigger="check_6_critique_blocker",
            position=body,
        )

    disposition_path = work_unit_disposition_path(directory, inp.work_unit_id)
    if not disposition_path.exists():
        write_deterministic_work_unit_disposition(
            epic_dir=directory,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            critique=critique,
            timestamp=_now(),
        )

    validation = validate_work_unit_disposition(directory, inp.epic_id, inp.work_unit_id)
    if not validation.ok:
        write_deterministic_work_unit_disposition(
            epic_dir=directory,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            critique=critique,
            timestamp=_now(),
        )
        validation = validate_work_unit_disposition(directory, inp.epic_id, inp.work_unit_id)
        if not validation.ok:
            return _write_disposition_incomplete_gate(
                inp,
                "Primary disposition is invalid: " + "; ".join(validation.errors),
            )

    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        next_node=NodeType.VERIFICATION,
        paths=[work_unit_disposition_relpath(inp.epic_id, inp.work_unit_id)],
    )


def _stage_changed_work_unit_paths(repo_root: Path, epic_id: int, work_unit_id: str) -> list[str]:
    """Stage changed files that belong to the active work-unit path scope."""

    plan = load_plan(repo_root, epic_id)
    work_unit = work_unit_by_id(plan, work_unit_id)
    candidate_paths = [path for path in changed_paths(repo_root) if not path.startswith(".woof/")]
    try:
        work_unit_paths = filter_paths_matching(repo_root, candidate_paths, list(work_unit.paths))
    except PathspecEvaluationError as exc:
        raise StageStateError(f"work-unit pathspec evaluation failed: {exc}") from exc
    if work_unit_paths:
        git(repo_root, "add", "--", *work_unit_paths)
    return work_unit_paths


def _stage_work_unit_transaction_paths(
    repo_root: Path, epic_id: int, work_unit_id: str
) -> list[str]:
    """Stage work-unit and graph-owned files before Stage-5 commit-readiness checks.

    The producer owns work-unit content. The graph owns the transaction boundary:
    changed files within `work_unit.paths[]`, durable `.woof` state, dispatch audit
    files, critiques, dispositions, and JSONL events must be staged together so
    deterministic checks and reviewer critique inspect the same candidate diff.
    """

    plan = load_plan(repo_root, epic_id)
    work_unit = work_unit_by_id(plan, work_unit_id)
    work_unit_paths = _stage_changed_work_unit_paths(repo_root, epic_id, work_unit_id)
    manifest = build_work_unit_manifest(repo_root, epic_id, work_unit)
    graph_paths = [path for path in manifest.expected_paths if path.startswith(".woof/")]
    if graph_paths:
        git(repo_root, "add", "--", *graph_paths)
    return sorted(set(work_unit_paths + graph_paths))


def verification_node(inp: NodeInput) -> NodeOutput:
    if not inp.work_unit_id:
        raise ValueError("verification requires work_unit_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    result_path = epic_dir(inp.repo_root, inp.epic_id) / "check-result.json"
    try:
        _stage_work_unit_transaction_paths(inp.repo_root, inp.epic_id, inp.work_unit_id)
        prepare_commit_audit(inp.repo_root, directory)
        _stage_work_unit_transaction_paths(inp.repo_root, inp.epic_id, inp.work_unit_id)
    except (subprocess.CalledProcessError, StageStateError, ValueError) as exc:
        return _write_position_gate(
            inp,
            trigger="incomplete_stage_state",
            position=f"Work-unit transaction artefacts could not be staged: {exc}",
        )
    proc = subprocess.run(
        [
            *_woof_subprocess_argv(),
            "check",
            "stage-5",
            "--epic",
            str(inp.epic_id),
            "--work-unit",
            inp.work_unit_id,
            "--format",
            "json",
        ],
        cwd=inp.repo_root,
        env=_woof_subprocess_env(),
        capture_output=True,
        text=True,
    )
    if proc.stdout.strip():
        result_path.write_text(proc.stdout)
    validation_summary = _validation_summary_from_path(result_path)
    if proc.returncode != 0:
        if result_path.exists():
            write_gate_from_check_result(
                check_result_path=result_path,
                position_path=None,
                epic_dir=epic_dir(inp.repo_root, inp.epic_id),
                work_unit_id=inp.work_unit_id,
                schema_path=schema_dir() / "gate.schema.json",
            )
        else:
            write_gate_for_trigger(
                trigger="schema_validation_failed",
                epic_dir=epic_dir(inp.repo_root, inp.epic_id),
                work_unit_id=inp.work_unit_id,
                schema_path=schema_dir() / "gate.schema.json",
            )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            gate_path=_gate_path(inp.epic_id),
            validation_summary=validation_summary,
            triggered_by=validation_summary.triggered_by if validation_summary else [],
            message=proc.stderr.strip(),
        )
    try:
        _record_verified_index(inp.repo_root, result_path)
    except (OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        return _write_position_gate(
            inp,
            trigger="check_7_commit_transaction",
            position=f"Verified index could not be pinned after checks passed: {exc}",
        )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        next_node=NodeType.COMMIT,
        validation_summary=validation_summary,
        paths=[str(result_path.relative_to(inp.repo_root))],
    )


def _executor_result(repo_root: Path, epic_id: int) -> dict:
    path = epic_dir(repo_root, epic_id) / "executor_result.json"
    return json.loads(path.read_text())


def _commit_message(
    epic_id: int, work_unit_title: str, work_unit_id: str, executor_result: dict | None = None
) -> str:
    if executor_result:
        subject = str(executor_result.get("commit_subject") or "").strip()
        if subject:
            return " ".join(subject.splitlines())
    return f"feat: E{epic_id} {work_unit_id} - {work_unit_title}"


def commit_node(inp: NodeInput) -> NodeOutput:
    if not inp.work_unit_id:
        raise ValueError("commit requires work_unit_id")

    # Drift check: verify HEAD/branch haven't moved since the last dispatch.
    # subprocess_returned events live in dispatch.jsonl, not epic.jsonl.
    dispatch_jsonl = epic_dir(inp.repo_root, inp.epic_id) / "dispatch.jsonl"
    dispatch_events = _read_appended_dispatch_events(dispatch_jsonl, 0)
    last_returned = next(
        (
            e
            for e in reversed(dispatch_events)
            if e.get("event") == "subprocess_returned" and e.get("work_unit_id") == inp.work_unit_id
        ),
        None,
    )
    if last_returned is not None:
        expected_sha = last_returned.get("head_after")
        expected_branch = last_returned.get("branch_after")
        if not isinstance(expected_sha, str):
            expected_sha = None
        if not isinstance(expected_branch, str):
            expected_branch = None
        drift, drift_desc = head_branch_drift_detected(inp.repo_root, expected_sha, expected_branch)
        if drift:
            write_gate_for_trigger(
                trigger="head_branch_drift",
                epic_dir=epic_dir(inp.repo_root, inp.epic_id),
                work_unit_id=inp.work_unit_id,
                schema_path=schema_dir() / "gate.schema.json",
            )
            return NodeOutput(
                node_type=inp.node_type,
                status=NodeStatus.GATE_OPENED,
                epic_id=inp.epic_id,
                work_unit_id=inp.work_unit_id,
                gate_path=_gate_path(inp.epic_id),
                triggered_by=["head_branch_drift"],
                message=drift_desc,
            )

    plan = load_plan(inp.repo_root, inp.epic_id)
    work_unit = work_unit_by_id(plan, inp.work_unit_id)
    result = _executor_result(inp.repo_root, inp.epic_id)
    directory = epic_dir(inp.repo_root, inp.epic_id)

    try:
        prepare_commit_audit(inp.repo_root, directory)
    except (OSError, ValueError) as exc:
        position = f"Audit preparation failed before commit: {exc}\n"
        pos_path = directory / "audit-position.md"
        pos_path.write_text(position)
        write_gate_for_trigger(
            trigger="audit_redaction",
            epic_dir=directory,
            work_unit_id=inp.work_unit_id,
            position_path=pos_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
        pos_path.unlink(missing_ok=True)
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["audit_redaction"],
            message=position,
        )

    check_result_path = directory / "check-result.json"
    try:
        check_result = (
            json.loads(check_result_path.read_text(encoding="utf-8"))
            if check_result_path.exists()
            else {}
        )
    except (OSError, json.JSONDecodeError) as exc:
        check_result = {}
        pin_error = f"check-result.json could not be read for verified transaction pins: {exc}"
    else:
        pin_error = _check_verified_index(inp.repo_root, check_result)
        if pin_error is not None and _completed_resume_delta_is_graph_owned(
            inp.repo_root,
            inp.epic_id,
            inp.work_unit_id,
            check_result,
        ):
            pin_error = None
    if pin_error is not None:
        position = f"Verified transaction changed before commit: {pin_error}\n"
        pos_path = epic_dir(inp.repo_root, inp.epic_id) / "verified-index-position.md"
        pos_path.write_text(position)
        write_gate_for_trigger(
            trigger="check_7_commit_transaction",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=inp.work_unit_id,
            position_path=pos_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
        pos_path.unlink(missing_ok=True)
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["check_7_commit_transaction"],
            message=position,
        )

    manifest = build_work_unit_manifest(inp.repo_root, inp.epic_id, work_unit)
    if not manifest.audit_paths:
        write_gate_for_trigger(
            trigger="check_7_commit_transaction",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=inp.work_unit_id,
            schema_path=schema_dir() / "gate.schema.json",
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["check_7_commit_transaction"],
            message="transaction manifest has no audit files",
        )

    staged_extra = [
        path for path in staged_paths(inp.repo_root) if path not in manifest.expected_paths
    ]
    if staged_extra:
        position = f"Transaction manifest mismatch.\n\nUnexpected staged paths: {staged_extra}\n"
        pos_path = epic_dir(inp.repo_root, inp.epic_id) / "manifest-position.md"
        pos_path.write_text(position)
        write_gate_for_trigger(
            trigger="check_7_commit_transaction",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=inp.work_unit_id,
            position_path=pos_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
        pos_path.unlink(missing_ok=True)
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["check_7_commit_transaction"],
            message=position,
        )

    mark_work_unit_state(inp.repo_root, inp.epic_id, inp.work_unit_id, "done")
    append_epic_event_once(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "work_unit_completed",
            "at": _now(),
            "epic_id": inp.epic_id,
            "work_unit_id": inp.work_unit_id,
        },
        event="work_unit_completed",
        work_unit_id=inp.work_unit_id,
    )

    git(inp.repo_root, "add", "--", *manifest.expected_paths)
    verification = verify_staged_manifest(inp.repo_root, manifest)
    if not verification.ok:
        position = (
            "Transaction manifest mismatch.\n\n"
            f"Missing staged paths: {verification.missing_paths}\n"
            f"Unexpected staged paths: {verification.extra_paths}\n"
        )
        pos_path = epic_dir(inp.repo_root, inp.epic_id) / "manifest-position.md"
        pos_path.write_text(position)
        write_gate_for_trigger(
            trigger="check_7_commit_transaction",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=inp.work_unit_id,
            position_path=pos_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
        pos_path.unlink(missing_ok=True)
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            work_unit_id=inp.work_unit_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["check_7_commit_transaction"],
            message=position,
        )

    append_epic_event_once(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "transaction_manifest_verified",
            "at": _now(),
            "epic_id": inp.epic_id,
            "work_unit_id": inp.work_unit_id,
            "manifest": manifest.model_dump(),
        },
        event="transaction_manifest_verified",
        work_unit_id=inp.work_unit_id,
    )
    completed_plan = load_plan(inp.repo_root, inp.epic_id)
    if all(candidate.state in TERMINAL_WORK_UNIT_STATES for candidate in completed_plan.work_units):
        append_epic_event_once(
            inp.repo_root,
            inp.epic_id,
            {
                "event": "epic_completed",
                "at": _now(),
                "epic_id": inp.epic_id,
            },
            event="epic_completed",
        )
    git(inp.repo_root, "add", "--", f".woof/epics/E{inp.epic_id}/epic.jsonl")

    message = _commit_message(inp.epic_id, work_unit.title, inp.work_unit_id, result)
    body = result.get("commit_body")
    args = ["commit", "-m", message]
    if body:
        args.extend(["-m", body])
    git(inp.repo_root, *args)
    (epic_dir(inp.repo_root, inp.epic_id) / "executor_result.json").unlink(missing_ok=True)
    (epic_dir(inp.repo_root, inp.epic_id) / "check-result.json").unlink(missing_ok=True)
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        paths=manifest.expected_paths,
    )


def gate_open_node(inp: NodeInput) -> NodeOutput:
    if not inp.work_unit_id:
        write_gate_for_trigger(
            trigger=inp.reason or "manual",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=None,
            schema_path=schema_dir() / "gate.schema.json",
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=[inp.reason or "manual"],
        )

    directory = epic_dir(inp.repo_root, inp.epic_id)
    result_path = directory / "executor_result.json"
    check_result_path = directory / "check-result.json"
    trigger = inp.reason or "manual"
    position_path = None

    if not result_path.exists():
        return _write_incomplete_stage_gate(
            inp,
            f"Required Stage-5 artefact missing: {result_path.relative_to(inp.repo_root)}",
        )

    try:
        result = json.loads(result_path.read_text())
    except json.JSONDecodeError:
        return _write_incomplete_stage_gate(
            inp,
            f"Required Stage-5 artefact is malformed JSON: {result_path.relative_to(inp.repo_root)}",
        )

    outcome = result.get("outcome")
    if outcome == "aborted_with_position":
        trigger = "executor_aborted"
    elif outcome == "empty_diff":
        trigger = "empty_diff_review"
    elif outcome == "staged_for_verification" and check_result_path.exists():
        try:
            check_result = json.loads(check_result_path.read_text())
        except json.JSONDecodeError:
            return _write_incomplete_stage_gate(
                inp,
                "Required Stage-5 artefact is malformed JSON: "
                f"{check_result_path.relative_to(inp.repo_root)}",
            )
        if not check_result.get("ok", False):
            write_gate_from_check_result(
                check_result_path=check_result_path,
                position_path=None,
                epic_dir=directory,
                work_unit_id=inp.work_unit_id,
                schema_path=schema_dir() / "gate.schema.json",
            )
            return NodeOutput(
                node_type=inp.node_type,
                status=NodeStatus.GATE_OPENED,
                epic_id=inp.epic_id,
                work_unit_id=inp.work_unit_id,
                gate_path=_gate_path(inp.epic_id),
                validation_summary=_validation_summary(check_result),
                triggered_by=check_result.get("triggered_by") or ["schema_validation_failed"],
            )
    elif outcome != "staged_for_verification":
        return _write_incomplete_stage_gate(
            inp,
            "Required Stage-5 artefact has an unsupported executor outcome: "
            f"{result_path.relative_to(inp.repo_root)} outcome={outcome!r}",
        )

    if result.get("position"):
        position_path = directory / "gate-position.md"
        position_path.write_text(result["position"])

    write_gate_for_trigger(
        trigger=trigger,
        epic_dir=directory,
        work_unit_id=inp.work_unit_id,
        position_path=position_path,
        schema_path=schema_dir() / "gate.schema.json",
    )
    if position_path:
        position_path.unlink(missing_ok=True)
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        gate_path=_gate_path(inp.epic_id),
        triggered_by=[trigger],
    )


def _write_incomplete_stage_gate(inp: NodeInput, position: str) -> NodeOutput:
    position_path = epic_dir(inp.repo_root, inp.epic_id) / "gate-position.md"
    position_path.write_text(
        f"{position}\n\n"
        "The graph cannot safely infer or recreate this state. "
        "Resolve the gate by restoring the required artefact, revising the work-unit state, "
        "or explicitly abandoning the work unit."
    )
    try:
        write_gate_for_trigger(
            trigger="incomplete_stage_state",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            work_unit_id=inp.work_unit_id,
            position_path=position_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
    finally:
        position_path.unlink(missing_ok=True)
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        gate_path=_gate_path(inp.epic_id),
        triggered_by=["incomplete_stage_state"],
        message=position,
    )


def human_review_node(inp: NodeInput) -> NodeOutput:
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.HALTED,
        epic_id=inp.epic_id,
        work_unit_id=inp.work_unit_id,
        gate_path=_gate_path(inp.epic_id),
        message=_gate_operator_message(inp.repo_root, inp.epic_id),
    )


def default_registry() -> dict[NodeType, NodeHandler]:
    return {
        NodeType.DISCOVERY_RESEARCH: discovery_research_node,
        NodeType.DISCOVERY_THINKING: discovery_thinking_node,
        NodeType.DISCOVERY_IDEATE: discovery_ideate_node,
        NodeType.DISCOVERY_SYNTHESIS: discovery_synthesis_node,
        NodeType.EPIC_DEFINITION: epic_definition_node,
        NodeType.CONTRACT_READINESS: contract_readiness_node,
        NodeType.BREAKDOWN_PLANNING: breakdown_planning_node,
        NodeType.PLAN_CRITIQUE: plan_critique_node,
        NodeType.PLAN_GATE_OPEN: plan_gate_open_node,
        NodeType.EXECUTOR_DISPATCH: executor_dispatch_node,
        NodeType.CRITIQUE_DISPATCH: critique_dispatch_node,
        NodeType.REVIEW_DISPOSITION: review_disposition_node,
        NodeType.VERIFICATION: verification_node,
        NodeType.COMMIT: commit_node,
        NodeType.GATE_OPEN: gate_open_node,
        NodeType.HUMAN_REVIEW: human_review_node,
    }
