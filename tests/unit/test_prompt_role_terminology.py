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
EXECUTE_WORK_UNIT_PROMPT = REPO_ROOT / "playbooks" / "execution" / "work-unit.md"
WORK_UNIT_CRITIQUE_PROMPT = REPO_ROOT / "playbooks" / "critique" / "work-unit.md"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture.md"

# E19 S2 drift guard: each dispatch playbook must declare the cartography docs
# that S1 injects for its node. If the node mapping changes in nodes.py, this
# table must be updated to match.
PLAYBOOK_CARTOGRAPHY_DOCS: dict[str, list[str]] = {
    "playbooks/discovery/research.md": [
        ".woof/codebase/STACK.md",
        ".woof/codebase/INTEGRATIONS.md",
        ".woof/codebase/CONCERNS.md",
    ],
    "playbooks/discovery/thinking.md": [
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STRUCTURE.md",
    ],
    "playbooks/discovery/ideate.md": [
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STACK.md",
        ".woof/codebase/INTEGRATIONS.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/CONVENTIONS.md",
        ".woof/codebase/TESTING.md",
        ".woof/codebase/CONCERNS.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
        ".woof/codebase/PRINCIPLES.md",
    ],
    "playbooks/discovery/synthesis.md": [
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STACK.md",
        ".woof/codebase/INTEGRATIONS.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/CONVENTIONS.md",
        ".woof/codebase/TESTING.md",
        ".woof/codebase/CONCERNS.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
        ".woof/codebase/PRINCIPLES.md",
    ],
    "playbooks/discovery/definition.md": [
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/CONCERNS.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
        ".woof/codebase/PRINCIPLES.md",
    ],
    "playbooks/planning/breakdown.md": [
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
        ".woof/codebase/PRINCIPLES.md",
    ],
    "playbooks/execution/work-unit.md": [
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/CONVENTIONS.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
        ".woof/codebase/PRINCIPLES.md",
        ".woof/codebase/files.txt",
    ],
    "playbooks/critique/plan.md": [
        ".woof/codebase/CURRENT-ARCHITECTURE.md",
        ".woof/codebase/STRUCTURE.md",
        ".woof/codebase/CONCERNS.md",
        ".woof/codebase/TARGET-ARCHITECTURE.md",
    ],
    "playbooks/critique/work-unit.md": [
        ".woof/codebase/CONVENTIONS.md",
        ".woof/codebase/TESTING.md",
        ".woof/codebase/CONCERNS.md",
    ],
}
FORBIDDEN_PATTERNS = {
    "provider-specific prompt identity": re.compile(r"\b(?:Claude|Codex|claude|codex)\b"),
    "private wrapper spelling": re.compile(r"\b(?:cld|cod)\b"),
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
    "Right-sized work units",
    "Do not author `PLAN.md`",
    "Do not write `gate.md`",
    "Do not select the next node",
)
REQUIRED_STAGE5_EXECUTE_WORK_UNIT_PROMPT_PHRASES = (
    "Tracer-bullet red-green-refactor discipline",
    "`work_unit.satisfies[]` outcomes",
    "one assertion-bearing test",
    "before implementation",
    "Run the configured quality command after each cycle",
    "refactor pass with the tests as the harness",
    "horizontal-slicing anti-pattern",
    "all tests first then all implementation",
    "imagined-behaviour fingerprint",
)
REQUIRED_STAGE5_WORK_UNIT_CRITIQUE_PROMPT_PHRASES = (
    "Graph-owned input JSON",
    "Evidence must be concrete",
    "Test-fingerprint fidelity",
    "Behaviour-anchored assertions",
    "Data-structure-anchored assertions",
    "`test-fingerprint` finding with `severity: minor`",
    "category `marker_semantic_mismatch`",
    "category `contract_implementation`",
    "CD id/ref",
    "Check 9 periodic-review valve",
)
REQUIRED_STAGE5_ARCHITECTURE_PHRASES = (
    "tracer-bullet red-green-refactor",
    "assertion-bearing RED test before implementation",
    "horizontal-slicing anti-pattern",
    "imagined-behaviour fingerprint",
    "Checks 1-9",
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


def test_stage5_work_unit_prompts_codify_producer_discipline() -> None:
    execute_text = EXECUTE_WORK_UNIT_PROMPT.read_text()
    critique_text = WORK_UNIT_CRITIQUE_PROMPT.read_text()
    architecture_text = ARCHITECTURE_DOC.read_text()

    execute_missing = [
        phrase
        for phrase in REQUIRED_STAGE5_EXECUTE_WORK_UNIT_PROMPT_PHRASES
        if phrase not in execute_text
    ]
    critique_missing = [
        phrase
        for phrase in REQUIRED_STAGE5_WORK_UNIT_CRITIQUE_PROMPT_PHRASES
        if phrase not in critique_text
    ]
    architecture_missing = [
        phrase for phrase in REQUIRED_STAGE5_ARCHITECTURE_PHRASES if phrase not in architecture_text
    ]

    assert not execute_missing, "missing Stage 5 execute-work-unit phrases: " + ", ".join(
        execute_missing
    )
    assert not critique_missing, "missing Stage 5 work-unit critique phrases: " + ", ".join(
        critique_missing
    )
    assert not architecture_missing, "missing Stage 5 architecture phrases: " + ", ".join(
        architecture_missing
    )


DISCOVERY_BUILDING_BLOCK_DIRS = (
    REPO_ROOT / "playbooks" / "discovery" / "research",
    REPO_ROOT / "playbooks" / "discovery" / "consider",
)
NON_PORTABLE_PLAYBOOK_TOKENS = {
    "interactive AskUserQuestion tool": "AskUserQuestion",
    "slash-command argument placeholder": "$ARGUMENTS",
    "interactive intake gate": "<intake_gate>",
    "interactive decision gate": "<decision_gate>",
    "Claude-Code slash-command frontmatter": "argument-hint:",
    "non-portable artefact output path": "artifacts/research",
}


def test_discovery_building_block_playbooks_are_portable() -> None:
    """Stage-1 building-block playbooks must be non-interactive and bucket-bound."""

    failures: list[str] = []
    for directory in DISCOVERY_BUILDING_BLOCK_DIRS:
        playbooks = sorted(directory.glob("*.md"))
        assert playbooks, f"no building-block playbooks under {directory}"
        for path in playbooks:
            text = path.read_text()
            rel = path.relative_to(REPO_ROOT)
            for label, token in NON_PORTABLE_PLAYBOOK_TOKENS.items():
                if token in text:
                    failures.append(f"{rel}: {label}: {token!r}")
            if "type: discovery-playbook" not in text:
                failures.append(f"{rel}: missing `type: discovery-playbook` frontmatter")
            if "bucket:" not in text:
                failures.append(f"{rel}: missing `bucket:` frontmatter")
            if ".woof/epics/E<N>/discovery/" not in text:
                failures.append(f"{rel}: must direct output into a .woof/epics discovery bucket")

    assert not failures, "\n".join(failures)


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


def test_dispatch_playbooks_declare_cartography_context_documents() -> None:
    """E19 S2 drift guard: each dispatch playbook must name the cartography docs
    that nodes.py injects for its node. Update PLAYBOOK_CARTOGRAPHY_DOCS when the
    node mapping in _DISCOVERY_BUCKET_CARTOGRAPHY_DOCS et al. changes."""
    failures: list[str] = []
    for rel_path, expected_docs in PLAYBOOK_CARTOGRAPHY_DOCS.items():
        path = REPO_ROOT / rel_path
        text = path.read_text()
        for doc in expected_docs:
            if doc not in text:
                failures.append(f"{rel_path}: missing cartography doc reference: {doc!r}")

    assert not failures, "\n".join(failures)
