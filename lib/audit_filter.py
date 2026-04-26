"""Conservative redaction and per-file size cap for woof dispatch audit artefacts.

Built-in patterns cover the three highest-risk secret shapes that appear in
woof subprocess transcripts: JWT tokens, AWS access keys, and Bearer/Token
auth-header values.  Additional project-specific patterns flow in from
AuditConfig.redact_patterns (loaded from .woof/agents.toml [audit]).
"""

from __future__ import annotations

import re
from pathlib import Path

_TRUNCATION_FOOTER = (
    "\n... [truncated, full output at .woof/epics/E{epic_id}/audit/raw/]\n"
)

# Built-in patterns: (compiled regex, reason tag).
# Conservative bias: patterns are deliberately broad so false positives leave a
# [REDACTED] marker rather than leaking a secret to a committed file.
_BUILTIN: list[tuple[re.Pattern[str], str]] = [
    # JWT — three base64url segments separated by dots, header starting with eyJ
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "jwt"),
    # AWS access key — AKIA prefix followed by exactly 16 uppercase alphanumerics
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws-access-key"),
    # Bearer token value in HTTP Authorization header lines
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-=+/]{8,}"), "bearer-token"),
    # token= / Token: assignments (e.g. token=ghp_abc123, token=sk-abc123)
    (re.compile(r"(?i)\btoken[=:]\s*[A-Za-z0-9._\-=+/]{8,}"), "token-value"),
]


def redact(text: str, extra_patterns: tuple[str, ...] = ()) -> str:
    """Replace all secret-shaped spans in ``text`` with [REDACTED:<reason>].

    Built-in patterns run first; project-specific ``extra_patterns`` follow.
    """
    patterns: list[tuple[re.Pattern[str], str]] = list(_BUILTIN)
    for pat in extra_patterns:
        patterns.append((re.compile(pat), "custom"))
    for rx, reason in patterns:
        text = rx.sub(f"[REDACTED:{reason}]", text)
    return text


def apply_size_cap(
    text: str,
    *,
    max_bytes: int,
    raw_path: Path,
    epic_id: int,
) -> str:
    """Truncate ``text`` to ``max_bytes`` bytes if it exceeds the cap.

    The full pre-truncation text is written to ``raw_path`` (caller ensures
    the path is gitignored).  Returns the committed (possibly truncated) text.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(text, encoding="utf-8")

    footer = _TRUNCATION_FOOTER.format(epic_id=epic_id)
    footer_bytes = footer.encode("utf-8")
    keep = max(0, max_bytes - len(footer_bytes))
    # errors="ignore" drops any partial multi-byte sequence at the slice boundary,
    # ensuring the re-encoded result is always <= keep bytes (never expands).
    truncated = encoded[:keep].decode("utf-8", errors="ignore")
    return truncated + footer


def filter_audit_output(
    text: str,
    *,
    enabled: bool,
    max_bytes: int,
    redact_patterns: tuple[str, ...],
    raw_path: Path,
    epic_id: int,
) -> str:
    """Apply redaction then size cap to audit output when ``enabled`` is True.

    If ``enabled`` is False the text is returned unchanged and no raw file is
    written (allows the operator to opt out via agents.toml [audit].enabled).
    """
    if not enabled:
        return text
    text = redact(text, redact_patterns)
    return apply_size_cap(text, max_bytes=max_bytes, raw_path=raw_path, epic_id=epic_id)
