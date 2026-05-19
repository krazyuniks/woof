"""Node registry and implementations for ADR-001."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from woof.gate.write import write_gate, write_gate_for_trigger, write_gate_from_check_result
from woof.graph.dispositions import (
    FrontMatterError,
    MarkdownFrontMatter,
    critique_findings,
    critique_severity,
    read_markdown_front_matter,
    reviewer_blocker_gate_body,
    story_critique_path,
    story_disposition_path,
    story_disposition_relpath,
    validate_story_disposition,
)
from woof.graph.git import git, staged_paths
from woof.graph.manifest import build_story_manifest, verify_staged_manifest
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, Plan, ValidationSummary
from woof.graph.transitions import (
    StageStateError,
    append_epic_event,
    append_epic_event_once,
    discovery_synthesis_complete,
    discovery_synthesis_dir,
    discovery_synthesis_paths,
    epic_dir,
    load_plan,
    mark_story_status,
    plan_critique_path,
    plan_markdown_path,
    story_by_id,
)
from woof.graph.transitions import (
    gate_path as graph_gate_path,
)
from woof.lib.audit import prepare_commit_audit
from woof.paths import schema_dir, tool_root

NodeHandler = Callable[[NodeInput], NodeOutput]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _woof_bin() -> Path:
    return tool_root() / "bin" / "woof"


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


def _write_prompt_file(text: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        return Path(handle.name)


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


def _discovery_synthesis_payload(repo_root: Path, epic_id: int) -> dict:
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
    return payload


def _discovery_synthesis_artefacts(repo_root: Path, epic_id: int) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    source_paths = [repo_root / path for path in _discovery_source_paths(repo_root, epic_id)]
    return _existing_prompt_artefacts(repo_root, [directory / "spark.md", *source_paths])


def _epic_definition_payload(repo_root: Path, epic_id: int) -> dict:
    directory = epic_dir(repo_root, epic_id)
    return {
        "node_type": NodeType.EPIC_DEFINITION.value,
        "epic_id": epic_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": {
            "synthesis_dir": _relpath(repo_root, discovery_synthesis_dir(repo_root, epic_id)),
            "epic_path": _relpath(repo_root, directory / "EPIC.md"),
        },
    }


def _epic_definition_artefacts(repo_root: Path, epic_id: int) -> list[str]:
    return _existing_prompt_artefacts(
        repo_root,
        list(discovery_synthesis_paths(repo_root, epic_id).values()),
    )


def _breakdown_planning_payload(repo_root: Path, epic_id: int) -> dict:
    directory = epic_dir(repo_root, epic_id)
    return {
        "node_type": NodeType.BREAKDOWN_PLANNING.value,
        "epic_id": epic_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": {
            "epic_path": _relpath(repo_root, directory / "EPIC.md"),
            "plan_path": _relpath(repo_root, directory / "plan.json"),
            "plan_markdown_path": _relpath(repo_root, plan_markdown_path(repo_root, epic_id)),
        },
    }


def _breakdown_planning_artefacts(repo_root: Path, epic_id: int) -> list[str]:
    return _existing_prompt_artefacts(repo_root, [epic_dir(repo_root, epic_id) / "EPIC.md"])


def _plan_critique_payload(repo_root: Path, epic_id: int) -> dict:
    directory = epic_dir(repo_root, epic_id)
    return {
        "node_type": NodeType.PLAN_CRITIQUE.value,
        "epic_id": epic_id,
        "repo_root": str(repo_root),
        "epic_dir": _relpath(repo_root, directory),
        "inputs": {
            "epic_path": _relpath(repo_root, directory / "EPIC.md"),
            "plan_path": _relpath(repo_root, directory / "plan.json"),
            "plan_markdown_path": _relpath(repo_root, plan_markdown_path(repo_root, epic_id)),
            "critique_path": _relpath(repo_root, plan_critique_path(repo_root, epic_id)),
        },
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


def _discovery_synthesis_prompt(repo_root: Path, epic_id: int) -> str:
    payload = _discovery_synthesis_payload(repo_root, epic_id)
    return _prompt_template(
        tool_root() / "playbooks" / "discovery" / "synthesis.md",
        {"planning_input_json": json.dumps(payload, indent=2, sort_keys=True)},
    )


def _epic_definition_prompt(repo_root: Path, epic_id: int) -> str:
    payload = _epic_definition_payload(repo_root, epic_id)
    return _prompt_template(
        tool_root() / "playbooks" / "discovery" / "definition.md",
        {"planning_input_json": json.dumps(payload, indent=2, sort_keys=True)},
    )


def _breakdown_planning_prompt(repo_root: Path, epic_id: int) -> str:
    payload = _breakdown_planning_payload(repo_root, epic_id)
    return _prompt_template(
        tool_root() / "playbooks" / "planning" / "breakdown.md",
        {"planning_input_json": json.dumps(payload, indent=2, sort_keys=True)},
    )


def _plan_critique_prompt(repo_root: Path, epic_id: int) -> str:
    payload = _plan_critique_payload(repo_root, epic_id)
    template = (tool_root() / "playbooks" / "critique" / "plan.md").read_text(encoding="utf-8")
    return (
        "Graph-owned input:\n\n"
        "```json\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
        "```\n\n"
        f"{template}"
    )


def _validate_epic(repo_root: Path, epic_path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [str(_woof_bin()), "validate", "--schema", "epic", str(epic_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def _validate_plan(repo_root: Path, epic_id: int, plan_path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [str(_woof_bin()), "validate", "--schema", "plan", str(plan_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return False, (proc.stdout + proc.stderr).strip()
    try:
        plan = load_plan(repo_root, epic_id)
    except (StageStateError, ValueError) as exc:
        return False, str(exc)
    if plan.epic_id != epic_id:
        return False, f"plan epic_id {plan.epic_id} does not match E{epic_id}"
    return True, (proc.stdout + proc.stderr).strip()


def _validate_plan_critique(repo_root: Path, critique_path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [str(_woof_bin()), "validate", "--schema", "critique", str(critique_path)],
        cwd=repo_root,
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
    return True, (proc.stdout + proc.stderr).strip()


def _table_cell(value: object) -> str:
    text = str(value)
    return text.replace("\n", " ").replace("|", "\\|").strip()


def _csv(items: list[str]) -> str:
    return ", ".join(items) if items else "-"


def _render_plan_markdown(plan: Plan) -> str:
    out = [
        f"# Plan E{plan.epic_id}\n\n",
        f"{plan.goal}\n\n",
        "## Stories\n\n",
        "| ID | Title | Status | Satisfies | Implements CDs | Uses CDs | Depends On | Paths | Tests |\n",
        "|---|---|---|---|---|---|---|---|---|\n",
    ]
    for story in plan.stories:
        tests = story.tests if isinstance(story.tests, dict) else {}
        test_types = tests.get("types", [])
        if not isinstance(test_types, list):
            test_types = []
        test_count = tests.get("count", 0)
        out.append(
            "| "
            f"{_table_cell(story.id)} | "
            f"{_table_cell(story.title)} | "
            f"{_table_cell(story.status)} | "
            f"{_table_cell(_csv(story.satisfies))} | "
            f"{_table_cell(_csv(story.implements_contract_decisions))} | "
            f"{_table_cell(_csv(story.uses_contract_decisions))} | "
            f"{_table_cell(_csv(story.depends_on))} | "
            f"{_table_cell(_csv(story.paths))} | "
            f"{_table_cell(str(test_count) + ' ' + _csv([str(item) for item in test_types]))} |\n"
        )
    out.append("\n")
    return "".join(out)


def _story_prompt(epic_id: int, story_id: str) -> str:
    return f"""You are executing story {story_id} in epic E{epic_id}.

Read:
1. .woof/.current-epic
2. .woof/epics/E{epic_id}/plan.json
3. .woof/epics/E{epic_id}/EPIC.md
4. CLAUDE.md / AGENTS.md if present

Invoke /wf:execute-story with arguments "E{epic_id} {story_id}".
Produce .woof/epics/E{epic_id}/executor_result.json and exit.
Do not dispatch critique, verify, open gates, or commit.
"""


def _story_context_artefacts(repo_root: Path, epic_id: int) -> list[str]:
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


def _disposition_artefacts(repo_root: Path, epic_id: int, story_id: str) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    return _existing_prompt_artefacts(
        repo_root,
        [
            directory / "EPIC.md",
            directory / "plan.json",
            story_critique_path(directory, story_id),
        ],
    )


def _disposition_prompt(epic_id: int, story_id: str) -> str:
    template = (tool_root() / "playbooks" / "disposition" / "story.md").read_text()
    return template.format(epic_id=epic_id, story_id=story_id)


def _run_dispatch(
    repo_root: Path,
    role: str,
    epic_id: int,
    story_id: str | None,
    prompt: str,
    artefacts_loaded: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    prompt_file = _write_prompt_file(prompt)
    try:
        args = [
            str(_woof_bin()),
            "dispatch",
            "--role",
            role,
            "--epic",
            str(epic_id),
            "--prompt-file",
            str(prompt_file),
        ]
        if story_id:
            args.extend(["--story", story_id])
        for artefact in artefacts_loaded or []:
            args.extend(["--artefact", artefact])
        return subprocess.run(args, cwd=repo_root, capture_output=True, text=True)
    finally:
        prompt_file.unlink(missing_ok=True)


def discovery_synthesis_node(inp: NodeInput) -> NodeOutput:
    if inp.story_id:
        raise ValueError("discovery_synthesis does not accept story_id")
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
        discovery_synthesis_dir(inp.repo_root, inp.epic_id).mkdir(parents=True, exist_ok=True)
        proc = _run_dispatch(
            inp.repo_root,
            role="primary",
            epic_id=inp.epic_id,
            story_id=None,
            prompt=_discovery_synthesis_prompt(inp.repo_root, inp.epic_id),
            artefacts_loaded=_discovery_synthesis_artefacts(inp.repo_root, inp.epic_id),
        )
        if proc.returncode != 0:
            return _planning_halt(
                inp,
                stage=1,
                message=proc.stderr.strip(),
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
    if inp.story_id:
        raise ValueError("epic_definition does not accept story_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    epic_path = directory / "EPIC.md"
    epic_relpath = _relpath(inp.repo_root, epic_path)

    if not epic_path.exists():
        if not discovery_synthesis_complete(inp.repo_root, inp.epic_id):
            missing = _missing_discovery_outputs(inp.repo_root, inp.epic_id)
            return _planning_halt(
                inp,
                stage=2,
                message="Required Stage-2 synthesis inputs are missing: " + ", ".join(missing),
                triggered_by=["incomplete_stage_state"],
                check_count=4,
                failed_check_count=len(missing) or 4,
            )
        proc = _run_dispatch(
            inp.repo_root,
            role="primary",
            epic_id=inp.epic_id,
            story_id=None,
            prompt=_epic_definition_prompt(inp.repo_root, inp.epic_id),
            artefacts_loaded=_epic_definition_artefacts(inp.repo_root, inp.epic_id),
        )
        if proc.returncode != 0:
            return _planning_halt(
                inp,
                stage=2,
                message=proc.stderr.strip(),
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
        next_node=NodeType.BREAKDOWN_PLANNING,
        validation_summary=_planning_validation(
            ok=True,
            stage=2,
            check_count=1,
            failed_check_count=0,
        ),
        paths=[epic_relpath],
    )


def breakdown_planning_node(inp: NodeInput) -> NodeOutput:
    if inp.story_id:
        raise ValueError("breakdown_planning does not accept story_id")
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
        proc = _run_dispatch(
            inp.repo_root,
            role="primary",
            epic_id=inp.epic_id,
            story_id=None,
            prompt=_breakdown_planning_prompt(inp.repo_root, inp.epic_id),
            artefacts_loaded=_breakdown_planning_artefacts(inp.repo_root, inp.epic_id),
        )
        if proc.returncode != 0:
            return _planning_halt(
                inp,
                stage=3,
                message=proc.stderr.strip(),
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

    plan_ok, plan_message = _validate_plan(inp.repo_root, inp.epic_id, plan_path)
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

    plan = load_plan(inp.repo_root, inp.epic_id)
    plan_md_path.write_text(_render_plan_markdown(plan), encoding="utf-8")
    append_epic_event(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "breakdown_planned",
            "at": _now(),
            "epic_id": inp.epic_id,
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
    if inp.story_id:
        raise ValueError("plan_critique does not accept story_id")
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

    plan_ok, plan_message = _validate_plan(inp.repo_root, inp.epic_id, plan_path)
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

    if not critique_path.exists():
        critique_path.parent.mkdir(parents=True, exist_ok=True)
        proc = _run_dispatch(
            inp.repo_root,
            role="reviewer",
            epic_id=inp.epic_id,
            story_id=None,
            prompt=_plan_critique_prompt(inp.repo_root, inp.epic_id),
            artefacts_loaded=_plan_critique_artefacts(inp.repo_root, inp.epic_id),
        )
        if proc.returncode != 0:
            return _planning_halt(
                inp,
                stage=3,
                message=proc.stderr.strip(),
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

    critique_ok, critique_message = _validate_plan_critique(inp.repo_root, critique_path)
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
        "Woof always opens this gate before story execution.\n\n"
        "## Findings\n\n" + "\n".join(finding_lines) + "\n\n## Primary position\n\n"
        f"Source: `{plan_relpath}`\n\n"
        "The primary plan is ready for human review before Stage 5 starts.\n\n"
        "## Reviewer position\n\n"
        f"Source: `{critique_relpath}`\n\n"
        f"{reviewer_body}\n"
    )


def plan_gate_open_node(inp: NodeInput) -> NodeOutput:
    if inp.story_id:
        raise ValueError("plan_gate_open does not accept story_id")
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

    plan_ok, plan_message = _validate_plan(inp.repo_root, inp.epic_id, plan_path)
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

    critique_ok, critique_message = _validate_plan_critique(inp.repo_root, critique_path)
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
            story_id=None,
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
    if not inp.story_id:
        raise ValueError("executor_dispatch requires story_id")
    mark_story_status(inp.repo_root, inp.epic_id, inp.story_id, "in_progress")
    proc = _run_dispatch(
        inp.repo_root,
        role="primary",
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        prompt=_story_prompt(inp.epic_id, inp.story_id),
        artefacts_loaded=_story_context_artefacts(inp.repo_root, inp.epic_id),
    )
    if proc.returncode != 0:
        write_gate_for_trigger(
            trigger="subprocess_crash",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            story_id=inp.story_id,
            exit_code=proc.returncode,
            schema_path=schema_dir() / "gate.schema.json",
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            story_id=inp.story_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["subprocess_crash"],
            message=proc.stderr.strip(),
        )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        next_node=NodeType.CRITIQUE_DISPATCH,
    )


def critique_dispatch_node(inp: NodeInput) -> NodeOutput:
    if not inp.story_id:
        raise ValueError("critique_dispatch requires story_id")
    prompt = (tool_root() / "playbooks" / "critique" / "story.md").read_text()
    proc = _run_dispatch(
        inp.repo_root,
        role="reviewer",
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        prompt=prompt,
        artefacts_loaded=_story_context_artefacts(inp.repo_root, inp.epic_id),
    )
    if proc.returncode != 0:
        write_gate_for_trigger(
            trigger="reviewer_unreachable",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            story_id=inp.story_id,
            exit_code=None,
            schema_path=schema_dir() / "gate.schema.json",
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            story_id=inp.story_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["reviewer_unreachable"],
            message=proc.stderr.strip(),
        )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        next_node=NodeType.REVIEW_DISPOSITION,
    )


def _write_position_gate(inp: NodeInput, *, trigger: str, position: str) -> NodeOutput:
    position_path = epic_dir(inp.repo_root, inp.epic_id) / "gate-position.md"
    position_path.write_text(position)
    try:
        write_gate_for_trigger(
            trigger=trigger,
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            story_id=inp.story_id,
            position_path=position_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
    finally:
        position_path.unlink(missing_ok=True)
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
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
    if not inp.story_id:
        raise ValueError("review_disposition requires story_id")
    directory = epic_dir(inp.repo_root, inp.epic_id)
    critique_path = story_critique_path(directory, inp.story_id)

    try:
        critique = read_markdown_front_matter(critique_path)
    except (FileNotFoundError, FrontMatterError) as exc:
        return _write_disposition_incomplete_gate(inp, f"Reviewer critique is unreadable: {exc}")

    severity = critique_severity(critique.front)
    if severity == "blocker":
        body = reviewer_blocker_gate_body(
            epic_id=inp.epic_id,
            story_id=inp.story_id,
            critique=critique,
        )
        return _write_position_gate(
            inp,
            trigger="check_6_critique_blocker",
            position=body,
        )
    if severity not in {"info", "minor"}:
        return _write_disposition_incomplete_gate(
            inp,
            "Reviewer critique severity must be info, minor, or blocker.",
        )

    disposition_path = story_disposition_path(directory, inp.story_id)
    if not disposition_path.exists():
        proc = _run_dispatch(
            inp.repo_root,
            role="primary",
            epic_id=inp.epic_id,
            story_id=inp.story_id,
            prompt=_disposition_prompt(inp.epic_id, inp.story_id),
            artefacts_loaded=_disposition_artefacts(inp.repo_root, inp.epic_id, inp.story_id),
        )
        if proc.returncode != 0:
            write_gate_for_trigger(
                trigger="subprocess_crash",
                epic_dir=directory,
                story_id=inp.story_id,
                exit_code=proc.returncode,
                schema_path=schema_dir() / "gate.schema.json",
            )
            return NodeOutput(
                node_type=inp.node_type,
                status=NodeStatus.GATE_OPENED,
                epic_id=inp.epic_id,
                story_id=inp.story_id,
                gate_path=_gate_path(inp.epic_id),
                triggered_by=["subprocess_crash"],
                message=proc.stderr.strip(),
            )

    validation = validate_story_disposition(directory, inp.epic_id, inp.story_id)
    if not validation.ok:
        return _write_disposition_incomplete_gate(
            inp,
            "Primary disposition is invalid: " + "; ".join(validation.errors),
        )

    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        next_node=NodeType.VERIFICATION,
        paths=[story_disposition_relpath(inp.epic_id, inp.story_id)],
    )


def verification_node(inp: NodeInput) -> NodeOutput:
    if not inp.story_id:
        raise ValueError("verification requires story_id")
    result_path = epic_dir(inp.repo_root, inp.epic_id) / "check-result.json"
    proc = subprocess.run(
        [
            str(_woof_bin()),
            "check",
            "stage-5",
            "--epic",
            str(inp.epic_id),
            "--story",
            inp.story_id,
            "--format",
            "json",
        ],
        cwd=inp.repo_root,
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
                story_id=inp.story_id,
                schema_path=schema_dir() / "gate.schema.json",
            )
        else:
            write_gate_for_trigger(
                trigger="schema_validation_failed",
                epic_dir=epic_dir(inp.repo_root, inp.epic_id),
                story_id=inp.story_id,
                schema_path=schema_dir() / "gate.schema.json",
            )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            story_id=inp.story_id,
            gate_path=_gate_path(inp.epic_id),
            validation_summary=validation_summary,
            triggered_by=validation_summary.triggered_by if validation_summary else [],
            message=proc.stderr.strip(),
        )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        next_node=NodeType.COMMIT,
        validation_summary=validation_summary,
        paths=[str(result_path.relative_to(inp.repo_root))],
    )


def _executor_result(repo_root: Path, epic_id: int) -> dict:
    path = epic_dir(repo_root, epic_id) / "executor_result.json"
    return json.loads(path.read_text())


def _commit_message(epic_id: int, story_title: str, story_id: str) -> str:
    return f"feat(woof): E{epic_id} {story_id} - {story_title}"


def commit_node(inp: NodeInput) -> NodeOutput:
    if not inp.story_id:
        raise ValueError("commit requires story_id")
    plan = load_plan(inp.repo_root, inp.epic_id)
    story = story_by_id(plan, inp.story_id)
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
            story_id=inp.story_id,
            position_path=pos_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
        pos_path.unlink(missing_ok=True)
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            story_id=inp.story_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["audit_redaction"],
            message=position,
        )

    manifest = build_story_manifest(inp.repo_root, inp.epic_id, story)
    if not manifest.audit_paths:
        write_gate_for_trigger(
            trigger="check_7_commit_transaction",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            story_id=inp.story_id,
            schema_path=schema_dir() / "gate.schema.json",
        )
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            story_id=inp.story_id,
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
            story_id=inp.story_id,
            position_path=pos_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
        pos_path.unlink(missing_ok=True)
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            story_id=inp.story_id,
            gate_path=_gate_path(inp.epic_id),
            triggered_by=["check_7_commit_transaction"],
            message=position,
        )

    mark_story_status(inp.repo_root, inp.epic_id, inp.story_id, "done")
    append_epic_event_once(
        inp.repo_root,
        inp.epic_id,
        {
            "event": "story_completed",
            "at": _now(),
            "epic_id": inp.epic_id,
            "story_id": inp.story_id,
        },
        event="story_completed",
        story_id=inp.story_id,
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
            story_id=inp.story_id,
            position_path=pos_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
        pos_path.unlink(missing_ok=True)
        return NodeOutput(
            node_type=inp.node_type,
            status=NodeStatus.GATE_OPENED,
            epic_id=inp.epic_id,
            story_id=inp.story_id,
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
            "story_id": inp.story_id,
            "manifest": manifest.model_dump(),
        },
        event="transaction_manifest_verified",
        story_id=inp.story_id,
    )
    git(inp.repo_root, "add", "--", f".woof/epics/E{inp.epic_id}/epic.jsonl")

    message = _commit_message(inp.epic_id, story.title, inp.story_id)
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
        story_id=inp.story_id,
        paths=manifest.expected_paths,
    )


def gate_open_node(inp: NodeInput) -> NodeOutput:
    if not inp.story_id:
        write_gate_for_trigger(
            trigger=inp.reason or "manual",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            story_id=None,
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
                story_id=inp.story_id,
                schema_path=schema_dir() / "gate.schema.json",
            )
            return NodeOutput(
                node_type=inp.node_type,
                status=NodeStatus.GATE_OPENED,
                epic_id=inp.epic_id,
                story_id=inp.story_id,
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
        story_id=inp.story_id,
        position_path=position_path,
        schema_path=schema_dir() / "gate.schema.json",
    )
    if position_path:
        position_path.unlink(missing_ok=True)
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        gate_path=_gate_path(inp.epic_id),
        triggered_by=[trigger],
    )


def _write_incomplete_stage_gate(inp: NodeInput, position: str) -> NodeOutput:
    position_path = epic_dir(inp.repo_root, inp.epic_id) / "gate-position.md"
    position_path.write_text(
        f"{position}\n\n"
        "The graph cannot safely infer or recreate this state. "
        "Resolve the gate by restoring the required artefact, revising the story state, "
        "or explicitly abandoning the story."
    )
    try:
        write_gate_for_trigger(
            trigger="incomplete_stage_state",
            epic_dir=epic_dir(inp.repo_root, inp.epic_id),
            story_id=inp.story_id,
            position_path=position_path,
            schema_path=schema_dir() / "gate.schema.json",
        )
    finally:
        position_path.unlink(missing_ok=True)
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        gate_path=_gate_path(inp.epic_id),
        triggered_by=["incomplete_stage_state"],
        message=position,
    )


def human_review_node(inp: NodeInput) -> NodeOutput:
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.HALTED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        gate_path=_gate_path(inp.epic_id),
        message=_gate_operator_message(inp.repo_root, inp.epic_id),
    )


def default_registry() -> dict[NodeType, NodeHandler]:
    return {
        NodeType.DISCOVERY_SYNTHESIS: discovery_synthesis_node,
        NodeType.EPIC_DEFINITION: epic_definition_node,
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
