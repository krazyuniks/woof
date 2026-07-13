"""Mechanical Stage 1-3 planning contract checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from woof.checks.runners.check_5_plan_crossrefs import stage3_plan_contract_failures
from woof.graph.state import Plan
from woof.graph.transitions import discovery_synthesis_paths


@dataclass(frozen=True)
class OpenQuestion:
    id: str
    question: str
    deferral_reason: str


@dataclass(frozen=True)
class PlanningContractResult:
    ok: bool
    failures: list[str]
    open_questions: list[OpenQuestion]


_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_PROBLEM_HEADING_RE = re.compile(r"^#{2,6}\s+problem framing\s*$", re.IGNORECASE)
_PROBLEM_LABEL_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?problem framing(?:\*\*)?\s*[:\-]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_OPEN_QUESTION_HEADING_RE = re.compile(
    r"^#{2,6}\s+(OQ[1-9]\d*)\b(?P<title>.*?)\s*$",
    re.MULTILINE,
)
_NO_OPEN_QUESTIONS_RE = re.compile(r"\bno open questions\b", re.IGNORECASE)
_DEFERRAL_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?"
    r"(?:deferral reason|deferred until|decision needed by)"
    r"(?:\*\*)?(?::\*\*|\s*[:\-])\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def validate_discovery_synthesis_contract(project_key: str, epic_id: int) -> PlanningContractResult:
    """Validate Stage 1 synthesis content beyond non-empty file existence."""

    paths = discovery_synthesis_paths(project_key, epic_id)
    failures: list[str] = []

    concept_path = paths["concept_path"]
    try:
        concept_text = concept_path.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(f"{concept_path}: cannot read: {exc}")
    else:
        if not _has_problem_framing(concept_text):
            failures.append(
                f"{concept_path}: must include a non-empty `## Problem Framing` section"
            )

    questions_path = paths["open_questions_path"]
    try:
        open_questions, question_failures = parse_open_questions(questions_path)
    except OSError as exc:
        return PlanningContractResult(
            ok=False,
            failures=[
                *failures,
                f"{questions_path}: cannot read: {exc}",
            ],
            open_questions=[],
        )
    failures.extend(f"{questions_path}: {failure}" for failure in question_failures)
    return PlanningContractResult(
        ok=not failures,
        failures=failures,
        open_questions=open_questions,
    )


def validate_definition_open_questions(
    project_key: str, epic_id: int, epic_path: Path
) -> list[str]:
    """Ensure Definition resolves or carries every active Discovery question."""

    questions_path = discovery_synthesis_paths(project_key, epic_id)["open_questions_path"]
    if not questions_path.exists():
        return []

    open_questions, question_failures = parse_open_questions(questions_path)
    if question_failures:
        return [f"{questions_path}: {failure}" for failure in question_failures]
    if not open_questions:
        return _definition_unknown_question_failures(epic_path, active_ids=set())

    epic_front = _load_epic_front_matter(epic_path)
    active_ids = {question.id for question in open_questions}
    carried_ids = _question_ids(epic_front.get("open_questions"))
    resolved_ids = _question_ids(epic_front.get("resolved_open_questions"))
    covered_ids = carried_ids | resolved_ids

    failures: list[str] = []
    missing = sorted(active_ids - covered_ids)
    for question_id in missing:
        failures.append(
            f"{epic_path}: "
            f"Definition must resolve or carry forward discovery open question {question_id}"
        )
    duplicates = sorted(carried_ids & resolved_ids)
    for question_id in duplicates:
        failures.append(f"{epic_path}: {question_id} cannot be both carried forward and resolved")
    failures.extend(_definition_unknown_question_failures(epic_path, active_ids))
    return failures


def validate_stage3_plan_contract(repo_root: Path, epic_path: Path, plan_path: Path) -> list[str]:
    """Validate cross-artefact plan invariants before reviewer critique and gate."""

    failures: list[str] = []
    try:
        plan = Plan.model_validate_json(plan_path.read_text(encoding="utf-8")).model_dump(
            exclude_none=True
        )
    except ValueError as exc:
        return [f"{plan_path}: plan.json parse error: {exc}"]

    epic = _load_epic_front_matter(epic_path)
    if not epic:
        return [
            f"{epic_path}: "
            "EPIC.md front-matter could not be parsed for plan cross-reference validation"
        ]
    for failure in stage3_plan_contract_failures(plan, epic):
        failures.append(f"{plan_path}: {failure}")
    return failures


def parse_open_questions(path: Path) -> tuple[list[OpenQuestion], list[str]]:
    """Parse active open questions from a synthesis markdown file."""

    text = path.read_text(encoding="utf-8")
    matches = list(_OPEN_QUESTION_HEADING_RE.finditer(text))
    if not matches:
        if _NO_OPEN_QUESTIONS_RE.search(text):
            return [], []
        return [], [
            "must declare `No open questions.` or include one `## OQ<n> - ...` "
            "heading per active question"
        ]

    failures: list[str] = []
    questions: list[OpenQuestion] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        question_id = match.group(1)
        title = _clean_question_title(match.group("title"))
        block_start = match.end()
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[block_start:block_end]

        if question_id in seen:
            failures.append(f"{question_id}: duplicate open-question ID")
            continue
        seen.add(question_id)

        if _is_resolved_question(match.group(0), block):
            continue

        if not title:
            failures.append(f"{question_id}: heading must include a question")
            continue
        reason = _deferral_reason(block)
        if not reason:
            failures.append(
                f"{question_id}: active open question must include `Deferral reason:` "
                "or `Decision needed by:`"
            )
            continue
        questions.append(OpenQuestion(question_id, title, reason))
    return questions, failures


def _has_problem_framing(text: str) -> bool:
    label_match = _PROBLEM_LABEL_RE.search(text)
    if label_match and label_match.group(1).strip():
        return True

    headings = list(_HEADING_RE.finditer(text))
    for index, heading in enumerate(headings):
        if not _PROBLEM_HEADING_RE.match(heading.group(0)):
            continue
        section_start = heading.end()
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        return bool(text[section_start:section_end].strip())
    return False


def _clean_question_title(raw_title: str) -> str:
    title = raw_title.strip()
    title = re.sub(r"^(?:[-:\u2013\u2014]\s*)+", "", title).strip()
    title = re.sub(r"\s+(?:[-:\u2013\u2014]\s*)?RESOLVED\b.*$", "", title, flags=re.IGNORECASE)
    title = title.replace("~~", "").strip()
    return title


def _is_resolved_question(heading: str, block: str) -> bool:
    combined = f"{heading}\n{block}"
    if "~~" in heading:
        return True
    if re.search(r"\bRESOLVED\b", heading, flags=re.IGNORECASE):
        return True
    return bool(
        re.search(
            r"^\s*(?:[-*]\s*)?(?:\*\*)?status(?:\*\*)?\s*[:\-]\s*resolved\b",
            combined,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )


def _deferral_reason(block: str) -> str:
    match = _DEFERRAL_RE.search(block)
    return match.group(1).strip() if match else ""


def _load_epic_front_matter(epic_path: Path) -> dict[str, Any]:
    text = epic_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    payload = yaml.safe_load(text[4:end]) or {}
    return payload if isinstance(payload, dict) else {}


def _question_ids(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    ids: set[str] = set()
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.add(item["id"])
    return ids


def _definition_unknown_question_failures(epic_path: Path, active_ids: set[str]) -> list[str]:
    front = _load_epic_front_matter(epic_path)
    referenced_ids = _question_ids(front.get("open_questions")) | _question_ids(
        front.get("resolved_open_questions")
    )
    return [
        f"{epic_path}: Definition references unknown discovery open question {question_id}"
        for question_id in sorted(referenced_ids - active_ids)
    ]


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)
