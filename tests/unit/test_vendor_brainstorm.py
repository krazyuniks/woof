"""Drift guard for the vendored brainstorm skill playbook.

CI-safe: verifies the vendored files in playbooks/brainstorm/ match the hashes
recorded in VENDOR.json and still carry the generated marker. Needs no
agent-toolkit checkout. Re-vendoring from source is a developer action
(`just vendor-brainstorm`); this test only catches a vendored file that drifted
from its recorded pin (a hand-edit).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "vendor_brainstorm", REPO_ROOT / "scripts" / "vendor_brainstorm.py"
)
assert _spec and _spec.loader
vendor_brainstorm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vendor_brainstorm)


def test_vendored_brainstorm_playbook_is_intact() -> None:
    errors = vendor_brainstorm.check()
    assert errors == [], "vendored brainstorm playbook drift:\n" + "\n".join(errors)


def test_vendor_manifest_records_a_source_pin() -> None:
    manifest = vendor_brainstorm.MANIFEST
    assert manifest.is_file(), "playbooks/brainstorm/VENDOR.json missing"
    import json

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["source"]["repo"] == "agent-toolkit"
    assert data["source"]["commit"], "VENDOR.json must record a source commit pin"
