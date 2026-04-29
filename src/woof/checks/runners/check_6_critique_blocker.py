"""check_6_critique_blocker — Stage-5 Check 6.

Verifies that the Codex critique artefact:
  1. Exists at critique/story-S<k>.md
  2. Has valid YAML front-matter with required fields
  3. Top-level severity equals max(findings[].severity)
  4. severity != "blocker"
"""

from __future__ import annotations

import yaml

from woof.checks import CheckContext, CheckOutcome

_SEVERITY_ORDER = {"info": 0, "minor": 1, "blocker": 2}
_VALID_SEVERITIES = set(_SEVERITY_ORDER)
_REQUIRED_FIELDS = {"target", "target_id", "severity", "timestamp", "harness"}

CHECK_ID = "check_6_critique_blocker"


def check_6_critique_blocker_runner(ctx: CheckContext) -> CheckOutcome:
    critique_path = ctx.epic_dir / "critique" / f"story-{ctx.story_id}.md"

    if not critique_path.exists():
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"critique file missing: critique/story-{ctx.story_id}.md",
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
    if findings:
        max_sev = max(
            (f.get("severity", "info") for f in findings if isinstance(f, dict)),
            key=lambda s: _SEVERITY_ORDER.get(s, 0),
        )
        if _SEVERITY_ORDER.get(top_sev, 0) != _SEVERITY_ORDER.get(max_sev, 0):
            return CheckOutcome(
                id=CHECK_ID,
                ok=False,
                severity="blocker",
                summary=(
                    f"critique top-level severity {top_sev!r} != max finding severity {max_sev!r}"
                ),
                evidence=f"findings severities: {[f.get('severity') for f in findings if isinstance(f, dict)]}",
            )

    if top_sev == "blocker":
        blocker_summaries = [
            f.get("summary", "")
            for f in findings
            if isinstance(f, dict) and f.get("severity") == "blocker"
        ]
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"critique severity is blocker ({len(blocker_summaries)} finding(s))",
            evidence="; ".join(blocker_summaries) if blocker_summaries else None,
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity=top_sev,
        summary=f"critique severity={top_sev!r}; no blocker findings",
    )
