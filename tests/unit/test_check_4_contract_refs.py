"""Tests for Stage-5 Check 4 contract reference validation."""

from __future__ import annotations

import shutil
from pathlib import Path

from woof.checks import CheckContext
from woof.checks.runners.check_4_contract_refs import check_4_contract_refs_runner

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "woof" / "e146"

ORIG_OPENAPI = "tests/fixtures/woof/e146/spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch"
ORIG_PYDANTIC = "tests/fixtures/woof/e146/webapp/comment_schema.py:CommentEdit"
ORIG_JSON_SCHEMA = "tests/fixtures/woof/e146/schemas/audit-event.schema.json"


def _ctx(
    *,
    epic_dir: Path,
    repo_root: Path,
    owned: list[str],
    story_id: str = "S1",
) -> CheckContext:
    return CheckContext(
        epic_id=146,
        story_id=story_id,
        repo_root=repo_root,
        epic_dir=epic_dir,
        plan={
            "epic_id": 146,
            "goal": "contract refs",
            "stories": [
                {
                    "id": story_id,
                    "title": "contracts",
                    "intent": "verify contract refs",
                    "paths": [],
                    "satisfies": [],
                    "implements_contract_decisions": owned,
                    "uses_contract_decisions": [],
                    "depends_on": [],
                    "tests": {"count": 0, "types": []},
                    "status": "in_progress",
                }
            ],
        },
    )


def _fixture_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "e146"
    shutil.copytree(FIXTURE_DIR, dest)
    epic = dest / "EPIC.md"
    text = epic.read_text()
    text = text.replace(ORIG_OPENAPI, "spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch")
    text = text.replace(ORIG_PYDANTIC, "webapp/comment_schema.py:CommentEdit")
    text = text.replace(ORIG_JSON_SCHEMA, "schemas/audit-event.schema.json")
    epic.write_text(text)
    return dest


def test_openapi_contract_ref_verifies_for_owned_cd() -> None:
    outcome = check_4_contract_refs_runner(
        _ctx(epic_dir=FIXTURE_DIR, repo_root=REPO_ROOT, owned=["CD1"])
    )

    assert outcome.ok is True
    assert outcome.summary == "all 1 owned contract reference(s) verified"
    assert "CD1 (openapi_ref)" in (outcome.evidence or "")


def test_pydantic_contract_ref_verifies_for_owned_cd() -> None:
    outcome = check_4_contract_refs_runner(
        _ctx(epic_dir=FIXTURE_DIR, repo_root=REPO_ROOT, owned=["CD2"])
    )

    assert outcome.ok is True
    assert "CD2 (pydantic_ref)" in (outcome.evidence or "")


def test_json_schema_contract_ref_verifies_for_owned_cd() -> None:
    outcome = check_4_contract_refs_runner(
        _ctx(epic_dir=FIXTURE_DIR, repo_root=REPO_ROOT, owned=["CD3"])
    )

    assert outcome.ok is True
    assert "CD3 (json_schema_ref)" in (outcome.evidence or "")


def test_unowned_contract_refs_do_not_block_current_story(tmp_path: Path) -> None:
    epic_dir = _fixture_copy(tmp_path)
    epic = epic_dir / "EPIC.md"
    epic.write_text(
        epic.read_text().replace(
            "spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch",
            "spec/openapi.yaml#/paths/~1missing/post",
        )
    )

    outcome = check_4_contract_refs_runner(
        _ctx(epic_dir=epic_dir, repo_root=epic_dir, owned=["CD2"])
    )

    assert outcome.ok is True
    assert "CD2 (pydantic_ref)" in (outcome.evidence or "")
    assert "CD1" not in (outcome.evidence or "")


def test_broken_owned_contract_ref_blocks_story(tmp_path: Path) -> None:
    epic_dir = _fixture_copy(tmp_path)
    epic = epic_dir / "EPIC.md"
    epic.write_text(
        epic.read_text().replace(
            "spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch",
            "spec/openapi.yaml#/paths/~1missing/post",
        )
    )

    outcome = check_4_contract_refs_runner(
        _ctx(epic_dir=epic_dir, repo_root=epic_dir, owned=["CD1"])
    )

    assert outcome.ok is False
    assert outcome.severity == "blocker"
    assert "CD1 (openapi_ref)" in (outcome.evidence or "")
    assert "did not resolve" in (outcome.evidence or "")


def test_unknown_owned_contract_ref_blocks_story() -> None:
    outcome = check_4_contract_refs_runner(
        _ctx(epic_dir=FIXTURE_DIR, repo_root=REPO_ROOT, owned=["CD404"])
    )

    assert outcome.ok is False
    assert "CD404 (missing)" in (outcome.evidence or "")
