"""Typed config loader for the [audit] block in .woof/agents.toml.

Consumed by woof dispatch (S2) to drive redaction and per-file size cap
on committed audit artefacts. S1 lands the contract surface only; no
behavioural change until S2 wires this into dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MAX_BYTES = 262_144  # 256 KB


@dataclass(frozen=True)
class AuditConfig:
    """Resolved audit policy. All fields have safe defaults."""

    enabled: bool = True
    max_bytes: int = DEFAULT_MAX_BYTES
    redact_patterns: tuple[str, ...] = field(default_factory=tuple)


def load_audit_config(agents: dict) -> AuditConfig:
    """Extract audit policy from a parsed agents.toml dict.

    Missing [audit] block or individual keys fall back to AuditConfig defaults.
    """
    block = agents.get("audit") or {}
    return AuditConfig(
        enabled=bool(block.get("enabled", True)),
        max_bytes=int(block.get("max_bytes", DEFAULT_MAX_BYTES)),
        redact_patterns=tuple(block.get("redact_patterns") or []),
    )
