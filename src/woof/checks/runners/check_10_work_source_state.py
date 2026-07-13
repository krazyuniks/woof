"""check_10_work_source_state - Stage-5 Check 10.

Unit-state writeback is engine-exclusive (ADR-017). The engine flips a work
unit's ``state:`` in the work-source PM document; a producer never does. This
check reads the produced diff and rejects it before publish when it mutates unit
state in the drained document, so the producer pre-mark defect - a producer that
marks its own unit done, or marks a sibling it never touched - cannot reach a
commit.

Every unit id on either side of the diff is compared, so inventing a unit that
arrives pre-marked, or deleting a unit and the state it recorded, is caught too.
A document the diff adds with no committed baseline is compared against an empty
baseline rather than waved through: with nothing recorded, every state it carries
must be one the engine's plan accounts for.

A staged state that already matches the engine's plan is the engine's own
writeback carried in the diff, not a producer edit, so it passes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from woof.checks import CheckContext, CheckOutcome
from woof.graph.git import staged_paths
from woof.graph.work_source import (
    BACKLOG_STATE_BY_ENGINE_STATE,
    WorkSourceError,
    resolve_work_source,
    unit_states,
)

CHECK_ID = "check_10_work_source_state"


def check_10_work_source_state_runner(ctx: CheckContext) -> CheckOutcome:
    try:
        document = resolve_work_source(ctx.project_key, ctx.plan.get("context"))
    except WorkSourceError as exc:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="the drained work-source document cannot be read",
            evidence=str(exc),
        )

    if document is None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary="this run has no work-source document, so no unit state can be mutated",
        )

    try:
        relative = document.resolve().relative_to(ctx.repo_root.resolve()).as_posix()
    except ValueError:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=(
                f"work-source document {document} is outside the delivery checkout, "
                "so the produced diff cannot touch it"
            ),
        )

    if relative not in staged_paths(ctx.repo_root):
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"work-source document {relative} is not in the produced diff",
        )

    committed = _blob(ctx.repo_root, f"HEAD:{relative}")
    produced = _blob(ctx.repo_root, f":{relative}")
    if produced is None:  # pragma: no cover - a staged path always has an index blob
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"staged work-source document {relative} has no index blob",
        )

    try:
        # No committed baseline means the produced diff adds the document itself, so
        # there is no recorded state to compare against. The baseline is empty rather
        # than exempt: every unit state the added document carries must still be one
        # the engine's plan accounts for, or a producer wrote it.
        before = unit_states(committed) if committed is not None else {}
        after = unit_states(produced)
    except WorkSourceError as exc:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"work-source document {relative} is not readable as a work_units[] document",
            evidence=str(exc),
            paths=[relative],
        )

    engine = _engine_states(ctx)
    mutations = _mutations(before, after, engine)
    if mutations:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=(
                f"the produced diff mutates work-unit state in {relative}; "
                "only the engine flips unit state in the work-source document"
            ),
            evidence="\n".join(mutations),
            paths=[relative],
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=f"the produced diff mutates no work-unit state in {relative}",
        paths=[relative],
    )


def _mutations(
    before: dict[str, str],
    after: dict[str, str],
    engine: dict[str, str],
) -> list[str]:
    """The unit-state changes in the produced diff that the engine did not make.

    Every unit id on either side is considered, not only the ones the committed
    document already carried: adding a unit that arrives pre-marked, or deleting a
    unit and with it the state it recorded, defeats engine-exclusive writeback just
    as surely as flipping a unit that was already there.
    """

    mutations = []
    for work_unit_id in sorted(before.keys() | after.keys()):
        recorded = before.get(work_unit_id)
        produced = after.get(work_unit_id)
        if produced == recorded:
            continue
        if produced is not None and produced == engine.get(work_unit_id):
            continue  # the state the engine's plan holds: its own writeback, in the diff
        mutations.append(
            f"{work_unit_id}: {recorded or 'absent'} -> {produced if produced is not None else 'removed'}"
        )
    return mutations


def _engine_states(ctx: CheckContext) -> dict[str, str]:
    """The state each unit carries in the engine's plan, in the document's vocabulary."""

    states: dict[str, str] = {}
    for work_unit in ctx.plan.get("work_units", []):
        if not isinstance(work_unit, dict):
            continue
        work_unit_id = work_unit.get("id")
        backlog_state = BACKLOG_STATE_BY_ENGINE_STATE.get(str(work_unit.get("state")))
        if isinstance(work_unit_id, str) and backlog_state is not None:
            states[work_unit_id] = backlog_state
    return states


def _blob(repo_root: Path, revision: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", revision],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None
