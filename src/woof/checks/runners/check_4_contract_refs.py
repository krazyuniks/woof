"""check_4_contract_refs - Stage-5 Check 4.

Validates the contract-decision artefacts owned by the active work unit. The
underlying reference checks are shared with ``woof check-cd`` so the E146
regression fixture and the Stage-5 runner exercise the same contract boundary:
native reference resolution plus bounded conformance checks where the artefact
itself exposes them.
"""

from __future__ import annotations

from typing import Any

from woof.checks import CheckContext, CheckOutcome
from woof.checks.contract_refs import (
    AJV_MISSING_HINT,
    ContractRefUsageError,
    validate_contract_refs,
)

CHECK_ID = "check_4_contract_refs"


def check_4_contract_refs_runner(ctx: CheckContext) -> CheckOutcome:
    work_unit = _work_unit_for_context(ctx)
    if work_unit is None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"work unit {ctx.work_unit_id} not found in plan.json",
            paths=[_display_path(ctx.epic_dir / "plan.json", ctx.repo_root)],
        )

    owned_cd_ids = _owned_contract_ids(work_unit)
    if owned_cd_ids is None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"work unit {ctx.work_unit_id} has malformed implements_contract_decisions[]",
            evidence=(
                f"implements_contract_decisions={work_unit.get('implements_contract_decisions')!r}"
            ),
            paths=[_display_path(ctx.epic_dir / "plan.json", ctx.repo_root)],
        )

    epic_md = ctx.epic_dir / "EPIC.md"
    if not owned_cd_ids:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"work unit {ctx.work_unit_id} owns no contract decision refs to verify",
            paths=[_display_path(epic_md, ctx.repo_root)],
        )

    try:
        result = validate_contract_refs(epic_md, only_ids=owned_cd_ids)
    except ContractRefUsageError as exc:
        message = str(exc)
        if message == AJV_MISSING_HINT:
            summary = "ajv-cli unavailable; run `woof preflight` for install instructions"
        else:
            summary = "contract reference validation could not load EPIC.md"
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=summary,
            evidence=message,
            paths=[_display_path(epic_md, ctx.repo_root)],
        )

    failed = [finding for finding in result.findings if not finding.ok]
    evidence = "\n".join(
        f"{finding.id} ({finding.kind}) {finding.ref}: {finding.detail}"
        for finding in result.findings
        if not finding.ok
    )
    epic_display = _display_path(epic_md, ctx.repo_root)

    if failed:
        broken_sources = sorted({finding.source_path for finding in failed if finding.source_path})
        paths = [epic_display, *broken_sources]
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"{len(failed)} owned contract reference(s) failed validation",
            evidence=evidence,
            paths=paths,
            command=f"woof check-cd --format json {epic_display}",
            exit_code=1,
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=f"all {result.verified} owned contract reference(s) verified",
        evidence=_format_verified(result.findings),
        paths=[epic_display],
        command=f"woof check-cd --format json {epic_display}",
        exit_code=0,
    )


def _work_unit_for_context(ctx: CheckContext) -> dict[str, Any] | None:
    work_units = ctx.plan.get("work_units", [])
    if not isinstance(work_units, list):
        return None
    for work_unit in work_units:
        if isinstance(work_unit, dict) and work_unit.get("id") == ctx.work_unit_id:
            return work_unit
    return None


def _owned_contract_ids(work_unit: dict[str, Any]) -> set[str] | None:
    raw = work_unit.get("implements_contract_decisions", [])
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return None
    return set(raw)


def _format_verified(findings: list[Any]) -> str | None:
    if not findings:
        return None
    return "\n".join(
        f"{finding.id} ({finding.kind}) {finding.ref}: {finding.detail}" for finding in findings
    )


def _display_path(path, repo_root) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)
