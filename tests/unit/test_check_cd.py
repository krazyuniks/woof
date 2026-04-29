"""Black-box tests for ``woof check-cd``.

The E146 fixture (``tests/fixtures/woof/e146/``) is the load-bearing regression:
each contract_decision in the fixture EPIC.md must verify, and synthetic
mutations of any reference must fail loudly. This is the "first dogfood test"
called out in Workflow.md §3 — without it, contract drift can re-enter the
planner with no mechanical alarm.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "woof" / "e146"
FIXTURE_EPIC = FIXTURE_DIR / "EPIC.md"


pytestmark = pytest.mark.host_only


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), "check-cd", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


# ---------------------------------------------------------------------------
# Happy path — the canonical fixture verifies clean
# ---------------------------------------------------------------------------


def test_e146_fixture_all_three_cds_verify() -> None:
    proc = _run(str(FIXTURE_EPIC))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "3 contract decision(s)" in out
    assert "3/3 verified" in out
    # Each CD line shows OK
    assert "OK   CD1    (openapi_ref)" in out
    assert "OK   CD2    (pydantic_ref)" in out
    assert "OK   CD3    (json_schema_ref)" in out


def test_e146_fixture_json_format() -> None:
    proc = _run(str(FIXTURE_EPIC), "--format", "json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["total"] == 3
    assert payload["verified"] == 3
    by_id = {f["id"]: f for f in payload["findings"]}
    assert by_id["CD1"]["kind"] == "openapi_ref"
    assert by_id["CD1"]["ok"] is True
    assert by_id["CD2"]["kind"] == "pydantic_ref"
    assert by_id["CD2"]["ok"] is True
    assert by_id["CD3"]["kind"] == "json_schema_ref"
    assert by_id["CD3"]["ok"] is True


# ---------------------------------------------------------------------------
# Mutation tests — break each ref type, assert fail-loud
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_copy(tmp_path: Path) -> Path:
    """Copy of the E146 fixture in a tmp dir, safe to mutate."""
    dest = tmp_path / "e146"
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


def _set_epic_field(epic_md: Path, search: str, replacement: str) -> None:
    text = epic_md.read_text()
    assert search in text, f"sentinel not found in {epic_md}: {search!r}"
    epic_md.write_text(text.replace(search, replacement))


# Refs in the copied EPIC are rewritten to be relative to the fixture dir
# (woof check-cd resolves CD refs against ``epic_md.parent`` when no .git is
# found above it).
ORIG_OPENAPI = "tests/fixtures/woof/e146/spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch"
ORIG_PYDANTIC = "tests/fixtures/woof/e146/webapp/comment_schema.py:CommentEdit"
ORIG_JSON_SCHEMA = "tests/fixtures/woof/e146/schemas/audit-event.schema.json"


def _localise_refs(epic_md: Path) -> None:
    """Rewrite the canonical refs in the copied EPIC.md to fixture-dir-relative."""
    text = epic_md.read_text()
    text = text.replace(ORIG_OPENAPI, "spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch")
    text = text.replace(ORIG_PYDANTIC, "webapp/comment_schema.py:CommentEdit")
    text = text.replace(ORIG_JSON_SCHEMA, "schemas/audit-event.schema.json")
    epic_md.write_text(text)


def test_broken_openapi_ref_pointer_fails(fixture_copy: Path) -> None:
    """JSON pointer that doesn't resolve in the OpenAPI doc must fail."""
    epic = fixture_copy / "EPIC.md"
    _localise_refs(epic)
    _set_epic_field(
        epic,
        "spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch",
        "spec/openapi.yaml#/paths/~1nonexistent/post",
    )
    proc = subprocess.run(
        [str(WOOF_BIN), "check-cd", "--format", "json", str(epic)],
        capture_output=True,
        text=True,
        cwd=fixture_copy,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    cd1 = next(f for f in payload["findings"] if f["id"] == "CD1")
    assert cd1["ok"] is False
    assert "did not resolve" in cd1["detail"]


def test_broken_openapi_ref_file_missing_fails(fixture_copy: Path) -> None:
    """OpenAPI document that doesn't exist on disk must fail."""
    epic = fixture_copy / "EPIC.md"
    _localise_refs(epic)
    _set_epic_field(epic, "spec/openapi.yaml", "spec/missing.yaml")
    proc = subprocess.run(
        [str(WOOF_BIN), "check-cd", "--format", "json", str(epic)],
        capture_output=True,
        text=True,
        cwd=fixture_copy,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    cd1 = next(f for f in payload["findings"] if f["id"] == "CD1")
    assert cd1["ok"] is False
    assert "not found" in cd1["detail"]


def test_broken_pydantic_ref_class_missing_fails(fixture_copy: Path) -> None:
    epic = fixture_copy / "EPIC.md"
    _localise_refs(epic)
    _set_epic_field(
        epic, "webapp/comment_schema.py:CommentEdit", "webapp/comment_schema.py:CommentDelete"
    )
    proc = subprocess.run(
        [str(WOOF_BIN), "check-cd", "--format", "json", str(epic)],
        capture_output=True,
        text=True,
        cwd=fixture_copy,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    cd2 = next(f for f in payload["findings"] if f["id"] == "CD2")
    assert cd2["ok"] is False
    assert "not found" in cd2["detail"].lower()


def test_broken_pydantic_ref_not_basemodel_fails(fixture_copy: Path) -> None:
    """Reference to a class that exists but isn't a BaseModel must fail."""
    extra = fixture_copy / "webapp" / "not_a_model.py"
    extra.write_text("class JustAClass:\n    x = 1\n")
    epic = fixture_copy / "EPIC.md"
    _localise_refs(epic)
    _set_epic_field(
        epic, "webapp/comment_schema.py:CommentEdit", "webapp/not_a_model.py:JustAClass"
    )
    proc = subprocess.run(
        [str(WOOF_BIN), "check-cd", "--format", "json", str(epic)],
        capture_output=True,
        text=True,
        cwd=fixture_copy,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    cd2 = next(f for f in payload["findings"] if f["id"] == "CD2")
    assert cd2["ok"] is False
    assert "BaseModel" in cd2["detail"]


def test_broken_json_schema_ref_file_missing_fails(fixture_copy: Path) -> None:
    epic = fixture_copy / "EPIC.md"
    _localise_refs(epic)
    _set_epic_field(epic, "schemas/audit-event.schema.json", "schemas/missing.schema.json")
    proc = subprocess.run(
        [str(WOOF_BIN), "check-cd", "--format", "json", str(epic)],
        capture_output=True,
        text=True,
        cwd=fixture_copy,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    cd3 = next(f for f in payload["findings"] if f["id"] == "CD3")
    assert cd3["ok"] is False
    assert "not found" in cd3["detail"]


def test_broken_json_schema_ref_invalid_schema_fails(fixture_copy: Path) -> None:
    """A file that exists but isn't a valid JSON Schema must fail under ajv compile."""
    bad = fixture_copy / "schemas" / "bad.schema.json"
    bad.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "not-a-real-type",
            }
        )
    )
    epic = fixture_copy / "EPIC.md"
    _localise_refs(epic)
    _set_epic_field(epic, "schemas/audit-event.schema.json", "schemas/bad.schema.json")
    proc = subprocess.run(
        [str(WOOF_BIN), "check-cd", "--format", "json", str(epic)],
        capture_output=True,
        text=True,
        cwd=fixture_copy,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    cd3 = next(f for f in payload["findings"] if f["id"] == "CD3")
    assert cd3["ok"] is False


# ---------------------------------------------------------------------------
# Argument / parse errors
# ---------------------------------------------------------------------------


def test_missing_epic_md() -> None:
    proc = _run("/tmp/does-not-exist.md")
    assert proc.returncode == 2
    assert "not found" in proc.stderr


def test_invalid_front_matter(tmp_path: Path) -> None:
    bad = tmp_path / "EPIC.md"
    # Missing required acceptance_criteria
    bad.write_text(
        "---\n"
        "epic_id: 1\n"
        "title: T\n"
        "observable_outcomes: [{id: O1, statement: x, verification: automated}]\n"
        "contract_decisions: []\n"
        "---\n\nintent\n"
    )
    proc = _run(str(bad))
    assert proc.returncode == 2
    assert "front-matter invalid" in proc.stderr
