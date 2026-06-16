"""Shared JSON Schema validation via ajv-cli.

Single source of validation strictness: both ``woof validate`` and the
check runners use ``validate_against_schema`` so runtime behaviour equals
the schema by construction.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from woof.paths import schema_dir


def run_ajv(schema_path: Path, data_json: bytes) -> tuple[bool, str]:
    """Run ajv-cli; return (ok, combined-output)."""
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as fh:
        fh.write(data_json)
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
                str(schema_path),
                "-d",
                data_path,
            ],
            capture_output=True,
            text=True,
        )
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, output
    except FileNotFoundError:
        return (
            False,
            "ajv not found on PATH — install ajv-cli and ajv-formats (e.g. `volta install ajv-cli ajv-formats`)",
        )
    finally:
        Path(data_path).unlink(missing_ok=True)


def validate_against_schema(payload: object, schema_name: str) -> tuple[bool, str]:
    """Validate ``payload`` against the named woof schema. Returns (ok, error-output).

    Uses the same ajv-cli path as ``woof validate``, so strictness equals the
    schema by construction — no second, looser validator.
    """
    schema_path = schema_dir() / f"{schema_name}.schema.json"
    data_json = json.dumps(payload).encode()
    return run_ajv(schema_path, data_json)
