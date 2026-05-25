"""Operator-facing help and schema wording checks."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_epic_help_uses_tracker_language(run_woof) -> None:
    """Epic IDs are tracker-assigned, not always GitHub issue numbers."""

    for args in (
        ("dispatch", "--help"),
        ("render-epic", "--help"),
        ("check", "stage-5", "--help"),
    ):
        proc = run_woof(*args)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        output = proc.stdout + proc.stderr
        assert "tracker-assigned epic id" in output
        assert "gh issue number" not in output


def test_render_epic_help_uses_tracker_body_language(run_woof) -> None:
    proc = run_woof("render-epic", "--help")

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "managed tracker body" in proc.stdout
    assert "gh issue body" not in proc.stdout


def test_schema_descriptions_match_implemented_operator_workflow() -> None:
    """Schemas should not describe older driver or unimplemented Check 4 behaviour."""

    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((REPO_ROOT / "schemas").glob("*.schema.json"))
    }
    rendered = json.dumps(schemas, sort_keys=True)

    assert "wf-run driver" not in rendered
    assert "just wf-preflight" not in rendered
    assert "schemathesis or equivalent" not in rendered
    assert "gh issue number" not in rendered


def test_definition_playbook_warns_about_yaml_string_quoting() -> None:
    text = (REPO_ROOT / "playbooks" / "discovery" / "definition.md").read_text(encoding="utf-8")

    assert "YAML safety rule" in text
    assert "backtick" in text
    assert "quote every string value" in text
