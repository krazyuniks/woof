"""`woof wf` - deterministic graph entry point."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from woof import state
from woof.graph.decisions import all_decisions, validate_decision
from woof.graph.dispositions import (
    NON_BLOCKING_SEVERITIES,
    FrontMatterError,
    critique_severity,
    read_markdown_front_matter,
)
from woof.graph.intake import ingest_predecomposed_work_units
from woof.graph.lock import WorkflowLockError
from woof.graph.runner import GraphNoProgressError, run_graph
from woof.graph.state import (
    TERMINAL_WORK_UNIT_STATES,
    GateDecision,
    NodeStatus,
    Plan,
    WorkUnitSpec,
)
from woof.graph.transitions import (
    StageStateError,
    append_epic_event,
    append_epic_event_once,
    archived_epic_contract_path,
    archived_epic_contracts,
    archived_epic_findings_path,
    epic_definition_dir,
    load_plan,
    mark_work_unit_state,
    plan_critique_path,
    plan_markdown_path,
    write_plan,
)
from woof.paths import ProjectKeyError, repo_root_from_git, resolve_project_key
from woof.trackers import (
    CONFLICT_DECISIONS,
    CONFLICT_TRIGGERS,
    NON_APPROVING_TRIGGERS,
    Tracker,
    TrackerError,
    resolve_tracker,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gate_front(gate_path: Path) -> dict:
    text = gate_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict):
        return {}
    return front


def _gate_resolved_event_name(gate_type: str | None) -> str | None:
    if gate_type == "plan_gate":
        return "plan_gate_resolved"
    if gate_type == "work_unit_gate":
        return "work_unit_gate_resolved"
    if gate_type == "review_gate":
        return "review_gate_resolved"
    if gate_type == "readiness_gate":
        return "readiness_gate_resolved"
    return None


def _remove_paths(*paths: Path) -> list[str]:
    removed: list[str] = []
    for path in paths:
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


def _plan_artefacts(project_key: str, epic_id: int) -> tuple[Path, Path, Path]:
    """The planning artefacts a plan revision must clear."""

    return (
        state.plan_path(project_key, epic_id),
        plan_markdown_path(project_key, epic_id),
        plan_critique_path(project_key, epic_id),
    )


def _work_unit_critique_requires_requeue(critique_path: Path) -> bool:
    try:
        front = read_markdown_front_matter(critique_path).front
    except (FileNotFoundError, FrontMatterError):
        return True
    return critique_severity(front) not in NON_BLOCKING_SEVERITIES


def _check_result_ok(check_result_path: Path) -> bool:
    try:
        payload = json.loads(check_result_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("ok") is True


def _update_work_unit(project_key: str, epic_id: int, work_unit_id: str, **updates: object) -> None:
    plan = load_plan(project_key, epic_id)
    work_units: list[WorkUnitSpec] = []
    found = False
    for work_unit in plan.work_units:
        if work_unit.id == work_unit_id:
            data = work_unit.model_dump()
            data.update(updates)
            work_units.append(WorkUnitSpec.model_validate(data))
            found = True
        else:
            work_units.append(work_unit)
    if not found:
        raise StageStateError(f"work unit {work_unit_id} not found in E{epic_id} plan")
    write_plan(
        project_key,
        Plan(epic_id=plan.epic_id, context=plan.context, goal=plan.goal, work_units=work_units),
    )


def _abandon_epic(project_key: str, epic_id: int, tracker: Tracker) -> list[str]:
    """Apply the shared abandon_epic effect (E17 P4 / D-AB).

    Closes the tracker issue as not delivered, then appends the graph-owned
    ``epic_abandoned`` marker that ``transitions.next_node`` consults to return an
    abandoned-terminal outcome distinct from ``EPIC_COMPLETE``. The tracker close
    runs first: if it raises ``TrackerError`` the caller maps that to exit 2 and
    the gate stays open, so the epic is never marked abandoned without its issue
    being closed. abandon_epic is valid at the readiness, plan, work-unit, and review
    gates and routes through this one path from every one of them.
    """

    changed: list[str] = []
    result = tracker.close_not_delivered(epic_id)
    changed.append(str(result.last_sync_path))
    append_epic_event_once(
        project_key,
        epic_id,
        {
            "event": "epic_abandoned",
            "at": _now(),
            "epic_id": epic_id,
        },
        event="epic_abandoned",
    )
    return changed


def _gate_body(gate_path: Path) -> str:
    """Return the open gate's body (front matter stripped), or ``""`` if absent."""
    try:
        text = gate_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            text = text[end + len("\n---\n") :]
    return text.strip()


def _revise_epic_contract(project_key: str, epic_id: int) -> list[str]:
    """Apply the shared revise_epic_contract archive effect (E17 P5 / D-RC).

    Archive the prior ``EPIC.md`` to ``definition/EPIC.<n>.archived.md`` and snapshot
    the resolving gate's findings to ``definition/EPIC.<n>.findings.md`` so the
    re-dispatched definition node revises an evidence-backed contract instead of
    silently overwriting a lost one. Moving ``EPIC.md`` out of place forces the
    definition node to re-dispatch rather than re-validate an operator-edited file,
    keeping hand-editing forbidden; the ``gate_resolved`` event ``_resolve_gate``
    appends drives ``transitions.definition_revision_requested``, which re-enters
    definition. revise_epic_contract is valid at the readiness and plan gates and
    routes through this one path from both.
    """

    epic_path = state.epic_contract_path(project_key, epic_id)
    changed: list[str] = []
    if not epic_path.exists():
        return changed
    epic_definition_dir(project_key, epic_id).mkdir(parents=True, exist_ok=True)
    archives = archived_epic_contracts(project_key, epic_id)
    index = (archives[-1][0] + 1) if archives else 1
    archived = archived_epic_contract_path(project_key, epic_id, index)
    epic_path.replace(archived)
    changed.append(str(archived))
    body = _gate_body(state.gate_path(project_key, epic_id))
    if body:
        findings = archived_epic_findings_path(project_key, epic_id, index)
        state.atomic_write_text(findings, body + "\n")
        changed.append(str(findings))
    return changed


def _apply_gate_resolution_effects(
    project_key: str,
    epic_id: int,
    *,
    decision: GateDecision,
    gate_type: str | None,
    work_unit_id: str | None,
    triggered_by: list[str],
    tracker: Tracker,
) -> list[str]:
    changed: list[str] = []

    # A non-approving trigger (e.g. incomplete_stage_state) means the operator
    # fixed the halt condition; no approval effects run for any gate type.
    if decision == "approve" and any(trigger in NON_APPROVING_TRIGGERS for trigger in triggered_by):
        return changed

    if any(trigger in CONFLICT_TRIGGERS for trigger in triggered_by):
        validate_decision("tracker_sync_conflict", decision)
        result = tracker.resolve_conflict(epic_id, decision)
        changed.append(str(result.last_sync_path))
        if result.epic_path is not None:
            changed.append(str(result.epic_path))
        return changed

    if decision in CONFLICT_DECISIONS:
        raise StageStateError(f"{decision} is only valid for tracker_sync_conflict gates")

    if decision == "abandon_epic":
        # abandon_epic is one shared terminal effect across every gate type that
        # offers it; validate it against this gate's allowed set, then route to the
        # canonical path. An invalid gate type (no abandon_epic) raises here.
        validate_decision(gate_type, decision)
        return _abandon_epic(project_key, epic_id, tracker)

    if gate_type == "readiness_gate":
        validate_decision("readiness_gate", decision)
        # abandon_epic already returned via the shared _abandon_epic path above,
        # so this branch handles only approve_with_reason and revise_epic_contract.
        if decision == "revise_epic_contract":
            # Archive the prior EPIC.md + readiness findings and re-enter definition
            # (E17 P5 / D-RC). The gate_resolved event _resolve_gate appends drives
            # transitions.definition_revision_requested, which routes next_node back
            # to the definition node; the node re-dispatches with the prior epic +
            # findings as declared inputs.
            return _revise_epic_contract(project_key, epic_id)
        # approve_with_reason: no file effects at Stage 2.5. The readiness_gate_resolved
        # event _resolve_gate appends (decision=approve_with_reason, after the latest
        # definition_closed) satisfies transitions.readiness_satisfied, so the unchanged
        # contract advances to planning without re-running readiness.
        return changed

    if gate_type == "plan_gate":
        validate_decision("plan_gate", decision)
        if decision == "approve":
            tracker.push_plan_summary(epic_id)
            return changed
        if decision == "revise_epic_contract":
            # Archive the prior EPIC.md + plan-gate findings and re-enter definition
            # (E17 P5 / D-RC), then clear the now-stale plan artefacts the prior
            # contract produced.
            changed.extend(_revise_epic_contract(project_key, epic_id))
            changed.extend(_remove_paths(*_plan_artefacts(project_key, epic_id)))
            return changed
        if decision == "revise_plan":
            changed.extend(_remove_paths(*_plan_artefacts(project_key, epic_id)))
            return changed
        return changed

    if gate_type in {"work_unit_gate", "review_gate"}:
        validate_decision(gate_type, decision)
        check_result = state.check_result_path(project_key, epic_id)
        executor_result = state.executor_result_path(project_key, epic_id)
        if decision == "approve":
            preserve_check_result = (
                "check_7_commit_transaction" in triggered_by and _check_result_ok(check_result)
            )
            if not preserve_check_result:
                changed.extend(_remove_paths(check_result))
            if work_unit_id and "check_6_critique_blocker" in triggered_by:
                critique_path = state.work_unit_critique_path(project_key, epic_id, work_unit_id)
                if _work_unit_critique_requires_requeue(critique_path):
                    changed.extend(
                        _remove_paths(
                            critique_path,
                            state.work_unit_disposition_path(project_key, epic_id, work_unit_id),
                        )
                    )
            if work_unit_id and "empty_diff_review" in triggered_by:
                _update_work_unit(project_key, epic_id, work_unit_id, state="done", empty_diff=True)
                changed.append(str(state.plan_path(project_key, epic_id)))
                changed.extend(_remove_paths(executor_result))
                append_epic_event_once(
                    project_key,
                    epic_id,
                    {
                        "event": "work_unit_completed",
                        "at": _now(),
                        "epic_id": epic_id,
                        "work_unit_id": work_unit_id,
                        "empty_diff": True,
                    },
                    event="work_unit_completed",
                    work_unit_id=work_unit_id,
                )
            return changed
        if decision == "retry_work_unit":
            if not work_unit_id:
                # End-of-epic review_gates carry work_unit_id: null. retry_work_unit has
                # no work unit to reset there, so it is a structured error rather than
                # a silent "successful retry". Raising before any effect runs keeps
                # the gate open (_resolve_gate maps StageStateError to exit 2 and
                # neither records the gate resolved nor deletes gate.md).
                raise StageStateError(
                    "retry_work_unit requires a targeted work unit, but the open "
                    f"{gate_type} carries no work_unit_id"
                )
            # retry_work_unit recovers a crashed or aborted executor; a terminal work unit
            # (done or abandoned) is out of that domain (a crashed executor never
            # reaches a terminal state). Resetting it to pending here would strand
            # the prior work_unit_completed/work_unit_abandoned event in the audit log -
            # append_epic_event_once would dedupe the rerun's re-emitted terminal
            # event - so the timeline would show the work unit finishing before the
            # retry and never for the rerun. Reject it before any mutation;
            # post-completion recovery semantics belong to E18's completion-event
            # reconciliation, not this verb.
            plan = load_plan(project_key, epic_id)
            target_work_unit = next((s for s in plan.work_units if s.id == work_unit_id), None)
            if target_work_unit is not None and target_work_unit.state in TERMINAL_WORK_UNIT_STATES:
                raise StageStateError(
                    f"retry_work_unit targets {work_unit_id}, but it is already "
                    f"{target_work_unit.state}; retry_work_unit resets crashed or aborted "
                    "executors, not finished work units"
                )
            # A crashed or aborted executor: reset the work unit to pending and clear
            # its per-work-unit executor/check/critique/disposition artefacts so
            # next_node re-dispatches it cleanly. Sibling work units and their
            # artefacts are untouched.
            mark_work_unit_state(project_key, epic_id, work_unit_id, "pending")
            changed.append(str(state.plan_path(project_key, epic_id)))
            changed.extend(
                _remove_paths(
                    check_result,
                    executor_result,
                    state.work_unit_critique_path(project_key, epic_id, work_unit_id),
                    state.work_unit_disposition_path(project_key, epic_id, work_unit_id),
                )
            )
            append_epic_event(
                project_key,
                epic_id,
                {
                    "event": "work_unit_retried",
                    "at": _now(),
                    "epic_id": epic_id,
                    "work_unit_id": work_unit_id,
                },
            )
            return changed
        if decision in {"revise_work_unit_scope", "revise_plan"}:
            changed.extend(_remove_paths(check_result))
            if decision == "revise_plan":
                changed.extend(_remove_paths(*_plan_artefacts(project_key, epic_id)))
            return changed
        if decision == "abandon_work_unit" and work_unit_id:
            # Honest terminal: mark the work unit abandoned (not done) and record a
            # work_unit_abandoned event, never work_unit_completed. next_node treats an
            # abandoned work unit as terminal and skips it; the epic still completes
            # on its remaining work units ("skip and continue"). The state is
            # distinct from done so reconstruction never conflates the two.
            _update_work_unit(project_key, epic_id, work_unit_id, state="abandoned")
            changed.append(str(state.plan_path(project_key, epic_id)))
            changed.extend(_remove_paths(check_result, executor_result))
            append_epic_event_once(
                project_key,
                epic_id,
                {
                    "event": "work_unit_abandoned",
                    "at": _now(),
                    "epic_id": epic_id,
                    "work_unit_id": work_unit_id,
                    "decision": "abandon_work_unit",
                },
                event="work_unit_abandoned",
                work_unit_id=work_unit_id,
            )
            return changed
    return changed


def _resolve_gate(project_key: str, epic_id: int, decision: GateDecision, tracker: Tracker) -> int:
    gate = state.gate_path(project_key, epic_id)
    if not gate.exists():
        sys.stderr.write(f"woof wf: no open gate at {gate}\n")
        return 2
    front = _gate_front(gate)
    gate_type = front.get("type") if isinstance(front.get("type"), str) else None
    work_unit_id = front.get("work_unit_id") if isinstance(front.get("work_unit_id"), str) else None
    raw_triggered_by = front.get("triggered_by")
    triggered_by = (
        [str(item) for item in raw_triggered_by if isinstance(item, str)]
        if isinstance(raw_triggered_by, list)
        else []
    )
    try:
        changed_paths = _apply_gate_resolution_effects(
            project_key,
            epic_id,
            decision=decision,
            gate_type=gate_type,
            work_unit_id=work_unit_id,
            triggered_by=triggered_by,
            tracker=tracker,
        )
    except TrackerError as exc:
        sys.stderr.write(f"woof wf: tracker error: {exc}\n")
        return 2
    except StageStateError as exc:
        sys.stderr.write(f"woof wf: gate resolution failed: {exc}\n")
        return 2
    specific_event_name = _gate_resolved_event_name(gate_type)
    # Stage-state halt resolutions must not emit an approval-shaped specific event;
    # the generic gate_resolved below is the sole audit record for those resolutions.
    emit_specific = (
        specific_event_name
        and not (specific_event_name == "work_unit_gate_resolved" and not work_unit_id)
        and not any(trigger in NON_APPROVING_TRIGGERS for trigger in triggered_by)
    )
    if emit_specific:
        specific_event = {
            "event": specific_event_name,
            "at": _now(),
            "epic_id": epic_id,
            "decision": decision,
            "gate_type": gate_type,
            "triggered_by": triggered_by,
        }
        if work_unit_id:
            specific_event["work_unit_id"] = work_unit_id
        if changed_paths:
            specific_event["paths"] = changed_paths
        append_epic_event(project_key, epic_id, specific_event)
    event = {
        "event": "gate_resolved",
        "at": _now(),
        "epic_id": epic_id,
        "decision": decision,
        "triggered_by": triggered_by,
    }
    if gate_type:
        event["gate_type"] = gate_type
    if work_unit_id:
        event["work_unit_id"] = work_unit_id
    if changed_paths:
        event["paths"] = changed_paths
    append_epic_event(project_key, epic_id, event)
    gate.unlink()
    sys.stdout.write(f"woof wf: gate resolved decision={decision}\n")
    return 0


# `woof wf reset` returns an epic to its spark. It is deny-by-default: it removes
# every entry in the epic directory except the inputs and lineage it must keep -
# spark.md, the tracker linkage (.last-sync), and the append-only epic.jsonl log.
# Removing everything else (discovery/, EPIC.md, the plan, critiques, dispositions/,
# dispatch.jsonl, audit/, gate/result/position files, the workflow lock, and any
# future derived artefact) means no stale derived state can leak into a rebuilt
# epic or the observe views. The reset appends an `epic_reset` marker so the kept
# event log's state-derivation readers ignore the superseded events
# (see graph.transitions.iter_epic_events).
_RESET_KEEP = frozenset({"spark.md", ".last-sync", "epic.jsonl"})


def _reset_targets(directory: Path) -> list[Path]:
    """Return the derived artefacts a reset would remove, present on disk."""
    return sorted(child for child in directory.iterdir() if child.name not in _RESET_KEEP)


def _reset_epic(project_key: str, epic_id: int, *, assume_yes: bool) -> int:
    directory = state.epic_dir(project_key, epic_id)
    if not directory.is_dir():
        sys.stderr.write(f"woof wf reset: E{epic_id} not found at {directory}\n")
        return 2

    present = _reset_targets(directory)
    if not present:
        sys.stdout.write(f"woof wf reset: E{epic_id} is already at spark; nothing to remove\n")
        return 0

    if not assume_yes:
        sys.stderr.write(
            f"woof wf reset will permanently delete {len(present)} derived artefact(s) from "
            f"E{epic_id}, keeping spark.md, .last-sync, and epic.jsonl:\n"
        )
        for path in present:
            sys.stderr.write(f"  - {path}\n")
        sys.stderr.write("Proceed? [y/N] ")
        sys.stderr.flush()
        reply = sys.stdin.readline().strip().lower()
        if reply not in {"y", "yes"}:
            sys.stdout.write("woof wf reset: aborted; nothing was removed\n")
            return 1

    removed: list[str] = []
    for path in present:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path))

    append_epic_event(
        project_key,
        epic_id,
        {
            "event": "epic_reset",
            "at": _now(),
            "epic_id": epic_id,
            "removed": removed,
        },
    )
    sys.stdout.write(
        f"woof wf reset: E{epic_id} reset to spark; removed {len(removed)} artefact(s)\n"
    )
    for item in removed:
        sys.stdout.write(f"  removed {item}\n")
    sys.stdout.write(f"Next: woof wf --epic {epic_id}\n")
    return 0


def cmd_wf(args: argparse.Namespace) -> int:
    # The project key selects the engine state; the repo root is the delivery
    # checkout the graph runs its git work in. They are independent (ADR-017).
    try:
        project_key = resolve_project_key(getattr(args, "project", None))
    except ProjectKeyError as exc:
        sys.stderr.write(f"woof wf: {exc}\n")
        return 2

    try:
        repo_root = repo_root_from_git()
    except FileNotFoundError as exc:
        sys.stderr.write(f"woof wf: {exc}\n")
        return 2

    try:
        tracker = resolve_tracker(project_key)
    except TrackerError as exc:
        sys.stderr.write(f"woof wf: tracker not configured: {exc}\n")
        return 2

    def check_runtime() -> bool:
        try:
            tracker.assert_runtime_reachable()
        except TrackerError as exc:
            sys.stderr.write(f"woof wf: tracker not reachable: {exc}\n")
            return False
        return True

    if args.action == "reset":
        if args.epic is None:
            sys.stderr.write("woof wf reset: --epic is required, e.g. `woof wf reset --epic 12`\n")
            return 2
        return _reset_epic(project_key, args.epic, assume_yes=args.yes)

    if args.action == "new":
        if args.epic is not None:
            sys.stderr.write("woof wf new: --epic is assigned by the tracker; omit --epic\n")
            return 2
        if not args.spark:
            sys.stderr.write('woof wf new: spark is required, e.g. `woof wf new "..."`\n')
            return 2
        if not check_runtime():
            return 2
        try:
            result = tracker.create_epic(args.spark)
        except TrackerError as exc:
            sys.stderr.write(f"woof wf new: tracker error: {exc}\n")
            return 2
        if args.format == "json":
            paths = [str(result.spark_path)]
            if result.last_sync_path.exists():
                paths.append(str(result.last_sync_path))
            paths.append(str(result.current_epic_path))
            payload = {
                "epic_id": result.epic_id,
                "status": "created",
                "epic_ref": result.epic_ref,
                "epic_dir": str(result.epic_dir),
                "current_epic_path": str(result.current_epic_path),
                "next_command": f"woof wf --epic {result.epic_id}",
                "paths": paths,
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            sys.stdout.write(
                f"woof wf new: created E{result.epic_id} at {result.epic_ref}; "
                f"initialised {result.spark_path} and "
                f"{state.current_epic_path(project_key)}\n"
                f"Next: woof wf --epic {result.epic_id}\n"
            )
        return 0

    if args.action == "intake":
        if args.epic is not None:
            sys.stderr.write("woof wf intake: --epic is not used for pre-decomposed intake\n")
            return 2
        if args.source is None:
            sys.stderr.write("woof wf intake: --source is required\n")
            return 2
        try:
            result = ingest_predecomposed_work_units(
                project_key,
                args.source,
                project_ref=args.project_ref,
                set_id=args.set_id,
                source_ref=args.source_ref,
            )
        except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            sys.stderr.write(f"woof wf intake: {exc}\n")
            return 2
        paths = [
            str(result.plan_path),
            str(result.plan_markdown_path),
            str(result.metadata_path),
        ]
        if args.format == "json":
            payload = {
                "status": "intaked",
                "context": result.context,
                "work_unit_count": result.work_unit_count,
                "directory": str(result.directory),
                "paths": paths,
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            sys.stdout.write(
                "woof wf intake: intaked "
                f"{result.work_unit_count} work unit(s) into {result.directory}\n"
            )
            for path in paths:
                sys.stdout.write(f"  wrote {path}\n")
        return 0

    if args.epic is None:
        sys.stderr.write(
            'woof wf: --epic is required unless using `woof wf new "<spark>"` '
            "or `woof wf intake --source PATH`\n"
        )
        return 2

    if not check_runtime():
        return 2

    directory = state.epic_dir(project_key, args.epic)
    if not directory.exists():
        try:
            result = tracker.fetch_epic(args.epic)
        except TrackerError as exc:
            sys.stderr.write(f"woof wf: tracker error: {exc}\n")
            return 2
        if args.format == "json":
            paths = [str(result.spark_path), str(result.last_sync_path)]
            if result.epic_path:
                paths.insert(1, str(result.epic_path))
            payload = {
                "epic_id": args.epic,
                "status": "initialised",
                "epic_dir": str(result.epic_dir),
                "next_command": f"woof wf --epic {args.epic}",
                "paths": paths,
            }
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            epic_state = " and EPIC.md" if result.epic_path else ""
            sys.stdout.write(
                f"woof wf: initialised E{args.epic} from the tracker with spark.md{epic_state}\n"
                f"Next: woof wf --epic {args.epic}\n"
            )
        return 0

    try:
        tracker.assert_epic_authority(args.epic)
    except TrackerError as exc:
        sys.stderr.write(f"woof wf: tracker error: {exc}\n")
        return 2

    if args.resolve:
        return _resolve_gate(project_key, args.epic, args.resolve, tracker)

    try:
        outputs = run_graph(project_key, repo_root, args.epic, once=args.once)
    except WorkflowLockError as exc:
        sys.stderr.write(f"woof wf: workflow lock active: {exc}\n")
        return 2
    except GraphNoProgressError as exc:
        sys.stderr.write(f"woof wf: no_progress: {exc}\n")
        return 2
    except StageStateError as exc:
        sys.stderr.write(f"woof wf: incomplete_stage_state: {exc}\n")
        return 2
    if any(
        output.status == NodeStatus.EPIC_COMPLETE for output in outputs
    ) and tracker.has_sync_state(args.epic):
        try:
            tracker.complete_epic(args.epic)
        except TrackerError as exc:
            sys.stderr.write(f"woof wf: tracker error: {exc}\n")
            return 2
        append_epic_event_once(
            project_key,
            args.epic,
            {
                "event": "epic_completed",
                "at": _now(),
                "epic_id": args.epic,
            },
            event="epic_completed",
        )
    for output in outputs:
        if args.format == "json":
            sys.stdout.write(output.model_dump_json() + "\n")
        else:
            work_unit = f" {output.work_unit_id}" if output.work_unit_id else ""
            msg = f": {output.message}" if output.message else ""
            sys.stdout.write(
                f"woof wf: {output.node_type.value}{work_unit} -> {output.status.value}{msg}\n"
            )
    return 0


def setup_wf_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    project: argparse.ArgumentParser,
) -> None:
    wf = sub.add_parser("wf", help="run the deterministic Woof graph", parents=[project])
    wf.add_argument(
        "action",
        nargs="?",
        choices=["new", "reset", "intake"],
        help=(
            'optional action; `new "<spark>"` creates a tracker-backed epic, '
            "`intake --source PATH` ingests pre-decomposed work_units, "
            "`reset --epic N` returns an epic to its spark (destructive)"
        ),
    )
    wf.add_argument("spark", nargs="?", help="spark text for `woof wf new`")
    wf.add_argument("--epic", type=int, help="epic id (tracker-assigned epic identifier)")
    wf.add_argument("--source", type=Path, help="pre-decomposed work_units source for intake")
    wf.add_argument("--project-ref", help="project_ref for pre-decomposed intake")
    wf.add_argument("--set-id", help="stable set_id for pre-decomposed intake")
    wf.add_argument("--source-ref", help="natural source reference for pre-decomposed intake")
    wf.add_argument("--once", action="store_true", help="run a single graph node and stop")
    wf.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="skip the confirmation prompt for destructive actions (`woof wf reset`)",
    )
    wf.add_argument(
        "--resolve",
        choices=list(all_decisions()),
        help="resolve the currently open gate with a structured decision",
    )
    wf.add_argument("--format", choices=["text", "json"], default="text")
    wf.set_defaults(func=cmd_wf)
