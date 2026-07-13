"""check_10_work_source_state - Stage-5 Check 10.

Unit-state writeback is engine-exclusive. A produced diff that mutates a work
unit's ``state:`` in the drained work-source document is rejected before publish
(the producer pre-mark defect observed in the wild).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.support import DEFAULT_PROJECT_KEY
from woof import state
from woof.checks import CheckContext
from woof.checks.registry import REGISTRY
from woof.checks.runners.check_10_work_source_state import check_10_work_source_state_runner

KEY = DEFAULT_PROJECT_KEY
SET_ID = "wave-5"

BACKLOG = """\
---
schema_version: 1
type: backlog
project_ref: woof
status: active
work_units:
  - id: alpha
    title: First unit
    kind: build
    state: todo
    priority: high
  - id: beta
    title: Second unit
    kind: build
    state: todo
    priority: high
---

# Wave 5

Prose the operator wrote.
"""


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True)


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    document = repo_root / "docs" / "backlog.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(BACKLOG, encoding="utf-8")
    (repo_root / "src").mkdir(exist_ok=True)
    (repo_root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo_root, "add", "--", "docs/backlog.md", "src/app.py")
    _git(repo_root, "commit", "-m", "seed")
    return document


def _seed_intake(document: Path) -> None:
    directory = state.work_unit_set_dir(KEY, SET_ID)
    directory.mkdir(parents=True, exist_ok=True)
    state.atomic_write_json(
        directory / "intake.json",
        {
            "schema_version": 1,
            "kind": "pre_decomposed_work_units",
            "source": {"path": document.as_posix(), "source_ref": document.as_posix()},
        },
    )


def _plan(context: dict | None, *, alpha_state: str = "in_progress") -> dict:
    return {
        "epic_id": 7,
        "context": context,
        "goal": "Drain wave 5.",
        "work_units": [
            {
                "id": "alpha",
                "title": "First unit",
                "summary": "First unit.",
                "paths": ["**/*"],
                "state": alpha_state,
            },
            {
                "id": "beta",
                "title": "Second unit",
                "summary": "Second unit.",
                "paths": ["**/*"],
                "state": "pending",
            },
        ],
    }


def _ctx(repo_root: Path, plan: dict) -> CheckContext:
    return CheckContext(
        epic_id=7,
        work_unit_id="alpha",
        project_key=KEY,
        repo_root=repo_root,
        epic_dir=state.epic_dir(KEY, 7),
        plan=plan,
    )


def _set_context() -> dict:
    return {"kind": "work_unit_set", "project_ref": KEY, "set_id": SET_ID}


def test_check_10_is_registered() -> None:
    assert REGISTRY["check_10_work_source_state"].stage == 5


def test_a_produced_diff_that_marks_a_unit_done_is_rejected(tmp_path: Path) -> None:
    """The producer pre-mark defect: the producer flips its own unit to done."""

    repo_root = tmp_path / "repo"
    document = _init_repo(repo_root)
    _seed_intake(document)
    document.write_text(
        BACKLOG.replace("    state: todo\n", "    state: done\n", 1), encoding="utf-8"
    )
    _git(repo_root, "add", "--", "docs/backlog.md")

    outcome = check_10_work_source_state_runner(_ctx(repo_root, _plan(_set_context())))

    assert outcome.ok is False
    assert outcome.severity == "blocker"
    assert "work-unit state" in outcome.summary
    assert "alpha" in (outcome.evidence or "")
    assert outcome.paths == ["docs/backlog.md"]


def test_a_produced_diff_that_marks_a_sibling_unit_is_rejected(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    document = _init_repo(repo_root)
    _seed_intake(document)
    document.write_text(BACKLOG.replace("state: todo", "state: cancelled"), encoding="utf-8")
    _git(repo_root, "add", "--", "docs/backlog.md")

    outcome = check_10_work_source_state_runner(_ctx(repo_root, _plan(_set_context())))

    assert outcome.ok is False
    assert "beta" in (outcome.evidence or "")


def test_a_produced_diff_that_edits_the_documents_prose_is_allowed(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    document = _init_repo(repo_root)
    _seed_intake(document)
    document.write_text(
        BACKLOG.replace("Prose the operator wrote.", "Prose the producer improved."),
        encoding="utf-8",
    )
    _git(repo_root, "add", "--", "docs/backlog.md")

    outcome = check_10_work_source_state_runner(_ctx(repo_root, _plan(_set_context())))

    assert outcome.ok is True
    assert outcome.severity == "info"


def test_the_engines_own_writeback_is_not_a_producer_mutation(tmp_path: Path) -> None:
    """A staged state that matches the engine plan is the engine's own flip."""

    repo_root = tmp_path / "repo"
    document = _init_repo(repo_root)
    _seed_intake(document)
    document.write_text(
        BACKLOG.replace("    state: todo\n", "    state: in_progress\n", 1), encoding="utf-8"
    )
    _git(repo_root, "add", "--", "docs/backlog.md")

    outcome = check_10_work_source_state_runner(_ctx(repo_root, _plan(_set_context())))

    assert outcome.ok is True


def test_an_untouched_work_source_document_passes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    document = _init_repo(repo_root)
    _seed_intake(document)
    (repo_root / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo_root, "add", "--", "src/app.py")

    outcome = check_10_work_source_state_runner(_ctx(repo_root, _plan(_set_context())))

    assert outcome.ok is True
    assert "not in the produced diff" in outcome.summary


def test_a_run_with_no_work_source_document_passes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    document = _init_repo(repo_root)
    document.write_text(
        BACKLOG.replace("    state: todo\n", "    state: done\n", 1), encoding="utf-8"
    )
    _git(repo_root, "add", "--", "docs/backlog.md")

    epic_context = {"kind": "epic", "project_ref": KEY, "epic_id": 7}
    outcome = check_10_work_source_state_runner(_ctx(repo_root, _plan(epic_context)))

    assert outcome.ok is True
    assert "no work-source document" in outcome.summary


def test_a_work_source_document_outside_the_delivery_checkout_passes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _init_repo(repo_root)
    outside = tmp_path / "pm" / "backlog.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(BACKLOG, encoding="utf-8")
    _seed_intake(outside)

    outcome = check_10_work_source_state_runner(_ctx(repo_root, _plan(_set_context())))

    assert outcome.ok is True
    assert "outside the delivery checkout" in outcome.summary
