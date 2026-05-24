"""Conformance matrix for the nine real Stage-5 check runners."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml

from woof.checks import CheckContext
from woof.checks.registry import REGISTRY, STAGE_5_CHECK_IDS

pytestmark = pytest.mark.host_only

REPO_ROOT = Path(__file__).resolve().parents[2]
E146_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "woof" / "e146"
ORIG_OPENAPI = "tests/fixtures/woof/e146/spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch"
ORIG_PYDANTIC = "tests/fixtures/woof/e146/webapp/comment_schema.py:CommentEdit"
ORIG_JSON_SCHEMA = "tests/fixtures/woof/e146/schemas/audit-event.schema.json"


@dataclass(frozen=True)
class Stage5ConformanceFixture:
    case_id: str
    check_id: str
    kind: Literal["success", "failure"]
    contract: str
    build: Callable[[Path], CheckContext]
    expected_ok: bool
    expected_severity: str | None
    summary_contains: str
    evidence_contains: str | None = None
    path_contains: str | None = None


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True)


def _init_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")


def _stage(repo_root: Path, *paths: str) -> None:
    _git(repo_root, "add", "--", *paths)


def _write(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def _write_quality_gates(repo_root: Path, command: str) -> None:
    _write(
        repo_root / ".woof" / "quality-gates.toml",
        f"[gates.unit]\ncommand = {json.dumps(command)}\ntimeout_seconds = 5\n",
    )


def _story(**overrides: Any) -> dict[str, Any]:
    story = {
        "id": "S1",
        "title": "Story 1",
        "intent": "Exercise a Stage-5 check contract.",
        "paths": ["src/"],
        "satisfies": ["O1"],
        "implements_contract_decisions": [],
        "uses_contract_decisions": [],
        "depends_on": [],
        "tests": {"count": 1, "types": ["unit"]},
        "status": "in_progress",
    }
    story.update(overrides)
    return story


def _plan(*stories: dict[str, Any], epic_id: int = 1) -> dict[str, Any]:
    return {
        "epic_id": epic_id,
        "goal": "Exercise Stage-5 checker contracts.",
        "stories": list(stories) or [_story()],
    }


def _ctx(
    repo_root: Path,
    *,
    epic_id: int = 1,
    story_id: str = "S1",
    plan: dict[str, Any] | None = None,
    epic_dir: Path | None = None,
) -> CheckContext:
    resolved_epic_dir = epic_dir or repo_root / ".woof" / "epics" / f"E{epic_id}"
    resolved_epic_dir.mkdir(parents=True, exist_ok=True)
    return CheckContext(
        epic_id=epic_id,
        story_id=story_id,
        repo_root=repo_root,
        epic_dir=resolved_epic_dir,
        plan=plan or _plan(epic_id=epic_id),
        critique=None,
    )


def _write_marker_config(repo_root: Path) -> None:
    _write(
        repo_root / ".woof" / "test-markers.toml",
        """\
[languages.python]
test_paths = ["tests/"]
marker_regex = '(?<![A-Za-z0-9])O\\d+(?![A-Za-z0-9])'
docstring_keyword = "outcomes:"
comment_prefix = "#"
context_lines = 3
""",
    )


def _write_epic(epic_dir: Path, *, outcomes: list[str], cds: list[str]) -> None:
    front_matter = {
        "epic_id": 1,
        "title": "Stage-5 conformance",
        "observable_outcomes": [
            {
                "id": outcome_id,
                "statement": f"Outcome {outcome_id} is observable.",
                "verification": "automated",
            }
            for outcome_id in outcomes
        ],
        "contract_decisions": [
            {
                "id": cd_id,
                "related_outcomes": ["O1"],
                "title": f"Contract {cd_id}",
                "json_schema_ref": "schemas/contract.schema.json",
            }
            for cd_id in cds
        ],
        "acceptance_criteria": ["Stage-5 check matrix passes."],
    }
    _write(epic_dir / "EPIC.md", "---\n" + yaml.safe_dump(front_matter) + "---\n")


def _write_plan(epic_dir: Path, plan: dict[str, Any]) -> None:
    _write(epic_dir / "plan.json", json.dumps(plan))


def _write_critique(
    epic_dir: Path,
    story_id: str,
    *,
    severity: str,
    findings: list[dict[str, Any]],
) -> None:
    _write(
        epic_dir / "critique" / f"story-{story_id}.md",
        "---\n"
        "target: story\n"
        f"target_id: {story_id}\n"
        f"severity: {severity}\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "harness: test-reviewer\n"
        f"findings: {json.dumps(findings)}\n"
        "---\n"
        "Reviewer critique.\n",
    )


def _write_disposition(
    epic_dir: Path,
    epic_id: int,
    story_id: str,
    *,
    severity: str,
    finding_ids: list[str],
) -> None:
    dispositions = [
        {
            "finding_id": finding_id,
            "decision": "accepted",
            "rationale": "Handled by the staged story artefacts.",
        }
        for finding_id in finding_ids
    ]
    _write(
        epic_dir / "dispositions" / f"story-{story_id}.md",
        "---\n"
        "target: story\n"
        f"target_id: {story_id}\n"
        f"critique_path: .woof/epics/E{epic_id}/critique/story-{story_id}.md\n"
        f"severity: {severity}\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "harness: test-primary\n"
        f"dispositions: {json.dumps(dispositions)}\n"
        "---\n"
        "Primary disposition.\n",
    )


def _write_required_durable(repo_root: Path, plan: dict[str, Any]) -> list[str]:
    epic_dir = repo_root / ".woof" / "epics" / "E1"
    _write_plan(epic_dir, plan)
    _write(epic_dir / "epic.jsonl", "{}\n")
    _write(epic_dir / "dispatch.jsonl", "{}\n")
    _write_critique(epic_dir, "S1", severity="info", findings=[])
    _write_disposition(epic_dir, 1, "S1", severity="info", finding_ids=[])
    return [
        ".woof/epics/E1/plan.json",
        ".woof/epics/E1/epic.jsonl",
        ".woof/epics/E1/dispatch.jsonl",
        ".woof/epics/E1/critique/story-S1.md",
        ".woof/epics/E1/dispositions/story-S1.md",
    ]


def _copy_contract_fixture(repo_root: Path) -> Path:
    shutil.copytree(E146_FIXTURE, repo_root, dirs_exist_ok=True)
    epic = repo_root / "EPIC.md"
    text = epic.read_text()
    text = text.replace(ORIG_OPENAPI, "spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch")
    text = text.replace(ORIG_PYDANTIC, "webapp/comment_schema.py:CommentEdit")
    text = text.replace(ORIG_JSON_SCHEMA, "schemas/audit-event.schema.json")
    epic.write_text(text)
    return repo_root


def _build_quality_gates_success(repo_root: Path) -> CheckContext:
    _write_quality_gates(repo_root, _python_command("print('ok')"))
    return _ctx(repo_root)


def _build_quality_gates_failure(repo_root: Path) -> CheckContext:
    _write_quality_gates(repo_root, _python_command("import sys; print('fail'); sys.exit(7)"))
    return _ctx(repo_root)


def _build_outcome_markers_success(repo_root: Path) -> CheckContext:
    _init_repo(repo_root)
    _write_marker_config(repo_root)
    _write(
        repo_root / "tests" / "test_story.py",
        "def test_story_realises_O1():\n    assert True\n",
    )
    _stage(repo_root, "tests/test_story.py")
    return _ctx(repo_root, plan=_plan(_story(paths=["tests/"], satisfies=["O1"])))


def _build_outcome_markers_failure(repo_root: Path) -> CheckContext:
    _init_repo(repo_root)
    _write_marker_config(repo_root)
    _write(
        repo_root / "tests" / "test_story.py",
        "def test_story_mentions_O2_only():\n    assert True\n",
    )
    _stage(repo_root, "tests/test_story.py")
    return _ctx(repo_root, plan=_plan(_story(paths=["tests/"], satisfies=["O1"])))


def _build_scope_success(repo_root: Path) -> CheckContext:
    _init_repo(repo_root)
    plan = _plan(_story(paths=["src/"]))
    required = _write_required_durable(repo_root, plan)
    _write(repo_root / "src" / "app.py", "print('O1')\n")
    _stage(repo_root, "src/app.py", *required)
    return _ctx(repo_root, plan=plan)


def _build_scope_failure(repo_root: Path) -> CheckContext:
    _init_repo(repo_root)
    _write(repo_root / "src" / "app.py")
    _write(repo_root / "docs" / "notes.md")
    _stage(repo_root, "src/app.py", "docs/notes.md")
    return _ctx(repo_root, plan=_plan(_story(paths=["src/"])))


def _build_contract_refs_success(repo_root: Path) -> CheckContext:
    epic_dir = _copy_contract_fixture(repo_root)
    plan = _plan(
        _story(paths=["spec/openapi.yaml"], implements_contract_decisions=["CD1"]),
        epic_id=146,
    )
    return _ctx(repo_root, epic_id=146, plan=plan, epic_dir=epic_dir)


def _build_contract_refs_failure(repo_root: Path) -> CheckContext:
    epic_dir = _copy_contract_fixture(repo_root)
    epic = epic_dir / "EPIC.md"
    epic.write_text(
        epic.read_text().replace(
            "spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch",
            "spec/openapi.yaml#/paths/~1missing/post",
        )
    )
    plan = _plan(
        _story(paths=["spec/openapi.yaml"], implements_contract_decisions=["CD1"]),
        epic_id=146,
    )
    return _ctx(repo_root, epic_id=146, plan=plan, epic_dir=epic_dir)


def _build_plan_crossrefs_success(repo_root: Path) -> CheckContext:
    epic_dir = repo_root / ".woof" / "epics" / "E1"
    plan = _plan(_story(implements_contract_decisions=["CD1"]))
    _write_epic(epic_dir, outcomes=["O1"], cds=["CD1"])
    _write_plan(epic_dir, plan)
    return _ctx(repo_root, plan=plan, epic_dir=epic_dir)


def _build_plan_crossrefs_failure(repo_root: Path) -> CheckContext:
    epic_dir = repo_root / ".woof" / "epics" / "E1"
    plan = _plan(_story(satisfies=["O404"], implements_contract_decisions=[]))
    _write_epic(epic_dir, outcomes=["O1"], cds=["CD1"])
    _write_plan(epic_dir, plan)
    return _ctx(repo_root, plan=plan, epic_dir=epic_dir)


def _build_critique_success(repo_root: Path) -> CheckContext:
    epic_dir = repo_root / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir,
        "S1",
        severity="minor",
        findings=[{"id": "F1", "severity": "minor", "summary": "Disposition required"}],
    )
    _write_disposition(epic_dir, 1, "S1", severity="minor", finding_ids=["F1"])
    return _ctx(repo_root, epic_dir=epic_dir)


def _build_critique_failure(repo_root: Path) -> CheckContext:
    epic_dir = repo_root / ".woof" / "epics" / "E1"
    _write_critique(
        epic_dir,
        "S1",
        severity="blocker",
        findings=[{"id": "F1", "severity": "blocker", "summary": "Stop for review"}],
    )
    return _ctx(repo_root, epic_dir=epic_dir)


def _build_transaction_success(repo_root: Path) -> CheckContext:
    _init_repo(repo_root)
    plan = _plan(_story(paths=["src/*.py"]))
    required = _write_required_durable(repo_root, plan)
    _write(repo_root / "src" / "app.py", "print('O1')\n")
    _stage(repo_root, "src/app.py", *required)
    return _ctx(repo_root, plan=plan)


def _build_transaction_failure(repo_root: Path) -> CheckContext:
    _init_repo(repo_root)
    plan = _plan(_story(paths=["src/*.py"]))
    required = [
        path
        for path in _write_required_durable(repo_root, plan)
        if path != ".woof/epics/E1/dispatch.jsonl"
    ]
    _write(repo_root / "src" / "app.py", "print('O1')\n")
    _stage(repo_root, "src/app.py", *required)
    return _ctx(repo_root, plan=plan)


def _write_docs_paths(repo_root: Path) -> None:
    _write(
        repo_root / ".woof" / "docs-paths.toml",
        """\
[[mappings]]
code_pattern = "src/**/*.py"
doc_pattern = "docs/**/*.md"
rationale = "public behaviour changed"
""",
    )


def _build_docs_drift_success(repo_root: Path) -> CheckContext:
    _init_repo(repo_root)
    _write_docs_paths(repo_root)
    _write(repo_root / "src" / "package" / "service.py")
    _write(repo_root / "docs" / "package" / "service.md")
    _stage(
        repo_root,
        ".woof/docs-paths.toml",
        "src/package/service.py",
        "docs/package/service.md",
    )
    return _ctx(repo_root)


def _build_docs_drift_failure(repo_root: Path) -> CheckContext:
    _init_repo(repo_root)
    _write_docs_paths(repo_root)
    _write(repo_root / "src" / "package" / "service.py")
    _stage(repo_root, ".woof/docs-paths.toml", "src/package/service.py")
    return _ctx(repo_root)


def _write_agents(repo_root: Path) -> None:
    _write(
        repo_root / ".woof" / "agents.toml",
        "[roles]\n\n[review_valve]\nevery_n_stories = 2\nend_of_epic = false\n",
    )


def _review_valve_plan() -> dict[str, Any]:
    return _plan(
        _story(id="S1", status="done"),
        _story(id="S2", status="in_progress"),
    )


def _build_review_valve_success(repo_root: Path) -> CheckContext:
    _write_agents(repo_root)
    plan = _review_valve_plan()
    ctx = _ctx(repo_root, story_id="S2", plan=plan)
    _write_critique(ctx.epic_dir, "S2", severity="info", findings=[])
    return ctx


def _build_review_valve_failure(repo_root: Path) -> CheckContext:
    _write_agents(repo_root)
    plan = _review_valve_plan()
    ctx = _ctx(repo_root, story_id="S2", plan=plan)
    _write_critique(
        ctx.epic_dir,
        "S2",
        severity="minor",
        findings=[{"id": "F1", "severity": "minor", "summary": "Review cumulative risk"}],
    )
    return ctx


STAGE5_CONFORMANCE_FIXTURES = [
    Stage5ConformanceFixture(
        case_id="check_1_success",
        check_id="check_1_quality_gates",
        kind="success",
        contract="blocking quality-gate commands exit 0",
        build=_build_quality_gates_success,
        expected_ok=True,
        expected_severity="info",
        summary_contains="quality gate command(s) passed",
    ),
    Stage5ConformanceFixture(
        case_id="check_1_failure",
        check_id="check_1_quality_gates",
        kind="failure",
        contract="blocking quality-gate command exits non-zero",
        build=_build_quality_gates_failure,
        expected_ok=False,
        expected_severity="blocker",
        summary_contains="quality gate command(s) failed",
        evidence_contains="gate 'unit' exited 7",
    ),
    Stage5ConformanceFixture(
        case_id="check_2_success",
        check_id="check_2_outcome_markers",
        kind="success",
        contract="each story satisfies[] outcome is marked in the staged test diff",
        build=_build_outcome_markers_success,
        expected_ok=True,
        expected_severity="info",
        summary_contains="outcome marker(s) present",
        evidence_contains="O1: tests/test_story.py",
    ),
    Stage5ConformanceFixture(
        case_id="check_2_failure",
        check_id="check_2_outcome_markers",
        kind="failure",
        contract="a required outcome marker is absent from staged test files",
        build=_build_outcome_markers_failure,
        expected_ok=False,
        expected_severity="blocker",
        summary_contains="missing staged test markers",
        evidence_contains="present markers: none",
        path_contains="tests/test_story.py",
    ),
    Stage5ConformanceFixture(
        case_id="check_3_success",
        check_id="check_3_scope",
        kind="success",
        contract="staged story paths and durable epic artefacts are within scope",
        build=_build_scope_success,
        expected_ok=True,
        expected_severity=None,
        summary_contains="within story S1 scope",
        path_contains=".woof/epics/E1/critique/story-S1.md",
    ),
    Stage5ConformanceFixture(
        case_id="check_3_failure",
        check_id="check_3_scope",
        kind="failure",
        contract="a staged story path outside story.paths[] blocks the story",
        build=_build_scope_failure,
        expected_ok=False,
        expected_severity="blocker",
        summary_contains="outside story S1 scope",
        path_contains="docs/notes.md",
    ),
    Stage5ConformanceFixture(
        case_id="check_4_success",
        check_id="check_4_contract_refs",
        kind="success",
        contract="owned contract references resolve through native artefact validation",
        build=_build_contract_refs_success,
        expected_ok=True,
        expected_severity="info",
        summary_contains="owned contract reference(s) verified",
        evidence_contains="CD1 (openapi_ref)",
    ),
    Stage5ConformanceFixture(
        case_id="check_4_failure",
        check_id="check_4_contract_refs",
        kind="failure",
        contract="a broken owned contract reference blocks the story",
        build=_build_contract_refs_failure,
        expected_ok=False,
        expected_severity="blocker",
        summary_contains="owned contract reference(s) failed validation",
        evidence_contains="did not resolve",
        path_contains="spec/openapi.yaml",
    ),
    Stage5ConformanceFixture(
        case_id="check_5_success",
        check_id="check_5_plan_crossrefs",
        kind="success",
        contract="plan schema, outcome refs, CD ownership, dependencies, and status are coherent",
        build=_build_plan_crossrefs_success,
        expected_ok=True,
        expected_severity="info",
        summary_contains="plan schema and cross-reference invariants valid",
    ),
    Stage5ConformanceFixture(
        case_id="check_5_failure",
        check_id="check_5_plan_crossrefs",
        kind="failure",
        contract="plan cross-reference drift blocks the story",
        build=_build_plan_crossrefs_failure,
        expected_ok=False,
        expected_severity="blocker",
        summary_contains="plan cross-reference validation failed",
        evidence_contains="S1: satisfies unknown outcome O404",
    ),
    Stage5ConformanceFixture(
        case_id="check_6_success",
        check_id="check_6_critique_blocker",
        kind="success",
        contract="non-blocking critiques require a covering primary disposition",
        build=_build_critique_success,
        expected_ok=True,
        expected_severity="minor",
        summary_contains="primary disposition recorded",
    ),
    Stage5ConformanceFixture(
        case_id="check_6_failure",
        check_id="check_6_critique_blocker",
        kind="failure",
        contract="reviewer blocker critiques halt Stage 5",
        build=_build_critique_failure,
        expected_ok=False,
        expected_severity="blocker",
        summary_contains="critique severity is blocker",
        evidence_contains="F1: Stop for review",
    ),
    Stage5ConformanceFixture(
        case_id="check_7_success",
        check_id="check_7_commit_transaction",
        kind="success",
        contract="story paths plus required durable .woof artefacts are staged and clean",
        build=_build_transaction_success,
        expected_ok=True,
        expected_severity="info",
        summary_contains="required durable artefacts are commit-ready",
        path_contains=".woof/epics/E1/dispatch.jsonl",
    ),
    Stage5ConformanceFixture(
        case_id="check_7_failure",
        check_id="check_7_commit_transaction",
        kind="failure",
        contract="missing required durable transaction files block commit readiness",
        build=_build_transaction_failure,
        expected_ok=False,
        expected_severity="blocker",
        summary_contains="commit transaction is not ready",
        evidence_contains="missing required staged paths",
        path_contains=".woof/epics/E1/dispatch.jsonl",
    ),
    Stage5ConformanceFixture(
        case_id="check_8_success",
        check_id="check_8_docs_drift",
        kind="success",
        contract="mapped code changes are accompanied by mapped docs changes",
        build=_build_docs_drift_success,
        expected_ok=True,
        expected_severity="info",
        summary_contains="mapped code path(s) accompanied",
        path_contains="docs/package/service.md",
    ),
    Stage5ConformanceFixture(
        case_id="check_8_failure",
        check_id="check_8_docs_drift",
        kind="failure",
        contract="mapped code changes without matching docs block the story",
        build=_build_docs_drift_failure,
        expected_ok=False,
        expected_severity="blocker",
        summary_contains="docs drift detected",
        evidence_contains="requires staged doc path",
        path_contains="src/package/service.py",
    ),
    Stage5ConformanceFixture(
        case_id="check_9_success",
        check_id="check_9_review_valve",
        kind="success",
        contract="due review-valve boundaries pass when no minor critique findings accumulated",
        build=_build_review_valve_success,
        expected_ok=True,
        expected_severity="info",
        summary_contains="no minor critique findings accumulated",
    ),
    Stage5ConformanceFixture(
        case_id="check_9_failure",
        check_id="check_9_review_valve",
        kind="failure",
        contract="due review-valve boundaries surface accumulated minor critique findings",
        build=_build_review_valve_failure,
        expected_ok=False,
        expected_severity="minor",
        summary_contains="minor finding(s) require review",
        evidence_contains="S2/F1: Review cumulative risk",
        path_contains=".woof/epics/E1/critique/story-S2.md",
    ),
]


def test_stage_5_conformance_matrix_has_success_and_failure_for_each_check() -> None:
    kinds_by_check: dict[str, set[str]] = defaultdict(set)
    for fixture in STAGE5_CONFORMANCE_FIXTURES:
        kinds_by_check[fixture.check_id].add(fixture.kind)

    assert set(kinds_by_check) == set(STAGE_5_CHECK_IDS)
    assert all(kinds == {"success", "failure"} for kinds in kinds_by_check.values())


@pytest.mark.parametrize(
    "fixture",
    STAGE5_CONFORMANCE_FIXTURES,
    ids=lambda fixture: fixture.case_id,
)
def test_stage_5_check_runner_conformance_matrix(
    tmp_path: Path, fixture: Stage5ConformanceFixture
) -> None:
    ctx = fixture.build(tmp_path / fixture.case_id)

    outcome = REGISTRY[fixture.check_id].runner(ctx)

    assert outcome.id == fixture.check_id
    assert outcome.ok is fixture.expected_ok, fixture.contract
    assert outcome.severity == fixture.expected_severity, fixture.contract
    assert fixture.summary_contains in outcome.summary
    if fixture.evidence_contains is not None:
        assert fixture.evidence_contains in (outcome.evidence or "")
    if fixture.path_contains is not None:
        assert fixture.path_contains in outcome.paths
