"""Drift guard for the generated `woof-brainstorm` skill.

CI-safe: verifies that ``skills/woof-brainstorm/`` matches the hashes recorded in
its ``VENDOR.json`` and still carries the generated and vendored markers. Needs no
agent-toolkit checkout. Regenerating from source is a developer action
(`just gen-brainstorm`); this test only catches a generated file that drifted from
its recorded pin (a hand-edit).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "gen_woof_brainstorm", REPO_ROOT / "scripts" / "gen_woof_brainstorm.py"
)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def test_generated_skill_is_intact() -> None:
    errors = gen.check()
    assert errors == [], "woof-brainstorm skill drift:\n" + "\n".join(errors)


def test_manifest_records_a_source_pin() -> None:
    assert gen.MANIFEST.is_file(), "skills/woof-brainstorm/VENDOR.json missing"
    data = json.loads(gen.MANIFEST.read_text(encoding="utf-8"))
    assert data["source"]["repo"] == "agent-toolkit"
    assert data["source"]["commit"], "VENDOR.json must record a source commit pin"
    assert data["skill_body_sha256"], "VENDOR.json must record the vendored body hash"


def test_skill_composes_wrapper_and_vendored_body() -> None:
    text = gen.SKILL_MD.read_text(encoding="utf-8")
    # woof-owned wrapper.
    assert "name: woof-brainstorm" in text
    assert "Wrapper: how this runs inside Woof" in text
    assert "woof wf reset --epic" in text  # the Start-fresh redo path
    assert "discovery/brainstorm/" in text
    assert "woof wf --epic" in text  # the handoff
    # vendored canonical body, between the markers, reflecting the post-B11 source.
    body = gen._extract_embedded_body(text)
    assert body is not None
    assert "Loop 1 - Brainstorm" in body
    assert "Loop 2 - Grill Me" in body
    assert "## Modes" not in body  # collapsed into tiers (B11)
