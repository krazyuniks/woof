"""Tests for executor-result.schema.json — CD2.

Verifies the schema compiles and that valid/invalid instances validate
as expected.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "executor-result.schema.json"

pytestmark = pytest.mark.host_only


def _validate(payload: dict) -> tuple[bool, str]:
    if not shutil.which("ajv"):
        pytest.skip("ajv not on PATH")
    data = json.dumps(payload).encode()
    import tempfile

    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as fh:
        fh.write(data)
        data_path = fh.name
    try:
        proc = subprocess.run(
            [
                "ajv",
                "validate",
                "--spec=draft2020",
                "-c",
                "ajv-formats",
                "-s",
                str(SCHEMA_PATH),
                "-d",
                data_path,
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(data_path).unlink(missing_ok=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def test_staged_for_verification_valid() -> None:
    """staged_for_verification with commit_body validates OK."""
    ok, msg = _validate(
        {
            "epic_id": 182,
            "story_id": "S1",
            "outcome": "staged_for_verification",
            "commit_body": "Bootstrap: registry + check_6 + driver protocol",
            "position": None,
        }
    )
    assert ok, msg


def test_aborted_with_position_valid() -> None:
    """aborted_with_position with position validates OK."""
    ok, msg = _validate(
        {
            "epic_id": 181,
            "story_id": "S2",
            "outcome": "aborted_with_position",
            "commit_body": None,
            "position": "Critique returned blocker; halting.",
        }
    )
    assert ok, msg


def test_empty_diff_valid() -> None:
    """empty_diff with position validates OK."""
    ok, msg = _validate(
        {
            "epic_id": 182,
            "story_id": "S3",
            "outcome": "empty_diff",
            "commit_body": None,
            "position": "No changes — outcome already realised by S1.",
        }
    )
    assert ok, msg


def test_staged_for_verification_missing_commit_body_invalid() -> None:
    """staged_for_verification without commit_body fails allOf rule."""
    ok, _ = _validate(
        {
            "epic_id": 182,
            "story_id": "S1",
            "outcome": "staged_for_verification",
        }
    )
    assert not ok, "Expected validation failure but got OK"


def test_unknown_outcome_invalid() -> None:
    """An unrecognised outcome value fails the enum constraint."""
    ok, _ = _validate(
        {
            "epic_id": 182,
            "story_id": "S1",
            "outcome": "committed",
            "commit_body": None,
        }
    )
    assert not ok, "Expected validation failure but got OK"


def test_schema_compiles() -> None:
    """executor-result.schema.json itself is a valid JSON Schema."""
    if not shutil.which("ajv"):
        pytest.skip("ajv not on PATH")
    proc = subprocess.run(
        [
            "ajv",
            "compile",
            "--spec=draft2020",
            "-c",
            "ajv-formats",
            "-s",
            str(SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (proc.stdout + proc.stderr).strip()
