"""`woof wf` - deterministic graph entry point."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from woof.graph.decisions import all_decisions, validate_decision
from woof.graph.dispositions import (
    NON_BLOCKING_SEVERITIES,
    FrontMatterError,
    critique_severity,
    read_markdown_front_matter,
    story_critique_path,
    story_disposition_path,
)
from woof.graph.lock import WorkflowLockError
from woof.graph.runner import run_graph
from woof.graph.state import (
    TERMINAL_STORY_STATUSES,
    GateDecision,
    NodeStatus,
    Plan,
    StorySpec,
)
from woof.graph.transitions import (
    StageStateError,
    append_epic_event,
    append_epic_event_once,
    archived_epic_contract_path,
    archived_epic_contracts,
    archived_epic_findings_path,
    epic_definition_dir,
    epic_dir,
    load_plan,
    mark_story_status,
    write_plan,
)
from woof.paths import find_project_root
from woof.trackers import (
    CONFLICT_DECISIONS,
    CONFLICT_TRIGGERS,
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
    if gate_type == "story_gate":
        return "story_gate_resolved"
    if gate_type == "review_gate":
        return "review_gate_resolved"
    if gate_type == "readiness_gate":
        return "readiness_gate_resolved"
    return None


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _remove_paths(repo_root: Path, *paths: Path) -> list[str]:
    removed: list[str] = []
    for path in paths:
        if path.exists():
            path.unlink()
            removed.append(_display_path(repo_root, path))
    return removed


def _story_critique_requires_requeue(critique_path: Path) -> bool:
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


def _update_story(repo_root: Path, epic_id: int, story_id: str, **updates: object) -> None:
    plan = load_plan(repo_root, epic_id)
    stories: list[StorySpec] = []
    found = False
    for story in plan.stories:
        if story.id == story_id:
            data = story.model_dump()
            data.update(updates)
            stories.append(StorySpec.model_validate(data))
            found = True
        else:
            stories.append(story)
    if not found:
        raise StageStateError(f"story {story_id} not found in E{epic_id} plan")
    write_plan(repo_root, Plan(epic_id=plan.epic_id, goal=plan.goal, stories=stories))


def _abandon_epic(repo_root: Path, epic_id: int, tracker: Tracker) -> list[str]:
    """Apply the shared abandon_epic effect (E17 P4 / D-AB).

    Closes the tracker issue as not delivered, then appends the graph-owned
    ``epic_abandoned`` marker that ``transitions.next_node`` consults to return an
    abandoned-terminal outcome distinct from ``EPIC_COMPLETE``. The tracker close
    runs first: if it raises ``TrackerError`` the caller maps that to exit 2 and
    the gate stays open, so the epic is never marked abandoned without its issue
    being closed. abandon_epic is valid at the readiness, plan, story, and review
    gates and routes through this one path from every one of them.
    """

    changed: list[str] = []
    result = tracker.close_not_delivered(epic_id)
    changed.append(_display_path(repo_root, result.last_sync_path))
    append_epic_event_once(
        repo_root,
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


def _revise_epic_contract(repo_root: Path, epic_id: int) -> list[str]:
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

    directory = epic_dir(repo_root, epic_id)
    epic_path = directory / "EPIC.md"
    changed: list[str] = []
    if not epic_path.exists():
        return changed
    epic_definition_dir(repo_root, epic_id).mkdir(parents=True, exist_ok=True)
    archives = archived_epic_contracts(repo_root, epic_id)
    index = (archives[-1][0] + 1) if archives else 1
    archived = archived_epic_contract_path(repo_root, epic_id, index)
    epic_path.replace(archived)
    changed.append(_display_path(repo_root, archived))
    body = _gate_body(directory / "gate.md")
    if body:
        findings = archived_epic_findings_path(repo_root, epic_id, index)
        findings.write_text(body + "\n", encoding="utf-8")
        changed.append(_display_path(repo_root, findings))
    return changed


def _apply_gate_resolution_effects(
    repo_root: Path,
    epic_id: int,
    *,
    decision: GateDecision,
    gate_type: str | None,
    story_id: str | None,
    triggered_by: list[str],
    tracker: Tracker,
) -> list[str]:
    directory = epic_dir(repo_root, epic_id)
    changed: list[str] = []

    if any(trigger in CONFLICT_TRIGGERS for trigger in triggered_by):
        validate_decision("tracker_sync_conflict", decision)
        result = tracker.resolve_conflict(epic_id, decision)
        changed.append(_display_path(repo_root, result.last_sync_path))
        if result.epic_path is not None:
            changed.append(_display_path(repo_root, result.epic_path))
        return changed

    if decision in CONFLICT_DECISIONS:
        raise StageStateError(f"{decision} is only valid for tracker_sync_conflict gates")

    if decision == "abandon_epic":
        # abandon_epic is one shared terminal effect across every gate type that
        # offers it; validate it against this gate's allowed set, then route to the
        # canonical path. An invalid gate type (no abandon_epic) raises here.
        validate_decision(gate_type, decision)
        return _abandon_epic(repo_root, epic_id, tracker)

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
            return _revise_epic_contract(repo_root, epic_id)
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
            changed.extend(_revise_epic_contract(repo_root, epic_id))
            changed.extend(
                _remove_paths(
                    repo_root,
                    directory / "plan.json",
                    directory / "PLAN.md",
                    directory / "critique" / "plan.md",
                )
            )
            return changed
        if decision == "revise_plan":
            changed.extend(
                _remove_paths(
                    repo_root,
                    directory / "plan.json",
                    directory / "PLAN.md",
                    directory / "critique" / "plan.md",
                )
            )
            return changed
        return changed

    if gate_type in {"story_gate", "review_gate"}:
        validate_decision(gate_type, decision)
        check_result = directory / "check-result.json"
        executor_result = directory / "executor_result.json"
        if decision == "approve":
            preserve_check_result = (
                "check_7_commit_transaction" in triggered_by and _check_result_ok(check_result)
            )
            if not preserve_check_result:
                changed.extend(_remove_paths(repo_root, check_result))
            if story_id and "check_6_critique_blocker" in triggered_by:
                critique_path = story_critique_path(directory, story_id)
                if _story_critique_requires_requeue(critique_path):
                    changed.extend(
                        _remove_paths(
                            repo_root,
                            critique_path,
                            story_disposition_path(directory, story_id),
                        )
                    )
            if story_id and "empty_diff_review" in triggered_by:
                _update_story(repo_root, epic_id, story_id, status="done", empty_diff=True)
                changed.append(_display_path(repo_root, directory / "plan.json"))
                changed.extend(
                    _remove_paths(
                        repo_root,
                        executor_result,
                    )
                )
                append_epic_event_once(
                    repo_root,
                    epic_id,
                    {
                        "event": "story_completed",
                        "at": _now(),
                        "epic_id": epic_id,
                        "story_id": story_id,
                        "empty_diff": True,
                    },
                    event="story_completed",
                    story_id=story_id,
                )
            return changed
        if decision == "retry_story":
            if not story_id:
                # End-of-epic review_gates carry story_id: null. retry_story has
                # no story to reset there, so it is a structured error rather than
                # a silent "successful retry". Raising before any effect runs keeps
                # the gate open (_resolve_gate maps StageStateError to exit 2 and
                # neither records the gate resolved nor deletes gate.md).
                raise StageStateError(
                    "retry_story requires a targeted story, but the open "
                    f"{gate_type} carries no story_id"
                )
            # retry_story recovers a crashed or aborted executor; a terminal story
            # (done or abandoned) is out of that domain (a crashed executor never
            # reaches a terminal status). Resetting it to pending here would strand
            # the prior story_completed/story_abandoned event in the audit log -
            # append_epic_event_once would dedupe the rerun's re-emitted terminal
            # event - so the timeline would show the story finishing before the
            # retry and never for the rerun. Reject it before any mutation;
            # post-completion recovery semantics belong to E18's completion-event
            # reconciliation, not this verb.
            plan = load_plan(repo_root, epic_id)
            target_story = next((s for s in plan.stories if s.id == story_id), None)
            if target_story is not None and target_story.status in TERMINAL_STORY_STATUSES:
                raise StageStateError(
                    f"retry_story targets {story_id}, but it is already "
                    f"{target_story.status}; retry_story resets crashed or aborted "
                    "executors, not finished stories"
                )
            # A crashed or aborted executor: reset the story to pending and clear
            # its per-story executor/check/critique/disposition artefacts so
            # next_node re-dispatches it cleanly. Sibling stories and their
            # artefacts are untouched.
            mark_story_status(repo_root, epic_id, story_id, "pending")
            changed.append(_display_path(repo_root, directory / "plan.json"))
            changed.extend(
                _remove_paths(
                    repo_root,
                    check_result,
                    executor_result,
                    story_critique_path(directory, story_id),
                    story_disposition_path(directory, story_id),
                )
            )
            append_epic_event(
                repo_root,
                epic_id,
                {
                    "event": "story_retried",
                    "at": _now(),
                    "epic_id": epic_id,
                    "story_id": story_id,
                },
            )
            return changed
        if decision in {"revise_story_scope", "revise_plan"}:
            changed.extend(_remove_paths(repo_root, check_result))
            if decision == "revise_plan":
                changed.extend(
                    _remove_paths(
                        repo_root,
                        directory / "plan.json",
                        directory / "PLAN.md",
                        directory / "critique" / "plan.md",
                    )
                )
            return changed
        if decision == "abandon_story" and story_id:
            # Honest terminal: mark the story abandoned (not done) and record a
            # story_abandoned event, never story_completed. next_node treats an
            # abandoned story as terminal and skips it; the epic still completes
            # on its remaining stories ("skip and continue"). The status is
            # distinct from done so reconstruction never conflates the two.
            _update_story(repo_root, epic_id, story_id, status="abandoned")
            changed.append(_display_path(repo_root, directory / "plan.json"))
            changed.extend(_remove_paths(repo_root, check_result, executor_result))
            append_epic_event_once(
                repo_root,
                epic_id,
                {
                    "event": "story_abandoned",
                    "at": _now(),
                    "epic_id": epic_id,
                    "story_id": story_id,
                    "decision": "abandon_story",
                },
                event="story_abandoned",
                story_id=story_id,
            )
            return changed
    return changed


def _resolve_gate(repo_root: Path, epic_id: int, decision: GateDecision, tracker: Tracker) -> int:
    gate = epic_dir(repo_root, epic_id) / "gate.md"
    if not gate.exists():
        sys.stderr.write(f"woof wf: no open gate at {gate}\n")
        return 2
    front = _gate_front(gate)
    gate_type = front.get("type") if isinstance(front.get("type"), str) else None
    story_id = front.get("story_id") if isinstance(front.get("story_id"), str) else None
    raw_triggered_by = front.get("triggered_by")
    triggered_by = (
        [str(item) for item in raw_triggered_by if isinstance(item, str)]
        if isinstance(raw_triggered_by, list)
        else []
    )
    try:
        changed_paths = _apply_gate_resolution_effects(
            repo_root,
            epic_id,
            decision=decision,
            gate_type=gate_type,
            story_id=story_id,
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
    if specific_event_name and not (specific_event_name == "story_gate_resolved" and not story_id):
        specific_event = {
            "event": specific_event_name,
            "at": _now(),
            "epic_id": epic_id,
            "decision": decision,
            "gate_type": gate_type,
            "triggered_by": triggered_by,
        }
        if story_id:
            specific_event["story_id"] = story_id
        if changed_paths:
            specific_event["paths"] = changed_paths
        append_epic_event(repo_root, epic_id, specific_event)
    event = {
        "event": "gate_resolved",
        "at": _now(),
        "epic_id": epic_id,
        "decision": decision,
        "triggered_by": triggered_by,
    }
    if gate_type:
        event["gate_type"] = gate_type
    if story_id:
        event["story_id"] = story_id
    if changed_paths:
        event["paths"] = changed_paths
    append_epic_event(repo_root, epic_id, event)
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


def _reset_epic(repo_root: Path, epic_id: int, *, assume_yes: bool) -> int:
    directory = epic_dir(repo_root, epic_id)
    if not directory.is_dir():
        sys.stderr.write(
            f"woof wf reset: E{epic_id} not found at {_display_path(repo_root, directory)}\n"
        )
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
            sys.stderr.write(f"  - {_display_path(repo_root, path)}\n")
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
        removed.append(_display_path(repo_root, path))

    append_epic_event(
        repo_root,
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
    try:
        repo_root = find_project_root(Path.cwd())
    except FileNotFoundError as exc:
        sys.stderr.write(f"woof wf: {exc}\n")
        return 2

    try:
        tracker = resolve_tracker(repo_root)
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
        return _reset_epic(repo_root, args.epic, assume_yes=args.yes)

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
                f"initialised spark.md and .woof/.current-epic\n"
                f"Next: woof wf --epic {result.epic_id}\n"
            )
        return 0

    if args.epic is None:
        sys.stderr.write('woof wf: --epic is required unless using `woof wf new "<spark>"`\n')
        return 2

    if not check_runtime():
        return 2

    directory = epic_dir(repo_root, args.epic)
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
        return _resolve_gate(repo_root, args.epic, args.resolve, tracker)

    try:
        outputs = run_graph(repo_root, args.epic, once=args.once)
    except WorkflowLockError as exc:
        sys.stderr.write(f"woof wf: workflow lock active: {exc}\n")
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
            repo_root,
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
            story = f" {output.story_id}" if output.story_id else ""
            msg = f": {output.message}" if output.message else ""
            sys.stdout.write(
                f"woof wf: {output.node_type.value}{story} -> {output.status.value}{msg}\n"
            )
    return 0


def setup_wf_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    wf = sub.add_parser("wf", help="run the deterministic Woof graph")
    wf.add_argument(
        "action",
        nargs="?",
        choices=["new", "reset"],
        help=(
            'optional action; `new "<spark>"` creates a tracker-backed epic, '
            "`reset --epic N` returns an epic to its spark (destructive)"
        ),
    )
    wf.add_argument("spark", nargs="?", help="spark text for `woof wf new`")
    wf.add_argument("--epic", type=int, help="epic id (tracker-assigned epic identifier)")
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
