"""check_6_critique_blocker — Stage-5 Check 6.

Verifies that the reviewer critique artefact:
  1. Exists at critique/work-unit-S<k>.md
  2. Has valid YAML front-matter with required fields
  3. Top-level severity equals max(findings[].severity)
  4. severity != "blocker"
  5. Non-blocking critiques have a valid primary disposition record
"""

from __future__ import annotations

import yaml

from woof.checks import CheckContext, CheckOutcome
from woof.graph.dispositions import (
    SEVERITIES,
    check_blocker_findings_evidence,
    check_critique_rollup,
    validate_work_unit_disposition,
)

_VALID_SEVERITIES = SEVERITIES
_REQUIRED_FIELDS = {"target", "target_id", "severity", "timestamp", "harness"}

CHECK_ID = "check_6_critique_blocker"


def check_6_critique_blocker_runner(ctx: CheckContext) -> CheckOutcome:
    critique_path = ctx.epic_dir / "critique" / f"work-unit-{ctx.work_unit_id}.md"

    if not critique_path.exists():
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"critique file missing: critique/work-unit-{ctx.work_unit_id}.md",
        )

    text = critique_path.read_text()
    if not text.startswith("---\n"):
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="critique file missing YAML front-matter (must start with '---')",
        )

    end = text.find("\n---\n", 4)
    if end < 0:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="critique file has unterminated YAML front-matter",
        )

    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"critique front-matter YAML parse error: {exc}",
        )

    missing = _REQUIRED_FIELDS - set(fm.keys())
    if missing:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"critique front-matter missing required fields: {sorted(missing)}",
        )

    top_sev = fm["severity"]
    if top_sev not in _VALID_SEVERITIES:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"critique severity invalid: {top_sev!r} (expected info|minor|blocker)",
        )

    findings = fm.get("findings") or []
    rollup_errors = check_critique_rollup(fm)
    if rollup_errors:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=rollup_errors[0],
            evidence=f"findings severities: {[f.get('severity') for f in findings if isinstance(f, dict)]}",
        )

    if top_sev == "blocker":
        blocker_findings = [
            finding
            for finding in findings
            if isinstance(finding, dict) and finding.get("severity") == "blocker"
        ]

        # S4: every blocker finding must carry resolvable evidence.
        bad_evidence = _check_blocker_evidence(blocker_findings, ctx)
        if bad_evidence:
            return CheckOutcome(
                id=CHECK_ID,
                ok=False,
                severity="blocker",
                summary=f"{len(bad_evidence)} blocker finding(s) lack resolvable evidence",
                evidence="\n".join(bad_evidence),
            )

        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"critique severity is blocker ({len(blocker_findings)} finding(s))",
            evidence=_format_findings(blocker_findings),
        )

    disposition = validate_work_unit_disposition(ctx.epic_dir, ctx.epic_id, ctx.work_unit_id)
    if not disposition.ok:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="non-blocking reviewer critique is missing a valid primary disposition",
            evidence="; ".join(disposition.errors),
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity=top_sev,
        summary=f"critique severity={top_sev!r}; primary disposition recorded",
    )


def _check_blocker_evidence(blocker_findings: list[dict], ctx: CheckContext) -> list[str]:
    return check_blocker_findings_evidence(
        blocker_findings,
        repo_root=ctx.repo_root,
        plan=ctx.plan,
        epic_dir=ctx.epic_dir,
    )


def _format_findings(findings: list[dict]) -> str | None:
    if not findings:
        return None
    lines: list[str] = []
    for finding in findings:
        finding_id = str(finding.get("id") or "<unknown>")
        category = finding.get("category")
        category_text = f" [{category}]" if isinstance(category, str) and category else ""
        summary = str(finding.get("summary") or "").strip()
        evidence = str(finding.get("evidence") or "").strip()
        suggestion = str(finding.get("suggestion") or "").strip()
        line = (
            f"{finding_id}{category_text}: {summary}" if summary else f"{finding_id}{category_text}"
        )
        if evidence:
            line += f" Evidence: {evidence}"
        if suggestion:
            line += f" Suggestion: {suggestion}"
        lines.append(line)
    return "\n".join(lines)
