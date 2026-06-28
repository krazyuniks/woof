"""Tracker-agnostic transforms between ``EPIC.md`` and the managed issue body.

The Definition-stage contract is authored as ``EPIC.md`` front-matter. Every
tracker renders the same managed markdown body from that front-matter and
parses a managed body back into front-matter on cold-start. The rendering is
deterministic and provider-neutral; only where the body is *stored* differs
between adapters.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from woof.graph.state import Plan
from woof.trackers.base import TrackerError

WOOF_SENTINEL = (
    "<!-- woof — structured sections above are rewritten on Definition/plan "
    "changes. Free-form prose above `## Observable Outcomes` is preserved on "
    "overwrite. Do not edit structured sections directly in the issue tracker. -->"
)
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_STRUCTURED_HEADING_RE = re.compile(r"^##\s+Observable Outcomes\s*$", re.MULTILINE)
_OUTCOME_RE = re.compile(r"^- \*\*(O[1-9]\d*)\*\*\s+(?:—|-)\s+(.+?)\s*$")
_OPEN_QUESTION_RE = re.compile(
    r"^- \*\*(OQ[1-9]\d*)\*\*\s+(?:—|-)\s+(.+?)"
    r"(?:\s+\(Deferred:\s+(.+?)\))?\s*$"
)
_VERIFICATION_RE = re.compile(r"^\s+- Verification:\s+(.+?)\s*$")
_DEPRECATED_OUTCOME_RE = re.compile(r"\s+_\(deprecated(?:\s+→\s+(O[1-9]\d*))?\)_$")
_DEPRECATED_CD_RE = re.compile(r"\s+_\(deprecated(?:\s+→\s+(CD[1-9]\d*))?\)_$")


def split_epic_front_matter(path: Any) -> tuple[dict[str, Any], str]:
    """Return ``EPIC.md`` front-matter and prose body."""

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: no YAML front-matter (file must start with '---\\n')")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{path}: unterminated YAML front-matter")
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict):
        raise ValueError(f"{path}: YAML front-matter root must be an object")
    prose = text[end + len("\n---\n") :]
    return front, prose


def render_epic_issue_body(
    front: dict[str, Any],
    prose: str,
    remote_body: str | None = None,
    *,
    plan: Plan | None = None,
    completed: bool = False,
) -> str:
    """Render ``EPIC.md`` front-matter into the managed issue body.

    If a managed body already has the structured heading, only the free-form
    prefix above that heading is preserved. Managed sections are rewritten
    wholesale from schema-valid front-matter.
    """

    out: list[str] = []
    remote_prefix = managed_body_prefix(remote_body)
    if remote_prefix is not None:
        out.append(remote_prefix.rstrip() + "\n\n")
    else:
        intent = _front_intent(front) or _first_paragraph(prose)
        out.append((intent or "_(intent pending)_") + "\n\n")

    out.append("## Observable Outcomes\n\n")
    for outcome in front["observable_outcomes"]:
        suffix = _deprecation_suffix(outcome)
        out.append(f"- **{outcome['id']}** — {_single_line(outcome['statement'])}{suffix}\n")
        out.append(f"  - Verification: {outcome['verification']}\n")
    out.append("\n")

    decisions = front.get("contract_decisions") or []
    if decisions:
        out.append("## Contract Decisions\n\n")
        out.append("| ID | Related Outcomes | Title | Contract Reference |\n")
        out.append("|---|---|---|---|\n")
        for decision in decisions:
            related = ", ".join(decision["related_outcomes"])
            title = _table_cell(decision["title"] + _deprecation_suffix(decision))
            out.append(f"| {decision['id']} | {related} | {title} | {_contract_ref(decision)} |\n")
        out.append("\n")

    out.append("## Acceptance Criteria\n\n")
    for criterion in front["acceptance_criteria"]:
        out.append(f"- {_single_line(criterion)}\n")
    out.append("\n")

    open_questions = front.get("open_questions") or []
    if open_questions:
        out.append("## Open Questions\n\n")
        for question in open_questions:
            out.append(f"- {_open_question_line(question)}\n")
        out.append("\n")

    if plan is not None:
        out.append(render_plan_summary(plan))

    if completed:
        if plan is None:
            raise TrackerError("completion rendering requires plan.json")
        out.append(render_completion_summary(plan))

    out.append("---\n\n")
    out.append(WOOF_SENTINEL + "\n")
    return "".join(out)


def render_plan_summary(plan: Plan) -> str:
    out = ["## Plan Summary\n\n"]
    for unit in plan.work_units:
        out.append(f"- **{unit.id}** — {_single_line(unit.title)}\n")
    out.append("\n")
    return "".join(out)


def render_completion_summary(plan: Plan) -> str:
    total = len(plan.work_units)
    done = sum(1 for unit in plan.work_units if unit.status == "done")
    noun = "work unit" if total == 1 else "work units"
    return f"## Closing Summary\n\nEpic completed with {done}/{total} planned {noun} done.\n\n"


def epic_markdown_from_issue(*, epic_id: int, title: str, body: str) -> str | None:
    """Reconstruct ``EPIC.md`` text from a managed tracker body, or None."""

    split = _split_managed_body(body)
    if split is None:
        return None
    sections = _sections(body)
    if "Observable Outcomes" not in sections or "Acceptance Criteria" not in sections:
        raise TrackerError(
            "tracker epic has managed Woof headings but is missing required structured sections"
        )
    front: dict[str, Any] = {
        "epic_id": epic_id,
        "title": title,
        "observable_outcomes": _parse_observable_outcomes(sections["Observable Outcomes"]),
        "contract_decisions": _parse_contract_decisions(sections.get("Contract Decisions", "")),
        "acceptance_criteria": _parse_bullets(
            sections["Acceptance Criteria"], label="Acceptance Criteria", require_items=True
        ),
    }
    open_questions = _parse_open_questions(sections.get("Open Questions", ""))
    if open_questions:
        front["open_questions"] = open_questions

    prose = split[0].strip()
    yaml_text = yaml.safe_dump(front, sort_keys=False)
    return f"---\n{yaml_text}---\n{prose}\n"


def seed_from_spark(spark: str) -> tuple[str, str]:
    """Split a raw spark into ``(title, body)``.

    The first non-empty line is the title; the remaining lines are the body.
    A spark with only a title uses the title as the body. The returned body
    carries a trailing newline so it round-trips as a tracker issue body.
    """

    text = spark.strip()
    if not text:
        raise TrackerError("spark must not be empty")
    lines = text.splitlines()
    title = lines[0].strip()
    if not title:
        raise TrackerError("spark must contain a non-empty first line")
    body = "\n".join(lines[1:]).strip() or title
    return title, body + "\n"


def spark_markdown(title: str, body: str) -> str:
    """Render a ``spark.md`` body from a tracker title and free-form body."""

    split = _split_managed_body(body) if body else None
    prose = (split[0] if split else body).strip()
    if prose:
        return f"# {title.strip()}\n\n{prose}\n"
    return f"# {title.strip()}\n"


def managed_body_prefix(body: str | None) -> str | None:
    """Return the free-form prefix above the managed heading, or None."""

    if body is None:
        return None
    split = _split_managed_body(body)
    if split is None:
        return None
    return split[0]


def _sections(body: str) -> dict[str, str]:
    matches = list(_HEADING_RE.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[heading] = body[start:end].strip()
    return sections


def _parse_observable_outcomes(section: str) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    lines = section.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        match = _OUTCOME_RE.match(line)
        if not match:
            index += 1
            continue
        outcome_id, statement = match.groups()
        statement, deprecated, replaced_by = _strip_deprecation(statement, _DEPRECATED_OUTCOME_RE)
        verification: str | None = None
        lookahead = index + 1
        while lookahead < len(lines) and not lines[lookahead].lstrip().startswith("- **"):
            verification_match = _VERIFICATION_RE.match(lines[lookahead])
            if verification_match:
                verification = verification_match.group(1).strip()
                break
            lookahead += 1
        if verification not in {"automated", "manual", "hybrid"}:
            raise TrackerError(
                f"Observable outcome {outcome_id} has invalid or missing verification"
            )
        item: dict[str, Any] = {
            "id": outcome_id,
            "statement": statement,
            "verification": verification,
        }
        if deprecated:
            item["deprecated"] = True
        if replaced_by:
            item["replaced_by"] = replaced_by
        outcomes.append(item)
        index = lookahead + 1
    if not outcomes:
        raise TrackerError("tracker epic has no parseable observable outcomes")
    return outcomes


def _parse_contract_decisions(section: str) -> list[dict[str, Any]]:
    if not section.strip():
        return []
    decisions: list[dict[str, Any]] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4 or cells[0] == "ID":
            continue
        decision_id, related_raw, title_raw, ref_raw = cells
        if not re.fullmatch(r"CD[1-9]\d*", decision_id):
            continue
        title, deprecated, replaced_by = _strip_deprecation(title_raw, _DEPRECATED_CD_RE)
        item: dict[str, Any] = {
            "id": decision_id,
            "related_outcomes": [
                value.strip() for value in related_raw.split(",") if value.strip()
            ],
            "title": title,
        }
        kind, ref = _parse_contract_ref(ref_raw)
        item[kind] = ref
        if deprecated:
            item["deprecated"] = True
        if replaced_by:
            item["replaced_by"] = replaced_by
        decisions.append(item)
    return decisions


def _parse_contract_ref(raw: str) -> tuple[str, str]:
    text = raw.strip().strip("`").strip()
    if not text:
        raise TrackerError("Contract decision row is missing a contract reference")
    if ":" not in text:
        raise TrackerError(f"Unsupported contract reference: {raw}")
    kind, ref = [part.strip() for part in text.split(":", 1)]
    field_by_kind = {
        "openapi": "openapi_ref",
        "pydantic": "pydantic_ref",
        "json_schema": "json_schema_ref",
    }
    try:
        field = field_by_kind[kind]
    except KeyError as exc:
        raise TrackerError(f"Unsupported contract reference kind: {kind}") from exc
    if not ref:
        raise TrackerError(f"Contract reference for {kind} is empty")
    return field, ref


def _strip_deprecation(text: str, pattern: re.Pattern[str]) -> tuple[str, bool, str | None]:
    match = pattern.search(text)
    if not match:
        return text.strip(), False, None
    return text[: match.start()].strip(), True, match.group(1)


def _split_managed_body(body: str) -> tuple[str, str] | None:
    match = _STRUCTURED_HEADING_RE.search(body)
    if not match:
        return None
    return body[: match.start()], body[match.start() :]


def _front_intent(front: dict[str, Any]) -> str:
    intent = front.get("intent")
    return intent.strip() if isinstance(intent, str) else ""


def _first_paragraph(prose: str) -> str:
    paragraphs = re.split(r"\n\s*\n", prose.strip())
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        if stripped:
            return _single_line(stripped)
    return ""


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _table_cell(value: str) -> str:
    return _single_line(value).replace("|", r"\|")


def _deprecation_suffix(item: dict[str, Any]) -> str:
    if not item.get("deprecated"):
        return ""
    replaced_by = item.get("replaced_by")
    return f" _(deprecated → {replaced_by})_" if replaced_by else " _(deprecated)_"


def _contract_ref(decision: dict[str, Any]) -> str:
    if decision.get("openapi_ref"):
        return f"`openapi: {decision['openapi_ref']}`"
    if decision.get("pydantic_ref"):
        return f"`pydantic: {decision['pydantic_ref']}`"
    if decision.get("json_schema_ref"):
        return f"`json_schema: {decision['json_schema_ref']}`"
    return ""


def _open_question_line(question: object) -> str:
    if not isinstance(question, dict):
        return _single_line(str(question))
    question_id = str(question.get("id", "")).strip()
    text = _single_line(str(question.get("question", "")))
    reason = _single_line(str(question.get("deferral_reason", "")))
    line = f"**{question_id}** — {text}"
    if reason:
        line += f" (Deferred: {reason})"
    return line


def _parse_bullets(markdown: str, *, label: str, require_items: bool = False) -> list[str]:
    items = [
        line.strip()[2:].strip() for line in markdown.splitlines() if line.strip().startswith("- ")
    ]
    if require_items and not items:
        raise TrackerError(f"{label} has no parseable bullet items")
    return items


def _parse_open_questions(markdown: str) -> list[dict[str, str]]:
    questions: list[dict[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        match = _OPEN_QUESTION_RE.match(line)
        if match:
            question_id, question, deferral_reason = match.groups()
        else:
            question_id = f"OQ{len(questions) + 1}"
            question = line[2:].strip()
            deferral_reason = "carried forward from the tracker epic"
        item = {
            "id": question_id,
            "question": _single_line(question),
            "deferral_reason": _single_line(
                deferral_reason or "carried forward from the tracker epic"
            ),
        }
        questions.append(item)
    return questions
