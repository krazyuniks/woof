"""Deterministic transition table for the Woof graph."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from woof.graph.dispositions import (
    FrontMatterError,
    critique_severity,
    read_markdown_front_matter,
    story_critique_path,
    story_disposition_path,
    validate_story_disposition,
)
from woof.graph.git import changed_paths, staged_paths
from woof.graph.manifest import build_story_manifest
from woof.graph.state import TERMINAL_STORY_STATUSES, NodeStatus, NodeType, Plan, StorySpec
from woof.trackers.base import CONFLICT_TRIGGERS


class StageStateError(RuntimeError):
    """Filesystem state cannot be mapped to a valid graph node."""

    def __init__(
        self,
        message: str,
        *,
        operator_recoverable: bool = False,
        gate_type: str = "plan_gate",
        story_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operator_recoverable = operator_recoverable
        self.gate_type = gate_type
        self.story_id = story_id


def epic_dir(repo_root: Path, epic_id: int) -> Path:
    return repo_root / ".woof" / "epics" / f"E{epic_id}"


def discovery_synthesis_dir(repo_root: Path, epic_id: int) -> Path:
    return epic_dir(repo_root, epic_id) / "discovery" / "synthesis"


def discovery_synthesis_paths(repo_root: Path, epic_id: int) -> dict[str, Path]:
    directory = discovery_synthesis_dir(repo_root, epic_id)
    return {
        "concept_path": directory / "CONCEPT.md",
        "principles_path": directory / "PRINCIPLES.md",
        "architecture_path": directory / "ARCHITECTURE.md",
        "open_questions_path": directory / "OPEN_QUESTIONS.md",
    }


def discovery_synthesis_complete(repo_root: Path, epic_id: int) -> bool:
    return all(
        path.is_file() and path.read_text(encoding="utf-8").strip()
        for path in discovery_synthesis_paths(repo_root, epic_id).values()
    )


DISCOVERY_BUCKETS = ("research", "thinking", "ideate")

_DISCOVERY_BUCKET_NODES = (
    ("research", NodeType.DISCOVERY_RESEARCH),
    ("thinking", NodeType.DISCOVERY_THINKING),
    ("ideate", NodeType.DISCOVERY_IDEATE),
)

# The interactive Stage-0 bucket written by the `woof-brainstorm` skill. When
# present it stands in for the headless research/thinking/ideate chain (which is
# the autonomy fallback): synthesis ingests it like any other discovery source.
INTERACTIVE_DISCOVERY_BUCKET = "brainstorm"


def discovery_bucket_dir(repo_root: Path, epic_id: int, bucket: str) -> Path:
    return epic_dir(repo_root, epic_id) / "discovery" / bucket


def discovery_bucket_complete(repo_root: Path, epic_id: int, bucket: str) -> bool:
    """Return whether a Stage-1 producer bucket has at least one artefact."""

    directory = discovery_bucket_dir(repo_root, epic_id, bucket)
    if not directory.is_dir():
        return False
    return any(
        path.is_file() and path.read_text(encoding="utf-8").strip()
        for path in directory.glob("*.md")
    )


def interactive_brainstorm_bundle_present(repo_root: Path, epic_id: int) -> bool:
    """Return whether an accepted brainstorm bundle sits in the interactive bucket.

    The interactive bucket is written by the `woof-brainstorm` skill straight into
    its final location, so "any markdown present" is too weak a skip signal: a
    partial write, a draft, or a rejected (back-edge) design must not short-circuit
    the headless chain. Require a resolved Contract-2 bundle - a markdown file whose
    front-matter declares ``status: accepted``. A malformed or front-matter-less
    file is ignored, so a half-written bundle never triggers the skip.
    """

    directory = discovery_bucket_dir(repo_root, epic_id, INTERACTIVE_DISCOVERY_BUCKET)
    if not directory.is_dir():
        return False
    for path in sorted(directory.glob("*.md")):
        try:
            front = read_markdown_front_matter(path).front
        except (FileNotFoundError, FrontMatterError):
            continue
        if isinstance(front, dict) and front.get("status") == "accepted":
            return True
    return False


def plan_markdown_path(repo_root: Path, epic_id: int) -> Path:
    return epic_dir(repo_root, epic_id) / "PLAN.md"


def plan_critique_path(repo_root: Path, epic_id: int) -> Path:
    return epic_dir(repo_root, epic_id) / "critique" / "plan.md"


def gate_path(repo_root: Path, epic_id: int) -> Path:
    return epic_dir(repo_root, epic_id) / "gate.md"


def load_plan(repo_root: Path, epic_id: int) -> Plan:
    path = epic_dir(repo_root, epic_id) / "plan.json"
    try:
        return Plan.model_validate_json(path.read_text())
    except FileNotFoundError as exc:
        raise StageStateError(
            f"required Stage-5 artefact missing: {path}",
            operator_recoverable=True,
        ) from exc
    except ValueError as exc:
        raise StageStateError(
            f"required Stage-5 artefact is malformed: {path}",
            operator_recoverable=True,
        ) from exc


def write_plan(repo_root: Path, plan: Plan) -> None:
    path = epic_dir(repo_root, plan.epic_id) / "plan.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(plan.model_dump_json(indent=2) + "\n")
    tmp.replace(path)


def story_by_id(plan: Plan, story_id: str) -> StorySpec:
    for story in plan.stories:
        if story.id == story_id:
            return story
    raise ValueError(f"story {story_id} not found in E{plan.epic_id} plan")


def next_ready_story(plan: Plan) -> StorySpec | None:
    done = {story.id for story in plan.stories if story.status == "done"}
    for story in plan.stories:
        if story.status != "pending":
            continue
        if all(dep in done for dep in story.depends_on):
            return story
    return None


def mark_story_status(repo_root: Path, epic_id: int, story_id: str, status: str) -> None:
    plan = load_plan(repo_root, epic_id)
    if all(story.id != story_id for story in plan.stories):
        raise StageStateError(f"story {story_id} not found in E{epic_id} plan")
    stories = []
    for story in plan.stories:
        if story.id == story_id:
            data = story.model_dump()
            data["status"] = status
            stories.append(StorySpec.model_validate(data))
        else:
            stories.append(story)
    write_plan(repo_root, Plan(epic_id=plan.epic_id, goal=plan.goal, stories=stories))


def append_epic_event(repo_root: Path, epic_id: int, event: dict) -> None:
    path = epic_dir(repo_root, epic_id) / "epic.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def epic_event_exists(repo_root: Path, epic_id: int, **fields: object) -> bool:
    path = epic_dir(repo_root, epic_id) / "epic.jsonl"
    if not path.exists():
        return False
    for event in iter_epic_events(repo_root, epic_id):
        if all(event.get(key) == value for key, value in fields.items()):
            return True
    return False


def iter_epic_events(repo_root: Path, epic_id: int) -> list[dict]:
    path = epic_dir(repo_root, epic_id) / "epic.jsonl"
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    # `woof wf reset` starts a new logical life for the epic: it keeps the full
    # append-only log on disk but appends an `epic_reset` marker, and every
    # state-derivation reader (next_node, the plan/gate resolvers, observe's
    # stage prediction) must ignore the superseded events from before the reset.
    # The raw timeline view reads epic.jsonl directly, so it still shows all
    # history.
    resets = [index for index, event in enumerate(events) if event.get("event") == "epic_reset"]
    if resets:
        return events[resets[-1] + 1 :]
    return events


def append_epic_event_once(
    repo_root: Path, epic_id: int, event_payload: dict, **identity: object
) -> None:
    if not epic_event_exists(repo_root, epic_id, **identity):
        append_epic_event(repo_root, epic_id, event_payload)


def plan_gate_resolved(repo_root: Path, epic_id: int) -> bool:
    """Return whether the mandatory Stage-4 plan gate has been resolved."""

    resolved = False
    for event in iter_epic_events(repo_root, epic_id):
        if event.get("event") == "plan_gate_resolved":
            decision = event.get("decision")
            resolved = decision in {None, "approve"}
        if event.get("event") == "gate_resolved" and event.get("gate_type") == "plan_gate":
            triggered_by = event.get("triggered_by")
            if not isinstance(triggered_by, list):
                triggered_by = []
            decision = event.get("decision")
            if decision in {"revise_epic_contract", "revise_plan"}:
                resolved = False
            elif decision == "approve" and not any(
                trigger in CONFLICT_TRIGGERS for trigger in triggered_by
            ):
                resolved = True
    return resolved


def definition_revision_requested(repo_root: Path, epic_id: int) -> bool:
    """Return whether a gate resolution has requested Stage-2 re-entry."""

    requested = False
    for event in iter_epic_events(repo_root, epic_id):
        if (
            event.get("event") == "gate_resolved"
            and event.get("decision") == "revise_epic_contract"
        ):
            requested = True
        elif event.get("event") == "definition_closed":
            requested = False
    return requested


def readiness_satisfied(repo_root: Path, epic_id: int) -> bool:
    """Return whether Stage-2.5 contract readiness is satisfied for this contract.

    Satisfied iff, after the most recent ``definition_closed``, either a
    ``readiness_passed`` event (the deterministic node passed) or a
    ``readiness_gate_resolved`` event with ``decision == "approve_with_reason"``
    (the operator approved an unready contract at the readiness gate, E17 P2 /
    D-RA) was recorded. A revised or re-closed contract appends a new
    ``definition_closed``, which re-arms readiness - the prior approval no longer
    counts. ``iter_epic_events`` already drops events superseded by an
    ``epic_reset`` marker.
    """

    events = iter_epic_events(repo_root, epic_id)
    last_definition_closed = -1
    for index, event in enumerate(events):
        if event.get("event") == "definition_closed":
            last_definition_closed = index
    if last_definition_closed < 0:
        return False
    for event in events[last_definition_closed + 1 :]:
        if event.get("event") == "readiness_passed":
            return True
        if (
            event.get("event") == "readiness_gate_resolved"
            and event.get("decision") == "approve_with_reason"
        ):
            return True
    return False


def epic_abandoned(repo_root: Path, epic_id: int) -> bool:
    """Return whether the epic has been abandoned (E17 P4 / D-AB).

    The ``abandon_epic`` gate verb appends a graph-owned ``epic_abandoned`` event;
    ``next_node`` consults this to return an abandoned-terminal outcome distinct
    from ``EPIC_COMPLETE``. ``epic_event_exists`` reads through ``iter_epic_events``,
    so a ``woof wf reset`` (which appends an ``epic_reset`` marker) un-abandons the
    epic by superseding the prior ``epic_abandoned`` event.
    """

    return epic_event_exists(repo_root, epic_id, event="epic_abandoned")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _json_loads_ok(path: Path) -> bool:
    try:
        json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return True


def _has_uncommitted_commit_work(repo_root: Path, epic_id: int, story: StorySpec) -> bool:
    try:
        manifest = build_story_manifest(repo_root, epic_id, story)
        changed = set(changed_paths(repo_root))
        staged = set(staged_paths(repo_root))
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise StageStateError(
            "could not inspect interrupted commit state for "
            f"E{epic_id} {story.id}; preserving executor_result.json and "
            f"check-result.json. Git failed: {detail}"
        ) from exc
    except ValueError as exc:
        raise StageStateError(
            "could not inspect interrupted commit state for "
            f"E{epic_id} {story.id}; preserving executor_result.json and "
            f"check-result.json. {exc}"
        ) from exc
    expected = set(manifest.expected_paths)
    return bool(staged or changed & expected)


def _resumable_commit_story(repo_root: Path, epic_id: int, plan: Plan) -> str | None:
    directory = epic_dir(repo_root, epic_id)
    result_path = directory / "executor_result.json"
    check_result_path = directory / "check-result.json"
    if not result_path.exists() or not check_result_path.exists():
        return None

    result = _load_json(result_path)
    check_result = _load_json(check_result_path)
    if result.get("outcome") != "staged_for_verification" or not check_result.get("ok", False):
        return None

    story_id = result.get("story_id")
    if not isinstance(story_id, str):
        return None
    try:
        story = story_by_id(plan, story_id)
    except ValueError:
        return None
    if story.status != "done":
        return None
    critique_path = directory / "critique" / f"story-{story.id}.md"
    if not critique_path.exists():
        return None
    if not _has_uncommitted_commit_work(repo_root, epic_id, story):
        result_path.unlink(missing_ok=True)
        check_result_path.unlink(missing_ok=True)
        return None
    return story.id


def next_node(repo_root: Path, epic_id: int) -> tuple[NodeType | NodeStatus | None, str | None]:
    """Return the next node and story id from filesystem state.

    Terminal outcomes use the first slot for a :class:`NodeStatus` sentinel:
    ``(None, None)`` means the epic is complete, while
    ``(NodeStatus.EPIC_ABANDONED, None)`` means the epic was abandoned
    (E17 P4 / D-AB) - a terminal outcome the runner maps to a distinct
    ``NodeOutput`` status, never to ``EPIC_COMPLETE``.
    """

    directory = epic_dir(repo_root, epic_id)
    # An abandoned epic is unconditionally terminal: short-circuit before any
    # other state read so a lingering gate or plan cannot mask the outcome.
    if epic_abandoned(repo_root, epic_id):
        return NodeStatus.EPIC_ABANDONED, None
    if gate_path(repo_root, epic_id).exists():
        return NodeType.HUMAN_REVIEW, None

    plan_path = directory / "plan.json"
    if not plan_path.exists():
        if (directory / "EPIC.md").exists():
            if epic_event_exists(
                repo_root, epic_id, event="definition_closed"
            ) and not definition_revision_requested(repo_root, epic_id):
                if readiness_satisfied(repo_root, epic_id):
                    return NodeType.BREAKDOWN_PLANNING, None
                return NodeType.CONTRACT_READINESS, None
            return NodeType.EPIC_DEFINITION, None
        if discovery_synthesis_complete(repo_root, epic_id):
            return NodeType.EPIC_DEFINITION, None
        if (directory / "spark.md").exists():
            if interactive_brainstorm_bundle_present(repo_root, epic_id):
                return NodeType.DISCOVERY_SYNTHESIS, None
            for bucket, node in _DISCOVERY_BUCKET_NODES:
                if not discovery_bucket_complete(repo_root, epic_id, bucket):
                    return node, None
            return NodeType.DISCOVERY_SYNTHESIS, None
        raise StageStateError(
            f"required planning artefact missing: {directory / 'plan.json'} "
            f"(or pre-plan input {directory / 'spark.md'} / {directory / 'EPIC.md'})",
            operator_recoverable=True,
        )

    plan = load_plan(repo_root, epic_id)
    resumable_story = _resumable_commit_story(repo_root, epic_id, plan)
    if resumable_story is not None:
        return NodeType.COMMIT, resumable_story

    if all(story.status in TERMINAL_STORY_STATUSES for story in plan.stories):
        return None, None

    in_progress = next((story for story in plan.stories if story.status == "in_progress"), None)
    critique_path = plan_critique_path(repo_root, epic_id)
    if in_progress is None:
        if (directory / "EPIC.md").exists() and not critique_path.exists():
            return NodeType.PLAN_CRITIQUE, None

        if epic_event_exists(repo_root, epic_id, event="breakdown_planned"):
            if not epic_event_exists(repo_root, epic_id, event="plan_critiqued"):
                return NodeType.PLAN_CRITIQUE, None
            if not plan_gate_resolved(repo_root, epic_id):
                return NodeType.PLAN_GATE_OPEN, None

        if critique_path.exists() and not plan_gate_resolved(repo_root, epic_id):
            return NodeType.PLAN_GATE_OPEN, None

    if in_progress is None:
        ready = next_ready_story(plan)
        if ready is None:
            raise StageStateError(
                f"E{epic_id} has pending stories, but no story has satisfied dependencies",
                operator_recoverable=True,
            )
        return NodeType.EXECUTOR_DISPATCH, ready.id

    result_path = directory / "executor_result.json"
    critique_path = story_critique_path(directory, in_progress.id)
    check_result_path = directory / "check-result.json"

    if not result_path.exists():
        return NodeType.GATE_OPEN, in_progress.id
    if not _json_loads_ok(result_path):
        return NodeType.GATE_OPEN, in_progress.id

    result = json.loads(result_path.read_text())
    outcome = result.get("outcome")
    if outcome in {"aborted_with_position", "empty_diff"}:
        return NodeType.GATE_OPEN, in_progress.id
    if outcome != "staged_for_verification":
        return NodeType.GATE_OPEN, in_progress.id
    if not critique_path.exists():
        return NodeType.CRITIQUE_DISPATCH, in_progress.id
    try:
        critique_front = read_markdown_front_matter(critique_path).front
    except (FileNotFoundError, ValueError):
        return NodeType.REVIEW_DISPOSITION, in_progress.id
    if critique_severity(critique_front) == "blocker":
        return NodeType.REVIEW_DISPOSITION, in_progress.id
    if not story_disposition_path(directory, in_progress.id).exists():
        return NodeType.REVIEW_DISPOSITION, in_progress.id
    disposition = validate_story_disposition(directory, epic_id, in_progress.id)
    if not disposition.ok:
        return NodeType.REVIEW_DISPOSITION, in_progress.id
    if not check_result_path.exists():
        return NodeType.VERIFICATION, in_progress.id
    if not _json_loads_ok(check_result_path):
        return NodeType.GATE_OPEN, in_progress.id

    check_result = json.loads(check_result_path.read_text())
    if not check_result.get("ok", False):
        return NodeType.GATE_OPEN, in_progress.id
    return NodeType.COMMIT, in_progress.id
