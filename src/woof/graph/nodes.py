"""Node registry and implementations for ADR-001."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from woof.gate.write import write_gate_for_trigger, write_gate_from_check_result
from woof.graph.git import git, staged_paths
from woof.graph.manifest import build_story_manifest, verify_staged_manifest
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, ValidationSummary
from woof.graph.transitions import (
    append_epic_event_once,
    epic_dir,
    load_plan,
    mark_story_status,
    story_by_id,
)
from woof.paths import schema_dir, tool_root

NodeHandler = Callable[[NodeInput], NodeOutput]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _woof_bin() -> Path:
    return tool_root() / "bin" / "woof"


def _gate_path(epic_id: int) -> str:
    return f".woof/epics/E{epic_id}/gate.md"


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


def _run_dispatch(
    repo_root: Path,
    target: str,
    role: str,
    epic_id: int,
    story_id: str | None,
    prompt: str,
) -> subprocess.CompletedProcess[str]:
    prompt_file = _write_prompt_file(prompt)
    try:
        args = [
            str(_woof_bin()),
            "dispatch",
            target,
            "--role",
            role,
            "--epic",
            str(epic_id),
            "--prompt-file",
            str(prompt_file),
        ]
        if story_id:
            args.extend(["--story", story_id])
        return subprocess.run(args, cwd=repo_root, capture_output=True, text=True)
    finally:
        prompt_file.unlink(missing_ok=True)


def executor_dispatch_node(inp: NodeInput) -> NodeOutput:
    if not inp.story_id:
        raise ValueError("executor_dispatch requires story_id")
    mark_story_status(inp.repo_root, inp.epic_id, inp.story_id, "in_progress")
    proc = _run_dispatch(
        inp.repo_root,
        target="claude",
        role="story-executor",
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        prompt=_story_prompt(inp.epic_id, inp.story_id),
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
        target="codex",
        role="critiquer",
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        prompt=prompt,
    )
    if proc.returncode != 0:
        write_gate_for_trigger(
            trigger="codex_unreachable",
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
            triggered_by=["codex_unreachable"],
            message=proc.stderr.strip(),
        )
    return NodeOutput(
        node_type=inp.node_type,
        status=NodeStatus.COMPLETED,
        epic_id=inp.epic_id,
        story_id=inp.story_id,
        next_node=NodeType.VERIFICATION,
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
        message=f"gate open at .woof/epics/E{inp.epic_id}/gate.md",
    )


def default_registry() -> dict[NodeType, NodeHandler]:
    return {
        NodeType.EXECUTOR_DISPATCH: executor_dispatch_node,
        NodeType.CRITIQUE_DISPATCH: critique_dispatch_node,
        NodeType.VERIFICATION: verification_node,
        NodeType.COMMIT: commit_node,
        NodeType.GATE_OPEN: gate_open_node,
        NodeType.HUMAN_REVIEW: human_review_node,
    }
