"""Audit artefact redaction and size capping for commit-bound files."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern

from woof.lib.audit_config import AuditConfig, load_audit_config

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|auth|bearer|credential|jwt|oauth|password|secret|token)", re.IGNORECASE
)
BUILTIN_PATTERNS: tuple[tuple[Pattern[str], str], ...] = (
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        "bearer_token",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "jwt",
    ),
    (
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        "aws_access_key",
    ),
    (
        re.compile(r"(?i)\b(?:api[_-]?key|password|secret|token)\b\s*[:=]\s*[\"']?[^\"'\s,}]+"),
        "secret_assignment",
    ),
)


@dataclass(frozen=True)
class RedactionPattern:
    regex: Pattern[str]
    reason: str


@dataclass(frozen=True)
class AuditFileSummary:
    path: str
    original_bytes: int
    committed_bytes: int
    redacted: bool = False
    truncated: bool = False
    raw_path: str | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)


def load_project_audit_config(repo_root: Path) -> AuditConfig:
    """Load the project audit policy, falling back to safe defaults."""

    agents_path = repo_root / ".woof" / "agents.toml"
    if not agents_path.is_file():
        return AuditConfig()
    with agents_path.open("rb") as fh:
        agents = tomllib.load(fh)
    return load_audit_config(agents)


def prepare_commit_audit(repo_root: Path, epic_dir: Path) -> list[AuditFileSummary]:
    """Redact and cap audit files that may be included in a story commit."""

    config = load_project_audit_config(repo_root)
    audit_dir = epic_dir / "audit"
    if not config.enabled or not audit_dir.is_dir():
        return []

    patterns = _redaction_patterns(repo_root, config)
    summaries: list[AuditFileSummary] = []
    for path in _commit_bound_audit_files(audit_dir):
        original = path.read_bytes()
        text = original.decode("utf-8", errors="replace")
        redacted_text, reasons = _redact(text, patterns)

        raw_path: Path | None = None
        candidate = redacted_text.encode()
        if len(candidate) > config.max_bytes:
            raw_path = _raw_path(audit_dir, path)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(original)
            raw_rel = raw_path.relative_to(repo_root).as_posix()
            redacted_text = _cap_text(redacted_text, config.max_bytes, raw_rel)

        committed = redacted_text.encode()
        if committed != original:
            path.write_text(redacted_text)

        summaries.append(
            AuditFileSummary(
                path=path.relative_to(repo_root).as_posix(),
                original_bytes=len(original),
                committed_bytes=len(committed),
                redacted=bool(reasons),
                truncated=raw_path is not None,
                raw_path=raw_path.relative_to(repo_root).as_posix() if raw_path else None,
                reasons=tuple(sorted(reasons)),
            )
        )
    return summaries


def _commit_bound_audit_files(audit_dir: Path) -> list[Path]:
    files = []
    for path in audit_dir.rglob("*"):
        if not path.is_file():
            continue
        if "raw" in path.relative_to(audit_dir).parts:
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


def _raw_path(audit_dir: Path, path: Path) -> Path:
    rel = path.relative_to(audit_dir).as_posix().replace("/", "__")
    return audit_dir / "raw" / rel


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
