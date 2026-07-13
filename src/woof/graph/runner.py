"""Graph runner for `woof wf`."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from woof import state
from woof.cli.preflight import _check_profile_a_worktrees_for_plans
from woof.gate.write import write_gate, write_gate_for_trigger
from woof.graph.git import git
from woof.graph.lock import epic_workflow_lock
from woof.graph.nodes import NodeHandler, default_registry
from woof.graph.resilience import detect_resilience_gate
from woof.graph.state import NodeInput, NodeOutput, NodeStatus, NodeType, Plan
from woof.graph.transitions import StageStateError, load_plan, next_node
from woof.paths import schema_dir


class GraphNoProgressError(StageStateError):
    """A graph cycle re-entered the same node without changing any state it reads.

    ``next_node`` is a pure function of the epic's durable state plus the delivery
    checkout's git state. If a cycle runs a node and neither of those changes, the
    next cycle asks the same question of the same state and gets the same answer,
    so the runner would spin forever. That is always a defect - a node whose write
    lands somewhere the transition function does not read it back - and the runner
    names it rather than burning CPU.
    """


def _gate_path(project_key: str, epic_id: int) -> str:
    """The gate's location in the operator home. It is not a repo path any more."""

    return str(state.gate_path(project_key, epic_id))


def _state_fingerprint(project_key: str, repo_root: Path, epic_id: int) -> str:
    """Digest everything ``next_node`` reads: the epic's state and the git worktree."""

    digest = hashlib.sha256()
    directory = state.epic_dir(project_key, epic_id)
    for path in sorted(path for path in directory.rglob("*") if path.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    digest.update(_git_fingerprint(repo_root).encode())
    return digest.hexdigest()


def _git_fingerprint(repo_root: Path) -> str:
    """HEAD plus the porcelain worktree status, or empty when this is not a checkout."""

    try:
        head = git(repo_root, "rev-parse", "HEAD", check=False)
        status = git(repo_root, "status", "--porcelain=v1", "--untracked-files=all", check=False)
    except OSError:
        return ""
    return f"{head.stdout}\0{status.stdout}"


@dataclass(frozen=True)
class DrainStatus:
    ready: list[str] = field(default_factory=list)
    blocked: dict[str, list[str]] = field(default_factory=dict)
    downstream: dict[str, list[str]] = field(default_factory=dict)


def drain_status(plan: Plan) -> DrainStatus:
    """Summarise ready, blocked, and downstream work units in plan order."""

    done = {work_unit.id for work_unit in plan.work_units if work_unit.state == "done"}
    by_id = {work_unit.id: work_unit for work_unit in plan.work_units}
    ready: list[str] = []
    blocked: dict[str, list[str]] = {}
    for work_unit in plan.work_units:
        if work_unit.state != "pending":
            continue
        missing = [dep_id for dep_id in work_unit.deps if dep_id not in done]
        if not missing:
            ready.append(work_unit.id)
            continue
        terminal_missing = [dep_id for dep_id in missing if by_id[dep_id].state in {"abandoned"}]
        if terminal_missing:
            blocked[work_unit.id] = terminal_missing

    downstream: dict[str, list[str]] = {work_unit_id: [] for work_unit_id in blocked}
    blocked_ids = set(blocked)
    downstream_ids: set[str] = set()
    for work_unit in plan.work_units:
        if work_unit.state != "pending" or work_unit.id in blocked_ids:
            continue
        if any(dep_id in blocked_ids or dep_id in downstream_ids for dep_id in work_unit.deps):
            parent = next(
                dep_id
                for dep_id in work_unit.deps
                if dep_id in blocked_ids or dep_id in downstream_ids
            )
            root = (
                parent
                if parent in blocked_ids
                else next(root_id for root_id, items in downstream.items() if parent in items)
            )
            downstream[root].append(work_unit.id)
            downstream_ids.add(work_unit.id)
    return DrainStatus(ready=ready, blocked=blocked, downstream=downstream)


def _stage_state_gate_body(epic_id: int, message: str) -> str:
    return (
        "## Context\n\n"
        f"Woof could not map E{epic_id} filesystem state to a valid next graph node.\n\n"
        "## Findings\n\n"
        f"- {message}\n\n"
        "## Primary position\n\n"
        "No producer output was accepted after this malformed governance state was detected. "
        "Restore the required artefact or revise the plan state before resolving this gate.\n\n"
        "## Reviewer position\n\n"
        "The deterministic graph opened this gate because continuing would require guessing "
        "the intended workflow state.\n"
    )


def _open_resilience_gate(
    project_key: str, epic_id: int, work_unit_id: str | None, trigger: str
) -> NodeOutput:
    node_type = NodeType.GATE_OPEN
    write_gate_for_trigger(
        trigger=trigger,
        project_key=project_key,
        epic_id=epic_id,
        work_unit_id=work_unit_id,
        schema_path=schema_dir() / "gate.schema.json",
    )
    return NodeOutput(
        node_type=node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=epic_id,
        work_unit_id=work_unit_id,
        gate_path=_gate_path(project_key, epic_id),
        triggered_by=[trigger],
    )


def _open_stage_state_gate(project_key: str, epic_id: int, exc: StageStateError) -> NodeOutput:
    gate_type = exc.gate_type
    work_unit_id = exc.work_unit_id
    node_type = NodeType.PLAN_GATE_OPEN if gate_type == "plan_gate" else NodeType.GATE_OPEN
    write_gate(
        project_key=project_key,
        epic_id=epic_id,
        work_unit_id=work_unit_id,
        triggered_by=["incomplete_stage_state"],
        position_text=_stage_state_gate_body(epic_id, str(exc)),
        schema_path=schema_dir() / "gate.schema.json",
        validate=True,
        gate_type=gate_type,
    )
    return NodeOutput(
        node_type=node_type,
        status=NodeStatus.GATE_OPENED,
        epic_id=epic_id,
        work_unit_id=work_unit_id,
        gate_path=_gate_path(project_key, epic_id),
        triggered_by=["incomplete_stage_state"],
        message=str(exc),
    )


def _profile_a_worktree_failure(
    project_key: str, repo_root: Path, epic_id: int, work_unit_id: str
) -> StageStateError | None:
    plan_path = state.plan_path(project_key, epic_id)
    findings = _check_profile_a_worktrees_for_plans(repo_root, [plan_path])
    failed = [finding for finding in findings if not finding.ok]
    if not failed:
        return None
    detail = "; ".join(f"{finding.id}: {finding.detail}" for finding in failed)
    return StageStateError(
        f"Profile A worktree preflight failed for {work_unit_id}: {detail}",
        operator_recoverable=True,
        gate_type="work_unit_gate",
        work_unit_id=work_unit_id,
    )


def _drain_block_message(plan: Plan) -> str:
    status = drain_status(plan)
    blocked = "; ".join(
        f"{work_unit_id} (deps not done: {', '.join(deps)})"
        for work_unit_id, deps in status.blocked.items()
    )
    downstream = "; ".join(
        f"{work_unit_id} -> {', '.join(items)}"
        for work_unit_id, items in status.downstream.items()
        if items
    )
    parts = []
    if blocked:
        parts.append(f"blocked work units: {blocked}")
    if downstream:
        parts.append(f"downstream pending: {downstream}")
    return "; ".join(parts) if parts else "no dependency-satisfied pending work unit"


def run_graph(
    project_key: str,
    repo_root: Path,
    epic_id: int,
    *,
    once: bool = False,
    drain_cycle: bool = True,
    registry: dict | None = None,
) -> list[NodeOutput]:
    """Run the graph until it halts, gates, completes, or publishes one work unit."""

    with epic_workflow_lock(project_key, epic_id):
        handlers: dict = registry or default_registry()
        outputs: list[NodeOutput] = []
        previous_cycle: tuple[NodeType, str | None] | None = None
        previous_fingerprint: str | None = None
        while True:
            try:
                node_type, work_unit_id = next_node(project_key, repo_root, epic_id)
            except StageStateError as exc:
                if exc.operator_recoverable:
                    outputs.append(_open_stage_state_gate(project_key, epic_id, exc))
                    return outputs
                raise
            if node_type is NodeStatus.EPIC_ABANDONED:
                # next_node returns the abandoned-terminal sentinel (E17 P4 / D-AB);
                # surface it as a distinct NodeOutput status, never EPIC_COMPLETE.
                outputs.append(
                    NodeOutput(
                        node_type=NodeType.HUMAN_REVIEW,
                        status=NodeStatus.EPIC_ABANDONED,
                        epic_id=epic_id,
                        message=f"E{epic_id} abandoned",
                    )
                )
                return outputs
            if not isinstance(node_type, NodeType):
                # The only remaining non-NodeType sentinel is None: epic complete.
                outputs.append(
                    NodeOutput(
                        node_type=NodeType.HUMAN_REVIEW,
                        status=NodeStatus.EPIC_COMPLETE,
                        epic_id=epic_id,
                        message=f"E{epic_id} complete",
                    )
                )
                return outputs
            if node_type is NodeType.EXECUTOR_DISPATCH and work_unit_id is not None:
                failure = _profile_a_worktree_failure(project_key, repo_root, epic_id, work_unit_id)
                if failure is not None:
                    outputs.append(_open_stage_state_gate(project_key, epic_id, failure))
                    return outputs

            fingerprint = _state_fingerprint(project_key, repo_root, epic_id)
            if (node_type, work_unit_id) == previous_cycle and fingerprint == previous_fingerprint:
                work_unit = f" work unit {work_unit_id}" if work_unit_id else ""
                raise GraphNoProgressError(
                    f"E{epic_id} made no progress: node {node_type.value}{work_unit} ran and "
                    "left the epic state and the delivery checkout unchanged, so the graph "
                    "would re-enter it forever. The node's write is not landing where "
                    "next_node reads it back.",
                    work_unit_id=work_unit_id,
                )
            previous_cycle = (node_type, work_unit_id)
            previous_fingerprint = fingerprint

            handler: NodeHandler = handlers[node_type]
            try:
                out = handler(
                    NodeInput(
                        node_type=node_type,
                        epic_id=epic_id,
                        work_unit_id=work_unit_id,
                        project_key=project_key,
                        repo_root=repo_root,
                    )
                )
            except StageStateError as exc:
                if exc.operator_recoverable:
                    outputs.append(_open_stage_state_gate(project_key, epic_id, exc))
                    return outputs
                raise
            outputs.append(out)
            if (
                node_type in {NodeType.EXECUTOR_DISPATCH, NodeType.CRITIQUE_DISPATCH}
                and out.status == NodeStatus.COMPLETED
            ):
                trigger = detect_resilience_gate(project_key, repo_root, epic_id, work_unit_id)
                if trigger is not None:
                    outputs.append(
                        _open_resilience_gate(project_key, epic_id, work_unit_id, trigger)
                    )
                    return outputs
            if once or out.status in {
                NodeStatus.GATE_OPENED,
                NodeStatus.HALTED,
                NodeStatus.EPIC_COMPLETE,
            }:
                return outputs
            if drain_cycle and node_type is NodeType.COMMIT:
                try:
                    follow_type, follow_work_unit_id = next_node(project_key, repo_root, epic_id)
                except StageStateError as exc:
                    if exc.operator_recoverable:
                        try:
                            plan = load_plan(project_key, epic_id)
                        except StageStateError:
                            outputs.append(_open_stage_state_gate(project_key, epic_id, exc))
                        else:
                            outputs.append(
                                _open_stage_state_gate(
                                    project_key,
                                    epic_id,
                                    StageStateError(
                                        f"{exc}; {_drain_block_message(plan)}",
                                        operator_recoverable=True,
                                    ),
                                )
                            )
                        return outputs
                    raise
                if (
                    follow_type is NodeType.EXECUTOR_DISPATCH
                    and follow_work_unit_id != work_unit_id
                ):
                    return outputs
