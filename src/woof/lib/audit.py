"""Audit artefact redaction and size capping.

Audit artefacts live in the operator's state home and are never staged into a
delivery commit. Redaction still runs over them: the tree holds raw executor
transcripts, and a secret that leaks into one must not survive at rest.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern

from woof import state
from woof.project_config import AuditConfig, load_project_config

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|auth|bearer|credential|jwt|oauth|password|secret|token)", re.IGNORECASE
)
# High-signal secret tokens: distinctive provider prefixes and key material with
# near-zero false-positive rates. Safe to BLOCK on, so these back the cartography
# preflight secret gate over committed planning docs; they are also folded into
# the redaction set below.
SECRET_TOKEN_PATTERNS: tuple[tuple[Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "openai_key"),
    (re.compile(r"sk_live_[A-Za-z0-9]{10,}"), "stripe_live_key"),
    (re.compile(r"sk_test_[A-Za-z0-9]{10,}"), "stripe_test_key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "github_pat"),
    (re.compile(r"\bgho_[A-Za-z0-9]{36}\b"), "github_oauth_token"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "gitlab_pat"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "aws_access_key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "slack_token"),
    (re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"), "private_key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "jwt"),
)

# Context-dependent patterns. Over-redaction of executor transcripts is harmless,
# but these false-positive on ordinary prose ("the password field", "a Bearer
# token"), so the cartography gate must not use them; redaction only.
REDACTION_ONLY_PATTERNS: tuple[tuple[Pattern[str], str], ...] = (
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE), "bearer_token"),
    (
        re.compile(r"(?i)\b(?:api[_-]?key|password|secret|token)\b\s*[:=]\s*[\"']?[^\"'\s,}]+"),
        "secret_assignment",
    ),
)

# Redaction applies the full set; the high-signal tokens run first so a value
# matched as e.g. an aws_access_key is not also reported as a generic assignment.
BUILTIN_PATTERNS: tuple[tuple[Pattern[str], str], ...] = (
    SECRET_TOKEN_PATTERNS + REDACTION_ONLY_PATTERNS
)


@dataclass(frozen=True)
class RedactionPattern:
    regex: Pattern[str]
    reason: str


@dataclass(frozen=True)
class AuditFileSummary:
    """One audit file after redaction. Paths are relative to the epic's audit directory."""

    path: str
    original_bytes: int
    stored_bytes: int
    redacted: bool = False
    truncated: bool = False
    raw_path: str | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SecretHit:
    reason: str
    line: int


def scan_text_for_secrets(text: str) -> list[SecretHit]:
    """Scan text for high-signal secret tokens.

    Returns one hit per (pattern, line) match, carrying the pattern reason and
    the 1-based line number. The matched value is never returned or logged, so
    the result is safe to surface in preflight output, caches, and CLI text.
    """

    hits: list[SecretHit] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for regex, reason in SECRET_TOKEN_PATTERNS:
            if regex.search(line):
                hits.append(SecretHit(reason=reason, line=lineno))
    return hits


def load_project_audit_config(project_key: str | None = None) -> AuditConfig:
    """Return the ``[dispatch.audit]`` section of the project's config."""

    return load_project_config(project_key).dispatch.audit


def redact_audit_artefacts(
    project_key: str, epic_id: int, *, repo_root: Path
) -> list[AuditFileSummary]:
    """Redact and cap the epic's audit files in place.

    ``repo_root`` is read only for the redaction patterns derived from the driven
    repository's own secret files; the audit tree itself lives under the operator home.
    """

    config = load_project_audit_config(project_key)
    audit_dir = state.audit_dir(project_key, epic_id)
    if not config.enabled or not audit_dir.is_dir():
        return []

    raw_dir = state.audit_raw_dir(project_key, epic_id)
    patterns = _redaction_patterns(repo_root, config)
    summaries: list[AuditFileSummary] = []
    for path in _redactable_audit_files(audit_dir, raw_dir):
        original = path.read_bytes()
        text = original.decode("utf-8", errors="replace")
        redacted_text, reasons = _redact(text, patterns)

        raw_path: Path | None = None
        candidate = redacted_text.encode()
        if len(candidate) > config.max_bytes:
            raw_path = _raw_path(audit_dir, raw_dir, path)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(original)
            raw_rel = raw_path.relative_to(audit_dir).as_posix()
            redacted_text = _cap_text(redacted_text, config.max_bytes, raw_rel)

        stored = redacted_text.encode()
        if stored != original:
            path.write_text(redacted_text)

        summaries.append(
            AuditFileSummary(
                path=path.relative_to(audit_dir).as_posix(),
                original_bytes=len(original),
                stored_bytes=len(stored),
                redacted=bool(reasons),
                truncated=raw_path is not None,
                raw_path=raw_path.relative_to(audit_dir).as_posix() if raw_path else None,
                reasons=tuple(sorted(reasons)),
            )
        )
    return summaries


def _redactable_audit_files(audit_dir: Path, raw_dir: Path) -> list[Path]:
    files = []
    for path in audit_dir.rglob("*"):
        if not path.is_file() or path.is_relative_to(raw_dir):
            continue
        files.append(path)
    return sorted(files)


def _redaction_patterns(repo_root: Path, config: AuditConfig) -> list[RedactionPattern]:
    patterns = [RedactionPattern(regex, reason) for regex, reason in BUILTIN_PATTERNS]
    patterns.extend(_env_local_patterns(repo_root))
    patterns.extend(_gts_auth_patterns(repo_root))
    for pattern in config.redact_patterns:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid audit redact pattern {pattern!r}: {exc}") from exc
        patterns.append(RedactionPattern(regex, "custom_pattern"))
    return patterns


def _env_local_patterns(repo_root: Path) -> list[RedactionPattern]:
    env_path = repo_root / "env.local.sh"
    if not env_path.is_file():
        return []
    patterns: list[RedactionPattern] = []
    for raw in env_path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if not match or not SENSITIVE_KEY_RE.search(match.group(1)):
            continue
        value = _strip_shell_quotes(match.group(2).strip())
        if len(value) >= 4:
            patterns.append(RedactionPattern(re.compile(re.escape(value)), "env_local"))
    return patterns


def _strip_shell_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _gts_auth_patterns(repo_root: Path) -> list[RedactionPattern]:
    auth_path = repo_root / ".gts-auth.json"
    if not auth_path.is_file():
        return []
    try:
        payload = json.loads(auth_path.read_text())
    except json.JSONDecodeError:
        return []
    patterns: list[RedactionPattern] = []
    for value in _sensitive_json_values(payload):
        patterns.append(RedactionPattern(re.compile(re.escape(value)), "gts_auth"))
    return patterns


def _sensitive_json_values(value: object, key: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield from _sensitive_json_values(child_value, str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from _sensitive_json_values(child, key)
    elif isinstance(value, str) and len(value) >= 4 and SENSITIVE_KEY_RE.search(key):
        yield value


def _redact(text: str, patterns: list[RedactionPattern]) -> tuple[str, set[str]]:
    reasons: set[str] = set()
    redacted = text
    for pattern in patterns:
        marker = f"[REDACTED:{pattern.reason}]"
        redacted, count = pattern.regex.subn(marker, redacted)
        if count:
            reasons.add(pattern.reason)
    return redacted, reasons


def _raw_path(audit_dir: Path, raw_dir: Path, path: Path) -> Path:
    rel = path.relative_to(audit_dir).as_posix().replace("/", "__")
    return raw_dir / rel


def _cap_text(text: str, max_bytes: int, raw_rel: str) -> str:
    footer = f"\n... [truncated, full output at {raw_rel}]\n"
    data = text.encode()
    footer_bytes = footer.encode()
    if len(footer_bytes) >= max_bytes:
        return footer_bytes[:max_bytes].decode("utf-8", errors="ignore")

    keep = max_bytes - len(footer_bytes)
    prefix = data[:keep].decode("utf-8", errors="ignore")
    capped = prefix + footer
    while len(capped.encode()) > max_bytes and prefix:
        prefix = prefix[:-1]
        capped = prefix + footer
    return capped
