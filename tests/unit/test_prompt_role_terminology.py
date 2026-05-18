"""Static checks for ADR-002 role terminology in prompt files."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_ROOTS = (
    REPO_ROOT / "playbooks",
    REPO_ROOT / ".claude" / "commands",
)
FORBIDDEN_PATTERNS = {
    "provider-specific prompt identity": re.compile(r"\b(?:Claude|Codex|claude|codex)\b"),
    "Ryan-local wrapper spelling": re.compile(r"\b(?:cld|cod)\b"),
    "legacy planner role": re.compile(r"\bplanner\b"),
    "legacy story-executor role": re.compile(r"\bstory-executor\b"),
    "legacy critiquer role": re.compile(r"\bcritiquer\b"),
    "LLM orchestration authority": re.compile(r"\borchestrator\b"),
}


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
