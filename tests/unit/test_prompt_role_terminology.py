"""Static checks for ADR-002 role terminology in prompt files."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_ROOTS = (
    REPO_ROOT / "playbooks",
    REPO_ROOT / ".claude" / "commands",
)
PLANNING_BREAKDOWN_PROMPT = REPO_ROOT / "playbooks" / "planning" / "breakdown.md"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture.md"
FORBIDDEN_PATTERNS = {
    "provider-specific prompt identity": re.compile(r"\b(?:Claude|Codex|claude|codex)\b"),
    "Ryan-local wrapper spelling": re.compile(r"\b(?:cld|cod)\b"),
    "legacy planner role": re.compile(r"\bplanner\b"),
    "legacy story-executor role": re.compile(r"\bstory-executor\b"),
    "legacy critiquer role": re.compile(r"\bcritiquer\b"),
    "LLM orchestration authority": re.compile(r"\borchestrator\b"),
}
REQUIRED_STAGE3_BREAKDOWN_PROMPT_PHRASES = (
    "Graph-owned input:",
    "{planning_input_json}",
    "produce only `plan.json`",
    "Outcome-driven granularity",
    "Path discipline",
    "Explicit dependencies",
    "Contract ownership",
    "Right-sized stories",
    "Do not author `PLAN.md`",
    "`woof dispatch`",
    "Do not write `gate.md`",
    "Do not select the next node",
)


def _prompt_files() -> list[Path]:
    files: list[Path] = []
    for root in PROMPT_ROOTS:
        files.extend(sorted(root.rglob("*.md")))
    return files


def test_prompts_use_semantic_primary_reviewer_roles() -> None:
    failures: list[str] = []
    for path in _prompt_files():
        text = path.read_text()
        rel = path.relative_to(REPO_ROOT)
        for label, pattern in FORBIDDEN_PATTERNS.items():
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                failures.append(f"{rel}:{line_no}: {label}: {match.group(0)!r}")

    assert not failures, "\n".join(failures)


def test_stage3_breakdown_prompt_owns_plan_generation_rules() -> None:
    text = PLANNING_BREAKDOWN_PROMPT.read_text()
    missing = [phrase for phrase in REQUIRED_STAGE3_BREAKDOWN_PROMPT_PHRASES if phrase not in text]

    assert not missing, "missing Stage 3 breakdown prompt phrases: " + ", ".join(missing)
    assert not (REPO_ROOT / "playbooks" / "discovery" / "breakdown.md").exists()


def test_stage3_plan_generation_rules_are_not_architecture_prose() -> None:
    text = ARCHITECTURE_DOC.read_text()
    forbidden = (
        "### Stage 3 Breakdown prompt philosophy",
        "**Prompt rules:**",
        "**The prompt forbids:**",
        "Self-validation before reviewer dispatch",
    )
    leftovers = [phrase for phrase in forbidden if phrase in text]

    assert not leftovers, "Stage 3 prompt guidance left in architecture: " + ", ".join(leftovers)
    assert "`playbooks/planning/breakdown.md`" in text
