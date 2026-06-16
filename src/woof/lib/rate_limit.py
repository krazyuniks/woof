"""Rate-limit signal classification for dispatch telemetry."""

from __future__ import annotations

import re

# Unambiguous affirmative patterns — no negation check required.
_TOO_MANY_REQUESTS_RE = re.compile(r"\btoo\s+many\s+requests\b")
_429_HTTP_RE = re.compile(r"\b(?:http|status|error|code)\s+429\b")
_QUOTA_EXCEEDED_RE = re.compile(r"\bquota[\s_]exceeded\b")
_RESOURCE_EXHAUSTED_RE = re.compile(r"\bresource[\s_]exhausted\b")

# "Rate limit" phrase patterns — must be absent from negated context.
_RATE_LIMIT_EXCEEDED_RE = re.compile(r"\brate[\s_-]?limit\s+exceeded\b")
_RATE_LIMITED_RE = re.compile(r"\brate[\s_-]?limited\b")
_RATELIMIT_RE = re.compile(r"\bratelimit(?:ed)?\b")

# Negated rate-limit forms: "no rate limit", "no rate limited", "rate limit: none",
# and "no [qualifier] 429" with one optional intervening word to handle compound forms
# such as "no HTTP status 429" or "no error code 429".
_NEGATED_RE = re.compile(
    r"\bno\s+rate[\s_-]?limit(?:ed)?\b"
    r"|\brate[\s_-]?limit\s*:\s*none\b"
    r"|\bno\b(?:\s+\w+)?\s+(?:http|status|error|code)\s+429\b",
    re.IGNORECASE,
)


def classify(stdout: str, stderr: str) -> str | None:
    """Return 'rate_limited' if adapter output signals rate-limiting, else None.

    Prefers unambiguous markers (HTTP 429, too many requests, quota exceeded)
    before falling back to "rate limit" phrases. Negated forms such as
    "no rate limit was hit" are excluded from classification.
    """
    combined = (stdout + "\n" + stderr).lower()

    # Short-circuit on explicit negation before any pattern check.
    if _NEGATED_RE.search(combined):
        return None

    # Unambiguous patterns.
    if _TOO_MANY_REQUESTS_RE.search(combined):
        return "rate_limited"
    if _429_HTTP_RE.search(combined):
        return "rate_limited"
    if _QUOTA_EXCEEDED_RE.search(combined):
        return "rate_limited"
    if _RESOURCE_EXHAUSTED_RE.search(combined):
        return "rate_limited"

    # "Rate limit" phrases.
    if _RATE_LIMIT_EXCEEDED_RE.search(combined):
        return "rate_limited"
    if _RATE_LIMITED_RE.search(combined):
        return "rate_limited"
    if _RATELIMIT_RE.search(combined):
        return "rate_limited"

    return None
