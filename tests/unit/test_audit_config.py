"""Unit tests for woof/lib/audit_config.py.

Outcome coverage: O3 — operator can configure audit policy via .woof/agents.toml
without re-deploying the woof CLI.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_CONFIG_PATH = REPO_ROOT / "src" / "woof" / "lib" / "audit_config.py"

pytestmark = pytest.mark.host_only


def _load_module():
    """Load woof/lib/audit_config.py as a module without package import machinery.

    Registers the module in sys.modules before exec_module so that Python 3.14's
    dataclasses annotation resolver can find it (it calls sys.modules[cls.__module__]).
    """
    import sys

    loader = SourceFileLoader("audit_config", str(AUDIT_CONFIG_PATH))
    spec = importlib.util.spec_from_loader("audit_config", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_config"] = mod
    loader.exec_module(mod)
    return mod


def test_defaults_when_no_audit_block() -> None:
    """O3: absent [audit] block yields defaults — enabled, 256 KB cap, no patterns."""
    mod = _load_module()
    cfg = mod.load_audit_config({})
    assert cfg.enabled is True
    assert cfg.max_bytes == mod.DEFAULT_MAX_BYTES
    assert cfg.redact_patterns == ()


def test_custom_max_bytes() -> None:
    """O3: operator can raise the per-file cap via [audit].max_bytes."""
    mod = _load_module()
    cfg = mod.load_audit_config({"audit": {"max_bytes": 1_048_576}})
    assert cfg.max_bytes == 1_048_576


def test_disable_redaction() -> None:
    """O3: operator can disable redaction + size cap via [audit].enabled = false."""
    mod = _load_module()
    cfg = mod.load_audit_config({"audit": {"enabled": False}})
    assert cfg.enabled is False


def test_custom_redact_patterns() -> None:
    """O3: operator can add project-specific redact patterns via [audit].redact_patterns."""
    mod = _load_module()
    patterns = ["SECRET_[A-Z]+", r"api_key=\S+"]
    cfg = mod.load_audit_config({"audit": {"redact_patterns": patterns}})
    assert cfg.redact_patterns == tuple(patterns)
